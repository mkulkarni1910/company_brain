import asyncio

import pytest

from app.orchestrator.planner import QueryPlan, QueryPlanner


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def complete(self, **kwargs) -> str:
        return self._reply


def test_planner_parses_valid_json() -> None:
    llm = _FakeLLM('{"needs_retrieval": true, "needs_live_fetch": true, '
                   '"entities": ["on call"], "rewrite": "current on-call engineer"}')
    plan = asyncio.run(QueryPlanner(llm=llm).plan("who is on call right now?"))
    assert plan.needs_live_fetch is True
    assert plan.rewrite == "current on-call engineer"


def test_planner_falls_back_to_heuristic_on_bad_json() -> None:
    llm = _FakeLLM("not json at all")
    # freshness query -> heuristic says needs_live_fetch True; rewrite = original
    plan = asyncio.run(QueryPlanner(llm=llm).plan("who is on call right now?"))
    assert plan.needs_live_fetch is True
    assert plan.rewrite == "who is on call right now?"


def test_planner_falls_back_on_llm_error() -> None:
    class _BrokenLLM:
        async def complete(self, **kwargs) -> str:
            raise RuntimeError("openai down")

    plan = asyncio.run(QueryPlanner(llm=_BrokenLLM()).plan("what is our PTO policy?"))
    assert plan.needs_live_fetch is False     # heuristic: static query
    assert plan.rewrite == "what is our PTO policy?"


@pytest.mark.integration
async def test_planner_real_llm_static_query() -> None:
    from app.generation.azure_openai import AzureOpenAIClient

    llm = AzureOpenAIClient()
    try:
        plan = await QueryPlanner(llm=llm).plan("what is our PTO policy?")
        assert isinstance(plan, QueryPlan)
        assert plan.needs_live_fetch is False  # static query, no freshness markers
        assert isinstance(plan.rewrite, str) and plan.rewrite
    finally:
        await llm.aclose()
