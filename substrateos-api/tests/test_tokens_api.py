import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.tokens import router as tokens_router
from app.config import get_settings
from app.domain.token import TokenCreated, TokenMeta


class FakeStore:
    def __init__(self):
        self._items: dict[str, TokenMeta] = {}
        self.created_plaintext = "sbx_live_secret"
    async def create(self, *, user, name):
        meta = TokenMeta(token_id="tk1", name=name, masked="sbx_live_••••cret",
                         created_at="2026-06-02T00:00:00+00:00")
        self._items["tk1"] = meta
        return meta, self.created_plaintext
    async def list(self, *, user):
        return list(self._items.values())
    async def revoke(self, *, user, token_id):
        return self._items.pop(token_id, None) is not None


@pytest.fixture
def client():
    get_settings().enable_debug_auth = True
    app = FastAPI()
    store = FakeStore()
    app.state.token_store = store
    app.include_router(tokens_router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://t"), store


_AUTH = {"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"}


@pytest.mark.asyncio
async def test_create_then_list_then_revoke(client) -> None:
    ac, _ = client
    async with ac:
        r = await ac.post("/tokens", json={"name": "laptop"}, headers=_AUTH)
        assert r.status_code == 200
        body = TokenCreated.model_validate(r.json())
        assert body.token == "sbx_live_secret"
        assert body.meta.name == "laptop"

        r = await ac.get("/tokens", headers=_AUTH)
        assert [m["name"] for m in r.json()] == ["laptop"]

        r = await ac.delete(f"/tokens/{body.meta.token_id}", headers=_AUTH)
        assert r.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_requires_auth(client) -> None:
    ac, _ = client
    async with ac:
        r = await ac.get("/tokens")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_pat_bearer_cannot_manage_tokens(client) -> None:
    # A PAT bearer reaches resolve_user WITHOUT a token_store here, so it is not a
    # valid principal for token management — falls through to JWT validation → 401.
    ac, _ = client
    async with ac:
        r = await ac.get("/tokens", headers={"Authorization": "Bearer sbx_live_anything"})
        assert r.status_code == 401
