# Context API + MCP Server + Connect Panels — Design Spec

**Date:** 2026-06-02
**Status:** Approved (brainstorming) — UI mockup `mockups/connect-panels.html`

## Goal

Open the intelligence layer to external apps and AI assistants:
1. **Personal Access Tokens (PATs)** — per-user, revocable tokens for programmatic auth.
2. **Context API** — a documented, PAT-authed surface (`/context`, plus PAT-accessible `/query` + `/search`) that returns grounded, ACL-scoped company context.
3. **MCP server** — a hosted remote (Streamable HTTP) `/mcp` endpoint exposing `ask_company_brain` + `search_company_brain` tools.
4. **Connect panels** — the topbar `Web · Teams · Slack · API · MCP` chips become clickable → a tabbed modal showing how to use each surface (token management + copy-paste snippets for API & MCP; honest "coming soon" for Slack/Teams).

Everything is **ACL-scoped to the token owner**, reusing the existing retrieval/answer/orchestrator stack and the pilot-tenant mapping.

## Non-Goals
- Real Slack/Teams bots (panels are informational placeholders).
- OAuth/3-legged flows for the MCP server (PAT header auth is sufficient for the pilot).
- Token scopes/expiry beyond create + revoke (no per-scope grants in v1).

## Architecture

```
Browser (Easy Auth)  ──► POST/GET/DELETE /tokens         (manage your own PATs)
External app/agent   ──► POST /context|/query|/search    (Authorization: Bearer sbx_live_…)
AI assistant (MCP)   ──► /mcp  (Streamable HTTP)          (Authorization: Bearer sbx_live_…)
                              │
                resolve_user(PAT → TokenStore → User → pilot-tenant map)
                              │
                  orchestrator / search_service (ACL-scoped, unchanged)
```

## Backend

### TokenStore — `app/tokens/store.py`
Modeled on `CosmosConnectionStore`: vertices `label:cbrain_token` in the **existing people graph** (injected `graph=app.state.people_graph`; partition key `tenant_id`; no new container/secret). Falls back to a Redis-backed store when Cosmos is unconfigured (and that no-ops without Redis) — but in India it's Cosmos. Best-effort reads (degrade to []/None); writes log on failure.

Each token vertex: `token_id` (uuid), `tenant_id`, `user_id`, `name`, `hash` (sha256 of the plaintext), `prefix` (first 12 chars, for display masking), `created_at`, `last_used_at`.

- `create(*, user, name) -> (TokenMeta, plaintext)`: generate `sbx_live_<32 url-safe bytes>`, store its sha256, return the plaintext **once**.
- `list(*, user) -> list[TokenMeta]`: the user's tokens (masked: `sbx_live_••••<last4>`), recency-ordered.
- `revoke(*, user, token_id) -> bool`: delete the vertex (scoped to user_id + tenant_id).
- `resolve(plaintext) -> User | None`: sha256 the input, look up the vertex; if found, build a `User(user_id, tenant_id, …, group_ids=set())`, best-effort bump `last_used_at`. Returns None on miss/error. **The lookup is scoped only by hash** (the token is the secret), then the stored user_id/tenant_id define identity.

### Domain — `app/domain/token.py`
```python
class TokenMeta(BaseModel):
    token_id: str
    name: str
    masked: str          # sbx_live_••••a210
    created_at: datetime
    last_used_at: datetime | None = None

class TokenCreated(BaseModel):
    token: str           # plaintext, shown once
    meta: TokenMeta
```

### resolve_user PAT support — `app/api/_auth_resolve.py`
`resolve_user` gains an optional `token_store=None`. New precedence: **Easy Auth header → PAT (bearer starts with `sbx_`) → Entra JWT bearer → debug**. The PAT branch: `user = await token_store.resolve(token)`; 401 if None; then `_apply_pilot_tenant(user)`. PAT identity flows through the same pilot mapping, so its ACL matches the corpus.

### `/tokens` endpoints — `app/api/tokens.py` (Easy-Auth / browser-bearer only; NOT PAT-authed)
- `POST /tokens {name}` → `TokenCreated` (plaintext once).
- `GET /tokens` → `list[TokenMeta]`.
- `DELETE /tokens/{token_id}` → `{revoked: bool}`.
Auth via `resolve_user` **without** `token_store` (so a PAT cannot mint/list/revoke tokens — only an interactive session can).

### Context API — `app/api/context.py`
- `POST /context` (PAT **or** Easy Auth bearer via `resolve_user(token_store=…)`). Body `{query, top?=8}`. Returns:
```python
class ContextHit(BaseModel):
    doc_id: str; title: str; source_url: str; source: str
    snippet: str; score: float; signals: dict[str, float]
class ContextResponse(BaseModel):
    query: str; hits: list[ContextHit]
```
Implemented via `orchestrator.retrieve_ranked(QueryRequest(query=…, k=top), user=user)` → map `RankedResult` (`candidate.chunk` + `final_score` + `signal_breakdown`). Snippet = first ~240 chars of chunk content.
- The existing **`/query`** and **`/search`** routes are extended to accept PATs by threading `token_store` into their `resolve_user` calls (one-line change each). Together these three are the documented "Context API".

