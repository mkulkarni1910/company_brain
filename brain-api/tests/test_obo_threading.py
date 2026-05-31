"""Task 4: per-user OBO token threading through the orchestrator.

Unit-level (no real Azure / no msal call). Verifies that the requesting user's
token is threaded from orchestrator.answer() -> retrieve_ranked() ->
LiveFetcher.fetch(user_token=...). The OBO HTTP exchange itself is
integration-only (guarded by the flag + a real msal call) and not exercised here.
"""

import asyncio
from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.live_fetch.base import needs_live_fetch as _heur
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlan
from app.ranking.personalized_ranker import PersonalizedRanker


def _chunk(doc_id: str) -> Chunk:
    now = datetime.now(UTC)
    return Chunk(
        chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test", source="uploaded",
        source_url=f"x://{doc_id}", title=doc_id, content="c", content_vector=[],
        acl_principals=["t-test:everyone"], author_id=None, entities=[],
        created_at=now, modified_at=now, chunk_index=0,
    )


class _FakeRetriever:
    async def retrieve(self, *, query, user, k):
        return [Candidate(chunk=_chunk("idx-1"), sources_hit={"vector"},
                          raw_scores={"content_rrf": 0.9})]


class _FakeACLStore:
    async def recheck(self, *, candidates, user):
        principals = user.principals()
        return [c for c in candidates if principals & set(c.chunk.acl_principals)]


class _FakeProximity:
    async def score(self, *, user, doc_ids):
        return {}


class _FakeActivity:
    async def score(self, *, user, doc_ids):
        return {}


class _FakeCache:
    async def get_json(self, key):
        return None

    async def set_json(self, key, value, ttl_seconds):
        return None


class _FakeLLM:
    async def complete(self, **kw):
        return "answer [1]"


class _FakePlanner:
    async def plan(self, query):
        return QueryPlan(needs_retrieval=True, needs_live_fetch=_heur(query),
                         entities=[], rewrite=query)


class _SpyLiveFetcher:
    """Records the user_token it was called with; returns no live results."""

    def __init__(self) -> None:
        self.called = False
        self.user_token = "<unset>"

    async def fetch(self, *, query, user, user_token=None):
        self.called = True
        self.user_token = user_token
        return []


def _orch(live_fetcher) -> SemanticKernelOrchestrator:
    return SemanticKernelOrchestrator(
        retriever=_FakeRetriever(), llm=_FakeLLM(), cache=_FakeCache(),
        acl_store=_FakeACLStore(), proximity=_FakeProximity(), activity=_FakeActivity(),
        ranker=PersonalizedRanker(weight_content=1.0, weight_people=0.0, weight_activity=0.0),
        live_fetcher=live_fetcher, planner=_FakePlanner(),
    )


def _user() -> User:
    return User(user_id="u", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"t-test:everyone"})


def test_user_token_threaded_to_live_fetch() -> None:
    # The freshness query ("latest status now") triggers live fetch via the
    # heuristic-based fake planner; the spy must record the threaded token.
    spy = _SpyLiveFetcher()
    orch = _orch(spy)
    asyncio.run(orch.answer(QueryRequest(query="latest status now"),
                            user=_user(), user_token="tok-123"))
    assert spy.called
    assert spy.user_token == "tok-123"


def test_user_token_defaults_none() -> None:
    spy = _SpyLiveFetcher()
    orch = _orch(spy)
    asyncio.run(orch.answer(QueryRequest(query="latest status now"), user=_user()))
    assert spy.called
    assert spy.user_token is None
