# SubStrateOS Admin Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an `/admin` panel (Overview dashboard + Data Sources) inside the existing `web/` app that lets admins connect SharePoint sites and auto-ingest their files into the intelligence layer.

**Architecture:** New `app/connectors/` backend module (MS Graph SharePoint enumeration + text extraction + Redis connection/job store + background sync runner that feeds the existing `IngestPipeline`), a small `MetricsStore` for honest Overview tiles, and `/admin/*` API routes guarded by the existing `x-admin-key`. Frontend adds an `/admin` route group reusing the SubStrateOS design tokens, gated by an admin-key prompt on top of Easy Auth.

**Tech Stack:** Python 3.12 / FastAPI / uv / redis.asyncio / azure-search-documents / MS Graph REST / python-docx / pypdf · Next.js 14 / React 18 / TypeScript.

**Spec:** `docs/superpowers/specs/2026-05-31-substrateos-admin-panel-design.md`

---

## File Structure

**Backend (`brain-api/`):**
- `app/connectors/__init__.py` — new package
- `app/connectors/extract.py` — bytes+mime → text (txt/md/html/csv/docx/pdf; else skip)
- `app/connectors/models.py` — `Connection`, `SyncJob`, `ActivityEntry`, `RemoteFile` pydantic models
- `app/connectors/store.py` — `ConnectionStore` (Redis: connections, jobs, activity log)
- `app/connectors/sharepoint.py` — `SharePointConnector` (Graph sites/files/content, degrades to empty)
- `app/connectors/sync.py` — `SyncRunner` (enumerate → SourceDoc → IngestPipeline; updates job)
- `app/metrics/__init__.py`, `app/metrics/store.py` — `MetricsStore` (Redis INCR + PFADD)
- `app/api/admin.py` — MODIFY: add stats/connections/sites/jobs routes
- `app/retrieval/ai_search_client.py` — MODIFY: add `count_docs(tenant)`
- `app/config.py` — MODIFY: add `connector_max_items`
- `app/deps.py` — MODIFY: getters for connection store, metrics, connector
- `app/main.py` — MODIFY: lifespan wiring + record metrics on query path
- `app/api/query.py` — MODIFY: fire-and-forget metrics record
- `pyproject.toml` — MODIFY: add `python-docx`, `pypdf`
- Tests under `brain-api/tests/`

**Frontend (`web/`):**
- `mockups/admin-overview.html`, `mockups/admin-data-sources.html` — design source
- `web/lib/adminApi.ts` — admin client
- `web/app/admin/layout.tsx` — shell + nav + key gate
- `web/app/admin/page.tsx` — Overview
- `web/app/admin/sources/page.tsx` — Data Sources + connect flow
- `web/app/admin/surfaces/page.tsx`, `permissions/page.tsx`, `developer/page.tsx` — stubs
- `web/app/globals.css` — MODIFY: append admin styles

**Conventions to follow:** Redis stores take an optional injected `client` and swallow `_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)` (see `app/history/store.py`). Graph clients use `DefaultAzureCredential` `.default` and never raise — return `[]`/`None` on error (see `app/live_fetch/graph_search.py`). Tests use `pytest` + `pytest-asyncio`; run with `cd brain-api && uv run pytest`.

---

## Task 1: Mockups (visual source of truth)

**Files:**
- Create: `mockups/admin-overview.html`
- Create: `mockups/admin-data-sources.html`

Restyle the reference "Overview" design into the SubStrateOS system: reuse the exact CSS tokens/classes from `mockups/web-chat-light.html` (`--paper`, `--amber`, `.rail`, `.glyph`, `.nav`, `.card` etc). Left rail groups: `WORKSPACE → Overview`; `CONNECT → Data Sources, Surfaces, Permissions`; `BUILD → Developer`. Add an `ADMIN` mono badge next to the brand. Top-right tenant chip = `t-eval`.

- [ ] **Step 1: Build `admin-overview.html`** — header "Overview / Your work context layer at a glance.", 4 stat tiles (Active users, Sources live, Items indexed, Queries · 7d), two-column NEEDS ATTENTION + SOURCE HEALTH (SharePoint/OneDrive/Teams… with live/syncing bars), RECENT ACTIVITY list. Use static representative values (this is the mockup only).
- [ ] **Step 2: Build `admin-data-sources.html`** — header "Data Sources", a "Connect a source" row with a prominent **SharePoint** card + others (OneDrive/Teams greyed "soon"), a "Connected sources" table (name · type · status · items · last sync · actions), and a connect modal mock (site picker → Connect). Reuse `.card`, `.nav`, button styles.
- [ ] **Step 3: Open both in a browser, confirm they match the SubStrateOS look** (fonts load, amber accent, warm bg).
- [ ] **Step 4: Commit**

```bash
git add mockups/admin-overview.html mockups/admin-data-sources.html
git commit -m "design(admin): overview + data-sources mockups (SubStrateOS theme)"
```

**CHECKPOINT:** Get user visual approval on the mockups before porting (Tasks 11-16).

---

## Task 2: Dependencies + config

**Files:**
- Modify: `brain-api/pyproject.toml`
- Modify: `brain-api/app/config.py:67` (after `admin_api_key`)

- [ ] **Step 1: Add deps** — in `pyproject.toml` `dependencies`, add `"python-docx>=1.1.2"` and `"pypdf>=5.1.0"`.
- [ ] **Step 2: Install** — Run: `cd brain-api && uv sync`. Expected: resolves + installs both.
- [ ] **Step 3: Add config** — in `Settings`, after `admin_api_key`:

```python
    # SharePoint connector: hard cap on files ingested per site sync (no silent truncation).
    connector_max_items: int = 500
```

- [ ] **Step 4: Verify import** — Run: `cd brain-api && uv run python -c "import docx, pypdf; from app.config import get_settings; print(get_settings().connector_max_items)"`. Expected: prints `500`.
- [ ] **Step 5: Commit**

```bash
git add brain-api/pyproject.toml brain-api/uv.lock brain-api/app/config.py
git commit -m "feat(connectors): add python-docx/pypdf deps + connector_max_items config"
```

---

## Task 3: Text extraction

**Files:**
- Create: `brain-api/app/connectors/__init__.py` (empty)
- Create: `brain-api/app/connectors/extract.py`
- Test: `brain-api/tests/test_connector_extract.py`

- [ ] **Step 1: Write failing tests**

```python
# brain-api/tests/test_connector_extract.py
import io
from app.connectors.extract import extract_text, is_supported

def test_plain_text():
    assert extract_text(b"hello world", "text/plain", "a.txt") == "hello world"

def test_markdown():
    assert "# Title" in extract_text(b"# Title\n\nbody", "text/markdown", "a.md")

def test_html_strips_tags():
    out = extract_text(b"<html><body><h1>Hi</h1><p>there</p></body></html>", "text/html", "a.html")
    assert "Hi" in out and "there" in out and "<" not in out

def test_csv_passthrough():
    assert "a,b" in extract_text(b"a,b\n1,2", "text/csv", "a.csv")

def test_docx_roundtrip():
    import docx
    d = docx.Document()
    d.add_paragraph("Quarterly plan")
    buf = io.BytesIO(); d.save(buf)
    out = extract_text(buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx")
    assert "Quarterly plan" in out

def test_unsupported_returns_none():
    assert extract_text(b"\x00\x01", "image/png", "a.png") is None
    assert is_supported("a.png", "image/png") is False
    assert is_supported("a.docx", None) is True

def test_corrupt_supported_file_returns_none():
    # garbage that claims to be docx must not raise
    assert extract_text(b"not a real docx", 
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx") is None
```

- [ ] **Step 2: Run, verify fail** — Run: `cd brain-api && uv run pytest tests/test_connector_extract.py -v`. Expected: import error / fail.
- [ ] **Step 3: Implement `extract.py`**

```python
"""Best-effort text extraction for SharePoint files. Returns None for unsupported
types or on any extraction error (the file is then skipped + counted by the runner)."""
from __future__ import annotations

import csv
import io
import logging
import re

logger = logging.getLogger(__name__)

_TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yaml", ".yml"}
_HTML_EXT = {".html", ".htm"}
_DOCX_EXT = {".docx"}
_PDF_EXT = {".pdf"}
_SUPPORTED = _TEXT_EXT | _HTML_EXT | _DOCX_EXT | _PDF_EXT


def _ext(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i >= 0 else ""


def is_supported(name: str, mime: str | None) -> bool:
    return _ext(name) in _SUPPORTED


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def extract_text(data: bytes, mime: str | None, name: str) -> str | None:
    ext = _ext(name)
    try:
        if ext in _TEXT_EXT:
            if ext == ".csv":
                rows = list(csv.reader(io.StringIO(data.decode("utf-8", "replace"))))
                return "\n".join(", ".join(r) for r in rows).strip()
            return data.decode("utf-8", "replace").strip()
        if ext in _HTML_EXT:
            return _strip_html(data.decode("utf-8", "replace"))
        if ext in _DOCX_EXT:
            import docx  # python-docx
            doc = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        if ext in _PDF_EXT:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
    except Exception as e:  # noqa: BLE001 — extraction is best-effort; skip on failure
        logger.warning("extract_text failed for %s (%s): %s", name, mime, e)
        return None
    return None
```

