"""GithubFlow: the raise-PR playbook (transport-agnostic)."""
import pytest

from app.connectors.github_store import GithubStore
from app.connectors.models import GithubConfig, SurfaceConfig
from app.domain.workflow import PrDraft
from app.workflows.github_pr import GithubFlow
from app.workflows.store import RunStore

DRAFT = PrDraft(path="docs/refund-policy.md", base_sha="file-sha",
                new_content="# 30 days", summary="window 14→30 days",
                title="Update refund window", body="Extends the window.")


class _Connections:
    def __init__(self, enabled=True): self._enabled = enabled
    async def list_surfaces(self, tenant):
        return [SurfaceConfig(name="github", enabled=self._enabled)]


class _Engine:
    def __init__(self, draft=DRAFT, clarify=None):
        self._result = (draft, clarify)
    async def draft(self, text, *, client, config):
        return self._result


class _Client:
    """Records calls; first create_branch attempt may collide."""
    def __init__(self, collide_first=False):
        self.calls: list[tuple] = []
        self._collide = collide_first
    async def branch_sha(self, owner, repo, branch):
        self.calls.append(("branch_sha", branch)); return "base-sha"
    async def create_branch(self, owner, repo, name, sha):
        self.calls.append(("create_branch", name))
        if self._collide and name.count("-") == 1:  # first attempt only
            return False
        return True
    async def put_file(self, owner, repo, path, **kw):
        self.calls.append(("put_file", path, kw["branch"]))
    async def create_pr(self, owner, repo, **kw):
        self.calls.append(("create_pr", kw["head"]))
        return "https://github.com/acme/policies/pull/9"


def _flow(*, enabled=True, engine=None, client=None):
    store = RunStore(client=None, force_memory=True)
    github = GithubStore(client=None, force_memory=True)
    client = client or _Client()
    flow = GithubFlow(store=store, github=github, connections=_Connections(enabled),
                      engine=engine or _Engine(), client_factory=lambda tok: client)
    return flow, store, github, client


async def _seed(github, *, config=True, token=True):
    if config:
        await github.put_config("t-test", GithubConfig(owner="acme", repo="policies"))
    if token:
        await github.put_user_token("t-test", "tom@x", "gho_tok")


def _creds(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    monkeypatch.setenv("SUBSTRATEOS_TENANT_ID", "t-test")
    from app.config import get_settings
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_start_without_token_returns_connect_link(monkeypatch):
    _creds(monkeypatch)
    flow, store, github, _ = _flow()
    await _seed(github, token=False)
    r = await flow.start("raise a PR", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    assert r.status == "connect"
    assert "/auth/github/start?s=" in r.connect_url
    assert await store.list_runs() == []  # nothing recorded until we can draft


@pytest.mark.asyncio
async def test_start_drafts_and_waits_for_confirm(monkeypatch):
    _creds(monkeypatch)
    flow, store, github, _ = _flow()
    await _seed(github)
    r = await flow.start("update refund window to 30 days", requester_name="Tom",
                         requester_email="tom@x", surface="slack",
                         channel="C1", thread_ts="9.9")
    assert r.status == "preview"
    run = r.run
    assert run.kind == "github_pr" and run.status == "pending_confirm"
    assert run.surface == "slack" and run.requester_email == "tom@x"
    assert run.pr_draft.path == "docs/refund-policy.md"
    steps = [e.step for e in await store.list_events(run.id)]
    assert steps == ["Request received", "Change drafted", "Preview shown"]


@pytest.mark.asyncio
async def test_tool_disabled_blocks(monkeypatch):
    _creds(monkeypatch)
    flow, _, github, _ = _flow(enabled=False)
    await _seed(github)
    r = await flow.start("raise a PR", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    assert r.status == "blocked"


@pytest.mark.asyncio
async def test_clarify_when_engine_cannot_ground(monkeypatch):
    _creds(monkeypatch)
    flow, store, github, _ = _flow(engine=_Engine(draft=None, clarify="Which doc?"))
    await _seed(github)
    r = await flow.start("change it", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    assert r.status == "clarify" and "Which doc?" in r.message


@pytest.mark.asyncio
async def test_confirm_creates_pr_and_completes(monkeypatch):
    _creds(monkeypatch)
    client = _Client()
    flow, store, github, _ = _flow(client=client)
    await _seed(github)
    r = await flow.start("update window", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    out = await flow.confirm(r.run.id, actor_email="tom@x", actor_name="Tom")
    assert out.ok and out.pr_url.endswith("/pull/9")
    run = await store.get(r.run.id)
    assert run.status == "completed" and run.pr_url == out.pr_url
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Confirmed" in steps and "PR created" in steps
    assert ("create_pr", f"substrateos/{r.run.id.lower()}") in client.calls


@pytest.mark.asyncio
async def test_confirm_rejected_for_non_requester(monkeypatch):
    _creds(monkeypatch)
    flow, store, github, _ = _flow()
    await _seed(github)
    r = await flow.start("update window", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    out = await flow.confirm(r.run.id, actor_email="mallory@x", actor_name="Mallory")
    assert not out.ok
    assert (await store.get(r.run.id)).status == "pending_confirm"  # unchanged


@pytest.mark.asyncio
async def test_cancel_marks_cancelled(monkeypatch):
    _creds(monkeypatch)
    flow, store, github, client = _flow()
    await _seed(github)
    r = await flow.start("update window", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    out = await flow.cancel(r.run.id, actor_email="tom@x", actor_name="Tom")
    assert out.ok
    assert (await store.get(r.run.id)).status == "cancelled"
    assert all(c[0] != "create_pr" for c in client.calls)  # nothing touched GitHub


@pytest.mark.asyncio
async def test_branch_collision_retries_with_suffix(monkeypatch):
    _creds(monkeypatch)
    client = _Client(collide_first=True)
    flow, store, github, _ = _flow(client=client)
    await _seed(github)
    r = await flow.start("update window", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    out = await flow.confirm(r.run.id, actor_email="tom@x", actor_name="Tom")
    assert out.ok
    branch_attempts = [c[1] for c in client.calls if c[0] == "create_branch"]
    assert len(branch_attempts) == 2 and branch_attempts[1].endswith("-2")
