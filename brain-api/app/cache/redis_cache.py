from __future__ import annotations

import hashlib
import json
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings

logger = logging.getLogger(__name__)

_CACHE_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)


def _embed_key(text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()
    return f"cache:embed:{h}"


class RedisCache:
    def __init__(self) -> None:
        s = get_settings()
        # Caching is optional: with no host configured (e.g. the India deploy has no
        # Redis), the cache becomes a no-op — callers already tolerate a miss.
        if not s.azure_redis_host:
            self._r = None
            return
        self._r = redis.Redis(
            host=s.azure_redis_host,
            port=s.azure_redis_port,
            ssl=s.azure_redis_ssl,
            password=s.redis_key,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    async def aclose(self) -> None:
        if self._r is not None:
            await self._r.aclose()

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        if self._r is None:
            return
        try:
            await self._r.set(name=key, value=json.dumps(value), ex=ttl_seconds)
        except _CACHE_ERRORS as e:
            logger.warning("Redis set_json failed (key=%s); skipping cache write: %s", key, e)

    async def get_json(self, key: str) -> dict | None:
        if self._r is None:
            return None
        try:
            v = await self._r.get(key)
        except _CACHE_ERRORS as e:
            logger.warning("Redis get_json failed (key=%s); treating as cache miss: %s", key, e)
            return None
        return json.loads(v) if v else None

    async def set_embedding(self, text: str, vec: list[float], ttl_seconds: int = 86400) -> None:
        await self.set_json(_embed_key(text), {"v": vec}, ttl_seconds=ttl_seconds)

    async def get_embedding(self, text: str) -> list[float] | None:
        d = await self.get_json(_embed_key(text))
        return d["v"] if d else None
