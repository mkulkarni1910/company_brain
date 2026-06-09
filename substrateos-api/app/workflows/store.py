from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.workflow import RefundRun, RunEvent

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)

_SEQ_KEY = "runs:seq"       # INCR counter; first id is RB-4471
_INDEX_KEY = "runs:all"     # LPUSH run ids, newest first
_SEQ_START = 4470


def _run_key(run_id: str) -> str:
    return f"run:{run_id}"


def _events_key(run_id: str) -> str:
    return f"run:{run_id}:events"


class RunStore:
    """Redis-backed store for workflow runs + audit events.

    Mirrors writes to an in-process dict so the flow keeps working within a
    single process when Redis is unavailable (same degradation philosophy as
    SkillStore, but runs are flow-critical so a memory fallback is kept).
    Reads prefer Redis and fall back to the process-local mirror, so a partially failed Redis write can shadow mirror-only data — acceptable for this demo-grade audit trail.
    """

    def __init__(self, client: redis.Redis | None = None, *, force_memory: bool = False) -> None:
        self._mem_runs: dict[str, str] = {}
        self._mem_events: dict[str, list[str]] = {}
        self._mem_index: list[str] = []
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

    async def _next_id(self) -> str:
        if self._r is not None:
            try:
                await self._r.set(_SEQ_KEY, _SEQ_START, nx=True)
                n = await self._r.incr(_SEQ_KEY)
                return f"RB-{n}"
            except _ERRORS as e:
                logger.warning("RunStore._next_id redis failed: %s", e)
        self._mem_seq += 1
        return f"RB-{self._mem_seq}"

    async def create(
        self, *, requester_name: str, requester_slack_id: str | None,
        channel: str | None, thread_ts: str | None,
        kind: str = "refund", request_text: str | None = None,
    ) -> RefundRun:
        now = datetime.now(UTC)
        run = RefundRun(
            id=await self._next_id(), kind=kind, request_text=request_text,
            requester_name=requester_name,
            requester_slack_id=requester_slack_id, channel=channel, thread_ts=thread_ts,
            created_at=now, updated_at=now,
        )
        await self._write(run, new=True)
        return run

    async def save(self, run: RefundRun) -> None:
        run.updated_at = datetime.now(UTC)
        await self._write(run, new=False)

    async def _write(self, run: RefundRun, *, new: bool) -> None:
        blob = run.model_dump_json()
        self._mem_runs[run.id] = blob
        if new:
            self._mem_index.insert(0, run.id)
        if self._r is None:
            return
        try:
            await self._r.set(_run_key(run.id), blob)
            if new:
                await self._r.lpush(_INDEX_KEY, run.id)
        except _ERRORS as e:
            logger.warning("RunStore._write redis failed: %s", e)

    async def get(self, run_id: str) -> RefundRun | None:
        raw: str | None = None
        if self._r is not None:
            try:
                raw = await self._r.get(_run_key(run_id))
            except _ERRORS as e:
                logger.warning("RunStore.get redis failed: %s", e)
        raw = raw or self._mem_runs.get(run_id)
        if not raw:
            return None
        with contextlib.suppress(Exception):
            return RefundRun.model_validate_json(raw)
        return None

    async def list_runs(self, limit: int = 50) -> list[RefundRun]:
        ids: list[str] = []
        if self._r is not None:
            try:
                ids = await self._r.lrange(_INDEX_KEY, 0, limit - 1)
            except _ERRORS as e:
                logger.warning("RunStore.list_runs redis failed: %s", e)
        if not ids:
            ids = self._mem_index[:limit]
        runs = [r for rid in ids if (r := await self.get(rid)) is not None]
        return runs

    async def add_event(self, run_id: str, *, step: str, detail: str, actor: str) -> None:
        event = RunEvent(ts=datetime.now(UTC), step=step, detail=detail, actor=actor)
        blob = event.model_dump_json()
        self._mem_events.setdefault(run_id, []).append(blob)
        if self._r is None:
            return
        try:
            await self._r.rpush(_events_key(run_id), blob)
        except _ERRORS as e:
            logger.warning("RunStore.add_event redis failed: %s", e)

    async def list_events(self, run_id: str) -> list[RunEvent]:
        raws: list[str] = []
        if self._r is not None:
            try:
                raws = await self._r.lrange(_events_key(run_id), 0, -1)
            except _ERRORS as e:
                logger.warning("RunStore.list_events redis failed: %s", e)
        if not raws:
            raws = self._mem_events.get(run_id, [])
        events = []
        for raw in raws:
            with contextlib.suppress(Exception):
                events.append(RunEvent.model_validate_json(raw))
        return events

    async def find_routed_run(self, order_id: str | None) -> RefundRun | None:
        """Most recent customer hand-off run for this order still awaiting an
        outcome. list_runs is newest-first, so the first match wins. Notification
        flips the run's status, so a second lookup finds nothing — natural
        double-notify protection."""
        if not order_id:
            return None
        for run in await self.list_runs(limit=100):
            if (run.kind == "refund" and run.status == "routed_to_support"
                    and run.decision is not None
                    and run.decision.order_id == order_id):
                return run
        return None

    async def find_handoff_run(self, channel: str | None,
                               thread_ts: str | None) -> RefundRun | None:
        """Customer hand-off run whose support-channel card anchors this thread.
        An agent replying under the card means *this* request — the flow reuses
        the run's already-fetched order facts instead of re-extracting an order
        from the reply text. Most recent match wins (list_runs is newest-first)."""
        if not channel or not thread_ts:
            return None
        for run in await self.list_runs(limit=100):
            if (run.kind == "refund" and run.status == "routed_to_support"
                    and run.handoff_channel == channel
                    and run.handoff_ts == thread_ts):
                return run
        return None
