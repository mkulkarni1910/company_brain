# Governed Act Layer (DO-NOW core) — Design

**Date:** 2026-06-07
**Status:** In review
**Builds on:** `2026-06-07-entra-approval-routing-design.md` (user directory,
manager-only approval), `2026-06-05-refund-experience-design.md` (refund
playbook). Source documents: "SubstrateOS — Platform Positioning" and
"SubstrateOS — Governed Act Layer: Now vs Later + DO-NOW Implementation Plan"
(PDFs, 2026-06).

## Problem

The positioning claims SubstrateOS is "the governed act layer": deterministic
policy-as-code guardrails, a human on every risky decision, an identity-stamped
audit trail, and a control plane every skill inherits. In code today the
guardrail is a sentence inside an LLM prompt (`workflows/engine.py:
DECISION_PROMPT` asks GPT-4o to compute `auto_approve`) — non-deterministic,
unauditable, bypassable. Audit events carry free-string actors. Approval
routing and authorization are real (the directory work), but live inline in
`flow.py`, and the same approval/audit pattern is copy-pasted across three
flows (refund, generic approval, github_pr), so "every skill inherits it" is
false in code.

This design makes the governed-execution claims true on the refund use case,
as the PDF's workstreams 1–4. Workstream 5 (managed skill lifecycle:
status/version/promote/rollback) is deliberately a **separate follow-up spec**.

**Principle (non-negotiable):** governance is enforced in code, outside the
model. The model may parse inputs and draft text. It never decides whether an
action is allowed, whether a human is needed, or what gets recorded.

## Decisions (user-confirmed)

1. **Scope:** workstreams 1–4 in this spec; managed skill lifecycle (WS 5) is
   its own follow-up spec.
2. **Isolation:** all work happens on the worktree branch
   (`worktree-platform-approach-investigation`). `main` and
   `feat/identity-aware-orders` are never written to. The first implementation
   step merges `feat/identity-aware-orders` *into the worktree branch* so the
   directory + manager-only routing is the base.
3. **Seam reach:** the refund flow converts fully (policy + approval + audit).
   ApprovalFlow and github_pr adopt ApprovalService + AuditLog only (no policy
   engine — their gates are inherently human), as a stretch slice.
4. **Admin UI:** the admin run-detail view is upgraded in this iteration to
   render the governance data ("the receipt"). Mockup-first applies.
5. **Deadline:** demo in days → Approach A, demo-first vertical slices; the
   repo must be demoable after every slice.
6. **Invariant:** the refund use case works end-to-end (same Slack UX, same
   outcomes for the same inputs) after every slice. Existing refund tests are
   updated where shapes change but must pass before a slice is done.
7. **Role vocabulary:** policies use the directory's roles
   (`manager`/`agent`/`customer`). `refund.v1.yaml` sets
   `required_role: manager` (not the PDF's `support_manager`, which predates
   the directory design). Routing remains *the requester's Entra manager* per
   the approval-routing spec; the policy only declares the role that approver
   must hold.

## Slices

### Slice 0 — Foundations

- Merge `feat/identity-aware-orders` into the worktree branch.
- New packages: `app/policy/`, `app/audit/`, `app/approvals/`;
  `substrateos-api/policies/*.yaml` ships in the image and versions with git.
- `deps.py` gains `get_policy_engine`, `get_policy_store`, `get_audit_log`,
  `get_approval_service` following the existing `request.app.state` pattern.

### Slice 1 — Guardrail engine (`PolicyEngine`) — the keystone

PDF workstream 1. Replace prompt-as-policy with deterministic `evaluate()`
over typed facts.

**Models — `app/domain/policy.py`:**

```python
PolicyResult = Literal["allow", "require_approval", "deny", "stop"]

class Condition(BaseModel):
    fact: str
    op: Literal["<=", ">=", "<", ">", "==", "!=", "in"]
    value: object

class Policy(BaseModel):
    id: str; version: int; owner: str; description: str
    all: list[Condition] = []          # AND group
    on_pass: PolicyResult = "allow"
    on_fail: PolicyResult = "require_approval"
    required_role: str | None = None   # directory role, used on require_approval
    on_missing_data: PolicyResult = "stop"  # FAIL CLOSED

class Decision(BaseModel):
    result: PolicyResult
    reason: str
    rule_id: str; rule_version: int
    required_role: str | None = None
    evidence: dict = {}                # the facts the decision was made on
```

