# SubstrateOS — Production: Auth + Azure Deploy + Hardening

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development for the CODE tasks. CONTROLLER-RUN and USER-IN-LOOP tasks are run by the controller / human respectively (like Phase 1b/2b). Steps use checkbox (`- [ ]`).

**Goal:** Take SubstrateOS from a local debug demo to a deployed, Entra-authenticated production app on Azure Container Apps — real per-user identity (no debug bypass), secrets in Key Vault, locked CORS, and per-user OBO so Live Fetch returns each user's own Graph results.

**Architecture:** Two Azure Container Apps in `rg-company-brain-dev` (region `swedencentral`, env `cbrain-lokesh-capp-env`, registry `cbrainlokeshacr`): **brain-api** (FastAPI image) and **substrateos-web** (Next.js standalone image). Both fronted by **Container Apps built-in authentication (Easy Auth)** bound to one Entra app (auto-created by `az containerapp auth microsoft set`). The browser logs in at the platform; brain-api resolves the user from the Easy Auth `X-MS-CLIENT-PRINCIPAL` header (debug header only honored when `ENABLE_DEBUG_AUTH`, which is **false** in prod). Secrets load from Key Vault via the apps' managed identity. Live Fetch uses on-behalf-of with the user's token.

**Tech Stack:** existing + Docker (two images), `az containerapp` (deploy + auth), `azure-keyvault-secrets` (already a dep), Container Apps Easy Auth, MSAL on-behalf-of via `msal`/`azure-identity` (OBO).

**Ownership tags:** `[CODE]` subagent/controller, local + verifiable. `[CTRL]` controller runs `az`/docker (real Azure, costs money). `[USER]` human action in Azure portal (Entra admin consent).

**Cost note:** Container Apps consumption scales to zero; two apps + image pulls add modestly to the existing ~$ baseline. ACR/env already provisioned.

**Prerequisites:** `phase-4-zone4-complete` + `web-chat-light-v1` shipped. `az login` active (sub "Company Brain Microsoft Hackathon", tenant f3bddc3c). brain-api auth.py has JWT validation. Corpus under tenant `t-eval`.

---

## Task 1 [CODE]: brain-api resolves user from Easy Auth principal (+ keep JWT/debug paths)

**Why:** In prod, Easy Auth injects `X-MS-CLIENT-PRINCIPAL` (base64 JSON of claims) and `X-MS-CLIENT-PRINCIPAL-NAME`. brain-api must build its `User` from that, expand groups, and NOT trust the debug header unless `ENABLE_DEBUG_AUTH` (false in prod). Bearer JWT path (auth.py) stays as a fallback for direct API clients.

**Files:**
- Modify: `brain-api/app/auth.py` (add `user_from_easy_auth_header`)
- Modify: `brain-api/app/api/query.py`, `brain-api/app/api/feedback.py` (shared resolver: easy-auth → bearer → debug)
- Create: `brain-api/app/api/_auth_resolve.py` (one shared `resolve_user(...)`)
- Create: `brain-api/tests/test_easy_auth.py`

- [ ] **Step 1: Write the failing unit test**

`brain-api/tests/test_easy_auth.py`:

```python
import base64, json
from app.auth import user_from_easy_auth_header


def _principal(oid: str, tid: str, name: str, groups: list[str]) -> str:
    claims = [{"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": oid},
              {"typ": "http://schemas.microsoft.com/identity/claims/tenantid", "val": tid},
              {"typ": "name", "val": name},
              {"typ": "preferred_username", "val": "u@x"}]
    claims += [{"typ": "groups", "val": g} for g in groups]
    payload = {"auth_typ": "aad", "claims": claims}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_user_from_easy_auth_header_parses_claims() -> None:
    hdr = _principal("oid-1", "tid-1", "Alex", ["g-sales", "g-central"])
    u = user_from_easy_auth_header(hdr)
    assert u.user_id == "oid-1"
    assert u.tenant_id == "tid-1"
    assert u.display_name == "Alex"
    assert {"g-sales", "g-central"} <= u.group_ids


def test_bad_header_raises() -> None:
    import pytest
    from app.auth import InvalidToken
    with pytest.raises(InvalidToken):
        user_from_easy_auth_header("not-base64-json")
```

