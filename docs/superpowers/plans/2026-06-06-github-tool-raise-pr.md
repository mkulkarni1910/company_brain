# GitHub Tool — Raise AI-Drafted PRs from Chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users on Web, Slack, or Teams ask SubstrateOS to "raise a PR …"; the brain drafts the file edit against the admin-configured repo, the requester previews and confirms, and a branch + commit + PR is created **as that user** via their own GitHub OAuth login — with every step in the Runs audit trail.

**Architecture:** A transport-agnostic `GithubFlow` (When→Check→Stop→Do→Record, shaped like `ApprovalFlow`) backed by a `GithubStore` (Redis: admin repo config, per-user tokens, OAuth states), a `GithubClient` (httpx against the GitHub REST API, always with the requesting user's token), and a two-step `PrDraftEngine` (LLM picks the target file from the repo tree, then drafts the full new content). Three thin surface adapters render the preview: Slack blocks + interactive buttons, Teams Adaptive Card + Action.Submit, Web `Answer.pending_action` + a new `POST /workflows/runs/{id}/action`. Admin: GitHub card on the Surfaces screen (tag `Tool`), `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` env + Key Vault, repo target editable via `/admin/github/config`.

**Tech Stack:** FastAPI · Python 3.12 · uv · pytest(-asyncio) · respx · redis.asyncio · Next.js 14 · React 18 · Tailwind-free hand CSS (existing design system). No new libraries.

**Spec:** `docs/superpowers/specs/2026-06-06-github-tool-raise-pr-design.md`

**Branch:** create `feat/github-tool` off `main` before Task 1.

**Working agreements:**
- Backend commands run from `substrateos-api/`: `uv run pytest tests/ -q` (full) or `uv run pytest tests/test_<x>.py -q` (one file).
- Web commands run from `web/`: `pnpm typecheck && pnpm lint && pnpm build`.
- Tests that read `Settings` must `monkeypatch.setenv(...)` then `get_settings.cache_clear()` (see `tests/test_approval_flow.py`).
- **Phase C (React) is GATED on the user approving the Phase A mockups.** Phase B (backend) may proceed in parallel after the mockups are presented.

---

## Phase A — Mockups (user approval gate)

### Task 1: Admin mockup — GitHub card + setup modal

**Files:**
- Modify: `mockups/admin-portal.html` (Surfaces section)

The mockup is the contract for Task 17. Match the existing card markup exactly (the React page and the mockup share class names: `surf-card`, `surf-logo`, `surf-chip`, `admin-modal`, `setup-steps`…).

- [ ] **Step 1: Add the GitHub card right after the Teams card**

Find the Teams `surf-card` in the Surfaces section and insert after it (adapt the literal markup to whatever card structure the file actually uses — same elements, same order as its siblings):

```html
<div class="surf-card">
  <div class="surf-top">
    <div class="surf-head">
      <div class="surf-logo sl-github">
        <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
          <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/>
        </svg>
      </div>
      <div>
        <div class="surf-name">GitHub</div>
        <span class="surf-chip">Tool</span>
      </div>
    </div>
    <button class="sw on" aria-label="Disable GitHub"></button>
  </div>
  <div class="surf-desc">Action connector — where SubstrateOS acts. Users raise AI-drafted pull
    requests to your configured repo from chat. Each PR is authored by the requesting user via
    their own GitHub login.</div>
  <div class="surf-foot">
    <button class="surf-install-btn btn-github">Connect GitHub</button>
  </div>
</div>
```

Add to the mockup's `<style>` (next to `.sl-slack`/`.sl-teams` and `.btn-slack`/`.btn-teams`):

```css
.sl-github { background:#24292f; color:#fff; }
.btn-github { background:#24292f; color:#fff; }
```

- [ ] **Step 2: Add the setup modal**

Clone the structure of the Teams install modal in the mockup. Content (the copy is the deliverable — it must teach the app-credential vs user-token distinction):

- Header: logo + **Connect SubstrateOS to GitHub** · sub: `One-time setup · ~5 minutes · a GitHub account that can create OAuth Apps`
- Info note: `One app credential for SubstrateOS (this setup) — then each user connects their own GitHub from chat, so every PR is authored by the person who asked.`
- Steps:
  1. On GitHub go to **Settings → Developer settings → OAuth Apps → New OAuth App**. Set the callback URL to: `code: http://localhost:8000/auth/github/callback`
  2. Copy the **Client ID** and generate a **Client Secret**, add them to the server environment, and restart the API. `code: GITHUB_CLIENT_ID=…   GITHUB_CLIENT_SECRET=…`
  3. Enter the repository SubstrateOS raises PRs against: two inline inputs `owner/repo` and `base branch` (default `main`) + a **Save repo** button.
  4. Done — the card shows **Connected to owner/repo**. Users connect their own GitHub the first time they ask for a PR.
- Footer: Close + a `btn-github` button **Open GitHub OAuth Apps ↗**.

- [ ] **Step 3: Open for review**

```bash
open mockups/admin-portal.html
```

Present to the user; iterate until approved. **Record approval before Phase C.**

### Task 2: Web-chat mockup — PR preview card

**Files:**
- Modify: `mockups/user-web-chat.html`

- [ ] **Step 1: Add a PR preview card inside an assistant message**

In the chat transcript area, add (after an existing assistant bubble) a demo turn: user asks *"Raise a PR updating the refund policy to a 30-day window"*, assistant bubble says *"Here's the change I drafted — review and confirm before anything touches GitHub."* followed by the preview card. Match the warm-paper palette and existing card/citation styling; suggested structure:

```html
<div class="pr-card">
  <div class="pr-head">
    <span class="pr-icon"><!-- reuse the GitHub svg path from Task 1, 16px --></span>
    <span class="pr-title">Update refund policy to a 30-day window</span>
    <span class="pr-chip">pending your confirm</span>
  </div>
  <div class="pr-meta">acme/policies · <code>docs/refund-policy.md</code> · branch <code>substrateos/rb-4474</code></div>
  <div class="pr-summary">Changes the auto-approve window from 14 days to 30 days; everything else unchanged.</div>
  <div class="pr-actions">
    <button class="pr-create">Create PR</button>
    <button class="pr-cancel">Cancel</button>
  </div>
  <div class="pr-foot">🔒 Nothing reaches GitHub until you confirm — the PR will be authored as you.</div>
</div>
```

Style with the existing CSS variables (border, radius, Fraunces for the title, JetBrains Mono for paths); `pr-create` uses the page's primary accent, `pr-cancel` is a quiet/ghost button. Also add a second, "done" variant card in another demo turn: chip `PR created`, actions replaced by a link `View PR #128 ↗`.

- [ ] **Step 2: Open for review**

```bash
open mockups/user-web-chat.html
```

Present to the user; iterate until approved.

> **GATE:** Do not start Tasks 16–19 until the user has explicitly approved both mockups. Tasks 3–15 may proceed.

---

## Phase B — Backend

### Task 3: Settings — GitHub OAuth App credentials

**Files:**
- Modify: `substrateos-api/app/config.py`
- Test: `substrateos-api/tests/test_config.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_config.py`, following its existing style)

```python
def test_github_settings_default_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    from app.config import Settings
    s = Settings(_env_file=None)
    assert s.github_client_id is None
    assert s.github_client_secret is None


def test_github_secret_loaded_from_keyvault():
    from app.config import Settings, load_secrets_from_keyvault

    class _FakeSecret:
        def __init__(self, value): self.value = value

    class _FakeKV:
        def get_secret(self, name):
            if name == "github-client-secret":
                return _FakeSecret("gh-secret")
            raise KeyError(name)

    s = Settings(_env_file=None)
    s.use_key_vault = True
    s.azure_key_vault_url = "https://kv.example"
    load_secrets_from_keyvault(s, client=_FakeKV())
    assert s.github_client_secret == "gh-secret"
```

Note: `Settings` has required fields (`azure_tenant_id` etc.) — conftest sets those env vars already; if `Settings(_env_file=None)` fails locally, mirror how the existing tests in `test_config.py` construct it and adapt.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -q` — Expected: FAIL (`github_client_id` attribute missing).

- [ ] **Step 3: Implement** — in `app/config.py`, after the `slack_refund_approver_id` line:

```python
    # GitHub tool (raise-PR action connector). App-level OAuth App credentials —
    # per-user tokens are obtained through the user's own GitHub login and stored
    # in GithubStore. Secret loaded from Key Vault in prod (github-client-secret).
    github_client_id: str | None = None       # GITHUB_CLIENT_ID
    github_client_secret: str | None = None   # GITHUB_CLIENT_SECRET
```

And in `load_secrets_from_keyvault`, after the `gemini_api_key` line:

```python
    settings.github_client_secret = (
        _get("github-client-secret") or settings.github_client_secret
    )
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_config.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(config): GitHub OAuth App credentials (env + Key Vault)"`

### Task 4: Domain — run statuses, kind, PrDraft, GithubConfig

**Files:**
- Modify: `substrateos-api/app/domain/workflow.py`
- Modify: `substrateos-api/app/connectors/models.py`
- Test: `substrateos-api/tests/test_github_domain.py` (new)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_domain.py -q` — Expected: ImportError on `PrDraft` / `GithubConfig`.

- [ ] **Step 3: Implement** — in `app/domain/workflow.py`:

```python
RunStatus = Literal[
    "running", "pending_approval", "pending_confirm",
    "approved", "rejected", "completed", "cancelled", "error",
]
```

```python
RunKind = Literal["refund", "approval", "github_pr"]
```

```python
class PrDraft(BaseModel):
    """The AI-drafted change awaiting the requester's confirm (github_pr runs)."""
    path: str
    base_sha: str       # sha of the current file (Contents API requires it on update)
    new_content: str
    summary: str        # one-line, shown on the preview card
    title: str          # PR title
    body: str           # PR description (markdown)
```

And on `RefundRun`, after `approver_source`:

```python
    # github_pr playbook
    surface: str | None = None          # "web" | "slack" | "teams"
    requester_email: str | None = None  # identity key for the per-user GitHub token
    pr_draft: PrDraft | None = None
    pr_url: str | None = None
```

In `app/connectors/models.py`, after `SurfaceConfig`:

```python
class GithubConfig(BaseModel):
    """Admin-configured PR target for the GitHub tool."""
    owner: str
    repo: str
    base_branch: str = "main"
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_domain.py tests/test_domain.py tests/test_connector_models.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(domain): github_pr run kind, PrDraft, GithubConfig"`

### Task 5: GithubStore — config, per-user tokens, OAuth states

**Files:**
- Create: `substrateos-api/app/connectors/github_store.py`
- Test: `substrateos-api/tests/test_github_store.py`

