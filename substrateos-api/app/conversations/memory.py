from __future__ import annotations

import logging

from app.conversations.store import ConversationStore
from app.domain.conversation import ConversationTurn
from app.domain.identity import User
from app.domain.query import Answer

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 6
MAX_ANSWER_CHARS = 800


class ConversationMemory:
    """Loads recent conversation turns for prompt context and records new turns.
    Best-effort: load failures degrade to a stateless answer; record failures
    are already swallowed by ConversationStore.append."""

    def __init__(self, store: ConversationStore | None) -> None:
        self._store = store

    async def load_history(
        self, *, user: User, conversation_id: str | None
    ) -> list[ConversationTurn]:
        if self._store is None or not conversation_id:
            return []
        try:
            conv = await self._store.get(user=user, conversation_id=conversation_id)
        except Exception as e:  # noqa: BLE001 - memory must never break the answer path
            logger.warning("memory load failed (cid=%s): %s", conversation_id, e)
            return []
        if conv is None:
            return []
        return [_trim(t) for t in conv.turns[-MAX_HISTORY_TURNS:]]

    async def record(
        self, *, user: User, conversation_id: str, query: str, answer: Answer
    ) -> None:
        if self._store is None:
            return
        await self._store.append(
            user=user, conversation_id=conversation_id, query=query, answer=answer
        )


def _trim(turn: ConversationTurn) -> ConversationTurn:
    text = turn.answer.text
    if len(text) <= MAX_ANSWER_CHARS:
        return turn
    return turn.model_copy(
        update={"answer": turn.answer.model_copy(update={"text": text[:MAX_ANSWER_CHARS] + "…"})}
    )
