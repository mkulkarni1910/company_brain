from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.connectors.extract import extract_text
from app.connectors.models import ActivityEntry, Connection, SyncJob
from app.connectors.store import ConnectionStore
from app.domain.chunk import SourceDoc

logger = logging.getLogger(__name__)


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
            files = await self._connector.list_files(connection.site_id, max_items=cap)
            job.total = len(files)
            job.truncated = len(files) >= cap
            await self._store.put_job(job)

            for f in files:
                data = await self._connector.fetch_content(f.drive_id, f.item_id)
                text = extract_text(data, f.mime, f.name) if data is not None else None
                if not text:
                    job.skipped += 1
                else:
                    now = datetime.now(UTC)
                    doc = SourceDoc(
                        doc_id=f"sp:{connection.site_id}:{f.item_id}",
                        tenant_id=tenant, source="sharepoint", source_url=f.web_url,
                        title=f.name, body=text, author_id=f.author_id,
                        acl_principals=[f"{tenant}:everyone"],
                        created_at=f.created_at or now, modified_at=f.modified_at or now,
                        mime=f.mime or "text/plain")
                    try:
                        await self._pipeline.process(doc)
                        job.processed += 1
                    except Exception as e:  # noqa: BLE001 — skip the file, keep syncing
                        logger.warning("ingest failed for %s: %s", f.name, e)
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
