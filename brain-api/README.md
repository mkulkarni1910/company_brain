# brain-api

Zone 4 intelligence layer monolith. FastAPI + Semantic Kernel + Azure AI Search
+ Azure OpenAI + Redis.

## Local dev

```
uv sync
cp .env.example .env   # fill from infra/provision.sh output
uv run uvicorn app.main:app --reload --port 8000
```

## Tests

```
uv run pytest -v -m "not integration"     # unit tests only
uv run pytest -v -m integration           # require live Azure resources
```

## Eval

```
uv run python eval/run_eval.py --mode retrieval --report eval/reports/today.json
```

## Endpoints (Phase 1)

- `GET /healthz`
- `POST /admin/ingest` — body: `SourceDoc` JSON
- `POST /query` — body: `{ "query": "...", "k": 5 }`, requires Entra Bearer (or `x-debug-bypass-auth: <tenant>,<user_id>,<group1>,<group2>` for tests)
- `POST /admin/seed-people?users_limit=&groups_limit=` — seed People pillar from MS Graph (requires `x-admin-key`)
- `POST /admin/retrieve` — ranked candidate doc_ids without generation (eval/debug; requires `x-debug-bypass-auth` + `ENABLE_DEBUG_AUTH=true`)
- `POST /feedback` — record an engagement event (Activity pillar); requires `x-debug-bypass-auth` + `ENABLE_DEBUG_AUTH=true`
- `POST /admin/seed-activity?events_per_doc=` — seed synthetic engagement (requires `x-admin-key`)

## Phase 2a — Personalization

People pillar (Cosmos Gremlin), query-time ACL re-check, and a personalized
ranker (Content + People). Provision Cosmos first:

```
./infra/provision_cosmos.sh   # appends COSMOS_GREMLIN_* to print; copy into .env
```

Seed the org graph, then the same query ranks differently per user. Ranker
weights are `RANK_WEIGHT_CONTENT` / `RANK_WEIGHT_PEOPLE` in `.env` (default
0.7 / 0.3). Activity pillar (ADX) is Phase 2b.

## Phase 2b — Activity pillar

Engagement signal from Azure Data Explorer (free cluster). `/feedback` ingests
events; the ranker fuses a recency-weighted engagement score as its third signal
(`RANK_WEIGHT_ACTIVITY`, default 0.2; content 0.5 / people 0.3 / activity 0.2).
Create the free cluster per `infra/adx_setup.md`, then `POST /admin/seed-activity`
to populate demo engagement. ADX outages degrade gracefully to activity=0.

See `docs/superpowers/specs/2026-05-28-company-brain-zone4-design.md` for the
full architecture and `docs/superpowers/plans/2026-05-28-company-brain-phase1-mvp-qa.md`
for the implementation plan.
