# Entra-Driven Approval Routing + User Directory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refund approvals route to the requester's Entra manager (Managers group only); customers route to the support channel; identity comes from a daily-synced Slack+Entra user directory in Redis.

**Architecture:** New `app/directory/` module (store / sync / service) mirroring the `RunStore` Redis+memory pattern; a generic `app/scheduler.py` periodic runner started in FastAPI lifespan; `RefundFlow` branches on directory role and enforces manager-only approval on button clicks. `SLACK_REFUND_APPROVER_ID` is removed everywhere.

**Tech Stack:** FastAPI · Python 3.12 · uv · pytest (+pytest-asyncio, respx) · redis.asyncio · httpx · Microsoft Graph (client-credentials via existing `graph_token`) · Slack Web API.

**Spec:** `docs/superpowers/specs/2026-06-07-entra-approval-routing-design.md` — read it first.

**Branch:** `feat/entra-approval-routing` (already exists, spec committed).

**Run tests from** `substrateos-api/`: `uv run pytest tests/ -q`. Two pre-existing failures are known when local env sets `SUBSTRATEOS_TENANT_ID=t-eval` (test_acl_resolver default-acl + test_config settings) — ignore those two ONLY; everything else must pass.

**File map:**

| Action | Path | Responsibility |
|---|---|---|
| Modify | `substrateos-api/app/domain/workflow.py` | +2 run statuses, +`approver_slack_id` |
| Modify | `web/lib/runsApi.ts` | mirror status union |
| Create | `substrateos-api/app/domain/directory.py` | `DirectoryUser` model |
| Create | `substrateos-api/app/directory/__init__.py` | package |
| Create | `substrateos-api/app/directory/store.py` | `DirectoryStore` (Redis + memory) |
| Create | `substrateos-api/app/directory/sync.py` | `DirectorySync` (Slack+Graph merge) |
| Create | `substrateos-api/app/directory/service.py` | `DirectoryService` (resolve + live fallback) |
| Create | `substrateos-api/app/scheduler.py` | `start_periodic` |
| Modify | `substrateos-api/app/bots/slack.py` | +`slack_users_list` |
| Modify | `substrateos-api/app/bots/refund_cards.py` | +`customer_request_blocks` |
| Modify | `substrateos-api/app/workflows/flow.py` | role routing + click enforcement |
| Modify | `substrateos-api/app/workflows/approval.py` | drop env-var fallback |
| Modify | `substrateos-api/app/config.py` | +4 settings, −`slack_refund_approver_id` |
| Create | `substrateos-api/app/api/admin_directory.py` | admin sync/inspect endpoints |
| Modify | `substrateos-api/app/deps.py` | 3 new getters |
| Modify | `substrateos-api/app/main.py` | wiring + scheduler lifecycle |
| Tests | `tests/test_directory_store.py`, `tests/test_directory_sync.py`, `tests/test_directory_service.py`, `tests/test_scheduler.py`, `tests/test_slack_users_list.py`, `tests/test_admin_directory.py` | new |
| Tests | `tests/test_refund_flow.py` (rewrite), `tests/test_refund_cards.py`, `tests/test_approval_flow.py`, `tests/test_workflow_models.py` | update |

---

### Task 1: Run statuses + approver_slack_id (backend model + web mirror)

**Files:**
- Modify: `substrateos-api/app/domain/workflow.py`
- Modify: `web/lib/runsApi.ts:24`
- Test: `substrateos-api/tests/test_workflow_models.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_workflow_models.py`:

```python
def test_routing_statuses_and_approver_slack_id():
    from datetime import UTC, datetime

    from app.domain.workflow import RefundRun

    now = datetime.now(UTC)
    stopped = RefundRun(id="RB-1", requester_name="Tom", status="needs_attention",
                        approver_slack_id="U_DIANA", created_at=now, updated_at=now)
    assert stopped.status == "needs_attention"
    assert stopped.approver_slack_id == "U_DIANA"
    routed = RefundRun(id="RB-2", requester_name="Priya", status="routed_to_support",
                       created_at=now, updated_at=now)
    assert routed.status == "routed_to_support"
    assert routed.approver_slack_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd substrateos-api && uv run pytest tests/test_workflow_models.py::test_routing_statuses_and_approver_slack_id -q`
Expected: FAIL — pydantic ValidationError (literal mismatch on `status`).

- [ ] **Step 3: Implement** — in `app/domain/workflow.py`:

Replace the `RunStatus` literal:

```python
RunStatus = Literal[
    "running", "pending_approval", "pending_confirm",
    "approved", "rejected", "completed", "cancelled", "error",
    "needs_attention",    # stopped: no eligible approver / identity unknown
    "routed_to_support",  # customer request handed to the support channel
]
```

In `RefundRun`, directly under `approver_name: str | None = None`, add:

```python
    approver_slack_id: str | None = None  # the routed approver — click enforcement key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_workflow_models.py -q` — Expected: PASS.

- [ ] **Step 5: Mirror the union in the web type** — `web/lib/runsApi.ts` line 24, replace the `status:` union with:

```ts
  status: "running" | "pending_approval" | "approved" | "rejected" | "completed" | "error" | "pending_confirm" | "cancelled" | "needs_attention" | "routed_to_support";
```

