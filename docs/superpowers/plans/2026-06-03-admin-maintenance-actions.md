# Admin Maintenance Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-source **Sync** and a tenant-scoped **Purge Everything** action to the Admin Data Sources page, and remove the obsolete Build/Developer nav.

**Architecture:** New `POST /admin/purge` (admin-key protected, inherits the router-level guard) deletes the tenant's index docs (primary, must-succeed) plus best-effort ACL + activity, returning a summary with an `errors[]` list so partial outcomes are explicit. The web Data Sources page gains a per-row Sync button (reusing the existing `resync` API + polling) and a Purge button gated by a typed `PURGE` confirmation modal.

**Tech Stack:** Python / FastAPI / pytest (httpx ASGITransport) backend; Next.js (App Router) + TypeScript web (verified via `pnpm typecheck` / `pnpm build`, no unit tests).

**Branch:** Work on the current branch (`main`) per the user's established preference; commit per task.

**Spec:** `docs/superpowers/specs/2026-06-03-admin-maintenance-actions-design.md`

---

## File Structure

**Backend (`brain-api/`):**
- Modify `app/retrieval/ai_search_client.py` — add `_DELETE_PAGE` const + `delete_tenant_docs()`.
- Modify `app/acl/store.py` — add `clear_tenant()`.
- Modify `app/activity/store.py` — add `purge_tenant()`.
- Modify `app/api/admin.py` — add `PurgeResult` model + `POST /admin/purge`.
- Test `tests/test_purge.py` (new) — store methods + endpoint.

**Web (`web/`):**
- Modify `lib/adminApi.ts` — `PurgeResult` type + `purgeEverything()`.
- Modify `app/admin/sources/page.tsx` — per-source Sync button + Purge button/modal.
- Modify `app/admin/layout.tsx` — remove Build/Developer nav group.
- Modify `app/globals.css` — add `.btn.danger` + `.row-sync`.
- Delete `app/admin/developer/page.tsx`.

---

## Task 1: `AISearchClient.delete_tenant_docs`

**Files:**
- Modify: `brain-api/app/retrieval/ai_search_client.py`
- Test: `brain-api/tests/test_purge.py`

- [ ] **Step 1: Write the failing test**

Create `brain-api/tests/test_purge.py`:

```python
import asyncio

import app.retrieval.ai_search_client as aisc


class _FakeSearchCli:
    """Async SearchClient stand-in: pages keyed by skip//top; records deletes."""

    def __init__(self, pages: list[list[str]]) -> None:
        self._pages = pages
        self.deleted: list[str] = []

    async def search(self, *, search_text, filter, select, top, skip):
        idx = skip // top
        rows = self._pages[idx] if idx < len(self._pages) else []

        async def _gen():
            for cid in rows:
                yield {"chunk_id": cid}

        return _gen()

    async def delete_documents(self, *, documents):
        self.deleted.extend(d["chunk_id"] for d in documents)


def _client(cli) -> aisc.AISearchClient:
    c = aisc.AISearchClient.__new__(aisc.AISearchClient)
    c._credential = None
    c._cli = cli
    return c


def test_delete_tenant_docs_pages_and_deletes(monkeypatch):
    monkeypatch.setattr(aisc, "_DELETE_PAGE", 2)
    cli = _FakeSearchCli([["a", "b"], ["c"]])  # full page (2) then partial (1) -> stop
    n = asyncio.run(_client(cli).delete_tenant_docs(tenant_id="t-eval"))
    assert n == 3
    assert sorted(cli.deleted) == ["a", "b", "c"]


def test_delete_tenant_docs_empty(monkeypatch):
    monkeypatch.setattr(aisc, "_DELETE_PAGE", 2)
    cli = _FakeSearchCli([[]])
    n = asyncio.run(_client(cli).delete_tenant_docs(tenant_id="t-eval"))
    assert n == 0
    assert cli.deleted == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain-api && .venv/bin/python -m pytest tests/test_purge.py -q`
