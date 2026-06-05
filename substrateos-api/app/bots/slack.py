from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time

import httpx

from app.domain.query import Answer

logger = logging.getLogger(__name__)


def verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Verify Slack's HMAC-SHA256 request signature and reject replays >5 min old."""
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
        base = f"v0:{timestamp}:".encode() + body
        expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:  # noqa: BLE001
        return False


def strip_bot_mention(text: str) -> str:
    """Remove <@USERID> prefix Slack injects at the start of app_mention text."""
    return re.sub(r"^<@[A-Z0-9]+>\s*", "", text).strip()


async def post_slack_reply(
    token: str, channel: str, thread_ts: str | None, answer: Answer
) -> str | None:
    """Post a formatted Slack message with answer text and source links.

    Returns the Slack API error string on failure, None on success. Slack returns
    HTTP 200 even for API errors (not_in_channel, missing_scope, …) — the real
    outcome is the `ok` field in the JSON body, so it must be checked and logged.
    """
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": answer.text[:3000]}},
    ]
    if answer.citations:
        links = " · ".join(
            f"<{c.source_url}|{c.title[:40]}>" for c in answer.citations[:5]
        )
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Sources: {links}"}],
        })
    # `text` fallback keeps notifications working and satisfies clients that
    # reject blocks-only messages.
    payload: dict = {"channel": channel, "blocks": blocks, "text": answer.text[:3000]}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10.0,
            )
        body = resp.json()
        if not body.get("ok"):
            error = body.get("error", "unknown_error")
            logger.warning("Slack chat.postMessage failed: %s (channel=%s)", error, channel)
            return error
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Slack post_message failed")
        return "request_failed"


async def slack_call(token: str, method: str, payload: dict) -> dict | None:
    """POST a Slack Web API method; return the body when ok=true, else None.

    Slack returns HTTP 200 even for API errors — `ok` in the body is the truth.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://slack.com/api/{method}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10.0,
            )
        body = resp.json()
        if not body.get("ok"):
            logger.warning("Slack %s failed: %s", method, body.get("error", "unknown_error"))
            return None
        return body
    except Exception:  # noqa: BLE001
        logger.exception("Slack %s request failed", method)
        return None