- [ ] **Step 4: Run, verify pass** — Run: `cd brain-api && uv run pytest tests/test_connector_extract.py -v`. Expected: all pass.
- [ ] **Step 5: Commit**

```bash
git add brain-api/app/connectors/__init__.py brain-api/app/connectors/extract.py brain-api/tests/test_connector_extract.py
git commit -m "feat(connectors): text extraction (txt/md/html/csv/docx/pdf)"
```

---

## Task 4: Connector domain models

**Files:**
- Create: `brain-api/app/connectors/models.py`
- Test: `brain-api/tests/test_connector_models.py`

- [ ] **Step 1: Write failing test**

```python
# brain-api/tests/test_connector_models.py
from app.connectors.models import Connection, SyncJob, RemoteFile, ActivityEntry

def test_connection_defaults():
    c = Connection(connection_id="c1", tenant_id="t", type="sharepoint",
                   site_id="s1", name="Sales", web_url="https://x")
    assert c.status == "pending" and c.item_count == 0 and c.error is None

def test_syncjob_progress_roundtrip():
    j = SyncJob(job_id="j1", tenant_id="t", connection_id="c1")
    j.total = 5; j.processed = 2; j.skipped = 1
    assert SyncJob.model_validate_json(j.model_dump_json()).processed == 2

def test_remote_file():
    f = RemoteFile(drive_id="d", item_id="i", name="a.docx", mime=None,
                   web_url="https://x", size=10)
    assert f.name == "a.docx"
```

- [ ] **Step 2: Run, verify fail.** Run: `cd brain-api && uv run pytest tests/test_connector_models.py -v`. Expected: import error.
- [ ] **Step 3: Implement `models.py`**

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ConnStatus = Literal["pending", "syncing", "live", "error"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]


class Connection(BaseModel):
    connection_id: str
    tenant_id: str
    type: Literal["sharepoint"] = "sharepoint"
    site_id: str
    name: str
    web_url: str
    status: ConnStatus = "pending"
    item_count: int = 0
    last_sync: datetime | None = None
    last_job_id: str | None = None
    error: str | None = None


class SyncJob(BaseModel):
    job_id: str
    tenant_id: str
    connection_id: str
    status: JobStatus = "queued"
    total: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    truncated: bool = False
    message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RemoteFile(BaseModel):
    drive_id: str
    item_id: str
    name: str
    mime: str | None = None
    web_url: str = ""
    size: int = 0
    created_at: datetime | None = None
    modified_at: datetime | None = None
    author_id: str | None = None


class ActivityEntry(BaseModel):
    ts: datetime
    actor: str
    text: str
    kind: Literal["connect", "sync", "disconnect", "system"] = "system"
```

- [ ] **Step 4: Run, verify pass.** Run: `cd brain-api && uv run pytest tests/test_connector_models.py -v`. Expected: pass.
- [ ] **Step 5: Commit**

```bash
git add brain-api/app/connectors/models.py brain-api/tests/test_connector_models.py
git commit -m "feat(connectors): domain models (Connection/SyncJob/RemoteFile/ActivityEntry)"
```

---

## Task 5: ConnectionStore (Redis)

**Files:**
- Create: `brain-api/app/connectors/store.py`
- Test: `brain-api/tests/test_connector_store.py`

Mirrors `app/history/store.py`: optional injected client, swallows `_ERRORS`, degrades to empty. Keys: connections in a hash `connections:{tenant}` (field=connection_id → JSON); jobs `connector:job:{tenant}:{job_id}` (string JSON, TTL 1 day); activity list `admin:activity:{tenant}` (cap 50).

- [ ] **Step 1: Write failing tests** (use a fake redis via `fakeredis.aioredis` if available, else a minimal async stub)

```python
# brain-api/tests/test_connector_store.py
import pytest
from app.connectors.models import Connection, SyncJob, ActivityEntry
from app.connectors.store import ConnectionStore

class FakeRedis:
    def __init__(self): self.h={}; self.kv={}; self.lists={}
    async def hset(self, k, field, val): self.h.setdefault(k,{})[field]=val
    async def hgetall(self, k): return dict(self.h.get(k,{}))
    async def hdel(self, k, field): self.h.get(k,{}).pop(field,None)
    async def set(self, name, value, ex=None): self.kv[name]=value
    async def get(self, name): return self.kv.get(name)
    async def lpush(self, k, v): self.lists.setdefault(k,[]).insert(0,v)
    async def ltrim(self, k, a, b): self.lists[k]=self.lists.get(k,[])[a:b+1]
    async def lrange(self, k, a, b):
        xs=self.lists.get(k,[]); return xs[a:(b+1) if b>=0 else None]
    def pipeline(self, transaction=False):
        store=self
        class P:
            def __init__(s): s.ops=[]
            def lpush(s,k,v): s.ops.append(("lpush",k,v)); return s
            def ltrim(s,k,a,b): s.ops.append(("ltrim",k,a,b)); return s
            async def execute(s):
                for op in s.ops:
                    await getattr(store, op[0])(*op[1:])
            async def __aenter__(s): return s
            async def __aexit__(s,*a): return False
        return P()

@pytest.mark.asyncio
async def test_connection_crud():
    st = ConnectionStore(client=FakeRedis())
    c = Connection(connection_id="c1", tenant_id="t", type="sharepoint",
                   site_id="s", name="Sales", web_url="https://x")
    await st.put_connection(c)
    got = await st.list_connections("t")
    assert len(got)==1 and got[0].name=="Sales"
    c.status="live"; c.item_count=12; await st.put_connection(c)
    assert (await st.get_connection("t","c1")).item_count==12
    await st.delete_connection("t","c1")
    assert await st.list_connections("t")==[]

@pytest.mark.asyncio
async def test_job_roundtrip():
    st = ConnectionStore(client=FakeRedis())
    j = SyncJob(job_id="j1", tenant_id="t", connection_id="c1", status="running", total=3)
    await st.put_job(j)
    assert (await st.get_job("t","j1")).total==3
    assert await st.get_job("t","missing") is None

@pytest.mark.asyncio
async def test_activity_log_caps():
    st = ConnectionStore(client=FakeRedis())
    for i in range(3):
        await st.log_activity("t", ActivityEntry(ts=__import__("datetime").datetime(2026,1,1),
            actor="admin", text=f"e{i}", kind="connect"))
    items = await st.recent_activity("t")
    assert items[0].text=="e2" and len(items)==3

@pytest.mark.asyncio
async def test_degrades_on_error():
    class Boom:
        async def hgetall(self,k): raise ConnectionError("down")
        async def get(self,n): raise ConnectionError("down")
        async def lrange(self,k,a,b): raise ConnectionError("down")
    st = ConnectionStore(client=Boom())
    assert await st.list_connections("t")==[]
    assert await st.get_job("t","j")==None
    assert await st.recent_activity("t")==[]
```

- [ ] **Step 2: Run, verify fail.** Run: `cd brain-api && uv run pytest tests/test_connector_store.py -v`.
- [ ] **Step 3: Implement `store.py`**

```python
from __future__ import annotations

import contextlib
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.connectors.models import ActivityEntry, Connection, SyncJob

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)
_ACTIVITY_MAX = 50
_JOB_TTL = 86400


def _conn_key(tenant: str) -> str: return f"connections:{tenant}"
def _job_key(tenant: str, job_id: str) -> str: return f"connector:job:{tenant}:{job_id}"
def _activity_key(tenant: str) -> str: return f"admin:activity:{tenant}"


