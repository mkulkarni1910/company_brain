# Refund Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working refund-approval use case: support agent Tom asks about a refund in Slack → the `refund` workflow-typed skill retrieves mocked order + policy docs from AI Search → one LLM call decides auto-approve vs route-to-manager → manager Diana gets a Slack DM with real Approve/Reject buttons → outcome posts back to the channel, with a full audit trail visible on a new web **Runs** page.

**Architecture:** Adds an optional `workflow` field to the existing Skill model. When the SkillRouter resolves a skill whose `workflow == "refund"`, the Slack bot diverts to a `RefundFlow` (engine: retrieval + single structured-JSON LLM call; store: Redis-backed run + audit-event store; surfaces: Block Kit cards + a new `/bot/slack/interactive` endpoint). A `GET /runs` API feeds a new Runs view in the Next.js web app.

**Tech Stack:** FastAPI, Pydantic v2, Redis (async), Azure AI Search (existing HybridRetriever), Gemini via existing `llm.complete()`, Slack Web API (httpx), Next.js app router, pytest.

**Spec:** `docs/superpowers/specs/2026-06-05-refund-experience-design.md`

**Conventions for every task:**
- Backend work dir: `substrateos-api/`. Run tests with `cd substrateos-api && .venv/bin/python -m pytest <file> -v`.
- All Python files start with `from __future__ import annotations`.
- Follow the graceful-degradation style of `app/skills/store.py` (catch Redis errors, log warning, keep working).

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `substrateos-api/app/domain/skill.py` | Modify | Add `workflow` field |
| `substrateos-api/app/skills/service.py` | Modify | Carry `workflow` onto `ResolvedSkill` |
| `substrateos-api/app/domain/workflow.py` | Create | `RefundRun`, `RunEvent`, `RefundDecision` models |
| `substrateos-api/app/workflows/__init__.py` | Create | Package marker (empty) |
| `substrateos-api/app/workflows/store.py` | Create | `RunStore` (Redis + in-process fallback) |
| `substrateos-api/app/workflows/engine.py` | Create | `RefundEngine` — retrieval + LLM decision |
| `substrateos-api/app/workflows/flow.py` | Create | `RefundFlow` — Slack-side orchestration |
| `substrateos-api/app/bots/slack.py` | Modify | Add generic `slack_call()` helper |
| `substrateos-api/app/bots/refund_cards.py` | Create | Block Kit card builders (pure functions) |
| `substrateos-api/app/config.py` | Modify | `slack_refund_approver_id` setting |
| `substrateos-api/app/deps.py` | Modify | `get_run_store`, `get_refund_flow` |
| `substrateos-api/app/main.py` | Modify | Wire `run_store`, `refund_flow`, runs router |
| `substrateos-api/app/api/bots.py` | Modify | Skill routing in Slack webhook + `/bot/slack/interactive` |
| `substrateos-api/app/api/runs.py` | Create | `GET /runs`, `GET /runs/{id}` |
| `substrateos-api/scripts/seed_refund_demo.py` | Create | Seed orders + policy docs + refund skill |
| `substrateos-api/tests/test_workflow_models.py` | Create | Model + workflow-field tests |
| `substrateos-api/tests/test_run_store.py` | Create | RunStore tests |
| `substrateos-api/tests/test_refund_engine.py` | Create | Engine tests (fake retriever/LLM) |
| `substrateos-api/tests/test_refund_flow.py` | Create | Flow tests (mocked Slack) |
| `substrateos-api/tests/test_slack_interactive.py` | Create | Interactivity endpoint tests |
| `substrateos-api/tests/test_runs_api.py` | Create | Runs API tests |
| `substrateos-api/tests/test_refund_e2e_integration.py` | Create | Live integration test (marked) |
| `web/lib/runsApi.ts` | Create | Runs API client |
| `web/app/runs/page.tsx` | Create | Runs list + audit detail view |
| `web/components/Chat.tsx` | Modify | "Runs" nav view |

---

### Task 1: `workflow` field on Skill models + router carry-through

**Files:**
- Modify: `substrateos-api/app/domain/skill.py`
- Modify: `substrateos-api/app/skills/service.py`
- Test: `substrateos-api/tests/test_workflow_models.py`

- [ ] **Step 1: Write the failing test**

Create `substrateos-api/tests/test_workflow_models.py`:

```python
from __future__ import annotations

import pytest

from app.domain.skill import ResolvedSkill, Skill, SkillCreate, SkillUpdate
from app.skills.service import SkillRouter
from app.skills.store import SkillStore
from tests.test_skills_store import _make_redis, _skill_json


def test_skill_workflow_field_default_none():
    create = SkillCreate(
        slug="refund", name="Refund Processing", description="d", team="Support",
        system_prompt="p",
    )
    assert create.workflow is None


def test_skill_workflow_field_roundtrip():
    create = SkillCreate(
        slug="refund", name="Refund Processing", description="d", team="Support",
        system_prompt="p", workflow="refund",
    )
    assert create.workflow == "refund"
    update = SkillUpdate(workflow="refund")
    assert update.workflow == "refund"


def test_resolved_skill_carries_workflow():
    r = ResolvedSkill(id="1", slug="refund", name="Refund", system_prompt="p",
                      clean_query="q", workflow="refund")
    assert r.workflow == "refund"
    # default stays None for existing call sites
    r2 = ResolvedSkill(id="1", slug="s", name="n", system_prompt="p", clean_query="q")
    assert r2.workflow is None


@pytest.mark.asyncio
async def test_router_explicit_slug_carries_workflow():
    skill_raw = _skill_json(slug="refund", workflow="refund")
    import json
    skill_id = json.loads(skill_raw)["id"]
    store = SkillStore(client=_make_redis({skill_id: skill_raw}))
    router = SkillRouter(skill_store=store, llm=None)
    resolved = await router.resolve_skill("/refund can we refund order 48213?")
    assert resolved is not None
    assert resolved.workflow == "refund"
    assert resolved.clean_query == "can we refund order 48213?"
```

Note: `_skill_json(**overrides)` in `tests/test_skills_store.py` builds a full Skill JSON and merges overrides — check it accepts arbitrary keys (it does: `base.update(overrides)`-style; if it uses `dict(...)` then `base |= overrides`, adapt the call accordingly after reading it).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_workflow_models.py -v`
Expected: FAIL — `workflow` is not a field on `SkillCreate` (pydantic ignores unknown? No — extra="ignore" is only on Settings; pydantic models raise no error for missing but `create.workflow` raises `AttributeError`).

- [ ] **Step 3: Implement**

In `substrateos-api/app/domain/skill.py`:

1. In `class Skill`, after `run_scope`: add
   ```python
   workflow: str | None = None  # e.g. "refund" — diverts to a workflow engine instead of plain RAG
   ```
2. In `class SkillCreate`, after `run_scope`: add `workflow: str | None = None`
3. In `class SkillUpdate`, after `run_scope`: add `workflow: str | None = None`
4. In `@dataclass ResolvedSkill`, after `clean_query`: add `workflow: str | None = None`

In `substrateos-api/app/skills/store.py` `create()`: add `workflow=data.workflow,` to the `Skill(...)` constructor call (after `run_scope=data.run_scope`).

In `substrateos-api/app/skills/service.py`: in BOTH `_resolve_explicit` and `_resolve_auto`, add `workflow=skill.workflow,` to the `ResolvedSkill(...)` constructor calls.

- [ ] **Step 4: Run tests to verify they pass (plus no regressions)**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_workflow_models.py tests/test_skills_store.py tests/test_skills_routing.py tests/test_skills_api.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/domain/skill.py substrateos-api/app/skills/store.py substrateos-api/app/skills/service.py substrateos-api/tests/test_workflow_models.py
git commit -m "feat(workflows): add workflow field to Skill model and ResolvedSkill"
```

---

### Task 2: Run domain models + RunStore

**Files:**
- Create: `substrateos-api/app/domain/workflow.py`
- Create: `substrateos-api/app/workflows/__init__.py` (empty)
- Create: `substrateos-api/app/workflows/store.py`
- Test: `substrateos-api/tests/test_run_store.py`

- [ ] **Step 1: Write the domain models** (pure pydantic, no test needed beyond store tests)

Create `substrateos-api/app/domain/workflow.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RunStatus = Literal["running", "pending_approval", "approved", "rejected", "completed", "error"]


class RefundDecision(BaseModel):
    """Structured output of the refund engine's single LLM call."""
    found: bool = False
    order_id: str | None = None
    customer: str | None = None
    amount_usd: float | None = None
    order_age_days: int | None = None
    policy_limit_usd: float | None = None
    policy_limit_days: int | None = None
    auto_approve: bool = False
    reasoning: str = ""


class RunEvent(BaseModel):
    """One audit-trail entry for a workflow run."""
    ts: datetime
    step: str
    detail: str
    actor: str


class RefundRun(BaseModel):
    """State of one refund workflow run (RB-xxxx)."""
    id: str
    status: RunStatus = "running"
    requester_name: str
    requester_slack_id: str | None = None
    channel: str | None = None
    thread_ts: str | None = None
    dm_channel: str | None = None
    dm_ts: str | None = None
    decision: RefundDecision | None = None
    approver_name: str | None = None
    created_at: datetime
    updated_at: datetime
```