Expected: FAIL — `AttributeError: module 'app.retrieval.ai_search_client' has no attribute '_DELETE_PAGE'` (and `delete_tenant_docs` undefined).

- [ ] **Step 3: Add the constant and method**

In `brain-api/app/retrieval/ai_search_client.py`, add a module-level constant near the top (after the imports, before `class AISearchClient`):

```python
# Page size for the purge scan/delete loop; small enough to batch, monkeypatched in tests.
_DELETE_PAGE = 1000
```

Add this method to `AISearchClient` (e.g. right after `upsert_chunks`):

```python
    async def delete_tenant_docs(self, *, tenant_id: str) -> int:
        """Delete every indexed document for the tenant. Returns the count deleted.

        Collects keys first (stable, no concurrent writes during purge), then
        deletes by key (`chunk_id`) in batches — avoids the eventual-consistency
        re-query loop.
        """
        flt = f"tenant_id eq '{tenant_id.replace(chr(39), chr(39) * 2)}'"
        keys: list[str] = []
        skip = 0
        while True:
            res = await self._cli.search(
                search_text="*", filter=flt, select=["chunk_id"], top=_DELETE_PAGE, skip=skip
            )
            batch = [r["chunk_id"] async for r in res]
            keys.extend(batch)
            if len(batch) < _DELETE_PAGE:
                break
            skip += _DELETE_PAGE
        if not keys:
            return 0
        for i in range(0, len(keys), _DELETE_PAGE):
            await self._cli.delete_documents(
                documents=[{"chunk_id": k} for k in keys[i : i + _DELETE_PAGE]]
            )
        return len(keys)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain-api && .venv/bin/python -m pytest tests/test_purge.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add brain-api/app/retrieval/ai_search_client.py brain-api/tests/test_purge.py
git commit -m "feat(search): delete_tenant_docs for tenant-scoped index purge"
```

---

## Task 2: `ACLStore.clear_tenant`

**Files:**
- Modify: `brain-api/app/acl/store.py`
- Test: `brain-api/tests/test_purge.py`

- [ ] **Step 1: Write the failing test**

Append to `brain-api/tests/test_purge.py`:

```python
import fnmatch

from app.acl.store import ACLStore


class _FakeRedisAcl:
    def __init__(self, keys) -> None:
        self._keys = set(keys)
        self.deleted: list[str] = []

    async def scan_iter(self, match, count=500):
        for k in list(self._keys):
            if fnmatch.fnmatch(k, match):
                yield k

    async def delete(self, k):
        self.deleted.append(k)
        self._keys.discard(k)


def test_clear_tenant_noop_without_redis():
    store = ACLStore.__new__(ACLStore)
    store._r = None
    assert asyncio.run(store.clear_tenant(tenant_id="t-eval")) is None


def test_clear_tenant_deletes_only_matching():
    store = ACLStore.__new__(ACLStore)
    store._r = _FakeRedisAcl(
        {"acl:doc:t-eval:d1", "acl:doc:t-eval:d2", "acl:doc:other:x"}
    )
    n = asyncio.run(store.clear_tenant(tenant_id="t-eval"))
    assert n == 2
    assert sorted(store._r.deleted) == ["acl:doc:t-eval:d1", "acl:doc:t-eval:d2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain-api && .venv/bin/python -m pytest tests/test_purge.py -k clear_tenant -q`
Expected: FAIL — `AttributeError: 'ACLStore' object has no attribute 'clear_tenant'`.

- [ ] **Step 3: Add the method**

In `brain-api/app/acl/store.py`, add to `ACLStore` (after `doc_principals`):

