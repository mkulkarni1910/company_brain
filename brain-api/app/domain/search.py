from datetime import datetime

from pydantic import BaseModel

from app.domain.query import Answer


class SearchHit(BaseModel):
    doc_id: str
    title: str
    source: str
    source_url: str
    author_id: str | None
    modified_at: datetime
    snippet: str


class SourceFacet(BaseModel):
    source: str
    count: int


class PersonHit(BaseModel):
    user_id: str
    display_name: str
    role: str | None = None


class PersonFacet(BaseModel):
    user_id: str
    display_name: str
    count: int


class SearchPage(BaseModel):
    results: list[SearchHit]
    facets: list[SourceFacet]
    author_facets: list[tuple[str, int]] = []
    total: int


class SearchResponse(BaseModel):
    query: str
    answer: Answer | None = None
    results: list[SearchHit]
    facets: list[SourceFacet]
    people: list[PersonHit]
    authors: list[PersonFacet] = []
    total: int
