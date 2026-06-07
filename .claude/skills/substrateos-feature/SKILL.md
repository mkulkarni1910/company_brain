---
name: substrateos-feature
description: >-
  The end-to-end workflow for building ANY feature, change, or fix on the
  SubstrateOS / Company Brain project (the backend `substrateos-api/`, the User
  Web Chat at `web/app/`, or the Admin Panel at `web/app/admin/`). Use this
  WHENEVER the user asks to add, build, implement, change, extend, or wire up
  anything in this repo — e.g. "add a feedback button to the chat", "new admin
  screen for X", "expose a /something endpoint", "let users filter sources",
  "build the Teams connector UI". It enforces the project's rules: mockup-first
  for any frontend change (update the mockup and show a browser preview for
  approval BEFORE writing React), every feature in its own git worktree (never
  edit directly on main), parallel subagents for independent tasks, keep mockups
  + `mockups/architecture.html` + the tech-stack tracker in sync, write and run
  tests, then merge to main, clean up the worktree, and deploy via the
  substrateos-deploy skill only after explicit approval. Invoke
  this even if the user doesn't name it — almost any build/change request on
  this repo should start here.
---

# SubstrateOS — Feature Workflow

This project competes in the Microsoft Build AI challenge under the theme
**"Productivity & Teamwork Reimagined."** Every feature should be judged against
that lens and against the two qualities the architecture explicitly prioritizes:

1. **Intelligence Design** — how well the feature uses retrieval, ranking,
   grounding, personalization, and orchestration to do something genuinely smart.
2. **System Architecture & Engineering Quality** — clean module boundaries,
   real tests, security (double-enforcement ACLs), observability, and deployability.

Keep these in mind while you build; they are also the headline sections of the
architecture doc you'll keep updated.

## The north star (read `references/north-star.md`)

SubstrateOS is *The Company Brain*: **"Building AI agents is becoming easy.
Trusting them with real work is the hard part."** We turn the know-how in people's
heads into **playbooks the AI runs** — fast to build, and safe. The product's edge
is climbing past *Find → Plan* to **Do → Stay in control**, and every playbook
follows one shape: **When → Check → Stop (if risky, a human approves) → Do it →
Record.** When you build a feature, ask: does it make the brain *act* on real work,
and does it keep that *safe* (known identity, human approval, full audit, stops if
unsure)? `references/north-star.md` has the full pitch — skim it for any
non-trivial feature so what you ship advances the actual product, not a tangent.

**Surfaces.** The engine is reachable from five surfaces, each carrying identity:
**Slack · Teams · Web app · Other AI tools (MCP) · API (PAT/context).** A new
surface plugs into the same engine + identity layer (`app/bots/`, `app/mcp/`,
`app/api/context`, `app/auth.py`) — never fork the playbook logic per surface.

## The three components

| Component | Lives in | Stack | Mockup |
|-----------|----------|-------|--------|
| **Backend (brain-api)** | `substrateos-api/` | FastAPI · Python 3.12 · uv · pytest | — |
| **User Web Chat** | `web/app/` (root routes) | Next.js 14 · React 18 · Tailwind · MSAL | `mockups/user-web-chat.html` |
| **Admin Panel** | `web/app/admin/` | same Next.js app | `mockups/admin-portal.html` |

Read `references/techstack.md` before reaching for a new library — we reuse what's
already here. Add to that file when you genuinely introduce something new.

## The workflow

Work through these phases in order. Skip a phase only when it clearly doesn't
apply (e.g. a backend-only change skips the mockup phase), and say so out loud.
If the change is non-trivial, **brainstorm the design with the user first** — this
workflow is about *how* to ship safely, not a license to skip thinking.

### Where the work happens — a worktree per feature, never on main

**Never edit files directly on `main` or in the primary checkout.** Every
feature gets its own git worktree + branch so multiple features can proceed in
parallel without stepping on each other:

```bash
git worktree add .worktrees/<feature> -b feat/<feature>
```

Do ALL phases below inside that worktree (use the native worktree tooling /
superpowers:using-git-worktrees when available). The worktree is merged back to
`main` and removed in Phase 5 — it never outlives the feature.

### Parallelize independent tasks

When the request decomposes into **2+ independent tasks** (no shared files, no
sequential dependency), don't do them one after another — dispatch **one
subagent per task, each in its own worktree**, and run them concurrently
(superpowers:dispatching-parallel-agents / superpowers:subagent-driven-development).
Tasks that share files or depend on each other's output stay sequential.
Each parallel track still follows every phase below; merges to `main` in
Phase 5 happen one at a time, re-running tests after each merge.

### Phase 0 — Orient

- Decide which component(s) the feature touches. Most user-facing features touch
  both the backend (an endpoint) and a frontend (the surface that calls it).
- Skim the matching design spec/plan in `docs/superpowers/specs/` and
  `docs/superpowers/plans/` — this project has a spec for nearly every feature,
  and they encode decisions you shouldn't silently contradict.
- Check `references/techstack.md` so you build with the stack we already run.

### Phase 1 — Mockup first (frontend changes only)

This is the project's firmest rule, because design review is cheap in HTML and
expensive in React. **For any change to the User Web Chat or Admin Panel, do NOT
write a single line of `.tsx` until the mockup is approved.**

