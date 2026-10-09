"""Stdlib HTTP helpers with a portable CA bundle."""
import html
import json
import re
import ssl
import urllib.request

UA = "Mozilla/5.0 (job-hunt-agent; +https://github.com/yih0nk/job-hunt-agent)"


def ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
        return r.read().decode("utf-8", "replace")


def fetch_json(url: str, timeout: int = 30):
    return json.loads(fetch(url, timeout))


def fetch_json_post(url: str, body: dict, timeout: int = 30):
    """POST a JSON body and parse the JSON reply (Workday's listing API is POST-only)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def final_url(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
        return r.geturl()


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()
