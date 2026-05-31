from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.api._auth_resolve import resolve_user
from app.deps import get_history_store
from app.domain.history import HistoryEntry

router = APIRouter(tags=["history"])


@router.get("/history", response_model=list[HistoryEntry])
async def history(
    limit: int = 50,
    store=Depends(get_history_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[HistoryEntry]:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    if store is None:
        return []
    return await store.recent(user=user, limit=min(max(limit, 1), 50))