Run: `cd web && pnpm typecheck` — Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add substrateos-api/app/domain/workflow.py substrateos-api/tests/test_workflow_models.py web/lib/runsApi.ts
git commit -m "feat(workflows): needs_attention + routed_to_support statuses, approver_slack_id"
```

---

### Task 2: DirectoryUser model + DirectoryStore

**Files:**
- Create: `substrateos-api/app/domain/directory.py`
- Create: `substrateos-api/app/directory/__init__.py` (empty)
- Create: `substrateos-api/app/directory/store.py`
- Test: `substrateos-api/tests/test_directory_store.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_directory_store.py`:

```python
"""DirectoryStore: Redis-backed (memory fallback) email↔Slack-id↔role records."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser


def _tom() -> DirectoryUser:
    return DirectoryUser(
        email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
        entra_id="guid-tom", manager_email="diane@x",
        groups=["Support Agent"], role="agent", synced_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_upsert_and_get_by_email_roundtrip():
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(_tom())
    got = await store.get_by_email("tom@x")
    assert got is not None
    assert got.slack_id == "U_TOM" and got.role == "agent"
    assert got.manager_email == "diane@x"


@pytest.mark.asyncio
async def test_email_lookup_is_case_insensitive():
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(_tom())
    assert (await store.get_by_email("TOM@X")) is not None


@pytest.mark.asyncio
async def test_get_by_slack_id_reverse_index():
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(_tom())
    got = await store.get_by_slack_id("U_TOM")
    assert got is not None and got.email == "tom@x"
    assert (await store.get_by_slack_id("U_NOBODY")) is None


@pytest.mark.asyncio
async def test_upsert_overwrites_and_list_all():
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(_tom())
    promoted = _tom().model_copy(update={"role": "manager", "groups": ["Managers"]})
    await store.upsert(promoted)
    users = await store.list_all()
    assert len(users) == 1
    assert users[0].role == "manager"


@pytest.mark.asyncio
async def test_missing_email_returns_none():
    store = DirectoryStore(client=None, force_memory=True)
    assert (await store.get_by_email("ghost@x")) is None
    assert (await store.get_by_email(None)) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_directory_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.directory'`.

- [ ] **Step 3: Implement.**

`app/domain/directory.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DirectoryRole = Literal["manager", "agent", "customer"]


class DirectoryUser(BaseModel):
    """One person in the synced user directory — the email-keyed join of a
    Slack member and an Entra ID user, carrying the role playbooks route by."""

    email: str
    slack_id: str | None = None
    display_name: str | None = None
    entra_id: str | None = None
    manager_email: str | None = None
    groups: list[str] = []
    role: DirectoryRole = "customer"
    synced_at: datetime | None = None
```

`app/directory/__init__.py`: empty file.

`app/directory/store.py`:

```python
from __future__ import annotations

import contextlib
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.directory import DirectoryUser

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)

_EMAILS_KEY = "directory:emails"  # SET of every known (lowercase) email


def _user_key(email: str) -> str:
    return f"directory:user:{email}"


def _slack_key(slack_id: str) -> str:
    return f"directory:slack:{slack_id}"


class DirectoryStore:
    """Redis-backed user directory with an in-process mirror (RunStore pattern):
    routing keeps working within a single process when Redis is unavailable."""

    def __init__(self, client: redis.Redis | None = None, *, force_memory: bool = False) -> None:
        self._mem_users: dict[str, str] = {}
        self._mem_slack: dict[str, str] = {}
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

    async def upsert(self, user: DirectoryUser) -> None:
        email = user.email.lower()
        blob = user.model_dump_json()
        self._mem_users[email] = blob
        if user.slack_id:
            self._mem_slack[user.slack_id] = email
        if self._r is None:
            return
        try:
            await self._r.set(_user_key(email), blob)
            await self._r.sadd(_EMAILS_KEY, email)
            if user.slack_id:
                await self._r.set(_slack_key(user.slack_id), email)
        except _ERRORS as e:
            logger.warning("DirectoryStore.upsert redis failed: %s", e)

    async def get_by_email(self, email: str | None) -> DirectoryUser | None:
        if not email:
            return None
        email = email.lower()
        raw: str | None = None
        if self._r is not None:
            try:
                raw = await self._r.get(_user_key(email))
            except _ERRORS as e:
                logger.warning("DirectoryStore.get_by_email redis failed: %s", e)
        raw = raw or self._mem_users.get(email)
        if not raw:
            return None
        with contextlib.suppress(Exception):
            return DirectoryUser.model_validate_json(raw)
        return None

    async def get_by_slack_id(self, slack_id: str | None) -> DirectoryUser | None:
        if not slack_id:
            return None
        email: str | None = None
        if self._r is not None:
            try:
                email = await self._r.get(_slack_key(slack_id))
            except _ERRORS as e:
                logger.warning("DirectoryStore.get_by_slack_id redis failed: %s", e)
        email = email or self._mem_slack.get(slack_id)
        return await self.get_by_email(email) if email else None

    async def list_all(self) -> list[DirectoryUser]:
        emails: list[str] = []
        if self._r is not None:
            try:
                emails = sorted(await self._r.smembers(_EMAILS_KEY))
            except _ERRORS as e:
                logger.warning("DirectoryStore.list_all redis failed: %s", e)
        if not emails:
            emails = sorted(self._mem_users)
        users = [u for e in emails if (u := await self.get_by_email(e)) is not None]
        return users
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_directory_store.py -q` — Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/domain/directory.py substrateos-api/app/directory/ substrateos-api/tests/test_directory_store.py
git commit -m "feat(directory): DirectoryUser model + Redis-backed DirectoryStore"
```

---

### Task 3: `slack_users_list` wrapper

**Files:**
- Modify: `substrateos-api/app/bots/slack.py` (append at end)
- Test: `substrateos-api/tests/test_slack_users_list.py`

`users.list` is one of the Slack methods that does NOT accept a JSON POST body
(unlike the methods `slack_call` serves) — use GET with query params. Returns
`None` on failure so the sync can abort instead of wiping good data with a
partial page.

- [ ] **Step 1: Write the failing tests** — `tests/test_slack_users_list.py`:

```python
"""slack_users_list: paginated GET wrapper over Slack users.list."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.bots.slack import slack_users_list

_URL = "https://slack.com/api/users.list"


@pytest.mark.asyncio
@respx.mock
async def test_paginates_until_cursor_empty():
    page1 = {"ok": True, "members": [{"id": "U1"}, {"id": "U2"}],
             "response_metadata": {"next_cursor": "abc"}}
    page2 = {"ok": True, "members": [{"id": "U3"}],
             "response_metadata": {"next_cursor": ""}}
    route = respx.get(_URL).mock(side_effect=[Response(200, json=page1),
                                              Response(200, json=page2)])
    members = await slack_users_list("xoxb-test")
    assert [m["id"] for m in members] == ["U1", "U2", "U3"]
    assert route.call_count == 2
    # second call carries the cursor
    assert route.calls[1].request.url.params["cursor"] == "abc"


@pytest.mark.asyncio
@respx.mock
async def test_api_error_returns_none():
    respx.get(_URL).mock(return_value=Response(200, json={"ok": False, "error": "invalid_auth"}))
    assert (await slack_users_list("xoxb-bad")) is None


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_returns_none():
    respx.get(_URL).mock(side_effect=ConnectionError)
    assert (await slack_users_list("xoxb-test")) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_slack_users_list.py -q`
Expected: FAIL — `ImportError: cannot import name 'slack_users_list'`.

- [ ] **Step 3: Implement** — append to `app/bots/slack.py`:

```python
async def slack_users_list(token: str) -> list[dict] | None:
    """Fetch every workspace member via paginated users.list.

    users.list does not accept a JSON POST body (unlike the methods slack_call
    serves) — it's a GET with query params. Returns None on ANY failure so the
    directory sync can keep its previous data instead of merging a partial page.
    """
    members: list[dict] = []
    cursor = ""
    try:
        async with httpx.AsyncClient() as client:
            while True:
                params: dict = {"limit": 200}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get(
                    "https://slack.com/api/users.list",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params, timeout=10.0,
                )
                body = resp.json()
                if not body.get("ok"):
                    logger.warning("Slack users.list failed: %s",
                                   body.get("error", "unknown_error"))
                    return None
                members.extend(body.get("members") or [])
                cursor = ((body.get("response_metadata") or {}).get("next_cursor") or "")
                if not cursor:
                    return members
    except Exception:  # noqa: BLE001
        logger.exception("Slack users.list request failed")
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_slack_users_list.py -q` — Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/bots/slack.py substrateos-api/tests/test_slack_users_list.py
git commit -m "feat(slack): paginated users.list wrapper for the directory sync"
```

---

### Task 4: New config settings (additive only)

`slack_refund_approver_id` is removed later (Task 8/9 must stop using it first).

**Files:**
- Modify: `substrateos-api/app/config.py`

- [ ] **Step 1: Add settings** — in `app/config.py`, directly under the
`slack_refund_approver_id` line (106), add:

```python
    # User directory + Entra-driven approval routing
    entra_managers_group: str = "Managers"        # Entra group → role "manager"
    entra_agents_group: str = "Support Agent"     # Entra group → role "agent"
    slack_refund_channel_id: str | None = None    # SLACK_REFUND_CHANNEL_ID — customer requests land here
    directory_sync_interval_hours: float = 24.0   # daily Slack+Entra directory sync
```

- [ ] **Step 2: Verify nothing broke**

Run: `uv run pytest tests/test_config.py -q` — Expected: same pass/fail as before this task (the known t-eval failure only, if running with local .env).

- [ ] **Step 3: Commit**

```bash
git add substrateos-api/app/config.py
git commit -m "feat(config): directory + routing settings (groups, channel, sync interval)"
```

---

### Task 5: DirectorySync

**Files:**
- Create: `substrateos-api/app/directory/sync.py`
- Test: `substrateos-api/tests/test_directory_sync.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_directory_sync.py`:

