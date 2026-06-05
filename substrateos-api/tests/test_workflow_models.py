from __future__ import annotations

import json

import pytest

from app.domain.skill import ResolvedSkill, Skill, SkillCreate, SkillUpdate
from app.skills.service import SkillRouter
from app.skills.store import SkillStore
from tests.test_skills_store import _make_redis, _skill_json


def test_skill_workflow_field_default_none():
    create = SkillCreate(
        slug="refund", name="Refund Processing", description="d", team="Support",
        system_prompt="p",
    )
    assert create.workflow is None


def test_skill_workflow_field_roundtrip():
    create = SkillCreate(
        slug="refund", name="Refund Processing", description="d", team="Support",
        system_prompt="p", workflow="refund",
    )
    assert create.workflow == "refund"
    update = SkillUpdate(workflow="refund")
    assert update.workflow == "refund"


def test_resolved_skill_carries_workflow():
    r = ResolvedSkill(id="1", slug="refund", name="Refund", system_prompt="p",
                      clean_query="q", workflow="refund")
    assert r.workflow == "refund"
    # default stays None for existing call sites
    r2 = ResolvedSkill(id="1", slug="s", name="n", system_prompt="p", clean_query="q")
    assert r2.workflow is None


@pytest.mark.asyncio
async def test_router_explicit_slug_carries_workflow():
    skill_raw = _skill_json(slug="refund", workflow="refund")
    skill_id = json.loads(skill_raw)["id"]
    store = SkillStore(client=_make_redis({skill_id: skill_raw}))
    router = SkillRouter(skill_store=store, llm=None)
    resolved = await router.resolve_skill("/refund can we refund order 48213?")
    assert resolved is not None
    assert resolved.workflow == "refund"
    assert resolved.clean_query == "can we refund order 48213?"


@pytest.mark.asyncio
async def test_router_auto_routing_carries_workflow():
    skill_raw = _skill_json(slug="refund", workflow="refund")
    skill_id = json.loads(skill_raw)["id"]
    store = SkillStore(client=_make_redis({skill_id: skill_raw}))

    class FakeLLM:
        async def complete(self, *, messages, deployment=None, temperature=0.0, max_tokens=60):
            return '{"skill": "refund"}'

    router = SkillRouter(skill_store=store, llm=FakeLLM())
    query = "can we refund order 48213?"
    resolved = await router.resolve_skill(query)
    assert resolved is not None
    assert resolved.workflow == "refund"
    assert resolved.clean_query == query
