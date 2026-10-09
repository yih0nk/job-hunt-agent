"""Job sources. Every fetcher returns rows in one shape:

    {company, title, location, url, age_days, source, description?}

Supported:
  listing_repo        GitHub README tables (markdown pipe tables or HTML tables),
                      e.g. SimplifyJobs/Summer2027-Internships, DereC4/internships-and-newgrad
  github_issues       open "New Internship" submission issues on a listing repo
                      (a leading indicator: they land hours-days before the README)
  greenhouse / lever / ashby / workday / smartrecruiters / workable / bamboohr / recruitee
                      a single company's public job board API (Workday is the one POST)
  careers_page        any careers page that embeds schema.org JobPosting JSON-LD
  early_career_radar  earlycareerradar.com public listings page (never its /api/)

`detect_source(text)` turns a pasted careers URL into the right Source (see docs/sourcing.md).
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import re

from .models import Source
from .net import fetch, fetch_json, fetch_json_post, html_to_text

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


# --- More company boards --------------------------------------------------------------
# None of these fetch the JD up front unless the listing call already carries it: the
# scorer's _ensure_description pulls it later, only for rows that survive the funnel.

def _workday_parts(board: str) -> tuple[str, str, str]:
    """board = "tenant|wd5|External_Careers" (the watchlist convention; '/' works too)."""
    parts = [p.strip() for p in board.replace("/", "|").split("|") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Workday board must be tenant|wdN|site, got {board!r}")
    return parts[0], parts[1], parts[2]


WD_POSTED = re.compile(r"(\d+)(\+?)\s*(day|hour|minute)", re.I)


def workday_age(posted: str | None):
    """'Posted Today' -> 0, 'Posted Yesterday' -> 1, 'Posted 3 Days Ago' -> 3, 'Posted 30+ Days Ago' -> 31."""
    t = (posted or "").lower()
    if not t:
        return None
    if "today" in t or "just" in t:
        return 0.0
    if "yesterday" in t:
        return 1.0
    m = WD_POSTED.search(t)
    if not m:
        return None
    n = float(m.group(1)) + (1 if m.group(2) else 0)
    unit = m.group(3)
    return n / 24 if unit == "hour" else n / 1440 if unit == "minute" else n


def fetch_workday(src: Source) -> list[dict]:
    tenant, wd, site = _workday_parts(src.board)
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    company = src.name or _title_from_slug(tenant)
    rows, offset, limit = [], 0, 20
    while True:
        data = fetch_json_post(f"{host}/wday/cxs/{tenant}/{site}/jobs",
                               {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": src.term or ""})
        posts = data.get("jobPostings") or []
        for j in posts:
            path = j.get("externalPath", "")
            rows.append({"company": company, "title": j.get("title", ""), "location": j.get("locationsText", ""),
                         "url": f"{host}/{site}{path}" if path else "", "age_days": workday_age(j.get("postedOn"))})
        offset += limit
        if not posts or offset >= (data.get("total") or 0) or offset >= 2000:
            break
    return rows


def fetch_smartrecruiters(src: Source) -> list[dict]:
    company = src.name or _title_from_slug(src.board)
    rows, offset = [], 0
    while True:
        data = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{src.board}/postings?limit=100&offset={offset}")
        items = data.get("content") or []
        for j in items:
            loc = j.get("location") or {}
            place = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
            if loc.get("remote"):
                place = f"{place} (Remote)".strip()
            rows.append({"company": (j.get("company") or {}).get("name") or company, "title": j.get("name", ""),
                         "location": place, "url": f"https://jobs.smartrecruiters.com/{src.board}/{j.get('id')}",
                         "age_days": days_since(j.get("releasedDate"))})
        offset += 100
        if not items or offset >= (data.get("totalFound") or 0) or offset >= 2000:
            break
    return rows


def _place(loc, *keys: str) -> str:
    if not isinstance(loc, dict):
        return str(loc or "")
    return ", ".join(filter(None, [loc.get(k) for k in keys]))


def fetch_workable(src: Source) -> list[dict]:
    data = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{src.board}?details=true")
    company = src.name or data.get("name") or _title_from_slug(src.board)
    rows = []
    for j in data.get("jobs", []):
        place = _place(j.get("location"), "city", "region", "country")
        if j.get("telecommuting") or j.get("remote"):
            place = f"{place} (Remote)".strip()
        rows.append({"company": company, "title": j.get("title", ""), "location": place,
                     "url": j.get("url") or j.get("application_url", ""),
                     "age_days": days_since(j.get("published_on")),
                     "description": html_to_text(j.get("description") or "")})
    return rows


def fetch_bamboohr(src: Source) -> list[dict]:
    data = fetch_json(f"https://{src.board}.bamboohr.com/careers/list")
    company = src.name or _title_from_slug(src.board)
    rows = []
    for j in data.get("result") or []:
        place = _place(j.get("location"), "city", "state", "country")
        if j.get("isRemote"):
            place = f"{place} (Remote)".strip()
        rows.append({"company": company, "title": j.get("jobOpeningName", ""), "location": place,
                     "url": f"https://{src.board}.bamboohr.com/careers/{j.get('id')}",
                     "age_days": days_since(j.get("datePosted"))})
    return rows


def fetch_recruitee(src: Source) -> list[dict]:
    data = fetch_json(f"https://{src.board}.recruitee.com/api/offers/")
    company = src.name or _title_from_slug(src.board)
    rows = []
    for j in data.get("offers") or []:
        place = ", ".join(filter(None, [j.get("city"), j.get("country")]))
        if j.get("remote"):
            place = f"{place} (Remote)".strip()
        rows.append({"company": company, "title": j.get("title", ""), "location": place,
                     "url": j.get("careers_url", ""), "age_days": days_since(j.get("published_at")),
                     "description": html_to_text(j.get("description") or "")})
    return rows


# --- Any careers page with schema.org JobPosting JSON-LD ------------------------------
# What Google Jobs indexes: a <script type="application/ld+json"> JobPosting per role, often on
# the listing page too. The generic fallback for companies on a custom careers site.

LDJSON = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _job_postings(obj) -> list[dict]:
    """Walk JSON-LD (objects, lists, @graph, ItemList) and collect every JobPosting."""
    out: list[dict] = []
    if isinstance(obj, list):
        for o in obj:
            out.extend(_job_postings(o))
    elif isinstance(obj, dict):
        t = obj.get("@type")
        if "JobPosting" in (t if isinstance(t, list) else [t]):
            out.append(obj)
        for k in ("@graph", "itemListElement", "mainEntity", "item"):
            if k in obj:
                out.extend(_job_postings(obj[k]))
    return out


def _ld_location(j: dict) -> str:
    locs = j.get("jobLocation") or []
    names = []
    for loc in locs if isinstance(locs, list) else [locs]:
        a = loc.get("address", loc) if isinstance(loc, dict) else {}
        if isinstance(a, str):
            names.append(a)
            continue
        country = a.get("addressCountry")
        names.append(", ".join(filter(None, [a.get("addressLocality"), a.get("addressRegion"),
                                             country.get("name") if isinstance(country, dict) else country])))
    place = "; ".join(n for n in names if n)
    if str(j.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        place = f"{place} (Remote)".strip()
    return place


def postings_in_page(page: str) -> list[dict]:
    out = []
    for block in LDJSON.findall(page):
        try:
            out.extend(_job_postings(json.loads(block.strip())))
        except ValueError:
            continue
    return out


def fetch_careers_page(src: Source) -> list[dict]:
    rows = []
    for j in postings_in_page(fetch(src.url, timeout=45)):
        org = j.get("hiringOrganization") or {}
        rows.append({"company": src.name or (org.get("name") if isinstance(org, dict) else str(org)) or "",
                     "title": j.get("title") or j.get("name") or "", "location": _ld_location(j),
                     "url": j.get("url") or j.get("sameAs") or src.url, "age_days": days_since(j.get("datePosted")),
                     "description": html_to_text(j.get("description") or "")})
    return rows


def _title_from_slug(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


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
    "workday": fetch_workday,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "bamboohr": fetch_bamboohr,
    "recruitee": fetch_recruitee,
    "careers_page": fetch_careers_page,
    "early_career_radar": fetch_ecr,
}
BOARD_KINDS = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable", "bamboohr", "recruitee"}


def source_label(src: Source) -> str:
    if src.name:
        return src.name
    if src.kind in ("listing_repo", "github_issues"):
        return src.repo + (" (issues)" if src.kind == "github_issues" else "")
    if src.kind in BOARD_KINDS:
        return f"{src.board} ({src.kind})"
    if src.kind == "careers_page":
        return re.sub(r"^https?://(www\.)?", "", src.url).split("/")[0]
    return src.kind


def fetch_source(src: Source) -> list[dict]:
    rows = FETCHERS[src.kind](src)
    label = source_label(src)
    for r in rows:
        r["source"] = label
        r["source_kind"] = src.kind
    return rows


# --- ATS auto-detect ------------------------------------------------------------------
# Paste any careers URL -> the right Source. URL shapes first; for a custom careers site,
# one page fetch looks for an embedded board, then for JobPosting JSON-LD.

DETECT: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_(?:board|app)/?\?(?:[^#]*&)?for=)?([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"greenhouse\.io/[^#]*?[?&]for=([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"), "ashby"),
    (re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.-]+)"), "ashby"),
    (re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[\w-]+/)?(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)"), "workday"),
    (re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"), "smartrecruiters"),
    (re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)"), "workable"),
    (re.compile(r"https?://([\w-]+)\.workable\.com"), "workable"),
    (re.compile(r"https?://([\w-]+)\.bamboohr\.com"), "bamboohr"),
    (re.compile(r"https?://([\w-]+)\.recruitee\.com"), "recruitee"),
    (re.compile(r"github\.com/([\w.-]+/[\w.-]+)"), "listing_repo"),
]
NOT_BOARDS = {"embed", "jobs", "job", "api", "www", "careers", "en", "wday"}


def _match(text: str, skip_repo: bool = False) -> dict | None:
    for rx, kind in DETECT:
        if skip_repo and kind == "listing_repo":
            continue
        m = rx.search(text)
        if not m:
            continue
        if kind == "listing_repo":
            repo = m.group(1).removesuffix(".git")
            return {"kind": kind, "repo": repo, "name": repo, "branch": "main", "file": "README.md"}
        if kind == "workday":
            tenant, wd, site = m.group(1), m.group(2), m.group(3)
            if site.lower() in NOT_BOARDS:
                continue
            return {"kind": kind, "board": f"{tenant}|{wd}|{site}", "name": _title_from_slug(tenant)}
        board = m.group(1)
        if board.lower() in NOT_BOARDS:
            continue
        return {"kind": kind, "board": board, "name": _title_from_slug(board)}
    return None


def detect_source(text: str, sniff: bool = True) -> dict | None:
    """A careers URL (or board link) -> {kind, board|repo|url, name}, or None if unrecognized.
    With sniff, an unrecognized http URL is fetched once to find an embedded board or JSON-LD."""
    t = (text or "").strip()
    if not t:
        return None
    found = _match(t)
    if found:
        return found
    if not sniff or not t.startswith("http"):
        return None
    try:
        page = fetch(t, timeout=20)
    except Exception:
        return None
    found = _match(page, skip_repo=True)
    if found:
        return found
    if postings_in_page(page):
        host = re.sub(r"^https?://(www\.)?", "", t).split("/")[0]
        return {"kind": "careers_page", "url": t, "name": _title_from_slug(host.split(".")[0])}
    return None


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
