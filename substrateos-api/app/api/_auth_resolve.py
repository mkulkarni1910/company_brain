"""Unified request-identity resolution: Easy Auth header > Bearer JWT > debug header."""
from __future__ import annotations

from fastapi import HTTPException

from app.auth import InvalidToken, user_from_bearer, user_from_easy_auth_header
from app.config import get_settings
from app.domain.identity import User


def _debug_user(header: str) -> User:
    parts = header.split(",")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="bad debug header")
    tenant, user_id, *groups = parts
    return User(
        user_id=user_id,
        tenant_id=tenant,
        email=f"{user_id}@debug",
        display_name=user_id,
        group_ids=set(groups),
    )


def _apply_pilot_tenant(user: User) -> User:
    """Single-org pilot: map a real authenticated user onto the pilot brain tenant
    and grant the tenant-wide `everyone` group so the loaded corpus is reachable
    without per-user provisioning. No-op unless `pilot_single_tenant` is set."""
    s = get_settings()
    if not s.pilot_single_tenant:
        return user
    user.tenant_id = s.substrateos_tenant_id
    user.group_ids = set(user.group_ids) | {f"{s.substrateos_tenant_id}:everyone"}
    return user


async def resolve_user(
    *,
    easy_auth: str | None,
    authorization: str | None,
    debug_header: str | None,
    token_store=None,
) -> User:
    if easy_auth:  # Container Apps Easy Auth (production)
        try:
            return _apply_pilot_tenant(user_from_easy_auth_header(easy_auth))
        except InvalidToken as e:
            raise HTTPException(status_code=401, detail=f"invalid principal: {e}") from e
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        # Personal Access Token: only when a token_store is supplied (Context API,
        # /query, /search). A leaked PAT therefore can't authenticate token
        # management, which never passes a token_store.
        if token_store is not None and token.startswith(get_settings().token_prefix):
            user = await token_store.resolve(token)
            if user is None:
                raise HTTPException(status_code=401, detail="invalid token")
            return _apply_pilot_tenant(user)
        try:
            return _apply_pilot_tenant(await user_from_bearer(token))
        except InvalidToken as e:
            raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e
    if get_settings().enable_debug_auth and debug_header:
        return _debug_user(debug_header)
    raise HTTPException(status_code=401, detail="auth required")