Shape it on `RunStore` (Redis + in-process mirror, `force_memory` for tests) — user tokens are flow-critical for the demo, so keep the memory fallback.

- [ ] **Step 1: Write the failing test**

```python
"""GithubStore: repo config, per-user tokens, one-shot OAuth states."""
import pytest

from app.connectors.github_store import GithubStore
from app.connectors.models import GithubConfig


@pytest.mark.asyncio
async def test_config_roundtrip():
    store = GithubStore(client=None, force_memory=True)
    assert await store.get_config("t-test") is None
    await store.put_config("t-test", GithubConfig(owner="acme", repo="policies", base_branch="dev"))
    cfg = await store.get_config("t-test")
    assert (cfg.owner, cfg.repo, cfg.base_branch) == ("acme", "policies", "dev")


@pytest.mark.asyncio
async def test_user_token_roundtrip_and_isolation():
    store = GithubStore(client=None, force_memory=True)
    assert await store.get_user_token("t-test", "tom@x") is None
    await store.put_user_token("t-test", "tom@x", "gho_abc")
    assert await store.get_user_token("t-test", "tom@x") == "gho_abc"
    assert await store.get_user_token("t-test", "diana@x") is None


@pytest.mark.asyncio
async def test_oauth_state_is_one_shot():
    store = GithubStore(client=None, force_memory=True)
    state = await store.mint_connect_state("t-test", "tom@x")
    assert isinstance(state, str) and len(state) >= 20
    assert await store.peek_connect_state(state) == ("t-test", "tom@x")   # /start: not consumed
    assert await store.consume_connect_state(state) == ("t-test", "tom@x")  # callback: consumed
    assert await store.consume_connect_state(state) is None              # reuse rejected
    assert await store.consume_connect_state("bogus") is None
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_store.py -q` — Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement** `app/connectors/github_store.py`:

```python
"""Redis-backed state for the GitHub tool: admin repo config, per-user OAuth
tokens, and one-shot connect states. Mirrors writes to an in-process dict so
the flow keeps working within a single process when Redis is unavailable
(same degradation philosophy as RunStore)."""

from __future__ import annotations

import contextlib
import logging
import secrets

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.connectors.models import GithubConfig

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)


def _config_key(tenant: str) -> str: return f"github:config:{tenant}"
def _token_key(tenant: str, email: str) -> str: return f"github:token:{tenant}:{email.lower()}"
def _state_key(state: str) -> str: return f"github:oauth:{state}"


class GithubStore:
    def __init__(self, client: redis.Redis | None = None, *, force_memory: bool = False) -> None:
        self._mem: dict[str, str] = {}
        if force_memory:
            self._r = None
            return
        if client is not None:
            self._r = client
            return
        s = get_settings()
        if not s.azure_redis_host:
            self._r = None
            return
        self._r = redis.Redis(
            host=s.azure_redis_host, port=s.azure_redis_port,
            ssl=s.azure_redis_ssl, password=s.redis_key,
            decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
        )

    async def aclose(self) -> None:
        if self._r is not None:
            with contextlib.suppress(Exception):
                await self._r.aclose()

    # ── shared get/set with memory mirror ──────────────────────────────────────

    async def _set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self._mem[key] = value
        if self._r is None:
            return
        try:
            await self._r.set(key, value, ex=ex)
        except _ERRORS as e:
            logger.warning("GithubStore set failed: %s", e)

    async def _get(self, key: str) -> str | None:
        if self._r is not None:
            try:
                v = await self._r.get(key)
                if v is not None:
                    return v
            except _ERRORS as e:
                logger.warning("GithubStore get failed: %s", e)
        return self._mem.get(key)

    async def _getdel(self, key: str) -> str | None:
        redis_val: str | None = None
        if self._r is not None:
            try:
                redis_val = await self._r.getdel(key)
            except _ERRORS as e:
                logger.warning("GithubStore getdel failed: %s", e)
        return self._mem.pop(key, None) if redis_val is None else (self._mem.pop(key, None), redis_val)[1]

    # ── admin repo config ───────────────────────────────────────────────────────

    async def get_config(self, tenant: str) -> GithubConfig | None:
        raw = await self._get(_config_key(tenant))
        if not raw:
            return None
        with contextlib.suppress(Exception):
            return GithubConfig.model_validate_json(raw)
        return None

    async def put_config(self, tenant: str, cfg: GithubConfig) -> None:
        await self._set(_config_key(tenant), cfg.model_dump_json())

    # ── per-user tokens ─────────────────────────────────────────────────────────

    async def get_user_token(self, tenant: str, email: str | None) -> str | None:
        if not email:
            return None
        return await self._get(_token_key(tenant, email))

    async def put_user_token(self, tenant: str, email: str, token: str) -> None:
        await self._set(_token_key(tenant, email), token)

    # ── one-shot connect states (CSRF) ──────────────────────────────────────────

    async def mint_connect_state(self, tenant: str, email: str) -> str:
        state = secrets.token_urlsafe(24)
        ttl = get_settings().oauth_state_ttl_seconds
        await self._set(_state_key(state), f"{tenant}|{email}", ex=ttl)
        return state

    async def peek_connect_state(self, state: str) -> tuple[str, str] | None:
        raw = await self._get(_state_key(state))
        if not raw or "|" not in raw:
            return None
        tenant, email = raw.split("|", 1)
        return tenant, email

    async def consume_connect_state(self, state: str) -> tuple[str, str] | None:
        raw = await self._getdel(_state_key(state))
        if not raw or "|" not in raw:
            return None
        tenant, email = raw.split("|", 1)
        return tenant, email
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_store.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(github): GithubStore — config, user tokens, one-shot OAuth states"`

### Task 6: GithubClient — REST wrapper + code exchange

**Files:**
- Create: `substrateos-api/app/connectors/github.py`
- Test: `substrateos-api/tests/test_github_client.py`

- [ ] **Step 1: Write the failing test** (respx, like the other HTTP-client tests)

```python
"""GithubClient against a mocked GitHub REST API."""
import base64

import pytest
import respx
from httpx import Response

from app.connectors.github import (
    GithubApiError,
    GithubAuthError,
    GithubClient,
    exchange_code,
)

API = "https://api.github.com"


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_returns_token():
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=Response(200, json={"access_token": "gho_xyz", "token_type": "bearer"}))
    tok = await exchange_code(client_id="cid", client_secret="sec", code="c0de")
    assert tok == "gho_xyz"


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_error_returns_none():
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=Response(200, json={"error": "bad_verification_code"}))
    assert await exchange_code(client_id="cid", client_secret="sec", code="bad") is None


@pytest.mark.asyncio
@respx.mock
async def test_repo_operations_happy_path():
    c = GithubClient("gho_xyz")
    respx.get(f"{API}/repos/acme/policies/git/ref/heads/main").mock(
        return_value=Response(200, json={"object": {"sha": "base-sha"}}))
    respx.post(f"{API}/repos/acme/policies/git/refs").mock(
        return_value=Response(201, json={"ref": "refs/heads/substrateos/rb-1"}))
    respx.get(f"{API}/repos/acme/policies/git/trees/main", params={"recursive": "1"}).mock(
        return_value=Response(200, json={"tree": [
            {"path": "docs/refund-policy.md", "type": "blob"},
            {"path": "docs", "type": "tree"},
        ]}))
    content_b64 = base64.b64encode(b"# Refund policy\n14 days").decode()
    respx.get(f"{API}/repos/acme/policies/contents/docs/refund-policy.md").mock(
        return_value=Response(200, json={"content": content_b64, "sha": "file-sha"}))
    respx.put(f"{API}/repos/acme/policies/contents/docs/refund-policy.md").mock(
        return_value=Response(200, json={"commit": {"sha": "new"}}))
    respx.post(f"{API}/repos/acme/policies/pulls").mock(
        return_value=Response(201, json={"html_url": "https://github.com/acme/policies/pull/7"}))

    assert await c.branch_sha("acme", "policies", "main") == "base-sha"
    assert await c.create_branch("acme", "policies", "substrateos/rb-1", "base-sha") is True
    assert await c.list_paths("acme", "policies", "main") == ["docs/refund-policy.md"]
    content, sha = await c.get_file("acme", "policies", "docs/refund-policy.md", ref="main")
    assert "14 days" in content and sha == "file-sha"
    await c.put_file("acme", "policies", "docs/refund-policy.md",
                     content="# Refund policy\n30 days", message="Update window",
                     branch="substrateos/rb-1", sha="file-sha")
    url = await c.create_pr("acme", "policies", title="t", body="b",
                            head="substrateos/rb-1", base="main")
    assert url.endswith("/pull/7")


@pytest.mark.asyncio
@respx.mock
async def test_branch_collision_returns_false():
    c = GithubClient("gho_xyz")
    respx.post(f"{API}/repos/acme/policies/git/refs").mock(
        return_value=Response(422, json={"message": "Reference already exists"}))
    assert await c.create_branch("acme", "policies", "dup", "sha") is False


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_auth_error():
    c = GithubClient("gho_revoked")
    respx.get(f"{API}/repos/acme/policies/git/ref/heads/main").mock(
        return_value=Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(GithubAuthError):
        await c.branch_sha("acme", "policies", "main")


@pytest.mark.asyncio
@respx.mock
async def test_other_errors_raise_api_error():
    c = GithubClient("gho_xyz")
    respx.get(f"{API}/repos/acme/nope/git/ref/heads/main").mock(
        return_value=Response(404, json={"message": "Not Found"}))
    with pytest.raises(GithubApiError):
        await c.branch_sha("acme", "nope", "main")
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_client.py -q` — Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement** `app/connectors/github.py`:

