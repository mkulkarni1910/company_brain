from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote

import httpx
from jose import JWTError, jwt

from app.bots.formatting import to_teams_text
from app.domain.query import Answer

logger = logging.getLogger(__name__)

_BF_JWKS_URL = "https://login.botframework.com/v1/.well-known/keys"
_BF_ISSUER = "https://api.botframework.com"
_BF_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_BF_SCOPE = "https://api.botframework.com/.default"
_JWKS_TTL = 3600.0

_jwks_cache: dict | None = None
_jwks_cache_ts: float = 0.0

# Connector access token cache: {"token": str | None, "exp": epoch seconds}
_token_cache: dict = {"token": None, "exp": 0.0}


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_cache_ts
    if _jwks_cache and time.time() - _jwks_cache_ts < _JWKS_TTL:
        return _jwks_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(_BF_JWKS_URL, timeout=5.0)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_cache_ts = time.time()
    return _jwks_cache  # type: ignore[return-value]


async def verify_teams_jwt(token: str, app_id: str) -> bool:
    """Verify a Bot Framework JWT against Microsoft's published JWKS."""
    try:
        jwks = await _get_jwks()
        jwt.decode(token, jwks, algorithms=["RS256"], audience=app_id, issuer=_BF_ISSUER)
        return True
    except (JWTError, Exception):  # noqa: BLE001
        return False


async def _connector_token(
    app_id: str, app_password: str, tenant_id: str | None = None
) -> str:
    """Client-credentials token for the Bot Framework Connector API (cached).

    Single-tenant bot registrations (the Dev Portal default since multi-tenant
    creation was deprecated) only accept tokens issued by the bot's own tenant —
    pass tenant_id for those; the botframework.com endpoint serves legacy
    multi-tenant bots.
    """
    if _token_cache["token"] and time.time() < _token_cache["exp"] - 60:
        return _token_cache["token"]
    token_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        if tenant_id else _BF_TOKEN_URL
    )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_password,
                "scope": _BF_SCOPE,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        d = resp.json()
    _token_cache["token"] = d["access_token"]
    _token_cache["exp"] = time.time() + float(d.get("expires_in", 3600))
    return _token_cache["token"]


async def send_teams_activity(
    *, incoming: dict, activity: dict, app_id: str, app_password: str,
    tenant_id: str | None = None,
) -> bool:
    """POST a reply activity to the conversation's serviceUrl.

    Teams ignores activities returned in the webhook's HTTP response body —
    replies only render when sent through the Connector REST API. Returns
    False (and logs) on any failure so the webhook never 500s back at Teams.
    """
    service_url = (incoming.get("serviceUrl") or "").rstrip("/")
    conv_id = (incoming.get("conversation") or {}).get("id") or ""
    reply_to = incoming.get("id") or ""
    if not service_url or not conv_id:
        logger.warning("teams reply skipped: missing serviceUrl/conversation.id")
        return False

    out = {
        **activity,
        "from": incoming.get("recipient") or {},   # the bot
        "recipient": incoming.get("from") or {},   # the human sender
        "conversation": {"id": conv_id},
        "replyToId": reply_to,
    }
    # conversation ids contain ':' '@' ';' — quote so they survive as one path segment
    path = f"/v3/conversations/{quote(conv_id, safe='')}/activities"
    if reply_to:
        path += f"/{quote(reply_to, safe='')}"
    try:
        token = await _connector_token(app_id, app_password, tenant_id=tenant_id)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{service_url}{path}",
                json=out,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
        if resp.status_code >= 300:
            logger.error("teams reply failed %s: %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception:  # noqa: BLE001 - reply failure must not 500 the webhook
        logger.exception("teams reply failed")
        return False


def strip_at_mention(text: str) -> str:
    """Remove <at>BotName</at> prefixes Teams injects into message text."""
    return re.sub(r"<at>[^<]*</at>", "", text).strip()


def build_teams_reply(answer: Answer) -> dict:
    """Build a Bot Framework Activity containing an Adaptive Card."""
    body: list[dict] = [
        {"type": "TextBlock", "text": to_teams_text(answer.text), "wrap": True}
    ]
    if answer.citations:
        n = len(answer.citations)
        body.append({
            "type": "TextBlock", "wrap": True, "isSubtle": True, "spacing": "Small",
            "size": "Small", "text": f"grounded · {n} source{'' if n == 1 else 's'}",
        })
    card: dict = {
        "type": "AdaptiveCard",
        "version": "1.5",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": body,
    }
    actions = [
        {"type": "Action.OpenUrl", "title": c.title[:50], "url": c.source_url}
        for c in answer.citations[:5]
    ]
    if actions:
        card["actions"] = actions
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
    }
