from pydantic import BaseModel


class TrendingDoc(BaseModel):
    doc_id: str
    title: str
    source: str
    source_url: str
    snippet: str
    score: float


class SourceActivity(BaseModel):
    source: str
    events: int
    score: float


class DiscoverResult(BaseModel):
    trending: list[TrendingDoc]
    by_source: list[SourceActivity]
    window_days: int