```python
"""GitHub REST client for the raise-PR tool. Every call carries the requesting
user's own OAuth token — attribution is structural: GitHub sees who acted."""

from __future__ import annotations

import base64
import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_OAUTH_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN = "https://github.com/login/oauth/access_token"


class GithubApiError(Exception):
    """GitHub returned an unexpected error."""


class GithubAuthError(GithubApiError):
    """Token rejected (revoked/expired) — the user must reconnect."""


async def exchange_code(*, client_id: str, client_secret: str, code: str) -> str | None:
    """Authorization-code → user access token. None on any failure."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GITHUB_OAUTH_TOKEN,
                json={"client_id": client_id, "client_secret": client_secret, "code": code},
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
        data = resp.json()
        return data.get("access_token")
    except Exception:  # noqa: BLE001
        logger.exception("github code exchange failed")
        return None


class GithubClient:
    def __init__(self, token: str, *, timeout: float = 10.0) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(base_url=GITHUB_API, headers=self._headers,
                                     timeout=self._timeout) as client:
            resp = await client.request(method, path, **kw)
        if resp.status_code == 401:
            raise GithubAuthError("GitHub rejected the token (reconnect needed)")
        return resp

    async def branch_sha(self, owner: str, repo: str, branch: str) -> str:
        r = await self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{quote(branch)}")
        if r.status_code != 200:
            raise GithubApiError(f"ref lookup failed ({r.status_code})")
        return r.json()["object"]["sha"]

    async def create_branch(self, owner: str, repo: str, name: str, sha: str) -> bool:
        """True on created; False when the ref already exists (422)."""
        r = await self._request("POST", f"/repos/{owner}/{repo}/git/refs",
                                json={"ref": f"refs/heads/{name}", "sha": sha})
        if r.status_code == 201:
            return True
        if r.status_code == 422:
            return False
        raise GithubApiError(f"create branch failed ({r.status_code})")

    async def list_paths(self, owner: str, repo: str, branch: str, *, limit: int = 400) -> list[str]:
        r = await self._request("GET", f"/repos/{owner}/{repo}/git/trees/{quote(branch)}",
                                params={"recursive": "1"})
        if r.status_code != 200:
            raise GithubApiError(f"tree listing failed ({r.status_code})")
        blobs = [t["path"] for t in r.json().get("tree", []) if t.get("type") == "blob"]
        return blobs[:limit]

    async def get_file(self, owner: str, repo: str, path: str, *, ref: str) -> tuple[str, str]:
        """Returns (decoded_content, blob_sha)."""
        r = await self._request("GET", f"/repos/{owner}/{repo}/contents/{quote(path)}",
                                params={"ref": ref})
        if r.status_code != 200:
            raise GithubApiError(f"get file failed ({r.status_code})")
        d = r.json()
        content = base64.b64decode(d.get("content") or "").decode("utf-8", errors="replace")
        return content, d["sha"]

    async def put_file(self, owner: str, repo: str, path: str, *, content: str,
                       message: str, branch: str, sha: str) -> None:
        r = await self._request("PUT", f"/repos/{owner}/{repo}/contents/{quote(path)}",
                                json={
                                    "message": message, "branch": branch, "sha": sha,
                                    "content": base64.b64encode(content.encode()).decode(),
                                })
        if r.status_code not in (200, 201):
            raise GithubApiError(f"commit failed ({r.status_code})")

    async def create_pr(self, owner: str, repo: str, *, title: str, body: str,
                        head: str, base: str) -> str:
        r = await self._request("POST", f"/repos/{owner}/{repo}/pulls",
                                json={"title": title, "body": body, "head": head, "base": base})
        if r.status_code != 201:
            raise GithubApiError(f"create PR failed ({r.status_code})")
        return r.json()["html_url"]
```

Note on the test for `get_file`: respx matches `contents/docs/refund-policy.md` — `quote(path)` leaves `/` encoded as `%2F`. GitHub accepts both, but make the test and impl agree: use `quote(path, safe='/')` in `get_file`/`put_file` so the URL keeps literal slashes. Adjust the implementation accordingly (`quote(path, safe="/")`).

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_client.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(github): REST client + OAuth code exchange"`

### Task 7: PrDraftEngine — two-step LLM draft

**Files:**
- Create: `substrateos-api/app/workflows/github_engine.py`
- Test: `substrateos-api/tests/test_github_engine.py`

- [ ] **Step 1: Write the failing test**

```python
"""PrDraftEngine: pick target file from the repo tree, then draft the edit."""
import json

import pytest

from app.connectors.models import GithubConfig
from app.workflows.github_engine import PrDraftEngine

CFG = GithubConfig(owner="acme", repo="policies", base_branch="main")


class _FakeClient:
    def __init__(self):
        self.paths = ["docs/refund-policy.md", "README.md"]

    async def list_paths(self, owner, repo, branch, **kw):
        return self.paths

    async def get_file(self, owner, repo, path, *, ref):
        return "# Refund policy\nWindow: 14 days\n", "file-sha"


class _FakeLLM:
    """Returns queued replies in order."""
    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls: list[list[dict]] = []

    async def complete(self, *, messages, temperature=0.0, max_tokens=0):
        self.calls.append(messages)
        return self._replies.pop(0)


@pytest.mark.asyncio
async def test_draft_happy_path():
    llm = _FakeLLM(
        json.dumps({"found": True, "path": "docs/refund-policy.md", "reasoning": "policy doc"}),
        json.dumps({"new_content": "# Refund policy\nWindow: 30 days\n",
                    "summary": "window 14→30 days", "title": "Update refund window",
                    "body": "Extends the refund window."}),
    )
    engine = PrDraftEngine(llm=llm)
    draft, clarify = await engine.draft("update the refund window to 30 days",
                                        client=_FakeClient(), config=CFG)
    assert clarify is None
    assert draft.path == "docs/refund-policy.md"
    assert draft.base_sha == "file-sha"
    assert "30 days" in draft.new_content
    assert draft.title == "Update refund window"


@pytest.mark.asyncio
async def test_no_target_file_returns_clarify_question():
    llm = _FakeLLM(json.dumps({"found": False, "question": "Which document should I change?"}))
    engine = PrDraftEngine(llm=llm)
    draft, clarify = await engine.draft("change the thing", client=_FakeClient(), config=CFG)
    assert draft is None
    assert "Which document" in clarify


@pytest.mark.asyncio
async def test_unparseable_llm_reply_returns_clarify():
    llm = _FakeLLM("I cannot answer in JSON, sorry")
    engine = PrDraftEngine(llm=llm)
    draft, clarify = await engine.draft("update policy", client=_FakeClient(), config=CFG)
    assert draft is None
    assert clarify  # stops and asks — never guesses


@pytest.mark.asyncio
async def test_edit_step_can_refuse():
    llm = _FakeLLM(
        json.dumps({"found": True, "path": "docs/refund-policy.md"}),
        json.dumps({"new_content": "", "question": "The policy has three windows — which one?"}),
    )
    engine = PrDraftEngine(llm=llm)
    draft, clarify = await engine.draft("update the window", client=_FakeClient(), config=CFG)
    assert draft is None
    assert "three windows" in clarify
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_engine.py -q` — Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement** `app/workflows/github_engine.py`:

```python
"""Drafts the PR content for the raise-PR playbook in two grounded LLM steps:
1) pick the target file from the real repo tree; 2) given the file's actual
current content, produce the full new content + PR metadata. If either step
can't ground the change, it returns a clarifying question — never guesses."""

from __future__ import annotations

import json
import logging
import re

from app.connectors.models import GithubConfig
from app.domain.workflow import PrDraft

logger = logging.getLogger(__name__)

_FALLBACK_QUESTION = (
    "I couldn't work out which file this change applies to — "
    "tell me the file (or the doc name) and I'll draft the PR."
)

TARGET_PROMPT = (
    "You are SubstrateOS running the raise-PR playbook. Given a user's change request "
    "and the list of file paths in the repository, pick the SINGLE file the change applies to. "
    "Respond ONLY with valid JSON, no other text:\n"
    '{"found": true, "path": "docs/example.md", "reasoning": "one sentence"}\n'
    "If no file clearly matches, respond with "
    '{"found": false, "question": "one clarifying question for the user"}.'
)

EDIT_PROMPT = (
    "You are SubstrateOS drafting a pull request. Given the CURRENT content of the file and "
    "the requested change, produce the FULL new file content with the change applied — keep "
    "all unrelated content byte-identical. Respond ONLY with valid JSON, no other text:\n"
    '{"new_content": "...", "summary": "one line describing what changed", '
    '"title": "PR title", "body": "PR description in markdown"}\n'
    "If the request cannot be applied to this file or is ambiguous, respond with "
    '{"new_content": "", "question": "one clarifying question for the user"}.'
)


def _json_or_none(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


class PrDraftEngine:
    def __init__(self, *, llm) -> None:
        self._llm = llm

    async def draft(self, text: str, *, client, config: GithubConfig
                    ) -> tuple[PrDraft | None, str | None]:
        """Returns (draft, clarify_question) — exactly one is non-None."""
        paths = await client.list_paths(config.owner, config.repo, config.base_branch)
        raw = await self._llm.complete(
            messages=[
                {"role": "system", "content": TARGET_PROMPT},
                {"role": "user", "content": (
                    f"Repository files:\n" + "\n".join(paths) +
                    f"\n\nChange request: {text}"
                )},
            ],
            temperature=0.0, max_tokens=300,
        )
        target = _json_or_none(raw)
        if not target:
            logger.warning("raise-pr target step: unparseable reply %r", raw[:200])
            return None, _FALLBACK_QUESTION
        if not target.get("found") or not target.get("path"):
            return None, target.get("question") or _FALLBACK_QUESTION

        path = target["path"]
        content, sha = await client.get_file(config.owner, config.repo, path,
                                             ref=config.base_branch)
        raw = await self._llm.complete(
            messages=[
                {"role": "system", "content": EDIT_PROMPT},
                {"role": "user", "content": (
                    f"File: {path}\n\nCurrent content:\n{content}\n\n"
                    f"Change request: {text}"
                )},
            ],
            temperature=0.0, max_tokens=8000,
        )
        edit = _json_or_none(raw)
        if not edit:
            logger.warning("raise-pr edit step: unparseable reply %r", raw[:200])
            return None, _FALLBACK_QUESTION
        if not edit.get("new_content"):
            return None, edit.get("question") or _FALLBACK_QUESTION
        return PrDraft(
            path=path, base_sha=sha, new_content=edit["new_content"],
            summary=edit.get("summary") or "Drafted change",
            title=edit.get("title") or f"Update {path}",
            body=edit.get("body") or "",
        ), None
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_engine.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(github): PrDraftEngine — grounded two-step PR drafting"`

### Task 8: GithubFlow — When→Check→Stop→Do→Record

**Files:**
- Create: `substrateos-api/app/workflows/github_pr.py`
- Test: `substrateos-api/tests/test_github_flow.py`

- [ ] **Step 1: Write the failing test**

