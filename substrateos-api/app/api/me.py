"""GET /me — the signed-in user's identity for the web UI.

The name comes from the Entra login (Easy Auth claims in prod, bearer JWT or
debug header otherwise); the subtitle is the user's Slack profile title,
resolved by email and cached in Redis for a day (the directory-sync cadence
from the Entra approval-routing design). Fail-soft everywhere: no Slack token,
no workspace match, or an API error just means `title: null` — the UI then
shows the name alone.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.bots.slack import slack_get
from app.config import get_settings
from app.deps import get_cache

logger = logging.getLogger(__name__)

router = APIRouter(tags=["me"])

_TITLE_TTL_SECONDS = 86400  # successful lookups: refresh daily
_TITLE_RETRY_TTL_SECONDS = 900  # failed lookups: retry sooner, don't pin a blank for a day


class Me(BaseModel):
    display_name: str
    email: str
    title: str | None = None


async def _slack_title(email: str, cache) -> str | None:
    """The user's Slack profile title ("what I do"), or None. Negative results
    are cached too, so users without Slack don't cost a lookup per page load."""
    if not email:
        return None
    key = f"directory:title:{email.lower()}"
    cached = await cache.get_json(key)
    if cached is not None:
        return cached.get("title") or None
    token = get_settings().slack_bot_token
    if not token:
        return None
    body = await slack_get(token, "users.lookupByEmail", {"email": email})
    if body is None:  # not in the workspace, or a transient Slack error
        await cache.set_json(key, {"title": None}, ttl_seconds=_TITLE_RETRY_TTL_SECONDS)
        return None
    title = (body.get("user") or {}).get("profile", {}).get("title") or None
    await cache.set_json(key, {"title": title}, ttl_seconds=_TITLE_TTL_SECONDS)
    return title


@router.get("/me", response_model=Me)
async def me(
    cache=Depends(get_cache),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Me:
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth)
    title = await _slack_title(user.email, cache)
    return Me(display_name=user.display_name, email=user.email, title=title)
