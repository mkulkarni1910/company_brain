# History & Discover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the two placeholder left-rail nav items into working surfaces — per-user **History** (re-run-live) and tenant-wide **Discover** (trending documents + activity-by-source), both ACL-respecting.

**Architecture:** History is a Redis list per user (write-on-query, read on demand). Discover aggregates the existing ADX `ActivityEvents` table for top docs + per-source activity, fetches doc metadata through AI Search **with the user's ACL filter**, and caches ~5 min in Redis. Two new GET endpoints resolve identity through the existing `resolve_user`. Frontend adds a `view` switcher to `Chat.tsx`.

**Tech Stack:** FastAPI, Pydantic, `redis.asyncio`, Azure Data Explorer (Kusto), Azure AI Search, Next.js 14 / React 18 / TypeScript, pytest, `uv`, `pnpm`.

**Conventions (read first):**
- Backend root is `brain-api/`; run all `uv`/`pytest` commands from there.
- Tests run without real Azure: `conftest.py` sets `ENABLE_DEBUG_AUTH=true` and dummy env. Non-`integration` tests must not open real connections — inject fakes.
- Debug auth header format: `x-debug-bypass-auth: <tenant>,<user_id>,<group...>` (e.g. `t-test,u-x,t-test:everyone`).
- After each backend task: `uv run ruff check <files>` must pass.
- Commit after every task.

---

## File Structure

Backend (`brain-api/`):
- Create `app/domain/history.py` — `HistoryEntry` model.
- Create `app/domain/discover.py` — `TrendingDoc`, `SourceActivity`, `DiscoverResult` models.
- Create `app/history/__init__.py`, `app/history/store.py` — `HistoryStore` (Redis list, injectable client).
- Modify `app/activity/store.py` — add `trending()` + `source_breakdown()` (degrade to empty on failure).
- Modify `app/retrieval/ai_search_client.py` — add `lookup_docs()` (ACL-filtered metadata fetch by doc_id).
- Create `app/discover/__init__.py`, `app/discover/service.py` — `DiscoverService`.
- Modify `app/deps.py` — `get_history_store`, `get_discover_service` (tolerant `getattr`).
- Create `app/api/history.py` — `GET /history`.
- Create `app/api/discover.py` — `GET /discover`.
- Modify `app/api/query.py` — fire-and-forget history write.
- Modify `app/main.py` — construct stores/service, include routers, close history store.
- Modify `app/api/admin.py` — richer `/admin/seed-activity` for demo data.
- Tests: `tests/test_history_store.py`, `tests/test_activity_trending.py`, `tests/test_lookup_docs.py`, `tests/test_discover_service.py`, `tests/test_history_discover_api.py`.

Frontend (`web/`):
- Modify `lib/api.ts` — types + `getHistory`, `getDiscover`, `logClick`.
- Modify `components/Chat.tsx` — view switcher + History/Discover views + citation click logging.
- Modify `app/globals.css` — styles for history list + discover cards.

---

## Task 1: Domain models

**Files:**
- Create: `brain-api/app/domain/history.py`
- Create: `brain-api/app/domain/discover.py`
- Test: `brain-api/tests/test_domain_history_discover.py`

- [ ] **Step 1: Write the failing test**

```python
# brain-api/tests/test_domain_history_discover.py
from datetime import UTC, datetime

from app.domain.discover import DiscoverResult, SourceActivity, TrendingDoc
from app.domain.history import HistoryEntry


def test_history_entry_roundtrips_json() -> None:
    e = HistoryEntry(query="pto?", query_id="q1", ts=datetime(2026, 5, 31, tzinfo=UTC))
    assert HistoryEntry.model_validate_json(e.model_dump_json()).query == "pto?"


def test_discover_result_shape() -> None:
    r = DiscoverResult(
        trending=[TrendingDoc(doc_id="d1", title="T", source="uploaded",
                              source_url="http://x", snippet="s", score=1.5)],
        by_source=[SourceActivity(source="uploaded", events=3, score=2.0)],
        window_days=14,
    )
    assert r.trending[0].doc_id == "d1"
    assert r.by_source[0].events == 3
    assert r.window_days == 14
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_domain_history_discover.py -q`
Expected: FAIL — `ModuleNotFoundError: app.domain.history`.

- [ ] **Step 3: Write the models**

```python
# brain-api/app/domain/history.py
from datetime import datetime

from pydantic import BaseModel


class HistoryEntry(BaseModel):
    query: str
    query_id: str
    ts: datetime
```

```python
# brain-api/app/domain/discover.py
from pydantic import BaseModel


class TrendingDoc(BaseModel):
    doc_id: str
    title: str
    source: str
    source_url: str
    snippet: str
    score: float


class SourceActivity(BaseModel):
    source: str
    events: int
    score: float


class DiscoverResult(BaseModel):
    trending: list[TrendingDoc]
    by_source: list[SourceActivity]
    window_days: int
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_domain_history_discover.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add brain-api/app/domain/history.py brain-api/app/domain/discover.py brain-api/tests/test_domain_history_discover.py
git commit -m "feat(history/discover): domain models"
```

---

## Task 2: HistoryStore (Redis list, injectable client)

**Files:**
- Create: `brain-api/app/history/__init__.py` (empty)
- Create: `brain-api/app/history/store.py`
- Test: `brain-api/tests/test_history_store.py`

- [ ] **Step 1: Write the failing test**

`fakeredis` is not installed, so inject a minimal async fake.

