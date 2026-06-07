"""Unit tests: requester identity threads into the prompt, customer candidates
are own-orders scoped, and requester-carrying requests bypass the answer cache
(it is keyed (user, query) with the shared bot user — caching would leak
identity-scoped answers across requesters)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlan
from app.ranking.personalized_ranker import PersonalizedRanker

_USER = User(user_id="bot", tenant_id="t-test", email="bot@x",
             display_name="Bot", group_ids={"t-test:everyone"})

_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")
_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     role="agent")

_PRIYA_ORDER = ("# Order #48213\n\n- **Customer:** Priya Sharma (priya@x)\n"
                "- **Order total:** $1,200.00\n")
_MARCUS_ORDER = ("# Order #48190\n\n- **Customer:** Marcus Lee (marcus.lee@example.com)\n"
                 "- **Order total:** $89.00\n")
_POLICY = "# Refund Policy\n\nAuto-approve refunds up to $500 within 30 days.\n"


def _candidate(doc_id: str, content: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0", doc_id=doc_id, tenant_id="t-test",
            source="uploaded", source_url=f"local://{doc_id}", title=doc_id,
            content=content, acl_principals=["t-test:everyone"],
            created_at=now, modified_at=now, chunk_index=0,
        ),
        raw_scores={"content_rrf": 0.9},
    )


class _FakeRetriever:
    async def retrieve(self, *, query, user, k, timer=None):
        return [_candidate("order-48213", _PRIYA_ORDER),
                _candidate("order-48190", _MARCUS_ORDER),
                _candidate("refund-policy", _POLICY)]


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


async def test_requester_note_reaches_system_prompt() -> None:
    orch, _, llm = _build()
    await orch.answer(QueryRequest(query="help with my order"), user=_USER,
                      requester=_PRIYA)
    system = llm.messages[0]["content"]
    assert "Priya Sharma" in system and "priya@x" in system
    assert "only discuss their own orders" in system


async def test_customer_candidates_are_own_order_scoped() -> None:
    orch, _, llm = _build()
    await orch.answer(QueryRequest(query="help with my order"), user=_USER,
                      requester=_PRIYA)
    context = llm.messages[-1]["content"]
    assert "48213" in context          # her order present
    assert "Marcus Lee" not in context  # other customer's order filtered out
    assert "Refund Policy" in context   # non-order docs untouched


async def test_staff_requester_sees_all_orders() -> None:
    orch, _, llm = _build()
    await orch.answer(QueryRequest(query="orders status"), user=_USER,
                      requester=_TOM)
    context = llm.messages[-1]["content"]
    assert "48213" in context and "Marcus Lee" in context


async def test_cache_skipped_when_requester_present() -> None:
    orch, cache, _ = _build()
    await orch.answer(QueryRequest(query="q"), user=_USER, requester=_PRIYA)
    assert cache.get_calls == [] and cache.set_calls == []


async def test_no_requester_keeps_anonymous_behavior() -> None:
    orch, cache, llm = _build()
    await orch.answer(QueryRequest(query="q"), user=_USER)
    assert "Requester:" not in llm.messages[0]["content"]
    assert len(cache.get_calls) == 1 and len(cache.set_calls) == 1
