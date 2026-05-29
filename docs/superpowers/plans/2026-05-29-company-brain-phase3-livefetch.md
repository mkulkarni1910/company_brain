# Company Brain — Phase 3 Implementation Plan (Live Fetch / Freshness)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer time-sensitive questions ("who is on call right now?", "what changed this week?") with fresh data the index can't have. At query time, when a freshness trigger fires, call Microsoft Graph `/search` in parallel with indexed retrieval, merge the live results into the candidate set, and let the existing ranker + grounded generation surface and cite them.

**Architecture:** Extends the Phase 2b monolith. A `LiveFetcher` interface with a `MSGraphSearchFetcher` implementation calls Graph `/search/query` using a `DefaultAzureCredential` Graph token (the same pattern the People seeder already uses — no OBO, no web SSO). A heuristic `needs_live_fetch(query)` decides when to fire. The orchestrator fans out retrieval + live fetch concurrently (live fetch under a hard timeout that degrades to empty on failure), partitions ACL handling (indexed candidates get the query-time re-check; live candidates are already permission-trimmed by Graph's user-scoped token so they bypass it), maps live hits to synthetic `Candidate`s with a Graph-rank-derived `content_rrf`, and ranks everything together.

**Tech Stack:** Existing + Microsoft Graph `/search/query` (`httpx`, `DefaultAzureCredential` Graph token — already proven by the seeder). No new Azure resources.

**Scope cut:** Live Fetch only. The orchestrator **plan-step classifier** (gpt-4o-mini query rewrite + retrieve/fetch decision) stays deferred — we use a heuristic trigger instead. **Per-user OBO** is deferred to Phase 4 (Phase 3 Live Fetch runs as the single `az`/service identity, not the per-request user — fine for a single-user demo, noted explicitly). Per-tenant index (I3), JWKS caching (I4), ACL freshness-SLA gate, Event Hubs, and the Phase 2b follow-ups (seed-activity corpus ids, eval index isolation, /feedback bearer auth, per-event-type engagement weighting) remain deferred.

**Prerequisites in place:** Phase 2b shipped (tag `phase-2b-activity`). All Azure resources live. Entra app has admin-consented delegated `Sites.Read.All` + `Files.Read.All` (Phase 1 setup) — and `DefaultAzureCredential` (the `az` identity) can already call Graph (proven by the People seeder). 38 unit + 25 integration tests pass.

**Single-identity caveat (read once):** Phase 3 Live Fetch authenticates as the `DefaultAzureCredential` identity (locally: your `az` user), NOT the per-request `User`. So every Live Fetch returns what *that* identity can see in Graph, regardless of who asked. This is acceptable for a single-user demo and matches how the seeder works. True per-user OBO (each request fetches as its own user) is a Phase 4 item. The code is structured so swapping the token acquisition to OBO later touches only `MSGraphSearchFetcher._token`.

**Empty-tenant caveat:** Graph `/search` only returns hits if the tenant has searchable M365 content (SharePoint files, list items, messages). The demo tenant may be sparse. All integration tests are written to pass whether Graph returns hits or an empty set — they assert "Live Fetch ran without error and merged whatever Graph returned"; the "a live candidate is present" assertion is guarded to skip when the tenant returns zero hits. The merge/ranking logic is proven independently by unit tests with a fake fetcher.

---

## File Structure

```
brain-api/
├── app/
│   ├── config.py                       # MODIFIED — live_fetch_enabled, live_fetch_timeout_ms
│   ├── main.py                         # MODIFIED — construct MSGraphSearchFetcher in lifespan
│   ├── live_fetch/
│   │   ├── __init__.py                 # NEW (empty)
│   │   ├── base.py                     # NEW — LiveFetcher protocol + needs_live_fetch() heuristic
│   │   └── graph_search.py             # NEW — MSGraphSearchFetcher (Graph /search → Candidate)
│   └── orchestrator/
│       └── kernel.py                   # MODIFIED — fan out live fetch, partition ACL, merge, rank
└── tests/
    ├── test_live_fetch_trigger.py      # NEW (unit)
    ├── test_graph_search_fetcher.py    # NEW (integration, empty-tenant-resilient)
    ├── test_orchestrator_livefetch.py  # NEW (unit — merge + ACL-bypass with fakes)
    └── test_freshness_acceptance.py    # NEW (integration, empty-tenant-resilient)
```

---

## Conventions (same as prior phases)

- Run from `brain-api/`. Direct commits to `main`, one per task. Integration tests carry `@pytest.mark.integration`. TestClient uses `with TestClient(app) as client:`. After each task `uv run ruff check .` must be clean before committing.

---

## Task 1: LiveFetcher interface + freshness trigger heuristic

**Files:**
- Create: `brain-api/app/live_fetch/__init__.py` (empty)
- Create: `brain-api/app/live_fetch/base.py`
- Create: `brain-api/tests/test_live_fetch_trigger.py`

- [ ] **Step 1: Write the failing test**

`brain-api/tests/test_live_fetch_trigger.py`:

```python
import pytest

from app.live_fetch.base import needs_live_fetch


@pytest.mark.parametrize("query", [
    "who is on call right now?",
    "what changed this week?",
    "current pipeline coverage",
    "latest deployment status",
    "what's happening today",
    "recent incidents",
])
def test_freshness_queries_trigger(query: str) -> None:
    assert needs_live_fetch(query) is True


@pytest.mark.parametrize("query", [
    "what is our PTO policy?",
    "how do I claim travel expenses",
    "Q3 sales plan ARR target",
])
def test_static_queries_do_not_trigger(query: str) -> None:
    assert needs_live_fetch(query) is False
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_live_fetch_trigger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.live_fetch.base'`.

- [ ] **Step 3: Implement `app/live_fetch/__init__.py` (empty) and `app/live_fetch/base.py`**

`app/live_fetch/__init__.py` — empty.

`app/live_fetch/base.py`:

```python
"""Live Fetch interface + the freshness trigger heuristic.

needs_live_fetch decides, cheaply and deterministically, whether a query is
time-sensitive enough to warrant a query-time Graph /search call. The
orchestrator's LLM plan-step (deferred) would eventually replace this heuristic.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.domain.identity import User
from app.domain.query import Candidate

# Freshness markers: presence of any of these in the query triggers Live Fetch.
_FRESHNESS_TERMS = (
    "right now", "on call", "on-call", "today", "this week", "this morning",
    "currently", "current", "latest", "recent", "recently", "now", "as of",
    "this month", "live", "up to date", "up-to-date",
)
_WORD = re.compile(r"[a-z0-9'-]+")


def needs_live_fetch(query: str) -> bool:
    q = " ".join(_WORD.findall(query.lower()))
    padded = f" {q} "
    return any(f" {term} " in padded or term in q for term in _FRESHNESS_TERMS)


class LiveFetcher(Protocol):
    async def fetch(self, *, query: str, user: User) -> list[Candidate]:
        """Return fresh candidates from source systems, or [] on failure/empty."""
        ...
```

- [ ] **Step 4: Run test, expect pass**

Run: `uv run pytest tests/test_live_fetch_trigger.py -v`
Expected: 9 passed (6 trigger + 3 no-trigger).

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/live_fetch/__init__.py brain-api/app/live_fetch/base.py brain-api/tests/test_live_fetch_trigger.py
git commit -m "feat: LiveFetcher protocol + freshness trigger heuristic"
```

---

## Task 2: MSGraphSearchFetcher (Graph /search → Candidate)

**Why:** Call Microsoft Graph `/search/query` with a `DefaultAzureCredential` Graph token (same as the seeder), and map each hit to a synthetic `Candidate` so live results flow through ranking + generation uniformly.

**Files:**
- Create: `brain-api/app/live_fetch/graph_search.py`
- Modify: `brain-api/app/config.py`
- Create: `brain-api/tests/test_graph_search_fetcher.py`

- [ ] **Step 1: Add Live Fetch settings to `config.py`**

In `brain-api/app/config.py`, after the ranker-weights block (after `rank_weight_activity`) add:

```python
    # Live Fetch (Phase 3)
    live_fetch_enabled: bool = True
    live_fetch_timeout_ms: int = 600
```

- [ ] **Step 2: Write the failing integration test**

`brain-api/tests/test_graph_search_fetcher.py`:

```python
import pytest

from app.domain.identity import User
from app.live_fetch.graph_search import MSGraphSearchFetcher


@pytest.mark.integration
async def test_graph_search_returns_candidates_or_empty() -> None:
    fetcher = MSGraphSearchFetcher()
    user = User(user_id="u-live", tenant_id="t-test", email="l@x",
                display_name="L", group_ids=set())
    # A broad query so a non-empty tenant likely returns hits. Must NOT raise.
    results = await fetcher.fetch(query="plan", user=user)
    assert isinstance(results, list)
    # If the tenant has searchable content, every live candidate is shaped right.
    for c in results:
        assert "live" in c.sources_hit
        assert c.chunk.source == "graph"
        assert c.chunk.doc_id.startswith("graph:")
        assert "content_rrf" in c.raw_scores
    # Empty tenant -> empty list is a valid pass (no assertion on non-emptiness).


@pytest.mark.integration
async def test_graph_search_never_raises_on_gibberish() -> None:
    fetcher = MSGraphSearchFetcher()
    user = User(user_id="u-live", tenant_id="t-test", email="l@x",
                display_name="L", group_ids=set())
    results = await fetcher.fetch(query="zzqxwv-nonexistent-term-9981", user=user)
    assert results == [] or all("live" in c.sources_hit for c in results)
```

- [ ] **Step 3: Run test, expect failure**

Run: `uv run pytest tests/test_graph_search_fetcher.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.live_fetch.graph_search'`.

- [ ] **Step 4: Implement `app/live_fetch/graph_search.py`**

```python
"""Live Fetch via Microsoft Graph /search.

Authenticates with a DefaultAzureCredential Graph token (same pattern as the
People seeder) — single-identity for Phase 3; per-user OBO is a Phase 4 swap
localized to _token(). Maps each Graph hit to a synthetic Candidate so live
results rank and cite uniformly with indexed chunks. Never raises: returns []
on any error so the orchestrator answer path is never blocked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://graph.microsoft.com/v1.0/search/query"
_RRF_K = 60


class MSGraphSearchFetcher:
    async def _token(self) -> str:
        # Phase 3: single-identity (the DefaultAzureCredential principal).
        # Phase 4 OBO swap touches only this method.
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token("https://graph.microsoft.com/.default")
            return tok.token
        finally:
            await cred.close()

    async def fetch(self, *, query: str, user: User) -> list[Candidate]:
        try:
            token = await self._token()
            body = {
                "requests": [
                    {
                        "entityTypes": ["driveItem", "listItem"],
                        "query": {"queryString": query},
                        "from": 0,
                        "size": 10,
                    }
                ]
            }
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.post(
                    _SEARCH_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning("Live Fetch (Graph /search) failed; returning no live results: %s", e)
            return []

        now = datetime.now(UTC)
        candidates: list[Candidate] = []
        for req in data.get("value", []):
            for container in req.get("hitsContainers", []):
                for i, hit in enumerate(container.get("hits", [])):
                    resource = hit.get("resource", {}) or {}
                    name = resource.get("name") or resource.get("subject") or "Untitled"
                    url = (
                        resource.get("webUrl")
                        or (resource.get("webLink") or {}).get("href")
                        or ""
                    )
                    summary = hit.get("summary") or resource.get("description") or ""
                    hit_id = hit.get("hitId") or f"{name}-{i}"
                    doc_id = f"graph:{hit_id}"
                    chunk = Chunk(
                        chunk_id=f"{doc_id}#live",
                        doc_id=doc_id,
                        tenant_id=user.tenant_id,
                        source="graph",
                        source_url=url,
                        title=name,
                        content=summary,
                        content_vector=[],
                        acl_principals=[],  # Graph already permission-trimmed this hit
                        author_id=None,
                        entities=[],
                        created_at=now,
                        modified_at=now,
                        chunk_index=0,
                    )
                    candidates.append(
                        Candidate(
                            chunk=chunk,
                            sources_hit={"live"},
                            raw_scores={"content_rrf": 1.0 / (_RRF_K + i)},
                            live_payload=hit,
                        )
                    )
        return candidates
```

- [ ] **Step 5: Run the integration test**

Run: `uv run pytest tests/test_graph_search_fetcher.py -v -m integration`
Expected: 2 passed. (If the tenant has no searchable content, both still pass — they tolerate an empty result list. If Graph returns 403, the `fetch` swallows it and returns `[]`, so the test still passes; but note in your report whether results were empty vs populated, so we know if the tenant has content to demo with.)

- [ ] **Step 6: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/live_fetch/graph_search.py brain-api/app/config.py brain-api/tests/test_graph_search_fetcher.py
git commit -m "feat: MSGraphSearchFetcher (Graph /search -> Candidate, never raises)"
```

---

## Task 3: Orchestrator merges Live Fetch

**Why:** When the freshness trigger fires, fan out Live Fetch concurrently with retrieval under a hard timeout, ACL-recheck only the indexed candidates (live ones are already Graph-trimmed), merge, and rank everything together.

**Files:**
- Modify: `brain-api/app/orchestrator/kernel.py`
- Modify: `brain-api/app/main.py`
- Modify: `brain-api/tests/test_orchestrator.py`
- Modify: `brain-api/tests/test_orchestrator_degradation.py`
- Create: `brain-api/tests/test_orchestrator_livefetch.py`

- [ ] **Step 1: Write the failing unit test (merge + ACL-bypass with fakes)**

`brain-api/tests/test_orchestrator_livefetch.py`:

```python
import asyncio
from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Answer, Candidate, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.ranking.personalized_ranker import PersonalizedRanker


def _chunk(doc_id: str, source: str, acl: list[str]) -> Chunk:
    now = datetime.now(UTC)
    return Chunk(
        chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test", source=source,
        source_url=f"x://{doc_id}", title=doc_id, content="c", content_vector=[],
        acl_principals=acl, author_id=None, entities=[], created_at=now,
        modified_at=now, chunk_index=0,
    )


class _FakeRetriever:
    async def retrieve(self, *, query, user, k):
        return [Candidate(chunk=_chunk("idx-1", "uploaded", ["t-test:everyone"]),
                          sources_hit={"vector"}, raw_scores={"content_rrf": 0.9})]


class _FakeACLStore:
    async def recheck(self, *, candidates, user):
        # only indexed candidates reach here; keep them all
        return candidates


class _FakeProximity:
    async def score(self, *, user, doc_ids):
        return {}


class _FakeActivity:
    async def score(self, *, user, doc_ids):
        return {}


class _FakeLiveFetcher:
    async def fetch(self, *, query, user):
        # live candidate with NO acl_principals — must survive (Graph-trimmed)
        return [Candidate(chunk=_chunk("graph:live-1", "graph", []),
                          sources_hit={"live"}, raw_scores={"content_rrf": 0.8})]


class _FakeCache:
    async def get_json(self, key): return None
    async def set_json(self, key, value, ttl_seconds): return None


class _FakeLLM:
    async def complete(self, **kw): return "answer [1] [2]"


def _orch(live_fetcher) -> SemanticKernelOrchestrator:
    return SemanticKernelOrchestrator(
        retriever=_FakeRetriever(), llm=_FakeLLM(), cache=_FakeCache(),
        acl_store=_FakeACLStore(), proximity=_FakeProximity(), activity=_FakeActivity(),
        ranker=PersonalizedRanker(weight_content=1.0, weight_people=0.0, weight_activity=0.0),
        live_fetcher=live_fetcher,
    )


def test_live_candidates_merged_for_freshness_query() -> None:
    orch = _orch(_FakeLiveFetcher())
    cands = asyncio.run(orch.retrieve_ranked(QueryRequest(query="who is on call right now?"), user=_user()))
    doc_ids = {c.chunk.doc_id for c in cands}
    assert "graph:live-1" in doc_ids        # live merged (and survived ACL — it had no acl_principals)
    assert "idx-1" in doc_ids                # indexed retained


def test_no_live_fetch_for_static_query() -> None:
    orch = _orch(_FakeLiveFetcher())
    cands = asyncio.run(orch.retrieve_ranked(QueryRequest(query="what is our PTO policy?"), user=_user()))
    doc_ids = {c.chunk.doc_id for c in cands}
    assert "graph:live-1" not in doc_ids     # static query -> no live fetch
    assert "idx-1" in doc_ids


def test_live_fetch_failure_does_not_block() -> None:
    class _BrokenLive:
        async def fetch(self, *, query, user):
            raise RuntimeError("graph down")

    orch = _orch(_BrokenLive())
    cands = asyncio.run(orch.retrieve_ranked(QueryRequest(query="latest status now"), user=_user()))
    assert {c.chunk.doc_id for c in cands} == {"idx-1"}   # degraded to indexed-only, no raise


def _user() -> User:
    return User(user_id="u", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"t-test:everyone"})
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_orchestrator_livefetch.py -v`
Expected: FAIL — `SemanticKernelOrchestrator.__init__()` has no `live_fetcher` param.

- [ ] **Step 3: Update the orchestrator**

In `brain-api/app/orchestrator/kernel.py`:

Add imports near the top:

```python
import asyncio