```python
# brain-api/tests/test_history_store.py
import pytest

from app.domain.identity import User
from app.history.store import HistoryStore


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}
        self.fail = False

    async def lpush(self, key, value):
        if self.fail:
            raise ConnectionError("down")
        self.store.setdefault(key, []).insert(0, value)

    async def ltrim(self, key, start, end):
        if self.fail:
            raise ConnectionError("down")
        self.store[key] = self.store.get(key, [])[start : end + 1]

    async def lrange(self, key, start, end):
        if self.fail:
            raise ConnectionError("down")
        return self.store.get(key, [])[start : end + 1]


def _user() -> User:
    return User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids=set())


@pytest.mark.asyncio
async def test_add_then_recent_is_newest_first() -> None:
    r = FakeRedis()
    s = HistoryStore(client=r)
    await s.add(user=_user(), query="first", query_id="q1")
    await s.add(user=_user(), query="second", query_id="q2")
    out = await s.recent(user=_user())
    assert [e.query for e in out] == ["second", "first"]


@pytest.mark.asyncio
async def test_caps_at_50() -> None:
    r = FakeRedis()
    s = HistoryStore(client=r)
    for i in range(60):
        await s.add(user=_user(), query=f"q{i}", query_id=str(i))
    assert len(r.store["history:t1:u1"]) == 50


@pytest.mark.asyncio
async def test_recent_degrades_to_empty_on_error() -> None:
    r = FakeRedis()
    s = HistoryStore(client=r)
    await s.add(user=_user(), query="x", query_id="q")
    r.fail = True
    assert await s.recent(user=_user()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_history_store.py -q`
Expected: FAIL — `ModuleNotFoundError: app.history.store`.

- [ ] **Step 3: Write the implementation**

```python
# brain-api/app/history/__init__.py
```

```python
# brain-api/app/history/store.py
from __future__ import annotations

import logging
from datetime import UTC, datetime

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.history import HistoryEntry
from app.domain.identity import User

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)
_MAX = 50


def _key(user: User) -> str:
    return f"history:{user.tenant_id}:{user.user_id}"


class HistoryStore:
    """Per-user recent-query list backed by a Redis list. Best-effort: all
    operations swallow Redis errors so history never breaks the query path."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        if client is not None:
            self._r = client
        else:
            s = get_settings()
            self._r = redis.Redis(
                host=s.azure_redis_host,
                port=s.azure_redis_port,
                ssl=s.azure_redis_ssl,
                password=s.redis_key,
                decode_responses=True,
            )

    async def aclose(self) -> None:
        try:
            await self._r.aclose()
        except Exception:  # noqa: BLE001 - close is best-effort
            pass

    async def add(self, *, user: User, query: str, query_id: str) -> None:
        entry = HistoryEntry(query=query, query_id=query_id, ts=datetime.now(UTC))
        key = _key(user)
        try:
            await self._r.lpush(key, entry.model_dump_json())
            await self._r.ltrim(key, 0, _MAX - 1)
        except _ERRORS as e:
            logger.warning("history add failed (key=%s): %s", key, e)

    async def recent(self, *, user: User, limit: int = _MAX) -> list[HistoryEntry]:
        key = _key(user)
        try:
            raw = await self._r.lrange(key, 0, max(0, limit - 1))
        except _ERRORS as e:
            logger.warning("history recent failed (key=%s): %s", key, e)
            return []
        out: list[HistoryEntry] = []
        for item in raw:
            try:
                out.append(HistoryEntry.model_validate_json(item))
            except Exception:  # noqa: BLE001 - skip corrupt entries
                continue
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_history_store.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app/history/store.py tests/test_history_store.py
git add brain-api/app/history/ brain-api/tests/test_history_store.py
git commit -m "feat(history): HistoryStore Redis list with degradation"
```

---

## Task 3: ActivityStore.trending + source_breakdown

**Files:**
- Modify: `brain-api/app/activity/store.py`
- Test: `brain-api/tests/test_activity_trending.py`

Window/limit are server-controlled ints, inlined into the KQL after `int()` coercion (injection-safe); `tid`/`dids` stay parameterized like the existing `engagement_scores`.

- [ ] **Step 1: Write the failing test**

```python
# brain-api/tests/test_activity_trending.py
import pytest

from app.activity.store import ActivityStore


class _Rows(list):
    pass


class _Resp:
    def __init__(self, rows):
        self.primary_results = [rows]


class FakeKusto:
    def __init__(self, rows, *, fail=False):
        self._rows = rows
        self.fail = fail
        self.last_query = None

    def execute_query(self, db, query, crp):
        self.last_query = query
        if self.fail:
            raise RuntimeError("adx down")
        return _Resp(self._rows)


def _store(fake) -> ActivityStore:
    s = ActivityStore.__new__(ActivityStore)  # bypass __init__ (no real cluster)
    s._db = "brain"
    s._client = fake
    return s


@pytest.mark.asyncio
async def test_trending_parses_and_orders() -> None:
    fake = FakeKusto([{"DocId": "d1", "score": 5.0}, {"DocId": "d2", "score": 2.0}])
    out = await _store(fake).trending(tenant_id="t1", window_days=14, limit=8)
    assert out == [("d1", 5.0), ("d2", 2.0)]
    assert "top 8 by score desc" in fake.last_query
    assert "ago(14d)" in fake.last_query


@pytest.mark.asyncio
async def test_trending_degrades_to_empty() -> None:
    out = await _store(FakeKusto([], fail=True)).trending(tenant_id="t1")
    assert out == []


@pytest.mark.asyncio
async def test_source_breakdown_parses() -> None:
    fake = FakeKusto([{"Source": "sharepoint", "events": 4, "score": 6.0}])
    out = await _store(fake).source_breakdown(
        tenant_id="t1", doc_ids=["d1", "d2"], window_days=14
    )
    assert out == [("sharepoint", 4, 6.0)]


@pytest.mark.asyncio
async def test_source_breakdown_empty_doc_ids() -> None:
    out = await _store(FakeKusto([])).source_breakdown(tenant_id="t1", doc_ids=[])
    assert out == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_activity_trending.py -q`
