from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config import get_settings
from app.connectors.models import ActivityEntry, Connection, SyncJob
from app.connectors.store import ConnectionStore

logger = logging.getLogger(__name__)


@dataclass
class CollectResult:
    docs: list = field(default_factory=list)   # list[SourceDoc]
    skipped: int = 0
    truncated: bool = False


class SyncRunner:
    def __init__(self, *, connector, pipeline, store: ConnectionStore) -> None:
        self._connector = connector
        self._pipeline = pipeline
        self._store = store

    async def run(self, *, connection: Connection, actor: str = "admin") -> SyncJob:
        tenant = connection.tenant_id
        job = SyncJob(job_id=uuid.uuid4().hex, tenant_id=tenant,
                      connection_id=connection.connection_id, status="running",
                      started_at=datetime.now(UTC))
        connection.status = "syncing"
        connection.last_job_id = job.job_id
        await self._store.put_connection(connection)
        await self._store.put_job(job)

        cap = get_settings().connector_max_items
        try:
            result: CollectResult = await self._connector.collect_documents(cap)
            job.total = len(result.docs) + result.skipped
            job.skipped = result.skipped
            job.truncated = result.truncated
            await self._store.put_job(job)

            for doc in result.docs:
                try:
                    await self._pipeline.process(doc)
                    job.processed += 1
                except Exception as e:  # noqa: BLE001 — skip the doc, keep syncing
                    logger.warning("ingest failed for %s: %s", doc.doc_id, e)
                    job.errors += 1
                await self._store.put_job(job)

            job.status = "succeeded"
            connection.status = "live"
            connection.item_count = job.processed
        except Exception as e:  # noqa: BLE001
            logger.warning("sync run failed: %s", e)
            job.status = "failed"
            job.message = str(e)
            connection.status = "error"
            connection.error = str(e)

        job.finished_at = datetime.now(UTC)
        connection.last_sync = job.finished_at
        await self._store.put_job(job)
        await self._store.put_connection(connection)
        await self._store.log_activity(tenant, ActivityEntry(
            ts=job.finished_at, actor=actor, kind="sync",
            text=f"Synced {connection.name}: {job.processed} indexed, {job.skipped} skipped"
            + (f", {job.errors} errors" if job.errors else "")
            + (" (truncated)" if job.truncated else "")))
        return job
