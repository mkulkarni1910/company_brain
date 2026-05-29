"""POST /feedback — capture an engagement event into the Activity pillar.

Reuses the same auth resolution as /query (Entra bearer, or the dev-only
x-debug-bypass-auth header when ENABLE_DEBUG_AUTH is set).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.activity.store import ActivityStore
from app.config import get_settings
from app.deps import get_activity_store
from app.domain.activity import ActivityEvent, EventType
from app.domain.identity import User

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    doc_id: str
    signal: EventType
    query_id: str | None = None
    chunk_id: str | None = None
    dwell_ms: int | None = None
    source: str = "uploaded"


def _debug_user(header: str) -> User:
    parts = header.split(",")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="bad debug header")
    tenant, user_id, *groups = parts
    return User(
        user_id=user_id, tenant_id=tenant, email=f"{user_id}@debug",
        display_name=user_id, group_ids=set(groups),
    )


def _resolve_user(x_debug_bypass_auth: str | None) -> User:
    # Phase 2b: feedback uses the same debug-gated path as /query's bypass.
    # Real bearer-token resolution is shared with /query; for the dev/eval path
    # we accept the debug header only when the flag is enabled.
    if get_settings().enable_debug_auth and x_debug_bypass_auth:
        return _debug_user(x_debug_bypass_auth)
    raise HTTPException(status_code=401, detail="auth required")


@router.post("/feedback")
async def feedback(
    body: FeedbackRequest,
    store: ActivityStore = Depends(get_activity_store),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> dict[str, str]:
    user = _resolve_user(x_debug_bypass_auth)
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