```python
"""GithubFlow: the raise-PR playbook (transport-agnostic)."""
import pytest

from app.connectors.github import GithubApiError
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


def _flow(*, enabled=True, engine=None, client=None, with_config=True, with_token=True):
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


@pytest.mark.asyncio
async def test_start_without_token_returns_connect_link(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store, github, _ = _flow()
    await _seed(github, token=False)
    r = await flow.start("raise a PR", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    assert r.status == "connect"
    assert "/auth/github/start?s=" in r.connect_url
    assert await store.list_runs() == []  # nothing recorded until we can draft


@pytest.mark.asyncio
async def test_start_drafts_and_waits_for_confirm(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
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
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, _, github, _ = _flow(enabled=False)
    await _seed(github)
    r = await flow.start("raise a PR", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    assert r.status == "blocked"


@pytest.mark.asyncio
async def test_clarify_when_engine_cannot_ground(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store, github, _ = _flow(engine=_Engine(draft=None, clarify="Which doc?"))
    await _seed(github)
    r = await flow.start("change it", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    assert r.status == "clarify" and "Which doc?" in r.message


@pytest.mark.asyncio
async def test_confirm_creates_pr_and_completes(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
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
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store, github, _ = _flow()
    await _seed(github)
    r = await flow.start("update window", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    out = await flow.confirm(r.run.id, actor_email="mallory@x", actor_name="Mallory")
    assert not out.ok
    assert (await store.get(r.run.id)).status == "pending_confirm"  # unchanged


@pytest.mark.asyncio
async def test_cancel_marks_cancelled(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
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
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    client = _Client(collide_first=True)
    flow, store, github, _ = _flow(client=client)
    await _seed(github)
    r = await flow.start("update window", requester_name="Tom", requester_email="tom@x",
                         surface="web")
    out = await flow.confirm(r.run.id, actor_email="tom@x", actor_name="Tom")
    assert out.ok
    branch_attempts = [c[1] for c in client.calls if c[0] == "create_branch"]
    assert len(branch_attempts) == 2 and branch_attempts[1].endswith("-2")
```

Note: `_Client.create_branch` collision heuristic (`name.count("-") == 1`) relies on the base branch being `substrateos/rb-NNNN` (one hyphen) and the retry `substrateos/rb-NNNN-2` (two). Keep flow branch naming consistent with that.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_flow.py -q` — Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement** `app/workflows/github_pr.py`:

```python
"""The raise-PR playbook (When → Check → Stop → Do → Record), transport-agnostic.

Surface adapters (Slack blocks, Teams Adaptive Cards, the web pending_action
payload) call start/confirm/cancel and render the returned results — the logic
is never forked per surface. PRs are created with the requesting user's own
GitHub token, so attribution is structural."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import get_settings
from app.connectors.github import GithubApiError, GithubAuthError, GithubClient
from app.connectors.github_store import GithubStore
from app.domain.workflow import RefundRun
from app.workflows.github_engine import PrDraftEngine
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)

_NOT_CONFIGURED = (
    "The GitHub tool isn't set up yet — ask your admin to connect a repository "
    "in Admin → Surfaces → GitHub."
)
_DISABLED = "The GitHub tool is disabled — raise-PR requests are refused."
_RECONNECT = "Your GitHub connection expired — reconnect and ask again: "


@dataclass
class StartResult:
    status: Literal["preview", "connect", "clarify", "blocked", "error"]
    run: RefundRun | None = None
    connect_url: str | None = None
    message: str = ""


@dataclass
class ActionResult:
    ok: bool
    status: str = ""
    pr_url: str | None = None
    message: str = ""


class GithubFlow:
    def __init__(self, *, store: RunStore, github: GithubStore, connections,
                 engine: PrDraftEngine, client_factory=GithubClient) -> None:
        self._store = store
        self._github = github
        self._connections = connections
        self._engine = engine
        self._client_factory = client_factory

    async def _tool_enabled(self, tenant: str) -> bool:
        try:
            surfaces = await self._connections.list_surfaces(tenant)
            cfg = next((s for s in surfaces if s.name == "github"), None)
            return cfg.enabled if cfg is not None else True
        except Exception:  # noqa: BLE001 — config-store outage must not silence the tool
            return True

    async def _connect_result(self, tenant: str, email: str, *, expired: bool = False) -> StartResult:
        state = await self._github.mint_connect_state(tenant, email)
        url = f"{get_settings().substrateos_api_base_url}/auth/github/start?s={state}"
        msg = (_RECONNECT if expired else
               "Connect your GitHub account first — the PR will be authored as you: ")
        return StartResult(status="connect", connect_url=url, message=msg + url)

    # ── When + Check + Stop ────────────────────────────────────────────────────

    async def start(self, text: str, *, requester_name: str, requester_email: str | None,
                    surface: str, channel: str | None = None,
                    thread_ts: str | None = None) -> StartResult:
        s = get_settings()
        tenant = s.substrateos_tenant_id
        if not await self._tool_enabled(tenant):
            return StartResult(status="blocked", message=_DISABLED)
        cfg = await self._github.get_config(tenant)
        if cfg is None or not s.github_client_id or not s.github_client_secret:
            return StartResult(status="error", message=_NOT_CONFIGURED)
        if not requester_email:
            return StartResult(status="error",
                               message="I couldn't resolve your email on this surface, "
                                       "so I can't link a GitHub login to you.")
        token = await self._github.get_user_token(tenant, requester_email)
        if not token:
            return await self._connect_result(tenant, requester_email)

        try:
            draft, clarify = await self._engine.draft(
                text, client=self._client_factory(token), config=cfg)
        except GithubAuthError:
            return await self._connect_result(tenant, requester_email, expired=True)
        except GithubApiError as e:
            logger.warning("raise-pr draft failed: %s", e)
            return StartResult(status="error",
                               message=f"I couldn't read {cfg.owner}/{cfg.repo} — "
                                       "check the repo configuration with your admin.")
        if draft is None:
            return StartResult(status="clarify", message=clarify or "")

        run = await self._store.create(
            requester_name=requester_name, requester_slack_id=None,
            channel=channel, thread_ts=thread_ts, kind="github_pr", request_text=text,
        )
        run.status = "pending_confirm"
        run.surface = surface
        run.requester_email = requester_email
        run.pr_draft = draft
        await self._store.save(run)
        await self._store.add_event(run.id, step="Request received",
                                    detail=f"{text[:160]} · from {surface}", actor=requester_name)
        await self._store.add_event(run.id, step="Change drafted",
                                    detail=f"{draft.path} — {draft.summary}", actor="SubstrateOS")
        await self._store.add_event(run.id, step="Preview shown",
                                    detail="Awaiting the requester's confirm — nothing acts until they decide",
                                    actor="SubstrateOS")
        return StartResult(status="preview", run=run)

    # ── shared action guards ───────────────────────────────────────────────────

    async def _guarded_run(self, run_id: str, actor_email: str | None) -> tuple[RefundRun | None, ActionResult | None]:
        run = await self._store.get(run_id)
        if run is None or run.kind != "github_pr":
            return None, ActionResult(ok=False, status="unknown", message="Unknown run.")
        if not actor_email or actor_email.lower() != (run.requester_email or "").lower():
            await self._store.add_event(run.id, step="Action rejected",
                                        detail=f"Confirm/cancel attempted by {actor_email or 'unknown'} — requester only",
                                        actor="SubstrateOS")
            return None, ActionResult(ok=False, status=run.status,
                                      message="Only the requester can act on this PR.")
        if run.status != "pending_confirm":
            return None, ActionResult(ok=False, status=run.status, pr_url=run.pr_url,
                                      message=f"This run is already {run.status}.")
        return run, None

    # ── Do + Record ────────────────────────────────────────────────────────────

    async def confirm(self, run_id: str, *, actor_email: str | None,
                      actor_name: str) -> ActionResult:
        run, err = await self._guarded_run(run_id, actor_email)
        if err is not None:
            return err
        s = get_settings()
        tenant = s.substrateos_tenant_id
        cfg = await self._github.get_config(tenant)
        token = await self._github.get_user_token(tenant, run.requester_email)
        if cfg is None or not token:
            return ActionResult(ok=False, status=run.status, message=_NOT_CONFIGURED)
        client = self._client_factory(token)
        draft = run.pr_draft
        base_branch = f"substrateos/{run.id.lower()}"
        try:
            sha = await client.branch_sha(cfg.owner, cfg.repo, cfg.base_branch)
            branch = base_branch
            for attempt in range(2, 7):
                if await client.create_branch(cfg.owner, cfg.repo, branch, sha):
                    break
                branch = f"{base_branch}-{attempt}"
            else:
                raise GithubApiError("could not allocate a branch name")
            await client.put_file(cfg.owner, cfg.repo, draft.path,
                                  content=draft.new_content, message=draft.title,
                                  branch=branch, sha=draft.base_sha)
            body = (f"{draft.body}\n\n---\nRaised via SubstrateOS by {run.requester_name} "
                    f"from {run.surface} · run {run.id}.")
            pr_url = await client.create_pr(cfg.owner, cfg.repo, title=draft.title,
                                            body=body, head=branch, base=cfg.base_branch)
        except GithubAuthError:
            await self._store.add_event(run.id, step="Token rejected",
                                        detail="GitHub rejected the user's token — reconnect needed",
                                        actor="SubstrateOS")
            return ActionResult(ok=False, status=run.status,
                                message="GitHub rejected your token — reconnect and try again.")
        except GithubApiError as e:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="PR failed", detail=str(e)[:200],
                                        actor="SubstrateOS")
            return ActionResult(ok=False, status="error",
                                message="GitHub refused the change — the run is recorded; "
                                        "check the repo settings.")
        run.status = "completed"
        run.pr_url = pr_url
        await self._store.save(run)
        await self._store.add_event(run.id, step="Confirmed",
                                    detail=f"{actor_name} confirmed the drafted change", actor=actor_name)
        await self._store.add_event(run.id, step="PR created",
                                    detail=f"{pr_url} · branch {branch} · authored as the requester",
                                    actor="SubstrateOS")
        return ActionResult(ok=True, status="completed", pr_url=pr_url)

    async def cancel(self, run_id: str, *, actor_email: str | None,
                     actor_name: str) -> ActionResult:
        run, err = await self._guarded_run(run_id, actor_email)
        if err is not None:
            return err
        run.status = "cancelled"
        await self._store.save(run)
        await self._store.add_event(run.id, step="Cancelled",
                                    detail=f"{actor_name} cancelled — nothing reached GitHub",
                                    actor=actor_name)
        return ActionResult(ok=True, status="cancelled")
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_flow.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(github): GithubFlow — preview-confirm raise-PR playbook"`

### Task 9: API — OAuth endpoints, run-action endpoint, wiring

**Files:**
- Create: `substrateos-api/app/api/github.py`
- Modify: `substrateos-api/app/deps.py`
- Modify: `substrateos-api/app/main.py`
- Test: `substrateos-api/tests/test_github_api.py`

- [ ] **Step 1: Write the failing test** (small FastAPI app + dependency overrides, like `tests/test_surfaces_api.py`)

```python
"""GitHub OAuth + run-action endpoints."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.api.github as github_api
from app.api.github import router
from app.connectors.github_store import GithubStore
from app.connectors.models import GithubConfig
from app.deps import get_github_flow, get_github_store
from app.workflows.github_pr import ActionResult


def _app(store: GithubStore, flow=None) -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    a.dependency_overrides[get_github_store] = lambda: store
    a.dependency_overrides[get_github_flow] = lambda: flow
    return a


def _client(a: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=a), base_url="http://t")


