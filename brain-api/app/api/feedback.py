"""POST /feedback — capture an engagement event into the Activity pillar.

Reuses the same auth resolution as /query (Easy Auth principal, Entra bearer,
or the dev-only x-debug-bypass-auth header when ENABLE_DEBUG_AUTH is set).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.activity.store import ActivityStore
from app.api._auth_resolve import resolve_user
from app.deps import get_activity_store
from app.domain.activity import ActivityEvent, EventType

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    doc_id: str
    signal: EventType
    query_id: str | None = None
    chunk_id: str | None = None
    dwell_ms: int | None = None
    source: str = "uploaded"


@router.post("/feedback")
async def feedback(
    body: FeedbackRequest,
    store: ActivityStore = Depends(get_activity_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> dict[str, str]:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    event = ActivityEvent(
        timestamp=datetime.now(UTC),
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        doc_id=body.doc_id,
        event_type=body.signal,
        source=body.source,
        query_id=body.query_id,
        chunk_id=body.chunk_id,
        duration_ms=body.dwell_ms,
    )
    await store.ingest_event(event)
    return {"status": "recorded"}
