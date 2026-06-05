from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import RedisError

from app.domain.skill import Skill, SkillCreate, SkillUpdate
from app.skills.store import SkillStore, SkillStorePersistenceError


def _make_redis(data: dict | None = None) -> MagicMock:
    """Return a mock Redis client pre-populated with `data` (field→json)."""
    r = MagicMock()
    _store: dict[str, str] = {k: v for k, v in (data or {}).items()}

    async def hgetall(key):
        return dict(_store)

    async def hset(key, field, value):
        _store[field] = value

    async def hget(key, field):
        return _store.get(field)

    async def hdel(key, field):
        existed = field in _store
        _store.pop(field, None)
        return 1 if existed else 0

    async def delete(*keys):
        pass

    r.hgetall = hgetall
    r.hset = hset
    r.hget = hget
    r.hdel = hdel
    r.delete = delete
    r.get = AsyncMock(return_value=None)
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
    assert skill.id
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
    assert updated.slug == "test-skill"


@pytest.mark.asyncio
async def test_create_raises_when_no_redis_backend():
    # Deploy with no AZURE_REDIS_HOST -> _r is None -> writes must fail loudly,
    # not return a phantom skill that was never persisted.
    store = SkillStore(client=_make_redis())
    store._r = None
    data = SkillCreate(slug="x", name="X", description="D", team="T", system_prompt="S.")
    with pytest.raises(SkillStorePersistenceError):
        await store.create(data)


@pytest.mark.asyncio
async def test_create_raises_when_hset_fails():
    r = _make_redis()

    async def boom(*a, **k):
        raise RedisError("connection refused")

    r.hset = boom
    store = SkillStore(client=r)
    data = SkillCreate(slug="x", name="X", description="D", team="T", system_prompt="S.")
    with pytest.raises(SkillStorePersistenceError):
        await store.create(data)


@pytest.mark.asyncio
async def test_update_raises_when_no_redis_backend():
    store = SkillStore(client=_make_redis())
    store._r = None
    with pytest.raises(SkillStorePersistenceError):
        await store.update("some-id", SkillUpdate(name="New"))


@pytest.mark.asyncio
async def test_update_raises_when_hset_fails():
    skill_id = str(uuid.uuid4())
    r = _make_redis({skill_id: _skill_json(id=skill_id)})

    async def boom(*a, **k):
        raise RedisError("connection refused")

    r.hset = boom
    store = SkillStore(client=r)
    with pytest.raises(SkillStorePersistenceError):
        await store.update(skill_id, SkillUpdate(name="New Name"))


@pytest.mark.asyncio
async def test_delete_removes_skill():
    skill_id = str(uuid.uuid4())
    r = _make_redis({skill_id: _skill_json(id=skill_id)})
    store = SkillStore(client=r)
    deleted = await store.delete(skill_id)
    assert deleted is True
    assert await store.get_by_id(skill_id) is None