```python
"""DirectorySync: Slack users.list + Graph users/groups → merged directory."""

from __future__ import annotations

import pytest

from app.directory.store import DirectoryStore
from app.directory.sync import DirectorySync

_GRAPH = "https://graph.microsoft.com/v1.0"

_SLACK_MEMBERS = [
    {"id": "USLACKBOT", "profile": {"email": ""}},
    {"id": "U_BOT", "is_bot": True, "profile": {"email": "bot@x"}},
    {"id": "U_GONE", "deleted": True, "profile": {"email": "gone@x"}},
    {"id": "U_TOM", "profile": {"email": "Tom@X", "real_name": "Tom Reyes"}},
    {"id": "U_DIANE", "profile": {"email": "diane@x", "real_name": "Diane Foster"}},
    {"id": "U_PRIYA", "profile": {"email": "priya@x", "real_name": "Priya Sharma"}},
]

_GRAPH_USERS = {"value": [
    {"id": "g-tom", "displayName": "Tom", "mail": "tom@x",
     "manager": {"mail": "Diane@X"}},
    {"id": "g-diane", "displayName": "Diane", "mail": "diane@x"},
    {"id": "g-manoj", "displayName": "Manoj", "mail": "manoj@x"},  # Entra-only
]}


def _graph_fake(group_pages: dict[str, dict]):
    """graph_get_json fake keyed by substring of the URL."""
    async def get(token: str, url: str) -> dict:
        if "/users?" in url:
            return _GRAPH_USERS
        if "$filter=" in url and "Managers" in url:
            return {"value": [{"id": "gid-managers"}]}
        if "$filter=" in url:
            return {"value": [{"id": "gid-agents"}]}
        if "gid-managers/members" in url:
            return group_pages["managers"]
        if "gid-agents/members" in url:
            return group_pages["agents"]
        raise AssertionError(f"unexpected graph url {url}")
    return get


async def _token(tenant_id):  # noqa: ANN001
    return "tok"


def _sync(store, *, slack=_SLACK_MEMBERS, managers=None, agents=None):
    pages = {"managers": {"value": [{"mail": m} for m in (managers or [])]},
             "agents": {"value": [{"mail": a} for a in (agents or [])]}}

    async def slack_users(token):  # noqa: ANN001
        return slack

    return DirectorySync(store=store, slack_users=slack_users,
                         token_fn=_token, get_fn=_graph_fake(pages))


@pytest.mark.asyncio
async def test_merge_roles_and_manager(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    sync = _sync(store, managers=["diane@x"], agents=["tom@x", "diane@x"])
    summary = await sync.run()

    assert summary["slack_users"] == 3      # bot, slackbot, deleted skipped
    assert summary["entra_users"] == 3
    assert summary["errors"] == []

    tom = await store.get_by_email("tom@x")
    assert tom.role == "agent" and tom.slack_id == "U_TOM"
    assert tom.manager_email == "diane@x"   # lowercased
    diane = await store.get_by_email("diane@x")
    assert diane.role == "manager"          # manager wins over agent
    assert sorted(diane.groups) == ["Managers", "Support Agent"]
    priya = await store.get_by_email("priya@x")
    assert priya.role == "customer" and priya.entra_id is None
    manoj = await store.get_by_email("manoj@x")  # Entra-only, no Slack
    assert manoj.role == "customer" and manoj.slack_id is None


@pytest.mark.asyncio
async def test_slack_failure_keeps_old_data(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    await _sync(store, managers=["diane@x"], agents=["tom@x"]).run()  # seed

    async def broken(token):  # noqa: ANN001
        return None

    sync2 = DirectorySync(store=store, slack_users=broken,
                          token_fn=_token, get_fn=_graph_fake(
                              {"managers": {"value": []}, "agents": {"value": []}}))
    summary = await sync2.run()
    assert summary["errors"] == ["slack: users.list failed"]
    assert (await store.get_by_email("tom@x")).role == "agent"  # untouched


@pytest.mark.asyncio
async def test_graph_failure_keeps_old_data(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    await _sync(store, managers=["diane@x"], agents=["tom@x"]).run()  # seed

    async def boom(token, url):  # noqa: ANN001
        raise RuntimeError("graph down")

    async def slack_users(token):  # noqa: ANN001
        return _SLACK_MEMBERS

    sync2 = DirectorySync(store=store, slack_users=slack_users,
                          token_fn=_token, get_fn=boom)
    summary = await sync2.run()
    assert len(summary["errors"]) == 1 and "graph" in summary["errors"][0]
    assert (await store.get_by_email("diane@x")).role == "manager"  # untouched
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_directory_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.directory.sync'`.

- [ ] **Step 3: Implement** — `app/directory/sync.py`:

```python
"""Daily Slack+Entra directory sync.

Pulls every Slack workspace member and every Entra user (+manager, +the two
role groups), merges on lowercase email, and upserts DirectoryUser records.
Idempotent and fail-soft: any fetch failure aborts the upsert phase so the
previous day's data survives — never wipe on error.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from app.bots.slack import slack_users_list
from app.config import get_settings
from app.connectors.graph import GRAPH, graph_get_json, graph_token
from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser

logger = logging.getLogger(__name__)


class DirectorySync:
    """Fetch + merge + upsert. Fetchers are injectable for tests."""

    def __init__(self, *, store: DirectoryStore,
                 slack_users=None, token_fn=None, get_fn=None) -> None:
        self._store = store
        self._slack_users = slack_users or slack_users_list
        self._token_fn = token_fn or graph_token
        self._get_fn = get_fn or graph_get_json

    async def run(self) -> dict:
        s = get_settings()
        summary: dict = {"slack_users": 0, "entra_users": 0, "matched": 0,
                         "managers": 0, "agents": 0, "customers": 0, "errors": []}

        members = await self._slack_users(s.slack_bot_token or "")
        if members is None:
            summary["errors"].append("slack: users.list failed")
            return summary
        slack_by_email: dict[str, dict] = {}
        for m in members:
            if m.get("deleted") or m.get("is_bot") or m.get("id") == "USLACKBOT":
                continue
            email = ((m.get("profile") or {}).get("email") or "").lower()
            if email:
                slack_by_email[email] = {
                    "slack_id": m.get("id"),
                    "display_name": (m.get("profile") or {}).get("real_name") or m.get("name"),
                }
        summary["slack_users"] = len(slack_by_email)

        try:
            token = await self._token_fn(s.azure_tenant_id)
            entra_by_email = await self._fetch_entra_users(token)
            manager_emails = await self._group_member_emails(token, s.entra_managers_group)
            agent_emails = await self._group_member_emails(token, s.entra_agents_group)
        except Exception as e:  # noqa: BLE001 — keep yesterday's data on any Graph failure
            logger.warning("directory sync: graph fetch failed: %s", e)
            summary["errors"].append(f"graph: {e}")
            return summary
        summary["entra_users"] = len(entra_by_email)

        now = datetime.now(UTC)
        for email in set(slack_by_email) | set(entra_by_email):
            sl = slack_by_email.get(email) or {}
            en = entra_by_email.get(email) or {}
            role = ("manager" if email in manager_emails
                    else "agent" if email in agent_emails else "customer")
            groups = [g for g, in_group in (
                (s.entra_managers_group, email in manager_emails),
                (s.entra_agents_group, email in agent_emails),
            ) if in_group]
            await self._store.upsert(DirectoryUser(
                email=email, slack_id=sl.get("slack_id"),
                display_name=sl.get("display_name") or en.get("display_name"),
                entra_id=en.get("entra_id"), manager_email=en.get("manager_email"),
                groups=groups, role=role, synced_at=now,
            ))
            summary[role + "s"] += 1
            if sl and en:
                summary["matched"] += 1
        logger.info("directory sync: %s", summary)
        return summary

    async def _fetch_entra_users(self, token: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        url = f"{GRAPH}/users?$select=id,displayName,mail&$expand=manager($select=mail)"
        while url:
            data = await self._get_fn(token, url)
            for u in data.get("value", []):
                email = (u.get("mail") or "").lower()
                if not email:
                    continue
                out[email] = {
                    "entra_id": u.get("id"),
                    "display_name": u.get("displayName"),
                    "manager_email": ((u.get("manager") or {}).get("mail") or "").lower() or None,
                }
            url = data.get("@odata.nextLink")
        return out

    async def _group_member_emails(self, token: str, group_name: str) -> set[str]:
        safe = group_name.replace("'", "''")
        flt = quote(f"displayName eq '{safe}'")
        data = await self._get_fn(token, f"{GRAPH}/groups?$filter={flt}&$select=id")
        groups = data.get("value", [])
        if not groups:
            logger.warning("directory sync: Entra group %r not found", group_name)
            return set()
        emails: set[str] = set()
        url = f"{GRAPH}/groups/{groups[0]['id']}/members?$select=mail"
        while url:
            data = await self._get_fn(token, url)
            emails |= {(m.get("mail") or "").lower()
                       for m in data.get("value", []) if m.get("mail")}
            url = data.get("@odata.nextLink")
        return emails
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_directory_sync.py -q` — Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/directory/sync.py substrateos-api/tests/test_directory_sync.py
git commit -m "feat(directory): Slack+Entra sync with role derivation, fail-soft merge"
```

---

### Task 6: DirectoryService (cache-first resolve with live write-through)

**Files:**
- Create: `substrateos-api/app/directory/service.py`
- Test: `substrateos-api/tests/test_directory_service.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_directory_service.py`:

```python
"""DirectoryService.resolve: store hit → done; miss → live Slack+Graph fallback."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.directory.service import DirectoryService
from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser


def _slack_fake(known: dict[str, str]):
    async def fake(token, method, payload):  # noqa: ANN001
        assert method == "users.lookupByEmail"
        sid = known.get(payload["email"])
        if not sid:
            return None  # slack_call returns None on users_not_found
        return {"ok": True, "user": {"id": sid, "profile": {"real_name": "Live Person"}}}
    return fake


async def _token(tenant_id):  # noqa: ANN001
    return "tok"


def _graph_fake(*, found: bool, group_names: list[str], manager_mail: str | None = None):
    async def get(token, url):  # noqa: ANN001
        if "$filter=" in url:
            if not found:
                return {"value": []}
            user = {"id": "g-live", "displayName": "Live Person", "mail": "live@x"}
            if manager_mail:
                user["manager"] = {"mail": manager_mail}
            return {"value": [user]}
        if "/memberOf" in url:
            return {"value": [{"displayName": n} for n in group_names]}
        raise AssertionError(f"unexpected url {url}")
    return get


@pytest.mark.asyncio
async def test_store_hit_skips_live_lookup(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(DirectoryUser(email="tom@x", slack_id="U_TOM", role="agent"))
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=False, group_names=[]))
    with patch("app.directory.service.slack_call") as nope:
        got = await svc.resolve("TOM@X")
    assert got.role == "agent"
    nope.assert_not_called()


@pytest.mark.asyncio
async def test_miss_resolves_live_and_writes_through(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=True, group_names=["Managers"],
                                              manager_mail="Boss@X"))
    with patch("app.directory.service.slack_call", new=_slack_fake({"live@x": "U_LIVE"})):
        got = await svc.resolve("live@x")
    assert got.slack_id == "U_LIVE" and got.role == "manager"
    assert got.manager_email == "boss@x"
    # write-through: second call hits the store
    with patch("app.directory.service.slack_call") as nope:
        again = await svc.resolve("live@x")
    assert again.role == "manager"
    nope.assert_not_called()


@pytest.mark.asyncio
async def test_slack_unknown_email_returns_none(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=False, group_names=[]))
    with patch("app.directory.service.slack_call", new=_slack_fake({})):
        assert (await svc.resolve("ghost@x")) is None
    assert (await svc.resolve(None)) is None


@pytest.mark.asyncio
async def test_entra_unknown_is_customer(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=False, group_names=[]))
    with patch("app.directory.service.slack_call", new=_slack_fake({"ext@x": "U_EXT"})):
        got = await svc.resolve("ext@x")
    assert got.role == "customer" and got.entra_id is None


@pytest.mark.asyncio
async def test_graph_error_degrades_to_customer(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)

    async def boom(token, url):  # noqa: ANN001
        raise RuntimeError("graph down")

    svc = DirectoryService(store=store, token_fn=_token, get_fn=boom)
    with patch("app.directory.service.slack_call", new=_slack_fake({"x@x": "U_X"})):
        got = await svc.resolve("x@x")
    assert got is not None and got.role == "customer"


@pytest.mark.asyncio
async def test_get_by_slack_id_delegates_to_store(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(DirectoryUser(email="d@x", slack_id="U_D", role="manager"))
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=False, group_names=[]))
    assert (await svc.get_by_slack_id("U_D")).role == "manager"
    assert (await svc.get_by_slack_id("U_NOPE")) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_directory_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.directory.service'`.

- [ ] **Step 3: Implement** — `app/directory/service.py`:

```python
"""Identity checks for playbooks: directory-first, live Slack+Graph fallback.

