from datetime import datetime

from pydantic import BaseModel


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
    """Fast search results — facets, people, authors. The AI Overview is fetched
    separately by the client (via /query) so the LLM never blocks the result list."""

    query: str
    results: list[SearchHit]
    facets: list[SourceFacet]
    people: list[PersonHit]
    authors: list[PersonFacet] = []
    total: int
