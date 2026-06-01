from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)
_TTL = 8 * 86400  # keep ~8 days of daily buckets


def _days(n: int) -> list[str]:
    today = datetime.now(UTC).date()
    return [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


class MetricsStore:
    """Cheap real Overview metrics: daily query counters + distinct-user HLLs.
    record_query is fire-and-forget; reads return None when unavailable (UI shows '—')."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        if client is not None:
            self._r = client
        else:
            s = get_settings()
            self._r = redis.Redis(host=s.azure_redis_host, port=s.azure_redis_port,
                ssl=s.azure_redis_ssl, password=s.redis_key, decode_responses=True)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._r.aclose()

    async def record_query(self, tenant: str, user_id: str) -> None:
        d = _days(1)[0]
        try:
            qk = f"metrics:queries:{tenant}:{d}"
            uk = f"metrics:users:{tenant}:{d}"
            await self._r.incr(qk)
            await self._r.expire(qk, _TTL)
            await self._r.pfadd(uk, user_id)
            await self._r.expire(uk, _TTL)
        except _ERRORS as e:
            logger.warning("record_query failed: %s", e)

    async def queries_last_7d(self, tenant: str) -> int | None:
        keys = [f"metrics:queries:{tenant}:{d}" for d in _days(7)]
        try:
            vals = await self._r.mget(keys)
        except _ERRORS as e:
            logger.warning("queries_last_7d failed: %s", e)
            return None
        return sum(int(v) for v in vals if v)

    async def active_users_7d(self, tenant: str) -> int | None:
        keys = [f"metrics:users:{tenant}:{d}" for d in _days(7)]
        try:
            return int(await self._r.pfcount(*keys))
        except _ERRORS as e:
            logger.warning("active_users_7d failed: %s", e)
            return None
