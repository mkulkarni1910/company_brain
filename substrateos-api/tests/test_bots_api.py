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

from app.connectors.models import SurfaceConfig
from app.deps import get_connection_store, get_orchestrator
from app.domain.query import Answer
from app.main import app

_ADMIN = {"x-admin-key": "dev-admin-key-local"}
_TEAMS_APP_ID = "teams-test-app-id"
_TEAMS_PASSWORD = "teams-test-password"
_SLACK_TOKEN = "xoxb-test-token"
_SLACK_SECRET = "slack-test-secret"


class _FakeOrchestrator:
    async def answer(self, request, *, user, user_token=None, skill_context=None):
        return Answer(text="Here is the answer.", citations=[], query_id="q1")


class _FakeStore:
    """Connection store stub exposing only list_surfaces."""

    def __init__(self, disabled: set[str] | None = None):
        self._disabled = disabled or set()

    async def list_surfaces(self, tenant):
        return [
            SurfaceConfig(name=n, enabled=n not in self._disabled)
            for n in ("slack", "teams", "web", "api", "mcp")
        ]


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

class _ExplodingOrchestrator:
    """Greetings must never reach the RAG pipeline."""

    async def answer(self, request, *, user, user_token=None):
        raise AssertionError("orchestrator must not be called for small talk")