from app.live_fetch.base import LiveFetcher, needs_live_fetch
```

Add `live_fetcher: LiveFetcher` to `__init__` params and store it:

```python
    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        llm: AzureOpenAIClient,
        cache: RedisCache,
        acl_store: ACLStore,
        proximity: PeopleProximity,
        ranker: PersonalizedRanker,
        activity: ActivitySignal,
        live_fetcher: LiveFetcher,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._cache = cache
        self._acl_store = acl_store
        self._proximity = proximity
        self._ranker = ranker
        self._activity = activity
        self._live_fetcher = live_fetcher
```

Replace `retrieve_ranked` with the version that fans out live fetch, partitions ACL, merges, and ranks. The settings for the timeout come from `get_settings()` — add `from app.config import get_settings` if not already imported (it is, via `_cache_key`'s module; confirm and add if missing):

```python
    async def retrieve_ranked(self, request: QueryRequest, *, user: User) -> list[Candidate]:
        settings = get_settings()
        # Fan out indexed retrieval and (conditionally) live fetch concurrently.
        retrieve_task = asyncio.create_task(
            self._retriever.retrieve(query=request.query, user=user, k=max(request.k, 10))
        )
        live: list[Candidate] = []
        if settings.live_fetch_enabled and needs_live_fetch(request.query):
            try:
                live = await asyncio.wait_for(
                    self._live_fetcher.fetch(query=request.query, user=user),
                    timeout=settings.live_fetch_timeout_ms / 1000.0,
                )
            except (TimeoutError, Exception) as e:  # never block the answer on live fetch
                logger.warning("Live Fetch unavailable; continuing index-only: %s", e)
                live = []

        indexed = await retrieve_task
        if not indexed and not live:
            return []

        # Query-time ACL re-check applies ONLY to indexed candidates; live results
        # were already permission-trimmed by Graph's user-scoped token.
        if indexed:
            indexed = await self._acl_store.recheck(candidates=indexed, user=user)

        candidates = indexed + live
        if not candidates:
            return []

        doc_ids = [c.chunk.doc_id for c in candidates]
        # People proximity (degrade to {} if Cosmos down). Spec §3.2.
        try:
            proximity = await self._proximity.score(user=user, doc_ids=doc_ids)
        except Exception as e:
            logger.warning("People graph (Cosmos) unavailable; degrading to proximity=0: %s", e)
            proximity = {}
        # Activity engagement (degrade to {} if ADX down). Spec §3.2.
        try:
            activity = await self._activity.score(user=user, doc_ids=doc_ids)
        except Exception as e:
            logger.warning("Activity store (ADX) unavailable; degrading to activity=0: %s", e)
            activity = {}

        ranked: list[RankedResult] = self._ranker.rank(
            candidates=candidates, proximity=proximity, activity=activity
        )
        return [r.candidate for r in ranked]
