from fastapi import APIRouter, Depends, Header

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.deps import get_connection_store, get_token_store
from app.connectors.store import ConnectionStore

router = APIRouter(tags=["sources"])

_DISPLAY = {
    "sharepoint": "SharePoint",
    "teams": "Teams",
    "outlook_mail": "Outlook Mail",
    "outlook_calendar": "Outlook Calendar",
}


@router.get("/sources")
async def list_connected_sources(
    store: ConnectionStore = Depends(get_connection_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[dict]:
    """Read-only list of admin-connected sources — user auth only, no admin key.
    Returns only live/syncing sources so the UI never shows coming-soon entries."""
    await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    tenant = get_settings().substrateos_tenant_id
    conns = await store.list_connections(tenant)
    seen: set[str] = set()
    result = []
    for c in conns:
        if c.type not in seen and c.status in ("live", "syncing"):
            seen.add(c.type)
            result.append({
                "type": c.type,
                "name": _DISPLAY.get(c.type, c.type),
                "status": c.status,
            })
    return result