class ConnectionStore:
    """Redis-backed connector state. Best-effort: reads degrade to empty/None."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        if client is not None:
            self._r = client
        else:
            s = get_settings()
            self._r = redis.Redis(host=s.azure_redis_host, port=s.azure_redis_port,
                ssl=s.azure_redis_ssl, password=s.redis_key, decode_responses=True)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._r.aclose()

    async def put_connection(self, c: Connection) -> None:
        try:
            await self._r.hset(_conn_key(c.tenant_id), c.connection_id, c.model_dump_json())
        except _ERRORS as e:
            logger.warning("put_connection failed: %s", e)

    async def get_connection(self, tenant: str, connection_id: str) -> Connection | None:
        for c in await self.list_connections(tenant):
            if c.connection_id == connection_id:
                return c
        return None

    async def list_connections(self, tenant: str) -> list[Connection]:
        try:
            raw = await self._r.hgetall(_conn_key(tenant))
        except _ERRORS as e:
            logger.warning("list_connections failed: %s", e)
            return []
        out: list[Connection] = []
        for v in raw.values():
            with contextlib.suppress(Exception):
                out.append(Connection.model_validate_json(v))
        return out

    async def delete_connection(self, tenant: str, connection_id: str) -> None:
        with contextlib.suppress(*_ERRORS):
            await self._r.hdel(_conn_key(tenant), connection_id)

    async def put_job(self, j: SyncJob) -> None:
        with contextlib.suppress(*_ERRORS):
            await self._r.set(_job_key(j.tenant_id, j.job_id), j.model_dump_json(), ex=_JOB_TTL)

    async def get_job(self, tenant: str, job_id: str) -> SyncJob | None:
        try:
            v = await self._r.get(_job_key(tenant, job_id))
        except _ERRORS as e:
            logger.warning("get_job failed: %s", e)
            return None
        if not v:
            return None
        with contextlib.suppress(Exception):
            return SyncJob.model_validate_json(v)
        return None

    async def log_activity(self, tenant: str, entry: ActivityEntry) -> None:
        key = _activity_key(tenant)
        try:
            async with self._r.pipeline(transaction=False) as pipe:
                pipe.lpush(key, entry.model_dump_json())
                pipe.ltrim(key, 0, _ACTIVITY_MAX - 1)
                await pipe.execute()
        except _ERRORS as e:
            logger.warning("log_activity failed: %s", e)

    async def recent_activity(self, tenant: str, limit: int = _ACTIVITY_MAX) -> list[ActivityEntry]:
        try:
            raw = await self._r.lrange(_activity_key(tenant), 0, max(0, limit - 1))
        except _ERRORS as e:
            logger.warning("recent_activity failed: %s", e)
            return []
        out: list[ActivityEntry] = []
        for item in raw:
            with contextlib.suppress(Exception):
                out.append(ActivityEntry.model_validate_json(item))
        return out
```

- [ ] **Step 4: Run, verify pass.** Run: `cd brain-api && uv run pytest tests/test_connector_store.py -v`. Expected: pass. (If `fakeredis` not installed the inline `FakeRedis` covers it; no new dep needed.)
- [ ] **Step 5: Commit**

```bash
git add brain-api/app/connectors/store.py brain-api/tests/test_connector_store.py
git commit -m "feat(connectors): Redis ConnectionStore (connections/jobs/activity)"
```

---

## Task 6: MetricsStore (Redis) + AI Search count

**Files:**
- Create: `brain-api/app/metrics/__init__.py` (empty), `brain-api/app/metrics/store.py`
- Modify: `brain-api/app/retrieval/ai_search_client.py` (add `count_docs`)
- Test: `brain-api/tests/test_metrics_store.py`

- [ ] **Step 1: Write failing tests**

```python
# brain-api/tests/test_metrics_store.py
import pytest
from app.metrics.store import MetricsStore

class FakeRedis:
    def __init__(self): self.counts={}; self.hll={}
    async def incr(self, k): self.counts[k]=self.counts.get(k,0)+1; return self.counts[k]
    async def expire(self, k, ttl): pass
    async def mget(self, keys): return [self.counts.get(k) for k in keys]
    async def pfadd(self, k, *vals): self.hll.setdefault(k,set()).update(vals)
    async def pfcount(self, *keys):
        u=set()
        for k in keys: u|=self.hll.get(k,set())
        return len(u)

@pytest.mark.asyncio
async def test_query_counter_and_users():
    r=FakeRedis(); st=MetricsStore(client=r)
    for _ in range(3): await st.record_query("t","u-1")
    await st.record_query("t","u-2")
    assert await st.queries_last_7d("t")==4
    assert await st.active_users_7d("t")==2

@pytest.mark.asyncio
async def test_degrades_to_none():
    class Boom:
        async def incr(self,k): raise ConnectionError()
        async def pfadd(self,k,*v): raise ConnectionError()
        async def mget(self,ks): raise ConnectionError()
        async def pfcount(self,*ks): raise ConnectionError()
        async def expire(self,k,t): raise ConnectionError()
    st=MetricsStore(client=Boom())
    await st.record_query("t","u")  # must not raise
    assert await st.queries_last_7d("t") is None
    assert await st.active_users_7d("t") is None
```

- [ ] **Step 2: Run, verify fail.** Run: `cd brain-api && uv run pytest tests/test_metrics_store.py -v`.
- [ ] **Step 3: Implement `metrics/store.py`**

```python
from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)
_TTL = 8 * 86400  # keep ~8 days of daily buckets


def _days(n: int) -> list[str]:
    today = datetime.now(UTC).date()
    return [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


class MetricsStore:
    """Cheap real Overview metrics: daily query counters + distinct-user HLLs.
    record_query is fire-and-forget; reads return None when unavailable (UI shows '—')."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        if client is not None:
            self._r = client
        else:
            s = get_settings()
            self._r = redis.Redis(host=s.azure_redis_host, port=s.azure_redis_port,
                ssl=s.azure_redis_ssl, password=s.redis_key, decode_responses=True)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._r.aclose()

    async def record_query(self, tenant: str, user_id: str) -> None:
        d = _days(1)[0]
        try:
            qk = f"metrics:queries:{tenant}:{d}"
            uk = f"metrics:users:{tenant}:{d}"
            await self._r.incr(qk); await self._r.expire(qk, _TTL)
            await self._r.pfadd(uk, user_id); await self._r.expire(uk, _TTL)
        except _ERRORS as e:
            logger.warning("record_query failed: %s", e)

    async def queries_last_7d(self, tenant: str) -> int | None:
        keys = [f"metrics:queries:{tenant}:{d}" for d in _days(7)]
        try:
            vals = await self._r.mget(keys)
        except _ERRORS as e:
            logger.warning("queries_last_7d failed: %s", e)
            return None
        return sum(int(v) for v in vals if v)

    async def active_users_7d(self, tenant: str) -> int | None:
        keys = [f"metrics:users:{tenant}:{d}" for d in _days(7)]
        try:
            return int(await self._r.pfcount(*keys))
        except _ERRORS as e:
            logger.warning("active_users_7d failed: %s", e)
            return None
```

- [ ] **Step 4: Add `count_docs` to AISearchClient** — in `app/retrieval/ai_search_client.py`, add method (after `lookup_docs`):

```python
    async def count_docs(self, *, tenant_id: str) -> int | None:
        """Distinct-ish indexed chunk count for a tenant (chunk-level; honest approximation
        of 'items indexed'). Returns None on any search error so the tile shows '—'."""
        try:
            results = await self._cli.search(
                search_text="*",
                filter=f"tenant_id eq '{tenant_id.replace(chr(39), chr(39) * 2)}'",
                top=0,
                include_total_count=True,
            )
            return int(await results.get_count() or 0)
        except Exception:  # noqa: BLE001 — degrade to unknown
            return None
```

- [ ] **Step 5: Run, verify pass.** Run: `cd brain-api && uv run pytest tests/test_metrics_store.py -v`. Expected: pass.
- [ ] **Step 6: Commit**

```bash
git add brain-api/app/metrics/ brain-api/app/retrieval/ai_search_client.py brain-api/tests/test_metrics_store.py
git commit -m "feat(metrics): query/active-user metrics + AI Search count_docs"
```

---

## Task 7: SharePointConnector (MS Graph)

**Files:**
- Create: `brain-api/app/connectors/sharepoint.py`
- Test: `brain-api/tests/test_connector_sharepoint.py`

Token via `DefaultAzureCredential` `.default` (copy the `_token` pattern from `app/live_fetch/graph_search.py`). Methods never raise — `[]`/`None` on error. Parsing is split into pure functions so tests don't need Graph/credentials.

- [ ] **Step 1: Write failing tests** (test the pure parsers + a fetch with a mocked HTTP client)

```python
# brain-api/tests/test_connector_sharepoint.py
import pytest
from app.connectors.sharepoint import _parse_sites, _parse_drive_children, SharePointConnector

def test_parse_sites():
    data={"value":[{"id":"s1","displayName":"Sales","webUrl":"https://x/sales"},
                   {"id":"s2","name":"Eng","webUrl":"https://x/eng"}]}
    sites=_parse_sites(data)
    assert sites[0]=={"site_id":"s1","name":"Sales","web_url":"https://x/sales"}
    assert sites[1]["name"]=="Eng"

def test_parse_drive_children_splits_files_and_folders():
    data={"value":[
        {"id":"f1","name":"plan.docx","file":{"mimeType":"application/vnd...docx"},
         "size":10,"webUrl":"https://x/plan","createdBy":{"user":{"id":"u1"}}},
        {"id":"d1","name":"sub","folder":{"childCount":2}},
    ]}
    files, folders = _parse_drive_children(data, drive_id="dr1")
    assert len(files)==1 and files[0].name=="plan.docx" and files[0].author_id=="u1"
    assert folders==["d1"]

@pytest.mark.asyncio
async def test_list_sites_degrades_on_error(monkeypatch):
    c=SharePointConnector()
    async def boom(*a, **k): raise RuntimeError("401")
    monkeypatch.setattr(c, "_get_json", boom)
    assert await c.list_sites()==[]
