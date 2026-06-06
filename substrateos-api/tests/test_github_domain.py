"""Domain models for the GitHub raise-PR playbook."""
from datetime import UTC, datetime

from app.connectors.models import GithubConfig
from app.domain.workflow import PrDraft, RefundRun


def _run(**kw) -> RefundRun:
    now = datetime.now(UTC)
    base = dict(id="RB-1", requester_name="Tom", created_at=now, updated_at=now)
    base.update(kw)
    return RefundRun(**base)


def test_github_pr_run_roundtrip():
    draft = PrDraft(path="docs/policy.md", base_sha="abc123",
                    new_content="# new", summary="window 14→30 days",
                    title="Update refund window", body="Changes the window.")
    run = _run(kind="github_pr", status="pending_confirm", surface="slack",
               requester_email="tom@x", pr_draft=draft)
    again = RefundRun.model_validate_json(run.model_dump_json())
    assert again.pr_draft.path == "docs/policy.md"
    assert again.status == "pending_confirm"
    assert again.surface == "slack"


def test_cancelled_status_and_pr_url():
    run = _run(kind="github_pr", status="cancelled", pr_url=None)
    assert run.status == "cancelled"
    done = _run(kind="github_pr", status="completed", pr_url="https://github.com/o/r/pull/1")
    assert done.pr_url.endswith("/pull/1")


def test_github_config_defaults():
    cfg = GithubConfig(owner="acme", repo="policies")
    assert cfg.base_branch == "main"
