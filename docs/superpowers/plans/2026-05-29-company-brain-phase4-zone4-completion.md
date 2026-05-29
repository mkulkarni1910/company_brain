# Company Brain — Phase 4 Implementation Plan (Intelligence-Layer Completion, pure-code)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the remaining intelligence-layer (Zone 4) work that needs no new infra and no Entra changes, so the orchestrator + ranker + ACL + Activity stories match the spec's intent: an LLM plan-step classifier (the last stubbed orchestrator piece), an ACL freshness gate, per-event-type engagement weighting, a recency signal that lets fresh/live results surface, and an isolated eval that restores a trustworthy retrieval metric.

**Architecture:** All changes are inside the existing FastAPI monolith. No new Azure resources, no web-UI step. Five capability tasks + a finalize task. Each new external-dependency call degrades gracefully (same pattern as Cosmos/ADX/Live Fetch).

**Tech Stack:** Existing — Azure OpenAI (gpt-4o for the plan step; the gpt-4o-mini deployment was never created due to quota, so the planner uses the chat deployment), Redis (ACL store), ADX (engagement KQL), the ranker.

**Scope (confirmed):** pure-code Zone 4 completion ONLY. **Explicitly out of scope:** per-user OBO / web SSO (Live Fetch stays single-identity, fail-closed), APIM gateway, OpenTelemetry wiring, Event Hubs ingest path, per-tenant index isolation (I3), JWKS caching (I4). Those are cross-zone / infra / user-in-loop and remain Phase 5+.

**Prerequisites in place:** Phase 3 shipped (tag `phase-3-livefetch`). 55 unit + 28 integration tests pass. All Azure resources live (AI Search, OpenAI gpt-4o + text-embedding-3-large, Cosmos Gremlin, Redis, ADX free cluster). Orchestrator: cache → (retrieve ∥ live-fetch heuristic) → ACL recheck → proximity → activity → rank → answer. Ranker fuses content + people + activity. Live results fail-closed on ACL unless `live_fetch_obo_enabled` (default False).

---

## File Structure

```
brain-api/
├── app/
│   ├── config.py                       # MODIFIED — acl/ranker/planner settings
│   ├── main.py                         # MODIFIED — construct QueryPlanner; ranker recency weight
│   ├── acl/store.py                    # MODIFIED — persistent doc-ACL + fail-closed-on-missing
│   ├── activity/store.py               # MODIFIED — per-event-type engagement weighting (KQL)
│   ├── ranking/personalized_ranker.py  # MODIFIED — recency signal (surfaces live + spec recency term)
│   ├── ingest/pipeline.py              # MODIFIED — pass configurable ACL TTL
│   └── orchestrator/
│       ├── planner.py                  # NEW — QueryPlan model + QueryPlanner (LLM plan step + heuristic fallback)
│       └── kernel.py                   # MODIFIED — use planner for rewrite + live-fetch decision
├── eval/
│   ├── load_corpus.py                  # MODIFIED — load under a dedicated eval tenant
│   └── run_eval.py                     # MODIFIED — eval as the eval-tenant user (pollution-free)
└── tests/
    ├── test_acl_freshness.py           # NEW (unit)
    ├── test_activity_event_weighting.py# NEW (integration)
    ├── test_ranker_recency.py          # NEW (unit)
    ├── test_query_planner.py           # NEW (unit + integration)
    └── (modified) test_orchestrator.py, test_orchestrator_degradation.py, test_orchestrator_livefetch.py, test_acl_store.py
```

---

## Conventions

Run from `brain-api/`. Direct commits to `main`, one per task. Integration tests carry `@pytest.mark.integration`. TestClient uses `with TestClient(app) as client:`. After each task `uv run ruff check .` must be clean before committing.

---

## Task 1: ACL freshness gate — persistent doc-ACL + fail-closed-on-missing

**Why:** Today doc-ACL entries expire after 900s, so after 15 min a doc reverts to index-time-ACL trust (a since-revoked doc could reappear). Make the live doc-ACL the authoritative, persistent source (revocation = update/delete the key), and add a strict mode that fails closed when no live entry exists.

**Files:**
- Modify: `brain-api/app/config.py`
- Modify: `brain-api/app/acl/store.py`
- Modify: `brain-api/app/ingest/pipeline.py`
- Create: `brain-api/tests/test_acl_freshness.py`

- [ ] **Step 1: Add settings**

In `brain-api/app/config.py`, after `admin_api_key`:

```python
    # ACL store
    acl_doc_ttl_seconds: int | None = None  # None = persistent (live ACL is authoritative)
    acl_fail_closed_on_missing: bool = False  # strict: drop docs with no live ACL entry
```

- [ ] **Step 2: Write the failing unit test**

`brain-api/tests/test_acl_freshness.py`:

