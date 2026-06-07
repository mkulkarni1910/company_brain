# SME Skill Studio — Design

**Date:** 2026-06-07
**Status:** Approved (brainstorm with Lokesh)
**Demo context:** Act 1 of `demo_flow_diagram.svg` — "An SME authors it → an external agent uses it → SubstrateOS governs it." This feature delivers the **core authoring flow only**: plain English → AI-populated skill form → SME review → Admin approval → live.

## Problem

Today only Entra "Admin" group members can create org skills, via the raw CRUD form in
`/admin/skills`. Subject-matter experts (e.g. Finance) hold the actual know-how but cannot
author skills, and there is no review gate between "someone typed a skill" and "the catalog,
router, and MCP surface serve it".

## Goal

Members of the Entra **"Finance SME"** group get a dedicated **Skill Studio** where they
describe a skill in plain English; AI auto-populates the structured skill form; the SME
reviews/edits and submits; an **Admin approves** (Slack card or admin queue) before the
skill becomes live. Every step is audited.

## Decision: submission is a workflow run (Approach C)

A submission does **not** touch the live skill store. It is a run in the existing run store:

- `kind = "skill_publish"`, `status = "pending_approval"`
- run payload carries the AI-drafted `SkillCreate` + the SME's source text + submitter identity
- run events provide the audit trail and the submission appears in `/admin/runs` automatically
- the existing approval playbook resolves the SME's **manager via Entra** and DMs the Slack
  Approve/Reject card
- on **approve** → `SkillStore.create()` writes the live skill (enabled); on **reject** →
  note recorded on the run, visible to the SME in the Studio

Alternatives considered and rejected:

- **`status` field on `Skill` in the live store** — every read path (catalog, router, MCP)
  must filter correctly or an unapproved skill leaks; cuts against double-enforcement.
- **Separate `SkillSubmission` store** — duplicates the approval-state machine and audit
  trail the run store already provides.

With Approach C an unapproved skill is *structurally unable* to reach the catalog.

## Architecture & data flow

```
SME (Entra "Finance SME")                       Admin (Entra "Admin")
   │                                                  │
   ▼                                                  ▼
/studio (new route, light shell)              /admin/skills (+ Pending queue)
   │ 1. plain English                                 │
   ▼                                                  │
POST /studio/draft ──► LLM (existing client) ─► populated SkillCreate form
   │ 2. SME reviews/edits, submits                    │
   ▼                                                  │
POST /studio/submit ─► Run(kind="skill_publish",      │
   │                   status=pending_approval,       │
   │                   payload=draft + submitter)     │
   │                       │                          │
   │                       ├─► approval playbook: resolve SME's manager
   │                       │   via Entra → Slack Approve/Reject card
   │                       └─► run events = audit trail (shows in /admin/runs)
   │                                                  │
   │            3. approve (Slack card OR admin queue)│
   │                       ▼                          │
   │            SkillStore.create() → skill is LIVE in catalog/MCP/router
   │            reject → note recorded, SME sees it in Studio
```

## Backend

### Auth — SME gate

- New setting `ENTRA_SME_GROUP` (default `"Finance SME"`).
- New `require_sme` dependency mirroring `app/api/_admin_guard.py` exactly:
  token `group_ids` → app-only Graph `transitiveMemberOf` fallback → 10-minute cache by
  email → **fail closed** on Graph error.
- Members of the Admin group implicitly pass the SME gate (admins can do anything SMEs can).
- `GET /me` response gains `is_sme: bool` alongside `is_admin`.

### Endpoints

