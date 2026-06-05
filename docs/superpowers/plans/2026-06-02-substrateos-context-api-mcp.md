# Context API + MCP Server + Connect Panels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the intelligence layer to external apps and AI assistants via per-user Personal Access Tokens (PATs), a documented Context API, and a remote MCP server — surfaced to users through clickable "Connect" panels in the topbar.

**Architecture:** A `TokenStore` (Cosmos Gremlin vertices in the existing people graph, mirroring `CosmosConnectionStore`) mints/stores/resolves sha256-hashed PATs. `resolve_user` gains a PAT branch so `/context`, `/query`, and `/search` accept `Authorization: Bearer sbx_live_…`. A FastMCP Streamable-HTTP app mounted at `/mcp` exposes `ask_company_brain` + `search_company_brain`, authenticating each request via the same `TokenStore` through ASGI middleware → `ContextVar`. Everything resolves to the token owner and runs through the unchanged ACL-scoped orchestrator + pilot-tenant mapping. The frontend turns the `Web · Teams · Slack · API · MCP` topbar chips into buttons opening a tabbed `ConnectModal` (token manager + copy-paste snippets).

**Tech Stack:** Python 3.12 / FastAPI / Pydantic / gremlinpython / `mcp` SDK (FastMCP) / pytest-asyncio (backend); Next.js 14 / React 18 / TypeScript (frontend).

---

## File Structure

**Backend (`brain-api/`):**
- Create `app/domain/token.py` — `TokenMeta`, `TokenCreated` Pydantic models.
- Create `app/tokens/__init__.py` + `app/tokens/store.py` — `CosmosTokenStore` (real) + `NullTokenStore` (no-op fallback).
- Create `app/api/tokens.py` — `/tokens` CRUD router (interactive-auth only).
- Create `app/api/context.py` — `POST /context` router (PAT or bearer).
- Create `app/mcp/__init__.py` + `app/mcp/server.py` — FastMCP app, `mcp_bind`, ASGI auth middleware, tool logic.
- Modify `app/api/_auth_resolve.py` — add `token_store` param + PAT branch.
- Modify `app/api/query.py` — thread `token_store`; don't treat a PAT as an OBO token.
- Modify `app/api/search.py` — thread `token_store`.
- Modify `app/deps.py` — `get_token_store`.
- Modify `app/config.py` — `token_prefix`, `mcp_enabled`, `public_base_url`.
- Modify `app/main.py` — build `token_store` in lifespan, `mcp_bind`, mount `/mcp`, register routers, run MCP session-manager lifespan.
- Modify `pyproject.toml` — add `mcp>=1.2`.
- Tests: `tests/test_token_store.py`, `tests/test_auth_resolve_pat.py`, `tests/test_tokens_api.py`, `tests/test_context_api.py`, `tests/test_mcp_tools.py`.

**Frontend (`web/`):**
- Modify `lib/api.ts` — `TokenMeta`/`TokenCreated` types, `apiBaseUrl()`, `listTokens`/`createToken`/`revokeToken`.
- Modify `components/Chat.tsx` — chips → buttons, `connectSurface` state, `ConnectModal` component.
- Modify `app/globals.css` — `.cmodal`, `.m-*`, `.code`, `.endpoint`, `.tok-row`, `.tool` styles (ported from `mockups/connect-panels.html`).

---

## Task 1: Token domain models

**Files:**
- Create: `brain-api/app/domain/token.py`
- Test: `brain-api/tests/test_token_store.py` (created here, extended in Task 2)

- [ ] **Step 1: Write the failing test**

Create `brain-api/tests/test_token_store.py`:

```python
from datetime import datetime, timezone

from app.domain.token import TokenCreated, TokenMeta


def test_token_meta_and_created_shapes() -> None:
    meta = TokenMeta(
        token_id="tk1",
        name="laptop",
        masked="sbx_live_••••a210",
        created_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    assert meta.last_used_at is None
    created = TokenCreated(token="sbx_live_secret", meta=meta)
    assert created.token == "sbx_live_secret"
    assert created.meta.token_id == "tk1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain-api && uv run pytest tests/test_token_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.token'`.

- [ ] **Step 3: Write the implementation**

Create `brain-api/app/domain/token.py`:

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TokenMeta(BaseModel):
    token_id: str
    name: str
    masked: str          # e.g. sbx_live_••••a210
    created_at: datetime
    last_used_at: datetime | None = None


class TokenCreated(BaseModel):
    token: str           # plaintext, shown to the caller exactly once
    meta: TokenMeta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain-api && uv run pytest tests/test_token_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add brain-api/app/domain/token.py brain-api/tests/test_token_store.py
git commit -m "feat(tokens): TokenMeta + TokenCreated domain models"
```

---

## Task 2: TokenStore (Cosmos Gremlin + no-op fallback)

**Files:**
- Create: `brain-api/app/tokens/__init__.py`, `brain-api/app/tokens/store.py`
- Test: `brain-api/tests/test_token_store.py` (extend)

The store mirrors `app/connectors/cosmos_store.py`: vertices `cbrain_token` in the injected people graph, properties `tid`(=token_id, key), `tenant_id`(partition), `user_id`, `hash`(sha256), and `data`(JSON record). Best-effort reads (degrade to `[]`/`None`); writes log on failure. The shared Gremlin client is NOT closed here.

- [ ] **Step 1: Write the failing tests (append to `tests/test_token_store.py`)**

```python
import pytest

from app.tokens.store import CosmosTokenStore, NullTokenStore, _hash


class FakeGraph:
    """In-memory stand-in for PeopleGraphClient.submit covering the four query
    shapes CosmosTokenStore issues (upsert / list-by-user / resolve-by-hash / drop)."""

    def __init__(self) -> None:
        self.rows: list[dict] = []  # each: {tid, tenant_id, user_id, hash, data}

    async def submit(self, query: str, bindings=None):
        b = bindings or {}
        if "addV('cbrain_token')" in query or ".property('data'" in query:
            # upsert: replace any row with same tid, else insert
            self.rows = [r for r in self.rows if r["tid"] != b["k"]]
            self.rows.append(
                {"tid": b["k"], "tenant_id": b["tid"], "user_id": b["uid"],
                 "hash": b["h"], "data": b["d"]}
            )
            return []
        if ".drop()" in query:
            self.rows = [
                r for r in self.rows
                if not (r["tid"] == b["k"] and r["tenant_id"] == b["tid"]
                        and r["user_id"] == b["uid"])
            ]
            return []
        if "has('hash'" in query:
            return [r["data"] for r in self.rows if r["hash"] == b["h"]]
        if "has('user_id'" in query:  # list
            return [r["data"] for r in self.rows
                    if r["tenant_id"] == b["tid"] and r["user_id"] == b["uid"]]
        return []


def _user(uid="u-demo", tid="t-eval"):
    from app.domain.identity import User
    return User(user_id=uid, tenant_id=tid, email=f"{uid}@x", display_name=uid, group_ids=set())


@pytest.mark.asyncio
async def test_create_returns_plaintext_once_and_stores_hash() -> None:
    store = CosmosTokenStore(graph=FakeGraph())
    meta, plaintext = await store.create(user=_user(), name="laptop")
    assert plaintext.startswith("sbx_live_")
    assert meta.name == "laptop"
    assert meta.masked.startswith("sbx_live_••••")
    assert plaintext not in meta.masked  # plaintext never embedded in metadata