def _teams_env(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    from app.config import get_settings
    get_settings.cache_clear()
    return get_settings


def test_teams_webhook_greeting_short_circuits(monkeypatch):
    get_settings = _teams_env(monkeypatch)
    app.dependency_overrides[get_orchestrator] = lambda: _ExplodingOrchestrator()
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with patch("app.api.bots.send_teams_activity", new=AsyncMock(return_value=True)) as mock_send:
                with TestClient(app) as client:
                    resp = client.post(
                        "/bot/teams",
                        json={
                            "type": "message",
                            "text": "<at>SubStrateOS</at> Hello",
                            "from": {"id": "u1"}, "conversation": {"id": "c1"}, "id": "a1",
                        },
                        headers={"Authorization": "Bearer fake-jwt"},
                    )
        assert resp.status_code == 200
        mock_send.assert_awaited_once()
        activity = mock_send.await_args.kwargs["activity"]
        assert "Try asking" in activity["text"]
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_teams_webhook_welcome_on_bot_added(monkeypatch):
    get_settings = _teams_env(monkeypatch)
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with patch("app.api.bots.send_teams_activity", new=AsyncMock(return_value=True)) as mock_send:
                with TestClient(app) as client:
                    resp = client.post(
                        "/bot/teams",
                        json={
                            "type": "conversationUpdate",
                            "membersAdded": [{"id": "28:bot-id"}, {"id": "29:user"}],
                            "recipient": {"id": "28:bot-id"},
                            "conversation": {"id": "c1"},
                            "serviceUrl": "https://smba.trafficmanager.net",
                        },
                        headers={"Authorization": "Bearer fake-jwt"},
                    )
        assert resp.status_code == 200
        mock_send.assert_awaited_once()
        activity = mock_send.await_args.kwargs["activity"]
        assert "Try asking" in activity["text"]
    finally:
        get_settings.cache_clear()


def test_teams_webhook_no_welcome_for_other_members(monkeypatch):
    get_settings = _teams_env(monkeypatch)
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with patch("app.api.bots.send_teams_activity", new=AsyncMock(return_value=True)) as mock_send:
                with TestClient(app) as client:
                    resp = client.post(
                        "/bot/teams",
                        json={
                            "type": "conversationUpdate",
                            "membersAdded": [{"id": "29:someone-else"}],
                            "recipient": {"id": "28:bot-id"},
                            "conversation": {"id": "c1"},
                        },
                        headers={"Authorization": "Bearer fake-jwt"},
                    )
        assert resp.status_code == 200
        mock_send.assert_not_awaited()
    finally:
        get_settings.cache_clear()


def test_slack_webhook_greeting_short_circuits(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    app.dependency_overrides[get_orchestrator] = lambda: _ExplodingOrchestrator()
    try:
        body = json.dumps({
            "type": "event_callback",
            "event": {"type": "app_mention", "text": "<@U1> hello", "user": "u1",
                      "channel": "C1", "ts": "1.0"},
        }).encode()
        ts = str(int(time.time()))
        with patch("app.api.bots.post_slack_reply", new=AsyncMock(return_value=None)) as mock_post:
            with TestClient(app) as client:
                resp = client.post(
                    "/bot/slack", content=body,
                    headers={
                        "x-slack-request-timestamp": ts,
                        "x-slack-signature": _slack_sig(_SLACK_SECRET, ts, body),
                        "content-type": "application/json",
                    },
                )
        assert resp.status_code == 200
        mock_post.assert_awaited_once()
        answer = mock_post.await_args.args[3]
        assert "Try asking" in answer.text
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_teams_webhook_valid(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    from app.config import get_settings
    get_settings.cache_clear()
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            # Teams ignores activities returned in the webhook response body —
            # the reply must be POSTed to the serviceUrl via the Connector API.
            with patch("app.api.bots.send_teams_activity", new=AsyncMock(return_value=True)) as mock_send:
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
        assert resp.json() == {}
        mock_send.assert_awaited_once()
        kwargs = mock_send.await_args.kwargs
        assert kwargs["incoming"]["conversation"]["id"] == "conv1"
        activity = kwargs["activity"]
        assert activity["type"] == "message"
        assert activity["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
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


# ── surface disabled gate ─────────────────────────────────────────────────────

def test_teams_webhook_surface_disabled(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    from app.config import get_settings
    get_settings.cache_clear()
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    app.dependency_overrides[get_connection_store] = lambda: _FakeStore(disabled={"teams"})
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with patch("app.api.bots.send_teams_activity", new=AsyncMock(return_value=True)) as mock_send:
                with TestClient(app) as client:
                    resp = client.post(
                        "/bot/teams",
                        json={
                            "type": "message",
                            "text": "<at>SubStrateOS</at> what is PTO?",
                            "from": {"id": "u1"}, "conversation": {"id": "c1"}, "id": "a1",
                        },
                        headers={"Authorization": "Bearer fake-jwt"},
                    )
        assert resp.status_code == 200
        assert resp.json() == {}
        mock_send.assert_awaited_once()
        activity = mock_send.await_args.kwargs["activity"]
        assert activity["type"] == "message"
        assert "disabled" in activity["text"].lower()
        assert "attachments" not in activity  # no answer card — query never ran
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_slack_webhook_surface_disabled(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    app.dependency_overrides[get_connection_store] = lambda: _FakeStore(disabled={"slack"})
    try:
        body = json.dumps({
            "type": "event_callback",
            "event": {"type": "app_mention", "text": "<@U1> hi", "user": "u1",
                      "channel": "C1", "ts": "1.0"},
        }).encode()
        ts = str(int(time.time()))
        with patch("app.api.bots.post_slack_reply", new=AsyncMock(return_value=None)) as mock_post:
            with TestClient(app) as client:
                resp = client.post(
                    "/bot/slack", content=body,
                    headers={
                        "content-type": "application/json",
                        "x-slack-signature": _slack_sig(_SLACK_SECRET, ts, body),
                        "x-slack-request-timestamp": ts,
                    },
                )
        assert resp.status_code == 200
        mock_post.assert_awaited_once()
        sent_answer = mock_post.await_args.args[3]
        assert "disabled" in sent_answer.text.lower()
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_slack_webhook_surface_enabled_answers(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    try:
        body = json.dumps({
            "type": "event_callback",
            # a real question — greetings now short-circuit to the intro reply
            "event": {"type": "app_mention", "text": "<@U1> what is PTO?", "user": "u1",
                      "channel": "C1", "ts": "1.0"},
        }).encode()
        ts = str(int(time.time()))
        with patch("app.api.bots.post_slack_reply", new=AsyncMock(return_value=None)) as mock_post:
            with TestClient(app) as client:
                resp = client.post(
                    "/bot/slack", content=body,
                    headers={
                        "content-type": "application/json",
                        "x-slack-signature": _slack_sig(_SLACK_SECRET, ts, body),
                        "x-slack-request-timestamp": ts,
                    },
                )
        assert resp.status_code == 200
        mock_post.assert_awaited_once()
        sent_answer = mock_post.await_args.args[3]
        assert sent_answer.text == "Here is the answer."
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


# ── POST /bot/slack (refund workflow divert) ──────────────────────────────────

from app.deps import get_refund_flow, get_skill_router_svc  # noqa: E402
from app.domain.skill import ResolvedSkill  # noqa: E402


class _FakeRouter:
    def __init__(self, resolved):
        self._resolved = resolved

    async def resolve_skill(self, query):
        return self._resolved


class _FakeFlow:
    def __init__(self):
        self.requests = []

    async def handle_request(self, *, text, channel, thread_ts, requester_slack_id, user):
        self.requests.append({"text": text, "channel": channel,
                              "requester_slack_id": requester_slack_id})


def _slack_event_body(text: str, user: str = "U_TOM") -> bytes:
    return json.dumps({
        "type": "event_callback",
        "event": {"type": "app_mention", "text": f"<@UBOT> {text}",
                  "user": user, "channel": "C_REFUNDS", "ts": "100.1"},
    }).encode()


def _post_signed_slack(client, body: bytes, path: str = "/bot/slack"):
    ts = str(int(time.time()))
    sig = _slack_sig(_SLACK_SECRET, ts, body)
    return client.post(path, content=body, headers={
        "X-Slack-Signature": sig, "X-Slack-Request-Timestamp": ts,
        "Content-Type": "application/json",
    })


def test_slack_webhook_diverts_to_refund_workflow(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    resolved = ResolvedSkill(id="1", slug="refund", name="Refund", system_prompt="p",
                             clean_query="refund $1,200 order 48213", workflow="refund")
    flow = _FakeFlow()
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    app.dependency_overrides[get_skill_router_svc] = lambda: _FakeRouter(resolved)
    app.dependency_overrides[get_refund_flow] = lambda: flow
    try:
        with TestClient(app) as client:
            resp = _post_signed_slack(client, _slack_event_body("refund $1,200 order 48213"))
        assert resp.status_code == 200
        assert len(flow.requests) == 1
        assert flow.requests[0]["channel"] == "C_REFUNDS"
        assert flow.requests[0]["requester_slack_id"] == "U_TOM"
        assert "48213" in flow.requests[0]["text"]
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_slack_webhook_non_workflow_skill_uses_orchestrator(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    resolved = ResolvedSkill(id="1", slug="faq", name="FAQ", system_prompt="p",
                             clean_query="what is the vacation policy")
    flow = _FakeFlow()
    orch = _FakeOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    app.dependency_overrides[get_skill_router_svc] = lambda: _FakeRouter(resolved)
    app.dependency_overrides[get_refund_flow] = lambda: flow
    try:
        with patch("app.api.bots.post_slack_reply", new=AsyncMock()) as mock_post:
            with TestClient(app) as client:
                resp = _post_signed_slack(client, _slack_event_body("what is the vacation policy"))
        assert resp.status_code == 200
        assert flow.requests == []
        mock_post.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