Create empty `substrateos-api/app/workflows/__init__.py`.

- [ ] **Step 2: Write the failing store test**

Create `substrateos-api/tests/test_run_store.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.workflows.store import RunStore


def _make_redis() -> MagicMock:
    """Mock async Redis supporting the string/list/seq ops RunStore uses."""
    r = MagicMock()
    kv: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    seq = {"n": 4470}

    async def set_(key, value):
        kv[key] = value

    async def get(key):
        return kv.get(key)

    async def incr(key):
        seq["n"] += 1
        return seq["n"]

    async def rpush(key, value):
        lists.setdefault(key, []).append(value)

    async def lpush(key, value):
        lists.setdefault(key, []).insert(0, value)

    async def lrange(key, start, end):
        items = lists.get(key, [])
        return items if end == -1 else items[start:end + 1]

    r.set = set_
    r.get = get
    r.incr = incr
    r.rpush = rpush
    r.lpush = lpush
    r.lrange = lrange
    return r


@pytest.mark.asyncio
async def test_create_assigns_sequential_rb_id():
    store = RunStore(client=_make_redis())
    run = await store.create(requester_name="Tom Reyes", requester_slack_id="U1",
                             channel="C1", thread_ts="123.45")
    assert run.id == "RB-4471"
    assert run.status == "running"
    run2 = await store.create(requester_name="Tom Reyes", requester_slack_id="U1",
                              channel="C1", thread_ts="123.46")
    assert run2.id == "RB-4472"


@pytest.mark.asyncio
async def test_save_and_get_roundtrip():
    store = RunStore(client=_make_redis())
    run = await store.create(requester_name="Tom", requester_slack_id=None,
                             channel="C1", thread_ts=None)
    run.status = "pending_approval"
    run.approver_name = "Diana Foster"
    await store.save(run)
    loaded = await store.get(run.id)
    assert loaded is not None
    assert loaded.status == "pending_approval"
    assert loaded.approver_name == "Diana Foster"


@pytest.mark.asyncio
async def test_get_unknown_returns_none():
    store = RunStore(client=_make_redis())
    assert await store.get("RB-9999") is None


@pytest.mark.asyncio
async def test_events_append_and_list_in_order():
    store = RunStore(client=_make_redis())
    run = await store.create(requester_name="Tom", requester_slack_id=None,
                             channel="C1", thread_ts=None)
    await store.add_event(run.id, step="Request received", detail="Refund $1,200", actor="Tom Reyes")
    await store.add_event(run.id, step="Facts gathered", detail="Order #48213", actor="SubStrateOS")
    events = await store.list_events(run.id)
    assert [e.step for e in events] == ["Request received", "Facts gathered"]
    assert events[0].actor == "Tom Reyes"


@pytest.mark.asyncio
async def test_list_runs_newest_first():
    store = RunStore(client=_make_redis())
    r1 = await store.create(requester_name="Tom", requester_slack_id=None, channel="C1", thread_ts=None)
    r2 = await store.create(requester_name="Tom", requester_slack_id=None, channel="C1", thread_ts=None)
    runs = await store.list_runs()
    assert [r.id for r in runs] == [r2.id, r1.id]


@pytest.mark.asyncio
async def test_memory_fallback_without_redis():
    store = RunStore(client=None, force_memory=True)
    run = await store.create(requester_name="Tom", requester_slack_id=None, channel="C1", thread_ts=None)
    assert run.id == "RB-4471"
    await store.add_event(run.id, step="Request received", detail="d", actor="Tom")
    assert (await store.get(run.id)) is not None
    assert len(await store.list_events(run.id)) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_run_store.py -v`
Expected: FAIL — `ModuleNotFoundError: app.workflows.store`

- [ ] **Step 4: Implement RunStore**

Create `substrateos-api/app/workflows/store.py`:

```python
from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.workflow import RefundRun, RunEvent

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)

_SEQ_KEY = "runs:seq"       # INCR counter; first id is RB-4471
_INDEX_KEY = "runs:all"     # LPUSH run ids, newest first
_SEQ_START = 4470


def _run_key(run_id: str) -> str:
    return f"run:{run_id}"


def _events_key(run_id: str) -> str:
    return f"run:{run_id}:events"


class RunStore:
    """Redis-backed store for workflow runs + audit events.

    Mirrors writes to an in-process dict so the flow keeps working within a
    single process when Redis is unavailable (same degradation philosophy as
    SkillStore, but runs are flow-critical so a memory fallback is kept).
    """

    def __init__(self, client: redis.Redis | None = None, *, force_memory: bool = False) -> None:
        self._mem_runs: dict[str, str] = {}
        self._mem_events: dict[str, list[str]] = {}
        self._mem_index: list[str] = []
        self._mem_seq = _SEQ_START
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

    async def _next_id(self) -> str:
        if self._r is not None:
            try:
                # Initialise the counter on first use, then increment.
                await self._r.set(_SEQ_KEY, _SEQ_START) if not await self._r.get(_SEQ_KEY) else None
                n = await self._r.incr(_SEQ_KEY)
                return f"RB-{n}"
            except _ERRORS as e:
                logger.warning("RunStore._next_id redis failed: %s", e)
        self._mem_seq += 1
        return f"RB-{self._mem_seq}"

    async def create(
        self, *, requester_name: str, requester_slack_id: str | None,
        channel: str | None, thread_ts: str | None,
    ) -> RefundRun:
        now = datetime.now(UTC)
        run = RefundRun(
            id=await self._next_id(), requester_name=requester_name,
            requester_slack_id=requester_slack_id, channel=channel, thread_ts=thread_ts,
            created_at=now, updated_at=now,
        )
        await self._write(run, new=True)
        return run

    async def save(self, run: RefundRun) -> None:
        run.updated_at = datetime.now(UTC)
        await self._write(run, new=False)

    async def _write(self, run: RefundRun, *, new: bool) -> None:
        blob = run.model_dump_json()
        self._mem_runs[run.id] = blob
        if new:
            self._mem_index.insert(0, run.id)
        if self._r is None:
            return
        try:
            await self._r.set(_run_key(run.id), blob)
            if new:
                await self._r.lpush(_INDEX_KEY, run.id)
        except _ERRORS as e:
            logger.warning("RunStore._write redis failed: %s", e)

    async def get(self, run_id: str) -> RefundRun | None:
        raw: str | None = None
        if self._r is not None:
            try:
                raw = await self._r.get(_run_key(run_id))
            except _ERRORS as e:
                logger.warning("RunStore.get redis failed: %s", e)
        raw = raw or self._mem_runs.get(run_id)
        if not raw:
            return None
        with contextlib.suppress(Exception):
            return RefundRun.model_validate_json(raw)
        return None

    async def list_runs(self, limit: int = 50) -> list[RefundRun]:
        ids: list[str] = []
        if self._r is not None:
            try:
                ids = await self._r.lrange(_INDEX_KEY, 0, limit - 1)
            except _ERRORS as e:
                logger.warning("RunStore.list_runs redis failed: %s", e)
        if not ids:
            ids = self._mem_index[:limit]
        runs = [r for rid in ids if (r := await self.get(rid)) is not None]
        return runs

    async def add_event(self, run_id: str, *, step: str, detail: str, actor: str) -> None:
        event = RunEvent(ts=datetime.now(UTC), step=step, detail=detail, actor=actor)
        blob = event.model_dump_json()
        self._mem_events.setdefault(run_id, []).append(blob)
        if self._r is None:
            return
        try:
            await self._r.rpush(_events_key(run_id), blob)
        except _ERRORS as e:
            logger.warning("RunStore.add_event redis failed: %s", e)

    async def list_events(self, run_id: str) -> list[RunEvent]:
        raws: list[str] = []
        if self._r is not None:
            try:
                raws = await self._r.lrange(_events_key(run_id), 0, -1)
            except _ERRORS as e:
                logger.warning("RunStore.list_events redis failed: %s", e)
        if not raws:
            raws = self._mem_events.get(run_id, [])
        events = []
        for raw in raws:
            with contextlib.suppress(Exception):
                events.append(RunEvent.model_validate_json(raw))
        return events
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_run_store.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add substrateos-api/app/domain/workflow.py substrateos-api/app/workflows/ substrateos-api/tests/test_run_store.py
git commit -m "feat(workflows): RefundRun/RunEvent/RefundDecision models + Redis RunStore"
```

---

### Task 3: RefundEngine (retrieval + LLM decision)

**Files:**
- Create: `substrateos-api/app/workflows/engine.py`
- Test: `substrateos-api/tests/test_refund_engine.py`

- [ ] **Step 1: Write the failing test**

