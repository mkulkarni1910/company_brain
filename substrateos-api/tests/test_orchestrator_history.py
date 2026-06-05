"""Unit tests: history is threaded into the LLM messages, and history-carrying
requests bypass the (user, query)-keyed answer cache in both directions."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.conversation import ConversationTurn
from app.domain.identity import User
from app.domain.query import Answer, Candidate, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlan
from app.ranking.personalized_ranker import PersonalizedRanker

_USER = User(user_id="u-x", tenant_id="t-test", email="u@x",
             display_name="U", group_ids={"t-test:everyone"})


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


def _history_turn(q: str, a: str) -> ConversationTurn:
    return ConversationTurn(
        query=q, answer=Answer(text=a, citations=[], query_id="h"), ts=datetime.now(UTC)
    )


class _FakeRetriever:
    async def retrieve(self, *, query, user, k, timer=None):
        return [_candidate("up:doc")]


class _FakeACLStore:
    async def recheck(self, *, candidates, user):
        return list(candidates)


class _ZeroSignal:
    async def score(self, *, user, doc_ids):
        return {}


class _RecordingCache:
    def __init__(self):
        self.get_calls, self.set_calls = [], []

    async def get_json(self, key):
        self.get_calls.append(key)
        return None

    async def set_json(self, key, value, ttl_seconds):
        self.set_calls.append(key)


class _RecordingLLM:
    def __init__(self):
        self.messages = None

    async def complete(self, *, messages, temperature, max_tokens):
        self.messages = messages
        return "answer text"


class _FakeLiveFetcher:
    async def fetch(self, *, query, user, user_token=None):
        return []


class _FakePlanner:
    async def plan(self, query):
        return QueryPlan(needs_retrieval=True, needs_live_fetch=False,
                         entities=[], rewrite=query)


def _build() -> tuple[SemanticKernelOrchestrator, _RecordingCache, _RecordingLLM]:
    cache, llm = _RecordingCache(), _RecordingLLM()
    orch = SemanticKernelOrchestrator(
        retriever=_FakeRetriever(), llm=llm, cache=cache, acl_store=_FakeACLStore(),
        proximity=_ZeroSignal(),
        ranker=PersonalizedRanker(weight_content=0.7, weight_people=0.3),
        activity=_ZeroSignal(), live_fetcher=_FakeLiveFetcher(), planner=_FakePlanner(),
    )
    return orch, cache, llm


async def test_history_rendered_into_llm_messages() -> None:
    orch, _, llm = _build()
    history = [_history_turn("my name is Tom", "Hi Tom")]
    await orch.answer(QueryRequest(query="what was my name?"), user=_USER, history=history)
    roles = [m["role"] for m in llm.messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert llm.messages[1]["content"] == "my name is Tom"
    assert llm.messages[2]["content"] == "Hi Tom"


async def test_cache_skipped_when_history_present() -> None:
    orch, cache, _ = _build()
    history = [_history_turn("a", "b")]
    await orch.answer(QueryRequest(query="q"), user=_USER, history=history)
    assert cache.get_calls == [] and cache.set_calls == []


async def test_cache_used_when_no_history() -> None:
    orch, cache, _ = _build()
    await orch.answer(QueryRequest(query="q"), user=_USER)
    assert len(cache.get_calls) == 1 and len(cache.set_calls) == 1