```python
    async def clear_tenant(self, *, tenant_id: str) -> int | None:
        """Delete all live ACL entries for the tenant. Returns the count, or None
        when no store is configured (Redis host unset, e.g. the India deploy)."""
        if self._r is None:
            return None
        pattern = f"acl:doc:{tenant_id}:*"
        deleted = 0
        try:
            async for key in self._r.scan_iter(match=pattern, count=500):
                await self._r.delete(key)
                deleted += 1
        except (RedisError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning("ACLStore clear_tenant failed (tenant=%s): %s", tenant_id, e)
        return deleted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain-api && .venv/bin/python -m pytest tests/test_purge.py -k clear_tenant -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add brain-api/app/acl/store.py brain-api/tests/test_purge.py
git commit -m "feat(acl): clear_tenant to purge live ACL entries (no-op without Redis)"
```

---

## Task 3: `ActivityStore.purge_tenant`

**Files:**
- Modify: `brain-api/app/activity/store.py`
- Test: `brain-api/tests/test_purge.py`

- [ ] **Step 1: Write the failing test**

Append to `brain-api/tests/test_purge.py`:

```python
from app.activity.store import ActivityStore


def test_purge_tenant_noop_without_cluster():
    store = ActivityStore.__new__(ActivityStore)
    store._client = None
    store._db = "brain"
    assert asyncio.run(store.purge_tenant(tenant_id="t-eval")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain-api && .venv/bin/python -m pytest tests/test_purge.py -k purge_tenant -q`
Expected: FAIL — `AttributeError: 'ActivityStore' object has no attribute 'purge_tenant'`.

- [ ] **Step 3: Add the method**

In `brain-api/app/activity/store.py`, add to `ActivityStore` (after `engagement_scores`):

```python
    async def purge_tenant(self, *, tenant_id: str) -> int | None:
        """Best-effort soft-delete of this tenant's events. Returns the number of
        records removed, or None when no cluster is configured. Raises on an ADX
        failure so the caller can record it (e.g. missing managed-identity grant).

        tenant_id is server config (brain_tenant_id), not user input, so it is
        inlined safely — consistent with the inlined ints in the Discover queries.
        """
        if self._client is None:
            return None
        safe = tenant_id.replace('"', '\\"')

        def _count():
            q = f'{_TABLE} | where TenantId == "{safe}" | count'
            return self._client.execute_query(self._db, q)

        resp = await asyncio.to_thread(_count)
        rows = list(resp.primary_results[0])
        n = int(rows[0]["Count"]) if rows else 0

        cmd = f'.delete table {_TABLE} records <| {_TABLE} | where TenantId == "{safe}"'

        def _del():
            return self._client.execute_mgmt(self._db, cmd)

        await asyncio.to_thread(_del)
        return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain-api && .venv/bin/python -m pytest tests/test_purge.py -k purge_tenant -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add brain-api/app/activity/store.py brain-api/tests/test_purge.py
git commit -m "feat(activity): purge_tenant ADX soft-delete (best-effort, no-op without cluster)"
```

---

## Task 4: `POST /admin/purge` endpoint

**Files:**
- Modify: `brain-api/app/api/admin.py`
- Test: `brain-api/tests/test_purge.py`

- [ ] **Step 1: Write the failing test**

Append to `brain-api/tests/test_purge.py`:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import router
from app.config import get_settings


def _build_app(monkeypatch, **state):
    monkeypatch.setenv("ADMIN_API_KEY", "k")
    monkeypatch.setenv("BRAIN_TENANT_ID", "t-eval")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(router)
    for key, val in state.items():
        setattr(app.state, key, val)
    return app


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    yield
    get_settings.cache_clear()


class _FakeSearch:
    async def delete_tenant_docs(self, *, tenant_id):
        assert tenant_id == "t-eval"
        return 6


class _FakeACL:
    async def clear_tenant(self, *, tenant_id):
        return None


@pytest.mark.asyncio
async def test_purge_returns_summary(monkeypatch):
    class _ActivityOK:
        async def purge_tenant(self, *, tenant_id):
            return 12
        async def aclose(self):
            return None

    monkeypatch.setattr("app.api.admin.ActivityStore", lambda: _ActivityOK())
    app = _build_app(monkeypatch, ai_search=_FakeSearch(), acl_store=_FakeACL())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/admin/purge", headers={"x-admin-key": "k"})
    assert r.status_code == 200
    b = r.json()
    assert b["docs_deleted"] == 6
    assert b["acl_cleared"] is None
    assert b["activity_cleared"] == 12
    assert b["errors"] == []