Create `substrateos-api/tests/test_refund_engine.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate
from app.workflows.engine import RefundEngine, RefundEngineError


def _candidate(title: str, content: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(chunk=Chunk(
        chunk_id=f"c-{title}", doc_id=f"d-{title}", tenant_id="t-test", source="uploaded",
        source_url="https://example.com", title=title, content=content,
        acl_principals=["t-test:everyone"], created_at=now, modified_at=now, chunk_index=0,
    ))


class _FakeRetriever:
    def __init__(self):
        self.queries: list[str] = []

    async def retrieve(self, *, query, user, k=30, timer=None):
        self.queries.append(query)
        return [
            _candidate("Order #48213", "Order #48213 · Priya Sharma · $1,200 · placed 45 days ago"),
            _candidate("Refund Policy v3", "Auto-approve only when amount <= $500 AND age <= 30 days"),
        ]


class _FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.messages = None

    async def complete(self, *, messages, deployment=None, temperature=0.0, max_tokens=800):
        self.messages = messages
        return self.reply


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@substrateos",
                display_name="Bot", group_ids={"t-test:everyone"})


_DECISION = {
    "found": True, "order_id": "48213", "customer": "Priya Sharma",
    "amount_usd": 1200, "order_age_days": 45,
    "policy_limit_usd": 500, "policy_limit_days": 30,
    "auto_approve": False,
    "reasoning": "Amount and age exceed the auto-approve limits in refund policy v3.",
}


@pytest.mark.asyncio
async def test_evaluate_parses_decision():
    llm = _FakeLLM(json.dumps(_DECISION))
    retriever = _FakeRetriever()
    engine = RefundEngine(retriever=retriever, llm=llm)
    d = await engine.evaluate("refund $1,200 on order #48213", user=_user())
    assert d.found is True
    assert d.auto_approve is False
    assert d.order_id == "48213"
    assert d.amount_usd == 1200
    # two retrievals: the request text and the policy lookup
    assert len(retriever.queries) == 2
    assert "refund policy" in retriever.queries[1].lower()
    # context + today's date reach the LLM
    user_msg = llm.messages[-1]["content"]
    assert "Order #48213" in user_msg
    assert "Today's date" in user_msg


@pytest.mark.asyncio
async def test_evaluate_handles_json_wrapped_in_prose():
    llm = _FakeLLM("Sure! Here is the result:\n```json\n" + json.dumps(_DECISION) + "\n```")
    engine = RefundEngine(retriever=_FakeRetriever(), llm=llm)
    d = await engine.evaluate("refund order 48213", user=_user())
    assert d.found is True


@pytest.mark.asyncio
async def test_evaluate_raises_on_garbage():
    llm = _FakeLLM("I cannot help with that.")
    engine = RefundEngine(retriever=_FakeRetriever(), llm=llm)
    with pytest.raises(RefundEngineError):
        await engine.evaluate("refund order 48213", user=_user())


@pytest.mark.asyncio
async def test_evaluate_dedupes_chunks():
    class _DupRetriever(_FakeRetriever):
        async def retrieve(self, *, query, user, k=30, timer=None):
            c = _candidate("Refund Policy v3", "policy text")
            return [c, c]

    llm = _FakeLLM(json.dumps(_DECISION))
    engine = RefundEngine(retriever=_DupRetriever(), llm=llm)
    await engine.evaluate("refund", user=_user())
    assert llm.messages[-1]["content"].count("[Refund Policy v3]") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_refund_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: app.workflows.engine`

- [ ] **Step 3: Implement the engine**

Create `substrateos-api/app/workflows/engine.py`:

```python
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from app.domain.identity import User
from app.domain.workflow import RefundDecision
from app.orchestrator.timing import StageTimer

logger = logging.getLogger(__name__)

_POLICY_QUERY = "refund policy auto-approve limits manager approval"

DECISION_PROMPT = (
    "You are SubStrateOS running the Acme refund playbook (refund_v1). "
    "Use ONLY the provided context documents (order records and the refund policy) to "
    "evaluate the refund request. Extract the facts and decide whether the refund can be "
    "auto-approved under the policy. Compute the order age in days from the order date "
    "and today's date when the age is not stated explicitly.\n"
    "Respond ONLY with valid JSON, no other text:\n"
    '{"found": true, "order_id": "...", "customer": "...", "amount_usd": 0, '
    '"order_age_days": 0, "policy_limit_usd": 0, "policy_limit_days": 0, '
    '"auto_approve": true, "reasoning": "one sentence citing the policy"}\n'
    'If the order cannot be found in the context documents, respond with '
    '{"found": false, "reasoning": "..."}.'
)


class RefundEngineError(Exception):
    """The LLM reply could not be parsed into a RefundDecision."""


class RefundEngine:
    """Gathers grounded facts and makes the (LLM-driven) refund decision."""

    def __init__(self, *, retriever, llm) -> None:
        self._retriever = retriever
        self._llm = llm

    async def evaluate(self, text: str, *, user: User) -> RefundDecision:
        timer = StageTimer()
        order_hits = await self._retriever.retrieve(query=text, user=user, k=6, timer=timer)
        policy_hits = await self._retriever.retrieve(
            query=_POLICY_QUERY, user=user, k=4, timer=timer
        )
        seen: set[str] = set()
        parts: list[str] = []
        for cand in [*order_hits, *policy_hits]:
            ch = cand.chunk
            if ch.chunk_id in seen:
                continue
            seen.add(ch.chunk_id)
            parts.append(f"[{ch.title}]\n{ch.content}")
        context = "\n\n".join(parts[:8]) or "(no documents found)"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        messages = [
            {"role": "system", "content": DECISION_PROMPT},
            {"role": "user", "content": (
                f"Today's date: {today}\n\nContext documents:\n{context}\n\n"
                f"Refund request: {text}"
            )},
        ]
        raw = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=500)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("Refund engine: no JSON in LLM reply: %r", raw[:200])
            raise RefundEngineError("no JSON in LLM reply")
        try:
            return RefundDecision.model_validate(json.loads(match.group(0)))
        except Exception as e:  # noqa: BLE001
            logger.warning("Refund engine: unparseable decision: %s", e)
            raise RefundEngineError(str(e)) from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_refund_engine.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/workflows/engine.py substrateos-api/tests/test_refund_engine.py
git commit -m "feat(workflows): RefundEngine — grounded retrieval + structured LLM decision"
```

---

### Task 4: Slack helper + refund Block Kit cards

**Files:**
- Modify: `substrateos-api/app/bots/slack.py` (append `slack_call`)
- Create: `substrateos-api/app/bots/refund_cards.py`
- Test: extend `substrateos-api/tests/test_refund_flow.py` is Task 5; cards get direct asserts here in `substrateos-api/tests/test_refund_cards.py`

- [ ] **Step 1: Write the failing card tests**

Create `substrateos-api/tests/test_refund_cards.py`:

```python
from __future__ import annotations

import json

from app.bots.refund_cards import (
    approval_dm_blocks,
    auto_approved_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.domain.workflow import RefundDecision

_D = RefundDecision(
    found=True, order_id="48213", customer="Priya Sharma", amount_usd=1200,
    order_age_days=45, policy_limit_usd=500, policy_limit_days=30,
    auto_approve=False, reasoning="Over the auto-approve limit of $500 / 30 days.",
)


def _text(blocks: list[dict]) -> str:
    return json.dumps(blocks)


def test_needs_approval_blocks_mentions_facts_and_approver():
    blocks = needs_approval_blocks(_D, approver_label="Diana Foster", run_id="RB-4471")
    s = _text(blocks)
    assert "Needs approval" in s
    assert "$1,200" in s
    assert "Diana Foster" in s
    assert "RB-4471" in s


def test_approval_dm_blocks_have_buttons_with_run_id():
    blocks = approval_dm_blocks(_D, requester_name="Tom Reyes", run_id="RB-4471")
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions) == 1
    ids = {e["action_id"]: e["value"] for e in actions[0]["elements"]}
    assert ids == {"refund_approve": "RB-4471", "refund_reject": "RB-4471"}
    assert "Tom Reyes" in _text(blocks)


def test_decided_dm_blocks_no_buttons():
    blocks = decided_dm_blocks(_D, approved=True, approver_name="Diana Foster")
    s = _text(blocks)
    assert "Approved by Diana Foster" in s
    assert not [b for b in blocks if b.get("type") == "actions"]


def test_outcome_blocks_approved_and_rejected():
    ok = _text(outcome_blocks(_D, approved=True, approver_name="Diana Foster"))
    assert "Approved" in ok and "issued" in ok
    no = _text(outcome_blocks(_D, approved=False, approver_name="Diana Foster"))
    assert "Rejected" in no


def test_auto_approved_blocks():
    d = _D.model_copy(update={"auto_approve": True, "amount_usd": 89.0, "order_id": "48190"})
    s = _text(auto_approved_blocks(d, run_id="RB-4472"))
    assert "Auto-approved" in s and "$89" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_refund_cards.py -v`
Expected: FAIL — `ModuleNotFoundError: app.bots.refund_cards`

