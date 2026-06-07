# SME Skill Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entra "Finance SME" group members author skills in plain English at `/studio`; AI populates the skill form; an Admin approves (Slack card to the SME's manager, or the admin-panel queue) before the skill enters the live catalog.

**Architecture:** A submission is a workflow run (`kind="skill_publish"`) in the existing `RunStore` — the AI-drafted `SkillCreate` rides on the run and the live `SkillStore` is only written on approval (Approach C in the spec, `docs/superpowers/specs/2026-06-07-sme-skill-studio-design.md`). A new `require_sme` guard mirrors `_admin_guard.py`. One LLM call drafts the form.

**Tech Stack:** FastAPI + Pydantic + Redis (existing stores), existing Gemini/AzureOpenAI `complete()` client, Slack Block Kit cards, Next.js 14 + Tailwind (existing web app). **No new libraries.**

**Worktree:** all work in `.worktrees/sme-skill-studio` on branch `feat/sme-skill-studio`. All backend commands run from `substrateos-api/`, frontend from `web/`.

**Execution gates:**
- ⛔ **Task 1 ends with a USER APPROVAL GATE** (mockup review in the browser). Tasks 10–11 (React) MUST NOT start before that approval. Tasks 2–9 (backend) may proceed while waiting.
- ⛔ Merge/deploy (after Task 13) needs explicit user approval — not part of this plan.

## File map

| File | Action | Responsibility |
|---|---|---|
| `mockups/sme-studio.html` | Create | Studio mockup (Task 1) |
| `mockups/admin-portal.html` | Modify | Pending-approval queue on Org Skills (Task 1) |
| `substrateos-api/app/domain/workflow.py` | Modify | `skill_publish` run kind + draft fields (Task 2) |
| `substrateos-api/app/config.py` | Modify | `entra_sme_group` setting (Task 3) |
| `substrateos-api/app/api/_sme_guard.py` | Create | `user_is_sme` / `require_sme` (Task 3) |
| `substrateos-api/app/api/me.py` | Modify | `is_sme` on `/me` (Task 4) |
| `substrateos-api/app/skills/drafter.py` | Create | plain English → `SkillCreate` via LLM (Task 5) |
| `substrateos-api/app/bots/approval_cards.py` | Modify | skill-publish Slack card (Task 6) |
| `substrateos-api/app/workflows/skill_publish.py` | Create | submit → route → decide flow (Task 7) |
| `substrateos-api/app/api/studio.py` | Create | `/studio/*` + `/admin/skill-submissions/*` (Task 8) |
| `substrateos-api/app/deps.py` | Modify | new providers (Task 8) |
| `substrateos-api/app/main.py` | Modify | wiring + routers (Task 8) |
| `substrateos-api/app/api/bots.py` | Modify | `skillpub_` Slack dispatch (Task 9) |
| `web/lib/api.ts` | Modify | `Me.is_sme` (Task 10) |
| `web/lib/studioApi.ts` | Create | studio + submissions API client (Task 10) |
| `web/app/studio/page.tsx` | Create | Studio page (Task 10) |
| `web/app/admin/skills/page.tsx` | Modify | pending queue (Task 11) |
| `mockups/architecture.html` | Modify | both views updated (Task 13) |

Tests: `tests/test_sme_guard.py`, `tests/test_skill_drafter.py`, `tests/test_skill_publish_flow.py`, `tests/test_studio_api.py` (create); `tests/test_me_api.py`, `tests/test_slack_interactive.py` (extend).

---

### Task 1: Mockups (frontend gate — do this first)

**Files:**
- Create: `mockups/sme-studio.html`
- Modify: `mockups/admin-portal.html`

The project's firmest rule: no `.tsx` until these are approved. Match the warm-paper design system used by `mockups/user-web-chat.html` / `mockups/admin-portal.html` — Fraunces (display), Archivo (UI), JetBrains Mono (code/slugs), and the CSS variables already defined in those files (`--paper`, `--ink`, `--ink-dim`, `--ink-faint`, etc.). Reuse the existing button/badge/table styles; invent no new visual language.

