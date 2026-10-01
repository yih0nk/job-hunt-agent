"""Cheap deterministic filters, applied before anything costs an LLM call.

Titles are strict (a role must match one of the user's keywords). Locations are soft:
a role is dropped only when its location clearly names places outside what the user
accepts. Empty, multi-site, and unparseable locations pass through to the scorer.
"""
from __future__ import annotations

import hashlib
import re

from .models import Preferences


def job_id(company: str, title: str, location: str) -> str:
    key = f"{company}|{title}|{location}".lower()
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def keyword_match(text: str, keywords: list[str]) -> bool:
    """Short keywords (<=3 chars: ai/ml/swe) need whole-word matches; longer ones
    prefix-match so 'software engineer' also catches 'software engineering'."""
    t = text.lower()
    for k in keywords:
        k = k.lower().strip()
        if not k:
            continue
        end = r"\b" if len(k) <= 3 else ""
        if re.search(r"\b" + re.escape(k) + end, t):
            return True
    return False


def company_listed(company: str, names: list[str]) -> bool:
    c = company.lower()
    return any(n.strip() and re.search(r"(?<![a-z0-9])" + re.escape(n.lower().strip()) + r"(?![a-z0-9])", c)
               for n in names)


def loc_match(loc: str, accept: list[str]) -> bool:
    """Long tokens match case-insensitively. Short tokens (SF, LA, NYC) match
    case-sensitively and must not be followed by a lowercase letter, so run-together
    cells like 'SFNYC' hit while 'Lakewood' does not hit 'LA'."""
    loc_l = loc.lower()
    for a in accept:
        a = a.strip()
        if not a:
            continue
        if len(a) > 3:
            if a.lower() in loc_l:
                return True
        elif re.search(r"\b" + re.escape(a.upper()) + r"(?![a-z])", loc):
            return True
    return False


US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia", "WI": "wisconsin",
    "WY": "wyoming", "DC": "district of columbia"}

# Places -> country. Deliberately partial: unknown places are never dropped.
FOREIGN = {
    "canada": ["canada", "ontario", "toronto", "vancouver", "montreal", "british columbia",
               "quebec", "alberta", "waterloo", "ottawa", "calgary"],
    "united kingdom": ["united kingdom", "uk", "london", "england", "scotland", "edinburgh",
                       "manchester", "cambridge, uk"],
    "ireland": ["ireland", "dublin"], "germany": ["germany", "berlin", "munich", "hamburg"],
    "france": ["france", "paris"], "netherlands": ["netherlands", "amsterdam", "rotterdam"],
    "switzerland": ["switzerland", "zurich", "geneva"],
    "india": ["india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi", "chennai"],
    "china": ["china", "shanghai", "beijing", "shenzhen"], "hong kong": ["hong kong"],
    "singapore": ["singapore"], "japan": ["japan", "tokyo"], "south korea": ["korea", "seoul"],
    "taiwan": ["taiwan", "taipei"], "israel": ["israel", "tel aviv"],
    "australia": ["australia", "sydney", "melbourne"], "brazil": ["brazil", "são paulo", "sao paulo"],
    "mexico": ["mexico"], "poland": ["poland", "warsaw", "krakow"], "spain": ["spain", "madrid", "barcelona"],
    "italy": ["italy", "milan"], "sweden": ["sweden", "stockholm"], "denmark": ["denmark", "copenhagen"],
    "finland": ["finland", "helsinki"], "norway": ["norway", "oslo"], "portugal": ["portugal", "lisbon"],
    "romania": ["romania", "bucharest"], "czechia": ["czech", "prague"],
    "philippines": ["philippines", "manila"], "vietnam": ["vietnam"], "uae": ["uae", "dubai"],
}
AMBIGUOUS = re.compile(r"locations|multiple|various|several|anywhere|hybrid|nationwide|tbd|"
                       r"flexible|north america", re.I)
REMOTE = re.compile(r"\bremote\b", re.I)


def _countries_named(loc: str) -> set[str]:
    loc_l = loc.lower()
    found = set()
    if re.search(r"united states|\busa?\b", loc_l):
        found.add("united states")
    for ab, name in US_STATES.items():
        if re.search(r"(?<![A-Za-z])" + ab + r"(?![A-Za-z])", loc) or name in loc_l:
            found.add("united states")
            break
    for country, places in FOREIGN.items():
        if any(re.search(r"\b" + re.escape(p) + r"\b", loc_l) for p in places):
            found.add(country)
    return found


def location_ok(loc: str, prefs: Preferences) -> bool:
    if not loc or not loc.strip():
        return True
    if loc_match(loc, prefs.locations):
        return True
    named = _countries_named(loc)
    accepted = {c.lower() for c in prefs.countries}
    if REMOTE.search(loc):
        # "London (Remote)" is remote from another country; only accept remote in accepted countries.
        return prefs.remote_ok and (not named or not accepted or bool(named & accepted))
    if AMBIGUOUS.search(loc):
        return True
    if prefs.locations:
        # The user named specific places. Anything else that is a recognizable place is out.
        return not named
    if not named or not accepted:
        return True
    return bool(named & accepted)


# Company boards list every level; curated listing repos are already one level, and
# their titles often omit "intern". So the level check only applies to company boards.
LEVEL_TITLE = {
    "internship": re.compile(r"\b(intern|internship|co-?op|apprentice|placement|summer student)", re.I),
    "new_grad": re.compile(r"\b(new grad|graduate|early career|university|campus|entry|junior|associate)|\b(i|1)\b", re.I),
}
BOARD_KINDS = {"greenhouse", "lever", "ashby"}


def passes(row: dict, prefs: Preferences) -> tuple[bool, str]:
    """Return (ok, reason_if_dropped)."""
    title = row["title"]
    if prefs.exclude_title_keywords and keyword_match(title, prefs.exclude_title_keywords):
        return False, "title_excluded"
    if prefs.exclude_companies and company_listed(row["company"], prefs.exclude_companies):
        return False, "company_excluded"
    if prefs.role_keywords and not keyword_match(title, prefs.role_keywords):
        return False, "title_not_matched"
    level_re = LEVEL_TITLE.get(prefs.level)
    if level_re and row.get("source_kind") in BOARD_KINDS and not level_re.search(title):
        return False, "level"
    if not location_ok(row.get("location", ""), prefs):
        return False, "location"
    if prefs.max_age_days is not None and row.get("age_days") is not None \
            and row["age_days"] > prefs.max_age_days:
        return False, "too_old"
    return True, ""


# --- Position-level dedup against what's already drafted/applied -------------------

ROLE_STOP = {"intern", "internship", "summer", "winter", "fall", "spring", "co", "op", "coop",
             "the", "a", "an", "and", "of", "for", "software", "engineer", "engineering", "swe",
             "start", "undergraduate", "new", "grad", "graduate", "i", "ii", "iii", "junior", "entry", "level"}


def _norm_company(c: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", c.lower()))


def _norm_role(r: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", r.lower()) if t and not t.isdigit() and t not in ROLE_STOP}


def same_position(a: tuple[str, str], b: tuple[str, str]) -> bool:
    if _norm_company(a[0]) != _norm_company(b[0]):
        return False
    ra, rb = _norm_role(a[1]), _norm_role(b[1])
    if ra == rb:
        return True
    if not ra or not rb:
        return False
    inter = len(ra & rb)
    return (inter >= 2 and inter / min(len(ra), len(rb)) >= 0.6) or inter / len(ra | rb) >= 0.5
