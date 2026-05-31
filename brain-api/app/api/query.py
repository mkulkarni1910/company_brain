from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.api._auth_resolve import resolve_user
from app.deps import get_orchestrator
from app.domain.query import Answer, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(tags=["query"])


@router.post("/query", response_model=Answer)
async def query(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Answer:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    # Raw bearer token (the user assertion) for per-user OBO Live Fetch. Easy Auth
    # token-store threading is a deploy concern; for now pass the inbound bearer.
    tok = (
        authorization.split(" ", 1)[1]
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    return await orchestrator.answer(body, user=user, user_token=tok)
