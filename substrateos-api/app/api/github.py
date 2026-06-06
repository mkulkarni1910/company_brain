"""GitHub tool endpoints: per-user OAuth (start/callback) and the surface-agnostic
run action endpoint the web chat (and tests) use for Create PR / Cancel."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.connectors.github import GITHUB_OAUTH_AUTHORIZE, exchange_code
from app.deps import get_github_flow, get_github_store, get_token_store

router = APIRouter(tags=["github"])

_PAGE = """<!doctype html><html><head><title>SubstrateOS · GitHub</title>
<style>body{{font-family:system-ui;display:grid;place-items:center;height:100vh;margin:0;
background:#faf6ef;color:#1d1d1b}}div{{text-align:center;max-width:28rem}}</style></head>
<body><div><h2>{title}</h2><p>{body}</p></div></body></html>"""


@router.get("/auth/github/start")
async def github_oauth_start(s: str, github_store=Depends(get_github_store)):
    if github_store is None or await github_store.peek_connect_state(s) is None:
        raise HTTPException(status_code=404, detail="unknown or expired connect link")
    cfg = get_settings()
    params = urlencode({
        "client_id": cfg.github_client_id or "",
        "redirect_uri": f"{cfg.substrateos_api_base_url}/auth/github/callback",
        "scope": "repo",
        "state": s,
    })
    return RedirectResponse(f"{GITHUB_OAUTH_AUTHORIZE}?{params}")


@router.get("/auth/github/callback")
async def github_oauth_callback(code: str = "", state: str = "",
                                github_store=Depends(get_github_store)) -> HTMLResponse:
    consumed = await github_store.consume_connect_state(state) if github_store else None
    if consumed is None or not code:
        return HTMLResponse(_PAGE.format(
            title="Link expired", body="This connect link was already used or has expired — "
            "ask SubstrateOS for a PR again to get a fresh one."), status_code=400)
    tenant, email = consumed
    s = get_settings()
    token = await exchange_code(client_id=s.github_client_id or "",
                                client_secret=s.github_client_secret or "", code=code)
    if not token:
        return HTMLResponse(_PAGE.format(
            title="GitHub sign-in failed", body="GitHub didn't accept the sign-in — "
            "try again from chat."), status_code=400)
    await github_store.put_user_token(tenant, email, token)
    return HTMLResponse(_PAGE.format(
        title="GitHub Connected ✓",
        body="You're connected — return to chat and ask for the PR again. "
             "PRs will be authored as you."))


class RunActionRequest(BaseModel):
    action: Literal["create", "cancel"]


@router.post("/workflows/runs/{run_id}/action")
async def run_action(
    run_id: str,
    body: RunActionRequest,
    flow=Depends(get_github_flow),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> dict:
    if flow is None:
        raise HTTPException(status_code=503, detail="GitHub tool not configured")
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth, token_store=token_store,
    )
    kw = {"actor_email": user.email, "actor_name": user.display_name or user.email}
    result = await (flow.confirm(run_id, **kw) if body.action == "create"
                    else flow.cancel(run_id, **kw))
    return {"ok": result.ok, "status": result.status,
            "pr_url": result.pr_url, "message": result.message}