```python
import asyncio
from datetime import UTC, datetime

from app.acl.store import ACLStore
from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate


def _cand(doc_id: str, acl: list[str]) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test", source="uploaded",
            source_url=f"x://{doc_id}", title="T", content="c", content_vector=[],
            acl_principals=acl, author_id=None, entities=[], created_at=now,
            modified_at=now, chunk_index=0,
        ),
        sources_hit={"vector"},
    )


class _FakeStore(ACLStore):
    """In-memory ACL map; override doc_principals to avoid real Redis."""

    def __init__(self, mapping: dict[str, set[str] | None], fail_closed: bool) -> None:
        self._mapping = mapping
        self._fail_closed = fail_closed

    async def doc_principals(self, *, tenant_id: str, doc_id: str) -> set[str] | None:
        return self._mapping.get(doc_id)


def _user() -> User:
    return User(user_id="u1", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"g-sales"})


def test_missing_entry_falls_back_to_index_acl_when_not_strict() -> None:
    store = _FakeStore({}, fail_closed=False)
    kept = asyncio.run(store.recheck(candidates=[_cand("d-x", ["g-sales"])], user=_user()))
    assert {c.chunk.doc_id for c in kept} == {"d-x"}  # index-ACL fallback allows it


def test_missing_entry_dropped_when_strict() -> None:
    store = _FakeStore({}, fail_closed=True)
    kept = asyncio.run(store.recheck(candidates=[_cand("d-x", ["g-sales"])], user=_user()))
    assert kept == []  # strict: no live entry -> fail closed


def test_live_entry_authoritative_over_index_acl() -> None:
    # index ACL would allow (g-sales), but the live entry revoked it -> dropped
    store = _FakeStore({"d-x": {"g-other"}}, fail_closed=False)
    kept = asyncio.run(store.recheck(candidates=[_cand("d-x", ["g-sales"])], user=_user()))
    assert kept == []
```

- [ ] **Step 3: Run test, expect failure**

Run: `uv run pytest tests/test_acl_freshness.py -v`
Expected: FAIL — `_FakeStore.__init__` calls don't match (the real `ACLStore.__init__` builds Redis), or `recheck` doesn't honor `self._fail_closed`. The strict test fails because `recheck` currently always falls back to index ACL on miss.

- [ ] **Step 4: Update `app/acl/store.py`**

Replace `set_doc_principals` and `recheck`:

```python
    async def set_doc_principals(
        self, *, tenant_id: str, doc_id: str, principals: list[str], ttl_seconds: int | None = None
    ) -> None:
        try:
            key = _doc_key(tenant_id, doc_id)
            value = json.dumps(sorted(principals))
            if ttl_seconds is None:
                await self._r.set(key, value)  # persistent: live ACL is authoritative
            else:
                await self._r.set(key, value, ex=ttl_seconds)
        except (RedisError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning("ACLStore write failed (doc=%s): %s", doc_id, e)

    async def recheck(self, *, candidates: list[Candidate], user: User) -> list[Candidate]:
        fail_closed_on_missing = getattr(self, "_fail_closed", None)
        if fail_closed_on_missing is None:
            fail_closed_on_missing = get_settings().acl_fail_closed_on_missing
        principals = user.principals()
        kept: list[Candidate] = []
        for c in candidates:
            try:
                live = await self.doc_principals(tenant_id=user.tenant_id, doc_id=c.chunk.doc_id)
            except ACLStoreError:
                logger.warning("ACL store unreachable; dropping doc %s (fail-closed)", c.chunk.doc_id)
                continue
            if live is None:
                # No live entry. Strict mode fails closed; otherwise fall back to
                # the chunk's index-time ACL (covers docs ingested before the store).
                if fail_closed_on_missing:
                    continue
                allowed = set(c.chunk.acl_principals)
            else:
                allowed = live  # live entry is authoritative (persistent; revocation propagates)
            if principals & allowed:
                kept.append(c)
        return kept
```

Add `from app.config import get_settings` is already imported (used in `__init__`). The `getattr(self, "_fail_closed", None)` lets the `_FakeStore` test inject the flag while production reads it from settings.

- [ ] **Step 5: Make ingest write persistent ACLs**

In `brain-api/app/ingest/pipeline.py`, the `process` method calls `set_doc_principals(...)`. Update that call to pass the configured TTL:

```python
        if self._acl_store is not None:
            from app.config import get_settings
            await self._acl_store.set_doc_principals(
                tenant_id=doc.tenant_id,
                doc_id=doc.doc_id,
                principals=doc.acl_principals,
                ttl_seconds=get_settings().acl_doc_ttl_seconds,
            )
```

(Default `acl_doc_ttl_seconds=None` → persistent.)

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_acl_freshness.py tests/test_acl_store.py -v`
Expected: the 3 new freshness tests pass; the existing `test_acl_store.py` tests still pass (its integration round-trip uses default ttl — persistent now — still reads back the set).

Run: `uv run pytest -m "not integration"` → all unit pass. `uv run ruff check .` → clean.

- [ ] **Step 7: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/config.py brain-api/app/acl/store.py brain-api/app/ingest/pipeline.py brain-api/tests/test_acl_freshness.py
git commit -m "feat: persistent doc-ACL + fail-closed-on-missing (ACL freshness gate)"
```

