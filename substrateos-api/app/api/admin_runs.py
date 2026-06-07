"""Admin Runs — surfaces real conversation activity (web/Slack/Teams) as runs for
the Admin Panel, org-wide (tenant-scoped). Workflow runs keep their own /runs API;
this adds the conversation side. Admin-key gated like the rest of /admin."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api._admin_guard import require_admin
from app.config import get_settings
from app.deps import get_conversation_store

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


def _surface(conv_id: str) -> str:
    if conv_id.startswith("slack:"):
        return "slack"
    if conv_id.startswith("teams:"):
        return "teams"
    return "web"


def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


@router.get("/conversation-runs")
async def conversation_runs(store=Depends(get_conversation_store)) -> list[dict]:
    """Every conversation in the tenant, newest first, as run summaries."""
    if store is None:
        return []
    tid = get_settings().substrateos_tenant_id
    items = await store.list_all(tenant_id=tid, limit=50)
    return [
        {
            "id": it["id"],
            "title": it["title"] or "Conversation",
            "surface": _surface(it["id"]),
            "turn_count": it["turn_count"],
            "updated_at": _iso(it["updated_at"]),
        }
        for it in items
    ]


@router.get("/conversation-runs/{conversation_id:path}")
async def conversation_run(conversation_id: str, request: Request, store=Depends(get_conversation_store)) -> dict:
    """One conversation: surface, resolved asker, and every turn (the audit trail)."""
    if store is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    tid = get_settings().substrateos_tenant_id
    res = await store.get_any(tenant_id=tid, conversation_id=conversation_id)
    if res is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    conv = res["conversation"]
    uid = res["user_id"]

    # Web turns carry the real asker id; Slack/Teams are stored under the bot user,
    # so the human asker isn't known — the surface stands in for them.
    asker: str | None = None
    if uid and uid != "bot":
        people_graph = getattr(request.app.state, "people_graph", None)
        if people_graph is not None:
            try:
                people = await people_graph.resolve_people([uid], tid)
                asker = people[0].display_name if people else None
            except Exception:  # noqa: BLE001 - best-effort
                pass

    return {
        "id": conv.id,
        "title": conv.title or "Conversation",
        "surface": _surface(conv.id),
        "updated_at": _iso(conv.updated_at),
        "asker": asker,
        "turns": [
            {
                "query": t.query,
                "answer": {
                    "text": t.answer.text,
                    "citations": [c.model_dump() for c in t.answer.citations],
                },
                "ts": _iso(t.ts),
            }
            for t in conv.turns
        ],
    }
