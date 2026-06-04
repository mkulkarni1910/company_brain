# Teams & Slack Bot Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real Teams and Slack bot handlers to SubStrateOS so users can @-mention the bot in either platform and get grounded answers, and make the admin Surfaces page "Install" buttons drive a real setup flow.

**Architecture:** New `app/bots/` Python module (teams.py, slack.py, manifest.py) + `app/api/bots.py` router with 4 endpoints. Teams responds synchronously (Adaptive Card in 200 body); Slack acknowledges immediately and replies asynchronously via `chat.postMessage`. Frontend fetches bot status on load and shows three-state (Teams) or two-state (Slack) install UI with modals.

**Tech Stack:** `python-jose[cryptography]` (JWT verification, already installed), `httpx` (Slack API calls, already installed), stdlib `zipfile`/`struct`/`zlib`/`hmac` (manifest + HMAC), FastAPI `BackgroundTasks` (Slack async reply), Next.js + TypeScript.

---

## File Map

**New:**
- `substrateos-api/app/bots/__init__.py` — empty package marker
- `substrateos-api/app/bots/teams.py` — `strip_at_mention`, `verify_teams_jwt`, `build_teams_reply`
- `substrateos-api/app/bots/slack.py` — `verify_slack_signature`, `strip_bot_mention`, `post_slack_reply`
- `substrateos-api/app/bots/manifest.py` — `build_manifest_zip`
- `substrateos-api/app/api/bots.py` — 4 FastAPI endpoints
- `substrateos-api/tests/test_bots.py` — unit tests for all helper functions
- `substrateos-api/tests/test_bots_api.py` — integration tests for endpoints

**Modified:**
- `substrateos-api/app/config.py` — 4 new optional bot credential fields
- `substrateos-api/app/main.py` — register bots router
- `substrateos-api/app/api/admin.py` — extend `SurfacePatch` + `patch_surface` to handle `installed`/`workspace_name`
- `web/lib/adminApi.ts` — `getBotStatus`, `downloadTeamsManifest`, extend `patchSurface`
- `web/app/admin/surfaces/page.tsx` — bot status fetch, install modals, three-state cards

---

## Task 1: Config — add bot credential settings

**Files:**
- Modify: `substrateos-api/app/config.py`

- [ ] **Step 1: Write the failing test**

Add to a new file `substrateos-api/tests/test_bot_config.py`:

```python
import pytest
from app.config import get_settings


def test_bot_config_defaults_to_none(monkeypatch):
    get_settings.cache_clear()
    for k in ("TEAMS_BOT_APP_ID", "TEAMS_BOT_APP_PASSWORD", "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"):
        monkeypatch.delenv(k, raising=False)
    s = get_settings()
    assert s.teams_bot_app_id is None
    assert s.teams_bot_app_password is None
    assert s.slack_bot_token is None
    assert s.slack_signing_secret is None


def test_bot_config_reads_from_env(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("TEAMS_BOT_APP_ID", "my-app-id")
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", "my-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "signingsecret")
    s = get_settings()
    assert s.teams_bot_app_id == "my-app-id"
    assert s.teams_bot_app_password == "my-secret"
    assert s.slack_bot_token == "xoxb-123"
    assert s.slack_signing_secret == "signingsecret"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd substrateos-api && python -m pytest tests/test_bot_config.py -v
```

Expected: `FAILED` — `Settings` has no attribute `teams_bot_app_id`.

- [ ] **Step 3: Add the four fields to Settings in `app/config.py`**

After the `mcp_enabled` line, add:

```python
    # Bot integrations (Teams + Slack)
    teams_bot_app_id: str | None = None        # TEAMS_BOT_APP_ID
    teams_bot_app_password: str | None = None  # TEAMS_BOT_APP_PASSWORD
    slack_bot_token: str | None = None         # SLACK_BOT_TOKEN
    slack_signing_secret: str | None = None    # SLACK_SIGNING_SECRET
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd substrateos-api && python -m pytest tests/test_bot_config.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/config.py substrateos-api/tests/test_bot_config.py
git commit -m "feat(bots): add Teams/Slack credential settings to config"
```

---

## Task 2: Teams bot helpers

**Files:**
- Create: `substrateos-api/app/bots/__init__.py`
- Create: `substrateos-api/app/bots/teams.py`
- Test in: `substrateos-api/tests/test_bots.py`

- [ ] **Step 1: Write failing tests for text stripping and JWT verification**