@pytest.mark.asyncio
async def test_purge_isolates_activity_failure(monkeypatch):
    class _ActivityBoom:
        async def purge_tenant(self, *, tenant_id):
            raise RuntimeError("adx down")
        async def aclose(self):
            return None

    monkeypatch.setattr("app.api.admin.ActivityStore", lambda: _ActivityBoom())
    app = _build_app(monkeypatch, ai_search=_FakeSearch(), acl_store=_FakeACL())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/admin/purge", headers={"x-admin-key": "k"})
    assert r.status_code == 200
    b = r.json()
    assert b["docs_deleted"] == 6
    assert b["activity_cleared"] is None
    assert any("activity" in e for e in b["errors"])


@pytest.mark.asyncio
async def test_purge_requires_admin_key(monkeypatch):
    app = _build_app(monkeypatch, ai_search=_FakeSearch(), acl_store=_FakeACL())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/admin/purge")
    assert r.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain-api && .venv/bin/python -m pytest tests/test_purge.py -k purge_ -q`
Expected: FAIL — `/admin/purge` returns 404 (route not defined).

- [ ] **Step 3: Add the model and route**

In `brain-api/app/api/admin.py`, add a response model near the other `BaseModel` request classes (e.g. after `SeedActivityRequest`):

```python
class PurgeResult(BaseModel):
    docs_deleted: int
    acl_cleared: int | None
    activity_cleared: int | None
    errors: list[str] = []
