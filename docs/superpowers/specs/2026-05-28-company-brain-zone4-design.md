# Company Brain — Zone 4 Intelligence Layer (v1 Design)

**Status:** Draft for review
**Date:** 2026-05-28
**Source architecture:** `Company_Brain_Architecture.pdf` (24pp)
**This spec covers:** Zone 4 (Intelligence Layer) + minimal UX to make it demoable end-to-end, as the first runnable slice of the broader Company Brain platform.
**Out of scope here (each gets its own spec):** Crawlers (Zone 2), full ingest event bus, Document Intelligence + PII redaction (Zone 3), Teams/Slack bots, hard multi-tenancy, Purview lineage, learned ranker.

---

## 1. Goals & non-goals

### Goals

- Stand up a **demoable enterprise intelligence layer** on real Azure services in ~6 working days (hackathon pace, single developer + AI pair).
- Implement the full Zone 4 component set from the PDF: three pillars (Content / People / Activity), Semantic Kernel orchestrator, hybrid retrieval, Live Fetch, double-enforcement ACLs, personalized ranker, Azure OpenAI generation with citations, Redis caching.
- Ground every claim with a citation; refuse out-of-corpus questions explicitly.
- Demonstrate personalization: same query, different ranking for two personas with different group memberships and activity history.
- Demonstrate freshness: Live Fetch path for "today / right now" queries hits Microsoft Graph at query time.
- Demonstrate the double-enforcement security narrative: index-time ACL stamps + query-time ACL recheck, fail-closed on ACL Store outage.
- Ship an **eval harness from day one** with golden Q&A regression tests and an A/B config harness — quality changes are measurable, not vibes.
- Keep module boundaries clean so any component can later be lifted into its own Container App without rewriting interfaces.

### Non-goals (v1)

- Real source crawlers (Content/Permissions/Identity/Activity Listener) — replaced by admin ingest + Graph-based seeders.
- Continuous event bus (Event Grid / Service Bus / Event Hubs) on the ingestion path.
- Document Intelligence OCR / PII redaction — v1 ingests pre-extracted text.
- Real Content Safety / Prompt Shield — interface-only stub.
- Microsoft Purview lineage.
- Teams Bot, Slack Bot — web chat only.
- Hard multi-tenancy (dedicated resource groups per tenant).
- Bicep / Terraform IaC — `az` CLI provision script in v1.
- Learned ML ranker — heuristic multi-signal ranker in v1.

---

## 2. Approach

**Monolithic FastAPI service (`brain-api`) with strict internal module boundaries that map 1:1 to PDF Zone 4 components.** Each component is a Python module/class with an explicit interface; no cross-imports between query and admin paths except through these interfaces. A later split into separate Container Apps is a mechanical lift (copy the admin router + ingest module into a new service, point at the same Azure resources) — not a rewrite.

**Why monolith for v1:** one deploy target, one log stream, one health check, one local dev command (`uvicorn`). The two-service split costs 4–6 hours of inter-service auth/discovery/dual-deploy yak shaving before the first query can be answered.

**Why module boundaries matter even in a monolith:** the boundaries are the spec for the eventual microservice split. They also make components individually testable (swap AI Search for an in-memory store; swap Cosmos Gremlin for a stub).

### Top-level topology

```
Next.js Web Chat (Entra SSO via MSAL)
        │ HTTPS + Bearer
        ▼
Azure API Management (auth, rate limit, WAF)        ← deferred to Day 6; Container Apps ingress in early days
        │
        ▼
brain-api (Python 3.12 + FastAPI on Container Apps)
  /query           Orchestrator (Semantic Kernel)
                    ├─ Hybrid Retriever  ──▶ Azure AI Search (hybrid)
                    │                    ──▶ Cosmos DB Gremlin (People proximity)
                    │                    ──▶ Azure Data Explorer (Activity scores)
                    ├─ Live Fetch        ──▶ Microsoft Graph /search (on-behalf-of)
                    ├─ ACL Recheck       ──▶ Redis ACL Store
                    ├─ Personalized Ranker (RRF + weighted signals)
                    └─ Generation (Azure OpenAI GPT-4o, grounded with citations)
  /admin/ingest    chunk → embed → ACL-stamp → index
  /admin/seed-*    Graph → Cosmos; synthetic events → ADX
  /feedback        click/dwell → ADX (real Activity)
```

Identity: every Azure SDK call goes through `DefaultAzureCredential` → user-assigned managed identity in cloud, `az login` locally. No connection strings in code. Key Vault holds only what can't use managed identity (Entra app client secret for OBO, third-party keys).

Observability: OpenTelemetry → Application Insights, one trace per request, signal breakdown attached as span attributes.