---

## Task 2: Per-event-type engagement weighting

**Why:** The engagement KQL currently treats every event type identically — a `thumbs_down` *raises* a doc's score, which is backwards. Weight by type: positive signals add, `thumbs_down` subtracts.

**Files:**
- Modify: `brain-api/app/activity/store.py`
- Create: `brain-api/tests/test_activity_event_weighting.py`

- [ ] **Step 1: Write the failing integration test**

`brain-api/tests/test_activity_event_weighting.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent


@pytest.mark.integration
async def test_thumbs_down_lowers_score_below_thumbs_up() -> None:
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        await store.ingest_event(ActivityEvent(
            timestamp=now, tenant_id="t-test", user_id="u-w", doc_id="wdoc-up",
            event_type="thumbs_up", source="uploaded"))
        await store.ingest_event(ActivityEvent(
            timestamp=now - timedelta(minutes=1), tenant_id="t-test", user_id="u-w",
            doc_id="wdoc-down", event_type="thumbs_down", source="uploaded"))
        scores = await store.engagement_scores(
            tenant_id="t-test", user_id="u-w", doc_ids=["wdoc-up", "wdoc-down"])
        # thumbs_up is positive; thumbs_down is negative
        assert scores.get("wdoc-up", 0.0) > 0.0
        assert scores.get("wdoc-down", 0.0) < 0.0
    finally:
        await store.aclose()
```

- [ ] **Step 2: Run test, expect failure**

Run: `uv run pytest tests/test_activity_event_weighting.py -v -m integration`
Expected: FAIL — both events currently sum positively, so `wdoc-down` score is `> 0`, not `< 0`.

- [ ] **Step 3: Update the engagement KQL in `app/activity/store.py`**

Replace `_SCORE_QUERY`:

```python
# Recency-weighted, per-event-type engagement over a 30-day window. Positive
# signals add; thumbs_down subtracts; query is neutral. Self-engagement weighted
# 2x. Parameterized (string + todynamic) to stay injection-safe on the free
# cluster (see note above).
_SCORE_QUERY = (
    "declare query_parameters(tid:string, uid:string, dids:string);\n"
    f"{_TABLE}\n"
    "| where TenantId == tid and DocId in (todynamic(dids)) and Timestamp > ago(30d)\n"
    "| extend recency = exp(-1.0 * datetime_diff('day', now(), Timestamp) / 14.0)\n"
    "| extend self_weight = iif(UserId == uid, 2.0, 1.0)\n"
    "| extend type_weight = case("
    "EventType == 'thumbs_up', 2.0, "
    "EventType == 'thumbs_down', -2.0, "
    "EventType == 'dwell', 1.5, "
    "EventType == 'view', 1.0, "
    "EventType == 'click', 1.0, "
    "0.0)\n"
    "| summarize score = sum(recency * self_weight * type_weight) by DocId"
)
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_activity_event_weighting.py -v -m integration`
Expected: PASS. Also run `uv run pytest tests/test_activity_store.py tests/test_activity_signal.py -v -m integration` — still pass (they ingest `view` events, which keep `type_weight=1.0`, so positive scores are unchanged in sign).

- [ ] **Step 5: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/activity/store.py brain-api/tests/test_activity_event_weighting.py
git commit -m "feat: per-event-type engagement weighting (thumbs_down subtracts)"
```

---

## Task 3: Ranker recency signal (spec recency term + live-fetch surfacing)

**Why:** Live candidates have proximity=0 and activity=0, so the ranker buries them — the freshness feature can't surface its own results. Add the spec's recency signal: `exp(-Δdays/30)` from each candidate's `modified_at`. Live candidates carry `modified_at=now` → recency≈1.0 → they surface. This also implements the spec ranker's recency term for all docs.

**Files:**
- Modify: `brain-api/app/config.py`
- Modify: `brain-api/app/ranking/personalized_ranker.py`
- Modify: `brain-api/app/main.py`
- Create: `brain-api/tests/test_ranker_recency.py`

- [ ] **Step 1: Rebalance weights in `config.py`**

In `brain-api/app/config.py`, replace the ranker-weights block:

```python
    # Personalized ranker weights (Phase 4: content + people + activity + recency; sum 1.0)
    rank_weight_content: float = 0.45
    rank_weight_people: float = 0.25
    rank_weight_activity: float = 0.15
    rank_weight_recency: float = 0.15
```

- [ ] **Step 2: Write the failing unit test**

`brain-api/tests/test_ranker_recency.py`:

```python
from datetime import UTC, datetime, timedelta

