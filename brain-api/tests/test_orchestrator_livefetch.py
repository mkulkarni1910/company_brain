import asyncio
from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.live_fetch.base import needs_live_fetch as _heur
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlan
from app.ranking.personalized_ranker import PersonalizedRanker


def _chunk(doc_id: str, source: str, acl: list[str]) -> Chunk:
    now = datetime.now(UTC)
    return Chunk(
        chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test", source=source,
        source_url=f"x://{doc_id}", title=doc_id, content="c", content_vector=[],
        acl_principals=acl, author_id=None, entities=[], created_at=now,
        modified_at=now, chunk_index=0,
    )


class _FakeRetriever:
    async def retrieve(self, *, query, user, k):
        return [Candidate(chunk=_chunk("idx-1", "uploaded", ["t-test:everyone"]),
                          sources_hit={"vector"}, raw_scores={"content_rrf": 0.9})]


class _FakeACLStore:
    async def recheck(self, *, candidates, user):
        # Mimic real fail-closed recheck: keep a candidate only if the user's
        # principals intersect the chunk's acl_principals. A live candidate with
        # acl_principals=[] therefore gets DROPPED (no per-user OBO trimming).
        principals = user.principals()
        return [c for c in candidates if principals & set(c.chunk.acl_principals)]


class _FakeProximity:
    async def score(self, *, user, doc_ids):
        return {}


class _FakeActivity:
    async def score(self, *, user, doc_ids):
        return {}


class _FakeLiveFetcher:
    async def fetch(self, *, query, user):
        # live candidate with NO acl_principals — must survive (Graph-trimmed)
        return [Candidate(chunk=_chunk("graph:live-1", "graph", []),
                          sources_hit={"live"}, raw_scores={"content_rrf": 0.8})]


class _FakeCache:
    async def get_json(self, key): return None
    async def set_json(self, key, value, ttl_seconds): return None


class _FakeLLM:
    async def complete(self, **kw): return "answer [1] [2]"


class _FakePlanner:
    # Deterministic/offline: drive needs_live_fetch from the freshness heuristic
    # (matches the prior behaviour these tests assert on), no LLM call.
    async def plan(self, query):
        return QueryPlan(needs_retrieval=True, needs_live_fetch=_heur(query),
                         entities=[], rewrite=query)


def _orch(live_fetcher) -> SemanticKernelOrchestrator:
    return SemanticKernelOrchestrator(
        retriever=_FakeRetriever(), llm=_FakeLLM(), cache=_FakeCache(),
        acl_store=_FakeACLStore(), proximity=_FakeProximity(), activity=_FakeActivity(),
        ranker=PersonalizedRanker(weight_content=1.0, weight_people=0.0, weight_activity=0.0),
        live_fetcher=live_fetcher, planner=_FakePlanner(),
    )


def test_live_dropped_failclosed_without_obo() -> None:
    # Default: live_fetch_obo_enabled=False. The live candidate has no
    # acl_principals (single service identity, NOT per-user trimmed), so the
    # fail-closed recheck DROPS it. Only the indexed candidate survives.
    orch = _orch(_FakeLiveFetcher())
    cands = asyncio.run(orch.retrieve_ranked(QueryRequest(query="who is on call right now?"), user=_user()))
    doc_ids = {c.chunk.doc_id for c in cands}
    assert "graph:live-1" not in doc_ids     # live dropped fail-closed (no per-user OBO)
    assert "idx-1" in doc_ids                # indexed retained


def test_live_kept_with_obo(monkeypatch) -> None:
    # With genuine per-user OBO, live results are already user-trimmed -> bypass
    # recheck and keep them.
    from app.config import get_settings

    monkeypatch.setenv("LIVE_FETCH_OBO_ENABLED", "true")
    get_settings.cache_clear()
    orch = _orch(_FakeLiveFetcher())
    cands = asyncio.run(orch.retrieve_ranked(QueryRequest(query="who is on call right now?"), user=_user()))
    doc_ids = {c.chunk.doc_id for c in cands}
    assert "graph:live-1" in doc_ids         # live kept (recheck bypassed under OBO)
    assert "idx-1" in doc_ids                # indexed retained


def test_no_live_fetch_for_static_query() -> None:
    orch = _orch(_FakeLiveFetcher())
    cands = asyncio.run(orch.retrieve_ranked(QueryRequest(query="what is our PTO policy?"), user=_user()))
    doc_ids = {c.chunk.doc_id for c in cands}
    assert "graph:live-1" not in doc_ids     # static query -> no live fetch
    assert "idx-1" in doc_ids


def test_live_fetch_failure_does_not_block() -> None:
    class _BrokenLive:
        async def fetch(self, *, query, user):
            raise RuntimeError("graph down")

    orch = _orch(_BrokenLive())
    cands = asyncio.run(orch.retrieve_ranked(QueryRequest(query="latest status now"), user=_user()))
    assert {c.chunk.doc_id for c in cands} == {"idx-1"}   # degraded to indexed-only, no raise


def _user() -> User:
    return User(user_id="u", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"t-test:everyone"})