---

## 3. Module layout

Single FastAPI app, structured so each Zone 4 component is one module with a stable interface.

```
brain-api/
├── app/
│   ├── main.py                       # FastAPI app, lifespan, OpenTelemetry init
│   ├── config.py                     # pydantic-settings; reads Key Vault + env
│   ├── auth.py                       # Entra JWT validation, user-claims expansion
│   │
│   ├── api/
│   │   ├── query.py                  # POST /query, POST /feedback
│   │   └── admin.py                  # POST /admin/ingest, /seed-people, /seed-activity
│   │
│   ├── domain/                       # plain pydantic data classes
│   │   ├── identity.py               # User, Group, OrgEdge
│   │   ├── chunk.py                  # SourceDoc, Chunk, ChunkMetadata
│   │   ├── query.py                  # QueryRequest, Candidate, RankedResult, Answer, Citation
│   │   └── activity.py               # ActivityEvent
│   │
│   ├── orchestrator/
│   │   └── kernel.py                 # SemanticKernelOrchestrator: plan → retrieve → fetch → rank → answer
│   │
│   ├── retrieval/
│   │   ├── hybrid_retriever.py       # HybridRetriever.retrieve(query, user, k) → list[Candidate]
│   │   ├── ai_search_client.py       # AI Search hybrid (vector + BM25 + semantic re-ranker)
│   │   ├── people_proximity.py       # PeopleProximity.score(user, doc_ids) via Cosmos Gremlin
│   │   └── activity_signal.py        # ActivitySignal.score(user, doc_ids) via ADX KQL
│   │
│   ├── live_fetch/
│   │   ├── base.py                   # LiveFetcher protocol
│   │   └── graph_search.py           # MSGraphSearchFetcher (Microsoft Graph /search)
│   │
│   ├── acl/
│   │   ├── store.py                  # ACLStore (Redis): user_acl_hashes, recheck
│   │   └── enforcement.py            # index-time filter expression + query-time recheck
│   │
│   ├── ranking/
│   │   └── personalized_ranker.py    # RRF + weighted signals (content/people/activity/recency/authority)
│   │
│   ├── generation/
│   │   ├── azure_openai.py           # AzureOpenAIClient: embed(), complete()
│   │   └── prompts.py                # grounded-answer system prompt, citation enforcer
│   │
│   ├── safety/
│   │   └── content_safety.py         # ContentSafetyStub (real impl deferred); same interface
│   │
│   ├── cache/
│   │   └── redis_cache.py            # query result cache, embedding cache (tenant-scoped keys)
│   │
│   ├── ingest/
│   │   ├── pipeline.py               # IngestPipeline.process(SourceDoc) → chunks → index
│   │   ├── chunker.py                # structure-aware chunker (300–800 tokens, overlap)
│   │   └── acl_resolver.py           # synthetic ACL stamping from upload metadata
│   │
│   ├── seeders/
│   │   ├── people_seed.py            # Microsoft Graph → Cosmos Gremlin
│   │   └── activity_seed.py          # generate N synthetic ActivityEvents into ADX
│   │
│   └── observability/
│       └── otel.py                   # OpenTelemetry → App Insights
│
├── eval/                             # eval harness (separate from runtime)
│   ├── golden.jsonl                  # 30 golden Q→expected-citation pairs (grow to 200)
│   ├── corpus/                       # ~200 deterministic test docs committed to repo
│   ├── personas.json                 # fixture Users with real Cosmos vertices + Activity history
│   ├── load_corpus.py                # ingest test corpus into AI Search / Cosmos / ADX
│   ├── run_eval.py                   # retrieval / e2e / full modes
│   ├── run_ab.py                     # A/B config comparison with bootstrap CIs
│   ├── judges/                       # LLM-as-judge templates + judge-drift checks
│   └── configs/                      # YAML variants (ranker weights, chunk size, prompt)
│
├── tests/                            # pytest unit + integration
├── infra/
│   ├── provision.sh                  # az CLI v1 bootstrap
│   └── bicep/                        # follow-up
├── Dockerfile
├── pyproject.toml                    # uv-managed
└── .env.example
```

### Component interfaces (the contracts that make the future split mechanical)

