from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException

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


@router.post("/seed-activity")
async def seed_activity(events_per_doc: int = 5) -> dict:
    """Generate synthetic engagement: u-sales engages the sales plan, u-eng the eng plan."""
    tenant = get_settings().brain_tenant_id
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        plan = [
            ("p-sales", "up:persona-sales-plan"),
            ("p-eng", "up:persona-eng-plan"),
        ]
        written = 0
        for user_id, doc_id in plan:
            for i in range(events_per_doc):
                await store.ingest_event(ActivityEvent(
                    timestamp=now - timedelta(hours=i),
                    tenant_id=tenant, user_id=user_id, doc_id=doc_id,
                    event_type="view", source="uploaded",
                ))
                written += 1
        return {"tenant_id": tenant, "events_written": written}
    finally:
        await store.aclose()
