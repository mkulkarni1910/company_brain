# GitHub Tool — Raise AI-Drafted PRs from Chat — Design

**Date:** 2026-06-06
**Status:** Approved

## Goal

Add **GitHub** as the first *action connector* ("Tool") in SubstrateOS: a user on
any chat surface — **Web, Slack, or Teams** — asks the brain to make a change
("raise a PR to update the refund policy doc to the new 30-day window"), the
brain drafts the file edit against an admin-configured repo, shows the requester
a preview with **Create PR / Cancel** buttons, and on confirm creates the branch,
commit, and pull request **authored as the requesting user** via their own
GitHub login. Every step lands in the existing Runs audit trail.

This is a pure climb up the ladder: **Find → Plan → Do → Stay in control.** The
PR is the *Do*; the requester preview is the *Stop*; GitHub's own review is the
second gate; RunStore is the *Record*.

## Conceptual framing — Tool, not Surface

In the north star, **surfaces** are where requests *originate*, each carrying
identity (Slack · Teams · Web · MCP · API). GitHub here is the opposite
direction: a **tool the brain acts in** (the "act in your tools" arm of the
engine). Decision: the card lives on the existing **Surfaces** admin screen,
placed right after Teams, but tagged **`Tool`** (a new tag alongside
Individual/Team/Platform) and described as an action connector — "where
SubstrateOS acts." The architecture doc gets the same distinction.

## Decisions made

| Question | Decision |
|---|---|
| Admin placement | Card on the existing Surfaces screen, after Teams, tagged `Tool` |
| What "raise a PR" does | AI-drafted change: brain generates the file edit(s), creates branch + commit + PR. Scoped to text/doc-style edits the LLM can draft reliably |
| Stop step | **Requester previews & confirms** in chat (files touched, diff summary, PR title, Create PR / Cancel buttons). No manager approval — GitHub review is the second gate |
| GitHub auth | **Per-user OAuth**: each user connects their GitHub account once; PRs are authored as them. The OAuth **App's** `client_id`/`client_secret` are app-level credentials set by the admin |
| App credential storage | `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` env vars + Key Vault overlay in prod — same pattern as Slack/Teams creds. Requires API restart on first setup; setup modal explains the app-credential vs user-token distinction |
| Repo target | Admin-editable in the setup modal: `owner/repo` + base branch, stored in ConnectionStore (Redis) via a new `/admin/github/config` endpoint — no redeploy to change repos |
| Surfaces in v1 | **All three**: Web, Slack, Teams. One flow engine, three renderers — never fork the logic |
| User token storage | Redis `github:token:{email}` — same posture as other Redis-held state; classic-OAuth `repo` scope; GitHub App upgrade noted as future work |

## Demo script (target behaviour)

1. Admin → **Surfaces**: GitHub card shows *needs setup*. Opens modal: ① create
   GitHub OAuth App (callback `{API}/auth/github/callback`), ② set
   `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`, restart API, ③ enter target repo
   `acme/policies` + base `main`. Card flips to **Connected to acme/policies**.
2. **Tom** in Slack: `@SubstrateOS raise a PR updating the refund policy to a
   30-day window`.
3. First time only: bot replies with a personal **Connect GitHub** link → browser
   → GitHub authorize → "Connected — return to chat and ask again."
4. Bot acks, fetches the actual policy file from the repo, LLM drafts the edit,
   and posts a **preview card**: file touched, change summary, PR title, with
   **Create PR / Cancel** buttons.
5. Tom clicks **Create PR** → branch + commit + PR created *as Tom* → card
   updates with the PR link. The PR on GitHub shows Tom as the author.
6. Admin → **Runs**: run `#RB-xxxx`, kind `github_pr`, full audit trail
   (request → draft → preview → confirmed by Tom → PR URL).
7. Same flow works from the web chat (inline preview card with buttons) and
   Teams (Adaptive Card with Action.Submit buttons).

Cancel path: Tom clicks **Cancel** → run `cancelled`, nothing touches GitHub.

## Components

### 1. Admin — Surfaces screen (web + mockup)

- New `SurfaceMeta` entry after Teams: `name: "github"`, `tag: "Tool"`,
  GitHub mark icon, desc framing it as an action connector, `installable: true`,
  `blockedMsg: "GitHub tool disabled — raise-PR requests are refused."`
- **Setup modal** (pattern of the Slack/Teams modals): the three steps above,
  plus a repo/base-branch form that saves through `/admin/github/config`. Copy
  explicitly distinguishes the app credential (admin, once) from user tokens
  (each user, via Connect).
- Card state: `needs-setup` until client creds present **and** repo configured;
  then "Connected to `owner/repo`". Existing toggle/filters work unchanged once
  the card joins the `SURFACES` list (auto-heal pattern reused).

### 2. Backend — config & OAuth

- `Settings`: `github_client_id`, `github_client_secret` (env; Key Vault overlay
  `github-client-id` / `github-client-secret` in `load_secrets_from_keyvault`).
- `"github"` added to `_VALID_SURFACES` (app/api/admin.py) and
  `_DEFAULT_SURFACES` (app/connectors/store.py).
