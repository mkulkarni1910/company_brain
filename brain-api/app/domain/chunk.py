from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["sharepoint", "teams", "uploaded", "slack", "jira", "graph"]


class SourceDoc(BaseModel):
    doc_id: str
    tenant_id: str
    source: Source
    source_url: str
    title: str
    body: str
    author_id: str | None
    acl_principals: list[str]
    created_at: datetime
    modified_at: datetime
    mime: str


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    tenant_id: str
    source: Source
    source_url: str
    title: str
    content: str
    content_vector: list[float] = Field(default_factory=list)
    acl_principals: list[str]
    author_id: str | None = None
    entities: list[str] = Field(default_factory=list)
    created_at: datetime
    modified_at: datetime
    chunk_index: int
