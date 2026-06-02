import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.activity.store import ActivityStore
from app.config import get_settings
from app.connectors.factory import connector_for
from app.connectors.models import Connection
from app.connectors.oauth import admin_consent_url
from app.connectors.sharepoint import SharePointConnector
from app.connectors.store import ConnectionStore
from app.connectors.sync import SyncRunner
from app.deps import (
    get_ai_search,
    get_connection_store,
    get_ingest_pipeline,
    get_metrics_store,
    get_sharepoint,
)
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
# Callback is a browser redirect from Microsoft (can't carry x-admin-key) — it's
# authorized by the one-shot CSRF `state` instead, so it lives on a key-free router.
callback_router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/connections/oauth/connect")
async def oauth_connect(
    provider: str = "sharepoint",
    store: ConnectionStore = Depends(get_connection_store),
) -> dict:
    """Generic admin-consent start: supports provider ∈ {sharepoint, teams}."""
    if provider not in {"sharepoint", "teams"}:
        raise HTTPException(status_code=400, detail=f"unknown provider: {provider!r}")
    s = get_settings()
    state = secrets.token_urlsafe(24)
    await store.put_oauth_state(state, s.brain_tenant_id, provider)
    redirect_uri = f"{s.brain_api_base_url.rstrip('/')}/admin/connections/oauth/callback"
    return {"auth_url": admin_consent_url(
        client_id=s.azure_client_id or "", redirect_uri=redirect_uri, state=state)}


@callback_router.get("/connections/oauth/callback")
async def oauth_callback(
    state: str = "",
    tenant: str = "",
    admin_consent: str = "",
    error: str = "",
    store: ConnectionStore = Depends(get_connection_store),
    pipeline: IngestPipeline = Depends(get_ingest_pipeline),
) -> RedirectResponse:
    s = get_settings()
    web = s.web_base_url.rstrip("/")
    state_result = await store.consume_oauth_state(state) if state else None
    if state_result is None:
        brain_tenant, provider = None, None
    else:
        brain_tenant, provider = state_result
    ok = admin_consent.lower() == "true" and bool(tenant) and brain_tenant is not None and not error
    if not ok:
        return RedirectResponse(url=f"{web}/admin/sources?error=oauth", status_code=302)
    display = "SharePoint" if provider == "sharepoint" else "Teams"
    conn = Connection(
        connection_id=uuid.uuid4().hex, tenant_id=brain_tenant, type=provider,
        site_id=tenant, name=f"{display} · {tenant[:8]}", web_url="",
        connected_tenant_id=tenant, status="syncing",
    )
    await store.put_connection(conn)
    runner = SyncRunner(connector=connector_for(conn), pipeline=pipeline, store=store)
    asyncio.create_task(runner.run(connection=conn, actor="admin"))  # detached; we 302 now
    return RedirectResponse(url=f"{web}/admin/sources?connected={provider}", status_code=302)


# ---- Back-compat: /sharepoint/connect + /sharepoint/callback (old redirect URI registered in Entra) ----

@router.post("/connections/sharepoint/connect")
async def sharepoint_connect(store: ConnectionStore = Depends(get_connection_store)) -> dict:
    """Back-compat: same as /oauth/connect?provider=sharepoint but uses the old redirect_uri."""
    s = get_settings()
    state = secrets.token_urlsafe(24)
    await store.put_oauth_state(state, s.brain_tenant_id, "sharepoint")
    redirect_uri = f"{s.brain_api_base_url.rstrip('/')}/admin/connections/sharepoint/callback"
    return {"auth_url": admin_consent_url(
        client_id=s.azure_client_id or "", redirect_uri=redirect_uri, state=state)}


@callback_router.get("/connections/sharepoint/callback")
async def sharepoint_callback(
    state: str = "",
    tenant: str = "",
    admin_consent: str = "",
    error: str = "",
    store: ConnectionStore = Depends(get_connection_store),
    pipeline: IngestPipeline = Depends(get_ingest_pipeline),
) -> RedirectResponse:
    """Back-compat: delegate to the same logic as the generic callback."""
    s = get_settings()
    web = s.web_base_url.rstrip("/")
    state_result = await store.consume_oauth_state(state) if state else None
    if state_result is None:
        brain_tenant, provider = None, "sharepoint"
    else:
        brain_tenant, provider = state_result
    ok = admin_consent.lower() == "true" and bool(tenant) and brain_tenant is not None and not error
    if not ok:
        return RedirectResponse(url=f"{web}/admin/sources?error=oauth", status_code=302)
    display = "SharePoint" if provider == "sharepoint" else "Teams"
    conn = Connection(
        connection_id=uuid.uuid4().hex, tenant_id=brain_tenant, type=provider,
        site_id=tenant, name=f"{display} · {tenant[:8]}", web_url="",
        connected_tenant_id=tenant, status="syncing",
    )
    await store.put_connection(conn)
    runner = SyncRunner(connector=connector_for(conn), pipeline=pipeline, store=store)
    asyncio.create_task(runner.run(connection=conn, actor="admin"))  # detached; we 302 now
    return RedirectResponse(url=f"{web}/admin/sources?connected={provider}", status_code=302)


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