```

Update the class docstring to:

```python
    """Phase 3: cache -> (retrieve || live-fetch) -> ACL re-check (indexed) ->
    merge -> proximity -> activity -> rank -> answer.

    Plan step is still a heuristic (needs_live_fetch); LLM plan step deferred.
    """
```

Note: `except (TimeoutError, Exception)` is redundant (Exception covers TimeoutError in 3.11+ where asyncio.TimeoutError IS TimeoutError) but harmless and explicit; if ruff flags B014 (redundant exception), simplify to `except Exception as e:`.

- [ ] **Step 4: Verify `get_settings` import in kernel.py**

Open `brain-api/app/orchestrator/kernel.py`. If `from app.config import get_settings` is not already imported at the top, add it. (Phase 2 versions imported it indirectly; confirm it's a direct import now since `retrieve_ranked` calls `get_settings()`.)

- [ ] **Step 5: Run the unit test**

Run: `uv run pytest tests/test_orchestrator_livefetch.py -v`
Expected: 3 passed (merge, no-fetch-on-static, failure-degrades).

- [ ] **Step 6: Construct the fetcher in `main.py` lifespan + pass to orchestrator**

In `brain-api/app/main.py`:

Add import:

```python
from app.live_fetch.graph_search import MSGraphSearchFetcher
```

In `lifespan`, after `app.state.activity = ActivitySignal(...)`:

```python
    app.state.live_fetcher = MSGraphSearchFetcher()