Create `substrateos-api/tests/test_bots.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import struct
import time
import zipfile

import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat,
)
from jose import jwt


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_rsa_keypair():
    """Return (private_pem_bytes, jwks_dict) using a fresh RSA-2048 key."""
    priv = rsa.generate_private_key(65537, 2048, default_backend())
    pub_nums = priv.public_key().public_numbers()
    n_bytes = pub_nums.n.to_bytes((pub_nums.n.bit_length() + 7) // 8, "big")
    e_bytes = pub_nums.e.to_bytes((pub_nums.e.bit_length() + 7) // 8, "big")
    jwks = {
        "keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "k1",
            "n": base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode(),
            "e": base64.urlsafe_b64encode(e_bytes).rstrip(b"=").decode(),
        }]
    }
    pem = priv.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    return pem, jwks


def _make_token(pem: bytes, app_id: str, *, issuer: str = "https://api.botframework.com") -> str:
    return jwt.encode(
        {"aud": app_id, "iss": issuer, "exp": int(time.time()) + 3600},
        pem, algorithm="RS256", headers={"kid": "k1"},
    )


# ── strip_at_mention ──────────────────────────────────────────────────────────

def test_strip_at_mention_basic():
    from app.bots.teams import strip_at_mention
    assert strip_at_mention("<at>SubStrateOS</at> what is PTO?") == "what is PTO?"


def test_strip_at_mention_newline():
    from app.bots.teams import strip_at_mention
    assert strip_at_mention("<at>Bot</at>\nhello world") == "hello world"


def test_strip_at_mention_no_mention():
    from app.bots.teams import strip_at_mention
    assert strip_at_mention("just a message") == "just a message"


# ── verify_teams_jwt ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_teams_jwt_valid(monkeypatch):
    import app.bots.teams as m
    pem, jwks = _make_rsa_keypair()
    monkeypatch.setattr(m, "_jwks_cache", jwks)
    monkeypatch.setattr(m, "_jwks_cache_ts", time.time())
    token = _make_token(pem, "my-app")
    assert await m.verify_teams_jwt(token, "my-app") is True


@pytest.mark.asyncio
async def test_verify_teams_jwt_wrong_audience(monkeypatch):
    import app.bots.teams as m
    pem, jwks = _make_rsa_keypair()
    monkeypatch.setattr(m, "_jwks_cache", jwks)
    monkeypatch.setattr(m, "_jwks_cache_ts", time.time())
    token = _make_token(pem, "other-app")
    assert await m.verify_teams_jwt(token, "my-app") is False


@pytest.mark.asyncio
async def test_verify_teams_jwt_wrong_issuer(monkeypatch):
    import app.bots.teams as m
    pem, jwks = _make_rsa_keypair()
    monkeypatch.setattr(m, "_jwks_cache", jwks)
    monkeypatch.setattr(m, "_jwks_cache_ts", time.time())
    token = _make_token(pem, "my-app", issuer="https://evil.com")
    assert await m.verify_teams_jwt(token, "my-app") is False


@pytest.mark.asyncio
async def test_verify_teams_jwt_empty_token(monkeypatch):
    import app.bots.teams as m
    pem, jwks = _make_rsa_keypair()
    monkeypatch.setattr(m, "_jwks_cache", jwks)
    monkeypatch.setattr(m, "_jwks_cache_ts", time.time())
    assert await m.verify_teams_jwt("", "my-app") is False


# ── build_teams_reply ────────────────────────────────────────────────────────

def test_build_teams_reply_shape():
    from app.bots.teams import build_teams_reply
    from app.domain.query import Answer, Citation
    answer = Answer(
        text="Here is the answer.",
        citations=[
            Citation(doc_id="d1", chunk_id="c1", source_url="https://x.com/doc",
                     title="Policy Doc", snippet="snippet"),
        ],
        query_id="q1",
    )
    reply = build_teams_reply(answer)
    assert reply["type"] == "message"
    assert reply["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = reply["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert any(b.get("text") == "Here is the answer." for b in card["body"])
    assert card["actions"][0]["type"] == "Action.OpenUrl"
    assert card["actions"][0]["url"] == "https://x.com/doc"


def test_build_teams_reply_caps_citations():
    from app.bots.teams import build_teams_reply
    from app.domain.query import Answer, Citation
    cits = [Citation(doc_id=f"d{i}", chunk_id=f"c{i}", source_url=f"https://x.com/{i}",
                     title=f"Doc {i}", snippet="s") for i in range(10)]
    reply = build_teams_reply(Answer(text="t", citations=cits, query_id="q"))
    assert len(reply["attachments"][0]["content"]["actions"]) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd substrateos-api && python -m pytest tests/test_bots.py::test_strip_at_mention_basic tests/test_bots.py::test_build_teams_reply_shape -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'app.bots'`.