resolve(email) is what request-time routing calls: a store hit costs one Redis
GET; a miss does users.lookupByEmail + two Graph calls and writes the result
through, so the next request is warm. Unknown to Entra ⇒ role 'customer'
(the spec's "rest are customers" rule); unknown to Slack ⇒ None (the flow
stops — we can't route to someone we can't reach).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from app.bots.slack import slack_call
from app.config import get_settings
from app.connectors.graph import GRAPH, graph_get_json, graph_token
from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser

logger = logging.getLogger(__name__)


class DirectoryService:
    def __init__(self, *, store: DirectoryStore, token_fn=None, get_fn=None) -> None:
        self._store = store
        self._token_fn = token_fn or graph_token
        self._get_fn = get_fn or graph_get_json

    async def get_by_slack_id(self, slack_id: str | None) -> DirectoryUser | None:
        return await self._store.get_by_slack_id(slack_id)

    async def resolve(self, email: str | None) -> DirectoryUser | None:
        if not email:
            return None
        email = email.lower()
        hit = await self._store.get_by_email(email)
        if hit:
            return hit
        s = get_settings()
        body = await slack_call(s.slack_bot_token or "",
                                "users.lookupByEmail", {"email": email})
        slack_user = (body or {}).get("user") or {}
        if not slack_user.get("id"):
            return None
        user = DirectoryUser(
            email=email, slack_id=slack_user["id"],
            display_name=((slack_user.get("profile") or {}).get("real_name")
                          or slack_user.get("name")),
            role="customer", synced_at=datetime.now(UTC),
        )
        # Entra enrichment is best-effort: failures leave them a customer.
        # Guests' UPN ≠ email, so look up by mail filter, not /users/{email}.
        try:
            token = await self._token_fn(s.azure_tenant_id)
            safe = email.replace("'", "''")
            flt = quote(f"mail eq '{safe}'")
            data = await self._get_fn(
                token, f"{GRAPH}/users?$filter={flt}"
                       f"&$select=id,displayName,mail&$expand=manager($select=mail)")
            found = (data.get("value") or [None])[0]
            if found:
                user.entra_id = found.get("id")
                user.display_name = user.display_name or found.get("displayName")
                user.manager_email = (((found.get("manager") or {}).get("mail") or "")
                                      .lower() or None)
                member = await self._get_fn(
                    token, f"{GRAPH}/users/{found['id']}/memberOf?$select=displayName")
                names = {g.get("displayName") for g in member.get("value", [])}
                user.groups = [g for g in (s.entra_managers_group, s.entra_agents_group)
                               if g in names]
                user.role = ("manager" if s.entra_managers_group in names
                             else "agent" if s.entra_agents_group in names
                             else "customer")
        except Exception:  # noqa: BLE001 — enrichment must not block routing
            logger.warning("directory resolve: graph enrichment failed for %s", email)
        await self._store.upsert(user)
        return user
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_directory_service.py -q` — Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/directory/service.py substrateos-api/tests/test_directory_service.py
git commit -m "feat(directory): resolve() — cache-first identity with live write-through"
```

---

### Task 7: Scheduler

**Files:**
- Create: `substrateos-api/app/scheduler.py`
- Test: `substrateos-api/tests/test_scheduler.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_scheduler.py`:

```python
"""start_periodic: ticks repeat, exceptions don't kill the loop, cancel stops it."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.scheduler import start_periodic

# 1h == 3600s; use a tiny interval so two ticks land within the test.
_FAST = 0.02 / 3600  # 20ms


@pytest.mark.asyncio
async def test_ticks_repeat_and_cancel_stops():
    ticks: list[int] = []

    async def tick():
        ticks.append(1)
        return {"ok": len(ticks)}

    task = start_periodic("t", tick, interval_hours=_FAST, initial_delay_s=0)
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(ticks) >= 2
    n = len(ticks)
    await asyncio.sleep(0.05)
    assert len(ticks) == n  # genuinely stopped


@pytest.mark.asyncio
async def test_exception_does_not_kill_loop():
    ticks: list[int] = []

    async def tick():
        ticks.append(1)
        raise RuntimeError("boom")

    task = start_periodic("t", tick, interval_hours=_FAST, initial_delay_s=0)
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(ticks) >= 2  # survived the first failure
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_scheduler.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.scheduler'`.

- [ ] **Step 3: Implement** — `app/scheduler.py`:

```python
"""In-process periodic task runner, owned by the FastAPI lifespan.

The app's first scheduler: today it drives the daily directory sync; the
Outlook subscription-renewal maintenance (currently a manual /admin POST) is
the next intended consumer. Runs per replica — ticks must be idempotent
(directory upserts are), so extra replicas only do redundant work.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def start_periodic(
    name: str,
    tick: Callable[[], Awaitable[object]],
    *,
    interval_hours: float,
    initial_delay_s: float = 10.0,
) -> asyncio.Task:
    """Run `tick` after `initial_delay_s`, then every `interval_hours`, forever.
    Exceptions are logged and never kill the loop; cancel the task on shutdown."""

    async def _loop() -> None:
        await asyncio.sleep(initial_delay_s)
        while True:
            try:
                result = await tick()
                logger.info("periodic[%s] tick ok: %s", name, result)
            except Exception:  # noqa: BLE001 — the loop must outlive any one tick
                logger.exception("periodic[%s] tick failed; retrying next interval", name)
            await asyncio.sleep(interval_hours * 3600)

    return asyncio.create_task(_loop(), name=f"periodic:{name}")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_scheduler.py -q` — Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/scheduler.py substrateos-api/tests/test_scheduler.py
git commit -m "feat(scheduler): lifespan-owned periodic runner (first consumer: directory sync)"
```

---

### Task 8: ApprovalFlow — drop the env-var fallback

**Files:**
- Modify: `substrateos-api/app/workflows/approval.py:69-88`
- Modify: `substrateos-api/tests/test_approval_flow.py:84-99`

- [ ] **Step 1: Update the test** — in `tests/test_approval_flow.py`, REPLACE
`test_falls_back_to_configured_approver` (lines 84–99) with:

```python
@pytest.mark.asyncio
async def test_no_manager_means_no_fallback(monkeypatch):
    """No env-var fallback approver exists anymore — manager or stop."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = RunStore(client=None, force_memory=True)
    flow = ApprovalFlow(store=store, people=_People(None))  # no manager
    calls, fake = _slack_recorder()
    with patch("app.workflows.approval.slack_call", new=fake):
        await flow.handle_request(text="get this signed off", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "error"
    assert run.approver_source is None
    assert "conversations.open" not in [m for m, _ in calls]
```

Also in `test_no_approver_asks_requester` (line ~105) delete the now-meaningless line:

```python
    monkeypatch.delenv("SLACK_REFUND_APPROVER_ID", raising=False)
```

- [ ] **Step 2: Run to verify the new test fails**

Run: `uv run pytest tests/test_approval_flow.py -q`
Expected: `test_no_manager_means_no_fallback` FAILS (run lands on `pending_approval` via fallback "Sam Approver").

- [ ] **Step 3: Implement** — in `app/workflows/approval.py`, replace
`_resolve_approver` (lines 71–88) with:

```python
    async def _resolve_approver(self, token: str, requester_slack_id: str | None,
                                tenant_id: str) -> tuple[str | None, str | None, str]:
        """Returns (approver_slack_id, approver_name, source). Source is
        'manager' or 'none' — there is no fallback approver: the playbook
        stops rather than guessing who may sign off."""
        if self._people is not None:
            email = await self._email(token, requester_slack_id)
            if email:
                mgr = await self._people.manager_of(email=email, tenant_id=tenant_id)
                if mgr:
                    sid = await self._slack_id_for_email(token, mgr.get("email"))
                    if sid:
                        return sid, mgr.get("display_name") or "your manager", "manager"
        return None, None, "none"
```

Also update the module docstring's "with a configured fallback" phrase (line 5) to "or stops".
And in `handle_request`, the line `role = "requester's manager" if source == "manager" else "configured approver"` becomes simply:

```python
        role = "requester's manager"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_approval_flow.py -q` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/workflows/approval.py substrateos-api/tests/test_approval_flow.py
git commit -m "feat(approval): manager-or-stop — remove the env-var fallback approver"
```

---

### Task 9: Customer-request card

**Files:**
- Modify: `substrateos-api/app/bots/refund_cards.py` (append)
- Test: `substrateos-api/tests/test_refund_cards.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_refund_cards.py`:

```python
def test_customer_request_blocks():
    from app.bots.refund_cards import customer_request_blocks

    card = customer_request_blocks(
        request_text="I want a refund for order 48213",
        customer_name="Priya Sharma", run_id="RB-4480",
    )
    assert "RB-4480" in card["blocks"][0]["text"]["text"]
    body = str(card["attachments"])
    assert "Priya Sharma" in body and "order 48213" in body
    assert card["attachments"][0]["color"] == "#c8860d"  # amber: waiting on a human
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_refund_cards.py -q`
Expected: FAIL — ImportError on `customer_request_blocks`.

- [ ] **Step 3: Implement** — append to `app/bots/refund_cards.py`:

```python
def customer_request_blocks(*, request_text: str, customer_name: str, run_id: str) -> dict:
    """Channel card for a customer's refund ask — needs a support agent to pick
    it up and run the playbook themselves (customers can't trigger refunds)."""
    return {
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f":wave: *Customer refund request* — needs a support agent · run {run_id}"}}],
        "attachments": [_bar(_AMBER, [
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*From*\n{customer_name}"},
                {"type": "mrkdwn", "text": f"*Request*\n{request_text[:500]}"},
            ]},
            {"type": "context", "elements": [{"type": "mrkdwn",
                "text": "Customers can't trigger refunds directly — an agent should "
                        "pick this up and run it."}]},
        ])],
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_refund_cards.py -q` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/bots/refund_cards.py substrateos-api/tests/test_refund_cards.py
git commit -m "feat(refund): customer-request channel card"
```

---

### Task 10: RefundFlow — directory-driven routing (`handle_request`)

**Files:**
- Modify: `substrateos-api/app/workflows/flow.py`
- Test: `substrateos-api/tests/test_refund_flow.py` (FULL REWRITE below)

- [ ] **Step 1: Replace `tests/test_refund_flow.py` ENTIRELY with:**

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.workflow import RefundDecision
from app.workflows.engine import RefundEngineError
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore

_OVER_LIMIT = RefundDecision(
    found=True, order_id="48213", customer="Priya Sharma", amount_usd=1200,
    order_age_days=45, policy_limit_usd=500, policy_limit_days=30,
    auto_approve=False, reasoning="Over the $500 / 30 day auto-approve limit.",
)
_WITHIN = _OVER_LIMIT.model_copy(update={
    "order_id": "48190", "customer": "Marcus Lee", "amount_usd": 89.0,
    "order_age_days": 12, "auto_approve": True,
    "reasoning": "Within the $500 / 30 day auto-approve limit.",
})

_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     manager_email="diana@x", groups=["Support Agent"], role="agent")
_DIANA = DirectoryUser(email="diana@x", slack_id="U_DIANA", display_name="Diana Foster",
                       groups=["Managers"], role="manager")
_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")


class _Directory:
    """In-memory stand-in for DirectoryService."""

    def __init__(self, *records: DirectoryUser) -> None:
        self._by_email = {r.email: r for r in records}
        self._by_slack = {r.slack_id: r for r in records if r.slack_id}

    async def resolve(self, email):  # noqa: ANN001
        return self._by_email.get((email or "").lower())

    async def get_by_slack_id(self, slack_id):  # noqa: ANN001
        return self._by_slack.get(slack_id)


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@substrateos",
                display_name="Bot", group_ids={"t-test:everyone"})


def _flow(decision=None, error=False, directory=None):
    engine = AsyncMock()
    if error:
        engine.evaluate.side_effect = RefundEngineError("boom")
    else:
        engine.evaluate.return_value = decision
    store = RunStore(client=None, force_memory=True)
    directory = directory if directory is not None else _Directory(_TOM, _DIANA, _PRIYA)
    return RefundFlow(engine=engine, store=store, directory=directory), store


def _slack_recorder():
    """Patchable fake slack_call that records (method, payload) and returns canned bodies."""
    calls: list[tuple[str, dict]] = []

    async def fake(token, method, payload):
        calls.append((method, payload))
        if method == "users.info":
            uid = payload.get("user")
            people = {"U_TOM": ("Tom Reyes", "tom@x"),
                      "U_DIANA": ("Diana Foster", "diana@x"),
                      "U_PRIYA": ("Priya Sharma", "priya@x")}
            name, email = people.get(uid, ("Someone", ""))
            return {"ok": True, "user": {"real_name": name,
                                         "profile": {"display_name": "", "email": email}}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "111.222", "channel": payload["channel"]}
        return {"ok": True}

    return calls, fake