```

Add the route (e.g. right after the `seed_activity` handler). `get_ai_search` and `get_acl_store` are already imported from `app.deps`; `ActivityStore` and `get_settings` are already imported:

```python
@router.post("/purge", response_model=PurgeResult)
async def purge_everything(
    ai_search=Depends(get_ai_search),
    acl_store=Depends(get_acl_store),
) -> PurgeResult:
    """Tenant-scoped purge: index docs (primary) + best-effort ACL + activity.
    Connections are intentionally kept so sources can be re-synced. Partial
    failures are reported in `errors`, never hidden."""
    tenant = get_settings().brain_tenant_id
    errors: list[str] = []

    # Primary: index documents. If this raises, the whole request 500s.
    docs_deleted = await ai_search.delete_tenant_docs(tenant_id=tenant)

    # Best-effort: live ACL entries (no-op without Redis).
    acl_cleared: int | None = None
    if acl_store is not None:
        try:
            acl_cleared = await acl_store.clear_tenant(tenant_id=tenant)
        except Exception as e:  # noqa: BLE001 - report, don't fail the purge
            errors.append(f"acl: {e}")

    # Best-effort: activity events (ADX; may be unreachable / lack MI grant).
    activity_cleared: int | None = None
    store = ActivityStore()
    try:
        activity_cleared = await store.purge_tenant(tenant_id=tenant)
    except Exception as e:  # noqa: BLE001 - report, don't fail the purge
        errors.append(f"activity: {e}")
    finally:
        await store.aclose()

    return PurgeResult(
        docs_deleted=docs_deleted,
        acl_cleared=acl_cleared,
        activity_cleared=activity_cleared,
        errors=errors,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain-api && .venv/bin/python -m pytest tests/test_purge.py -q`
Expected: PASS (all 8 tests).

- [ ] **Step 5: Lint + full non-integration suite**

Run: `cd brain-api && .venv/bin/ruff check app/ tests/test_purge.py && .venv/bin/python -m pytest -m "not integration" -q`
Expected: ruff "All checks passed!"; suite passes (prior baseline 278 + the new purge tests).

- [ ] **Step 6: Commit**

```bash
git add brain-api/app/api/admin.py brain-api/tests/test_purge.py
git commit -m "feat(admin): POST /admin/purge — tenant-scoped docs+ACL+activity purge"
```

---

## Task 5: Web admin API — `purgeEverything`

**Files:**
- Modify: `web/lib/adminApi.ts`

- [ ] **Step 1: Add the type and function**

In `web/lib/adminApi.ts`, add after the `SyncJob` type:

```typescript
export type PurgeResult = {
  docs_deleted: number;
  acl_cleared: number | null;
  activity_cleared: number | null;
  errors: string[];
};
```

Add after the `getJob` export at the bottom:

```typescript
export const purgeEverything = () =>
  call<PurgeResult>("/admin/purge", { method: "POST" });
```

- [ ] **Step 2: Typecheck**

Run: `cd web && pnpm typecheck`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/lib/adminApi.ts
git commit -m "feat(web): purgeEverything admin API client + PurgeResult type"
```

---

## Task 6: Data Sources page — per-source Sync button

**Files:**
- Modify: `web/app/admin/sources/page.tsx`
- Modify: `web/app/globals.css`

- [ ] **Step 1: Add the `.row-sync` style**

In `web/app/globals.css`, after the `.btn.ghost` rule (line ~430), add:

```css
.row-sync { display: inline-flex; align-items: center; gap: 6px; margin-right: 10px; background: var(--surface); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: 5px 11px; font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
.row-sync:hover { border-color: #cabd9f; }
.row-sync:disabled { opacity: .5; cursor: default; }
```

- [ ] **Step 2: Import `resync`**

In `web/app/admin/sources/page.tsx`, change the import on line 3:

```typescript
import { getConnections, disconnect, connectProvider, resync, Connection } from "@/lib/adminApi";
```

- [ ] **Step 3: Thread an `onSync` prop through the row + table types**

In `RowProps` (around line 137), add `onSync`:

```typescript
type RowProps = {
  provider: Provider;
  conn: Connection | null;
  searchTerm: string;
  statusFilter: string;
  onEnable: () => void;
  onDisable: (id: string) => void;
  onSync: (id: string) => void;
};
```

In the `ProviderRow` signature destructure (around line 146), add `onSync`:

```typescript
function ProviderRow({ provider: p, conn, searchTerm, statusFilter, onEnable, onDisable, onSync }: RowProps) {
```

In the `c-enable` cell of `ProviderRow` (around line 195), render a Sync button before the `Toggle` when a connection exists:

```tsx
      <td className="c-enable">
        {p.connectable && conn && (
          <button
            className="row-sync"
            title="Sync now"
            disabled={conn.status === "syncing"}
            onClick={() => onSync(conn.connection_id)}
          >
            Sync
          </button>
        )}
        <Toggle
          connectable={!!p.connectable}
          conn={conn}
          onEnable={onEnable}
          onDisable={() => conn && onDisable(conn.connection_id)}
        />
      </td>
```

In `CatTableProps` (around line 209), add `onSync`:

```typescript
type CatTableProps = {
  category: Category;
  connByType: Record<string, Connection>;
  searchTerm: string;
  catFilter: string;
  statusFilter: string;
  onEnable: (provider: string) => void;
  onDisable: (id: string) => void;
  onSync: (id: string) => void;
};
```

In the `CategoryTable` signature (around line 223), add `onSync`:

```typescript
function CategoryTable({ category, connByType, searchTerm, catFilter, statusFilter, onEnable, onDisable, onSync }: CatTableProps) {
```

In the `<ProviderRow ... />` render inside `CategoryTable` (around line 260), pass `onSync`:

```tsx
              <ProviderRow
                key={p.key}
                provider={p}
                conn={connOf(p, connByType)}
                searchTerm={searchTerm}
                statusFilter={statusFilter}
                onEnable={() => onEnable(p.connType ?? "")}
                onDisable={onDisable}
                onSync={onSync}
              />
```

- [ ] **Step 4: Add the `onSync` handler and pass it from the page**

In `DataSources`, add a handler next to `onDisable` (around line 337):

```typescript
  const onSync = async (id: string) => {
    try {
      await resync(id);
      pollUntilSettled();
    } catch { /* 403 → layout gate re-prompts */ }
  };
```

In the `<CategoryTable ... />` render (around line 404), pass `onSync`:

```tsx
            <CategoryTable
              key={cat.label}
              category={cat}
              connByType={connByType}
              searchTerm={searchTerm}
              catFilter={catFilter}
              statusFilter={statusFilter}
              onEnable={onEnable}
              onDisable={onDisable}
              onSync={onSync}
            />
```

- [ ] **Step 5: Typecheck + build**

Run: `cd web && pnpm typecheck && pnpm build`
Expected: typecheck clean; build succeeds.

- [ ] **Step 6: Commit**

```bash
git add web/app/admin/sources/page.tsx web/app/globals.css
git commit -m "feat(web): per-source Sync button on Data Sources rows"
```

---

## Task 7: Data Sources page — Purge Everything button + confirm modal

**Files:**
- Modify: `web/app/admin/sources/page.tsx`
- Modify: `web/app/globals.css`

- [ ] **Step 1: Add the `.btn.danger` style**

In `web/app/globals.css`, after the `.btn.ghost` rule, add:

```css
.btn.danger { background: #b42318; color: #fff; border: none; }
.btn.danger:hover { background: #9a1c12; }
```

- [ ] **Step 2: Import `purgeEverything` + `PurgeResult`**

Update the import in `web/app/admin/sources/page.tsx` (line 3) to also bring in the purge API:

```typescript
import { getConnections, disconnect, connectProvider, resync, purgeEverything, PurgeResult, Connection } from "@/lib/adminApi";
```

- [ ] **Step 3: Add purge state**

In `DataSources`, after the `banner` state (around line 286), add:

```typescript
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [purgeText, setPurgeText] = useState("");
  const [purging, setPurging] = useState(false);
```

- [ ] **Step 4: Add the confirm handler**

In `DataSources`, after `onSync` (from Task 6), add:

```typescript
  const confirmPurge = async () => {
    if (purgeText !== "PURGE" || purging) return;
    setPurging(true);
    try {
      const r: PurgeResult = await purgeEverything();
      const parts = [`Purged ${r.docs_deleted} document(s)`];
      if (r.activity_cleared != null) parts.push(`${r.activity_cleared} activity event(s)`);
      if (r.errors.length) parts.push(`skipped: ${r.errors.join("; ")}`);
      setBanner(parts.join(" — "));
    } catch {
      setBanner("Purge failed. Check the admin key and try again.");
    } finally {
      setPurging(false);
      setPurgeOpen(false);
      setPurgeText("");
      refresh();
    }
  };
```

- [ ] **Step 5: Add the Purge button to the page header**

In the `head` block (around line 365), add a right-aligned danger button. Replace the existing `head` div with:

```tsx
        <div className="head" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
          <div>
            <h1>Data Sources</h1>
            <p>Connect sources to bring their content into the intelligence layer.</p>
          </div>
          <button className="btn danger" onClick={() => setPurgeOpen(true)}>Purge Everything</button>
        </div>
```

- [ ] **Step 6: Add the confirm modal**

In `DataSources`, just before the final closing `</div>` of the returned JSX (after the `!anyVisible` block, around line 419), add:

```tsx
        {purgeOpen && (
          <div className="admin-modal" onClick={() => !purging && setPurgeOpen(false)}>
            <div className="modal narrow" onClick={(e) => e.stopPropagation()}>
              <h2>Purge everything</h2>
              <p>This permanently deletes all indexed documents (and any activity/ACL
                data) for this workspace. Connected sources are kept — you can re-sync
                them afterward. Type <b>PURGE</b> to confirm.</p>
              <input
                type="text"
                value={purgeText}
                onChange={(e) => setPurgeText(e.target.value)}
                placeholder="PURGE"
                autoFocus
                disabled={purging}
                style={{ width: "100%", marginTop: 12, padding: "9px 12px", borderRadius: 9, border: "1px solid var(--line)", font: "inherit" }}
              />
              <div className="modal-foot">
                <button className="modal-close" onClick={() => setPurgeOpen(false)} disabled={purging}>Cancel</button>
                <button className="btn danger" onClick={confirmPurge} disabled={purgeText !== "PURGE" || purging}>
                  {purging ? "Purging…" : "Purge Everything"}
                </button>
              </div>
            </div>
          </div>
        )}
```

- [ ] **Step 7: Typecheck + build**

Run: `cd web && pnpm typecheck && pnpm build`
Expected: typecheck clean; build succeeds.

- [ ] **Step 8: Commit**

```bash
git add web/app/admin/sources/page.tsx web/app/globals.css
git commit -m "feat(web): Purge Everything button + typed-confirm modal on Data Sources"
```

---

## Task 8: Remove Build/Developer nav + delete stub page

**Files:**
- Modify: `web/app/admin/layout.tsx`
- Delete: `web/app/admin/developer/page.tsx`

- [ ] **Step 1: Remove the Build group from `NAV`**

In `web/app/admin/layout.tsx`, delete the entire `Build` group object from the `NAV` array (the object with `group: "Build"` containing the `Developer` item, lines ~61-76). The `NAV` array should end after the `Connect` group's closing `},`.

- [ ] **Step 2: Delete the stub page**

Run:

```bash
git rm web/app/admin/developer/page.tsx
rmdir web/app/admin/developer 2>/dev/null || true
```

- [ ] **Step 3: Typecheck + build**

Run: `cd web && pnpm typecheck && pnpm build`
Expected: typecheck clean; build succeeds (no remaining references to `/admin/developer`).

- [ ] **Step 4: Verify no dangling references**

Run: `cd web && grep -rn "admin/developer\|Developer" app/ lib/ --include="*.tsx" --include="*.ts"`
Expected: no matches (or only unrelated). If any nav/link references remain, remove them.

- [ ] **Step 5: Commit**

```bash
git add web/app/admin/layout.tsx
git commit -m "chore(web): remove Build/Developer nav group and stub page"
```

---

## Done / Deploy

- [ ] **Backend deploy** (manual — ACR Tasks disabled): from `brain-api/`:
  ```bash
  az acr login --name cbrainindiaacr
  docker build --platform linux/amd64 -t cbrainindiaacr.azurecr.io/brain-api:india13 -f Dockerfile .
  docker push cbrainindiaacr.azurecr.io/brain-api:india13
  az containerapp update -n brain-api -g rg-company-brain-india --image cbrainindiaacr.azurecr.io/brain-api:india13
  ```
- [ ] **Web deploy** via its own image/pipeline (substrateos-web) — same RG/registry pattern.
- [ ] **Smoke test:** on `/admin/sources`, click a source's **Sync** (status animates syncing→live), then **Purge Everything** → type `PURGE` → confirm → banner shows `Purged N document(s)`; re-running the "planning priorities" query should now return "I don't have information about that." until a real source is synced.

---

## Self-Review

- **Spec coverage:** Sync per-source (T6) ✓; Purge Everything docs+ACL+activity keep-connections (T1-4) ✓; typed PURGE confirm (T7) ✓; remove Build/Developer (T8) ✓; tenant-scoped + reported errors (T4) ✓; new `delete_tenant_docs` (T1) ✓.
- **Type consistency:** `PurgeResult` fields (`docs_deleted`, `acl_cleared`, `activity_cleared`, `errors`) identical in backend model (T4) and TS type (T5) and UI usage (T7). `delete_tenant_docs`/`clear_tenant`/`purge_tenant` signatures match between definition (T1-3) and endpoint calls (T4). `resync`/`purgeEverything` names match adminApi (T5) and page imports (T6-7).
- **Placeholders:** none — every step has concrete code/commands.