- [ ] **Step 2: Run, expect fail** (`uv run pytest tests/test_easy_auth.py -v` → ImportError).

- [ ] **Step 3: Implement `user_from_easy_auth_header` in `app/auth.py`**

Append:

```python
import base64
import json as _json


def user_from_easy_auth_header(principal_b64: str) -> User:
    """Build a User from the Container Apps Easy Auth X-MS-CLIENT-PRINCIPAL header."""
    try:
        payload = _json.loads(base64.b64decode(principal_b64).decode())
        claims = {(_TYPE_ALIASES.get(c["typ"], c["typ"])): c["val"] for c in payload.get("claims", [])}
        groups = {c["val"] for c in payload.get("claims", []) if c["typ"] in ("groups", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role")}
        oid = claims.get("oid") or claims.get("objectidentifier")
        tid = claims.get("tid") or claims.get("tenantid")
        if not oid or not tid:
            raise InvalidToken("missing oid/tid in principal")
        return User(
            user_id=oid, tenant_id=tid,
            email=claims.get("preferred_username") or claims.get("email") or "",
            display_name=claims.get("name") or oid,
            group_ids=groups,
        )
    except InvalidToken:
        raise
    except Exception as e:
        raise InvalidToken(f"bad easy-auth principal: {e}") from e


_TYPE_ALIASES = {
    "http://schemas.microsoft.com/identity/claims/objectidentifier": "oid",
    "http://schemas.microsoft.com/identity/claims/tenantid": "tid",
}
```

- [ ] **Step 4: Run unit test → pass.**

- [ ] **Step 5: Shared resolver `app/api/_auth_resolve.py`**

```python
"""Unified request-identity resolution: Easy Auth header > Bearer JWT > debug header."""
from __future__ import annotations
from fastapi import HTTPException
from app.auth import InvalidToken, user_from_bearer, user_from_easy_auth_header
from app.config import get_settings
from app.domain.identity import User


def _debug_user(header: str) -> User:
    parts = header.split(",")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="bad debug header")
    tenant, user_id, *groups = parts
    return User(user_id=user_id, tenant_id=tenant, email=f"{user_id}@debug",
                display_name=user_id, group_ids=set(groups))


async def resolve_user(*, easy_auth: str | None, authorization: str | None, debug_header: str | None) -> User:
    if easy_auth:  # Container Apps Easy Auth (production)
        try:
            return user_from_easy_auth_header(easy_auth)
        except InvalidToken as e:
            raise HTTPException(status_code=401, detail=f"invalid principal: {e}") from e
    if authorization and authorization.lower().startswith("bearer "):
        try:
            return await user_from_bearer(authorization.split(" ", 1)[1])
        except InvalidToken as e:
            raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e
    if get_settings().enable_debug_auth and debug_header:
        return _debug_user(debug_header)
    raise HTTPException(status_code=401, detail="auth required")
```

- [ ] **Step 6: Use it in `query.py` and `feedback.py`**

In both endpoints, accept the headers and call `resolve_user`. For `app/api/query.py` query handler signature:

```python
from fastapi import Header
from app.api._auth_resolve import resolve_user
...
@router.post("/query", response_model=Answer)
async def query(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Answer:
    user = await resolve_user(easy_auth=x_ms_client_principal, authorization=authorization, debug_header=x_debug_bypass_auth)
    return await orchestrator.answer(body, user=user)
```

Mirror in `feedback.py` (replace its `_resolve_user` with the shared `resolve_user`, adding the `x_ms_client_principal` header param). Remove now-duplicated `_debug_user` from query.py/feedback.py (use the shared module).

- [ ] **Step 7: Run suite + ruff**

`uv run pytest -m "not integration"` (unit incl. new) + `uv run pytest tests/test_query_e2e.py tests/test_feedback.py -v -m integration` (debug path still works because ENABLE_DEBUG_AUTH=true locally) + `uv run ruff check .` → clean.