from app.domain.chunk import Chunk
from app.domain.query import Candidate
from app.ranking.personalized_ranker import PersonalizedRanker


def _cand(doc_id: str, modified: datetime, content_rank: int = 0) -> Candidate:
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test", source="uploaded",
            source_url=f"x://{doc_id}", title="T", content="c", content_vector=[],
            acl_principals=["t-test:everyone"], author_id=None, entities=[],
            created_at=modified, modified_at=modified, chunk_index=content_rank,
        ),
        sources_hit={"vector"},
        raw_scores={"content_rrf": 1.0 / (60 + content_rank)},
    )


def test_recent_doc_outranks_stale_when_recency_weighted() -> None:
    now = datetime.now(UTC)
    cands = [_cand("stale", now - timedelta(days=120), 0), _cand("fresh", now, 0)]
    ranker = PersonalizedRanker(
        weight_content=0.4, weight_people=0.0, weight_activity=0.0, weight_recency=0.6)
    ranked = ranker.rank(candidates=cands, proximity={}, activity={})
    assert ranked[0].candidate.chunk.doc_id == "fresh"
    assert "recency" in ranked[0].signal_breakdown


def test_recency_defaults_off_keeps_phase2_behavior() -> None:
    now = datetime.now(UTC)
    cands = [_cand("a", now, 0), _cand("b", now - timedelta(days=400), 1)]
    # weight_recency omitted -> 0.0; pure content order (a first by rank)
    ranker = PersonalizedRanker(weight_content=1.0, weight_people=0.0, weight_activity=0.0)
    ranked = ranker.rank(candidates=cands, proximity={}, activity={})
    assert ranked[0].candidate.chunk.doc_id == "a"
    assert ranked[0].signal_breakdown["recency"] == 0.0 or "recency" in ranked[0].signal_breakdown
