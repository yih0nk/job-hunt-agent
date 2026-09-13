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
        if not (company and role_text):
            continue
        rows.append({"company": company, "role": role_text,
                     "url": role_url, "location": location})
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
    loc = row["location"].lower()
    for bad in prefs.get("drop_if_role_contains", []):
        if bad.lower() in row["role"].lower() or bad in row["role"]:
            return False
    if not role_type_match(row["role"], prefs.get("role_types", [])):
        return False
    accept = [a.lower() for a in prefs["locations"].get("accept", [])]
    loc_ok = any(a in loc for a in accept)
    if prefs["locations"].get("accept_metros") and ("," in loc or "hybrid" in loc):
        loc_ok = True  # let the Matcher's eligibility gate make the final call
    if not loc_ok:
        return False
    if row["company"].lower() in [c.lower() for c in prefs.get("exclude_companies", [])]:
        return False
    return True

def row_hash(row):
    key = f"{row['company']}|{row['role']}|{row['location']}".lower()
    return hashlib.sha1(key.encode()).hexdigest()[:16]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/preferences.yaml")
    args = ap.parse_args()
    prefs = load_yaml(ROOT / args.config)

    STATE.mkdir(exist_ok=True)
    seen_path = STATE / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()

    new_rows, scanned, passed = [], 0, 0
    for src in prefs["sources"].get("github_repos", []):
        md = fetch(src["repo"], src.get("branch", "main"), src.get("file", "README.md"))
        for row in parse_table(md):
            scanned += 1
            if not matches(row, prefs):
                continue
            passed += 1
            h = row_hash(row)
            if h in seen:
                continue
            row["hash"] = h
            row["source"] = src["repo"]
            new_rows.append(row)
            seen.add(h)

    seen_path.write_text(json.dumps(sorted(seen)))
    with open(STATE / "queue.jsonl", "a") as f:
        for row in new_rows:
            f.write(json.dumps(row) + "\n")

    print(json.dumps({"scanned": scanned, "passed_filters": passed,
                      "new": len(new_rows),
                      "new_rows": new_rows}, indent=2))

if __name__ == "__main__":
    main()