**Engine — `app/policy/engine.py`:** pure code, no LLM. Every referenced fact
must be present and correctly typed, else `on_missing_data` (stop). All
conditions pass → `on_pass`; any fail → `on_fail` + `required_role`. The
returned `Decision` always carries `rule_id`, `rule_version`,
`evidence=facts`.

**Store — `app/policy/store.py`:** loads and Pydantic-validates
`policies/<id>.v<version>.yaml` from disk at startup. No Redis.

**`policies/refund.v1.yaml`:** `amount_usd <= 500` AND `order_age_days <= 30`
→ allow; else require_approval with `required_role: manager`; missing data →
stop. Same thresholds the seeded policy document states, so outcomes are
unchanged.

**The split — `workflows/engine.py`:** `DECISION_PROMPT` is rewritten to
extract **facts only** → new `RefundFacts {found, order_id, customer,
amount_usd, order_age_days, summary}`. `auto_approve` and `policy_limit_*`
are removed from the model output. Retrieval is unchanged (order + policy
docs still ground the model's summary text), but retrieved policy text has no
authority over the decision. The flow calls
`PolicyEngine.evaluate(policy, facts.model_dump())`.

**Blast-radius control:** `RefundDecision` is read by `flow.py`,
`refund_cards.py`, and the runs API. `RefundRun` gains `facts: RefundFacts |
None` and `guardrail: Decision | None`; a helper assembles a legacy-shaped
`RefundDecision` view (limits from the `Policy`, `auto_approve = result ==
"allow"`) so Slack cards and the admin page render unchanged during this
slice. The view is removed in slice 2 when those surfaces move to the new
shapes.

**Demo moment:** flip 500→300 in the YAML — the same request now routes for
approval with no code or prompt edit. Remove a fact — the run stops, fail
closed.

### Slice 2 — Typed audit + the admin receipt

PDF workstream 3. One ordered, identity-stamped, append-only record per
`run_id`.

**Models — `app/domain/audit.py`:**

```python
ActorType = Literal["human", "system", "agent"]

class Actor(BaseModel):
    type: ActorType
    id: str                       # email for humans, component id otherwise
    idp: str | None = None        # "entra" for directory-matched humans
    display: str | None = None

class AuditEvent(BaseModel):      # append-only, queryable by run_id
    ts: datetime; run_id: str; step: str
    actor: Actor
    action: str
    inputs_summary: str | None = None
    rule: dict | None = None      # {id, version, result} on the guardrail step
    decision: str | None = None
    target: dict | None = None    # {order_id?, refund_id?}
    surface: str | None = None
```

**Actor conventions:**

| Step | Actor |
|---|---|
| Request received | `human` — directory record (`id=email`, `idp="entra"` when matched) |
| Facts gathered | `agent` — `refund_engine@<azure-openai-deployment>` |
| Rule evaluated | `system` — `policy_engine`, with `rule={id, version, result}` |
| Approved / Rejected | `human` — approver's directory identity (email + entra_id) |
| Refund issued | `system` — `substrateos` in slice 2; `connector:stripe_mock` once the connector seam lands in slice 3 |

**Storage — `app/audit/log.py`:** `AuditLog.append/query` wraps the existing
`RunStore` Redis persistence (same keys, richer payload).
`RunStore.add_event(step, detail, actor: str)` remains as a shim that builds a
system-actor `AuditEvent`, so unmigrated call sites (ApprovalFlow, github_pr)
keep working until slice 4.

**Read view + UI:** `GET /runs/{id}` returns the full `AuditEvent` list. The
admin run-detail view renders actor-type chips (human/system/agent), the rule
id+version+result on the guardrail step, and the approver identity on the
approval step. Mockup-first: `mockups/admin-portal.html` is updated and
approved before any `.tsx`.

### Slice 3 — Approval gate seam (`ApprovalService`)

PDF workstream 2. The branch already proved routing + authorization; this
slice moves it behind a service and binds decisions to directory identity.

**Models — `app/domain/approval.py`:**

```python
ApprovalChoice = Literal["approve", "reject"]
ApprovalStatus = Literal["pending", "approved", "rejected"]

class PendingApproval(BaseModel):   # persisted — survives restarts
    id: str; run_id: str; step: str
    required_role: str
    approver_email: str; approver_slack_id: str | None
    decision_context: dict          # what the approver sees
    rule_id: str; rule_version: int
    created_at: datetime
    status: ApprovalStatus = "pending"

class ApprovalDecision(BaseModel):
    approval_id: str
    choice: ApprovalChoice
    approver: DirectoryUser         # identity-bound: email + entra_id
    decided_at: datetime
```

**Service — `app/approvals/service.py` + `store.py`** (Redis, mirroring
`RunStore`'s in-process fallback pattern):

- `request(...) -> approval_id`: persists the `PendingApproval`, delivers the
  card via an injected `deliver` callback (today: the existing Slack DM code).
  A full `SurfaceAdapter` abstraction is **not** built now — one surface,
  YAGNI.
- `resolve(approval_id, choice, actor_slack_id)`: loads (idempotent if already
  decided); **authorization moves here from `flow.py`** — the actor must be
  the routed approver *and* hold `required_role` per the directory (the
  branch's hard-deny semantics, now owned by the service); records the
  identity-bound `ApprovalDecision`; emits the audit event; resumes the flow
  via callback.

`RefundFlow.handle_action` shrinks to: Slack click →
`approvals.resolve(...)` → render outcome cards. After this slice `flow.py`
contains no policy logic, no authorization, no raw audit strings —
orchestration only. The Slack interactive contract (payload in, cards out) is
unchanged.

**Connector seam (light):** `app/connectors/act/stripe_mock.py` exposing
`refund(order_id, amount) -> refund_id`; the act step becomes a (mock)
connector call instead of a narrated event.

### Slice 4 (stretch) — ApprovalFlow + github_pr adopt the seams

Both flows adopt `ApprovalService` (pause/resolve + authorization) and emit
typed `AuditEvent`s. No policy engine for either: ApprovalFlow is always
human sign-off; github_pr's gate is the requester's confirm. If the demo
clock wins, this slice drops without weakening slices 1–3.

## Error handling

- **Fail closed, everywhere.** Missing/untyped fact → `stop` → run status
  `needs_attention` (reusing the branch's status and Slack messaging).
  Missing/invalid policy file → the engine refuses to evaluate → same stop
  path. A run never reaches `act` on an error.
- **Approval edges (owned by `ApprovalService`):** double-click → idempotent
  re-render of the decided card; unauthorized clicker → hard-deny + an audit
  event recording who tried; run with no recorded approver → hard-deny;
  unknown/mismatched run → logged warning, no-op.
- **Redis degradation:** reads degrade gracefully; writes that would lose
  governance data fail loudly (the `SkillStorePersistenceError` pattern). A
  pending approval that cannot be persisted aborts the run with `error`
  rather than continuing ungoverned.
- **LLM failure (`RefundEngineError`):** unchanged — the run errors before
  any guardrail or act step.

## Testing

- `test_policy_engine.py`: 300/20d → allow; 600 → require_approval +
  `required_role=manager`; missing amount → stop; a YAML threshold flip flips
  the outcome with no code change (fixture policy variant).
- `test_policy_store.py`: loads and validates `refund.v1.yaml`; malformed
  file → refused.
- `test_approval_service.py`: unauthorized identity → denied + audited;
  pending survives a store reload; resolve is idempotent; `act` is reached
  only on approve.
- `test_audit_log.py`: one `run_id` yields the ordered trail; the guardrail
  event carries `rule.id/version/result`; the approve event carries a human
  Actor with email + entra_id.
- Updated, not broken: `test_refund_flow.py`, `test_refund_engine.py`,
  `test_refund_cards.py`, `test_runs_api.py`,
  `test_refund_e2e_integration.py` adapt to the facts/decision split and
  must pass per slice.
- Frontend: `pnpm typecheck && pnpm lint && pnpm build` after the admin
  trail change.

Each slice is done only with its tests green
(`cd substrateos-api && uv run pytest tests/ -q`).

## Out of scope (parked, stated honestly)

Managed skill lifecycle (WS 5 — next spec); AI-drafted skills and drift/gap
detection; fully generic multi-playbook YAML engine (one playbook; prove
reusability via seams); multi-tenant; deep RBAC; tamper-evident audit; full
OpenTelemetry; real Stripe / broad connectors. README gains a "what's real
vs mocked" note: guardrail, approval, audit, identity enforced in code on
the refund use case; order/customer data and Stripe are seeded/mocked.
