from __future__ import annotations

import hashlib
import json
from functools import lru_cache

import redis.asyncio as redis

from app.config import get_settings


@lru_cache(maxsize=1)
def _pool() -> redis.Redis:
    s = get_settings()
    # Azure Cache for Redis: use AAD via the access key OR managed identity (preview).
    # For Phase 1 simplicity, use the primary key from Settings (loaded from .env);
    # this moves to Key Vault in Phase 4 hardening.
    return redis.Redis(
        host=s.azure_redis_host,
        port=s.azure_redis_port,
        ssl=s.azure_redis_ssl,
        password=s.redis_key,
        decode_responses=True,
    )


def _embed_key(text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()
    return f"cache:embed:{h}"


class RedisCache:
    def __init__(self) -> None:
        self._r = _pool()

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        await self._r.set(name=key, value=json.dumps(value), ex=ttl_seconds)

    async def get_json(self, key: str) -> dict | None:
        v = await self._r.get(key)
        return json.loads(v) if v else None

    async def set_embedding(self, text: str, vec: list[float], ttl_seconds: int = 86400) -> None:
        await self.set_json(_embed_key(text), {"v": vec}, ttl_seconds=ttl_seconds)

    async def get_embedding(self, text: str) -> list[float] | None:
        d = await self.get_json(_embed_key(text))
        return d["v"] if d else None
