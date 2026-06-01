from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.api._auth_resolve import resolve_user
from app.deps import get_conversation_store, get_orchestrator
from app.domain.query import Answer, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(tags=["query"])


@router.post("/query", response_model=Answer)
async def query(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    conversation_store=Depends(get_conversation_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Answer:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    tok = (
        authorization.split(" ", 1)[1]
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    answer = await orchestrator.answer(body, user=user, user_token=tok)
    # Persist the turn only when the client supplies a conversation_id — the Ask chat
    # does; the search AI-Overview call does not, so overviews aren't logged.
    if body.conversation_id and conversation_store is not None:
        await conversation_store.append(
            user=user, conversation_id=body.conversation_id, query=body.query, answer=answer
        )
    return answer