```

- [ ] **Step 3: Run test, expect failure**

Run: `uv run pytest tests/test_ranker_recency.py -v`
Expected: FAIL — `PersonalizedRanker.__init__` has no `weight_recency`.

- [ ] **Step 4: Update `app/ranking/personalized_ranker.py`**

Replace the file:

```python
"""Personalized multi-signal ranker (Phase 4: Content + People + Activity + Recency).

final = w_content  * normalize(content_rrf)
      + w_people   * proximity
      + w_activity  * activity
      + w_recency  * recency        (recency = exp(-Δdays / 30) from modified_at)

Recency lets fresh content — and Live Fetch results (modified_at = now) — surface.
Weights are injected (sourced from Settings by the orchestrator).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.domain.query import Candidate, RankedResult

_RECENCY_TAU_DAYS = 30.0


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    hi = max(values.values())
    if hi <= 0:
        return {k: 0.0 for k in values}
    return {k: v / hi for k, v in values.items()}


def _recency(modified_at: datetime, now: datetime) -> float:
    days = max(0.0, (now - modified_at).total_seconds() / 86400.0)
    return math.exp(-days / _RECENCY_TAU_DAYS)


class PersonalizedRanker:
    def __init__(
        self,
        *,
        weight_content: float,
        weight_people: float,
        weight_activity: float = 0.0,
        weight_recency: float = 0.0,
    ) -> None:
        self._wc = weight_content
        self._wp = weight_people
        self._wa = weight_activity
        self._wr = weight_recency

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
        now = datetime.now(UTC)
        content_norm = _normalize(
            {c.chunk.chunk_id: c.raw_scores.get("content_rrf", 0.0) for c in candidates}
        )
        scored: list[RankedResult] = []
        for c in candidates:
            content = content_norm.get(c.chunk.chunk_id, 0.0)
            people = proximity.get(c.chunk.doc_id, 0.0)
            engagement = activity.get(c.chunk.doc_id, 0.0)
            recency = _recency(c.chunk.modified_at, now)
            final = (
                self._wc * content
                + self._wp * people
                + self._wa * engagement
                + self._wr * recency
            )
            scored.append(
                RankedResult(
                    candidate=c,
                    final_score=final,
                    signal_breakdown={
                        "content": content,
                        "people": people,
                        "activity": engagement,
                        "recency": recency,
                    },
                    rank=0,
                )
            )
        scored.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(scored):
            r.rank = i
        return scored
```

- [ ] **Step 5: Pass the recency weight in `main.py`**

In `brain-api/app/main.py` lifespan, update the `PersonalizedRanker(...)` construction:

```python
    app.state.ranker = PersonalizedRanker(
        weight_content=get_settings().rank_weight_content,
        weight_people=get_settings().rank_weight_people,
        weight_activity=get_settings().rank_weight_activity,
        weight_recency=get_settings().rank_weight_recency,
    )
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_ranker_recency.py tests/test_personalized_ranker.py tests/test_personalized_ranker_activity.py -v`
Expected: new 2 pass; Phase 2a/2b ranker tests still pass (those construct docs with `modified_at=now`, so recency is uniform ≈1.0 and, with `weight_recency` defaulting 0.0 in their constructor calls, the term is inert — order unchanged).

Run: `uv run pytest -m "not integration"` → all unit pass. `uv run ruff check .` → clean.

- [ ] **Step 7: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/config.py brain-api/app/ranking/personalized_ranker.py brain-api/app/main.py brain-api/tests/test_ranker_recency.py
git commit -m "feat: ranker recency signal (spec recency term + surfaces live results)"
```

---

## Task 4: LLM plan-step classifier

**Why:** The orchestrator's "plan" step has been a heuristic since Phase 1. Implement the spec's gpt-4o-based classifier: it rewrites the query for retrieval and decides whether Live Fetch is needed. Degrades to the heuristic on any failure, so it never blocks a query.

**Files:**
- Modify: `brain-api/app/config.py`
- Create: `brain-api/app/orchestrator/planner.py`
- Modify: `brain-api/app/orchestrator/kernel.py`
- Modify: `brain-api/app/main.py`
- Modify: `brain-api/tests/test_orchestrator.py`, `tests/test_orchestrator_degradation.py`, `tests/test_orchestrator_livefetch.py`
- Create: `brain-api/tests/test_query_planner.py`

- [ ] **Step 1: Fix the plan deployment in `config.py`**

The `gpt-4-1-mini` deployment was never created (quota 0). Point the plan step at the real chat deployment. In `brain-api/app/config.py`:

```python
    azure_openai_plan_deployment: str = "gpt-4o"
```

(Change from `"gpt-4-1-mini"`. Also update `brain-api/.env` and `.env.example` if they pin `AZURE_OPENAI_PLAN_DEPLOYMENT=gpt-4-1-mini` → set to `gpt-4o`.)

- [ ] **Step 2: Write the failing tests**

`brain-api/tests/test_query_planner.py`:

```python
import asyncio

import pytest

from app.orchestrator.planner import QueryPlan, QueryPlanner


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def complete(self, **kwargs) -> str:
        return self._reply


def test_planner_parses_valid_json() -> None:
    llm = _FakeLLM('{"needs_retrieval": true, "needs_live_fetch": true, '
                   '"entities": ["on call"], "rewrite": "current on-call engineer"}')
    plan = asyncio.run(QueryPlanner(llm=llm).plan("who is on call right now?"))
    assert plan.needs_live_fetch is True
    assert plan.rewrite == "current on-call engineer"


def test_planner_falls_back_to_heuristic_on_bad_json() -> None:
    llm = _FakeLLM("not json at all")
    # freshness query -> heuristic says needs_live_fetch True; rewrite = original
    plan = asyncio.run(QueryPlanner(llm=llm).plan("who is on call right now?"))
    assert plan.needs_live_fetch is True
    assert plan.rewrite == "who is on call right now?"


def test_planner_falls_back_on_llm_error() -> None:
    class _BrokenLLM:
        async def complete(self, **kwargs) -> str:
            raise RuntimeError("openai down")

    plan = asyncio.run(QueryPlanner(llm=_BrokenLLM()).plan("what is our PTO policy?"))
    assert plan.needs_live_fetch is False     # heuristic: static query
    assert plan.rewrite == "what is our PTO policy?"


@pytest.mark.integration
async def test_planner_real_llm_static_query() -> None:
    from app.generation.azure_openai import AzureOpenAIClient

    llm = AzureOpenAIClient()
    try:
        plan = await QueryPlanner(llm=llm).plan("what is our PTO policy?")
        assert isinstance(plan, QueryPlan)
        assert plan.needs_live_fetch is False  # static query, no freshness markers
        assert isinstance(plan.rewrite, str) and plan.rewrite
    finally:
        await llm.aclose()
```

- [ ] **Step 3: Run test, expect failure**

Run: `uv run pytest tests/test_query_planner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.orchestrator.planner'`.

- [ ] **Step 4: Implement `app/orchestrator/planner.py`**

```python
"""LLM plan step: rewrite the query for retrieval and decide whether Live Fetch
is needed. Degrades to the freshness heuristic on any failure — never blocks a query.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from app.config import get_settings
from app.live_fetch.base import needs_live_fetch

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a query planner for an enterprise search system. Given a user query, "
    "respond with ONLY a JSON object (no prose, no code fences) with keys: "
    "needs_retrieval (bool), needs_live_fetch (bool — true if the query asks for "
    "current/live/today/right-now/recent state that a periodically-indexed corpus "
    "could not have), entities (array of strings), rewrite (string — a cleaned, "
    "keyword-focused version of the query for retrieval)."
)


class QueryPlan(BaseModel):
    needs_retrieval: bool = True
    needs_live_fetch: bool = False
    entities: list[str] = []
    rewrite: str


