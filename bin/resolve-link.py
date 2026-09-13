#!/usr/bin/env python3
"""Resolve an aggregator/tracking link (e.g. zapply.jobs) to the real ATS URL.

Follows HTTP redirects and returns the final landing URL plus a best-guess ATS
vendor (greenhouse / lever / workday / ashby / ...). If the link cannot be
resolved, the Applier should fall back to a web search of "<company> <role>".

Usage:
    python3 bin/resolve-link.py "https://zapply.jobs/l/d/...."
"""
import ssl, sys, urllib.request

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
    final, vendor, err = resolve(sys.argv[1])
    # If we ended on a known ATS vendor, it's resolved. Otherwise (still on an aggregator,
    # or a generic landing page) the Applier must fall back to a web search.
    stuck = any(a in final for a in AGGREGATORS)
    needs_search = err is not None or vendor is None or stuck
    import json
    print(json.dumps({"final_url": final, "vendor": vendor, "error": err,
                      "needs_search": needs_search}, indent=2))
