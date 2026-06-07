"""In-process periodic task runner, owned by the FastAPI lifespan.

The app's first scheduler: today it drives the daily directory sync; the
Outlook subscription-renewal maintenance (currently a manual /admin POST) is
the next intended consumer. Runs per replica — ticks must be idempotent
(directory upserts are), so extra replicas only do redundant work.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def start_periodic(
    name: str,
    tick: Callable[[], Awaitable[object]],
    *,
    interval_hours: float,
    initial_delay_s: float = 10.0,
) -> asyncio.Task:
    """Run `tick` after `initial_delay_s`, then every `interval_hours`, forever.
    Exceptions are logged and never kill the loop; cancel the task on shutdown."""

    async def _loop() -> None:
        await asyncio.sleep(initial_delay_s)
        while True:
            try:
                result = await tick()
                logger.info("periodic[%s] tick ok: %s", name, result)
            except Exception:  # noqa: BLE001 — the loop must outlive any one tick
                logger.exception("periodic[%s] tick failed; retrying next interval", name)
            await asyncio.sleep(interval_hours * 3600)

    return asyncio.create_task(_loop(), name=f"periodic:{name}")
