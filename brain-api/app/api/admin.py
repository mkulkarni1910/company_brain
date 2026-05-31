import os
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.activity.store import ActivityStore
from app.config import get_settings
from app.deps import get_ingest_pipeline
from app.domain.activity import ActivityEvent
from app.domain.chunk import SourceDoc
from app.ingest.pipeline import IngestPipeline, IngestResult
from app.people.graph_client import PeopleGraphClient
from app.people.seeder import PeopleSeeder


def require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    """Phase 1 admin auth: shared API key via x-admin-key header.

    Rejects with 403 when ADMIN_API_KEY is unset (closed by default) or when
    the supplied header does not match.
    """
    expected = get_settings().admin_api_key
    if not expected or x_admin_key != expected:
        raise HTTPException(status_code=403, detail="admin key required")


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


@router.post("/ingest", response_model=None)
async def ingest(
    doc: SourceDoc,
    pipeline: IngestPipeline = Depends(get_ingest_pipeline),
) -> dict[str, int | str]:
    result: IngestResult = await pipeline.process(doc)
    return {"doc_id": result.doc_id, "chunks_indexed": result.chunks_indexed}


@router.post("/seed-people")
async def seed_people(users_limit: int = 50, groups_limit: int = 50) -> dict:
    tenant = get_settings().brain_tenant_id
    gc = PeopleGraphClient()
    try:
        seeder = PeopleSeeder(graph=gc, tenant_id=tenant)
        u = await seeder.seed_users(limit=users_limit)
        g = await seeder.seed_groups(limit=groups_limit)
        return {"tenant_id": tenant, **u, **g}
    finally:
        await gc.aclose()


class SeedActivityRequest(BaseModel):
    doc_ids: list[str] = []
    events_per_doc: int = 6


@router.post("/seed-activity")
async def seed_activity(body: SeedActivityRequest) -> dict:
    """Seed synthetic engagement across real corpus docs so the Discover surface and
    the ranker's activity signal are demonstrable. Pass real doc_ids; falls back to a
    known set. Mixes event types + sources so trending and by-source both render."""
    tenant = os.environ.get("EVAL_TENANT", "t-eval")
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        ids = body.doc_ids or [
            "up:planning-q3-sales-plan",
            "up:engineering-oncall-runbook",
            "up:policy-pto",
        ]
        types = ["view", "view", "click", "dwell", "thumbs_up"]
        sources = ["sharepoint", "teams", "uploaded"]
        written = 0
        for j, doc_id in enumerate(ids):
            for i in range(body.events_per_doc):
                await store.ingest_event(ActivityEvent(
                    timestamp=now - timedelta(hours=i + j),
                    tenant_id=tenant, user_id=f"u-{i % 3}", doc_id=doc_id,
                    event_type=types[(i + j) % len(types)],
                    source=sources[(i + j) % len(sources)]))
                written += 1
        return {"tenant_id": tenant, "events_written": written, "docs": len(ids)}
    finally:
        await store.aclose()
