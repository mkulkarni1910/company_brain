"""start_periodic: ticks repeat, exceptions don't kill the loop, cancel stops it."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.scheduler import start_periodic

# 1h == 3600s; use a tiny interval so two ticks land within the test.
_FAST = 0.02 / 3600  # 20ms


@pytest.mark.asyncio
async def test_ticks_repeat_and_cancel_stops():
    ticks: list[int] = []

    async def tick():
        ticks.append(1)
        return {"ok": len(ticks)}

    task = start_periodic("t", tick, interval_hours=_FAST, initial_delay_s=0)
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(ticks) >= 2
    n = len(ticks)
    await asyncio.sleep(0.05)
    assert len(ticks) == n  # genuinely stopped


@pytest.mark.asyncio
async def test_exception_does_not_kill_loop():
    ticks: list[int] = []

    async def tick():
        ticks.append(1)
        raise RuntimeError("boom")

    task = start_periodic("t", tick, interval_hours=_FAST, initial_delay_s=0)
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(ticks) >= 2  # survived the first failure
