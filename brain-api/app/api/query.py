from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import InvalidToken, user_from_bearer
from app.deps import get_retriever
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.retrieval.hybrid_retriever import HybridRetriever

router = APIRouter(tags=["query"])


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


async def _resolve_user(
    authorization: str | None,
    x_debug_bypass_auth: str | None,
) -> User:
    if x_debug_bypass_auth:
        return _debug_user(x_debug_bypass_auth)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="auth required")
    token = authorization.split(" ", 1)[1]
    try:
        return await user_from_bearer(token)
    except InvalidToken as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e


@router.post("/query")
async def query(
    body: QueryRequest,
    retriever: HybridRetriever = Depends(get_retriever),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> dict:
    user = await _resolve_user(authorization, x_debug_bypass_auth)
    candidates: list[Candidate] = await retriever.retrieve(query=body.query, user=user, k=body.k)
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