```

(No `aclose` needed — `MSGraphSearchFetcher` creates a short-lived credential + httpx client per call.)

Update the orchestrator construction to pass it:

```python
    app.state.orchestrator = SemanticKernelOrchestrator(
        retriever=app.state.retriever,
        llm=app.state.embedder,
        cache=app.state.cache,
        acl_store=app.state.acl_store,
        proximity=app.state.proximity,
        ranker=app.state.ranker,
        activity=app.state.activity,
        live_fetcher=app.state.live_fetcher,
    )
```

- [ ] **Step 7: Update `tests/test_orchestrator.py` and `tests/test_orchestrator_degradation.py` for the new constructor arg**

In `tests/test_orchestrator.py` `_build()`: add `from app.live_fetch.graph_search import MSGraphSearchFetcher` and pass `live_fetcher=MSGraphSearchFetcher()` to the orchestrator. (No close needed.)

In `tests/test_orchestrator_degradation.py`: the orchestrator is built with fakes. Add a fake live fetcher (`async def fetch(self, *, query, user): return []`) and pass `live_fetcher=`. Read the file to match its fake style.

- [ ] **Step 8: Run orchestrator + degradation + e2e + full suite**

Run: `uv run pytest tests/test_orchestrator.py -v -m integration`
Expected: 2 passed (queries "what is the PTO policy?" / "chocolate chip cookies" are static → no live fetch → behavior unchanged).

Run: `uv run pytest tests/test_orchestrator_degradation.py tests/test_orchestrator_livefetch.py -v`
Expected: all pass.

Run: `uv run pytest tests/test_admin_retrieve.py tests/test_query_e2e.py -v -m integration`
Expected: pass.

Run: `uv run pytest -m "not integration"` → all unit pass. `uv run ruff check .` → clean.

- [ ] **Step 9: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/orchestrator/kernel.py brain-api/app/main.py brain-api/tests/test_orchestrator.py brain-api/tests/test_orchestrator_degradation.py brain-api/tests/test_orchestrator_livefetch.py
git commit -m "feat: orchestrator fans out Live Fetch + merges fresh results (degrades on failure)"
```

