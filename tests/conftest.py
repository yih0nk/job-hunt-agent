import pytest


@pytest.fixture(autouse=True)
def _no_real_keychain(monkeypatch):
    """Tests never touch the developer's real OS keychain."""
    monkeypatch.setenv("JOBHUNT_KEYRING", "0")
    from jobhunt import keystore
    keystore._cache.clear()
