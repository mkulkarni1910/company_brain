"""Azure Data Explorer (Kusto) store for the Activity pillar.

Free-cluster compatible: ingests via INLINE ingestion control commands through
the query client (no separate ingest endpoint, which free clusters lack).
Engagement scoring is a parameterized recency-weighted KQL aggregate.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from azure.identity import DefaultAzureCredential
from azure.kusto.data import (
    ClientRequestProperties,
    KustoClient,
    KustoConnectionStringBuilder,
)

from app.config import get_settings

if TYPE_CHECKING:
    from app.domain.activity import ActivityEvent

logger = logging.getLogger(__name__)

_TABLE = "ActivityEvents"

_CREATE = (
    f".create-merge table {_TABLE} "
    "(Timestamp:datetime, TenantId:string, UserId:string, QueryId:string, "
    "DocId:string, ChunkId:string, EventType:string, Source:string, DurationMs:int)"
)

# Recency-weighted, per-event-type engagement over a 30-day window. Positive
# signals add; thumbs_down subtracts; query is neutral. Self-engagement weighted
# 2x. Parameterized (string + todynamic) to stay injection-safe on the free
# cluster (see note above).
#
# NOTE (ADX SDK/cluster adaptation): `dids` is declared as a `string` (a JSON
# array) and parsed inside KQL with `todynamic()`, rather than a native
# `dynamic` query parameter. This cluster's gateway returns an internal service
# error when a `dynamic` query parameter is supplied; the string+todynamic form
# is reliable and stays fully parameterized (injection-safe).
_SCORE_QUERY = (
    "declare query_parameters(tid:string, uid:string, dids:string);\n"
    f"{_TABLE}\n"
    "| where TenantId == tid and DocId in (todynamic(dids)) and Timestamp > ago(30d)\n"
    "| extend recency = exp(-1.0 * datetime_diff('day', now(), Timestamp) / 14.0)\n"
    "| extend self_weight = iif(UserId == uid, 2.0, 1.0)\n"
    "| extend type_weight = case("
    "EventType == 'thumbs_up', 2.0, "
    "EventType == 'thumbs_down', -2.0, "
    "EventType == 'dwell', 1.5, "
    "EventType == 'view', 1.0, "
    "EventType == 'click', 1.0, "
    "0.0)\n"
    "| summarize score = sum(recency * self_weight * type_weight) by DocId"
)


def _kcsb() -> KustoConnectionStringBuilder:
    s = get_settings()
    if not s.adx_cluster_uri:
        raise RuntimeError("ADX_CLUSTER_URI is not configured")
    return KustoConnectionStringBuilder.with_azure_token_credential(
        s.adx_cluster_uri, DefaultAzureCredential()
    )


def _escape(v: str) -> str:
    # Inline-ingest CSV: our values contain no commas/quotes/newlines, but guard anyway.
    return v.replace('"', '""')


class ActivityStore:
    def __init__(self) -> None:
        self._db = get_settings().adx_database
        self._client = KustoClient(_kcsb())

    async def aclose(self) -> None:
        def _close() -> None:
            self._client.close()

        await asyncio.to_thread(_close)

    async def ensure_table(self) -> None:
        await asyncio.to_thread(self._client.execute_mgmt, self._db, _CREATE)

    async def ingest_event(self, e: ActivityEvent) -> None:
        # One CSV row matching the table column order.
        row = ",".join(
            _escape(str(x)) for x in [
                e.timestamp.isoformat(),
                e.tenant_id,
                e.user_id,
                e.query_id or "",
                e.doc_id,
                e.chunk_id or "",
                e.event_type,
                e.source,
                e.duration_ms if e.duration_ms is not None else "",
            ]
        )
        cmd = f".ingest inline into table {_TABLE} <|\n{row}"
        await asyncio.to_thread(self._client.execute_mgmt, self._db, cmd)

    async def engagement_scores(
        self, *, tenant_id: str, user_id: str, doc_ids: list[str]
    ) -> dict[str, float]:
        if not doc_ids:
            return {}
        crp = ClientRequestProperties()
        crp.set_parameter("tid", tenant_id)
        crp.set_parameter("uid", user_id)
        # `dids` is a JSON-array string parsed by todynamic() in the query.
        crp.set_parameter("dids", json.dumps(doc_ids))

        def _run():
            return self._client.execute_query(self._db, _SCORE_QUERY, crp)

        resp = await asyncio.to_thread(_run)
        out: dict[str, float] = {}
        for row in resp.primary_results[0]:
            out[row["DocId"]] = float(row["score"])
        return out
