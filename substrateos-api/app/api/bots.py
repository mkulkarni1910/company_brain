from __future__ import annotations

import contextlib
import json
import logging
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from app.api.admin import require_admin_key
from app.bots.manifest import build_manifest_zip
from app.bots.slack import post_slack_reply, strip_bot_mention, verify_slack_signature
from app.bots.smalltalk import WELCOME_TEXT, is_smalltalk
from app.bots.teams import (
    build_teams_reply,
    send_teams_activity,
    strip_at_mention,
    verify_teams_jwt,
)
from app.config import get_settings
from app.deps import (
    get_acknowledger,
    get_approval_flow,
    get_connection_store,
    get_conversation_memory,
    get_orchestrator,
    get_refund_flow,
    get_skill_router_svc,
)
from app.domain.identity import User
from app.domain.query import Answer, QueryRequest

router = APIRouter(tags=["bots"])
logger = logging.getLogger(__name__)

_ERROR_TEXT = "Sorry, I couldn't find an answer right now. Try rephrasing your question."
_DISABLED_TEXT = (
    "SubstrateOS is disabled for {surface} — your admin has turned off this surface. "
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
        display_name="SubstrateOS Bot",
        group_ids={f"{tid}:everyone"},
    )


@router.post("/bot/teams")
async def teams_webhook(
    request: Request,
    orchestrator=Depends(get_orchestrator),
    store=Depends(get_connection_store),
    memory=Depends(get_conversation_memory),
    acknowledger=Depends(get_acknowledger),
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

    # Welcome moment: Teams sends conversationUpdate when the bot is installed
    # or added to a chat — introduce the bot instead of staying silent.
    if body.get("type") == "conversationUpdate":
        bot_id = (body.get("recipient") or {}).get("id")
        added = {m.get("id") for m in body.get("membersAdded") or []}
        if bot_id and bot_id in added and await _surface_enabled(store, "teams"):
            await send_teams_activity(
                incoming=body,
                activity={"type": "message", "text": WELCOME_TEXT},
                app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
                tenant_id=s.teams_bot_tenant_id,
            )
        return {}

    if body.get("type") != "message":
        return {}

    text = strip_at_mention(body.get("text") or "").strip()
    if not text:
        return {}

    if not await _surface_enabled(store, "teams"):
        await send_teams_activity(
            incoming=body,
            activity={"type": "message", "text": _DISABLED_TEXT.format(surface="Teams")},
            app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
            tenant_id=s.teams_bot_tenant_id,
        )
        return {}

    # Greetings retrieve nothing and earn an unhelpful refusal — intro instead.
    if is_smalltalk(text):
        await send_teams_activity(
            incoming=body,
            activity={"type": "message", "text": WELCOME_TEXT},
            app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
            tenant_id=s.teams_bot_tenant_id,
        )
        return {}

    conv_id = (body.get("conversation") or {}).get("id") or ""
    cid = f"teams:{conv_id}" if conv_id else None

    # Acknowledge immediately with the fast model, then research with the strong one.
    with contextlib.suppress(Exception):
        ack = await acknowledger.make_ack(text, name=(body.get("from") or {}).get("name"))
        await send_teams_activity(
            incoming=body, activity={"type": "message", "text": ack},
            app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
            tenant_id=s.teams_bot_tenant_id,
        )
    try:
        history = await memory.load_history(user=_bot_user(), conversation_id=cid)
        answer = await orchestrator.answer(
            QueryRequest(query=text), user=_bot_user(), history=history
        )
        if cid:
            await memory.record(
                user=_bot_user(), conversation_id=cid, query=text, answer=answer
            )
    except Exception:
        logger.exception("Teams bot query failed")
        answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")

    # Teams ignores the webhook response body — reply through the Connector API.
    await send_teams_activity(
        incoming=body, activity=build_teams_reply(answer),
        app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
        tenant_id=s.teams_bot_tenant_id,
    )
    return {}


@router.post("/bot/slack")
async def slack_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    orchestrator=Depends(get_orchestrator),
    store=Depends(get_connection_store),
    skill_router=Depends(get_skill_router_svc),
    refund_flow=Depends(get_refund_flow),
    approval_flow=Depends(get_approval_flow),
    memory=Depends(get_conversation_memory),
    acknowledger=Depends(get_acknowledger),
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
    slack_user = event.get("user")
    slack_token = s.slack_bot_token
    enabled = await _surface_enabled(store, "slack")

    async def _reply() -> None:
        if not enabled:
            answer = Answer(
                text=_DISABLED_TEXT.format(surface="Slack"), citations=[], query_id="disabled"
            )
            await post_slack_reply(slack_token, channel, thread_ts, answer)
            return
        if is_smalltalk(text):
            answer = Answer(text=WELCOME_TEXT, citations=[], query_id="smalltalk")
            await post_slack_reply(slack_token, channel, thread_ts, answer)
            return
        # Acknowledge immediately with the fast model, before the heavy research path
        # (orchestrator or refund workflow). Slack has no cheap display name → no greeting.
        with contextlib.suppress(Exception):
            ack = await acknowledger.make_ack(text)
            await post_slack_reply(
                slack_token, channel, thread_ts,
                Answer(text=ack, citations=[], query_id="ack"),
            )
        skill_ctx = None
        if skill_router is not None:
            with contextlib.suppress(Exception):
                skill_ctx = await skill_router.resolve_skill(text)
        workflow = getattr(skill_ctx, "workflow", None) if skill_ctx else None
        if workflow == "refund" and refund_flow is not None:
            try:
                await refund_flow.handle_request(
                    text=skill_ctx.clean_query, channel=channel, thread_ts=thread_ts,
                    requester_slack_id=slack_user, user=_bot_user(),
                )
            except Exception:
                logger.exception("Refund workflow failed")
                answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")
                await post_slack_reply(slack_token, channel, thread_ts, answer)
            return
        if workflow == "approval" and approval_flow is not None:
            try:
                await approval_flow.handle_request(
                    text=skill_ctx.clean_query, channel=channel, thread_ts=thread_ts,
                    requester_slack_id=slack_user, user=_bot_user(),
                )
            except Exception:
                logger.exception("Approval workflow failed")
                answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")
                await post_slack_reply(slack_token, channel, thread_ts, answer)
            return
        try:
            effective = skill_ctx.clean_query if skill_ctx else text
            cid = f"slack:{channel}:{thread_ts}"
            history = await memory.load_history(user=_bot_user(), conversation_id=cid)
            answer = await orchestrator.answer(
                QueryRequest(query=effective), user=_bot_user(),
                skill_context=skill_ctx, history=history,
            )
            await memory.record(
                user=_bot_user(), conversation_id=cid, query=effective, answer=answer
            )
        except Exception:
            logger.exception("Slack bot query failed")
            answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")
        await post_slack_reply(slack_token, channel, thread_ts, answer)

    background_tasks.add_task(_reply)
    return {}


@router.post("/bot/slack/interactive")
async def slack_interactive(
    request: Request,
    background_tasks: BackgroundTasks,
    refund_flow=Depends(get_refund_flow),
    approval_flow=Depends(get_approval_flow),
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> dict:
    """Slack interactivity (button clicks). Must ack within 3s — work runs in background."""
    raw_body = await request.body()
    s = get_settings()
    if not s.slack_bot_token or not s.slack_signing_secret:
        raise HTTPException(status_code=503, detail="Slack bot not configured")
    if not verify_slack_signature(
        s.slack_signing_secret, x_slack_request_timestamp or "", raw_body, x_slack_signature or ""
    ):
        raise HTTPException(status_code=403, detail="invalid signature")
    payload_raw = (parse_qs(raw_body.decode(errors="replace")).get("payload") or ["{}"])[0]
    try:
        payload = json.loads(payload_raw)
    except ValueError:
        return {}
    if payload.get("type") != "block_actions":
        return {}
    # Dispatch by action_id so refund and approval cards each reach their own flow.
    action_id = ((payload.get("actions") or [{}])[0]).get("action_id", "")
    if action_id.startswith("approval_") and approval_flow is not None:
        background_tasks.add_task(approval_flow.handle_action, payload)
    elif refund_flow is not None:
        background_tasks.add_task(refund_flow.handle_action, payload)
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