class ConnectRequest(BaseModel):
    site_id: str
    name: str | None = None
    web_url: str | None = None


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


@router.get("/stats")
async def stats(
    store: ConnectionStore = Depends(get_connection_store),
    metrics=Depends(get_metrics_store),
    ai_search=Depends(get_ai_search),
) -> dict:
    tenant = get_settings().brain_tenant_id
    conns = await store.list_connections(tenant)
    items = await ai_search.count_docs(tenant_id=tenant)
    activity = await store.recent_activity(tenant, limit=10)
    sources_live = sum(1 for c in conns if c.status == "live")
    needs: list[dict] = []
    if not conns:
        needs.append({"text": "No data sources connected yet", "where": "Data Sources"})
    for c in conns:
        if c.status == "syncing":
            needs.append({"text": f"{c.name} is still indexing", "where": "Data Sources"})
        if c.status == "error":
            needs.append({"text": f"{c.name} sync failed: {c.error or 'unknown'}", "where": "Data Sources"})
    return {
        "active_users": await metrics.active_users_7d(tenant),
        "queries_7d": await metrics.queries_last_7d(tenant),
        "items_indexed": items,
        "sources_live": sources_live,
        "source_health": [
            {"name": c.name, "type": c.type, "status": c.status, "items": c.item_count}
            for c in conns
        ],
        "recent_activity": [a.model_dump(mode="json") for a in activity],
        "needs_attention": needs,
    }


@router.get("/connections")
async def list_connections(
    store: ConnectionStore = Depends(get_connection_store),
) -> list[dict]:
    tenant = get_settings().brain_tenant_id
    return [c.model_dump(mode="json") for c in await store.list_connections(tenant)]


@router.get("/sharepoint/sites")
async def sharepoint_sites(
    sp: SharePointConnector = Depends(get_sharepoint),
) -> list[dict]:
    return await sp.list_sites()


@router.post("/connections")
async def create_connection(
    body: ConnectRequest,
    bg: BackgroundTasks,
    store: ConnectionStore = Depends(get_connection_store),
    sp: SharePointConnector = Depends(get_sharepoint),
    pipeline=Depends(get_ingest_pipeline),
) -> dict:
    tenant = get_settings().brain_tenant_id
    conn = Connection(
        connection_id=uuid.uuid4().hex, tenant_id=tenant, type="sharepoint",
        site_id=body.site_id, name=body.name or body.site_id,
        web_url=body.web_url or "", status="syncing",
    )
    await store.put_connection(conn)
    runner = SyncRunner(connector=sp, pipeline=pipeline, store=store)
    bg.add_task(runner.run, connection=conn, actor="admin")
    return {"connection_id": conn.connection_id, "status": "syncing"}


@router.post("/connections/{connection_id}/sync")
async def resync(
    connection_id: str,
    bg: BackgroundTasks,
    store: ConnectionStore = Depends(get_connection_store),
    sp: SharePointConnector = Depends(get_sharepoint),
    pipeline=Depends(get_ingest_pipeline),
) -> dict:
    tenant = get_settings().brain_tenant_id
    conn = await store.get_connection(tenant, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="connection not found")
    runner = SyncRunner(connector=sp, pipeline=pipeline, store=store)
    bg.add_task(runner.run, connection=conn, actor="admin")
    return {"connection_id": connection_id, "status": "syncing"}


@router.delete("/connections/{connection_id}")
async def disconnect(
    connection_id: str,
    store: ConnectionStore = Depends(get_connection_store),
) -> dict:
    tenant = get_settings().brain_tenant_id
    await store.delete_connection(tenant, connection_id)
    return {"connection_id": connection_id, "deleted": True}


@router.get("/connections/{connection_id}/job")
async def connection_job(
    connection_id: str,
    store: ConnectionStore = Depends(get_connection_store),
) -> dict:
    tenant = get_settings().brain_tenant_id
    conn = await store.get_connection(tenant, connection_id)
    if not conn or not conn.last_job_id:
        return {"status": "unknown"}
    job = await store.get_job(tenant, conn.last_job_id)
    return job.model_dump(mode="json") if job else {"status": "unknown"}