| Component              | Method                          | Signature                                                          |
| ---------------------- | ------------------------------- | ------------------------------------------------------------------ |
| `HybridRetriever`      | `retrieve`                      | `(query: str, user: User, k: int) -> list[Candidate]`              |
| `LiveFetcher`          | `fetch`                         | `(query: str, user: User) -> list[Candidate]`                      |
| `ACLStore`             | `user_principals`               | `(user_id: str, tenant_id: str) -> set[str]`                       |
| `ACLStore`             | `recheck`                       | `(candidates: list[Candidate], user: User) -> list[Candidate]`     |
| `PersonalizedRanker`   | `rank`                          | `(candidates: list[Candidate], user: User) -> list[RankedResult]`  |
| `Orchestrator`         | `answer`                        | `(QueryRequest) -> Answer`                                         |
| `IngestPipeline`       | `process`                       | `(doc: SourceDoc) -> IngestResult`                                 |
| `ContentSafety`        | `screen_input` / `screen_output`| `(text: str) -> SafetyVerdict`                                     |

Every external Azure dependency lives behind a thin client class in its module; SDK calls do not leak into business logic.

---

## 4. Data shapes & schemas

### Domain models (pydantic)

```python
class User:
    user_id: str            # Entra OID
    tenant_id: str          # Entra TID
    email: str
    display_name: str
    group_ids: set[str]     # transitive memberships, expanded once per request
    manager_id: str | None

class SourceDoc:                       # input to ingest
    doc_id: str                         # stable: f"{source}:{source_id}"
    tenant_id: str
    source: Literal["sharepoint","teams","uploaded","slack","jira"]
    source_url: str
    title: str
    body: str                           # pre-extracted text (Doc Intelligence out of scope v1)
    author_id: str | None
    acl_principals: list[str]           # user/group IDs allowed to read
    created_at: datetime
    modified_at: datetime
    mime: str

class Chunk:                            # what lands in AI Search
    chunk_id: str                       # f"{doc_id}#chunk-{i}"
    doc_id: str
    tenant_id: str
    source: str
    source_url: str
    title: str
    content: str                        # 300–800 tokens, structure-aware
    content_vector: list[float]         # 3072d, text-embedding-3-large
    acl_principals: list[str]           # index-time ACL stamp
    author_id: str | None
    entities: list[str]                 # extracted people/projects
    created_at: datetime
    modified_at: datetime
    chunk_index: int

class Candidate:                        # post-retrieval, pre-rank
    chunk: Chunk
    sources_hit: set[Literal["vector","bm25","semantic","live","graph"]]
    raw_scores: dict[str, float]
    live_payload: dict | None           # for Live Fetch candidates with no stored chunk

class RankedResult:
    candidate: Candidate
    final_score: float
    signal_breakdown: dict[str, float]
    rank: int

class Answer:
    text: str
    citations: list[Citation]
    query_id: str                       # UUID v7 for feedback correlation
    debug: dict | None                  # signals, latencies (off in prod)

class Citation:
    doc_id: str
    chunk_id: str
    source_url: str
    title: str
    snippet: str

class ActivityEvent:
    timestamp: datetime
    tenant_id: str
    user_id: str
    query_id: str | None
    doc_id: str
    chunk_id: str | None
    event_type: Literal["view","click","thumbs_up","thumbs_down","dwell","query"]
    source: str
    duration_ms: int | None
```

### Azure AI Search index — `brain-content-{tenant_id}` (one per tenant)

| Field             | Type                       | Attributes                                              |
| ----------------- | -------------------------- | ------------------------------------------------------- |
| `chunk_id`        | `Edm.String`               | key                                                     |
| `doc_id`          | `Edm.String`               | filterable, retrievable                                 |
| `tenant_id`       | `Edm.String`               | filterable                                              |
| `source`          | `Edm.String`               | filterable, facetable                                   |
| `source_url`      | `Edm.String`               | retrievable                                             |
| `title`           | `Edm.String`               | searchable, retrievable                                 |
| `content`         | `Edm.String`               | searchable, retrievable                                 |
| `content_vector`  | `Collection(Edm.Single)`   | vector, dim=3072, HNSW, cosine                          |
| `acl_principals`  | `Collection(Edm.String)`   | filterable                                              |
| `author_id`       | `Edm.String`               | filterable                                              |
| `entities`        | `Collection(Edm.String)`   | filterable, facetable                                   |
| `created_at`      | `Edm.DateTimeOffset`       | filterable, sortable                                    |
| `modified_at`     | `Edm.DateTimeOffset`       | filterable, sortable                                    |
| `chunk_index`     | `Edm.Int32`                | retrievable                                             |

**Semantic configuration:** `title` in `titleField`; `content` in `prioritizedContentFields`; `entities` in `prioritizedKeywordsFields`. Query mode: `simple` + `queryType=semantic` + `vectorQueries` (k=50). Fusion: **explicit Reciprocal Rank Fusion in our ranker** — we don't use AI Search's built-in fuser so we can A/B fusion weights.

