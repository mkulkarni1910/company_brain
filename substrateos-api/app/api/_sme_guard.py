"""Studio-route guard: Entra SME group (ENTRA_SME_GROUP, "Finance SME" default).

Mirrors _admin_guard: membership comes from the token's group claims when they
carry it, otherwise from an app-only Graph lookup of the group's member emails,
cached for ten minutes. The Graph path fails CLOSED. Admins implicitly pass —
anything an SME may do, an admin may do. Unlike require_admin this returns the
resolved User: studio endpoints need the submitter's identity.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.api._admin_guard import user_is_admin
from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.connectors.graph import graph_token, group_member_emails
from app.domain.identity import User

_MEMBERS_TTL_SECONDS = 600  # re-check group membership via Graph every 10 min


async def _sme_member_emails(cache) -> set[str]:
    """Emails of the SME group's members via app-only Graph, cached. Failures
    return an empty set (deny) and are NOT cached — same shape as _admin_guard."""
    s = get_settings()
    if s.enable_debug_auth:
        return set()  # debug header carries group names directly
    key = f"sme:members:{s.entra_sme_group.lower()}"
    if cache is not None:
        cached = await cache.get_json(key)
        if cached is not None:
            return set(cached.get("emails", []))
    try:
        token = await graph_token(s.azure_tenant_id)
        emails = await group_member_emails(token, s.entra_sme_group)
    except Exception:  # noqa: BLE001 — fail closed, never fail open
        return set()
    if cache is not None:
        await cache.set_json(key, {"emails": sorted(emails)},
                             ttl_seconds=_MEMBERS_TTL_SECONDS)
    return emails


async def user_is_sme(user: User, cache) -> bool:
    """True for SME-group members — and for admins (superset)."""
    s = get_settings()
    if s.entra_sme_group in user.group_ids:
        return True
    email = (user.email or "").lower()
    if email and email in await _sme_member_emails(cache):
        return True
    return await user_is_admin(user, cache)


async def require_sme(
    request: Request,
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> User:
    """Dependency for /studio — returns the resolved submitter."""
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth)
    cache = getattr(request.app.state, "cache", None)
    if await user_is_sme(user, cache):
        return user
    raise HTTPException(
        status_code=403,
        detail=f"studio access requires the {get_settings().entra_sme_group!r} Entra group")
