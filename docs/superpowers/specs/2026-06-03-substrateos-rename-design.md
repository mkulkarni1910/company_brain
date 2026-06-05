# Design: brain → SubstrateOS rename

**Date:** 2026-06-03
**Status:** Approved (design)

## Goal

Replace every "brain" reference in the codebase and infrastructure with
"SubstrateOS" — user-facing UI copy, MCP tool names, internal code identifiers,
config/env-var names, and (separately) live Azure resource names. The web
container app is already named `substrateos-web`, so the brand migration is
half-done; this completes it.

## Scope decisions (locked)

- **Depth:** everything, including live Azure resources.
- **Sequencing:** code rename first as one reviewable PR (Phase A), then a
  separate ops runbook for the live Azure cutover (Phase B). Keeps the risky
  live ops isolated from the testable diff.
- **Data:** fresh re-ingest — provision empty `substrateos` resources and
  rebuild the Search index + people graph from the connectors. No data
  migration. Conversation history is not preserved.
- **MCP tools:** hard rename (`ask_company_brain` → `ask_substrateos`,
  `search_company_brain` → `search_substrateos`). No deprecation aliases.
- **Internal identifiers:** rename `brain_tenant_id` → `substrateos_tenant_id`
  everywhere (~75 call sites), per the deepest-scope choice.
- **Historical docs:** leave `docs/superpowers/{plans,specs}/*company-brain*.md`
  **filenames** untouched (dated archives; renaming rewrites history). Their
  content is not edited either.

## Architecture: two phases

### Phase A — Codebase rename (one PR)

Everything in the repo. Verified by the existing pytest suite + `pnpm build` +
LSP diagnostics. **Not deployed to the old `brain-api` container app** — its
env still uses `BRAIN_*` names, so deploying renamed code there would break it.
Phase A merges and waits for Phase B to deploy it onto new infra.

### Phase B — Azure cutover (ops runbook, separate)

Provision `substrateos`-named resources, set new env-var names, deploy the
Phase A image there, fresh re-ingest, flip URLs, decommission old, update
memory. Needs live `az` access + a downtime window.

## The rename map

| Category | From | To |
|---|---|---|
| UI copy | "the brain", "company brain", "Ask the brain" | "SubstrateOS" |
| MCP tools | `ask_company_brain`, `search_company_brain` | `ask_substrateos`, `search_substrateos` |
| Backend dir / image / container app | `brain-api` | `substrateos-api` |
| Config fields | `brain_tenant_id`, `brain_api_base_url`, `brain_log_level` | `substrateos_tenant_id`, `substrateos_api_base_url`, `substrateos_log_level` |
| Env vars | `BRAIN_TENANT_ID`, `BRAIN_API_BASE_URL`, `BRAIN_LOG_LEVEL`, `BRAIN_API_URL` | `SUBSTRATEOS_*` |
| Redis/storage key prefixes | `cbrain_token`, `cbrain_syncjob`, `cbrain_subscription`, `cbrain_oauthstate`, `cbrain_delta`, `cbrain_connection`, `cbrain_connactivity` | `sos_*` |
| Search index / semantic | `brain-content-t-test`, `brain-semantic` | `substrateos-content-t-test`, `substrateos-semantic` |
| **Phase B** Azure | `rg-company-brain-{dev,india}`, `cbrainindiaacr`, `cbrain-lokesh-*`, ADX/Cosmos db `brain` | `rg-substrateos-{dev,india}`, `substrateosindiaacr`, `substrateos-lokesh-*`, db `substrateos` |
| **Exclude** | `JetBrains_Mono` (Google font), `companybrain.microsoft@gmail.com` (external account) | unchanged |

### Naming notes
- ACR disallows hyphens: `substrateosindiaacr` (19 chars, under the 50 limit).
- Container apps: api → `substrateos-api`; web stays `substrateos-web`.
- When `brain-api` becomes `substrateos-api`, its container-app FQDN changes,
  so `web/.env.production` `NEXT_PUBLIC_API_BASE_URL` and any external
  MCP/PAT endpoints must be updated at cutover (Phase B).

## Phase A scope (the PR)

- **`web/`**: UI strings in `Chat.tsx`, `admin/surfaces/page.tsx`; the
  Connect-panel tool names; `lib/api.ts` error/comment strings.
- **`brain-api/` → `substrateos-api/`**: `git mv` the dir; rename MCP tools in
  `mcp/server.py`; rename config fields + env reads in `config.py` and all
  ~75 `brain_tenant_id` call sites across connectors/tests; Redis key prefixes;
  Dockerfile refs.
- **`.github/workflows/ci.yml`**: job name + `working-directory`.
- **Deploy skill** (`.claude/skills/substrateos-deploy/{SKILL.md,scripts/deploy.sh}`):
  source dir, image repo, container-app name. **Keeps `rg-company-brain-india`
  and `cbrainindiaacr` until Phase B** — the skill stays pointed at live infra
  until cutover.
- **`infra/*.sh`**: parameterize so the brain→substrateos default names flip;
  these are templates, not run until Phase B.
- **`README.md`**.
- New spec: this file.

### Env-var safety (why Phase A is not deployed to old infra)
Renaming the env-var *names* the code reads (`BRAIN_TENANT_ID` →
`SUBSTRATEOS_TENANT_ID`) while the old container apps still *set* `BRAIN_*`
would break the app on deploy. Therefore Phase A is merged but only deployed as
part of Phase B's new-infra cutover, which sets the new env names. No
transitional aliases are introduced (consistent with the hard-rename choice).

## Phase B runbook (separate, after A merges)

1. Provision new RG/ACR/Search/OpenAI/Cosmos/Redis/KV/identity/container-apps
   env under `substrateos` names.
2. Grant managed-identity RBAC + Easy Auth app registration.
3. Set `SUBSTRATEOS_*` env on the new apps.
4. Build/push the Phase A image to `substrateosindiaacr`; create the
   `substrateos-api` container app.
5. Fresh re-ingest: run SharePoint/Outlook connectors to rebuild the index;
   re-seed the people graph; re-create KV secrets.
6. Update `web/.env.production` to the new `substrateos-api` FQDN; rebuild +
   redeploy `substrateos-web`.
7. Smoke test (`/healthz` 200; web 401; an authed query returns a grounded
   answer from re-ingested data).
8. Decommission the `brain-api` container app + old resource groups.
9. Update the deploy skill + memory files to the new names; update
   `infra/*.md` docs (`entra_setup.md`, `adx_setup.md`, `README.md`).

## Testing

- **Phase A:** `cd substrateos-api && .venv/bin/pytest` (all green),
  `cd web && pnpm build`, LSP diagnostics clean. The suite references
  `brain_tenant_id` heavily, so a passing suite proves the rename is internally
  consistent. A repo-wide `grep -ri brain` (excluding `JetBrains`, the external
  account email, historical `*company-brain*` doc filenames, and `node_modules`)
  should return nothing.
- **Phase B:** post-cutover health checks + interactive query verification.

## Out of scope

- Data migration (chosen: fresh re-ingest).
- Renaming historical plan/spec doc filenames or editing their content.
- MCP backward-compat aliases.