- [ ] **Step 8: Commit** — `feat: brain-api resolves user from Easy Auth principal (prod) + shared auth resolver`

---

## Task 2 [CODE]: Load secrets from Key Vault via managed identity

**Why:** Remove plaintext secrets (Redis key, Cosmos key, admin key) from env in prod. When `AZURE_KEY_VAULT_URL` is set and `USE_KEY_VAULT=true`, fetch named secrets via `DefaultAzureCredential` at startup and overlay them onto Settings.

**Files:**
- Modify: `brain-api/app/config.py` (add `use_key_vault: bool=False`; a `load_secrets()` helper)
- Modify: `brain-api/app/main.py` (call `load_secrets()` in lifespan before constructing clients)
- Create: `brain-api/tests/test_keyvault_overlay.py` (unit, mock the KV client)

- [ ] **Step 1: Failing unit test** — `load_secrets` overlays settings from a fake KV client (inject a mapping); when `use_key_vault=false`, it's a no-op. Assert `settings.redis_key` becomes the KV value.

- [ ] **Step 2: Implement** in `config.py`:

```python
    use_key_vault: bool = False
```

Add a module function:

```python
def load_secrets_from_keyvault(settings: "Settings", client=None) -> None:
    """Overlay secrets from Key Vault onto settings (prod). Secret names:
    redis-key, cosmos-gremlin-key, admin-api-key, adx is AAD (no secret)."""
    if not settings.use_key_vault or not settings.azure_key_vault_url:
        return
    if client is None:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        client = SecretClient(vault_url=settings.azure_key_vault_url, credential=DefaultAzureCredential())
    def _get(name):
        try:
            return client.get_secret(name).value
        except Exception:
            return None
    settings.redis_key = _get("redis-key") or settings.redis_key
    settings.cosmos_gremlin_key = _get("cosmos-gremlin-key") or settings.cosmos_gremlin_key
    settings.admin_api_key = _get("admin-api-key") or settings.admin_api_key
```

- [ ] **Step 3: Call in lifespan** (`main.py`), first line after `get_settings()`:

```python
    from app.config import load_secrets_from_keyvault
    s = get_settings()
    load_secrets_from_keyvault(s)
```

(No-op locally since `use_key_vault=false`.)

- [ ] **Step 4: Test + ruff + commit** — `feat: load secrets from Key Vault via managed identity (prod opt-in)`

---

## Task 3 [CODE]: Production CORS + config from env

**Why:** CORS must allow the deployed web origin (not just localhost). Make origins configurable.

**Files:** `brain-api/app/config.py`, `brain-api/app/main.py`

- [ ] **Step 1:** Add to Settings: `cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"` (comma-separated).
- [ ] **Step 2:** In `main.py`, replace the hardcoded CORS list with `allow_origins=[o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()]`.
- [ ] **Step 3:** Quick unit test that the split parses multiple origins. Commit — `feat: configurable CORS origins for prod`

---

## Task 4 [CODE]: Per-user OBO for Live Fetch

**Why:** With real per-user tokens, Live Fetch should query Graph as the requesting user (OBO), returning their own files/sites, and `live_fetch_obo_enabled` flips true so live results bypass the fail-closed recheck safely.

**Files:**
- Modify: `brain-api/app/live_fetch/graph_search.py` (`fetch(*, query, user, user_token=None)`; OBO exchange when token present)
- Modify: `brain-api/app/live_fetch/base.py` (protocol signature gains optional `user_token`)
- Modify: `brain-api/app/orchestrator/kernel.py` (thread `user_token` from request → retrieve_ranked → fetcher)
- Modify: `brain-api/app/domain/query.py` (QueryRequest gains `user_token: str | None = None` — set server-side, not by clients) OR thread via answer() param
- Modify: `brain-api/app/api/query.py` (pass the inbound bearer/easy-auth token to the orchestrator)
- Create: `brain-api/tests/test_obo_threading.py` (unit: fetcher OBO path is invoked with the token; falls back to app token when none)

