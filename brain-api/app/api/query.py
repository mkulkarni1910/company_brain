from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.deps import get_conversation_store, get_orchestrator, get_token_store
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
    # A PAT is not an OBO token — don't forward it to Live Fetch.
    tok = bearer if bearer and not bearer.startswith(get_settings().token_prefix) else None
    answer = await orchestrator.answer(body, user=user, user_token=tok)
    # Resolve the authors of the cited documents to display names for the right-rail
    # "Related people" panel. Best-effort: empty when the people graph has no matching
    # person vertices (e.g. before people are seeded).
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
            except Exception:  # noqa: BLE001 — related-people panel is best-effort
                pass
    metrics = getattr(request.app.state, "metrics_store", None)
    if metrics is not None:
        import contextlib
        with contextlib.suppress(Exception):
            await metrics.record_query(user.tenant_id, user.user_id)
    # Persist the turn only when the client supplies a conversation_id — the Ask chat
    # does; the search AI-Overview call does not, so overviews aren't logged.
    if body.conversation_id and conversation_store is not None:
        await conversation_store.append(
            user=user, conversation_id=body.conversation_id, query=body.query, answer=answer
        )
    return answer
