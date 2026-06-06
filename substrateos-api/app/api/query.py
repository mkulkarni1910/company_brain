from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, Depends, Header, Request

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.deps import (
    get_acknowledger,
    get_conversation_memory,
    get_orchestrator,
    get_skill_router_svc,
    get_skill_store,
    get_token_store,
)
from app.domain.query import Answer, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(tags=["query"])


def github_answer(result, *, repo_label: str | None) -> Answer:
    """Render a GithubFlow StartResult as a web Answer."""
    if result.status == "preview":
        d = result.run.pr_draft
        return Answer(
            text="Here's the change I drafted — review and confirm before anything touches GitHub.",
            citations=[], query_id=f"github-{result.run.id}",
            pending_action={
                "type": "github_pr", "run_id": result.run.id, "title": d.title,
                "summary": d.summary, "path": d.path, "repo": repo_label,
                "branch": f"substrateos/{result.run.id.lower()}",
            })
    if result.status == "connect":
        return Answer(text=result.message, citations=[], query_id="github-connect",
                      pending_action={"type": "github_connect",
                                      "connect_url": result.connect_url})
    return Answer(text=result.message or "I couldn't action that.",
                  citations=[], query_id=f"github-{result.status}")


@router.post("/query/ack")
async def query_ack(
    body: QueryRequest,
    acknowledger=Depends(get_acknowledger),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> dict:
    """Fast, context-aware 'On it…' line from the small model — the web chat calls
    this immediately to fill the pending bubble while POST /query (strong model) runs.
    Never blocks the answer: make_ack degrades to a template on any failure."""
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    ack = await acknowledger.make_ack(body.query, name=getattr(user, "display_name", None))
    return {"ack": ack}


@router.post("/query", response_model=Answer)
async def query(
    request: Request,
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    memory=Depends(get_conversation_memory),
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

    if getattr(skill_ctx, "workflow", None) == "github":
        github_flow = getattr(request.app.state, "github_flow", None)
        github_store = getattr(request.app.state, "github_store", None)
        if github_flow is not None:
            result = await github_flow.start(
                effective_body.query, requester_name=user.display_name or user.email or "You",
                requester_email=user.email, surface="web")
            repo_label = None
            if github_store is not None:
                cfg = await github_store.get_config(get_settings().substrateos_tenant_id)
                if cfg:
                    repo_label = f"{cfg.owner}/{cfg.repo}"
            return github_answer(result, repo_label=repo_label)

    history = await memory.load_history(user=user, conversation_id=body.conversation_id)
    answer = await orchestrator.answer(
        effective_body, user=user, user_token=tok, skill_context=skill_ctx, history=history
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
        with contextlib.suppress(Exception):
            await metrics.record_query(user.tenant_id, user.user_id)
    if body.conversation_id:
        await memory.record(
            user=user, conversation_id=body.conversation_id, query=body.query, answer=answer
        )
    return answer
