# brain → SubStrateOS Rename — Phase A (Codebase) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename every "brain" reference in the repository to "SubStrateOS" — UI copy, MCP tool names, the backend directory, config/env identifiers, storage-key prefixes, and index/semantic names — as one reviewable PR, leaving live Azure resources for Phase B.

**Architecture:** Pure mechanical refactor verified by the existing pytest suite (`substrateos-api/.venv/bin/pytest`) + `web` build + a final repo-wide grep gate. The backend dir `brain-api/` is `git mv`'d to `substrateos-api/` first so all later edits use the new path. Phase A is **not** deployed to the old `brain-api` container app (its env still sets `BRAIN_*`); Phase B provisions new infra with `SUBSTRATEOS_*` env and deploys this image.

**Tech Stack:** Python 3.12 / FastAPI / pydantic-settings (backend), Next.js / TypeScript (web), Azure Container Apps, pytest, pnpm.

**Spec:** `docs/superpowers/specs/2026-06-03-substrateos-rename-design.md`

**Exclusions (never rename):** `JetBrains_Mono` (Google font in `web/app/layout.tsx` + `globals.css`), `companybrain.microsoft@gmail.com` (external account, in memory only — not in repo), and historical `docs/superpowers/{plans,specs}/*company-brain*.md` filenames/content.

**Deploy-skill note (refines spec for operational safety):** Phase A updates only the deploy skill's *build-context path* (`brain-api/` → `substrateos-api/`). The image repo, container-app name, RG (`rg-company-brain-india`), and ACR (`cbrainindiaacr`) stay at their current live values until Phase B, so the skill keeps working against live infra. Renaming the container-app name before the `substrateos-api` app exists would break the skill.

---

### Task 1: Rename the backend directory `brain-api/` → `substrateos-api/`

**Files:**
- Move: `brain-api/` → `substrateos-api/` (whole tree)
- Modify: `.github/workflows/ci.yml` (working-directory + job name)
- Modify: `.claude/skills/substrateos-deploy/scripts/deploy.sh` (build-context path)
- Modify: `.claude/skills/substrateos-deploy/SKILL.md` (source-dir column + manual-fallback path)
- Modify: `README.md`, `infra/provision.sh`, `infra/provision_cosmos.sh` (prose `brain-api/.env` refs)

- [ ] **Step 1: Move the directory with git**

```bash
cd "$(git rev-parse --show-toplevel)"
git mv brain-api substrateos-api
```

(`git mv` renames the directory on disk including the untracked `.venv`, so the existing virtualenv still works.)

- [ ] **Step 2: Verify the test suite still runs from the new path**

```bash
cd substrateos-api && .venv/bin/pytest -q && cd ..
```

Expected: all tests PASS (the rename of the dir alone changes no Python identifiers yet).

- [ ] **Step 3: Update CI working-directory and job name**

In `.github/workflows/ci.yml`, change the job key `brain-api:` → `substrateos-api:` and `working-directory: brain-api` → `working-directory: substrateos-api`.

- [ ] **Step 4: Update the deploy skill build-context path only**

In `.claude/skills/substrateos-deploy/scripts/deploy.sh`, change the build context / Dockerfile path argument from `brain-api` (the source dir) to `substrateos-api` in the `deploy_one "brain-api" ...` call — update **only the source-dir argument**, leaving the image-repo and container-app-name arguments as `brain-api` (Phase B renames those). The line `deploy_one "brain-api" "brain-api" "brain-api"` becomes `deploy_one "brain-api" "brain-api" "substrateos-api"` **only if** the third positional is the source dir — verify the function signature first:

```bash
grep -n 'deploy_one()' -A12 .claude/skills/substrateos-deploy/scripts/deploy.sh
```