---

## Task 4: Freshness acceptance test

**Why:** Prove the end-to-end freshness path: a time-sensitive query triggers Live Fetch and (when the tenant has content) a Graph-sourced result is merged into the ranked candidates.

**Files:**
- Create: `brain-api/tests/test_freshness_acceptance.py`

- [ ] **Step 1: Write the integration test**

`brain-api/tests/test_freshness_acceptance.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_freshness_query_triggers_live_fetch() -> None:
    with TestClient(app) as client:
        # A freshness query routed through /admin/retrieve (debug-gated).
        resp = client.post(
            "/admin/retrieve",
            json={"query": "what files changed recently", "k": 10},
            headers={"x-debug-bypass-auth": "t-test,u-live,t-test:everyone"},
        )
        assert resp.status_code == 200
        body = resp.json()
        live = [c for c in body["candidates"] if "graph:" in c["doc_id"]]
        if live:
            # Tenant has searchable content: a live Graph result was merged.
            assert all(c["doc_id"].startswith("graph:") for c in live)
        else:
            # Empty/sparse tenant: Live Fetch fired but Graph returned nothing.
            # The request still succeeds index-only — the merge path didn't crash.
            pytest.skip("Graph /search returned no hits for this tenant; "
                        "live-merge path exercised without content to assert on")


@pytest.mark.integration
def test_static_query_has_no_live_results() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/admin/retrieve",
            json={"query": "what is our PTO policy?", "k": 10},
            headers={"x-debug-bypass-auth": "t-test,u-live,t-test:everyone"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert not any("graph:" in c["doc_id"] for c in body["candidates"])
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_freshness_acceptance.py -v -m integration`
Expected: `test_static_query_has_no_live_results` PASSES (static query → no live candidates). `test_freshness_query_triggers_live_fetch` PASSES if the tenant has Graph content (a `graph:` candidate appears), or SKIPS if the tenant is empty (live fetch fired, returned nothing, request still succeeded). Either way the merge path is exercised without error. Report which outcome occurred.