- [ ] **Step 3: Implement cards + slack_call**

Create `substrateos-api/app/bots/refund_cards.py`:

```python
from __future__ import annotations

from app.domain.workflow import RefundDecision


def _usd(v: float | None) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def _facts_fields(d: RefundDecision) -> dict:
    return {
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Customer*\n{d.customer or '—'}"},
            {"type": "mrkdwn", "text": f"*Order*\n#{d.order_id or '—'}"},
            {"type": "mrkdwn", "text": f"*Amount*\n{_usd(d.amount_usd)}"},
            {"type": "mrkdwn", "text": f"*Age*\n{d.order_age_days} days"},
        ],
    }


def needs_approval_blocks(d: RefundDecision, *, approver_label: str, run_id: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f":warning: *I can't auto-approve this one.*  `Needs approval` · run {run_id}"}},
        _facts_fields(d),
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Why:* {d.reasoning}\n*What I'm doing:* Routed to *{approver_label}* for approval. I'll update here."}},
    ]


def auto_approved_blocks(d: RefundDecision, *, run_id: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f":white_check_mark: *Auto-approved within policy.* · run {run_id}"}},
        _facts_fields(d),
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Why:* {d.reasoning}\n*Done:* Refund of {_usd(d.amount_usd)} issued to "
                 f"{d.customer} on order #{d.order_id}. Recorded in the audit log."}},
    ]


def approval_dm_blocks(d: RefundDecision, *, requester_name: str, run_id: str) -> list[dict]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Refund needs your approval"}},
        _facts_fields(d),
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Requested by:* {requester_name}\n*Reason:* {d.reasoning}\n_run {run_id}_"}},
        {"type": "actions", "elements": [
            {"type": "button", "style": "primary", "action_id": "refund_approve",
             "value": run_id, "text": {"type": "plain_text", "text": "Approve"}},
            {"type": "button", "style": "danger", "action_id": "refund_reject",
             "value": run_id, "text": {"type": "plain_text", "text": "Reject"}},
        ]},
    ]


def decided_dm_blocks(d: RefundDecision, *, approved: bool, approver_name: str) -> list[dict]:
    verdict = "Approved" if approved else "Rejected"
    icon = ":white_check_mark:" if approved else ":x:"
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"{icon} *{verdict} by {approver_name}*\nRefund of {_usd(d.amount_usd)} on "
                 f"order #{d.order_id} · decision recorded in the audit log."}},
    ]


def outcome_blocks(d: RefundDecision, *, approved: bool, approver_name: str) -> list[dict]:
    if approved:
        text = (f":white_check_mark: *Approved* by *{approver_name}*.\nRefund of "
                f"{_usd(d.amount_usd)} issued to {d.customer} on order #{d.order_id}. "
                "Confirmation sent to the customer; the full record is in the audit log.")
    else:
        text = (f":x: *Rejected* by *{approver_name}*.\nThe refund of {_usd(d.amount_usd)} on "
                f"order #{d.order_id} was declined. The decision is recorded in the audit log.")
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
```

Append to `substrateos-api/app/bots/slack.py` (after `post_slack_reply`):

```python
async def slack_call(token: str, method: str, payload: dict) -> dict | None:
    """POST a Slack Web API method; return the body when ok=true, else None.

    Slack returns HTTP 200 even for API errors — `ok` in the body is the truth.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://slack.com/api/{method}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10.0,
            )
        body = resp.json()
        if not body.get("ok"):
            logger.warning("Slack %s failed: %s", method, body.get("error", "unknown_error"))
            return None
        return body
    except Exception:  # noqa: BLE001
        logger.exception("Slack %s request failed", method)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_refund_cards.py tests/test_bots.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/bots/refund_cards.py substrateos-api/app/bots/slack.py substrateos-api/tests/test_refund_cards.py
git commit -m "feat(bots): refund Block Kit cards + generic slack_call helper"
```

---

### Task 5: RefundFlow (Slack-side orchestration)

**Files:**
- Create: `substrateos-api/app/workflows/flow.py`
- Modify: `substrateos-api/app/config.py` (add setting)
- Test: `substrateos-api/tests/test_refund_flow.py`

- [ ] **Step 1: Add the config setting**

In `substrateos-api/app/config.py`, below `slack_signing_secret`:

```python
    slack_refund_approver_id: str | None = None  # SLACK_REFUND_APPROVER_ID — Diana's Slack member ID
```

- [ ] **Step 2: Write the failing flow tests**

Create `substrateos-api/tests/test_refund_flow.py`:

```python
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

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


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@substrateos",
                display_name="Bot", group_ids={"t-test:everyone"})


def _flow(decision=None, error=False):
    engine = AsyncMock()
    if error:
        engine.evaluate.side_effect = RefundEngineError("boom")
    else:
        engine.evaluate.return_value = decision
    store = RunStore(client=None, force_memory=True)
    return RefundFlow(engine=engine, store=store), store


def _slack_recorder():
    """Patchable fake slack_call that records (method, payload) and returns canned bodies."""
    calls: list[tuple[str, dict]] = []

    async def fake(token, method, payload):
        calls.append((method, payload))
        if method == "users.info":
            uid = payload.get("user")
            names = {"U_TOM": "Tom Reyes", "U_DIANA": "Diana Foster"}
            return {"ok": True, "user": {"real_name": names.get(uid, "Someone"),
                                         "profile": {"display_name": ""}}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "111.222", "channel": payload["channel"]}
        return {"ok": True}

    return calls, fake


@pytest.mark.asyncio
async def test_needs_approval_path(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_REFUND_APPROVER_ID", "U_DIANA")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund $1,200 order 48213", channel="C_REFUNDS",
                                  thread_ts="100.1", requester_slack_id="U_TOM", user=_user())
    runs = await store.list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "pending_approval"
    assert run.dm_channel == "D_DIANA"
    assert run.dm_ts == "111.222"
    methods = [m for m, _ in calls]
    # ack + decision card to channel, users.info x2, conversations.open, DM card
    assert methods.count("chat.postMessage") == 3
    assert "conversations.open" in methods
    events = await store.list_events(run.id)
    steps = [e.step for e in events]
    assert steps == ["Request received", "Facts gathered", "Rule evaluated", "Routed for approval"]


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
    assert steps == ["Request received", "Facts gathered", "Rule evaluated",
                     "Auto-approved", "Refund issued"]
    assert not any(m == "conversations.open" for m, _ in calls)


@pytest.mark.asyncio
async def test_engine_error_marks_run(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(error=True)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id=None, user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "error"


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
                                  requester_slack_id=None, user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "completed"
    assert "Order not found" in [e.step for e in await store.list_events(run.id)]


@pytest.mark.asyncio
async def test_no_approver_configured_still_posts_card(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_REFUND_APPROVER_ID", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund", channel="C", thread_ts=None,
                                  requester_slack_id="U_TOM", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval"
    assert run.dm_channel is None
    assert not any(m == "conversations.open" for m, _ in calls)


@pytest.mark.asyncio
async def test_handle_action_approve(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await store.create(requester_name="Tom Reyes", requester_slack_id="U_TOM",
                             channel="C_REFUNDS", thread_ts="100.1")
    run.decision = _OVER_LIMIT
    run.status = "pending_approval"
    run.dm_channel, run.dm_ts = "D_DIANA", "111.222"
    await store.save(run)
    payload = {
        "type": "block_actions",
        "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
        "actions": [{"action_id": "refund_approve", "value": run.id}],
    }
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)
    loaded = await store.get(run.id)
    assert loaded.status == "completed"
    assert loaded.approver_name == "Diana Foster"
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Approved" in steps and "Refund issued" in steps
    methods = [m for m, _ in calls]
    assert "chat.update" in methods            # DM card replaced
    assert "chat.postMessage" in methods       # outcome to origin channel


@pytest.mark.asyncio
async def test_handle_action_reject(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    run = await store.create(requester_name="Tom", requester_slack_id=None,
                             channel="C", thread_ts=None)
    run.decision = _OVER_LIMIT
    run.status = "pending_approval"
    await store.save(run)
    payload = {
        "type": "block_actions",
        "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
        "actions": [{"action_id": "refund_reject", "value": run.id}],
    }
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)
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
    run = await store.create(requester_name="Tom", requester_slack_id=None,
                             channel="C", thread_ts=None)
    run.decision = _OVER_LIMIT
    run.status = "pending_approval"
    run.dm_channel, run.dm_ts = "D_DIANA", "111.222"
    await store.save(run)
    payload = {
        "type": "block_actions",
        "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_DIANA", "message_ts": "111.222"},
        "actions": [{"action_id": "refund_approve", "value": run.id}],
    }
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)
        n_events = len(await store.list_events(run.id))
        await flow.handle_action(payload)  # second click
    assert len(await store.list_events(run.id)) == n_events  # no duplicate audit entries


@pytest.mark.asyncio
async def test_handle_action_unknown_run_is_noop(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    payload = {"type": "block_actions", "user": {"id": "U_DIANA"},
               "actions": [{"action_id": "refund_approve", "value": "RB-0000"}]}
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_action(payload)  # must not raise
    assert calls == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_refund_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: app.workflows.flow`

