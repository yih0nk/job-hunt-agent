"""Resolve a listing link to the real ATS posting, then fetch the JD text and (where the
ATS exposes them publicly) the application's real questions."""
from __future__ import annotations

import re
import urllib.parse

from .net import fetch, fetch_json, final_url, html_to_text

MIN_JD_CHARS = 200   # anything shorter isn't a real job description

AGGREGATORS = ("zapply.jobs", "simplify.jobs", "getro.com", "jobright.ai")


def decode_zapply(url: str) -> str | None:
    """zapply deep links encode the ATS: /l/d/<vendor>-<company>-<id>."""
    if "zapply.jobs" not in url:
        return None
    m = re.search(r"/l/d/([^/?]+)", urllib.parse.urlparse(url).path)
    if not m:
        return None
    parts = m.group(1).split("-")
    vendor = parts[0].lower()
    if vendor == "bytedance":
        return f"https://joinbytedance.com/search/{parts[-1]}"
    if len(parts) < 3:
        return None
    company, rest = parts[1], "-".join(parts[2:])
    return {
        "ashby": f"https://jobs.ashbyhq.com/{company}/{rest}",
        "lever": f"https://jobs.lever.co/{company}/{rest}",
        "gh": f"https://boards.greenhouse.io/{company}/jobs/{rest}",
        "greenhouse": f"https://boards.greenhouse.io/{company}/jobs/{rest}",
        "sr": f"https://jobs.smartrecruiters.com/{company}/{rest}",
    }.get(vendor)


def resolve(url: str) -> str:
    if not url:
        return url
    decoded = decode_zapply(url)
    if decoded:
        return decoded
    if any(a in url for a in AGGREGATORS):
        try:
            return final_url(url)
        except Exception:
            return url
    return url


def _greenhouse(url: str):
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url)
    if not m:
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        if "gh_jid" in q:
            # Company-hosted Greenhouse pages (stripe.com/jobs/...?gh_jid=N): the board token is
            # usually the company's domain name.
            board = q["for"][0] if "for" in q else (u.hostname or "").split(".")[-2] if u.hostname else ""
            return (board, q["gh_jid"][0]) if board else None
        return None
    return m.group(1), m.group(2)


