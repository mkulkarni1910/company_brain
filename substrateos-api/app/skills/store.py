from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.skill import Skill, SkillCreate, SkillUpdate

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)


class SkillStorePersistenceError(RuntimeError):
    """Raised when a write cannot be persisted (no Redis backend, or Redis is
    unreachable). Reads degrade gracefully to empty; writes must fail loudly so
    callers never get a phantom 201 for a skill that was never stored."""

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

    async def create(self, data: SkillCreate) -> Skill:
        if self._r is None:
            raise SkillStorePersistenceError("no Redis backend configured (AZURE_REDIS_HOST unset)")
        existing = await self.get_by_slug(data.slug)
        if existing is not None:
            raise ValueError(f"slug '{data.slug}' already exists (id={existing.id})")
        now = datetime.now(UTC)
        skill = Skill(
            id=str(uuid.uuid4()),
            slug=data.slug, name=data.name, description=data.description,
            team=data.team, run_scope=data.run_scope, workflow=data.workflow, enabled=data.enabled,
            steps=data.steps, data_feeds=data.data_feeds,
            system_prompt=data.system_prompt, retrieval_config=data.retrieval_config,
            created_at=now, updated_at=now,
        )
        try:
            await self._r.hset(_DATA_KEY, skill.id, skill.model_dump_json())
        except _ERRORS as e:
            raise SkillStorePersistenceError(f"failed to persist skill: {e}") from e
        await self._invalidate_catalog()
        return skill

    async def update(self, skill_id: str, data: SkillUpdate) -> Skill | None:
        if self._r is None:
            raise SkillStorePersistenceError("no Redis backend configured (AZURE_REDIS_HOST unset)")
        skill = await self.get_by_id(skill_id)
        if skill is None:
            return None
        patch = data.model_dump(exclude_none=True)
        updated = skill.model_copy(update={**patch, "updated_at": datetime.now(UTC)})
        try:
            await self._r.hset(_DATA_KEY, skill_id, updated.model_dump_json())
        except _ERRORS as e:
            raise SkillStorePersistenceError(f"failed to persist skill: {e}") from e
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