Expected: FAIL — `AttributeError: 'ActivityStore' object has no attribute 'trending'`.

- [ ] **Step 3: Add the methods**

Add these query builders near the top of `app/activity/store.py` (after the existing `_SCORE_QUERY` block) and import `logging` at top if not present (`import logging` and `logger = logging.getLogger(__name__)`):

```python
# --- Discover surface queries (window/limit are server ints, inlined safely) ---
_TYPE_WEIGHT = (
    "| extend type_weight = case("
    "EventType == 'thumbs_up', 2.0, EventType == 'thumbs_down', -2.0, "
    "EventType == 'dwell', 1.5, EventType == 'view', 1.0, EventType == 'click', 1.0, 0.0)\n"
)


def _trending_query(window_days: int, limit: int) -> str:
    w, lim = int(window_days), int(limit)
    return (
        "declare query_parameters(tid:string);\n"
        f"{_TABLE}\n"
        f"| where TenantId == tid and Timestamp > ago({w}d)\n"
        "| extend recency = exp(-1.0 * datetime_diff('day', now(), Timestamp) / 14.0)\n"
        f"{_TYPE_WEIGHT}"
        "| summarize score = sum(recency * type_weight), events = count() by DocId\n"
        "| where score > 0\n"
        f"| top {lim} by score desc"
    )


def _source_query(window_days: int) -> str:
    w = int(window_days)
    return (
        "declare query_parameters(tid:string, dids:string);\n"
        f"{_TABLE}\n"
        f"| where TenantId == tid and Timestamp > ago({w}d) and DocId in (todynamic(dids))\n"
        f"{_TYPE_WEIGHT}"
        "| summarize score = sum(type_weight), events = count() by Source\n"
        "| top 6 by score desc"
    )
```

Add these methods to the `ActivityStore` class (after `engagement_scores`):

```python
    async def trending(
        self, *, tenant_id: str, window_days: int = 14, limit: int = 8
    ) -> list[tuple[str, float]]:
        crp = ClientRequestProperties()
        crp.set_parameter("tid", tenant_id)
        query = _trending_query(window_days, limit)

        def _run():
            return self._client.execute_query(self._db, query, crp)

        try:
            resp = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001 - Discover degrades to empty
            logger.warning("ADX trending failed: %s", e)
            return []
        return [(row["DocId"], float(row["score"])) for row in resp.primary_results[0]]

    async def source_breakdown(
        self, *, tenant_id: str, doc_ids: list[str], window_days: int = 14
    ) -> list[tuple[str, int, float]]:
        if not doc_ids:
            return []
        crp = ClientRequestProperties()
        crp.set_parameter("tid", tenant_id)
        crp.set_parameter("dids", json.dumps(doc_ids))
        query = _source_query(window_days)

        def _run():
            return self._client.execute_query(self._db, query, crp)

        try:
            resp = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001 - Discover degrades to empty
            logger.warning("ADX source_breakdown failed: %s", e)
            return []
        return [
            (row["Source"], int(row["events"]), float(row["score"]))
            for row in resp.primary_results[0]
        ]
```

(`json`, `asyncio`, and `ClientRequestProperties` are already imported in this file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_activity_trending.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app/activity/store.py tests/test_activity_trending.py
git add brain-api/app/activity/store.py brain-api/tests/test_activity_trending.py
git commit -m "feat(activity): trending + source_breakdown KQL for Discover"
```

---

## Task 4: AISearchClient.lookup_docs (ACL-filtered metadata by doc_id)

**Files:**
- Modify: `brain-api/app/retrieval/ai_search_client.py`
- Test: `brain-api/tests/test_lookup_docs.py`

- [ ] **Step 1: Write the failing test**

```python
# brain-api/tests/test_lookup_docs.py
from datetime import UTC, datetime

import pytest

from app.domain.identity import User
from app.retrieval.ai_search_client import AISearchClient


def _doc(doc_id: str, chunk_id: str) -> dict:
    now = datetime(2026, 5, 31, tzinfo=UTC).isoformat()
    return {
        "chunk_id": chunk_id, "doc_id": doc_id, "tenant_id": "t1", "source": "uploaded",
        "source_url": f"http://x/{doc_id}", "title": doc_id.upper(), "content": "body text",
        "acl_principals": ["t1:everyone"], "author_id": None, "entities": [],
        "created_at": now, "modified_at": now, "chunk_index": 0,
    }


class FakeSearchResults:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield dict(d)
        return gen()


class FakeSearchCli:
    def __init__(self, docs):
        self._docs = docs
        self.last_filter = None

    async def search(self, *, search_text, filter, top, select):  # noqa: A002
        self.last_filter = filter
        return FakeSearchResults(self._docs)


def _client(docs) -> AISearchClient:
    c = AISearchClient.__new__(AISearchClient)  # bypass __init__ (no real endpoint)
    c._cli = FakeSearchCli(docs)
    return c


@pytest.mark.asyncio
async def test_lookup_docs_dedupes_by_doc_and_builds_acl_filter() -> None:
    user = User(user_id="u1", tenant_id="t1", email="", display_name="U",
                group_ids={"t1:everyone"})
    docs = [_doc("d1", "d1#0"), _doc("d1", "d1#1"), _doc("d2", "d2#0")]
    c = _client(docs)
    out = await c.lookup_docs(doc_ids=["d1", "d2"], user=user)
    assert set(out.keys()) == {"d1", "d2"}
    assert out["d1"].chunk_id == "d1#0"  # first chunk wins
    assert "tenant_id eq 't1'" in c._cli.last_filter
    assert "search.in(doc_id, 'd1,d2', ',')" in c._cli.last_filter


@pytest.mark.asyncio
async def test_lookup_docs_empty_ids() -> None:
    user = User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids=set())
    assert await _client([]).lookup_docs(doc_ids=[], user=user) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lookup_docs.py -q`
Expected: FAIL — `AttributeError: 'AISearchClient' object has no attribute 'lookup_docs'`.

- [ ] **Step 3: Add the method**

Add to the `AISearchClient` class in `app/retrieval/ai_search_client.py` (after `hybrid_search`):

```python
    async def lookup_docs(self, *, doc_ids: list[str], user: User) -> dict[str, "Chunk"]:
        """Fetch one chunk of metadata per doc_id, applying the user's ACL filter.
        Returns only docs the user can access (the ACL enforcement point for Discover)."""
        if not doc_ids:
            return {}
        ids = ",".join(d.replace("'", "''") for d in doc_ids)
        flt = f"({build_acl_filter(user)}) and search.in(doc_id, '{ids}', ',')"
        results = await self._cli.search(
            search_text="*",
            filter=flt,
            top=min(1000, len(doc_ids) * 20),
            select=[
                "chunk_id", "doc_id", "tenant_id", "source", "source_url", "title",
                "content", "acl_principals", "author_id", "entities", "created_at",
                "modified_at", "chunk_index",
            ],
        )
        out: dict[str, Chunk] = {}
        async for r in results:
            r["content_vector"] = []
            c = _from_search_doc(r)
            out.setdefault(c.doc_id, c)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_lookup_docs.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app/retrieval/ai_search_client.py tests/test_lookup_docs.py
