"""Tracker import: turn a user's existing application tracker (CSV / TSV / Markdown table /
plain lines) into jobs with their real statuses, so the Tracker is complete and the scanner
never re-surfaces roles they've already applied to.

Imported rows land with applied/interviewing/offer/rejected/archived statuses, which is exactly
the set `Store.tracked_pairs()` feeds to `scan()` for fuzzy dedup — so the skip is free.

No LLM is involved: parsing is plain code, matching is `filters.same_position`.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections import Counter

from .filters import job_id, same_position
from .store import Store

SOURCE = "tracker import"

# Header synonyms, matched on the normalized header (lowercase, punctuation -> space).
FIELDS: dict[str, list[str]] = {
    "company": ["company", "employer", "org", "organization", "organisation", "firm", "company name"],
    "title": ["role", "title", "position", "job", "job title", "role title", "job name", "posting title"],
    "location": ["location", "city", "where", "office", "loc"],
    "url": ["url", "link", "posting link", "job link", "job url", "apply", "application link", "apply link", "posting url"],
    "status": ["status", "stage", "state", "outcome", "result", "progress"],
    "applied_at": ["applied", "date applied", "applied on", "applied date", "date", "submitted", "sent", "applied at",
                   "application date", "when"],
}
# Looser fallback when no synonym matches exactly: the first header containing the token wins.
CONTAINS: list[tuple[str, str]] = [
    ("company", "company"), ("company", "employer"), ("title", "role"), ("title", "title"), ("title", "position"),
    ("url", "link"), ("url", "url"), ("status", "status"), ("status", "stage"), ("applied_at", "applied"),
    ("applied_at", "date"), ("location", "location"), ("location", "city"),
]

# Earlier stages never overwrite later ones on an update; terminal statuses are set as given.
RANK = {"new": 0, "scored": 1, "review": 2, "ineligible": 2, "drafting": 3, "drafted": 4,
        "applied": 5, "interviewing": 6, "offer": 7}
TERMINAL = {"rejected", "archived"}
IMPORT_STATUSES = {"applied", "interviewing", "offer", "rejected", "archived"}

LINK = re.compile(r"\[(.*?)\]\((.*?)\)")
DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%d/%m/%Y",
                "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%b %Y", "%B %Y"]


# --- normalization -------------------------------------------------------------------

def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def map_headers(headers: list[str]) -> dict[str, str]:
    """{header -> field}. Exact synonyms first, then a containment fallback; one header per field."""
    norm = {h: _norm_header(h) for h in headers}
    out: dict[str, str] = {}
    taken: set[str] = set()
    for field, names in FIELDS.items():
        for h, n in norm.items():
            if h not in out and n in names:
                out[h], _ = field, taken.add(field)
                break
    for field, token in CONTAINS:
        if field in taken:
            continue
        for h, n in norm.items():
            if h not in out and token in n:
                out[h], _ = field, taken.add(field)
                break
    return out


def norm_status(s: str | None) -> str:
    """Map whatever a tracker says to the app's vocabulary. Unknown -> applied: a row in an
    application tracker is, by default, something you applied to."""
    t = (s or "").strip().lower()
    if not t:
        return "applied"
    if any(k in t for k in ("reject", "declin", "closed", "ghost", "no response", "not selected", "unsuccessful",
                            "turned down", "❌", "✗")):
        return "rejected"
    if any(k in t for k in ("offer", "accepted", "signed", "hired")):
        return "offer"
    if any(k in t for k in ("interview", "screen", "assessment", "onsite", "on site", "final", "recruiter",
                            "technical", "round", "hirevue", "take home", "take-home")) or re.search(r"\boa\b", t):
        return "interviewing"
    if any(k in t for k in ("withdr", "not applying", "skip", "archiv", "pass", "ignore")):
        return "archived"
    return "applied"


def parse_date(s: str | None) -> float | None:
    """ISO or common human formats -> unix timestamp (UTC midnight). None rather than a guess."""
    t = (s or "").strip()
    if not t:
        return None
    try:
        d = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        if len(t) <= 10:          # date only: noon UTC (see below)
            d = d.replace(hour=12)
        return d.timestamp()
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            # Noon UTC, so a date-only value shows the same calendar day in every timezone.
            return dt.datetime.strptime(t, fmt).replace(hour=12, tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _cell_text_url(cell: str) -> tuple[str, str]:
    m = LINK.search(cell)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return cell.strip(), ""


# --- parsing -------------------------------------------------------------------------

def _rows_from_table(headers: list[str], records: list[list[str]]) -> tuple[list[dict], dict[str, str]]:
    mapping = map_headers(headers)
    if "company" not in mapping.values() or "title" not in mapping.values():
        return [], mapping
    idx = {field: headers.index(h) for h, field in mapping.items()}
    rows = []
    for rec in records:
        get = lambda f: rec[idx[f]].strip() if f in idx and idx[f] < len(rec) else ""
        company, c_url = _cell_text_url(get("company"))
        title, t_url = _cell_text_url(get("title"))
        if not company or not title:
            continue
        url = get("url") or t_url or c_url
        rows.append({"company": company, "title": title, "location": _cell_text_url(get("location"))[0],
                     "url": _cell_text_url(url)[1] or url, "raw_status": get("status"),
                     "status": norm_status(get("status")), "applied_at": parse_date(get("applied_at"))})
    return rows, mapping


def _parse_markdown(text: str) -> tuple[list[dict], dict[str, str]]:
    table = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    cells = []
    for ln in table:
        parts = [c.strip() for c in ln.strip("|").split("|")]
        if parts and all(set(c) <= set("-: ") for c in parts):
            continue   # separator row
        cells.append(parts)
    if len(cells) < 2:
        return [], {}
    return _rows_from_table(cells[0], cells[1:])


def _parse_delimited(text: str) -> tuple[list[dict], dict[str, str]]:
    sample = "\n".join(text.splitlines()[:10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delim = dialect.delimiter
    except csv.Error:
        delim = "\t" if "\t" in sample else ","
    records = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if len(records) < 2:
        return [], {}
    return _rows_from_table([h.strip() for h in records[0]], records[1:])


def _parse_lines(text: str) -> list[dict]:
    """Headerless lines: 'Company — Role — Status', 'Company - Role', 'Company | Role'."""
    rows = []
    for ln in text.splitlines():
        parts = [p.strip() for p in re.split(r"\s+[—–-]\s+|\s*\|\s*|\t", ln.strip()) if p.strip()]
        if len(parts) >= 2:
            rows.append({"company": parts[0], "title": parts[1], "location": "", "url": "",
                         "raw_status": parts[2] if len(parts) > 2 else "",
                         "status": norm_status(parts[2] if len(parts) > 2 else ""), "applied_at": None})
    return rows


def parse_tracker(data: bytes, filename: str = "") -> tuple[list[dict], dict[str, str]]:
    """-> (rows, header_mapping). Rows: {company, title, location, url, status, raw_status, applied_at}.
    Duplicates within the file (same position) keep the first occurrence."""
    text = data.decode("utf-8-sig", "replace")
    rows: list[dict] = []
    mapping: dict[str, str] = {}
    if filename.lower().endswith(".md") or text.lstrip().startswith("|"):
        rows, mapping = _parse_markdown(text)
    if not rows:
        rows, mapping = _parse_delimited(text)
    if not rows:
        rows, mapping = _parse_lines(text), {}
    out: list[dict] = []
    for r in rows:
        if any(same_position((r["company"], r["title"]), (o["company"], o["title"])) for o in out):
            continue
        out.append(r)
    return out, mapping
