"""Company logos for the UI, cached on disk so each company is looked up once.

Domain: the posting's own host when it isn't an ATS or aggregator, else Clearbit's public
company autocomplete (accepted only on an exact name match). Image, sharpest first: the
site's apple-touch-icon, the largest icon its homepage declares (SVG preferred), then
Google's favicon service as a last resort, since it upscales small favicons and blurs.
Only company names and domains leave the machine. Misses are remembered for two weeks so the UI falls back to the
letter avatar without retrying on every render.
"""
from __future__ import annotations

import json
import re
import struct
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from .net import ssl_ctx
from .paths import data_dir

MISS_TTL = 14 * 86400
# Some sites refuse non-browser user agents for their homepage.
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MIN_PX = 96     # below this a logo looks soft in a 48px avatar on a retina screen
TYPES = {"png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg", "webp": "image/webp"}
# Hosts that belong to an ATS or job aggregator, not the hiring company.
NOT_COMPANY = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "workday.com",
               "smartrecruiters.com", "icims.com", "workable.com", "jobvite.com", "taleo.net",
               "successfactors.com", "bamboohr.com", "recruitee.com", "breezy.hr", "rippling.com",
               "zapply.jobs", "simplify.jobs", "getro.com", "jobright.ai", "linkedin.com", "indeed.com",
               "wellfound.com", "ycombinator.com", "workatastartup.com", "github.com", "example.com",
               "earlycareerradar.com", "oraclecloud.com", "paylocity.com", "adp.com", "dayforcehcm.com")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _dir() -> Path:
    p = data_dir() / "logos" / "v2"   # v1 held blurry Google-only icons
    p.mkdir(parents=True, exist_ok=True)
    return p


def _key(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", clean_name(company).lower()).strip("-")[:60] or "unknown"


def clean_name(company: str) -> str:
    """Drop emoji / decorations listings add ("🔥Waymo", "Acme (YC W24)")."""
    name = re.sub(r"\(.*?\)", "", company)
    name = re.sub(r"[^\w&.,' -]", "", name, flags=re.UNICODE)
    return re.sub(r"\s+", " ", name).strip()


def _norm(s: str) -> str:
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|labs?|group)\b\.?", "", s.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def _get(url: str, timeout: int = 8) -> tuple[int, bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
            return r.status, r.read(2_000_000), r.headers.get("content-type", ""), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, b"", "", url
    except Exception:
        return 0, b"", "", url


def _kind(body: bytes, ctype: str) -> Optional[str]:
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if body[:3] == b"\xff\xd8\xff":
        return "jpg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    head = body[:400].lstrip().lower()
    if "svg" in ctype or head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in body[:2000].lower()):
        return "svg"
    return None


def _png_px(body: bytes) -> int:
    return min(struct.unpack(">II", body[16:24])) if body[:8] == b"\x89PNG\r\n\x1a\n" else 0


def _good(body: bytes, ctype: str, min_px: int = MIN_PX) -> Optional[str]:
    """Return the image kind if it's a usable, sharp-enough logo."""
    k = _kind(body, ctype)
    if k == "svg":
        return k
    if k == "png" and _png_px(body) >= min_px:
        return k
    if k in ("jpg", "webp") and len(body) > 2000:
        return k
    return None


def domain_from_url(url: str) -> Optional[str]:
    host = urllib.parse.urlparse(url or "").hostname or ""
    if not host or any(host == d or host.endswith("." + d) for d in NOT_COMPANY):
        return None
    parts = host.split(".")
    # careers.acme.com -> acme.com; amazon.jobs stays as is.
    return ".".join(parts[-2:]) if len(parts) > 2 else host


def domain_from_name(company: str) -> Optional[str]:
    name = clean_name(company)
    if not name:
        return None
    status, body, _, _ = _get("https://autocomplete.clearbit.com/v1/companies/suggest?query=" + urllib.parse.quote(name))
    if status != 200:
        return None
    try:
        hits = json.loads(body)
    except ValueError:
        return None
    want = _norm(name)
    for h in hits:
        if _norm(h.get("name", "")) == want and h.get("domain"):
            return h["domain"]
    return None


LINK = re.compile(r"<link\b[^>]*>", re.I)


def _declared_icons(domain: str) -> list[tuple[int, str]]:
    """(size, absolute url) of icons the homepage declares, SVG ranked as infinitely sharp."""
    status, body, _, final = _get(f"https://{domain}/")
    if status != 200 or not body:
        status, body, _, final = _get(f"https://www.{domain}/")
    if status != 200:
        return []
    out = []
    for tag in LINK.findall(body.decode("utf-8", "replace")):
        rel = re.search(r"""rel=["']([^"']+)""", tag, re.I)
        href = re.search(r"""href=["']([^"']+)""", tag, re.I)
        if not rel or not href or "icon" not in rel.group(1).lower() or "mask" in rel.group(1).lower():
            continue
        url = urllib.parse.urljoin(final, href.group(1).replace("&amp;", "&"))
        size = re.search(r"""sizes=["'](\d+)x\d+""", tag, re.I)
        px = 10_000 if url.lower().split("?")[0].endswith(".svg") or "svg" in tag.lower() else int(size.group(1)) if size else 32
        if "apple-touch" in rel.group(1).lower() and not size:
            px = 180
        out.append((px, url))
    return sorted(out, reverse=True)


def _fetch_icon(domain: str) -> Optional[tuple[bytes, str]]:
    """Sharpest logo for a domain as (bytes, kind), or None."""
    for path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
        status, body, ctype, _ = _get(f"https://{domain}{path}")
        if status == 200 and (k := _good(body, ctype)):
            return body, k
    for px, url in _declared_icons(domain)[:4]:
        if px < MIN_PX:
            break
        status, body, ctype, _ = _get(url)
        if status == 200 and (k := _good(body, ctype)):
            return body, k
    # Last resort: Google's favicon service (404 for unknown domains). Accept small icons
    # here; the UI shows them at natural size rather than stretching them.
    status, body, ctype, _ = _get(f"https://www.google.com/s2/favicons?domain={urllib.parse.quote(domain)}&sz=256")
    if status == 200 and (k := _good(body, ctype, min_px=16)):
        return body, k
    return None


def _cached(key: str) -> Optional[tuple[bytes, str]]:
    for kind in TYPES:
        f = _dir() / f"{key}.{kind}"
        if f.exists():
            return f.read_bytes(), TYPES[kind]
    return None


def logo(company: str, url: str = "") -> Optional[tuple[bytes, str]]:
    """(bytes, media type) for the company's logo, or None. Cached: hits forever, misses
    for MISS_TTL."""
    key = _key(company)
    miss = _dir() / f"{key}.miss"
    if hit := _cached(key):
        return hit
    if miss.exists() and time.time() - miss.stat().st_mtime < MISS_TTL:
        return None
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:  # one lookup per company even when the inbox asks for it many times at once
        if hit := _cached(key):
            return hit
        found = None
        domain = domain_from_url(url)
        if domain:
            found = _fetch_icon(domain)
        if found is None:
            domain = domain_from_name(company)
            found = _fetch_icon(domain) if domain else None
        if found:
            body, kind = found
            (_dir() / f"{key}.{kind}").write_bytes(body)
            miss.unlink(missing_ok=True)
            return body, TYPES[kind]
        miss.touch()
        return None