**ACL filter expression** (computed once per request after group expansion):
```
tenant_id eq '{tenant}' and search.in(acl_principals, '{user_id},{','.join(group_ids)}', ',')
```

### Cosmos DB Gremlin — `brain-people` (single graph, partitioned by `tenant_id`)

| Vertex     | Properties                                                  |
| ---------- | ----------------------------------------------------------- |
| `User`     | `user_id`, `tenant_id`, `email`, `display_name`             |
| `Group`    | `group_id`, `tenant_id`, `name`                             |
| `Document` | `doc_id`, `tenant_id`                                       |

| Edge                | From → To       | Properties                                  |
| ------------------- | --------------- | ------------------------------------------- |
| `manages`           | User → User     | (manager → report)                          |
| `member_of`         | User → Group    |                                             |
| `authored`          | User → Document | `ts`                                        |
| `collaborates_with` | User → User     | `weight` (derived from co-edit / mentions)  |

**People proximity query** (batched for candidate doc list):

```
g.V().has('User','user_id', $user_id).has('tenant_id', $tenant_id)
 .repeat(__.both('manages','member_of','collaborates_with').simplePath()).times(2)
 .where(__.out('authored').has('doc_id', within($doc_ids)))
 .group().by(__.out('authored').values('doc_id')).by(count())
```

Returns `{doc_id → hop-weighted reachability}`. Normalized to `[0,1]` in `people_proximity.py`.

### Azure Data Explorer — `brain-activity` cluster, `activity` database

```kusto
.create table ActivityEvents (
  Timestamp:datetime, TenantId:string, UserId:string, QueryId:string,
  DocId:string, ChunkId:string, EventType:string, Source:string, DurationMs:int
)
```

**Engagement scoring KQL** (batched for the candidate doc list):

```kusto
ActivityEvents
| where TenantId == tenant and DocId in (docIds) and Timestamp > ago(30d)
| extend recency = exp(-1.0 * datetime_diff('day', now(), Timestamp) / 14.0)
| extend self_weight = iif(UserId == user, 2.0, 1.0)
| summarize score = sum(recency * self_weight) by DocId
```

Self-engagement weighted 2×; exponential recency decay τ=14d.

### Redis — ACL Store + Query Cache

| Key                                                                      | Value                              | TTL    |
| ------------------------------------------------------------------------ | ---------------------------------- | ------ |
| `acl:user:{tenant}:{user_id}`                                            | Set of principal IDs (self + groups) | 15 min |
| `acl:doc:{tenant}:{doc_id}`                                              | Set of principal IDs allowed         | 15 min |
| `cache:answer:{tenant}:sha256(user_acl_set‖normalized_q)`                | Answer JSON                          | 10 min |
| `cache:embed:sha256(text)`                                               | `list[float]` (3072d embedding)      | 24 h   |

**Cache-key safety property:** the answer-cache key is bound to the user's expanded ACL set, so two users with different access never share a cached answer (prevents cross-tenant or cross-user leakage). Live-Fetch queries bypass the answer cache.

---

## 5. Query flow (target: ~2.5s p95)

### Step 0 — Gateway (~5ms)

API Management validates the Entra JWT, applies tenant rate limits, attaches `x-user-claims`, forwards to `brain-api`. In days 1–5 we expose Container Apps ingress directly with TLS; APIM goes in front on day 6.

### Step 1 — Auth + user expansion (~30ms cold / ~2ms warm)

`app/auth.py`:
- Verify JWT against Entra JWKS (cached).
- Extract `user_id`, `tenant_id`, `email`.
- Expand transitive group memberships via Graph `/users/{user_id}/transitiveMemberOf` using an **app-only token** (admin-consented `Directory.Read.All`). Cached per user in Redis for 10 min. Using app-only here keeps Days 0–4 off the OBO critical path; OBO is required only for Live Fetch in Step 5.
- Mint `query_id` (UUID v7 — sortable, used for feedback correlation).

### Step 2 — Cache lookup (~3ms)

`app/cache/redis_cache.py`:
- `cache_key = sha256(sorted(user.principals) ‖ normalized(query))`.
- Hit: return cached `Answer`, fire-and-forget `ActivityEvent(event_type="query", cached=true)`, done.

### Step 3 — Orchestrator: plan (~80ms)

`app/orchestrator/kernel.py`. Semantic Kernel call to `gpt-4o-mini`:

```
Classify the query into a plan:
- needs_retrieval: bool
- needs_live_fetch: bool   # true for "today", "now", "this week", "current", volatile entities
- entities: [string]
- rewrite: cleaned-up query string for retrieval
```

