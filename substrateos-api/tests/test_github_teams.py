"""Teams plumbing for the raise-PR playbook."""
from __future__ import annotations

import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock, patch

from app.bots.github_cards import teams_preview_activity
from app.bots.teams import get_teams_member_email
from app.domain.workflow import PrDraft

DRAFT = PrDraft(path="docs/p.md", base_sha="s", new_content="x",
                summary="sum", title="Title", body="b")


def test_teams_preview_card_has_submit_actions():
    act = teams_preview_activity(draft=DRAFT, repo_label="acme/policies", run_id="RB-7")
    card = act["attachments"][0]["content"]
    data = [a["data"] for a in card["actions"]]
    assert {"action": "github_create", "run_id": "RB-7"} in data
    assert {"action": "github_cancel", "run_id": "RB-7"} in data


@pytest.mark.asyncio
@respx.mock
async def test_member_email_lookup(monkeypatch):
    import app.bots.teams as teams_mod
    async def fake_token(app_id, app_password, tenant_id=None):
        return "tok"
    monkeypatch.setattr(teams_mod, "_connector_token", fake_token)
    respx.get("https://smba.example/v3/conversations/c%3A1/members/29%3Auser").mock(
        return_value=Response(200, json={"email": "tom@x", "userPrincipalName": "tom@x"}))
    incoming = {"serviceUrl": "https://smba.example",
                "conversation": {"id": "c:1"}, "from": {"id": "29:user"}}
    email = await get_teams_member_email(incoming=incoming, app_id="a", app_password="p")
    assert email == "tom@x"


@pytest.mark.asyncio
async def test_member_email_lookup_degrades_to_none():
    incoming = {"serviceUrl": "", "conversation": {}, "from": {}}
    email = await get_teams_member_email(incoming=incoming, app_id="a", app_password="p")
    assert email is None


# ── webhook integration tests ─────────────────────────────────────────────────

from fastapi.testclient import TestClient  # noqa: E402

from app.connectors.models import SurfaceConfig  # noqa: E402
from app.deps import (  # noqa: E402
    get_connection_store,
    get_github_flow,
    get_skill_router_svc,
)
from app.domain.skill import ResolvedSkill  # noqa: E402
from app.domain.workflow import RefundRun  # noqa: E402
from app.main import app  # noqa: E402

_TEAMS_APP_ID = "teams-test-app-id"
_TEAMS_PASSWORD = "teams-test-password"


class _FakeStore:
    async def list_surfaces(self, tenant):
        return [
            SurfaceConfig(name=n, enabled=True)
            for n in ("slack", "teams", "web", "api", "mcp", "github")
        ]


class _FakeSkillRouter:
    def __init__(self, resolved):
        self._resolved = resolved

    async def resolve_skill(self, query):
        return self._resolved