1. Update (or add) the relevant mockup in `mockups/` — `user-web-chat.html` or
   `admin-portal.html`. Match the existing design system (Fraunces/Archivo/
   JetBrains Mono, the warm-paper palette, the CSS variables already defined in
   those files). Reuse the existing components and tokens rather than inventing
   new visual language.
2. Open it in the browser for review:
   ```bash
   open mockups/user-web-chat.html   # or admin-portal.html
   ```
3. Present the change to the user and **wait for explicit approval.** Iterate on
   the mockup until they're happy. Only then proceed to Phase 2.

Backend-only work skips this phase entirely — note that you're skipping it.

**Renames / brand / copy changes count as frontend changes too.** A find/replace
is not exempt from the preview gate. Two traps that a browser preview catches and
a grep does not: (1) brand wordmarks are usually **split across tags** — e.g.
`Substrate<span>OS</span>` or `Sub<span>strate</span>OS` — so a replace of the
contiguous string silently misses them; grep the split form too. (2) the visual
result (casing, color emphasis, line wrap) only shows in the rendered page. After
any text/brand change, `open` the affected mockup(s) and the live UI and eyeball
them before calling it done.

### Phase 2 — Implement

- **Backend:** add code under `substrateos-api/app/`, respecting the existing
  module boundaries (one module per concern: `retrieval/`, `ranking/`,
  `generation/`, `acl/`, `orchestrator/`, `api/`, …). Wire any new router in
  `app/main.py`. Every Azure call goes through the existing client wrappers and
  `DefaultAzureCredential` — never new connection strings in code. Preserve the
  double-enforcement ACL story: anything that returns content must be ACL-filtered.
- **Frontend:** implement the React to match the **approved** mockup as closely
  as the framework allows. Reuse components in `web/components/` and helpers in
  `web/lib/`. Call the backend through the existing client/auth layer.
- Use LSP (`findReferences` before changing a signature, diagnostics after edits)
  per the global code-intelligence guidance.

### Phase 3 — Tests (never skip)

Nothing ships untested. Add tests alongside the change, then run them and report
real output — if something fails, say so.

**Ask the user which scope to run during development** (saves time on
iteration; default to **targeted** if they don't say):

- **Targeted (fast inner loop):** only the new/affected tests —
  ```bash
  cd substrateos-api && uv run pytest tests/test_<feature>.py -q
  ```
- **Full suite:** everything —
  ```bash
  cd substrateos-api && uv run pytest tests/ -q
  ```

Targeted runs are a development convenience, not a substitute: **the full suite
must pass before the merge in Phase 5** — if only targeted tests have run so
far, run the full suite then.

- **Backend:** add a `tests/test_<feature>.py`; follow the patterns in the
  existing suite (fakes/stubs for Azure clients, `respx` for HTTP,
  `pytest-asyncio`).
- **Frontend:**
  ```bash
  cd web && pnpm typecheck && pnpm lint && pnpm build
  ```
  Add component/behaviour tests where the project has a harness for them; at
  minimum the typecheck + build must pass.

### Phase 4 — Sync the design + docs (this is what keeps the project coherent)

After the code works, bring the living docs back in line. This is not optional
busywork — the mockups and architecture doc are how reviewers understand the
system, so drift makes the whole project look unmaintained.

1. **Mockups ↔ frontend:** make the mockup reflect exactly what shipped. If you
   discover the *existing* frontend already drifted from its mockup while you were
   in there, **flag the inconsistency to the user** and offer to reconcile it.
2. **Architecture (`mockups/architecture.html`):** update BOTH views to include
   the change:
   - **Detailed architecture** — discloses everything: components down to the
     smallest module, data flow, every dependency, security and observability.
   - **High-level architecture** — a quick one-screen overview.
   Make sure the **Intelligence Design** and **System Architecture & Engineering
   Quality** pillars stay highlighted and accurate, and that the **Surfaces**, the
   **playbook engine** (When→Check→Stop→Do→Record), **governance**, and **vision**
   sections still reflect reality. `architecture.html` uses the **Master Deck
   palette** (deep navy `#102444` + amber `#c8860d`, amber left spine) — keep new
   elements in that palette so the doc stays pitch-consistent. Open it to eyeball it:
   ```bash
   open mockups/architecture.html
   ```
3. **Tech stack:** if you introduced anything new, add it to
   `references/techstack.md` with a one-line "why / where used" so it's reusable.

### Phase 5 — Merge & Deploy (only with approval)

1. Confirm the **full test suite** passes (not just the targeted subset from
   Phase 3) and the worktree is clean.
2. Merge the feature branch to `main` — confirm with the user before merging.
   When several parallel tracks finish together, merge them one at a time and
   re-run the full suite after each merge.
3. **Delete the worktree and merged branch** — worktrees never outlive their
   feature:
   ```bash
   git worktree remove .worktrees/<feature> && git branch -d feat/<feature>
   ```
4. **Get explicit approval to deploy.** Production (`centralindia`) is shared and
   live; never deploy on your own initiative.
5. On approval, deploy with the **`substrateos-deploy`** skill (it handles
   build → ACR push → Container Apps rollout → health check, and refuses to
   deploy from anything but `main`).

## Definition of done

A feature is done when: it was built in its own git worktree (never directly on
`main`), the mockup (if any) was approved before implementation, the code matches
it, tests are written and the **full suite** is green, the mockups +
`architecture.html` + tech-stack tracker are updated, it's merged to `main`, the
worktree and branch are deleted, and — if the user approved — deployed via
`substrateos-deploy` and health-checked.
