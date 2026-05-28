"""Phase 1 /query endpoint: returns retrieved candidates only — no LLM yet.

Includes a temporary `x-debug-bypass-auth` header to inject a User for
integration tests until real Entra auth lands in Task 19.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.deps import get_retriever
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.retrieval.hybrid_retriever import HybridRetriever

router = APIRouter(tags=["query"])


def _debug_user(header: str | None) -> User:
    if not header:
        raise HTTPException(status_code=401, detail="auth required")
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


@router.post("/query")
async def query(
    body: QueryRequest,
    retriever: HybridRetriever = Depends(get_retriever),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> dict:
    user = _debug_user(x_debug_bypass_auth)
    candidates: list[Candidate] = await retriever.retrieve(query=body.query, user=user, k=body.k)
    # Strip vectors before returning (large + not needed)
    payload = []
    for c in candidates:
        chunk = c.chunk.model_dump()
        chunk["content_vector"] = []
        payload.append(
            {
                "chunk": chunk,
                "sources_hit": sorted(c.sources_hit),
                "raw_scores": c.raw_scores,
            }
        )
    return {"candidates": payload}
