"""Print the most recent run's id/kind/status/requester_slack_id from Redis.
One-off diagnostic. Run: PYTHONPATH=. uv run python scripts/show_last_run.py
"""

from __future__ import annotations

import asyncio
import json

import redis.asyncio as redis

from app.config import get_settings, load_secrets_from_keyvault


async def main() -> int:
    s = get_settings()
    try:
        load_secrets_from_keyvault(s)
    except Exception:  # noqa: BLE001
        pass
    r = redis.Redis(host=s.azure_redis_host, port=s.azure_redis_port,
                    ssl=s.azure_redis_ssl, password=s.redis_key, decode_responses=True)
    ids = await r.lrange("runs:all", 0, 5)
    print(f"latest {len(ids)} run id(s): {ids}")
    for rid in ids[:3]:
        blob = await r.get(f"run:{rid}")
        if not blob:
            continue
        run = json.loads(blob)
        print(f"  {rid}: kind={run.get('kind')} status={run.get('status')} "
              f"requester={run.get('requester_name')!r} requester_slack_id={run.get('requester_slack_id')!r}")
    await r.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
