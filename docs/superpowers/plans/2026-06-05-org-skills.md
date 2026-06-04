# Org Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Org Skills registry — a Redis-backed catalog of named, prompt-injecting skills invoked via `/slug` commands or auto-routed by the query pipeline, with a user-facing catalog page and a full admin CRUD interface.

**Architecture:** Skills are stored in Redis (HSET `skills:all`). At query time, `app/api/query.py` resolves an active skill (explicit `/slug` prefix or fast LLM routing call), then passes a `SkillContext` to the orchestrator which prepends the skill's `system_prompt` to the generation messages. The frontend adds a `/skills` catalog page, `/admin/skills` management page, and `/`-autocomplete + skill badge to the chat UI.

**Tech Stack:** FastAPI, Pydantic v2, redis.asyncio, GeminiClient (flash model for routing), Next.js 14 app router, React, CSS custom properties (existing design system).

---

## File Map

**New — backend**
- `substrateos-api/app/domain/skill.py` — Pydantic models (Skill, SkillSummary, SkillCreate, SkillUpdate, SkillCatalogEntry, SkillContext)
- `substrateos-api/app/skills/__init__.py` — empty package marker
- `substrateos-api/app/skills/store.py` — Redis-backed CRUD + catalog cache
- `substrateos-api/app/skills/service.py` — SkillRouter (slug detection + LLM auto-routing)
- `substrateos-api/app/api/skills.py` — user + admin API routes
- `substrateos-api/tests/test_skills_store.py`
- `substrateos-api/tests/test_skills_api.py`
- `substrateos-api/tests/test_skills_routing.py`

**Modified — backend**
- `substrateos-api/app/domain/query.py` — add `skill_used` to Answer; add `active_skill_prompt` + `active_skill_id` as internal-use fields
- `substrateos-api/app/generation/prompts.py` — accept optional `skill_prompt` param
- `substrateos-api/app/orchestrator/kernel.py` — accept `skill_context: SkillContext | None`
- `substrateos-api/app/api/query.py` — resolve skill before orchestrator call
- `substrateos-api/app/main.py` — init SkillStore + SkillRouter in lifespan
- `substrateos-api/app/deps.py` — add `get_skill_store`, `get_skill_router`

**New — frontend**
- `web/lib/skillsApi.ts` — typed API client for skills endpoints
- `web/app/skills/page.tsx` — user-facing skill catalog
- `web/app/admin/skills/page.tsx` — admin CRUD page

**Modified — frontend**
- `web/app/admin/layout.tsx` — add Skills nav item
- `web/app/globals.css` — skills catalog + admin table CSS
- `web/components/Chat.tsx` — `/`-autocomplete, `?prefill` URL param, skill_used badge
- `web/lib/api.ts` — add `skill_used` to Answer type; add `getSkills`

---

## Task 1: Domain models

**Files:**
- Create: `substrateos-api/app/domain/skill.py`

- [ ] **Step 1: Write the file**

```python
# substrateos-api/app/domain/skill.py
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
class SkillContext:
    """Resolved skill passed through the query pipeline. Not a Pydantic model — internal only."""
    id: str
    slug: str
    name: str
    system_prompt: str
```

- [ ] **Step 2: Commit**

```bash
git add substrateos-api/app/domain/skill.py
git commit -m "feat(skills): add domain models for Skill, SkillSummary, SkillCreate, SkillUpdate, SkillContext"
```

---

## Task 2: SkillStore

**Files:**
- Create: `substrateos-api/app/skills/__init__.py`
- Create: `substrateos-api/app/skills/store.py`
- Create: `substrateos-api/tests/test_skills_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# substrateos-api/tests/test_skills_store.py
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.skill import Skill, SkillCreate, SkillUpdate
from app.skills.store import SkillStore


def _make_redis(data: dict | None = None) -> MagicMock:
    """Return a mock Redis client pre-populated with `data` (field→json)."""
    r = MagicMock()
    _store: dict[str, str] = {k: v for k, v in (data or {}).items()}

    async def hgetall(key):
        return dict(_store)

    async def hset(key, field, value):
        _store[field] = value

    async def hdel(key, field):
        _store.pop(field, None)

    async def delete(*keys):
        pass  # catalog cache invalidation

    r.hgetall = hgetall
    r.hset = hset
    r.hdel = hdel
    r.delete = delete
    r.get = AsyncMock(return_value=None)   # cache miss for catalog
    r.set = AsyncMock()
    return r


def _skill_json(**overrides) -> str:
    now = datetime.now(UTC).isoformat()
    base = dict(
        id=str(uuid.uuid4()), slug="test-skill", name="Test Skill",
        description="A test skill.", team="Engineering", run_scope="org",
        enabled=True, steps=["Step 1"], data_feeds=["repo"],
        system_prompt="Do something.", retrieval_config=None,
        rating=0.0, rating_count=0, run_count=0,
        created_at=now, updated_at=now,
    )
    base.update(overrides)
    return json.dumps(base)


@pytest.mark.asyncio
async def test_list_all_returns_all_skills():
    skill_id = str(uuid.uuid4())
    r = _make_redis({skill_id: _skill_json(id=skill_id)})
    store = SkillStore(client=r)
    skills = await store.list_all()
    assert len(skills) == 1
    assert skills[0].id == skill_id


@pytest.mark.asyncio
async def test_list_enabled_filters_disabled():
    id1, id2 = str(uuid.uuid4()), str(uuid.uuid4())
    r = _make_redis({
        id1: _skill_json(id=id1, enabled=True),
        id2: _skill_json(id=id2, enabled=False),
    })
    store = SkillStore(client=r)
    skills = await store.list_enabled()
    assert len(skills) == 1
    assert skills[0].id == id1


@pytest.mark.asyncio
async def test_get_by_slug_returns_matching():
    skill_id = str(uuid.uuid4())
    r = _make_redis({skill_id: _skill_json(id=skill_id, slug="my-skill")})
    store = SkillStore(client=r)
    skill = await store.get_by_slug("my-skill")
    assert skill is not None
    assert skill.slug == "my-skill"


@pytest.mark.asyncio
async def test_get_by_slug_enabled_only_skips_disabled():
    skill_id = str(uuid.uuid4())
    r = _make_redis({skill_id: _skill_json(id=skill_id, slug="my-skill", enabled=False)})
    store = SkillStore(client=r)
    skill = await store.get_by_slug("my-skill", enabled_only=True)
    assert skill is None


@pytest.mark.asyncio
async def test_create_stores_and_returns_skill():
    r = _make_redis()
    store = SkillStore(client=r)
    data = SkillCreate(slug="new-skill", name="New", description="Desc",
                       team="Product", system_prompt="Do it.")
    skill = await store.create(data)
    assert skill.slug == "new-skill"
    assert skill.id  # UUID assigned
    # Verify persisted
    assert await store.get_by_id(skill.id) is not None


@pytest.mark.asyncio
async def test_create_raises_on_duplicate_slug():
    skill_id = str(uuid.uuid4())
    r = _make_redis({skill_id: _skill_json(id=skill_id, slug="dup-slug")})
    store = SkillStore(client=r)
    data = SkillCreate(slug="dup-slug", name="Dup", description="D",
                       team="Engineering", system_prompt="S.")
    with pytest.raises(ValueError, match="slug.*dup-slug.*already"):
        await store.create(data)


@pytest.mark.asyncio
async def test_update_merges_fields():
    skill_id = str(uuid.uuid4())
    r = _make_redis({skill_id: _skill_json(id=skill_id, name="Old Name")})
    store = SkillStore(client=r)
    updated = await store.update(skill_id, SkillUpdate(name="New Name"))
    assert updated is not None
    assert updated.name == "New Name"
    assert updated.slug == "test-skill"  # unchanged


@pytest.mark.asyncio
async def test_delete_removes_skill():
    skill_id = str(uuid.uuid4())
    r = _make_redis({skill_id: _skill_json(id=skill_id)})
    store = SkillStore(client=r)
    deleted = await store.delete(skill_id)
    assert deleted is True
    assert await store.get_by_id(skill_id) is None
```

- [ ] **Step 2: Run tests — expect FAIL (module not found)**

```bash
cd substrateos-api && python -m pytest tests/test_skills_store.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'app.skills'`

- [ ] **Step 3: Write `app/skills/__init__.py`**

```python
# substrateos-api/app/skills/__init__.py
```
(empty)

- [ ] **Step 4: Write `app/skills/store.py`**

