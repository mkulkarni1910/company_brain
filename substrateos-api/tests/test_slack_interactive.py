from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from app.deps import get_refund_flow
from app.main import app

_SECRET = "slack-test-secret"


def _sig(ts: str, body: bytes) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(_SECRET.encode(), base, hashlib.sha256).hexdigest()


class _FakeFlow:
    def __init__(self):
        self.payloads = []

    async def handle_action(self, payload):
        self.payloads.append(payload)


def _payload(action_id: str = "refund_approve", run_id: str = "RB-4471") -> bytes:
    data = {
        "type": "block_actions",
        "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D1", "message_ts": "1.2"},
        "actions": [{"action_id": action_id, "value": run_id}],
    }
    return urlencode({"payload": json.dumps(data)}).encode()


def _post(client, body: bytes, *, sign: bool = True):
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if sign:
        headers["X-Slack-Signature"] = _sig(ts, body)
        headers["X-Slack-Request-Timestamp"] = ts
    return client.post("/bot/slack/interactive", content=body, headers=headers)


def _env(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SECRET)
    from app.config import get_settings
    get_settings.cache_clear()


def test_interactive_dispatches_action(monkeypatch):
    _env(monkeypatch)
    flow = _FakeFlow()
    app.dependency_overrides[get_refund_flow] = lambda: flow
    try:
        with TestClient(app) as client:
            resp = _post(client, _payload())
        assert resp.status_code == 200
        assert len(flow.payloads) == 1
        assert flow.payloads[0]["actions"][0]["value"] == "RB-4471"
    finally:
        app.dependency_overrides.clear()


def test_interactive_rejects_bad_signature(monkeypatch):
    _env(monkeypatch)
    flow = _FakeFlow()
    app.dependency_overrides[get_refund_flow] = lambda: flow
    try:
        with TestClient(app) as client:
            resp = _post(client, _payload(), sign=False)
        assert resp.status_code == 403
        assert flow.payloads == []
    finally:
        app.dependency_overrides.clear()


def test_interactive_ignores_non_block_actions(monkeypatch):
    _env(monkeypatch)
    flow = _FakeFlow()
    app.dependency_overrides[get_refund_flow] = lambda: flow
    body = urlencode({"payload": json.dumps({"type": "view_submission"})}).encode()
    try:
        with TestClient(app) as client:
            resp = _post(client, body)
        assert resp.status_code == 200
        assert flow.payloads == []
    finally:
        app.dependency_overrides.clear()


def test_interactive_unconfigured_returns_503(monkeypatch):
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    with TestClient(app) as client:
        resp = _post(client, _payload())
    assert resp.status_code == 503
