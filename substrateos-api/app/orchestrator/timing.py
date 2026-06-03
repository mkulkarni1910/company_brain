"""Lightweight per-stage timing for the query pipeline.

Collects wall-clock duration of each orchestrator stage so latency can be
localized — e.g. a slow Gemini generation vs. a hanging Cosmos/ADX call. Each
stage is logged at INFO and, for `include_debug` requests, the full breakdown is
surfaced in `Answer.debug["timings_ms"]`.

The context manager records duration even when the wrapped block raises, so a
stage that times out or degrades (proximity/activity catch broadly) still shows
how long it blocked before failing — which is exactly what we need to see.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger("app.timing")


class StageTimer:
    def __init__(self, *, query_id: str | None = None) -> None:
        self._query_id = query_id
        # Accumulated per stage: a stage that runs more than once (retry/fallback)
        # sums, so the number reflects total time spent in that stage.
        self.timings_ms: dict[str, float] = {}

    @asynccontextmanager
    async def stage(self, name: str) -> AsyncIterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = round((time.perf_counter() - t0) * 1000, 1)
            self.timings_ms[name] = round(self.timings_ms.get(name, 0.0) + dt, 1)
            logger.info(
                "timing stage=%s ms=%.1f query_id=%s", name, dt, self._query_id or "-"
            )

    def summary(self) -> str:
        parts = " ".join(f"{k}={v}ms" for k, v in self.timings_ms.items())
        return f"query_id={self._query_id or '-'} {parts}".rstrip()


@asynccontextmanager
async def maybe_stage(timer: StageTimer | None, name: str) -> AsyncIterator[None]:
    """Time `name` on `timer`, or no-op when no timer was threaded through.

    Lets shared code (e.g. the retriever, used by query/search/ingest) opt into
    timing only when a caller passes a timer, without branching at every call.
    """
    if timer is None:
        yield
    else:
        async with timer.stage(name):
            yield
