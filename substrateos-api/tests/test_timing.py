"""StageTimer: per-stage pipeline timing used to localize query latency."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlan
from app.orchestrator.timing import StageTimer, maybe_stage
from app.ranking.personalized_ranker import PersonalizedRanker


def test_records_each_stage() -> None:
    async def run() -> StageTimer:
        t = StageTimer(query_id="q1")
        async with t.stage("plan"):
            await asyncio.sleep(0)
        async with t.stage("generate"):
            await asyncio.sleep(0)
        return t

    t = asyncio.run(run())
    assert set(t.timings_ms) == {"plan", "generate"}
    assert all(v >= 0.0 for v in t.timings_ms.values())
    assert "plan=" in t.summary() and "query_id=q1" in t.summary()


def test_records_duration_even_when_block_raises() -> None:
    """A stage that hangs-then-fails (proximity/activity degrade) must still be
    timed — that blocked time is the whole point of the instrumentation."""

    async def run() -> StageTimer:
        t = StageTimer()
        with pytest.raises(ValueError):
            async with t.stage("proximity"):
                raise ValueError("cosmos down")
        return t

    t = asyncio.run(run())
    assert "proximity" in t.timings_ms


def test_repeated_stage_accumulates() -> None:
    async def run() -> StageTimer:
        t = StageTimer()
        async with t.stage("retrieve"):
            await asyncio.sleep(0)
        async with t.stage("retrieve"):
            await asyncio.sleep(0)
        return t

    t = asyncio.run(run())
    # One key, summed — not overwritten.
    assert list(t.timings_ms) == ["retrieve"]


def test_maybe_stage_is_noop_without_timer() -> None:
    async def run() -> None:
        async with maybe_stage(None, "embed"):
            pass  # must not raise

    asyncio.run(run())


def test_maybe_stage_records_with_timer() -> None:
    async def run() -> StageTimer:
        t = StageTimer()
        async with maybe_stage(t, "embed"):
            await asyncio.sleep(0)
        return t

    t = asyncio.run(run())
    assert "embed" in t.timings_ms


# --- end-to-end: timings surface in Answer.debug for include_debug requests ---


def _candidate(doc_id: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0", doc_id=doc_id, tenant_id="t-test",
            source="uploaded", source_url=f"local://{doc_id}", title=doc_id,
            content="hello world", acl_principals=["t-test:everyone"],
            created_at=now, modified_at=now, chunk_index=0,
        ),
        raw_scores={"content_rrf": 0.9},
    )


class _FakeRetriever:
    async def retrieve(self, *, query, user, k, timer=None):
        return [_candidate("d1")]


class _FakeACL:
    async def recheck(self, *, candidates, user):
        return list(candidates)


class _Zero:
    async def score(self, *, user, doc_ids):
        return {}


class _FakeCache:
    async def get_json(self, key):
        return None

    async def set_json(self, key, value, ttl_seconds):
        return None


class _FakeLive:
    async def fetch(self, *, query, user, user_token=None):
        return []


class _FakePlanner:
    async def plan(self, query):
        return QueryPlan(needs_retrieval=True, needs_live_fetch=False, entities=[], rewrite=query)


class _FakeLLM:
    async def complete(self, *, messages, temperature=0.0, max_tokens=800):
        return "An answer."


def test_answer_debug_surfaces_per_stage_timings() -> None:
    orch = SemanticKernelOrchestrator(
        retriever=_FakeRetriever(),
        llm=_FakeLLM(),
        cache=_FakeCache(),
        acl_store=_FakeACL(),
        proximity=_Zero(),
        ranker=PersonalizedRanker(weight_content=0.7, weight_people=0.3),
        activity=_Zero(),
        live_fetcher=_FakeLive(),
        planner=_FakePlanner(),
    )
    user = User(
        user_id="u-x", tenant_id="t-test", email="u@x", display_name="U",
        group_ids={"t-test:everyone"},
    )

    answer = asyncio.run(
        orch.answer(QueryRequest(query="anything", include_debug=True), user=user)
    )

    timings = answer.debug["timings_ms"]
    assert {"plan", "retrieve_await", "acl_recheck", "proximity", "activity", "generate"} <= set(
        timings
    )
    assert all(isinstance(v, float) for v in timings.values())