If `needs_live_fetch=true`, Live Fetch runs in parallel with retrieval. Explicit freshness terms in the user query force Live Fetch on regardless of classifier confidence.

### Step 4 — Hybrid retrieval (~250ms p95, parallel with Step 5)

`app/retrieval/hybrid_retriever.py` — all inside one `asyncio.gather`:

**a. AI Search hybrid call** (~180ms):

```python
search_client.search(
    search_text=rewrite,
    vector_queries=[VectorizedQuery(
        vector=embed(rewrite), k_nearest_neighbors=50, fields="content_vector"
    )],
    query_type="semantic",
    semantic_configuration_name="brain-semantic",
    filter=f"tenant_id eq '{tenant}' and search.in(acl_principals, '{user_principals}', ',')",
    top=30,
)
```

Returns up to 30 candidates with `@search.score`, `@search.rerankerScore`, `@search.captions`.

**b. People proximity** (~80ms): Cosmos Gremlin query batched over the candidate `doc_id` set from (a). Returns `{doc_id → proximity ∈ [0,1]}`.

**c. Activity signal** (~60ms): one KQL query against ADX over the candidate set. Returns `{doc_id → engagement_score}`.

### Step 5 — Live Fetch (~400ms p95, parallel with Step 4)

`app/live_fetch/graph_search.py` (when `needs_live_fetch=true`):
- Call Microsoft Graph `/search/query` with the user's delegated token (on-behalf-of flow). Graph enforces source ACLs natively.
- Map results to `Candidate` with `sources_hit={"live"}`, `live_payload=raw_graph_hit`.
- **Hard timeout 600ms.** On timeout: log, return `[]`, flag answer "live data unavailable" — never block the answer.

### Step 6 — ACL recheck (~15ms)

`app/acl/enforcement.py`:
- For each candidate: fetch `acl:doc:{tenant}:{doc_id}` from Redis ACL Store, intersect with `user.principals`. Empty → drop.
- Missing key fallback: use the chunk's index-time `acl_principals` (conservative; in production the Permissions Crawler keeps the store warm).
- **Fail-closed:** ACL Store unreachable AND index-time ACL not verified within freshness SLA (15 min) → drop the chunk. Per PDF §3.2.

### Step 7 — Personalized Ranker (~10ms)

`app/ranking/personalized_ranker.py`. Reciprocal Rank Fusion across the candidate ranks (vector, BM25, semantic, live), then a weighted multi-signal score:

```
rrf_score(c) = Σ over (rank_vector, rank_bm25, rank_semantic, rank_live) of 1 / (60 + rank)

final(c) = w_content    * normalize(rrf_score)
        + w_people     * proximity[c.doc_id]
        + w_activity   * engagement[c.doc_id]
        + w_recency    * exp(-Δdays / 30)
        + w_authority  * authority_term(c, user)
```

`authority_term`: `1.0` if `author == user.manager`, `0.5` if `author ∈ user.team`, else `0`.

**v1 weights** (config-driven, A/B-able via eval harness):
```
w_content=0.55, w_people=0.20, w_activity=0.15, w_recency=0.05, w_authority=0.05
```

Returns top-`k` (default 5) with full `signal_breakdown` for debug.

### Step 8 — Input safety (~40ms; v1 stub passes through)

`ContentSafetyStub` — same interface as the real Azure Content Safety Prompt Shield call. Real implementation screens the **retrieved chunk text** (the injection vector), not the user query (the user is trusted). Swap is mechanical.

### Step 9 — Generation (~800–1500ms)

`app/generation/azure_openai.py`. GPT-4o with strict grounding prompt:

```
System: Answer ONLY from the provided context. Cite every claim with [n].
        If the context does not contain the answer, say so explicitly.
User: {original_query}
Context:
  [1] {title} — {source_url}\n{chunk.content}
  [2] ...
```

Citations parsed from `[n]` markers in the output and resolved back to `Citation` objects against the ranked list. Orphan `[n]` markers (no matching chunk) are stripped defensively.

### Step 10 — Output safety + assemble (~30ms)