@pytest.mark.asyncio
async def test_list_masks_and_is_user_scoped() -> None:
    g = FakeGraph()
    store = CosmosTokenStore(graph=g)
    await store.create(user=_user("u1"), name="a")
    await store.create(user=_user("u2"), name="b")
    mine = await store.list(user=_user("u1"))
    assert [m.name for m in mine] == ["a"]
    assert all("•" in m.masked for m in mine)


@pytest.mark.asyncio
async def test_resolve_matches_by_hash_and_misses_return_none() -> None:
    store = CosmosTokenStore(graph=FakeGraph())
    _, plaintext = await store.create(user=_user("u9", "t-eval"), name="cli")
    resolved = await store.resolve(plaintext)
    assert resolved is not None
    assert resolved.user_id == "u9"
    assert resolved.tenant_id == "t-eval"
    assert await store.resolve("sbx_live_wrong") is None


@pytest.mark.asyncio
async def test_revoke_is_user_and_tenant_scoped() -> None:
    g = FakeGraph()
    store = CosmosTokenStore(graph=g)
    meta, _ = await store.create(user=_user("u1"), name="a")
    assert await store.revoke(user=_user("u1"), token_id=meta.token_id) is True
    assert await store.list(user=_user("u1")) == []


@pytest.mark.asyncio
async def test_null_store_noops() -> None:
    store = NullTokenStore()
    assert await store.list(user=_user()) == []
    assert await store.resolve("sbx_live_x") is None
    assert await store.revoke(user=_user(), token_id="x") is False
    meta, plaintext = await store.create(user=_user(), name="x")
    assert plaintext == ""  # cannot mint without a backing store


def test_hash_is_sha256_hex() -> None:
    assert len(_hash("abc")) == 64
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain-api && uv run pytest tests/test_token_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tokens'`.

- [ ] **Step 3: Write the implementation**

Create `brain-api/app/tokens/__init__.py` (empty file).

Create `brain-api/app/tokens/store.py`:

```python
"""Personal Access Tokens stored on Cosmos DB (Gremlin), reusing the people graph.

Mirrors CosmosConnectionStore: vertices `cbrain_token` carry indexed props
`tid`(=token_id, vertex key), `tenant_id`(partition), `user_id`, `hash`(sha256
of the plaintext) plus a JSON `data` blob. The plaintext is shown once at
creation and never stored. Reads degrade to []/None; writes log on failure. The
shared Gremlin client is owned elsewhere (app.state.people_graph) — never closed
here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.domain.identity import User
from app.domain.token import TokenMeta

logger = logging.getLogger(__name__)

_LABEL = "cbrain_token"


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta_from_data(d: dict) -> TokenMeta:
    return TokenMeta(
        token_id=d["token_id"],
        name=d["name"],
        masked=d["masked"],
        created_at=d["created_at"],
        last_used_at=d.get("last_used_at"),
    )


class CosmosTokenStore:
    def __init__(self, graph) -> None:
        # `graph` exposes async submit(query, bindings) -> list (PeopleGraphClient).
        self._g = graph

    async def aclose(self) -> None:
        return  # shared client owned elsewhere

    async def _upsert(self, *, token_id: str, tenant: str, user_id: str,
                      token_hash: str, data: str) -> None:
        try:
            await self._g.submit(
                f"g.V().has('{_LABEL}','tid', k).has('tenant_id', tid).fold()"
                f".coalesce(unfold(),"
                f" addV('{_LABEL}').property('tid', k).property('tenant_id', tid))"
                f".property('user_id', uid).property('hash', h).property('data', d)",
                {"k": token_id, "tid": tenant, "uid": user_id, "h": token_hash, "d": data},
            )
        except Exception as e:  # noqa: BLE001 — token writes are best-effort
            logger.warning("cosmos token upsert failed: %s", e)

    async def create(self, *, user: User, name: str) -> tuple[TokenMeta, str]:
        prefix = get_settings().token_prefix  # "sbx_live_"
        plaintext = f"{prefix}{secrets.token_urlsafe(32)}"
        token_id = uuid.uuid4().hex
        masked = f"{prefix}••••{plaintext[-4:]}"
        record = {
            "token_id": token_id, "tenant_id": user.tenant_id, "user_id": user.user_id,
            "email": user.email, "display_name": user.display_name,
            "name": name, "masked": masked, "created_at": _now(), "last_used_at": None,
        }
        await self._upsert(
            token_id=token_id, tenant=user.tenant_id, user_id=user.user_id,
            token_hash=_hash(plaintext), data=json.dumps(record),
        )
        return _meta_from_data(record), plaintext

    async def list(self, *, user: User) -> list[TokenMeta]:
        try:
            rows = await self._g.submit(
                f"g.V().has('{_LABEL}','tenant_id', tid).has('user_id', uid).values('data')",
                {"tid": user.tenant_id, "uid": user.user_id},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos token list failed: %s", e)
            return []
        out: list[TokenMeta] = []
        for data in rows:
            try:
                out.append(_meta_from_data(json.loads(data)))
            except Exception:  # noqa: BLE001 — skip corrupt rows
                continue
        out.sort(key=lambda m: m.created_at, reverse=True)
        return out

    async def revoke(self, *, user: User, token_id: str) -> bool:
        try:
            await self._g.submit(
                f"g.V().has('{_LABEL}','tid', k).has('tenant_id', tid)"
                f".has('user_id', uid).drop()",
                {"k": token_id, "tid": user.tenant_id, "uid": user.user_id},
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos token revoke failed: %s", e)
            return False

    async def resolve(self, plaintext: str) -> User | None:
        try:
            rows = await self._g.submit(
                f"g.V().has('{_LABEL}','hash', h).values('data')",
                {"h": _hash(plaintext)},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos token resolve failed: %s", e)
            return None
        if not rows:
            return None
        try:
            d = json.loads(rows[0])
        except Exception:  # noqa: BLE001
            return None
        # Best-effort last_used_at bump (re-upsert with refreshed record).
        d["last_used_at"] = _now()
        await self._upsert(
            token_id=d["token_id"], tenant=d["tenant_id"], user_id=d["user_id"],
            token_hash=_hash(plaintext), data=json.dumps(d),
        )
        return User(
            user_id=d["user_id"], tenant_id=d["tenant_id"],
            email=d.get("email", f"{d['user_id']}@token"),
            display_name=d.get("display_name", d["user_id"]),
            group_ids=set(),
        )


class NullTokenStore:
    """Fallback when Cosmos is unconfigured — cannot mint or resolve tokens."""

    async def aclose(self) -> None:
        return

    async def create(self, *, user: User, name: str) -> tuple[TokenMeta, str]:
        meta = TokenMeta(token_id="", name=name, masked="(unavailable)", created_at=datetime.now(timezone.utc))
        return meta, ""

    async def list(self, *, user: User) -> list[TokenMeta]:
        return []

    async def revoke(self, *, user: User, token_id: str) -> bool:
        return False

    async def resolve(self, plaintext: str) -> User | None:
        return None
```

