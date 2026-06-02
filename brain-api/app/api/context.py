from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.deps import get_orchestrator, get_token_store
from app.domain.query import QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

logger = logging.getLogger(__name__)
router = APIRouter(tags=["context"])

_SNIPPET = 240


class ContextRequest(BaseModel):
    query: str
    top: int = 8


class ContextHit(BaseModel):
    doc_id: str
    title: str
    source_url: str
    source: str
    snippet: str
    score: float
    signals: dict[str, float]


class ContextResponse(BaseModel):
    query: str
    hits: list[ContextHit]


@router.post("/context", response_model=ContextResponse)
async def context(
    body: ContextRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> ContextResponse:
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth, token_store=store,
    )
    top = min(max(body.top, 1), 25)
    try:
        ranked = await orchestrator.retrieve_ranked(
            QueryRequest(query=body.query, k=top), user=user
        )
    except Exception as e:  # noqa: BLE001 — never 500 a programmatic surface
        logger.warning("context retrieval failed: %s", e)
        ranked = []
    hits = [
        ContextHit(
            doc_id=r.candidate.chunk.doc_id,
            title=r.candidate.chunk.title,
            source_url=r.candidate.chunk.source_url,
            source=r.candidate.chunk.source,
            snippet=r.candidate.chunk.content[:_SNIPPET],
            score=r.final_score,
            signals=r.signal_breakdown,
        )
        for r in ranked[:top]
    ]
    return ContextResponse(query=body.query, hits=hits)