```python
# substrateos-api/app/skills/store.py
from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.skill import Skill, SkillCatalogEntry, SkillCreate, SkillUpdate

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)

_DATA_KEY = "skills:all"          # HSET field=skill_id value=Skill JSON
_CATALOG_KEY = "skills:catalog"   # JSON list cache, TTL 5 min
_CATALOG_TTL = 300


class SkillStore:
    """Redis-backed store for org skills. Falls back gracefully when Redis is unavailable."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        if client is not None:
            self._r = client
            return
        s = get_settings()
        if not s.azure_redis_host:
            self._r = None
            return
        self._r = redis.Redis(
            host=s.azure_redis_host, port=s.azure_redis_port,
            ssl=s.azure_redis_ssl, password=s.redis_key,
            decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
        )

    async def aclose(self) -> None:
        if self._r is not None:
            with contextlib.suppress(Exception):
                await self._r.aclose()

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _hgetall(self) -> dict[str, str]:
        if self._r is None:
            return {}
        try:
            return await self._r.hgetall(_DATA_KEY)
        except _ERRORS as e:
            logger.warning("SkillStore.hgetall failed: %s", e)
            return {}

    def _parse(self, raw: str) -> Skill | None:
        with contextlib.suppress(Exception):
            return Skill.model_validate_json(raw)
        return None

    async def _invalidate_catalog(self) -> None:
        if self._r is None:
            return
        with contextlib.suppress(_ERRORS):
            await self._r.delete(_CATALOG_KEY)

    # ── reads ─────────────────────────────────────────────────────────────────

    async def list_all(self) -> list[Skill]:
        raw = await self._hgetall()
        return [s for v in raw.values() if (s := self._parse(v)) is not None]

    async def list_enabled(self) -> list[Skill]:
        return [s for s in await self.list_all() if s.enabled]

    async def get_by_id(self, skill_id: str) -> Skill | None:
        if self._r is None:
            return None
        try:
            raw = await self._r.hget(_DATA_KEY, skill_id)
            return self._parse(raw) if raw else None
        except _ERRORS as e:
            logger.warning("SkillStore.get_by_id failed: %s", e)
            return None

    async def get_by_slug(self, slug: str, *, enabled_only: bool = False) -> Skill | None:
        skills = await self.list_all()
        for s in skills:
            if s.slug == slug:
                if enabled_only and not s.enabled:
                    return None
                return s
        return None

    async def get_catalog(self) -> list[dict]:
        """Return [{slug, name, description}] for enabled skills. Redis-cached."""
        if self._r is not None:
            try:
                cached = await self._r.get(_CATALOG_KEY)
                if cached:
                    return json.loads(cached)
            except _ERRORS:
                pass
        skills = await self.list_enabled()
        catalog = [{"slug": s.slug, "name": s.name, "description": s.description} for s in skills]
        if self._r is not None:
            with contextlib.suppress(_ERRORS):
                await self._r.set(_CATALOG_KEY, json.dumps(catalog), ex=_CATALOG_TTL)
        return catalog

    # ── writes ────────────────────────────────────────────────────────────────

    async def create(self, data: SkillCreate) -> Skill:
        existing = await self.get_by_slug(data.slug)
        if existing is not None:
            raise ValueError(f"slug '{data.slug}' already exists (id={existing.id})")
        now = datetime.now(UTC)
        skill = Skill(
            id=str(uuid.uuid4()),
            slug=data.slug, name=data.name, description=data.description,
            team=data.team, run_scope=data.run_scope, enabled=data.enabled,
            steps=data.steps, data_feeds=data.data_feeds,
            system_prompt=data.system_prompt, retrieval_config=data.retrieval_config,
            created_at=now, updated_at=now,
        )
        if self._r is not None:
            try:
                await self._r.hset(_DATA_KEY, skill.id, skill.model_dump_json())
            except _ERRORS as e:
                logger.warning("SkillStore.create hset failed: %s", e)
        await self._invalidate_catalog()
        return skill

    async def update(self, skill_id: str, data: SkillUpdate) -> Skill | None:
        skill = await self.get_by_id(skill_id)
        if skill is None:
            return None
        patch = data.model_dump(exclude_none=True)
        updated = skill.model_copy(update={**patch, "updated_at": datetime.now(UTC)})
        if self._r is not None:
            try:
                await self._r.hset(_DATA_KEY, skill_id, updated.model_dump_json())
            except _ERRORS as e:
                logger.warning("SkillStore.update hset failed: %s", e)
        await self._invalidate_catalog()
        return updated

    async def delete(self, skill_id: str) -> bool:
        if self._r is None:
            return False
        try:
            deleted = await self._r.hdel(_DATA_KEY, skill_id)
        except _ERRORS as e:
            logger.warning("SkillStore.delete failed: %s", e)
            return False
        await self._invalidate_catalog()
        return bool(deleted)

    async def increment_run_count(self, skill_id: str) -> None:
        skill = await self.get_by_id(skill_id)
        if skill is None:
            return
        updated = skill.model_copy(update={"run_count": skill.run_count + 1, "updated_at": datetime.now(UTC)})
        if self._r is not None:
            with contextlib.suppress(_ERRORS):
                await self._r.hset(_DATA_KEY, skill_id, updated.model_dump_json())

    async def update_rating(self, skill_id: str, new_rating: float) -> Skill | None:
        skill = await self.get_by_id(skill_id)
        if skill is None:
            return None
        rolling = (skill.rating * skill.rating_count + new_rating) / (skill.rating_count + 1)
        updated = skill.model_copy(update={
            "rating": round(rolling, 2),
            "rating_count": skill.rating_count + 1,
            "updated_at": datetime.now(UTC),
        })
        if self._r is not None:
            with contextlib.suppress(_ERRORS):
                await self._r.hset(_DATA_KEY, skill_id, updated.model_dump_json())
        return updated
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
python -m pytest tests/test_skills_store.py -v
```
Expected: all 8 tests PASS

- [ ] **Step 6: Commit**

```bash
git add substrateos-api/app/skills/ substrateos-api/tests/test_skills_store.py
git commit -m "feat(skills): add SkillStore (Redis-backed CRUD + catalog cache)"
```

---

## Task 3: Skills API routes

**Files:**
- Create: `substrateos-api/app/api/skills.py`
- Create: `substrateos-api/tests/test_skills_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# substrateos-api/tests/test_skills_api.py
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
    def __init__(self, skills: list[Skill] | None = None):
        self._skills: dict[str, Skill] = {s.id: s for s in (skills or [])}

    async def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    async def list_enabled(self) -> list[Skill]:
        return [s for s in self._skills.values() if s.enabled]

    async def get_by_id(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    async def get_by_slug(self, slug: str, *, enabled_only: bool = False) -> Skill | None:
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

    async def update(self, skill_id: str, data: SkillUpdate) -> Skill | None:
        skill = self._skills.get(skill_id)
        if not skill:
            return None
        patch = data.model_dump(exclude_none=True)
        updated = skill.model_copy(update={**patch, "updated_at": datetime.now(UTC)})
        self._skills[skill_id] = updated
        return updated

    async def delete(self, skill_id: str) -> bool:
        return bool(self._skills.pop(skill_id, None))

    async def increment_run_count(self, skill_id: str) -> None:
        pass

    async def update_rating(self, skill_id: str, new_rating: float) -> Skill | None:
        skill = self._skills.get(skill_id)
        if not skill:
            return None
        updated = skill.model_copy(update={"rating": new_rating, "rating_count": skill.rating_count + 1})
        self._skills[skill_id] = updated
        return updated

    async def get_catalog(self) -> list[dict]:
        return [{"slug": s.slug, "name": s.name, "description": s.description}
                for s in self._skills.values() if s.enabled]


def _client(store: _FakeStore) -> TestClient:
    app.dependency_overrides[get_skill_store] = lambda: store
    return TestClient(app)


def test_list_skills_returns_enabled_only():
    store = _FakeStore([_skill(enabled=True), _skill(id=str(uuid.uuid4()), slug="off-skill", enabled=False)])
    with _client(store) as c:
        resp = c.get("/skills", headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["slug"] == "test-skill"


def test_list_skills_omits_system_prompt():
    store = _FakeStore([_skill()])
    with _client(store) as c:
        resp = c.get("/skills", headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "system_prompt" not in resp.json()[0]


def test_admin_list_skills_includes_all():
    store = _FakeStore([_skill(enabled=True), _skill(id=str(uuid.uuid4()), slug="off", enabled=False)])
    with _client(store) as c:
        resp = c.get("/admin/skills", headers=_ADMIN)
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_admin_create_skill():
    store = _FakeStore()
    with _client(store) as c:
        resp = c.post("/admin/skills", headers=_ADMIN, json={
            "slug": "new-skill", "name": "New", "description": "Desc",
            "team": "HR", "system_prompt": "Do it."
        })
    app.dependency_overrides.clear()
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "new-skill"
    assert body["system_prompt"] == "Do it."


def test_admin_create_duplicate_slug_returns_409():
    store = _FakeStore([_skill(slug="exists")])
    with _client(store) as c:
        resp = c.post("/admin/skills", headers=_ADMIN, json={
            "slug": "exists", "name": "X", "description": "D",
            "team": "Eng", "system_prompt": "S."
        })
    app.dependency_overrides.clear()
    assert resp.status_code == 409


def test_admin_patch_enabled():
    s = _skill()
    store = _FakeStore([s])
    with _client(store) as c:
        resp = c.patch(f"/admin/skills/{s.id}", headers=_ADMIN, json={"enabled": False})
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_admin_delete_skill():
    s = _skill()
    store = _FakeStore([s])
    with _client(store) as c:
        resp = c.delete(f"/admin/skills/{s.id}", headers=_ADMIN)
    app.dependency_overrides.clear()
    assert resp.status_code == 204


def test_rate_skill():
    s = _skill()
    store = _FakeStore([s])
    with _client(store) as c:
        resp = c.post(f"/skills/{s.id}/rate",
                      headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"},
                      json={"rating": 5})
    app.dependency_overrides.clear()
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_skills_api.py -v 2>&1 | head -20
```
Expected: FAIL — `get_skill_store` not found / routes not registered