- [ ] **Step 4: Add `token_prefix` to settings so `create` resolves it**

This is needed now for the tests. In `brain-api/app/config.py`, add after the `cors_allow_origins` line (around line 74):

```python
    # Programmatic access (Context API + MCP)
    token_prefix: str = "sbx_live_"
    mcp_enabled: bool = True
    public_base_url: str | None = None  # brain-api URL surfaced to the UI for snippets
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd brain-api && uv run pytest tests/test_token_store.py -v`
Expected: PASS (all 6 new + the 1 from Task 1).

- [ ] **Step 6: Commit**

```bash
git add brain-api/app/tokens/ brain-api/app/config.py brain-api/tests/test_token_store.py
git commit -m "feat(tokens): CosmosTokenStore + NullTokenStore + config knobs"
```

---

## Task 3: `resolve_user` PAT branch

**Files:**
- Modify: `brain-api/app/api/_auth_resolve.py`
- Test: `brain-api/tests/test_auth_resolve_pat.py`

Precedence becomes **Easy Auth → PAT (`sbx_` bearer, when a token_store is supplied) → Entra JWT bearer → debug**. `token_store` defaults to `None`, so existing callers (`/feedback`, `/history`, `/conversations`, `/discover`) keep JWT/Easy-Auth-only behavior unchanged.

- [ ] **Step 1: Write the failing test**

Create `brain-api/tests/test_auth_resolve_pat.py`:

```python
import pytest

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.domain.identity import User


class FakeTokenStore:
    def __init__(self, user):
        self._user = user
    async def resolve(self, plaintext):
        return self._user if plaintext == "sbx_live_good" else None


def _pilot_on():
    s = get_settings()
    s.pilot_single_tenant = True
    s.brain_tenant_id = "t-eval"


@pytest.mark.asyncio
async def test_pat_bearer_resolves_via_token_store_and_pilot_maps(monkeypatch) -> None:
    _pilot_on()
    raw = User(user_id="u9", tenant_id="aad-guid", email="u9@x", display_name="U9", group_ids=set())
    store = FakeTokenStore(raw)
    user = await resolve_user(
        easy_auth=None, authorization="Bearer sbx_live_good",
        debug_header=None, token_store=store,
    )
    assert user.user_id == "u9"
    assert user.tenant_id == "t-eval"                      # pilot remap applied
    assert "t-eval:everyone" in user.group_ids


@pytest.mark.asyncio
async def test_bad_pat_is_401() -> None:
    from fastapi import HTTPException
    store = FakeTokenStore(None)
    with pytest.raises(HTTPException) as ei:
        await resolve_user(easy_auth=None, authorization="Bearer sbx_live_bad",
                           debug_header=None, token_store=store)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_non_pat_bearer_skips_token_store(monkeypatch) -> None:
    # A non-sbx_ bearer must NOT hit the token store; it falls through to JWT.
    seen = {"called": False}

    class Spy(FakeTokenStore):
        async def resolve(self, plaintext):
            seen["called"] = True
            return None

    async def fake_jwt(token):
        return User(user_id="jwtuser", tenant_id="t-eval", email="j@x",
                    display_name="J", group_ids=set())

    monkeypatch.setattr("app.api._auth_resolve.user_from_bearer", fake_jwt)
    user = await resolve_user(easy_auth=None, authorization="Bearer eyJhbGci...",
                              debug_header=None, token_store=Spy(None))
    assert user.user_id == "jwtuser"
    assert seen["called"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain-api && uv run pytest tests/test_auth_resolve_pat.py -v`
Expected: FAIL — `resolve_user()` got an unexpected keyword argument `token_store`.

- [ ] **Step 3: Edit `app/api/_auth_resolve.py`**

Replace the `resolve_user` function (lines 37–52) with:

```python
async def resolve_user(
    *,
    easy_auth: str | None,
    authorization: str | None,
    debug_header: str | None,
    token_store=None,
) -> User:
    if easy_auth:  # Container Apps Easy Auth (production)
        try:
            return _apply_pilot_tenant(user_from_easy_auth_header(easy_auth))
        except InvalidToken as e:
            raise HTTPException(status_code=401, detail=f"invalid principal: {e}") from e
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        # Personal Access Token: only when a token_store is supplied (Context API,
        # /query, /search). A leaked PAT therefore can't authenticate token
        # management, which never passes a token_store.
        if token_store is not None and token.startswith(get_settings().token_prefix):
            user = await token_store.resolve(token)
            if user is None:
                raise HTTPException(status_code=401, detail="invalid token")
            return _apply_pilot_tenant(user)
        try:
            return _apply_pilot_tenant(await user_from_bearer(token))
        except InvalidToken as e:
            raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e
    if get_settings().enable_debug_auth and debug_header:
        return _debug_user(debug_header)
    raise HTTPException(status_code=401, detail="auth required")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain-api && uv run pytest tests/test_auth_resolve_pat.py tests/test_pilot_tenant.py tests/test_auth.py -v`
Expected: PASS (new tests pass; existing auth tests still pass).

- [ ] **Step 5: Commit**

```bash
git add brain-api/app/api/_auth_resolve.py brain-api/tests/test_auth_resolve_pat.py
git commit -m "feat(auth): PAT bearer branch in resolve_user (token_store opt-in)"
```

---

## Task 4: `get_token_store` dependency

**Files:**
- Modify: `brain-api/app/deps.py`

- [ ] **Step 1: Add the dependency**

In `brain-api/app/deps.py`, append after `get_conversation_store` (after line 61):

```python
def get_token_store(request: Request):
    return getattr(request.app.state, "token_store", None)
```

- [ ] **Step 2: Verify import sanity**

Run: `cd brain-api && uv run python -c "from app.deps import get_token_store; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add brain-api/app/deps.py
git commit -m "feat(deps): get_token_store dependency"
```

---

## Task 5: `/tokens` CRUD endpoints (interactive-auth only)

**Files:**
- Create: `brain-api/app/api/tokens.py`
- Test: `brain-api/tests/test_tokens_api.py`

These routes call `resolve_user` **without** `token_store`, so a PAT bearer cannot mint/list/revoke — only an Easy-Auth/JWT session can.

- [ ] **Step 1: Write the failing test**

Create `brain-api/tests/test_tokens_api.py`:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.tokens import router as tokens_router
from app.config import get_settings
from app.domain.token import TokenCreated, TokenMeta


class FakeStore:
    def __init__(self):
        self._items: dict[str, TokenMeta] = {}
        self.created_plaintext = "sbx_live_secret"
    async def create(self, *, user, name):
        meta = TokenMeta(token_id="tk1", name=name, masked="sbx_live_••••cret",
                         created_at="2026-06-02T00:00:00+00:00")
        self._items["tk1"] = meta
        return meta, self.created_plaintext
    async def list(self, *, user):
        return list(self._items.values())
    async def revoke(self, *, user, token_id):
        return self._items.pop(token_id, None) is not None


