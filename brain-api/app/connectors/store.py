from __future__ import annotations

import contextlib
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.connectors.models import ActivityEntry, Connection, SyncJob

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)
_ACTIVITY_MAX = 50
_JOB_TTL = 86400


def _conn_key(tenant: str) -> str: return f"connections:{tenant}"
def _job_key(tenant: str, job_id: str) -> str: return f"connector:job:{tenant}:{job_id}"
def _activity_key(tenant: str) -> str: return f"admin:activity:{tenant}"


class ConnectionStore:
    """Redis-backed connector state. Best-effort: reads degrade to empty/None."""

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

    async def put_connection(self, c: Connection) -> None:
        try:
            await self._r.hset(_conn_key(c.tenant_id), c.connection_id, c.model_dump_json())
        except _ERRORS as e:
            logger.warning("put_connection failed: %s", e)

    async def get_connection(self, tenant: str, connection_id: str) -> Connection | None:
        for c in await self.list_connections(tenant):
            if c.connection_id == connection_id:
                return c
        return None

    async def list_connections(self, tenant: str) -> list[Connection]:
        try:
            raw = await self._r.hgetall(_conn_key(tenant))
        except _ERRORS as e:
            logger.warning("list_connections failed: %s", e)
            return []
        out: list[Connection] = []
        for v in raw.values():
            with contextlib.suppress(Exception):
                out.append(Connection.model_validate_json(v))
        return out

    async def delete_connection(self, tenant: str, connection_id: str) -> None:
        with contextlib.suppress(*_ERRORS):
            await self._r.hdel(_conn_key(tenant), connection_id)

    async def put_job(self, j: SyncJob) -> None:
        with contextlib.suppress(*_ERRORS):
            await self._r.set(_job_key(j.tenant_id, j.job_id), j.model_dump_json(), ex=_JOB_TTL)

    async def get_job(self, tenant: str, job_id: str) -> SyncJob | None:
        try:
            v = await self._r.get(_job_key(tenant, job_id))
        except _ERRORS as e:
            logger.warning("get_job failed: %s", e)
            return None
        if not v:
            return None
        with contextlib.suppress(Exception):
            return SyncJob.model_validate_json(v)
        return None

    async def log_activity(self, tenant: str, entry: ActivityEntry) -> None:
        key = _activity_key(tenant)
        try:
            async with self._r.pipeline(transaction=False) as pipe:
                pipe.lpush(key, entry.model_dump_json())
                pipe.ltrim(key, 0, _ACTIVITY_MAX - 1)
                await pipe.execute()
        except _ERRORS as e:
            logger.warning("log_activity failed: %s", e)

    async def recent_activity(self, tenant: str, limit: int = _ACTIVITY_MAX) -> list[ActivityEntry]:
        try:
            raw = await self._r.lrange(_activity_key(tenant), 0, max(0, limit - 1))
        except _ERRORS as e:
            logger.warning("recent_activity failed: %s", e)
            return []
        out: list[ActivityEntry] = []
        for item in raw:
            with contextlib.suppress(Exception):
                out.append(ActivityEntry.model_validate_json(item))
        return out