# ── agent path: routes to the requester's Entra manager ───────────────────────

@pytest.mark.asyncio
async def test_needs_approval_routes_to_managers_dm(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund $1,200 order 48213", channel="C_REFUNDS",
                                  thread_ts="100.1", requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval"
    assert run.approver_name == "Diana Foster"
    assert run.approver_slack_id == "U_DIANA"
    assert run.dm_channel == "D_DIANA" and run.dm_ts == "111.222"
    opened = [p for m, p in calls if m == "conversations.open"]
    assert opened == [{"users": "U_DIANA"}]
    steps = [e.step for e in await store.list_events(run.id)]
    assert steps == ["Request received", "Identity checked", "Facts gathered",
                     "Rule evaluated", "Routed for approval"]
    routed = [e for e in await store.list_events(run.id) if e.step == "Routed for approval"][0]
    assert "Tom Reyes's manager" in routed.detail and "Managers" in routed.detail


@pytest.mark.asyncio
async def test_auto_approve_path(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_WITHIN)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund $89 order 48190", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "completed"
    steps = [e.step for e in await store.list_events(run.id)]
    assert steps == ["Request received", "Identity checked", "Facts gathered",
                     "Rule evaluated", "Auto-approved", "Refund issued"]
    assert not any(m == "conversations.open" for m, _ in calls)


# ── stop-the-run: no usable manager ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_without_manager_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    orphan = _TOM.model_copy(update={"manager_email": None})
    flow, store = _flow(decision=_OVER_LIMIT, directory=_Directory(orphan, _DIANA))
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund order 48213", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "needs_attention"
    assert "No eligible approver" in [e.step for e in await store.list_events(run.id)]
    assert not any(m == "conversations.open" for m, _ in calls)


@pytest.mark.asyncio
async def test_manager_not_in_managers_group_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    demoted = _DIANA.model_copy(update={"role": "agent", "groups": ["Support Agent"]})
    flow, store = _flow(decision=_OVER_LIMIT, directory=_Directory(_TOM, demoted))
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund order 48213", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "needs_attention"
    detail = [e for e in await store.list_events(run.id)
              if e.step == "No eligible approver"][0].detail
    assert "Managers" in detail


@pytest.mark.asyncio
async def test_manager_without_slack_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    no_slack = _DIANA.model_copy(update={"slack_id": None})
    flow, store = _flow(decision=_OVER_LIMIT, directory=_Directory(_TOM, no_slack))
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund order 48213", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    assert (await store.list_runs())[0].status == "needs_attention"


# ── customer path ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customer_routes_to_support_channel(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_REFUND_CHANNEL_ID", "C_SUPPORT")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="I want a refund for order 48213", channel="D_PRIYA",
                                  thread_ts=None, requester_slack_id="U_PRIYA", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "routed_to_support"
    # engine must NOT run for customers
    flow._engine.evaluate.assert_not_called()
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert any(p["channel"] == "C_SUPPORT" for p in posts)   # support card
    assert any(p["channel"] == "D_PRIYA" for p in posts)     # customer told
    assert "Routed to support" in [e.step for e in await store.list_events(run.id)]


@pytest.mark.asyncio
async def test_customer_without_channel_config_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_REFUND_CHANNEL_ID", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund please", channel="D_PRIYA", thread_ts=None,
                                  requester_slack_id="U_PRIYA", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "needs_attention"
    assert "No support channel" in [e.step for e in await store.list_events(run.id)]


# ── identity unknown ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_identity_stops(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT, directory=_Directory())  # empty directory
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id=None, user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "needs_attention"
    assert "Identity unknown" in [e.step for e in await store.list_events(run.id)]
    flow._engine.evaluate.assert_not_called()


# ── engine outcomes (agent identity established) ───────────────────────────────

@pytest.mark.asyncio
async def test_engine_error_marks_run(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(error=True)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    assert (await store.list_runs())[0].status == "error"


@pytest.mark.asyncio
async def test_order_not_found(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    nf = RefundDecision(found=False, reasoning="No order matching #99999 in the context.")
    flow, store = _flow(decision=nf)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund order 99999", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "completed"
    assert "Order not found" in [e.step for e in await store.list_events(run.id)]


# ── button clicks: manager-only enforcement ────────────────────────────────────

async def _pending_run(store):
    run = await store.create(requester_name="Tom Reyes", requester_slack_id="U_TOM",
                             channel="C_REFUNDS", thread_ts="100.1")
    run.decision = _OVER_LIMIT
    run.status = "pending_approval"
    run.approver_slack_id = "U_DIANA"
    run.dm_channel, run.dm_ts = "D_DIANA", "111.222"
    await store.save(run)
    return run


def _click(action_id: str, run_id: str, *, user_id: str, name: str) -> dict:
    return {
        "type": "block_actions",
        "user": {"id": user_id, "name": name},
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
        "actions": [{"action_id": action_id, "value": run_id}],
    }


@pytest.mark.asyncio
async def test_routed_manager_can_approve(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", run.id,
                                        user_id="U_DIANA", name="diana"))
    loaded = await store.get(run.id)
    assert loaded.status == "completed"
    assert loaded.approver_name == "Diana Foster"
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Approved" in steps and "Refund issued" in steps


@pytest.mark.asyncio
async def test_agent_click_is_denied(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", run.id,
                                        user_id="U_TOM", name="tom"))
    loaded = await store.get(run.id)
    assert loaded.status == "pending_approval"          # untouched
    assert "Approval denied" in [e.step for e in await store.list_events(run.id)]
    assert any(m == "chat.postEphemeral" for m, _ in calls)


@pytest.mark.asyncio
async def test_unknown_clicker_is_denied(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_reject", run.id,
                                        user_id="U_STRANGER", name="who"))
    assert (await store.get(run.id)).status == "pending_approval"


@pytest.mark.asyncio
async def test_handle_action_reject(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_reject", run.id,
                                        user_id="U_DIANA", name="diana"))
    loaded = await store.get(run.id)
    assert loaded.status == "rejected"
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Rejected" in steps and "Refund issued" not in steps


@pytest.mark.asyncio
async def test_handle_action_idempotent_second_click(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await _pending_run(store)
    payload = _click("refund_approve", run.id, user_id="U_DIANA", name="diana")
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)
        n_events = len(await store.list_events(run.id))
        await flow.handle_action(payload)  # second click
    assert len(await store.list_events(run.id)) == n_events


