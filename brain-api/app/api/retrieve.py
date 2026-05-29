"""Retrieval-only debug endpoint for the eval harness.

Returns ranked candidate doc_ids WITHOUT generation, so the eval harness can
measure true Recall@k / MRR@k against retrieval (not post-LLM citations).
Gated behind the same debug-auth flag as /query's bypass header.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import get_settings
from app.deps import get_orchestrator
from app.domain.identity import User
from app.domain.query import QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(prefix="/admin", tags=["admin"])


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


@router.post("/retrieve")
async def retrieve(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> dict:
    if not get_settings().enable_debug_auth or not x_debug_bypass_auth:
        raise HTTPException(status_code=401, detail="debug auth required")
    user = _debug_user(x_debug_bypass_auth)
    ranked = await orchestrator.retrieve_ranked(body, user=user)
    return {
        "doc_ids": [c.chunk.doc_id for c in ranked],
        "candidates": [
            {"doc_id": c.chunk.doc_id, "chunk_id": c.chunk.chunk_id, "scores": c.raw_scores}
            for c in ranked
        ],
    }