@pytest.mark.asyncio
async def test_start_redirects_to_github_for_valid_state(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    store = GithubStore(client=None, force_memory=True)
    state = await store.mint_connect_state("t-test", "tom@x")
    async with _client(_app(store)) as c:
        r = await c.get(f"/auth/github/start?s={state}")
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://github.com/login/oauth/authorize")
    assert "client_id=cid" in loc and f"state={state}" in loc and "scope=repo" in loc


@pytest.mark.asyncio
async def test_start_unknown_state_404():
    store = GithubStore(client=None, force_memory=True)
    async with _client(_app(store)) as c:
        r = await c.get("/auth/github/start?s=bogus")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_callback_exchanges_and_stores_token(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    store = GithubStore(client=None, force_memory=True)
    state = await store.mint_connect_state("t-test", "tom@x")

    async def fake_exchange(**kw):
        assert kw["code"] == "c0de"
        return "gho_new"
    monkeypatch.setattr(github_api, "exchange_code", fake_exchange)

    async with _client(_app(store)) as c:
        r = await c.get(f"/auth/github/callback?code=c0de&state={state}")
    assert r.status_code == 200 and "Connected" in r.text
    assert await store.get_user_token("t-test", "tom@x") == "gho_new"
    # state is one-shot:
    async with _client(_app(store)) as c:
        r2 = await c.get(f"/auth/github/callback?code=c0de&state={state}")
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_run_action_routes_to_flow(monkeypatch):
    class _Flow:
        async def confirm(self, run_id, *, actor_email, actor_name):
            assert run_id == "RB-9" and actor_email == "u-demo@substrateos"
            return ActionResult(ok=True, status="completed",
                                pr_url="https://github.com/o/r/pull/3")
        async def cancel(self, run_id, *, actor_email, actor_name):
            return ActionResult(ok=True, status="cancelled")

    store = GithubStore(client=None, force_memory=True)
    async with _client(_app(store, flow=_Flow())) as c:
        r = await c.post("/workflows/runs/RB-9/action", json={"action": "create"},
                         headers={"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["pr_url"].endswith("/pull/3")
```

Check how `tests/test_surfaces_api.py` provides debug auth (`x-debug-bypass-auth` requires `enable_debug_auth`) — mirror its monkeypatch/env arrangement exactly for the run-action test, and mirror the email shape `resolve_user` produces for the debug principal (adjust the asserted `actor_email` to match; read `app/api/_auth_resolve.py` first).

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_api.py -q` — Expected: ImportError.

- [ ] **Step 3: Implement** `app/api/github.py`:

```python
"""GitHub tool endpoints: per-user OAuth (start/callback) and the surface-agnostic
run action endpoint the web chat (and tests) use for Create PR / Cancel."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.connectors.github import GITHUB_OAUTH_AUTHORIZE, exchange_code
from app.deps import get_github_flow, get_github_store, get_token_store

router = APIRouter(tags=["github"])

_PAGE = """<!doctype html><html><head><title>SubstrateOS · GitHub</title>
<style>body{{font-family:system-ui;display:grid;place-items:center;height:100vh;margin:0;
background:#faf6ef;color:#1d1d1b}}div{{text-align:center;max-width:28rem}}</style></head>
<body><div><h2>{title}</h2><p>{body}</p></div></body></html>"""


@router.get("/auth/github/start")
async def github_oauth_start(s: str, github_store=Depends(get_github_store)):
    if await github_store.peek_connect_state(s) is None:
        raise HTTPException(status_code=404, detail="unknown or expired connect link")
    cfg = get_settings()
    params = urlencode({
        "client_id": cfg.github_client_id or "",
        "redirect_uri": f"{cfg.substrateos_api_base_url}/auth/github/callback",
        "scope": "repo",
        "state": s,
    })
    return RedirectResponse(f"{GITHUB_OAUTH_AUTHORIZE}?{params}")


@router.get("/auth/github/callback")
async def github_oauth_callback(code: str = "", state: str = "",
                                github_store=Depends(get_github_store)) -> HTMLResponse:
    consumed = await github_store.consume_connect_state(state)
    if consumed is None or not code:
        return HTMLResponse(_PAGE.format(
            title="Link expired", body="This connect link was already used or has expired — "
            "ask SubstrateOS for a PR again to get a fresh one."), status_code=400)
    tenant, email = consumed
    s = get_settings()
    token = await exchange_code(client_id=s.github_client_id or "",
                                client_secret=s.github_client_secret or "", code=code)
    if not token:
        return HTMLResponse(_PAGE.format(
            title="GitHub sign-in failed", body="GitHub didn't accept the sign-in — "
            "try again from chat."), status_code=400)
    await github_store.put_user_token(tenant, email, token)
    return HTMLResponse(_PAGE.format(
        title="GitHub connected ✓",
        body="You're connected — return to chat and ask for the PR again. "
             "PRs will be authored as you."))


class RunActionRequest(BaseModel):
    action: Literal["create", "cancel"]


@router.post("/workflows/runs/{run_id}/action")
async def run_action(
    run_id: str,
    body: RunActionRequest,
    flow=Depends(get_github_flow),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> dict:
    if flow is None:
        raise HTTPException(status_code=503, detail="GitHub tool not configured")
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth, token_store=token_store,
    )
    kw = {"actor_email": user.email, "actor_name": user.display_name or user.email}
    result = await (flow.confirm(run_id, **kw) if body.action == "create"
                    else flow.cancel(run_id, **kw))
    return {"ok": result.ok, "status": result.status,
            "pr_url": result.pr_url, "message": result.message}
```

In `app/deps.py`, append:

```python
def get_github_store(request: Request):
    return getattr(request.app.state, "github_store", None)


def get_github_flow(request: Request):
    return getattr(request.app.state, "github_flow", None)
```

In `app/main.py`:
- imports: `from app.api.github import router as github_router`, `from app.connectors.github_store import GithubStore`, `from app.workflows.github_engine import PrDraftEngine`, `from app.workflows.github_pr import GithubFlow`
- after the `approval_flow` wiring:

```python
    app.state.github_store = GithubStore()
    app.state.github_flow = GithubFlow(
        store=app.state.run_store,
        github=app.state.github_store,
        connections=app.state.connection_store,
        engine=PrDraftEngine(llm=app.state.llm),
    )
```

- in the `finally:` block, alongside the other closes: `await app.state.github_store.aclose()`
- with the other routers: `app.include_router(github_router)`

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_api.py tests/test_healthz.py -q` — Expected: PASS (healthz exercises app import/wiring).
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(github): OAuth endpoints + run action endpoint + app wiring"`

### Task 10: Admin — surface registration + repo config endpoints

**Files:**
- Modify: `substrateos-api/app/api/admin.py` (`_VALID_SURFACES` + new endpoints)
- Modify: `substrateos-api/app/connectors/store.py` and `substrateos-api/app/connectors/cosmos_store.py` (`_DEFAULT_SURFACES`)
- Modify: `substrateos-api/app/api/bots.py` (`bot_status`)
- Test: `substrateos-api/tests/test_admin_github.py`

- [ ] **Step 1: Write the failing test**

```python
"""Admin endpoints for the GitHub tool (config + surface registration)."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import router
from app.connectors.github_store import GithubStore
from app.connectors.models import GithubConfig
from app.connectors.store import _DEFAULT_SURFACES
from app.deps import get_connection_store, get_github_store

ADMIN = {"x-admin-key": "k"}


class _Surfaces:
    def __init__(self): self.saved = []
    async def list_surfaces(self, tenant): return list(_DEFAULT_SURFACES)
    async def put_surface(self, tenant, surface): self.saved.append(surface)


def _app(github: GithubStore) -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    a.dependency_overrides[get_github_store] = lambda: github
    a.dependency_overrides[get_connection_store] = lambda: _Surfaces()
    return a


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "k")
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_github_in_default_surfaces():
    assert any(s.name == "github" for s in _DEFAULT_SURFACES)
    from app.connectors.cosmos_store import _DEFAULT_SURFACES as COSMOS_DEFAULTS
    assert any(s.name == "github" for s in COSMOS_DEFAULTS)


@pytest.mark.asyncio
async def test_get_config_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    github = GithubStore(client=None, force_memory=True)
    async with AsyncClient(transport=ASGITransport(app=_app(github)), base_url="http://t") as c:
        r = await c.get("/admin/github/config", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["repo_configured"] is False and body["app_configured"] is False


@pytest.mark.asyncio
async def test_put_config_roundtrip(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    github = GithubStore(client=None, force_memory=True)
    async with AsyncClient(transport=ASGITransport(app=_app(github)), base_url="http://t") as c:
        r = await c.put("/admin/github/config", headers=ADMIN,
                        json={"owner": "acme", "repo": "policies", "base_branch": "main"})
        assert r.status_code == 200
        body = r.json()
        assert body == {"owner": "acme", "repo": "policies", "base_branch": "main",
                        "app_configured": True, "repo_configured": True}
        r2 = await c.put("/admin/github/config", headers=ADMIN,
                         json={"owner": "", "repo": "x"})
        assert r2.status_code == 422 or r2.status_code == 400


@pytest.mark.asyncio
async def test_patch_surface_accepts_github(monkeypatch):
    github = GithubStore(client=None, force_memory=True)
    async with AsyncClient(transport=ASGITransport(app=_app(github)), base_url="http://t") as c:
        r = await c.patch("/admin/surfaces/github", headers=ADMIN, json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
```

Mirror the body shape the existing `PATCH /admin/surfaces/{name}` expects (read `app/api/admin.py:295-314` first and adjust the patch payload in the test to match).

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_admin_github.py -q` — Expected: FAIL.

- [ ] **Step 3: Implement**

In **both** `app/connectors/store.py` and `app/connectors/cosmos_store.py`, extend the defaults:

```python
_DEFAULT_SURFACES: list[SurfaceConfig] = [
    SurfaceConfig(name="slack"),
    SurfaceConfig(name="teams"),
    SurfaceConfig(name="github"),
    SurfaceConfig(name="web"),
    SurfaceConfig(name="api"),
    SurfaceConfig(name="mcp"),
]
```

In `app/api/admin.py`:

```python
_VALID_SURFACES = {"slack", "teams", "github", "web", "api", "mcp"}
```

Add (near the surfaces endpoints; import `get_github_store` from `app.deps` and `GithubConfig` from `app.connectors.models`):

```python
class GithubConfigBody(BaseModel):
    owner: str
    repo: str
    base_branch: str = "main"


def _github_config_response(cfg: GithubConfig | None) -> dict:
    s = get_settings()
    return {
        "owner": cfg.owner if cfg else None,
        "repo": cfg.repo if cfg else None,
        "base_branch": cfg.base_branch if cfg else "main",
        "app_configured": bool(s.github_client_id and s.github_client_secret),
        "repo_configured": cfg is not None,
    }


@router.get("/github/config")
async def get_github_config(github_store=Depends(get_github_store)) -> dict:
    tenant = get_settings().substrateos_tenant_id
    cfg = await github_store.get_config(tenant) if github_store else None
    return _github_config_response(cfg)


@router.put("/github/config")
async def put_github_config(body: GithubConfigBody,
                            github_store=Depends(get_github_store)) -> dict:
    if not body.owner.strip() or not body.repo.strip():
        raise HTTPException(status_code=400, detail="owner and repo are required")
    if github_store is None:
        raise HTTPException(status_code=503, detail="github store unavailable")
    cfg = GithubConfig(owner=body.owner.strip(), repo=body.repo.strip(),
                       base_branch=body.base_branch.strip() or "main")
    await github_store.put_config(get_settings().substrateos_tenant_id, cfg)
    return _github_config_response(cfg)
```

In `app/api/bots.py` `bot_status()`, add to the returned dict:

```python
        "github": {"configured": bool(s.github_client_id and s.github_client_secret)},
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_admin_github.py tests/test_admin_api.py tests/test_surfaces_api.py tests/test_bot_config.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(admin): github surface + /admin/github/config endpoints"`

### Task 11: Slack adapter — cards, dispatch, interactive actions

**Files:**
- Create: `substrateos-api/app/bots/github_cards.py`
- Modify: `substrateos-api/app/api/bots.py`
- Test: `substrateos-api/tests/test_github_slack.py`

- [ ] **Step 1: Write the failing test**

```python
"""Slack rendering + dispatch for the raise-PR playbook."""
import pytest

from app.bots.github_cards import cancelled_blocks, pr_created_blocks, preview_blocks
from app.domain.workflow import PrDraft

DRAFT = PrDraft(path="docs/refund-policy.md", base_sha="s", new_content="x",
                summary="window 14→30 days", title="Update refund window", body="b")


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
```

(The webhook-dispatch path is covered in `tests/test_bots_api.py` style; add there in step 3b a test that a `workflow="github"` skill routes to `github_flow.start` — follow how that file stubs `skill_router` and flows for refund/approval, asserting a stub `GithubFlow.start` was awaited.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_slack.py -q` — Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement** `app/bots/github_cards.py` (Slack builders + the Teams Adaptive Card builder used in Task 12):

```python
"""Cards for the raise-PR playbook: Slack blocks (colored left-bar, same shape
as approval_cards) + the Teams Adaptive Card preview."""

from __future__ import annotations

from app.domain.workflow import PrDraft

_AMBER = "#c8860d"
_GREEN = "#2f8f5b"
_RED = "#c8546a"


def _bar(color: str, blocks: list[dict]) -> dict:
    return {"color": color, "blocks": blocks}


# ── Slack ──────────────────────────────────────────────────────────────────────

def preview_blocks(*, draft: PrDraft, repo_label: str, run_id: str) -> dict:
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "PR drafted — confirm to create"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{draft.title}*\n{draft.summary}"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"`{repo_label}` · `{draft.path}`"}},
        ],
        "attachments": [_bar(_AMBER, [
            {"type": "context", "elements": [{"type": "mrkdwn",
                "text": f":lock: nothing reaches GitHub until you confirm — the PR will be authored as you · run {run_id}"}]},
            {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "github_create",
                 "value": run_id, "text": {"type": "plain_text", "text": "Create PR"}},
                {"type": "button", "style": "danger", "action_id": "github_cancel",
                 "value": run_id, "text": {"type": "plain_text", "text": "Cancel"}},
            ]},
        ])],
    }


def pr_created_blocks(*, pr_url: str, title: str, actor_name: str) -> dict:
    return {"attachments": [_bar(_GREEN, [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f":white_check_mark: *PR created* — <{pr_url}|{title}>\nConfirmed by {actor_name}; authored as them on GitHub."}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: recorded in the audit log"}]},
    ])]}


