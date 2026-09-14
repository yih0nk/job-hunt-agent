#!/usr/bin/env python3
"""Poll a GitHub listing repo, parse its markdown job table, apply hard filters,
and emit only roles not seen before.

Diff strategy: each row is hashed on (company, role_text, location) so table
re-sorts and changed tracking-links do not produce false "new" rows. Seen hashes
persist in state/seen.json; brand-new roles are appended to state/queue.jsonl.

Usage:
    python3 bin/poll-repo.py --config config/preferences.yaml
Stdlib HTTP only; needs PyYAML for config parsing.
"""
import argparse, hashlib, json, os, re, ssl, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"

def ssl_ctx():
    """Portable CA context: prefer certifi (fixes python.org builds with no CA bundle)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

def load_yaml(p):
    import yaml
    with open(os.path.expanduser(p)) as f:
        return yaml.safe_load(f)

def fetch(repo, branch, file):
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{file}"
    req = urllib.request.Request(url, headers={"User-Agent": "job-hunt-agent"})
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as r:
        return r.read().decode("utf-8", "replace")

# Markdown link -> (text, url); plain text passes through.
LINK = re.compile(r"\[(.*?)\]\((.*?)\)")

def cell_parts(cell):
    m = LINK.search(cell)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return cell.strip(), ""

def parse_table(md):
    """Yield dict rows from every pipe table in the markdown."""
    rows = []
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or all(set(c) <= set("-: ") for c in cells):
            continue  # separator row
        low = [c.lower() for c in cells]
        if "company" in low and "role" in " ".join(low):
            continue  # header row
        if len(cells) < 3:
            continue
        company, _ = cell_parts(cells[0])
        role_text, role_url = cell_parts(cells[1])
        location, _ = cell_parts(cells[2])
        age_raw, _ = cell_parts(cells[3]) if len(cells) > 3 else ("", "")
        if not (company and role_text):
            continue
        rows.append({"company": company, "role": role_text, "url": role_url,
                     "location": location, "age": age_raw,
                     "age_days": age_to_days(age_raw)})
    return rows

def age_to_days(s):
    """Parse the listing 'Age' cell ('0d','3d','2w','1mo','1h','1y') to days. None if unknown."""
    if not s:
        return None
    m = re.match(r"\s*(\d+)\s*(h|hr|hrs|hour|hours|d|day|days|w|wk|week|weeks|mo|mon|month|months|y|yr|year|years)?",
                 s.strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = (m.group(2) or "d")[0:2]
    if unit.startswith("h"):
        return n / 24.0
    if unit.startswith("w"):
        return n * 7
    if unit == "mo" or unit.startswith("mo"):
        return n * 30
    if unit.startswith("y"):
        return n * 365
    return float(n)  # days

def _strip_tags(s):
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()

def parse_html_table(html_text):
    """Parse an HTML <table> listing (e.g. SimplifyJobs). Columns:
    Company | Role | Location | Application | Age. The Application cell's first
    non-simplify.jobs href is the real ATS URL, so no redirect resolution is needed.
    '↳' in the company cell means 'same company as the row above'."""
    rows, prev_company = [], ""
    for tr in re.findall(r"<tr>(.*?)</tr>", html_text, re.DOTALL):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
        if len(tds) < 4:
            continue
        company = _strip_tags(tds[0])
        if company in ("", "↳") or "8618" in company:
            company = prev_company
        else:
            prev_company = company
        role = _strip_tags(tds[1])
        location = _strip_tags(tds[2])
        hrefs = re.findall(r'href="([^"]+)"', tds[3])
        url = next((h for h in hrefs if "simplify.jobs" not in h), hrefs[0] if hrefs else "")
        age_raw = _strip_tags(tds[4]) if len(tds) > 4 else ""
        if not (company and role):
            continue
        rows.append({"company": company, "role": role, "url": url,
                     "location": location, "age": age_raw,
                     "age_days": age_to_days(age_raw)})
    return rows

def role_type_match(role, role_types):
    """Short tokens (<=3 chars: ai/ml/swe) need a full-word match so they don't hit
    'maintenance'/'html'. Longer tokens prefix-match so 'software engineer' also catches
    'software engineering'."""
    role_l = role.lower()
    for rt in role_types:
        rt = rt.lower().strip()
        boundary = r"\b" if len(rt) <= 3 else ""
        if re.search(r"\b" + re.escape(rt) + boundary, role_l):
            return True
    return False

def matches(row, prefs):
    company_l = row["company"].lower()
    role_l = row["role"].lower()
    loc = row["location"].lower()
    for bad in prefs.get("drop_if_role_contains", []):
        if bad.lower() in role_l or bad in row["role"]:
            return False
    # Hard no: defense / clearance keywords anywhere in the role title.
    for kw in prefs.get("exclude_role_keywords", []):
        if kw.lower() in role_l:
            return False
    # Hard no: excluded companies (incl. defense) and companies already applied to.
    excluded = [c.lower() for c in prefs.get("exclude_companies", [])]
    excluded += [c.lower() for c in prefs.get("defense_companies", [])]
    excluded += [c.lower() for c in prefs.get("already_applied", [])]
    if company_l in excluded:
        return False
    if not role_type_match(row["role"], prefs.get("role_types", [])):
        return False
    accept = [a.lower() for a in prefs["locations"].get("accept", [])]
    loc_ok = any(a in loc for a in accept)
    if prefs["locations"].get("accept_metros") and ("," in loc or "hybrid" in loc):
        loc_ok = True  # let the Matcher's eligibility gate make the final call
    if not loc_ok:
        return False
    return True

def row_hash(row):
    key = f"{row['company']}|{row['role']}|{row['location']}".lower()
    return hashlib.sha1(key.encode()).hexdigest()[:16]

# --- Tracker dedup: skip positions already in the applications tracker -------------
ROLE_STOP = {"intern", "internship", "summer", "winter", "fall", "spring", "co", "op",
             "coop", "2025", "2026", "2027", "2028", "the", "a", "an", "and", "of", "for",
             "software", "engineer", "engineering", "swe", "start", "undergraduate"}

def _norm_company(c):
    c = re.sub(r"\(.*?\)", "", c)                 # drop parentheticals
    return re.sub(r"[^a-z0-9]", "", c.lower())

def _norm_role(r):
    # Keep parenthetical team tags (e.g. "(AML-Ark)") — they distinguish roles at one company.
    toks = re.split(r"[^a-z0-9]+", r.lower())
    return {t for t in toks if t and not t.isdigit() and t not in ROLE_STOP}

def load_applied(tracker_path):
    """Parse the markdown tracker into a list of (company_norm, role_tokens)."""
    applied = []
    p = os.path.expanduser(tracker_path)
    if not os.path.exists(p):
        return applied
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2 or cells[0].lower() == "company":
                continue
            if all(set(c) <= set("-: ") for c in cells):
                continue
            company = re.sub(r"^[—\-\s]+", "", cells[0])   # strip leading em-dash markers
            cn = _norm_company(company)
            if cn:
                applied.append((cn, _norm_role(cells[1])))
    return applied

def is_tracked(row, applied):
    cn, rt = _norm_company(row["company"]), _norm_role(row["role"])
    for tc, tr in applied:
        if tc != cn:
            continue
        if rt == tr:
            return True
        if not rt and not tr:        # both generic (e.g. plain "SWE Intern") at same company
            return True
        if not rt or not tr:         # one generic, one specific -> different positions
            continue
        inter = len(rt & tr)
        # Overlap coefficient handles verbose vs abbreviated titles (MLE vs Machine Learning
        # Engineer); require >=2 shared distinctive tokens to avoid single-word false hits.
        if inter >= 2 and inter / min(len(rt), len(tr)) >= 0.6:
            return True
        if inter / (len(rt | tr)) >= 0.5:   # plain Jaccard as a second net
            return True
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/preferences.yaml")
    ap.add_argument("--max-age-days", type=float, default=None,
                    help="Only roles posted within this many days (e.g. 1 = past day, 7 = past week).")
    ap.add_argument("--ignore-seen", action="store_true",
                    help="Query mode: don't dedup against seen.json and don't record results. "
                         "Use for ad-hoc 'scan the past day' queries.")
    args = ap.parse_args()
    prefs = load_yaml(ROOT / args.config)

    STATE.mkdir(exist_ok=True)
    seen_path = STATE / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()

    # Tracker is the source of truth for already-applied / already-drafted positions
    # (covers manual applications and the inbox sweep). Filter them out at scan time.
    applied = load_applied(prefs["tracker_path"]) if prefs.get("tracker_path") else []

    new_rows, scanned, passed, aged_out, tracked = [], 0, 0, 0, 0
    for src in prefs["sources"].get("github_repos", []):
        md = fetch(src["repo"], src.get("branch", "main"), src.get("file", "README.md"))
        rows = parse_table(md)
        if not rows and "<td" in md:      # HTML-table repos (e.g. SimplifyJobs)
            rows = parse_html_table(md)
        for row in rows:
            scanned += 1
            if not matches(row, prefs):
                continue
            if applied and is_tracked(row, applied):
                tracked += 1
                continue
            if args.max_age_days is not None:
                # Unknown age is excluded from an explicit time window.
                if row["age_days"] is None or row["age_days"] > args.max_age_days:
                    aged_out += 1
                    continue
            passed += 1
            h = row_hash(row)
            if not args.ignore_seen and h in seen:
                continue
            row["hash"] = h
            row["source"] = src["repo"]
            new_rows.append(row)
            seen.add(h)

    if not args.ignore_seen:
        seen_path.write_text(json.dumps(sorted(seen)))
        with open(STATE / "queue.jsonl", "a") as f:
            for row in new_rows:
                f.write(json.dumps(row) + "\n")

    print(json.dumps({"scanned": scanned, "passed_filters": passed,
                      "already_tracked": tracked, "aged_out": aged_out,
                      "new": len(new_rows), "max_age_days": args.max_age_days,
                      "new_rows": new_rows}, indent=2))

if __name__ == "__main__":
    main()
