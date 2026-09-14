#!/usr/bin/env python3
"""Resolve an aggregator/tracking link (e.g. zapply.jobs) to the real ATS URL.

Two strategies, tried in order:
  1. Slug decode. zapply deep-links encode the ATS in the path:
     /l/d/<vendor>-<company>-<id>  ->  the real ATS URL. This is deterministic and
     needs no network, so it is tried first (zapply does client-side JS redirects
     that HTTP-following cannot reach).
  2. Redirect follow. For non-zapply aggregators, follow HTTP redirects.
If neither yields a known ATS vendor, needs_search=True and the Applier should fall
back to a web search of "<company> <role>".

Usage:
    python3 bin/resolve-link.py "https://zapply.jobs/l/d/...."
"""
import re, ssl, sys, urllib.parse, urllib.request

def ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

# Aggregator/tracking hosts that do client-side redirects HTTP-following can't reach.
AGGREGATORS = ["zapply.jobs", "simplify.jobs", "getro.com", "jobright.ai"]

VENDORS =["greenhouse.io", "lever.co", "myworkdayjobs.com", "ashbyhq.com",
           "smartrecruiters.com", "icims.com", "workable.com", "jobvite.com",
           "taleo.net", "successfactors.com"]

def decode_zapply_slug(url):
    """Reconstruct the real ATS URL from a zapply /l/d/<vendor>-<company>-<id> slug.
    Returns (url, vendor) or (None, None) if the vendor is unknown or unreconstructable
    (e.g. Workday, whose per-company subdomain is not encoded)."""
    if "zapply.jobs" not in url:
        return None, None
    path = urllib.parse.urlparse(url).path            # /l/d/ashby-circleback-<uuid>
    m = re.search(r"/l/d/([^/?]+)", path)
    if not m:
        return None, None
    slug = m.group(1)
    parts = slug.split("-")
    vendor = parts[0].lower()
    # bytedance-<numericid> has no company segment.
    if vendor == "bytedance":
        return f"https://joinbytedance.com/search/{parts[-1]}", "bytedance"
    if len(parts) < 3:
        return None, None
    company, rest = parts[1], "-".join(parts[2:])     # id may itself contain hyphens (UUIDs)
    if vendor == "ashby":
        return f"https://jobs.ashbyhq.com/{company}/{rest}", "ashbyhq.com"
    if vendor == "lever":
        return f"https://jobs.lever.co/{company}/{rest}", "lever.co"
    if vendor in ("gh", "greenhouse"):
        return f"https://boards.greenhouse.io/{company}/jobs/{rest}", "greenhouse.io"
    if vendor == "sr":                                # SmartRecruiters, id is numeric
        return f"https://jobs.smartrecruiters.com/{company}/{rest}", "smartrecruiters.com"
    # workday (wd/workday) subdomain is company-specific and not encoded -> can't rebuild.
    return None, None

def resolve(url, max_hops=8):
    seen = []
    for _ in range(max_hops):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 job-hunt-agent"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as r:
                final = r.geturl()
        except Exception as e:
            return url, None, f"error: {e}"
        seen.append(final)
        if final == url:
            break
        url = final
    vendor = next((v for v in VENDORS if v in url), None)
    return url, vendor, None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: resolve-link.py <url>")
    src = sys.argv[1]
    import json
    # Strategy 1: slug decode (deterministic, no network).
    decoded, vendor = decode_zapply_slug(src)
    if decoded:
        print(json.dumps({"final_url": decoded, "vendor": vendor, "error": None,
                          "needs_search": False, "method": "slug"}, indent=2))
        sys.exit(0)
    # Strategy 2: follow redirects.
    final, vendor, err = resolve(src)
    stuck = any(a in final for a in AGGREGATORS)
    needs_search = err is not None or vendor is None or stuck
    print(json.dumps({"final_url": final, "vendor": vendor, "error": err,
                      "needs_search": needs_search, "method": "redirect"}, indent=2))