@pytest.mark.asyncio
async def test_handle_action_unknown_run_is_noop(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(_click("refund_approve", "RB-0000",
                                        user_id="U_DIANA", name="diana"))
    assert calls == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_refund_flow.py -q`
Expected: FAIL — `TypeError: RefundFlow.__init__() got an unexpected keyword argument 'directory'`.

- [ ] **Step 3: Implement `handle_request`** — in `app/workflows/flow.py`:

Update imports (add `customer_request_blocks` to the refund_cards import; add the service type):

```python
from app.bots.refund_cards import (
    approval_dm_blocks,
    auto_approved_blocks,
    customer_request_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.directory.service import DirectoryService
```

Constructor:

```python
    def __init__(self, *, engine: RefundEngine, store: RunStore,
                 directory: DirectoryService) -> None:
        self._engine = engine
        self._store = store
        self._directory = directory
```

Add a `_profile` helper next to `_display_name` (which stays — `handle_action` uses it):

```python
    async def _profile(self, token: str, slack_user_id: str | None
                       ) -> tuple[str | None, str | None]:
        """(display_name, email) via users.info — both None when unreachable."""
        if not slack_user_id:
            return None, None
        body = await slack_call(token, "users.info", {"user": slack_user_id})
        if not body:
            return None, None
        u = body.get("user") or {}
        profile = u.get("profile") or {}
        name = profile.get("display_name") or u.get("real_name") or u.get("name")
        return name, (profile.get("email") or "").lower() or None
```

REPLACE the whole `handle_request` method with:

```python
    async def handle_request(self, *, text: str, channel: str, thread_ts: str | None,
                             requester_slack_id: str | None, user: User) -> None:
        s = get_settings()
        token = s.slack_bot_token or ""
        requester, requester_email = await self._profile(token, requester_slack_id)
        requester = requester or "Support agent"
        run = await self._store.create(
            requester_name=requester, requester_slack_id=requester_slack_id,
            channel=channel, thread_ts=thread_ts,
        )
        await self._store.add_event(
            run.id, step="Request received",
            detail=f"{text[:160]} · from Slack", actor=requester,
        )

        # Check: who is asking, per the synced directory (Slack id ↔ Entra groups).
        record = await self._directory.resolve(requester_email)
        if record is None:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(
                run.id, step="Identity unknown",
                detail="Could not establish the requester's identity (no Slack email match)",
                actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="I couldn't verify who's asking, so I've stopped. "
                                  "Make sure your Slack profile has an email address.")
            return
        groups = ", ".join(record.groups) if record.groups else "no role groups"
        await self._store.add_event(
            run.id, step="Identity checked",
            detail=f"{requester} → {record.role} ({groups})", actor="SubstrateOS")

        if record.role == "customer":
            await self._route_to_support(token, run, text=text, requester=requester,
                                         channel=channel, thread_ts=thread_ts)
            return

        first = requester.split()[0]
        await self._post(token, channel, thread_ts,
                         text=f"On it, {first} — pulling up the order and checking the refund policy…")

        try:
            decision = await self._engine.evaluate(text, user=user)
        except RefundEngineError:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail="Could not evaluate the request", actor="SubstrateOS")
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        run.decision = decision
        if not decision.found:
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Order not found",
                                        detail=decision.reasoning, actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text=f"I couldn't find that order in our records. {decision.reasoning}")
            return

        await self._store.add_event(
            run.id, step="Facts gathered",
            detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                    f"age {decision.order_age_days} days · customer {decision.customer}"),
            actor="SubstrateOS",
        )
        await self._store.add_event(
            run.id, step="Rule evaluated",
            detail=(f"Auto-approve limits ${decision.policy_limit_usd:,.0f} / "
                    f"{decision.policy_limit_days} days → "
                    f"{'within limit' if decision.auto_approve else 'over limit'}"),
            actor="refund_v1",
        )

        if decision.auto_approve:
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Auto-approved",
                                        detail=decision.reasoning, actor="refund_v1")
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=(f"${decision.amount_usd:,.0f} refunded to {decision.customer} · "
                        "confirmation sent"),
                actor="SubstrateOS",
            )
            await self._post(token, channel, thread_ts,
                             text="Auto-approved within policy — refund issued.",
                             card=auto_approved_blocks(decision, run_id=run.id))
            return

        # Needs approval — Stop: only the requester's Entra manager, who must be
        # in the Managers group and reachable on Slack, may approve. No fallback.
        mgr = (await self._directory.resolve(record.manager_email)
               if record.manager_email else None)
        reason: str | None = None
        if mgr is None:
            reason = "no manager is set for you in Entra ID"
        elif mgr.role != "manager":
            reason = (f"{mgr.display_name or mgr.email} is not in the "
                      f"{s.entra_managers_group} group")
        elif not mgr.slack_id:
            reason = f"{mgr.display_name or mgr.email} has no Slack account"
        if reason:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(run.id, step="No eligible approver",
                                        detail=f"Stopped: {reason}", actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text=f"I can't route this for approval — {reason}. "
                                  "An admin needs to fix the directory before I can continue.")
            return

        run.status = "pending_approval"
        run.approver_name = mgr.display_name or mgr.email
        run.approver_slack_id = mgr.slack_id
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed for approval",
            detail=(f"Sent to {run.approver_name} — {requester}'s manager "
                    f"({s.entra_managers_group} group)"),
            actor="SubstrateOS")
        await self._post(token, channel, thread_ts,
                         text="I can't auto-approve this one — routing to your manager for approval.",
                         card=needs_approval_blocks(decision, approver_label=run.approver_name,
                                                    run_id=run.id))
        opened = await slack_call(token, "conversations.open", {"users": mgr.slack_id})
        dm = ((opened or {}).get("channel") or {}).get("id")
        if not dm:
            await self._post(token, channel, thread_ts,
                             text="I couldn't reach the approver in a DM — please review manually.")
            return
        posted = await slack_call(token, "chat.postMessage", {
            "channel": dm, "text": "Refund needs your approval",
            **approval_dm_blocks(decision, requester_name=requester, run_id=run.id),
        })
        if posted:
            run.dm_channel = dm
            run.dm_ts = posted.get("ts")
            await self._store.save(run)
```

Add the `_route_to_support` helper (after `_post`):

```python
    async def _route_to_support(self, token: str, run, *, text: str, requester: str,
                                channel: str, thread_ts: str | None) -> None:
        """Customer path: no engine run — hand the ask to the support channel."""
        support_channel = get_settings().slack_refund_channel_id
        if not support_channel:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(
                run.id, step="No support channel",
                detail="SLACK_REFUND_CHANNEL_ID is not configured — customer request not routed",
                actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="Refunds are handled by our support team — "
                                  "please contact them directly.")
            return
        posted = await slack_call(token, "chat.postMessage", {
            "channel": support_channel,
            "text": f"Customer refund request from {requester}",
            **customer_request_blocks(request_text=text, customer_name=requester,
                                      run_id=run.id),
        })
        if not posted:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Routing failed",
                                        detail="Could not post to the refunds channel",
                                        actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="I couldn't reach the support team — please contact them directly.")
            return
        run.status = "routed_to_support"
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed to support",
            detail=f"Posted to the refunds channel for a support agent ({requester} is a customer)",
            actor="SubstrateOS")
        await self._post(token, channel, thread_ts,
                         text="Refunds are handled by our support team — I've passed your "
                              "request to them and someone will follow up here.")
```

- [ ] **Step 4: Implement click enforcement** — in `handle_action`, directly AFTER the existing idempotency block (`if run.status != "pending_approval": ... return`) and BEFORE `approved = action_id == "refund_approve"`, insert:

```python
        # Only the routed approver — who must be a manager in the directory —
        # may act. Anyone else is refused and the attempt is audited.
        actor_record = await self._directory.get_by_slack_id(approver_id)
        is_routed = run.approver_slack_id is None or approver_id == run.approver_slack_id
        if not is_routed or actor_record is None or actor_record.role != "manager":
            actor_name = (await self._display_name(token, approver_id)
                          or (payload.get("user") or {}).get("name") or "Someone")
            await self._store.add_event(
                run.id, step="Approval denied",
                detail=(f"{actor_name} tried to act but is not the routed approver "
                        "(managers only)"),
                actor=actor_name)
            if dm_channel and approver_id:
                await slack_call(token, "chat.postEphemeral", {
                    "channel": dm_channel, "user": approver_id,
                    "text": "Only the routed approver (a manager) can act on this request.",
                })
            return
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_refund_flow.py -q` — Expected: 15 passed.

- [ ] **Step 6: Check other RefundFlow constructions still compile**

Run: `grep -rn "RefundFlow(" app/ tests/` — only `app/main.py` (fixed in Task 12) and `tests/test_refund_flow.py` should construct it. If anything else does, add `directory=` there too.

- [ ] **Step 7: Commit**

```bash
git add substrateos-api/app/workflows/flow.py substrateos-api/tests/test_refund_flow.py
git commit -m "feat(refund): directory-driven routing — customer/agent/manager paths, manager-only clicks"
```

---

### Task 11: Remove `slack_refund_approver_id` from config

Nothing references it after Tasks 8+10.

**Files:**
- Modify: `substrateos-api/app/config.py:106`

- [ ] **Step 1: Verify it's unused**

Run: `grep -rn "slack_refund_approver_id\|SLACK_REFUND_APPROVER_ID" app/ tests/`
Expected: ONLY the `app/config.py:106` definition. If anything else shows, fix it first.

- [ ] **Step 2: Delete the line** in `app/config.py`:

```python
    slack_refund_approver_id: str | None = None  # SLACK_REFUND_APPROVER_ID — Diana's Slack member ID