git add brain-api/app/retrieval/ai_search_client.py brain-api/tests/test_lookup_docs.py
git commit -m "feat(search): lookup_docs ACL-filtered metadata fetch by doc_id"
```

---

## Task 5: DiscoverService

**Files:**
- Create: `brain-api/app/discover/__init__.py` (empty)
- Create: `brain-api/app/discover/service.py`
- Test: `brain-api/tests/test_discover_service.py`

- [ ] **Step 1: Write the failing test**

```python
# brain-api/tests/test_discover_service.py
from datetime import UTC, datetime

import pytest

from app.discover.service import DiscoverService
from app.domain.chunk import Chunk
from app.domain.identity import User


def _chunk(doc_id: str) -> Chunk:
    now = datetime(2026, 5, 31, tzinfo=UTC)
    return Chunk(
        chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t1", source="uploaded",
        source_url=f"http://x/{doc_id}", title=doc_id.upper(),
        content="some body content " * 20, acl_principals=["t1:everyone"],
        created_at=now, modified_at=now, chunk_index=0,
    )


class FakeActivity:
    def __init__(self, trending, sources):
        self._t = trending
        self._s = sources

    async def trending(self, *, tenant_id, window_days=14, limit=8):
        return self._t

    async def source_breakdown(self, *, tenant_id, doc_ids, window_days=14):
        return self._s


class FakeSearch:
    def __init__(self, docs):
        self._docs = docs

    async def lookup_docs(self, *, doc_ids, user):
        return {d: self._docs[d] for d in doc_ids if d in self._docs}


class FakeCache:
    def __init__(self):
        self.data = {}

    async def get_json(self, key):
        return self.data.get(key)

    async def set_json(self, key, value, ttl_seconds):
        self.data[key] = value


def _user():
    return User(user_id="u1", tenant_id="t1", email="", display_name="U",
                group_ids={"t1:everyone"})


@pytest.mark.asyncio
async def test_orders_by_score_drops_inaccessible_and_caps() -> None:
    activity = FakeActivity(
        trending=[("d1", 5.0), ("d2", 9.0), ("dX", 7.0)],  # dX not accessible
        sources=[("uploaded", 12, 14.0)],
    )
    search = FakeSearch({"d1": _chunk("d1"), "d2": _chunk("d2")})
    cache = FakeCache()
    svc = DiscoverService(activity=activity, search=search, cache=cache)
    res = await svc.result(user=_user(), limit=8)
    assert [t.doc_id for t in res.trending] == ["d2", "d1"]  # score desc, dX dropped
    assert res.by_source[0].source == "uploaded"
    assert res.window_days == 14
    assert cache.data  # cached


@pytest.mark.asyncio
async def test_returns_cached_when_present() -> None:
    cache = FakeCache()
    cache.data["discover:t1:u1"] = {
        "trending": [], "by_source": [], "window_days": 14,
    }
    svc = DiscoverService(
        activity=FakeActivity([("d1", 1.0)], []),
        search=FakeSearch({"d1": _chunk("d1")}),
        cache=cache,
    )
    res = await svc.result(user=_user())
    assert res.trending == []  # served from cache, activity not consulted


@pytest.mark.asyncio
async def test_degrades_when_no_activity() -> None:
    svc = DiscoverService(
        activity=FakeActivity([], []), search=FakeSearch({}), cache=FakeCache()
    )
    res = await svc.result(user=_user())
    assert res.trending == [] and res.by_source == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_discover_service.py -q`
Expected: FAIL — `ModuleNotFoundError: app.discover.service`.

- [ ] **Step 3: Write the service**

```python
# brain-api/app/discover/__init__.py
```

```python
# brain-api/app/discover/service.py
from __future__ import annotations

import logging

from app.activity.store import ActivityStore
from app.cache.redis_cache import RedisCache
from app.domain.discover import DiscoverResult, SourceActivity, TrendingDoc
from app.domain.identity import User
from app.retrieval.ai_search_client import AISearchClient