- [ ] **Step 3: Write `app/api/skills.py`**

```python
# substrateos-api/app/api/skills.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.admin import require_admin_key
from app.api._auth_resolve import resolve_user
from app.deps import get_skill_store, get_token_store
from app.domain.skill import Skill, SkillCreate, SkillSummary, SkillUpdate

router = APIRouter(tags=["skills"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


# ── user-facing ──────────────────────────────────────────────────────────────

@router.get("/skills", response_model=list[SkillSummary])
async def list_skills(
    store=Depends(get_skill_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
    token_store=Depends(get_token_store),
) -> list[SkillSummary]:
    # Auth: resolve user to confirm the caller is authenticated. ACL scope enforcement
    # is deferred to v2 — for now all authenticated users see all enabled skills.
    await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    skills = await store.list_enabled()
    return [SkillSummary.from_skill(s) for s in skills]


@router.post("/skills/{skill_id}/run", status_code=204)
async def run_skill(
    skill_id: str,
    store=Depends(get_skill_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
    token_store=Depends(get_token_store),
) -> None:
    await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth, token_store=token_store,
    )
    await store.increment_run_count(skill_id)


@router.post("/skills/{skill_id}/rate", response_model=SkillSummary)
async def rate_skill(
    skill_id: str,
    body: dict,
    store=Depends(get_skill_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
    token_store=Depends(get_token_store),
) -> SkillSummary:
    await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth, token_store=token_store,
    )
    rating = float(body.get("rating", 0))
    if not (1 <= rating <= 5):
        raise HTTPException(status_code=422, detail="rating must be 1–5")
    updated = await store.update_rating(skill_id, rating)
    if updated is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return SkillSummary.from_skill(updated)


# ── admin ─────────────────────────────────────────────────────────────────────

@admin_router.get("/skills", response_model=list[Skill])
async def admin_list_skills(store=Depends(get_skill_store)) -> list[Skill]:
    return await store.list_all()


@admin_router.post("/skills", response_model=Skill, status_code=201)
async def admin_create_skill(body: SkillCreate, store=Depends(get_skill_store)) -> Skill:
    try:
        return await store.create(body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@admin_router.patch("/skills/{skill_id}", response_model=Skill)
async def admin_update_skill(
    skill_id: str, body: SkillUpdate, store=Depends(get_skill_store)
) -> Skill:
    updated = await store.update(skill_id, body)
    if updated is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return updated


@admin_router.delete("/skills/{skill_id}", status_code=204)
async def admin_delete_skill(skill_id: str, store=Depends(get_skill_store)) -> None:
    deleted = await store.delete(skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="skill not found")
```

- [ ] **Step 4: Add `get_skill_store` to `app/deps.py`**

Append to `substrateos-api/app/deps.py`:

```python
def get_skill_store(request: Request):
    return getattr(request.app.state, "skill_store", None)

def get_skill_router_svc(request: Request):
    return getattr(request.app.state, "skill_router_svc", None)
```

- [ ] **Step 5: Register routers in `app/main.py`**

Add these two imports near the other router imports in `app/main.py`:

```python
from app.api.skills import admin_router as skills_admin_router
from app.api.skills import router as skills_router
```

Add these two lines after the other `app.include_router()` calls:

```python
app.include_router(skills_router)
app.include_router(skills_admin_router)
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
python -m pytest tests/test_skills_api.py -v
```
Expected: all 8 tests PASS

- [ ] **Step 7: Commit**

```bash
git add substrateos-api/app/api/skills.py substrateos-api/app/deps.py substrateos-api/app/main.py substrateos-api/tests/test_skills_api.py
git commit -m "feat(skills): add user + admin API routes (GET /skills, CRUD /admin/skills, rate, run)"
```

---

## Task 4: Extend Answer model + prompt builder

**Files:**
- Modify: `substrateos-api/app/domain/query.py`
- Modify: `substrateos-api/app/generation/prompts.py`

- [ ] **Step 1: Add `skill_used` to `Answer` in `app/domain/query.py`**

In `substrateos-api/app/domain/query.py`, change the `Answer` class to:

```python
class Answer(BaseModel):
    text: str
    citations: list[Citation]
    query_id: str
    skill_used: dict | None = None  # {id, slug, name} when a skill was applied
    debug: dict | None = None
```

- [ ] **Step 2: Add `skill_prompt` param to `build_grounded_messages` in `app/generation/prompts.py`**

Change the function signature and system prompt construction:

```python
def build_grounded_messages(
    *, query: str, candidates: list[Candidate], skill_prompt: str | None = None
) -> list[dict[str, str]]:
    system = SYSTEM_PROMPT
    if skill_prompt:
        system = f"{skill_prompt}\n\n{system}"
    blocks: list[str] = []
    for i, c in enumerate(candidates, start=1):
        blocks.append(
            f"[{i}] {c.chunk.title} — {c.chunk.source_url}\n{c.chunk.content}"
        )
    user = f"QUESTION: {query}\n\nCONTEXT:\n" + "\n\n".join(blocks)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
```

- [ ] **Step 3: Verify no existing tests broke**

```bash
python -m pytest tests/ -v -k "not skills" 2>&1 | tail -10
```
Expected: all pre-existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add substrateos-api/app/domain/query.py substrateos-api/app/generation/prompts.py
git commit -m "feat(skills): add skill_used to Answer; add skill_prompt param to build_grounded_messages"
```

---

## Task 5: SkillRouter service

**Files:**
- Create: `substrateos-api/app/skills/service.py`
- Create: `substrateos-api/tests/test_skills_routing.py`

- [ ] **Step 1: Write the failing tests**

```python
# substrateos-api/tests/test_skills_routing.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.skill import Skill
from app.skills.service import SkillRouter


def _skill(slug: str, enabled: bool = True) -> Skill:
    now = datetime.now(UTC)
    return Skill(id=str(uuid.uuid4()), slug=slug, name=slug.replace("-", " ").title(),
                 description=f"The {slug} skill.", team="Engineering", run_scope="org",
                 enabled=enabled, steps=[], data_feeds=[], system_prompt=f"Do {slug}.",
                 created_at=now, updated_at=now)


class _FakeStore:
    def __init__(self, skills: list[Skill]):
        self._skills = {s.slug: s for s in skills}

    async def get_by_slug(self, slug: str, *, enabled_only: bool = False) -> Skill | None:
        s = self._skills.get(slug)
        if s and enabled_only and not s.enabled:
            return None
        return s

    async def get_catalog(self) -> list[dict]:
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
    assert ctx is None  # fail open