### MCP server — `app/mcp/server.py`
A **FastMCP** (official `mcp` SDK) **Streamable HTTP** app mounted at `/mcp` on the FastAPI app. Two tools:
- `ask_company_brain(query: str) -> str` → `orchestrator.answer(...)` text + a compact sources list.
- `search_company_brain(query: str) -> str` → `search_service.result(...)` titles + snippets + source_urls (JSON-ish text).

**Per-request auth:** an ASGI middleware wrapping the mounted MCP app reads `Authorization: Bearer sbx_…`, resolves the user via `TokenStore` + pilot mapping, and stashes it in a `ContextVar`; the tools read that user (401 if absent). The tools reach the orchestrator/search_service/token_store via module-level singletons bound at lifespan startup (`mcp_bind(orchestrator=…, search=…, token_store=…)`). MCP errors degrade to a tool-level error string, never a 500.

### Wiring — `app/main.py`, `app/deps.py`, `app/config.py`
- Lifespan: build `app.state.token_store` (Cosmos via `people_graph` when configured, else Redis store); `mcp_bind(...)`; `app.mount("/mcp", mcp_app)`.
- `deps.get_token_store` (tolerant `getattr`).
- Config: `token_prefix: str = "sbx_live_"`, `mcp_enabled: bool = True`, `public_base_url: str | None` (the brain-api URL, surfaced to the UI for snippets — else the UI derives it from `NEXT_PUBLIC_API_BASE_URL`).
- `pyproject`: add `mcp>=1.2`.

### ACL / security
- PAT plaintext is shown once; only the sha256 is stored. Lookups are constant-ish (hash match).
- Every Context API / MCP call resolves to the token owner and runs through the **same ACL-trimmed retrieval** as the browser — a token can't see more than its user.
- Token management requires an interactive (Easy Auth) session — a leaked PAT can read context but cannot mint or revoke tokens.
- CORS unchanged; PAT calls are server-to-server (no browser origin).

## Frontend

### `web/lib/api.ts`
`TokenMeta`, `TokenCreated` types; `listTokens()`, `createToken(name)`, `revokeToken(id)` (Easy-Auth path, via `authedFetch`). A small helper `apiBaseUrl()` (returns `NEXT_PUBLIC_API_BASE_URL`) for snippet rendering.

### `web/components/Chat.tsx` — Connect modal
- Make the topbar `.surfaces .chip` elements **buttons**; clicking sets `connectSurface` state → opens `<ConnectModal surface=… onClose=…/>`.
- **`ConnectModal`**: a centered modal (backdrop) with **surface tabs** (Web · Teams · Slack · API · MCP). Tab content:
  - **API:** base URL, endpoint list (`/context`,`/query`,`/search`), token manager (`listTokens` → rows with Revoke; "Create token" → `createToken` → show plaintext once with copy + warning), and a curl snippet.
  - **MCP:** `/mcp` URL, a generated `mcp.json` block (with the user's most recent token id hint / placeholder), the two tools, Create-token reused.
  - **Slack / Teams / Web:** "coming soon" copy + a disabled "Notify me".
- Copy-to-clipboard on code blocks. Styles ported from `mockups/connect-panels.html` into `globals.css` (`.cmodal`, `.m-*`, `.code`, `.endpoint`, `.tok-row`, `.tool`, etc.).

## Error handling / degradation
- TokenStore unavailable → `/tokens` returns []/best-effort, Context API/MCP PAT auth → 401 (can't resolve). Never 500.
- MCP tool failures → returned as an error string to the assistant.
- Context API on retrieval failure → empty `hits`.

## Testing (TDD)
- `TokenStore` (fake gremlin, mirroring `test_resolve_people`): create stores a hash + returns plaintext once; list masks; revoke is user+tenant scoped; resolve matches by hash, bumps last_used, returns None on miss.
- `resolve_user`: PAT bearer (`sbx_…`) → TokenStore.resolve → pilot-mapped user; non-PAT bearer still hits JWT; PAT precedence over JWT.
- `/tokens` endpoints: require interactive auth; create/list/revoke; a PAT bearer is rejected for `/tokens` management.
- `/context`: PAT-auth → ranked hits (mock orchestrator); 401 without auth; empty on retrieval failure.
- MCP tools: `ask`/`search` call the right collaborator with the resolved user; missing user → error.
- Frontend smoke: chip opens modal on the right tab; create token shows plaintext once; revoke removes a row; snippets render the base URL.

## Out of scope / deferred
Token scopes/expiry/rotation, MCP OAuth, real Slack/Teams bots, rate limiting, usage analytics per token.
