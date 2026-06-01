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


# --- Discover surface queries (window/limit are server ints, inlined safely) ---
_TYPE_WEIGHT = (
    "| extend type_weight = case("
    "EventType == 'thumbs_up', 2.0, EventType == 'thumbs_down', -2.0, "
    "EventType == 'dwell', 1.5, EventType == 'view', 1.0, EventType == 'click', 1.0, 0.0)\n"
)


def _trending_query(window_days: int, limit: int) -> str:
    w, lim = int(window_days), int(limit)
    return (
        "declare query_parameters(tid:string);\n"
        f"{_TABLE}\n"
        f"| where TenantId == tid and Timestamp > ago({w}d)\n"
        "| extend recency = exp(-1.0 * datetime_diff('day', now(), Timestamp) / 14.0)\n"
        f"{_TYPE_WEIGHT}"
        "| summarize score = sum(recency * type_weight), events = count() by DocId\n"
        "| where score > 0\n"
        f"| top {lim} by score desc"
    )


def _source_query(window_days: int) -> str:
    w = int(window_days)
    return (
        "declare query_parameters(tid:string, dids:string);\n"
        f"{_TABLE}\n"
        f"| where TenantId == tid and Timestamp > ago({w}d) and DocId in (todynamic(dids))\n"
        f"{_TYPE_WEIGHT}"
        "| summarize score = sum(type_weight), events = count() by Source\n"
        "| top 6 by score desc"
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
        s = get_settings()
        self._db = s.adx_database
        # Activity (ADX) is optional: with no cluster configured the store no-ops,
        # so the activity ranking signal simply contributes 0 (already handled).
        self._client = KustoClient(_kcsb()) if s.adx_cluster_uri else None

    async def aclose(self) -> None:
        if self._client is None:
            return

        def _close() -> None:
            self._client.close()

        await asyncio.to_thread(_close)

    async def ensure_table(self) -> None:
        if self._client is None:
            return
        await asyncio.to_thread(self._client.execute_mgmt, self._db, _CREATE)

    async def ingest_event(self, e: ActivityEvent) -> None:
        if self._client is None:
            return
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
        if self._client is None or not doc_ids:
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

    async def trending(
        self, *, tenant_id: str, window_days: int = 14, limit: int = 8
    ) -> list[tuple[str, float]]:
        if self._client is None:
            return []
        crp = ClientRequestProperties()
        crp.set_parameter("tid", tenant_id)
        query = _trending_query(window_days, limit)

        def _run():
            return self._client.execute_query(self._db, query, crp)

        try:
            resp = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001 - Discover degrades to empty
            logger.warning("ADX trending failed: %s", e)
            return []
        return [(row["DocId"], float(row["score"])) for row in resp.primary_results[0]]

    async def source_breakdown(
        self, *, tenant_id: str, doc_ids: list[str], window_days: int = 14
    ) -> list[tuple[str, int, float]]:
        if self._client is None or not doc_ids:
            return []
        crp = ClientRequestProperties()
        crp.set_parameter("tid", tenant_id)
        crp.set_parameter("dids", json.dumps(doc_ids))
        query = _source_query(window_days)

        def _run():
            return self._client.execute_query(self._db, query, crp)

        try:
            resp = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001 - Discover degrades to empty
            logger.warning("ADX source_breakdown failed: %s", e)
            return []
        return [
            (row["Source"], int(row["events"]), float(row["score"]))
            for row in resp.primary_results[0]
        ]