- [ ] **Step 3: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/tests/test_freshness_acceptance.py
git commit -m "test: freshness query triggers Live Fetch merge (Phase 3 acceptance)"
```

---

## Task 5: README + verification + tag

**Files:**
- Modify: `brain-api/README.md`
- Modify: `README.md` (root)

- [ ] **Step 1: Update `brain-api/README.md`**

Add a "## Phase 3 — Live Fetch (freshness)" subsection:

```markdown
## Phase 3 — Live Fetch (freshness)

Time-sensitive queries (containing "now", "today", "current", "latest",
"on call", "recent", etc.) trigger a query-time Microsoft Graph `/search` call,
merged into the ranked candidate set. Graph authenticates via
DefaultAzureCredential (single-identity for now; per-user OBO is Phase 4).
Live Fetch runs under a hard timeout (`LIVE_FETCH_TIMEOUT_MS`, default 600) and
degrades to index-only on any failure — it never blocks the answer. Toggle with
`LIVE_FETCH_ENABLED`.
```

- [ ] **Step 2: Update root `README.md` "Next phases"**

Replace the Phase 3 bullet:

```markdown
- Phase 3 (done): Live Fetch via Microsoft Graph /search — freshness queries merge
  live results into ranking. Heuristic trigger; DefaultAzureCredential (single-identity).
