"""GET /me — signed-in identity (Entra name + Slack profile title) for the web UI."""
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import get_cache
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    async def get_json(self, key: str):
        return self.store.get(key)

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        self.store[key] = value


def _client_with(cache: FakeCache) -> TestClient:
    app.dependency_overrides[get_cache] = lambda: cache
    return TestClient(app)


def test_me_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/me").status_code == 401


def test_me_returns_identity_with_slack_title(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    get_settings.cache_clear()
    calls: list[tuple[str, dict]] = []

    async def fake_slack_get(token, method, params):
        calls.append((method, params))
        return {"ok": True, "user": {"profile": {"title": "Sales Lead — Central"}}}

    monkeypatch.setattr("app.api.me.slack_get", fake_slack_get)
    try:
        with _client_with(FakeCache()) as client:
            r = client.get("/me", headers=_HDR)
        assert r.status_code == 200
        body = r.json()
        assert body["display_name"] == "u-x"
        assert body["email"] == "u-x@debug"
        assert body["title"] == "Sales Lead — Central"
        assert calls == [("users.lookupByEmail", {"email": "u-x@debug"})]
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_me_title_served_from_cache(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    get_settings.cache_clear()
    calls: list[str] = []

    async def fake_slack_get(token, method, params):
        calls.append(method)
        return {"ok": True, "user": {"profile": {"title": "Sales Lead — Central"}}}

    monkeypatch.setattr("app.api.me.slack_get", fake_slack_get)
    cache = FakeCache()
    try:
        with _client_with(cache) as client:
            client.get("/me", headers=_HDR)
            r = client.get("/me", headers=_HDR)
        assert r.json()["title"] == "Sales Lead — Central"
        assert len(calls) == 1  # second request hit the cache
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_me_no_slack_token_means_no_title(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        with _client_with(FakeCache()) as client:
            r = client.get("/me", headers=_HDR)
        assert r.status_code == 200
        assert r.json()["title"] is None
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_me_slack_failure_is_fail_soft(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    get_settings.cache_clear()

    async def fake_slack_get(token, method, params):
        return None  # user not in Slack / API error

    monkeypatch.setattr("app.api.me.slack_get", fake_slack_get)
    try:
        with _client_with(FakeCache()) as client:
            r = client.get("/me", headers=_HDR)
        assert r.status_code == 200
        assert r.json()["title"] is None
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_me_reports_admin_for_admins_group_member(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        with _client_with(FakeCache()) as client:
            r = client.get("/me", headers={"x-debug-bypass-auth": "t-test,u-a,Admin"})
        assert r.status_code == 200
        assert r.json()["is_admin"] is True
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_me_reports_non_admin_for_regular_user(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        with _client_with(FakeCache()) as client:
            r = client.get("/me", headers=_HDR)
        assert r.status_code == 200
        assert r.json()["is_admin"] is False
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_me_empty_slack_title_is_null(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    get_settings.cache_clear()

    async def fake_slack_get(token, method, params):
        return {"ok": True, "user": {"profile": {"title": ""}}}

    monkeypatch.setattr("app.api.me.slack_get", fake_slack_get)
    try:
        with _client_with(FakeCache()) as client:
            r = client.get("/me", headers=_HDR)
        assert r.json()["title"] is None
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_me_reports_sme_membership() -> None:
    # Debug header lists group names directly — "Finance SME" grants is_sme.
    hdr = {"x-debug-bypass-auth": "t-test,u-deepa,t-test:everyone,Finance SME"}
    try:
        with _client_with(FakeCache()) as client:
            r = client.get("/me", headers=hdr)
            assert r.status_code == 200
            assert r.json()["is_sme"] is True
            r2 = client.get("/me", headers=_HDR)  # plain user: not an SME
            assert r2.json()["is_sme"] is False
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
