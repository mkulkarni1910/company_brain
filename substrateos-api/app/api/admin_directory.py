"""Admin Directory — inspect + manually refresh the synced user directory
(email ↔ Slack id ↔ Entra role) that approval routing reads. Admin-key gated
like the rest of /admin."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api._admin_guard import require_admin
from app.deps import get_directory_store, get_directory_sync

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


def _redact(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


@router.post("/directory/sync")
async def directory_sync(sync=Depends(get_directory_sync)) -> dict:
    """Run the Slack+Entra directory sync now; returns the merge summary."""
    if sync is None:
        return {"errors": ["directory sync not configured"]}
    return await sync.run()


@router.get("/directory")
async def directory_list(store=Depends(get_directory_store)) -> list[dict]:
    """Every directory record, emails redacted."""
    if store is None:
        return []
    return [
        {
            "email": _redact(u.email),
            "slack_id": u.slack_id,
            "display_name": u.display_name,
            "role": u.role,
            "groups": u.groups,
            "manager_email": _redact(u.manager_email) if u.manager_email else None,
            "synced_at": u.synced_at.isoformat() if u.synced_at else None,
        }
        for u in await store.list_all()
    ]
