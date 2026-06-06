"""Slack rendering + dispatch for the raise-PR playbook."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.bots.github_cards import cancelled_blocks, pr_created_blocks, preview_blocks
from app.domain.workflow import PrDraft

DRAFT = PrDraft(path="docs/refund-policy.md", base_sha="s", new_content="x",
                summary="window 14→30 days", title="Update refund window", body="b")

# ── card builders ─────────────────────────────────────────────────────────────

def test_preview_blocks_carry_run_id_buttons():
    card = preview_blocks(draft=DRAFT, repo_label="acme/policies", run_id="RB-7")
    flat = str(card)
    assert "github_create" in flat and "github_cancel" in flat and "RB-7" in flat
    assert "acme/policies" in flat and "docs/refund-policy.md" in flat


def test_outcome_blocks():
    ok = pr_created_blocks(pr_url="https://github.com/o/r/pull/3",
                           title="Update refund window", actor_name="Tom")
    assert "pull/3" in str(ok)
    no = cancelled_blocks(title="Update refund window", actor_name="Tom")
    assert "Cancelled" in str(no) or "cancelled" in str(no)


# ── webhook dispatch ──────────────────────────────────────────────────────────

_SLACK_TOKEN = "xoxb-test-token"
_SLACK_SECRET = "slack-test-secret"
_ADMIN = {"x-admin-key": "dev-admin-key-local"}


def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _post_signed_slack(client, body: bytes, path: str = "/bot/slack"):
    ts = str(int(time.time()))
    sig = _slack_sig(_SLACK_SECRET, ts, body)
    return client.post(path, content=body, headers={
        "X-Slack-Signature": sig, "X-Slack-Request-Timestamp": ts,
        "Content-Type": "application/json",
    })


def _slack_event_body(text: str, user: str = "U_TOM") -> bytes:
    return json.dumps({
        "type": "event_callback",
        "event": {"type": "app_mention", "text": f"<@UBOT> {text}",
                  "user": user, "channel": "C_GITHUB", "ts": "200.1"},
    }).encode()


# Stubs shared by webhook + interactive tests

from app.connectors.models import SurfaceConfig  # noqa: E402
from app.deps import (  # noqa: E402
    get_connection_store,
    get_github_flow,
    get_github_store,
    get_skill_router_svc,
)
from app.domain.skill import ResolvedSkill  # noqa: E402
from app.domain.workflow import RefundRun  # noqa: E402
from app.main import app  # noqa: E402


class _FakeStore:
    def __init__(self, disabled: set[str] | None = None):
        self._disabled = disabled or set()

    async def list_surfaces(self, tenant):
        return [
            SurfaceConfig(name=n, enabled=n not in self._disabled)
            for n in ("slack", "teams", "web", "api", "mcp", "github")
        ]


class _FakeSkillRouter:
    def __init__(self, resolved):
        self._resolved = resolved

    async def resolve_skill(self, query):
        return self._resolved


class _FakeGithubStore:
    async def get_config(self, tenant):
        from app.connectors.models import GithubConfig
        return GithubConfig(owner="acme", repo="policies", base_branch="main")


def _make_run(run_id: str = "RB-7") -> RefundRun:
    from datetime import datetime
    return RefundRun(
        id=run_id,
        kind="github_pr",
        status="pending_confirm",
        requester_name="Tom",
        requester_email="tom@acme.com",
        channel="C_GITHUB",
        thread_ts="200.1",
        pr_draft=DRAFT,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


class _FakeGithubFlow:
    def __init__(self, start_result=None, action_result=None):
        self._start_result = start_result
        self._action_result = action_result
        self.start_calls = []
        self.confirm_calls = []
        self.cancel_calls = []

    async def start(self, text, *, requester_name, requester_email, surface, channel=None, thread_ts=None):
        from app.workflows.github_pr import StartResult
        self.start_calls.append({
            "text": text, "requester_name": requester_name,
            "requester_email": requester_email, "surface": surface,
        })
        if self._start_result is not None:
            return self._start_result
        run = _make_run()
        return StartResult(status="preview", run=run)

    async def confirm(self, run_id, *, actor_email, actor_name):
        from app.workflows.github_pr import ActionResult
        self.confirm_calls.append({"run_id": run_id, "actor_email": actor_email})
        if self._action_result is not None:
            return self._action_result
        return ActionResult(ok=True, status="completed",
                            pr_url="https://github.com/acme/policies/pull/42")

    async def cancel(self, run_id, *, actor_email, actor_name):
        from app.workflows.github_pr import ActionResult
        self.cancel_calls.append({"run_id": run_id, "actor_email": actor_email})
        if self._action_result is not None:
            return self._action_result
        return ActionResult(ok=True, status="cancelled")


def _slack_env(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    return get_settings


def test_slack_webhook_routes_github_workflow(monkeypatch):
    """Message event whose skill resolves to workflow='github' calls GithubFlow.start
    with surface='slack' and the user's email, then posts a preview card with
    blocks containing 'github_create'."""
    get_settings = _slack_env(monkeypatch)
    resolved = ResolvedSkill(id="1", slug="github", name="GitHub",
                              system_prompt="p", clean_query="update refund window to 30 days",
                              workflow="github")
    flow = _FakeGithubFlow()
    github_store = _FakeGithubStore()

    # users.info stub — returns a fake profile for U_TOM
    fake_users_info_body = {
        "ok": True,
        "user": {
            "name": "tom",
            "real_name": "Tom Tester",
            "profile": {"display_name": "Tom", "email": "tom@acme.com"},
        },
    }

    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    app.dependency_overrides[get_skill_router_svc] = lambda: _FakeSkillRouter(resolved)
    app.dependency_overrides[get_github_flow] = lambda: flow
    app.dependency_overrides[get_github_store] = lambda: github_store
    try:
        slack_calls = []

        async def fake_slack_call(token, method, payload):
            slack_calls.append({"method": method, "payload": payload})
            if method == "users.info":
                return fake_users_info_body
            return {"ok": True}

        with patch("app.api.bots.slack_call", new=fake_slack_call):
            with TestClient(app) as client:
                resp = _post_signed_slack(client, _slack_event_body("update refund window to 30 days"))

        assert resp.status_code == 200

        # GithubFlow.start must have been called once with surface="slack" and the email
        assert len(flow.start_calls) == 1
        assert flow.start_calls[0]["surface"] == "slack"
        assert flow.start_calls[0]["requester_email"] == "tom@acme.com"

        # A chat.postMessage must have been sent with "github_create" in the payload
        post_calls = [c for c in slack_calls if c["method"] == "chat.postMessage"]
        assert len(post_calls) == 1
        payload_str = str(post_calls[0]["payload"])
        assert "github_create" in payload_str
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_slack_interactive_github_action_routes(monkeypatch):
    """block_actions with action_id='github_create' and value='RB-7' calls
    flow.confirm and chat.update the container message with pr_url on ok."""
    get_settings = _slack_env(monkeypatch)
    flow = _FakeGithubFlow()

    fake_users_info_body = {
        "ok": True,
        "user": {
            "name": "tom",
            "real_name": "Tom Tester",
            "profile": {"display_name": "Tom", "email": "tom@acme.com"},
        },
    }

    app.dependency_overrides[get_github_flow] = lambda: flow
    try:
        slack_calls = []

        async def fake_slack_call(token, method, payload):
            slack_calls.append({"method": method, "payload": payload})
            if method == "users.info":
                return fake_users_info_body
            return {"ok": True}

        # Build block_actions payload
        interactive_payload = {
            "type": "block_actions",
            "user": {"id": "U_TOM", "name": "tom"},
            "container": {"channel_id": "C_GITHUB", "message_ts": "200.1"},
            "actions": [
                {"action_id": "github_create", "value": "RB-7"},
            ],
        }
        payload_raw = json.dumps(interactive_payload)
        body = f"payload={payload_raw}".encode()

        ts = str(int(time.time()))
        sig = _slack_sig(_SLACK_SECRET, ts, body)

        with patch("app.api.bots.slack_call", new=fake_slack_call):
            with TestClient(app) as client:
                resp = client.post(
                    "/bot/slack/interactive",
                    content=body,
                    headers={
                        "X-Slack-Signature": sig,
                        "X-Slack-Request-Timestamp": ts,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )

        assert resp.status_code == 200

        # confirm must have been called with run_id="RB-7"
        assert len(flow.confirm_calls) == 1
        assert flow.confirm_calls[0]["run_id"] == "RB-7"
        assert flow.confirm_calls[0]["actor_email"] == "tom@acme.com"

        # chat.update must have been called with the pr_url in the payload
        update_calls = [c for c in slack_calls if c["method"] == "chat.update"]
        assert len(update_calls) == 1
        update_payload_str = str(update_calls[0]["payload"])
        assert "pull/42" in update_payload_str
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
