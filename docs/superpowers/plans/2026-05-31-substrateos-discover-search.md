# Discover → Enterprise Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the Discover surface with an enterprise search — query box + filters, ranked results with source facets, and an always-on grounded AI Overview — all ACL-trimmed.

**Architecture:** `POST /search` runs, concurrently, `AISearchClient.search_page` (faceted result page) and `orchestrator.answer` (grounded AI Overview), then resolves "people who work on this" from result authors via the People graph. Frontend replaces the Discover view with a SearchView.

**Tech Stack:** FastAPI, Pydantic, azure-search-documents (async), Azure OpenAI embeddings, Cosmos Gremlin (People), Next.js 14 / React 18 / TS, pytest, `uv`, `pnpm`.

**Conventions:** Backend root `brain-api/`; tests `uv run pytest`; lint `uv run ruff check <files>` (must pass); work on `main` (no branch); commit after each task with trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Non-integration tests must inject fakes (no real Azure). Debug header: `x-debug-bypass-auth: t-test,u-x,t-test:everyone`.

---

## File Structure
Backend:
- Create `app/domain/search.py` — `SearchHit`, `SourceFacet`, `PersonHit`, `SearchPage`, `SearchResponse`.
- Modify `app/retrieval/ai_search_client.py` — add `search_page()` (faceted, highlighted, ACL+filter, doc-dedup).
- Modify `app/people/graph_client.py` — add `resolve_people(user_ids, tenant_id)` (best-effort name lookup).
- Create `app/search/__init__.py`, `app/search/service.py` — `SearchService`.
- Create `app/api/search.py` — `POST /search`.
- Modify `app/deps.py` (`get_search_service`), `app/main.py` (construct + include router).
- Tests: `tests/test_search_page.py`, `tests/test_resolve_people.py`, `tests/test_search_service.py`, `tests/test_search_api.py`.

Frontend:
- Modify `web/lib/api.ts` — search types + `postSearch`.
- Modify `web/components/Chat.tsx` — replace `DiscoverView` with `SearchView`.
- Modify `web/app/globals.css` — port mockup CSS (`mockups/user-web-chat.html (Discover view — `.view-discover`)`).

---

## Task 1: Search domain models

**Files:** Create `brain-api/app/domain/search.py`; Test `brain-api/tests/test_search_models.py`

- [ ] **Step 1: Failing test**

```python
# brain-api/tests/test_search_models.py
from datetime import UTC, datetime

from app.domain.search import PersonHit, SearchHit, SearchPage, SearchResponse, SourceFacet


def test_models_construct() -> None:
    hit = SearchHit(doc_id="d1", title="T", source="sharepoint", source_url="http://x",
                    author_id="u1", modified_at=datetime(2026, 5, 31, tzinfo=UTC), snippet="s")
    page = SearchPage(results=[hit], facets=[SourceFacet(source="sharepoint", count=3)], total=3)
    resp = SearchResponse(query="q", answer=None, results=[hit],
                          facets=page.facets, people=[PersonHit(user_id="u1", display_name="Priya")],
                          total=3)
    assert resp.results[0].doc_id == "d1"
    assert resp.facets[0].count == 3
    assert resp.people[0].display_name == "Priya"
    assert resp.answer is None
```

- [ ] **Step 2: Run → fails** `uv run pytest tests/test_search_models.py -q` (ModuleNotFoundError).

- [ ] **Step 3: Implement**

```python
# brain-api/app/domain/search.py
from datetime import datetime

from pydantic import BaseModel

from app.domain.query import Answer


class SearchHit(BaseModel):
    doc_id: str
    title: str
    source: str
    source_url: str
    author_id: str | None
    modified_at: datetime
    snippet: str


class SourceFacet(BaseModel):
    source: str
    count: int


class PersonHit(BaseModel):
    user_id: str
    display_name: str
    role: str | None = None


class SearchPage(BaseModel):
    results: list[SearchHit]
    facets: list[SourceFacet]
    total: int


class SearchResponse(BaseModel):
    query: str
    answer: Answer | None = None
    results: list[SearchHit]
    facets: list[SourceFacet]
    people: list[PersonHit]
    total: int
```

- [ ] **Step 4: Run → passes** `uv run pytest tests/test_search_models.py -q` (1 passed).
- [ ] **Step 5: Lint + commit**
```bash
uv run ruff check app/domain/search.py tests/test_search_models.py
git add app/domain/search.py tests/test_search_models.py
git commit -m "feat(search): domain models"
```