```

- [ ] **Step 2: Run, verify fail.** Run: `cd brain-api && uv run pytest tests/test_connector_sharepoint.py -v`.
- [ ] **Step 3: Implement `sharepoint.py`**

```python
from __future__ import annotations

import logging
from datetime import datetime

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.config import get_settings
from app.connectors.extract import is_supported
from app.connectors.models import RemoteFile

logger = logging.getLogger(__name__)
_GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"


def _parse_sites(data: dict) -> list[dict]:
    out = []
    for s in data.get("value", []):
        out.append({"site_id": s.get("id", ""),
                    "name": s.get("displayName") or s.get("name") or "Untitled",
                    "web_url": s.get("webUrl", "")})
    return [s for s in out if s["site_id"]]


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _parse_drive_children(data: dict, drive_id: str) -> tuple[list[RemoteFile], list[str]]:
    files: list[RemoteFile] = []
    folders: list[str] = []
    for it in data.get("value", []):
        if "folder" in it:
            folders.append(it.get("id", ""))
            continue
        if "file" not in it:
            continue
        name = it.get("name", "")
        author = ((it.get("createdBy") or {}).get("user") or {}).get("id") \
            or ((it.get("lastModifiedBy") or {}).get("user") or {}).get("id")
        files.append(RemoteFile(
            drive_id=drive_id, item_id=it.get("id", ""), name=name,
            mime=(it.get("file") or {}).get("mimeType"),
            web_url=it.get("webUrl", ""), size=int(it.get("size") or 0),
            created_at=_dt(it.get("createdDateTime")),
            modified_at=_dt(it.get("lastModifiedDateTime")),
            author_id=author))
    return files, [f for f in folders if f]


