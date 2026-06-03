"""LLM plan step: rewrite the query for retrieval and decide whether Live Fetch
is needed. Degrades to the freshness heuristic on any failure — never blocks a query.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from app.config import get_settings
from app.live_fetch.base import needs_live_fetch

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a query planner for an enterprise search system. Given a user query, "
    "respond with ONLY a JSON object (no prose, no code fences) with keys: "
    "needs_retrieval (bool), needs_live_fetch (bool — true if the query asks for "
    "current/live/today/right-now/recent state that a periodically-indexed corpus "
    "could not have), entities (array of strings), rewrite (string — a cleaned, "
    "keyword-focused version of the query for retrieval)."
)


class QueryPlan(BaseModel):
    needs_retrieval: bool = True
    needs_live_fetch: bool = False
    entities: list[str] = []
    rewrite: str


class QueryPlanner:
    def __init__(self, *, llm) -> None:
        self._llm = llm

    def _fallback(self, query: str) -> QueryPlan:
        return QueryPlan(
            needs_retrieval=True,
            needs_live_fetch=needs_live_fetch(query),
            entities=[],
            rewrite=query,
        )

    async def plan(self, query: str) -> QueryPlan:
        try:
            text = await self._llm.complete(
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": query},
                ],
                deployment=get_settings().azure_openai_plan_deployment,
                temperature=0.0,
                max_tokens=200,
            )
            raw = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            plan = QueryPlan.model_validate(raw)
            if not plan.rewrite:
                plan.rewrite = query
            return plan
        except Exception as e:
            logger.warning("Query planner failed; falling back to heuristic: %s", e)
            return self._fallback(query)