@pytest.mark.asyncio
async def test_no_catalog_skips_auto_routing():
    store = _FakeStore([])  # empty catalog
    llm = MagicMock()
    llm.complete = AsyncMock()
    router = SkillRouter(skill_store=store, llm=llm)
    ctx = await router.resolve_skill("What is our SEO performance?")
    assert ctx is None
    llm.complete.assert_not_called()
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_skills_routing.py -v 2>&1 | head -20
```
Expected: FAIL — `SkillRouter` not found

- [ ] **Step 3: Write `app/skills/service.py`**

```python
# substrateos-api/app/skills/service.py
from __future__ import annotations

import contextlib
import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^/([a-z0-9][a-z0-9-]*)(?:\s|$)")

ROUTER_PROMPT = (
    "You are a skill router. Given a user query and a catalog of named skills, "
    "return the slug of the most relevant skill if one clearly applies. "
    "Be conservative — only return a skill when the match is strong and unambiguous. "
    "Respond ONLY with valid JSON: {\"skill\": \"<slug>\"} or {\"skill\": null}. "
    "No other text."
)


@dataclass
class ResolvedSkill:
    id: str
    slug: str
    name: str
    system_prompt: str
    clean_query: str  # query with /slug prefix stripped (or original query for auto)


class SkillRouter:
    """Resolves which skill (if any) applies to a given query.

    Touch point 1: explicit /slug prefix — fast path, no LLM call.
    Touch point 2: LLM auto-routing using the flash model — only when no explicit slug.
    """

    def __init__(self, *, skill_store, llm) -> None:
        self._store = skill_store
        self._llm = llm

    async def resolve_skill(self, query: str) -> ResolvedSkill | None:
        m = _SLUG_RE.match(query.lstrip())
        if m:
            return await self._resolve_explicit(query, slug=m.group(1))
        return await self._resolve_auto(query)

    async def _resolve_explicit(self, query: str, slug: str) -> ResolvedSkill | None:
        skill = await self._store.get_by_slug(slug, enabled_only=True)
        if skill is None:
            return None
        clean = _SLUG_RE.sub("", query.lstrip(), count=1).lstrip()
        return ResolvedSkill(
            id=skill.id, slug=skill.slug, name=skill.name,
            system_prompt=skill.system_prompt, clean_query=clean or query,
        )

    async def _resolve_auto(self, query: str) -> ResolvedSkill | None:
        catalog = await self._store.get_catalog()
        if not catalog:
            return None
        slug = await self._llm_route(query, catalog)
        if not slug:
            return None
        skill = await self._store.get_by_slug(slug, enabled_only=True)
        if skill is None:
            return None
        return ResolvedSkill(
            id=skill.id, slug=skill.slug, name=skill.name,
            system_prompt=skill.system_prompt, clean_query=query,
        )

    async def _llm_route(self, query: str, catalog: list[dict]) -> str | None:
        messages = [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": f"Catalog: {json.dumps(catalog)}\n\nUser query: {query}"},
        ]
        try:
            # Pass deployment= so GeminiClient uses the fast flash model (thinking off).
            text = await self._llm.complete(
                messages=messages, deployment="skill_router", temperature=0.0, max_tokens=60
            )
            # Extract JSON even if the model adds stray whitespace/backticks.
            match = re.search(r'\{.*?\}', text, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group(0))
            return data.get("skill") or None
        except Exception as e:  # noqa: BLE001 — fail open, never block the query
            logger.warning("Skill router LLM call failed: %s", e)
            return None
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/test_skills_routing.py -v
```
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/skills/service.py substrateos-api/tests/test_skills_routing.py
git commit -m "feat(skills): add SkillRouter (slash detection + LLM auto-routing via flash model)"
```

---

## Task 6: Wire orchestrator to accept SkillContext

**Files:**
- Modify: `substrateos-api/app/orchestrator/kernel.py`

- [ ] **Step 1: Add `skill_context` param to `answer` and `_answer`**

In `substrateos-api/app/orchestrator/kernel.py`:

Add import at the top:
```python
from app.skills.service import ResolvedSkill
```

Change the `answer` signature:
```python
async def answer(
    self, request: QueryRequest, *, user: User, user_token: str | None = None,
    skill_context: ResolvedSkill | None = None,
) -> Answer:
    query_id = str(uuid.uuid4())
    timer = StageTimer(query_id=query_id)
    t0 = time.perf_counter()
    try:
        return await self._answer(
            request, user=user, user_token=user_token, timer=timer,
            query_id=query_id, skill_context=skill_context,
        )
    finally:
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info("query timing %s total=%sms", timer.summary(), total_ms)
```

Change the `_answer` signature:
```python
async def _answer(
    self,
    request: QueryRequest,
    *,
    user: User,
    user_token: str | None,
    timer: StageTimer,
    query_id: str,
    skill_context: ResolvedSkill | None = None,
) -> Answer:
```

In `_answer`, replace the `build_grounded_messages` call:
```python
messages = build_grounded_messages(
    query=request.query,
    candidates=candidates[:5],
    skill_prompt=skill_context.system_prompt if skill_context else None,
)
```

Add `skill_used` to the returned Answer (replace the final `Answer(...)` construction):
```python
answer = Answer(
    text=text,
    citations=citations,
    query_id=query_id,
    skill_used={"id": skill_context.id, "slug": skill_context.slug, "name": skill_context.name}
    if skill_context else None,
    debug=debug,
)
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
python -m pytest tests/ -v -k "not skills" 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add substrateos-api/app/orchestrator/kernel.py
git commit -m "feat(skills): orchestrator accepts ResolvedSkill — injects system_prompt + returns skill_used in Answer"
```

---

## Task 7: Wire query endpoint

**Files:**
- Modify: `substrateos-api/app/api/query.py`

- [ ] **Step 1: Update `app/api/query.py` to resolve skill before calling orchestrator**

Replace the entire file:

```python
# substrateos-api/app/api/query.py
from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, Depends, Header, Request

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.deps import get_conversation_store, get_orchestrator, get_skill_router_svc, get_skill_store, get_token_store
from app.domain.query import Answer, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(tags=["query"])


@router.post("/query", response_model=Answer)
async def query(
    request: Request,
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    conversation_store=Depends(get_conversation_store),
    token_store=Depends(get_token_store),
    skill_store=Depends(get_skill_store),
    skill_router_svc=Depends(get_skill_router_svc),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Answer:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    bearer = (
        authorization.split(" ", 1)[1]
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    tok = bearer if bearer and not bearer.startswith(get_settings().token_prefix) else None

    # Resolve which skill applies to this query (if any).
    skill_ctx = None
    if skill_router_svc is not None and skill_store is not None:
        with contextlib.suppress(Exception):
            skill_ctx = await skill_router_svc.resolve_skill(body.query)

    # When the user typed /slug, strip it from the query the LLM sees.
    effective_body = (
        body.model_copy(update={"query": skill_ctx.clean_query})
        if skill_ctx and skill_ctx.clean_query != body.query
        else body
    )

    answer = await orchestrator.answer(
        effective_body, user=user, user_token=tok, skill_context=skill_ctx
    )

    # Fire-and-forget run_count increment — never blocks the response.
    if skill_ctx is not None and skill_store is not None:
        asyncio.create_task(skill_store.increment_run_count(skill_ctx.id))

    if answer.debug and answer.debug.get("related_author_ids"):
        people_graph = getattr(request.app.state, "people_graph", None)
        if people_graph is not None:
            try:
                people = await people_graph.resolve_people(
                    answer.debug["related_author_ids"], user.tenant_id
                )
                answer.debug["related_people"] = [
                    {"user_id": p.user_id, "display_name": p.display_name} for p in people
                ]
            except Exception:  # noqa: BLE001
                pass
    metrics = getattr(request.app.state, "metrics_store", None)
    if metrics is not None:
        import contextlib as _ctx
        with _ctx.suppress(Exception):
            await metrics.record_query(user.tenant_id, user.user_id)
    if body.conversation_id and conversation_store is not None:
        await conversation_store.append(
            user=user, conversation_id=body.conversation_id, query=body.query, answer=answer
        )
    return answer
```

- [ ] **Step 2: Verify existing query tests pass**

```bash
python -m pytest tests/test_query_e2e.py -v 2>&1 | tail -10
```
Expected: PASS (skill_router_svc is None when not initialized → graceful skip)

- [ ] **Step 3: Commit**

```bash
git add substrateos-api/app/api/query.py
git commit -m "feat(skills): wire skill routing into query endpoint — resolve skill, inject context, increment run_count"
```

---

## Task 8: Wire app startup