**Design:** add `user_token: str | None` as a parameter on `orchestrator.answer(request, *, user, user_token=None)` and `retrieve_ranked(..., user_token=None)`; the query endpoint passes the raw bearer token (or the Easy Auth token-store token). `MSGraphSearchFetcher.fetch(*, query, user, user_token=None)`: if `user_token` and `settings.live_fetch_obo_enabled`, do OBO (msal ConfidentialClientApplication.acquire_token_on_behalf_of with the brain-api client id/secret + scopes Files.Read.All/Sites.Read.All) → Graph token; else current DefaultAzureCredential fallback. Keep never-raises.

- [ ] **Step 1:** Unit test with a fake fetcher verifying `user_token` threads from `answer` → `retrieve_ranked` → `fetch`. (OBO HTTP itself is integration; mock at the fetcher boundary.)
- [ ] **Step 2-5:** Implement the threading + OBO exchange (msal confidential client; client id/secret from settings `azure_api_client_id` + a new `azure_api_client_secret` loaded from Key Vault). Keep `live_fetch_obo_enabled` default False; prod sets it true once consent is granted.
- [ ] **Step 6:** Tests + ruff + commit — `feat: per-user OBO for Live Fetch (threaded user token, msal on-behalf-of)`

(Note: OBO needs the Entra app client secret + admin-consented delegated Graph scopes — provisioned in Task 8/USER. Until then `live_fetch_obo_enabled=false` and Live Fetch stays single-identity/fail-closed.)

---

## Task 5 [CODE]: Dockerfiles + Next standalone

**Files:**
- Create: `brain-api/Dockerfile`, `brain-api/.dockerignore`
- Create: `web/Dockerfile`, `web/.dockerignore`
- Modify: `web/next.config.mjs` (`output: "standalone"`)

- [ ] **Step 1: `web/next.config.mjs`** → `const nextConfig = { reactStrictMode: true, output: "standalone" };`

- [ ] **Step 2: `brain-api/Dockerfile`** (uv-based):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY scripts ./scripts
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`brain-api/.dockerignore`: `.venv`, `tests`, `eval`, `__pycache__`, `*.pyc`, `.env`.

- [ ] **Step 3: `web/Dockerfile`** (Next standalone, multi-stage):

```dockerfile
FROM node:20-slim AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build
FROM node:20-slim AS run
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```

`web/.dockerignore`: `node_modules`, `.next`, `.env.local`.

- [ ] **Step 4: Local build smoke (optional, if Docker available):** `docker build -t brain-api:local brain-api` and `docker build -t web:local web` — both succeed. If Docker isn't available locally, skip; the cloud build (Task 6) uses `az acr build`.

- [ ] **Step 5: Commit** — `build: Dockerfiles for brain-api + web (Next standalone)`

---

## Task 6 [CTRL]: Build + push images, deploy brain-api container app

- [ ] **Step 1: Build + push brain-api image** (cloud build, no local Docker needed):

```bash
az acr build -r cbrainlokeshacr -t brain-api:v1 ./brain-api
```

- [ ] **Step 2: Create the brain-api container app** with the existing user-assigned managed identity (`cbrain-lokesh-mi`), internal-or-external ingress on 8000, and env vars (non-secret) + `USE_KEY_VAULT=true`, `ENABLE_DEBUG_AUTH=false`, `LIVE_FETCH_OBO_ENABLED=false` (flip later), `AZURE_*` endpoints, `CORS_ALLOW_ORIGINS=<web url>` (set after Task 7), `ADX_CLUSTER_URI`, `COSMOS_GREMLIN_*` (key via KV), `AZURE_KEY_VAULT_URL`. Pull creds via the MI / ACR. Example:

```bash
MI=$(az identity show -g rg-company-brain-dev -n cbrain-lokesh-mi --query id -o tsv)
az containerapp create -g rg-company-brain-dev -n brain-api \
  --environment cbrain-lokesh-capp-env \
  --image cbrainlokeshacr.azurecr.io/brain-api:v1 \
  --registry-server cbrainlokeshacr.azurecr.io --registry-identity "$MI" \
  --user-assigned "$MI" --ingress external --target-port 8000 \
  --min-replicas 0 --max-replicas 3 \
  --env-vars USE_KEY_VAULT=true ENABLE_DEBUG_AUTH=false LIVE_FETCH_OBO_ENABLED=false \
    AZURE_TENANT_ID=<tid> AZURE_CLIENT_ID=<mi-client-id> \
    AZURE_AI_SEARCH_ENDPOINT=<...> AZURE_AI_SEARCH_INDEX=brain-content-t-test \
    AZURE_OPENAI_ENDPOINT=<...> AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o AZURE_OPENAI_EMBED_DEPLOYMENT=text-embedding-3-large AZURE_OPENAI_PLAN_DEPLOYMENT=gpt-4o \
    AZURE_REDIS_HOST=<...> AZURE_REDIS_PORT=6380 AZURE_REDIS_SSL=true \
    COSMOS_GREMLIN_ENDPOINT=<...> COSMOS_GREMLIN_DATABASE=brain COSMOS_GREMLIN_GRAPH=people \
    ADX_CLUSTER_URI=<...> ADX_DATABASE=brain BRAIN_TENANT_ID=t-eval \
    AZURE_KEY_VAULT_URL=<...>
```

(Pull exact endpoint values from `brain-api/.env`. Use `BRAIN_TENANT_ID=t-eval` so seeded users/corpus line up, or t-test per preference.)

- [ ] **Step 3: Capture the brain-api FQDN** (`az containerapp show ... --query properties.configuration.ingress.fqdn`).

---

## Task 7 [CTRL]: Key Vault secrets + RBAC, then deploy web container app

- [ ] **Step 1: Put secrets in Key Vault** (`cbrain-lokesh-kv`): `redis-key`, `cosmos-gremlin-key`, `admin-api-key` (a strong value). `az keyvault secret set --vault-name cbrain-lokesh-kv -n redis-key --value <...>` etc. Ensure the MI has `Key Vault Secrets User` on the vault (granted in Phase 1b for the user; add for the MI: `az role assignment create --assignee <mi-principal> --role "Key Vault Secrets User" --scope <kv-id>`). Also grant the MI the AI Search/OpenAI/Cosmos/ADX data-plane roles it needs (Search Index Data Reader, Cognitive Services OpenAI User, Cosmos DB data role, ADX database viewer/ingestor).

- [ ] **Step 2: Build + push web image** — first set `NEXT_PUBLIC_API_BASE_URL` to the brain-api FQDN and `NEXT_PUBLIC_DEBUG_AUTH=` empty (prod uses Easy Auth, not the debug header). Bake via build args or a prod `.env.production`. Then:

```bash
az acr build -r cbrainlokeshacr -t substrateos-web:v1 ./web
```

- [ ] **Step 3: Create the web container app** (external ingress on 3000, MI for ACR pull):

```bash
az containerapp create -g rg-company-brain-dev -n substrateos-web \
  --environment cbrain-lokesh-capp-env \
  --image cbrainlokeshacr.azurecr.io/substrateos-web:v1 \
  --registry-server cbrainlokeshacr.azurecr.io --registry-identity "$MI" \
  --user-assigned "$MI" --ingress external --target-port 3000 --min-replicas 0 --max-replicas 3
```

- [ ] **Step 4: Set brain-api CORS** to the web FQDN: `az containerapp update -n brain-api ... --set-env-vars CORS_ALLOW_ORIGINS=https://<web-fqdn>`. Capture both FQDNs.

---

## Task 8 [CTRL + USER]: Enable Easy Auth + Entra app + OBO consent

- [ ] **Step 1 [CTRL]: Enable Easy Auth on the web app** (auto-creates the Entra app registration):

```bash
az containerapp auth microsoft set -g rg-company-brain-dev -n substrateos-web \
  --client-id "" --yes   # --yes lets the platform create+configure the AAD app
az containerapp auth update -g rg-company-brain-dev -n substrateos-web \
  --unauthenticated-client-action RedirectToLoginPage
```

