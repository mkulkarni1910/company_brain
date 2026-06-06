"""AuditLog — append-only provenance store, queryable by run_id.

Same Redis + in-process degradation philosophy as RunStore. Events are immutable
(``AuditEvent.model_config frozen``); the log only ever appends. ``record()`` is a
convenience that stamps ``ts`` and appends in one call.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.audit import Actor, AuditEvent

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)


def _audit_key(run_id: str) -> str:
    return f"audit:{run_id}"


class AuditLog:
    def __init__(self, client: redis.Redis | None = None, *, force_memory: bool = False) -> None:
        self._mem: dict[str, list[str]] = {}
        if force_memory:
            self._r = None
            return
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

    async def append(self, event: AuditEvent) -> None:
        blob = event.model_dump_json()
        self._mem.setdefault(event.run_id, []).append(blob)
        if self._r is None:
            return
        try:
            await self._r.rpush(_audit_key(event.run_id), blob)
        except _ERRORS as e:
            logger.warning("AuditLog.append redis failed: %s", e)

    async def record(self, *, run_id: str, step: str, actor: Actor, **fields) -> AuditEvent:
        event = AuditEvent(ts=datetime.now(UTC), run_id=run_id, step=step, actor=actor, **fields)
        await self.append(event)
        return event

    async def query(self, run_id: str) -> list[AuditEvent]:
        raws: list[str] = []
        if self._r is not None:
            try:
                raws = await self._r.lrange(_audit_key(run_id), 0, -1)
            except _ERRORS as e:
                logger.warning("AuditLog.query redis failed: %s", e)
        if not raws:
            raws = self._mem.get(run_id, [])
        events: list[AuditEvent] = []
        for raw in raws:
            with contextlib.suppress(Exception):
                events.append(AuditEvent.model_validate_json(raw))
        return events
