from __future__ import annotations

import logging
import re
import time

import httpx
from jose import JWTError, jwt

from app.domain.query import Answer

logger = logging.getLogger(__name__)

_BF_JWKS_URL = "https://login.botframework.com/v1/.well-known/keys"
_BF_ISSUER = "https://api.botframework.com"
_JWKS_TTL = 3600.0

_jwks_cache: dict | None = None
_jwks_cache_ts: float = 0.0


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


def strip_at_mention(text: str) -> str:
    """Remove <at>BotName</at> prefixes Teams injects into message text."""
    return re.sub(r"<at>[^<]*</at>", "", text).strip()


def build_teams_reply(answer: Answer) -> dict:
    """Build a Bot Framework Activity containing an Adaptive Card."""
    card: dict = {
        "type": "AdaptiveCard",
        "version": "1.5",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": [{"type": "TextBlock", "text": answer.text, "wrap": True}],
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