---

## Task 2: AISearchClient.search_page

**Files:** Modify `brain-api/app/retrieval/ai_search_client.py`; Test `brain-api/tests/test_search_page.py`

Mirror the existing `hybrid_search` call pattern (same `search_text`/`vector_queries`/`query_type`/`semantic_configuration_name`/`filter`/`select`), adding `facets`, `skip`, `include_total_count`, and highlighting. Group chunks to one hit per `doc_id`.

- [ ] **Step 1: Failing test**

```python
# brain-api/tests/test_search_page.py
from datetime import UTC, datetime

import pytest

from app.domain.identity import User
from app.retrieval.ai_search_client import AISearchClient


def _row(doc_id, chunk_id, *, source="sharepoint", highlights=None):
    now = datetime(2026, 5, 31, tzinfo=UTC).isoformat()
    r = {"chunk_id": chunk_id, "doc_id": doc_id, "tenant_id": "t1", "source": source,
         "source_url": f"http://x/{doc_id}", "title": doc_id.upper(), "content": "full body text here",
         "author_id": "u1", "acl_principals": ["t1:everyone"], "entities": [],
         "created_at": now, "modified_at": now, "chunk_index": 0}
    if highlights is not None:
        r["@search.highlights"] = highlights
    return r


class FakeResults:
    def __init__(self, rows, *, facets, count):
        self._rows = rows
        self._facets = facets
        self._count = count

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield dict(r)
        return gen()

    async def get_facets(self):
        return self._facets

    async def get_count(self):
        return self._count


class FakeCli:
    def __init__(self, results):
        self._results = results
        self.kwargs = None

    async def search(self, **kwargs):
        self.kwargs = kwargs
        return self._results


def _client(results) -> AISearchClient:
    c = AISearchClient.__new__(AISearchClient)
    c._cli = results and FakeCli(results)
    return c


def _user():
    return User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids={"t1:everyone"})


@pytest.mark.asyncio
async def test_search_page_dedupes_facets_total_and_filters() -> None:
    rows = [
        _row("d1", "d1#0", highlights={"content": ["a <b>vision</b> b"]}),
        _row("d1", "d1#1"),  # same doc → deduped
        _row("d2", "d2#0", source="teams"),
    ]
    results = FakeResults(rows, facets={"source": [{"value": "sharepoint", "count": 5},
                                                   {"value": "teams", "count": 2}]}, count=7)
    c = _client(results)
    page = await c.search_page(query="vision", user=_user(), vector=[0.1, 0.2], top=10,
                               sources=["sharepoint", "teams"], author_id="u1")
    assert [h.doc_id for h in page.results] == ["d1", "d2"]
    assert page.results[0].snippet == "a vision b"   # highlight tags normalized/kept as text
    assert page.total == 7
    assert {f.source: f.count for f in page.facets} == {"sharepoint": 5, "teams": 2}
    flt = c._cli.kwargs["filter"]
    assert "tenant_id eq 't1'" in flt
    assert "search.in(source, 'sharepoint,teams', ',')" in flt
    assert "author_id eq 'u1'" in flt


@pytest.mark.asyncio
async def test_search_page_degrades_to_empty_on_error() -> None:
    class Boom:
        async def search(self, **k):
            raise RuntimeError("search down")
    c = AISearchClient.__new__(AISearchClient)
    c._cli = Boom()
    page = await c.search_page(query="x", user=_user(), vector=[0.1])
    assert page.results == [] and page.facets == [] and page.total == 0
```

- [ ] **Step 2: Run → fails** `uv run pytest tests/test_search_page.py -q` (AttributeError: no search_page).

- [ ] **Step 3: Implement** — add imports near the top of `ai_search_client.py` (the file already imports `VectorizedQuery`, `build_acl_filter`, `Chunk`, `User`):

```python
from datetime import datetime  # add if not present

from app.domain.search import SearchHit, SearchPage, SourceFacet
```

Add a module-level helper and the method (after `lookup_docs`):

```python
def _snippet_from(row: dict) -> str:
    """Prefer a search highlight fragment; strip <b>/<em> tags to plain text; else
    fall back to the start of the content."""
    hl = row.get("@search.highlights") or {}
    frags = hl.get("content") or hl.get("title") or []
    text = frags[0] if frags else (row.get("content") or "")[:200]
    for tag in ("<b>", "</b>", "<em>", "</em>"):
        text = text.replace(tag, "")
    return text.strip()
```

