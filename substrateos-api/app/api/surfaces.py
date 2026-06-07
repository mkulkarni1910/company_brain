from fastapi import APIRouter, Depends, Header

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.connectors.store import ConnectionStore
from app.deps import get_connection_store, get_token_store

router = APIRouter(tags=["surfaces"])


@router.get("/surfaces")
async def list_surfaces(
    store: ConnectionStore = Depends(get_connection_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[dict]:
    """Read-only surface list — requires user auth, but no admin key required.
    Returns [{name, enabled}] so the web app can gate surface chips.
    """
    await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    tenant = get_settings().substrateos_tenant_id
    surfaces = await store.list_surfaces(tenant)
    return [{"name": s.name, "enabled": s.enabled} for s in surfaces]
