# Company Brain — Phase 2a Implementation Plan (Personalization + People Pillar)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the same query return a different ranking for different users by adding the People pillar (Cosmos DB Gremlin org graph seeded from Microsoft Graph), a query-time ACL re-check (the second half of double-enforcement), and a personalized multi-signal ranker that fuses Content relevance with People proximity.

**Architecture:** Extends the Phase 1 FastAPI monolith. Two foundational fixes land first: (I2) all Azure clients move from module-level `@lru_cache` into the FastAPI `lifespan` and are closed on shutdown, so adding the Cosmos client doesn't repeat the event-loop-binding/leak pattern; (M2) a real retrieval-only eval path replaces the citation-proxy metric. Then the People pillar: a `PeopleGraphClient` over Cosmos Gremlin, a seeder that materializes the Entra org graph, a `PeopleProximity` scorer, an `ACLStore` for query-time re-check, and a `PersonalizedRanker` that the orchestrator calls between retrieve and generate.

**Tech Stack:** Existing (Python 3.12, FastAPI, uv, pydantic v2, Azure AI Search, Azure OpenAI, Redis) + `gremlinpython` (Cosmos DB Gremlin), Microsoft Graph `/users` + `/groups` (app-only token, already consented `Directory.Read.All`).

**Scope cut:** This plan covers the **personalization slice** of spec Zone 4. The Activity pillar (Azure Data Explorer / Event Hubs) and the activity engagement signal are **deferred to Phase 2b**. Live Fetch (Microsoft Graph search) is **Phase 3**. Per-tenant index routing (review finding I3), JWKS caching (I4), and APIM/OpenTelemetry are **Phase 4**.

**Prerequisites in place:** Phase 1 shipped (tag `phase-1-mvp`). Azure resources live in `swedencentral`, resource group `rg-company-brain-dev`. `brain-api/.env` populated. Entra app `brain-api` has admin-consented `Directory.Read.All` (application). Signed-in `az` user has data-plane roles. `ENABLE_DEBUG_AUTH=true` and `ADMIN_API_KEY=dev-admin-key-local` set in local `.env`.

---

## File Structure

New files in Phase 2a:

```
brain-api/
├── app/
│   ├── deps.py                         # MODIFIED — read clients from app.state (Request), not lru_cache
│   ├── main.py                         # MODIFIED — construct + close clients in lifespan
│   ├── api/
│   │   ├── admin.py                    # MODIFIED — add POST /admin/seed-people
│   │   └── retrieve.py                 # NEW — POST /admin/retrieve (raw ranked retrieval for eval)
│   ├── domain/
│   │   └── query.py                    # MODIFIED — add RankedResult
│   ├── people/
│   │   ├── __init__.py                 # NEW (empty)
│   │   ├── graph_client.py             # NEW — PeopleGraphClient (Cosmos Gremlin wrapper)
│   │   ├── proximity.py                # NEW — PeopleProximity.score(user, doc_ids)
│   │   └── seeder.py                   # NEW — PeopleSeeder (MS Graph → Cosmos vertices/edges)
│   ├── acl/
│   │   ├── enforcement.py              # MODIFIED — add query_time_recheck()
│   │   └── store.py                    # NEW — ACLStore (Redis doc-ACL get + recheck)
│   ├── ranking/
│   │   ├── __init__.py                 # NEW (empty)
│   │   └── personalized_ranker.py      # NEW — PersonalizedRanker (RRF content + people)
│   ├── retrieval/
│   │   ├── ai_search_client.py         # MODIFIED — add aclose(); drop module lru_cache
│   │   └── hybrid_retriever.py         # MODIFIED — populate raw_scores with content rank
│   ├── generation/
│   │   └── azure_openai.py             # MODIFIED — add aclose(); drop module lru_cache
│   ├── cache/
│   │   └── redis_cache.py              # MODIFIED — add aclose(); drop module lru_cache
│   ├── ingest/
│   │   └── pipeline.py                 # MODIFIED — write doc ACL to ACLStore on ingest
│   ├── orchestrator/
│   │   └── kernel.py                   # MODIFIED — add retrieve_ranked(); wire proximity+recheck+rank
│   └── config.py                       # MODIFIED — add cosmos_* + ranker weight settings
├── eval/
│   ├── personas.json                   # NEW — 2 persona User fixtures
│   ├── run_eval.py                     # MODIFIED — retrieval mode hits /admin/retrieve
│   └── golden_personas.jsonl           # NEW — persona-differentiated ranking expectations
├── infra/
│   └── provision_cosmos.sh             # NEW — provision Cosmos DB Gremlin (idempotent)
└── tests/
    ├── test_lifespan_clients.py        # NEW
    ├── test_admin_retrieve.py          # NEW
    ├── test_hybrid_retriever_scores.py # NEW
    ├── test_people_graph_client.py     # NEW (integration)
    ├── test_people_seeder.py           # NEW (integration)
    ├── test_people_proximity.py        # NEW (integration)
    ├── test_acl_store.py               # NEW (integration + unit)
    ├── test_personalized_ranker.py     # NEW (unit)
    └── test_persona_ranking.py         # NEW (integration — the headline acceptance test)
```

---

## Conventions (same as Phase 1)

- Run from `brain-api/`. Single test: `uv run pytest tests/test_x.py::test_name -v`. All unit: `uv run pytest -m "not integration"`. Integration: `uv run pytest -m integration`.
- Direct commits to `main` (solo hackathon, user-consented). One commit per task.
- Integration tests carry `@pytest.mark.integration` and hit real Azure.
- `DefaultAzureCredential` uses the signed-in `az` user locally.
- After each task: `uv run ruff check .` must be clean before committing.

---

## Task 1: Move Azure clients into FastAPI lifespan (review finding I2)

**Why:** Module-level `@lru_cache` on async client factories binds aiohttp/redis sessions to the event loop that first built them, and nothing ever closes them. This works under single-worker uvicorn but is fragile and leaks sockets on redeploy. Before adding a Cosmos client (Task 5), fix the pattern: construct clients once in `lifespan`, store on `app.state`, close on shutdown. Direct-construction in tests still works (each builds its own).

**Files:**
- Modify: `brain-api/app/generation/azure_openai.py`
- Modify: `brain-api/app/retrieval/ai_search_client.py`
- Modify: `brain-api/app/cache/redis_cache.py`
- Modify: `brain-api/app/main.py`
- Modify: `brain-api/app/deps.py`
- Modify: `brain-api/tests/conftest.py`
- Create: `brain-api/tests/test_lifespan_clients.py`

- [ ] **Step 1: Write the failing test**

`brain-api/tests/test_lifespan_clients.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_lifespan_populates_and_closes_clients() -> None:
    # Entering the TestClient context runs the lifespan startup.
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        # clients are constructed and stored on app.state during startup
        assert app.state.embedder is not None
        assert app.state.ai_search is not None
        assert app.state.cache is not None
        assert app.state.retriever is not None
        assert app.state.orchestrator is not None
    # after the context exits, shutdown ran without raising
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_lifespan_clients.py -v`
Expected: FAIL — `AttributeError: 'State' object has no attribute 'embedder'`.

- [ ] **Step 3: Add `aclose()` to `AzureOpenAIClient` and drop the module lru_cache**

Replace `brain-api/app/generation/azure_openai.py` lines 1-25 (imports + `_client`) and the `__init__` so the client is constructed per-instance and closeable. Full file:

```python
from __future__ import annotations

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


class AzureOpenAIClient:
    def __init__(self) -> None:
        self._s = get_settings()
        self._credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            self._credential, "https://cognitiveservices.azure.com/.default"
        )
        self._cli = AsyncAzureOpenAI(
            azure_endpoint=self._s.azure_openai_endpoint,
            api_version=self._s.azure_openai_api_version,
            azure_ad_token_provider=token_provider,
        )

    async def aclose(self) -> None:
        await self._cli.close()
        await self._credential.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def embed(self, text: str) -> list[float]:
        resp = await self._cli.embeddings.create(
            model=self._s.azure_openai_embed_deployment,
            input=text,
            dimensions=3072,
        )
        return resp.data[0].embedding

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._cli.embeddings.create(
            model=self._s.azure_openai_embed_deployment,
            input=texts,
            dimensions=3072,
        )
        return [d.embedding for d in resp.data]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        deployment: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 800,
    ) -> str:
        resp = await self._cli.chat.completions.create(
            model=deployment or self._s.azure_openai_chat_deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
```

- [ ] **Step 4: Add `aclose()` to `AISearchClient` and drop the module lru_cache**

Replace `brain-api/app/retrieval/ai_search_client.py` lines 27-49 (the `_client` factory + `__init__` + `upsert_chunks` keep their bodies). Full file:

```python
from __future__ import annotations

from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from app.acl.enforcement import build_acl_filter
from app.config import get_settings
from app.domain.chunk import Chunk
from app.domain.identity import User


def _to_search_doc(c: Chunk) -> dict:
    d = c.model_dump(mode="python")
    d["created_at"] = c.created_at.isoformat()
    d["modified_at"] = c.modified_at.isoformat()
    return d


def _from_search_doc(d: dict) -> Chunk:
    return Chunk.model_validate(d)


class AISearchClient:
    def __init__(self) -> None:
        s = get_settings()
        self._credential = DefaultAzureCredential()
        self._cli = SearchClient(
            endpoint=s.azure_ai_search_endpoint,
            index_name=s.azure_ai_search_index,
            credential=self._credential,
        )

    async def aclose(self) -> None:
        await self._cli.close()
        await self._credential.close()

    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        await self._cli.merge_or_upload_documents(
            documents=[_to_search_doc(c) for c in chunks],
            params={"allowUnsafeKeys": "true"},
        )

    async def hybrid_search(
        self, *, query: str, user: User, vector: list[float], top: int = 30
    ) -> list[Chunk]:
        flt = build_acl_filter(user)
        vector_query = VectorizedQuery(
            vector=vector, k_nearest_neighbors=50, fields="content_vector"
        )
        results = await self._cli.search(
            search_text=query,
            vector_queries=[vector_query],
            query_type="semantic",
            semantic_configuration_name="brain-semantic",
            filter=flt,
            top=top,
            select=[
                "chunk_id", "doc_id", "tenant_id", "source", "source_url", "title",
                "content", "acl_principals", "author_id", "entities", "created_at",
                "modified_at", "chunk_index",
            ],
        )
        chunks: list[Chunk] = []
        async for r in results:
            r["content_vector"] = []
            chunks.append(_from_search_doc(r))
        return chunks
```