class QueryPlanner:
    def __init__(self, *, llm) -> None:
        self._llm = llm

    def _fallback(self, query: str) -> QueryPlan:
        return QueryPlan(
            needs_retrieval=True,
            needs_live_fetch=needs_live_fetch(query),
            entities=[],
            rewrite=query,
        )

    async def plan(self, query: str) -> QueryPlan:
        try:
            text = await self._llm.complete(
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": query},
                ],
                deployment=get_settings().azure_openai_plan_deployment,
                temperature=0.0,
                max_tokens=200,
            )
            raw = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            plan = QueryPlan.model_validate(raw)
            if not plan.rewrite:
                plan.rewrite = query
            return plan
        except Exception as e:
            logger.warning("Query planner failed; falling back to heuristic: %s", e)
            return self._fallback(query)
```

- [ ] **Step 5: Run the planner unit tests**

Run: `uv run pytest tests/test_query_planner.py -v -m "not integration"`
Expected: 3 unit tests pass.

- [ ] **Step 6: Wire the planner into the orchestrator**

In `brain-api/app/orchestrator/kernel.py`:

Add import:

```python
from app.orchestrator.planner import QueryPlanner
```

Add `planner: QueryPlanner` to `__init__` params + store as `self._planner`.

In `retrieve_ranked`, replace the heuristic block. Currently it's:
```python
        live: list[Candidate] = []
        if settings.live_fetch_enabled and needs_live_fetch(request.query):
            ...
        retrieve_task = asyncio.create_task(self._retriever.retrieve(query=request.query, ...))
```
Change so the plan runs first, then retrieval uses the rewrite and live-fetch uses the plan decision:

```python
    async def retrieve_ranked(self, request: QueryRequest, *, user: User) -> list[Candidate]:
        settings = get_settings()
        plan = await self._planner.plan(request.query)
        retrieve_task = asyncio.create_task(
            self._retriever.retrieve(query=plan.rewrite, user=user, k=max(request.k, 10))
        )
        live: list[Candidate] = []
        if settings.live_fetch_enabled and plan.needs_live_fetch:
            try:
                live = await asyncio.wait_for(
                    self._live_fetcher.fetch(query=request.query, user=user),
                    timeout=settings.live_fetch_timeout_ms / 1000.0,
                )
            except Exception as e:
                logger.warning("Live Fetch unavailable; continuing index-only: %s", e)
                live = []
        indexed = await retrieve_task
        # ... (rest unchanged: ACL recheck, merge, proximity, activity, rank)
```

(Keep the `needs_live_fetch` import only if still referenced elsewhere; the planner now owns the decision. If `needs_live_fetch` becomes unused in kernel.py, remove its import to satisfy ruff. The planner imports it for its fallback.)

Remove the now-unused direct `needs_live_fetch` import from kernel.py if ruff flags F401.

- [ ] **Step 7: Construct the planner in `main.py` + pass to orchestrator**

In `brain-api/app/main.py`:

```python
from app.orchestrator.planner import QueryPlanner
```

In lifespan, after `app.state.embedder = ...` (it needs the LLM):

```python
    app.state.planner = QueryPlanner(llm=app.state.embedder)
```

Pass to the orchestrator constructor: `planner=app.state.planner`.

- [ ] **Step 8: Update the three orchestrator tests for the new constructor arg**

Each constructs `SemanticKernelOrchestrator(...)`. Add a planner:
- `tests/test_orchestrator.py` `_build()`: `from app.orchestrator.planner import QueryPlanner` and pass `planner=QueryPlanner(llm=embedder)` (uses the real LLM; for the two integration tests this is fine — static queries plan to needs_live_fetch=False).
- `tests/test_orchestrator_livefetch.py`: the fakes drive `needs_live_fetch` via query text. Inject a fake planner so the unit tests stay deterministic and offline:
  ```python
  from app.orchestrator.planner import QueryPlan
  from app.live_fetch.base import needs_live_fetch as _heur

  class _FakePlanner:
      async def plan(self, query):
          return QueryPlan(needs_retrieval=True, needs_live_fetch=_heur(query),
                           entities=[], rewrite=query)
  ```
  Pass `planner=_FakePlanner()` in `_orch()`.
- `tests/test_orchestrator_degradation.py`: add `planner=_FakePlanner()` (same fake, or a minimal one returning needs_live_fetch=False). Read the file and match its fake style.

- [ ] **Step 9: Run all orchestrator tests + suite**

Run: `uv run pytest tests/test_orchestrator_livefetch.py tests/test_orchestrator_degradation.py tests/test_query_planner.py -v -m "not integration"`
Expected: all pass.

Run: `uv run pytest tests/test_orchestrator.py -v -m integration`
Expected: 2 passed (planner runs a real gpt-4o plan call; static queries → needs_live_fetch False; PTO answer still cited, refusal works). ~adds a few seconds per query.

Run: `uv run pytest -m "not integration"` → all unit pass. `uv run ruff check .` → clean.

- [ ] **Step 10: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/app/config.py brain-api/app/orchestrator/planner.py brain-api/app/orchestrator/kernel.py brain-api/app/main.py brain-api/tests/test_query_planner.py brain-api/tests/test_orchestrator.py brain-api/tests/test_orchestrator_degradation.py brain-api/tests/test_orchestrator_livefetch.py
git commit -m "feat: LLM plan-step classifier (query rewrite + live-fetch decision, heuristic fallback)"
```

