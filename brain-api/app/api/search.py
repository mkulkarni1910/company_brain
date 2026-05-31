from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.deps import get_search_service
from app.domain.search import SearchResponse

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    sources: list[str] | None = None
    date_from: datetime | None = None
    author_id: str | None = None
    top: int = 10
    skip: int = 0


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    service=Depends(get_search_service),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> SearchResponse:
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    if service is None:
        return SearchResponse(query=body.query, results=[], facets=[], people=[], total=0)
    return await service.result(
        user=user, query=body.query, top=min(max(body.top, 1), 25), skip=max(body.skip, 0),
        sources=body.sources, date_from=body.date_from, author_id=body.author_id,
    )
