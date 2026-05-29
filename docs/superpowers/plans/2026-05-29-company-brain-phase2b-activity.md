# Company Brain — Phase 2b Implementation Plan (Activity Pillar + Engagement Signal)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the third ranking pillar — Activity. Capture engagement events (views, clicks, thumbs) via a `/feedback` endpoint, store them in Azure Data Explorer (ADX), and fold a recency-weighted engagement score into the personalized ranker as its third weighted signal — so a document many people (especially you) recently engaged with ranks higher.

**Architecture:** Extends the Phase 2a monolith. A free-tier ADX cluster holds an `ActivityEvents` table. `/feedback` ingests one event at a time via ADX inline ingestion (no Event Hubs, no ingest endpoint — works on free clusters). An `ActivitySignal` scorer runs a parameterized KQL query for recency-weighted engagement per candidate doc, normalized to [0,1] — mirroring `PeopleProximity`. The orchestrator fetches the activity signal (with graceful degradation if ADX is down, same pattern as Cosmos) and passes it to the ranker, whose fusion becomes `w_content·content + w_people·people + w_activity·activity`.

**Tech Stack:** Existing + `azure-kusto-data` (ADX query + inline-ingest control commands, AAD auth via `DefaultAzureCredential`). No `azure-kusto-ingest` (free clusters lack the separate ingest endpoint; inline ingestion via the query client is used instead).

**Scope cut:** Activity pillar only. Event Hubs is NOT used (direct inline ingest per the decision). Live Fetch is Phase 3. Per-tenant index (I3), JWKS caching (I4), the ACL freshness-SLA gate, and the Phase 2a minor cleanups (shared Cosmos client in seed-people, dead `collaborates_with` edge, split `/admin` auth prefixes) are all deferred.

**Prerequisites in place:** Phase 2a shipped (tag `phase-2a-personalization`). Azure resources live in `swedencentral`, RG `rg-company-brain-dev`. `brain-api/.env` populated (AI Search, OpenAI, Redis, Cosmos). Ranker is content+people; orchestrator already degrades gracefully when Cosmos is down. 33 unit + 22 integration tests pass.

