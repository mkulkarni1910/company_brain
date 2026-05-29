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
        raw_scores={"content_rrf": 1.0 / (60 + content_rank)},
    )


def test_activity_signal_reorders_when_weighted() -> None:
    # Equal content + no people; doc-b has higher activity -> ranks first.
    cands = [_cand("doc-a", 0), _cand("doc-b", 0)]
    ranker = PersonalizedRanker(weight_content=0.4, weight_people=0.3, weight_activity=0.3)
    ranked = ranker.rank(
        candidates=cands,
        proximity={"doc-a": 0.0, "doc-b": 0.0},
        activity={"doc-a": 0.0, "doc-b": 1.0},
    )
    assert ranked[0].candidate.chunk.doc_id == "doc-b"
    assert "activity" in ranked[0].signal_breakdown


def test_activity_defaults_to_empty_when_omitted() -> None:
    cands = [_cand("doc-a", 0), _cand("doc-b", 1)]
    ranker = PersonalizedRanker(weight_content=0.5, weight_people=0.3, weight_activity=0.2)
    # activity omitted -> treated as all-zero; pure content+people order
    ranked = ranker.rank(candidates=cands, proximity={"doc-a": 0.0, "doc-b": 0.0})
    assert ranked[0].candidate.chunk.doc_id == "doc-a"
    assert ranked[0].signal_breakdown["activity"] == 0.0