def cancelled_blocks(*, title: str, actor_name: str) -> dict:
    return {"attachments": [_bar(_RED, [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f":x: *Cancelled by {actor_name}* — _{title}_\nNothing reached GitHub."}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": ":lock: recorded with the decision"}]},
    ])]}


# ── Teams (Adaptive Card) ──────────────────────────────────────────────────────

def teams_preview_activity(*, draft: PrDraft, repo_label: str, run_id: str) -> dict:
    card = {
        "type": "AdaptiveCard", "version": "1.5",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": [
            {"type": "TextBlock", "weight": "Bolder", "size": "Medium",
             "text": "PR drafted — confirm to create"},
            {"type": "TextBlock", "wrap": True, "text": f"**{draft.title}** — {draft.summary}"},
            {"type": "TextBlock", "wrap": True, "isSubtle": True, "spacing": "Small",
             "text": f"{repo_label} · {draft.path}"},
            {"type": "TextBlock", "wrap": True, "isSubtle": True, "size": "Small",
             "text": f"🔒 Nothing reaches GitHub until you confirm — the PR will be authored as you · run {run_id}"},
        ],
        "actions": [
            {"type": "Action.Submit", "title": "Create PR",
             "data": {"action": "github_create", "run_id": run_id}},
            {"type": "Action.Submit", "title": "Cancel",
             "data": {"action": "github_cancel", "run_id": run_id}},
        ],
    }
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}
```

- [ ] **Step 3b: Wire the Slack webhook + interactivity** in `app/api/bots.py`:

Add imports: `get_github_flow` and `get_github_store` to the `app.deps` import; `slack_call` to the `app.bots.slack` import; `from app.bots.github_cards import cancelled_blocks, pr_created_blocks, preview_blocks`.

Module-level helper (near `_bot_user`):

```python
async def _slack_profile(token: str, slack_user_id: str | None) -> tuple[str, str | None]:
    """(display_name, email) via users.info — degrades to ('A teammate', None)."""
    if not slack_user_id:
        return "A teammate", None
    body = await slack_call(token, "users.info", {"user": slack_user_id})
    u = (body or {}).get("user") or {}
    profile = u.get("profile") or {}
    name = profile.get("display_name") or u.get("real_name") or u.get("name") or "A teammate"
    return name, profile.get("email")
```

In `slack_webhook`, add `github_flow=Depends(get_github_flow)` to the signature, and inside `_reply()` after the `approval` branch:

```python
        if workflow == "github" and github_flow is not None:
            try:
                name, email = await _slack_profile(slack_token, slack_user)
                result = await github_flow.start(
                    skill_ctx.clean_query, requester_name=name, requester_email=email,
                    surface="slack", channel=channel, thread_ts=thread_ts,
                )
                if result.status == "preview":
                    repo = "the configured repo"
                    d = result.run.pr_draft
                    await slack_call(slack_token, "chat.postMessage", {
                        "channel": channel, "thread_ts": thread_ts,
                        "text": f"PR drafted: {d.title}",
                        **preview_blocks(draft=d, repo_label=repo, run_id=result.run.id),
                    })
                else:
                    await post_slack_reply(
                        slack_token, channel, thread_ts,
                        Answer(text=result.message or _ERROR_TEXT, citations=[],
                               query_id="github"))
            except Exception:
                logger.exception("GitHub workflow failed")
                await post_slack_reply(slack_token, channel, thread_ts,
                                       Answer(text=_ERROR_TEXT, citations=[], query_id="err"))
            return
```

(For the `repo` label, fetch via `github_store` if cheap — add `github_store=Depends(get_github_store)` and `cfg = await github_store.get_config(get_settings().substrateos_tenant_id)`; use `f"{cfg.owner}/{cfg.repo}"` when present.)

In `slack_interactive`, add `github_flow=Depends(get_github_flow)` and a dispatch branch **before** the refund fallback:

```python
    if action_id.startswith("github_") and github_flow is not None:
        background_tasks.add_task(_github_slack_action, payload, github_flow,
                                  s.slack_bot_token or "")
```

And the module-level handler:

```python
async def _github_slack_action(payload: dict, flow, token: str) -> None:
    actions = payload.get("actions") or []
    if not actions:
        return
    action_id = actions[0].get("action_id", "")
    run_id = actions[0].get("value") or ""
    actor_name, actor_email = await _slack_profile(token, (payload.get("user") or {}).get("id"))
    if action_id == "github_create":
        result = await flow.confirm(run_id, actor_email=actor_email, actor_name=actor_name)
    elif action_id == "github_cancel":
        result = await flow.cancel(run_id, actor_email=actor_email, actor_name=actor_name)
    else:
        return
    container = payload.get("container") or {}
    ch, ts = container.get("channel_id"), container.get("message_ts")
    if not (ch and ts):
        return
    if result.ok and result.pr_url:
        await slack_call(token, "chat.update", {
            "channel": ch, "ts": ts, "text": "PR created",
            **pr_created_blocks(pr_url=result.pr_url, title="PR created",
                                actor_name=actor_name)})
    elif result.ok:
        await slack_call(token, "chat.update", {
            "channel": ch, "ts": ts, "text": "Cancelled",
            **cancelled_blocks(title="PR draft", actor_name=actor_name)})
    else:
        await slack_call(token, "chat.postMessage", {
            "channel": ch, "thread_ts": ts, "text": result.message or "Action failed."})
```

(Improve the update titles by loading the run from the flow's store if desired — `flow._store.get(run_id)` is private; acceptable to pass the title through `ActionResult.message` instead. Keep it simple: use the literal strings above.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_slack.py tests/test_bots_api.py tests/test_bots.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(bots): Slack raise-PR cards + dispatch + interactive confirm"`

### Task 12: Teams adapter — member email, Action.Submit, skill routing

**Files:**
- Modify: `substrateos-api/app/bots/teams.py`
- Modify: `substrateos-api/app/api/bots.py` (teams webhook)
- Test: `substrateos-api/tests/test_github_teams.py`

- [ ] **Step 1: Write the failing test**

```python
"""Teams plumbing for the raise-PR playbook."""
import pytest
import respx
from httpx import Response

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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_teams.py -q` — Expected: ImportError on `get_teams_member_email` (the card builder landed in Task 11).

- [ ] **Step 3: Implement**

In `app/bots/teams.py`, after `send_teams_activity`:

```python
async def get_teams_member_email(*, incoming: dict, app_id: str, app_password: str,
                                 tenant_id: str | None = None) -> str | None:
    """The sender's email/UPN via the Connector members API — identity for the
    per-user GitHub token. None on any failure (the flow then explains itself)."""
    service_url = (incoming.get("serviceUrl") or "").rstrip("/")
    conv_id = (incoming.get("conversation") or {}).get("id") or ""
    user_id = (incoming.get("from") or {}).get("id") or ""
    if not (service_url and conv_id and user_id):
        return None
    try:
        token = await _connector_token(app_id, app_password, tenant_id=tenant_id)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{service_url}/v3/conversations/{quote(conv_id, safe='')}/members/{quote(user_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"}, timeout=10.0,
            )
        if resp.status_code >= 300:
            return None
        d = resp.json()
        return d.get("email") or d.get("userPrincipalName")
    except Exception:  # noqa: BLE001
        return None
```

