# Governed Act Layer — Reconcile PR #1 onto Current Main — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the governed act layer (PolicyEngine, ApprovalService, AuditLog, seams) from `origin/feat/governed-act-layer` (PR #1) onto current `origin/main` — which now has the user directory, manager-only approval routing, and refund outcome fan-out — then close the gaps PR #1 leaves (directory-bound approver identity, `manager` role vocabulary, audit trail in the runs API, admin receipt UI, PyYAML dependency).

**Architecture:** PR #1's new packages (`app/policy/`, `app/audit/`, `app/approvals/`, `app/connectors/act/`) are purely additive and merge clean. Six files conflict; the resolution rule everywhere is: **main's behavior is the base** (directory routing, customer path, outcome fan-out, hard-deny) and **the act layer's governance is grafted in** (facts/guardrail split, verdict dispatch, durable approval, typed audit). The model extracts facts; `PolicyEngine` decides; `ApprovalService` gates; `AuditLog` records.

**Tech Stack:** FastAPI · Python 3.12 · uv · pytest · Redis (`redis.asyncio`) · PyYAML (newly declared) · Next.js 14 admin panel.

**Branch:** all work on `worktree-platform-approach-investigation` in this worktree. Never push to `main` or `feat/governed-act-layer`.

**Invariant (from the spec):** the refund use case works end-to-end after every task. `cd substrateos-api && uv run pytest tests/ -q` must pass at the end of every task.

---

## Conflict map (computed with `git merge-tree`, 2026-06-07)

| File | Main brought | Act-layer brought | Resolution |
|---|---|---|---|
| `app/deps.py` | directory/github getters | `get_audit_log`, `get_approval_service`, `get_policy_engine` | union — keep both blocks |
| `app/domain/workflow.py` | `customer_email`, handoff fields, `needs_attention`/`routed_to_support`, `approver_slack_id`, RunKind etc. | `RefundFacts`, `approval_id`, `denied`/`halted` statuses | main's file + act's three additions (Task 3 has the full code) |
| `app/main.py` | directory + scheduler + github wiring | act-layer service wiring in lifespan | union — keep both wiring blocks; RefundFlow ctor gets directory **and** seams (Task 6) |
| `app/workflows/engine.py` | requester-aware retrieval, order scoping, `customer_email` extraction | facts-only prompt (no verdict) | act's facts-only shape **with** main's requester features (Task 4 has the full file) |
| `app/workflows/flow.py` | directory routing, customer path, fan-out, hard-deny | seams: policy/audit/approvals/connector, verdict dispatch | main's skeleton + act's governance (Task 5 has the full file) |
| `tests/test_refund_flow.py` | directory-fixture suite | seam-fixture suite | keep **main's** version at merge time; adapt in Task 7 (act's `test_refund_flow_seams.py` arrives additive) |

---

### Task 1: Sync the worktree branch to current origin/main

**Files:** none edited by hand (merge only).

- [ ] **Step 1: Confirm clean tree and current branch**

Run: `git status --porcelain && git branch --show-current`
Expected: empty status; `worktree-platform-approach-investigation`

- [ ] **Step 2: Merge current origin/main**

```bash
git fetch origin
git merge origin/main -m "merge: sync worktree branch to current main (directory + fan-out)"
```