logger = logging.getLogger(__name__)
_CACHE_TTL = 300


class DiscoverService:
    """Tenant-wide Discover surface: trending docs + activity-by-source, ACL-scoped
    to the requesting user. Every sub-step degrades to empty rather than raising."""

    def __init__(
        self, *, activity: ActivityStore, search: AISearchClient, cache: RedisCache
    ) -> None:
        self._activity = activity
        self._search = search
        self._cache = cache

    async def result(
        self, *, user: User, window_days: int = 14, limit: int = 8
    ) -> DiscoverResult:
        key = f"discover:{user.tenant_id}:{user.user_id}"
        cached = await self._cache.get_json(key)
        if cached:
            try:
                return DiscoverResult.model_validate(cached)
            except Exception:  # noqa: BLE001 - ignore corrupt cache
                pass

        # over-fetch trending so ACL filtering still leaves `limit` docs
        scored = await self._activity.trending(
            tenant_id=user.tenant_id, window_days=window_days, limit=limit * 3
        )
        score_by_id = dict(scored)

        docs = {}
        if score_by_id:
            try:
                docs = await self._search.lookup_docs(
                    doc_ids=list(score_by_id), user=user
                )
            except Exception as e:  # noqa: BLE001 - degrade
                logger.warning("discover lookup_docs failed: %s", e)

        trending: list[TrendingDoc] = []
        for doc_id, _ in sorted(score_by_id.items(), key=lambda kv: kv[1], reverse=True):
            c = docs.get(doc_id)
            if c is None:
                continue
            trending.append(
                TrendingDoc(
                    doc_id=doc_id,
                    title=c.title,
                    source=c.source,
                    source_url=c.source_url,
                    snippet=c.content[:160].strip(),
                    score=round(score_by_id[doc_id], 3),
                )
            )
            if len(trending) >= limit:
                break

        by_source: list[SourceActivity] = []
        if trending:
            rows = await self._activity.source_breakdown(
                tenant_id=user.tenant_id,
                doc_ids=[t.doc_id for t in trending],
                window_days=window_days,
            )
            by_source = [
                SourceActivity(source=s, events=e, score=round(sc, 3))
                for s, e, sc in rows
            ]

        res = DiscoverResult(trending=trending, by_source=by_source, window_days=window_days)
        await self._cache.set_json(key, res.model_dump(mode="json"), ttl_seconds=_CACHE_TTL)
        return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_discover_service.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app/discover/service.py tests/test_discover_service.py
git add brain-api/app/discover/ brain-api/tests/test_discover_service.py
git commit -m "feat(discover): DiscoverService (trending + by-source, cached, degrading)"
```

---

## Task 6: Deps + lifespan wiring + history write on /query

**Files:**
- Modify: `brain-api/app/deps.py`
- Modify: `brain-api/app/main.py`
- Modify: `brain-api/app/api/query.py`

No new test here (covered by Task 7 endpoint tests + existing `tests/test_debug_auth_guard.py`, which must still pass because the new deps tolerate a missing lifespan).

- [ ] **Step 1: Add tolerant dependency providers**

Append to `app/deps.py` (and add imports `from app.history.store import HistoryStore` and `from app.discover.service import DiscoverService` at top):

```python
def get_history_store(request: Request) -> "HistoryStore | None":
    return getattr(request.app.state, "history_store", None)


def get_discover_service(request: Request) -> "DiscoverService | None":
    return getattr(request.app.state, "discover_service", None)
```

- [ ] **Step 2: Wire into lifespan + routers**

In `app/main.py`:

Add imports:
```python
from app.api.discover import router as discover_router
from app.api.history import router as history_router
from app.discover.service import DiscoverService
from app.history.store import HistoryStore
```

In `lifespan`, after `app.state.orchestrator = ...` (and before `try:`), add:
```python
    app.state.history_store = HistoryStore()
    app.state.discover_service = DiscoverService(
        activity=app.state.activity_store,
        search=app.state.ai_search,
        cache=app.state.cache,
    )
```

In the `finally:` block, add (before `await app.state.cache.aclose()`):
```python
        await app.state.history_store.aclose()
```

After the existing `app.include_router(feedback_router)` line, add:
```python
app.include_router(history_router)
app.include_router(discover_router)
```

- [ ] **Step 3: Write history on /query (fire-and-forget, guarded)**

Replace the body of `app/api/query.py`'s `query` function. Add `get_history_store` to the import and a dependency, and write history after answering:

```python
from app.deps import get_history_store, get_orchestrator
...

