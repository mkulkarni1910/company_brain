"""Admin-route guard: Entra "Admin" group for people, x-admin-key for scripts.

The web admin panel no longer uses a shared key. A browser request is resolved
like any other request (Easy Auth > bearer JWT > debug header) and is allowed
only when the signed-in user belongs to the Entra group named by
ENTRA_ADMINS_GROUP ("Admin" by default). Membership comes from the token's
group claims when they carry it (the debug header lists group names; real
claims carry GUIDs — set ENTRA_ADMINS_GROUP to the group's GUID to match that
path directly), otherwise from an app-only Graph lookup of the group's member
emails, cached for ten minutes. The Graph path fails CLOSED: when the lookup
errors, nobody becomes an admin by accident.

The shared x-admin-key header survives for headless automation only (seed
scripts, eval corpus loader) — there is no signed-in user to check there.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.connectors.graph import graph_token, group_member_emails
from app.domain.identity import User

_MEMBERS_TTL_SECONDS = 600  # re-check group membership via Graph every 10 min


async def _admin_member_emails(cache) -> set[str]:
    """Emails of the admin group's members via app-only Graph, cached.

    Failures return an empty set (deny) and are NOT cached, so a transient
    Graph error denies one request rather than locking admins out for the TTL.
    """
    s = get_settings()
    if s.enable_debug_auth:
        # Dev/eval environment (flag is never on in prod): identity comes from
        # the debug header, which carries group names directly — a Graph
        # round-trip would only add latency/flakiness here.
        return set()
    key = f"admin:members:{s.entra_admins_group.lower()}"
    if cache is not None:
        cached = await cache.get_json(key)
        if cached is not None:
            return set(cached.get("emails", []))
    try:
        token = await graph_token(s.azure_tenant_id)
        emails = await group_member_emails(token, s.entra_admins_group)
    except Exception:  # noqa: BLE001 — fail closed, never fail open
        return set()
    if cache is not None:
        await cache.set_json(key, {"emails": sorted(emails)},
                             ttl_seconds=_MEMBERS_TTL_SECONDS)
    return emails


async def user_is_admin(user: User, cache) -> bool:
    """True when the user belongs to the configured Entra admins group."""
    s = get_settings()
    if s.entra_admins_group in user.group_ids:
        return True
    email = (user.email or "").lower()
    return bool(email) and email in await _admin_member_emails(cache)


async def require_admin(
    request: Request,
    x_admin_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> None:
    """Router dependency for everything under /admin (except OAuth callbacks)."""
    s = get_settings()
    if x_admin_key is not None:
        # Headless automation path. A wrong key is rejected outright rather
        # than falling through to the interactive path.
        if s.admin_api_key and x_admin_key == s.admin_api_key:
            return
        raise HTTPException(status_code=403, detail="admin key rejected")
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth)
    # Lifespan sets app.state.cache; tolerate its absence (some tests skip it).
    cache = getattr(request.app.state, "cache", None)
    if await user_is_admin(user, cache):
        return
    raise HTTPException(
        status_code=403,
        detail=f"admin access requires the {s.entra_admins_group!r} Entra group")