Map the source-dir positional to `substrateos-api`; keep app-name and image-repo positionals at `brain-api`. In `SKILL.md`, change the "Source dir" column for the backend row from `brain-api/` to `substrateos-api/`, and the manual-fallback `docker build ... -f brain-api/Dockerfile brain-api` to `-f substrateos-api/Dockerfile substrateos-api`. Leave `-t cbrainindiaacr.azurecr.io/brain-api:$TAG`, the app name `brain-api`, RG, and ACR unchanged.

- [ ] **Step 5: Update prose path references**

```bash
cd "$(git rev-parse --show-toplevel)"
grep -rn 'brain-api/' README.md infra/provision.sh infra/provision_cosmos.sh
```

Replace each `brain-api/.env` → `substrateos-api/.env` and any `brain-api/` path in `README.md`. Do **not** touch `cbrainindiaacr.azurecr.io/brain-api` image refs (those are Phase B).

- [ ] **Step 6: Verify CI yaml parses and tests still pass**

```bash
cd substrateos-api && .venv/bin/pytest -q && cd ..
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci yaml ok')"
```

Expected: tests PASS, `ci yaml ok`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: rename brain-api dir to substrateos-api (paths, CI, deploy build-context)"
```

---

### Task 2: Hard-rename the MCP tools

**Files:**
- Modify: `substrateos-api/app/mcp/server.py` (docstring + two tool functions)
- Modify: `web/components/Chat.tsx:402-403` (Connect-panel tool list)
- Test: `substrateos-api/.venv/bin/pytest` (whichever test imports the MCP server, if any)

- [ ] **Step 1: Rename the tool functions and docstring in the backend**

In `substrateos-api/app/mcp/server.py`:
- `async def ask_company_brain(` → `async def ask_substrateos(`
- `async def search_company_brain(` → `async def search_substrateos(`
- Update the module docstring lines 1-4: "company brain" → "SubStrateOS", and `ask_company_brain and search_company_brain` → `ask_substrateos and search_substrateos`.

Confirm no other references:

```bash
grep -rn 'company_brain' substrateos-api/app
```

Expected after edit: no matches.

- [ ] **Step 2: Run backend tests**

```bash
cd substrateos-api && .venv/bin/pytest -q && cd ..
```

Expected: PASS.

- [ ] **Step 3: Update the Connect-panel tool names in the web UI**

In `web/components/Chat.tsx`, lines ~402-403:
- `<span className="tn">ask_company_brain</span>` → `ask_substrateos`
- `<span className="tn">search_company_brain</span>` → `search_substrateos`

- [ ] **Step 4: Verify the web build**

```bash
cd web && pnpm build && cd ..
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: hard-rename MCP tools to ask_substrateos / search_substrateos"
```

---

### Task 3: Rename config fields and env-var names

**Files:**
- Modify: `substrateos-api/app/config.py` (fields `brain_tenant_id`, `brain_log_level`, `brain_api_base_url` + comments)
- Modify: `substrateos-api/.env.example` (`BRAIN_TENANT_ID`)
- Modify: `substrateos-api/eval/load_corpus.py:13` (`BRAIN_API_URL`)

Pydantic-settings maps a field `brain_tenant_id` to env var `BRAIN_TENANT_ID` automatically, so renaming the field renames the env var. Local `.env` is gitignored — only `.env.example` is committed.

- [ ] **Step 1: Rename the three fields and their comments in config.py**

In `substrateos-api/app/config.py`:
- `brain_tenant_id: str = "t-test"` → `substrateos_tenant_id: str = "t-test"`
- `brain_log_level: str = "INFO"` → `substrateos_log_level: str = "INFO"`
- `brain_api_base_url: str = "http://localhost:8000"` → `substrateos_api_base_url: str = "http://localhost:8000"`
- In the comments: `onto \`brain_tenant_id\`` → `onto \`substrateos_tenant_id\``, `<brain_tenant_id>:everyone` → `<substrateos_tenant_id>:everyone`, the `# Brain` section header → `# SubStrateOS`, `(env BRAIN_API_BASE_URL)` → `(env SUBSTRATEOS_API_BASE_URL)`, and `brain-api URL surfaced to the UI` → `substrateos-api URL surfaced to the UI`.

- [ ] **Step 2: Update .env.example and the eval script**

- `substrateos-api/.env.example`: `BRAIN_TENANT_ID=t-test` → `SUBSTRATEOS_TENANT_ID=t-test`
- `substrateos-api/eval/load_corpus.py:13`: `os.environ.get("BRAIN_API_URL"` → `os.environ.get("SUBSTRATEOS_API_URL"`

- [ ] **Step 3: Update local .env so the app/tests still load (not committed)**

```bash
sed -i '' 's/^BRAIN_TENANT_ID=/SUBSTRATEOS_TENANT_ID=/' substrateos-api/.env 2>/dev/null || true
```

(Best-effort; `.env` is gitignored. If other `BRAIN_*` keys exist locally, rename them to `SUBSTRATEOS_*` too.)

- [ ] **Step 4: Do not run tests yet** — call sites still reference `brain_tenant_id` and will fail until Task 4. Proceed directly.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: rename brain_ config fields/env vars to substrateos_ (config + .env.example)"
```

---

### Task 4: Propagate the renamed config fields across all call sites and tests

**Files:**
- Modify: every `substrateos-api/app/**` and `substrateos-api/tests/**` file referencing `brain_tenant_id`, `brain_api_base_url`, or `brain_log_level`
- Modify: test files monkeypatching `BRAIN_TENANT_ID` → `SUBSTRATEOS_TENANT_ID`
- Modify: test fixture tenant values `brain-t` / `my-brain-tenant`

- [ ] **Step 1: Find all remaining references**

```bash
cd "$(git rev-parse --show-toplevel)"
grep -rn 'brain_tenant_id\|brain_api_base_url\|brain_log_level' substrateos-api
grep -rn 'BRAIN_TENANT_ID\|BRAIN_API_BASE_URL\|BRAIN_LOG_LEVEL' substrateos-api
```

- [ ] **Step 2: Rename the Python identifiers in bulk (scoped to substrateos-api)**

```bash
cd substrateos-api
grep -rl 'brain_tenant_id' app tests | xargs sed -i '' 's/brain_tenant_id/substrateos_tenant_id/g'
grep -rl 'brain_api_base_url' app tests | xargs sed -i '' 's/brain_api_base_url/substrateos_api_base_url/g'
grep -rl 'brain_log_level' app tests | xargs sed -i '' 's/brain_log_level/substrateos_log_level/g'
cd ..
```

- [ ] **Step 3: Rename the env-var names in test monkeypatches**

```bash
cd substrateos-api
grep -rl 'BRAIN_TENANT_ID' tests | xargs sed -i '' 's/BRAIN_TENANT_ID/SUBSTRATEOS_TENANT_ID/g'
grep -rl 'BRAIN_API_BASE_URL\|BRAIN_LOG_LEVEL' tests app | xargs sed -i '' -e 's/BRAIN_API_BASE_URL/SUBSTRATEOS_API_BASE_URL/g' -e 's/BRAIN_LOG_LEVEL/SUBSTRATEOS_LOG_LEVEL/g' 2>/dev/null || true
cd ..
```

- [ ] **Step 4: Rename the test fixture tenant string values**

These are arbitrary tenant-id values used in fixtures (`test_connector_outlook_mail.py`, `test_connector_teams.py`, `test_connector_outlook_calendar.py`, `test_connector_sharepoint.py`), not the brand — rename for grep-cleanliness:

```bash
cd substrateos-api
grep -rl '"brain-t"' tests | xargs sed -i '' 's/"brain-t"/"sos-t"/g'
grep -rl 'my-brain-tenant' tests | xargs sed -i '' 's/my-brain-tenant/my-substrateos-tenant/g'
cd ..
```

- [ ] **Step 5: Run the full backend suite**

```bash
cd substrateos-api && .venv/bin/pytest -q && cd ..
```

Expected: all tests PASS. If any fail, grep the failure for a missed `brain_` identifier and fix.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: propagate substrateos_tenant_id/api_base_url/log_level across app + tests"
```

---

### Task 5: Rename storage-key prefixes `cbrain_*` → `sos_*`

**Files:**
- Modify: `substrateos-api/app/connectors/subscriptions.py:211-212`
- Modify: `substrateos-api/app/connectors/cosmos_store.py:24-27`
- Modify: `substrateos-api/app/tokens/store.py:3,25`

Fresh re-ingest (Phase B) means no stored data uses the old prefixes, so this is safe.

- [ ] **Step 1: Rename all `cbrain_` prefixes**

```bash
cd substrateos-api
grep -rl 'cbrain_' app | xargs sed -i '' 's/cbrain_/sos_/g'
grep -rn 'cbrain_' app    # expect: no matches
cd ..
```

- [ ] **Step 2: Run tests covering token + connector stores**

```bash
cd substrateos-api && .venv/bin/pytest -q tests/test_token_store.py tests/test_connector_realtime.py && .venv/bin/pytest -q && cd ..
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: rename cbrain_ storage-key prefixes to sos_"
```

---

### Task 6: Rename the AI Search index and semantic-config names

**Files:**
- Modify: `substrateos-api/app/retrieval/ai_search_client.py:126,208` (`brain-semantic`)
- Modify: `substrateos-api/tests/test_config.py:10,19` (`brain-content-t-test`)
- Modify: `substrateos-api/.env.example`, `infra/provision.sh:130` (`brain-content-t-test`)
- Modify: `substrateos-api/scripts/create_search_index.py:1` (docstring + any semantic-config definition)

The semantic-config name must match between index creation and the query. Renaming the query side here requires Phase B to create the index with the matching `substrateos-semantic` name.

- [ ] **Step 1: Rename the semantic-config name in the query client**

```bash
cd substrateos-api
sed -i '' 's/brain-semantic/substrateos-semantic/g' app/retrieval/ai_search_client.py
grep -rn 'brain-semantic' app    # expect: no matches
cd ..
```

- [ ] **Step 2: Check whether create_search_index.py defines the semantic config**

```bash
grep -n 'semantic\|brain-content' substrateos-api/scripts/create_search_index.py
```

If a semantic configuration is defined there, rename it to `substrateos-semantic`. Rename the docstring `brain-content-{tenant}` → `substrateos-content-{tenant}`.

- [ ] **Step 3: Rename the index name in tests, .env.example, and the provision template**

```bash
cd "$(git rev-parse --show-toplevel)"
sed -i '' 's/brain-content-t-test/substrateos-content-t-test/g' substrateos-api/tests/test_config.py substrateos-api/.env.example infra/provision.sh
# local .env (gitignored) too:
sed -i '' 's/brain-content-t-test/substrateos-content-t-test/g' substrateos-api/.env 2>/dev/null || true
```

- [ ] **Step 4: Run the full backend suite**

```bash
cd substrateos-api && .venv/bin/pytest -q && cd ..
```

Expected: PASS (test_config now asserts `substrateos-content-t-test`).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: rename search index to substrateos-content-* and semantic config to substrateos-semantic"
```

---

### Task 7: Rename user-facing brand copy in the web UI

**Files:**
- Modify: `web/components/Chat.tsx` (lines ~338-340, 395, 412, 504: "company brain" / "the brain")
- Modify: `web/app/admin/surfaces/page.tsx:3` ("Where the brain shows up")
- Modify: `web/lib/api.ts:85,187` (error message + comment `brain-api`)

- [ ] **Step 1: Find every brand-copy occurrence**

```bash
grep -rn 'brain' web/components/Chat.tsx web/app/admin/surfaces/page.tsx web/lib/api.ts
```

- [ ] **Step 2: Replace the copy**

In `web/components/Chat.tsx`:
- "Connect your AI assistant to the company brain" → "...to SubStrateOS"
- "Ask the brain without leaving your channels" (Slack + Teams) → "Ask SubStrateOS without leaving your channels"
- "search & ask the company brain — scoped to your access" → "search & ask SubStrateOS — scoped to your access"
- "`/ask` the brain or @mention it" → "`/ask` SubStrateOS or @mention it"
- `<div className="title">Ask the brain</div>` → `Ask SubStrateOS`

In `web/app/admin/surfaces/page.tsx:3`: "Where the brain shows up" → "Where SubStrateOS shows up".

In `web/lib/api.ts`: line 85 `\`brain-api ${resp.status}...\`` → `\`substrateos-api ${resp.status}...\``; line 187 comment `The brain-api base URL` → `The substrateos-api base URL`.

- [ ] **Step 3: Verify the web build**

```bash
cd web && pnpm build && cd ..
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: rename user-facing brand copy from 'brain' to 'SubStrateOS' in web UI"
```

---

### Task 8: Final sweep — grep gate + full verification

**Files:** none (verification only) — fix any stragglers found.

- [ ] **Step 1: Repo-wide grep for surviving 'brain' references**

```bash
cd "$(git rev-parse --show-toplevel)"
grep -rinE 'brain' \
  --include='*.ts' --include='*.tsx' --include='*.py' --include='*.json' \
  --include='*.sh' --include='*.yml' --include='*.yaml' --include='*.html' \
  --include='*.css' --include='*.md' --include='*.env*' \
  substrateos-api web infra .claude .github README.md \
  | grep -viE 'JetBrains|company-brain.*\.md|docs/superpowers/(plans|specs)' \
  | grep -v node_modules
```

Expected: **only** the deliberately-deferred Phase B live-infra names — `rg-company-brain-india`, `cbrainindiaacr`, `cbrain-lokesh-*`, the `cbrainindiaacr.azurecr.io/brain-api` image refs in the deploy skill, and the `brain-api` container-app name. Anything else is a straggler — fix it (re-run the relevant task's sed) and re-grep.

- [ ] **Step 2: Confirm the deferred Phase B names are the only remainder**

Eyeball the grep output. Every remaining line must be a live-infra identifier the spec explicitly defers to Phase B. If a code identifier, UI string, or `*_tenant_id` survived, it is a bug — fix and re-grep until clean.

- [ ] **Step 3: Full backend test suite**

```bash
cd substrateos-api && .venv/bin/pytest -q && cd ..
```

Expected: all PASS.

- [ ] **Step 4: Full web build**

```bash
cd web && pnpm build && cd ..
```

Expected: build succeeds.

- [ ] **Step 5: Commit any straggler fixes**

```bash
git add -A
git commit -m "refactor: final brain → SubStrateOS sweep (Phase A complete)" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:** UI copy (Task 7) ✓; MCP tools (Task 2) ✓; `brain-api`→`substrateos-api` dir/CI/deploy (Task 1) ✓; config fields + env vars (Tasks 3-4) ✓; `cbrain_*` prefixes (Task 5) ✓; index/semantic names (Task 6) ✓; exclusions (`JetBrains`, external email, historical doc filenames) honored in the grep gate (Task 8) ✓; Phase B deferral of live Azure names documented and validated by the grep gate ✓.

**Deviation from spec (intentional, noted in header):** the deploy skill's image repo + container-app name stay `brain-api` in Phase A for operational safety — renaming the app before it exists would break the skill. Phase B renames image repo, app name, RG, and ACR together. This narrows Task 1's deploy-skill edits to the build-context path only.

**Not in Phase A (Phase B runbook):** live Azure provisioning, fresh re-ingest, URL/`.env.production` cutover, decommissioning old resources, memory-file updates, `infra/*.md` doc updates.