def _make_run(run_id: str = "RB-7") -> RefundRun:
    from datetime import datetime
    return RefundRun(
        id=run_id,
        kind="github_pr",
        status="pending_confirm",
        requester_name="Tom",
        requester_email="tom@acme.com",
        channel="",
        thread_ts="",
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

    async def start(self, text, *, requester_name, requester_email, surface,
                    channel=None, thread_ts=None):
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


def _teams_env(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    from app.config import get_settings
    get_settings.cache_clear()
    return get_settings


def test_teams_action_submit_routes_to_confirm(monkeypatch):
    """Action.Submit with action='github_create' calls flow.confirm with the
    resolved email and replies with the pr_url."""
    get_settings = _teams_env(monkeypatch)
    flow = _FakeGithubFlow()

    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    app.dependency_overrides[get_github_flow] = lambda: flow
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with patch("app.api.bots.send_teams_activity",
                       new=AsyncMock(return_value=True)) as mock_send:
                # Patch email lookup to return a known address
                with patch("app.api.bots.get_teams_member_email",
                           new=AsyncMock(return_value="tom@acme.com")):
                    with TestClient(app) as client:
                        resp = client.post(
                            "/bot/teams",
                            json={
                                "type": "message",
                                "text": "",  # empty — button submit
                                "value": {"action": "github_create", "run_id": "RB-7"},
                                "from": {"id": "29:user", "name": "Tom"},
                                "conversation": {"id": "c:1"},
                                "serviceUrl": "https://smba.example",
                            },
                            headers={"Authorization": "Bearer fake-jwt"},
                        )

        assert resp.status_code == 200

        # confirm must have been called with run_id and actor_email
        assert len(flow.confirm_calls) == 1
        assert flow.confirm_calls[0]["run_id"] == "RB-7"
        assert flow.confirm_calls[0]["actor_email"] == "tom@acme.com"

        # A reply activity must have been sent containing the pr_url
        mock_send.assert_awaited_once()
        activity = mock_send.await_args.kwargs["activity"]
        assert "pull/42" in activity["text"]
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_teams_action_submit_blocked_when_surface_disabled(monkeypatch):
    """Action.Submit with teams surface disabled → flow.confirm NOT called,
    sent activity text mentions 'disabled'."""
    get_settings = _teams_env(monkeypatch)
    flow = _FakeGithubFlow()

    class _DisabledTeamsStore:
        async def list_surfaces(self, tenant):
            return [
                SurfaceConfig(name="teams", enabled=False),
                SurfaceConfig(name="slack", enabled=True),
            ]

    app.dependency_overrides[get_connection_store] = lambda: _DisabledTeamsStore()
    app.dependency_overrides[get_github_flow] = lambda: flow
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with patch("app.api.bots.send_teams_activity",
                       new=AsyncMock(return_value=True)) as mock_send:
                with patch("app.api.bots.get_teams_member_email",
                           new=AsyncMock(return_value="tom@acme.com")):
                    with TestClient(app) as client:
                        resp = client.post(
                            "/bot/teams",
                            json={
                                "type": "message",
                                "text": "",
                                "value": {"action": "github_create", "run_id": "RB-7"},
                                "from": {"id": "29:user", "name": "Tom"},
                                "conversation": {"id": "c:1"},
                                "serviceUrl": "https://smba.example",
                            },
                            headers={"Authorization": "Bearer fake-jwt"},
                        )

        assert resp.status_code == 200
        # confirm must NOT have been called
        assert len(flow.confirm_calls) == 0
        # A disabled-surface reply must have been sent
        mock_send.assert_awaited_once()
        activity = mock_send.await_args.kwargs["activity"]
        assert "disabled" in activity["text"].lower()
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_teams_github_skill_routes_to_start(monkeypatch):
    """Text message whose skill resolves to workflow='github' calls flow.start
    with surface='teams' and the member email, then sends an adaptive card
    containing 'github_create'."""
    get_settings = _teams_env(monkeypatch)
    resolved = ResolvedSkill(
        id="1", slug="github", name="GitHub",
        system_prompt="p",
        clean_query="update refund window to 30 days",
        workflow="github",
    )
    flow = _FakeGithubFlow()

    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    app.dependency_overrides[get_skill_router_svc] = lambda: _FakeSkillRouter(resolved)
    app.dependency_overrides[get_github_flow] = lambda: flow
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with patch("app.api.bots.send_teams_activity",
                       new=AsyncMock(return_value=True)) as mock_send:
                with patch("app.api.bots.get_teams_member_email",
                           new=AsyncMock(return_value="tom@acme.com")):
                    with TestClient(app) as client:
                        resp = client.post(
                            "/bot/teams",
                            json={
                                "type": "message",
                                "text": "update refund window to 30 days",
                                "from": {"id": "29:user", "name": "Tom"},
                                "conversation": {"id": "c:1"},
                                "serviceUrl": "https://smba.example",
                            },
                            headers={"Authorization": "Bearer fake-jwt"},
                        )

        assert resp.status_code == 200

        # GithubFlow.start must have been called with surface="teams" and the email
        assert len(flow.start_calls) == 1
        assert flow.start_calls[0]["surface"] == "teams"
        assert flow.start_calls[0]["requester_email"] == "tom@acme.com"

        # The sent activity must contain an adaptive card with "github_create"
        mock_send.assert_awaited_once()
        activity = mock_send.await_args.kwargs["activity"]
        assert "github_create" in str(activity)
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
