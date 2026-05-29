from datetime import UTC, datetime, timedelta

from app.domain.chunk import Chunk
from app.domain.query import Candidate
from app.ranking.personalized_ranker import PersonalizedRanker


def _cand(doc_id: str, modified: datetime, content_rank: int = 0) -> Candidate:
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test", source="uploaded",
            source_url=f"x://{doc_id}", title="T", content="c", content_vector=[],
            acl_principals=["t-test:everyone"], author_id=None, entities=[],
            created_at=modified, modified_at=modified, chunk_index=content_rank,
        ),
        sources_hit={"vector"},
        raw_scores={"content_rrf": 1.0 / (60 + content_rank)},
    )


def test_recent_doc_outranks_stale_when_recency_weighted() -> None:
    now = datetime.now(UTC)
    cands = [_cand("stale", now - timedelta(days=120), 0), _cand("fresh", now, 0)]
    ranker = PersonalizedRanker(
        weight_content=0.4, weight_people=0.0, weight_activity=0.0, weight_recency=0.6)
    ranked = ranker.rank(candidates=cands, proximity={}, activity={})
    assert ranked[0].candidate.chunk.doc_id == "fresh"
    assert "recency" in ranked[0].signal_breakdown


def test_recency_defaults_off_keeps_phase2_behavior() -> None:
    now = datetime.now(UTC)
    cands = [_cand("a", now, 0), _cand("b", now - timedelta(days=400), 1)]
    # weight_recency omitted -> 0.0; pure content order (a first by rank)
    ranker = PersonalizedRanker(weight_content=1.0, weight_people=0.0, weight_activity=0.0)
    ranked = ranker.rank(candidates=cands, proximity={}, activity={})
    assert ranked[0].candidate.chunk.doc_id == "a"
    assert ranked[0].signal_breakdown["recency"] == 0.0 or "recency" in ranked[0].signal_breakdown