```python
    async def search_page(
        self, *, query: str, user: User, vector: list[float], top: int = 10, skip: int = 0,
        sources: list[str] | None = None, date_from: datetime | None = None,
        author_id: str | None = None,
    ) -> SearchPage:
        """Faceted, ACL-filtered result page (one hit per doc). Degrades to an empty
        page on any search error."""
        def esc(s: str) -> str:
            return s.replace("'", "''")

        parts = [f"({build_acl_filter(user)})"]
        if sources:
            ids = ",".join(esc(s) for s in sources)
            parts.append(f"search.in(source, '{ids}', ',')")
        if date_from is not None:
            parts.append(f"modified_at ge {date_from.isoformat()}")
        if author_id:
            parts.append(f"author_id eq '{esc(author_id)}'")
        flt = " and ".join(parts)

        vq = VectorizedQuery(vector=vector, k_nearest_neighbors=50, fields="content_vector")
        try:
            results = await self._cli.search(
                search_text=query,
                vector_queries=[vq],
                query_type="semantic",
                semantic_configuration_name="brain-semantic",
                filter=flt,
                top=max(top * 4, 20),
                skip=skip,
                facets=["source,count:10"],
                include_total_count=True,
                highlight_fields="content,title",
                highlight_pre_tag="<b>",
                highlight_post_tag="</b>",
                select=[
                    "chunk_id", "doc_id", "tenant_id", "source", "source_url", "title",
                    "content", "author_id", "acl_principals", "created_at", "modified_at",
                    "chunk_index",
                ],
            )
            hits: dict[str, SearchHit] = {}
            async for r in results:
                doc_id = r["doc_id"]
                if doc_id in hits:
                    continue
                hits[doc_id] = SearchHit(
                    doc_id=doc_id, title=r["title"], source=r["source"],
                    source_url=r["source_url"], author_id=r.get("author_id"),
                    modified_at=r["modified_at"], snippet=_snippet_from(r),
                )
            facets_raw = await results.get_facets() or {}
            total = await results.get_count() or 0
        except Exception:  # noqa: BLE001 - search surface degrades to empty
            return SearchPage(results=[], facets=[], total=0)

        facets = [
            SourceFacet(source=f["value"], count=int(f["count"]))
            for f in (facets_raw.get("source") or [])
        ]
        return SearchPage(results=list(hits.values())[:top], facets=facets, total=total)
```

Note: `r["modified_at"]` arrives as an ISO string from the index; Pydantic coerces it to `datetime` in `SearchHit`.

- [ ] **Step 4: Run → passes** `uv run pytest tests/test_search_page.py -q` (2 passed).
- [ ] **Step 5: Lint + full suite + commit**
```bash
uv run ruff check app/retrieval/ai_search_client.py tests/test_search_page.py
uv run pytest tests/ -q -m "not integration"
git add app/retrieval/ai_search_client.py tests/test_search_page.py
git commit -m "feat(search): AISearchClient.search_page (facets, highlights, doc-dedup, ACL)"
```

---

## Task 3: PeopleGraphClient.resolve_people

**Files:** Modify `brain-api/app/people/graph_client.py`; Test `brain-api/tests/test_resolve_people.py`

People vertices are `addV('user').property('user_id',…).property('tenant_id',…).property('display_name',…)`. Resolve a set of author ids to names; best-effort (empty on failure). Unknown ids are simply absent.

- [ ] **Step 1: Failing test**

```python
# brain-api/tests/test_resolve_people.py
import pytest

from app.people.graph_client import PeopleGraphClient


class FakeGraph(PeopleGraphClient):
    def __init__(self, rows, *, fail=False):
        self._rows = rows
        self._fail = fail
        self.last = None

    async def submit(self, query, bindings=None):
        self.last = (query, bindings)
        if self._fail:
            raise RuntimeError("gremlin down")
        return self._rows


@pytest.mark.asyncio
async def test_resolve_people_maps_names() -> None:
    g = FakeGraph([
        {"user_id": ["u1"], "display_name": ["Priya Nair"]},
        {"user_id": ["u2"], "display_name": ["Sam Osei"]},
    ])
    out = await g.resolve_people(["u1", "u2"], tenant_id="t1")
    names = {p.user_id: p.display_name for p in out}
    assert names == {"u1": "Priya Nair", "u2": "Sam Osei"}


@pytest.mark.asyncio
async def test_resolve_people_empty_and_degrades() -> None:
    assert await FakeGraph([]).resolve_people([], tenant_id="t1") == []
    assert await FakeGraph([], fail=True).resolve_people(["u1"], tenant_id="t1") == []
```

