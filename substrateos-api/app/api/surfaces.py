from fastapi import APIRouter, Depends

from app.config import get_settings
from app.deps import get_connection_store
from app.connectors.store import ConnectionStore

router = APIRouter(tags=["surfaces"])


@router.get("/surfaces")
async def list_surfaces(
    store: ConnectionStore = Depends(get_connection_store),
) -> list[dict]:
    """Public read-only surface list — no admin key required.
    Returns [{name, enabled}] so the web app can gate surface chips.
    """
    tenant = get_settings().substrateos_tenant_id
    surfaces = await store.list_surfaces(tenant)
    return [{"name": s.name, "enabled": s.enabled} for s in surfaces]
