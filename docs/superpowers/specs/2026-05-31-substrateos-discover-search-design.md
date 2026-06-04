# Discover → Enterprise Search — Design Spec

**Date:** 2026-05-31
**Status:** Approved (brainstorming) — mockup `mockups/user-web-chat.html (Discover view — `.view-discover`)`
**Supersedes:** the trending-engagement Discover (`DiscoverService.result`, the Discover view from `history-discover-v1`). The trending backend code stays in the repo (still tested; `ActivityStore` remains used by the ranker) but is no longer surfaced in the UI.

## Goal

Turn **Discover** into an enterprise **search** surface: a query box + filters, a ranked result list with rich snippets and source facets, and a grounded **AI Overview** on top — all ACL-trimmed to the signed-in user. Reuses existing infrastructure: AI Search (hybrid + facets + highlights), the grounded-answer orchestrator (→ AI Overview), and the People graph (→ "people who work on this").

## Decisions (locked)
- **Placement:** replaces the Discover nav item. Nav stays Ask · Discover(search) · History.
- **AI Overview:** always shown above results (one grounded-answer call per search).
- **Filters v1:** **Source** facet (right rail, with counts) + **time range** (Anytime ▾ → `modified_at`) + **Who from** (author ▾, best-effort name resolution). *What type* and *My history* dropped from the UI entirely (My history would need the ADX activity data that isn't flowing yet).
- Out of scope: "Similar results" expansion (cosmetic; deferred), saved searches, type filter.

## Architecture

```
POST /search { query, filters, top, skip }
   ├─ AISearchClient.search_page(...)   → results page + source facets + total   (ACL-filtered)
   ├─ orchestrator.answer(...)          → AI Overview (grounded, cited)           (parallel)
   └─ people from result authors        → "people who work on this"
```

All three run for one request; results + overview are produced concurrently. Every part degrades independently (overview fails → results still render; search fails → empty state).

## Backend

### AISearchClient.search_page — `app/retrieval/ai_search_client.py`
New method returning a result page with facets, reusing the existing ACL filter (`build_acl_filter`).

```
async def search_page(self, *, query, user, vector, top=10, skip=0,
                      sources=None, date_from=None, author_id=None) -> SearchPage
```
- Filter = `build_acl_filter(user)` AND (optional `search.in(source, '<sources>', ',')`) AND (optional `modified_at ge <date_from>`) AND (optional `author_id eq '<id>'`).
- `self._cli.search(search_text=query, vector_queries=[...], query_type="semantic", filter=flt, top=top*N, skip=skip, facets=["source,count:10"], include_total_count=True, highlight_fields="content,title", ...)`.
- Group returned chunks by `doc_id` (keep best-ranked chunk, capture its `@search.highlights` for the snippet). Return up to `top` doc-level hits.
- `SearchPage` (new domain model): `results: list[SearchHit]`, `facets: list[SourceFacet]`, `total: int`.
- Degrade: on error return an empty `SearchPage`.

### Domain models — `app/domain/search.py`
```python
class SearchHit(BaseModel):
    doc_id: str; title: str; source: str; source_url: str
    author_id: str | None; modified_at: datetime
    snippet: str          # highlighted excerpt (HTML-safe markers stripped to <b>)
class SourceFacet(BaseModel):
    source: str; count: int
class PersonHit(BaseModel):
    user_id: str; display_name: str; role: str | None
class SearchResponse(BaseModel):
    query: str
    answer: Answer | None          # AI Overview (grounded); None if it failed/empty
    results: list[SearchHit]
    facets: list[SourceFacet]
    people: list[PersonHit]
    total: int
```

### SearchService — `app/search/service.py`
Composes the page + overview + people.
- `result(*, user, query, filters, top, skip) -> SearchResponse`:
  1. Embed the query once (reuse the embedder/cache) for the vector part.
  2. Concurrently (`asyncio.gather`): `search_page(...)` and `orchestrator.answer(grounded overview)`.
     - The overview reuses the existing grounded-answer path (`Answer` with citations). Wrap in try/except → `answer=None` on failure.
  3. `people`: distinct `author_id`s from the top results, resolved to names via the People graph (`PeopleGraphClient`/proximity); best-effort, empty on failure.
  4. Assemble `SearchResponse`. No caching v1 (queries are diverse).

### Endpoint — `app/api/search.py`
`POST /search` → `resolve_user` → `SearchService.result(...)`. Body: `{ query: str, sources?: list[str], date_from?: datetime, author_id?: str, top?: int=10, skip?: int=0 }`. ACL via `resolve_user` + the per-call filter. Wire `get_search_service` (tolerant) in `deps.py`; construct in `main.py` lifespan; include router.

### ACL
Identical guarantee to existing search: `build_acl_filter(user)` is applied to every `search_page` query, and the AI Overview goes through the orchestrator which already enforces ACL + the query-time recheck. Facet counts are computed over the ACL-filtered result set, so counts never reveal inaccessible content.

## Frontend

### `web/lib/api.ts`
- Types mirroring the backend models (`SearchHit`, `SourceFacet`, `PersonHit`, `SearchResponse`).
- `postSearch(query, opts?) -> SearchResponse` via `authedFetch` (POST `/search`).

### `web/components/Chat.tsx` (or a new `SearchView`)
Replace `DiscoverView` with `SearchView` (the `Discover` nav item renders it):
- **Search bar** (submits on Enter) + clear.
- **Filter chips:** Anytime ▾ (time-range menu → `date_from`), Who from ▾ (author menu), My history (disabled "soon").
- **AI Overview card** (always shown when `answer` present): the grounded answer rendered with the existing markdown `AnswerText` + citation chips; "grounded · N sources" badge; source pills.
- **Results list:** per hit — source icon, Fraunces title link (opens `source_url`, logs `click`), author · relative `modified_at` · 📁 source, snippet (highlighted terms bolded).
- **People** block: "People who work on this" cards from `people`.
- **Right rail facets:** "Found N results" + Source list with counts; clicking a source toggles the `sources` filter and re-queries.
- Empty state when `total === 0`; the Ask view + History view are unchanged.
- Styles: reuse `mockups/user-web-chat.html (Discover view — `.view-discover`)` (already in SubStrateOS tokens) — port its CSS into `globals.css`.

## Error handling / degradation
- AI Overview failure → render results without the card.
- `search_page` failure → empty state ("No results"), HTTP 200.
- People resolution failure → omit the people block.
- Never 500 the `/search` endpoint for data-layer failures.

## Testing (TDD)
- `AISearchClient.search_page`: filter composition (ACL + source/date/author), facet parsing, doc-level dedup, highlight→snippet, degrade-to-empty (mock search client).
- `SearchService.result`: concurrent assembly, overview-failure → `answer=None`, people from authors, empty query handling (mock collaborators).
- `/search` endpoint: requires auth (401), returns SearchResponse shape, degrades to empty when service unavailable.
- Frontend smoke: search submit renders overview + results + facets; facet click re-queries; result click logs.

## Out of scope / deferred
- "Similar results" neighbor expansion; saved searches; "What type" filter; "My history" (needs ADX activity data + the pending MI grant); facet counts at doc-granularity (v1 uses AI Search chunk-level facet counts, which slightly overcount — acceptable for the demo, noted in the UI as approximate).