- [ ] **Step 4: Implement RefundFlow**

Create `substrateos-api/app/workflows/flow.py`:

```python
from __future__ import annotations

import logging

from app.bots.refund_cards import (
    approval_dm_blocks,
    auto_approved_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.bots.slack import slack_call
from app.config import get_settings
from app.domain.identity import User
from app.workflows.engine import RefundEngine, RefundEngineError
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)

_ERROR = "Sorry, I couldn't evaluate that refund request right now. Please try again."


class RefundFlow:
    """Drives the refund playbook over Slack: ack → evaluate → act/route → decide."""

    def __init__(self, *, engine: RefundEngine, store: RunStore) -> None:
        self._engine = engine
        self._store = store

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _display_name(self, token: str, slack_user_id: str | None) -> str | None:
        if not slack_user_id:
            return None
        body = await slack_call(token, "users.info", {"user": slack_user_id})
        if not body:
            return None
        u = body.get("user") or {}
        profile = u.get("profile") or {}
        return profile.get("display_name") or u.get("real_name") or u.get("name")

    async def _post(self, token: str, channel: str, thread_ts: str | None,
                    *, text: str, blocks: list[dict] | None = None) -> dict | None:
        payload: dict = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return await slack_call(token, "chat.postMessage", payload)

    # ── inbound request (from the Slack webhook) ─────────────────────────────

    async def handle_request(self, *, text: str, channel: str, thread_ts: str | None,
                             requester_slack_id: str | None, user: User) -> None:
        s = get_settings()
        token = s.slack_bot_token or ""
        requester = await self._display_name(token, requester_slack_id) or "Support agent"
        run = await self._store.create(
            requester_name=requester, requester_slack_id=requester_slack_id,
            channel=channel, thread_ts=thread_ts,
        )
        await self._store.add_event(
            run.id, step="Request received",
            detail=f"{text[:160]} · from Slack", actor=requester,
        )
        first = requester.split()[0]
        await self._post(token, channel, thread_ts,
                         text=f"On it, {first} — pulling up the order and checking the refund policy…")

        try:
            decision = await self._engine.evaluate(text, user=user)
        except RefundEngineError:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail="Could not evaluate the request", actor="SubStrateOS")
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        run.decision = decision
        if not decision.found:
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Order not found",
                                        detail=decision.reasoning, actor="SubStrateOS")
            await self._post(token, channel, thread_ts,
                             text=f"I couldn't find that order in our records. {decision.reasoning}")
            return

        await self._store.add_event(
            run.id, step="Facts gathered",
            detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                    f"age {decision.order_age_days} days · customer {decision.customer}"),
            actor="SubStrateOS",
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
                actor="SubStrateOS",
            )
            await self._post(token, channel, thread_ts,
                             text="Auto-approved within policy — refund issued.",
                             blocks=auto_approved_blocks(decision, run_id=run.id))
            return

        # Needs approval — route to the configured manager.
        run.status = "pending_approval"
        await self._store.save(run)
        approver_id = s.slack_refund_approver_id
        approver_label = "a Support Manager"
        if approver_id:
            approver_label = await self._display_name(token, approver_id) or "Support Manager"
        await self._store.add_event(run.id, step="Routed for approval",
                                    detail=f"Sent to {approver_label} in Slack", actor="SubStrateOS")
        await self._post(token, channel, thread_ts,
                         text="I can't auto-approve this one — routing for approval.",
                         blocks=needs_approval_blocks(decision, approver_label=approver_label,
                                                      run_id=run.id))
        if not approver_id:
            logger.warning("SLACK_REFUND_APPROVER_ID not configured; run %s waits", run.id)
            return
        opened = await slack_call(token, "conversations.open", {"users": approver_id})
        dm = ((opened or {}).get("channel") or {}).get("id")
        if not dm:
            await self._post(token, channel, thread_ts,
                             text="I couldn't reach the approver in a DM — please review manually.")
            return
        posted = await slack_call(token, "chat.postMessage", {
            "channel": dm, "text": "Refund needs your approval",
            "blocks": approval_dm_blocks(decision, requester_name=requester, run_id=run.id),
        })
        if posted:
            run.dm_channel = dm
            run.dm_ts = posted.get("ts")
            await self._store.save(run)

    # ── button clicks (from /bot/slack/interactive) ──────────────────────────

    async def handle_action(self, payload: dict) -> None:
        s = get_settings()
        token = s.slack_bot_token or ""
        actions = payload.get("actions") or []
        if not actions:
            return
        action = actions[0]
        action_id = action.get("action_id")
        if action_id not in ("refund_approve", "refund_reject"):
            return
        run_id = action.get("value") or ""
        run = await self._store.get(run_id)
        if run is None or run.decision is None:
            logger.warning("refund action for unknown run %r", run_id)
            return
        approver_id = (payload.get("user") or {}).get("id")
        container = payload.get("container") or {}
        dm_channel = run.dm_channel or container.get("channel_id")
        dm_ts = run.dm_ts or container.get("message_ts")

        if run.status != "pending_approval":
            # Idempotent: re-render the decided card, change nothing.
            if dm_channel and dm_ts:
                await slack_call(token, "chat.update", {
                    "channel": dm_channel, "ts": dm_ts,
                    "text": f"Refund {run.status}",
                    "blocks": decided_dm_blocks(run.decision,
                                                approved=(run.status in ("approved", "completed")),
                                                approver_name=run.approver_name or "a manager"),
                })
            return

        approved = action_id == "refund_approve"
        approver_name = (await self._display_name(token, approver_id)
                         or (payload.get("user") or {}).get("name") or "Manager")
        run.status = "approved" if approved else "rejected"
        run.approver_name = approver_name
        await self._store.save(run)
        d = run.decision
        await self._store.add_event(
            run.id, step="Approved" if approved else "Rejected",
            detail=(f"Manager {'approved' if approved else 'rejected'} the over-limit refund "
                    f"of ${d.amount_usd:,.0f} on order #{d.order_id}"),
            actor=approver_name,
        )
        if dm_channel and dm_ts:
            await slack_call(token, "chat.update", {
                "channel": dm_channel, "ts": dm_ts,
                "text": f"Refund {'approved' if approved else 'rejected'}",
                "blocks": decided_dm_blocks(d, approved=approved, approver_name=approver_name),
            })
        if approved:
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=f"${d.amount_usd:,.0f} refunded to {d.customer} · confirmation sent",
                actor="SubStrateOS",
            )
            run.status = "completed"
            await self._store.save(run)
        if run.channel:
            await self._post(token, run.channel, run.thread_ts,
                             text=f"Refund {'approved' if approved else 'rejected'} by {approver_name}",
                             blocks=outcome_blocks(d, approved=approved, approver_name=approver_name))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_refund_flow.py tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add substrateos-api/app/workflows/flow.py substrateos-api/app/config.py substrateos-api/tests/test_refund_flow.py
git commit -m "feat(workflows): RefundFlow — Slack ack/decision/DM-approval orchestration"
```

---

### Task 6: Wire Slack webhook to the workflow (+ app wiring)

**Files:**
- Modify: `substrateos-api/app/api/bots.py` (slack_webhook only)
- Modify: `substrateos-api/app/deps.py`
- Modify: `substrateos-api/app/main.py`
- Test: extend `substrateos-api/tests/test_bots_api.py` (new tests appended)

- [ ] **Step 1: Write the failing webhook tests**

Append to `substrateos-api/tests/test_bots_api.py`:

```python
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
```

Note: `_FakeOrchestrator.answer` must accept the new `skill_context` kwarg — update it at the top of the file to:

```python
class _FakeOrchestrator:
    async def answer(self, request, *, user, user_token=None, skill_context=None):
        return Answer(text="Here is the answer.", citations=[], query_id="q1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_bots_api.py -v -k refund_workflow`
Expected: FAIL — `ImportError: cannot import name 'get_refund_flow'`

- [ ] **Step 3: Implement wiring**

In `substrateos-api/app/deps.py`, append:

```python
def get_run_store(request: Request):
    return getattr(request.app.state, "run_store", None)


def get_refund_flow(request: Request):
    return getattr(request.app.state, "refund_flow", None)
```

In `substrateos-api/app/main.py`, after the `skill_router_svc` block (around line 152–155), add:

```python
    app.state.run_store = RunStore()
    app.state.refund_flow = RefundFlow(
        engine=RefundEngine(retriever=app.state.retriever, llm=app.state.llm),
        store=app.state.run_store,
    )
```

with imports at the top:

```python
from app.workflows.engine import RefundEngine
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore
```

In the shutdown section (near `await app.state.orchestrator.aclose()`), add `await app.state.run_store.aclose()`.

In `substrateos-api/app/api/bots.py`:

1. Add imports: `from app.deps import get_connection_store, get_orchestrator, get_refund_flow, get_skill_router_svc` (extend the existing deps import) and `import contextlib`.
2. Extend `slack_webhook` signature with:
   ```python
   skill_router=Depends(get_skill_router_svc),
   refund_flow=Depends(get_refund_flow),
   ```
3. Capture the Slack user before the background task: after `thread_ts = ...` add `slack_user = event.get("user")`.
4. Replace the `try/except` orchestrator block inside `_reply` with:

```python
        skill_ctx = None
        if skill_router is not None:
            with contextlib.suppress(Exception):
                skill_ctx = await skill_router.resolve_skill(text)
        if (skill_ctx is not None and getattr(skill_ctx, "workflow", None) == "refund"
                and refund_flow is not None):
            try:
                await refund_flow.handle_request(
                    text=skill_ctx.clean_query, channel=channel, thread_ts=thread_ts,
                    requester_slack_id=slack_user, user=_bot_user(),
                )
            except Exception:
                logger.exception("Refund workflow failed")
                answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")
                await post_slack_reply(slack_token, channel, thread_ts, answer)
            return
        try:
            effective = skill_ctx.clean_query if skill_ctx else text
            answer = await orchestrator.answer(
                QueryRequest(query=effective), user=_bot_user(), skill_context=skill_ctx
            )
        except Exception:
            logger.exception("Slack bot query failed")
            answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")
        await post_slack_reply(slack_token, channel, thread_ts, answer)
```

(This also gives the Slack bot ordinary skill routing — non-workflow skills now inject their prompt, matching `/query`.)