- `ContentSafetyStub` passes the answer (real impl checks harm categories).
- Build `Answer{text, citations, query_id}`, cache it (Step 2's key) with 10-min TTL.

### Step 11 — Fire-and-forget telemetry

- `ActivityEvent(event_type="query", query_id, user, tenant, ts)` to ADX (direct ingest API in v1; batched in v2).
- OpenTelemetry span emitted with signal breakdown attached as attributes.

### Response (~2.5s p95)

```json
{
  "query_id": "01JF...",
  "answer": "Our Q3 sales plan targets $42M ARR with focus on enterprise upsell. [1][2]",
  "citations": [
    {"doc_id":"sp:123","title":"Q3 Plan","source_url":"https://...","snippet":"..."}
  ],
  "debug": { "signals":{...}, "latencies":{...}, "live_fetch_used": true }
}
```

### Feedback loop

```
POST /feedback {query_id, doc_id, signal: "click"|"thumbs_up"|"thumbs_down", dwell_ms}
```

Writes an `ActivityEvent` to ADX. Feeds the Activity signal at rank time AND the eval harness's CTR/MRR dashboards.

### Failure modes (graceful degradation)

| Failure                  | Behavior                                                                                 |
| ------------------------ | ---------------------------------------------------------------------------------------- |
| AI Search down           | Return Live Fetch results only, banner "limited results"                                 |
| Azure OpenAI throttled   | Extractive answer (top chunk snippet) with banner; cache absorbs repeat queries          |
| Live Fetch timeout       | Index-only answer with banner "fresh data unavailable"                                   |
| ACL Store down           | **Fail-closed** — no results returned; log incident                                      |
| Cosmos Gremlin down      | Skip People signal (proximity=0); ranker still runs                                      |
| ADX down                 | Skip Activity signal; ranker still runs                                                  |
| Redis down               | Skip answer cache; Step 7 ranking proceeds                                               |

---

## 6. Eval harness

Lives at `eval/`. Shares only domain models with the runtime — tests the real system, not a memoized version.

### Metrics

| Metric                      | What it tells us                                | How                                                                          |
| --------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------------- |
| **Recall@10**               | Is the right doc in the candidate set?          | Expected `doc_id` ∈ top-10 candidates pre-rank                               |
| **MRR@10**                  | How high does the right doc rank?               | Mean of `1/rank` of expected `doc_id` post-rank                              |
| **nDCG@5**                  | Graded relevance over top-5                     | Standard formula; `relevance` labels 0–3 in golden file                      |
| **Citation-grounded rate** | Does the citation back the claim?               | LLM-as-judge: gpt-4o-mini on `(claim, evidence)`                              |
| **Answer faithfulness**     | Did the model say anything not in the context?  | LLM-as-judge over `(answer, retrieved_chunks)`                                |
| **Answer correctness**      | Does it match the gold answer?                  | LLM-as-judge equivalence, scaled 0–1                                          |
| **Refusal accuracy**        | Says "I don't know" on out-of-corpus?           | Binary correct/wrong on `expect_refusal=true` subset                          |
| **p50 / p95 latency**       | Query latency                                   | Wall clock per request                                                       |
| **Cost per query**          | $ / Q                                           | Token counts × Azure OpenAI pricing                                          |

CTR / session success live in App Insights / ADX dashboards, not the offline harness.

### Golden file format — `eval/golden.jsonl` (start ~30 entries, grow to 200)

```jsonl
{"qid":"q001","query":"what is our Q3 sales plan?",
 "expected_doc_ids":["sp:sites/sales/q3-plan.docx"],
 "expected_chunk_substrings":["target $42M ARR"],
 "gold_answer":"The Q3 sales plan targets $42M ARR, focused on enterprise upsell.",
 "relevance_labels":{"sp:sites/sales/q3-plan.docx":3,"sp:sites/sales/q2-recap.docx":1},
 "user_persona":"sales_rep_central","expect_refusal":false,
 "tags":["sales","planning","content-pillar"]}

{"qid":"q017","query":"who is on call for the payments service right now?",
 "expect_live_fetch":true,"expected_source":"graph",
 "user_persona":"engineer_payments","tags":["live-fetch","freshness"]}

{"qid":"q024","query":"what's the recipe for chocolate chip cookies?",
 "expect_refusal":true,"tags":["out-of-corpus","refusal"]}
```

### Personas — `eval/personas.json`

Real Cosmos Gremlin vertices + synthetic Activity histories tied to each persona so personalization claims are *testable*, not theatrical.

```json
{
  "sales_rep_central": {
    "user_id":"u-001","tenant_id":"t-test",
    "email":"alex@contoso.com","display_name":"Alex (Sales)",
    "group_ids":["g-sales","g-central-region"],
    "manager_id":"u-100"
  },
  "engineer_payments": { "user_id":"u-002","group_ids":["g-eng","g-payments"], ... }
}
```

### Test corpus — `eval/corpus/`

~200 deterministic docs committed to repo:
- 50 policy docs (PTO, expenses, security)
- 50 planning docs (Q3 plan, roadmap, OKRs)
- 50 engineering docs (runbooks, postmortems)
- 50 team notes (varied authors → personas)

Loaded via `eval/load_corpus.py` into actual AI Search / Cosmos / ADX before each full run.

### Run modes

```
eval/run_eval.py [--mode {retrieval,e2e,full}] [--config configs/v1.yaml]
                 [--report-out reports/2026-05-28.json] [--baseline reports/baseline.json]
```

- **`retrieval`** — skip generation. ~30s. Runs on every PR.
- **`e2e`** — full query path including GPT-4o. ~5 min, ~$0.50 per run.
- **`full`** — e2e + faithfulness + correctness LLM-as-judge passes. ~10 min, ~$1.50 per run.

### A/B harness

`eval/run_ab.py --a configs/v1.yaml --b configs/v1_higher_people_weight.yaml`

YAML config controls embedding model, chunk size, ranker weights, prompt template, top-k, vector vs hybrid mode. Output: per-metric delta with bootstrap (1000 resamples) confidence intervals. Significant deltas flagged `*` / `**`.

### CI integration

- **On every PR:** `retrieval` mode. Recall@10 regression > 3% blocks merge.
- **Nightly:** `full` mode. Posted to Slack / GitHub issue. 7-day rolling baseline drift alerts.
- **Pre-deploy:** `full` mode against staging. MRR drop vs last green build blocks deploy.

### Judge drift protection

LLM-as-judge prompts pin model + temperature + prompt version in every report. A separate weekly job compares judge outputs against a 50-example human-labeled subset to detect judge regressions.

---

## 7. Azure provisioning

### Region

**`eastus2`** — has Azure OpenAI, AI Search, Cosmos Gremlin, ADX, Container Apps all GA. Alternative: `swedencentral` for EU residency.

### Resources

| Resource                      | SKU                                                              | ~$/mo            |
| ----------------------------- | ---------------------------------------------------------------- | ---------------- |
| Resource group                | `rg-company-brain-dev`                                           | —                |
| Azure AI Search               | **Basic** (hybrid + semantic ranker need ≥ Basic)                | ~$75             |
| Azure OpenAI                  | Standard (S0): `gpt-4o`, `gpt-4o-mini`, `text-embedding-3-large` | tokens           |
| Cosmos DB (Gremlin)           | **Serverless**                                                   | ~$10–30          |
| Azure Data Explorer           | **Dev/Test (D11_v2, 1 node)** — pause between demos              | ~$120 running    |
| Azure Cache for Redis         | **Basic C0** (250 MB)                                            | ~$16             |
| Container Apps environment    | Consumption (scale-to-zero)                                      | ~$10–30          |
| Container Registry            | Basic                                                            | ~$5              |
| Key Vault                     | Standard                                                         | ~$0–1            |
| API Management                | **Consumption** (defer to Day 6; Container Apps ingress earlier) | $0 idle          |
| Application Insights          | Pay-as-you-go (first 5GB ingest/mo free)                         | ~$0–10           |

**Estimated continuously-running: ~$300–500/mo.** Hackathon-only (resources up for 72h, ADX paused between demos): **under $50.**

### Identity & secrets

- `brain-api` runs with a user-assigned managed identity `mi-brain-api`.
- RBAC roles: `Search Service Contributor` on AI Search; `Cosmos DB Built-in Data Contributor` on Cosmos; `Key Vault Secrets User`; `Cognitive Services OpenAI User` on Azure OpenAI; ADX `Database User` on `activity`.
- No connection strings in code; all Azure SDK clients use `DefaultAzureCredential`.
- Key Vault holds Entra app client secret (OBO flow for Live Fetch) and any third-party keys.

### Local dev

```bash
# one-time
brew install azure-cli uv pnpm
az login
cd brain-api && uv sync
cd ../web && pnpm install
cp brain-api/.env.example brain-api/.env   # fill resource names

# run
cd brain-api && uv run uvicorn app.main:app --reload   # :8000
cd web && pnpm dev                                      # :3000
```

`DefaultAzureCredential` picks up `az login` creds → all Azure calls work locally with the developer's identity. Docker only for building the image we push to ACR.

---

## 8. Build sequence

Single dev + AI pair, hackathon pace. Assumes Azure quota and Entra consent sorted on Day 0.

| Day      | Deliverable                                                                                                            | Demoable                          |
| -------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| **0**    | `infra/provision.sh` provisions all resources. Entra app registered with: custom scope for `brain-api`, `Directory.Read.All` (app, admin-consented), `Sites.Read.All` + `Files.Read.All` (delegated, admin-consented for Day-5 OBO). `GET /healthz` returns 200. Web shell with MSAL login | "I can log in" |
| **1**    | Ingest endpoint + chunker + embed + AI Search index with ~50 test docs. `POST /query` returns top-5 chunks (no LLM yet) | Search works                      |
| **2**    | Orchestrator + GPT-4o grounded answer with citations. Redis cache. Eval harness scaffold + 10 golden Qs                 | Grounded Q&A with citations       |
| **3**    | People pillar seeding from Graph + Cosmos Gremlin + proximity query. Personalized ranker. Two-persona demo               | Personalization story             |
| **4**    | Activity pillar seeding (synthetic) + ADX + engagement signal. ACL store + index-time stamps + query-time recheck       | Security + activity signal story  |
| **5**    | Live Fetch via MS Graph `/search`. Orchestrator live-vs-index routing. Failure-mode banners                              | Freshness story                   |
| **6**    | APIM in front + per-tenant index naming + hardening (timeouts, retries, structured logs). Eval grows to 30 Qs; CI runs   | Production-ish demo               |
| **7**    | Buffer: fix what broke. Run demo three times end-to-end. Cost dashboard                                                  | Ready to present                  |

---

## 9. Risks & mitigations

| Risk                                                         | Likelihood | Impact   | Mitigation                                                                                                                       |
| ------------------------------------------------------------ | ---------- | -------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Azure OpenAI quota too low in `eastus2`                       | Medium     | High     | Use `gpt-4o-mini` for plan step; batch embedding calls in 16s; request quota uplift Day 0                                        |
| Cosmos Gremlin RU throttling during People seed               | Medium     | Medium   | Batched writes, exponential backoff; Serverless scales but caps per request                                                      |
| ADX cluster cold-start (~5 min) during demo                   | High       | High     | Warm cluster overnight before demo; pause only between non-demo days                                                             |
| Graph `/search` delegated consent missing                     | Medium     | High     | Day 0 admin consent for `Sites.Read.All`, `Files.Read.All` (delegated) and `Directory.Read.All` (application); OBO flow tested before Day 5 |
| Cache leakage across users                                    | Low        | Critical | Cache key bound to user's ACL set; tested explicitly in `tests/cache_isolation_test.py`                                          |
| Stale ACLs leak via index-time-only filter                    | Medium     | Critical | Query-time recheck against Redis ACL Store; fail-closed on store outage                                                          |
| Prompt injection from indexed content                          | High       | High     | Defense: grounding prompt isolates retrieved content; v2 swaps `ContentSafetyStub` for Prompt Shield                              |
| Hallucinated citations                                        | Medium     | Medium   | Defensive parsing strips `[n]` with no matching chunk; faithfulness judge in eval harness flags unsupported claims                |
| Live Fetch latency spikes blocking query                      | High       | Medium   | 600ms hard timeout; parallel with retrieval; banner on timeout                                                                   |
| Cost overrun on Azure OpenAI                                  | Medium     | Medium   | Redis answer cache; gpt-4o-mini for plan step; per-tenant rate limit in APIM Day 6                                               |

---

## 10. Open questions (resolve before plan)

- **APIM Consumption vs Developer SKU** — Consumption is cheaper and pay-per-call but has cold-start; Developer SKU is ~$50/mo always-on. Decide by Day 5.
- **OBO flow for Live Fetch** — needs the web app to pass the user's access token to `brain-api`, which exchanges for a Graph token. Validate the Entra app registration supports this before Day 5.
- **Activity event ingest path** — direct ADX ingest API in v1 (simpler) vs Event Hubs path (PDF Zone 2 canonical). Direct API for v1, Event Hubs in the follow-up.

---

## 11. Acceptance criteria

A demo is ready when:

1. Two personas (sales_rep_central, engineer_payments) can sign in via Entra SSO on the web chat.
2. Each persona gets a **different ranking** on the same content query (e.g., "what are our planning priorities?") and the difference is explained by the `signal_breakdown` debug panel.
3. A query like "who is on call right now?" triggers Live Fetch and the answer cites a Graph-sourced result with `live_fetch_used: true`.
4. Revoking a user's access to a document (manual ACL store update) causes that document to disappear from results within 15 minutes, with no code change.
5. The eval harness `retrieval` mode reports Recall@10 ≥ 0.8, MRR@10 ≥ 0.5 on the 30-question golden set.
6. The `full` eval reports Citation-grounded rate ≥ 0.9 and Refusal accuracy ≥ 0.9 on the out-of-corpus subset.
7. p95 query latency under 3.0s on a warm cache; under 4.0s on cold cache with Live Fetch.
8. All Azure SDK calls authenticate via `DefaultAzureCredential` — no secrets in environment variables or code.

---

## 12. After this spec

The next step is `superpowers:writing-plans` to produce a day-by-day implementation plan keyed to the build sequence in §8, with each day broken into committable units and explicit verification gates.
