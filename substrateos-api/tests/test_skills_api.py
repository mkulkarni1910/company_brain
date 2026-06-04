from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.deps import get_skill_store
from app.domain.skill import Skill, SkillCreate, SkillUpdate
from app.main import app

_ADMIN = {"x-admin-key": "dev-admin-key-local"}

def _skill(**overrides) -> Skill:
    now = datetime.now(UTC)
    base = dict(id=str(uuid.uuid4()), slug="test-skill", name="Test Skill",
                description="Does things.", team="Engineering", run_scope="org",
                enabled=True, steps=["Step 1"], data_feeds=["repo"],
                system_prompt="Do the thing.", retrieval_config=None,
                rating=0.0, rating_count=0, run_count=0, created_at=now, updated_at=now)
    base.update(overrides)
    return Skill(**base)


class _FakeStore:
    def __init__(self, skills=None):
        self._skills: dict[str, Skill] = {s.id: s for s in (skills or [])}

    async def list_all(self): return list(self._skills.values())
    async def list_enabled(self): return [s for s in self._skills.values() if s.enabled]
    async def get_by_id(self, sid): return self._skills.get(sid)
    async def get_by_slug(self, slug, *, enabled_only=False):
        for s in self._skills.values():
            if s.slug == slug:
                return None if (enabled_only and not s.enabled) else s
        return None
    async def create(self, data: SkillCreate) -> Skill:
        for s in self._skills.values():
            if s.slug == data.slug:
                raise ValueError(f"slug '{data.slug}' already exists (id={s.id})")
        now = datetime.now(UTC)
        skill = Skill(id=str(uuid.uuid4()), slug=data.slug, name=data.name,
                      description=data.description, team=data.team, run_scope=data.run_scope,
                      enabled=data.enabled, steps=data.steps, data_feeds=data.data_feeds,
                      system_prompt=data.system_prompt, retrieval_config=data.retrieval_config,
                      created_at=now, updated_at=now)
        self._skills[skill.id] = skill
        return skill
    async def update(self, sid, data: SkillUpdate):
        skill = self._skills.get(sid)
        if not skill: return None
        patch = data.model_dump(exclude_none=True)
        updated = skill.model_copy(update={**patch, "updated_at": datetime.now(UTC)})
        self._skills[sid] = updated
        return updated
    async def delete(self, sid): return bool(self._skills.pop(sid, None))
    async def increment_run_count(self, sid): pass
    async def update_rating(self, sid, new_rating):
        skill = self._skills.get(sid)
        if not skill: return None
        updated = skill.model_copy(update={"rating": new_rating, "rating_count": skill.rating_count + 1})
        self._skills[sid] = updated
        return updated
    async def get_catalog(self): return []


_AUTH = {"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"}


def test_list_skills_returns_enabled_only():
    store = _FakeStore([_skill(enabled=True), _skill(id=str(uuid.uuid4()), slug="off", enabled=False)])
    app.dependency_overrides[get_skill_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.get("/skills", headers=_AUTH)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["slug"] == "test-skill"
    finally:
        app.dependency_overrides.clear()


def test_list_skills_omits_system_prompt():
    store = _FakeStore([_skill()])
    app.dependency_overrides[get_skill_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.get("/skills", headers=_AUTH)
        assert resp.status_code == 200
        assert "system_prompt" not in resp.json()[0]
    finally:
        app.dependency_overrides.clear()


def test_admin_list_skills_includes_all():
    store = _FakeStore([_skill(enabled=True), _skill(id=str(uuid.uuid4()), slug="off", enabled=False)])
    app.dependency_overrides[get_skill_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.get("/admin/skills", headers=_ADMIN)
        assert resp.status_code == 200
        assert len(resp.json()) == 2
    finally:
        app.dependency_overrides.clear()


def test_admin_create_skill():
    store = _FakeStore()
    app.dependency_overrides[get_skill_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.post("/admin/skills", headers=_ADMIN, json={
                "slug": "new-skill", "name": "New", "description": "Desc",
                "team": "HR", "system_prompt": "Do it."
            })
        assert resp.status_code == 201
        assert resp.json()["slug"] == "new-skill"
        assert resp.json()["system_prompt"] == "Do it."
    finally:
        app.dependency_overrides.clear()


def test_admin_create_duplicate_slug_returns_409():
    store = _FakeStore([_skill(slug="exists")])
    app.dependency_overrides[get_skill_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.post("/admin/skills", headers=_ADMIN, json={
                "slug": "exists", "name": "X", "description": "D",
                "team": "Eng", "system_prompt": "S."
            })
        assert resp.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_admin_patch_enabled():
    s = _skill()
    store = _FakeStore([s])
    app.dependency_overrides[get_skill_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.patch(f"/admin/skills/{s.id}", headers=_ADMIN, json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
    finally:
        app.dependency_overrides.clear()


def test_admin_delete_skill():
    s = _skill()
    store = _FakeStore([s])
    app.dependency_overrides[get_skill_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.delete(f"/admin/skills/{s.id}", headers=_ADMIN)
        assert resp.status_code == 204
    finally:
        app.dependency_overrides.clear()


def test_rate_skill():
    s = _skill()
    store = _FakeStore([s])
    app.dependency_overrides[get_skill_store] = lambda: store
    try:
        with TestClient(app) as c:
            resp = c.post(f"/skills/{s.id}/rate", headers=_AUTH, json={"rating": 5})
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()
