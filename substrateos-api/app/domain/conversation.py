from datetime import datetime

from pydantic import BaseModel

from app.domain.query import Answer


class ConversationTurn(BaseModel):
    query: str
    answer: Answer
    ts: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime
    turn_count: int


class Conversation(BaseModel):
    id: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime
    turns: list[ConversationTurn]