**Files:**
- Modify: `substrateos-api/app/main.py`
- Modify: `substrateos-api/app/deps.py`

- [ ] **Step 1: Add imports to `app/main.py`**

Add these two imports near the top of `app/main.py` with the other imports:

```python
from app.skills.store import SkillStore
from app.skills.service import SkillRouter
```

- [ ] **Step 2: Initialize SkillStore and SkillRouter in the lifespan function**

Inside the `lifespan` function in `app/main.py`, add after `app.state.metrics_store = MetricsStore()`:

```python
    app.state.skill_store = SkillStore()
    app.state.skill_router_svc = SkillRouter(
        skill_store=app.state.skill_store,
        llm=app.state.llm,
    )
```

In the `finally` block, add cleanup after `await app.state.metrics_store.aclose()`:

```python
        await app.state.skill_store.aclose()
```

- [ ] **Step 3: Verify the app starts cleanly**

```bash
cd substrateos-api && python -c "from app.main import app; print('ok')"
```
Expected: `ok` (no import errors)

- [ ] **Step 4: Commit**

```bash
git add substrateos-api/app/main.py substrateos-api/app/deps.py
git commit -m "feat(skills): initialize SkillStore + SkillRouter in app lifespan"
```

---

## Task 9: Frontend API client

**Files:**
- Create: `web/lib/skillsApi.ts`
- Modify: `web/lib/api.ts` — add `skill_used` to Answer type

- [ ] **Step 1: Add `skill_used` to Answer type in `web/lib/api.ts`**

Change the `Answer` type:

```typescript
export type SkillUsed = { id: string; slug: string; name: string };
export type Answer = {
  query_id: string; text: string; citations: Citation[];
  skill_used?: SkillUsed | null;
  debug?: AnswerDebug | null;
};
```

- [ ] **Step 2: Write `web/lib/skillsApi.ts`**