@pytest.fixture
def client():
    get_settings().enable_debug_auth = True
    app = FastAPI()
    store = FakeStore()
    app.state.token_store = store
    app.include_router(tokens_router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://t"), store


_AUTH = {"x-debug-bypass-auth": "t-eval,u-demo,t-eval:everyone"}


@pytest.mark.asyncio
async def test_create_then_list_then_revoke(client) -> None:
    ac, _ = client
    async with ac:
        r = await ac.post("/tokens", json={"name": "laptop"}, headers=_AUTH)
        assert r.status_code == 200
        body = TokenCreated.model_validate(r.json())
        assert body.token == "sbx_live_secret"
        assert body.meta.name == "laptop"

        r = await ac.get("/tokens", headers=_AUTH)
        assert [m["name"] for m in r.json()] == ["laptop"]

        r = await ac.delete(f"/tokens/{body.meta.token_id}", headers=_AUTH)
        assert r.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_requires_auth(client) -> None:
    ac, _ = client
    async with ac:
        r = await ac.get("/tokens")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_pat_bearer_cannot_manage_tokens(client) -> None:
    # A PAT bearer reaches resolve_user WITHOUT a token_store here, so it is not a
    # valid principal for token management — falls through to JWT validation → 401.
    ac, _ = client
    async with ac:
        r = await ac.get("/tokens", headers={"Authorization": "Bearer sbx_live_anything"})
        assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain-api && uv run pytest tests/test_tokens_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.tokens'`.

- [ ] **Step 3: Write the implementation**

Create `brain-api/app/api/tokens.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.deps import get_token_store
from app.domain.token import TokenCreated, TokenMeta

router = APIRouter(tags=["tokens"])


class CreateTokenRequest(BaseModel):
    name: str = "token"


async def _interactive_user(authorization, x_debug_bypass_auth, x_ms_client_principal):
    # NOTE: no token_store passed — a PAT can never manage tokens.
    return await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )


@router.post("/tokens", response_model=TokenCreated)
async def create_token(
    body: CreateTokenRequest,
    store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> TokenCreated:
    user = await _interactive_user(authorization, x_debug_bypass_auth, x_ms_client_principal)
    if store is None:
        raise HTTPException(status_code=503, detail="token store unavailable")
    meta, plaintext = await store.create(user=user, name=body.name.strip() or "token")
    if not plaintext:
        raise HTTPException(status_code=503, detail="token store unavailable")
    return TokenCreated(token=plaintext, meta=meta)


@router.get("/tokens", response_model=list[TokenMeta])
async def list_tokens(
    store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[TokenMeta]:
    user = await _interactive_user(authorization, x_debug_bypass_auth, x_ms_client_principal)
    if store is None:
        return []
    return await store.list(user=user)


@router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> dict[str, bool]:
    user = await _interactive_user(authorization, x_debug_bypass_auth, x_ms_client_principal)
    if store is None:
        return {"revoked": False}
    return {"revoked": await store.revoke(user=user, token_id=token_id)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain-api && uv run pytest tests/test_tokens_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add brain-api/app/api/tokens.py brain-api/tests/test_tokens_api.py
git commit -m "feat(api): /tokens CRUD (interactive auth only)"
```

---

## Task 6: `POST /context` endpoint

**Files:**
- Create: `brain-api/app/api/context.py`
- Test: `brain-api/tests/test_context_api.py`

Returns ranked, ACL-scoped hits via `orchestrator.retrieve_ranked`. PAT **or** bearer auth (passes `token_store` to `resolve_user`).

- [ ] **Step 1: Write the failing test**

Create `brain-api/tests/test_context_api.py`:

```python
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.context import router as context_router
from app.config import get_settings
from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate, RankedResult


def _ranked():
    chunk = Chunk(
        chunk_id="c1", doc_id="d1", tenant_id="t-eval", source="sharepoint",
        source_url="https://x/d1", title="Travel Policy",
        content="Economy fares for flights under 6 hours. " * 10,
        acl_principals=["t-eval:everyone"],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc), chunk_index=0,
    )
    cand = Candidate(chunk=chunk)
    return [RankedResult(candidate=cand, final_score=0.91,
                         signal_breakdown={"content": 0.5, "people": 0.2}, rank=0)]


class FakeOrch:
    def __init__(self, ranked):
        self._ranked = ranked
        self.seen_user = None
    async def retrieve_ranked(self, request, *, user, user_token=None):
        self.seen_user = user
        return self._ranked


class FakePATStore:
    async def resolve(self, plaintext):
        if plaintext == "sbx_live_ok":
            return User(user_id="u9", tenant_id="t-eval", email="u9@x",
                        display_name="U9", group_ids=set())
        return None


@pytest.fixture
def app():
    get_settings().enable_debug_auth = True
    get_settings().pilot_single_tenant = False
    a = FastAPI()
    a.state.orchestrator = FakeOrch(_ranked())
    a.state.token_store = FakePATStore()
    a.include_router(context_router)
    return a


@pytest.mark.asyncio
async def test_context_with_pat_returns_hits(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/context", json={"query": "travel policy", "top": 5},
                          headers={"Authorization": "Bearer sbx_live_ok"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "travel policy"
    hit = body["hits"][0]
    assert hit["doc_id"] == "d1"
    assert hit["title"] == "Travel Policy"
    assert hit["source_url"] == "https://x/d1"
    assert len(hit["snippet"]) <= 240
    assert hit["score"] == pytest.approx(0.91)
    assert hit["signals"]["content"] == 0.5


@pytest.mark.asyncio
async def test_context_requires_auth(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/context", json={"query": "x"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_context_empty_on_no_results(app) -> None:
    app.state.orchestrator = FakeOrch([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/context", json={"query": "x"},
                          headers={"Authorization": "Bearer sbx_live_ok"})
    assert r.status_code == 200
    assert r.json()["hits"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain-api && uv run pytest tests/test_context_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.context'`.

- [ ] **Step 3: Write the implementation**

Create `brain-api/app/api/context.py`:

```python
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.deps import get_orchestrator, get_token_store
from app.domain.query import QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

logger = logging.getLogger(__name__)
router = APIRouter(tags=["context"])

_SNIPPET = 240


class ContextRequest(BaseModel):
    query: str
    top: int = 8


class ContextHit(BaseModel):
    doc_id: str
    title: str
    source_url: str
    source: str
    snippet: str
    score: float
    signals: dict[str, float]


class ContextResponse(BaseModel):
    query: str
    hits: list[ContextHit]


@router.post("/context", response_model=ContextResponse)
async def context(
    body: ContextRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> ContextResponse:
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth, token_store=store,
    )
    top = min(max(body.top, 1), 25)
    try:
        ranked = await orchestrator.retrieve_ranked(
            QueryRequest(query=body.query, k=top), user=user
        )
    except Exception as e:  # noqa: BLE001 — never 500 a programmatic surface
        logger.warning("context retrieval failed: %s", e)
        ranked = []
    hits = [
        ContextHit(
            doc_id=r.candidate.chunk.doc_id,
            title=r.candidate.chunk.title,
            source_url=r.candidate.chunk.source_url,
            source=r.candidate.chunk.source,
            snippet=r.candidate.chunk.content[:_SNIPPET],
            score=r.final_score,
            signals=r.signal_breakdown,
        )
        for r in ranked[:top]
    ]
    return ContextResponse(query=body.query, hits=hits)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain-api && uv run pytest tests/test_context_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add brain-api/app/api/context.py brain-api/tests/test_context_api.py
git commit -m "feat(api): POST /context — ranked ACL-scoped hits (PAT or bearer)"
```

---

## Task 7: Thread `token_store` into `/query` and `/search`

**Files:**
- Modify: `brain-api/app/api/query.py`
- Modify: `brain-api/app/api/search.py`

- [ ] **Step 1: Edit `app/api/query.py`**

Add the import (line 6 area) and the dependency + token_store wiring. Replace the function body's signature/auth block.

Change the imports block (lines 5-6) to:

```python
from app.api._auth_resolve import resolve_user
from app.deps import get_conversation_store, get_orchestrator, get_token_store
```

Replace the handler (lines 13-33) up to the `answer = ...` line with:

```python
@router.post("/query", response_model=Answer)
async def query(
    request: Request,
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    conversation_store=Depends(get_conversation_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Answer:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    bearer = (
        authorization.split(" ", 1)[1]
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    # A PAT is not an OBO token — don't forward it to Live Fetch.
    tok = bearer if bearer and not bearer.startswith(get_settings().token_prefix) else None
    answer = await orchestrator.answer(body, user=user, user_token=tok)
```

Add the `get_settings` import at the top of `app/api/query.py` (after the existing imports):

```python
from app.config import get_settings
```

- [ ] **Step 2: Edit `app/api/search.py`**

Change the import (line 9) to add `get_token_store`:

```python
from app.deps import get_search_service, get_token_store
```

Replace the handler signature + auth call (lines 24-35) with:

```python
@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    service=Depends(get_search_service),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> SearchResponse:
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth, token_store=token_store,
    )
```

- [ ] **Step 3: Run the existing query/search tests to confirm no regression**

Run: `cd brain-api && uv run pytest tests/test_query_e2e.py tests/test_search_api.py tests/test_query_conversation_logging.py -v`
Expected: PASS (these tests don't pass a token_store on app.state, so `get_token_store` returns `None` → unchanged behavior).

- [ ] **Step 4: Commit**

```bash
git add brain-api/app/api/query.py brain-api/app/api/search.py
git commit -m "feat(api): accept PATs on /query and /search (Context API surface)"
```

---

## Task 8: MCP server (FastMCP Streamable HTTP)

**Files:**
- Create: `brain-api/app/mcp/__init__.py`, `brain-api/app/mcp/server.py`
- Modify: `brain-api/pyproject.toml` (add `mcp>=1.2`)
- Test: `brain-api/tests/test_mcp_tools.py`

The tool *logic* lives in plain async functions (`_ask`, `_search`) so it is unit-testable without an MCP session. Thin `@mcp.tool()` wrappers read the per-request user from a `ContextVar` set by ASGI auth middleware.

- [ ] **Step 1: Add the dependency**

In `brain-api/pyproject.toml`, add `"mcp>=1.2",` to the `[project] dependencies` list. Then:

Run: `cd brain-api && uv add "mcp>=1.2"`
Expected: resolves and updates `uv.lock`.

- [ ] **Step 2: Write the failing test**

Create `brain-api/tests/test_mcp_tools.py`:

```python
from datetime import datetime, timezone

import pytest

from app.domain.identity import User
from app.domain.query import Answer, Citation
from app.domain.search import SearchHit, SearchResponse
from app.mcp.server import _ask, _search


def _user():
    return User(user_id="u9", tenant_id="t-eval", email="u9@x",
                display_name="U9", group_ids=set())


class FakeOrch:
    def __init__(self):
        self.seen = None
    async def answer(self, request, *, user, user_token=None):
        self.seen = user
        return Answer(
            text="Economy fares only.",
            citations=[Citation(doc_id="d1", chunk_id="c1", source_url="https://x/d1",
                                title="Travel Policy", snippet="...")],
            query_id="q1",
        )


class FakeSearch:
    async def result(self, *, user, query, top=10, skip=0, sources=None,
                     date_from=None, author_id=None):
        return SearchResponse(
            query=query,
            results=[SearchHit(doc_id="d1", title="Travel Policy", source="sharepoint",
                               source_url="https://x/d1", author_id=None,
                               modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                               snippet="Economy fares")],
            facets=[], people=[], authors=[], total=1,
        )


@pytest.mark.asyncio
async def test_ask_calls_orchestrator_with_user_and_includes_sources() -> None:
    orch = FakeOrch()
    out = await _ask("travel policy", _user(), orchestrator=orch)
    assert orch.seen.user_id == "u9"
    assert "Economy fares only." in out
    assert "Travel Policy" in out and "https://x/d1" in out


@pytest.mark.asyncio
async def test_search_formats_titles_and_urls() -> None:
    out = await _search("travel", _user(), search=FakeSearch())
    assert "Travel Policy" in out
    assert "https://x/d1" in out
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd brain-api && uv run pytest tests/test_mcp_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.mcp'`.

- [ ] **Step 4: Write the implementation**

Create `brain-api/app/mcp/__init__.py` (empty file).

Create `brain-api/app/mcp/server.py`:

```python
"""Remote MCP server (FastMCP, Streamable HTTP) for the company brain.

Mounted at /mcp on the FastAPI app. Two tools — ask_company_brain and
search_company_brain — resolve to the PAT owner via TokenStore and run through
the same ACL-scoped stack as the browser. Collaborators are bound at lifespan
startup (mcp_bind); per-request auth is handled by AuthMiddleware, which stashes
the resolved User in a ContextVar the tool wrappers read.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP

from app.domain.identity import User
from app.domain.query import QueryRequest

logger = logging.getLogger(__name__)

# Serve at the mount root so app.mount("/mcp", ...) yields exactly /mcp.
mcp = FastMCP("substrateos", stateless_http=True, streamable_http_path="/")

_state: dict = {}
_current_user: ContextVar[User | None] = ContextVar("mcp_user", default=None)


def mcp_bind(*, orchestrator, search, token_store) -> None:
    _state["orchestrator"] = orchestrator
    _state["search"] = search
    _state["token_store"] = token_store


async def _ask(query: str, user: User, *, orchestrator) -> str:
    ans = await orchestrator.answer(QueryRequest(query=query, k=5), user=user)
    sources = "\n".join(f"- {c.title} ({c.source_url})" for c in ans.citations)
    return ans.text + (f"\n\nSources:\n{sources}" if sources else "")


async def _search(query: str, user: User, *, search) -> str:
    resp = await search.result(user=user, query=query, top=8)
    if not resp.results:
        return "No results."
    lines = [f"- {h.title}: {h.snippet} ({h.source_url})" for h in resp.results]
    return "\n".join(lines)


@mcp.tool()
async def ask_company_brain(query: str) -> str:
    """Answer a question using the company's grounded knowledge (ACL-scoped to you)."""
    user = _current_user.get()
    if user is None:
        return "error: unauthorized — send Authorization: Bearer <your sbx_live_ token>"
    try:
        return await _ask(query, user, orchestrator=_state["orchestrator"])
    except Exception as e:  # noqa: BLE001 — surface as a tool error, never a 500
        logger.warning("mcp ask failed: %s", e)
        return f"error: {e}"


@mcp.tool()
async def search_company_brain(query: str) -> str:
    """Search company documents (ACL-scoped to you). Returns titles, snippets, URLs."""
    user = _current_user.get()
    if user is None:
        return "error: unauthorized — send Authorization: Bearer <your sbx_live_ token>"
    try:
        return await _search(query, user, search=_state["search"])
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp search failed: %s", e)
        return f"error: {e}"


class AuthMiddleware:
    """ASGI middleware: resolve the PAT bearer to a User and stash it in a
    ContextVar for the duration of the request. Missing/invalid → tools see no
    user and return an auth error (the protocol handshake itself stays open)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        user = None
        store = _state.get("token_store")
        if store is not None:
            for k, v in scope.get("headers", []):
                if k == b"authorization":
                    val = v.decode()
                    if val.lower().startswith("bearer "):
                        try:
                            user = await store.resolve(val.split(" ", 1)[1])
                        except Exception as e:  # noqa: BLE001
                            logger.warning("mcp auth resolve failed: %s", e)
                    break
        if user is not None:
            from app.api._auth_resolve import _apply_pilot_tenant
            user = _apply_pilot_tenant(user)
        token = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(token)


def build_mcp_asgi():
    """The Streamable-HTTP ASGI app wrapped with PAT auth. Mount at /mcp."""
    return AuthMiddleware(mcp.streamable_http_app())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd brain-api && uv run pytest tests/test_mcp_tools.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add brain-api/app/mcp/ brain-api/pyproject.toml brain-api/uv.lock brain-api/tests/test_mcp_tools.py
git commit -m "feat(mcp): FastMCP server with ask/search tools + PAT auth middleware"
```

---

## Task 9: Wire token store + MCP into the app (lifespan + mount + routers)

**Files:**
- Modify: `brain-api/app/main.py`
- Test: `brain-api/tests/test_healthz.py` (smoke — app boots with new wiring)

The MCP Streamable-HTTP app owns a session manager that must run inside the parent app's lifespan. We mount the ASGI app at module scope (so the route registers) and run `mcp.session_manager.run()` around the existing setup in the lifespan.

- [ ] **Step 1: Add imports**

In `brain-api/app/main.py`, add to the import block (after the existing `app.api.*` imports, ~line 17):

```python
from app.api.context import router as context_router
from app.api.tokens import router as tokens_router
```

and after the connector imports (~line 22):

```python
from app.mcp.server import build_mcp_asgi, mcp, mcp_bind
from app.tokens.store import CosmosTokenStore, NullTokenStore
```

- [ ] **Step 2: Build the token store in the lifespan**

In `brain-api/app/main.py`, immediately after the `connection_store` block (after line 93, before `app.state.metrics_store = MetricsStore()`), add:

```python
    # PATs: Cosmos (reuses the people graph) when configured, else a no-op store.
    if _s.cosmos_gremlin_endpoint and _s.cosmos_gremlin_key:
        app.state.token_store = CosmosTokenStore(graph=app.state.people_graph)
    else:
        app.state.token_store = NullTokenStore()
```

- [ ] **Step 3: Bind MCP collaborators + run its session manager**

In `brain-api/app/main.py`, replace the `try: / yield / finally:` block (lines 96-110) with:

```python
    mcp_bind(
        orchestrator=app.state.orchestrator,
        search=app.state.search_service,
        token_store=app.state.token_store,
    )
    try:
        if get_settings().mcp_enabled:
            async with mcp.session_manager.run():
                yield
        else:
            yield
    finally:
        await app.state.orchestrator.aclose()
        await app.state.acl_store.aclose()
        await app.state.people_graph.aclose()
        await app.state.activity_store.aclose()
        await app.state.history_store.aclose()
        await app.state.conversation_store.aclose()
        await app.state.cache.aclose()
        await app.state.ai_search.aclose()
        await app.state.embedder.aclose()
        await app.state.connection_store.aclose()
        await app.state.metrics_store.aclose()
        await app.state.token_store.aclose()
```

- [ ] **Step 4: Register routers + mount the MCP app**

In `brain-api/app/main.py`, after the existing `app.include_router(conversations_router)` line, add:

```python
app.include_router(tokens_router)
app.include_router(context_router)

if get_settings().mcp_enabled:
    app.mount("/mcp", build_mcp_asgi())
```

- [ ] **Step 5: Run the boot smoke test**

Run: `cd brain-api && uv run pytest tests/test_healthz.py tests/test_lifespan_clients.py -v`
Expected: PASS. If `test_lifespan_clients.py` constructs real Azure clients it may already be skipped/mocked — do not modify it; just confirm no import errors.

Also confirm the app imports cleanly:
Run: `cd brain-api && uv run python -c "import app.main; print('routes', [r.path for r in app.main.app.routes if getattr(r,'path',None) in ('/context','/tokens','/mcp')])"`
Expected: prints a list containing `/context`, `/tokens`, and `/mcp`.

- [ ] **Step 6: Run the full backend suite**

Run: `cd brain-api && uv run pytest -q`
Expected: all green (the new tests + the existing suite).

- [ ] **Step 7: Commit**

```bash
git add brain-api/app/main.py
git commit -m "feat(app): wire TokenStore, /tokens, /context, and mount /mcp"
```

---

## Task 10: Frontend API client (tokens + base URL)

**Files:**
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Add types + functions**

In `web/lib/api.ts`, append at the end of the file:

```typescript
export type TokenMeta = {
  token_id: string;
  name: string;
  masked: string;
  created_at: string;
  last_used_at: string | null;
};
export type TokenCreated = { token: string; meta: TokenMeta };

// The brain-api base URL — surfaced in copy-paste snippets in the Connect panels.
export function apiBaseUrl(): string {
  return API_BASE;
}

export async function listTokens(): Promise<TokenMeta[]> {
  try {
    const resp = await authedFetch(`${API_BASE}/tokens`);
    if (!resp.ok) return [];
    return (await resp.json()) as TokenMeta[];
  } catch {
    return [];
  }
}

export async function createToken(name: string): Promise<TokenCreated | null> {
  try {
    const resp = await authedFetch(`${API_BASE}/tokens`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!resp.ok) return null;
    return (await resp.json()) as TokenCreated;
  } catch {
    return null;
  }
}

export async function revokeToken(tokenId: string): Promise<boolean> {
  try {
    const resp = await authedFetch(`${API_BASE}/tokens/${encodeURIComponent(tokenId)}`, {
      method: "DELETE",
    });
    if (!resp.ok) return false;
    const body = (await resp.json()) as { revoked: boolean };
    return body.revoked;
  } catch {
    return false;
  }
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && pnpm exec tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/lib/api.ts
git commit -m "feat(web): token API client + apiBaseUrl helper"
```

---

## Task 11: Connect modal styles

**Files:**
- Modify: `web/app/globals.css`

- [ ] **Step 1: Append the styles**

Open `mockups/connect-panels.html` for the canonical look, then append to `web/app/globals.css`:

```css
/* ---- Connect panels (API / MCP / coming-soon surfaces) ---- */
.surfaces .chip { cursor: pointer; background: none; border: 1px solid var(--line); font: inherit; }
.surfaces .chip:hover { border-color: var(--violet); }
.cmodal-backdrop {
  position: fixed; inset: 0; background: rgba(10, 12, 20, 0.55);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.cmodal {
  width: min(720px, 92vw); max-height: 86vh; overflow: auto;
  background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35); padding: 0;
}
.cmodal-head {
  display: flex; align-items: center; gap: 12px;
  padding: 16px 20px; border-bottom: 1px solid var(--line);
}
.cmodal-head h3 { margin: 0; font-size: 16px; }
.cmodal-x {
  margin-left: auto; cursor: pointer; background: none; border: none;
  font-size: 20px; color: var(--muted); line-height: 1;
}
.m-tabs { display: flex; gap: 6px; padding: 12px 20px 0; flex-wrap: wrap; }
.m-tab {
  cursor: pointer; padding: 6px 12px; border-radius: 8px; font-size: 13px;
  border: 1px solid var(--line); background: none; color: var(--muted);
}
.m-tab.on { color: var(--ink); border-color: var(--violet); background: var(--violet-weak, rgba(124,92,255,.08)); }
.m-body { padding: 16px 20px 22px; }
.m-body h4 { margin: 16px 0 6px; font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
.endpoint { display: flex; gap: 8px; align-items: center; font-size: 13px; padding: 4px 0; }
.endpoint .verb { font-weight: 700; color: var(--violet); min-width: 52px; }
.code {
  position: relative; background: var(--code-bg, #0d1019); color: #e6e8ef;
  border-radius: 10px; padding: 12px 14px; font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap; word-break: break-all; margin: 6px 0;
}
.code .copy {
  position: absolute; top: 8px; right: 8px; cursor: pointer; font-size: 11px;
  border: 1px solid rgba(255,255,255,.18); border-radius: 6px; padding: 2px 8px;
  background: rgba(255,255,255,.06); color: #e6e8ef;
}
.tok-row {
  display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--line);
  font-size: 13px;
}
.tok-row .tok-name { font-weight: 600; }
.tok-row .tok-mask { color: var(--muted); font-family: ui-monospace, monospace; }
.tok-row .tok-rev { margin-left: auto; cursor: pointer; color: var(--rose); background: none; border: none; font-size: 12px; }
.tok-new {
  display: flex; gap: 8px; margin-top: 10px;
}
.tok-new input { flex: 1; padding: 7px 10px; border: 1px solid var(--line); border-radius: 8px; font: inherit; }
.tok-new button, .m-btn {
  cursor: pointer; padding: 7px 14px; border-radius: 8px; border: none;
  background: var(--violet); color: #fff; font: inherit; font-weight: 600;
}
.tok-warn {
  background: rgba(255, 196, 0, .12); border: 1px solid rgba(255,196,0,.4);
  border-radius: 8px; padding: 10px 12px; font-size: 12.5px; margin: 8px 0;
}
.tool { font-size: 13px; padding: 6px 0; }
.tool code { background: var(--code-bg, #0d1019); color: #e6e8ef; padding: 1px 6px; border-radius: 5px; }
.m-soon { text-align: center; color: var(--muted); padding: 28px 0; }
.m-soon .m-btn { opacity: .5; cursor: not-allowed; margin-top: 10px; }
```

- [ ] **Step 2: Build the CSS (sanity)**

Run: `cd web && pnpm build` (or `pnpm exec next lint` if a full build is slow)
Expected: no CSS/compile error. (A full `pnpm build` also covers Task 12 once that lands; running it here just verifies the CSS parses.)

- [ ] **Step 3: Commit**

```bash
git add web/app/globals.css
git commit -m "feat(web): Connect modal styles (ported from mockup)"
```

---

## Task 12: Connect modal component + clickable chips

**Files:**
- Modify: `web/components/Chat.tsx`

- [ ] **Step 1: Add imports + the ConnectModal component**

In `web/components/Chat.tsx`, extend the api import (line 5-6) to include the token functions + types:

```typescript
import { postQuery, postFeedback, getConversations, getConversation, logClick, postSearch,
  listTokens, createToken, revokeToken, apiBaseUrl,
  Answer, Citation, ConversationSummary, SearchResponse, TokenMeta } from "@/lib/api";
```

Add the `ConnectModal` component near the bottom of the file, just before the final default export / closing of the module (after the `sourceIcon` helper region is fine; place it at top-level, not nested). Insert this complete component:

```tsx
type Surface = "Web" | "Teams" | "Slack" | "API" | "MCP";

function CodeBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="code">
      <button
        className="copy"
        onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
      >{copied ? "Copied" : "Copy"}</button>
      {text}
    </div>
  );
}

function TokenManager({ onNewToken }: { onNewToken: (plaintext: string) => void }) {
  const [tokens, setTokens] = useState<TokenMeta[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { listTokens().then(setTokens); }, []);

  async function create() {
    if (!name.trim() || busy) return;
    setBusy(true);
    const created = await createToken(name.trim());
    setBusy(false);
    if (created) {
      onNewToken(created.token);
      setTokens((t) => [created.meta, ...t]);
      setName("");
    }
  }
  async function revoke(id: string) {
    if (await revokeToken(id)) setTokens((t) => t.filter((x) => x.token_id !== id));
  }

  return (
    <>
      <h4>Your tokens</h4>
      {tokens.length === 0 && <div className="m-soon" style={{ padding: "12px 0" }}>No tokens yet.</div>}
      {tokens.map((t) => (
        <div className="tok-row" key={t.token_id}>
          <span className="tok-name">{t.name}</span>
          <span className="tok-mask">{t.masked}</span>
          <button className="tok-rev" onClick={() => revoke(t.token_id)}>Revoke</button>
        </div>
      ))}
      <div className="tok-new">
        <input placeholder="Token name (e.g. my-laptop)" value={name}
          onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
        <button onClick={create} disabled={busy}>{busy ? "Creating…" : "Create token"}</button>
      </div>
    </>
  );
}

function ConnectModal({ surface, onClose }: { surface: Surface; onClose: () => void }) {
  const [tab, setTab] = useState<Surface>(surface);
  const [newToken, setNewToken] = useState<string | null>(null);
  const base = apiBaseUrl();
  const tabs: Surface[] = ["Web", "Teams", "Slack", "API", "MCP"];

  const curl = `curl -X POST ${base}/context \\
  -H "Authorization: Bearer sbx_live_…" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "What is our travel policy?", "top": 5}'`;

  const mcpJson = `{
  "mcpServers": {
    "substrateos": {
      "type": "http",
      "url": "${base}/mcp",
      "headers": { "Authorization": "Bearer sbx_live_…" }
    }
  }
}`;

  return (
    <div className="cmodal-backdrop" onClick={onClose}>
      <div className="cmodal" onClick={(e) => e.stopPropagation()}>
        <div className="cmodal-head">
          <h3>Connect to SubstrateOS</h3>
          <button className="cmodal-x" onClick={onClose}>×</button>
        </div>
        <div className="m-tabs">
          {tabs.map((s) => (
            <button key={s} className={`m-tab ${tab === s ? "on" : ""}`} onClick={() => setTab(s)}>{s}</button>
          ))}
        </div>
        <div className="m-body">
          {newToken && (
            <div className="tok-warn">
              Copy your new token now — it won’t be shown again.
              <CodeBlock text={newToken} />
            </div>
          )}

          {tab === "API" && (
            <>
              <h4>Base URL</h4>
              <CodeBlock text={base} />
              <h4>Endpoints</h4>
              <div className="endpoint"><span className="verb">POST</span> /context — ranked, ACL-scoped context</div>
              <div className="endpoint"><span className="verb">POST</span> /query — a grounded answer with citations</div>
              <div className="endpoint"><span className="verb">POST</span> /search — faceted document search</div>
              <h4>Example</h4>
              <CodeBlock text={curl} />
              <TokenManager onNewToken={setNewToken} />
            </>
          )}

          {tab === "MCP" && (
            <>
              <h4>Remote MCP endpoint</h4>
              <CodeBlock text={`${base}/mcp`} />
              <h4>Add to your MCP client (mcp.json)</h4>
              <CodeBlock text={mcpJson} />
              <h4>Tools</h4>
              <div className="tool"><code>ask_company_brain(query)</code> — a grounded answer, scoped to you.</div>
              <div className="tool"><code>search_company_brain(query)</code> — matching documents.</div>
              <TokenManager onNewToken={setNewToken} />
            </>
          )}

          {(tab === "Web" || tab === "Teams" || tab === "Slack") && (
            <div className="m-soon">
              {tab === "Web"
                ? "You’re using the web app right now."
                : `${tab} integration is coming soon.`}
              {tab !== "Web" && <div><button className="m-btn" disabled>Notify me</button></div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add `connectSurface` state**

Find the `Chat` component's state declarations (near the top of the component, where `view`, `q`, `turns` etc. are declared with `useState`). Add:

```tsx
  const [connectSurface, setConnectSurface] = useState<Surface | null>(null);
```

- [ ] **Step 3: Make the topbar chips buttons that open the modal**

Replace the topbar `.surfaces` block (lines 321-325) with:

```tsx
          <div className="surfaces">
            <button className="chip on" onClick={() => setConnectSurface("Web")}><span className="d" />Web</button>
            <button className="chip" onClick={() => setConnectSurface("Teams")}>Teams</button>
            <button className="chip" onClick={() => setConnectSurface("Slack")}>Slack</button>
            <button className="chip" onClick={() => setConnectSurface("API")}>API</button>
            <button className="chip" onClick={() => setConnectSurface("MCP")}>MCP</button>
          </div>
```

- [ ] **Step 4: Render the modal**

Immediately after the `<main className="main">` … `</main>` of the Ask view (find the `{view === "ask" && (` … `)}` block's closing), render the modal once at the end of the component's returned JSX, just before the outermost closing tag. Add:

```tsx
      {connectSurface && (
        <ConnectModal surface={connectSurface} onClose={() => setConnectSurface(null)} />
      )}
```

(Place it as a sibling of the `{view === "ask" && (...)}` block so it overlays regardless of the active view.)

- [ ] **Step 5: Type-check + build**

Run: `cd web && pnpm exec tsc --noEmit && pnpm build`
Expected: no type errors; build succeeds.

- [ ] **Step 6: Manual smoke (local dev)**

Run the web app against a local or deployed brain-api. Click each topbar chip:
- **API** tab shows base URL, the three endpoints, the curl snippet, and a token manager. "Create token" with a name shows the plaintext once in a warning box with a Copy button; Revoke removes the row.
- **MCP** tab shows `${base}/mcp`, the `mcp.json` block, and the two tools.
- **Slack/Teams** show "coming soon" with a disabled "Notify me"; **Web** shows the "you're using it now" copy.

- [ ] **Step 7: Commit**

```bash
git add web/components/Chat.tsx
git commit -m "feat(web): Connect modal + clickable topbar chips (API/MCP/coming-soon)"
```

---

## Task 13: End-to-end verification (manual, against deployed India brain-api)

**Files:** none (verification only).

- [ ] **Step 1: Mint a token via the UI**

Log into the web app, open the **API** Connect panel, create a token named `e2e`, and copy the `sbx_live_…` plaintext.

- [ ] **Step 2: Call the Context API with the PAT**

```bash
curl -s -X POST "$BRAIN_API/context" \
  -H "Authorization: Bearer sbx_live_…" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is our travel policy?", "top": 5}' | python -m json.tool
```
Expected: JSON `{"query": ..., "hits": [...]}` with at least one ACL-scoped hit (assuming the pilot corpus is loaded). A bad/missing token → `401`.

- [ ] **Step 3: Verify the MCP endpoint handshake**

```bash
curl -s -X POST "$BRAIN_API/mcp" \
  -H "Authorization: Bearer sbx_live_…" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```
Expected: a JSON-RPC `result` with `serverInfo.name == "substrateos"` (transport may respond as an SSE `data:` line — that's fine; the body should contain the initialize result, not a 404/500).

- [ ] **Step 4: Revoke + confirm**

Revoke the `e2e` token in the UI, then repeat Step 2 — expect `401`.

- [ ] **Step 5 (deploy):** Build + push images and update both Container Apps per the established India flow (`docker buildx --platform linux/amd64 --push` to `cbrainindiaacr`, then `az containerapp update`). Bump tags (e.g. `brain-api:india5`, `substrateos-web:india3`). This step follows the existing deploy runbook — no code changes.

---

## Self-Review

**1. Spec coverage:**
- PATs (create/list/revoke/resolve, sha256, shown once) → Tasks 1, 2, 5. ✓
- `resolve_user` PAT branch + precedence + pilot map → Task 3. ✓
- Context API (`/context` + PAT on `/query`,`/search`) → Tasks 6, 7. ✓
- MCP server (FastMCP Streamable HTTP, 2 tools, ContextVar auth) → Tasks 8, 9. ✓
- `/tokens` interactive-only (PAT rejected) → Task 5 (`test_pat_bearer_cannot_manage_tokens`). ✓
- Wiring (lifespan token_store, mcp_bind, mount, deps, config, pyproject) → Tasks 2, 4, 8, 9. ✓
- Frontend (api.ts client, ConnectModal tabs, styles) → Tasks 10, 11, 12. ✓
- Error/degradation (NullTokenStore, never-500 context, MCP tool error strings) → Tasks 2, 6, 8. ✓
- Out-of-scope items (scopes/expiry/rotation, MCP OAuth, real bots, rate limiting) → not implemented, as intended. ✓

**2. Placeholder scan:** No `TBD`/`TODO`/"add error handling" — every code step shows full code. The `sbx_live_…` strings in snippets are intentional UI placeholders, not plan gaps.

**3. Type consistency:**
- `TokenMeta`/`TokenCreated` fields identical across `domain/token.py`, store, API, `api.ts`. ✓
- `CosmosTokenStore.create/list/revoke/resolve` signatures match callers in Tasks 5, 6, 3, 8. ✓
- `QueryRequest(query=…, k=…)` (not `top`) used in `/context` and MCP `_ask`. ✓
- `RankedResult` → `r.candidate.chunk.{doc_id,title,source_url,source,content}`, `r.final_score`, `r.signal_breakdown` match `domain/query.py` + `domain/chunk.py`. ✓
- `mcp_bind(orchestrator=, search=, token_store=)` matches the lifespan call. ✓
- `get_settings().token_prefix` referenced in store, `_auth_resolve`, query — added in Task 2 Step 4. ✓

No gaps found.