class SharePointConnector:
    """MS Graph SharePoint reader. Single-identity (DefaultAzureCredential .default).
    Returns 401-empty until Sites.Read.All/Files.Read.All are consented. Never raises."""

    async def _token(self) -> str:
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token(_SCOPE)
            return tok.token
        finally:
            await cred.close()

    async def _get_json(self, url: str) -> dict:
        token = await self._token()
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.get(url, headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            return r.json()

    async def list_sites(self) -> list[dict]:
        try:
            data = await self._get_json(f"{_GRAPH}/sites?search=*&$top=50")
            return _parse_sites(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("list_sites failed (Graph perm pending?): %s", e)
            return []

    async def list_files(self, site_id: str, max_items: int | None = None) -> list[RemoteFile]:
        """Enumerate files across the site's default drive (recursive, BFS). Caps at
        connector_max_items. Returns [] on any error."""
        cap = max_items or get_settings().connector_max_items
        try:
            drive = await self._get_json(f"{_GRAPH}/sites/{site_id}/drive")
            drive_id = drive.get("id", "")
            if not drive_id:
                return []
            files: list[RemoteFile] = []
            queue = [f"{_GRAPH}/drives/{drive_id}/root/children"]
            while queue and len(files) < cap:
                data = await self._get_json(queue.pop(0))
                fs, folder_ids = _parse_drive_children(data, drive_id)
                files.extend(f for f in fs if is_supported(f.name, f.mime))
                for fid in folder_ids:
                    queue.append(f"{_GRAPH}/drives/{drive_id}/items/{fid}/children")
            return files[:cap]
        except Exception as e:  # noqa: BLE001
            logger.warning("list_files failed for site %s: %s", site_id, e)
            return []

    async def fetch_content(self, drive_id: str, item_id: str) -> bytes | None:
        try:
            token = await self._token()
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                r = await http.get(f"{_GRAPH}/drives/{drive_id}/items/{item_id}/content",
                                   headers={"Authorization": f"Bearer {token}"})
                r.raise_for_status()
                return r.content
        except Exception as e:  # noqa: BLE001
            logger.warning("fetch_content failed for %s: %s", item_id, e)
            return None
```

- [ ] **Step 4: Run, verify pass.** Run: `cd brain-api && uv run pytest tests/test_connector_sharepoint.py -v`. Expected: pass.
- [ ] **Step 5: Commit**

```bash
git add brain-api/app/connectors/sharepoint.py brain-api/tests/test_connector_sharepoint.py
git commit -m "feat(connectors): SharePoint Graph reader (sites/files/content, degrades)"
```

---

## Task 8: SyncRunner

**Files:**
- Create: `brain-api/app/connectors/sync.py`
- Test: `brain-api/tests/test_connector_sync.py`

Enumerate → for each file fetch+extract → `SourceDoc` → `IngestPipeline.process()`; update job counters in the store as it goes; set connection live/error at the end; log activity.

- [ ] **Step 1: Write failing test** (fake connector + fake pipeline + FakeRedis store)

```python
# brain-api/tests/test_connector_sync.py
import pytest
from app.connectors.models import Connection, RemoteFile, SyncJob
from app.connectors.store import ConnectionStore
from app.connectors.sync import SyncRunner
from tests.test_connector_store import FakeRedis  # reuse

class FakeConnector:
    def __init__(self, files, contents): self._f=files; self._c=contents
    async def list_files(self, site_id, max_items=None): return self._f
    async def fetch_content(self, drive_id, item_id): return self._c.get(item_id)

class FakePipeline:
    def __init__(self): self.calls=[]
    async def process(self, doc):
        self.calls.append(doc)
        class R: chunks_indexed=2; doc_id=doc.doc_id
        return R()

@pytest.mark.asyncio
async def test_sync_ingests_supported_files_and_completes():
    files=[RemoteFile(drive_id="d",item_id="i1",name="a.md",web_url="https://x/a",size=5),
           RemoteFile(drive_id="d",item_id="i2",name="b.md",web_url="https://x/b",size=5)]
    conn=FakeConnector(files, {"i1": b"# A", "i2": b"# B"})
    pipe=FakePipeline(); store=ConnectionStore(client=FakeRedis())
    c=Connection(connection_id="c1",tenant_id="t",site_id="s",name="Sales",web_url="https://x")
    await store.put_connection(c)
    runner=SyncRunner(connector=conn, pipeline=pipe, store=store)
    job=await runner.run(connection=c, actor="admin")
    assert job.status=="succeeded" and job.processed==2 and job.skipped==0
    assert {d.source for d in pipe.calls}=={"sharepoint"}
    assert pipe.calls[0].doc_id=="sp:s:i1" and pipe.calls[0].acl_principals==["t:everyone"]
    refreshed=await store.get_connection("t","c1")
    assert refreshed.status=="live" and refreshed.item_count==2

@pytest.mark.asyncio
async def test_sync_skips_unfetchable_and_counts():
    files=[RemoteFile(drive_id="d",item_id="i1",name="a.md",web_url="https://x",size=5),
           RemoteFile(drive_id="d",item_id="i2",name="b.md",web_url="https://x",size=5)]
    conn=FakeConnector(files, {"i1": b"# A"})  # i2 content missing → skip
    pipe=FakePipeline(); store=ConnectionStore(client=FakeRedis())
    c=Connection(connection_id="c1",tenant_id="t",site_id="s",name="S",web_url="https://x")
    runner=SyncRunner(connector=conn, pipeline=pipe, store=store)
    job=await runner.run(connection=c, actor="admin")
    assert job.processed==1 and job.skipped==1 and job.status=="succeeded"

@pytest.mark.asyncio
async def test_sync_no_files_marks_live_zero():
    conn=FakeConnector([], {}); pipe=FakePipeline(); store=ConnectionStore(client=FakeRedis())
    c=Connection(connection_id="c1",tenant_id="t",site_id="s",name="S",web_url="https://x")
    runner=SyncRunner(connector=conn, pipeline=pipe, store=store)
    job=await runner.run(connection=c, actor="admin")
    assert job.status=="succeeded" and job.total==0
```

- [ ] **Step 2: Run, verify fail.** Run: `cd brain-api && uv run pytest tests/test_connector_sync.py -v`.
- [ ] **Step 3: Implement `sync.py`**

```python
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.connectors.extract import extract_text
from app.connectors.models import ActivityEntry, Connection, SyncJob
from app.connectors.store import ConnectionStore
from app.domain.chunk import SourceDoc

logger = logging.getLogger(__name__)


class SyncRunner:
    def __init__(self, *, connector, pipeline, store: ConnectionStore) -> None:
        self._connector = connector
        self._pipeline = pipeline
        self._store = store

    async def run(self, *, connection: Connection, actor: str = "admin") -> SyncJob:
        tenant = connection.tenant_id
        job = SyncJob(job_id=uuid.uuid4().hex, tenant_id=tenant,
                      connection_id=connection.connection_id, status="running",
                      started_at=datetime.now(UTC))
        connection.status = "syncing"; connection.last_job_id = job.job_id
        await self._store.put_connection(connection)
        await self._store.put_job(job)

        cap = get_settings().connector_max_items
        try:
            files = await self._connector.list_files(connection.site_id, max_items=cap)
            job.total = len(files)
            job.truncated = len(files) >= cap
            await self._store.put_job(job)

            for f in files:
                data = await self._connector.fetch_content(f.drive_id, f.item_id)
                text = extract_text(data, f.mime, f.name) if data is not None else None
                if not text:
                    job.skipped += 1
                else:
                    now = datetime.now(UTC)
                    doc = SourceDoc(
                        doc_id=f"sp:{connection.site_id}:{f.item_id}",
                        tenant_id=tenant, source="sharepoint", source_url=f.web_url,
                        title=f.name, body=text, author_id=f.author_id,
                        acl_principals=[f"{tenant}:everyone"],
                        created_at=f.created_at or now, modified_at=f.modified_at or now,
                        mime=f.mime or "text/plain")
                    try:
                        await self._pipeline.process(doc)
                        job.processed += 1
                    except Exception as e:  # noqa: BLE001 — skip the file, keep syncing
                        logger.warning("ingest failed for %s: %s", f.name, e)
                        job.errors += 1
                await self._store.put_job(job)

            job.status = "succeeded"
            connection.status = "live"
            connection.item_count = job.processed
        except Exception as e:  # noqa: BLE001
            logger.warning("sync run failed: %s", e)
            job.status = "failed"; job.message = str(e)
            connection.status = "error"; connection.error = str(e)

        job.finished_at = datetime.now(UTC)
        connection.last_sync = job.finished_at
        await self._store.put_job(job)
        await self._store.put_connection(connection)
        await self._store.log_activity(tenant, ActivityEntry(
            ts=job.finished_at, actor=actor, kind="sync",
            text=f"Synced {connection.name}: {job.processed} indexed, {job.skipped} skipped"
            + (f", {job.errors} errors" if job.errors else "")
            + (" (truncated)" if job.truncated else "")))
        return job
```

- [ ] **Step 4: Run, verify pass.** Run: `cd brain-api && uv run pytest tests/test_connector_sync.py -v`. Expected: pass.
- [ ] **Step 5: Commit**

```bash
git add brain-api/app/connectors/sync.py brain-api/tests/test_connector_sync.py
git commit -m "feat(connectors): SyncRunner (enumerate → SourceDoc → IngestPipeline)"
```

---

## Task 9: Admin API routes + wiring

**Files:**
- Modify: `brain-api/app/api/admin.py`
- Modify: `brain-api/app/deps.py`
- Modify: `brain-api/app/main.py` (lifespan + close)
- Modify: `brain-api/app/api/query.py` (record metrics)
- Test: `brain-api/tests/test_admin_api.py`

- [ ] **Step 1: Wire stores in `main.py` lifespan** — after `app.state.search_service = ...` add:

```python
    app.state.connection_store = ConnectionStore()
    app.state.metrics_store = MetricsStore()
    app.state.sharepoint = SharePointConnector()
```

Add imports at top:

```python
from app.connectors.store import ConnectionStore
from app.connectors.sharepoint import SharePointConnector
from app.metrics.store import MetricsStore
```

In the `finally:` block add:

```python
        await app.state.connection_store.aclose()
        await app.state.metrics_store.aclose()
```

- [ ] **Step 2: Add deps** — in `app/deps.py`:

```python
def get_connection_store(request: Request):
    return request.app.state.connection_store

def get_metrics_store(request: Request):
    return request.app.state.metrics_store

def get_sharepoint(request: Request):
    return request.app.state.sharepoint
```

- [ ] **Step 3: Record metrics on query** — in `app/api/query.py`, after `answer = await orchestrator.answer(...)`, add (best-effort, never blocks):

```python
    metrics = getattr(orchestrator, "_metrics", None)  # not available here; use app.state via Request
```

Instead, add a `Request` param and use app.state directly. Replace the handler signature to include `request: Request` (import `Request` from fastapi) and add after computing `answer`:

```python
    import contextlib
    with contextlib.suppress(Exception):
        await request.app.state.metrics_store.record_query(user.tenant_id, user.user_id)
```

- [ ] **Step 4: Write failing API tests**

```python
# brain-api/tests/test_admin_api.py
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.admin import router, require_admin_key
from app.connectors.store import ConnectionStore
from tests.test_connector_store import FakeRedis

def _app(monkeypatch, **state):
    from app import config
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings(
        azure_tenant_id="t", azure_client_id="c", azure_ai_search_endpoint="https://x",
        azure_ai_search_index="i", azure_openai_endpoint="https://x",
        azure_redis_host="h", admin_api_key="k", brain_tenant_id="t-eval"))
    app=FastAPI(); app.include_router(router)
    for key,val in state.items(): setattr(app.state, key, val)
    return app

@pytest.mark.asyncio
async def test_admin_requires_key(monkeypatch):
    app=_app(monkeypatch, connection_store=ConnectionStore(client=FakeRedis()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r=await c.get("/admin/connections")  # no key
        assert r.status_code==403
        r=await c.get("/admin/connections", headers={"x-admin-key":"k"})
        assert r.status_code==200 and r.json()==[]

@pytest.mark.asyncio
async def test_sites_degrades_empty(monkeypatch):
    class FakeSP:
        async def list_sites(self): return []
    app=_app(monkeypatch, sharepoint=FakeSP())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r=await c.get("/admin/sharepoint/sites", headers={"x-admin-key":"k"})
        assert r.status_code==200 and r.json()==[]
```

- [ ] **Step 5: Run, verify fail.** Run: `cd brain-api && uv run pytest tests/test_admin_api.py -v`.
- [ ] **Step 6: Add routes to `app/api/admin.py`** — add imports + models + endpoints (keep existing routes):

```python
import uuid
from fastapi import BackgroundTasks, Request

from app.connectors.models import Connection
from app.connectors.sharepoint import SharePointConnector
from app.connectors.store import ConnectionStore
from app.connectors.sync import SyncRunner
from app.deps import (get_connection_store, get_metrics_store, get_sharepoint,
                      get_ai_search, get_ingest_pipeline)
from app.domain.identity import User


class ConnectRequest(BaseModel):
    site_id: str
    name: str | None = None
    web_url: str | None = None


@router.get("/stats")
async def stats(request: Request,
                store: ConnectionStore = Depends(get_connection_store),
                metrics=Depends(get_metrics_store),
                ai_search=Depends(get_ai_search)) -> dict:
    tenant = get_settings().brain_tenant_id
    conns = await store.list_connections(tenant)
    items = await ai_search.count_docs(tenant_id=tenant)
    activity = await store.recent_activity(tenant, limit=10)
    sources_live = sum(1 for c in conns if c.status == "live")
    needs = []
    if not conns:
        needs.append({"text": "No data sources connected yet", "where": "Data Sources"})
    for c in conns:
        if c.status == "syncing":
            needs.append({"text": f"{c.name} is still indexing", "where": "Data Sources"})
        if c.status == "error":
            needs.append({"text": f"{c.name} sync failed: {c.error or 'unknown'}", "where": "Data Sources"})
    return {
        "active_users": await metrics.active_users_7d(tenant),
        "queries_7d": await metrics.queries_last_7d(tenant),
        "items_indexed": items,
        "sources_live": sources_live,
        "source_health": [
            {"name": c.name, "type": c.type, "status": c.status, "items": c.item_count}
            for c in conns],
        "recent_activity": [a.model_dump(mode="json") for a in activity],
        "needs_attention": needs,
    }


@router.get("/connections")
async def list_connections(store: ConnectionStore = Depends(get_connection_store)) -> list[dict]:
    tenant = get_settings().brain_tenant_id
    return [c.model_dump(mode="json") for c in await store.list_connections(tenant)]


@router.get("/sharepoint/sites")
async def sharepoint_sites(sp: SharePointConnector = Depends(get_sharepoint)) -> list[dict]:
    return await sp.list_sites()


@router.post("/connections")
async def create_connection(
    body: ConnectRequest, bg: BackgroundTasks,
    store: ConnectionStore = Depends(get_connection_store),
    sp: SharePointConnector = Depends(get_sharepoint),
    pipeline=Depends(get_ingest_pipeline)) -> dict:
    tenant = get_settings().brain_tenant_id
    conn = Connection(connection_id=uuid.uuid4().hex, tenant_id=tenant, type="sharepoint",
                      site_id=body.site_id, name=body.name or body.site_id,
                      web_url=body.web_url or "", status="syncing")
    await store.put_connection(conn)
    job_id = uuid.uuid4().hex  # placeholder; runner generates its own. We return connection.
    runner = SyncRunner(connector=sp, pipeline=pipeline, store=store)
    bg.add_task(runner.run, connection=conn, actor="admin")
    return {"connection_id": conn.connection_id, "status": "syncing"}


@router.post("/connections/{connection_id}/sync")
async def resync(connection_id: str, bg: BackgroundTasks,
                 store: ConnectionStore = Depends(get_connection_store),
                 sp: SharePointConnector = Depends(get_sharepoint),
                 pipeline=Depends(get_ingest_pipeline)) -> dict:
    tenant = get_settings().brain_tenant_id
    conn = await store.get_connection(tenant, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="connection not found")
    runner = SyncRunner(connector=sp, pipeline=pipeline, store=store)
    bg.add_task(runner.run, connection=conn, actor="admin")
    return {"connection_id": connection_id, "status": "syncing"}


@router.delete("/connections/{connection_id}")
async def disconnect(connection_id: str,
                     store: ConnectionStore = Depends(get_connection_store)) -> dict:
    tenant = get_settings().brain_tenant_id
    await store.delete_connection(tenant, connection_id)
    return {"connection_id": connection_id, "deleted": True}


@router.get("/connections/{connection_id}/job")
async def connection_job(connection_id: str,
                         store: ConnectionStore = Depends(get_connection_store)) -> dict:
    tenant = get_settings().brain_tenant_id
    conn = await store.get_connection(tenant, connection_id)
    if not conn or not conn.last_job_id:
        return {"status": "unknown"}
    job = await store.get_job(tenant, conn.last_job_id)
    return job.model_dump(mode="json") if job else {"status": "unknown"}
```

Remove the unused `job_id` placeholder line in `create_connection` (left in above by mistake — delete `job_id = uuid.uuid4().hex  # ...`).

- [ ] **Step 7: Run, verify pass.** Run: `cd brain-api && uv run pytest tests/test_admin_api.py -v`. Expected: pass.
- [ ] **Step 8: Run full backend suite + lint.** Run: `cd brain-api && uv run pytest -q && uv run ruff check app/`. Expected: all green.
- [ ] **Step 9: Commit**

```bash
git add brain-api/app/api/admin.py brain-api/app/api/query.py brain-api/app/deps.py brain-api/app/main.py brain-api/tests/test_admin_api.py
git commit -m "feat(admin): /admin stats/connections/sites/sync/job routes + metrics on query"
```

---

## Task 10: Admin API client (frontend)

**Files:**
- Create: `web/lib/adminApi.ts`

Reuses the `authedFetch` Easy-Auth wrapper pattern from `web/lib/api.ts`, plus an `x-admin-key` header read from `sessionStorage` (`adminKey`). A 403 throws `AdminAuthError` so the UI can re-prompt.

- [ ] **Step 1: Implement `adminApi.ts`**

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEBUG_AUTH = process.env.NEXT_PUBLIC_DEBUG_AUTH ?? "t-eval,u-demo,t-eval:everyone";

export class AdminAuthError extends Error {}

export function getAdminKey(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem("adminKey");
}
export function setAdminKey(k: string) { sessionStorage.setItem("adminKey", k); }
export function clearAdminKey() { sessionStorage.removeItem("adminKey"); }

let _idTokenPromise: Promise<string | null> | null = null;
async function easyAuthIdToken(): Promise<string | null> {
  if (!_idTokenPromise) {
    _idTokenPromise = fetch("/.auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => (Array.isArray(d) && d[0]?.id_token) || null)
      .catch(() => null);
  }
  return _idTokenPromise;
}

async function headers(): Promise<Record<string, string>> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const key = getAdminKey();
  if (key) h["x-admin-key"] = key;
  if (DEBUG_AUTH) h["x-debug-bypass-auth"] = DEBUG_AUTH;
  else { const t = await easyAuthIdToken(); if (t) h["Authorization"] = `Bearer ${t}`; }
  return h;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`,
    { ...init, headers: { ...(init.headers ?? {}), ...(await headers()) } });
  if (resp.status === 403) { clearAdminKey(); throw new AdminAuthError("admin key rejected"); }
  if (!resp.ok) throw new Error(`admin-api ${resp.status}: ${await resp.text()}`);
  return (await resp.json()) as T;
}

export type SourceHealth = { name: string; type: string; status: string; items: number };
export type ActivityItem = { ts: string; actor: string; text: string; kind: string };
export type NeedsItem = { text: string; where: string };
export type AdminStats = {
  active_users: number | null; queries_7d: number | null;
  items_indexed: number | null; sources_live: number;
  source_health: SourceHealth[]; recent_activity: ActivityItem[]; needs_attention: NeedsItem[];
};
export type Connection = {
  connection_id: string; type: string; site_id: string; name: string; web_url: string;
  status: string; item_count: number; last_sync: string | null; error: string | null;
};
export type SiteOption = { site_id: string; name: string; web_url: string };
export type SyncJob = {
  status: string; total: number; processed: number; skipped: number;
  errors: number; truncated: boolean; message: string | null;
};

export const getStats = () => call<AdminStats>("/admin/stats");
export const getConnections = () => call<Connection[]>("/admin/connections");
export const getSites = () => call<SiteOption[]>("/admin/sharepoint/sites");
export const connectSite = (s: SiteOption) =>
  call<{ connection_id: string; status: string }>("/admin/connections",
    { method: "POST", body: JSON.stringify({ site_id: s.site_id, name: s.name, web_url: s.web_url }) });
export const resync = (id: string) =>
  call<{ status: string }>(`/admin/connections/${id}/sync`, { method: "POST" });
export const disconnect = (id: string) =>
  call<{ deleted: boolean }>(`/admin/connections/${id}`, { method: "DELETE" });
export const getJob = (id: string) => call<SyncJob>(`/admin/connections/${id}/job`);
```

- [ ] **Step 2: Typecheck.** Run: `cd web && pnpm typecheck`. Expected: no errors.
- [ ] **Step 3: Commit**

```bash
git add web/lib/adminApi.ts
git commit -m "feat(web/admin): admin api client (x-admin-key + easy-auth)"
```

---

## Task 11: Admin shell layout + key gate

**Files:**
- Create: `web/app/admin/layout.tsx`

Client component. Renders the left nav rail (matching the mockup groups) + an admin-key gate overlay shown until a key is present in sessionStorage. Active nav item via `usePathname()`.

- [ ] **Step 1: Implement `layout.tsx`**

```tsx
"use client";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getAdminKey, setAdminKey } from "@/lib/adminApi";

const NAV = [
  { group: "Workspace", items: [{ href: "/admin", label: "Overview" }] },
  { group: "Connect", items: [
    { href: "/admin/sources", label: "Data Sources" },
    { href: "/admin/surfaces", label: "Surfaces" },
    { href: "/admin/permissions", label: "Permissions" }] },
  { group: "Build", items: [{ href: "/admin/developer", label: "Developer" }] },
];

function Gate({ onUnlock }: { onUnlock: () => void }) {
  const [val, setVal] = useState("");
  return (
    <div className="admin-gate">
      <form className="admin-gate-card" onSubmit={(e) => { e.preventDefault(); if (val) { setAdminKey(val); onUnlock(); } }}>
        <div className="glyph" />
        <h2>Admin access</h2>
        <p>Enter the admin key to manage data sources.</p>
        <input type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder="Admin key" autoFocus />
        <button type="submit">Unlock</button>
      </form>
    </div>
  );
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [unlocked, setUnlocked] = useState(false);
  useEffect(() => { setUnlocked(!!getAdminKey()); }, []);
  return (
    <div className="app app--norail admin">
      <aside className="rail">
        <div className="brand">
          <div className="glyph" />
          <div><h1>SubStrate<span style={{ color: "var(--amber)" }}>OS</span></h1>
            <div className="sub">Admin</div></div>
        </div>
        {NAV.map((g) => (
          <div key={g.group}>
            <h2>{g.group}</h2>
            <nav className="nav">
              {g.items.map((it) => (
                <Link key={it.href} href={it.href}
                  className={path === it.href ? "active" : ""}>{it.label}</Link>
              ))}
            </nav>
          </div>
        ))}
        <div className="foot"><div className="avatar">A</div>
          <div className="who">Admin<span>t-eval</span></div></div>
      </aside>
      <main className="main">{unlocked ? children : <Gate onUnlock={() => setUnlocked(true)} />}</main>
    </div>
  );
}
```

NOTE TO IMPLEMENTER: Ensure the `@/lib/adminApi` path alias resolves (it does — see `tsconfig.json` `paths`).

- [ ] **Step 2: Typecheck.** Run: `cd web && pnpm typecheck`. Expected: no errors.
- [ ] **Step 3: Commit**

```bash
git add web/app/admin/layout.tsx
git commit -m "feat(web/admin): admin shell layout + key gate"
```

---

## Task 12: Overview page

**Files:**
- Create: `web/app/admin/page.tsx`

- [ ] **Step 1: Implement `page.tsx`**

```tsx
"use client";
import { useEffect, useState } from "react";
import { getStats, AdminStats } from "@/lib/adminApi";

const fmt = (n: number | null) => (n === null || n === undefined ? "—" : n.toLocaleString());

export default function Overview() {
  const [s, setS] = useState<AdminStats | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { getStats().then(setS).catch(() => setErr(true)); }, []);
  return (
    <div className="admin-page">
      <header className="admin-head"><h1>Overview</h1>
        <p>Your work context layer at a glance.</p></header>
      {err && <div className="admin-note">Couldn’t load stats. Check the admin key / API.</div>}
      <div className="tiles">
        <Tile label="Active users" value={fmt(s?.active_users ?? null)} hint="last 7d" />
        <Tile label="Sources live" value={fmt(s?.sources_live ?? 0)} hint="connected" />
        <Tile label="Items indexed" value={fmt(s?.items_indexed ?? null)} hint="chunks" />
        <Tile label="Queries · 7d" value={fmt(s?.queries_7d ?? null)} hint="total" />
      </div>
      <div className="admin-cols">
        <section className="card">
          <h3>Needs attention</h3>
          {(s?.needs_attention ?? []).length === 0 && <p className="muted">All clear.</p>}
          {(s?.needs_attention ?? []).map((n, i) => (
            <div className="attn" key={i}><span className="dot amber" /><div>{n.text}</div>
              <span className="where">{n.where}</span></div>))}
        </section>
        <section className="card">
          <h3>Source health</h3>
          {(s?.source_health ?? []).length === 0 && <p className="muted">No sources yet.</p>}
          {(s?.source_health ?? []).map((h, i) => (
            <div className="health" key={i}>
              <span className={`dot ${h.status === "live" ? "green" : h.status === "error" ? "rose" : "amber"}`} />
              <div className="hn">{h.name}<span>{h.items} items</span></div>
              <span className="status">{h.status}</span></div>))}
        </section>
      </div>
      <section className="card">
        <h3>Recent activity</h3>
        {(s?.recent_activity ?? []).length === 0 && <p className="muted">No activity yet.</p>}
        {(s?.recent_activity ?? []).map((a, i) => (
          <div className="activity" key={i}><span className="dot green" /><div>{a.text}</div>
            <span className="who-when">{a.actor}</span></div>))}
      </section>
    </div>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (<div className="tile"><div className="tile-label">{label}</div>
    <div className="tile-value">{value}</div><div className="tile-hint">{hint}</div></div>);
}
```

- [ ] **Step 2: Typecheck.** Run: `cd web && pnpm typecheck`. Expected: no errors.
- [ ] **Step 3: Commit**

```bash
git add web/app/admin/page.tsx
git commit -m "feat(web/admin): Overview dashboard"
```

---

## Task 13: Data Sources page (connect flow + polling)

**Files:**
- Create: `web/app/admin/sources/page.tsx`

Lists connections; "Connect SharePoint" opens a site picker (`getSites()`); selecting + Connect calls `connectSite`, then polls `getJob(connection_id)` every 2s until status is `succeeded|failed|unknown`, refreshing the list. Empty site list shows the "Graph permission pending" note.

- [ ] **Step 1: Implement `sources/page.tsx`**

```tsx
"use client";
import { useEffect, useState, useCallback } from "react";
import { getConnections, getSites, connectSite, resync, disconnect, getJob,
         Connection, SiteOption, SyncJob } from "@/lib/adminApi";

export default function DataSources() {
  const [conns, setConns] = useState<Connection[]>([]);
  const [picking, setPicking] = useState(false);
  const [sites, setSites] = useState<SiteOption[] | null>(null);
  const [job, setJob] = useState<SyncJob | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(() => { getConnections().then(setConns).catch(() => {}); }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const openPicker = () => { setPicking(true); setSites(null); getSites().then(setSites).catch(() => setSites([])); };

  const poll = useCallback((id: string) => {
    const tick = async () => {
      try {
        const j = await getJob(id); setJob(j);
        if (["succeeded", "failed", "unknown"].includes(j.status)) { refresh(); setBusyId(null); return; }
      } catch { /* keep trying */ }
      setTimeout(tick, 2000);
    };
    tick();
  }, [refresh]);

  const onConnect = async (s: SiteOption) => {
    setPicking(false); setBusyId("new");
    const r = await connectSite(s); refresh(); poll(r.connection_id);
  };
  const onResync = async (id: string) => { setBusyId(id); await resync(id); poll(id); };
  const onDisconnect = async (id: string) => { await disconnect(id); refresh(); };

  return (
    <div className="admin-page">
      <header className="admin-head"><h1>Data Sources</h1>
        <p>Connect SharePoint to bring its files into the intelligence layer.</p></header>

      <div className="connect-row">
        <button className="connect-card" onClick={openPicker}>
          <div className="ci sp">SP</div><div><b>SharePoint</b><span>Sites &amp; document libraries</span></div></button>
        <div className="connect-card soon"><div className="ci">OD</div><div><b>OneDrive</b><span>Soon</span></div></div>
        <div className="connect-card soon"><div className="ci">TM</div><div><b>Teams</b><span>Soon</span></div></div>
      </div>

      {busyId && job && (
        <div className="card sync-status">
          <b>Syncing…</b> {job.processed}/{job.total} indexed · {job.skipped} skipped
          {job.errors ? ` · ${job.errors} errors` : ""}{job.truncated ? " · truncated" : ""}
          <div className="bar"><span style={{ width: `${job.total ? (100 * (job.processed + job.skipped)) / job.total : 0}%` }} /></div>
        </div>)}

      <section className="card">
        <h3>Connected sources</h3>
        {conns.length === 0 && <p className="muted">Nothing connected yet.</p>}
        <table className="conn-table"><tbody>
          {conns.map((c) => (
            <tr key={c.connection_id}>
              <td><b>{c.name}</b><span className="sub2">{c.type}</span></td>
              <td><span className={`pill ${c.status}`}>{c.status}</span></td>
              <td>{c.item_count} items</td>
              <td className="actions">
                <button onClick={() => onResync(c.connection_id)} disabled={busyId === c.connection_id}>Sync</button>
                <button className="danger" onClick={() => onDisconnect(c.connection_id)}>Disconnect</button>
              </td>
            </tr>))}
        </tbody></table>
      </section>

      {picking && (
        <div className="admin-modal" onClick={() => setPicking(false)}>
          <div className="admin-modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>Connect a SharePoint site</h3>
            {sites === null && <p className="muted">Loading sites…</p>}
            {sites !== null && sites.length === 0 && (
              <p className="muted">No sites available. Connecting is blocked until the
                <b> Sites.Read.All</b> Graph permission is consented on the SubStrateOS app.</p>)}
            {(sites ?? []).map((s) => (
              <button key={s.site_id} className="site-row" onClick={() => onConnect(s)}>
                <b>{s.name}</b><span>{s.web_url}</span></button>))}
            <button className="modal-close" onClick={() => setPicking(false)}>Cancel</button>
          </div>
        </div>)}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck.** Run: `cd web && pnpm typecheck`. Expected: no errors.
- [ ] **Step 3: Commit**

```bash
git add web/app/admin/sources/page.tsx
git commit -m "feat(web/admin): Data Sources — connect SharePoint + live sync progress"
```

---

## Task 14: Stub pages

**Files:**
- Create: `web/app/admin/surfaces/page.tsx`, `web/app/admin/permissions/page.tsx`, `web/app/admin/developer/page.tsx`

- [ ] **Step 1: Implement three stubs** (same shape, different title)

```tsx
// web/app/admin/surfaces/page.tsx
export default function Surfaces() {
  return (<div className="admin-page"><header className="admin-head"><h1>Surfaces</h1>
    <p>Where the brain shows up — Web, Teams, Slack, API.</p></header>
    <div className="card"><p className="muted">Coming soon.</p></div></div>);
}
```

Repeat for `permissions/page.tsx` (title "Permissions", subtitle "Who can access what.") and `developer/page.tsx` (title "Developer", subtitle "API keys & integrations.").

- [ ] **Step 2: Typecheck.** Run: `cd web && pnpm typecheck`. Expected: no errors.
- [ ] **Step 3: Commit**

```bash
git add web/app/admin/surfaces/page.tsx web/app/admin/permissions/page.tsx web/app/admin/developer/page.tsx
git commit -m "feat(web/admin): Surfaces/Permissions/Developer stub pages"
```

---

## Task 15: Admin styles

**Files:**
- Modify: `web/app/globals.css` (append admin block at end)

Reuse existing tokens. Port the visual treatment finalized in the Task 1 mockups. Add classes: `.admin-page`, `.admin-head`, `.tiles`/`.tile*`, `.admin-cols`, `.card`, `.attn`/`.health`/`.activity`, `.dot.green|.amber|.rose`, `.connect-row`/`.connect-card`/`.ci`, `.conn-table`/`.pill.live|.syncing|.error`, `.sync-status`/`.bar`, `.admin-modal*`/`.site-row`, `.admin-gate*`, `.muted`/`.where`/`.sub2`.

- [ ] **Step 1: Append the admin CSS block** (full block — derived from the approved mockups; keep within the existing token system, no new colors beyond the `:root` vars).

```css
  /* ===== Admin panel ===== */
  .admin .main{overflow:auto}
  .admin-page{max-width:1080px;margin:0 auto;padding:34px 36px 60px;display:flex;flex-direction:column;gap:26px}
  .admin-head h1{font-family:"Fraunces",serif;font-size:30px;font-weight:600;margin:0}
  .admin-head p{color:var(--ink-faint);margin:4px 0 0}
  .admin-note{background:var(--amber-bg);border:1px solid #e7cd96;border-radius:12px;padding:11px 14px;font-size:13px}
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
  .tile{background:var(--surface);border:1px solid var(--line-soft);border-radius:var(--radius);padding:18px 18px 16px;box-shadow:var(--shadow)}
  .tile-label{font-size:12.5px;color:var(--ink-faint)}
  .tile-value{font-family:"Fraunces",serif;font-size:34px;font-weight:600;line-height:1.1;margin:6px 0 2px}
  .tile-hint{font-family:"JetBrains Mono",monospace;font-size:10px;color:var(--ink-faint);text-transform:uppercase;letter-spacing:.6px}
  .admin-cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .card{background:var(--surface);border:1px solid var(--line-soft);border-radius:var(--radius);padding:20px 22px;box-shadow:var(--shadow)}
  .card h3{font-family:"Fraunces",serif;font-size:16px;font-weight:600;margin:0 0 14px}
  .muted{color:var(--ink-faint);font-size:13px;margin:0}
  .attn,.health,.activity{display:flex;align-items:center;gap:11px;padding:10px 0;border-bottom:1px dashed var(--line-soft);font-size:13.5px}
  .attn:last-child,.health:last-child,.activity:last-child{border-bottom:none}
  .dot{width:9px;height:9px;border-radius:50%;flex:none}
  .dot.green{background:var(--green)}.dot.amber{background:var(--amber)}.dot.rose{background:var(--rose)}
  .where,.who-when,.status{margin-left:auto;font-family:"JetBrains Mono",monospace;font-size:10.5px;color:var(--ink-faint)}
  .health .hn{display:flex;flex-direction:column}.health .hn span{font-size:11px;color:var(--ink-faint)}
  .connect-row{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
  .connect-card{display:flex;align-items:center;gap:12px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:15px 16px;cursor:pointer;text-align:left;transition:.16s}
  .connect-card:hover{border-color:#cabd9f;box-shadow:var(--shadow)}
  .connect-card.soon{opacity:.5;cursor:default}
  .connect-card b{display:block;font-size:14px}.connect-card span{font-size:11.5px;color:var(--ink-faint)}
  .ci{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;font-family:"JetBrains Mono",monospace;font-size:12px;font-weight:700;color:#fff;background:linear-gradient(135deg,var(--amber),#9a5e0e);flex:none}
  .ci.sp{background:linear-gradient(135deg,#0f897e,#0a5f57)}
  .conn-table{width:100%;border-collapse:collapse}
  .conn-table td{padding:12px 8px;border-bottom:1px solid var(--line-soft);font-size:13.5px;vertical-align:middle}
  .conn-table .sub2{display:block;font-family:"JetBrains Mono",monospace;font-size:10px;color:var(--ink-faint)}
  .pill{font-family:"JetBrains Mono",monospace;font-size:10px;text-transform:uppercase;letter-spacing:.5px;padding:3px 9px;border-radius:99px;border:1px solid var(--line)}
  .pill.live{color:var(--green);border-color:rgba(63,143,94,.4);background:rgba(63,143,94,.1)}
  .pill.syncing{color:var(--amber);border-color:#e7cd96;background:var(--amber-bg)}
  .pill.error{color:var(--rose);border-color:rgba(200,84,106,.4);background:rgba(200,84,106,.1)}
  .actions{text-align:right}
  .actions button{font-size:12px;border:1px solid var(--line);background:var(--surface);border-radius:8px;padding:5px 11px;margin-left:6px;cursor:pointer;color:var(--ink-dim)}
  .actions button:hover{border-color:#cabd9f;color:var(--ink)}
  .actions button.danger:hover{color:var(--rose);border-color:rgba(200,84,106,.5)}
  .sync-status .bar{height:7px;background:var(--paper-2);border-radius:99px;margin-top:9px;overflow:hidden}
  .sync-status .bar span{display:block;height:100%;background:linear-gradient(90deg,var(--amber),#d68b1e);transition:width .4s}
  .admin-modal{position:fixed;inset:0;background:rgba(26,22,17,.34);display:grid;place-items:center;z-index:50;backdrop-filter:blur(3px)}
  .admin-modal-card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:24px;width:min(460px,92vw);max-height:80vh;overflow:auto;box-shadow:var(--shadow)}
  .admin-modal-card h3{font-family:"Fraunces",serif;font-size:18px;margin:0 0 14px}
  .site-row{display:flex;flex-direction:column;align-items:flex-start;width:100%;text-align:left;border:1px solid var(--line-soft);border-radius:11px;padding:11px 13px;margin-bottom:8px;background:var(--surface-2);cursor:pointer}
  .site-row:hover{border-color:#cabd9f}.site-row b{font-size:13.5px}.site-row span{font-size:11px;color:var(--ink-faint)}
  .modal-close{margin-top:6px;background:none;border:none;color:var(--ink-faint);cursor:pointer;font-size:12.5px}
  .admin-gate{display:grid;place-items:center;height:100%}
  .admin-gate-card{display:flex;flex-direction:column;align-items:center;gap:10px;background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:34px 38px;box-shadow:var(--shadow);width:min(360px,90vw)}
  .admin-gate-card h2{font-family:"Fraunces",serif;margin:6px 0 0}
  .admin-gate-card p{color:var(--ink-faint);font-size:13px;margin:0;text-align:center}
  .admin-gate-card input{width:100%;padding:10px 13px;border:1px solid var(--line);border-radius:10px;font-size:14px;background:var(--paper)}
  .admin-gate-card button{width:100%;padding:10px;border:none;border-radius:10px;background:linear-gradient(135deg,var(--amber),#9a5e0e);color:#fff;font-weight:600;cursor:pointer}
  @media(max-width:880px){.tiles{grid-template-columns:repeat(2,1fr)}.admin-cols,.connect-row{grid-template-columns:1fr}}
```

- [ ] **Step 2: Run the app, eyeball it.** Run (two terminals): `cd brain-api && uv run uvicorn app.main:app --port 8000` and `cd web && pnpm dev`. Visit `http://localhost:3000/admin`. Confirm: gate prompts; enter `dev-admin-key-local`; Overview renders tiles (items indexed real, others may be 0/—); Data Sources renders; "Connect SharePoint" opens picker showing the "permission pending" note (Graph 401 in dev) OR sites if available.
- [ ] **Step 3: Commit**

```bash
git add web/app/globals.css
git commit -m "feat(web/admin): admin panel styles (SubStrateOS tokens)"
```

---

## Task 16: Build + final verification

- [ ] **Step 1: Backend suite.** Run: `cd brain-api && uv run pytest -q`. Expected: all pass (existing + ~6 new test files).
- [ ] **Step 2: Backend lint.** Run: `cd brain-api && uv run ruff check app/`. Expected: clean.
- [ ] **Step 3: Frontend typecheck + build.** Run: `cd web && pnpm typecheck && pnpm build`. Expected: compiles; `/admin`, `/admin/sources`, `/admin/surfaces`, `/admin/permissions`, `/admin/developer` in the route manifest.
- [ ] **Step 4: Manual smoke** (dev servers running): gate → unlock → Overview tiles → Data Sources → connect attempt shows correct state. Verify a 403 (wrong key) re-prompts.
- [ ] **Step 5: Update memory** — append admin-panel status to `MEMORY.md` + a new memory file noting: shipped admin panel, `/admin` route, SharePoint connector wired but Graph-grant-blocked (Sites.Read.All/Files.Read.All), dev admin key `dev-admin-key-local`.
- [ ] **Step 6: Final commit (if any uncommitted)**

```bash
git add -A && git commit -m "chore(admin): final verification + memory"
```

---

## Self-Review Notes

- **Spec coverage:** placement (T11), admin gate/x-admin-key (T10/T11), SharePoint connector real+degrade (T7), Overview+Data Sources scope (T12/T13), stubs (T14), auto-ingest-everything (T8/T9 `create_connection`→`SyncRunner.run`), honest metrics (T6/T9), connection store (T5), API surface (T9), deps/config (T2), extraction (T3), mockups-first (T1). All covered.
- **Out-of-scope** items (scheduled sync, per-file ACL, pptx/xlsx, RBAC, purge-on-disconnect) intentionally absent.
- **Type consistency:** `Connection`/`SyncJob`/`RemoteFile`/`ActivityEntry` field names are reused verbatim across store/sync/api/adminApi. `getJob` hits `/admin/connections/{id}/job` (matches T9 route). `connector_max_items` consistent.
- **Known implementer traps flagged inline:** (a) delete the unused `job_id` placeholder line in T9 `create_connection`; (b) `count_docs` uses chunk-level count (honest approximation — labeled "chunks" in the tile hint).
```
