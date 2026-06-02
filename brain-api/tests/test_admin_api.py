import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import callback_router, router
from app.config import get_settings
from app.connectors.store import ConnectionStore
from tests.test_connector_store import FakeRedis


class StateRedis(FakeRedis):
    """FakeRedis + getdel, for the OAuth state one-shot."""
    async def getdel(self, name):
        return self.kv.pop(name, None) if hasattr(self, "kv") else None


def _build_app(monkeypatch, **state):
    monkeypatch.setenv("ADMIN_API_KEY", "k")
    monkeypatch.setenv("BRAIN_TENANT_ID", "t-eval")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(router)
    app.include_router(callback_router)
    for key, val in state.items():
        setattr(app.state, key, val)
    return app


@pytest.fixture(autouse=True)
def _clear_settings():
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_admin_requires_key(monkeypatch):
    app = _build_app(monkeypatch, connection_store=ConnectionStore(client=FakeRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/connections")
        assert r.status_code == 403
        r = await c.get("/admin/connections", headers={"x-admin-key": "k"})
        assert r.status_code == 200 and r.json() == []


@pytest.mark.asyncio
async def test_sites_degrades_empty(monkeypatch):
    class FakeSP:
        async def list_sites(self):
            return []
    app = _build_app(monkeypatch, sharepoint=FakeSP())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/sharepoint/sites", headers={"x-admin-key": "k"})
        assert r.status_code == 200 and r.json() == []


@pytest.mark.asyncio
async def test_stats_shape(monkeypatch):
    class FakeMetrics:
        async def active_users_7d(self, t): return 3
        async def queries_last_7d(self, t): return 42
    class FakeSearch:
        async def count_docs(self, *, tenant_id): return 100
    app = _build_app(
        monkeypatch,
        connection_store=ConnectionStore(client=FakeRedis()),
        metrics_store=FakeMetrics(),
        ai_search=FakeSearch(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/stats", headers={"x-admin-key": "k"})
        assert r.status_code == 200
        body = r.json()
        assert body["items_indexed"] == 100 and body["sources_live"] == 0
        assert body["active_users"] == 3 and body["queries_7d"] == 42
        assert body["needs_attention"][0]["where"] == "Data Sources"


@pytest.mark.asyncio
async def test_sharepoint_connect_returns_consent_url(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
    app = _build_app(monkeypatch, connection_store=ConnectionStore(client=StateRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/admin/connections/sharepoint/connect")).status_code == 403
        r = await c.post("/admin/connections/sharepoint/connect", headers={"x-admin-key": "k"})
        assert r.status_code == 200
        assert "adminconsent" in r.json()["auth_url"] and "client_id=cid" in r.json()["auth_url"]


@pytest.mark.asyncio
async def test_sharepoint_callback_valid_and_invalid(monkeypatch):
    async def _noop(self, **kw):  # don't run a real sync/network in the test
        return None
    monkeypatch.setattr("app.connectors.sync.SyncRunner.run", _noop)
    store = ConnectionStore(client=StateRedis())
    app = _build_app(monkeypatch, connection_store=store)
    app.dependency_overrides = {}
    from app.deps import get_ingest_pipeline
    app.dependency_overrides[get_ingest_pipeline] = lambda: object()
    await store.put_oauth_state("good", "t-eval")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # invalid state → error redirect, no connection
        r = await c.get("/admin/connections/sharepoint/callback",
                        params={"state": "bad", "tenant": "T", "admin_consent": "true"})
        assert r.status_code == 302 and "error=oauth" in r.headers["location"]
        assert await store.list_connections("t-eval") == []
        # valid state → connection created + connected redirect
        r = await c.get("/admin/connections/sharepoint/callback",
                        params={"state": "good", "tenant": "tenantX", "admin_consent": "true"})
        assert r.status_code == 302 and "connected=sharepoint" in r.headers["location"]
        conns = await store.list_connections("t-eval")
        assert len(conns) == 1 and conns[0].connected_tenant_id == "tenantX"