```typescript
// web/lib/skillsApi.ts
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export type SkillSummary = {
  id: string; slug: string; name: string; description: string;
  team: string; run_scope: "org" | "team"; enabled: boolean;
  steps: string[]; data_feeds: string[];
  rating: number; rating_count: number; run_count: number;
};

export type SkillFull = SkillSummary & { system_prompt: string; retrieval_config: object | null };

export type SkillCreate = {
  slug: string; name: string; description: string; team: string;
  run_scope?: "org" | "team"; enabled?: boolean;
  steps?: string[]; data_feeds?: string[]; system_prompt: string;
};

export type SkillUpdate = Partial<Omit<SkillCreate, "slug">>;

function getAdminKey(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem("adminKey");
}

async function easyAuthToken(): Promise<string | null> {
  return fetch("/.auth/me", { credentials: "include" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (Array.isArray(d) && d[0]?.id_token) || null)
    .catch(() => null);
}

async function userHeaders(): Promise<Record<string, string>> {
  if (DEBUG_AUTH) return { "x-debug-bypass-auth": DEBUG_AUTH };
  const t = await easyAuthToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function adminHeaders(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const key = getAdminKey();
  if (key) h["x-admin-key"] = key;
  if (DEBUG_AUTH) h["x-debug-bypass-auth"] = DEBUG_AUTH;
  else { const t = await easyAuthToken(); if (t) h["Authorization"] = `Bearer ${t}`; }
  return h;
}

// ── user-facing ──────────────────────────────────────────────────────────────

export async function getSkills(): Promise<SkillSummary[]> {
  try {
    const resp = await fetch(`${API_BASE}/skills`, { headers: await userHeaders() });
    if (!resp.ok) return [];
    return (await resp.json()) as SkillSummary[];
  } catch {
    return [];
  }
}

export async function rateSkill(id: string, rating: number): Promise<void> {
  await fetch(`${API_BASE}/skills/${id}/rate`, {
    method: "POST",
    headers: { ...(await userHeaders()), "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  }).catch(() => {});
}

// ── admin ─────────────────────────────────────────────────────────────────────

async function adminCall<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), ...(await adminHeaders()) },
  });
  if (resp.status === 403) throw new Error("admin key rejected");
  if (!resp.ok) throw new Error(`skills-api ${resp.status}: ${await resp.text()}`);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export async function adminListSkills(): Promise<SkillFull[]> {
  return adminCall<SkillFull[]>("/admin/skills");
}

export async function adminCreateSkill(body: SkillCreate): Promise<SkillFull> {
  return adminCall<SkillFull>("/admin/skills", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function adminUpdateSkill(id: string, body: SkillUpdate & { enabled?: boolean }): Promise<SkillFull> {
  return adminCall<SkillFull>(`/admin/skills/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function adminDeleteSkill(id: string): Promise<void> {
  return adminCall<void>(`/admin/skills/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 3: Commit**

```bash
git add web/lib/skillsApi.ts web/lib/api.ts
git commit -m "feat(skills): add frontend API client (skillsApi.ts) + skill_used type in api.ts"
```

---

## Task 10: Skills catalog page + CSS

**Files:**
- Create: `web/app/skills/page.tsx`
- Modify: `web/app/globals.css`

- [ ] **Step 1: Add CSS for skills catalog to `web/app/globals.css`**

Append to the end of `web/app/globals.css`:

```css
  /* ── Skills catalog ── */
  .skills-page{padding:40px 0 60px}
  .skills-header{margin-bottom:28px}
  .skills-header h1{font-family:var(--font-fraunces),serif;font-size:28px;font-weight:600;letter-spacing:-.01em;margin:0 0 6px}
  .skills-header p{color:var(--ink-dim);font-size:14.5px;margin:0}
  .skills-filter{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}
  .filter-chip{font-family:var(--font-mono),monospace;font-size:11px;letter-spacing:.04em;padding:6px 13px;border-radius:20px;border:1px solid var(--line);background:var(--surface);color:var(--ink-dim);cursor:pointer;transition:.15s;text-transform:uppercase}
  .filter-chip:hover{border-color:var(--ink-dim)}
  .filter-chip.active{background:var(--ink);color:var(--paper);border-color:var(--ink)}
  .skills-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  @media(max-width:900px){.skills-grid{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:600px){.skills-grid{grid-template-columns:1fr}}
  .skill-card{border:1px solid var(--line-soft);border-radius:14px;background:var(--surface);padding:20px;cursor:pointer;position:relative;transition:.18s;overflow:hidden}
  .skill-card:hover{transform:translateY(-2px);border-color:var(--ink-dim);box-shadow:var(--shadow)}
  .skill-team{font-family:var(--font-mono),monospace;font-size:10px;letter-spacing:.06em;text-transform:uppercase;padding:4px 9px;border-radius:20px;display:inline-block;margin-bottom:12px}
  .t-engineering{background:#e0ede8;color:#1f5c4d}
  .t-product{background:#f4e3da;color:#9a3b1f}
  .t-hr{background:#ece2f0;color:#6b3f7a}
  .t-marketing{background:#f6ecd2;color:#946112}
  .t-business{background:#dce8ee;color:#2a5a72}
  .t-default{background:var(--amber-bg);color:var(--amber)}
  .skill-card h3{font-family:var(--font-fraunces),serif;font-size:17px;font-weight:600;line-height:1.25;margin:0 0 7px}
  .skill-card p{font-size:13px;color:var(--ink-dim);line-height:1.5;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;margin:0}
  .skill-card-foot{display:flex;align-items:center;gap:10px;margin-top:14px;padding-top:12px;border-top:1px dashed var(--line-soft);font-family:var(--font-mono),monospace;font-size:11px;color:var(--ink-faint)}
  .skill-rating{color:var(--amber);font-weight:600}
  .skill-runs{margin-left:auto}
  .skill-card .star-badge{position:absolute;top:15px;right:16px;font-size:11px;font-family:var(--font-mono),monospace;color:var(--amber)}
  /* Skills modal */
  .skill-modal-bg{position:fixed;inset:0;background:rgba(26,22,17,.5);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;padding:24px;z-index:90}
  .skill-modal{background:var(--paper);border-radius:16px;max-width:580px;width:100%;max-height:86vh;overflow:auto;border:1px solid var(--line);box-shadow:0 30px 80px -20px rgba(0,0,0,.45)}
  .skill-modal-head{padding:22px 24px;border-bottom:1px solid var(--line);display:flex;align-items:flex-start;gap:12px}
  .skill-modal-head h3{font-family:var(--font-fraunces),serif;font-size:21px;font-weight:600;flex:1;margin:0}
  .skill-modal-x{cursor:pointer;font-family:var(--font-mono),monospace;font-size:16px;color:var(--ink-dim);border:1px solid var(--line);width:28px;height:28px;border-radius:8px;display:grid;place-items:center;flex-shrink:0;background:none}
  .skill-modal-body{padding:22px 24px}
  .skill-modal-label{font-family:var(--font-mono),monospace;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--amber);margin:18px 0 9px}
  .skill-modal-label:first-child{margin-top:0}
  .skill-steps{list-style:none;padding:0;margin:0}
  .skill-steps li{display:flex;gap:11px;font-size:13.5px;margin-bottom:8px}
  .skill-steps .n{font-family:var(--font-mono),monospace;font-size:10px;background:var(--ink);color:var(--paper);width:20px;height:20px;border-radius:6px;display:grid;place-items:center;flex-shrink:0;margin-top:1px}
  .skill-feeds{display:flex;flex-wrap:wrap;gap:7px}
  .skill-feed{font-family:var(--font-mono),monospace;font-size:11px;border:1px solid var(--line);background:var(--surface);padding:5px 10px;border-radius:20px;color:var(--ink-dim)}
  .skill-modal-foot{display:flex;gap:9px;margin-top:22px;padding-top:18px;border-top:1px solid var(--line-soft)}
  .skill-btn-primary{font-family:var(--font-mono),monospace;font-size:12px;padding:10px 18px;border-radius:9px;background:var(--ink);color:var(--paper);border:none;cursor:pointer;transition:.15s}
  .skill-btn-primary:hover{background:#2a2520}
  .skill-btn-ghost{font-family:var(--font-mono),monospace;font-size:12px;padding:10px 18px;border-radius:9px;background:transparent;color:var(--ink-dim);border:1px solid var(--line);cursor:pointer;transition:.15s}
  .skill-btn-ghost:hover{background:var(--paper-2)}
  .skills-empty{padding:60px 0;text-align:center;color:var(--ink-faint);font-size:14px}
```

- [ ] **Step 2: Write `web/app/skills/page.tsx`**

```tsx
// web/app/skills/page.tsx
"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSkills, SkillSummary } from "@/lib/skillsApi";

const TEAM_COLORS: Record<string, string> = {
  "Engineering": "t-engineering",
  "Product": "t-product",
  "HR": "t-hr",
  "HR / People": "t-hr",
  "Marketing": "t-marketing",
  "Business / Ops": "t-business",
  "Business": "t-business",
};

function teamClass(team: string): string {
  return TEAM_COLORS[team] ?? "t-default";
}

function StarRating({ rating }: { rating: number }) {
  const stars = Math.round(rating);
  return (
    <span className="skill-rating">
      {"★".repeat(stars)}{"☆".repeat(5 - stars)} {rating > 0 ? rating.toFixed(1) : "–"}
    </span>
  );
}

function SkillModal({ skill, onClose, onRun }: {
  skill: SkillSummary; onClose: () => void; onRun: (skill: SkillSummary) => void;
}) {
  return (
    <div className="skill-modal-bg" onClick={onClose}>
      <div className="skill-modal" onClick={(e) => e.stopPropagation()}>
        <div className="skill-modal-head">
          <h3>{skill.name}</h3>
          <button className="skill-modal-x" onClick={onClose}>✕</button>
        </div>
        <div className="skill-modal-body">
          <div className="skill-modal-label">What this skill does</div>
          <p style={{ fontSize: 14, color: "var(--ink-dim)", lineHeight: 1.55 }}>{skill.description}</p>

          {skill.steps.length > 0 && (
            <>
              <div className="skill-modal-label">Steps it runs</div>
              <ol className="skill-steps">
                {skill.steps.map((step, i) => (
                  <li key={i}><span className="n">{i + 1}</span><div>{step}</div></li>
                ))}
              </ol>
            </>
          )}

          {skill.data_feeds.length > 0 && (
            <>
              <div className="skill-modal-label">Data it reads (ACL-scoped)</div>
              <div className="skill-feeds">
                {skill.data_feeds.map((f) => <span key={f} className="skill-feed">{f}</span>)}
              </div>
            </>
          )}

          <div className="skill-modal-foot">
            <button className="skill-btn-primary" onClick={() => { onRun(skill); onClose(); }}>
              ▶ Run skill
            </button>
            <button className="skill-btn-ghost" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [activeTeam, setActiveTeam] = useState("All");
  const [modal, setModal] = useState<SkillSummary | null>(null);
  const router = useRouter();

  useEffect(() => { getSkills().then(setSkills); }, []);

  const teams = ["All", ...Array.from(new Set(skills.map((s) => s.team)))];
  const visible = activeTeam === "All" ? skills : skills.filter((s) => s.team === activeTeam);

  const handleRun = (skill: SkillSummary) => {
    router.push(`/?prefill=${encodeURIComponent("/" + skill.slug + " ")}`);
  };

  return (
    <main className="main">
      <div style={{ padding: "0 28px" }}>
        <div className="skills-page">
          <div className="skills-header">
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "2px", textTransform: "uppercase", color: "var(--amber)", marginBottom: 10 }}>
              Org Skills
            </div>
            <h1>Your team&apos;s proven workflows</h1>
            <p>Reusable skills distilled from how your org does recurring tasks. Run with <code style={{ fontFamily: "var(--font-mono)", fontSize: 12, background: "var(--panel)", padding: "2px 6px", borderRadius: 5 }}>/skill-name</code> in chat or click Run below.</p>
          </div>

          <div className="skills-filter">
            {teams.map((t) => (
              <button
                key={t}
                className={`filter-chip${activeTeam === t ? " active" : ""}`}
                onClick={() => setActiveTeam(t)}
              >
                {t}
              </button>
            ))}
          </div>

          {visible.length === 0 ? (
            <div className="skills-empty">No skills available yet. Ask an admin to add some.</div>
          ) : (
            <div className="skills-grid">
              {visible.map((skill) => (
                <div key={skill.id} className="skill-card" onClick={() => setModal(skill)}>
                  {skill.rating > 0 && (
                    <span className="star-badge">★ {skill.rating.toFixed(1)}</span>
                  )}
                  <span className={`skill-team ${teamClass(skill.team)}`}>{skill.team}</span>
                  <h3>{skill.name}</h3>
                  <p>{skill.description}</p>
                  <div className="skill-card-foot">
                    <StarRating rating={skill.rating} />
                    <span className="skill-runs">{skill.run_count.toLocaleString()} runs</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      {modal && (
        <SkillModal skill={modal} onClose={() => setModal(null)} onRun={handleRun} />
      )}
    </main>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add web/app/skills/page.tsx web/app/globals.css
git commit -m "feat(skills): add /skills catalog page with team filter, card grid, and run modal"
```

---

## Task 11: Admin skills page + admin nav

**Files:**
- Create: `web/app/admin/skills/page.tsx`
- Modify: `web/app/admin/layout.tsx`
- Modify: `web/app/globals.css`

- [ ] **Step 1: Add admin skills table CSS to `web/app/globals.css`**

Append to the end of `web/app/globals.css`:

```css
  /* ── Admin skills table ── */
  .skills-table{width:100%;border-collapse:collapse;font-size:13.5px}
  .skills-table th{font-family:var(--font-mono),monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-faint);padding:10px 14px;text-align:left;border-bottom:1px solid var(--line-soft);font-weight:500}
  .skills-table td{padding:12px 14px;border-bottom:1px solid var(--line-soft);vertical-align:middle}
  .skills-table tr:last-child td{border-bottom:none}
  .skill-row-name{font-weight:600;color:var(--ink)}
  .skill-row-slug{font-family:var(--font-mono),monospace;font-size:11px;color:var(--ink-faint);display:block;margin-top:2px}
  .skill-row-actions{display:flex;gap:8px}
  .skill-action-btn{font-family:var(--font-mono),monospace;font-size:11px;padding:5px 10px;border-radius:7px;border:1px solid var(--line);background:transparent;color:var(--ink-dim);cursor:pointer;transition:.15s}
  .skill-action-btn:hover{background:var(--paper-2);border-color:var(--ink-dim)}
  .skill-action-btn.del{color:var(--rose);border-color:var(--rose)}
  .skill-action-btn.del:hover{background:#fdf0f2}
  /* Skills form modal */
  .skill-form-modal{position:fixed;inset:0;background:rgba(26,22,17,.5);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;padding:24px;z-index:90}
  .skill-form-card{background:var(--paper);border-radius:16px;max-width:620px;width:100%;max-height:90vh;overflow:auto;border:1px solid var(--line);box-shadow:0 30px 80px -20px rgba(0,0,0,.45);padding:28px}
  .skill-form-card h3{font-family:var(--font-fraunces),serif;font-size:20px;font-weight:600;margin:0 0 20px}
  .skill-form-row{margin-bottom:16px}
  .skill-form-label{font-family:var(--font-mono),monospace;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:6px;display:block}
  .skill-form-input{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:9px;font-family:inherit;font-size:13.5px;background:var(--surface);color:var(--ink);outline:none;transition:.15s}
  .skill-form-input:focus{border-color:var(--amber)}
  .skill-form-textarea{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:9px;font-family:var(--font-mono),monospace;font-size:12.5px;background:var(--surface);color:var(--ink);outline:none;resize:vertical;transition:.15s}
  .skill-form-textarea:focus{border-color:var(--amber)}
  .skill-form-foot{display:flex;gap:10px;margin-top:22px;justify-content:flex-end}
  .skill-list-row{display:flex;gap:8px;margin-bottom:8px;align-items:center}
  .skill-list-row input{flex:1;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px;background:var(--surface);color:var(--ink)}
  .skill-list-remove{font-size:14px;color:var(--ink-faint);cursor:pointer;background:none;border:none;padding:4px}
  .skill-list-add{font-family:var(--font-mono),monospace;font-size:11px;color:var(--teal);background:none;border:none;cursor:pointer;padding:4px 0;text-decoration:underline}
```

- [ ] **Step 2: Add Skills nav item to `web/app/admin/layout.tsx`**

In the `NAV` array in `web/app/admin/layout.tsx`, add a new item to the `"Connect"` group's `items` array, after the Permissions item:

```typescript
      {
        href: "/admin/skills",
        label: "Org Skills",
        icon: (
          <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
          </svg>
        ),
      },
```

- [ ] **Step 3: Write `web/app/admin/skills/page.tsx`**

```tsx
// web/app/admin/skills/page.tsx
"use client";
import { useEffect, useState } from "react";
import {
  adminListSkills, adminCreateSkill, adminUpdateSkill, adminDeleteSkill,
  SkillFull, SkillCreate,
} from "@/lib/skillsApi";

type FormState = {
  slug: string; name: string; description: string; team: string;
  run_scope: "org" | "team"; enabled: boolean;
  steps: string[]; data_feeds: string[]; system_prompt: string;
};

const EMPTY_FORM: FormState = {
  slug: "", name: "", description: "", team: "", run_scope: "org",
  enabled: true, steps: [], data_feeds: [], system_prompt: "",
};

function toSlug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function StringList({
  label, items, onChange,
}: { label: string; items: string[]; onChange: (v: string[]) => void }) {
  const set = (i: number, v: string) => { const a = [...items]; a[i] = v; onChange(a); };
  const add = () => onChange([...items, ""]);
  const remove = (i: number) => onChange(items.filter((_, j) => j !== i));
  return (
    <div className="skill-form-row">
      <label className="skill-form-label">{label}</label>
      {items.map((v, i) => (
        <div key={i} className="skill-list-row">
          <input value={v} onChange={(e) => set(i, e.target.value)} placeholder="Enter value…" />
          <button className="skill-list-remove" onClick={() => remove(i)} type="button">✕</button>
        </div>
      ))}
      <button className="skill-list-add" onClick={add} type="button">+ Add</button>
    </div>
  );
}

function SkillForm({
  initial, onSave, onClose, saving,
}: { initial: FormState; onSave: (f: FormState) => void; onClose: () => void; saving: boolean }) {
  const [f, setF] = useState<FormState>(initial);
  const set = (k: keyof FormState, v: unknown) => setF((p) => ({ ...p, [k]: v }));

  const handleNameChange = (name: string) => {
    setF((p) => ({ ...p, name, slug: p.slug || toSlug(name) }));
  };

  return (
    <div className="skill-form-modal" onClick={onClose}>
      <div className="skill-form-card" onClick={(e) => e.stopPropagation()}>
        <h3>{initial.slug ? "Edit Skill" : "Add Skill"}</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div className="skill-form-row">
            <label className="skill-form-label">Name</label>
            <input className="skill-form-input" value={f.name} onChange={(e) => handleNameChange(e.target.value)} placeholder="SEO Research" />
          </div>
          <div className="skill-form-row">
            <label className="skill-form-label">Slug</label>
            <input className="skill-form-input" value={f.slug} onChange={(e) => set("slug", e.target.value)} placeholder="seo-research" style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
          </div>
        </div>
        <div className="skill-form-row">
          <label className="skill-form-label">Description</label>
          <textarea className="skill-form-textarea" rows={2} value={f.description} onChange={(e) => set("description", e.target.value)} placeholder="What this skill does in 1–2 sentences." />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
          <div className="skill-form-row">
            <label className="skill-form-label">Team</label>
            <input className="skill-form-input" value={f.team} onChange={(e) => set("team", e.target.value)} placeholder="Engineering" />
          </div>
          <div className="skill-form-row">
            <label className="skill-form-label">Scope</label>
            <select className="skill-form-input" value={f.run_scope} onChange={(e) => set("run_scope", e.target.value as "org" | "team")}>
              <option value="org">Org-wide</option>
              <option value="team">Team-only</option>
            </select>
          </div>
          <div className="skill-form-row" style={{ paddingTop: 22 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13.5 }}>
              <input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />
              Enabled
            </label>
          </div>
        </div>
        <StringList label="Steps" items={f.steps} onChange={(v) => set("steps", v)} />
        <StringList label="Data Feeds" items={f.data_feeds} onChange={(v) => set("data_feeds", v)} />
        <div className="skill-form-row">
          <label className="skill-form-label">System Prompt</label>
          <textarea className="skill-form-textarea" rows={6} value={f.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} placeholder="Instructions injected into the query when this skill is active…" />
        </div>
        <div className="skill-form-foot">
          <button className="skill-btn-ghost" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="skill-btn-primary" onClick={() => onSave(f)} disabled={saving}>
            {saving ? "Saving…" : "Save skill"}
          </button>
        </div>
      </div>
    </div>
  );
}

const TEAM_COLORS: Record<string, string> = {
  "Engineering": "t-engineering", "Product": "t-product",
  "HR": "t-hr", "HR / People": "t-hr", "Marketing": "t-marketing",
  "Business / Ops": "t-business", "Business": "t-business",
};
function teamClass(team: string) { return TEAM_COLORS[team] ?? "t-default"; }

export default function AdminSkillsPage() {
  const [skills, setSkills] = useState<SkillFull[]>([]);
  const [err, setErr] = useState(false);
  const [form, setForm] = useState<{ open: boolean; editing: SkillFull | null }>({ open: false, editing: null });
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const load = () => adminListSkills().then(setSkills).catch(() => setErr(true));
  useEffect(() => { load(); }, []);

  const handleToggle = async (skill: SkillFull) => {
    setSkills((p) => p.map((s) => s.id === skill.id ? { ...s, enabled: !s.enabled } : s));
    try {
      await adminUpdateSkill(skill.id, { enabled: !skill.enabled });
    } catch {
      setSkills((p) => p.map((s) => s.id === skill.id ? { ...s, enabled: skill.enabled } : s));
    }
  };

  const handleSave = async (f: FormState) => {
    setSaving(true);
    try {
      if (form.editing) {
        const updated = await adminUpdateSkill(form.editing.id, f);
        setSkills((p) => p.map((s) => s.id === updated.id ? updated : s));
      } else {
        const created = await adminCreateSkill(f as SkillCreate);
        setSkills((p) => [...p, created]);
      }
      setForm({ open: false, editing: null });
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await adminDeleteSkill(id);
      setSkills((p) => p.filter((s) => s.id !== id));
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setDeleteConfirm(null);
    }
  };

  const openAdd = () => setForm({ open: true, editing: null });
  const openEdit = (s: SkillFull) => setForm({ open: true, editing: s });
  const closeForm = () => setForm({ open: false, editing: null });

  const initialForm = form.editing
    ? { slug: form.editing.slug, name: form.editing.name, description: form.editing.description,
        team: form.editing.team, run_scope: form.editing.run_scope, enabled: form.editing.enabled,
        steps: form.editing.steps, data_feeds: form.editing.data_feeds, system_prompt: form.editing.system_prompt }
    : EMPTY_FORM;

  return (
    <div className="admin-page">
      <div className="admin-wrap">
        <header className="admin-head" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div>
            <h1>Org Skills</h1>
            <p>Manage reusable skills that any employee can invoke via <code style={{ fontFamily: "var(--font-mono)", fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>/skill-name</code> in chat or auto-detected by the query pipeline.</p>
          </div>
          <button className="skill-btn-primary" onClick={openAdd} style={{ flexShrink: 0, marginTop: 4 }}>+ Add skill</button>
        </header>

        {err && <div className="admin-note">Couldn&apos;t load skills. Check the admin key / API.</div>}

        {skills.length === 0 && !err ? (
          <div style={{ padding: "40px 0", textAlign: "center", color: "var(--ink-faint)", fontSize: 14 }}>
            No skills yet — click &quot;Add skill&quot; to create the first one.
          </div>
        ) : (
          <table className="skills-table">
            <thead>
              <tr>
                <th>Skill</th><th>Team</th><th>Scope</th>
                <th>Rating</th><th>Runs</th><th>Enabled</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {skills.map((s) => (
                <tr key={s.id}>
                  <td>
                    <span className="skill-row-name">{s.name}</span>
                    <span className="skill-row-slug">/{s.slug}</span>
                  </td>
                  <td><span className={`skill-team ${teamClass(s.team)}`}>{s.team}</span></td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-faint)" }}>{s.run_scope}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                    {s.rating > 0 ? `★ ${s.rating.toFixed(1)}` : "—"}
                  </td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{s.run_count}</td>
                  <td>
                    <button
                      className={`sw${s.enabled ? " on" : ""}`}
                      aria-label={s.enabled ? "Disable" : "Enable"}
                      onClick={() => handleToggle(s)}
                    />
                  </td>
                  <td>
                    <div className="skill-row-actions">
                      <button className="skill-action-btn" onClick={() => openEdit(s)}>Edit</button>
                      <button className="skill-action-btn del" onClick={() => setDeleteConfirm(s.id)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {form.open && (
        <SkillForm initial={initialForm} onSave={handleSave} onClose={closeForm} saving={saving} />
      )}

      {deleteConfirm && (
        <div className="skill-form-modal" onClick={() => setDeleteConfirm(null)}>
          <div className="skill-form-card" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3>Delete skill?</h3>
            <p style={{ fontSize: 14, color: "var(--ink-dim)", marginBottom: 20 }}>
              This cannot be undone. Users will no longer be able to invoke this skill.
            </p>
            <div className="skill-form-foot">
              <button className="skill-btn-ghost" onClick={() => setDeleteConfirm(null)}>Cancel</button>
              <button className="skill-btn-primary" style={{ background: "var(--rose)" }} onClick={() => handleDelete(deleteConfirm)}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add web/app/admin/skills/page.tsx web/app/admin/layout.tsx web/app/globals.css
git commit -m "feat(skills): add /admin/skills CRUD page + Skills nav item in admin layout"
```

---

## Task 12: Add Skills to main nav + Chat integration

**Files:**
- Modify: `web/components/Chat.tsx`

The main nav (left rail) lives inside `Chat.tsx`. This task adds: (1) a Skills nav link in the rail, (2) reading `?prefill` on mount, (3) `/`-autocomplete in the chat input, and (4) a `skill_used` badge on answered messages.

- [ ] **Step 1: Add Skills nav link to the rail in `Chat.tsx`**

In `Chat.tsx`, find the section where the nav links are rendered (the `.nav` div in the rail). Add a Skills link alongside History/Discover. Locate the nav links block and add:

```tsx
<button className={view === "skills" ? "active" : ""} onClick={() => setView("skills")}>
  <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
  </svg>
  Skills
</button>
```

Add `"skills"` to the `view` type union. When `view === "skills"`, render the `<SkillsPage />` component (import from `@/app/skills/page`).

- [ ] **Step 2: Read `?prefill` URL param on mount in `Chat.tsx`**

Add `useSearchParams` import:
```tsx
import { useSearchParams } from "next/navigation";
```

Inside the Chat component, add after the state declarations:
```tsx
const searchParams = useSearchParams();
useEffect(() => {
  const prefill = searchParams.get("prefill");
  if (prefill) setInput(prefill);
}, [searchParams]);
```

- [ ] **Step 3: Add `/`-autocomplete for skills in `Chat.tsx`**

Add state and skill fetch near the top of the Chat component:
```tsx
const [skills, setSkills] = useState<SkillSummary[]>([]);
const [autocomplete, setAutocomplete] = useState<SkillSummary[]>([]);
```

Import `getSkills` and `SkillSummary` from `@/lib/skillsApi`:
```tsx
import { getSkills, SkillSummary } from "@/lib/skillsApi";
```

Fetch skills on mount:
```tsx
useEffect(() => { getSkills().then(setSkills); }, []);
```

Add an effect that updates autocomplete when input starts with `/`:
```tsx
useEffect(() => {
  if (!input.startsWith("/")) { setAutocomplete([]); return; }
  const q = input.slice(1).toLowerCase();
  setAutocomplete(skills.filter(
    (s) => s.slug.includes(q) || s.name.toLowerCase().includes(q)
  ).slice(0, 5));
}, [input, skills]);
```

Render the autocomplete dropdown above the textarea (inside the input container div):
```tsx
{autocomplete.length > 0 && (
  <div className="skill-autocomplete">
    {autocomplete.map((s) => (
      <button
        key={s.id}
        className="skill-ac-item"
        onMouseDown={(e) => { e.preventDefault(); setInput(`/${s.slug} `); setAutocomplete([]); }}
      >
        <span className="skill-ac-name">/{s.slug}</span>
        <span className="skill-ac-desc">{s.name}</span>
      </button>
    ))}
  </div>
)}
```

Add these CSS classes to `web/app/globals.css`:
```css
  .skill-autocomplete{position:absolute;bottom:100%;left:0;right:0;background:var(--surface-2);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);overflow:hidden;margin-bottom:6px;z-index:20}
  .skill-ac-item{display:flex;align-items:baseline;gap:10px;width:100%;padding:10px 14px;background:none;border:none;text-align:left;cursor:pointer;transition:.12s;border-bottom:1px solid var(--line-soft)}
  .skill-ac-item:last-child{border-bottom:none}
  .skill-ac-item:hover{background:var(--paper-2)}
  .skill-ac-name{font-family:var(--font-mono),monospace;font-size:12px;color:var(--amber);font-weight:600}
  .skill-ac-desc{font-size:12.5px;color:var(--ink-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
```

- [ ] **Step 4: Add `skill_used` badge on answered messages in `Chat.tsx`**

In the message rendering section, after the answer text and before the citations/debug, add:
```tsx
{turn.answer?.skill_used && (
  <div className="skill-used-badge">
    ▶ via {turn.answer.skill_used.name}
  </div>
)}
```

Add CSS to `web/app/globals.css`:
```css
  .skill-used-badge{display:inline-block;font-family:var(--font-mono),monospace;font-size:10.5px;color:var(--teal);background:var(--teal-bg);border:1px solid rgba(15,137,126,.25);padding:3px 10px;border-radius:20px;margin-bottom:10px}
```

- [ ] **Step 5: Check TypeScript compiles**

```bash
cd web && pnpm tsc --noEmit 2>&1 | head -30
```
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add web/components/Chat.tsx web/app/globals.css
git commit -m "feat(skills): add Skills nav, ?prefill URL param, /autocomplete, and skill_used badge to Chat"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Data model (Task 1) ✓ · Store (Task 2) ✓ · API user+admin (Task 3) ✓ · Skill routing (Task 5) ✓ · Query integration (Tasks 6+7) ✓ · App wiring (Task 8) ✓ · Frontend catalog (Task 10) ✓ · Admin page (Task 11) ✓ · Chat integration (Task 12) ✓
- [x] **Placeholders:** None — every step has complete code
- [x] **Type consistency:** `ResolvedSkill` used in Tasks 5, 6, 7. `SkillSummary`/`SkillFull`/`SkillCreate` consistent across Tasks 9, 10, 11, 12. `Answer.skill_used: dict | None` set in Task 4 and read in Task 12.
- [x] **`skill_used` badge:** requires `Answer.skill_used` from Task 4 + `skill_context` pass-through from Task 6 — dependency captured in task order.
- [x] **`get_skill_store` / `get_skill_router_svc`:** defined in Task 3 (deps.py), used in Tasks 7+8 — consistent names.
