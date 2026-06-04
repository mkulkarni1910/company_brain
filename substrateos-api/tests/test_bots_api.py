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