| Endpoint | Guard | Behaviour |
|---|---|---|
| `POST /studio/draft` | require_sme | `{text}` → one LLM `complete()` call with a JSON-output prompt → `SkillCreate`-shaped draft (`name, slug, description, team, run_scope, steps[], data_feeds[], system_prompt`) |
| `POST /studio/submit` | require_sme | `{skill: SkillCreate, source_text}` → slug check (live + pending) → create run → record events → fire Slack card to manager → return run id |
| `GET /studio/submissions` | require_sme | Only the **caller's own** `skill_publish` runs (id, name, status, rejection note, timestamps) |
| `POST /admin/skill-submissions/{run_id}/approve` | require_admin | Re-check slug → `SkillStore.create()` → run status approved → events |
| `POST /admin/skill-submissions/{run_id}/reject` | require_admin | `{note}` → run status rejected, note on payload → events |

Slack card Approve/Reject actions route to the same approve/reject logic as the admin
endpoints (single code path for the decision).

### AI drafting

One `complete()` call against the existing LLM client (`app/generation/`), system prompt
instructing strict JSON matching the `SkillCreate` shape. Temperature 0. The returned draft
is validated through the `SkillCreate` Pydantic model before it reaches the client. The form
is fully editable afterwards, so an LLM failure degrades gracefully: 502 with a friendly
message and the SME can fill the form by hand.

## Frontend

Mockup-first, per the project workflow — no `.tsx` until mockups are approved.

- **`mockups/sme-studio.html`** (new): textarea → "Draft with AI" → populated, editable
  skill form → "Submit for approval"; below, **My submissions** with status badges
  (pending / approved / rejected + note). Warm-paper design system (Fraunces / Archivo /
  JetBrains Mono, existing CSS variables).
- **`mockups/admin-portal.html`**: Org Skills page gains a **Pending approval** section —
  drafted skill expanded for review, Approve / Reject-with-note buttons.
- React: new `/studio` route with its own light shell (not inside `/admin`), gated
  client-side on `is_sme || is_admin` from `/me`; backend enforcement via `require_sme`
  (double enforcement). Admin queue added to `web/app/admin/skills/page.tsx`.

## Error handling & edge cases

- **Manager not resolvable** → submission still succeeds and lands in the admin queue; an
  `approver_not_resolved` event is recorded and the Slack card is skipped. The admin queue
  is the source of truth; Slack is an accelerator.
- **Slug collision** → checked at submit (against live skills *and* pending submissions)
  and re-checked at approval; 409 with a clear message.
- **Graph outage** → SME gate fails closed, same as the admin gate.
- **Double decision** (Slack card vs admin queue race) → first decision wins; the second
  actor gets "already decided", matching existing run-status semantics.
- **LLM failure on draft** → 502; form remains manually fillable.

## Security

- Double enforcement everywhere: client-side gating is cosmetic; every `/studio/*` endpoint
  enforces `require_sme`, every decision endpoint enforces `require_admin`.
- Unapproved skills never enter the skill store — no filter discipline required on catalog,
  router, or MCP read paths.
- `GET /studio/submissions` is filtered server-side to the caller's own submissions.
- Slack decision actions are verified against the resolved approver identity (existing
  approval-card machinery).

## Testing

`substrateos-api/tests/test_studio.py`, following existing suite patterns (fakes for Azure
clients, stubbed LLM, `pytest-asyncio`):

1. SME gate: group member allowed, non-member 403, Graph failure → 403 (fail closed),
   Admin-group member allowed.
2. Draft: stubbed LLM returns JSON → validated `SkillCreate`; malformed LLM output → 502.
3. Submit: run created with kind/status/payload, events recorded, Slack card attempted;
   manager-unresolvable → submission still succeeds + `approver_not_resolved` event.
4. Approve: live skill created, run status approved; slug collision at approval → 409.
5. Reject: note recorded, run status rejected.
6. Submissions ACL: caller sees only their own.
7. Double decision: second decision → "already decided".

Frontend: `pnpm typecheck && pnpm lint && pnpm build`.

## Out of scope (deliberate, for later)

- Chips-confirmation + one clarifying question (diagram Act 1 step 2)
- Verify-by-example against real refund cases (step 3)
- Skill versioning ("Refunds v2" / Registry + Versions)
- SME edit-and-resubmit of a rejected draft (a rejected SME submits afresh)