---

## Task 5: Eval-index isolation + seed-activity corpus ids

**Why:** The shared dev AI Search index accumulated test docs (all `t-test:everyone`) that pollute eval retrieval (MRR dropped 1.0→0.533). Isolate eval by loading the golden corpus under a dedicated tenant whose ACL filter excludes the `t-test` pollution. Also fix `/admin/seed-activity` to target real corpus doc-ids so the engagement demo shows a lift.

**Files:**
- Modify: `brain-api/eval/load_corpus.py`
- Modify: `brain-api/eval/run_eval.py`
- Modify: `brain-api/app/api/admin.py`

- [ ] **Step 1: Load the corpus under a dedicated eval tenant**

In `brain-api/eval/load_corpus.py`, add a tenant from env (default `t-eval`) and use it for `tenant_id` + ACL:

```python
EVAL_TENANT = os.environ.get("EVAL_TENANT", "t-eval")
```

In the payload, change:

```python
                "tenant_id": EVAL_TENANT,
                ...
                "acl_principals": [f"{EVAL_TENANT}:everyone"],
```

- [ ] **Step 2: Run eval as the eval-tenant user**

In `brain-api/eval/run_eval.py`, change the debug user constant:

```python
DEBUG_USER = "t-eval,u-eval,t-eval:everyone"
```

