import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.surfaces import router
from app.connectors.store import ConnectionStore
from tests.test_connector_store import FakeRedis


def _build_app(store: ConnectionStore) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.connection_store = store
    return app


@pytest.mark.asyncio
async def test_surfaces_returns_default_list():
    app = _build_app(ConnectionStore(client=FakeRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/surfaces", headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    assert r.status_code == 200
    data = r.json()
    names = [s["name"] for s in data]
    assert "web" in names
    assert "api" in names
    assert "mcp" in names


@pytest.mark.asyncio
async def test_surfaces_no_admin_key_required():
    """Regular users must be able to call this endpoint without an admin key."""
    app = _build_app(ConnectionStore(client=FakeRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/surfaces", headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_surfaces_enabled_field_present():
    app = _build_app(ConnectionStore(client=FakeRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/surfaces", headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    for s in r.json():
        assert "name" in s
        assert "enabled" in s
