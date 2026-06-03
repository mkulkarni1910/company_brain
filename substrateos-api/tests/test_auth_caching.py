"""Per-request auth overhead fixes: JWKS caching + pilot group-expansion skip."""
from types import SimpleNamespace

import pytest

import app.auth as auth


class _Resp:
    def __init__(self, keys):
        self._keys = keys

    def json(self):
        return {"keys": self._keys}


def test_jwks_cached_and_refetched_on_force(monkeypatch) -> None:
    auth._JWKS_CACHE.clear()
    calls = {"n": 0}

    def fake_get(url, timeout=5.0):
        calls["n"] += 1
        return _Resp([{"kid": "k1"}])

    monkeypatch.setattr(auth.httpx, "get", fake_get)
    a = auth._get_jwks("t1")
    b = auth._get_jwks("t1")
    assert a is b and calls["n"] == 1  # second call served from cache
    auth._get_jwks("t1", force=True)
    assert calls["n"] == 2  # force refetch (key rotation)


@pytest.mark.asyncio
async def test_expand_groups_skipped_in_pilot(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(pilot_single_tenant=True))
    constructed = {"cred": False}

    class _Cred:
        def __init__(self):
            constructed["cred"] = True

    monkeypatch.setattr(auth, "DefaultAzureCredential", _Cred)
    out = await auth._expand_groups("u1", "t1")
    assert out == set()
    assert constructed["cred"] is False  # no Graph credential constructed → no round-trip