- [ ] **Step 4: Run tests to verify they pass (full bots suite)**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_bots_api.py tests/test_bots.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/api/bots.py substrateos-api/app/deps.py substrateos-api/app/main.py substrateos-api/tests/test_bots_api.py
git commit -m "feat(bots): skill routing in Slack webhook + refund workflow divert"
```

---

### Task 7: Slack interactivity endpoint

**Files:**
- Modify: `substrateos-api/app/api/bots.py` (new endpoint)
- Test: `substrateos-api/tests/test_slack_interactive.py`

- [ ] **Step 1: Write the failing tests**

Create `substrateos-api/tests/test_slack_interactive.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_slack_interactive.py -v`
Expected: FAIL — 404 (endpoint does not exist)

- [ ] **Step 3: Implement the endpoint**

In `substrateos-api/app/api/bots.py`, add `from urllib.parse import parse_qs, urlparse` (extend the existing `urlparse` import) and append after `slack_webhook`:

```python
@router.post("/bot/slack/interactive")
async def slack_interactive(
    request: Request,
    background_tasks: BackgroundTasks,
    refund_flow=Depends(get_refund_flow),
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> dict:
    """Slack interactivity (button clicks). Must ack within 3s — work runs in background."""
    raw_body = await request.body()
    s = get_settings()
    if not s.slack_bot_token or not s.slack_signing_secret:
        raise HTTPException(status_code=503, detail="Slack bot not configured")
    if not verify_slack_signature(
        s.slack_signing_secret, x_slack_request_timestamp or "", raw_body, x_slack_signature or ""
    ):
        raise HTTPException(status_code=403, detail="invalid signature")
    payload_raw = (parse_qs(raw_body.decode()).get("payload") or ["{}"])[0]
    try:
        payload = json.loads(payload_raw)
    except ValueError:
        return {}
    if payload.get("type") != "block_actions" or refund_flow is None:
        return {}
    background_tasks.add_task(refund_flow.handle_action, payload)
    return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_slack_interactive.py tests/test_bots_api.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/api/bots.py substrateos-api/tests/test_slack_interactive.py
git commit -m "feat(bots): /bot/slack/interactive endpoint for Approve/Reject buttons"
```

---

### Task 8: Runs API

**Files:**
- Create: `substrateos-api/app/api/runs.py`
- Modify: `substrateos-api/app/main.py` (include router)
- Test: `substrateos-api/tests/test_runs_api.py`

- [ ] **Step 1: Write the failing tests**

Create `substrateos-api/tests/test_runs_api.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.deps import get_run_store
from app.domain.workflow import RefundDecision
from app.main import app
from app.workflows.store import RunStore

_DEBUG = {"x-debug-bypass-auth": "t-test,u-tom,t-test:everyone"}


@pytest.fixture
def store():
    s = RunStore(client=None, force_memory=True)
    app.dependency_overrides[get_run_store] = lambda: s
    yield s
    app.dependency_overrides.clear()


async def _seed(store: RunStore):
    run = await store.create(requester_name="Tom Reyes", requester_slack_id="U_TOM",
                             channel="C", thread_ts=None)
    run.decision = RefundDecision(found=True, order_id="48213", customer="Priya Sharma",
                                  amount_usd=1200, order_age_days=45, policy_limit_usd=500,
                                  policy_limit_days=30, auto_approve=False, reasoning="over limit")
    run.status = "pending_approval"
    await store.save(run)
    await store.add_event(run.id, step="Request received", detail="d", actor="Tom Reyes")
    return run


def test_list_runs(store):
    import asyncio
    run = asyncio.get_event_loop().run_until_complete(_seed(store))
    with TestClient(app) as client:
        resp = client.get("/runs", headers=_DEBUG)
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["id"] == run.id
    assert body[0]["status"] == "pending_approval"
    assert body[0]["decision"]["customer"] == "Priya Sharma"


def test_get_run_with_events(store):
    import asyncio
    run = asyncio.get_event_loop().run_until_complete(_seed(store))
    with TestClient(app) as client:
        resp = client.get(f"/runs/{run.id}", headers=_DEBUG)
    assert resp.status_code == 200
    body = resp.json()
    assert body["run"]["id"] == run.id
    assert body["events"][0]["step"] == "Request received"


def test_get_run_404(store):
    with TestClient(app) as client:
        resp = client.get("/runs/RB-0000", headers=_DEBUG)
    assert resp.status_code == 404


def test_runs_require_auth(store):
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code in (401, 403)
```

Note on the asyncio fixture seeding: if `asyncio.get_event_loop()` is deprecated/fails on the test runner, use `asyncio.new_event_loop().run_until_complete(...)` or make the tests `@pytest.mark.asyncio` and call the endpoint via `TestClient` inside; keep it simple and consistent with how other tests in this repo seed async stores (check `tests/test_skills_api.py` for the established pattern and mirror it).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_runs_api.py -v`
Expected: FAIL — 404 (no /runs route)

- [ ] **Step 3: Implement the API**

Create `substrateos-api/app/api/runs.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api._auth_resolve import resolve_user
from app.deps import get_run_store, get_token_store

router = APIRouter(tags=["runs"])


async def _require_user(
    authorization: str | None,
    x_debug_bypass_auth: str | None,
    x_ms_client_principal: str | None,
    token_store,
):
    return await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )


@router.get("/runs")
async def list_runs(
    run_store=Depends(get_run_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[dict]:
    await _require_user(authorization, x_debug_bypass_auth, x_ms_client_principal, token_store)
    if run_store is None:
        return []
    runs = await run_store.list_runs(limit=50)
    return [r.model_dump(mode="json") for r in runs]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    run_store=Depends(get_run_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> dict:
    await _require_user(authorization, x_debug_bypass_auth, x_ms_client_principal, token_store)
    if run_store is None:
        raise HTTPException(status_code=404, detail="run not found")
    run = await run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = await run_store.list_events(run_id)
    return {"run": run.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in events]}
```

Check `app/api/_auth_resolve.py` first: confirm `resolve_user` raises an HTTPException (401/403) when no auth is supplied — the `test_runs_require_auth` test depends on it (this is the same guard `/query` uses).

In `substrateos-api/app/main.py`: import `from app.api.runs import router as runs_router` (match the existing import style at the top) and add `app.include_router(runs_router)` next to the other `include_router` lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_runs_api.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/api/runs.py substrateos-api/app/main.py substrateos-api/tests/test_runs_api.py
git commit -m "feat(api): GET /runs and GET /runs/{id} audit endpoints"
```

---

### Task 9: Seed script (orders + policy + skill)

**Files:**
- Create: `substrateos-api/scripts/seed_refund_demo.py`

No unit test — it's an operational script; it is exercised by running it against the live API (Task 11).

- [ ] **Step 1: Implement the script**

Create `substrateos-api/scripts/seed_refund_demo.py`:

```python
"""Seed the Refund Experience demo: mock orders + refund policy into AI Search,
and create/refresh the `refund` workflow skill.

Usage:
    python scripts/seed_refund_demo.py --api http://localhost:8000 \
        --admin-key $ADMIN_KEY --tenant $TENANT_ID

The API must be running with real AI Search + OpenAI credentials (ingest embeds).
Idempotent: re-ingesting the same doc_id replaces its chunks; the skill is
created or patched.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx

POLICY_BODY = """# Acme Refund Policy (refund-policy v3)

**Version 3 · Owner: D. Rao · Applies to all customer orders**

## Auto-approval rule

A refund may be **auto-approved** by SubStrateOS only when **both** conditions hold:

- Refund amount is **$500 or less**, AND
- The order was placed **30 days ago or less**.

## Manager approval

Any refund **over $500** or on an order **older than 30 days** is OVER LIMIT and
**must be approved by a Support Manager** before it is issued. The approval and
the approver's identity are recorded in the audit log with the decision.

## Process

1. Verify who is asking (requester identity).
2. Gather the facts: order, amount, order age, customer.
3. Check the rules above.
4. Within limits → issue the refund and confirm to the customer.
5. Over limits → hold the action and route to a Support Manager for approval.
"""

ORDER_48213_BODY = """# Order #48213

- **Customer:** Priya Sharma (priya.sharma@example.com)
- **Order ID:** 48213
- **Order total:** $1,200.00
- **Order date:** {d48213}
- **Status:** Delivered
- **Items:** Aurora X2 Standing Desk ($950.00), Ergo Pro Chair Mat ($250.00)
- **Payment method:** Visa ending 4421
- **Notes:** Customer reports the desk motor failed after six weeks of use.
"""

ORDER_48190_BODY = """# Order #48190

- **Customer:** Marcus Lee (marcus.lee@example.com)
- **Order ID:** 48190
- **Order total:** $89.00
- **Order date:** {d48190}
- **Status:** Delivered
- **Items:** Lumen Desk Lamp ($89.00)
- **Payment method:** Mastercard ending 9087
- **Notes:** Customer says the lamp arrived with a cracked shade.
"""

SKILL = {
    "slug": "refund",
    "name": "Refund Processing",
    "description": (
        "Handles customer refund requests end-to-end: looks up the order, checks the "
        "refund policy, auto-approves within limits or routes to a Support Manager "
        "for approval. Use for any question about refunding a customer order."
    ),
    "team": "Support",
    "run_scope": "org",
    "enabled": True,
    "workflow": "refund",
    "steps": [
        "Who's asking — verify the requester",
        "Gather the facts — order, amount, age, customer, policy",
        "Check the rules — auto-approve limits ($500 / 30 days)",
        "Decide — issue the refund or route to a Support Manager",
    ],
    "data_feeds": ["Orders", "Refund policy"],
    "system_prompt": (
        "You are the Acme refund playbook (refund_v1). Ground every statement in the "
        "retrieved order records and refund policy. Never promise a refund that the "
        "policy does not allow."
    ),
}


def _doc(doc_id: str, title: str, body: str, tenant: str) -> dict:
    now = datetime.now(UTC)
    return {
        "doc_id": doc_id, "tenant_id": tenant, "source": "uploaded",
        "source_url": f"https://internal.acme.example/{doc_id}",
        "title": title, "body": body, "author_id": None,
        "acl_principals": [f"{tenant}:everyone"],
        "created_at": now.isoformat(), "modified_at": now.isoformat(),
        "mime": "text/markdown",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default=os.environ.get("API_BASE", "http://localhost:8000"))
    p.add_argument("--admin-key", default=os.environ.get("ADMIN_KEY", ""))
    p.add_argument("--tenant", default=os.environ.get("TENANT_ID", ""))
    args = p.parse_args()
    if not args.admin_key or not args.tenant:
        print("--admin-key and --tenant are required (or ADMIN_KEY / TENANT_ID env)")
        return 1
    headers = {"x-admin-key": args.admin_key}
    today = datetime.now(UTC)
    d48213 = (today - timedelta(days=45)).strftime("%Y-%m-%d")
    d48190 = (today - timedelta(days=12)).strftime("%Y-%m-%d")

    docs = [
        _doc("refund-policy-v3", "Acme Refund Policy v3", POLICY_BODY, args.tenant),
        _doc("order-48213", "Order #48213 — Priya Sharma",
             ORDER_48213_BODY.format(d48213=d48213), args.tenant),
        _doc("order-48190", "Order #48190 — Marcus Lee",
             ORDER_48190_BODY.format(d48190=d48190), args.tenant),
    ]
    with httpx.Client(base_url=args.api, headers=headers, timeout=120.0) as client:
        for doc in docs:
            r = client.post("/ingest", json=doc)
            r.raise_for_status()
            print(f"ingested {doc['doc_id']}: {r.json()}")

        skills = client.get("/admin/skills").json()
        existing = next((s for s in skills if s.get("slug") == "refund"), None)
        if existing:
            patch = {k: v for k, v in SKILL.items() if k != "slug"}
            r = client.patch(f"/admin/skills/{existing['id']}", json=patch)
            r.raise_for_status()
            print(f"updated skill refund (id={existing['id']})")
        else:
            r = client.post("/admin/skills", json=SKILL)
            r.raise_for_status()
            print(f"created skill refund (id={r.json().get('id')})")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Before finalising, check the actual ingest route path in `app/api/admin.py` (the explore notes say `POST /ingest` guarded by the admin key) and `SkillUpdate` accepted fields (Task 1 added `workflow`; `run_scope`/`enabled`/`steps`/`data_feeds` are accepted — `slug` is not updatable, which is why it's excluded from the PATCH body).

- [ ] **Step 2: Syntax-check the script**

Run: `cd substrateos-api && .venv/bin/python -m py_compile scripts/seed_refund_demo.py && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add substrateos-api/scripts/seed_refund_demo.py
git commit -m "feat(scripts): seed_refund_demo — mock orders, refund policy, refund skill"
```

---

### Task 10: Web — Runs view

**Files:**
- Create: `web/lib/runsApi.ts`
- Create: `web/app/runs/page.tsx`
- Modify: `web/components/Chat.tsx`

- [ ] **Step 1: Create the API client**

Create `web/lib/runsApi.ts` (mirror the header logic of `web/lib/skillsApi.ts`):

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export type RefundDecision = {
  found: boolean; order_id: string | null; customer: string | null;
  amount_usd: number | null; order_age_days: number | null;
  policy_limit_usd: number | null; policy_limit_days: number | null;
  auto_approve: boolean; reasoning: string;
};

export type RunSummary = {
  id: string;
  status: "running" | "pending_approval" | "approved" | "rejected" | "completed" | "error";
  requester_name: string;
  approver_name: string | null;
  decision: RefundDecision | null;
  created_at: string;
  updated_at: string;
};

export type RunEvent = { ts: string; step: string; detail: string; actor: string };
export type RunDetail = { run: RunSummary; events: RunEvent[] };

async function easyAuthToken(): Promise<string | null> {
  return fetch("/.auth/me", { credentials: "include" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (Array.isArray(d) && d[0]?.id_token) || null)
    .catch(() => null);
}

async function userHeaders(): Promise<Record<string, string>> {
  if (DEBUG_AUTH) return { "x-debug-bypass-auth": DEBUG_AUTH };
  const t = await easyAuthToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export async function getRuns(): Promise<RunSummary[]> {
  try {
    const resp = await fetch(`${API_BASE}/runs`, { headers: await userHeaders() });
    if (!resp.ok) return [];
    return (await resp.json()) as RunSummary[];
  } catch {
    return [];
  }
}

export async function getRun(id: string): Promise<RunDetail | null> {
  try {
    const resp = await fetch(`${API_BASE}/runs/${id}`, { headers: await userHeaders() });
    if (!resp.ok) return null;
    return (await resp.json()) as RunDetail;
  } catch {
    return null;
  }
}
```

- [ ] **Step 2: Create the Runs page**

Create `web/app/runs/page.tsx` (reuses the existing `.skills-table` styles from `globals.css`; status colors inline):

```tsx
"use client";
import { useEffect, useState } from "react";
import { getRun, getRuns, RunDetail, RunSummary } from "@/lib/runsApi";

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  pending_approval: { bg: "#F5E6D0", fg: "#8a5a12" },
  approved: { bg: "#D8F0E4", fg: "#136345" },
  completed: { bg: "#D8F0E4", fg: "#136345" },
  rejected: { bg: "#FBE3E4", fg: "#8a1f2b" },
  running: { bg: "#E7EEFB", fg: "#1b4fae" },
  error: { bg: "#FBE3E4", fg: "#8a1f2b" },
};

function StatusPill({ status }: { status: string }) {
  const c = STATUS_COLORS[status] ?? { bg: "#eee", fg: "#444" };
  return (
    <span style={{
      background: c.bg, color: c.fg, borderRadius: 12, padding: "2px 10px",
      fontSize: 11, fontWeight: 700, letterSpacing: ".02em", whiteSpace: "nowrap",
    }}>
      {status.replace("_", " ")}
    </span>
  );
}

function usd(v: number | null | undefined): string {
  return v == null ? "—" : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function AuditTable({ detail }: { detail: RunDetail }) {
  return (
    <div style={{ margin: "12px 0 24px" }}>
      <table className="skills-table">
        <thead>
          <tr><th>Time</th><th>Step</th><th>Detail</th><th>Who</th></tr>
        </thead>
        <tbody>
          {detail.events.map((e, i) => (
            <tr key={i}>
              <td style={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                {new Date(e.ts).toLocaleTimeString()}
              </td>
              <td style={{ fontWeight: 600 }}>{e.step}</td>
              <td>{e.detail}</td>
              <td style={{ whiteSpace: "nowrap" }}>{e.actor}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);

  useEffect(() => { getRuns().then(setRuns); }, []);
  useEffect(() => {
    if (!open) { setDetail(null); return; }
    getRun(open).then(setDetail);
  }, [open]);

  return (
    <main className="main">
      <div style={{ padding: "0 28px" }}>
        <div className="skills-page">
          <div className="skills-header">
            <h1>Runs</h1>
            <p>Every workflow run, on the record — who asked, which rule fired, who approved.</p>
          </div>
          {runs.length === 0 && <div className="skills-empty">No runs yet.</div>}
          {runs.length > 0 && (
            <table className="skills-table">
              <thead>
                <tr><th>Run</th><th>Status</th><th>Requested by</th><th>Customer</th>
                    <th>Order</th><th>Amount</th><th>Approver</th><th>When</th></tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <>
                    <tr key={r.id} onClick={() => setOpen(open === r.id ? null : r.id)}
                        style={{ cursor: "pointer" }}>
                      <td style={{ fontWeight: 600 }}>{r.id}</td>
                      <td><StatusPill status={r.status} /></td>
                      <td>{r.requester_name}</td>
                      <td>{r.decision?.customer ?? "—"}</td>
                      <td>{r.decision?.order_id ? `#${r.decision.order_id}` : "—"}</td>
                      <td>{usd(r.decision?.amount_usd)}</td>
                      <td>{r.approver_name ?? "—"}</td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {new Date(r.created_at).toLocaleString()}
                      </td>
                    </tr>
                    {open === r.id && detail && (
                      <tr key={`${r.id}-detail`}>
                        <td colSpan={8}><AuditTable detail={detail} /></td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 3: Wire the nav in Chat.tsx**

In `web/components/Chat.tsx`:

1. Add import next to the SkillsPage import: `import RunsPage from "@/app/runs/page";`
2. Change the view union (around line 417): `useState<"ask" | "discover" | "history" | "skills" | "runs">("ask")`
3. After the Skills nav button (around line 509–510), add a Runs button **copying the exact markup/icon structure of the Skills button** (read the surrounding lines first), e.g.:
   ```tsx
   <button className={view === "runs" ? "active" : ""} onClick={() => setView("runs")}>
     Runs
   </button>
   ```
   (keep whatever icon/span wrapper the sibling buttons use)
4. After `{view === "skills" && <SkillsPage />}` (around line 543), add:
   ```tsx
   {view === "runs" && <RunsPage />}
   ```

- [ ] **Step 4: Type-check / build**

Run: `cd web && pnpm exec tsc --noEmit`
Expected: no errors (pre-existing errors unrelated to runs are acceptable — compare with `git stash`-free baseline if any appear)

- [ ] **Step 5: Commit**

```bash
git add web/lib/runsApi.ts web/app/runs/page.tsx web/components/Chat.tsx
git commit -m "feat(web): Runs view with audit trail table"
```

---

### Task 11: Full suite + live integration test

**Files:**
- Create: `substrateos-api/tests/test_refund_e2e_integration.py`

- [ ] **Step 1: Run the entire backend test suite**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/ -v -m "not integration"`
Expected: ALL PASS (no regressions anywhere)

- [ ] **Step 2: Write the live integration test**

This test uses the real `.env` (real AI Search + LLM) — the conftest skips env
overrides for `integration`-marked tests. Slack HTTP calls are faked. It
requires Task 9's seed script to have been run against the same index first.

Create `substrateos-api/tests/test_refund_e2e_integration.py`:

```python
"""Live e2e: real retrieval + real LLM decision, fake Slack.

Pre-req: scripts/seed_refund_demo.py has been run against the configured index.
Run with: .venv/bin/python -m pytest tests/test_refund_e2e_integration.py -v -m integration
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.identity import User
from app.workflows.engine import RefundEngine
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore

pytestmark = pytest.mark.integration


def _bot_user() -> User:
    tid = get_settings().substrateos_tenant_id
    return User(user_id="bot", tenant_id=tid, email="bot@substrateos",
                display_name="Bot", group_ids={f"{tid}:everyone"})


@pytest.fixture
async def engine():
    from app.generation.azure_openai import AzureOpenAIClient
    from app.generation.gemini import GeminiClient
    from app.retrieval.ai_search_client import AISearchClient
    from app.retrieval.hybrid_retriever import HybridRetriever
    embedder = AzureOpenAIClient()
    search = AISearchClient()
    llm = GeminiClient()
    retriever = HybridRetriever(search=search, embedder=embedder)
    yield RefundEngine(retriever=retriever, llm=llm)
    await embedder.aclose()


@pytest.mark.asyncio
async def test_over_limit_refund_needs_approval(engine):
    d = await engine.evaluate(
        "customer Priya Sharma is asking for a refund of $1,200 on order #48213. "
        "It's been about 45 days. Can we do it?", user=_bot_user(),
    )
    assert d.found is True
    assert d.order_id == "48213"
    assert d.amount_usd == pytest.approx(1200, rel=0.01)
    assert d.auto_approve is False


@pytest.mark.asyncio
async def test_small_recent_refund_auto_approves(engine):
    d = await engine.evaluate(
        "Marcus Lee wants a refund of $89 on order #48190 from about two weeks ago.",
        user=_bot_user(),
    )
    assert d.found is True
    assert d.order_id == "48190"
    assert d.auto_approve is True


@pytest.mark.asyncio
async def test_full_flow_with_fake_slack(engine, monkeypatch):
    """Needs-approval flow end to end: request → DM card → approve click → audit."""
    calls: list[tuple[str, dict]] = []

    async def fake_slack(token, method, payload):
        calls.append((method, payload))
        if method == "users.info":
            return {"ok": True, "user": {"real_name": "Tom Reyes", "profile": {}}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "1.2", "channel": payload["channel"]}
        return {"ok": True}

    monkeypatch.setattr("app.workflows.flow.slack_call", fake_slack)
    monkeypatch.setenv("SLACK_REFUND_APPROVER_ID", "U_DIANA")
    get_settings.cache_clear()

    store = RunStore(client=None, force_memory=True)
    flow = RefundFlow(engine=engine, store=store)
    await flow.handle_request(
        text="refund of $1,200 on order #48213, about 45 days old",
        channel="C_REFUNDS", thread_ts=None, requester_slack_id="U_TOM", user=_bot_user(),
    )
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval"

    await flow.handle_action({
        "type": "block_actions", "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_DIANA", "message_ts": "1.2"},
        "actions": [{"action_id": "refund_approve", "value": run.id}],
    })
    final = await store.get(run.id)
    assert final.status == "completed"
    steps = [e.step for e in await store.list_events(run.id)]
    assert steps == ["Request received", "Facts gathered", "Rule evaluated",
                     "Routed for approval", "Approved", "Refund issued"]
    get_settings.cache_clear()