Expected: clean auto-merge (the branch's only local commit is the spec doc, additive). If anything conflicts, stop and report — do not improvise.

- [ ] **Step 3: Baseline tests**

Run: `cd substrateos-api && uv run pytest tests/ -q`
Expected: all pass (this is current main's suite + nothing else). Record the count.

- [ ] **Step 4: Frontend baseline**

Run: `cd web && pnpm typecheck`
Expected: PASS. (Skip `pnpm build` here; it runs in Task 9.)

---

### Task 2: Merge feat/governed-act-layer — additive files land, conflicts staged

**Files:**
- Merge-in (additive, no hand edits): `substrateos-api/app/policy/*`, `app/audit/*`, `app/approvals/*`, `app/connectors/act/*`, `app/domain/policy.py`, `app/domain/approval.py`, `app/domain/audit.py`, `substrateos-api/policies/refund.v1.yaml`, `tests/test_policy_engine.py`, `tests/test_audit_log.py`, `tests/test_approval_service.py`, `tests/test_refund_flow_seams.py`
- Conflicted (resolved in Tasks 3–6): `app/deps.py`, `app/domain/workflow.py`, `app/main.py`, `app/workflows/engine.py`, `app/workflows/flow.py`, `tests/test_refund_flow.py`

- [ ] **Step 1: Start the merge (expect exactly 6 conflicts)**

```bash
git merge origin/feat/governed-act-layer
```

Expected output: `CONFLICT (content)` in exactly the six files in the conflict map. If any **other** file conflicts, stop and report.

- [ ] **Step 2: Resolve `app/deps.py` (union)**

Open `substrateos-api/app/deps.py`. Keep main's full file (it ends with `get_github_store`/`get_github_flow`), then append act's three getters at the end (dropping the conflict markers):

```python
def get_audit_log(request: Request):
    return getattr(request.app.state, "audit_log", None)


def get_approval_service(request: Request):
    return getattr(request.app.state, "approval_service", None)


def get_policy_engine(request: Request):
    return getattr(request.app.state, "policy_engine", None)
```

- [ ] **Step 3: Take main's side for the remaining conflicts (they get their real content in Tasks 3–6)**

```bash
cd substrateos-api
git checkout --ours app/domain/workflow.py app/main.py app/workflows/engine.py app/workflows/flow.py tests/test_refund_flow.py
git add app/deps.py app/domain/workflow.py app/main.py app/workflows/engine.py app/workflows/flow.py tests/test_refund_flow.py
```

Rationale: `--ours` (main's behavior) keeps the suite green at this commit; the act-layer grafts land as reviewable follow-on commits. The additive packages are already staged by the merge.

- [ ] **Step 4: Declare the PyYAML dependency (used by `app/policy/store.py`)**

In `substrateos-api/pyproject.toml`, add to `dependencies = [...]`:

```toml
  "pyyaml>=6.0",
```

Run: `cd substrateos-api && uv lock && uv sync`
Expected: lockfile updates, no resolver errors.

- [ ] **Step 5: Conclude the merge and verify**

```bash
git add substrateos-api/pyproject.toml substrateos-api/uv.lock
git commit --no-edit   # concludes the merge commit
cd substrateos-api && uv run pytest tests/ -q
```

Expected: the new `test_policy_engine.py`, `test_audit_log.py`, `test_approval_service.py` pass (they test the additive packages). `test_refund_flow_seams.py`, `test_refund_engine.py`, `test_refund_cards.py`, `test_refund_e2e_integration.py` may FAIL — they were written against the act-layer flow that isn't grafted yet. Record which fail; they must all pass by end of Task 7. If `test_refund_flow.py` (main's) fails, stop — main's behavior broke, which violates the invariant.

---

### Task 3: Merged domain model — `RefundFacts`, `approval_id`, `denied`/`halted`

**Files:**
- Modify: `substrateos-api/app/domain/workflow.py`
- Test: `substrateos-api/tests/test_workflow_models.py` (exists on main)

- [ ] **Step 1: Write the failing test**

Append to `substrateos-api/tests/test_workflow_models.py`:

```python
from app.domain.workflow import RefundFacts


def test_refund_facts_defaults_and_fields():
    f = RefundFacts()
    assert f.found is False and f.amount_usd is None
    f2 = RefundFacts(found=True, order_id="A-1001", customer="Dana",
                     customer_email="dana@acme.test", amount_usd=120.0,
                     order_age_days=10, reasoning="extracted")
    assert f2.customer_email == "dana@acme.test"


def test_run_supports_durable_approval_and_guardrail_statuses():
    from app.domain.workflow import RefundRun
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    run = RefundRun(id="RB-1", requester_name="Tom", created_at=now, updated_at=now,
                    approval_id="AP-8201", status="halted")
    assert run.approval_id == "AP-8201"
    run.status = "denied"  # both new statuses are valid RunStatus values
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd substrateos-api && uv run pytest tests/test_workflow_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'RefundFacts'`

- [ ] **Step 3: Implement**

In `substrateos-api/app/domain/workflow.py`:

(a) extend `RunStatus` (keep every existing literal, add two):

```python
RunStatus = Literal[
    "running", "pending_approval", "pending_confirm",
    "approved", "rejected", "completed", "cancelled", "error",
    "needs_attention",    # stopped: no eligible approver / identity unknown
    "routed_to_support",  # customer request handed to the support channel
    "denied",             # guardrail verdict: forbidden outright (policy-as-code)
    "halted",             # guardrail fail-closed: missing/ambiguous facts
]
```

(b) add `RefundFacts` directly above `RefundDecision`:

```python
class RefundFacts(BaseModel):
    """Typed facts the model EXTRACTS from the request + grounded order context.

    The model produces facts only — it never decides the outcome. The deterministic
    PolicyEngine (app/policy) decides allow/require_approval over these facts.
    """
    found: bool = False
    order_id: str | None = None
    customer: str | None = None
    customer_email: str | None = None  # from the order record — powers the outcome DM fallback
    amount_usd: float | None = None
    order_age_days: int | None = None
    reasoning: str = ""
```

(c) update `RefundDecision`'s docstring (fields unchanged — it stays the render
view for cards/runs, now assembled by the flow from facts + the policy):

```python
class RefundDecision(BaseModel):
    """Render view for the Slack cards + run record. Populated by the flow from the
    extracted RefundFacts plus the evaluated policy — NOT directly by the model
    (``policy_limit_*`` and ``auto_approve`` now come from the guardrail, in code)."""
```

(d) add one field to `RefundRun`, after `approver_slack_id`:

```python
    approval_id: str | None = None  # the durable PendingApproval gating this run
```

- [ ] **Step 4: Run tests**

Run: `cd substrateos-api && uv run pytest tests/test_workflow_models.py tests/test_run_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/domain/workflow.py substrateos-api/tests/test_workflow_models.py
git commit -m "feat(workflows): merged run model — RefundFacts, approval_id, denied/halted statuses"
```

---

### Task 4: Merged engine — facts-only prompt with requester-aware retrieval

**Files:**
- Modify: `substrateos-api/app/workflows/engine.py` (full replacement below)
- Test: `substrateos-api/tests/test_refund_engine.py` (merged in from the act branch; adapt for `customer_email` + requester)

- [ ] **Step 1: Replace `substrateos-api/app/workflows/engine.py` with exactly:**

```python
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.workflow import RefundFacts
from app.orchestrator.timing import StageTimer
from app.retrieval.order_scope import scope_order_chunks

logger = logging.getLogger(__name__)

# The model EXTRACTS FACTS only. It does not decide the verdict — the deterministic
# PolicyEngine does, in code, outside the model (the governed-act-layer invariant).
FACTS_PROMPT = (
    "You are SubstrateOS running the Acme refund playbook. "
    "Use ONLY the provided context documents (order records) to EXTRACT FACTS about "
    "the refund request. You do NOT decide whether the refund is approved — a "
    "deterministic policy engine does that, in code. Compute the order age in days "
    "from the order date and today's date when it is not stated explicitly.\n"
    "Respond ONLY with valid JSON, no other text:\n"
    '{"found": true, "order_id": "...", "customer": "...", "customer_email": "...", '
    '"amount_usd": 0, "order_age_days": 0, '
    '"reasoning": "one sentence describing the extracted facts"}\n'
    "Copy the customer's email address from the order record into customer_email "
    "when present, else use null. "
    "If the order cannot be found in the context documents, respond with "
    '{"found": false, "reasoning": "..."}.'
)


class RefundEngineError(Exception):
    """The LLM reply could not be parsed into RefundFacts."""


class RefundEngine:
    """Gathers grounded order context and extracts typed facts (no verdict)."""

    def __init__(self, *, retriever, llm) -> None:
        self._retriever = retriever
        self._llm = llm

    async def evaluate(self, text: str, *, user: User,
                       requester: DirectoryUser | None = None) -> RefundFacts:
        timer = StageTimer()
        order_query = text
        if requester is not None:
            who = f"{requester.display_name or ''} {requester.email or ''}".strip()
            order_query = f"{text} customer {who}"
        order_hits = await self._retriever.retrieve(
            query=order_query, user=user, k=6, timer=timer
        )
        order_hits = scope_order_chunks(list(order_hits), requester)
        seen: set[str] = set()
        parts: list[str] = []
        for cand in order_hits:
            ch = cand.chunk
            if ch.chunk_id in seen:
                continue
            seen.add(ch.chunk_id)
            parts.append(f"[{ch.title}]\n{ch.content}")
        context = "\n\n".join(parts[:8]) or "(no documents found)"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        requester_line = ""
        if requester is not None:
            requester_line = (
                f"Requester: {requester.display_name or requester.email} "
                f"({requester.email}), role {requester.role} — "
                "'my order' refers to them.\n"
            )
        messages = [
            {"role": "system", "content": FACTS_PROMPT},
            {"role": "user", "content": (
                f"Today's date: {today}\n{requester_line}\n"
                f"Context documents:\n{context}\n\n"
                f"Refund request: {text}"
            )},
        ]
        raw = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=500)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("Refund engine: no JSON in LLM reply: %r", raw[:200])
            raise RefundEngineError("no JSON in LLM reply")
        try:
            return RefundFacts.model_validate(json.loads(match.group(0)))
        except Exception as e:  # noqa: BLE001
            logger.warning("Refund engine: unparseable facts: %s", e)
            raise RefundEngineError(str(e)) from e
```

Notes on what changed vs each parent: from **act** — facts-only prompt, returns `RefundFacts`, drops the `_POLICY_QUERY` retrieval (the YAML is now the only policy authority; retrieved policy text must not even *appear* to ground the verdict). From **main** — `requester` parameter, requester-biased order query, `scope_order_chunks` own-orders enforcement, requester line in the user message, `customer_email` extraction (now a `RefundFacts` field).

- [ ] **Step 2: Adapt `tests/test_refund_engine.py`**

The act branch's version of this file landed in the merge. Update it so the fake LLM replies include `"customer_email": "dana@acme.test"` and assert `facts.customer_email == "dana@acme.test"`; add one test that `evaluate(..., requester=record)` passes the requester line (assert `"role customer" in` the captured user message, using a `DirectoryUser(email="dana@acme.test", role="customer")` fixture — see `tests/test_refund_engine_requester.py` on main for the existing fixture pattern, which must keep passing).

- [ ] **Step 3: Run tests**

Run: `cd substrateos-api && uv run pytest tests/test_refund_engine.py tests/test_refund_engine_requester.py tests/test_order_scope.py -q`
Expected: PASS. (`test_refund_engine_requester.py` asserts requester-aware behavior main shipped — if it references `RefundDecision` from the engine, update it to `RefundFacts`.)

- [ ] **Step 4: Commit**

```bash
git add substrateos-api/app/workflows/engine.py substrateos-api/tests/test_refund_engine.py substrateos-api/tests/test_refund_engine_requester.py
git commit -m "feat(workflows): facts-only engine keeps requester-aware retrieval — verdict moves to policy"
```

---

### Task 5: Merged flow — directory routing + governance seams

**Files:**
- Modify: `substrateos-api/app/workflows/flow.py` (full replacement below)
- Modify: `substrateos-api/policies/refund.v1.yaml` (role vocabulary)
- Modify: `substrateos-api/app/bots/refund_cards.py` only if its `decision=` params reject the rendered view (they take `RefundDecision`; no change expected)

- [ ] **Step 1: Fix the policy role vocabulary**

Replace `substrateos-api/policies/refund.v1.yaml` content with:

```yaml
id: refund.v1
version: 1
owner: support_manager
description: "Refunds auto-approve only within limit and window."
all:
  - { fact: amount_usd,     op: "<=", value: 500 }
  - { fact: order_age_days, op: "<=", value: 30 }
on_pass: allow
on_fail: require_approval
required_role: manager      # the directory's role vocabulary (Entra Managers group)
on_missing_data: stop
```

(Only `required_role` changes: `support_manager` → `manager`. `owner` is informational.)

- [ ] **Step 2: Replace `substrateos-api/app/workflows/flow.py` with exactly:**

```python
from __future__ import annotations

import logging

from app.approvals.service import (
    AlreadyResolved,
    ApprovalService,
    NotAuthorized,
    UnknownApproval,
)
from app.approvals.store import ApprovalStore
from app.audit.log import AuditLog
from app.bots.refund_cards import (
    approval_dm_blocks,
    auto_approved_blocks,
    customer_outcome_blocks,
    customer_request_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.bots.slack import slack_call
from app.config import get_settings
from app.connectors.act.stripe_mock import StripeRefundConnector
from app.directory.service import DirectoryService
from app.domain.audit import Actor
from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.policy import Policy
from app.domain.workflow import RefundDecision, RefundFacts
from app.policy.engine import PolicyEngine
from app.policy.store import PolicyNotFound, PolicyStore
from app.workflows.engine import RefundEngine, RefundEngineError
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)

_ERROR = "Sorry, I couldn't evaluate that refund request right now. Please try again."


def _directory_identity(record: DirectoryUser) -> User:
    """Map a synced directory record to the identity the ApprovalService authorizes.

    principals() = {user_id, *group_ids}; granting the directory role as a group
    means `required_role in identity.principals()` is the same check the flow's
    hard-deny does — one vocabulary (manager/agent/customer), enforced twice.
    """
    return User(
        user_id=record.email,
        tenant_id=get_settings().substrateos_tenant_id,
        email=record.email,
        display_name=record.display_name or record.email,
        group_ids={record.role},
    )


def _policy_limits(policy: Policy) -> tuple[float | None, int | None]:
    """Pull the display thresholds out of the policy conditions (for the cards)."""
    amount = age = None
    for cond in policy.all:
        if cond.fact == "amount_usd" and isinstance(cond.value, int | float):
            amount = float(cond.value)
        elif cond.fact == "order_age_days" and isinstance(cond.value, int):
            age = cond.value
    return amount, age


def _render_decision(facts: RefundFacts, *, policy: Policy | None = None,
                     auto_approve: bool = False, reasoning: str | None = None
                     ) -> RefundDecision:
    """Assemble the card/run render view from extracted facts + evaluated policy."""
    limit_usd = limit_days = None
    if policy is not None:
        limit_usd, limit_days = _policy_limits(policy)
    return RefundDecision(
        found=facts.found, order_id=facts.order_id, customer=facts.customer,
        customer_email=facts.customer_email, amount_usd=facts.amount_usd,
        order_age_days=facts.order_age_days,
        policy_limit_usd=limit_usd, policy_limit_days=limit_days,
        auto_approve=auto_approve, reasoning=reasoning or facts.reasoning,
    )


class RefundFlow:
    """Drives the refund playbook over Slack: ack → identity check → extract facts →
    guardrail (policy-as-code) → act/route → decide. The verdict is decided by
    PolicyEngine, in code — never by the model."""

    def __init__(
        self,
        *,
        engine: RefundEngine,
        store: RunStore,
        directory: DirectoryService,
        policy_engine: PolicyEngine | None = None,
        policy_store: PolicyStore | None = None,
        policy_id: str = "refund.v1",
        audit_log: AuditLog | None = None,
        refund_connector: StripeRefundConnector | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self._engine = engine
        self._store = store
        self._directory = directory
        self._policy_engine = policy_engine or PolicyEngine()
        self._policy_store = policy_store or PolicyStore()
        self._policy_id = policy_id
        # seams: provenance + the act connector + the approval gate
        self._audit = audit_log or AuditLog()
        self._refund_connector = refund_connector or StripeRefundConnector()
        self._approvals = approval_service or ApprovalService(
            store=ApprovalStore(), audit=self._audit)

    def _policy(self) -> Policy:
        # PolicyStore caches with mtime invalidation: a YAML edit is picked up on the
        # next request, no restart — "flip 500 → 300 in the file" works live.
        return self._policy_store.load(self._policy_id)

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

    async def _post(self, token: str, channel: str, thread_ts: str | None,
                    *, text: str, card: dict | None = None) -> dict | None:
        payload: dict = {"channel": channel, "text": text}
        if card:
            payload.update(card)  # {"blocks": [...], "attachments": [...]}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return await slack_call(token, "chat.postMessage", payload)

    async def _route_to_support(self, token: str, run, *, text: str, requester: str,
                                record, channel: str, thread_ts: str | None,
                                user: User) -> None:
        """Customer path: read-only engine lookup pre-fills the hand-off card;
        lookup failure never blocks routing."""
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
        decision = None
        try:
            facts = await self._engine.evaluate(text, user=user, requester=record)
            if facts.found:
                decision = _render_decision(facts)
        except RefundEngineError:
            logger.warning("customer order lookup failed; routing without facts")
        if decision is not None:
            run.decision = decision
            await self._store.add_event(
                run.id, step="Order fetched",
                detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                        f"age {decision.order_age_days} days — fetched for {requester}"),
                actor="SubstrateOS")
        posted = await slack_call(token, "chat.postMessage", {
            "channel": support_channel,
            "text": f"Customer refund request from {requester}",
            **customer_request_blocks(request_text=text, customer_name=requester,
                                      run_id=run.id, decision=decision),
        })
        if not posted:
            run.status = "needs_attention"
            run.decision = None  # facts never reached the channel — don't show them on the run
            await self._store.save(run)
            await self._store.add_event(run.id, step="Routing failed",
                                        detail="Could not post to the refunds channel",
                                        actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="I couldn't reach the support team — please contact them directly.")
            return
        run.status = "routed_to_support"
        run.handoff_channel = support_channel
        run.handoff_ts = posted.get("ts")
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed to support",
            detail=f"Posted to the refunds channel for a support agent ({requester} is a customer)",
            actor="SubstrateOS")
        await self._post(token, channel, thread_ts,
                         text="Refunds are handled by our support team — I've passed your "
                              "request to them and someone will follow up here.")

    async def _notify_customer(self, token: str, run, *, approved: bool,
                               approver_name: str) -> None:
        """Relay the outcome to the customer (their original thread, else a DM via
        the directory) and mark the support-channel hand-off card resolved.
        Fail-soft: the relay must never break the recorded decision."""
        d = run.decision
        if d is None or not d.order_id:
            return
        try:
            linked = await self._store.find_routed_run(d.order_id)
            notified_where: str | None = None
            if linked is not None and linked.channel:
                posted = await slack_call(token, "chat.postMessage", {
                    "channel": linked.channel, "thread_ts": linked.thread_ts,
                    "text": f"Your refund was {'approved' if approved else 'declined'}",
                    **customer_outcome_blocks(d, approved=approved),
                })
                if posted:
                    notified_where = "their thread"
                    # Post→save is not atomic: if the save fails after a successful
                    # post, a later decision could re-notify (demo-grade, accepted).
                    linked.status = "completed" if approved else "rejected"
                    await self._store.save(linked)
                    await self._store.add_event(
                        linked.id, step="Outcome relayed",
                        detail=(f"{'Approved' if approved else 'Rejected'} by "
                                f"{approver_name} — customer notified"),
                        actor="SubstrateOS")
            elif d.customer_email:
                record = await self._directory.resolve(d.customer_email)
                if record is not None and record.slack_id:
                    opened = await slack_call(token, "conversations.open",
                                              {"users": record.slack_id})
                    dm = ((opened or {}).get("channel") or {}).get("id")
                    if dm:
                        posted = await slack_call(token, "chat.postMessage", {
                            "channel": dm,
                            "text": f"Your refund was {'approved' if approved else 'declined'}",
                            **customer_outcome_blocks(d, approved=approved),
                        })
                        if posted:
                            notified_where = "a DM"
            await self._store.add_event(
                run.id,
                step="Customer notified" if notified_where else "Customer not reachable",
                detail=(f"Outcome sent to {d.customer} in {notified_where}" if notified_where
                        else f"No conversation or directory match for {d.customer or 'the customer'}"),
                actor="SubstrateOS")
            if linked is not None and linked.handoff_channel and linked.handoff_ts:
                mark = "✅" if approved else "✕"
                suffix = "customer notified" if notified_where else "customer not reachable"
                await slack_call(token, "chat.postMessage", {
                    "channel": linked.handoff_channel, "thread_ts": linked.handoff_ts,
                    "text": (f"{mark} Resolved — "
                             f"{'approved' if approved else 'rejected'} by {approver_name}, {suffix}"),
                })
        except Exception:  # noqa: BLE001 — relay must never break the decision
            logger.exception("customer outcome relay failed for run %s", run.id)

    # ── inbound request (from the Slack webhook) ─────────────────────────────

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
        await self._audit.record(
            run_id=run.id, step="Request received",
            actor=Actor(type="human", id=requester_email or requester),
            inputs_summary=text[:160], surface="slack",
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
        await self._audit.record(
            run_id=run.id, step="Identity checked",
            actor=Actor(type="human", id=record.email, idp="entra"),
            detail=f"{requester} → {record.role}",
        )

        if record.role == "customer":
            await self._route_to_support(token, run, text=text, requester=requester,
                                         record=record, channel=channel,
                                         thread_ts=thread_ts, user=user)
            return

        await self._post(token, channel, thread_ts,
                         text="Pulling up the order and checking the refund policy…")

        try:
            facts = await self._engine.evaluate(text, user=user, requester=record)
        except RefundEngineError:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail="Could not evaluate the request", actor="SubstrateOS")
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        if not facts.found:
            run.decision = _render_decision(facts)
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Order not found",
                                        detail=facts.reasoning, actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text=f"I couldn't find that order in our records. {facts.reasoning}")
            return

        # ── Guardrail: deterministic policy-as-code decides the verdict (not the model) ──
        try:
            policy = self._policy()
        except PolicyNotFound:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail=f"Policy {self._policy_id} not found",
                                        actor="SubstrateOS")
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        guardrail = self._policy_engine.evaluate(
            policy, {"amount_usd": facts.amount_usd, "order_age_days": facts.order_age_days}
        )
        rule = f"{guardrail.rule_id}@v{guardrail.rule_version}"
        decision = _render_decision(facts, policy=policy,
                                    auto_approve=(guardrail.result == "allow"),
                                    reasoning=guardrail.reason or facts.reasoning)
        run.decision = decision

        await self._store.add_event(
            run.id, step="Facts gathered",
            detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                    f"age {decision.order_age_days} days · customer {decision.customer}"),
            actor="SubstrateOS",
        )
        await self._store.add_event(
            run.id, step="Rule evaluated",
            detail=(f"{rule} → {guardrail.result} "
                    f"(limits ${(decision.policy_limit_usd or 0):,.0f} / "
                    f"{decision.policy_limit_days} days): {guardrail.reason}"),
            actor=rule,
        )
        # provenance: typed, identity-stamped audit trail (the receipt)
        await self._audit.record(
            run_id=run.id, step="Facts gathered", actor=Actor.agent("refund-engine"),
            target={"order_id": decision.order_id},
            detail=f"${(decision.amount_usd or 0):,.0f} · {decision.order_age_days}d · {decision.customer}",
        )
        await self._audit.record(
            run_id=run.id, step="Rule evaluated", actor=Actor.agent(rule),
            rule={"id": guardrail.rule_id, "version": guardrail.rule_version,
                  "result": guardrail.result},
            decision=guardrail.result, detail=guardrail.reason,
        )

        if guardrail.result == "allow":
            receipt = await self._refund_connector.refund(
                order_id=decision.order_id, amount_usd=decision.amount_usd
            )
            run.status = "completed"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Auto-approved",
                                        detail=guardrail.reason, actor=rule)
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=(f"${decision.amount_usd:,.0f} refunded to {decision.customer} · "
                        f"{receipt.refund_id}"),
                actor="SubstrateOS",
            )
            await self._audit.record(
                run_id=run.id, step="Refund issued", actor=Actor.system(),
                action="stripe.refund",
                target={"order_id": decision.order_id, "refund_id": receipt.refund_id},
            )
            await self._post(token, channel, thread_ts,
                             text="Auto-approved within policy — refund issued.",
                             card=auto_approved_blocks(decision, run_id=run.id))
            return

        if guardrail.result == "deny":
            run.status = "denied"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Denied",
                                        detail=guardrail.reason, actor=rule)
            await self._audit.record(
                run_id=run.id, step="Denied", actor=Actor.agent(rule),
                rule={"id": guardrail.rule_id, "version": guardrail.rule_version,
                      "result": "deny"},
                decision="deny", detail=guardrail.reason,
            )
            await self._post(token, channel, thread_ts,
                             text=f"This refund is denied by policy. {guardrail.reason}")
            return

        if guardrail.result == "stop":
            # fail-closed: the engine could NOT decide (missing/ambiguous facts).
            run.status = "halted"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Halted",
                                        detail=guardrail.reason, actor=rule)
            await self._audit.record(
                run_id=run.id, step="Halted", actor=Actor.agent(rule),
                rule={"id": guardrail.rule_id, "version": guardrail.rule_version,
                      "result": "stop"},
                decision="stop", detail=guardrail.reason,
            )
            await self._post(token, channel, thread_ts,
                             text=("I can't decide this one safely — the request is missing or "
                                   f"ambiguous data ({guardrail.reason}). Escalating for manual review."))
            return

        if guardrail.result != "require_approval":
            # defensive fail-closed: never silently route an unknown verdict to a human
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Error",
                                        detail=f"Unexpected guardrail result {guardrail.result!r}",
                                        actor=rule)
            await self._post(token, channel, thread_ts, text=_ERROR)
            return

        # require_approval — Stop: only the requester's Entra manager, who must be
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

        # Durable, identity-aware pending approval (the platform primitive);
        # the Slack card below mirrors it for the UX.
        run.status = "pending_approval"
        run.approver_name = mgr.display_name or mgr.email
        run.approver_slack_id = mgr.slack_id
        required_role = guardrail.required_role or policy.required_role or "manager"
        run.approval_id = await self._approvals.request(
            run_id=run.id, step="approve", required_role=required_role,
            decision_context={
                "order_id": facts.order_id, "amount_usd": facts.amount_usd,
                "order_age_days": facts.order_age_days, "result": guardrail.result,
            },
            rule_id=guardrail.rule_id, rule_version=guardrail.rule_version,
        )
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
                    **decided_dm_blocks(run.decision,
                                        approved=(run.status in ("approved", "completed")),
                                        approver_name=run.approver_name or "a manager"),
                })
            return

        # Only the routed approver — who must be a manager in the directory —
        # may act. Anyone else is refused and the attempt is audited. A run with
        # no recorded approver (pre-directory legacy) is hard-denied, not open.
        actor_record = await self._directory.get_by_slack_id(approver_id)
        is_routed = (run.approver_slack_id is not None
                     and approver_id == run.approver_slack_id)
        if not is_routed or actor_record is None or actor_record.role != "manager":
            actor_name = (await self._display_name(token, approver_id)
                          or (payload.get("user") or {}).get("name") or "Someone")
            await self._store.add_event(
                run.id, step="Approval denied",
                detail=(f"{actor_name} tried to act but is not the routed approver "
                        "(managers only)"),
                actor=actor_name)
            await self._audit.record(
                run_id=run.id, step="Approval denied",
                actor=Actor(type="human", id=(actor_record.email if actor_record
                                              else approver_id or actor_name)),
                decision="denied", detail=f"{actor_name} is not the routed approver",
            )
            if dm_channel and approver_id:
                await slack_call(token, "chat.postEphemeral", {
                    "channel": dm_channel, "user": approver_id,
                    "text": "Only the routed approver (a manager) can act on this request.",
                })
            return

        approved = action_id == "refund_approve"
        approver_name = (await self._display_name(token, approver_id)
                         or (payload.get("user") or {}).get("name") or "Manager")

        # Governed resolution: resolve THROUGH the ApprovalService — it enforces role
        # authZ on the directory-bound identity, stamps that identity, records a
        # rule-bearing audit event, and closes the PendingApproval (no orphan).
        if run.approval_id:
            pending = await self._approvals.get_pending(run.approval_id)
            if pending is not None and pending.status == "pending":
                identity = _directory_identity(actor_record)
                try:
                    await self._approvals.resolve(
                        run.approval_id, "approve" if approved else "reject", identity
                    )
                except NotAuthorized:
                    # Defense in depth: the routed/role check above should make this
                    # unreachable; if it fires, refuse rather than fall through.
                    await self._store.add_event(
                        run.id, step="Approval blocked",
                        detail=f"{approver_name} is not in role {pending.required_role}",
                        actor="SubstrateOS",
                    )
                    return
                except (AlreadyResolved, UnknownApproval):
                    pass  # already decided / not found — proceed idempotently
        else:
            # legacy run with no durable approval: best-effort typed audit
            await self._audit.record(
                run_id=run.id, step="Approved" if approved else "Rejected",
                actor=Actor(type="human", id=actor_record.email, idp="entra"),
                decision="approve" if approved else "reject",
                detail=f"{approver_name} {'approved' if approved else 'rejected'}",
            )

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
                **decided_dm_blocks(d, approved=approved, approver_name=approver_name),
            })
        if approved:
            receipt = await self._refund_connector.refund(
                order_id=d.order_id, amount_usd=d.amount_usd
            )
            await self._store.add_event(
                run.id, step="Refund issued",
                detail=f"${d.amount_usd:,.0f} refunded to {d.customer} · {receipt.refund_id}",
                actor="SubstrateOS",
            )
            await self._audit.record(
                run_id=run.id, step="Refund issued", actor=Actor.system(),
                action="stripe.refund",
                target={"order_id": d.order_id, "refund_id": receipt.refund_id},
            )
            run.status = "completed"
            await self._store.save(run)
        if run.channel:
            mention = (f"<@{run.requester_slack_id}>" if run.requester_slack_id else None)
            label = mention or run.requester_name
            await self._post(token, run.channel, run.thread_ts,
                             text=f"Hello {label} — refund {'approved' if approved else 'rejected'} by {approver_name}",
                             card=outcome_blocks(d, approved=approved,
                                                 approver_name=approver_name, mention=label))
        await self._notify_customer(token, run, approved=approved,
                                    approver_name=approver_name)
```

Key graft decisions encoded above (so the engineer doesn't re-derive them):
1. **Main's `card=` posting style kept** (act used `blocks=`; main's card helpers return `{"blocks": ...}` dicts spread with `**`).
2. **Act's `_approver_identity` demo mapping is gone** — replaced by `_directory_identity(actor_record)`; the routed/role hard-deny runs **first**, so `ApprovalService.resolve` is the second enforcement of the same `manager` vocabulary.
3. **Approver resolution stays main's** (requester's Entra manager, no fallback, `needs_attention` stop) — `s.slack_refund_approver_id` no longer exists and must not reappear.
4. **`run.approval_id` gates the governed path**; legacy runs without one still get a typed audit event.
5. **Customer path** pre-fills the hand-off card via `_render_decision(facts)` (no policy → no limits shown, `auto_approve=False`).

