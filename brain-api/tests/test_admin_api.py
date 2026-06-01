import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import router
from app.config import get_settings
from app.connectors.store import ConnectionStore
from tests.test_connector_store import FakeRedis


def _build_app(monkeypatch, **state):
    monkeypatch.setenv("ADMIN_API_KEY", "k")
    monkeypatch.setenv("BRAIN_TENANT_ID", "t-eval")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(router)
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