- [ ] **Step 3: Create the package and implement `app/bots/teams.py`**

Create `substrateos-api/app/bots/__init__.py` (empty):
```python
```

Create `substrateos-api/app/bots/teams.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd substrateos-api && python -m pytest tests/test_bots.py -k "strip_at_mention or teams_jwt or teams_reply" -v
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/bots/__init__.py substrateos-api/app/bots/teams.py substrateos-api/tests/test_bots.py
git commit -m "feat(bots): add Teams JWT verification, text stripping, Adaptive Card builder"
```

---

## Task 3: Slack bot helpers

**Files:**
- Create: `substrateos-api/app/bots/slack.py`
- Test: `substrateos-api/tests/test_bots.py` (append)

- [ ] **Step 1: Append failing Slack tests to `tests/test_bots.py`**

```python
# ── verify_slack_signature ───────────────────────────────────────────────────

def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_slack_signature_valid():
    from app.bots.slack import verify_slack_signature
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    sig = _slack_sig("mysecret", ts, body)
    assert verify_slack_signature("mysecret", ts, body, sig) is True


def test_slack_signature_tampered_body():
    from app.bots.slack import verify_slack_signature
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    sig = _slack_sig("mysecret", ts, body)
    assert verify_slack_signature("mysecret", ts, b'{"type":"tampered"}', sig) is False


def test_slack_signature_expired_timestamp():
    from app.bots.slack import verify_slack_signature
    ts = str(int(time.time()) - 400)   # 400 s > 300 s threshold
    body = b'{"type":"event_callback"}'
    sig = _slack_sig("mysecret", ts, body)
    assert verify_slack_signature("mysecret", ts, body, sig) is False


def test_slack_signature_wrong_secret():
    from app.bots.slack import verify_slack_signature
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    sig = _slack_sig("correct", ts, body)
    assert verify_slack_signature("wrong", ts, body, sig) is False


# ── strip_bot_mention ─────────────────────────────────────────────────────────

def test_strip_bot_mention_basic():
    from app.bots.slack import strip_bot_mention
    assert strip_bot_mention("<@U123ABC> what is PTO?") == "what is PTO?"


def test_strip_bot_mention_no_mention():
    from app.bots.slack import strip_bot_mention
    assert strip_bot_mention("hello") == "hello"


def test_strip_bot_mention_extra_spaces():
    from app.bots.slack import strip_bot_mention
    assert strip_bot_mention("<@UABC>   hello world") == "hello world"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd substrateos-api && python -m pytest tests/test_bots.py -k "slack_signature or strip_bot" -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'app.bots.slack'`.

- [ ] **Step 3: Create `substrateos-api/app/bots/slack.py`**

```python
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
) -> None:
    """Post a formatted Slack message with answer text and source links."""
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
    payload: dict = {"channel": channel, "blocks": blocks}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10.0,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Slack post_message failed")
```

- [ ] **Step 4: Run to verify tests pass**

```bash
cd substrateos-api && python -m pytest tests/test_bots.py -k "slack_signature or strip_bot" -v
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/bots/slack.py substrateos-api/tests/test_bots.py
git commit -m "feat(bots): add Slack HMAC verification, mention stripping, chat.postMessage helper"
```

---

## Task 4: Teams app manifest generator

**Files:**
- Create: `substrateos-api/app/bots/manifest.py`
- Test: `substrateos-api/tests/test_bots.py` (append)

- [ ] **Step 1: Append failing manifest tests to `tests/test_bots.py`**

```python
# ── build_manifest_zip ───────────────────────────────────────────────────────

def test_manifest_zip_structure():
    from app.bots.manifest import build_manifest_zip
    raw = build_manifest_zip("aaaa-bbbb-cccc", "api.example.com")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
    assert "manifest.json" in names
    assert "color.png" in names
    assert "outline.png" in names


def test_manifest_zip_app_id():
    import json
    from app.bots.manifest import build_manifest_zip
    raw = build_manifest_zip("my-app-id-123", "api.example.com")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["id"] == "my-app-id-123"
    assert manifest["bots"][0]["botId"] == "my-app-id-123"
    assert "api.example.com" in manifest["validDomains"]


def test_manifest_zip_icons_are_valid_png():
    from app.bots.manifest import build_manifest_zip
    _PNG_SIG = b"\x89PNG\r\n\x1a\n"
    raw = build_manifest_zip("app-id", "host.com")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert zf.read("color.png")[:8] == _PNG_SIG
        assert zf.read("outline.png")[:8] == _PNG_SIG
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd substrateos-api && python -m pytest tests/test_bots.py -k "manifest" -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'app.bots.manifest'`.