- [ ] **Step 5: Add `aclose()` to `RedisCache` and drop the module lru_cache**

Replace `brain-api/app/cache/redis_cache.py` lines 21-43 (the `_pool` factory + `__init__`). Full file:

```python
from __future__ import annotations

import hashlib
import json
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings

logger = logging.getLogger(__name__)

_CACHE_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)


def _embed_key(text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()
    return f"cache:embed:{h}"


class RedisCache:
    def __init__(self) -> None:
        s = get_settings()
        self._r = redis.Redis(
            host=s.azure_redis_host,
            port=s.azure_redis_port,
            ssl=s.azure_redis_ssl,
            password=s.redis_key,
            decode_responses=True,
        )

    async def aclose(self) -> None:
        await self._r.aclose()

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        try:
            await self._r.set(name=key, value=json.dumps(value), ex=ttl_seconds)
        except _CACHE_ERRORS as e:
            logger.warning("Redis set_json failed (key=%s); skipping cache write: %s", key, e)

    async def get_json(self, key: str) -> dict | None:
        try:
            v = await self._r.get(key)
        except _CACHE_ERRORS as e:
            logger.warning("Redis get_json failed (key=%s); treating as cache miss: %s", key, e)
            return None
        return json.loads(v) if v else None

    async def set_embedding(self, text: str, vec: list[float], ttl_seconds: int = 86400) -> None:
        await self.set_json(_embed_key(text), {"v": vec}, ttl_seconds=ttl_seconds)

    async def get_embedding(self, text: str) -> list[float] | None:
        d = await self.get_json(_embed_key(text))
        return d["v"] if d else None
```

- [ ] **Step 6: Construct + close clients in `lifespan`**

Replace `brain-api/app/main.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.query import router as query_router
from app.cache.redis_cache import RedisCache
from app.config import get_settings
from app.generation.azure_openai import AzureOpenAIClient
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings()
    app.state.embedder = AzureOpenAIClient()
    app.state.ai_search = AISearchClient()
    app.state.cache = RedisCache()
    app.state.retriever = HybridRetriever(
        search=app.state.ai_search, embedder=app.state.embedder
    )
    app.state.orchestrator = SemanticKernelOrchestrator(
        retriever=app.state.retriever,
        llm=app.state.embedder,
        cache=app.state.cache,
    )
    try:
        yield
    finally:
        await app.state.orchestrator.aclose()
        await app.state.cache.aclose()
        await app.state.ai_search.aclose()
        await app.state.embedder.aclose()


app = FastAPI(title="brain-api", version="0.1.0", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(query_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "brain-api"}
```

Note: `orchestrator.aclose()` is added in Step 7. Until then the file won't import — that's expected mid-task; complete Step 7 before running.

- [ ] **Step 7: Add a no-op `aclose()` to the orchestrator and rewrite `deps.py` to read from `app.state`**

Add to `brain-api/app/orchestrator/kernel.py` inside `SemanticKernelOrchestrator` (after `__init__`):

```python
    async def aclose(self) -> None:
        # Orchestrator owns no sockets of its own; its collaborators are closed
        # by the lifespan. Method exists so shutdown can call it uniformly.
        return None
```

Replace `brain-api/app/deps.py`:

```python
from fastapi import Request

from app.cache.redis_cache import RedisCache
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


def get_embedder(request: Request) -> AzureOpenAIClient:
    return request.app.state.embedder


def get_ai_search(request: Request) -> AISearchClient:
    return request.app.state.ai_search


def get_cache(request: Request) -> RedisCache:
    return request.app.state.cache


def get_retriever(request: Request) -> HybridRetriever:
    return request.app.state.retriever


def get_orchestrator(request: Request) -> SemanticKernelOrchestrator:
    return request.app.state.orchestrator


def get_ingest_pipeline(request: Request) -> IngestPipeline:
    return IngestPipeline(
        embedder=request.app.state.embedder, search=request.app.state.ai_search
    )
```

- [ ] **Step 8: Remove the now-stale lru_cache-clearing fixtures from `conftest.py`**

Open `brain-api/tests/conftest.py`. Find the autouse fixture(s) that call `.cache_clear()` on `_client`, `_pool`, `get_embedder`, `get_ai_search`, `get_ingest_pipeline`, `get_retriever`, `get_cache`, `get_orchestrator` (added across Phase 1 tasks 11/12/16/21/23). DELETE those `cache_clear()` calls and any fixture whose sole purpose was clearing them. Keep the `_default_env` env-setup fixture and the integration-env skip logic. The `_clear_settings_cache` fixture that clears `get_settings.cache_clear()` STAYS (config is still lru_cached).

After editing, the only autouse fixtures should be: `_default_env` (sets fake env for unit tests, skips for integration) and `_clear_settings_cache` (clears the settings singleton). If unsure which lines, search the file for `cache_clear` and remove every call EXCEPT `get_settings.cache_clear()`.

- [ ] **Step 9: Run the new test + full suite**

Run: `uv run pytest tests/test_lifespan_clients.py -v`
Expected: PASS.

Run: `uv run pytest -m "not integration" -v`
Expected: all unit tests pass (26 from Phase 1 + 1 new = 27).