- [ ] **Step 2: Run → fails** `uv run pytest tests/test_resolve_people.py -q`.

- [ ] **Step 3: Implement** — add to `PeopleGraphClient` (import the model at top: `from app.domain.search import PersonHit`):

```python
    async def resolve_people(self, user_ids: list[str], tenant_id: str) -> list["PersonHit"]:
        """Best-effort: map author user_ids to display names from the People graph.
        Returns [] on empty input or any graph error; unknown ids are omitted."""
        from app.domain.search import PersonHit

        if not user_ids:
            return []
        try:
            rows = await self.submit(
                "g.V().has('user','tenant_id', tid).has('user_id', within(ids))"
                ".valueMap('user_id','display_name')",
                {"tid": tenant_id, "ids": user_ids},
            )
        except Exception:  # noqa: BLE001 - people block is best-effort
            return []
        out: list[PersonHit] = []
        for r in rows:
            uid = (r.get("user_id") or [None])[0]
            name = (r.get("display_name") or [None])[0]
            if uid and name:
                out.append(PersonHit(user_id=uid, display_name=name))
        # preserve the requested order
        order = {u: i for i, u in enumerate(user_ids)}
        out.sort(key=lambda p: order.get(p.user_id, 999))
        return out
```

(`valueMap` returns each property as a list, hence the `[0]` unwrap — matching Cosmos Gremlin behavior.)

- [ ] **Step 4: Run → passes** `uv run pytest tests/test_resolve_people.py -q` (2 passed).
- [ ] **Step 5: Lint + commit**
```bash
uv run ruff check app/people/graph_client.py tests/test_resolve_people.py
git add app/people/graph_client.py tests/test_resolve_people.py
git commit -m "feat(search): PeopleGraphClient.resolve_people (best-effort name lookup)"
```

---

## Task 4: SearchService

**Files:** Create `brain-api/app/search/__init__.py`, `brain-api/app/search/service.py`; Test `brain-api/tests/test_search_service.py`

Composes: embed query → concurrently (search_page + grounded overview) → resolve people from result authors → assemble. Each part degrades independently.

- [ ] **Step 1: Failing test**

```python
# brain-api/tests/test_search_service.py
from datetime import UTC, datetime

import pytest

from app.domain.query import Answer
from app.domain.search import PersonHit, SearchHit, SearchPage, SourceFacet
from app.domain.identity import User
from app.search.service import SearchService


def _hit(doc_id, author):
    return SearchHit(doc_id=doc_id, title=doc_id, source="sharepoint", source_url="http://x",
                     author_id=author, modified_at=datetime(2026, 5, 31, tzinfo=UTC), snippet="s")


class FakeEmbedder:
    async def embed(self, text):
        return [0.1, 0.2]


class FakeSearch:
    def __init__(self, page):
        self._page = page
        self.kwargs = None
    async def search_page(self, **kwargs):
        self.kwargs = kwargs
        return self._page


class FakeOrch:
    def __init__(self, answer=None, boom=False):
        self._a = answer
        self._boom = boom
    async def answer(self, body, *, user, user_token=None):
        if self._boom:
            raise RuntimeError("overview down")
        return self._a


class FakePeople:
    def __init__(self, people):
        self._p = people
        self.asked = None
    async def resolve_people(self, user_ids, tenant_id):
        self.asked = user_ids
        return self._p


def _user():
    return User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids={"t1:everyone"})


def _svc(page, *, answer=None, boom=False, people=None):
    return SearchService(embedder=FakeEmbedder(), search=FakeSearch(page),
                         orchestrator=FakeOrch(answer, boom), people=FakePeople(people or []))


@pytest.mark.asyncio
async def test_assembles_results_overview_facets_people() -> None:
    page = SearchPage(results=[_hit("d1", "u1"), _hit("d2", "u2")],
                      facets=[SourceFacet(source="sharepoint", count=2)], total=2)
    ans = Answer(text="overview", citations=[], query_id="q1")
    svc = _svc(page, answer=ans, people=[PersonHit(user_id="u1", display_name="Priya")])
    resp = await svc.result(user=_user(), query="vision")
    assert resp.answer.text == "overview"
    assert [h.doc_id for h in resp.results] == ["d1", "d2"]
    assert resp.total == 2 and resp.facets[0].count == 2
    assert resp.people[0].display_name == "Priya"


@pytest.mark.asyncio
async def test_overview_failure_degrades_to_none() -> None:
    page = SearchPage(results=[_hit("d1", "u1")], facets=[], total=1)
    resp = await _svc(page, boom=True).result(user=_user(), query="vision")
    assert resp.answer is None
    assert resp.results[0].doc_id == "d1"


@pytest.mark.asyncio
async def test_empty_query_returns_empty() -> None:
    page = SearchPage(results=[], facets=[], total=0)
    resp = await _svc(page).result(user=_user(), query="   ")
    assert resp.results == [] and resp.answer is None and resp.total == 0
```

