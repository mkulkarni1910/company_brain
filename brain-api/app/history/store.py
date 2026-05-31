from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.history import HistoryEntry
from app.domain.identity import User

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)
_MAX = 50


def _key(user: User) -> str:
    return f"history:{user.tenant_id}:{user.user_id}"


class HistoryStore:
    """Per-user recent-query list backed by a Redis list. Best-effort: all
    operations swallow Redis errors so history never breaks the query path."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        if client is not None:
            self._r = client
        else:
            s = get_settings()
            self._r = redis.Redis(
                host=s.azure_redis_host,
                port=s.azure_redis_port,
                ssl=s.azure_redis_ssl,
                password=s.redis_key,
                decode_responses=True,
            )

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._r.aclose()

    async def add(self, *, user: User, query: str, query_id: str) -> None:
        entry = HistoryEntry(query=query, query_id=query_id, ts=datetime.now(UTC))
        key = _key(user)
        try:
            async with self._r.pipeline(transaction=False) as pipe:
                pipe.lpush(key, entry.model_dump_json())
                pipe.ltrim(key, 0, _MAX - 1)
                await pipe.execute()
        except _ERRORS as e:
            logger.warning("history add failed (key=%s): %s", key, e)

    async def recent(self, *, user: User, limit: int = _MAX) -> list[HistoryEntry]:
        key = _key(user)
        try:
            raw = await self._r.lrange(key, 0, max(0, limit - 1))
        except _ERRORS as e:
            logger.warning("history recent failed (key=%s): %s", key, e)
            return []
        out: list[HistoryEntry] = []
        for item in raw:
            try:
                out.append(HistoryEntry.model_validate_json(item))
            except Exception:  # noqa: BLE001 - skip corrupt entries
                continue
        return out
