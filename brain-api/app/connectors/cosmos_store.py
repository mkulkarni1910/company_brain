"""Durable connector state on Cosmos DB (Gremlin), for deployments without Redis.

Mirrors ConnectionStore's interface (duck-typed) so SyncRunner / the admin routes
work unchanged. Records are stored as label-separated vertices in the existing
people graph (no new container/secret), each carrying the whole model as a JSON
`data` property plus `tenant_id` (the Cosmos partition key). All reads degrade to
empty/None and writes swallow errors, so a Cosmos hiccup never breaks /admin.

The Gremlin client is shared (injected) — typically app.state.people_graph — so
this store opens no new connection and must NOT close it.
"""
from __future__ import annotations

import contextlib
import json
import logging
import time

from app.config import get_settings
from app.connectors.models import ActivityEntry, Connection, SyncJob

logger = logging.getLogger(__name__)

_CONN = "cbrain_connection"
_JOB = "cbrain_syncjob"
_ACT = "cbrain_connactivity"
_OAUTH = "cbrain_oauthstate"
_ACTIVITY_MAX = 50


class CosmosConnectionStore:
    def __init__(self, graph) -> None:
        # `graph` exposes async submit(query, bindings) -> list (see PeopleGraphClient).
        self._g = graph

    async def aclose(self) -> None:
        # The Gremlin client is shared/owned elsewhere — nothing to close here.
        return

    async def _upsert(self, label: str, keyprop: str, keyval: str, tenant: str, data: str) -> None:
        try:
            await self._g.submit(
                f"g.V().has('{label}','{keyprop}', k).has('tenant_id', tid).fold()"
                f".coalesce(unfold(),"
                f" addV('{label}').property('{keyprop}', k).property('tenant_id', tid))"
                f".property('data', d)",
                {"k": keyval, "tid": tenant, "d": data},
            )
        except Exception as e:  # noqa: BLE001 — connector state is best-effort
            logger.warning("cosmos upsert %s failed: %s", label, e)

    async def _values(self, label: str, tenant: str, keyprop: str | None = None,
                      keyval: str | None = None) -> list[str]:
        try:
            if keyprop:
                rows = await self._g.submit(
                    f"g.V().has('{label}','{keyprop}', k).has('tenant_id', tid).values('data')",
                    {"k": keyval, "tid": tenant},
                )
            else:
                rows = await self._g.submit(
                    f"g.V().has('{label}','tenant_id', tid).values('data')", {"tid": tenant}
                )
            return [r for r in rows if isinstance(r, str)]
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos read %s failed: %s", label, e)
            return []

    # ---- connections ----
    async def put_connection(self, c: Connection) -> None:
        await self._upsert(_CONN, "cid", c.connection_id, c.tenant_id, c.model_dump_json())

    async def list_connections(self, tenant: str) -> list[Connection]:
        out: list[Connection] = []
        for data in await self._values(_CONN, tenant):
            try:
                out.append(Connection.model_validate_json(data))
            except Exception:  # noqa: BLE001 — skip corrupt rows
                continue
        return out

    async def get_connection(self, tenant: str, connection_id: str) -> Connection | None:
        for data in await self._values(_CONN, tenant, "cid", connection_id):
            try:
                return Connection.model_validate_json(data)
            except Exception:  # noqa: BLE001
                return None
        return None

    async def delete_connection(self, tenant: str, connection_id: str) -> None:
        try:
            await self._g.submit(
                f"g.V().has('{_CONN}','cid', k).has('tenant_id', tid).drop()",
                {"k": connection_id, "tid": tenant},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos delete connection failed: %s", e)

    # ---- sync jobs ----
    async def put_job(self, j: SyncJob) -> None:
        await self._upsert(_JOB, "jid", j.job_id, j.tenant_id, j.model_dump_json())

    async def get_job(self, tenant: str, job_id: str) -> SyncJob | None:
        for data in await self._values(_JOB, tenant, "jid", job_id):
            try:
                return SyncJob.model_validate_json(data)
            except Exception:  # noqa: BLE001
                return None
        return None

    # ---- admin activity (rolling list in one vertex per tenant) ----
    async def log_activity(self, tenant: str, entry: ActivityEntry) -> None:
        items = [entry, *await self.recent_activity(tenant)][:_ACTIVITY_MAX]
        data = json.dumps([e.model_dump(mode="json") for e in items])
        await self._upsert(_ACT, "akey", f"act:{tenant}", tenant, data)

    async def recent_activity(self, tenant: str, limit: int = _ACTIVITY_MAX) -> list[ActivityEntry]:
        rows = await self._values(_ACT, tenant, "akey", f"act:{tenant}")
        if not rows:
            return []
        out: list[ActivityEntry] = []
        try:
            for d in json.loads(rows[0]):
                out.append(ActivityEntry.model_validate(d))
        except Exception:  # noqa: BLE001
            return []
        return out[:limit]

    # ---- OAuth CSRF state (one-shot, TTL'd; state is globally unique) ----
    async def put_oauth_state(self, state: str, tenant: str) -> None:
        await self._upsert(_OAUTH, "st", state, tenant,
                           json.dumps({"tenant": tenant, "ts": int(time.time())}))

    async def consume_oauth_state(self, state: str) -> str | None:
        try:
            rows = await self._g.submit(
                f"g.V().has('{_OAUTH}','st', k).values('data')", {"k": state})
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos oauth-state read failed: %s", e)
            return None
        if not rows:
            return None
        try:
            d = json.loads(rows[0])
        except Exception:  # noqa: BLE001
            return None
        with contextlib.suppress(Exception):  # one-shot: drop regardless of validity
            await self._g.submit(f"g.V().has('{_OAUTH}','st', k).drop()", {"k": state})
        if int(time.time()) - int(d.get("ts", 0)) > get_settings().oauth_state_ttl_seconds:
            return None
        return d.get("tenant")