In `app/api/bots.py` `teams_webhook`: add `skill_router=Depends(get_skill_router_svc)` and `github_flow=Depends(get_github_flow)` to the signature; import `get_teams_member_email` and `teams_preview_activity`.

**(a) Button submits** — insert right after the `conversationUpdate` block, before the `type != "message"` check:

```python
    # Adaptive Card Action.Submit arrives as a message with `value` and no text.
    value = body.get("value")
    if (body.get("type") == "message" and isinstance(value, dict)
            and str(value.get("action", "")).startswith("github_") and github_flow is not None):
        email = await get_teams_member_email(
            incoming=body, app_id=s.teams_bot_app_id,
            app_password=s.teams_bot_app_password, tenant_id=s.teams_bot_tenant_id)
        actor = (body.get("from") or {}).get("name") or email or "Someone"
        run_id = str(value.get("run_id") or "")
        if value["action"] == "github_create":
            result = await github_flow.confirm(run_id, actor_email=email, actor_name=actor)
            text = (f"✅ PR created — {result.pr_url}" if result.ok
                    else f"⚠ {result.message}")
        else:
            result = await github_flow.cancel(run_id, actor_email=email, actor_name=actor)
            text = ("✕ Cancelled — nothing reached GitHub." if result.ok
                    else f"⚠ {result.message}")
        await send_teams_activity(
            incoming=body, activity={"type": "message", "text": text},
            app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
            tenant_id=s.teams_bot_tenant_id)
        return {}
```

**(b) Start path** — after the smalltalk block and before the ack, resolve the skill and divert:

```python
    skill_ctx = None
    if skill_router is not None:
        with contextlib.suppress(Exception):
            skill_ctx = await skill_router.resolve_skill(text)
    if getattr(skill_ctx, "workflow", None) == "github" and github_flow is not None:
        email = await get_teams_member_email(
            incoming=body, app_id=s.teams_bot_app_id,
            app_password=s.teams_bot_app_password, tenant_id=s.teams_bot_tenant_id)
        name = (body.get("from") or {}).get("name") or "A teammate"
        result = await github_flow.start(skill_ctx.clean_query, requester_name=name,
                                         requester_email=email, surface="teams")
        if result.status == "preview":
            activity = teams_preview_activity(
                draft=result.run.pr_draft, repo_label="the configured repo",
                run_id=result.run.id)
        else:
            activity = {"type": "message", "text": result.message or _ERROR_TEXT}
        await send_teams_activity(
            incoming=body, activity=activity,
            app_id=s.teams_bot_app_id, app_password=s.teams_bot_app_password,
            tenant_id=s.teams_bot_tenant_id)
        return {}
```

(The plain-RAG path continues to ignore `skill_ctx` on Teams — unchanged behavior for non-github skills.)

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_teams.py tests/test_bots_api.py tests/test_bots.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(bots): Teams raise-PR — member email, Adaptive Card preview, Action.Submit"`

### Task 13: Web divert — Answer.pending_action + /query branch

**Files:**
- Modify: `substrateos-api/app/domain/query.py`
- Modify: `substrateos-api/app/api/query.py`
- Test: `substrateos-api/tests/test_github_web.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_github_web.py -q` — Expected: ImportError.

- [ ] **Step 3: Implement**

`app/domain/query.py` — on `Answer`:

```python
class Answer(BaseModel):
    text: str
    citations: list[Citation]
    query_id: str
    skill_used: dict | None = None
    pending_action: dict | None = None  # e.g. github_pr preview awaiting confirm
    debug: dict | None = None
```

`app/api/query.py` — add a module-level builder (import `StartResult` lazily or via `from app.workflows.github_pr import StartResult` under TYPE_CHECKING; plain import is fine):

```python
def github_answer(result, *, repo_label: str | None) -> Answer:
    """Render a GithubFlow StartResult as a web Answer."""
    if result.status == "preview":
        d = result.run.pr_draft
        return Answer(
            text="Here's the change I drafted — review and confirm before anything touches GitHub.",
            citations=[], query_id=f"github-{result.run.id}",
            pending_action={
                "type": "github_pr", "run_id": result.run.id, "title": d.title,
                "summary": d.summary, "path": d.path, "repo": repo_label,
            })
    if result.status == "connect":
        return Answer(text=result.message, citations=[], query_id="github-connect",
                      pending_action={"type": "github_connect",
                                      "connect_url": result.connect_url})
    return Answer(text=result.message or "I couldn't action that.",
                  citations=[], query_id=f"github-{result.status}")
```

In the `query` endpoint, right after the skill resolution / `effective_body` block and **before** `memory.load_history`:

```python
    if getattr(skill_ctx, "workflow", None) == "github":
        github_flow = getattr(request.app.state, "github_flow", None)
        github_store = getattr(request.app.state, "github_store", None)
        if github_flow is not None:
            result = await github_flow.start(
                effective_body.query, requester_name=user.display_name or user.email or "You",
                requester_email=user.email, surface="web")
            repo_label = None
            if github_store is not None:
                cfg = await github_store.get_config(user.tenant_id)
                if cfg:
                    repo_label = f"{cfg.owner}/{cfg.repo}"
            return github_answer(result, repo_label=repo_label)
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_github_web.py tests/test_answer_debug.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(query): web raise-PR divert — Answer.pending_action"`

### Task 14: Seed the raise-pr skill

**Files:**
- Create: `substrateos-api/scripts/seed_github_skill.py`

- [ ] **Step 1: Write the script** (same upsert-over-admin-API shape as `scripts/seed_refund_demo.py` — read its `main()`/client setup and mirror it):

```python
"""Create/refresh the `raise-pr` workflow skill so the router can route
"raise a PR …" requests to the GitHub playbook. Idempotent (upserts by slug).

Usage: uv run python scripts/seed_github_skill.py  (API + ADMIN_API_KEY via env,
same conventions as seed_refund_demo.py)."""

SKILL = {
    "slug": "raise-pr",
    "name": "Raise a PR",
    "description": (
        "Raise an AI-drafted pull request against the connected GitHub repository. "
        "Use when someone asks to open, raise, or create a PR, or to propose/apply a "
        "change to a doc, policy, or file in the repo."
    ),
    "team": "Platform",
    "run_scope": "org",
    "workflow": "github",
    "enabled": True,
    "steps": [
        "Find the target file in the repo",
        "Draft the change (grounded in the current file)",
        "Preview to the requester — Create PR / Cancel",
        "Create branch + commit + PR as the requester",
        "Record every step in the run log",
    ],
    "data_feeds": ["GitHub"],
    "system_prompt": "You are the raise-PR playbook.",
}
```

Then copy the upsert loop from `seed_refund_demo.py` (GET `/admin/skills`, PATCH if slug exists else POST), substituting `SKILL`.

- [ ] **Step 2: Verify it runs against a local API** (manual; requires the API up): `uv run python scripts/seed_github_skill.py` — Expected output: `created skill raise-pr (id=…)` (or `updated …` on re-run). If no local API is running, verify syntax with `uv run python -c "import scripts.seed_github_skill"` and defer the live run to the demo setup.
- [ ] **Step 3: Commit** — `git add -A && git commit -m "chore(scripts): seed raise-pr workflow skill"`

### Task 15: Backend gate — full suite

- [ ] **Step 1:** `cd substrateos-api && uv run pytest tests/ -q` — Expected: ALL PASS. Fix anything that broke (likely suspects: tests asserting the 5-surface default list, `bot_status` shape).
- [ ] **Step 2:** Commit any fixes — `git commit -am "test: green backend suite for github tool"`

---

## Phase C — Frontend (GATED on Phase A approval)

### Task 16: adminApi — GitHub config client

**Files:**
- Modify: `web/lib/adminApi.ts`

- [ ] **Step 1: Add types + calls** (after the `BotStatus` block; also extend `BotStatus`):

```ts
export type BotStatus = {
  teams: { configured: boolean; app_id: string | null };
  slack: { configured: boolean };
  github: { configured: boolean };
};

export type GithubConfig = {
  owner: string | null; repo: string | null; base_branch: string;
  app_configured: boolean; repo_configured: boolean;
};
export const getGithubConfig = () => call<GithubConfig>("/admin/github/config");
export const putGithubConfig = (owner: string, repo: string, base_branch: string) =>
  call<GithubConfig>("/admin/github/config", {
    method: "PUT",
    body: JSON.stringify({ owner, repo, base_branch }),
  });
```