- [ ] **Step 2: Run → fails** `uv run pytest tests/test_search_service.py -q`.

- [ ] **Step 3: Implement**

```python
# brain-api/app/search/__init__.py
```

```python
# brain-api/app/search/service.py
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.domain.identity import User
from app.domain.query import Answer, QueryRequest
from app.domain.search import SearchResponse

logger = logging.getLogger(__name__)


class SearchService:
    """Enterprise search: faceted result page + grounded AI Overview + people-from-authors.
    Each part degrades independently; the endpoint never 500s on a data-layer failure."""

    def __init__(self, *, embedder, search, orchestrator, people) -> None:
        self._embedder = embedder
        self._search = search
        self._orchestrator = orchestrator
        self._people = people

    async def _overview(self, *, user: User, query: str) -> Answer | None:
        try:
            return await self._orchestrator.answer(QueryRequest(query=query), user=user)
        except Exception as e:  # noqa: BLE001 - overview is optional
            logger.warning("search overview failed: %s", e)
            return None

    async def result(
        self, *, user: User, query: str, top: int = 10, skip: int = 0,
        sources: list[str] | None = None, date_from: datetime | None = None,
        author_id: str | None = None,
    ) -> SearchResponse:
        q = query.strip()
        if not q:
            return SearchResponse(query=query, answer=None, results=[], facets=[], people=[], total=0)

        vector = await self._embedder.embed(q)
        page, answer = await asyncio.gather(
            self._search.search_page(
                query=q, user=user, vector=vector, top=top, skip=skip,
                sources=sources, date_from=date_from, author_id=author_id,
            ),
            self._overview(user=user, query=q),
        )

        author_ids = list(dict.fromkeys(h.author_id for h in page.results if h.author_id))
        try:
            people = await self._people.resolve_people(author_ids, user.tenant_id)
        except Exception as e:  # noqa: BLE001 - people block is best-effort
            logger.warning("search people resolve failed: %s", e)
            people = []

        return SearchResponse(
            query=q, answer=answer, results=page.results,
            facets=page.facets, people=people, total=page.total,
        )
```

- [ ] **Step 4: Run → passes** `uv run pytest tests/test_search_service.py -q` (3 passed).
- [ ] **Step 5: Lint + full suite + commit**
```bash
uv run ruff check app/search/service.py tests/test_search_service.py
uv run pytest tests/ -q -m "not integration"
git add app/search/ tests/test_search_service.py
git commit -m "feat(search): SearchService (results + grounded overview + people, concurrent, degrading)"
```

---

## Task 5: /search endpoint + wiring

**Files:** Create `brain-api/app/api/search.py`; Modify `brain-api/app/deps.py`, `brain-api/app/main.py`; Test `brain-api/tests/test_search_api.py`

- [ ] **Step 1: Failing test**

