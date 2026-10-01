"""Resolve a listing link to the real ATS posting, then fetch the JD text and (where the
ATS exposes them publicly) the application's real questions."""
from __future__ import annotations

import re
import urllib.parse

from .net import fetch, fetch_json, final_url, html_to_text

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
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "gh_jid" in q and "for" in q:
            return q["for"][0], q["gh_jid"][0]
        return None
    return m.group(1), m.group(2)


def fetch_posting(url: str) -> dict:
    """Return {url, text, questions}. Best effort; never raises."""
    out = {"url": url, "text": "", "questions": []}
    if not url:
        return out
    try:
        gh = _greenhouse(url)
        if gh:
            j = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{gh[0]}/jobs/{gh[1]}?questions=true")
            import html as _h
            out["text"] = html_to_text(_h.unescape(j.get("content") or ""))
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
            return out
        m = re.search(r"jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})", url)
        if m:
            board = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
            for j in board.get("jobs", []):
                if m.group(2) in (j.get("id", ""), j.get("jobUrl", "")):
                    out["text"] = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
                    return out
        out["text"] = html_to_text(fetch(url))[:30000]
    except Exception as e:  # network errors, 404s, odd pages
        out["error"] = str(e)
    return out