(If `--yes` auto-registration isn't permitted by tenant policy, this becomes a [USER] step: register an app in the portal, set the redirect URI to `https://<web-fqdn>/.auth/login/aad/callback`, and pass its client id.)

- [ ] **Step 2 [CTRL]: Enable Easy Auth on brain-api** too (so direct API calls are authenticated and inject `X-MS-CLIENT-PRINCIPAL`), OR set brain-api ingress to accept the web's forwarded identity. Simplest: enable Easy Auth on brain-api with the SAME Entra app, `--unauthenticated-client-action Return401` (APIs return 401, not redirect).

- [ ] **Step 3 [USER]: Admin consent + OBO scopes** in the Entra portal for the auto-created app:
  - Grant admin consent for the app's sign-in (`User.Read` is default).
  - For OBO Live Fetch: add **delegated** `Files.Read.All` + `Sites.Read.All`, create a client secret (store in Key Vault as `azure-api-client-secret`), and grant admin consent. Provide the client id + secret to brain-api (env `AZURE_API_CLIENT_ID` + KV secret).
  - This is the one unavoidable human portal step.

- [ ] **Step 4 [CTRL]: Flip OBO on** once consent is in: `az containerapp update -n brain-api ... --set-env-vars LIVE_FETCH_OBO_ENABLED=true` and ensure the web's Easy Auth requests the Graph scopes (`az containerapp auth ... --scopes "openid profile Files.Read.All Sites.Read.All"`) so the token store has them for OBO.

---

## Task 9 [VERIFY + USER]: End-to-end production check

- [ ] **Step 1:** Open `https://<web-fqdn>` → redirected to Microsoft sign-in → after login, the SubstrateOS chat loads.
- [ ] **Step 2:** Ask "what is our PTO policy?" → grounded answer + citations + right-rail signals (now for the *real* signed-in user; people/activity reflect that user's graph/activity).
- [ ] **Step 3:** A freshness query ("what files changed recently") now returns the user's own Graph results via OBO (if their tenant has content).
- [ ] **Step 4:** Confirm prod safety: `curl https://<brain-api-fqdn>/query` without auth → 401 (Easy Auth) or 401 from resolver; the debug header is ignored (`ENABLE_DEBUG_AUTH=false`). Admin endpoints require the Key Vault admin key.
- [ ] **Step 5:** Update `README.md` with the production URLs + "auth via Entra Easy Auth; debug-auth disabled in prod". Commit. Tag `prod-deploy-v1`.

---

## Self-Review

**Coverage:** Easy Auth identity (T1), KV secrets (T2), prod CORS + debug-off (T3, T6 env), OBO (T4 + T8), images/deploy (T5–T7), Entra/consent (T8 USER), verify (T9). Matches the chosen scope (Easy Auth + both Container Apps + secrets→KV + OBO; debug-off & CORS-lock as mandatory baseline).

**Risks / honest flags:**
- **Easy Auth auto app-registration** (`--yes`) may be blocked by tenant policy → falls back to a [USER] portal app-reg (same blocker family as before, but Easy Auth's redirect-URI flow is simpler than the SPA "My APIs" scope dance).
- **OBO needs a client secret + delegated Graph admin consent** — the one hard human step; until done, `LIVE_FETCH_OBO_ENABLED=false` and Live Fetch stays single-identity/fail-closed (no regression).
- **Cross-app identity:** if direct browser→brain-api calls are used, brain-api needs its own Easy Auth (T8 Step 2) and CORS with credentials; alternative is routing API calls through the web app's server. Decide at T6/T8 based on what works; the code (T1) handles the `X-MS-CLIENT-PRINCIPAL` header either way.
- **Deploy spends real money** and several steps are irreversible-ish (public ingress) — controller pauses before T6 for explicit go.

**Out of scope:** APIM gateway (deferred per the choice), custom domain/TLS cert (Container Apps gives a default HTTPS FQDN), per-tenant index, OpenTelemetry, dark theme.

---

## Execution order

CODE first (T1–T5: subagent-driven, local, verifiable, reversible) → **pause for go-ahead** → CTRL deploy (T6–T7, costs money) → CTRL+USER auth (T8) → VERIFY (T9). The controller runs CTRL tasks via `az`; USER does the Entra consent.
