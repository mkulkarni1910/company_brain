from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.query import Candidate
from app.generation.prompts import (
    build_grounded_messages,
    parse_citations_from_answer,
)


def _make_chunk(chunk_id: str, doc_id: str, title: str, content: str) -> Chunk:
    now = datetime.now(UTC)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        tenant_id="t-test",
        source="uploaded",
        source_url=f"local://{doc_id}",
        title=title,
        content=content,
        content_vector=[],
        acl_principals=["t-test:everyone"],
        author_id=None,
        entities=[],
        created_at=now,
        modified_at=now,
        chunk_index=0,
    )


def test_messages_include_each_candidate_with_index() -> None:
    cands = [
        Candidate(chunk=_make_chunk("a#0", "a", "Policy A", "Policy text A.")),
        Candidate(chunk=_make_chunk("b#0", "b", "Policy B", "Policy text B.")),
    ]
    msgs = build_grounded_messages(query="what is policy?", candidates=cands)
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "[1]" in msgs[1]["content"]
    assert "[2]" in msgs[1]["content"]
    assert "Policy A" in msgs[1]["content"]
    assert "Policy B" in msgs[1]["content"]


def test_parse_citations_resolves_marker_to_candidate() -> None:
    cands = [
        Candidate(chunk=_make_chunk("a#0", "a", "Policy A", "Policy text A.")),
        Candidate(chunk=_make_chunk("b#0", "b", "Policy B", "Policy text B.")),
    ]
    answer = "Policy A states X. [1] Policy B differs. [2]"
    cites = parse_citations_from_answer(answer, cands)
    assert len(cites) == 2
    assert cites[0].chunk_id == "a#0"
    assert cites[1].chunk_id == "b#0"


def test_orphan_markers_are_dropped() -> None:
    cands = [Candidate(chunk=_make_chunk("a#0", "a", "Policy A", "Policy text A."))]
    answer = "Has [1] and [9] markers."
    cites = parse_citations_from_answer(answer, cands)
    assert [c.chunk_id for c in cites] == ["a#0"]
