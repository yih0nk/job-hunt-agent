"""API key storage in the OS keychain (macOS Keychain, Windows Credential Manager,
Secret Service on Linux). Falls back to the local database when no keychain is usable,
or when JOBHUNT_KEYRING=0 (tests)."""
from __future__ import annotations

import os
import threading

SERVICE = "job-hunt-agent"
USER = "anthropic-api-key"

_cache: dict[str, str | None] = {}
_lock = threading.Lock()


def _keyring():
    if os.environ.get("JOBHUNT_KEYRING", "1") == "0":
        return None
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
        return keyring
    except Exception:
        return None


def available() -> bool:
    return _keyring() is not None


def get() -> str | None:
    with _lock:
        if "key" in _cache:
            return _cache["key"]
        kr = _keyring()
        try:
            val = kr.get_password(SERVICE, USER) if kr else None
        except Exception:
            val = None
        _cache["key"] = val
        return val


def put(value: str) -> bool:
    """Store (or with "" delete) the key. Returns False if no keychain is usable."""
    kr = _keyring()
    if not kr:
        return False
    try:
        if value:
            kr.set_password(SERVICE, USER, value)
        else:
            try:
                kr.delete_password(SERVICE, USER)
            except Exception:
                pass
    except Exception:
        return False
    with _lock:
        _cache["key"] = value or None
    return True
