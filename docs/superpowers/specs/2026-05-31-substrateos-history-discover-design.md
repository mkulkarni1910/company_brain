# History & Discover — Design Spec

**Date:** 2026-05-31
**Status:** Approved (brainstorming)
**Surfaces:** SubstrateOS web chat left-rail (`Ask` exists; `Discover` and `History` are dead links to be built)

## Goal

Turn the two placeholder left-rail nav items into working surfaces:

- **History** — per-user list of past questions; clicking one **re-runs it live** against the brain.
- **Discover** — tenant-wide **trending documents** and **activity-by-source**, powered by the existing ADX Activity pillar, always **ACL-filtered** to what the signed-in user can access.

No new Azure resources. Reuses Redis (Azure Cache for Redis), ADX (`ActivityEvents`), AI Search, and the ACL filter.

## Non-Goals

- Popular-questions / "suggested for you" Discover modes (deferred).
- Storing or replaying full prior answers (History is re-run-live, lightweight).
- Cross-user history or admin analytics.

## Architecture

```
History   →  Redis list per user            (write-on-query; read on demand)
Discover  →  ADX aggregation + AI Search     (trending docs + activity-by-source, ACL-filtered; ~5-min Redis cache)
```

Both expose a GET endpoint resolved through the existing `resolve_user` (Easy Auth → Bearer → debug), so they are per-identity and ACL-correct by construction.

## Backend Components

### Domain
`app/domain/history.py`

```python
class HistoryEntry(BaseModel):
    query: str
    query_id: str
    ts: datetime
```

`app/domain/discover.py`

```python
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

### HistoryStore — `app/history/store.py`
Redis-backed, keyed `history:{tenant_id}:{user_id}`.

- `add(user, query, query_id)` → `LPUSH` JSON `{query, query_id, ts}`, then `LTRIM 0 49` (cap 50). No TTL (history persists). Best-effort: swallow Redis errors so a query never fails because history write failed.
- `recent(user, limit=50)` → `LRANGE 0 limit-1`, parse JSON → `list[HistoryEntry]`. On Redis error → `[]`.

Reuses the existing Redis connection (same client construction as `RedisCache`; inject the client/host config).

### ActivityStore additions — `app/activity/store.py`
Two new KQL methods, injection-safe in the same style as `engagement_scores` (declared `query_parameters`, `string`+`todynamic` where needed). Reuse the existing per-event-type weight `case` expression (factor it into a shared KQL fragment constant `_TYPE_WEIGHT`).

- `trending(*, tenant_id, window_days, limit)` → `dict[doc_id, score]` for the top docs:

```kusto
declare query_parameters(tid:string, win:int, lim:int);
ActivityEvents
| where TenantId == tid and Timestamp > ago(win * 1d)
| extend recency = exp(-1.0 * datetime_diff('day', now(), Timestamp) / 14.0)
| extend type_weight = case(EventType=='thumbs_up',2.0, EventType=='thumbs_down',-2.0,
                            EventType=='dwell',1.5, EventType=='view',1.0, EventType=='click',1.0, 0.0)
| summarize score = sum(recency * type_weight), events = count() by DocId
| where score > 0
| top lim by score desc
```

- `source_breakdown(*, tenant_id, window_days, doc_ids)` → `list[(source, events, score)]`. Filtered to `doc_ids` (the user's accessible trending set) so the breakdown respects ACL:

```kusto
declare query_parameters(tid:string, win:int, dids:string);
ActivityEvents
| where TenantId == tid and Timestamp > ago(win * 1d) and DocId in (todynamic(dids))
| extend type_weight = case(EventType=='thumbs_up',2.0, EventType=='thumbs_down',-2.0,
                            EventType=='dwell',1.5, EventType=='view',1.0, EventType=='click',1.0, 0.0)
