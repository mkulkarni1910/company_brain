from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.skill import ResolvedSkill, Skill
from app.skills.service import SkillRouter


def _skill(slug: str, enabled: bool = True) -> Skill:
    now = datetime.now(UTC)
    return Skill(id=str(uuid.uuid4()), slug=slug,
                 name=slug.replace("-", " ").title(),
                 description=f"The {slug} skill.", team="Engineering", run_scope="org",
                 enabled=enabled, steps=[], data_feeds=[], system_prompt=f"Do {slug}.",
                 created_at=now, updated_at=now)


class _FakeStore:
    def __init__(self, skills):
        self._skills = {s.slug: s for s in skills}

    async def get_by_slug(self, slug, *, enabled_only=False):
        s = self._skills.get(slug)
        if s and enabled_only and not s.enabled:
            return None
        return s

    async def get_catalog(self):
        return [{"slug": s.slug, "name": s.name, "description": s.description}
                for s in self._skills.values() if s.enabled]


@pytest.mark.asyncio
async def test_explicit_slug_resolves_skill():
    store = _FakeStore([_skill("seo-research")])
    router = SkillRouter(skill_store=store, llm=MagicMock())
    ctx = await router.resolve_skill("/seo-research tell me things")
    assert ctx is not None
    assert ctx.slug == "seo-research"


@pytest.mark.asyncio
async def test_explicit_slug_strips_prefix_from_query():
    store = _FakeStore([_skill("seo-research")])
    router = SkillRouter(skill_store=store, llm=MagicMock())
    ctx = await router.resolve_skill("/seo-research tell me things")
    assert ctx is not None
    assert ctx.clean_query == "tell me things"


@pytest.mark.asyncio
async def test_explicit_slug_not_found_returns_none():
    store = _FakeStore([])
    router = SkillRouter(skill_store=store, llm=MagicMock())
    ctx = await router.resolve_skill("/nonexistent do something")
    assert ctx is None


@pytest.mark.asyncio
async def test_disabled_skill_explicit_returns_none():
    store = _FakeStore([_skill("off-skill", enabled=False)])
    router = SkillRouter(skill_store=store, llm=MagicMock())
    ctx = await router.resolve_skill("/off-skill do something")
    assert ctx is None


@pytest.mark.asyncio
async def test_auto_routing_via_llm():
    store = _FakeStore([_skill("seo-research")])
    llm = MagicMock()
    llm.complete = AsyncMock(return_value='{"skill": "seo-research"}')
    router = SkillRouter(skill_store=store, llm=llm)
    ctx = await router.resolve_skill("Tell me SEO insights about our company")
    assert ctx is not None
    assert ctx.slug == "seo-research"


@pytest.mark.asyncio
async def test_auto_routing_no_match_returns_none():
    store = _FakeStore([_skill("seo-research")])
    llm = MagicMock()
    llm.complete = AsyncMock(return_value='{"skill": null}')
    router = SkillRouter(skill_store=store, llm=llm)
    ctx = await router.resolve_skill("What is the weather today?")
    assert ctx is None


@pytest.mark.asyncio
async def test_auto_routing_llm_failure_returns_none():
    store = _FakeStore([_skill("seo-research")])
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    router = SkillRouter(skill_store=store, llm=llm)
    ctx = await router.resolve_skill("Tell me SEO insights")
    assert ctx is None


@pytest.mark.asyncio
async def test_no_catalog_skips_auto_routing():
    store = _FakeStore([])
    llm = MagicMock()
    llm.complete = AsyncMock()
    router = SkillRouter(skill_store=store, llm=llm)
    ctx = await router.resolve_skill("What is our SEO performance?")
    assert ctx is None
    llm.complete.assert_not_called()
