# Search "Who from" (author) filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** Add the "Who from" author filter to the Discover search surface — an author facet (with counts + resolved names) that filters results by `author_id`.

**Architecture:** `search_page` returns an `author_id` facet; `SearchService` resolves those ids to display names (reusing `resolve_people`) and returns them as `authors` (user_id + name + count); the SearchView renders a "Who from ▾" dropdown that sets the `author_id` filter (already accepted by `/search`) and re-queries.

**Tech Stack:** FastAPI/Pydantic, azure-search-documents, Cosmos Gremlin, Next.js/React/TS. Conventions: backend root `brain-api/`; `uv run pytest`; `uv run ruff check`; stay on `main`; commit trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Non-integration tests inject fakes.

---

## Task 1: Author facet in models + search_page

**Files:** Modify `brain-api/app/domain/search.py`, `brain-api/app/retrieval/ai_search_client.py`, `brain-api/tests/test_search_page.py`, `brain-api/tests/test_search_api.py`

- [ ] **Step 1: Add models.** In `app/domain/search.py`:
  - Add `PersonFacet`:
    ```python
    class PersonFacet(BaseModel):
        user_id: str
        display_name: str
        count: int
    ```
  - Add `author_facets: list[tuple[str, int]] = []` to `SearchPage`.
  - Add `authors: list[PersonFacet] = []` to `SearchResponse` (defaulted so existing constructors stay valid).

- [ ] **Step 2: Failing test** — add to `tests/test_search_page.py` (reuse existing helpers `_row`, `FakeResults`, `FakeCli`, `_client`, `_user`):

```python
@pytest.mark.asyncio
async def test_search_page_returns_author_facets() -> None:
    rows = [_row("d1", "d1#0")]
    results = FakeResults(rows, facets={
        "source": [{"value": "sharepoint", "count": 1}],
        "author_id": [{"value": "u1", "count": 4}, {"value": "u2", "count": 2}],
    }, count=1)
    c = _client(results)
    page = await c.search_page(query="x", user=_user(), vector=[0.1])
    assert page.author_facets == [("u1", 4), ("u2", 2)]
    assert "author_id,count:10" in c._cli.kwargs["facets"]
```

- [ ] **Step 3: Run → fails** `uv run pytest tests/test_search_page.py::test_search_page_returns_author_facets -q`.

- [ ] **Step 4: Implement** in `search_page` (`app/retrieval/ai_search_client.py`):
  - Change the `facets=` kwarg to `facets=["source,count:10", "author_id,count:10"]`.
  - After computing `facets` (the source list), build author facets and include them in the returned `SearchPage`:
    ```python
        author_facets = [
            (f["value"], int(f["count"]))
            for f in (facets_raw.get("author_id") or [])
            if f.get("value")
        ]
        return SearchPage(results=list(hits.values())[:top], facets=facets,
                          author_facets=author_facets, total=total)
    ```
  - The early-return error path stays `return SearchPage(results=[], facets=[], total=0)` (author_facets defaults []).

- [ ] **Step 5: Fix the existing empty-dict assertion.** In `tests/test_search_api.py`, `test_search_empty_when_service_unavailable` asserts the full serialized dict — add `"authors": []`:
```python
        assert resp.json() == {"query": "vision", "answer": None, "results": [],
                               "facets": [], "people": [], "authors": [], "total": 0}
```

- [ ] **Step 6: Run all → pass** `uv run pytest tests/test_search_page.py tests/test_search_api.py tests/test_search_models.py -q` then `uv run pytest tests/ -q -m "not integration"`.

- [ ] **Step 7: Lint + commit**
```bash
uv run ruff check app/domain/search.py app/retrieval/ai_search_client.py tests/test_search_page.py tests/test_search_api.py
git add app/domain/search.py app/retrieval/ai_search_client.py tests/test_search_page.py tests/test_search_api.py
git commit -m "feat(search): author_id facet (PersonFacet + search_page.author_facets)"
```

---

## Task 2: SearchService resolves authors

**Files:** Modify `brain-api/app/search/service.py`, `brain-api/tests/test_search_service.py`

