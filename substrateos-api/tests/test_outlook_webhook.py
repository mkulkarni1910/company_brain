"""Outlook webhook + maintain endpoints, and the outlook provider connect/callback."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import callback_router, router
from app.config import get_settings
from app.connectors.store import ConnectionStore
from app.deps import (
    get_acl_store,
    get_connection_store,
    get_ingest_pipeline,
    get_subscription_store,
)


class FakePipeline:
    def __init__(self):
        self.processed = []

    async def process(self, doc):
        self.processed.append(doc)


class StateRedis:
    def __init__(self):
        self.kv = {}

    async def set(self, name, value, ex=None):
        self.kv[name] = value

    async def getdel(self, name):
        return self.kv.pop(name, None)

    async def hset(self, name, key, value):
        self.kv.setdefault(name, {})[key] = value

    async def hgetall(self, name):
        return dict(self.kv.get(name, {}))


def _build_app(monkeypatch, **overrides):
    monkeypatch.setenv("ADMIN_API_KEY", "k")
    monkeypatch.setenv("SUBSTRATEOS_TENANT_ID", "t-eval")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(router)
    app.include_router(callback_router)
    app.dependency_overrides[get_ingest_pipeline] = lambda: FakePipeline()
    app.dependency_overrides[get_acl_store] = lambda: None
    for dep, val in overrides.items():
        app.dependency_overrides[dep] = lambda v=val: v
    return app


@pytest.fixture(autouse=True)
def _clear():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_webhook_validation_handshake_echoes_token(monkeypatch):
    app = _build_app(monkeypatch, **{get_connection_store.__name__: None})
    app.dependency_overrides[get_connection_store] = lambda: ConnectionStore(client=StateRedis())
    client = TestClient(app)
    r = client.post("/admin/connections/webhook?validationToken=abc%20123")
    assert r.status_code == 200
    assert r.text == "abc 123"


def test_webhook_notification_returns_202(monkeypatch):
    app = _build_app(monkeypatch)
    app.dependency_overrides[get_connection_store] = lambda: ConnectionStore(client=StateRedis())
    client = TestClient(app)
    r = client.post("/admin/connections/webhook",
                    json={"value": []})
    assert r.status_code == 202


def test_maintain_requires_admin_key(monkeypatch):
    app = _build_app(monkeypatch)
    app.dependency_overrides[get_connection_store] = lambda: ConnectionStore(client=StateRedis())
    app.dependency_overrides[get_subscription_store] = lambda: None
    client = TestClient(app)
    assert client.post("/admin/connections/maintain").status_code == 403


def test_maintain_runs_with_no_connections(monkeypatch):
    app = _build_app(monkeypatch)
    app.dependency_overrides[get_connection_store] = lambda: ConnectionStore(client=StateRedis())
    from app.connectors.subscriptions import SubscriptionStore
    app.dependency_overrides[get_subscription_store] = lambda: SubscriptionStore(client=StateRedis())
    client = TestClient(app)
    r = client.post("/admin/connections/maintain", headers={"x-admin-key": "k"})
    assert r.status_code == 200
    assert r.json() == {"renewed": 0, "created": 0, "deleted": 0, "ingested": 0}


def test_oauth_connect_outlook_mail_returns_auth_url(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
    app = _build_app(monkeypatch)
    app.dependency_overrides[get_connection_store] = lambda: ConnectionStore(client=StateRedis())
    client = TestClient(app)
    r = client.post("/admin/connections/oauth/connect?provider=outlook_mail",
                    headers={"x-admin-key": "k"})
    assert r.status_code == 200
    assert "adminconsent" in r.json()["auth_url"]


@pytest.mark.asyncio
async def test_callback_outlook_calendar_creates_connection(monkeypatch):
    async def _noop(self, **kw):
        return None

    monkeypatch.setattr("app.connectors.sync.SyncRunner.run", _noop)
    store = ConnectionStore(client=StateRedis())
    await store.put_oauth_state("st1", "t-eval", "outlook_calendar")

    app = _build_app(monkeypatch)
    app.dependency_overrides[get_connection_store] = lambda: store
    app.dependency_overrides[get_subscription_store] = lambda: None  # skips bootstrap
    client = TestClient(app)
    r = client.get(
        "/admin/connections/oauth/callback",
        params={"state": "st1", "tenant": "org-tenant", "admin_consent": "True"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "connected=outlook_calendar" in r.headers["location"]
    conns = await store.list_connections("t-eval")
    assert any(c.type == "outlook_calendar" for c in conns)
