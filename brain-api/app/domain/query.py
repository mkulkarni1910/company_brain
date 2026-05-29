from typing import Literal

from pydantic import BaseModel, Field

from .chunk import Chunk

SourceHit = Literal["vector", "bm25", "semantic", "live", "graph"]


class QueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    k: int = 5


class Candidate(BaseModel):
    chunk: Chunk
    sources_hit: set[SourceHit] = Field(default_factory=set)
    raw_scores: dict[str, float] = Field(default_factory=dict)
    live_payload: dict | None = None


class RankedResult(BaseModel):
    candidate: Candidate
    final_score: float
    signal_breakdown: dict[str, float] = Field(default_factory=dict)
    rank: int


class Citation(BaseModel):
    doc_id: str
    chunk_id: str
    source_url: str
    title: str
    snippet: str


class Answer(BaseModel):
    text: str
    citations: list[Citation]
    query_id: str
    debug: dict | None = None