- [ ] **Step 1: Failing test** — add to `tests/test_search_service.py` (extend `FakeSearch` to carry author_facets, and `FakePeople` already returns PersonHits). Add a focused test:

```python
@pytest.mark.asyncio
async def test_authors_facet_resolved_with_names_and_counts() -> None:
    from app.domain.search import PersonHit, SearchPage, SourceFacet
    page = SearchPage(results=[_hit("d1", "u1")], facets=[SourceFacet(source="sharepoint", count=1)],
                      author_facets=[("u1", 4), ("u2", 2), ("u3", 1)], total=1)
    # u3 does not resolve → dropped from the authors filter list
    svc = _svc(page, answer=None,
               people=[PersonHit(user_id="u1", display_name="Priya"),
                       PersonHit(user_id="u2", display_name="Sam")])
    resp = await svc.result(user=_user(), query="vision")
    assert [(a.user_id, a.display_name, a.count) for a in resp.authors] == [
        ("u1", "Priya", 4), ("u2", "Sam", 2)]
```

Note: `_svc`/`FakeSearch` build `SearchPage` — ensure `FakeSearch.search_page` returns the page as-is (it does). `FakePeople.resolve_people` returns the provided PersonHits regardless of ids; the test relies on the join keeping only ids present in both the facet and the resolved set.

- [ ] **Step 2: Run → fails** (`resp.authors` empty / attribute missing).

- [ ] **Step 3: Implement** — in `SearchService.result`, after `people` is resolved, build `authors` from `page.author_facets` joined with resolved names. Resolve the union of result authors + facet authors in one call:

Replace the `author_ids`/`people` block and the final return with:
```python
        result_author_ids = [h.author_id for h in page.results if h.author_id]
        facet_author_ids = [a for a, _ in page.author_facets]
        all_ids = list(dict.fromkeys([*result_author_ids, *facet_author_ids]))
        try:
            resolved = await self._people.resolve_people(all_ids, user.tenant_id)
        except Exception as e:  # noqa: BLE001 - people block is best-effort
            logger.warning("search people resolve failed: %s", e)
            resolved = []
        name_by_id = {p.user_id: p.display_name for p in resolved}

        seen = set()
        people = []
        for uid in result_author_ids:
            if uid in name_by_id and uid not in seen:
                seen.add(uid)
                people.append(PersonHit(user_id=uid, display_name=name_by_id[uid]))
        authors = [
            PersonFacet(user_id=uid, display_name=name_by_id[uid], count=count)
            for uid, count in page.author_facets
            if uid in name_by_id
        ]

        return SearchResponse(
            query=q, answer=answer, results=page.results,
            facets=page.facets, people=people, authors=authors, total=page.total,
        )
```
Add imports at top: `from app.domain.search import PersonFacet, SearchResponse` and `from app.domain.query import Answer, QueryRequest` (Answer already imported; add `PersonHit` too): ensure `from app.domain.search import PersonFacet, SearchResponse` and `from app.people...`? No — `PersonHit` is in `app.domain.search`. So import `from app.domain.search import PersonFacet, PersonHit, SearchResponse`.

Also update the empty-query early return to include `authors=[]`:
```python
        if not q:
            return SearchResponse(query=query, answer=None, results=[], facets=[],
                                  people=[], authors=[], total=0)
```

- [ ] **Step 4: Run → pass** `uv run pytest tests/test_search_service.py -q` then `uv run pytest tests/ -q -m "not integration"`.

- [ ] **Step 5: Lint + commit**
```bash
uv run ruff check app/search/service.py tests/test_search_service.py
git add app/search/service.py tests/test_search_service.py
git commit -m "feat(search): resolve author facet to named authors (Who-from data)"
```

---

## Task 3: Frontend "Who from" dropdown

**Files:** Modify `web/lib/api.ts`, `web/components/Chat.tsx`, `web/app/globals.css`

- [ ] **Step 1: api.ts** — add the `PersonFacet` type and `authors` to `SearchResponse`:
```typescript
export type PersonFacet = { user_id: string; display_name: string; count: number };
```
and add `authors: PersonFacet[];` to the `SearchResponse` type. Update the `empty` fallback in `postSearch` to include `authors: []`.