- [ ] **Step 1: Build `mockups/sme-studio.html`** — a standalone page (own light shell, NOT the admin sidebar) containing:
  - Header: "Skill Studio" (Fraunces) with subtitle "Turn what you know into a governed skill — no code." and the signed-in SME identity top-right (e.g. "Deepa Rao · Finance SME").
  - **Card 1 — Describe it:** a large textarea (placeholder: *"e.g. Refunds under $500 and 30 days are auto-approved. Anything bigger needs my sign-off…"*) and a primary button **"Draft with AI"**.
  - **Card 2 — Review the draft** (shown populated): the skill form — Name, Slug (mono), Description, Team, Steps (editable list), Data feeds (editable list), System prompt (textarea) — pre-filled with a realistic refund-policy example, every field editable. Footer: ghost "Start over" + primary **"Submit for approval"**, with a caption "An admin reviews before this goes live." and a 🔒 lock motif.
  - **Card 3 — My submissions:** table of submissions with status badges — `pending approval` (amber), `live` (green), `rejected` (rose, with the admin's note shown beneath).
- [ ] **Step 2: Add the pending queue to `mockups/admin-portal.html`** — on the Org Skills section, above the existing skills table, a **"Pending approval"** block: one expanded submission card (name, slug, submitter, source text excerpt, drafted steps + system prompt) with **Approve** (primary) and **Reject…** (danger, reveals a note field) buttons. Style consistent with the existing admin cards.
- [ ] **Step 3: Open both in the browser**

```bash
open mockups/sme-studio.html mockups/admin-portal.html
```

- [ ] **Step 4: Commit**

```bash
git add mockups/sme-studio.html mockups/admin-portal.html
git commit -m "mockup(studio): SME Skill Studio + admin pending-approval queue"
```

- [ ] **Step 5: ⛔ USER APPROVAL GATE** — present both mockups and wait for explicit approval. Iterate here until approved. Tasks 10–11 are blocked on this gate.

---

### Task 2: Domain — `skill_publish` run kind + draft fields

**Files:**
- Modify: `substrateos-api/app/domain/workflow.py`
- Test: `substrateos-api/tests/test_skill_publish_flow.py` (created here, grown in Task 7)

- [ ] **Step 1: Write the failing test**

```python
"""SkillPublishFlow: submit → manager routing → decide. Runs use RunStore(force_memory=True)."""
from __future__ import annotations

import pytest

from app.domain.skill import SkillCreate
from app.domain.workflow import RefundRun


def _draft(**over) -> SkillCreate:
    base = dict(slug="refund-approvals", name="Refund approvals",
                description="Auto-approve small refunds, route big ones.",
                team="Finance", run_scope="org", enabled=True,
                steps=["Check amount", "Stop if over limit", "Record"],
                data_feeds=["Orders"], system_prompt="You enforce the refund policy.")
    base.update(over)
    return SkillCreate(**base)


def test_run_round_trips_skill_draft() -> None:
    run = RefundRun(id="RB-1", kind="skill_publish", status="pending_approval",
                    requester_name="Deepa Rao", requester_email="deepa@example.com",
                    skill_draft=_draft(), rejection_note=None,
                    created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                    updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC))
    parsed = RefundRun.model_validate_json(run.model_dump_json())
    assert parsed.kind == "skill_publish"
    assert parsed.skill_draft is not None and parsed.skill_draft.slug == "refund-approvals"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd substrateos-api && uv run pytest tests/test_skill_publish_flow.py -q`
Expected: FAIL — `Input should be 'refund', 'approval' or 'github_pr'` (and unknown fields).

- [ ] **Step 3: Implement** — in `app/domain/workflow.py`:

Add the import at the top (after the existing imports):

```python
from app.domain.skill import SkillCreate
```

Extend the kind literal:

```python
RunKind = Literal["refund", "approval", "github_pr", "skill_publish"]
```

Add to `RefundRun`, after the `pr_url: str | None = None` line:

```python
    # skill_publish playbook (SME Skill Studio): the AI-drafted skill awaiting
    # an admin's decision — the live SkillStore is only written on approval.
    skill_draft: SkillCreate | None = None
    rejection_note: str | None = None
```

- [ ] **Step 4: Run the test — PASS.** Also run `uv run pytest tests/test_workflow_models.py tests/test_run_store.py -q` (untouched behavior stays green).
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(domain): skill_publish run kind carries the drafted skill"`

---

### Task 3: Config + SME guard

**Files:**
- Modify: `substrateos-api/app/config.py`
- Create: `substrateos-api/app/api/_sme_guard.py`
- Test: `substrateos-api/tests/test_sme_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
"""user_is_sme: group-claim match, Graph member-email fallback, fail-closed, admin implies SME."""
import pytest

import app.api._sme_guard as guard
from app.config import get_settings
from app.domain.identity import User


class FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    async def get_json(self, key: str):
        return self.store.get(key)

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        self.store[key] = value


def _user(email: str = "deepa@example.com", groups: set[str] | None = None) -> User:
    return User(user_id="u1", tenant_id="t1", email=email,
                display_name="Deepa", group_ids=groups or set())


@pytest.fixture()
def _prod_like(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "false")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_group_claim_match_needs_no_graph(_prod_like) -> None:  # noqa: ANN001
    assert await guard.user_is_sme(_user(groups={"Finance SME"}), cache=None) is True


@pytest.mark.asyncio
async def test_admin_group_implies_sme(_prod_like, monkeypatch) -> None:  # noqa: ANN001
    # SME graph lookup misses; the Admin group claim still grants access.
    async def no_members(token, group_name, **kw):  # noqa: ANN001
        return set()

    async def fake_token(tenant):  # noqa: ANN001
        return "tok"

    monkeypatch.setattr(guard, "graph_token", fake_token)
    monkeypatch.setattr(guard, "group_member_emails", no_members)
    assert await guard.user_is_sme(_user(groups={"Admin"}), FakeCache()) is True


@pytest.mark.asyncio
async def test_graph_member_email_grants(monkeypatch, _prod_like) -> None:  # noqa: ANN001
    async def fake_token(tenant):  # noqa: ANN001
        return "tok"

    async def fake_members(token, group_name, **kw):  # noqa: ANN001
        return {"deepa@example.com"} if group_name == "Finance SME" else set()

    monkeypatch.setattr(guard, "graph_token", fake_token)
    monkeypatch.setattr(guard, "group_member_emails", fake_members)
    # _admin_guard does its own Graph round-trip for the admin fallback — patch it too.
    import app.api._admin_guard as admin_guard
    monkeypatch.setattr(admin_guard, "graph_token", fake_token)
    monkeypatch.setattr(admin_guard, "group_member_emails", fake_members)
    assert await guard.user_is_sme(_user("Deepa@Example.com"), FakeCache()) is True
    assert await guard.user_is_sme(_user("tom@example.com"), FakeCache()) is False


@pytest.mark.asyncio
async def test_graph_failure_fails_closed_and_uncached(monkeypatch, _prod_like) -> None:  # noqa: ANN001
    async def boom(tenant):  # noqa: ANN001
        raise RuntimeError("graph down")

    monkeypatch.setattr(guard, "graph_token", boom)
    import app.api._admin_guard as admin_guard
    monkeypatch.setattr(admin_guard, "graph_token", boom)
    cache = FakeCache()
    assert await guard.user_is_sme(_user(), cache) is False
    assert cache.store == {}  # transient error must not pin a deny for the TTL
```

- [ ] **Step 2: Run — FAIL** (`ModuleNotFoundError: app.api._sme_guard`): `uv run pytest tests/test_sme_guard.py -q`
- [ ] **Step 3: Add the setting** — in `app/config.py`, directly below the `entra_admins_group` line (~113):

```python
    entra_sme_group: str = "Finance SME"          # Entra group → /studio skill authoring
```

- [ ] **Step 4: Create `app/api/_sme_guard.py`**

```python
"""Studio-route guard: Entra SME group (ENTRA_SME_GROUP, "Finance SME" default).

Mirrors _admin_guard: membership comes from the token's group claims when they
carry it, otherwise from an app-only Graph lookup of the group's member emails,
cached for ten minutes. The Graph path fails CLOSED. Admins implicitly pass —
anything an SME may do, an admin may do. Unlike require_admin this returns the
resolved User: studio endpoints need the submitter's identity.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.api._admin_guard import user_is_admin
from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.connectors.graph import graph_token, group_member_emails
from app.domain.identity import User

_MEMBERS_TTL_SECONDS = 600  # re-check group membership via Graph every 10 min


async def _sme_member_emails(cache) -> set[str]:
    """Emails of the SME group's members via app-only Graph, cached. Failures
    return an empty set (deny) and are NOT cached — same shape as _admin_guard."""
    s = get_settings()
    if s.enable_debug_auth:
        return set()  # debug header carries group names directly
    key = f"sme:members:{s.entra_sme_group.lower()}"
    if cache is not None:
        cached = await cache.get_json(key)
        if cached is not None:
            return set(cached.get("emails", []))
    try:
        token = await graph_token(s.azure_tenant_id)
        emails = await group_member_emails(token, s.entra_sme_group)
    except Exception:  # noqa: BLE001 — fail closed, never fail open
        return set()
    if cache is not None:
        await cache.set_json(key, {"emails": sorted(emails)},
                             ttl_seconds=_MEMBERS_TTL_SECONDS)
    return emails


async def user_is_sme(user: User, cache) -> bool:
    """True for SME-group members — and for admins (superset)."""
    s = get_settings()
    if s.entra_sme_group in user.group_ids:
        return True
    email = (user.email or "").lower()
    if email and email in await _sme_member_emails(cache):
        return True
    return await user_is_admin(user, cache)


async def require_sme(
    request: Request,
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> User:
    """Dependency for /studio — returns the resolved submitter."""
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth)
    cache = getattr(request.app.state, "cache", None)
    if await user_is_sme(user, cache):
        return user
    raise HTTPException(
        status_code=403,
        detail=f"studio access requires the {get_settings().entra_sme_group!r} Entra group")
```

- [ ] **Step 5: Run — PASS**: `uv run pytest tests/test_sme_guard.py tests/test_config.py -q`
- [ ] **Step 6: Commit** — `git commit -am "feat(auth): Entra SME-group guard for the Skill Studio (fail-closed, admin implies SME)"`

---

### Task 4: `/me` gains `is_sme`

**Files:**
- Modify: `substrateos-api/app/api/me.py`
- Test: `substrateos-api/tests/test_me_api.py` (append)

- [ ] **Step 1: Append the failing test to `tests/test_me_api.py`**

```python
def test_me_reports_sme_membership() -> None:
    # Debug header lists group names directly — "Finance SME" grants is_sme.
    hdr = {"x-debug-bypass-auth": "t-test,u-deepa,t-test:everyone,Finance SME"}
    with _client_with(FakeCache()) as client:
        r = client.get("/me", headers=hdr)
        assert r.status_code == 200
        assert r.json()["is_sme"] is True
        r2 = client.get("/me", headers=_HDR)  # plain user: not an SME
        assert r2.json()["is_sme"] is False
```

- [ ] **Step 2: Run — FAIL** (`KeyError: 'is_sme'`): `uv run pytest tests/test_me_api.py -q`
- [ ] **Step 3: Implement** — in `app/api/me.py`:
  - add import: `from app.api._sme_guard import user_is_sme`
  - add to the `Me` model after `is_admin`:

```python
    # Member of the Entra SME group (config: ENTRA_SME_GROUP)? Gates /studio
    # in the web UI. Fail-soft false — the backend re-checks anyway.
    is_sme: bool = False
```

  - in the `me()` handler, after `is_admin = await user_is_admin(user, cache)`:

```python
    is_sme = await user_is_sme(user, cache)
```

  and extend the return: `return Me(display_name=..., email=..., title=title, is_admin=is_admin, is_sme=is_sme)`
- [ ] **Step 4: Run — PASS**: `uv run pytest tests/test_me_api.py -q`
- [ ] **Step 5: Commit** — `git commit -am "feat(me): expose is_sme for the Studio gate"`

---

### Task 5: SkillDrafter — plain English → `SkillCreate`

**Files:**
- Create: `substrateos-api/app/skills/drafter.py`
- Test: `substrateos-api/tests/test_skill_drafter.py`

- [ ] **Step 1: Write the failing tests**

```python
"""SkillDrafter: one LLM call → validated SkillCreate; garbage → SkillDraftError."""
import json

import pytest

from app.skills.drafter import SkillDraftError, SkillDrafter

_GOOD = {
    "name": "Refund approvals", "slug": "Refund Approvals!",  # messy slug on purpose
    "description": "Auto-approve refunds under $500 and 30 days.",
    "team": "Finance",
    "steps": ["Check amount and age", "Stop if over limit", "Record the outcome"],
    "data_feeds": ["Orders"],
    "system_prompt": "You enforce the refund policy: under $500 and 30 days auto-approves.",
}


class _LLM:
    def __init__(self, reply: str | Exception):
        self._reply = reply
        self.calls: list[dict] = []

    async def complete(self, *, messages, deployment=None, temperature=0.0, max_tokens=800):
        self.calls.append({"messages": messages, "temperature": temperature})
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


@pytest.mark.asyncio
async def test_draft_happy_path_normalizes_slug() -> None:
    drafter = SkillDrafter(llm=_LLM(json.dumps(_GOOD)))
    skill = await drafter.draft("Refunds under $500 and 30 days auto-approve.")
    assert skill.slug == "refund-approvals"
    assert skill.name == "Refund approvals"
    assert skill.enabled is True and skill.run_scope == "org" and skill.workflow is None
    assert skill.steps == _GOOD["steps"]


@pytest.mark.asyncio
async def test_draft_strips_code_fences() -> None:
    drafter = SkillDrafter(llm=_LLM("```json\n" + json.dumps(_GOOD) + "\n```"))
    skill = await drafter.draft("whatever")
    assert skill.slug == "refund-approvals"


@pytest.mark.asyncio
async def test_non_json_reply_raises() -> None:
    drafter = SkillDrafter(llm=_LLM("I'm sorry, I can't help with that."))
    with pytest.raises(SkillDraftError):
        await drafter.draft("whatever")


@pytest.mark.asyncio
async def test_llm_failure_raises() -> None:
    drafter = SkillDrafter(llm=_LLM(RuntimeError("model down")))
    with pytest.raises(SkillDraftError):
        await drafter.draft("whatever")


@pytest.mark.asyncio
async def test_missing_name_raises() -> None:
    drafter = SkillDrafter(llm=_LLM(json.dumps({"description": "x"})))
    with pytest.raises(SkillDraftError):
        await drafter.draft("whatever")
```

- [ ] **Step 2: Run — FAIL** (module missing): `uv run pytest tests/test_skill_drafter.py -q`
- [ ] **Step 3: Create `app/skills/drafter.py`**

```python
"""Plain English → a populated SkillCreate draft — the Studio's one LLM call.

The draft is a *suggestion*: the SME edits the form before submitting, and an
admin approves before anything goes live, so a mediocre draft costs nothing
but a failed draft must fail loudly (the API maps SkillDraftError to a 502
and the SME fills the form by hand).
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from app.domain.skill import SkillCreate

logger = logging.getLogger(__name__)


class SkillDraftError(RuntimeError):
    """LLM unavailable, or its reply isn't a usable skill draft."""


_SYSTEM_PROMPT = """\
You turn a subject-matter expert's plain-English description of a business
rule or process into a skill definition for SubstrateOS, the company brain.
Skills follow one shape: When → Check → Stop (a human approves if risky) →
Do it → Record.

Return ONLY a JSON object — no prose, no code fences — with exactly these keys:
  "name": short human title, e.g. "Refund approvals"
  "slug": kebab-case identifier derived from the name
  "description": 1-2 sentences — what the skill does and when to use it
  "team": the owning team inferred from the text (default "Finance")
  "steps": 3-6 short imperative strings following the When→Check→Stop→Do→Record shape
  "data_feeds": data sources the rule needs (e.g. "Orders", "Slack"); [] when none
  "system_prompt": second-person instructions the AI follows when running the
    skill, embedding every concrete threshold, limit, and rule from the text
"""


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "new-skill"


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    raise SkillDraftError("the model did not return JSON")


class SkillDrafter:
    def __init__(self, *, llm) -> None:
        self._llm = llm

    async def draft(self, text: str) -> SkillCreate:
        try:
            raw = await self._llm.complete(
                messages=[{"role": "system", "content": _SYSTEM_PROMPT},
                          {"role": "user", "content": text}],
                temperature=0.0, max_tokens=900)
        except Exception as e:  # noqa: BLE001 — any client failure is a draft failure
            raise SkillDraftError(f"draft call failed: {e}") from e
        data = _extract_json(raw)
        name = str(data.get("name") or "").strip()
        if not name:
            raise SkillDraftError("the draft is missing a name")
        try:
            return SkillCreate(
                slug=_slugify(str(data.get("slug") or name)),
                name=name,
                description=str(data.get("description") or "").strip(),
                team=str(data.get("team") or "Finance").strip(),
                run_scope="org", workflow=None, enabled=True,
                steps=[str(s) for s in (data.get("steps") or [])],
                data_feeds=[str(d) for d in (data.get("data_feeds") or [])],
                system_prompt=str(data.get("system_prompt") or "").strip(),
            )
        except ValidationError as e:
            raise SkillDraftError(f"draft failed validation: {e}") from e
```

- [ ] **Step 4: Run — PASS**: `uv run pytest tests/test_skill_drafter.py -q`
- [ ] **Step 5: Commit** — `git commit -am "feat(studio): SkillDrafter — plain English to a SkillCreate draft"`

---

### Task 6: Slack card for skill publishing

**Files:**
- Modify: `substrateos-api/app/bots/approval_cards.py`

No standalone test (pure dict builders, exercised by Task 7's flow tests — same treatment as the existing builders, which are covered via `test_approval_flow.py`).

- [ ] **Step 1: Append to `app/bots/approval_cards.py`**

```python
def skill_publish_dm_blocks(*, skill_name: str, slug: str, description: str,
                            steps: list[str], submitter_name: str, run_id: str) -> dict:
    """The Approve/Reject card DM'd to the submitter's manager for a new skill."""
    steps_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps[:6])) or "—"
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "New skill awaiting approval"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*{skill_name}*  `/{slug}`\n{description[:400]}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Steps*\n{steps_text[:600]}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Authored by*\n{submitter_name}"}},
        ],
        "attachments": [_bar(_AMBER, [
            {"type": "context", "elements": [{"type": "mrkdwn",
                "text": f":lock: not in the catalog until you decide · run {run_id}"}]},
            {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "skillpub_approve",
                 "value": run_id, "text": {"type": "plain_text", "text": "Approve"}},
                {"type": "button", "style": "danger", "action_id": "skillpub_reject",
                 "value": run_id, "text": {"type": "plain_text", "text": "Reject"}},
            ]},
        ])],
    }
```

(The decided/updated card reuses the existing `decided_dm_blocks`.)

- [ ] **Step 2: Sanity check**: `uv run pytest tests/test_refund_cards.py tests/test_approval_flow.py -q` — PASS (no behavior change).
- [ ] **Step 3: Commit** — `git commit -am "feat(slack): skill-publish approval card"`

---

### Task 7: SkillPublishFlow

**Files:**
- Create: `substrateos-api/app/workflows/skill_publish.py`
- Test: `substrateos-api/tests/test_skill_publish_flow.py` (extend from Task 2)

- [ ] **Step 1: Append the failing tests to `tests/test_skill_publish_flow.py`**

```python
from datetime import UTC, datetime

from app.domain.identity import User
from app.domain.skill import Skill
from app.workflows.skill_publish import (
    AlreadyDecidedError,
    SkillPublishFlow,
    SlugConflictError,
)
from app.workflows.store import RunStore


def _user(email: str = "deepa@example.com") -> User:
    return User(user_id="u-deepa", tenant_id="t-test", email=email,
                display_name="Deepa Rao", group_ids={"Finance SME"})


class _FakeSkillStore:
    def __init__(self, skills: list[Skill] | None = None):
        self._skills = {s.id: s for s in (skills or [])}
        self.created: list = []

    async def get_by_slug(self, slug, *, enabled_only=False):
        return next((s for s in self._skills.values() if s.slug == slug), None)

    async def create(self, data):
        if await self.get_by_slug(data.slug):
            raise ValueError(f"slug '{data.slug}' already exists")
        now = datetime.now(UTC)
        skill = Skill(id=f"id-{data.slug}", created_at=now, updated_at=now,
                      **data.model_dump())
        self._skills[skill.id] = skill
        self.created.append(skill)
        return skill


def _flow(skills=None) -> tuple[SkillPublishFlow, RunStore, _FakeSkillStore]:
    store = RunStore(force_memory=True)
    skill_store = _FakeSkillStore(skills)
    return SkillPublishFlow(store=store, skill_store=skill_store, people=None), store, skill_store


@pytest.mark.asyncio
async def test_submit_creates_pending_run_without_touching_skill_store() -> None:
    flow, store, skills = _flow()
    run = await flow.submit(draft=_draft(), source_text="refunds under $500…", user=_user())
    assert run.kind == "skill_publish" and run.status == "pending_approval"
    assert run.requester_email == "deepa@example.com"
    assert run.skill_draft.slug == "refund-approvals"
    assert skills.created == []  # nothing live yet
    events = await store.list_events(run.id)
    steps = [e.step for e in events]
    assert "Skill submitted" in steps
    assert "Approver not resolved" in steps  # people=None → no manager, still succeeds


@pytest.mark.asyncio
async def test_submit_rejects_slug_already_in_catalog() -> None:
    now = datetime.now(UTC)
    live = Skill(id="s1", created_at=now, updated_at=now, **_draft().model_dump())
    flow, _, _ = _flow([live])
    with pytest.raises(SlugConflictError):
        await flow.submit(draft=_draft(), source_text="x", user=_user())


@pytest.mark.asyncio
async def test_submit_rejects_slug_already_pending() -> None:
    flow, _, _ = _flow()
    await flow.submit(draft=_draft(), source_text="x", user=_user())
    with pytest.raises(SlugConflictError):
        await flow.submit(draft=_draft(), source_text="x", user=_user())


@pytest.mark.asyncio
async def test_approve_creates_live_skill_and_records() -> None:
    flow, store, skills = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    decided = await flow.decide(run_id=run.id, approve=True, actor_name="Diana")
    assert decided.status == "approved"
    assert [s.slug for s in skills.created] == ["refund-approvals"]
    assert any(e.step == "Approved" for e in await store.list_events(run.id))


@pytest.mark.asyncio
async def test_reject_records_note_and_keeps_catalog_clean() -> None:
    flow, store, skills = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    decided = await flow.decide(run_id=run.id, approve=False, actor_name="Diana",
                                note="Limit should be $250, not $500.")
    assert decided.status == "rejected"
    assert decided.rejection_note == "Limit should be $250, not $500."
    assert skills.created == []


@pytest.mark.asyncio
async def test_second_decision_raises_already_decided() -> None:
    flow, _, _ = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    await flow.decide(run_id=run.id, approve=True, actor_name="Diana")
    with pytest.raises(AlreadyDecidedError):
        await flow.decide(run_id=run.id, approve=False, actor_name="Tom")


@pytest.mark.asyncio
async def test_unknown_run_raises_keyerror() -> None:
    flow, _, _ = _flow()
    with pytest.raises(KeyError):
        await flow.decide(run_id="RB-9999", approve=True, actor_name="Diana")


@pytest.mark.asyncio
async def test_submit_routes_manager_card_when_resolvable(monkeypatch) -> None:
    """Spec: 'submit creates run + events + card attempted' — the resolved path."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()

    class _People:
        async def manager_of(self, *, email, tenant_id):
            assert email == "deepa@example.com"
            return {"user_id": "u-diana", "email": "diana@example.com",
                    "display_name": "Diana Prince"}

    slack_calls: list[tuple[str, dict]] = []

    async def fake_slack_get(token, method, params):
        slack_calls.append((method, params))
        return {"ok": True, "user": {"id": "U_DIANA"}}

    async def fake_slack_call(token, method, payload):
        slack_calls.append((method, payload))
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "171.001"}
        return {"ok": True}

    monkeypatch.setattr("app.workflows.skill_publish.slack_get", fake_slack_get)
    monkeypatch.setattr("app.workflows.skill_publish.slack_call", fake_slack_call)

    store = RunStore(force_memory=True)
    flow = SkillPublishFlow(store=store, skill_store=_FakeSkillStore(), people=_People())
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())

    assert run.approver_name == "Diana Prince"
    assert run.approver_slack_id == "U_DIANA"
    assert run.dm_channel == "D_DIANA" and run.dm_ts == "171.001"
    assert ("users.lookupByEmail", {"email": "diana@example.com"}) in slack_calls
    posted = next(p for m, p in slack_calls if m == "chat.postMessage")
    assert posted["channel"] == "D_DIANA"
    assert any(e.step == "Routed for approval" for e in await store.list_events(run.id))
```

- [ ] **Step 2: Run — FAIL** (module missing): `uv run pytest tests/test_skill_publish_flow.py -q`
- [ ] **Step 3: Create `app/workflows/skill_publish.py`**

```python
"""SME Skill Studio publish playbook: submit → manager sign-off → live skill.

When → Check → Stop → Do → Record over a web submission: a Finance SME submits
an AI-drafted skill from /studio, the draft is parked on a run (NEVER the live
skill store), the submitter's manager gets a Slack Approve/Reject card
(best-effort — the admin queue is the source of truth), and only a decision
writes the skill to the catalog. Mirrors ApprovalFlow, minus Slack-as-surface:
the request arrives over HTTP, so requester identity is the Entra User.
"""
from __future__ import annotations

import logging

from app.bots.approval_cards import decided_dm_blocks, skill_publish_dm_blocks
from app.bots.slack import slack_call, slack_get
from app.config import get_settings
from app.domain.identity import User
from app.domain.skill import SkillCreate
from app.domain.workflow import RefundRun
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)


class SlugConflictError(ValueError):
    """The draft's slug is already live or already pending."""


class AlreadyDecidedError(RuntimeError):
    """A second decision arrived after the run left pending_approval."""


class SkillPublishFlow:
    def __init__(self, *, store: RunStore, skill_store, people) -> None:
        self._store = store
        self._skills = skill_store
        self._people = people

    # ── submit ────────────────────────────────────────────────────────────────

    async def _check_slug_free(self, slug: str) -> None:
        if self._skills is not None and await self._skills.get_by_slug(slug) is not None:
            raise SlugConflictError(f"slug '{slug}' already exists in the catalog")
        for r in await self._store.list_runs(limit=100):
            if (r.kind == "skill_publish" and r.status == "pending_approval"
                    and r.skill_draft is not None and r.skill_draft.slug == slug):
                raise SlugConflictError(f"slug '{slug}' already has a pending submission")

    async def submit(self, *, draft: SkillCreate, source_text: str, user: User) -> RefundRun:
        await self._check_slug_free(draft.slug)
        run = await self._store.create(
            requester_name=user.display_name, requester_slack_id=None,
            channel=None, thread_ts=None, kind="skill_publish",
            request_text=source_text or None)
        run.requester_email = user.email
        run.skill_draft = draft
        run.status = "pending_approval"
        run.surface = "web"
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Skill submitted",
            detail=f"'{draft.name}' (/{draft.slug}) drafted from plain English in the Studio",
            actor=user.display_name)
        await self._route_to_manager(run, user)
        return run

    async def _route_to_manager(self, run: RefundRun, user: User) -> None:
        """Best-effort Slack card to the submitter's Entra manager. Failure to
        route never fails the submission — the admin queue is the source of truth."""
        s = get_settings()
        token = s.slack_bot_token or ""
        mgr: dict | None = None
        if self._people is not None and user.email:
            try:
                mgr = await self._people.manager_of(
                    email=user.email, tenant_id=s.substrateos_tenant_id)
            except Exception:  # noqa: BLE001 — best-effort
                mgr = None
        sid: str | None = None
        if token and mgr and mgr.get("email"):
            body = await slack_get(token, "users.lookupByEmail", {"email": mgr["email"]})
            sid = ((body or {}).get("user") or {}).get("id")
        if not sid:
            await self._store.add_event(
                run.id, step="Approver not resolved",
                detail="No manager reachable on Slack — review it in the admin queue",
                actor="SubstrateOS")
            return
        run.approver_name = mgr.get("display_name") or "your manager"
        run.approver_slack_id = sid
        run.approver_source = "manager"
        opened = await slack_call(token, "conversations.open", {"users": sid})
        dm = ((opened or {}).get("channel") or {}).get("id")
        if dm:
            d = run.skill_draft
            posted = await slack_call(token, "chat.postMessage", {
                "channel": dm, "text": "New skill awaiting approval",
                **skill_publish_dm_blocks(
                    skill_name=d.name, slug=d.slug, description=d.description,
                    steps=d.steps, submitter_name=run.requester_name, run_id=run.id),
            })
            if posted:
                run.dm_channel = dm
                run.dm_ts = posted.get("ts")
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed for approval",
            detail=f"Approve/Reject card sent to {run.approver_name} in Slack",
            actor="SubstrateOS")

    # ── decide (single code path: admin queue AND Slack card) ────────────────

    async def decide(self, *, run_id: str, approve: bool, actor_name: str,
                     note: str | None = None) -> RefundRun:
        run = await self._store.get(run_id)
        if run is None or run.kind != "skill_publish" or run.skill_draft is None:
            raise KeyError(run_id)
        if run.status != "pending_approval":
            raise AlreadyDecidedError(run.status)
        if approve:
            # ValueError (slug landed while pending) propagates → API maps to 409.
            await self._skills.create(run.skill_draft)
            run.status = "approved"
        else:
            run.status = "rejected"
            run.rejection_note = (note or "").strip() or None
        run.approver_name = actor_name
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Approved" if approve else "Rejected",
            detail=(f"{actor_name} approved — '{run.skill_draft.name}' is live in the catalog"
                    if approve else
                    f"{actor_name} rejected: {run.rejection_note or 'no note'}"),
            actor=actor_name)
        await self._update_dm_card(run, approved=approve)
        return run

    async def _update_dm_card(self, run: RefundRun, *, approved: bool) -> None:
        token = get_settings().slack_bot_token or ""
        if not (token and run.dm_channel and run.dm_ts):
            return
        await slack_call(token, "chat.update", {
            "channel": run.dm_channel, "ts": run.dm_ts,
            "text": f"Skill {'approved' if approved else 'rejected'}",
            **decided_dm_blocks(
                request_text=f"Skill '{run.skill_draft.name}' (/{run.skill_draft.slug})",
                approved=approved, approver_name=run.approver_name or "an admin"),
        })

    # ── Slack button clicks ───────────────────────────────────────────────────

    async def handle_action(self, payload: dict) -> None:
        token = get_settings().slack_bot_token or ""
        actions = payload.get("actions") or []
        if not actions:
            return
        action_id = actions[0].get("action_id")
        if action_id not in ("skillpub_approve", "skillpub_reject"):
            return
        run_id = actions[0].get("value") or ""
        run = await self._store.get(run_id)
        if run is None or run.kind != "skill_publish":
            logger.warning("skillpub action for unknown/mismatched run %r", run_id)
            return
        clicker = (payload.get("user") or {}).get("id")
        if run.approver_slack_id and clicker != run.approver_slack_id:
            logger.warning("skillpub click by %r ignored — routed approver is %r",
                           clicker, run.approver_slack_id)
            return
        body = await slack_call(token, "users.info", {"user": clicker}) if clicker else None
        profile = ((body or {}).get("user") or {})
        actor = (profile.get("profile", {}).get("display_name")
                 or profile.get("real_name")
                 or (payload.get("user") or {}).get("name") or "Manager")
        try:
            await self.decide(run_id=run_id, approve=(action_id == "skillpub_approve"),
                              actor_name=actor)
        except AlreadyDecidedError:
            await self._update_dm_card(run, approved=(run.status in ("approved", "completed")))
        except ValueError as e:  # slug conflict at approval time
            if run.dm_channel:
                await slack_call(token, "chat.postMessage", {
                    "channel": run.dm_channel,
                    "text": f"Couldn't publish: {e}. Review it in the admin panel."})
```

- [ ] **Step 4: Run — PASS**: `uv run pytest tests/test_skill_publish_flow.py -q`
- [ ] **Step 5: Commit** — `git commit -am "feat(studio): SkillPublishFlow — submission run, manager card, decide writes the catalog"`

---

### Task 8: Studio API + deps + main wiring

**Files:**
- Create: `substrateos-api/app/api/studio.py`
- Modify: `substrateos-api/app/deps.py`, `substrateos-api/app/main.py`
- Test: `substrateos-api/tests/test_studio_api.py`

- [ ] **Step 1: Write the failing tests**

```python
"""/studio + /admin/skill-submissions: SME-gated drafting/submission, admin decisions."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.deps import get_run_store, get_skill_drafter, get_skill_publish_flow
from app.domain.skill import Skill, SkillCreate
from app.main import app
from app.skills.drafter import SkillDraftError
from app.workflows.skill_publish import SkillPublishFlow
from app.workflows.store import RunStore

_SME = {"x-debug-bypass-auth": "t-test,u-deepa,t-test:everyone,Finance SME"}
_ADMIN = {"x-debug-bypass-auth": "t-test,u-diana,t-test:everyone,Admin"}
_PLAIN = {"x-debug-bypass-auth": "t-test,u-bob,t-test:everyone"}

_DRAFT = dict(slug="refund-approvals", name="Refund approvals",
              description="Auto-approve small refunds.", team="Finance",
              run_scope="org", enabled=True, steps=["Check", "Stop", "Record"],
              data_feeds=["Orders"], system_prompt="Enforce the refund policy.")


class _FakeSkillStore:
    def __init__(self):
        self._by_slug: dict[str, Skill] = {}

    async def get_by_slug(self, slug, *, enabled_only=False):
        return self._by_slug.get(slug)

    async def create(self, data: SkillCreate) -> Skill:
        if data.slug in self._by_slug:
            raise ValueError(f"slug '{data.slug}' already exists")
        now = datetime.now(UTC)
        skill = Skill(id=f"id-{data.slug}", created_at=now, updated_at=now,
                      **data.model_dump())
        self._by_slug[data.slug] = skill
        return skill


class _FakeDrafter:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    async def draft(self, text: str) -> SkillCreate:
        if self._error:
            raise self._error
        return self._result


@pytest.fixture()
def harness():
    run_store = RunStore(force_memory=True)
    skills = _FakeSkillStore()
    flow = SkillPublishFlow(store=run_store, skill_store=skills, people=None)
    app.dependency_overrides[get_run_store] = lambda: run_store
    app.dependency_overrides[get_skill_publish_flow] = lambda: flow
    app.dependency_overrides[get_skill_drafter] = lambda: _FakeDrafter(SkillCreate(**_DRAFT))
    yield run_store, skills
    app.dependency_overrides.clear()


def test_studio_requires_sme_group(harness) -> None:
    with TestClient(app) as client:
        assert client.post("/studio/draft", json={"text": "x"}, headers=_PLAIN).status_code == 403
        assert client.get("/studio/submissions", headers=_PLAIN).status_code == 403


def test_admin_passes_the_sme_gate(harness) -> None:
    with TestClient(app) as client:
        assert client.get("/studio/submissions", headers=_ADMIN).status_code == 200


def test_draft_returns_populated_skill(harness) -> None:
    with TestClient(app) as client:
        r = client.post("/studio/draft", json={"text": "refunds under $500…"}, headers=_SME)
    assert r.status_code == 200
    assert r.json()["slug"] == "refund-approvals"


def test_draft_failure_maps_to_502(harness) -> None:
    app.dependency_overrides[get_skill_drafter] = (
        lambda: _FakeDrafter(error=SkillDraftError("no json")))
    with TestClient(app) as client:
        r = client.post("/studio/draft", json={"text": "x"}, headers=_SME)
    assert r.status_code == 502


def test_submit_then_own_submissions_only(harness) -> None:
    with TestClient(app) as client:
        r = client.post("/studio/submit",
                        json={"skill": _DRAFT, "source_text": "refunds…"}, headers=_SME)
        assert r.status_code == 201
        run_id = r.json()["run_id"]
        mine = client.get("/studio/submissions", headers=_SME).json()
        assert [s["run_id"] for s in mine] == [run_id]
        assert mine[0]["status"] == "pending_approval"
        # another SME sees nothing
        other = {"x-debug-bypass-auth": "t-test,u-raj,t-test:everyone,Finance SME"}
        assert client.get("/studio/submissions", headers=other).json() == []


def test_duplicate_pending_slug_is_409(harness) -> None:
    with TestClient(app) as client:
        assert client.post("/studio/submit", json={"skill": _DRAFT},
                           headers=_SME).status_code == 201
        assert client.post("/studio/submit", json={"skill": _DRAFT},
                           headers=_SME).status_code == 409


def test_admin_queue_approve_creates_live_skill(harness) -> None:
    _, skills = harness
    with TestClient(app) as client:
        run_id = client.post("/studio/submit", json={"skill": _DRAFT},
                             headers=_SME).json()["run_id"]
        # queue is admin-only
        assert client.get("/admin/skill-submissions", headers=_SME).status_code == 403
        queue = client.get("/admin/skill-submissions", headers=_ADMIN).json()
        assert queue[0]["run_id"] == run_id and queue[0]["skill"]["slug"] == "refund-approvals"
        r = client.post(f"/admin/skill-submissions/{run_id}/approve", headers=_ADMIN)
        assert r.status_code == 200 and r.json()["status"] == "approved"
        assert skills._by_slug["refund-approvals"].enabled is True
        # double decision → 409
        assert client.post(f"/admin/skill-submissions/{run_id}/reject",
                           json={"note": "late"}, headers=_ADMIN).status_code == 409


def test_admin_reject_records_note_visible_to_sme(harness) -> None:
    with TestClient(app) as client:
        run_id = client.post("/studio/submit", json={"skill": _DRAFT},
                             headers=_SME).json()["run_id"]
        r = client.post(f"/admin/skill-submissions/{run_id}/reject",
                        json={"note": "Limit is $250."}, headers=_ADMIN)
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        mine = client.get("/studio/submissions", headers=_SME).json()
        assert mine[0]["rejection_note"] == "Limit is $250."


def test_unknown_run_is_404(harness) -> None:
    with TestClient(app) as client:
        assert client.post("/admin/skill-submissions/RB-0/approve",
                           headers=_ADMIN).status_code == 404
```

- [ ] **Step 2: Run — FAIL** (import errors): `uv run pytest tests/test_studio_api.py -q`
- [ ] **Step 3: Add providers to `app/deps.py`** (after `get_approval_flow`):

```python
def get_skill_drafter(request: Request):
    return getattr(request.app.state, "skill_drafter", None)


def get_skill_publish_flow(request: Request):
    return getattr(request.app.state, "skill_publish_flow", None)
```

- [ ] **Step 4: Create `app/api/studio.py`**

```python
"""SME Skill Studio: plain-English drafting, submission, and admin decisions.

/studio/*                      — require_sme (Entra ENTRA_SME_GROUP; admins pass)
/admin/skill-submissions/*     — require_admin

A submission is a skill_publish run; the live SkillStore is only written by an
approval (see app/workflows/skill_publish.py and the 2026-06-07 design spec).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.api._admin_guard import require_admin
from app.api._auth_resolve import resolve_user
from app.api._sme_guard import require_sme
from app.deps import get_run_store, get_skill_drafter, get_skill_publish_flow
from app.domain.identity import User
from app.domain.skill import SkillCreate
from app.domain.workflow import RefundRun
from app.skills.drafter import SkillDraftError
from app.skills.store import SkillStorePersistenceError
from app.workflows.skill_publish import AlreadyDecidedError, SlugConflictError

router = APIRouter(prefix="/studio", tags=["studio"])
admin_router = APIRouter(prefix="/admin", tags=["admin"],
                         dependencies=[Depends(require_admin)])


class DraftRequest(BaseModel):
    text: str


class SubmitRequest(BaseModel):
    skill: SkillCreate
    source_text: str = ""


class RejectRequest(BaseModel):
    note: str = ""


class SubmissionSummary(BaseModel):
    run_id: str
    name: str
    slug: str
    status: str
    rejection_note: str | None = None
    submitted_by: str
    created_at: datetime
    source_text: str | None = None
    skill: SkillCreate | None = None  # full draft — admin queue only


def _summary(r: RefundRun, *, include_draft: bool) -> SubmissionSummary:
    d = r.skill_draft
    return SubmissionSummary(
        run_id=r.id, name=d.name, slug=d.slug, status=r.status,
        rejection_note=r.rejection_note, submitted_by=r.requester_name,
        created_at=r.created_at,
        source_text=r.request_text if include_draft else None,
        skill=d if include_draft else None)


# ── SME endpoints ─────────────────────────────────────────────────────────────

@router.post("/draft")
async def draft_skill(body: DraftRequest, user: User = Depends(require_sme),
                      drafter=Depends(get_skill_drafter)) -> SkillCreate:
    if drafter is None:
        raise HTTPException(status_code=503, detail="drafter unavailable")
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="describe the skill first")
    try:
        return await drafter.draft(body.text)
    except SkillDraftError as e:
        raise HTTPException(status_code=502,
                            detail=f"couldn't draft a skill: {e}") from e


@router.post("/submit", status_code=201)
async def submit_skill(body: SubmitRequest, user: User = Depends(require_sme),
                       flow=Depends(get_skill_publish_flow)) -> dict:
    if flow is None:
        raise HTTPException(status_code=503, detail="studio unavailable")
    try:
        run = await flow.submit(draft=body.skill, source_text=body.source_text, user=user)
    except SlugConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SkillStorePersistenceError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"run_id": run.id, "status": run.status}


@router.get("/submissions")
async def my_submissions(user: User = Depends(require_sme),
                         store=Depends(get_run_store)) -> list[SubmissionSummary]:
    if store is None:
        return []
    return [_summary(r, include_draft=False)
            for r in await store.list_runs(limit=100)
            if (r.kind == "skill_publish" and r.skill_draft is not None
                and r.requester_email == user.email)]


# ── Admin endpoints ───────────────────────────────────────────────────────────

@admin_router.get("/skill-submissions")
async def list_submissions(store=Depends(get_run_store)) -> list[SubmissionSummary]:
    if store is None:
        return []
    return [_summary(r, include_draft=True)
            for r in await store.list_runs(limit=100)
            if r.kind == "skill_publish" and r.skill_draft is not None]


async def _actor_name(authorization, x_debug_bypass_auth, x_ms_client_principal) -> str:
    """Best-effort decision-maker name for the audit trail. The x-admin-key
    path has no signed-in user — record the decision as 'Admin'."""
    try:
        user = await resolve_user(
            easy_auth=x_ms_client_principal, authorization=authorization,
            debug_header=x_debug_bypass_auth)
        return user.display_name
    except HTTPException:
        return "Admin"


async def _decide(run_id: str, *, approve: bool, note: str | None, flow,
                  actor: str) -> SubmissionSummary:
    if flow is None:
        raise HTTPException(status_code=503, detail="studio unavailable")
    try:
        run = await flow.decide(run_id=run_id, approve=approve,
                                actor_name=actor, note=note)
    except KeyError:
        raise HTTPException(status_code=404, detail="submission not found") from None
    except AlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=f"already {e}") from e
    except SlugConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:  # slug landed in the catalog while this was pending
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SkillStorePersistenceError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return _summary(run, include_draft=True)


@admin_router.post("/skill-submissions/{run_id}/approve")
async def approve_submission(
    run_id: str, flow=Depends(get_skill_publish_flow),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> SubmissionSummary:
    actor = await _actor_name(authorization, x_debug_bypass_auth, x_ms_client_principal)
    return await _decide(run_id, approve=True, note=None, flow=flow, actor=actor)


@admin_router.post("/skill-submissions/{run_id}/reject")
async def reject_submission(
    run_id: str, body: RejectRequest, flow=Depends(get_skill_publish_flow),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> SubmissionSummary:
    actor = await _actor_name(authorization, x_debug_bypass_auth, x_ms_client_principal)
    return await _decide(run_id, approve=False, note=body.note, flow=flow, actor=actor)
```

- [ ] **Step 5: Wire `app/main.py`** — three edits:
  1. Imports (alphabetical, next to the other `app.api` imports):

```python
from app.api.studio import admin_router as studio_admin_router
from app.api.studio import router as studio_router
```

  and next to the other workflow/skills imports:

```python
from app.skills.drafter import SkillDrafter
from app.workflows.skill_publish import SkillPublishFlow
```

  2. State, in the lifespan directly after the `app.state.approval_flow = ApprovalFlow(...)` block:

```python
    app.state.skill_drafter = SkillDrafter(llm=app.state.llm)
    app.state.skill_publish_flow = SkillPublishFlow(
        store=app.state.run_store, skill_store=app.state.skill_store,
        people=app.state.people_graph)
```

  3. Routers, next to `app.include_router(skills_admin_router)`:

```python
app.include_router(studio_router)
app.include_router(studio_admin_router)
```

- [ ] **Step 6: Run — PASS**: `uv run pytest tests/test_studio_api.py tests/test_lifespan_clients.py -q`
- [ ] **Step 7: Commit** — `git commit -am "feat(api): /studio drafting+submission and /admin/skill-submissions decisions"`

---

### Task 9: Slack dispatch for `skillpub_` actions

**Files:**
- Modify: `substrateos-api/app/api/bots.py`
- Test: `substrateos-api/tests/test_slack_interactive.py` (append)

- [ ] **Step 1: Append the failing test** (reuses the file's existing `_env`, `_FakeFlow`, `_payload`, `_post` helpers):

```python
def test_interactive_dispatches_skillpub_action(monkeypatch):
    _env(monkeypatch)
    from app.deps import get_skill_publish_flow
    flow = _FakeFlow()
    app.dependency_overrides[get_skill_publish_flow] = lambda: flow
    try:
        with TestClient(app) as client:
            r = _post(client, _payload(action_id="skillpub_approve", run_id="RB-7001"))
        assert r.status_code == 200
        assert len(flow.payloads) == 1
        assert flow.payloads[0]["actions"][0]["value"] == "RB-7001"
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run — FAIL** (the payload falls through to the refund branch / no dispatch): `uv run pytest tests/test_slack_interactive.py -q`
- [ ] **Step 3: Implement** — in `app/api/bots.py`:
  - add `get_skill_publish_flow` to the existing `from app.deps import …` list
  - add the parameter to `slack_interactive` next to the other flows: `skill_publish_flow=Depends(get_skill_publish_flow),`
  - in the dispatch chain, insert **before** the final `elif refund_flow is not None` branch:

```python
    elif action_id.startswith("skillpub_") and skill_publish_flow is not None:
        background_tasks.add_task(skill_publish_flow.handle_action, payload)
```

- [ ] **Step 4: Run — PASS**: `uv run pytest tests/test_slack_interactive.py -q`
- [ ] **Step 5: Backend regression sweep + commit**

```bash
uv run pytest tests/ -q   # full backend suite — must be green
git commit -am "feat(slack): dispatch skillpub_ card actions to SkillPublishFlow"
```

---

### Task 10: Frontend — Studio page (⛔ requires Task 1 mockup approval)

**Files:**
- Modify: `web/lib/api.ts` (Me type)
- Create: `web/lib/studioApi.ts`
- Create: `web/app/studio/page.tsx`

- [ ] **Step 1: `web/lib/api.ts`** — extend the Me type (line ~38):

```ts
// is_admin = member of the Entra admins group; gates /admin (backend re-checks).
// is_sme   = member of the Entra SME group; gates /studio (backend re-checks).
export type Me = { display_name: string; email: string; title: string | null; is_admin: boolean; is_sme: boolean };
```

- [ ] **Step 2: Create `web/lib/studioApi.ts`** (same auth-header pattern as `skillsApi.ts`):

```ts
import type { SkillCreate } from "./skillsApi";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export type Submission = {
  run_id: string; name: string; slug: string; status: string;
  rejection_note: string | null; submitted_by: string; created_at: string;
  source_text?: string | null; skill?: SkillCreate | null;
};

async function easyAuthToken(): Promise<string | null> {
  return fetch("/.auth/me", { credentials: "include" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (Array.isArray(d) && d[0]?.id_token) || null)
    .catch(() => null);
}

async function headers(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (DEBUG_AUTH) h["x-debug-bypass-auth"] = DEBUG_AUTH;
  else { const t = await easyAuthToken(); if (t) h["Authorization"] = `Bearer ${t}`; }
  return h;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`,
    { ...init, headers: { ...(init.headers ?? {}), ...(await headers()) } });
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* keep status */ }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const draftSkill = (text: string) =>
  call<SkillCreate>("/studio/draft", { method: "POST", body: JSON.stringify({ text }) });

export const submitSkill = (skill: SkillCreate, source_text: string) =>
  call<{ run_id: string; status: string }>("/studio/submit",
    { method: "POST", body: JSON.stringify({ skill, source_text }) });

export const getMySubmissions = () => call<Submission[]>("/studio/submissions");

// Admin queue (require_admin server-side)
export const adminListSubmissions = () => call<Submission[]>("/admin/skill-submissions");
export const adminDecideSubmission = (runId: string, approve: boolean, note = "") =>
  call<Submission>(`/admin/skill-submissions/${encodeURIComponent(runId)}/${approve ? "approve" : "reject"}`,
    { method: "POST", body: approve ? undefined : JSON.stringify({ note }) });
```

- [ ] **Step 3: Create `web/app/studio/page.tsx`** — implement to match the **approved** mockup exactly; the code below is the functional baseline (states, calls, gating) and the executor styles it per the mockup using the existing globals.css classes:

```tsx
"use client";
import { useEffect, useState } from "react";
import { getMe, Me } from "@/lib/api";
import type { SkillCreate } from "@/lib/skillsApi";
import { draftSkill, getMySubmissions, submitSkill, Submission } from "@/lib/studioApi";

const STATUS_LABEL: Record<string, string> = {
  pending_approval: "Pending approval", approved: "Live", rejected: "Rejected",
};

export default function StudioPage() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = loading
  const [text, setText] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draft, setDraft] = useState<SkillCreate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [subs, setSubs] = useState<Submission[]>([]);

  useEffect(() => { getMe().then(setMe); }, []);
  const loadSubs = () => getMySubmissions().then(setSubs).catch(() => {});
  useEffect(() => { if (me && (me.is_sme || me.is_admin)) loadSubs(); }, [me]);

  if (me === undefined) return null; // loading
  if (!me || (!me.is_sme && !me.is_admin)) {
    return (
      <div className="studio-denied">
        <h1>Skill Studio</h1>
        <p>Access restricted — the Studio needs the Entra &quot;Finance SME&quot; group.</p>
      </div>
    );
  }

  const handleDraft = async () => {
    setDrafting(true); setError(null);
    try { setDraft(await draftSkill(text)); }
    catch (e) {
      setError(`${(e as Error).message} — you can still fill the form by hand.`);
      setDraft({ slug: "", name: "", description: "", team: "Finance",
                 run_scope: "org", enabled: true, steps: [], data_feeds: [], system_prompt: "" });
    }
    finally { setDrafting(false); }
  };

  const handleSubmit = async () => {
    if (!draft) return;
    setSubmitting(true); setError(null);
    try {
      await submitSkill(draft, text);
      setDraft(null); setText("");
      loadSubs();
    } catch (e) { setError((e as Error).message); }
    finally { setSubmitting(false); }
  };

  const set = (k: keyof SkillCreate, v: unknown) =>
    setDraft((p) => (p ? { ...p, [k]: v } : p));

  return (
    <div className="studio-page">{/* style per approved mockup */}
      <header>
        <h1>Skill Studio</h1>
        <p>Turn what you know into a governed skill — no code.</p>
        <span>{me.display_name}{me.title ? ` · ${me.title}` : ""}</span>
      </header>

      <section>{/* Card 1 — Describe it */}
        <h2>Describe it</h2>
        <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)}
          placeholder="e.g. Refunds under $500 and 30 days are auto-approved. Anything bigger needs my sign-off…" />
        <button onClick={handleDraft} disabled={drafting || !text.trim()}>
          {drafting ? "Drafting…" : "Draft with AI"}
        </button>
        {error && <p role="alert">{error}</p>}
      </section>

      {draft && (
        <section>{/* Card 2 — Review the draft (every field editable) */}
          <h2>Review the draft</h2>
          <input value={draft.name} onChange={(e) => set("name", e.target.value)} placeholder="Name" />
          <input value={draft.slug} onChange={(e) => set("slug", e.target.value.toLowerCase().replace(/[^a-z0-9-]+/g, "-"))} placeholder="slug" />
          <textarea rows={2} value={draft.description} onChange={(e) => set("description", e.target.value)} placeholder="Description" />
          <input value={draft.team} onChange={(e) => set("team", e.target.value)} placeholder="Team" />
          {(draft.steps ?? []).map((s, i) => (
            <input key={i} value={s} onChange={(e) => {
              const steps = [...(draft.steps ?? [])]; steps[i] = e.target.value; set("steps", steps);
            }} />
          ))}
          <button type="button" onClick={() => set("steps", [...(draft.steps ?? []), ""])}>+ Add step</button>
          {(draft.data_feeds ?? []).map((d, i) => (
            <input key={i} value={d} onChange={(e) => {
              const feeds = [...(draft.data_feeds ?? [])]; feeds[i] = e.target.value; set("data_feeds", feeds);
            }} />
          ))}
          <textarea rows={6} value={draft.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} placeholder="System prompt" />
          <footer>
            <button type="button" onClick={() => { setDraft(null); setError(null); }}>Start over</button>
            <button onClick={handleSubmit} disabled={submitting || !draft.name || !draft.slug}>
              {submitting ? "Submitting…" : "Submit for approval"}
            </button>
            <p>🔒 An admin reviews before this goes live.</p>
          </footer>
        </section>
      )}

      <section>{/* Card 3 — My submissions */}
        <h2>My submissions</h2>
        {subs.length === 0 ? <p>Nothing submitted yet.</p> : (
          <table>
            <thead><tr><th>Skill</th><th>Status</th><th>Submitted</th></tr></thead>
            <tbody>
              {subs.map((s) => (
                <tr key={s.run_id}>
                  <td>{s.name} <code>/{s.slug}</code></td>
                  <td>
                    <span className={`studio-status ${s.status}`}>{STATUS_LABEL[s.status] ?? s.status}</span>
                    {s.status === "rejected" && s.rejection_note && <div>{s.rejection_note}</div>}
                  </td>
                  <td>{new Date(s.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
```

  Add any new CSS classes (`studio-page`, `studio-status`, …) to `web/app/globals.css` mirroring the mockup's styles.
- [ ] **Step 4: Verify** — `cd web && pnpm typecheck && pnpm lint` → both clean.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(web): /studio — SME authoring with AI-drafted skill form"`

> Local-dev note (do not commit): to act as an SME locally, set `NEXT_PUBLIC_DEBUG_AUTH="t-eval,u-deepa,t-eval:everyone,Finance SME"` in `web/.env.local`; the debug header carries group names directly.

---

### Task 11: Frontend — admin pending queue (⛔ requires Task 1 mockup approval)

**Files:**
- Modify: `web/app/admin/skills/page.tsx`

- [ ] **Step 1: Implement** — in `AdminSkillsPage`:
  - imports: `import { adminListSubmissions, adminDecideSubmission, Submission } from "@/lib/studioApi";`
  - state + load alongside the existing skills load:

```tsx
  const [pending, setPending] = useState<Submission[]>([]);
  const [rejecting, setRejecting] = useState<{ runId: string; note: string } | null>(null);
  const loadPending = () =>
    adminListSubmissions()
      .then((all) => setPending(all.filter((s) => s.status === "pending_approval")))
      .catch(() => {});
  useEffect(() => { load(); loadPending(); }, []);   // replaces the existing useEffect

  const handleDecide = async (runId: string, approve: boolean, note = "") => {
    try {
      await adminDecideSubmission(runId, approve, note);
      setPending((p) => p.filter((s) => s.run_id !== runId));
      if (approve) load(); // the approved skill is now live in the table below
    } catch (e) { alert((e as Error).message); }
    finally { setRejecting(null); }
  };
```

  - render, between the `<header>` and the skills table (style per the approved mockup; reuse `admin-note`/card classes):

```tsx
        {pending.length > 0 && (
          <section className="pending-queue">
            <h2>Pending approval <span className="pending-count">{pending.length}</span></h2>
            {pending.map((s) => (
              <div key={s.run_id} className="pending-card">
                <div className="pending-head">
                  <span className="skill-row-name">{s.name}</span>
                  <span className="skill-row-slug">/{s.slug}</span>
                  <span className="pending-by">by {s.submitted_by}</span>
                </div>
                {s.source_text && <blockquote className="pending-source">{s.source_text}</blockquote>}
                {s.skill && (
                  <>
                    <p>{s.skill.description}</p>
                    <ol>{(s.skill.steps ?? []).map((st, i) => <li key={i}>{st}</li>)}</ol>
                    <details><summary>System prompt</summary><pre>{s.skill.system_prompt}</pre></details>
                  </>
                )}
                {rejecting?.runId === s.run_id ? (
                  <div className="pending-reject-row">
                    <input autoFocus value={rejecting.note} placeholder="Why? The SME sees this note."
                      onChange={(e) => setRejecting({ runId: s.run_id, note: e.target.value })} />
                    <button className="skill-action-btn del" onClick={() => handleDecide(s.run_id, false, rejecting.note)}>Reject</button>
                    <button className="skill-action-btn" onClick={() => setRejecting(null)}>Cancel</button>
                  </div>
                ) : (
                  <div className="skill-row-actions">
                    <button className="skill-btn-primary" onClick={() => handleDecide(s.run_id, true)}>Approve</button>
                    <button className="skill-action-btn del" onClick={() => setRejecting({ runId: s.run_id, note: "" })}>Reject…</button>
                  </div>
                )}
              </div>
            ))}
          </section>
        )}
```

  Add the `pending-*` CSS classes to `web/app/globals.css` per the approved mockup.
- [ ] **Step 2: Verify** — `cd web && pnpm typecheck && pnpm lint && pnpm build` → clean build.
- [ ] **Step 3: Commit** — `git add -A && git commit -m "feat(admin): pending-approval queue for SME skill submissions"`

---

### Task 12: Full verification

- [ ] **Step 1: Full backend suite** — `cd substrateos-api && uv run pytest tests/ -q` → all green. Report real output.
- [ ] **Step 2: Frontend** — `cd web && pnpm typecheck && pnpm lint && pnpm build` → clean.
- [ ] **Step 3: Smoke the wiring end-to-end locally** (debug auth):

```bash
cd substrateos-api && uv run uvicorn app.main:app --port 8000 &
sleep 3
# SME drafts (LLM may 502 without a key — that's the graceful path), submits, admin approves:
curl -s -X POST localhost:8000/studio/submit \
  -H 'content-type: application/json' \
  -H 'x-debug-bypass-auth: t-eval,u-deepa,t-eval:everyone,Finance SME' \
  -d '{"skill":{"slug":"smoke-skill","name":"Smoke","description":"d","team":"Finance","system_prompt":"p"},"source_text":"smoke"}'
curl -s localhost:8000/admin/skill-submissions \
  -H 'x-debug-bypass-auth: t-eval,u-diana,t-eval:everyone,Admin'
kill %1
```

  Expected: submit returns `{"run_id":"RB-…","status":"pending_approval"}` (or 503 if local Redis writes are required — note whichever happens truthfully). Check `/admin/runs` page shows the run with kind `skill_publish`; if the runs page renders the kind label oddly, add the label mapping there (small cosmetic fix, flag it in the report).

---

### Task 13: Sync the design + docs

- [ ] **Step 1: Mockups ↔ frontend** — make `mockups/sme-studio.html` and `mockups/admin-portal.html` reflect exactly what shipped; flag any pre-existing drift found in the Org Skills mockup.
- [ ] **Step 2: `mockups/architecture.html`** — update BOTH views (Master Deck palette: navy `#102444` + amber `#c8860d`):
  - **Detailed view:** add the Studio surface (`/studio` route), `_sme_guard` (Entra "Finance SME", fail-closed), `SkillDrafter` (one LLM call), `SkillPublishFlow` + `skill_publish` runs in the run store, the Slack manager card, and the approval-writes-catalog edge into `SkillStore`.
  - **High-level view:** add "SMEs author skills in plain English → admin-approved → live" under the playbook/governance story.
  - Keep the Intelligence Design + Engineering Quality pillars, Surfaces, When→Check→Stop→Do→Record, governance, and vision sections accurate. `open mockups/architecture.html` and eyeball it.
- [ ] **Step 3: `references/techstack.md`** — no new libraries were introduced; verify and explicitly note "no change" in the task report.
- [ ] **Step 4: Commit** — `git add -A && git commit -m "docs: architecture + mockups reflect the SME Skill Studio"`

---

## Definition of done

Mockups approved before any React; all tasks committed on `feat/sme-skill-studio`; full backend suite + web typecheck/lint/build green; architecture/mockups synced. Merge to `main`, worktree cleanup, and deploy (via `substrateos-deploy`) happen only with the user's explicit approval, per the substrateos-feature workflow Phase 5.