| summarize score = sum(type_weight), events = count() by Source
| top 6 by score desc
```

Both wrapped to return empty on ADX failure (degradation; do not raise).

### AISearchClient addition — `app/retrieval/ai_search_client.py`
`lookup_docs(*, doc_ids, user) -> dict[doc_id, Chunk]`: a filter-only search (`search_text="*"`) with filter `tenant_id eq '<u.tenant>' and (acl filter for u.principals()) and search.in(doc_id, '<ids>', ',')`, selecting `doc_id, title, source, source_url, content`; dedupe to the first chunk per `doc_id`. Returns only docs the user can access — this is the ACL enforcement point for Discover.

### DiscoverService — `app/discover/service.py`
Depends on `ActivityStore`, `AISearchClient`, and `RedisCache` (for caching).

- `result(user, window_days=14, limit=8) -> DiscoverResult`:
  1. Redis cache key `discover:{tenant}:{user}` (5-min TTL) — return on hit.
  2. `ids_scores = activity.trending(tenant, window_days, limit*2)` (over-fetch to survive ACL filtering).
  3. `docs = search.lookup_docs(doc_ids=list(ids_scores), user=user)` (ACL filter).
  4. Build `TrendingDoc` list for the accessible docs, ordered by ADX score, truncated to `limit`; snippet = first ~160 chars of content.
  5. `by_source = activity.source_breakdown(tenant, window_days, doc_ids=accessible_ids)`.
  6. Cache + return. Any sub-failure degrades to empty sections (never 500).

### Endpoints
- `app/api/history.py` — `GET /history?limit=50` → `resolve_user` → `history_store.recent(user, limit)` → `list[HistoryEntry]`.
- `app/api/discover.py` — `GET /discover` → `resolve_user` → `discover_service.result(user)` → `DiscoverResult`.
- `app/api/query.py` — after building the answer, call `history_store.add(user, request.query, answer.query_id)` (fire-and-forget; failure logged, not raised).
- `app/api/feedback.py` — already ingests thumbs events; extend the accepted `signal`/event path so a **`click`** event (doc_id, source) can be logged when a user opens a citation. (Reuse existing `ActivityEvent` ingest; `event_type="click"`.)

### Wiring — `app/main.py`
Construct `HistoryStore` and `DiscoverService` in lifespan (both need the Redis client; Discover also needs `ActivityStore` + `AISearchClient`). Attach to app state alongside the existing collaborators. All optional: if Redis/ADX unconfigured, the surfaces return empty rather than failing.

### Demo data
For the pilot tenant `t-eval`, seed a spread of `view`/`click`/`thumbs_up` activity across several corpus docs (via the existing `/admin/seed-activity`) so Discover renders meaningfully in the live demo.

## Frontend

### `web/lib/api.ts`
- `getHistory(): Promise<HistoryEntry[]>` — `GET /history` with auth headers.
- `getDiscover(): Promise<DiscoverResult>` — `GET /discover` with auth headers.
- `logClick(doc_id, source, query_id?)` — best-effort POST to `/feedback` with `signal: "click"` (or a dedicated field), used when a citation is opened.
- Add matching TS types (`HistoryEntry`, `TrendingDoc`, `SourceActivity`, `DiscoverResult`).

### `web/components/Chat.tsx`
- Add `view` state: `"ask" | "discover" | "history"`. Left-rail items become buttons that set `view` and carry the `active` class accordingly.
- **Ask view**: unchanged (composer, thread, right rail).
- **History view**: fetch on entry; list past questions with relative timestamps ("2h ago"). Clicking an item sets `view="ask"` and calls `ask(query)` (re-run live). Empty state when no history.
- **Discover view**: fetch on entry; "Trending this week" cards (title, source chip, snippet, a thin engagement bar from `score`) — clicking opens `source_url` (and `logClick`). Below: "Activity by source" mini bar list. Empty/degraded state when ADX has no data.
- Right rail ("Why this ranked") renders only in Ask view.
- Citation cards in answers call `logClick` on open.
- New CSS for history list + discover cards in `globals.css`, matching the existing light aesthetic (Fraunces/Archivo, amber accent).

## ACL & Privacy
- History is per `user_id` — a user only ever sees their own questions.
- Discover trending docs pass through `AISearchClient.lookup_docs` **with the user's tenant + ACL filter**, so engagement can never reveal a document the user cannot open. Source breakdown is computed only over that accessible doc set.

## Error Handling / Degradation
- History write is fire-and-forget; a failure never fails the query.
- `/history` and `/discover` catch store/ADX/search errors and return empty payloads (HTTP 200 with empty lists), so the UI shows a graceful empty state instead of an error.

## Testing (TDD)
- `HistoryStore`: add → trim at 50 → recent ordering; Redis-error → `[]` (fakeredis or a fake client).
- `ActivityStore.trending` / `source_breakdown`: KQL builds + parses against a mock Kusto response; ADX-error → empty.
- `AISearchClient.lookup_docs`: filter string includes tenant + ACL + `search.in(doc_id…)`; ACL excludes inaccessible docs (mock search).
- `DiscoverService.result`: orders by ADX score, drops ACL-filtered docs, caches; sub-failure → empty sections.
- Endpoints: `/history` and `/discover` require auth (401 without), return per-user data, degrade to empty.
- Frontend smoke: view switching, history item re-runs a query, discover cards render and link out.

## Out of Scope / Deferred
Popular-questions Discover mode, "suggested for you" (People-graph), saved/bookmarks, history search, pagination beyond 50.
