"""ApprovalStore — persists PendingApproval so a paused run survives restarts.

Same Redis + in-process degradation as RunStore.
"""

from __future__ import annotations

import contextlib
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.approval import PendingApproval

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)

_SEQ_KEY = "approvals:seq"
_SEQ_START = 8200


def _key(approval_id: str) -> str:
    return f"approval:{approval_id}"


class ApprovalStore:
    def __init__(self, client: redis.Redis | None = None, *, force_memory: bool = False) -> None:
        self._mem: dict[str, str] = {}
        self._mem_seq = _SEQ_START
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

    async def next_id(self) -> str:
        if self._r is not None:
            try:
                await self._r.set(_SEQ_KEY, _SEQ_START, nx=True)
                n = await self._r.incr(_SEQ_KEY)
                return f"AP-{n}"
            except _ERRORS as e:
                logger.warning("ApprovalStore.next_id redis failed: %s", e)
        self._mem_seq += 1
        return f"AP-{self._mem_seq}"

    async def create(self, pending: PendingApproval) -> None:
        await self._write(pending)

    async def save(self, pending: PendingApproval) -> None:
        await self._write(pending)

    async def _write(self, pending: PendingApproval) -> None:
        blob = pending.model_dump_json()
        self._mem[pending.id] = blob
        if self._r is None:
            return
        try:
            await self._r.set(_key(pending.id), blob)
        except _ERRORS as e:
            logger.warning("ApprovalStore._write redis failed: %s", e)

    async def get(self, approval_id: str) -> PendingApproval | None:
        raw: str | None = None
        if self._r is not None:
            try:
                raw = await self._r.get(_key(approval_id))
            except _ERRORS as e:
                logger.warning("ApprovalStore.get redis failed: %s", e)
        raw = raw or self._mem.get(approval_id)
        if not raw:
            return None
        with contextlib.suppress(Exception):
            return PendingApproval.model_validate_json(raw)
        return None