- [ ] **Step 3: Create `substrateos-api/app/bots/manifest.py`**

```python
from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib


def _make_png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Generate a solid-colour PNG without Pillow."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = bytes([0]) + bytes([r, g, b] * width)
    idat = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# SubStrateOS amber (#C35A13) — 192×192 colour icon and 32×32 outline icon
_COLOR_PNG = _make_png(192, 192, 195, 90, 19)
_OUTLINE_PNG = _make_png(32, 32, 195, 90, 19)


def build_manifest_zip(app_id: str, api_host: str) -> bytes:
    """Return bytes of a Teams app package (manifest.json + icons)."""
    manifest = {
        "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
        "manifestVersion": "1.17",
        "version": "1.0.0",
        "id": app_id,
        "packageName": "ai.substrateos.bot",
        "developer": {
            "name": "SubStrateOS",
            "websiteUrl": f"https://{api_host}",
            "privacyUrl": f"https://{api_host}",
            "termsOfUseUrl": f"https://{api_host}",
        },
        "name": {"short": "SubStrateOS", "full": "SubStrateOS Intelligence Layer"},
        "description": {
            "short": "Ask your company knowledge base",
            "full": (
                "SubStrateOS is your company intelligence layer. @-mention it in any channel "
                "or chat to get grounded answers drawn from SharePoint, Teams, and connected "
                "sources — scoped to what you can see."
            ),
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#C35A13",
        "bots": [{
            "botId": app_id,
            "scopes": ["personal", "team", "groupchat"],
            "isNotificationOnly": False,
        }],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [api_host],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("color.png", _COLOR_PNG)
        zf.writestr("outline.png", _OUTLINE_PNG)
    return buf.getvalue()
```

- [ ] **Step 4: Run to verify tests pass**

```bash
cd substrateos-api && python -m pytest tests/test_bots.py -k "manifest" -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/bots/manifest.py substrateos-api/tests/test_bots.py
git commit -m "feat(bots): add Teams app manifest generator (PNG icons embedded, no Pillow)"
```

---

## Task 5: Bot API router + integration tests

**Files:**
- Create: `substrateos-api/app/api/bots.py`
- Create: `substrateos-api/tests/test_bots_api.py`

- [ ] **Step 1: Write failing integration tests**

Create `substrateos-api/tests/test_bots_api.py`:

```python
from __future__ import annotations

import hashlib
import hmac
import json
import time
import zipfile
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.deps import get_orchestrator
from app.domain.query import Answer
from app.main import app

_ADMIN = {"x-admin-key": "dev-admin-key-local"}
_TEAMS_APP_ID = "teams-test-app-id"
_TEAMS_PASSWORD = "teams-test-password"
_SLACK_TOKEN = "xoxb-test-token"
_SLACK_SECRET = "slack-test-secret"


class _FakeOrchestrator:
    async def answer(self, request, *, user, user_token=None):
        return Answer(text="Here is the answer.", citations=[], query_id="q1")


def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


# ── GET /admin/bot/status ─────────────────────────────────────────────────────

def test_bot_status_unconfigured():
    with TestClient(app) as client:
        resp = client.get("/admin/bot/status", headers=_ADMIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["teams"]["configured"] is False
    assert body["teams"]["app_id"] is None
    assert body["slack"]["configured"] is False


def test_bot_status_configured(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            resp = client.get("/admin/bot/status", headers=_ADMIN)
        assert resp.status_code == 200
        body = resp.json()
        assert body["teams"]["configured"] is True
        assert body["teams"]["app_id"] == _TEAMS_APP_ID
        assert body["slack"]["configured"] is True
    finally:
        get_settings.cache_clear()


def test_bot_status_requires_admin_key():
    with TestClient(app) as client:
        assert client.get("/admin/bot/status").status_code == 403


# ── GET /admin/bot/teams/manifest ─────────────────────────────────────────────

def test_teams_manifest_unconfigured():
    with TestClient(app) as client:
        resp = client.get("/admin/bot/teams/manifest", headers=_ADMIN)
    assert resp.status_code == 404


def test_teams_manifest_download(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            resp = client.get("/admin/bot/teams/manifest", headers=_ADMIN)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "substrateos-teams.zip" in resp.headers["content-disposition"]
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            assert "manifest.json" in zf.namelist()
    finally:
        get_settings.cache_clear()


# ── POST /bot/slack (url_verification) ────────────────────────────────────────

def test_slack_url_verification():
    payload = {"type": "url_verification", "challenge": "abc123"}
    with TestClient(app) as client:
        resp = client.post("/bot/slack", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}


# ── POST /bot/slack (invalid HMAC) ────────────────────────────────────────────

def test_slack_invalid_hmac(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        body = json.dumps({"type": "event_callback", "event": {"type": "app_mention"}}).encode()
        ts = str(int(time.time()))
        with TestClient(app) as client:
            resp = client.post(
                "/bot/slack", content=body,
                headers={
                    "content-type": "application/json",
                    "x-slack-signature": "v0=badsig",
                    "x-slack-request-timestamp": ts,
                },
            )
        assert resp.status_code == 403
    finally:
        get_settings.cache_clear()


# ── POST /bot/teams ───────────────────────────────────────────────────────────

def test_teams_webhook_valid(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    from app.config import get_settings
    get_settings.cache_clear()
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with TestClient(app) as client:
                resp = client.post(
                    "/bot/teams",
                    json={
                        "type": "message",
                        "text": "<at>SubStrateOS</at> what is PTO?",
                        "from": {"id": "u1", "aadObjectId": "aad-u1"},
                        "conversation": {"id": "conv1"},
                        "id": "act1",
                        "serviceUrl": "https://smba.trafficmanager.net",
                        "channelData": {"tenant": {"id": "tenant1"}},
                    },
                    headers={"Authorization": "Bearer fake-jwt"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "message"
        assert body["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_teams_webhook_invalid_jwt(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=False)):
            with TestClient(app) as client:
                resp = client.post(
                    "/bot/teams",
                    json={"type": "message", "text": "hello"},
                    headers={"Authorization": "Bearer bad-token"},
                )
        assert resp.status_code == 401
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd substrateos-api && python -m pytest tests/test_bots_api.py -v 2>&1 | head -30
```

Expected: `ERROR` — `ImportError` from `app.api.bots` not existing.

- [ ] **Step 3: Create `substrateos-api/app/api/bots.py`**

```python
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
from app.deps import get_orchestrator
from app.domain.identity import User
from app.domain.query import Answer, QueryRequest

router = APIRouter(tags=["bots"])
logger = logging.getLogger(__name__)

_ERROR_TEXT = "Sorry, I couldn't find an answer right now. Try rephrasing your question."


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

    async def _reply() -> None:
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
```

- [ ] **Step 4: Run to verify tests pass**

```bash
cd substrateos-api && python -m pytest tests/test_bots_api.py -v
```