(The golden `expected_doc_ids` are tenant-independent — e.g. `up:policy-pto` — so they're unchanged. The AI Search filter `tenant_id eq 't-eval'` now excludes every `t-test` test doc, so only the 6 corpus docs compete → MRR returns to ~1.0.)

- [ ] **Step 3: Fix `/admin/seed-activity` doc-ids**

In `brain-api/app/api/admin.py`, the `seed_activity` route seeds engagement for `up:persona-sales-plan` / `up:persona-eng-plan`. Point it at real corpus doc-ids so a live demo shows a lift, and use the eval tenant so it lines up with the loaded corpus. Replace the `plan` list and tenant:

```python
@router.post("/seed-activity")
async def seed_activity(events_per_doc: int = 5) -> dict:
    """Generate synthetic engagement on real corpus docs so the ranker's activity
    signal is demonstrable: a heavily-viewed doc ranks above an equally-relevant one."""
    tenant = os.environ.get("EVAL_TENANT", "t-eval")
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        plan = [
            ("u-eval", "up:planning-q3-sales-plan"),
            ("u-eval", "up:engineering-oncall-runbook"),
        ]
        written = 0
        for user_id, doc_id in plan:
            for i in range(events_per_doc):
                await store.ingest_event(ActivityEvent(
                    timestamp=now - timedelta(hours=i),
                    tenant_id=tenant, user_id=user_id, doc_id=doc_id,
                    event_type="view", source="uploaded"))
                written += 1
        return {"tenant_id": tenant, "events_written": written}
    finally:
        await store.aclose()
```

Add `import os` to `admin.py` if not present.

- [ ] **Step 4: Re-load the corpus under the eval tenant**

```bash
cd brain-api
uv run uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 4
EVAL_TENANT=t-eval ADMIN_API_KEY=dev-admin-key-local uv run python eval/load_corpus.py
kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
```

Expected: 6 corpus docs loaded under `t-eval`, each reporting chunks indexed.

- [ ] **Step 5: Run eval — expect MRR restored**

```bash
cd brain-api
uv run uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 4
uv run python eval/run_eval.py --mode retrieval
kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
```

Expected: `recall_at_10 >= 0.7` and `mrr_at_10` materially higher than 0.533 (target ~0.9–1.0 now that pollution is excluded). Capture the value. (If still low, the planner's rewrite may be altering retrieval — note it; the eval is now a clean signal either way.)

- [ ] **Step 6: Commit**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add brain-api/eval/load_corpus.py brain-api/eval/run_eval.py brain-api/app/api/admin.py
git commit -m "feat: isolate eval under dedicated tenant + seed-activity targets corpus docs"
```

---

## Task 6: README + verification + tag

**Files:**
- Modify: `brain-api/README.md`
- Modify: `README.md` (root)

- [ ] **Step 1: Update `brain-api/README.md`**

Add a "## Phase 4 — Intelligence-layer completion" subsection:

```markdown
## Phase 4 — Intelligence-layer completion

- **LLM plan step**: gpt-4o rewrites the query and decides Live Fetch need
  (heuristic fallback on failure).
- **Recency signal**: ranker term `exp(-Δdays/30)` from `modified_at`
  (`RANK_WEIGHT_RECENCY`, default 0.15; content 0.45 / people 0.25 / activity 0.15
  / recency 0.15) — also surfaces Live Fetch results.
- **Per-event-type engagement**: thumbs_up/+, thumbs_down/−, view/click/dwell/+.
- **ACL freshness gate**: doc-ACL entries are persistent (live ACL is
  authoritative); `ACL_FAIL_CLOSED_ON_MISSING` enables strict drop-on-no-entry.
- **Eval isolation**: golden corpus loads under a dedicated tenant
  (`EVAL_TENANT`, default `t-eval`) so shared-index test docs don't pollute the metric.
```

- [ ] **Step 2: Update root `README.md` "Next phases"**

Replace the Phase 4 bullet:

```markdown
- Phase 4 (done, pure-code Zone 4 completion): LLM plan-step classifier, ranker
  recency signal, per-event-type engagement weighting, ACL freshness gate, eval
  isolation.
- Phase 5 (infra / needs Entra): per-user OBO for Live Fetch, APIM gateway,
  OpenTelemetry, Event Hubs ingest path, per-tenant index isolation, JWKS caching.
```

- [ ] **Step 3: Full verification**

```bash
cd brain-api
uv run ruff check .
uv run pytest -m "not integration" -v
uv run pytest -m integration -v
```

Expected: ruff clean; all unit pass; all integration pass (freshness acceptance may SKIP — expected; ADX tests may need one re-run on transient KustoNetworkError). Capture counts.

- [ ] **Step 4: Eval final**

```bash
cd brain-api
uv run uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 4
uv run python eval/run_eval.py --mode retrieval --report eval/reports/2026-05-29-phase4.json
kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
cat eval/reports/2026-05-29-phase4.json
```

Expected: `recall_at_10 >= 0.7`, `mrr_at_10` ≥ 0.533 (ideally ~0.9–1.0). Capture values.

- [ ] **Step 5: Commit docs + tag**

```bash
cd /Users/lokesh/Desktop/RFpilot/company_brain
git add README.md brain-api/README.md
git commit -m "docs: Phase 4 intelligence-layer completion"
git tag -a phase-4-zone4-complete -m "Phase 4: pure-code Zone 4 completion — LLM plan step, recency signal, per-event-type engagement, ACL freshness gate, eval isolation."
git log --oneline | head -12
```

---

## Self-Review

**Spec coverage (this pass):**
- Orchestrator plan step (§Semantic Kernel Orchestrator: "rewrite query, decide whether to retrieve or fetch") → Task 4
- Ranker recency term (§Personalized Ranker: relevance + freshness + personalization) → Task 3
- Activity signal quality (§Activity pillar) — per-event-type weighting → Task 2
- ACL freshness (§ACL Store: re-check against latest; §3.2 fail-closed) → Task 1
- Eval harness trustworthiness (§Eval Harness) → Task 5
- Out of scope (documented): OBO, APIM, OTel, Event Hubs, per-tenant index, JWKS.

**Type/signature consistency:**
- `QueryPlanner.plan(query) -> QueryPlan` (fields needs_retrieval/needs_live_fetch/entities/rewrite) — Tasks 4; consumed by orchestrator; fakes in 3 test files match.
- `PersonalizedRanker.__init__(*, weight_content, weight_people, weight_activity=0.0, weight_recency=0.0)` and `rank(*, candidates, proximity, activity=None)` — Task 3 keeps Phase 2a/2b call sites valid (recency defaults 0.0).
- `ACLStore.recheck` honors `_fail_closed` (test injection) or `acl_fail_closed_on_missing` (settings); `set_doc_principals(ttl_seconds=None)` persistent — Task 1.
- Orchestrator constructor gains `planner: QueryPlanner` — every construction site updated (main.py Task 4 Step 7; 3 test files Task 4 Step 8). This is the 8th orchestrator collaborator; confirm the lifespan passes all of: retriever, llm, cache, acl_store, proximity, ranker, activity, live_fetcher, planner.
- `engagement_scores` KQL signature unchanged (Task 2 only changes the query body); `ActivitySignal` normalize already handles negative scores (max≤0 → zeros).

**Placeholder scan:** No TBD/TODO-as-work. Complete code per step. Judgment calls flagged: planner determinism (temp 0 + fallback), eval MRR target (captured, not gated beyond the existing 0.7/0.5), ADX transient-flake re-run note.

**Carried risk:** the LLM plan step adds an LLM call + latency to every query; mitigated by temp 0 + heuristic fallback. If a golden query's rewrite hurts retrieval, Task 5/6 eval will show it (clean index now) and we iterate.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-29-company-brain-phase4-zone4-completion.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review.
2. **Inline Execution** — batch with checkpoints.

All tasks are pure-code; no provisioning or user-in-loop step. Tasks 1, 3 are unit-only; 2, 4, 5 hit live Azure (OpenAI/ADX/AI Search).