```python
# brain-api/tests/test_search_api.py
from fastapi.testclient import TestClient

from app.deps import get_search_service
from app.domain.query import Answer
from app.domain.search import SearchHit, SearchResponse, SourceFacet
from app.main import app
from datetime import UTC, datetime

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class FakeSearchService:
    async def result(self, *, user, query, top=10, skip=0, sources=None, date_from=None, author_id=None):
        return SearchResponse(
            query=query, answer=Answer(text="ov", citations=[], query_id="q1"),
            results=[SearchHit(doc_id="d1", title="T", source="sharepoint", source_url="http://x",
                               author_id="u1", modified_at=datetime(2026, 5, 31, tzinfo=UTC), snippet="s")],
            facets=[SourceFacet(source="sharepoint", count=1)], people=[], total=1)


def test_search_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.post("/search", json={"query": "x"}).status_code == 401


def test_search_returns_response() -> None:
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService()
    try:
        with TestClient(app) as client:
            resp = client.post("/search", json={"query": "vision", "sources": ["sharepoint"]}, headers=_HDR)
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"]["text"] == "ov"
        assert body["results"][0]["doc_id"] == "d1"
        assert body["facets"][0]["count"] == 1
        assert body["total"] == 1
    finally:
        app.dependency_overrides.clear()


def test_search_empty_when_service_unavailable() -> None:
    app.dependency_overrides[get_search_service] = lambda: None
    try:
        with TestClient(app) as client:
            resp = client.post("/search", json={"query": "vision"}, headers=_HDR)
        assert resp.status_code == 200
        assert resp.json() == {"query": "vision", "answer": None, "results": [],
                               "facets": [], "people": [], "total": 0}
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run → fails** `uv run pytest tests/test_search_api.py -q` (404).

- [ ] **Step 3a: Endpoint** — `brain-api/app/api/search.py`:

```python
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.deps import get_search_service
from app.domain.search import SearchResponse

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    sources: list[str] | None = None
    date_from: datetime | None = None
    author_id: str | None = None
    top: int = 10
    skip: int = 0


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    service=Depends(get_search_service),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> SearchResponse:
    user = await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    if service is None:
        return SearchResponse(query=body.query, answer=None, results=[], facets=[], people=[], total=0)
    return await service.result(
        user=user, query=body.query, top=min(max(body.top, 1), 25), skip=max(body.skip, 0),
        sources=body.sources, date_from=body.date_from, author_id=body.author_id,
    )
```

- [ ] **Step 3b: deps** — append to `app/deps.py` (add `from app.search.service import SearchService` at top):

```python
def get_search_service(request: Request) -> "SearchService | None":
    return getattr(request.app.state, "search_service", None)
```

- [ ] **Step 3c: main.py** — add imports (`from app.api.search import router as search_router`, `from app.search.service import SearchService`); in lifespan after the orchestrator + discover_service block add:

```python
    app.state.search_service = SearchService(
        embedder=app.state.embedder,
        search=app.state.ai_search,
        orchestrator=app.state.orchestrator,
        people=app.state.people_graph,
    )
```

and after the other `app.include_router(...)` lines add `app.include_router(search_router)`.

- [ ] **Step 4: Run → passes** `uv run pytest tests/test_search_api.py -q` (3 passed).
- [ ] **Step 5: Full suite + lint + commit**
```bash
uv run pytest tests/ -q -m "not integration"
uv run ruff check app/api/search.py app/deps.py app/main.py tests/test_search_api.py
git add app/api/search.py app/deps.py app/main.py tests/test_search_api.py
git commit -m "feat(search): POST /search endpoint + lifespan wiring"
```

---

## Task 6: Frontend API client

**Files:** Modify `web/lib/api.ts`

- [ ] **Step 1: Append types + `postSearch`** (reuse the existing `authedFetch` + `API_BASE`):

```typescript
export type SearchHit = {
  doc_id: string; title: string; source: string; source_url: string;
  author_id: string | null; modified_at: string; snippet: string;
};
export type SourceFacet = { source: string; count: number };
export type PersonHit = { user_id: string; display_name: string; role: string | null };
export type SearchResponse = {
  query: string; answer: Answer | null; results: SearchHit[];
  facets: SourceFacet[]; people: PersonHit[]; total: number;
};

export type SearchOpts = { sources?: string[]; date_from?: string; author_id?: string };