(Match the existing `call<…>` helper's exact body/headers conventions — see `patchSurface` at `web/lib/adminApi.ts:107`.)

- [ ] **Step 2:** `cd web && pnpm typecheck` — Expected: PASS.
- [ ] **Step 3: Commit** — `git add -A && git commit -m "feat(web): adminApi github config client"`

### Task 17: Surfaces screen — GitHub card + setup modal

**Files:**
- Modify: `web/app/admin/surfaces/page.tsx`
- Modify: `web/app/globals.css`

Implement exactly what the **approved** Task 1 mockup shows.

- [ ] **Step 1: Add the meta entry** to `SURFACES` after `teams`:

```ts
  {
    name: "github", label: "GitHub", tag: "Tool", logoClass: "sl-github",
    desc: "Action connector — where SubstrateOS acts. Users raise AI-drafted pull requests to your configured repo from chat. Each PR is authored by the requesting user via their own GitHub login.",
    scope: "All employees", installable: true,
    blockedMsg: "GitHub tool disabled — raise-PR requests are refused.",
  },
```

- [ ] **Step 2: Add the icon** to `ICONS` (the SVG path from Task 1's mockup, `fill="currentColor"`, 20×20).

- [ ] **Step 3: Add the modal.** New component `GithubInstallModal({ onClose })` mirroring `SlackInstallModal`, with the four steps from the approved mockup, plus the repo form wired to state:

```tsx
function GithubInstallModal({ onClose, onSaved }: { onClose: () => void; onSaved: (cfg: GithubConfig) => void }) {
  const [cfg, setCfg] = useState<GithubConfig | null>(null);
  const [ownerRepo, setOwnerRepo] = useState("");
  const [base, setBase] = useState("main");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getGithubConfig().then((c) => {
      setCfg(c);
      if (c.owner && c.repo) setOwnerRepo(`${c.owner}/${c.repo}`);
      setBase(c.base_branch || "main");
    }).catch(() => {});
  }, []);

  const save = async () => {
    const [owner, repo] = ownerRepo.split("/").map((s) => s.trim());
    if (!owner || !repo) { setErr("Enter the repo as owner/repo."); return; }
    setSaving(true); setErr(null);
    try { onSaved(await putGithubConfig(owner, repo, base.trim() || "main")); }
    catch { setErr("Save failed — check the admin key / API."); }
    finally { setSaving(false); }
  };
  /* …render the modal per the approved mockup: header, info note
     (app credential vs user token), steps 1–2 (OAuth App + env vars,
     callback URL `${API_URL}/auth/github/callback`), step 3 = the form
     (ownerRepo input, base input, Save repo button showing `saving`/`err`),
     step 4 done line; footer Close + "Open GitHub OAuth Apps ↗" linking to
     https://github.com/settings/developers — copy the JSX skeleton from
     SlackInstallModal and substitute. */
}
```

- [ ] **Step 4: Wire state into the page component:**
  - Fetch `getGithubConfig()` alongside `getSurfaces()`/`getBotStatus()` in the existing `useEffect`'s `Promise.all`; hold it in `const [ghCfg, setGhCfg] = useState<GithubConfig | null>(null)`.
  - Extend the auto-heal: `if (ghCfg && ghCfg.app_configured && ghCfg.repo_configured) heal("github", `${ghCfg.owner}/${ghCfg.repo}`, true);`
  - `handleInstall` accepts `"github"` → `setInstallModal("github")`; widen the modal state type to `"teams" | "slack" | "github" | null`; render `<GithubInstallModal onClose={…} onSaved={(c) => { setGhCfg(c); }} />`.
  - In the card-render loop, `botConfigured` for github: `meta.name === "github" ? (botStatus?.github?.configured ?? false) : …`.
  - Installed footer for github shows `Installed in acme/policies` via the healed `workspace_name` — no special-casing needed beyond the heal.

- [ ] **Step 5: CSS** — in `web/app/globals.css`, next to `.sl-slack`/`.btn-slack`:

```css
.sl-github { background: #24292f; color: #fff; }
.btn-github { background: #24292f; color: #fff; }
```

- [ ] **Step 6:** `cd web && pnpm typecheck && pnpm lint && pnpm build` — Expected: PASS.
- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat(admin): GitHub tool card + setup modal on Surfaces"`

### Task 18: Web chat — PR preview card + run action

**Files:**
- Modify: `web/lib/api.ts`
- Create: `web/components/PrActionCard.tsx`
- Modify: `web/components/Chat.tsx`

- [ ] **Step 1: Types + client** in `web/lib/api.ts` (extend `Answer`, add the action call):

```ts
export type PendingAction =
  | { type: "github_pr"; run_id: string; title: string; summary: string; path: string; repo: string | null }
  | { type: "github_connect"; connect_url: string };

export type Answer = {
  query_id: string; text: string; citations: Citation[];
  skill_used?: SkillUsed | null; pending_action?: PendingAction | null;
  debug?: AnswerDebug | null;
};

export type RunActionResult = { ok: boolean; status: string; pr_url: string | null; message: string };

export async function postRunAction(runId: string, action: "create" | "cancel"): Promise<RunActionResult> {
  const resp = await authedFetch(`${API_BASE}/workflows/runs/${encodeURIComponent(runId)}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  if (!resp.ok) throw new Error(`substrateos-api ${resp.status}`);
  return (await resp.json()) as RunActionResult;
}
```

- [ ] **Step 2: Component** `web/components/PrActionCard.tsx` (implement the approved Task 2 mockup; class names below assume the mockup's `pr-*` classes are added to `globals.css` — port the approved mockup CSS):

```tsx
"use client";
import { useState } from "react";
import { PendingAction, postRunAction } from "@/lib/api";

export default function PrActionCard({ action }: { action: PendingAction }) {
  const [state, setState] = useState<"pending" | "working" | "created" | "cancelled" | "error">("pending");
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  if (action.type === "github_connect") {
    return (
      <div className="pr-card">
        <div className="pr-head"><span className="pr-title">Connect GitHub</span></div>
        <div className="pr-summary">The PR will be authored as you — connect your GitHub once, then ask again.</div>
        <div className="pr-actions">
          <a className="pr-create" href={action.connect_url} target="_blank" rel="noreferrer">Connect GitHub ↗</a>
        </div>
      </div>
    );
  }

  const act = async (kind: "create" | "cancel") => {
    setState("working"); setMsg(null);
    try {
      const r = await postRunAction(action.run_id, kind);
      if (r.ok && kind === "create") { setState("created"); setPrUrl(r.pr_url); }
      else if (r.ok) setState("cancelled");
      else { setState("error"); setMsg(r.message); }
    } catch { setState("error"); setMsg("Action failed — try again."); }
  };

  return (
    <div className="pr-card">
      <div className="pr-head">
        <span className="pr-title">{action.title}</span>
        <span className="pr-chip">
          {state === "created" ? "PR created" : state === "cancelled" ? "cancelled" : "pending your confirm"}
        </span>
      </div>
      <div className="pr-meta">{action.repo ?? "configured repo"} · <code>{action.path}</code></div>
      <div className="pr-summary">{action.summary}</div>
      {state === "created" && prUrl && (
        <div className="pr-actions"><a className="pr-create" href={prUrl} target="_blank" rel="noreferrer">View PR ↗</a></div>
      )}
      {(state === "pending" || state === "working" || state === "error") && (
        <div className="pr-actions">
          <button className="pr-create" disabled={state === "working"} onClick={() => act("create")}>
            {state === "working" ? "Working…" : "Create PR"}
          </button>
          <button className="pr-cancel" disabled={state === "working"} onClick={() => act("cancel")}>Cancel</button>
        </div>
      )}
      {msg && <div className="pr-err">{msg}</div>}
      <div className="pr-foot">🔒 Nothing reaches GitHub until you confirm — the PR will be authored as you.</div>
    </div>
  );
}
```

- [ ] **Step 3: Render it in `Chat.tsx`** — find where an assistant message's answer body + citations render and add, directly after the answer text block:

```tsx
{m.answer?.pending_action && <PrActionCard action={m.answer.pending_action} />}
```

(adjust `m.answer` to the file's actual message shape; import the component). Port the `pr-*` CSS from the approved mockup into `globals.css`.

- [ ] **Step 4:** `cd web && pnpm typecheck && pnpm lint && pnpm build` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(web): PR preview card + run-action confirm in chat"`

### Task 19: Runs page — label github_pr runs

**Files:**
- Modify: `web/app/admin/runs/page.tsx`

- [ ] **Step 1:** Extend the workflow-type label (around `web/app/admin/runs/page.tsx:148`):

```ts
const type = isApproval(r) ? "request-approval"
  : r.kind === "github_pr" ? "raise-pr playbook"
  : "refund playbook";
```

Add status chips for the new statuses where the page maps them (near line 21): `pending_confirm: { cls: "stopped", label: "Awaiting confirm" }`, `cancelled: { cls: "bad", label: "Cancelled" }` (match the page's actual chip-class vocabulary). Extend `wfTitle` so `github_pr` runs show their request text like approval runs do, and — if the `RunSummary` type in `web/lib/runsApi.ts` lacks `kind`/`pr_url` variants — extend that type accordingly.

- [ ] **Step 2:** `cd web && pnpm typecheck && pnpm lint && pnpm build` — Expected: PASS.
- [ ] **Step 3: Commit** — `git add -A && git commit -m "feat(admin): runs view labels raise-pr runs"`

---

## Phase D — Docs sync + finish

### Task 20: Sync mockups + architecture + techstack

**Files:**
- Modify: `mockups/admin-portal.html`, `mockups/user-web-chat.html` (true-up to what shipped)
- Modify: `mockups/architecture.html`
- Check: `.claude/skills/substrateos-feature/references/techstack.md`

- [ ] **Step 1:** Re-open both feature mockups and reconcile any drift introduced during implementation (copy changes, button labels, states). `open mockups/admin-portal.html mockups/user-web-chat.html`
- [ ] **Step 2:** Update `mockups/architecture.html` — **both views**:
  - Detailed: add `connectors/github.py`, `connectors/github_store.py`, `workflows/github_pr.py`, `workflows/github_engine.py`, `api/github.py`, the three surface adapters, and the OAuth flow; show GitHub under the **Do it** step ("act in your tools") with the requester-confirm Stop gate and RunStore audit.
  - High-level: add **GitHub** as the first **Tool** (distinct from Surfaces — "where requests come from" vs "where SubstrateOS acts").
  - Keep the Master-Deck palette (navy `#102444`, amber `#c8860d`). `open mockups/architecture.html` and eyeball.
- [ ] **Step 3:** techstack — no new libraries were introduced (httpx/respx already present); confirm and skip, or note the GitHub REST API usage under existing httpx if the file tracks external services.
- [ ] **Step 4: Commit** — `git add -A && git commit -m "docs: sync mockups + architecture for the GitHub tool"`

### Task 21: Final verification + merge gate

- [ ] **Step 1:** `cd substrateos-api && uv run pytest tests/ -q` — ALL PASS (paste real output).
- [ ] **Step 2:** `cd web && pnpm typecheck && pnpm lint && pnpm build` — PASS.
- [ ] **Step 3:** Working tree clean (`git status`), branch `feat/github-tool` pushed.
- [ ] **Step 4:** **Ask the user** before merging to `main` (substrateos-feature Phase 5). Deployment only via the `substrateos-deploy` skill, only with explicit approval.

---

## Self-review notes (already applied)

- Spec coverage: admin card/modal (T1/T17), env+KeyVault creds (T3), repo config endpoint (T10), per-user OAuth (T5/T6/T9), grounded draft + stop-and-ask (T7), preview/confirm + requester-only + audit (T8), Slack (T11), Teams incl. new Action.Submit handling (T12), Web pending_action + action endpoint (T13/T18/T9), runs visibility (T19), error table (GithubAuthError→reconnect T6/T8; collision retry T8; disabled tool T8; non-requester T8), security notes carried in spec, docs sync (T20).
- Type consistency: `GithubFlow(store, github, connections, engine, client_factory)`; `StartResult.status ∈ {preview, connect, clarify, blocked, error}`; `ActionResult(ok, status, pr_url, message)`; `PrDraft(path, base_sha, new_content, summary, title, body)`; statuses `pending_confirm`/`cancelled`; action ids `github_create`/`github_cancel` used identically in Slack blocks, Teams submit data, and the web `{action: "create"|"cancel"}` mapping (web maps create→confirm in the endpoint).
- Known judgment calls for the executor: exact `quote(path, safe="/")` in GithubClient (noted in T6); the debug-auth email shape in T9's run-action test (verify against `_auth_resolve.py`); the existing PATCH surfaces body shape in T10's test.
