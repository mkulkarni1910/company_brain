from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.query import Candidate
from app.ranking.personalized_ranker import PersonalizedRanker


def _cand(doc_id: str, content_rank: int) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0", doc_id=doc_id, tenant_id="t-test",
            source="uploaded", source_url=f"local://{doc_id}", title="T",
            content="c", content_vector=[], acl_principals=["t-test:everyone"],
            author_id=None, entities=[], created_at=now, modified_at=now, chunk_index=0,
        ),
        sources_hit={"vector"},
        raw_scores={"content_rank": float(content_rank), "content_rrf": 1.0 / (60 + content_rank)},
    )


def test_people_signal_reorders_ties() -> None:
    # Two docs equal on content; doc-b has higher people proximity -> ranks first.
    cands = [_cand("doc-a", 0), _cand("doc-b", 1)]
    proximity = {"doc-a": 0.0, "doc-b": 1.0}
    ranker = PersonalizedRanker(weight_content=0.5, weight_people=0.5)
    ranked = ranker.rank(candidates=cands, proximity=proximity)
    assert ranked[0].candidate.chunk.doc_id == "doc-b"
    assert ranked[0].rank == 0
    assert ranked[1].rank == 1
    # breakdown carries both signals
    assert "content" in ranked[0].signal_breakdown
    assert "people" in ranked[0].signal_breakdown


def test_pure_content_when_people_weight_zero() -> None:
    cands = [_cand("doc-a", 0), _cand("doc-b", 1)]
    proximity = {"doc-a": 0.0, "doc-b": 1.0}
    ranker = PersonalizedRanker(weight_content=1.0, weight_people=0.0)
    ranked = ranker.rank(candidates=cands, proximity=proximity)
    # content rank 0 wins despite doc-b's proximity
    assert ranked[0].candidate.chunk.doc_id == "doc-a"


def test_empty_candidates_returns_empty() -> None:
    ranker = PersonalizedRanker(weight_content=0.7, weight_people=0.3)
    assert ranker.rank(candidates=[], proximity={}) == []
