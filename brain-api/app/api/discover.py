from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.api._auth_resolve import resolve_user
from app.deps import get_discover_service
from app.domain.discover import DiscoverResult

router = APIRouter(tags=["discover"])


@router.get("/discover", response_model=DiscoverResult)
async def discover(
    service=Depends(get_discover_service),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> DiscoverResult:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    if service is None:
        return DiscoverResult(trending=[], by_source=[], window_days=14)
    return await service.result(user=user)
