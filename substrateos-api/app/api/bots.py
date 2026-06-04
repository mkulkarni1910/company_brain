from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from app.api.admin import require_admin_key
from app.bots.manifest import build_manifest_zip
from app.bots.slack import post_slack_reply, strip_bot_mention, verify_slack_signature
from app.bots.teams import build_teams_reply, strip_at_mention, verify_teams_jwt
from app.config import get_settings
from app.deps import get_connection_store, get_orchestrator
from app.domain.identity import User
from app.domain.query import Answer, QueryRequest

router = APIRouter(tags=["bots"])
logger = logging.getLogger(__name__)

_ERROR_TEXT = "Sorry, I couldn't find an answer right now. Try rephrasing your question."
_DISABLED_TEXT = (
    "SubStrateOS is disabled for {surface} — your admin has turned off this surface. "
    "Contact your administrator to re-enable it."
)


async def _surface_enabled(store, name: str) -> bool:
    """Check the admin surface toggle; fail-open so a config-store outage
    never silences the bots."""
    try:
        surfaces = await store.list_surfaces(get_settings().substrateos_tenant_id)
        cfg = next((s for s in surfaces if s.name == name), None)
        return cfg.enabled if cfg is not None else True
    except Exception:  # noqa: BLE001
        logger.warning("surface check failed for %s; failing open", name)
        return True


def _bot_user() -> User:
    """Bot user mapped to the pilot tenant with tenant-wide everyone access."""
    s = get_settings()
    tid = s.substrateos_tenant_id
    return User(
        user_id="bot",
        tenant_id=tid,
        email="bot@substrateos",
        display_name="SubStrateOS Bot",
        group_ids={f"{tid}:everyone"},
    )


@router.post("/bot/teams")
async def teams_webhook(
    request: Request,
    orchestrator=Depends(get_orchestrator),
    store=Depends(get_connection_store),
    authorization: str | None = Header(default=None),
) -> dict:
    s = get_settings()
    if not s.teams_bot_app_id or not s.teams_bot_app_password:
        raise HTTPException(status_code=503, detail="Teams bot not configured")

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if not await verify_teams_jwt(token, s.teams_bot_app_id):
        raise HTTPException(status_code=401, detail="invalid token")

    body = await request.json()
    if body.get("type") != "message":
        return {}

    text = strip_at_mention(body.get("text") or "").strip()
    if not text:
        return {}

    if not await _surface_enabled(store, "teams"):
        return {"type": "message", "text": _DISABLED_TEXT.format(surface="Teams")}

    try:
        answer = await orchestrator.answer(QueryRequest(query=text), user=_bot_user())
    except Exception:
        logger.exception("Teams bot query failed")
        answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")

    return build_teams_reply(answer)


@router.post("/bot/slack")
async def slack_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    orchestrator=Depends(get_orchestrator),
    store=Depends(get_connection_store),
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> dict:
    raw_body = await request.body()
    body = json.loads(raw_body)

    # url_verification is Slack's initial handshake — no HMAC yet, always pass.
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    s = get_settings()
    if not s.slack_bot_token or not s.slack_signing_secret:
        raise HTTPException(status_code=503, detail="Slack bot not configured")

    if not verify_slack_signature(
        s.slack_signing_secret,
        x_slack_request_timestamp or "",
        raw_body,
        x_slack_signature or "",
    ):
        raise HTTPException(status_code=403, detail="invalid signature")

    event = body.get("event", {})
    if event.get("type") not in ("app_mention", "message") or event.get("bot_id"):
        return {}

    text = strip_bot_mention(event.get("text") or "").strip()
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts") or event.get("ts")
    slack_token = s.slack_bot_token
    enabled = await _surface_enabled(store, "slack")

    async def _reply() -> None:
        if not enabled:
            answer = Answer(
                text=_DISABLED_TEXT.format(surface="Slack"), citations=[], query_id="disabled"
            )
            await post_slack_reply(slack_token, channel, thread_ts, answer)
            return
        try:
            answer = await orchestrator.answer(QueryRequest(query=text), user=_bot_user())
        except Exception:
            logger.exception("Slack bot query failed")
            answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")
        await post_slack_reply(slack_token, channel, thread_ts, answer)

    background_tasks.add_task(_reply)
    return {}


@router.get("/admin/bot/status", dependencies=[Depends(require_admin_key)])
async def bot_status() -> dict:
    s = get_settings()
    return {
        "teams": {
            "configured": bool(s.teams_bot_app_id and s.teams_bot_app_password),
            "app_id": s.teams_bot_app_id,
        },
        "slack": {"configured": bool(s.slack_bot_token and s.slack_signing_secret)},
    }


@router.get("/admin/bot/teams/manifest", dependencies=[Depends(require_admin_key)])
async def teams_manifest() -> Response:
    s = get_settings()
    if not s.teams_bot_app_id:
        raise HTTPException(status_code=404, detail="Teams bot not configured")
    api_host = urlparse(s.substrateos_api_base_url).netloc or "localhost:8000"
    zip_bytes = build_manifest_zip(s.teams_bot_app_id, api_host)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=substrateos-teams.zip"},
    )
