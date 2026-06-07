from __future__ import annotations

import contextlib
import json
import logging
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from app.api.admin import require_admin_key
from app.bots.github_cards import (
    cancelled_blocks,
    pr_created_blocks,
    preview_blocks,
    teams_preview_activity,
)
from app.bots.manifest import build_manifest_zip
from app.bots.slack import post_slack_reply, slack_call, strip_bot_mention, verify_slack_signature
from app.bots.smalltalk import WELCOME_TEXT, is_smalltalk
from app.bots.teams import (
    build_teams_reply,
    get_teams_member_email,
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
    get_directory_service,
    get_github_flow,
    get_github_store,
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


async def _slack_profile(token: str, slack_user_id: str | None) -> tuple[str, str | None]:
    """(display_name, email) via users.info — degrades to ('A teammate', None)."""
    if not slack_user_id:
        return "A teammate", None
    body = await slack_call(token, "users.info", {"user": slack_user_id})
    u = (body or {}).get("user") or {}
    profile = u.get("profile") or {}
    name = profile.get("display_name") or u.get("real_name") or u.get("name") or "A teammate"
    return name, profile.get("email")


async def _github_slack_action(payload: dict, flow, token: str) -> None:
    actions = payload.get("actions") or []
    if not actions:
        return
    action_id = actions[0].get("action_id", "")
    run_id = actions[0].get("value") or ""
    actor_name, actor_email = await _slack_profile(token, (payload.get("user") or {}).get("id"))
    if action_id == "github_create":
        result = await flow.confirm(run_id, actor_email=actor_email, actor_name=actor_name)
    elif action_id == "github_cancel":
        result = await flow.cancel(run_id, actor_email=actor_email, actor_name=actor_name)
    else:
        return
    container = payload.get("container") or {}
    ch, ts = container.get("channel_id"), container.get("message_ts")
    if not (ch and ts):
        return
    if result.ok and result.pr_url:
        await slack_call(token, "chat.update", {
            "channel": ch, "ts": ts, "text": "PR created",
            **pr_created_blocks(pr_url=result.pr_url, title="PR created", actor_name=actor_name)})
    elif result.ok:
        await slack_call(token, "chat.update", {
            "channel": ch, "ts": ts, "text": "Cancelled",
            **cancelled_blocks(title="PR draft", actor_name=actor_name)})
    else:
        await slack_call(token, "chat.postMessage", {
            "channel": ch, "thread_ts": ts, "text": result.message or "Action failed."})


@router.post("/bot/teams")
async def teams_webhook(
    request: Request,
    orchestrator=Depends(get_orchestrator),
    store=Depends(get_connection_store),
    memory=Depends(get_conversation_memory),
    acknowledger=Depends(get_acknowledger),
    skill_router=Depends(get_skill_router_svc),
    github_flow=Depends(get_github_flow),
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

    # Adaptive Card Action.Submit arrives as a message with `value` and no text.
    value = body.get("value")
    if (body.get("type") == "message" and isinstance(value, dict)
            and str(value.get("action", "")).startswith("github_") and github_flow is not None):
        if not await _surface_enabled(store, "teams"):
            await send_teams_activity(
                incoming=body,
                activity={"type": "message", "text": _DISABLED_TEXT.format(surface="Teams")},
                app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
                tenant_id=s.teams_bot_tenant_id)
            return {}
        email = await get_teams_member_email(
            incoming=body, app_id=s.teams_bot_app_id,
            app_password=s.teams_bot_app_password, tenant_id=s.teams_bot_tenant_id)
        actor = (body.get("from") or {}).get("name") or email or "Someone"
        run_id = str(value.get("run_id") or "")
        if value["action"] == "github_create":
            result = await github_flow.confirm(run_id, actor_email=email, actor_name=actor)
            text = (f"✅ PR created — {result.pr_url}" if result.ok
                    else f"⚠ {result.message}")
        else:
            result = await github_flow.cancel(run_id, actor_email=email, actor_name=actor)
            text = ("✕ Cancelled — nothing reached GitHub." if result.ok
                    else f"⚠ {result.message}")
        await send_teams_activity(
            incoming=body, activity={"type": "message", "text": text},
            app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
            tenant_id=s.teams_bot_tenant_id)
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

    skill_ctx = None
    if skill_router is not None:
        with contextlib.suppress(Exception):
            skill_ctx = await skill_router.resolve_skill(text)
    if getattr(skill_ctx, "workflow", None) == "github" and github_flow is not None:
        email = await get_teams_member_email(
            incoming=body, app_id=s.teams_bot_app_id,
            app_password=s.teams_bot_app_password, tenant_id=s.teams_bot_tenant_id)
        name = (body.get("from") or {}).get("name") or "A teammate"
        result = await github_flow.start(skill_ctx.clean_query, requester_name=name,
                                         requester_email=email, surface="teams")
        if result.status == "preview":
            activity = teams_preview_activity(
                draft=result.run.pr_draft, repo_label="the configured repo",
                run_id=result.run.id)
        else:
            activity = {"type": "message", "text": result.message or _ERROR_TEXT}
        await send_teams_activity(
            incoming=body, activity=activity,
            app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
            tenant_id=s.teams_bot_tenant_id)
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
    github_flow=Depends(get_github_flow),
    github_store=Depends(get_github_store),
    memory=Depends(get_conversation_memory),
    acknowledger=Depends(get_acknowledger),
    directory=Depends(get_directory_service),
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
        if workflow == "github" and github_flow is not None:
            try:
                name, email = await _slack_profile(slack_token, slack_user)
                result = await github_flow.start(
                    skill_ctx.clean_query, requester_name=name, requester_email=email,
                    surface="slack", channel=channel, thread_ts=thread_ts,
                )
                if result.status == "preview":
                    repo_label = "the configured repo"
                    if github_store is not None:
                        cfg = await github_store.get_config(get_settings().substrateos_tenant_id)
                        if cfg:
                            repo_label = f"{cfg.owner}/{cfg.repo}"
                    d = result.run.pr_draft
                    await slack_call(slack_token, "chat.postMessage", {
                        "channel": channel, "thread_ts": thread_ts,
                        "text": f"PR drafted: {d.title}",
                        **preview_blocks(draft=d, repo_label=repo_label, run_id=result.run.id),
                    })
                else:
                    await post_slack_reply(
                        slack_token, channel, thread_ts,
                        Answer(text=result.message or _ERROR_TEXT, citations=[],
                               query_id="github"))
            except Exception:
                logger.exception("GitHub workflow failed")
                await post_slack_reply(slack_token, channel, thread_ts,
                                       Answer(text=_ERROR_TEXT, citations=[], query_id="err"))
            return
        # Known requester? Their identity scopes and personalizes the answer.
        requester = None
        if directory is not None:
            with contextlib.suppress(Exception):
                _, req_email = await _slack_profile(slack_token, slack_user)
                requester = await directory.resolve(req_email)
        try:
            effective = skill_ctx.clean_query if skill_ctx else text
            cid = f"slack:{channel}:{thread_ts}"
            history = await memory.load_history(user=_bot_user(), conversation_id=cid)
            answer = await orchestrator.answer(
                QueryRequest(query=effective), user=_bot_user(),
                skill_context=skill_ctx, history=history, requester=requester,
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
    github_flow=Depends(get_github_flow),
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
    # Dispatch by action_id so refund, approval, and github cards each reach their own flow.
    action_id = ((payload.get("actions") or [{}])[0]).get("action_id", "")
    if action_id.startswith("approval_") and approval_flow is not None:
        background_tasks.add_task(approval_flow.handle_action, payload)
    elif action_id.startswith("github_") and github_flow is not None:
        background_tasks.add_task(_github_slack_action, payload, github_flow,
                                  s.slack_bot_token or "")
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
        "github": {"configured": bool(s.github_client_id and s.github_client_secret)},
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