- [ ] **Step 2: SearchView (Chat.tsx)** — add an author filter. Add state `const [author, setAuthor] = useState<string | null>(null);` and a `whoOpen` toggle `const [whoOpen, setWhoOpen] = useState(false);`. Thread `author` into `run` (new signature param) and pass `author_id` in opts:
```tsx
  async function run(query: string, sources: string[], days: number | null, authorId: string | null) {
    const text = query.trim();
    if (!text) return;
    const id = ++reqId.current;
    setSubmitted(text); setLoading(true);
    const opts: { sources?: string[]; date_from?: string; author_id?: string } = {};
    if (sources.length) opts.sources = sources;
    if (days != null) opts.date_from = new Date(Date.now() - days * 864e5).toISOString();
    if (authorId) opts.author_id = authorId;
    const res = await postSearch(text, opts);
    if (id !== reqId.current) return;
    setData(res); setLoading(false);
  }
```
Update all existing `run(...)` call sites to pass the current `author` (form submit, time chip, `toggleSource`). Add a "Who from" chip + dropdown after the time chip in `.filters`:
```tsx
          <div className="fchip-wrap">
            <div className="fchip" onClick={() => setWhoOpen((o) => !o)}>
              {author ? (data?.authors.find((a) => a.user_id === author)?.display_name ?? "Who from") : "Who from"} <span className="cv">▾</span>
            </div>
            {whoOpen && (
              <div className="fmenu">
                <div className="fmenu-item" onClick={() => { setAuthor(null); setWhoOpen(false); if (submitted) run(submitted, activeSources, TIME_FILTERS[timeIdx].days, null); }}>Anyone</div>
                {(data?.authors ?? []).map((a) => (
                  <div className="fmenu-item" key={a.user_id} onClick={() => { setAuthor(a.user_id); setWhoOpen(false); if (submitted) run(submitted, activeSources, TIME_FILTERS[timeIdx].days, a.user_id); }}>
                    {a.display_name} <span className="fmenu-ct">{a.count}</span>
                  </div>
                ))}
                {(!data || data.authors.length === 0) && <div className="fmenu-item" style={{ opacity: .5 }}>No authors</div>}
              </div>
            )}
          </div>
```
(Render the "Who from" chip only after a search has run, i.e. wrap it in `{submitted && (...)}`, since the author list comes from results. The time chip stays always visible.)

- [ ] **Step 3: globals.css** — add dropdown styles:
```css
  .fchip-wrap{position:relative}
  .fmenu{position:absolute;top:calc(100% + 6px);left:0;z-index:20;min-width:200px;background:var(--surface-2);
    border:1px solid var(--line);border-radius:11px;box-shadow:var(--shadow);padding:6px;max-height:280px;overflow:auto}
  .fmenu-item{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 11px;
    border-radius:8px;font-size:13.5px;color:var(--ink-dim);cursor:pointer}
  .fmenu-item:hover{background:var(--panel);color:var(--ink)}
  .fmenu-ct{font-family:"JetBrains Mono",monospace;font-size:11px;color:var(--ink-faint)}
```

- [ ] **Step 4: Verify** `cd web && pnpm typecheck && pnpm build` → clean.

- [ ] **Step 5: Commit**
```bash
git add web/lib/api.ts web/components/Chat.tsx web/app/globals.css
git commit -m "feat(web): Who-from author filter dropdown in search"
```

---

## Task 4: Build, deploy, verify, tag (controller)

- [ ] Build + push `brain-api:v5` and `substrateos-web:v7`; deploy both; verify brain-api `/healthz` 200 + anon `POST /search` → 401; browser: log in → Discover → search → open "Who from" → pick an author → results filter to that author. Tag `search-whofrom-v1`.

---

## Notes
- Authors that don't resolve to a display name are dropped from the dropdown (we never show raw ids). If the People graph isn't seeded for the tenant, the dropdown shows "No authors" — acceptable graceful degradation.
- The author facet counts are chunk-level (consistent with the source facet) — labelled by name, count is approximate.
