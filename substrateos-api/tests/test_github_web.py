"""Web surface: /query diverts github-workflow skills to a pending_action answer."""
from app.api.query import github_answer
from app.domain.query import Answer
from app.domain.workflow import PrDraft
from app.workflows.github_pr import StartResult


def _run_stub():
    from datetime import UTC, datetime
    from app.domain.workflow import RefundRun
    now = datetime.now(UTC)
    return RefundRun(id="RB-7", kind="github_pr", status="pending_confirm",
                     requester_name="Demo", created_at=now, updated_at=now,
                     pr_draft=PrDraft(path="docs/p.md", base_sha="s", new_content="x",
                                      summary="sum", title="Title", body="b"))


def test_preview_answer_carries_pending_action():
    a = github_answer(StartResult(status="preview", run=_run_stub()), repo_label="acme/policies")
    assert isinstance(a, Answer)
    pa = a.pending_action
    assert pa["type"] == "github_pr" and pa["run_id"] == "RB-7"
    assert pa["path"] == "docs/p.md" and pa["repo"] == "acme/policies"


def test_connect_answer_carries_url():
    a = github_answer(StartResult(status="connect",
                                  connect_url="http://api/auth/github/start?s=x",
                                  message="Connect first: http://api/auth/github/start?s=x"),
                      repo_label=None)
    assert a.pending_action["type"] == "github_connect"
    assert a.pending_action["connect_url"].endswith("?s=x")


def test_clarify_is_plain_text():
    a = github_answer(StartResult(status="clarify", message="Which doc?"), repo_label=None)
    assert a.pending_action is None and "Which doc?" in a.text


# ── endpoint-level integration test ───────────────────────────────────────────

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.deps import get_skill_router_svc, get_token_store
from app.domain.skill import ResolvedSkill
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-demo,t-test:everyone"}


class _FakeSkillRouter:
    async def resolve_skill(self, query: str) -> ResolvedSkill:
        return ResolvedSkill(
            id="sk-github",
            slug="raise-pr",
            name="Raise PR",
            system_prompt="",
            clean_query=query,
            workflow="github",
        )


def _fake_flow_factory(result):
    flow = MagicMock()
    flow.start = AsyncMock(return_value=result)
    return flow


def test_query_diverts_github_workflow():
    """POST /query with a github-workflow skill should return pending_action."""
    from datetime import UTC, datetime
    from app.domain.workflow import RefundRun

    now = datetime.now(UTC)
    run = RefundRun(
        id="RB-99", kind="github_pr", status="pending_confirm",
        requester_name="demo", created_at=now, updated_at=now,
        requester_email="demo@example.com",
        pr_draft=PrDraft(path="docs/test.md", base_sha="sha1",
                         new_content="content", summary="Test change",
                         title="Update test doc", body="PR body"),
    )
    preview_result = StartResult(status="preview", run=run)
    fake_flow = _fake_flow_factory(preview_result)

    app.dependency_overrides[get_skill_router_svc] = lambda: _FakeSkillRouter()
    # Override token store to avoid Redis calls during auth resolution
    app.dependency_overrides[get_token_store] = lambda: None

    try:
        with TestClient(app) as client:
            # Set after lifespan runs so the real GithubFlow (set by lifespan) is replaced
            _original_flow = app.state.github_flow
            _original_store = app.state.github_store
            app.state.github_flow = fake_flow
            app.state.github_store = None  # no repo label lookup
            try:
                resp = client.post(
                    "/query",
                    json={"query": "update the refund policy"},
                    headers=_HDR,
                )
            finally:
                app.state.github_flow = _original_flow
                app.state.github_store = _original_store
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pending_action"]["type"] == "github_pr"
        assert body["pending_action"]["run_id"] == "RB-99"
        # flow.start was called with surface="web" and the user's email
        fake_flow.start.assert_called_once()
        call_kwargs = fake_flow.start.call_args
        assert call_kwargs.kwargs.get("surface") == "web"
        # debug auth sets email to "{user_id}@debug"; user_id from header is "u-demo"
        assert call_kwargs.kwargs.get("requester_email") == "u-demo@debug"
    finally:
        app.dependency_overrides.pop(get_skill_router_svc, None)
        app.dependency_overrides.pop(get_token_store, None)
