"""Job sources. Every fetcher returns rows in one shape:

    {company, title, location, url, age_days, source, description?}

Supported:
  listing_repo        GitHub README tables (markdown pipe tables or HTML tables),
                      e.g. SimplifyJobs/Summer2027-Internships, DereC4/internships-and-newgrad
  github_issues       open "New Internship" submission issues on a listing repo
                      (a leading indicator: they land hours-days before the README)
  greenhouse / lever / ashby
                      a single company's public job board API
  early_career_radar  earlycareerradar.com public listings page (never its /api/)
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import re

from .models import Source
from .net import fetch, fetch_json, html_to_text

# --- shared -------------------------------------------------------------------------

LINK = re.compile(r"\[(.*?)\]\((.*?)\)")


def age_to_days(s: str | None):
    """'0d', '3d', '2w', '1mo', '1h', '1y' -> days. None if unknown."""
    if not s:
        return None
    m = re.match(r"\s*(\d+)\s*(h|hr|hrs|hours?|d|days?|w|wks?|weeks?|mo|mon|months?|y|yrs?|years?)?",
                 s.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), (m.group(2) or "d")
    if unit.startswith("h"):
        return n / 24.0
    if unit.startswith("w"):
        return n * 7.0
    if unit.startswith("mo"):
        return n * 30.0
    if unit.startswith("y"):
        return n * 365.0
    return float(n)


def days_since(iso: str | None):
    if not iso:
        return None
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (dt.datetime.now(dt.timezone.utc) - d).total_seconds() / 86400)
    except ValueError:
        return None


def _strip(s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _cell(cell: str):
    m = LINK.search(cell)
    return (m.group(1).strip(), m.group(2).strip()) if m else (cell.strip(), "")


# --- GitHub listing repos -----------------------------------------------------------

def parse_markdown_table(md: str) -> list[dict]:
    rows, prev = [], ""
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or all(set(c) <= set("-: ") for c in cells):
            continue
        low = " ".join(c.lower() for c in cells)
        if "company" in low and "role" in low:
            continue
        if len(cells) < 3:
            continue
        company, _ = _cell(cells[0])
        company = _strip(company)
        if company in ("", "↳"):
            company = prev
        else:
            prev = company
        title, url = _cell(cells[1])
        location, _ = _cell(cells[2])
        # Some tables put the apply link in its own column.
        if not url and len(cells) > 3:
            _, url = _cell(cells[3])
        age = _cell(cells[-1])[0] if len(cells) > 3 else ""
        if company and title:
            rows.append({"company": company, "title": _strip(title), "url": url,
                         "location": _strip(location.replace("<br>", ", ")),
                         "age_days": age_to_days(age)})
    return rows


def parse_html_table(text: str) -> list[dict]:
    """HTML <table> listings (SimplifyJobs). Company | Role | Location | Application | Age.
    The first non-simplify href in the Application cell is the direct ATS link."""
    rows, prev = [], ""
    for tr in re.findall(r"<tr>(.*?)</tr>", text, re.DOTALL):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
        if len(tds) < 4:
            continue
        company = _strip(tds[0])
        if company in ("", "↳"):
            company = prev
        else:
            prev = company
        hrefs = re.findall(r'href="([^"]+)"', tds[3])
        url = next((h for h in hrefs if "simplify.jobs" not in h), hrefs[0] if hrefs else "")
        location = _strip(re.sub(r"(?i)<br\s*/?>|</summary>", ", ", tds[2]))
        if company and _strip(tds[1]):
            rows.append({"company": company, "title": _strip(tds[1]), "url": url,
                         "location": location,
                         "age_days": age_to_days(_strip(tds[4]) if len(tds) > 4 else "")})
    return rows


def fetch_listing_repo(src: Source) -> list[dict]:
    text = fetch(f"https://raw.githubusercontent.com/{src.repo}/{src.branch or 'main'}/{src.file or 'README.md'}")
    rows = parse_markdown_table(text)
    if not rows and "<td" in text:
        rows = parse_html_table(text)
    return rows


ISSUE_FIELD = re.compile(r"^###\s+(.+?)\s*$", re.M)


def fetch_github_issues(src: Source) -> list[dict]:
    items = fetch_json(f"https://api.github.com/repos/{src.repo}/issues?state=open&per_page=100")
    prefix = (src.title_prefix or "New Internship").lower()
    rows = []
    for it in items:
        if it.get("pull_request") or not it.get("title", "").lower().startswith(prefix):
            continue
        parts = ISSUE_FIELD.split(it.get("body") or "")
        f = {parts[i].strip().lower(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
        link = (f.get("link to internship posting") or "").split()
        if not link:
            continue
        if src.term and src.term.lower() not in f.get("what term(s) is this internship offered for?", "").lower():
            continue
        if "[x]" in f.get("advanced degree requirements", "").lower():
            continue
        if f.get("is this internship currently accepting applications?", "yes").lower().startswith("no"):
            continue
        rows.append({"company": f.get("company name", ""), "title": f.get("internship title", ""),
                     "location": f.get("location", "").replace("|", "·"),
                     "url": re.sub(r"[?&]fbclid=[^&]+", "", link[0]),
                     "age_days": days_since(it.get("created_at"))})
    return rows


# --- Company ATS boards (public posting APIs) ---------------------------------------

def fetch_greenhouse(src: Source) -> list[dict]:
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{src.board}/jobs?content=true")
    company = src.name or src.board
    return [{"company": company, "title": j.get("title", ""),
             "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url", ""),
             "age_days": days_since(j.get("first_published") or j.get("updated_at")),
             "description": html_to_text(_html.unescape(j.get("content") or ""))}
            for j in data.get("jobs", [])]


def fetch_lever(src: Source) -> list[dict]:
    data = fetch_json(f"https://api.lever.co/v0/postings/{src.board}?mode=json")
    company = src.name or src.board
    rows = []
    for j in data:
        created = j.get("createdAt")
        age = (dt.datetime.now().timestamp() - created / 1000) / 86400 if created else None
        desc = "\n\n".join(filter(None, [j.get("descriptionPlain"),
                                          *[f"{l.get('text')}\n{html_to_text(l.get('content', ''))}"
                                            for l in j.get("lists", [])],
                                          j.get("additionalPlain")]))
        rows.append({"company": company, "title": j.get("text", ""),
                     "location": (j.get("categories") or {}).get("location", ""),
                     "url": j.get("hostedUrl", ""), "age_days": age, "description": desc})
    return rows


def fetch_ashby(src: Source) -> list[dict]:
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{src.board}")
    company = src.name or src.board
    rows = []
    for j in data.get("jobs", []):
        loc = j.get("location", "")
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip()
        rows.append({"company": company, "title": j.get("title", ""), "location": loc,
                     "url": j.get("jobUrl", ""), "age_days": days_since(j.get("publishedAt")),
                     "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))})
    return rows


# --- Early Career Radar -------------------------------------------------------------

ECR_JOB = re.compile(r'\{"id":"job_[0-9a-f]+".*?"dismissed":(?:true|false)\}')


def fetch_ecr(src: Source) -> list[dict]:
    text = fetch(src.url or "https://earlycareerradar.com/summer-internships", timeout=60)
    text = text.replace('\\"', '"').replace("\\\\", "\\")
    tracks = [t.lower() for t in src.accept_tracks]
    rows, seen = [], set()
    for m in ECR_JOB.finditer(text):
        try:
            j = json.loads(m.group(0))
        except ValueError:
            continue
        if j.get("id") in seen or j.get("closed"):
            continue
        seen.add(j["id"])
        if tracks and (j.get("track") or "").lower() not in tracks:
            continue
        rows.append({"company": j.get("company", ""), "title": j.get("title", ""),
                     "location": j.get("location", ""), "url": j.get("applyUrl", ""),
                     "age_days": days_since(j.get("postedAt"))})
    return rows


FETCHERS = {
    "listing_repo": fetch_listing_repo,
    "github_issues": fetch_github_issues,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "early_career_radar": fetch_ecr,
}


def source_label(src: Source) -> str:
    if src.name:
        return src.name
    if src.kind in ("listing_repo", "github_issues"):
        return src.repo + (" (issues)" if src.kind == "github_issues" else "")
    if src.kind in ("greenhouse", "lever", "ashby"):
        return f"{src.board} ({src.kind})"
    return src.kind


def fetch_source(src: Source) -> list[dict]:
    rows = FETCHERS[src.kind](src)
    label = source_label(src)
    for r in rows:
        r["source"] = label
        r["source_kind"] = src.kind
    return rows


# Presets offered in onboarding. Users can add any repo/board themselves.
PRESETS: list[dict] = [
    {"kind": "listing_repo", "name": "SimplifyJobs Summer 2027 Internships",
     "repo": "SimplifyJobs/Summer2027-Internships", "branch": "dev", "file": "README.md",
     "for": ["internship"]},
    {"kind": "listing_repo", "name": "SimplifyJobs New Grad Positions",
     "repo": "SimplifyJobs/New-Grad-Positions", "branch": "dev", "file": "README.md",
     "for": ["new_grad", "entry"]},
    {"kind": "listing_repo", "name": "DereC4 Internships & New Grad",
     "repo": "DereC4/internships-and-newgrad", "branch": "main", "file": "README.md",
     "for": ["internship", "new_grad"]},
    {"kind": "github_issues", "name": "SimplifyJobs pending submissions",
     "repo": "SimplifyJobs/Summer2027-Internships", "title_prefix": "New Internship",
     "for": ["internship"]},
    {"kind": "early_career_radar", "name": "Early Career Radar",
     "url": "https://earlycareerradar.com/summer-internships", "accept_tracks": ["SWE", "ML & AI"],
     "for": ["internship", "new_grad"]},
]
