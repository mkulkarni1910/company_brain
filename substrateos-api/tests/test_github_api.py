"""GitHub OAuth + run-action endpoints."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.api.github as github_api
from app.api.github import router
from app.connectors.github_store import GithubStore
from app.deps import get_github_flow, get_github_store
from app.workflows.github_pr import ActionResult


def _app(store: GithubStore, flow=None) -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    a.dependency_overrides[get_github_store] = lambda: store
    a.dependency_overrides[get_github_flow] = lambda: flow
    return a


def _client(a: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=a), base_url="http://t")


@pytest.mark.asyncio
async def test_start_redirects_to_github_for_valid_state(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    store = GithubStore(client=None, force_memory=True)
    state = await store.mint_connect_state("t-test", "tom@x")
    async with _client(_app(store)) as c:
        r = await c.get(f"/auth/github/start?s={state}")
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://github.com/login/oauth/authorize")
    assert "client_id=cid" in loc and f"state={state}" in loc and "scope=repo" in loc


@pytest.mark.asyncio
async def test_start_unknown_state_404():
    store = GithubStore(client=None, force_memory=True)
    async with _client(_app(store)) as c:
        r = await c.get("/auth/github/start?s=bogus")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_callback_exchanges_and_stores_token(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    store = GithubStore(client=None, force_memory=True)
    state = await store.mint_connect_state("t-test", "tom@x")

    async def fake_exchange(**kw):
        assert kw["code"] == "c0de"
        return "gho_new"
    monkeypatch.setattr(github_api, "exchange_code", fake_exchange)

    async with _client(_app(store)) as c:
        r = await c.get(f"/auth/github/callback?code=c0de&state={state}")
    assert r.status_code == 200 and "Connected" in r.text
    assert await store.get_user_token("t-test", "tom@x") == "gho_new"
    # state is one-shot:
    async with _client(_app(store)) as c:
        r2 = await c.get(f"/auth/github/callback?code=c0de&state={state}")
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_run_action_routes_to_flow(monkeypatch):
    # enable debug auth — conftest autouse already sets ENABLE_DEBUG_AUTH=true
    # debug principal "t-eval,u-demo,t-eval:everyone" → email "u-demo@debug"
    class _Flow:
        async def confirm(self, run_id, *, actor_email, actor_name):
            assert run_id == "RB-9"
            assert actor_email == "u-demo@debug"
            return ActionResult(ok=True, status="completed",
                                pr_url="https://github.com/o/r/pull/3")
        async def cancel(self, run_id, *, actor_email, actor_name):
            return ActionResult(ok=True, status="cancelled")

    store = GithubStore(client=None, force_memory=True)
    async with _client(_app(store, flow=_Flow())) as c:
        r = await c.post("/workflows/runs/RB-9/action", json={"action": "create"},
                         headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["pr_url"].endswith("/pull/3")


@pytest.mark.asyncio
async def test_run_action_503_when_flow_missing():
    store = GithubStore(client=None, force_memory=True)
    async with _client(_app(store, flow=None)) as c:
        r = await c.post("/workflows/runs/RB-9/action", json={"action": "create"},
                         headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_run_action_cancel_routes():
    class _Flow:
        async def confirm(self, run_id, *, actor_email, actor_name):
            return ActionResult(ok=True, status="completed", pr_url="https://github.com/o/r/pull/1")
        async def cancel(self, run_id, *, actor_email, actor_name):
            assert run_id == "RB-9"
            return ActionResult(ok=True, status="cancelled")

    store = GithubStore(client=None, force_memory=True)
    async with _client(_app(store, flow=_Flow())) as c:
        r = await c.post("/workflows/runs/RB-9/action", json={"action": "cancel"},
                         headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["status"] == "cancelled"
