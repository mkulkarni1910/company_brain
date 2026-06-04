from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Skill(BaseModel):
    """Full skill document — stored in Redis, returned to admins only."""
    id: str
    slug: str
    name: str
    description: str
    team: str
    run_scope: Literal["org", "team"] = "org"
    enabled: bool = True
    steps: list[str] = Field(default_factory=list)
    data_feeds: list[str] = Field(default_factory=list)
    system_prompt: str
    retrieval_config: dict | None = None
    rating: float = 0.0
    rating_count: int = 0
    run_count: int = 0
    created_at: datetime
    updated_at: datetime


class SkillSummary(BaseModel):
    """Client-safe view — system_prompt omitted."""
    id: str
    slug: str
    name: str
    description: str
    team: str
    run_scope: Literal["org", "team"]
    enabled: bool
    steps: list[str]
    data_feeds: list[str]
    rating: float
    rating_count: int
    run_count: int

    @classmethod
    def from_skill(cls, s: Skill) -> "SkillSummary":
        return cls(
            id=s.id, slug=s.slug, name=s.name, description=s.description,
            team=s.team, run_scope=s.run_scope, enabled=s.enabled,
            steps=s.steps, data_feeds=s.data_feeds,
            rating=s.rating, rating_count=s.rating_count, run_count=s.run_count,
        )


class SkillCreate(BaseModel):
    slug: str
    name: str
    description: str
    team: str
    run_scope: Literal["org", "team"] = "org"
    enabled: bool = True
    steps: list[str] = Field(default_factory=list)
    data_feeds: list[str] = Field(default_factory=list)
    system_prompt: str
    retrieval_config: dict | None = None


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    team: str | None = None
    run_scope: Literal["org", "team"] | None = None
    enabled: bool | None = None
    steps: list[str] | None = None
    data_feeds: list[str] | None = None
    system_prompt: str | None = None
    retrieval_config: dict | None = None


class SkillCatalogEntry(BaseModel):
    """Minimal view sent to the LLM skill router."""
    slug: str
    name: str
    description: str


@dataclass
class ResolvedSkill:
    """Resolved skill passed through the query pipeline. Not a Pydantic model — internal only."""
    id: str
    slug: str
    name: str
    system_prompt: str
    clean_query: str  # query with /slug prefix stripped (or original query for auto)
