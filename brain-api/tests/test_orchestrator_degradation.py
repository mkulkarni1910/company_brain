"""Unit test: Cosmos (People graph) outage must not 500 a query.

Spec §3.2: "Cosmos down -> skip People signal (proximity=0); ranker still runs."
The orchestrator owns the degradation policy: a raising proximity.score() must be
swallowed and treated as empty proximity, so the ranker falls back to pure
content order. Pure unit test with fake collaborators -- no real Azure.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.ranking.personalized_ranker import PersonalizedRanker


def _candidate(doc_id: str, rrf: float) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0",
            doc_id=doc_id,
            tenant_id="t-test",
            source="uploaded",
            source_url=f"local://{doc_id}",
            title=doc_id,
            content="hello world",
            acl_principals=["t-test:everyone"],
            created_at=now,
            modified_at=now,
            chunk_index=0,
        ),
        raw_scores={"content_rrf": rrf},
    )


class _FakeRetriever:
    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates

    async def retrieve(self, *, query: str, user: User, k: int) -> list[Candidate]:
        return list(self._candidates)


class _FakeACLStore:
    async def recheck(self, *, candidates: list[Candidate], user: User) -> list[Candidate]:
        return list(candidates)


class _BrokenProximity:
    async def score(self, *, user: User, doc_ids: list[str]) -> dict[str, float]:
        raise RuntimeError("cosmos down")


class _FakeCache:
    async def get_json(self, key: str):
        return None

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        return None


async def test_retrieve_ranked_degrades_when_proximity_raises() -> None:
    c_hi = _candidate("up:high", rrf=0.9)
    c_lo = _candidate("up:low", rrf=0.1)
    orch = SemanticKernelOrchestrator(
        retriever=_FakeRetriever([c_lo, c_hi]),
        llm=None,
        cache=_FakeCache(),
        acl_store=_FakeACLStore(),
        proximity=_BrokenProximity(),
        ranker=PersonalizedRanker(weight_content=0.7, weight_people=0.3),
    )

    user = User(
        user_id="u-x",
        tenant_id="t-test",
        email="u@x",
        display_name="U",
        group_ids={"t-test:everyone"},
    )

    result = await orch.retrieve_ranked(QueryRequest(query="anything"), user=user)

    # No exception propagated; both candidates returned, ranked by content (hi first).
    assert [c.chunk.doc_id for c in result] == ["up:high", "up:low"]