- [ ] **Step 3: Wire `app/main.py`** — in the lifespan, directly after `app.state.run_store = RunStore()`, add the act-layer services and extend the `RefundFlow` constructor (which already has `directory=app.state.directory_service` from main):

```python
    # Governed-act-layer platform services (one impl each), shared across playbooks.
    app.state.audit_log = AuditLog()
    app.state.policy_engine = PolicyEngine()
    app.state.policy_store = PolicyStore()
    app.state.approval_store = ApprovalStore()
    app.state.approval_service = ApprovalService(
        store=app.state.approval_store, audit=app.state.audit_log,
    )
    app.state.refund_connector = StripeRefundConnector()
```

and the flow construction becomes:

```python
    app.state.refund_flow = RefundFlow(
        engine=RefundEngine(retriever=app.state.retriever, llm=app.state.llm),
        store=app.state.run_store,
        directory=app.state.directory_service,
        policy_engine=app.state.policy_engine,
        policy_store=app.state.policy_store,
        audit_log=app.state.audit_log,
        refund_connector=app.state.refund_connector,
        approval_service=app.state.approval_service,
    )
```

Add the imports (`ApprovalService`, `ApprovalStore`, `AuditLog`, `StripeRefundConnector`, `PolicyEngine`, `PolicyStore`) and the two shutdown lines in the `finally` block:

```python
        await app.state.audit_log.aclose()
        await app.state.approval_store.aclose()
```

(Keep main's `directory_service` name exactly as main spells it — check the existing lifespan line that constructs `RefundFlow` for the precise attribute name before editing.)

- [ ] **Step 4: Quick functional check**

Run: `cd substrateos-api && uv run pytest tests/test_lifespan_clients.py tests/test_refund_cards.py -q`
Expected: PASS (cards take the render view unchanged). `tests/test_refund_flow.py` is adapted in Task 7 — failures there are expected until then.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/workflows/flow.py substrateos-api/app/main.py substrateos-api/policies/refund.v1.yaml
git commit -m "feat(workflows): graft governance seams onto directory-routed refund flow"
```

---

### Task 6: ApprovalService identity — directory-vocabulary test

**Files:**
- Test: `substrateos-api/tests/test_approval_service.py` (merged in; extend)

The service itself needs no change — `required_role in identity.principals()` already works with `_directory_identity`. Prove the wiring with a test so a future vocabulary drift fails loudly.

- [ ] **Step 1: Add the test**

Append to `substrateos-api/tests/test_approval_service.py` (follow its existing fixture style — it constructs `ApprovalStore(force_memory=True)`):

```python
import pytest

from app.approvals.service import ApprovalService, NotAuthorized
from app.approvals.store import ApprovalStore
from app.domain.identity import User


def _identity(role: str) -> User:
    return User(user_id="diane@acme.test", tenant_id="t-demo",
                email="diane@acme.test", display_name="Diane",
                group_ids={role})


@pytest.mark.asyncio
async def test_directory_manager_role_resolves_and_customer_is_refused():
    svc = ApprovalService(store=ApprovalStore(force_memory=True))
    approval_id = await svc.request(run_id="RB-9", step="approve",
                                    required_role="manager",
                                    rule_id="refund.v1", rule_version=1)
    with pytest.raises(NotAuthorized):
        await svc.resolve(approval_id, "approve", _identity("customer"))
    decision = await svc.resolve(approval_id, "approve", _identity("manager"))
    assert decision.approver.email == "diane@acme.test"
```

- [ ] **Step 2: Run**

Run: `cd substrateos-api && uv run pytest tests/test_approval_service.py -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add substrateos-api/tests/test_approval_service.py
git commit -m "test(approvals): directory role vocabulary authorizes manager, refuses customer"
```

---

### Task 7: Adapt the flow test suites — everything green

**Files:**
- Modify: `substrateos-api/tests/test_refund_flow.py` (main's version — fake engine now returns `RefundFacts`)
- Modify: `substrateos-api/tests/test_refund_flow_seams.py` (act's version — constructor now requires `directory=`)
- Modify: `substrateos-api/tests/test_refund_e2e_integration.py`, `tests/test_slack_interactive.py` if they stub the engine/flow

- [ ] **Step 1: Run the refund suites to enumerate failures**

Run: `cd substrateos-api && uv run pytest tests/test_refund_flow.py tests/test_refund_flow_seams.py tests/test_refund_e2e_integration.py tests/test_slack_interactive.py -q`
Expected: failures from three causes only — (a) fake engines returning `RefundDecision` where the flow now expects `RefundFacts`; (b) `RefundFlow(...)` constructed without `directory=` (seams tests) or without seam kwargs (fine — they default); (c) changed "Rule evaluated" event text. Anything else: stop and report.

- [ ] **Step 2: Adapt main's `test_refund_flow.py`**

Mechanical changes, preserving every behavioral assertion:
- Fake engine `evaluate()` returns `RefundFacts(found=True, order_id=..., customer=..., customer_email=..., amount_usd=..., order_age_days=..., reasoning=...)` — delete `auto_approve=`/`policy_limit_*` kwargs (the YAML decides now: 300/20d cases auto-approve, 900/40d cases route).
- Where a test asserted `Rule evaluated` detail text, assert the new shape: `assert "refund.v1@v1 → allow" in detail` (or `→ require_approval`).
- Construct flows with an explicit in-memory governance kit so no test touches Redis:

```python
from app.approvals.service import ApprovalService
from app.approvals.store import ApprovalStore
from app.audit.log import AuditLog

def _flow(engine, store, directory):
    audit = AuditLog(force_memory=True)
    return RefundFlow(
        engine=engine, store=store, directory=directory,
        audit_log=audit,
        approval_service=ApprovalService(store=ApprovalStore(force_memory=True), audit=audit),
    )
```

- Add one new behavioral test — the governed click closes the durable approval:

```python
@pytest.mark.asyncio
async def test_manager_click_resolves_durable_approval(...existing fixtures...):
    # request that routes for approval (amount 900 > 500)
    await flow.handle_request(text="refund order A-1001 for $900", ...)
    run = (await store.list_runs(limit=1))[0]
    assert run.status == "pending_approval" and run.approval_id
    # the routed manager clicks approve
    await flow.handle_action(_click_payload(run.id, slack_id=MANAGER_SLACK_ID, approve=True))
    pending = await flow._approvals.get_pending(run.approval_id)
    assert pending.status == "approved"
```

(Reuse the suite's existing directory fixture for `MANAGER_SLACK_ID` and its click-payload helper — both exist in main's version of this file.)

- [ ] **Step 3: Adapt `test_refund_flow_seams.py`**

Act's seams suite stubs Slack and asserts audit/connector/approval interactions. Update its flow construction to pass a `directory=` fake whose `resolve()` returns a `DirectoryUser(email=..., role="agent", manager_email=...)` and whose `get_by_slack_id()` returns the manager record (`role="manager"`, `slack_id` matching the click). Delete any reference to `slack_refund_approver_id` or `_approver_identity` (both gone). The audit assertions (rule on the guardrail event, identity on the approve event) stay as written.

- [ ] **Step 4: Full suite**

Run: `cd substrateos-api && uv run pytest tests/ -q`
Expected: **everything passes.** This is the merge-complete checkpoint — the refund use case is governed AND directory-routed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/tests/
git commit -m "test(workflows): refund suites cover directory routing + governance seams together"
```

---

### Task 8: Audit trail on the runs API

**Files:**
- Modify: `substrateos-api/app/api/runs.py`
- Test: `substrateos-api/tests/test_runs_api.py`

- [ ] **Step 1: Write the failing test**

Append to `substrateos-api/tests/test_runs_api.py` (follow its existing app/fixture pattern — it installs fakes on `app.state`):

```python
@pytest.mark.asyncio
async def test_get_run_includes_typed_audit_trail(client_with_run):
    client, run_id, audit_log = client_with_run
    from app.domain.audit import Actor
    await audit_log.record(
        run_id=run_id, step="Rule evaluated", actor=Actor.agent("refund.v1@v1"),
        rule={"id": "refund.v1", "version": 1, "result": "allow"}, decision="allow",
    )
    resp = await client.get(f"/runs/{run_id}", headers=AUTH_HEADERS)
    body = resp.json()
    assert "audit" in body
    rule_events = [e for e in body["audit"] if e.get("rule")]
    assert rule_events and rule_events[0]["rule"]["version"] == 1
    assert rule_events[0]["actor"]["type"] == "agent"
```

(Add an `audit_log = AuditLog(force_memory=True)` to the fixture and set `app.state.audit_log = audit_log`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd substrateos-api && uv run pytest tests/test_runs_api.py -q`
Expected: FAIL — `KeyError: 'audit'`

- [ ] **Step 3: Implement**

In `substrateos-api/app/api/runs.py`, import `get_audit_log` from `app.deps`, add `audit_log=Depends(get_audit_log)` to `get_run`, and extend the return:

```python
    events = await run_store.list_events(run_id)
    audit = await audit_log.query(run_id) if audit_log is not None else []
    return {"run": run.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in events],
            "audit": [a.model_dump(mode="json") for a in audit]}
```

- [ ] **Step 4: Run tests**

Run: `cd substrateos-api && uv run pytest tests/test_runs_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/api/runs.py substrateos-api/tests/test_runs_api.py
git commit -m "feat(api): GET /runs/{id} returns the typed audit trail (the receipt)"
```

---

### Task 9: Admin receipt UI — mockup first, then React

**Files:**
- Modify: `mockups/admin-portal.html` (FIRST — user approval gate)
- Modify: `web/app/admin/runs/page.tsx` (events table, ~line 320)
- Modify: `web/lib/runsApi.ts` (`RunDetail` type gains `audit`)

- [ ] **Step 1: Update the mockup**

In `mockups/admin-portal.html`, find the workflow-run detail timeline. Extend each timeline row with: an **actor chip** (`human` warm-amber / `system` neutral-gray / `agent` ink-blue — reuse the existing chip CSS variables) and, on the guardrail row, a **rule badge**: `refund.v1 @ v1 → require_approval`. On the approval row show the approver identity line: `Diane Patel (diane@…) · Entra · manager`. Match the existing Fraunces/Archivo/JetBrains Mono design system — no new visual language.

- [ ] **Step 2: Present for approval (HARD GATE)**

Run: `open mockups/admin-portal.html`
Present to the user and **wait for explicit approval before writing any `.tsx`.** Iterate until approved.

- [ ] **Step 3: Type the audit payload**

In `web/lib/runsApi.ts`, add:

```typescript
export interface AuditActor {
  type: "human" | "system" | "agent";
  id: string;
  idp?: string | null;
}

export interface AuditEvent {
  ts: string;
  step: string;
  actor: AuditActor;
  action?: string;
  rule?: { id: string; version: number; result: string } | null;
  decision?: string | null;
  target?: Record<string, string> | null;
  detail?: string | null;
}
```

and extend `RunDetail` with `audit: AuditEvent[]`.

- [ ] **Step 4: Render the receipt**

In `web/app/admin/runs/page.tsx`, in the workflow-run detail (the `wf.events.map` table at ~line 320): join each legacy event row with its matching audit event (match on `step`, first unconsumed); render the actor chip from `audit.actor.type` (fall back to the existing Entra-chip heuristic when no audit match), the rule badge when `audit.rule` is present, and the approver identity (`actor.id` + `idp`) on Approved/Rejected rows. Follow the approved mockup exactly.

- [ ] **Step 5: Verify**

Run: `cd web && pnpm typecheck && pnpm lint && pnpm build`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add mockups/admin-portal.html web/lib/runsApi.ts web/app/admin/runs/page.tsx
git commit -m "feat(admin): run detail renders the governance receipt — actor chips, rule badge, approver identity"
```

---

### Task 10 (stretch): ApprovalFlow adopts ApprovalService + AuditLog

**Files:**
- Modify: `substrateos-api/app/workflows/approval.py`
- Modify: `substrateos-api/app/main.py` (pass `approval_service` + `audit_log` to `ApprovalFlow`)
- Test: `substrateos-api/tests/test_approval_flow.py`

- [ ] **Step 1: Write the failing test** — in `tests/test_approval_flow.py`, add: after `handle_request` routes to a manager, the run has `approval_id` set and `ApprovalService.get_pending(approval_id)` returns status `pending` with `required_role == "manager"`; after the routed manager's `handle_action` click, the pending is `approved` and `AuditLog.query(run_id)` contains a human-actor event with `decision == "approve"`. Reuse the file's existing Slack-stub fixtures; construct the flow with `ApprovalService(store=ApprovalStore(force_memory=True), audit=AuditLog(force_memory=True))`.

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_approval_flow.py -q` → FAIL (`ApprovalFlow.__init__` has no such kwargs).

- [ ] **Step 3: Implement** — `ApprovalFlow.__init__` gains `approval_service: ApprovalService | None = None, audit_log: AuditLog | None = None` (same defaulting pattern as `RefundFlow`). In `handle_request`, after the approver resolves: `run.approval_id = await self._approvals.request(run_id=run.id, step="approve", required_role="manager", decision_context={"request": text[:200]})`. In `handle_action`, after its existing status guard: load the pending, build `_directory_identity`-style identity from the clicking user's directory record (`self._directory.get_by_slack_id` — ApprovalFlow on main already resolves approvers through the directory; follow its existing pattern), call `self._approvals.resolve(...)`, refuse on `NotAuthorized` with an ephemeral, and pass on `AlreadyResolved`. Emit `Request received` / `Routed for approval` / decision audit events mirroring RefundFlow's calls. No policy engine — this playbook is always human sign-off.

- [ ] **Step 4: Run** — `uv run pytest tests/test_approval_flow.py tests/ -q` → all PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(approval): generic playbook adopts the durable approval gate + typed audit"`

---

### Task 11 (stretch): github_pr flow emits typed audit

**Files:**
- Modify: `substrateos-api/app/workflows/github_pr.py`
- Modify: `substrateos-api/app/main.py` (pass `audit_log` to the github flow)
- Test: `substrateos-api/tests/test_github_flow.py`

- [ ] **Step 1: Write the failing test** — after a full draft→confirm→raise cycle (the suite already has one), `AuditLog.query(run_id)` returns ≥3 events: `Request received` (human), `Draft prepared` (agent `pr-drafter`), `PR raised` (system, `action="github.create_pr"`, `target={"pr_url": ...}`), and the confirm event is a human actor (the requester confirms their own draft — that *is* the gate).

- [ ] **Step 2–4:** ctor gains `audit_log: AuditLog | None = None`; add the four `self._audit.record(...)` calls beside the existing `add_event` calls (same pattern as RefundFlow); run `uv run pytest tests/test_github_flow.py tests/ -q` → all PASS. No ApprovalService here: the requester's confirm is the gate and it's already identity-keyed by `requester_email`.

- [ ] **Step 5: Commit** — `git commit -m "feat(github): pr playbook emits the typed audit trail"`

---

### Task 12: Docs sync + honesty + full verification

**Files:**
- Modify: `substrateos-api/README.md`
- Modify: `mockups/architecture.html` (BOTH views)
- Verify: `~/.claude/skills/substrateos-feature/references/techstack.md` (PyYAML entry)

- [ ] **Step 1: README "What's real vs mocked"**

Add to `substrateos-api/README.md`:

```markdown
## What's real vs mocked (governed act layer)

**Real, enforced in code on the refund use case:** the guardrail
(`policies/refund.v1.yaml` evaluated by `app/policy/engine.py` — deterministic,
fail-closed, never the model), the durable approval gate
(`app/approvals/` — identity-bound, role-authorized, survives restarts), the
typed audit trail (`app/audit/` — append-only, actor-stamped, queryable per
run), and requester identity (Entra + Slack directory sync).

**Seeded/mocked:** order + customer data (seed corpus) and the Stripe
connector (`app/connectors/act/stripe_mock.py`).

**Roadmap (parked):** managed skill lifecycle (status/version/promote/rollback),
generic multi-playbook YAML engine, AI-drafted skills, multi-tenant, deep RBAC,
tamper-evident audit, OpenTelemetry, real connectors.
```

- [ ] **Step 2: Architecture doc** — in `mockups/architecture.html`, update BOTH views: detailed view gains the three governance modules (`policy/`, `approvals/`, `audit/`) wired between the playbook engine and the connectors, with the When→Check→Stop→Do→Record loop annotated to show *which* module owns Check (PolicyEngine), Stop (ApprovalService), Record (AuditLog); high-level view names "Governed Act Layer" as a band across the playbook engine. Keep the Master Deck palette (navy `#102444` / amber `#c8860d`). Then `open mockups/architecture.html` and eyeball both views.

- [ ] **Step 3: Techstack** — add to `references/techstack.md`: `PyYAML — policy-as-code specs (policies/*.yaml), loaded+validated by app/policy/store.py`.

- [ ] **Step 4: Full verification (the demo moments, by hand)**

```bash
cd substrateos-api && uv run pytest tests/ -q          # all green
cd ../web && pnpm typecheck && pnpm lint && pnpm build  # all green
```

Then the spec's two demo moments against a locally run API (seeded data):
1. Flip `policies/refund.v1.yaml` `value: 500` → `300`, re-run the same $400 refund → routes for approval instead of auto-approving, **no restart**. Flip back.
2. `GET /runs/{id}` for an approved over-limit run → `audit` shows the rule event (`refund.v1@v1 → require_approval`) and the human approve event with the approver's email + `idp: entra`.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/README.md mockups/architecture.html
git commit -m "docs: governed act layer — architecture views + what's real vs mocked"
```

---

## Self-review record (done at write time)

- **Spec coverage:** Slice 0 → Tasks 1–2; Slice 1 (guardrail) → merged packages + Tasks 4–5; Slice 2 (audit + receipt) → Tasks 5, 8, 9; Slice 3 (approval seam) → Tasks 5–7; Slice 4 (stretch) → Tasks 10–11; error handling → encoded in the flow graft (deny/halted/fail-closed paths) + suite assertions; honesty/README → Task 12. The spec's compatibility-view device became `_render_decision` (permanent, not transitional — the cards keep one render model).
- **Type consistency:** `RefundFacts` fields match between Task 3 (model), Task 4 (engine returns), Task 5 (flow consumes); `force_memory=True` exists on `ApprovalStore` and `AuditLog` (verified in branch source); `required_role="manager"` consistent across YAML, `_directory_identity`, and tests.
- **Known judgment call:** Task 2 resolves four conflicts with `--ours` then replaces content in Tasks 3–5 — chosen so every commit keeps main's behavior green rather than landing a half-grafted flow inside the merge commit.