```

Check `pyproject.toml` / `pytest.ini` for the `integration` marker registration (existing integration tests imply it exists; if not, register it).

- [ ] **Step 3: Seed the demo data**

Start the API locally (real `.env`), then:

```bash
cd substrateos-api && .venv/bin/python -m uvicorn app.main:app --port 8000 &
# wait for startup, then:
.venv/bin/python scripts/seed_refund_demo.py --api http://localhost:8000 \
    --admin-key "$ADMIN_KEY" --tenant "$TENANT_ID"
```

(`ADMIN_KEY` is the local admin key from `.env` — the same one the existing admin endpoints use; `TENANT_ID` is `substrateos_tenant_id` from `.env`.)
Expected: three `ingested ...` lines with `chunks_indexed >= 1`, plus `created skill refund` (or `updated`).

- [ ] **Step 4: Run the integration test**

Run: `cd substrateos-api && .venv/bin/python -m pytest tests/test_refund_e2e_integration.py -v -m integration`
Expected: 3 PASS (real retrieval + LLM; allow ~60s)

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/tests/test_refund_e2e_integration.py
git commit -m "test(workflows): live e2e integration test for the refund flow"
```

---

## Post-implementation (user actions, documented for handoff)

Not tasks for the executor — needed before the live Slack demo:

1. **Slack app config** (api.slack.com → the SubStrateOS app):
   - *Interactivity & Shortcuts* → ON → Request URL `https://<api-host>/bot/slack/interactive`
   - *OAuth scopes* → add `im:write`, `users:read` → reinstall app to workspace
2. **Create test users** Tom (agent) and Diana (manager) in the Slack workspace; invite the bot + both users to `#refunds`.
3. **Set env on the API container**: `SLACK_REFUND_APPROVER_ID=<Diana's member ID>` (Slack profile → Copy member ID).
4. **Seed prod**: run `scripts/seed_refund_demo.py` against the deployed API with the prod admin key + tenant id.
5. Deploy via the `substrateos-deploy` skill (API + web).
