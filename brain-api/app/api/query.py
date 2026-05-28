from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import InvalidToken, user_from_bearer
from app.config import get_settings
from app.deps import get_orchestrator
from app.domain.identity import User
from app.domain.query import Answer, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

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


async def _resolve_user(authorization: str | None, debug_header: str | None) -> User:
    if debug_header and get_settings().enable_debug_auth:
        return _debug_user(debug_header)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="auth required")
    try:
        return await user_from_bearer(authorization.split(" ", 1)[1])
    except InvalidToken as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e


@router.post("/query", response_model=Answer)
async def query(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> Answer:
    user = await _resolve_user(authorization, x_debug_bypass_auth)
    return await orchestrator.answer(body, user=user)
