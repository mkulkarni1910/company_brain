from datetime import datetime

from pydantic import BaseModel


class HistoryEntry(BaseModel):
    query: str
    query_id: str
    ts: datetime