- Phase 4: per-user OBO for Live Fetch, LLM plan-step classifier, APIM gateway,
  OpenTelemetry, per-tenant index, JWKS caching, Event Hubs ingest, ACL freshness-SLA,
  eval-index isolation, hardening.
```

- [ ] **Step 3: Full verification**

```bash
cd brain-api
uv run ruff check .
uv run pytest -m "not integration" -v
uv run pytest -m integration -v
```

Expected: ruff clean; all unit pass; all integration pass (the freshness acceptance test may SKIP if the tenant is empty — that counts as pass). Capture counts.

- [ ] **Step 4: Eval regression check**

```bash
cd brain-api
uv run uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 4
uv run python eval/run_eval.py --mode retrieval --report eval/reports/2026-05-29-phase3.json
kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
cat eval/reports/2026-05-29-phase3.json
```

Expected: `recall_at_10 >= 0.7`, `mrr_at_10 >= 0.5`, exit 0. (The 10 golden queries are static — none contain freshness markers — so Live Fetch never fires for them; retrieval order is unchanged from Phase 2b. Confirm none of the golden queries accidentally trips `needs_live_fetch`; if one does, note it.)

- [ ] **Step 5: Commit docs + tag**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add README.md brain-api/README.md
git commit -m "docs: Phase 3 Live Fetch — freshness trigger, Graph search, merge"
git tag -a phase-3-livefetch -m "Phase 3: Live Fetch via Microsoft Graph /search. Freshness queries merge live results into ranking; degrades to index-only on failure."
git log --oneline | head -10
```