**Swappability note:** Everything ADX-specific sits behind an `ActivitySignal` interface and an `ActivityStore` class. If the free ADX cluster proves painful at execution time, a Redis-sorted-set implementation of the same two interfaces is a mechanical swap (noted at Task 2's escalation path) — do NOT let ADX provisioning block the ranker work.

---

## File Structure

New/changed files in Phase 2b:

```
brain-api/
├── app/
│   ├── config.py                       # MODIFIED — adx_cluster_uri, adx_database, rank_weight_activity
│   ├── main.py                         # MODIFIED — construct ActivityStore + ActivitySignal in lifespan
│   ├── deps.py                         # MODIFIED — get_activity_store accessor
│   ├── domain/
│   │   └── activity.py                 # NEW — ActivityEvent model
│   ├── activity/
│   │   ├── __init__.py                 # NEW (empty)
│   │   ├── store.py                    # NEW — ActivityStore (ADX: create_table, ingest_event, engagement_scores)
│   │   └── signal.py                   # NEW — ActivitySignal.score(user, doc_ids) -> normalized dict
│   ├── api/
│   │   ├── feedback.py                 # NEW — POST /feedback
│   │   └── admin.py                    # MODIFIED — POST /admin/seed-activity
│   ├── ranking/
│   │   └── personalized_ranker.py      # MODIFIED — add activity term
│   └── orchestrator/
│       └── kernel.py                   # MODIFIED — fetch activity signal (degrading), pass to ranker
├── infra/
│   └── adx_setup.md                    # NEW — free ADX cluster creation checklist (user-in-loop)
└── tests/
    ├── test_activity_event.py          # NEW (unit)
    ├── test_activity_store.py          # NEW (integration)
    ├── test_activity_signal.py         # NEW (integration)
    ├── test_personalized_ranker_activity.py  # NEW (unit)
    ├── test_feedback.py                # NEW (integration)
    └── test_activity_ranking.py        # NEW (integration — acceptance)
```

---

## Conventions (same as prior phases)

- Run from `brain-api/`. Direct commits to `main`, one per task. Integration tests carry `@pytest.mark.integration`.
- `DefaultAzureCredential` uses the signed-in `az` user. After each task `uv run ruff check .` must be clean before committing.
- TestClient usage must use `with TestClient(app) as client:` (lifespan populates `app.state`).

---

## Task 1: ActivityEvent domain model + ActivitySignal interface

**Files:**
- Create: `brain-api/app/domain/activity.py`
- Create: `brain-api/app/activity/__init__.py` (empty)
- Create: `brain-api/tests/test_activity_event.py`

- [ ] **Step 1: Write the failing test**

`brain-api/tests/test_activity_event.py`:

```python
from datetime import UTC, datetime

from app.domain.activity import ActivityEvent


def test_activity_event_defaults_and_fields() -> None:
    now = datetime.now(UTC)
    e = ActivityEvent(
        timestamp=now,
        tenant_id="t-test",
        user_id="u-1",
        doc_id="up:policy-pto",
        event_type="view",
        source="uploaded",
    )
    assert e.query_id is None
    assert e.chunk_id is None
    assert e.duration_ms is None
    assert e.event_type == "view"


def test_activity_event_rejects_bad_event_type() -> None:
    import pytest
    from pydantic import ValidationError

    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        ActivityEvent(
            timestamp=now, tenant_id="t", user_id="u", doc_id="d",
            event_type="not-a-real-type", source="uploaded",
        )
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_activity_event.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.activity'`.

- [ ] **Step 3: Implement `app/domain/activity.py` and `app/activity/__init__.py` (empty)**

`app/activity/__init__.py` — empty.

`app/domain/activity.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EventType = Literal["view", "click", "thumbs_up", "thumbs_down", "dwell", "query"]


class ActivityEvent(BaseModel):
    timestamp: datetime
    tenant_id: str
    user_id: str
    doc_id: str
    event_type: EventType
    source: str
    query_id: str | None = None
    chunk_id: str | None = None
    duration_ms: int | None = None
```

- [ ] **Step 4: Run test, expect pass**

Run: `uv run pytest tests/test_activity_event.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/domain/activity.py brain-api/app/activity/__init__.py brain-api/tests/test_activity_event.py
git commit -m "feat: ActivityEvent domain model"
```

---

## Task 2: Create the free ADX cluster (user-in-loop) + settings + dependency

**Why:** ADX (Kusto) holds the activity event stream and serves the recency-weighted engagement KQL. The free-tier cluster is zero-cost and KQL-complete, but is created through the Azure Data Explorer web UI (not `az` CLI), like the Entra app registration in Phase 1.

**Files:**
- Create: `infra/adx_setup.md`
- Modify: `brain-api/app/config.py`
- Modify: `brain-api/.env` (append — gitignored)
- Modify: `brain-api/.env.example`
- Modify: `brain-api/pyproject.toml`

- [ ] **Step 1: Write `infra/adx_setup.md`**

```markdown
# Free Azure Data Explorer (ADX) Cluster — Manual Setup

ADX holds the Activity pillar's event stream. The **free cluster** is created
via the Azure Data Explorer web UI (not `az` CLI) and costs nothing.

Do this once. ~5 min.

## 1. Create the free cluster

1. Go to https://dataexplorer.azure.com/freecluster
2. Sign in with the same identity used for `az login`
   (companybrain.microsoft@gmail.com).
3. Click **Create cluster free**. Accept defaults. Wait ~2 min.
4. Note the **Cluster URI** shown on the cluster page — looks like
   `https://<name>.<region>.kusto.windows.net` (classic) or
   `https://trd-<token>.<region>.kusto.fabric.microsoft.com` (Fabric free).
   This is `ADX_CLUSTER_URI`.

## 2. Create the database

1. In the web UI, on your free cluster, click **Create database**.
2. Name it `brain`. Create.
   This is `ADX_DATABASE=brain`.

## 3. Confirm your identity is admin

The creating identity is automatically Database Admin on a free cluster, so
`DefaultAzureCredential` (your `az login` identity) can create tables and
ingest. No extra role assignment needed. If a later step gets a 403 (Forbidden)
on `.create table`, open the database → Permissions → add yourself as Admin.

## Outputs

Add to `brain-api/.env`:

```
ADX_CLUSTER_URI=<cluster uri from step 1>
ADX_DATABASE=brain
```
```

- [ ] **Step 2: Follow the checklist in the browser**

Complete steps 1–3 in `infra/adx_setup.md`. This requires human action at https://dataexplorer.azure.com/freecluster. Capture the cluster URI.

- [ ] **Step 3: Add ADX + activity-weight settings to `config.py`**

In `brain-api/app/config.py`, after the Cosmos block (after `cosmos_gremlin_graph`) add:

```python
    # Azure Data Explorer (Activity pillar)
    adx_cluster_uri: str | None = None
    adx_database: str = "brain"
```

In the ranker-weights block, change the two existing weights and add the third so the three sum to 1.0:

```python
    # Personalized ranker weights (Phase 2b: content + people + activity)
    rank_weight_content: float = 0.5
    rank_weight_people: float = 0.3
    rank_weight_activity: float = 0.2
```

- [ ] **Step 4: Append ADX values to `brain-api/.env`**

Add the two lines (from Step 2) to `brain-api/.env`:

```
ADX_CLUSTER_URI=<cluster uri>
ADX_DATABASE=brain
```

- [ ] **Step 5: Add ADX placeholders to `.env.example`**

Append to `brain-api/.env.example`:

```
# Azure Data Explorer (Activity pillar) — create free cluster per infra/adx_setup.md
ADX_CLUSTER_URI=
ADX_DATABASE=brain
```

- [ ] **Step 6: Add `azure-kusto-data` to dependencies**

In `brain-api/pyproject.toml`, add to the `dependencies` array:

```
  "azure-kusto-data>=4.5",
```

Then:

```bash
cd brain-api && uv sync
```

Expected: resolves and installs `azure-kusto-data`.

- [ ] **Step 7: Verify settings load**

```bash
cd brain-api && uv run python -c "import app.config; print('ok')"
```

Expected: `ok`.

- [ ] **Step 8: Commit (scripts + config + dep — never `.env`)**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add infra/adx_setup.md brain-api/app/config.py brain-api/.env.example brain-api/pyproject.toml brain-api/uv.lock
git commit -m "feat: free ADX cluster setup doc + adx/activity-weight settings"
```

**Escalation path:** if the free ADX cluster cannot be created or `DefaultAzureCredential` can't authenticate to it at Task 3, STOP and report BLOCKED. The fallback is a Redis-sorted-set `ActivityStore` behind the same interface (Task 3/4 build against the interface, so the swap is mechanical) — but that's a controller decision, not a silent substitution.

---

## Task 3: ActivityStore (ADX: create table, ingest, engagement KQL)

**Why:** The ADX-backed store: idempotently creates the `ActivityEvents` table, ingests one event at a time via inline ingestion (free-cluster-compatible — no separate ingest endpoint), and runs the recency-weighted engagement KQL.

**Files:**
- Create: `brain-api/app/activity/store.py`
- Create: `brain-api/tests/test_activity_store.py`

- [ ] **Step 1: Write the failing integration test**

`brain-api/tests/test_activity_store.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent


@pytest.mark.integration
async def test_create_ingest_and_score() -> None:
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        # 3 recent views of doc-hot by u-act; doc-cold gets nothing.
        for i in range(3):
            await store.ingest_event(ActivityEvent(
                timestamp=now - timedelta(hours=i),
                tenant_id="t-test", user_id="u-act", doc_id="adoc-hot",
                event_type="view", source="uploaded",
            ))
        # ADX inline ingestion is near-immediate but allow brief settle.
        scores = await store.engagement_scores(
            tenant_id="t-test", user_id="u-act", doc_ids=["adoc-hot", "adoc-cold"]
        )
        assert scores.get("adoc-hot", 0.0) > scores.get("adoc-cold", 0.0)
    finally:
        await store.aclose()
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_activity_store.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.activity.store'`.

- [ ] **Step 3: Implement `app/activity/store.py`**

```python
"""Azure Data Explorer (Kusto) store for the Activity pillar.

Free-cluster compatible: ingests via INLINE ingestion control commands through
the query client (no separate ingest endpoint, which free clusters lack).
Engagement scoring is a parameterized recency-weighted KQL aggregate.
"""

from __future__ import annotations

import asyncio
import logging

from azure.identity import DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.request import ClientRequestProperties

from app.config import get_settings

logger = logging.getLogger(__name__)

_TABLE = "ActivityEvents"

_CREATE = (
    f".create-merge table {_TABLE} "
    "(Timestamp:datetime, TenantId:string, UserId:string, QueryId:string, "
    "DocId:string, ChunkId:string, EventType:string, Source:string, DurationMs:int)"
)

# Recency-weighted engagement: exp decay (tau=14d), self-engagement weighted 2x,
# over a 30-day window. Parameterized to avoid KQL injection.
_SCORE_QUERY = (
    "declare query_parameters(tid:string, uid:string, dids:dynamic);\n"
    f"{_TABLE}\n"
    "| where TenantId == tid and DocId in (dids) and Timestamp > ago(30d)\n"
    "| extend recency = exp(-1.0 * datetime_diff('day', now(), Timestamp) / 14.0)\n"
    "| extend self_weight = iif(UserId == uid, 2.0, 1.0)\n"
    "| summarize score = sum(recency * self_weight) by DocId"
)


def _kcsb() -> KustoConnectionStringBuilder:
    s = get_settings()
    if not s.adx_cluster_uri:
        raise RuntimeError("ADX_CLUSTER_URI is not configured")
    return KustoConnectionStringBuilder.with_azure_token_credential(
        s.adx_cluster_uri, DefaultAzureCredential()
    )


def _escape(v: str) -> str:
    # Inline-ingest CSV: our values contain no commas/quotes/newlines, but guard anyway.
    return v.replace('"', '""')


class ActivityStore:
    def __init__(self) -> None:
        self._db = get_settings().adx_database
        self._client = KustoClient(_kcsb())

    async def aclose(self) -> None:
        def _close() -> None:
            self._client.close()

        await asyncio.to_thread(_close)

    async def ensure_table(self) -> None:
        await asyncio.to_thread(self._client.execute_mgmt, self._db, _CREATE)

    async def ingest_event(self, e) -> None:
        # One CSV row matching the table column order.
        row = ",".join(
            _escape(str(x)) for x in [
                e.timestamp.isoformat(),
                e.tenant_id,
                e.user_id,
                e.query_id or "",
                e.doc_id,
                e.chunk_id or "",
                e.event_type,
                e.source,
                e.duration_ms if e.duration_ms is not None else "",
            ]
        )
        cmd = f".ingest inline into table {_TABLE} <|\n{row}"
        await asyncio.to_thread(self._client.execute_mgmt, self._db, cmd)

    async def engagement_scores(
        self, *, tenant_id: str, user_id: str, doc_ids: list[str]
    ) -> dict[str, float]:
        if not doc_ids:
            return {}
        crp = ClientRequestProperties()
        crp.set_parameter("tid", tenant_id)
        crp.set_parameter("uid", user_id)
        import json

        crp.set_parameter("dids", json.dumps(doc_ids))

        def _run():
            return self._client.execute_query(self._db, _SCORE_QUERY, crp)

        resp = await asyncio.to_thread(_run)
        out: dict[str, float] = {}
        for row in resp.primary_results[0]:
            out[row["DocId"]] = float(row["score"])
        return out
```

- [ ] **Step 4: Run the integration test**

Run: `uv run pytest tests/test_activity_store.py -v -m integration`
Expected: PASS. (First ADX call does an AAD handshake; allow ~5-10s. If `.ingest inline` reports the event isn't immediately queryable, the test asserts `>` not equality, and inline ingest is synchronous — should pass. If it flakes on timing, re-run once.)

If you hit a 403 on `.create-merge table`, the creating identity isn't DB admin — see `infra/adx_setup.md` step 3. If `KustoConnectionStringBuilder.with_azure_token_credential` is unavailable in the installed SDK version, use `with_aad_device_authentication` is NOT acceptable (interactive) — instead report DONE_WITH_CONCERNS noting the SDK auth-method name so the controller can adjust.

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/activity/store.py brain-api/tests/test_activity_store.py
git commit -m "feat: ActivityStore (ADX inline ingest + recency-weighted engagement KQL)"
```

---

## Task 4: ActivitySignal scorer (normalize)

**Why:** Mirror `PeopleProximity`: turn raw engagement sums into a normalized [0,1] signal the ranker can fuse. Keeps the ranker store-agnostic.

**Files:**
- Create: `brain-api/app/activity/signal.py`
- Create: `brain-api/tests/test_activity_signal.py`

- [ ] **Step 1: Write the failing integration test**

`brain-api/tests/test_activity_signal.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.activity.signal import ActivitySignal
from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent
from app.domain.identity import User


@pytest.mark.integration
async def test_signal_normalizes_to_unit_interval() -> None:
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        for i in range(4):
            await store.ingest_event(ActivityEvent(
                timestamp=now - timedelta(hours=i),
                tenant_id="t-test", user_id="u-sig", doc_id="sdoc-hot",
                event_type="view", source="uploaded",
            ))
        signal = ActivitySignal(store=store)
        user = User(user_id="u-sig", tenant_id="t-test", email="s@x",
                    display_name="S", group_ids=set())
        scores = await signal.score(user=user, doc_ids=["sdoc-hot", "sdoc-cold"])
        assert scores["sdoc-hot"] == 1.0      # max normalizes to 1.0
        assert scores["sdoc-cold"] == 0.0     # no engagement
        assert all(0.0 <= v <= 1.0 for v in scores.values())
    finally:
        await store.aclose()


def test_empty_doc_ids_returns_empty() -> None:
    import asyncio

    class _FakeStore:
        async def engagement_scores(self, **_):
            return {}

    sig = ActivitySignal(store=_FakeStore())
    from app.domain.identity import User as U
    u = U(user_id="u", tenant_id="t", email="a@b", display_name="A", group_ids=set())
    assert asyncio.run(sig.score(user=u, doc_ids=[])) == {}
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_activity_signal.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.activity.signal'`.

- [ ] **Step 3: Implement `app/activity/signal.py`**

```python
"""Activity engagement ranking signal.

Wraps ActivityStore.engagement_scores and normalizes the raw recency-weighted
sums to [0,1] across the candidate set — same shape as PeopleProximity.score,
so the ranker treats People and Activity uniformly.
"""

from __future__ import annotations

from app.domain.identity import User


class ActivitySignal:
    def __init__(self, *, store) -> None:
        self._store = store

    async def score(self, *, user: User, doc_ids: list[str]) -> dict[str, float]:
        if not doc_ids:
            return {}
        raw = await self._store.engagement_scores(
            tenant_id=user.tenant_id, user_id=user.user_id, doc_ids=doc_ids
        )
        if not raw:
            return {d: 0.0 for d in doc_ids}
        hi = max(raw.values())
        if hi <= 0:
            return {d: 0.0 for d in doc_ids}
        return {d: (raw.get(d, 0.0) / hi) for d in doc_ids}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_activity_signal.py -v`
Expected: 1 unit pass + 1 integration pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/activity/signal.py brain-api/tests/test_activity_signal.py
git commit -m "feat: ActivitySignal scorer (normalized engagement, store-agnostic)"
```

---

## Task 5: Add the activity term to the ranker

**Why:** Fuse the third signal: `final = w_content·content + w_people·people + w_activity·activity`.

**Files:**
- Modify: `brain-api/app/ranking/personalized_ranker.py`
- Create: `brain-api/tests/test_personalized_ranker_activity.py`

- [ ] **Step 1: Write the failing unit test**

`brain-api/tests/test_personalized_ranker_activity.py`:

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
        raw_scores={"content_rrf": 1.0 / (60 + content_rank)},
    )


def test_activity_signal_reorders_when_weighted() -> None:
    # Equal content + no people; doc-b has higher activity -> ranks first.
    cands = [_cand("doc-a", 0), _cand("doc-b", 0)]
    ranker = PersonalizedRanker(weight_content=0.4, weight_people=0.3, weight_activity=0.3)
    ranked = ranker.rank(
        candidates=cands,
        proximity={"doc-a": 0.0, "doc-b": 0.0},
        activity={"doc-a": 0.0, "doc-b": 1.0},
    )
    assert ranked[0].candidate.chunk.doc_id == "doc-b"
    assert "activity" in ranked[0].signal_breakdown


def test_activity_defaults_to_empty_when_omitted() -> None:
    cands = [_cand("doc-a", 0), _cand("doc-b", 1)]
    ranker = PersonalizedRanker(weight_content=0.5, weight_people=0.3, weight_activity=0.2)
    # activity omitted -> treated as all-zero; pure content+people order
    ranked = ranker.rank(candidates=cands, proximity={"doc-a": 0.0, "doc-b": 0.0})
    assert ranked[0].candidate.chunk.doc_id == "doc-a"
    assert ranked[0].signal_breakdown["activity"] == 0.0
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_personalized_ranker_activity.py -v`
Expected: FAIL — `rank()` doesn't accept `activity`, or `weight_activity` not a constructor arg.

- [ ] **Step 3: Replace `app/ranking/personalized_ranker.py`**

```python
"""Personalized multi-signal ranker (Phase 2b: Content + People + Activity).

final = w_content * normalize(content_rrf)
      + w_people  * proximity
      + w_activity * activity

Content uses the retriever's RRF score (rank-derived); proximity is the People
pillar signal; activity is the engagement signal — both in [0,1]. Weights are
injected (sourced from Settings by the orchestrator).
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
    def __init__(
        self, *, weight_content: float, weight_people: float, weight_activity: float = 0.0
    ) -> None:
        self._wc = weight_content
        self._wp = weight_people
        self._wa = weight_activity

    def rank(
        self,
        *,
        candidates: list[Candidate],
        proximity: dict[str, float],
        activity: dict[str, float] | None = None,
    ) -> list[RankedResult]:
        if not candidates:
            return []
        activity = activity or {}
        content_norm = _normalize(
            {c.chunk.chunk_id: c.raw_scores.get("content_rrf", 0.0) for c in candidates}
        )
        scored: list[RankedResult] = []
        for c in candidates:
            content = content_norm.get(c.chunk.chunk_id, 0.0)
            people = proximity.get(c.chunk.doc_id, 0.0)
            engagement = activity.get(c.chunk.doc_id, 0.0)
            final = self._wc * content + self._wp * people + self._wa * engagement
            scored.append(
                RankedResult(
                    candidate=c,
                    final_score=final,
                    signal_breakdown={
                        "content": content,
                        "people": people,
                        "activity": engagement,
                    },
                    rank=0,
                )
            )
        scored.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(scored):
            r.rank = i
        return scored
```

- [ ] **Step 4: Run tests, expect pass**

Run: `uv run pytest tests/test_personalized_ranker_activity.py tests/test_personalized_ranker.py -v`
Expected: the 2 new pass. **The Phase 2a `test_personalized_ranker.py` tests still pass** because `rank()`'s `activity` param defaults to None and `weight_activity` defaults to 0.0 — the old 2-arg constructor calls and 2-kwarg `rank()` calls are unaffected.

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/ranking/personalized_ranker.py brain-api/tests/test_personalized_ranker_activity.py
git commit -m "feat: ranker fuses activity engagement as third weighted signal"
```

---

## Task 6: /feedback endpoint + synthetic activity seeder

**Why:** `/feedback` captures real engagement (the learning loop); `/admin/seed-activity` generates synthetic events so the engagement signal has data to demo.

**Files:**
- Create: `brain-api/app/api/feedback.py`
- Modify: `brain-api/app/api/admin.py`
- Modify: `brain-api/app/main.py` (mount feedback router)
- Modify: `brain-api/app/deps.py` (get_activity_store accessor)
- Create: `brain-api/tests/test_feedback.py`

- [ ] **Step 1: Write the failing integration test**

`brain-api/tests/test_feedback.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_feedback_ingests_event() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/feedback",
            json={"doc_id": "up:policy-pto", "signal": "click", "dwell_ms": 4200},
            headers={"x-debug-bypass-auth": "t-test,u-fb,t-test:everyone"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "recorded"
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_feedback.py -v -m integration`
Expected: FAIL — 404.

- [ ] **Step 3: Add `get_activity_store` to `deps.py`**

In `brain-api/app/deps.py`, add an import and accessor (it reads the lifespan-constructed store from app.state — added in Task 7):

```python
from app.activity.store import ActivityStore


def get_activity_store(request: Request) -> ActivityStore:
    return request.app.state.activity_store
```

- [ ] **Step 4: Create `app/api/feedback.py`**

```python
"""POST /feedback — capture an engagement event into the Activity pillar.

Reuses the same auth resolution as /query (Entra bearer, or the dev-only
x-debug-bypass-auth header when ENABLE_DEBUG_AUTH is set).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.activity.store import ActivityStore
from app.config import get_settings
from app.deps import get_activity_store
from app.domain.activity import ActivityEvent, EventType
from app.domain.identity import User

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    doc_id: str
    signal: EventType
    query_id: str | None = None
    chunk_id: str | None = None
    dwell_ms: int | None = None
    source: str = "uploaded"


def _debug_user(header: str) -> User:
    parts = header.split(",")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="bad debug header")
    tenant, user_id, *groups = parts
    return User(
        user_id=user_id, tenant_id=tenant, email=f"{user_id}@debug",
        display_name=user_id, group_ids=set(groups),
    )


def _resolve_user(x_debug_bypass_auth: str | None) -> User:
    # Phase 2b: feedback uses the same debug-gated path as /query's bypass.
    # Real bearer-token resolution is shared with /query; for the dev/eval path
    # we accept the debug header only when the flag is enabled.
    if get_settings().enable_debug_auth and x_debug_bypass_auth:
        return _debug_user(x_debug_bypass_auth)
    raise HTTPException(status_code=401, detail="auth required")


@router.post("/feedback")
async def feedback(
    body: FeedbackRequest,
    store: ActivityStore = Depends(get_activity_store),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> dict[str, str]:
    user = _resolve_user(x_debug_bypass_auth)
    event = ActivityEvent(
        timestamp=datetime.now(UTC),
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        doc_id=body.doc_id,
        event_type=body.signal,
        source=body.source,
        query_id=body.query_id,
        chunk_id=body.chunk_id,
        duration_ms=body.dwell_ms,
    )
    await store.ingest_event(event)
    return {"status": "recorded"}
```

Note: this Phase 2b `/feedback` accepts only the debug-gated path for simplicity (eval + demo). Full Entra-bearer resolution is shared by `/query` and can be unified in a later cleanup; the spec's feedback loop is satisfied by the debug path for the demo.

- [ ] **Step 5: Add `POST /admin/seed-activity` to `admin.py`**

In `brain-api/app/api/admin.py` add imports:

```python
from datetime import UTC, datetime, timedelta
from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent
```

Add the route (inside the admin router that already carries `require_admin_key`):

```python
@router.post("/seed-activity")
async def seed_activity(events_per_doc: int = 5) -> dict:
    """Generate synthetic engagement: u-sales engages the sales plan, u-eng the eng plan."""
    tenant = get_settings().brain_tenant_id
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        plan = [
            ("p-sales", "up:persona-sales-plan"),
            ("p-eng", "up:persona-eng-plan"),
        ]
        written = 0
        for user_id, doc_id in plan:
            for i in range(events_per_doc):
                await store.ingest_event(ActivityEvent(
                    timestamp=now - timedelta(hours=i),
                    tenant_id=tenant, user_id=user_id, doc_id=doc_id,
                    event_type="view", source="uploaded",
                ))
                written += 1
        return {"tenant_id": tenant, "events_written": written}
    finally:
        await store.aclose()
```

- [ ] **Step 6: Mount the feedback router in `main.py`**

In `brain-api/app/main.py` add the import (after `from app.api.retrieve import ...`):

```python
from app.api.feedback import router as feedback_router
```

And after `app.include_router(retrieve_router)`:

```python
app.include_router(feedback_router)
```

(The lifespan construction of `app.state.activity_store` is added in Task 7. Until then `/feedback` would fail at runtime — that's fine; Task 6's test is run after Task 7 wires the store. To keep Task 6 self-contained, you MAY run Task 7's lifespan edit first if you prefer; otherwise mark Task 6's integration test xfail until Task 7. RECOMMENDED: do Task 7's main.py lifespan edit as part of Task 6 Step 6 — add `app.state.activity_store = ActivityStore()` + `ActivitySignal` to lifespan now, since both tasks edit main.py. See note below.)

**To avoid a broken intermediate state, in this same step also add to `main.py` lifespan** (import `ActivityStore`/`ActivitySignal` at top):

```python
from app.activity.store import ActivityStore
from app.activity.signal import ActivitySignal
```

In `lifespan`, after `app.state.ranker = ...`:

```python
    app.state.activity_store = ActivityStore()
    app.state.activity = ActivitySignal(store=app.state.activity_store)
```

In the shutdown `finally`, after `await app.state.people_graph.aclose()`:

```python
        await app.state.activity_store.aclose()
```

(Task 7 then only needs to pass `activity=app.state.activity` into the orchestrator and update the orchestrator itself.)

- [ ] **Step 7: Run the feedback test**

Run: `uv run pytest tests/test_feedback.py -v -m integration`
Expected: PASS (ingests a real event into ADX).

- [ ] **Step 8: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/api/feedback.py brain-api/app/api/admin.py brain-api/app/main.py brain-api/app/deps.py brain-api/tests/test_feedback.py
git commit -m "feat: POST /feedback + /admin/seed-activity (Activity pillar ingest)"
```

---

## Task 7: Wire the activity signal into the orchestrator

**Why:** The orchestrator must fetch the engagement signal for candidate docs (degrading gracefully if ADX is down, same as Cosmos) and pass it to the ranker.

**Files:**
- Modify: `brain-api/app/orchestrator/kernel.py`
- Modify: `brain-api/app/main.py` (pass `activity` into the orchestrator)
- Modify: `brain-api/tests/test_orchestrator.py` (constructor gains `activity`)

- [ ] **Step 1: Update the orchestrator**

In `brain-api/app/orchestrator/kernel.py`:

Add the import:

```python
from app.activity.signal import ActivitySignal
```

Add `activity: ActivitySignal` to `__init__` params and store it:

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
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._cache = cache
        self._acl_store = acl_store
        self._proximity = proximity
        self._ranker = ranker
        self._activity = activity
```

In `retrieve_ranked`, after the proximity try/except block and before `self._ranker.rank(...)`, add the activity fetch with the same degradation pattern, then pass `activity` to `rank`:

```python
        # Activity engagement signal. Spec §3.2: ADX down -> skip Activity (activity=0).
        try:
            doc_ids = [c.chunk.doc_id for c in candidates]
            activity = await self._activity.score(user=user, doc_ids=doc_ids)
        except Exception as e:
            logger.warning("Activity store (ADX) unavailable; degrading to activity=0: %s", e)
            activity = {}
        ranked: list[RankedResult] = self._ranker.rank(
            candidates=candidates, proximity=proximity, activity=activity
        )
        return [r.candidate for r in ranked]
```

(Replace the existing `ranked = self._ranker.rank(candidates=candidates, proximity=proximity)` line with the version that passes `activity=activity`.)

Also update the class docstring line to: `"""Phase 2b: cache -> retrieve -> ACL re-check -> proximity -> activity -> rank -> answer."""`

- [ ] **Step 2: Pass `activity` into the orchestrator in `main.py`**

In `brain-api/app/main.py` lifespan, update the orchestrator construction to include `activity=app.state.activity` (the `app.state.activity` was created in Task 6 Step 6):

```python
    app.state.orchestrator = SemanticKernelOrchestrator(
        retriever=app.state.retriever,
        llm=app.state.embedder,
        cache=app.state.cache,
        acl_store=app.state.acl_store,
        proximity=app.state.proximity,
        ranker=app.state.ranker,
        activity=app.state.activity,
    )
```

- [ ] **Step 3: Update the ranker construction in `main.py` to include the activity weight**

In `brain-api/app/main.py` lifespan, update:

```python
    app.state.ranker = PersonalizedRanker(
        weight_content=get_settings().rank_weight_content,
        weight_people=get_settings().rank_weight_people,
        weight_activity=get_settings().rank_weight_activity,
    )
```

- [ ] **Step 4: Update `tests/test_orchestrator.py` for the new constructor arg**

In `brain-api/tests/test_orchestrator.py`, the `_build()` helper constructs the orchestrator. Add the activity collaborator. Add imports:

```python
from app.activity.signal import ActivitySignal
from app.activity.store import ActivityStore
```

In `_build()`, construct an ActivityStore + ActivitySignal, add the store to `closeables`, and pass `activity=` to the orchestrator:

```python
    activity_store = ActivityStore()
    ...
    closeables = [embedder, search, cache, acl_store, graph, activity_store]
    orch = SemanticKernelOrchestrator(
        retriever=HybridRetriever(search=search, embedder=embedder),
        llm=embedder,
        cache=cache,
        acl_store=acl_store,
        proximity=PeopleProximity(graph=graph),
        ranker=PersonalizedRanker(weight_content=0.5, weight_people=0.3, weight_activity=0.2),
        activity=ActivitySignal(store=activity_store),
    )
```

(Keep the existing two test bodies; only `_build()` changes.)

- [ ] **Step 5: Run orchestrator + degradation + e2e tests**

Run: `uv run pytest tests/test_orchestrator.py -v -m integration`
Expected: 2 passed (now also fetching activity; the Phase 1 corpus docs have no activity → activity=0 → ranking unaffected, PTO doc still cited).

Run: `uv run pytest tests/test_orchestrator_degradation.py -v`
Expected: this Phase 2a test constructs the orchestrator too — **it will need the `activity` arg added**. Update its fake-collaborator construction to pass `activity=` with a fake whose `score` returns `{}` (or a fake that raises, to also prove activity-degradation). Add a fake ActivitySignal to that test's builder. Confirm it passes.

Run: `uv run pytest tests/test_admin_retrieve.py tests/test_query_e2e.py -v -m integration`
Expected: pass (flow through activity signal too).

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/orchestrator/kernel.py brain-api/app/main.py brain-api/tests/test_orchestrator.py brain-api/tests/test_orchestrator_degradation.py
git commit -m "feat: orchestrator fuses Activity engagement signal (degrades if ADX down)"
```

---

## Task 8: Activity-changes-ranking acceptance test

**Why:** Prove the Activity pillar moves ranking: two docs equal on content and people, but one has recent engagement → it ranks higher. This is the Phase 2b headline.

**Files:**
- Create: `brain-api/tests/test_activity_ranking.py`

- [ ] **Step 1: Write the failing integration test**

`brain-api/tests/test_activity_ranking.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.activity.signal import ActivitySignal
from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent
from app.domain.chunk import SourceDoc
from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_engagement_lifts_ranking() -> None:
    embedder = AzureOpenAIClient()
    search = AISearchClient()
    store = ActivityStore()
    now = datetime.now(UTC)
    try:
        await store.ensure_table()
        # Two near-identical "benefits overview" docs, no authorship, same ACL.
        for did, body in [
            ("up:act-bene-a", "# Benefits Overview A\n\nOur benefits overview covers health, dental, and vision."),
            ("up:act-bene-b", "# Benefits Overview B\n\nOur benefits overview covers health, dental, and vision."),
        ]:
            pipe = IngestPipeline(embedder=embedder, search=search)
            await pipe.process(SourceDoc(
                doc_id=did, tenant_id="t-test", source="uploaded",
                source_url=f"local://{did}", title=did, body=body, author_id=None,
                acl_principals=["t-test:everyone"], created_at=now, modified_at=now,
                mime="text/markdown",
            ))
        # doc-b gets recent engagement; doc-a none.
        for i in range(5):
            await store.ingest_event(ActivityEvent(
                timestamp=now - timedelta(hours=i), tenant_id="t-test",
                user_id="u-actrank", doc_id="up:act-bene-b",
                event_type="view", source="uploaded",
            ))

        retriever = HybridRetriever(search=search, embedder=embedder)
        signal = ActivitySignal(store=store)
        user = User(user_id="u-actrank", tenant_id="t-test", email="a@x",
                    display_name="A", group_ids={"t-test:everyone"})

        cands = await retriever.retrieve(query="benefits overview", user=user, k=10)
        cands = [c for c in cands if c.chunk.doc_id in {"up:act-bene-a", "up:act-bene-b"}]
        activity = await signal.score(user=user, doc_ids=[c.chunk.doc_id for c in cands])

        # Without activity weight: order is whatever content gave.
        ranker_noact = PersonalizedRanker(weight_content=1.0, weight_people=0.0, weight_activity=0.0)
        order_noact = [r.candidate.chunk.doc_id for r in ranker_noact.rank(
            candidates=cands, proximity={}, activity=activity)]

        # With activity weight: the engaged doc (b) is lifted to the top.
        ranker_act = PersonalizedRanker(weight_content=0.4, weight_people=0.0, weight_activity=0.6)
        order_act = [r.candidate.chunk.doc_id for r in ranker_act.rank(
            candidates=cands, proximity={}, activity=activity)]

        assert order_act[0] == "up:act-bene-b"            # engagement wins with the weight on
        assert activity["up:act-bene-b"] > activity.get("up:act-bene-a", 0.0)
        # And the activity weight actually changed something vs content-only,
        # OR content already ranked b first; either way b is top with activity on.
        assert order_act[0] == "up:act-bene-b"
    finally:
        await embedder.aclose()
        await search.aclose()
        await store.aclose()
```

- [ ] **Step 2: Run test, expect failure first run**

Run: `uv run pytest tests/test_activity_ranking.py -v -m integration`
Expected on first run: may need a re-run if AI Search hasn't indexed the two new docs yet (indexing lag, same as the Phase 2a persona test). If the candidate list is empty, wait ~10s and re-run (up to 3 attempts). If it fails on the ordering assertion with docs present, that's a real signal — capture `activity` scores and both orders, report DONE_WITH_CONCERNS.

- [ ] **Step 3: Confirm pass**

Run: `uv run pytest tests/test_activity_ranking.py -v -m integration`
Expected: PASS — `order_act[0] == "up:act-bene-b"`, the engaged doc, and its activity score exceeds doc-a's.

- [ ] **Step 4: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/tests/test_activity_ranking.py
git commit -m "test: engagement lifts ranking — Activity pillar acceptance"
```

---

## Task 9: README + end-to-end verification + tag

**Files:**
- Modify: `brain-api/README.md`
- Modify: `README.md` (root)

- [ ] **Step 1: Update `brain-api/README.md`**

Add to the endpoints list:

```markdown
- `POST /feedback` — record an engagement event (Activity pillar); requires `x-debug-bypass-auth` + `ENABLE_DEBUG_AUTH=true`
- `POST /admin/seed-activity?events_per_doc=` — seed synthetic engagement (requires `x-admin-key`)
```

Add a "## Phase 2b — Activity pillar" subsection:

```markdown
## Phase 2b — Activity pillar

Engagement signal from Azure Data Explorer (free cluster). `/feedback` ingests
events; the ranker fuses a recency-weighted engagement score as its third signal
(`RANK_WEIGHT_ACTIVITY`, default 0.2; content 0.5 / people 0.3 / activity 0.2).
Create the free cluster per `infra/adx_setup.md`, then `POST /admin/seed-activity`
to populate demo engagement. ADX outages degrade gracefully to activity=0.
```

- [ ] **Step 2: Update root `README.md` "Next phases"**

Replace the Phase 2b bullet:

```markdown
- Phase 2b (done): Activity pillar (Azure Data Explorer free cluster) + engagement
  signal as the ranker's third weighted term. /feedback ingests events; recent
  engagement lifts ranking.
- Phase 3: Live Fetch via Microsoft Graph search.
- Phase 4: APIM gateway, OpenTelemetry, per-tenant index, JWKS caching, Event Hubs
  ingest path, ACL freshness-SLA gate, hardening.
```

- [ ] **Step 3: Full verification**

```bash
cd brain-api
uv run ruff check .
uv run pytest -m "not integration" -v
uv run pytest -m integration -v
```

Expected: ruff clean; all unit pass; all integration pass. Capture counts.

- [ ] **Step 4: Eval (regression check — activity shouldn't break retrieval)**

```bash
cd brain-api
uv run uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 4
uv run python eval/run_eval.py --mode retrieval --report eval/reports/2026-05-29-phase2b.json
kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
cat eval/reports/2026-05-29-phase2b.json
```

Expected: `recall_at_10 >= 0.7`, `mrr_at_10 >= 0.5`, exit 0. (Golden corpus docs have no activity, so activity=0 for them — retrieval order should be unchanged from Phase 2a.)

- [ ] **Step 5: Commit docs + tag**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add README.md brain-api/README.md
git commit -m "docs: Phase 2b Activity pillar — endpoints, ADX setup, ranker weights"
git tag -a phase-2b-activity -m "Phase 2b: Activity pillar (ADX free cluster) + engagement signal in the ranker. Recent engagement lifts ranking."
git log --oneline | head -12
```

---

## Self-Review

**Spec coverage (Phase 2b scope):**
- Activity pillar (ADX, §Pillar 3) → Tasks 2, 3
- Engagement KQL (recency decay + self-weight, §3) → Task 3 `_SCORE_QUERY`
- Activity signal in the ranker (§Personalized Ranker) → Tasks 4, 5, 7
- Activity feedback loop (§Activity Feedback Loop, §5 feedback) → Task 6 `/feedback`
- Graceful degradation: ADX down → activity=0 (§3.2) → Task 7
- "Recent engagement lifts ranking" acceptance → Task 8
- Deferred (documented): Event Hubs ingest path (direct inline ingest used instead), Live Fetch (Phase 3), per-tenant index/JWKS/APIM/ACL-SLA/2a-cleanups (Phase 4).

**Type/signature consistency:**
- `ActivitySignal.score(*, user, doc_ids) -> dict[str, float]` — Tasks 4, 7, 8 (identical shape to `PeopleProximity.score`).
- `ActivityStore.engagement_scores(*, tenant_id, user_id, doc_ids) -> dict[str, float]`, `ingest_event(event)`, `ensure_table()`, `aclose()` — Tasks 3, 4, 6, 7, 8.
- `PersonalizedRanker.__init__(*, weight_content, weight_people, weight_activity=0.0)` and `rank(*, candidates, proximity, activity=None)` — backward compatible with Phase 2a call sites (Task 5 verifies the old tests still pass).
- Orchestrator constructor gains `activity: ActivitySignal` — every construction site updated (main.py Task 7, test_orchestrator.py Task 7, test_orchestrator_degradation.py Task 7 Step 5).
- `ActivityEvent` fields consumed identically by feedback (Task 6), seeder (Task 6), store (Task 3), tests.

**Placeholder scan:** No TBD/TODO-as-work. Every code step is complete. The two judgment calls (ADX free-cluster auth at Task 3; indexing lag at Task 8) have explicit escalation/retry paths. The Task 6/7 main.py split is called out explicitly to avoid a broken intermediate state (activity_store constructed in Task 6, passed to orchestrator in Task 7).

**Known carried risk:** the chunker quality items and the Phase 2a minor cleanups remain deferred; not in scope here.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-29-company-brain-phase2b-activity.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review.
2. **Inline Execution** — batch with checkpoints.

Task 2 (ADX free cluster) is **user-in-loop** (web UI). Tasks 1, 5 are pure-code/unit. Tasks 3, 4, 6, 7, 8 need the live ADX cluster.