export async function postSearch(query: string, opts: SearchOpts = {}): Promise<SearchResponse> {
  const empty: SearchResponse = { query, answer: null, results: [], facets: [], people: [], total: 0 };
  try {
    const resp = await authedFetch(`${API_BASE}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, ...opts }),
    });
    if (!resp.ok) return empty;
    return (await resp.json()) as SearchResponse;
  } catch {
    return empty;
  }
}
```

- [ ] **Step 2: Typecheck** `cd web && pnpm typecheck` → clean.
- [ ] **Step 3: Commit**
```bash
git add web/lib/api.ts
git commit -m "feat(web): postSearch api client"
```

---

## Task 7: SearchView (replaces Discover view) + styles

**Files:** Modify `web/components/Chat.tsx`, `web/app/globals.css`

READ `web/components/Chat.tsx` first (it has `view: "ask"|"discover"|"history"`, a `DiscoverView`, and `ask()`/`AnswerText`). Read `mockups/user-web-chat.html (Discover view — `.view-discover`)` for the exact markup/classes to port.

- [ ] **Step 1: Replace `DiscoverView` with `SearchView`** in `Chat.tsx`.

Update the api import to drop `getDiscover` and add `postSearch` + types:
```typescript
import { postQuery, postFeedback, getHistory, logClick, postSearch,
  Answer, Citation, HistoryEntry, SearchResponse } from "@/lib/api";
```
Delete the `DiscoverView` component. Add `SearchView` (uses `AnswerText` for the overview — it's already defined in this file; ensure it's declared above `SearchView` or hoisted as a function declaration, which it is):

```tsx
const TIME_FILTERS: { label: string; days: number | null }[] = [
  { label: "Anytime", days: null }, { label: "Past week", days: 7 },
  { label: "Past month", days: 30 }, { label: "Past quarter", days: 90 },
];

function SearchView({ onAsk }: { onAsk: (q: string) => void }) {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeSources, setActiveSources] = useState<string[]>([]);
  const [timeIdx, setTimeIdx] = useState(0);

  async function run(query: string, sources: string[], days: number | null) {
    const text = query.trim();
    if (!text) return;
    setSubmitted(text); setLoading(true);
    const opts: { sources?: string[]; date_from?: string } = {};
    if (sources.length) opts.sources = sources;
    if (days != null) opts.date_from = new Date(Date.now() - days * 864e5).toISOString();
    const res = await postSearch(text, opts);
    setData(res); setLoading(false);
  }

  function toggleSource(s: string) {
    const next = activeSources.includes(s)
      ? activeSources.filter((x) => x !== s) : [...activeSources, s];
    setActiveSources(next);
    if (submitted) run(submitted, next, TIME_FILTERS[timeIdx].days);
  }

  return (
    <main className="main">
      <div className="searchwrap">
        <form className="searchbar" onSubmit={(e) => { e.preventDefault(); run(q, activeSources, TIME_FILTERS[timeIdx].days); }}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="var(--ink-faint)" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
          <input placeholder="Search across SharePoint, Teams, and more…" value={q} onChange={(e) => setQ(e.target.value)} />
          {q && <span className="clr" onClick={() => setQ("")}>✕</span>}
        </form>
        <div className="filters">
          <div className="fchip" onClick={() => { const n = (timeIdx + 1) % TIME_FILTERS.length; setTimeIdx(n); if (submitted) run(submitted, activeSources, TIME_FILTERS[n].days); }}>
            {TIME_FILTERS[timeIdx].label} <span className="cv">▾</span>
          </div>
        </div>
      </div>

      <div className="scroll">
        {!submitted && <div className="panel-wrap"><div className="empty-p">Search your company knowledge — grounded answers with sources.</div></div>}
        {submitted && (
          <div className="sgrid">
            <div>
              {loading && <div className="empty-p">Searching…</div>}
              {!loading && data?.answer && (
                <section className="ai">
                  <div className="ai-head">
                    <svg className="ai-spark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /><circle cx="12" cy="12" r="3.2" /></svg>
                    <span className="ai-title">AI Overview</span>
                    <span className="ai-badge">● grounded · {data.answer.citations.length} sources</span>
                  </div>
                  <div className="ai-body"><AnswerText text={data.answer.text} citations={data.answer.citations} /></div>
                </section>
              )}
              {!loading && (
                <>
                  <p className="rescount">{data?.total ?? 0} results · ranked for you</p>
                  {data?.results.map((h) => (
                    <div className="result" key={h.doc_id}>
                      <div className="ricon">{sourceIcon(h.source)}</div>
                      <div className="rmain">
                        <a className="rtitle" href={h.source_url} target="_blank" rel="noopener noreferrer" onClick={() => logClick(h.doc_id, h.source)}>{h.title}</a>
                        <div className="rmeta">{h.author_id ? `${h.author_id} · ` : ""}{relTime(h.modified_at)} · <span className="fold">📁 {h.source}</span></div>
                        <div className="rsnip">{h.snippet}</div>
                      </div>
                    </div>
                  ))}
                  {data && data.results.length === 0 && <div className="empty-p">No results.</div>}
                  {data && data.people.length > 0 && (
                    <div className="people">
                      <div className="people-h">People who work on this</div>
                      <div className="pcards">
                        {data.people.map((p) => (
                          <div className="pcard" key={p.user_id}><div className="pav">{initials(p.display_name)}</div><div><div className="nm">{p.display_name}</div>{p.role && <div className="rl">{p.role}</div>}</div></div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
            <aside className="facets">
              <div className="facet-h" style={{ marginTop: 0 }}><span>Sources</span></div>
              {(data?.facets ?? []).map((f) => (
                <div className={"fac" + (activeSources.includes(f.source) ? " on" : "")} key={f.source} onClick={() => toggleSource(f.source)}>
                  <span className="ic">{sourceIcon(f.source)}</span>{f.source}<span className="ct">{f.count}</span>
                </div>
              ))}
            </aside>
          </div>
        )}
      </div>
    </main>
  );
}

function sourceIcon(s: string): string {
  return ({ sharepoint: "📁", teams: "💬", uploaded: "📄", slack: "🟪", jira: "🟦", graph: "🌐" } as Record<string, string>)[s] ?? "📄";
}
```

Change the Discover branch in `Chat`'s return from `<DiscoverView .../>` to `<SearchView onAsk={(query) => { setView("ask"); ask(query); }} />`. (`onAsk` is kept for parity though SearchView currently only searches; leave the prop wired for future "ask this" actions. If lint flags `onAsk` unused, drop the prop and pass nothing.)

- [ ] **Step 2: Port mockup CSS** — append the search-surface CSS from `mockups/user-web-chat.html (Discover view — `.view-discover`)` to `web/app/globals.css`: the `.searchwrap`, `.searchbar`, `.filters`, `.fchip`, `.sgrid` (use `grid-template-columns:1fr 290px;gap:30px;padding:24px 36px 60px;max-width:1180px;margin:0 auto` — rename the mockup's `.grid` to `.sgrid` to avoid collisions), `.ai`, `.ai-*`, `.rescount`, `.result`, `.ricon`, `.rmain`, `.rtitle`, `.rmeta`, `.rsnip`, `.people`, `.people-h`, `.pcards`, `.pcard`, `.facets`, `.facet-h`, `.fac` rules. Keep the existing `cite-ref` rule (already in globals.css) — do not duplicate it.

- [ ] **Step 3: Typecheck + build** `cd web && pnpm typecheck && pnpm build` → clean. Fix any unused-import/lint errors (e.g., remove `getDiscover`, `DiscoverResult` imports now unused).

- [ ] **Step 4: Commit**
```bash
git add web/components/Chat.tsx web/app/globals.css
git commit -m "feat(web): SearchView replaces Discover trending view"
```

---

## Task 8: Build, deploy, verify, tag (controller)

- [ ] **Step 1: Build + push** `brain-api:v4` and `substrateos-web:v6`:
```bash
az acr login -n cbrainlokeshacr
docker build --platform linux/amd64 -t cbrainlokeshacr.azurecr.io/brain-api:v4 brain-api && docker push cbrainlokeshacr.azurecr.io/brain-api:v4
docker build --platform linux/amd64 -t cbrainlokeshacr.azurecr.io/substrateos-web:v6 web && docker push cbrainlokeshacr.azurecr.io/substrateos-web:v6
```
- [ ] **Step 2: Deploy**
```bash
az containerapp update -n brain-api -g rg-company-brain-dev --image cbrainlokeshacr.azurecr.io/brain-api:v4
az containerapp update -n substrateos-web -g rg-company-brain-dev --image cbrainlokeshacr.azurecr.io/substrateos-web:v6
```
- [ ] **Step 3: Verify** brain-api `/healthz` 200 (retry for cold start); anon `POST /search` → 401.
- [ ] **Step 4: Browser** — log in → Discover → type a query (e.g. "planning priorities") → expect AI Overview card + ranked results + Source facet counts; clicking a facet re-queries; result links open + log clicks. Confirm `/search` returns 200 in the Network tab.
- [ ] **Step 5: Tag** `git tag discover-search-v1`.

---

## Notes for the executor
- Run backend commands from `brain-api/`. Keep the full non-integration suite green: `uv run pytest -q -m "not integration"`.
- The `/search` surface must never 500 on data-layer failures (search/overview/people all degrade).
- Match the SubStrateOS aesthetic already in `globals.css` and `mockups/user-web-chat.html (Discover view — `.view-discover`)`.
- The old trending Discover backend (`DiscoverService`, `/discover`, `ActivityStore.trending/source_breakdown`) stays in place (still tested; `ActivityStore` is used by the ranker) but is no longer surfaced — do not delete it in this plan.
- **Deferred to a fast-follow:** the **"Who from" (author) filter UI**. The `/search` API already accepts `author_id`, but the dropdown (author facet + name resolution) is not built in v1 — v1 ships **time-range + source facets** only. "Similar results" expansion and doc-granular facet counts are also deferred (v1 uses chunk-level facet counts, labelled "results").