- ConnectionStore: `github` config blob `{owner, repo, base_branch}`; admin
  endpoints `GET/PUT /admin/github/config`; `GET /admin/bot/status` response
  gains a `github` entry (configured = creds present + repo set) so the card
  reuses the existing status fetch.
- **OAuth endpoints:** `GET /auth/github/start` — requires user identity, mints
  a short-TTL state token in Redis bound to user + originating surface,
  redirects to GitHub authorize (`repo` scope). `GET /auth/github/callback` —
  validates state, exchanges code, stores token at `github:token:{email}`,
  renders a "Connected — return to chat" page. Handles GitHub's optional
  expiring-token mode (store refresh token if returned).

### 3. Backend — GithubClient

`httpx` wrapper (style of the Slack client): get ref, create branch ref,
get file contents, put file (commit), create pull request, get authenticated
login. Every call uses **the requesting user's token** — attribution is
structural, not cosmetic.

### 4. Backend — GithubFlow (When → Check → Stop → Do → Record)

New `app/workflows/github_pr.py`, shaped like `ApprovalFlow`; run kind
`github_pr` on the shared RunStore.

- **When** — seeded skill `raise-pr` (`workflow: "github"`); SkillRouter routes
  it (explicit `/raise-pr` or LLM auto-routing) from any surface.
- **Check** — surface toggle enabled? repo configured? user has a GitHub token
  (else reply with their Connect link and pause)? Then the LLM drafts: target
  file(s), new content, branch name, PR title/body — **grounded by fetching the
  current file from the repo**. Can't locate or ground the change → stop and
  ask, never guess.
- **Stop** — run `pending_confirm`; preview posted to the originating surface
  with Create PR / Cancel.
- **Do** — on confirm: branch off base → commit → open PR as the user → reply
  with PR link; run `completed`.
- **Record** — RunStore events for every step (request, draft, preview shown,
  confirmed/cancelled + by whom, PR URL) → admin Runs screen.

### 5. Surface renderers (one engine, three faces)

- **Slack:** colored-left-bar block card (existing style); buttons through the
  existing `/bot/slack/interactive` dispatcher, new branch on run kind.
- **Teams:** Adaptive Card with `Action.Submit` Create/Cancel; new submit-action
  handler in the Teams bot (approval buttons previously existed only in Slack —
  this is the genuinely new surface plumbing).
- **Web:** the chat answer carries a structured `pending_action` payload; the
  web chat renders a preview card whose buttons call a new
  `POST /workflows/runs/{id}/action` (MSAL-auth'd, requester-only). Slack and
  Teams handlers funnel into the same flow methods internally.

## Error handling

| Failure | Behaviour |
|---|---|
| User token revoked/expired (GitHub 401) | Reply with a reconnect link; run paused, event recorded |
| Branch name collision | Numeric suffix and retry |
| Repo misconfigured / not found | Check-step failure with an admin-facing hint |
| Tool toggled off | Blocked message at the Check step; no GitHub calls |
| LLM can't ground the change | Stop and ask the user — never guess (north-star rule) |
| Confirm clicked by someone other than the requester | Rejected; event recorded |

## Security notes (recorded trade-offs)

- Classic OAuth `repo` scope is broad; the finer-grained production path is a
  **GitHub App** with per-installation tokens — explicitly future work.
- Per-user tokens live in Redis (`github:token:{email}`) with the same posture
  as other Redis-held state; client secret stays in env/Key Vault.
- The `runs/{id}/action` endpoint authorizes the **requester only** for
  confirm/cancel; Slack/Teams button payloads are verified by the existing
  signature checks.

## Testing

- **pytest:** GithubFlow happy path + every Check failure (fake GithubClient,
  fake LLM); OAuth state round-trip (mint, validate, expire, reject reuse);
  `/admin/github/config` auth + validation; surface-name validation with
  `github`; `respx` tests for GithubClient against the GitHub REST API; run
  action endpoint — requester-only authorization.
- **Web:** `pnpm typecheck && pnpm lint && pnpm build`; mockup-first
  (admin card + modal in `admin-portal.html`, PR preview card in
  `user-web-chat.html`) approved **before** any `.tsx`.

## Docs to sync (Phase 4)

- `mockups/admin-portal.html` — GitHub card + setup modal.
- `mockups/user-web-chat.html` — PR preview card in chat.
- `mockups/architecture.html` — both views: GitHub as the first **Tool** in the
  *Do it* step ("act in your tools"), Master-Deck palette; Surfaces section
  gains the surface-vs-tool distinction.
- `references/techstack.md` — only if a new library is actually introduced
  (GitHub calls go through `httpx`, already in the stack).

## Out of scope (YAGNI)

- GitHub App auth, fine-grained tokens — future hardening.
- Multi-repo routing, repo pickers in chat — single admin-configured repo.
- Code-aware edits (multi-file refactors, running tests on the branch) — v1 is
  text/doc-style edits.
- PR-from-existing-branch shape — not in this milestone.
- Webhooks back from GitHub (PR merged/closed status sync) — future.