@router.post("/query", response_model=Answer)
async def query(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    history_store=Depends(get_history_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Answer:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    tok = (
        authorization.split(" ", 1)[1]
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    answer = await orchestrator.answer(body, user=user, user_token=tok)
    if history_store is not None:
        await history_store.add(user=user, query=body.query, query_id=answer.query_id)
    return answer
```

- [ ] **Step 4: Verify existing query tests still pass**

Run: `uv run pytest tests/test_debug_auth_guard.py tests/test_query_e2e.py -q`
Expected: PASS (existing behavior unchanged; `history_store` is `None` when lifespan didn't populate state, so the write is skipped).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check app/deps.py app/main.py app/api/query.py
git add brain-api/app/deps.py brain-api/app/main.py brain-api/app/api/query.py
git commit -m "feat(history/discover): wire stores into lifespan + write history on /query"
```

---

## Task 7: /history and /discover endpoints

**Files:**
- Create: `brain-api/app/api/history.py`
- Create: `brain-api/app/api/discover.py`
- Test: `brain-api/tests/test_history_discover_api.py`

- [ ] **Step 1: Write the failing test**

```python
# brain-api/tests/test_history_discover_api.py
from fastapi.testclient import TestClient

from app.deps import get_discover_service, get_history_store
from app.domain.discover import DiscoverResult, TrendingDoc
from app.domain.history import HistoryEntry
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class FakeHistory:
    async def recent(self, *, user, limit=50):
        from datetime import UTC, datetime
        return [HistoryEntry(query="pto?", query_id="q1", ts=datetime.now(UTC))]


class FakeDiscover:
    async def result(self, *, user, window_days=14, limit=8):
        return DiscoverResult(
            trending=[TrendingDoc(doc_id="d1", title="T", source="uploaded",
                                  source_url="http://x", snippet="s", score=1.0)],
            by_source=[], window_days=14,
        )


def test_history_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/history").status_code == 401


def test_history_returns_entries() -> None:
    app.dependency_overrides[get_history_store] = lambda: FakeHistory()
    try:
        with TestClient(app) as client:
            resp = client.get("/history", headers=_HDR)
        assert resp.status_code == 200
        assert resp.json()[0]["query"] == "pto?"
    finally:
        app.dependency_overrides.clear()


def test_history_empty_when_store_unavailable() -> None:
    app.dependency_overrides[get_history_store] = lambda: None
    try:
        with TestClient(app) as client:
            resp = client.get("/history", headers=_HDR)
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        app.dependency_overrides.clear()


def test_discover_returns_result() -> None:
    app.dependency_overrides[get_discover_service] = lambda: FakeDiscover()
    try:
        with TestClient(app) as client:
            resp = client.get("/discover", headers=_HDR)
        assert resp.status_code == 200
        assert resp.json()["trending"][0]["doc_id"] == "d1"
    finally:
        app.dependency_overrides.clear()


def test_discover_empty_when_service_unavailable() -> None:
    app.dependency_overrides[get_discover_service] = lambda: None
    try:
        with TestClient(app) as client:
            resp = client.get("/discover", headers=_HDR)
        assert resp.status_code == 200
        assert resp.json() == {"trending": [], "by_source": [], "window_days": 14}
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_history_discover_api.py -q`
Expected: FAIL — endpoints 404 / routers don't exist.

- [ ] **Step 3: Write the endpoints**

```python
# brain-api/app/api/history.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.api._auth_resolve import resolve_user
from app.deps import get_history_store
from app.domain.history import HistoryEntry

router = APIRouter(tags=["history"])


@router.get("/history", response_model=list[HistoryEntry])
async def history(
    limit: int = 50,
    store=Depends(get_history_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[HistoryEntry]:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    if store is None:
        return []
    return await store.recent(user=user, limit=min(max(limit, 1), 50))
```

```python
# brain-api/app/api/discover.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from app.api._auth_resolve import resolve_user
from app.deps import get_discover_service
from app.domain.discover import DiscoverResult

router = APIRouter(tags=["discover"])


@router.get("/discover", response_model=DiscoverResult)
async def discover(
    service=Depends(get_discover_service),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> DiscoverResult:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    if service is None:
        return DiscoverResult(trending=[], by_source=[], window_days=14)
    return await service.result(user=user)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_history_discover_api.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Full suite + lint + commit**

```bash
uv run pytest -q -m "not integration"
uv run ruff check app/api/history.py app/api/discover.py
git add brain-api/app/api/history.py brain-api/app/api/discover.py brain-api/tests/test_history_discover_api.py
git commit -m "feat(api): GET /history and GET /discover"
```

---

## Task 8: Frontend API client (types + getHistory + getDiscover + logClick)

**Files:**
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Add types + functions**

Append to `web/lib/api.ts` (the file already defines `API_BASE` and a module-local `async function authHeaders()`; reuse it):

```typescript
export type HistoryEntry = { query: string; query_id: string; ts: string };
export type TrendingDoc = {
  doc_id: string; title: string; source: string; source_url: string; snippet: string; score: number;
};
export type SourceActivity = { source: string; events: number; score: number };
export type DiscoverResult = { trending: TrendingDoc[]; by_source: SourceActivity[]; window_days: number };

export async function getHistory(): Promise<HistoryEntry[]> {
  try {
    const resp = await fetch(`${API_BASE}/history`, { headers: { ...(await authHeaders()) } });
    if (!resp.ok) return [];
    return (await resp.json()) as HistoryEntry[];
  } catch {
    return [];
  }
}

export async function getDiscover(): Promise<DiscoverResult> {
  const empty = { trending: [], by_source: [], window_days: 14 };
  try {
    const resp = await fetch(`${API_BASE}/discover`, { headers: { ...(await authHeaders()) } });
    if (!resp.ok) return empty;
    return (await resp.json()) as DiscoverResult;
  } catch {
    return empty;
  }
}

export async function logClick(doc_id: string, source: string, query_id?: string): Promise<void> {
  await fetch(`${API_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ doc_id, signal: "click", source, query_id }),
  }).catch(() => {/* best-effort */});
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && pnpm typecheck`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/lib/api.ts
git commit -m "feat(web): api client for history, discover, click logging"
```

---

## Task 9: Frontend views (view switcher + History + Discover) + styles

**Files:**
- Modify: `web/components/Chat.tsx`
- Modify: `web/app/globals.css`

- [ ] **Step 1: Add view state + nav switching + view components**

In `web/components/Chat.tsx`:

(a) Update imports:
```typescript
import { postQuery, postFeedback, getHistory, getDiscover, logClick,
  Answer, Citation, HistoryEntry, DiscoverResult } from "@/lib/api";
```

(b) Add a relative-time helper near `initials`:
```typescript
function relTime(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
```

(c) In the `Chat` component, add view state:
```typescript
const [view, setView] = useState<"ask" | "discover" | "history">("ask");
```

(d) Replace the three left-rail `<a>` nav items (currently `Ask`/`Discover`/`History` with `href="#"`) with buttons that switch the view, keeping the existing SVG icons. The `active` class follows `view`:
```tsx
<nav className="nav">
  <button className={view === "ask" ? "active" : ""} onClick={() => setView("ask")}>
    <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>Ask
  </button>
  <button className={view === "discover" ? "active" : ""} onClick={() => setView("discover")}>
    <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" /></svg>Discover
  </button>
  <button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>
    <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>History
  </button>
</nav>
```

(e) Render the main pane by view. Wrap the existing `<main className="main">…</main>` so it shows only when `view === "ask"`, and add History/Discover panes. The right rail (`rail--right`) should also render only when `view === "ask"`. Add these two components above `export default function Chat`:

```tsx
function HistoryView({ onPick }: { onPick: (q: string) => void }) {
  const [items, setItems] = useState<HistoryEntry[] | null>(null);
  useEffect(() => { getHistory().then(setItems); }, []);
  return (
    <main className="main">
      <header className="topbar"><div className="title">History</div></header>
      <div className="scroll">
        <div className="panel-wrap">
          {items === null && <div className="empty-p">Loading…</div>}
          {items?.length === 0 && <div className="empty-p">No questions yet — ask something in Ask.</div>}
          {items?.map((h) => (
            <button className="hist-row" key={h.query_id} onClick={() => onPick(h.query)}>
              <span className="hist-q">{h.query}</span>
              <span className="hist-t">{relTime(h.ts)}</span>
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}

function DiscoverView({ onAsk }: { onAsk: (q: string) => void }) {
  const [data, setData] = useState<DiscoverResult | null>(null);
  useEffect(() => { getDiscover().then(setData); }, []);
  const max = Math.max(1, ...(data?.trending.map((t) => t.score) ?? [1]));
  return (
    <main className="main">
      <header className="topbar"><div className="title">Discover</div>
        <span className="tenant">trending · last {data?.window_days ?? 14} days</span></header>
      <div className="scroll">
        <div className="panel-wrap">
          {data === null && <div className="empty-p">Loading…</div>}
          {data?.trending.length === 0 && <div className="empty-p">No activity yet — engagement will surface here.</div>}
          {data?.trending.map((t) => (
            <div className="disc-card" key={t.doc_id}>
              <div className="disc-main">
                <a className="disc-title" href={t.source_url} target="_blank" rel="noopener noreferrer"
                   onClick={() => logClick(t.doc_id, t.source)}>{t.title}</a>
                <span className="disc-src">{t.source}</span>
                <p className="disc-snip">{t.snippet}</p>
                <div className="disc-bar"><span style={{ width: `${(t.score / max) * 100}%` }} /></div>
              </div>
              <button className="disc-ask" onClick={() => onAsk(`Tell me about ${t.title}`)}>Ask</button>
            </div>
          ))}
          {data && data.by_source.length > 0 && (
            <div className="disc-sources">
              <div className="lbl">Activity by source</div>
              {data.by_source.map((s) => (
                <div className="src-row" key={s.source}><span>{s.source}</span><span className="meta">{s.events} events</span></div>
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
```

(f) In the `Chat` return, render conditionally. `onPick`/`onAsk` switch to Ask and run the query — reuse the existing `ask` function:
```tsx
{view === "history" && <HistoryView onPick={(q) => { setView("ask"); ask(q); }} />}
{view === "discover" && <DiscoverView onAsk={(q) => { setView("ask"); ask(q); }} />}
{view === "ask" && (
  <main className="main">
    {/* ...existing main content unchanged... */}
  </main>
)}
```
And guard the right rail: `{view === "ask" && (<aside className="rail--right">…</aside>)}`.

(g) Log a click when a citation card is opened — add `onClick={() => logClick(c.doc_id, "uploaded", t.answer!.query_id)}` to the existing citation `<a className="cite">`.

- [ ] **Step 2: Add styles**

Append to `web/app/globals.css`:

```css
  .panel-wrap{max-width:760px;margin:0 auto;padding:26px 0}
  .hist-row{display:flex;align-items:center;justify-content:space-between;width:100%;gap:14px;
    background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 16px;margin:0 0 9px;
    cursor:pointer;text-align:left;transition:.15s;font:inherit;color:var(--ink)}
  .hist-row:hover{border-color:var(--amber);background:#fff}
  .hist-q{font-size:14.5px}
  .hist-t{font-family:"JetBrains Mono",monospace;font-size:11px;color:var(--ink-faint);white-space:nowrap}
  .disc-card{display:flex;gap:14px;align-items:flex-start;justify-content:space-between;
    background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:15px 17px;margin:0 0 11px}
  .disc-main{min-width:0;flex:1}
  .disc-title{font-family:"Fraunces",serif;font-size:16px;color:var(--ink);text-decoration:none}
  .disc-title:hover{color:var(--amber)}
  .disc-src{display:inline-block;margin-left:9px;font-family:"JetBrains Mono",monospace;font-size:10px;
    color:var(--teal);background:var(--teal-bg);border:1px solid rgba(15,137,126,.3);border-radius:5px;padding:1px 6px;vertical-align:1px}
  .disc-snip{font-size:13.5px;color:var(--ink-faint);margin:7px 0 9px;line-height:1.6}
  .disc-bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden}
  .disc-bar span{display:block;height:100%;background:var(--amber)}
  .disc-ask{flex:none;align-self:center;background:var(--amber);color:#fff;border:none;border-radius:8px;
    padding:7px 14px;font:inherit;font-size:13px;cursor:pointer}
  .disc-ask:hover{filter:brightness(1.05)}
  .disc-sources{margin-top:18px;background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:14px 17px}
  .disc-sources .lbl{font-family:"JetBrains Mono",monospace;font-size:10px;letter-spacing:.08em;
    text-transform:uppercase;color:var(--ink-faint);margin-bottom:9px}
  .src-row{display:flex;justify-content:space-between;font-size:13.5px;padding:5px 0;border-top:1px solid var(--line)}
  .src-row .meta{color:var(--ink-faint);font-family:"JetBrains Mono",monospace;font-size:11px}
  .nav button{display:flex;align-items:center;gap:11px;width:100%;background:none;border:none;
    font:inherit;color:inherit;cursor:pointer;text-align:left}
```
(The `.nav button` rule makes the new `<button>` nav items inherit the existing `.nav a` styling; verify the existing `.nav a` selector also covers `.nav button` — if `.nav a{…}` uses element selector, duplicate its declarations for `.nav button` here.)

- [ ] **Step 3: Typecheck + local build**

Run: `cd web && pnpm typecheck && pnpm build`
Expected: compiles, no type errors.

- [ ] **Step 4: Commit**

```bash
git add web/components/Chat.tsx web/app/globals.css
git commit -m "feat(web): History + Discover views with left-rail switcher"
```

---

## Task 10: Demo seed + deploy + verify + tag

**Files:**
- Modify: `brain-api/app/api/admin.py` (richer seed for a meaningful Discover demo)

- [ ] **Step 1: Broaden /admin/seed-activity**

Replace the `seed_activity` handler in `app/api/admin.py` so it seeds a spread of event types across several real corpus docs and varied event-sources (so trending + by-source both render). Keep it tenant-scoped via `EVAL_TENANT`:

```python
class SeedActivityRequest(BaseModel):
    doc_ids: list[str] = []
    events_per_doc: int = 6


@router.post("/seed-activity")
async def seed_activity(body: SeedActivityRequest) -> dict:
    """Seed synthetic engagement across real corpus docs so Discover/ranker
    surfaces are demonstrable. Pass real doc_ids; falls back to a known set."""
    tenant = os.environ.get("EVAL_TENANT", "t-eval")
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        ids = body.doc_ids or [
            "up:planning-q3-sales-plan",
            "up:engineering-oncall-runbook",
            "up:policy-pto",
        ]
        types = ["view", "view", "click", "dwell", "thumbs_up"]
        sources = ["sharepoint", "teams", "uploaded"]
        written = 0
        for j, doc_id in enumerate(ids):
            for i in range(body.events_per_doc):
                await store.ingest_event(ActivityEvent(
                    timestamp=now - timedelta(hours=i + j),
                    tenant_id=tenant, user_id=f"u-{i % 3}", doc_id=doc_id,
                    event_type=types[(i + j) % len(types)],
                    source=sources[(i + j) % len(sources)]))
                written += 1
        return {"tenant_id": tenant, "events_written": written, "docs": len(ids)}
    finally:
        await store.aclose()
```

Ensure `from pydantic import BaseModel` is imported in `admin.py` (add if missing). Run `uv run ruff check app/api/admin.py`, then commit:
```bash
git add brain-api/app/api/admin.py
git commit -m "feat(admin): richer seed-activity for Discover demo"
```

- [ ] **Step 2: Build + push images (controller)**

```bash
az acr login -n cbrainlokeshacr
docker build --platform linux/amd64 -t cbrainlokeshacr.azurecr.io/brain-api:v3 brain-api && docker push cbrainlokeshacr.azurecr.io/brain-api:v3
docker build --platform linux/amd64 -t cbrainlokeshacr.azurecr.io/substrateos-web:v4 web && docker push cbrainlokeshacr.azurecr.io/substrateos-web:v4
```

- [ ] **Step 3: Deploy**

```bash
az containerapp update -n brain-api -g rg-company-brain-dev --image cbrainlokeshacr.azurecr.io/brain-api:v3
az containerapp update -n substrateos-web -g rg-company-brain-dev --image cbrainlokeshacr.azurecr.io/substrateos-web:v4
```

- [ ] **Step 4: Seed demo activity against prod (controller)**

Get the admin key from Key Vault, list real corpus doc_ids for `t-eval` (via `/admin/retrieve` with the admin key), then POST `/admin/seed-activity` with those ids:
```bash
KEY=$(az keyvault secret show --vault-name <kv> -n admin-api-key --query value -o tsv)
API=https://brain-api.gentlebush-9de671e3.swedencentral.azurecontainerapps.io
curl -s -X POST "$API/admin/seed-activity" -H "x-admin-key: $KEY" -H 'Content-Type: application/json' \
  -d '{"doc_ids":["up:planning-q3-sales-plan","up:engineering-oncall-runbook","up:policy-pto"],"events_per_doc":8}'
```
(Use the actual admin-auth header name from `app/api/admin.py`; adjust doc_ids to the real corpus set.)

- [ ] **Step 5: Verify in browser**

Log in to the web app → click **History** (shows the questions you asked) → click one (re-runs live) → click **Discover** (shows trending cards + activity-by-source). Confirm no console errors and that `/history` + `/discover` return 200 in the Network tab.

- [ ] **Step 6: Tag the release**

```bash
git tag history-discover-v1
```

---

## Notes for the executor
- Run backend commands from `brain-api/`; the shell working dir may already be there.
- The full non-integration suite must stay green: `uv run pytest -q -m "not integration"`.
- Keep every store/service failure path returning empty — these surfaces must never 500.
- Frontend: match the existing light aesthetic (Fraunces display, Archivo body, amber accent, `--panel`/`--line`/`--ink` tokens already defined in `globals.css`).
