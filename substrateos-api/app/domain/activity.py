from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EventType = Literal["view", "click", "thumbs_up", "thumbs_down", "dwell", "query"]


class ActivityEvent(BaseModel):
    timestamp: datetime
    tenant_id: str
    user_id: str
    doc_id: str
    event_type: EventType
    source: str
    query_id: str | None = None
    chunk_id: str | None = None
    duration_ms: int | None = None
