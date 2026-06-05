from datetime import UTC, datetime

from app.conversations.memory import MAX_ANSWER_CHARS, MAX_HISTORY_TURNS, ConversationMemory
from app.domain.conversation import Conversation, ConversationTurn
from app.domain.identity import User
from app.domain.query import Answer

_USER = User(user_id="u1", tenant_id="t1", email="u@x", display_name="U", group_ids=set())


def _turn(i: int, answer_text: str = "a") -> ConversationTurn:
    return ConversationTurn(
        query=f"q{i}",
        answer=Answer(text=answer_text, citations=[], query_id=f"id{i}"),
        ts=datetime.now(UTC),
    )


def _conv(turns: list[ConversationTurn]) -> Conversation:
    return Conversation(id="c1", title="t", updated_at=datetime.now(UTC), turns=turns)


class _Store:
    def __init__(self, conv=None, err=False):
        self.conv, self.err = conv, err
        self.appended = []

    async def get(self, *, user, conversation_id):
        if self.err:
            raise RuntimeError("boom")
        return self.conv

    async def append(self, *, user, conversation_id, query, answer):
        self.appended.append((conversation_id, query, answer.text))


async def test_load_returns_last_n_turns() -> None:
    turns = [_turn(i) for i in range(10)]
    mem = ConversationMemory(_Store(conv=_conv(turns)))
    out = await mem.load_history(user=_USER, conversation_id="c1")
    assert len(out) == MAX_HISTORY_TURNS
    assert out[0].query == "q4" and out[-1].query == "q9"


async def test_load_trims_long_answers() -> None:
    long_text = "x" * (MAX_ANSWER_CHARS + 500)
    mem = ConversationMemory(_Store(conv=_conv([_turn(1, long_text)])))
    out = await mem.load_history(user=_USER, conversation_id="c1")
    assert len(out[0].answer.text) == MAX_ANSWER_CHARS + 1  # +1 for the ellipsis
    assert out[0].answer.text.endswith("…")


async def test_load_empty_when_no_store() -> None:
    mem = ConversationMemory(None)
    assert await mem.load_history(user=_USER, conversation_id="c1") == []


async def test_load_empty_when_no_conversation_id() -> None:
    mem = ConversationMemory(_Store(conv=_conv([_turn(1)])))
    assert await mem.load_history(user=_USER, conversation_id=None) == []


async def test_load_empty_when_conversation_missing() -> None:
    mem = ConversationMemory(_Store(conv=None))
    assert await mem.load_history(user=_USER, conversation_id="c1") == []


async def test_load_empty_on_store_error() -> None:
    mem = ConversationMemory(_Store(err=True))
    assert await mem.load_history(user=_USER, conversation_id="c1") == []


async def test_record_delegates_to_append() -> None:
    store = _Store()
    mem = ConversationMemory(store)
    ans = Answer(text="hello", citations=[], query_id="x")
    await mem.record(user=_USER, conversation_id="c1", query="hi", answer=ans)
    assert store.appended == [("c1", "hi", "hello")]


async def test_record_noop_when_no_store() -> None:
    mem = ConversationMemory(None)
    ans = Answer(text="hello", citations=[], query_id="x")
    await mem.record(user=_USER, conversation_id="c1", query="hi", answer=ans)  # must not raise