```

- [ ] **Step 3: Full backend suite**

Run: `uv run pytest tests/ -q` — Expected: all pass (modulo the two known t-eval failures if running with local .env).

- [ ] **Step 4: Commit**

```bash
git add substrateos-api/app/config.py
git commit -m "feat(config): drop SLACK_REFUND_APPROVER_ID — routing is directory-driven"
```

---

### Task 12: Wiring — deps, admin endpoints, lifespan + scheduler

**Files:**
- Modify: `substrateos-api/app/deps.py` (append)
- Create: `substrateos-api/app/api/admin_directory.py`
- Modify: `substrateos-api/app/main.py`
- Test: `substrateos-api/tests/test_admin_directory.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_admin_directory.py`:

```python
"""Admin directory endpoints: manual sync trigger + redacted listing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.admin_directory import _redact
from app.deps import get_directory_store, get_directory_sync
from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser
from app.main import app


def test_redact():
    assert _redact("tom@omkar.com") == "t***@omkar.com"
    assert _redact("no-at-sign") == "***"


class _Sync:
    async def run(self):
        return {"slack_users": 4, "entra_users": 6, "matched": 4,
                "managers": 1, "agents": 2, "customers": 3, "errors": []}


@pytest.fixture()
def _client(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    app.dependency_overrides[get_directory_store] = lambda: store
    app.dependency_overrides[get_directory_sync] = lambda: _Sync()
    yield TestClient(app), store
    app.dependency_overrides.clear()


def test_sync_requires_admin_key(_client):
    client, _ = _client
    assert client.post("/admin/directory/sync").status_code == 403


def test_sync_returns_summary(_client):
    client, _ = _client
    r = client.post("/admin/directory/sync", headers={"x-admin-key": "secret"})
    assert r.status_code == 200
    assert r.json()["managers"] == 1


@pytest.mark.asyncio
async def test_list_redacts_emails(_client):
    client, store = _client
    await store.upsert(DirectoryUser(email="diane@omkar.com", slack_id="U_D",
                                     display_name="Diane", manager_email=None,
                                     groups=["Managers"], role="manager"))
    r = client.get("/admin/directory", headers={"x-admin-key": "secret"})
    assert r.status_code == 200
    [row] = r.json()
    assert row["email"] == "d***@omkar.com"
    assert row["role"] == "manager" and row["slack_id"] == "U_D"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_admin_directory.py -q`
Expected: FAIL — import errors for `app.api.admin_directory` / `get_directory_store`.

- [ ] **Step 3: Implement.**

Append to `app/deps.py`:

```python
def get_directory_store(request: Request):
    return getattr(request.app.state, "directory_store", None)


def get_directory_service(request: Request):
    return getattr(request.app.state, "directory", None)


def get_directory_sync(request: Request):
    return getattr(request.app.state, "directory_sync", None)
```

Create `app/api/admin_directory.py`:

```python
"""Admin Directory — inspect + manually refresh the synced user directory
(email ↔ Slack id ↔ Entra role) that approval routing reads. Admin-key gated
like the rest of /admin."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.admin import require_admin_key
from app.deps import get_directory_store, get_directory_sync

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin_key)])
logger = logging.getLogger(__name__)


def _redact(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


@router.post("/directory/sync")
async def directory_sync(sync=Depends(get_directory_sync)) -> dict:
    """Run the Slack+Entra directory sync now; returns the merge summary."""
    if sync is None:
        return {"errors": ["directory sync not configured"]}
    return await sync.run()


@router.get("/directory")
async def directory_list(store=Depends(get_directory_store)) -> list[dict]:
    """Every directory record, emails redacted."""
    if store is None:
        return []
    return [
        {
            "email": _redact(u.email),
            "slack_id": u.slack_id,
            "display_name": u.display_name,
            "role": u.role,
            "groups": u.groups,
            "manager_email": _redact(u.manager_email) if u.manager_email else None,
            "synced_at": u.synced_at.isoformat() if u.synced_at else None,
        }
        for u in await store.list_all()
    ]
```

Modify `app/main.py`:

(a) Add to the imports block:

```python
import asyncio
import contextlib

from app.api.admin_directory import router as admin_directory_router
from app.directory.service import DirectoryService
from app.directory.store import DirectoryStore
from app.directory.sync import DirectorySync
from app.scheduler import start_periodic
```

(b) In `lifespan`, REPLACE the `app.state.refund_flow = RefundFlow(...)` block with (directory objects must exist first):

```python
    app.state.directory_store = DirectoryStore()
    app.state.directory = DirectoryService(store=app.state.directory_store)
    app.state.directory_sync = DirectorySync(store=app.state.directory_store)
    app.state.refund_flow = RefundFlow(
        engine=RefundEngine(retriever=app.state.retriever, llm=app.state.llm),
        store=app.state.run_store,
        directory=app.state.directory,
    )
```

(c) Directly before the `try:` that wraps the `yield` (after the `mcp_bind(...)` call), add:

```python
    # Daily Slack+Entra directory sync — only when the Slack bot is configured
    # (tests and Slack-less deploys skip it).
    _dir_task: asyncio.Task | None = None
    if _s.slack_bot_token:
        _dir_task = start_periodic(
            "directory_sync", app.state.directory_sync.run,
            interval_hours=_s.directory_sync_interval_hours,
        )
```

(d) In the `finally:` block, FIRST lines (before the other acloses):

```python
        if _dir_task is not None:
            _dir_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _dir_task
        await app.state.directory_store.aclose()
```

(e) With the other routers: `app.include_router(admin_directory_router)`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_admin_directory.py -q` — Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/deps.py substrateos-api/app/api/admin_directory.py substrateos-api/app/main.py substrateos-api/tests/test_admin_directory.py
git commit -m "feat(directory): wire store/service/sync + daily scheduler + admin endpoints"
```

---

### Task 13: Full verification + docs sync

- [ ] **Step 1: Full backend suite**

Run: `cd substrateos-api && uv run pytest tests/ -q`
Expected: everything passes except (only when running against local `.env` with `SUBSTRATEOS_TENANT_ID=t-eval`) the two pre-existing known failures. Paste the summary line into the final report.

- [ ] **Step 2: Web checks**

Run: `cd web && pnpm typecheck && pnpm lint && pnpm build` — Expected: clean.

- [ ] **Step 3: Architecture doc** — `mockups/architecture.html` (Master Deck palette: navy `#102444`, amber `#c8860d`):

- Detailed view, near the Workflows/RefundFlow elements: add a **User Directory** box — "`app/directory/` — Slack ↔ Entra identity (email join): role (manager/agent/customer), manager edge; Redis-backed, daily sync" — and a **Scheduler** box — "`app/scheduler.py` — lifespan-owned periodic runner; consumer: directory sync (24h)". Draw/describe the flow: RefundFlow → DirectoryService → DirectoryStore (Redis) ← DirectorySync ← (Slack users.list + Graph users/groups).
- Update the refund playbook description: "approval routes to the requester's Entra manager (Managers group only); customers route to the support channel; stops when no eligible approver".
- High-level view: mention the directory under the identity/governance pillar.
- `open mockups/architecture.html` and eyeball both views.

- [ ] **Step 4: Tech stack tracker** — `.claude/skills/substrateos-feature/references/techstack.md`: no new libraries were introduced (httpx/redis/pydantic all pre-existing) — confirm and change nothing.

- [ ] **Step 5: Commit docs**

```bash
git add mockups/architecture.html
git commit -m "docs(architecture): user directory + scheduler in both views"
```

---

## Post-merge user actions (NOT in this plan — surface them in the final report)

1. Grant Graph application permissions `User.Read.All` + `GroupMember.Read.All` on the existing app registration (`azure_client_id`) + admin consent.
2. Set `SLACK_REFUND_CHANNEL_ID` env on the `substrateos-api` container app; remove the `SLACK_REFUND_APPROVER_ID` env var.
3. Confirm Slack scopes `users:read`, `users:read.email` (already verified live 2026-06-07).
4. Deploy via the `substrateos-deploy` skill (main only, with explicit approval), then `POST /admin/directory/sync` once and check `GET /admin/directory`.
```
