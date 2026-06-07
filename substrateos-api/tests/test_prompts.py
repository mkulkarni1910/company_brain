from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.conversation import ConversationTurn
from app.domain.query import Answer, Candidate
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


# --- conversational history ---


def _history_turn(q: str, a: str) -> ConversationTurn:
    return ConversationTurn(
        query=q,
        answer=Answer(text=a, citations=[], query_id="h1"),
        ts=datetime.now(UTC),
    )


def test_history_renders_as_alternating_messages() -> None:
    history = [_history_turn("my name is Tom", "Nice to meet you, Tom.")]
    msgs = build_grounded_messages(query="what was my name?", candidates=[], history=history)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "my name is Tom"
    assert msgs[2]["content"] == "Nice to meet you, Tom."
    assert msgs[3]["content"].startswith("QUESTION: what was my name?")


def test_no_history_keeps_two_message_shape() -> None:
    msgs = build_grounded_messages(query="q", candidates=[])
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_system_prompt_allows_conversation_facts() -> None:
    msgs = build_grounded_messages(query="q", candidates=[], history=[])
    assert "conversation" in msgs[0]["content"].lower()


def test_skill_prompt_still_prepended_with_history() -> None:
    history = [_history_turn("a", "b")]
    msgs = build_grounded_messages(
        query="q", candidates=[], skill_prompt="SKILL RULES", history=history
    )
    assert msgs[0]["content"].startswith("SKILL RULES")


def test_history_answers_have_citation_markers_stripped() -> None:
    history = [_history_turn("q", "PTO is 20 days. [1] See policy. [2]")]
    msgs = build_grounded_messages(query="and?", candidates=[], history=history)
    assert msgs[2]["content"] == "PTO is 20 days.  See policy."


def test_multi_turn_history_ordering() -> None:
    history = [_history_turn("a", "A"), _history_turn("b", "B")]
    msgs = build_grounded_messages(query="c", candidates=[], history=history)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert msgs[1]["content"] == "a"
    assert msgs[3]["content"] == "b"


def test_requester_note_for_customer_and_staff() -> None:
    from app.domain.directory import DirectoryUser
    from app.generation.prompts import requester_note_for

    priya = DirectoryUser(email="priya@x", display_name="Priya Sharma", role="customer")
    note = requester_note_for(priya)
    assert "Priya Sharma" in note and "priya@x" in note and "customer" in note
    assert "only discuss their own orders" in note

    tom = DirectoryUser(email="tom@x", display_name="Tom Reyes", role="agent")
    note = requester_note_for(tom)
    assert "Tom Reyes" in note and "agent" in note
    assert "any customer" in note
    assert "only discuss" not in note


def test_requester_note_injected_into_system_message() -> None:
    msgs = build_grounded_messages(
        query="q", candidates=[], skill_prompt="SKILL RULES",
        requester_note="Requester: Priya (priya@x), role: customer.",
    )
    system = msgs[0]["content"]
    assert "SKILL RULES" in system
    assert "Requester: Priya (priya@x)" in system
    # note comes after the skill prompt
    assert system.index("SKILL RULES") < system.index("Requester: Priya")


def test_no_requester_note_leaves_system_unchanged() -> None:
    msgs = build_grounded_messages(query="q", candidates=[])
    assert "Requester:" not in msgs[0]["content"]