def _token_candidates(first: str, company: str) -> list[str]:
    out = [first]
    core = re.sub(r"^(with|join|careers|jobs|work|life|team|go|get)", "", first)
    core = re.sub(r"(careers|jobs|hq|inc|team|talent)$", "", core)
    out.append(core)
    if company:
        out.append(re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", company.lower())))
    return [t for i, t in enumerate(out) if t and t not in out[:i]]


def _greenhouse_token_from_page(url: str) -> str:
    try:
        page = fetch(url)
    except Exception:
        return ""
    bad = {"embed", "v1", "jobs", "job_app", "job_board"}
    for m in re.finditer(r"(?:boards|job-boards|boards-api)\.greenhouse\.io/(?:embed/job_(?:board|app)(?:/js)?\?for=|v1/boards/)?([A-Za-z0-9_-]+)", page):
        if m.group(1).lower() not in bad:
            return m.group(1)
    m = re.search(r"""["']?(?:for|board_token|boardToken)["']?\s*[:=]\s*["']([A-Za-z0-9_-]+)["']""", page)
    return m.group(1) if m else ""


def _slug_name(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", slug) if w)


def _meta(html: str, prop: str) -> str:
    m = re.search(r"""<meta[^>]+(?:property|name)=["']%s["'][^>]+content=["']([^"']+)""" % re.escape(prop), html, re.I) \
        or re.search(r"""<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']%s["']""" % re.escape(prop), html, re.I)
    import html as _h
    return _h.unescape(m.group(1)).strip() if m else ""


def fetch_posting(url: str, company_hint: str = "") -> dict:
    """Return {url, text, questions, title, company, location}. Fields the source doesn't
    give are "". Best effort; never raises. company_hint helps find company-hosted boards."""
    out = _fetch_posting(url, company_hint)
    if len(out.get("text", "").strip()) < MIN_JD_CHARS:   # JS-only pages come back as "-" or a cookie banner
        out["text"] = ""
    for k in ("title", "company", "location"):
        out[k] = re.sub(r"\s+", " ", out.get(k) or "").strip()
    return out


def _fetch_posting(url: str, company_hint: str = "") -> dict:
    out = {"url": url, "text": "", "questions": [], "title": "", "company": "", "location": ""}
    if not url:
        return out
    try:
        gh = _greenhouse(url)
        if gh:
            j, last = None, None
            # Company-hosted pages (careers.withwaymo.com?gh_jid=N) don't name the board, so
            # try likely tokens: the domain, the domain minus with/join/careers..., the company
            # name, then whatever the page itself references.
            for token in _token_candidates(gh[0], company_hint) + [None]:
                token = token or _greenhouse_token_from_page(url)
                if not token:
                    break
                try:
                    j = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{gh[1]}?questions=true")
                    gh = (token, gh[1])
                    break
                except Exception as e:
                    last = e
            if j is None:
                raise last or ValueError("Greenhouse board not found")
            import html as _h
            out["text"] = html_to_text(_h.unescape(j.get("content") or ""))
            out["title"] = j.get("title", "")
            out["location"] = (j.get("location") or {}).get("name", "")
            out["company"] = j.get("company_name") or _slug_name(gh[0])
            try:
                out["company"] = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{gh[0]}").get("name") or out["company"]
            except Exception:
                pass
            out["questions"] = [
                {"label": q.get("label", ""), "required": q.get("required", False),
                 "type": (q.get("fields") or [{}])[0].get("type", ""),
                 "options": [v.get("label") for v in (q.get("fields") or [{}])[0].get("values", [])]}
                for q in j.get("questions", [])
                if q.get("label", "").lower() not in {"first name", "last name", "email", "phone",
                                                       "resume/cv", "cover letter"}]
            return out
        m = re.search(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})", url)
        if m:
            j = fetch_json(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
            lists = "\n\n".join(f"{l.get('text')}\n{html_to_text(l.get('content', ''))}" for l in j.get("lists", []))
            out["text"] = "\n\n".join(filter(None, [j.get("descriptionPlain"), lists, j.get("additionalPlain")]))
            out.update(title=j.get("text", ""), company=_slug_name(m.group(1)),
                       location=(j.get("categories") or {}).get("location", ""))
            return out
        m = re.search(r"jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})", url)
        if m:
            board = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
            for j in board.get("jobs", []):
                if m.group(2) in (j.get("id", ""), j.get("jobUrl", "")):
                    out["text"] = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
                    out.update(title=j.get("title", ""), company=_slug_name(m.group(1)), location=j.get("location", ""))
                    return out
        m = re.search(r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?]+)/job/([^?#]+)", url)
        if m:  # Workday renders client-side; its CXS JSON API has everything
            tenant, wd, site, rest = m.groups()
            j = fetch_json(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{rest}")
            info = j.get("jobPostingInfo") or {}
            out.update(text=html_to_text(info.get("jobDescription", "")), title=info.get("title", ""),
                       location=info.get("location", ""),
                       company=(j.get("hiringOrganization") or {}).get("name") or _slug_name(tenant))
            return out
        m = re.search(r"apply\.workable\.com/([\w-]+)/j/(\w+)", url)
        if m:  # Workable too
            j = fetch_json(f"https://apply.workable.com/api/v2/accounts/{m.group(1)}/jobs/{m.group(2)}")
            loc = j.get("location") or {}
            out.update(text=html_to_text("\n".join(filter(None, [j.get("description"), j.get("requirements"), j.get("benefits")]))),
                       title=j.get("title", ""), company=_slug_name(m.group(1)),
                       location=", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")])) if isinstance(loc, dict) else str(loc))
            return out
        page = fetch(url)
        out["text"] = html_to_text(page)[:30000]
        # Generic pages (LinkedIn, company career sites): take what the page declares; an LLM
        # pass fills anything still missing.
        out["page_title"] = _meta(page, "og:title") or (re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I).group(1).strip()
                                                       if re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I) else "")
        out["site_name"] = _meta(page, "og:site_name")
    except Exception as e:  # network errors, 404s, odd pages
        out["error"] = str(e)
    return out