Run: `uv run pytest -m integration -v`
Expected: 13 integration tests pass (they construct clients directly; the lru_cache removal means each builds its own — fine).

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/main.py brain-api/app/deps.py brain-api/app/generation/azure_openai.py brain-api/app/retrieval/ai_search_client.py brain-api/app/cache/redis_cache.py brain-api/app/orchestrator/kernel.py brain-api/tests/conftest.py brain-api/tests/test_lifespan_clients.py
git commit -m "refactor: construct Azure clients in lifespan + close on shutdown (I2)"
```

---

## Task 2: Real retrieval-only eval path (review finding M2)

**Why:** The Phase 1 eval measured post-LLM citations from the top-5 as a proxy for retrieval — it can't measure true Recall@10/MRR@10, and once the ranker lands we need an honest gate. Add a `POST /admin/retrieve` endpoint that returns ranked candidate doc_ids without generation, and point the eval harness at it. The endpoint calls a new orchestrator method `retrieve_ranked`, which in this task simply wraps the retriever; Task 10 enriches it with proximity + ACL re-check + ranker, so the eval metric automatically reflects personalization later.

**Files:**
- Modify: `brain-api/app/orchestrator/kernel.py`
- Create: `brain-api/app/api/retrieve.py`
- Modify: `brain-api/app/main.py`
- Modify: `brain-api/eval/run_eval.py`
- Create: `brain-api/tests/test_admin_retrieve.py`

- [ ] **Step 1: Write the failing test**

`brain-api/tests/test_admin_retrieve.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_admin_retrieve_returns_ranked_doc_ids() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/admin/retrieve",
            json={"query": "PTO policy", "k": 10},
            headers={"x-debug-bypass-auth": "t-test,u-eval,t-test:everyone"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "doc_ids" in body
        assert isinstance(body["doc_ids"], list)
        # the PTO doc should be retrieved for a PTO query
        assert any("pto" in d.lower() for d in body["doc_ids"])
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_admin_retrieve.py -v -m integration`
Expected: FAIL — 404 (route doesn't exist).

- [ ] **Step 3: Add `retrieve_ranked` to the orchestrator**

Add to `brain-api/app/orchestrator/kernel.py` inside `SemanticKernelOrchestrator` (after `answer`):

```python
    async def retrieve_ranked(self, request: QueryRequest, *, user: User) -> list[Candidate]:
        """Return candidates in final rank order WITHOUT generating an answer.

        Phase 2a: this is the retriever's output. Task 10 enriches it with
        People proximity, ACL re-check, and the personalized ranker so the
        eval metric reflects personalization. Used by /admin/retrieve for the
        retrieval-quality eval gate.
        """
        return await self._retriever.retrieve(
            query=request.query, user=user, k=max(request.k, 10)
        )
```

- [ ] **Step 4: Create the `/admin/retrieve` endpoint**

`brain-api/app/api/retrieve.py`:

```python
"""Retrieval-only debug endpoint for the eval harness.

Returns ranked candidate doc_ids WITHOUT generation, so the eval harness can
measure true Recall@k / MRR@k against retrieval (not post-LLM citations).
Gated behind the same debug-auth flag as /query's bypass header.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import get_settings
from app.deps import get_orchestrator
from app.domain.identity import User
from app.domain.query import QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(prefix="/admin", tags=["admin"])


def _debug_user(header: str) -> User:
    parts = header.split(",")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="bad debug header")
    tenant, user_id, *groups = parts
    return User(
        user_id=user_id,
        tenant_id=tenant,
        email=f"{user_id}@debug",
        display_name=user_id,
        group_ids=set(groups),
    )


@router.post("/retrieve")
async def retrieve(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> dict:
    if not get_settings().enable_debug_auth or not x_debug_bypass_auth:
        raise HTTPException(status_code=401, detail="debug auth required")
    user = _debug_user(x_debug_bypass_auth)
    ranked = await orchestrator.retrieve_ranked(body, user=user)
    return {
        "doc_ids": [c.chunk.doc_id for c in ranked],
        "candidates": [
            {"doc_id": c.chunk.doc_id, "chunk_id": c.chunk.chunk_id, "scores": c.raw_scores}
            for c in ranked
        ],
    }
```

- [ ] **Step 5: Mount the retrieve router**

In `brain-api/app/main.py`, add the import and include line. After `from app.api.query import router as query_router` add:

```python
from app.api.retrieve import router as retrieve_router
```

After `app.include_router(query_router)` add:

```python
app.include_router(retrieve_router)
```

- [ ] **Step 6: Run the endpoint test**

Run: `uv run pytest tests/test_admin_retrieve.py -v -m integration`
Expected: PASS.

- [ ] **Step 7: Point the eval harness at `/admin/retrieve`**

In `brain-api/eval/run_eval.py`, find `run_retrieval`. Replace the per-question request block so it calls `/admin/retrieve` and reads `doc_ids` directly (true retrieval, not citations). Replace the body of the `for q in golden:` loop:

```python
        for q in golden:
            t0 = time.perf_counter()
            resp = client.post(
                f"{API}/admin/retrieve",
                json={"query": q["query"], "k": 10},
                headers={"x-debug-bypass-auth": DEBUG_USER},
            )
            latencies.append(time.perf_counter() - t0)
            if resp.status_code != 200:
                failures.append(f"{q['qid']}: HTTP {resp.status_code}")
                recalls.append(0.0)
                rrs.append(0.0)
                continue
            doc_ids = resp.json().get("doc_ids", [])
            rank = _hit_rank(q["expected_doc_ids"], doc_ids)
            recalls.append(1.0 if rank else 0.0)
            rrs.append(1.0 / rank if rank else 0.0)
```

(Leave `_hit_rank`, the metric aggregation, and the `--report`/threshold logic unchanged. `DEBUG_USER` and `API` constants are already defined at module top.)

- [ ] **Step 8: Run the eval to confirm the new path works**

```bash
cd brain-api
uv run uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 4
uv run python eval/run_eval.py --mode retrieval
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: `recall_at_10 >= 0.7`, `mrr_at_10 >= 0.5`, exit code 0. (Now measuring true retrieval order, not citations.)

- [ ] **Step 9: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/orchestrator/kernel.py brain-api/app/api/retrieve.py brain-api/app/main.py brain-api/eval/run_eval.py brain-api/tests/test_admin_retrieve.py
git commit -m "feat: /admin/retrieve raw-retrieval endpoint + eval measures true recall (M2)"
```

---

## Task 3: Capture content rank in the retriever

**Why:** The ranker (Task 9) needs each candidate's content-relevance position to compute the RRF content score. The retriever currently sets `raw_scores={}`. AI Search returns results already ranked by its hybrid+semantic fusion, so the candidate's index in the returned list IS its content rank.

**Files:**
- Modify: `brain-api/app/retrieval/hybrid_retriever.py`
- Create: `brain-api/tests/test_hybrid_retriever_scores.py`

- [ ] **Step 1: Write the failing test**

`brain-api/tests/test_hybrid_retriever_scores.py`:

```python
import pytest

from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_candidates_carry_content_rank() -> None:
    retriever = HybridRetriever(search=AISearchClient(), embedder=AzureOpenAIClient())
    user = User(
        user_id="u-x", tenant_id="t-test", email="x@y", display_name="X",
        group_ids={"t-test:everyone"},
    )
    candidates = await retriever.retrieve(query="PTO policy", user=user, k=10)
    assert len(candidates) > 0
    # every candidate has a content_rank (0-based position) and a content_rrf score
    ranks = [c.raw_scores.get("content_rank") for c in candidates]
    assert ranks == sorted(ranks)  # ascending, gap-free order
    assert ranks[0] == 0
    assert all("content_rrf" in c.raw_scores for c in candidates)
    # RRF score strictly decreases with rank
    assert candidates[0].raw_scores["content_rrf"] > candidates[-1].raw_scores["content_rrf"]
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_hybrid_retriever_scores.py -v -m integration`
Expected: FAIL — `content_rank` not in `raw_scores`.

- [ ] **Step 3: Populate `raw_scores` with content rank + RRF**

Replace `brain-api/app/retrieval/hybrid_retriever.py`:

```python
from __future__ import annotations

from app.domain.identity import User
from app.domain.query import Candidate
from app.generation.azure_openai import AzureOpenAIClient
from app.retrieval.ai_search_client import AISearchClient

# Reciprocal Rank Fusion constant (standard default; dampens top-rank dominance).
_RRF_K = 60


class HybridRetriever:
    """Phase 2a: fan-out to AI Search (hybrid: vector + BM25 + semantic).

    Records each candidate's content rank and RRF contribution in raw_scores so
    the PersonalizedRanker can fuse it with the People-proximity signal. The
    Activity signal (ADX) is added in Phase 2b.
    """

    def __init__(self, *, search: AISearchClient, embedder: AzureOpenAIClient) -> None:
        self._search = search
        self._embedder = embedder

    async def retrieve(self, *, query: str, user: User, k: int = 30) -> list[Candidate]:
        vec = await self._embedder.embed(query)
        chunks = await self._search.hybrid_search(query=query, user=user, vector=vec, top=k)
        return [
            Candidate(
                chunk=c,
                sources_hit={"vector", "bm25", "semantic"},
                raw_scores={
                    "content_rank": float(i),
                    "content_rrf": 1.0 / (_RRF_K + i),
                },
            )
            for i, c in enumerate(chunks)
        ]
```

- [ ] **Step 4: Run test, expect pass**

Run: `uv run pytest tests/test_hybrid_retriever_scores.py -v -m integration`
Expected: PASS.

Also run: `uv run pytest tests/test_hybrid_retriever.py -v -m integration`
Expected: still PASS (the Phase 1 test only checks `sources_hit` and tenant — unaffected).

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/retrieval/hybrid_retriever.py brain-api/tests/test_hybrid_retriever_scores.py
git commit -m "feat: retriever records content_rank + content_rrf in raw_scores"
```

---

## Task 4: Provision Cosmos DB Gremlin

**Why:** The People pillar is a property graph (users, groups, documents + manages/member_of/authored edges). Cosmos DB Gremlin is the spec's store. Provision it idempotently, pre-registering the resource provider (we hit `MissingSubscriptionRegistration` in Phase 1).

**Files:**
- Create: `brain-api/../infra/provision_cosmos.sh`  (i.e. `infra/provision_cosmos.sh`)
- Modify: `brain-api/.env` (append Cosmos settings — gitignored)
- Modify: `brain-api/.env.example` (append Cosmos placeholder keys)
- Modify: `brain-api/app/config.py`

- [ ] **Step 1: Create `infra/provision_cosmos.sh`**

```bash
#!/usr/bin/env bash
# Provision Cosmos DB (Gremlin API) for the People pillar. Idempotent.
# Requires az logged in. Uses the same RG/region as Phase 1.
set -euo pipefail

LOCATION="${LOCATION:-swedencentral}"
RG="${RG:-rg-company-brain-dev}"
NAME_PREFIX="${NAME_PREFIX:-cbrain-$(whoami | tr '[:upper:]' '[:lower:]')}"

COSMOS_NAME="${NAME_PREFIX}-cosmos"
DB_NAME="brain"
GRAPH_NAME="people"

echo "Ensuring Microsoft.DocumentDB provider is registered..."
az provider register --namespace Microsoft.DocumentDB 1>/dev/null
until [ "$(az provider show --namespace Microsoft.DocumentDB --query registrationState -o tsv)" = "Registered" ]; do
  echo "  ...waiting for DocumentDB provider registration"
  sleep 15
done

if ! az cosmosdb show -g "$RG" -n "$COSMOS_NAME" &>/dev/null; then
  echo "Creating Cosmos DB Gremlin account $COSMOS_NAME (serverless)..."
  az cosmosdb create -g "$RG" -n "$COSMOS_NAME" -l "$LOCATION" \
    --capabilities EnableGremlin EnableServerless \
    --default-consistency-level Session 1>/dev/null
fi

if ! az cosmosdb gremlin database show -g "$RG" -a "$COSMOS_NAME" -n "$DB_NAME" &>/dev/null; then
  echo "Creating Gremlin database $DB_NAME..."
  az cosmosdb gremlin database create -g "$RG" -a "$COSMOS_NAME" -n "$DB_NAME" 1>/dev/null
fi

if ! az cosmosdb gremlin graph show -g "$RG" -a "$COSMOS_NAME" -d "$DB_NAME" -n "$GRAPH_NAME" &>/dev/null; then
  echo "Creating Gremlin graph $GRAPH_NAME (partition key /tenant_id)..."
  az cosmosdb gremlin graph create -g "$RG" -a "$COSMOS_NAME" -d "$DB_NAME" -n "$GRAPH_NAME" \
    --partition-key-path "/tenant_id" 1>/dev/null
fi

GREMLIN_KEY=$(az cosmosdb keys list -g "$RG" -n "$COSMOS_NAME" --query primaryMasterKey -o tsv)

cat <<EOF

=== Done. Copy into brain-api/.env ===
COSMOS_GREMLIN_ENDPOINT=wss://${COSMOS_NAME}.gremlin.cosmos.azure.com:443/
COSMOS_GREMLIN_KEY=${GREMLIN_KEY}
COSMOS_GREMLIN_DATABASE=${DB_NAME}
COSMOS_GREMLIN_GRAPH=${GRAPH_NAME}
EOF
```

- [ ] **Step 2: Make executable and run it**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
chmod +x infra/provision_cosmos.sh
LOCATION=swedencentral ./infra/provision_cosmos.sh
```

Expected: ~3-6 min (Cosmos serverless provisions faster than a dedicated cluster). Ends with the `.env` block. **If it fails with a capacity/quota error like Phase 1's AI Search**, retry once; if it persists, STOP and report BLOCKED — the user may need a different region for Cosmos, or we fall back to the Postgres adjacency model (a separate decision).

- [ ] **Step 3: Append the printed values to `brain-api/.env`**

Paste the 4 `COSMOS_GREMLIN_*` lines into `brain-api/.env`. (The `.env` is gitignored; do this with an editor or `cat >>`.)

- [ ] **Step 4: Add Cosmos placeholders to `.env.example`**

Append to `brain-api/.env.example`:

```
# Cosmos DB Gremlin (People pillar) — fill from infra/provision_cosmos.sh output
COSMOS_GREMLIN_ENDPOINT=
COSMOS_GREMLIN_KEY=
COSMOS_GREMLIN_DATABASE=brain
COSMOS_GREMLIN_GRAPH=people
```

- [ ] **Step 5: Add Cosmos + ranker-weight settings to `config.py`**

In `brain-api/app/config.py`, after the Redis block (after `redis_key`) add:

```python
    # Cosmos DB Gremlin (People pillar)
    cosmos_gremlin_endpoint: str | None = None
    cosmos_gremlin_key: str | None = None
    cosmos_gremlin_database: str = "brain"
    cosmos_gremlin_graph: str = "people"
```

After the Brain block (after `admin_api_key`) add:

```python
    # Personalized ranker weights (Phase 2a: content + people only)
    rank_weight_content: float = 0.7
    rank_weight_people: float = 0.3
```

- [ ] **Step 6: Add `gremlinpython` to dependencies**

In `brain-api/pyproject.toml`, add to the `dependencies` array:

```
  "gremlinpython>=3.7",
```

Then:

```bash
cd brain-api && uv sync
```

Expected: resolves and installs gremlinpython.

- [ ] **Step 7: Verify settings load**

```bash
cd brain-api && uv run python -c "from app.config import Settings; import os; print('cosmos endpoint set:', bool(__import__('app.config', fromlist=['get_settings']).get_settings().cosmos_gremlin_endpoint))"
```

Expected: `cosmos endpoint set: True` (reads from `.env`).

- [ ] **Step 8: Commit (scripts + config only — never `.env`)**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add infra/provision_cosmos.sh brain-api/.env.example brain-api/app/config.py brain-api/pyproject.toml brain-api/uv.lock
git commit -m "feat: provision Cosmos DB Gremlin + add cosmos/ranker settings"
```

---

## Task 5: PeopleGraphClient (Cosmos Gremlin wrapper)

**Why:** A thin client over Cosmos Gremlin that the seeder and proximity scorer use. `gremlinpython`'s sync `Client` is simplest; we run submits in a thread to avoid blocking the event loop.

**Files:**
- Create: `brain-api/app/people/__init__.py` (empty)
- Create: `brain-api/app/people/graph_client.py`
- Create: `brain-api/tests/test_people_graph_client.py`

- [ ] **Step 1: Write the failing integration test**

`brain-api/tests/test_people_graph_client.py`:

```python
import pytest

from app.people.graph_client import PeopleGraphClient


@pytest.mark.integration
async def test_upsert_and_query_vertex_round_trip() -> None:
    gc = PeopleGraphClient()
    try:
        await gc.upsert_user(user_id="t5-u1", tenant_id="t-test", email="a@b", display_name="A")
        await gc.upsert_user(user_id="t5-u2", tenant_id="t-test", email="c@d", display_name="C")
        await gc.upsert_edge(
            label="manages", from_id="t5-u1", to_id="t5-u2", tenant_id="t-test"
        )
        count = await gc.submit(
            "g.V().has('user','user_id', uid).out('manages').count()",
            {"uid": "t5-u1"},
        )
        assert count[0] >= 1
    finally:
        await gc.aclose()
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_people_graph_client.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.people'`.

- [ ] **Step 3: Implement `app/people/__init__.py` (empty) and `app/people/graph_client.py`**

`app/people/__init__.py` — empty.

`app/people/graph_client.py`:

```python
"""Thin async wrapper over Cosmos DB Gremlin via gremlinpython.

gremlinpython ships a synchronous Client; we run submits in a worker thread
(asyncio.to_thread) so the event loop is never blocked. Vertices carry a
`tenant_id` property which is also the Cosmos partition key.
"""

from __future__ import annotations

import asyncio
from typing import Any

from gremlin_python.driver import client, serializer

from app.config import get_settings


class PeopleGraphClient:
    def __init__(self) -> None:
        s = get_settings()
        if not s.cosmos_gremlin_endpoint or not s.cosmos_gremlin_key:
            raise RuntimeError("Cosmos Gremlin settings are not configured")
        self._client = client.Client(
            s.cosmos_gremlin_endpoint,
            "g",
            username=f"/dbs/{s.cosmos_gremlin_database}/colls/{s.cosmos_gremlin_graph}",
            password=s.cosmos_gremlin_key,
            message_serializer=serializer.GraphSONSerializersV2d0(),
        )

    async def submit(self, query: str, bindings: dict[str, Any] | None = None) -> list[Any]:
        def _run() -> list[Any]:
            return self._client.submit(query, bindings or {}).all().result()

        return await asyncio.to_thread(_run)

    async def upsert_user(
        self, *, user_id: str, tenant_id: str, email: str, display_name: str
    ) -> None:
        # Cosmos Gremlin upsert idiom: coalesce(existing, addV).
        await self.submit(
            "g.V().has('user','user_id', uid).has('tenant_id', tid).fold()"
            ".coalesce(unfold(),"
            " addV('user').property('user_id', uid).property('tenant_id', tid))"
            ".property('email', em).property('display_name', dn)",
            {"uid": user_id, "tid": tenant_id, "em": email, "dn": display_name},
        )

    async def upsert_group(self, *, group_id: str, tenant_id: str, name: str) -> None:
        await self.submit(
            "g.V().has('group','group_id', gid).has('tenant_id', tid).fold()"
            ".coalesce(unfold(),"
            " addV('group').property('group_id', gid).property('tenant_id', tid))"
            ".property('name', nm)",
            {"gid": group_id, "tid": tenant_id, "nm": name},
        )

    async def upsert_document(self, *, doc_id: str, tenant_id: str) -> None:
        await self.submit(
            "g.V().has('document','doc_id', did).has('tenant_id', tid).fold()"
            ".coalesce(unfold(),"
            " addV('document').property('doc_id', did).property('tenant_id', tid))",
            {"did": doc_id, "tid": tenant_id},
        )

    async def upsert_edge(
        self, *, label: str, from_id: str, to_id: str, tenant_id: str
    ) -> None:
        # Match by the *_id property on either end (user_id / group_id / doc_id).
        await self.submit(
            "g.V().has('tenant_id', tid).or(has('user_id', a), has('group_id', a),"
            " has('doc_id', a)).as('src')"
            ".V().has('tenant_id', tid).or(has('user_id', b), has('group_id', b),"
            " has('doc_id', b)).as('dst')"
            ".coalesce("
            "  inE(lbl).where(outV().as('src')),"
            "  addE(lbl).from('src').to('dst'))",
            {"tid": tenant_id, "a": from_id, "b": to_id, "lbl": label},
        )

    async def aclose(self) -> None:
        def _close() -> None:
            self._client.close()

        await asyncio.to_thread(_close)
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_people_graph_client.py -v -m integration`
Expected: PASS. (First Cosmos call may take a few seconds for the websocket handshake.)

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/people/__init__.py brain-api/app/people/graph_client.py brain-api/tests/test_people_graph_client.py
git commit -m "feat: PeopleGraphClient (Cosmos Gremlin upsert vertices/edges + submit)"
```

---

## Task 6: People seeder (Microsoft Graph → Cosmos) + /admin/seed-people

**Why:** Populate the People pillar with the real org graph: users, groups, group memberships, and manager edges from Entra via Microsoft Graph (app-only token, `Directory.Read.All` already consented). Also seed `authored` edges from indexed documents whose `author_id` is set.

**Files:**
- Create: `brain-api/app/people/seeder.py`
- Modify: `brain-api/app/api/admin.py`
- Create: `brain-api/tests/test_people_seeder.py`

- [ ] **Step 1: Write the failing integration test**

`brain-api/tests/test_people_seeder.py`:

```python
import pytest

from app.people.graph_client import PeopleGraphClient
from app.people.seeder import PeopleSeeder


@pytest.mark.integration
async def test_seed_from_graph_creates_users() -> None:
    gc = PeopleGraphClient()
    seeder = PeopleSeeder(graph=gc, tenant_id="t-test")
    try:
        result = await seeder.seed_users(limit=5)
        assert result["users"] >= 1
        # at least one user vertex exists in the t-test partition
        count = await gc.submit(
            "g.V().has('user').has('tenant_id', tid).count()", {"tid": "t-test"}
        )
        assert count[0] >= 1
    finally:
        await gc.aclose()
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_people_seeder.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.people.seeder'`.

- [ ] **Step 3: Implement `app/people/seeder.py`**

```python
"""Seed the People pillar from Microsoft Graph (app-only token).

Reads users, groups, group memberships, and manager relationships from Entra
and writes them into the Cosmos Gremlin graph under a fixed tenant_id partition.
The Graph tenant's real directory is materialized; tenant_id is the Brain's
logical tenant (Phase 2a uses the single demo tenant 't-test').
"""

from __future__ import annotations

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.people.graph_client import PeopleGraphClient

_GRAPH = "https://graph.microsoft.com/v1.0"


class PeopleSeeder:
    def __init__(self, *, graph: PeopleGraphClient, tenant_id: str) -> None:
        self._graph = graph
        self._tenant_id = tenant_id

    async def _token(self) -> str:
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token("https://graph.microsoft.com/.default")
            return tok.token
        finally:
            await cred.close()

    async def seed_users(self, *, limit: int = 100) -> dict[str, int]:
        token = await self._token()
        users = 0
        managers = 0
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.get(
                f"{_GRAPH}/users",
                params={"$top": str(min(limit, 999)), "$select": "id,displayName,mail,userPrincipalName"},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            people = r.json().get("value", [])[:limit]
            for p in people:
                await self._graph.upsert_user(
                    user_id=p["id"],
                    tenant_id=self._tenant_id,
                    email=p.get("mail") or p.get("userPrincipalName") or "",
                    display_name=p.get("displayName") or "",
                )
                users += 1
            # manager edges (best-effort; a user may have no manager)
            for p in people:
                mr = await http.get(
                    f"{_GRAPH}/users/{p['id']}/manager",
                    params={"$select": "id"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if mr.status_code == 200:
                    mgr_id = mr.json().get("id")
                    if mgr_id:
                        await self._graph.upsert_edge(
                            label="manages",
                            from_id=mgr_id,
                            to_id=p["id"],
                            tenant_id=self._tenant_id,
                        )
                        managers += 1
        return {"users": users, "manager_edges": managers}

    async def seed_groups(self, *, limit: int = 100) -> dict[str, int]:
        token = await self._token()
        groups = 0
        memberships = 0
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.get(
                f"{_GRAPH}/groups",
                params={"$top": str(min(limit, 999)), "$select": "id,displayName"},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            gl = r.json().get("value", [])[:limit]
            for g in gl:
                await self._graph.upsert_group(
                    group_id=g["id"],
                    tenant_id=self._tenant_id,
                    name=g.get("displayName") or "",
                )
                groups += 1
                mr = await http.get(
                    f"{_GRAPH}/groups/{g['id']}/members",
                    params={"$select": "id", "$top": "100"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if mr.status_code == 200:
                    for m in mr.json().get("value", []):
                        await self._graph.upsert_edge(
                            label="member_of",
                            from_id=m["id"],
                            to_id=g["id"],
                            tenant_id=self._tenant_id,
                        )
                        memberships += 1
        return {"groups": groups, "membership_edges": memberships}
```

- [ ] **Step 4: Add `POST /admin/seed-people` to `admin.py`**

In `brain-api/app/api/admin.py`, add the endpoint (keep the existing `require_admin_key` dependency and `/ingest` route). Add these imports at the top:

```python
from app.people.graph_client import PeopleGraphClient
from app.people.seeder import PeopleSeeder
from app.config import get_settings
```

Add the route (after the ingest route, inside the same router that has `require_admin_key`):

```python
@router.post("/seed-people")
async def seed_people(users_limit: int = 50, groups_limit: int = 50) -> dict:
    tenant = get_settings().brain_tenant_id
    gc = PeopleGraphClient()
    try:
        seeder = PeopleSeeder(graph=gc, tenant_id=tenant)
        u = await seeder.seed_users(limit=users_limit)
        g = await seeder.seed_groups(limit=groups_limit)
        return {"tenant_id": tenant, **u, **g}
    finally:
        await gc.aclose()
```

(If `admin.py`'s router applies `require_admin_key` at the router level via `dependencies=[...]`, this route inherits it automatically. Confirm by reading the file; if the dependency is per-route, add `dependencies=[Depends(require_admin_key)]` to this route too.)

- [ ] **Step 5: Run the seeder test**

Run: `uv run pytest tests/test_people_seeder.py -v -m integration`
Expected: PASS. Seeds up to 5 users from the real Entra tenant into Cosmos.

- [ ] **Step 6: Seed the demo tenant for real**

```bash
cd brain-api
uv run uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 4
curl -s -X POST "http://localhost:8000/admin/seed-people?users_limit=50&groups_limit=50" \
  -H "x-admin-key: dev-admin-key-local" | python -m json.tool
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: JSON with `users >= 1`, `groups >= 0`, edge counts. (The hackathon tenant may have few users/groups — that's fine; the persona test in Task 11 seeds its own synthetic vertices.)

- [ ] **Step 7: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/people/seeder.py brain-api/app/api/admin.py brain-api/tests/test_people_seeder.py
git commit -m "feat: PeopleSeeder (MS Graph users/groups/managers → Cosmos) + /admin/seed-people"
```

---

## Task 7: PeopleProximity scorer

**Why:** Given a user and a set of candidate `doc_id`s, return a `[0,1]` proximity score per doc reflecting how close the user is (≤2 hops via manages/member_of/authored) to each document's author. This is the People ranking signal.

**Files:**
- Create: `brain-api/app/people/proximity.py`
- Create: `brain-api/tests/test_people_proximity.py`

- [ ] **Step 1: Write the failing integration test**

`brain-api/tests/test_people_proximity.py`:

```python
import pytest

from app.domain.identity import User
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity


@pytest.mark.integration
async def test_proximity_higher_for_authored_by_self() -> None:
    gc = PeopleGraphClient()
    try:
        # u-prox authored doc-near; nobody u-prox knows authored doc-far
        await gc.upsert_user(user_id="u-prox", tenant_id="t-test", email="p@x", display_name="P")
        await gc.upsert_document(doc_id="doc-near", tenant_id="t-test")
        await gc.upsert_document(doc_id="doc-far", tenant_id="t-test")
        await gc.upsert_user(user_id="u-stranger", tenant_id="t-test", email="s@x", display_name="S")
        await gc.upsert_edge(label="authored", from_id="u-prox", to_id="doc-near", tenant_id="t-test")
        await gc.upsert_edge(label="authored", from_id="u-stranger", to_id="doc-far", tenant_id="t-test")

        user = User(
            user_id="u-prox", tenant_id="t-test", email="p@x", display_name="P", group_ids=set()
        )
        scores = await PeopleProximity(graph=gc).score(user=user, doc_ids=["doc-near", "doc-far"])
        assert scores["doc-near"] > scores["doc-far"]
        assert 0.0 <= scores["doc-far"] <= 1.0
        assert 0.0 <= scores["doc-near"] <= 1.0
    finally:
        await gc.aclose()
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_people_proximity.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.people.proximity'`.

- [ ] **Step 3: Implement `app/people/proximity.py`**

```python
"""People-proximity ranking signal.

For each candidate doc, count how many of the document's authors are reachable
from the user within 2 hops over {manages, member_of, authored} (treated
undirected). Normalize counts to [0,1] across the candidate set. A user who
authored the doc, or whose manager/teammate authored it, scores high.
"""

from __future__ import annotations

from app.domain.identity import User
from app.people.graph_client import PeopleGraphClient


class PeopleProximity:
    def __init__(self, *, graph: PeopleGraphClient) -> None:
        self._graph = graph

    async def score(self, *, user: User, doc_ids: list[str]) -> dict[str, float]:
        if not doc_ids:
            return {}
        # Reachability count per doc: authors of each doc within 2 undirected hops.
        raw = await self._graph.submit(
            "g.V().has('user','user_id', uid).has('tenant_id', tid)"
            ".repeat(both('manages','member_of','collaborates_with').simplePath()).times(2)"
            ".dedup().in('authored').has('doc_id', within(dids))"
            ".groupCount().by('doc_id')",
            {"uid": user.user_id, "tid": user.tenant_id, "dids": doc_ids},
        )
        # groupCount returns a single map; default to empty.
        counts: dict[str, float] = {}
        if raw and isinstance(raw[0], dict):
            counts = {k: float(v) for k, v in raw[0].items()}
        # Self-authored docs: direct authored edge counts strongly too.
        self_authored = await self._graph.submit(
            "g.V().has('user','user_id', uid).has('tenant_id', tid)"
            ".out('authored').has('doc_id', within(dids)).values('doc_id')",
            {"uid": user.user_id, "tid": user.tenant_id, "dids": doc_ids},
        )
        for did in self_authored or []:
            counts[did] = counts.get(did, 0.0) + 2.0  # self-authorship weighted

        if not counts:
            return {d: 0.0 for d in doc_ids}
        hi = max(counts.values())
        return {d: (counts.get(d, 0.0) / hi if hi > 0 else 0.0) for d in doc_ids}
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_people_proximity.py -v -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/people/proximity.py brain-api/tests/test_people_proximity.py
git commit -m "feat: PeopleProximity scorer (2-hop org-graph reachability, normalized)"
```

---

## Task 8: ACL Store + query-time re-check (double-enforcement, second half)

**Why:** Phase 1 only filters by index-time ACL. The spec's double-enforcement re-checks each candidate against the *current* ACL state at query time, so a revocation propagates without re-indexing. Store the per-doc allowed-principal set in Redis at ingest time; re-check candidates against it before generation. Fail-closed on store error; fall back to the chunk's index-time ACL on a key miss.

**Files:**
- Create: `brain-api/app/acl/store.py`
- Modify: `brain-api/app/acl/enforcement.py`
- Modify: `brain-api/app/ingest/pipeline.py`
- Create: `brain-api/tests/test_acl_store.py`

- [ ] **Step 1: Write the failing tests (1 unit + 1 integration)**

`brain-api/tests/test_acl_store.py`:

```python
import pytest

from app.acl.store import ACLStore
from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate
from datetime import UTC, datetime


def _candidate(doc_id: str, acl: list[str]) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0", doc_id=doc_id, tenant_id="t-test",
            source="uploaded", source_url=f"local://{doc_id}", title="T",
            content="c", content_vector=[], acl_principals=acl, author_id=None,
            entities=[], created_at=now, modified_at=now, chunk_index=0,
        ),
        sources_hit={"vector"},
    )


class _FakeStore(ACLStore):
    """Override the Redis read with an in-memory map for the unit test."""

    def __init__(self, mapping: dict[str, set[str] | None]) -> None:
        self._mapping = mapping

    async def doc_principals(self, *, tenant_id: str, doc_id: str) -> set[str] | None:
        return self._mapping.get(doc_id)


def test_recheck_keeps_allowed_drops_revoked() -> None:
    import asyncio

    user = User(user_id="u1", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"g-sales"})
    store = _FakeStore({
        "doc-allow": {"g-sales"},      # live ACL still allows the group
        "doc-revoked": {"g-other"},    # live ACL no longer includes the user
    })
    cands = [_candidate("doc-allow", ["g-sales"]), _candidate("doc-revoked", ["g-sales"])]
    kept = asyncio.run(store.recheck(candidates=cands, user=user))
    kept_ids = {c.chunk.doc_id for c in kept}
    assert "doc-allow" in kept_ids
    assert "doc-revoked" not in kept_ids


def test_recheck_falls_back_to_index_acl_on_key_miss() -> None:
    import asyncio

    user = User(user_id="u1", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"g-sales"})
    store = _FakeStore({})  # no live ACL for any doc -> fall back to chunk.acl_principals
    cands = [_candidate("doc-x", ["g-sales"]), _candidate("doc-y", ["g-other"])]
    kept = {c.chunk.doc_id for c in asyncio.run(store.recheck(candidates=cands, user=user))}
    assert kept == {"doc-x"}  # index-time ACL allows g-sales on doc-x only


@pytest.mark.integration
async def test_acl_store_round_trip() -> None:
    store = ACLStore()
    try:
        await store.set_doc_principals(tenant_id="t-test", doc_id="doc-rt", principals=["g-sales", "u9"])
        got = await store.doc_principals(tenant_id="t-test", doc_id="doc-rt")
        assert got == {"g-sales", "u9"}
    finally:
        await store.aclose()
```

- [ ] **Step 2: Run tests, expect failure**

Run: `uv run pytest tests/test_acl_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.acl.store'`.

- [ ] **Step 3: Implement `app/acl/store.py`**

```python
"""Query-time ACL store: the current allowed-principal set per document.

Written at ingest time; read during the orchestrator's query-time re-check.
Fail-closed: if Redis errors, treat as "cannot verify" and drop the candidate.
On a key MISS (no entry yet), the caller falls back to the chunk's index-time
acl_principals (handled in recheck()).
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.identity import User
from app.domain.query import Candidate

logger = logging.getLogger(__name__)


class ACLStoreError(Exception):
    """Raised when the ACL store cannot be reached (forces fail-closed)."""


def _doc_key(tenant_id: str, doc_id: str) -> str:
    return f"acl:doc:{tenant_id}:{doc_id}"


class ACLStore:
    def __init__(self) -> None:
        s = get_settings()
        self._r = redis.Redis(
            host=s.azure_redis_host,
            port=s.azure_redis_port,
            ssl=s.azure_redis_ssl,
            password=s.redis_key,
            decode_responses=True,
        )

    async def aclose(self) -> None:
        await self._r.aclose()

    async def set_doc_principals(
        self, *, tenant_id: str, doc_id: str, principals: list[str], ttl_seconds: int = 900
    ) -> None:
        try:
            await self._r.set(_doc_key(tenant_id, doc_id), json.dumps(sorted(principals)), ex=ttl_seconds)
        except (RedisError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning("ACLStore write failed (doc=%s): %s", doc_id, e)

    async def doc_principals(self, *, tenant_id: str, doc_id: str) -> set[str] | None:
        """Return the live allowed-principal set, or None on key miss.

        Raises ACLStoreError if Redis is unreachable (caller fails closed).
        """
        try:
            v = await self._r.get(_doc_key(tenant_id, doc_id))
        except (RedisError, ConnectionError, TimeoutError, OSError) as e:
            raise ACLStoreError(str(e)) from e
        if v is None:
            return None
        return set(json.loads(v))

    async def recheck(self, *, candidates: list[Candidate], user: User) -> list[Candidate]:
        principals = user.principals()
        kept: list[Candidate] = []
        for c in candidates:
            try:
                live = await self.doc_principals(tenant_id=user.tenant_id, doc_id=c.chunk.doc_id)
            except ACLStoreError:
                # fail-closed: cannot verify -> drop
                logger.warning("ACL store unreachable; dropping doc %s (fail-closed)", c.chunk.doc_id)
                continue
            allowed = live if live is not None else set(c.chunk.acl_principals)
            if principals & allowed:
                kept.append(c)
        return kept
```

- [ ] **Step 4: Write the doc ACL at ingest time**

In `brain-api/app/ingest/pipeline.py`, extend `IngestPipeline` to accept an optional `acl_store` and write the doc's principals after upsert. Replace the file:

```python
from __future__ import annotations

from dataclasses import dataclass

from app.acl.store import ACLStore
from app.domain.chunk import Chunk, SourceDoc
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.chunker import chunk_markdown
from app.retrieval.ai_search_client import AISearchClient


@dataclass
class IngestResult:
    doc_id: str
    chunks_indexed: int


class IngestPipeline:
    def __init__(
        self,
        *,
        embedder: AzureOpenAIClient,
        search: AISearchClient,
        acl_store: ACLStore | None = None,
    ) -> None:
        self._embedder = embedder
        self._search = search
        self._acl_store = acl_store

    async def process(self, doc: SourceDoc) -> IngestResult:
        pieces = chunk_markdown(doc.body, max_tokens=600, overlap_tokens=75)
        if not pieces:
            return IngestResult(doc_id=doc.doc_id, chunks_indexed=0)
        vectors = await self._embedder.embed_batch([p.content for p in pieces])
        chunks = [
            Chunk(
                chunk_id=f"{doc.doc_id}#chunk-{p.chunk_index}",
                doc_id=doc.doc_id,
                tenant_id=doc.tenant_id,
                source=doc.source,
                source_url=doc.source_url,
                title=doc.title,
                content=p.content,
                content_vector=v,
                acl_principals=doc.acl_principals,
                author_id=doc.author_id,
                entities=[],
                created_at=doc.created_at,
                modified_at=doc.modified_at,
                chunk_index=p.chunk_index,
            )
            for p, v in zip(pieces, vectors, strict=True)
        ]
        await self._search.upsert_chunks(chunks)
        if self._acl_store is not None:
            await self._acl_store.set_doc_principals(
                tenant_id=doc.tenant_id, doc_id=doc.doc_id, principals=doc.acl_principals
            )
        return IngestResult(doc_id=doc.doc_id, chunks_indexed=len(chunks))
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_acl_store.py -v`
Expected: 2 unit pass; 1 integration passes (real Redis round-trip).

Run: `uv run pytest tests/test_ingest_pipeline.py -v -m integration`
Expected: still PASS (the `acl_store` param defaults to None, so the Phase 1 test's pipeline is unaffected).

- [ ] **Step 6: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/acl/store.py brain-api/app/ingest/pipeline.py brain-api/tests/test_acl_store.py
git commit -m "feat: ACLStore + query-time re-check (double-enforcement) + ingest writes doc ACL"
```

---

## Task 9: PersonalizedRanker

**Why:** Fuse Content (RRF from retrieval) and People (proximity) into one ordering, config-weighted. This is where two users diverge: same content candidates, different proximity → different order.

**Files:**
- Modify: `brain-api/app/domain/query.py`
- Create: `brain-api/app/ranking/__init__.py` (empty)
- Create: `brain-api/app/ranking/personalized_ranker.py`
- Create: `brain-api/tests/test_personalized_ranker.py`

- [ ] **Step 1: Add `RankedResult` to `domain/query.py`**

In `brain-api/app/domain/query.py`, after the `Candidate` class add:

```python
class RankedResult(BaseModel):
    candidate: Candidate
    final_score: float
    signal_breakdown: dict[str, float] = Field(default_factory=dict)
    rank: int
```

- [ ] **Step 2: Write the failing unit test**

`brain-api/tests/test_personalized_ranker.py`:

```python
from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.query import Candidate
from app.ranking.personalized_ranker import PersonalizedRanker


def _cand(doc_id: str, content_rank: int) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0", doc_id=doc_id, tenant_id="t-test",
            source="uploaded", source_url=f"local://{doc_id}", title="T",
            content="c", content_vector=[], acl_principals=["t-test:everyone"],
            author_id=None, entities=[], created_at=now, modified_at=now, chunk_index=0,
        ),
        sources_hit={"vector"},
        raw_scores={"content_rank": float(content_rank), "content_rrf": 1.0 / (60 + content_rank)},
    )


def test_people_signal_reorders_ties() -> None:
    # Two docs equal on content; doc-b has higher people proximity -> ranks first.
    cands = [_cand("doc-a", 0), _cand("doc-b", 1)]
    proximity = {"doc-a": 0.0, "doc-b": 1.0}
    ranker = PersonalizedRanker(weight_content=0.5, weight_people=0.5)
    ranked = ranker.rank(candidates=cands, proximity=proximity)
    assert ranked[0].candidate.chunk.doc_id == "doc-b"
    assert ranked[0].rank == 0
    assert ranked[1].rank == 1
    # breakdown carries both signals
    assert "content" in ranked[0].signal_breakdown
    assert "people" in ranked[0].signal_breakdown


def test_pure_content_when_people_weight_zero() -> None:
    cands = [_cand("doc-a", 0), _cand("doc-b", 1)]
    proximity = {"doc-a": 0.0, "doc-b": 1.0}
    ranker = PersonalizedRanker(weight_content=1.0, weight_people=0.0)
    ranked = ranker.rank(candidates=cands, proximity=proximity)
    # content rank 0 wins despite doc-b's proximity
    assert ranked[0].candidate.chunk.doc_id == "doc-a"


def test_empty_candidates_returns_empty() -> None:
    ranker = PersonalizedRanker(weight_content=0.7, weight_people=0.3)
    assert ranker.rank(candidates=[], proximity={}) == []
```

- [ ] **Step 3: Run tests, expect failure**

Run: `uv run pytest tests/test_personalized_ranker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ranking'`.

- [ ] **Step 4: Implement `app/ranking/__init__.py` (empty) and `app/ranking/personalized_ranker.py`**

`app/ranking/__init__.py` — empty.

`app/ranking/personalized_ranker.py`:

```python
"""Personalized multi-signal ranker (Phase 2a: Content + People).

final = w_content * normalize(content_rrf) + w_people * proximity

Content uses the retriever's RRF score (already rank-derived); proximity is the
People-pillar signal in [0,1]. Activity (ADX) is added as a third weighted term
in Phase 2b. Weights are injected (sourced from Settings by the orchestrator).
"""

from __future__ import annotations

from app.domain.query import Candidate, RankedResult


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    hi = max(values.values())
    if hi <= 0:
        return {k: 0.0 for k in values}
    return {k: v / hi for k, v in values.items()}


class PersonalizedRanker:
    def __init__(self, *, weight_content: float, weight_people: float) -> None:
        self._wc = weight_content
        self._wp = weight_people

    def rank(
        self, *, candidates: list[Candidate], proximity: dict[str, float]
    ) -> list[RankedResult]:
        if not candidates:
            return []
        content_norm = _normalize(
            {c.chunk.chunk_id: c.raw_scores.get("content_rrf", 0.0) for c in candidates}
        )
        scored: list[RankedResult] = []
        for c in candidates:
            content = content_norm.get(c.chunk.chunk_id, 0.0)
            people = proximity.get(c.chunk.doc_id, 0.0)
            final = self._wc * content + self._wp * people
            scored.append(
                RankedResult(
                    candidate=c,
                    final_score=final,
                    signal_breakdown={"content": content, "people": people},
                    rank=0,
                )
            )
        scored.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(scored):
            r.rank = i
        return scored
```

- [ ] **Step 5: Run tests, expect pass**

Run: `uv run pytest tests/test_personalized_ranker.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/domain/query.py brain-api/app/ranking brain-api/tests/test_personalized_ranker.py
git commit -m "feat: PersonalizedRanker (RRF content + people proximity, config-weighted)"
```

---

## Task 10: Wire proximity + ACL re-check + ranker into the orchestrator

**Why:** Connect the new pieces. The orchestrator now: retrieve → ACL re-check → people proximity → rank → generate (top-5 of the ranked order). `retrieve_ranked` returns the ranked candidates so `/admin/retrieve` and the eval reflect personalization.

**Files:**
- Modify: `brain-api/app/orchestrator/kernel.py`
- Modify: `brain-api/app/main.py` (construct ACLStore, PeopleGraphClient, PeopleProximity, PersonalizedRanker in lifespan; pass to orchestrator)
- Modify: `brain-api/app/deps.py` (no change if orchestrator is already read from app.state — verify)
- Create/Modify test: `brain-api/tests/test_orchestrator.py` (extend — the Phase 1 tests still must pass)

- [ ] **Step 1: Rewrite the orchestrator to fuse the signals**

Replace `brain-api/app/orchestrator/kernel.py`:

```python
from __future__ import annotations

import hashlib
import uuid

from app.acl.store import ACLStore
from app.cache.redis_cache import RedisCache
from app.domain.identity import User
from app.domain.query import Answer, Candidate, QueryRequest, RankedResult
from app.generation.azure_openai import AzureOpenAIClient
from app.generation.prompts import build_grounded_messages, parse_citations_from_answer
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.hybrid_retriever import HybridRetriever


def _cache_key(user: User, query: str) -> str:
    principals_blob = "|".join(sorted(user.principals()))
    normalized = " ".join(query.lower().split())
    h = hashlib.sha256(f"{principals_blob}::{normalized}".encode()).hexdigest()
    return f"cache:answer:{user.tenant_id}:{h}"


class SemanticKernelOrchestrator:
    """Phase 2a: cache -> retrieve -> ACL re-check -> proximity -> rank -> answer.

    Plan step + Live Fetch are still stubbed (Phase 3). Activity signal is Phase 2b.
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        llm: AzureOpenAIClient,
        cache: RedisCache,
        acl_store: ACLStore,
        proximity: PeopleProximity,
        ranker: PersonalizedRanker,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._cache = cache
        self._acl_store = acl_store
        self._proximity = proximity
        self._ranker = ranker

    async def aclose(self) -> None:
        return None

    async def retrieve_ranked(self, request: QueryRequest, *, user: User) -> list[Candidate]:
        candidates = await self._retriever.retrieve(
            query=request.query, user=user, k=max(request.k, 10)
        )
        if not candidates:
            return []
        # Query-time ACL re-check (double-enforcement, fail-closed on store error).
        candidates = await self._acl_store.recheck(candidates=candidates, user=user)
        if not candidates:
            return []
        # People proximity over the surviving candidate docs.
        proximity = await self._proximity.score(
            user=user, doc_ids=[c.chunk.doc_id for c in candidates]
        )
        ranked: list[RankedResult] = self._ranker.rank(
            candidates=candidates, proximity=proximity
        )
        return [r.candidate for r in ranked]

    async def answer(self, request: QueryRequest, *, user: User) -> Answer:
        query_id = str(uuid.uuid4())

        key = _cache_key(user, request.query)
        cached = await self._cache.get_json(key)
        if cached:
            return Answer.model_validate({**cached, "query_id": query_id})

        candidates = await self.retrieve_ranked(request, user=user)
        if not candidates:
            return Answer(
                text="I don't have information about that.",
                citations=[],
                query_id=query_id,
            )

        messages = build_grounded_messages(query=request.query, candidates=candidates[:5])
        text = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=800)
        citations = parse_citations_from_answer(text, candidates[:5])

        answer = Answer(text=text, citations=citations, query_id=query_id)

        cache_blob = answer.model_dump()
        cache_blob.pop("query_id", None)
        await self._cache.set_json(key, cache_blob, ttl_seconds=600)

        return answer
```

- [ ] **Step 2: Construct the new collaborators in `lifespan`**

In `brain-api/app/main.py`, add imports:

```python
from app.acl.store import ACLStore
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
```

In `lifespan`, after `app.state.cache = RedisCache()` and before constructing the orchestrator, add:

```python
    app.state.acl_store = ACLStore()
    app.state.people_graph = PeopleGraphClient()
    app.state.proximity = PeopleProximity(graph=app.state.people_graph)
    app.state.ranker = PersonalizedRanker(
        weight_content=get_settings().rank_weight_content,
        weight_people=get_settings().rank_weight_people,
    )
```

Replace the orchestrator construction with:

```python
    app.state.orchestrator = SemanticKernelOrchestrator(
        retriever=app.state.retriever,
        llm=app.state.embedder,
        cache=app.state.cache,
        acl_store=app.state.acl_store,
        proximity=app.state.proximity,
        ranker=app.state.ranker,
    )
```

In the shutdown `finally` block, add (before `await app.state.cache.aclose()`):

```python
        await app.state.acl_store.aclose()
        await app.state.people_graph.aclose()
```

- [ ] **Step 3: Update the orchestrator test for the new constructor**

The Phase 1 `tests/test_orchestrator.py` constructs `SemanticKernelOrchestrator(retriever=..., llm=..., cache=...)`. It now needs the three new args. Replace `tests/test_orchestrator.py`:

```python
import pytest

from app.acl.store import ACLStore
from app.cache.redis_cache import RedisCache
from app.domain.identity import User
from app.domain.query import QueryRequest
from app.generation.azure_openai import AzureOpenAIClient
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


def _build() -> tuple[SemanticKernelOrchestrator, list]:
    embedder = AzureOpenAIClient()
    search = AISearchClient()
    cache = RedisCache()
    acl_store = ACLStore()
    graph = PeopleGraphClient()
    closeables = [embedder, search, cache, acl_store, graph]
    orch = SemanticKernelOrchestrator(
        retriever=HybridRetriever(search=search, embedder=embedder),
        llm=embedder,
        cache=cache,
        acl_store=acl_store,
        proximity=PeopleProximity(graph=graph),
        ranker=PersonalizedRanker(weight_content=0.7, weight_people=0.3),
    )
    return orch, closeables


async def _aclose_all(closeables: list) -> None:
    for c in closeables:
        await c.aclose()


@pytest.mark.integration
async def test_orchestrator_returns_answer_with_citations() -> None:
    orch, closeables = _build()
    try:
        user = User(user_id="u-orch", tenant_id="t-test", email="u@x",
                    display_name="U", group_ids={"t-test:everyone"})
        answer = await orch.answer(QueryRequest(query="what is the PTO policy?"), user=user)
        assert isinstance(answer.text, str) and len(answer.text) > 0
        assert any("pto" in c.doc_id.lower() for c in answer.citations)
    finally:
        await _aclose_all(closeables)


@pytest.mark.integration
async def test_orchestrator_refuses_out_of_corpus() -> None:
    orch, closeables = _build()
    try:
        user = User(user_id="u-orch", tenant_id="t-test", email="u@x",
                    display_name="U", group_ids={"t-test:everyone"})
        answer = await orch.answer(
            QueryRequest(query="what is the recipe for chocolate chip cookies?"), user=user
        )
        assert "don't have" in answer.text.lower() or "do not have" in answer.text.lower()
    finally:
        await _aclose_all(closeables)
```

**Important ACL-store dependency:** these tests re-check ACLs against the ACL store. The Phase 1 corpus was ingested BEFORE the ACL store existed, so `acl:doc:*` keys don't exist for those docs → `recheck` falls back to the chunk's index-time `acl_principals` (which include `t-test:everyone`), so the PTO doc survives. Good — the fallback path is exercised. No re-ingest needed for this test.

- [ ] **Step 4: Run the orchestrator tests + the retrieve endpoint + full suite**

Run: `uv run pytest tests/test_orchestrator.py -v -m integration`
Expected: 2 passed.

Run: `uv run pytest tests/test_admin_retrieve.py tests/test_query_e2e.py -v -m integration`
Expected: pass (now flowing through ACL re-check + proximity + ranker).

Run: `uv run pytest -m "not integration"`
Expected: all unit pass.

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/orchestrator/kernel.py brain-api/app/main.py brain-api/tests/test_orchestrator.py
git commit -m "feat: orchestrator fuses ACL re-check + people proximity + ranker"
```

---

## Task 11: Two-persona ranking acceptance test (the headline demo)

**Why:** Prove the spec's acceptance criterion #2: the same query returns a different ranking for two users with different org positions. Seed a tiny synthetic org + two docs, then assert each persona ranks "their" doc higher.

**Files:**
- Create: `brain-api/eval/personas.json`
- Create: `brain-api/tests/test_persona_ranking.py`

- [ ] **Step 1: Create `eval/personas.json`**

```json
{
  "sales_rep": {
    "user_id": "p-sales", "tenant_id": "t-test",
    "email": "sales@contoso.com", "display_name": "Sales Persona",
    "group_ids": ["t-test:everyone", "g-sales"]
  },
  "engineer": {
    "user_id": "p-eng", "tenant_id": "t-test",
    "email": "eng@contoso.com", "display_name": "Eng Persona",
    "group_ids": ["t-test:everyone", "g-eng"]
  }
}
```

- [ ] **Step 2: Write the failing integration test**

`brain-api/tests/test_persona_ranking.py`:

```python
from datetime import UTC, datetime

import pytest

from app.domain.chunk import SourceDoc
from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_same_query_different_ranking_per_persona() -> None:
    embedder = AzureOpenAIClient()
    search = AISearchClient()
    graph = PeopleGraphClient()
    retriever = HybridRetriever(search=search, embedder=embedder)
    proximity = PeopleProximity(graph=graph)
    ranker = PersonalizedRanker(weight_content=0.4, weight_people=0.6)
    now = datetime.now(UTC)

    try:
        # Two "planning priorities" docs, one authored by each persona.
        for did, author, body in [
            ("up:persona-sales-plan", "p-sales",
             "# Sales Planning Priorities\n\nOur planning priorities focus on enterprise pipeline and upsell."),
            ("up:persona-eng-plan", "p-eng",
             "# Engineering Planning Priorities\n\nOur planning priorities focus on reliability and platform scale."),
        ]:
            from app.ingest.pipeline import IngestPipeline
            pipe = IngestPipeline(embedder=embedder, search=search)
            await pipe.process(SourceDoc(
                doc_id=did, tenant_id="t-test", source="uploaded",
                source_url=f"local://{did}", title=did, body=body,
                author_id=author, acl_principals=["t-test:everyone"],
                created_at=now, modified_at=now, mime="text/markdown",
            ))
            await graph.upsert_user(user_id=author, tenant_id="t-test",
                                    email=f"{author}@x", display_name=author)
            await graph.upsert_document(doc_id=did, tenant_id="t-test")
            await graph.upsert_edge(label="authored", from_id=author, to_id=did, tenant_id="t-test")

        async def ranked_doc_ids(user: User) -> list[str]:
            cands = await retriever.retrieve(query="what are our planning priorities?", user=user, k=10)
            cands = [c for c in cands if c.chunk.doc_id in {"up:persona-sales-plan", "up:persona-eng-plan"}]
            prox = await proximity.score(user=user, doc_ids=[c.chunk.doc_id for c in cands])
            ranked = ranker.rank(candidates=cands, proximity=prox)
            return [r.candidate.chunk.doc_id for r in ranked]

        sales = User(user_id="p-sales", tenant_id="t-test", email="s@x",
                     display_name="S", group_ids={"t-test:everyone", "g-sales"})
        eng = User(user_id="p-eng", tenant_id="t-test", email="e@x",
                   display_name="E", group_ids={"t-test:everyone", "g-eng"})

        sales_order = await ranked_doc_ids(sales)
        eng_order = await ranked_doc_ids(eng)

        # Each persona ranks their own authored doc first.
        assert sales_order[0] == "up:persona-sales-plan"
        assert eng_order[0] == "up:persona-eng-plan"
        # And the orderings differ — the headline claim.
        assert sales_order != eng_order
    finally:
        await embedder.aclose()
        await search.aclose()
        await graph.aclose()
```

- [ ] **Step 3: Run test, expect failure first run**

Run: `uv run pytest tests/test_persona_ranking.py -v -m integration`
Expected on first run: may FAIL if AI Search needs a few seconds to index the two new docs before they're retrievable. If it fails with an empty candidate list, re-run once (indexing lag). If it fails on the ordering assertion, that's a real signal — capture and report. Once docs are indexed, expect PASS.

- [ ] **Step 4: Confirm pass**

Run: `uv run pytest tests/test_persona_ranking.py -v -m integration`
Expected: PASS — `sales_order[0] == "up:persona-sales-plan"`, `eng_order[0] == "up:persona-eng-plan"`, orders differ.

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/eval/personas.json brain-api/tests/test_persona_ranking.py
git commit -m "test: two-persona ranking — same query, different order (acceptance #2)"
```

---

## Task 12: README update + end-to-end verification + tag

**Files:**
- Modify: `README.md` (root)
- Modify: `brain-api/README.md`

- [ ] **Step 1: Update `brain-api/README.md` endpoints + dev notes**

In `brain-api/README.md`, under the endpoints list, add:

```markdown
- `POST /admin/seed-people?users_limit=&groups_limit=` — seed People pillar from MS Graph (requires `x-admin-key`)
- `POST /admin/retrieve` — ranked candidate doc_ids without generation (eval/debug; requires `x-debug-bypass-auth` + `ENABLE_DEBUG_AUTH=true`)
```

Add a "Phase 2a" subsection:

```markdown
## Phase 2a — Personalization

People pillar (Cosmos Gremlin), query-time ACL re-check, and a personalized
ranker (Content + People). Provision Cosmos first:

```
./infra/provision_cosmos.sh   # appends COSMOS_GREMLIN_* to print; copy into .env
```

Seed the org graph, then the same query ranks differently per user. Ranker
weights are `RANK_WEIGHT_CONTENT` / `RANK_WEIGHT_PEOPLE` in `.env` (default
0.7 / 0.3). Activity pillar (ADX) is Phase 2b.
```

- [ ] **Step 2: Update root `README.md` "Next phases"**

In `README.md`, replace the Phase 2 bullet under "Next phases" with:

```markdown
- Phase 2a (done): People pillar (Cosmos Gremlin), query-time ACL re-check,
  personalized ranker (Content + People). Same query → different ranking per user.
- Phase 2b: Activity pillar (Event Hubs + ADX) + engagement signal in the ranker.
- Phase 3: Live Fetch via Microsoft Graph search.
- Phase 4: APIM gateway, OpenTelemetry, per-tenant index routing, JWKS caching, hardening.
```

- [ ] **Step 3: Full verification run**

```bash
cd brain-api
uv run ruff check .
uv run pytest -m "not integration" -v
uv run pytest -m integration -v
```

Expected: ruff clean; all unit tests pass; all integration tests pass. Capture the counts.

- [ ] **Step 4: Eval with the true retrieval metric**

```bash
cd brain-api
uv run uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 4
uv run python eval/run_eval.py --mode retrieval --report eval/reports/2026-05-29-phase2a.json
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
cat eval/reports/2026-05-29-phase2a.json
```

Expected: `recall_at_10 >= 0.7`, `mrr_at_10 >= 0.5`, exit 0. Capture the values.

- [ ] **Step 5: Commit docs + tag**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add README.md brain-api/README.md
git commit -m "docs: Phase 2a personalization — endpoints, seeding, ranker weights"
git tag -a phase-2a-personalization -m "Phase 2a: People pillar + query-time ACL re-check + personalized ranker. Same query, different ranking per user."
git log --oneline | head -15
```

---

## Self-Review

**Spec coverage (Phase 2a scope):**
- People pillar (Cosmos Gremlin, §Pillar 2) → Tasks 4, 5, 6, 7
- Personalized ranker (§Personalized Ranker) → Task 9, wired in Task 10
- Double-enforcement ACL query-time re-check (§ACL Store, §5 Step 6) → Task 8, wired in Task 10
- "Same query, different ranking per user" (acceptance #2) → Task 11
- Review finding I2 (client lifecycle) → Task 1
- Review finding M2 (true retrieval eval) → Tasks 2, 3
- Deferred (documented, not gaps): Activity pillar/ADX (Phase 2b), Live Fetch (Phase 3), per-tenant index I3 + JWKS I4 + APIM (Phase 4).

**Type/signature consistency:**
- `PeopleProximity.score(*, user, doc_ids) -> dict[str, float]` — used identically in Tasks 7, 10, 11.
- `PersonalizedRanker.rank(*, candidates, proximity) -> list[RankedResult]` — Tasks 9, 10, 11.
- `ACLStore.recheck(*, candidates, user) -> list[Candidate]` and `doc_principals(*, tenant_id, doc_id) -> set[str] | None` — Tasks 8, 10.
- `RankedResult` (candidate, final_score, signal_breakdown, rank) defined Task 9, consumed Tasks 10, 11.
- `Candidate.raw_scores["content_rrf"]` set in Task 3, read in Task 9.
- Orchestrator constructor gains `acl_store, proximity, ranker` in Task 10; every construction site (lifespan Task 10 Step 2, test Task 10 Step 3) updated.
- `aclose()` added to AzureOpenAIClient, AISearchClient, RedisCache, ACLStore, PeopleGraphClient, orchestrator — all closed in lifespan (Task 10 Step 2) and tests.

**Placeholder scan:** No TBD/TODO-as-work. Every code step has complete code. The one judgment call (Cosmos capacity/quota retry) is given an explicit BLOCKED escalation path in Task 4 Step 2.

**Known risk carried from Phase 1:** the chunker quality items (task #21 in the tracker) are still deferred; not in scope here.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-29-company-brain-phase2a-personalization.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — batch execution with checkpoints.

Tasks 4–6 and 11 need real Azure (Cosmos provisioning + Graph seeding); those are controller-run or user-in-loop like Phase 1b. Tasks 1–3, 8, 9 are pure-code/unit-testable and subagent-friendly.
