"""Purge all workflow run records (`run:*`) from Redis.

Destructive: deletes every refund/workflow run + its events from the RunStore.
Uses the same Redis connection as the app (from .env / Key Vault). Intended to
clear seeded demo runs. Run with: uv run python scripts/purge_runs.py
"""

from __future__ import annotations

import asyncio

import redis.asyncio as redis

from app.config import get_settings, load_secrets_from_keyvault


async def main() -> int:
    s = get_settings()
    try:
        load_secrets_from_keyvault(s)
    except Exception as e:  # noqa: BLE001 - keep going if KV not reachable; .env may have it
        print(f"(key vault load skipped: {e})")
    if not s.azure_redis_host:
        print("No Redis host configured — nothing to purge.")
        return 0
    r = redis.Redis(
        host=s.azure_redis_host, port=s.azure_redis_port,
        ssl=s.azure_redis_ssl, password=s.redis_key, decode_responses=True,
    )
    # Enumerate run ids from the index list (a single key — reliable on the
    # clustered/proxied endpoint, unlike SCAN which only sees one shard). Then
    # delete each run + its events one key at a time (single-key ops route fine;
    # multi-key DEL is rejected cross-slot).
    ids = await r.lrange("runs:all", 0, -1)
    print(f"runs:all index has {len(ids)} run id(s) on {s.azure_redis_host}:{s.azure_redis_port}")
    deleted = 0
    for run_id in ids:
        deleted += await r.delete(f"run:{run_id}")
        deleted += await r.delete(f"run:{run_id}:events")
    deleted += await r.delete("runs:all")
    print(f"Deleted {deleted} keys (runs + events + index).")
    await r.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