Expected: all tests pass (note: `test_slack_url_verification` will fail if router isn't wired — proceed to Task 6 first if needed, then re-run).

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/api/bots.py substrateos-api/tests/test_bots_api.py
git commit -m "feat(bots): add bot API router (Teams webhook, Slack webhook, status, manifest)"
```

---

## Task 6: Wire router + extend SurfacePatch

**Files:**
- Modify: `substrateos-api/app/main.py`
- Modify: `substrateos-api/app/api/admin.py`

- [ ] **Step 1: Register the bots router in `app/main.py`**

After the `sources_router` import add:
```python
from app.api.bots import router as bots_router
```

After `app.include_router(sources_router)` add:
```python
app.include_router(bots_router)
```

- [ ] **Step 2: Run the integration tests to confirm all pass**

```bash
cd substrateos-api && python -m pytest tests/test_bots_api.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 3: Extend `SurfacePatch` and `patch_surface` in `app/api/admin.py`**

Find the `SurfacePatch` class (around line 246) and replace it:

```python
class SurfacePatch(BaseModel):
    enabled: bool
    installed: bool | None = None
    workspace_name: str | None = None
```

In `patch_surface`, replace the body after `surface = SurfaceConfig(name=name)`:

```python
    surface.enabled = body.enabled
    if body.installed is not None:
        surface.installed = body.installed
    if body.workspace_name is not None:
        surface.workspace_name = body.workspace_name
    await store.put_surface(tenant, surface)
    return surface.model_dump()
```

- [ ] **Step 4: Run full test suite to confirm nothing broke**

```bash
cd substrateos-api && python -m pytest tests/ -v --ignore=tests/test_bot_config.py -x -q 2>&1 | tail -20
```

Expected: existing tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/main.py substrateos-api/app/api/admin.py
git commit -m "feat(bots): wire bots router; extend SurfacePatch with installed + workspace_name"
```

---

## Task 7: Frontend — adminApi.ts additions

**Files:**
- Modify: `web/lib/adminApi.ts`

- [ ] **Step 1: Add `BotStatus` type, `getBotStatus`, `downloadTeamsManifest`, and extend `patchSurface`**

Replace the current `patchSurface` and append to the bottom of `web/lib/adminApi.ts`:

```typescript
// Replace existing patchSurface:
export const patchSurface = (
  name: string,
  enabled: boolean,
  extra?: { installed?: boolean; workspace_name?: string },
) =>
  call<SurfaceConfig>(`/admin/surfaces/${name}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled, ...extra }),
  });

export type BotStatus = {
  teams: { configured: boolean; app_id: string | null };
  slack: { configured: boolean };
};

export const getBotStatus = () => call<BotStatus>("/admin/bot/status");

export async function downloadTeamsManifest(): Promise<void> {
  const resp = await fetch(`${API_BASE}/admin/bot/teams/manifest`, {
    headers: await headers(),
  });
  if (!resp.ok) throw new Error(`manifest ${resp.status}`);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "substrateos-teams.zip";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
```

Note: `headers` is the existing async helper inside `adminApi.ts`. `downloadTeamsManifest` is a standalone `export async function`, not using `call<T>` since it needs blob response.

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd web && npx tsc --noEmit 2>&1 | grep -v node_modules | head -20
```

Expected: no errors related to `adminApi.ts`.

- [ ] **Step 3: Commit**

```bash
git add web/lib/adminApi.ts
git commit -m "feat(bots): add getBotStatus, downloadTeamsManifest; extend patchSurface with installed/workspace_name"
```

---

## Task 8: Frontend — surfaces page install flow

**Files:**
- Modify: `web/app/admin/surfaces/page.tsx`

This task rewires the Surfaces page to: fetch bot status on mount, auto-heal installed state, show install modals for Teams/Slack, and render three-state (Teams) and two-state (Slack) card footers.

- [ ] **Step 1: Update imports at the top of `page.tsx`**

Replace the current import line:
```typescript
import { getSurfaces, patchSurface, SurfaceConfig } from "@/lib/adminApi";
```

With:
```typescript
import {
  getBotStatus, getSurfaces, patchSurface, downloadTeamsManifest,
  BotStatus, SurfaceConfig,
} from "@/lib/adminApi";
```

- [ ] **Step 2: Add `installModal` state and `botStatus` state to the `Surfaces` component**

In the `Surfaces` function, replace the existing state declarations:
```typescript
  const [configs, setConfigs] = useState<SurfaceConfig[]>([]);
  const [installing, setInstalling] = useState<string | null>(null);
  const [err, setErr] = useState(false);
```

With:
```typescript
  const [configs, setConfigs] = useState<SurfaceConfig[]>([]);
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [installModal, setInstallModal] = useState<"teams" | "slack" | null>(null);
  const [err, setErr] = useState(false);
```

- [ ] **Step 3: Replace the `useEffect` to fetch both surfaces and bot status**

Replace the existing `useEffect`:
```typescript
  useEffect(() => {
    getSurfaces().then(setConfigs).catch(() => setErr(true));
  }, []);
```

With:
```typescript
  useEffect(() => {
    Promise.all([getSurfaces(), getBotStatus()])
      .then(([surfaces, status]) => {
        setConfigs(surfaces);
        setBotStatus(status);
        // Auto-heal: if bot is configured but not yet marked installed, sync DB.
        const heal = (name: string, wsName: string, configured: boolean) => {
          const cfg = surfaces.find((s) => s.name === name);
          if (configured && cfg && !cfg.installed) {
            patchSurface(name, cfg.enabled, { installed: true, workspace_name: wsName })
              .then((updated) =>
                setConfigs((prev) => prev.map((c) => (c.name === name ? updated : c)))
              )
              .catch(() => {});
          }
        };
        heal("teams", "Microsoft Teams", status.teams.configured);
        heal("slack", "Slack", status.slack.configured);
      })
      .catch(() => setErr(true));
  }, []);
```

- [ ] **Step 4: Replace `handleToggle` and `handleInstall`**

Replace both handlers:
```typescript
  const handleToggle = async (name: string, enabled: boolean) => {
    setConfigs((prev) => prev.map((c) => (c.name === name ? { ...c, enabled } : c)));
    try {
      const updated = await patchSurface(name, enabled);
      setConfigs((prev) => prev.map((c) => (c.name === name ? updated : c)));
    } catch {
      setConfigs((prev) => prev.map((c) => (c.name === name ? { ...c, enabled: !enabled } : c)));
    }
  };

  const handleInstall = (name: string) => {
    if (name === "teams" || name === "slack") setInstallModal(name);
  };
```

- [ ] **Step 5: Update `CardProps` and `SurfaceCard` to handle the new three-state footer**

Replace the `CardProps` type and `SurfaceCard` component:

```typescript
type CardProps = {
  meta: SurfaceMeta;
  config: SurfaceConfig;
  onToggle: (enabled: boolean) => void;
  onInstall: () => void;
  botConfigured: boolean;
};

function SurfaceCard({ meta, config, onToggle, onInstall, botConfigured }: CardProps) {
  const { enabled, installed, workspace_name } = config;

  const footer = meta.installable ? (
    installed ? (
      <div className="surf-installed">
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)", display: "inline-block", flexShrink: 0 }} />
        Installed in {workspace_name ?? "your workspace"}
      </div>
    ) : botConfigured && meta.name === "teams" ? (
      <button
        className="surf-install-btn btn-teams"
        onClick={onInstall}
        disabled={!enabled}
      >
        Download manifest.zip
      </button>
    ) : (
      <button
        className={`surf-install-btn btn-${meta.name}`}
        onClick={onInstall}
        disabled={!enabled}
      >
        Install to {meta.label}
      </button>
    )
  ) : (
    meta.endpoint ? <span className="surf-url">{meta.endpoint}</span> : <span />
  );

  return (
    <div className={`surf-card${enabled ? "" : " surf-off"}`}>
      <div className="surf-top">
        <div className="surf-head">
          <div className={`surf-logo ${meta.logoClass}`}>{ICONS[meta.name]}</div>
          <div>
            <div className="surf-name">{meta.label}</div>
            <span className="surf-chip">{meta.tag}</span>
          </div>
        </div>
        <button
          className={`sw${enabled ? " on" : ""}`}
          aria-label={enabled ? `Disable ${meta.label}` : `Enable ${meta.label}`}
          onClick={() => onToggle(!enabled)}
        />
      </div>
      <div className="surf-desc">{meta.desc}</div>
      <div className={`surf-blocked${enabled ? "" : " show"}`}>
        <BlockedIcon />
        {meta.blockedMsg}
      </div>
      <div className="surf-foot">
        {footer}
        <span className="surf-scope">{meta.scope}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Add the Teams install modal component (before the `Surfaces` function)**

```typescript
const API_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function TeamsInstallModal({ onClose }: { onClose: () => void }) {
  const [downloading, setDownloading] = useState(false);
  const [dlErr, setDlErr] = useState(false);

  const handleDownload = async () => {
    setDownloading(true); setDlErr(false);
    try { await downloadTeamsManifest(); }
    catch { setDlErr(true); }
    finally { setDownloading(false); }
  };

  return (
    <div className="admin-modal" onClick={onClose}>
      <div className="admin-modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>Install SubStrateOS in Microsoft Teams</h3>
        <ol style={{ paddingLeft: 18, margin: "0 0 16px", lineHeight: 1.7, fontSize: 13 }}>
          <li>In <b>Azure Portal</b>, create an <b>Azure Bot</b> resource. Set the messaging endpoint to:<br />
            <code style={{ fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>
              {API_URL}/bot/teams
            </code>
          </li>
          <li>Copy the <b>App ID</b> and <b>App Password</b>, then set in your server environment:<br />
            <code style={{ fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>
              TEAMS_BOT_APP_ID=… TEAMS_BOT_APP_PASSWORD=…
            </code>
            &nbsp;and restart the API.
          </li>
          <li>Download the manifest package and upload it in <b>Teams Admin Center → Apps → Manage apps → Upload an app</b>.</li>
          <li>Done — this card will show Active on next load.</li>
        </ol>
        {dlErr && <p style={{ color: "var(--rose)", fontSize: 12, margin: "0 0 8px" }}>Download failed — check that TEAMS_BOT_APP_ID is set and the API is running.</p>}
        <div className="modal-foot">
          <button className="modal-close" onClick={onClose}>Close</button>
          <button className="surf-install-btn btn-teams" onClick={handleDownload} disabled={downloading}>
            {downloading ? "Preparing…" : "Download manifest.zip"}
          </button>
        </div>
      </div>
    </div>
  );
}

function SlackInstallModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="admin-modal" onClick={onClose}>
      <div className="admin-modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>Install SubStrateOS in Slack</h3>
        <ol style={{ paddingLeft: 18, margin: "0 0 16px", lineHeight: 1.7, fontSize: 13 }}>
          <li>Go to <b>api.slack.com/apps</b> → <b>Create new app</b> → From scratch → name it <b>SubStrateOS</b>.</li>
          <li>Under <b>OAuth &amp; Permissions</b>, add bot scopes: <code>app_mentions:read</code>, <code>chat:write</code>, <code>im:read</code>, <code>im:write</code>.</li>
          <li>Under <b>Event Subscriptions</b> → enable → set Request URL to:<br />
            <code style={{ fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>
              {API_URL}/bot/slack
            </code>
            <br />Subscribe to <code>app_mention</code> and <code>message.im</code>.
          </li>
          <li><b>Install to workspace</b>, copy the <b>Bot User OAuth Token</b> and <b>Signing Secret</b>.</li>
          <li>Set in your server environment:<br />
            <code style={{ fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>
              SLACK_BOT_TOKEN=xoxb-… SLACK_SIGNING_SECRET=…
            </code>
            &nbsp;and restart the API — the card will show Active.
          </li>
        </ol>
        <div className="modal-foot">
          <button className="modal-close" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Update the `Surfaces` render to pass `botConfigured` and render modals**

Replace the `return` block of `Surfaces`:

```typescript
  return (
    <div className="admin-page">
    <div className="admin-wrap">
      <header className="admin-head">
        <h1>Surfaces</h1>
        <p>Where SubStrateOS shows up — enable surfaces and install integrations for your team.</p>
      </header>
      {err && <div className="admin-note">Couldn&apos;t load surface config. Check the admin key / API.</div>}
      <div className="surf-grid">
        {SURFACES.map((meta) => {
          const bc =
            meta.name === "teams" ? (botStatus?.teams.configured ?? false) :
            meta.name === "slack" ? (botStatus?.slack.configured ?? false) : false;
          return (
            <SurfaceCard
              key={meta.name}
              meta={meta}
              config={configOf(meta.name)}
              onToggle={(enabled) => handleToggle(meta.name, enabled)}
              onInstall={() => handleInstall(meta.name)}
              botConfigured={bc}
            />
          );
        })}
      </div>
      {installModal === "teams" && <TeamsInstallModal onClose={() => setInstallModal(null)} />}
      {installModal === "slack" && <SlackInstallModal onClose={() => setInstallModal(null)} />}
    </div>
    </div>
  );
```

- [ ] **Step 8: Verify TypeScript compiles**

```bash
cd web && npx tsc --noEmit 2>&1 | grep -v node_modules | head -20
```

Expected: no errors.

- [ ] **Step 9: Run the dev server and manually verify the Surfaces page**

```bash
cd web && pnpm dev
```

Open `http://localhost:3000/admin/surfaces`. Verify:
- Teams card shows "Install to Teams" button (since no env vars set locally)
- Clicking "Install to Teams" opens the Teams modal with 4 steps and a "Download manifest.zip" button
- Clicking "Install to Slack" opens the Slack modal with 5 steps
- Closing either modal (backdrop click or Close button) works
- Toggle switches still work (enable/disable)

- [ ] **Step 10: Commit**

```bash
git add web/app/admin/surfaces/page.tsx
git commit -m "feat(bots): surfaces page — bot status fetch, Teams/Slack install modals, three-state cards"
```

---

## Self-Review Checklist

After all tasks are complete, verify:

- [ ] `python -m pytest tests/test_bots.py tests/test_bot_config.py tests/test_bots_api.py -v` — all pass
- [ ] `python -m pytest tests/ -q -m "not integration"` — no regressions
- [ ] TypeScript: `cd web && npx tsc --noEmit` — no errors
- [ ] All 4 endpoints exist: `GET /admin/bot/status`, `GET /admin/bot/teams/manifest`, `POST /bot/teams`, `POST /bot/slack`
- [ ] Teams card: 3 states work (no env vars → install button; env vars only → download manifest; env vars + installed → green dot)
- [ ] Slack card: 2 states work (no env vars → install button; env vars → green dot via auto-heal)
- [ ] Modals close on backdrop click and Close button
