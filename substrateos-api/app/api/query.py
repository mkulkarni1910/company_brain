from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, Depends, Header, Request

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.deps import get_conversation_store, get_orchestrator, get_skill_router_svc, get_skill_store, get_token_store
from app.domain.query import Answer, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(tags=["query"])


@router.post("/query", response_model=Answer)
async def query(
    request: Request,
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    conversation_store=Depends(get_conversation_store),
    token_store=Depends(get_token_store),
    skill_store=Depends(get_skill_store),
    skill_router_svc=Depends(get_skill_router_svc),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Answer:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    bearer = (
        authorization.split(" ", 1)[1]
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    tok = bearer if bearer and not bearer.startswith(get_settings().token_prefix) else None

    # Resolve which skill applies to this query (if any).
    skill_ctx = None
    if skill_router_svc is not None and skill_store is not None:
        with contextlib.suppress(Exception):
            skill_ctx = await skill_router_svc.resolve_skill(body.query)

    # When the user typed /slug, strip it from the query the LLM sees.
    effective_body = (
        body.model_copy(update={"query": skill_ctx.clean_query})
        if skill_ctx and skill_ctx.clean_query != body.query
        else body
    )

    answer = await orchestrator.answer(
        effective_body, user=user, user_token=tok, skill_context=skill_ctx
    )

    # Fire-and-forget run_count increment — never blocks the response.
    if skill_ctx is not None and skill_store is not None:
        asyncio.create_task(skill_store.increment_run_count(skill_ctx.id))

    if answer.debug and answer.debug.get("related_author_ids"):
        people_graph = getattr(request.app.state, "people_graph", None)
        if people_graph is not None:
            try:
                people = await people_graph.resolve_people(
                    answer.debug["related_author_ids"], user.tenant_id
                )
                answer.debug["related_people"] = [
                    {"user_id": p.user_id, "display_name": p.display_name} for p in people
                ]
            except Exception:  # noqa: BLE001
                pass
    metrics = getattr(request.app.state, "metrics_store", None)
    if metrics is not None:
        import contextlib as _ctx
        with _ctx.suppress(Exception):
            await metrics.record_query(user.tenant_id, user.user_id)
    if body.conversation_id and conversation_store is not None:
        await conversation_store.append(
            user=user, conversation_id=body.conversation_id, query=body.query, answer=answer
        )
    return answer