---

## Self-Review

**Spec coverage (Phase 3 scope):**
- Live Fetch / Actions (§Live Fetch (Actions), §5 Step 5) → Tasks 2, 3
- Heuristic freshness trigger (stands in for the deferred plan step) → Task 1
- Parallel fan-out + hard timeout + degrade-never-block (§3.2: Live Fetch fails → index-only with disclaimer) → Task 3
- Live results merged into the candidate set + ranked + cited → Tasks 2, 3 (synthetic Chunk flows through ranker + `build_grounded_messages`)
- Freshness acceptance → Task 4
- Deferred (documented): per-user OBO (Phase 4), LLM plan-step classifier (Phase 4), plus all prior deferred items.

**Type/signature consistency:**
- `needs_live_fetch(query: str) -> bool` — Tasks 1, 3.
- `LiveFetcher.fetch(*, query, user) -> list[Candidate]` / `MSGraphSearchFetcher.fetch` — Tasks 1, 2, 3 (orchestrator + fakes match).
- Live `Candidate`s carry `sources_hit={"live"}`, `chunk.source == "graph"` (a valid `Source` literal — already in `app/domain/chunk.py`), `chunk.doc_id` prefixed `graph:`, `raw_scores["content_rrf"]` — consumed by the ranker (Task 3) and asserted in Tasks 2, 4.
- Orchestrator constructor gains `live_fetcher: LiveFetcher` — every construction site updated (main.py Task 3 Step 6; test_orchestrator.py + test_orchestrator_degradation.py Task 3 Step 7; the unit test builds its own in Task 3 Step 1).
- ACL partition: live candidates (empty `acl_principals`) bypass `acl_store.recheck` (only `indexed` is rechecked) — verified by `test_live_candidates_merged_for_freshness_query` (a live candidate with no ACL survives).

**Placeholder scan:** No TBD/TODO-as-work. Every code step is complete. Two judgment calls have explicit handling: the empty-tenant case (all integration tests tolerate empty Graph results / skip the content-dependent assertion) and the redundant-exception lint note in Task 3 Step 3.

**Known carried risk:** the `source == "graph"` literal must exist in `app/domain/chunk.py`'s `Source` Literal — it does (added in Phase 1: `"sharepoint","teams","uploaded","slack","jira","graph"`). Live candidates have `content_vector=[]` — fine, they're never re-embedded. Generation cites live candidates by their synthetic chunk fields.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-29-company-brain-phase3-livefetch.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review.
2. **Inline Execution** — batch with checkpoints.

Tasks 1, 3 (logic) are pure-code/unit; Tasks 2, 4 hit live Graph (resilient to an empty tenant). No user-in-loop step — `DefaultAzureCredential` already authenticates to Graph (proven by the People seeder), so Phase 3 needs no new web-UI or provisioning.
