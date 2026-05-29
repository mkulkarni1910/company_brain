# Company Brain

Production-grade intelligence layer for unified enterprise search and LLM
orchestration on Microsoft Azure. See `docs/superpowers/specs/` for the
architecture spec and `docs/superpowers/plans/` for implementation plans.

## Phase 1 demo (Days 0–2)

Grounded Q&A with citations against ~12 markdown docs, end-to-end on real
Azure services.

### Prerequisites

1. Azure subscription with Owner/Contributor.
2. Azure OpenAI quota approved in the chosen region (default `swedencentral` (eastus2 was capacity-blocked at provision time)).
3. Entra ID tenant admin (for app registrations + admin consent).
4. `az` CLI logged in (`az login`), `uv` and `pnpm` installed locally.

### Bootstrap

```
./infra/provision.sh                # ~15 min, prints .env block at end
# follow infra/entra_setup.md       # ~15 min — Entra app reg in portal (manual)
cp <pasted .env block> brain-api/.env
```

### Run

```
# Terminal 1
cd brain-api && uv sync
uv run python scripts/create_search_index.py   # one-time index creation
uv run uvicorn app.main:app --port 8000

# Terminal 2 — load test corpus
cd brain-api && uv run python eval/load_corpus.py

# Terminal 3 — web
cd web && pnpm install && pnpm dev
```

Open http://localhost:3000, sign in with Entra, ask:

- "what is our PTO policy?"
- "how should I respond to a payments service alert?"
- "what is our Q3 ARR target?"

Each should return a grounded answer with one or more citations linking to
the corpus markdown file.

### Verify quality

```
cd brain-api
uv run python eval/run_eval.py --mode retrieval
```

Expected: `recall_at_10 >= 0.7`, `mrr_at_10 >= 0.5`.
Phase 1 baseline: 1.0 / 1.0 on 10 golden Qs (2026-05-29).

## Layout

- `brain-api/` — FastAPI monolith (Zone 4 intelligence layer)
- `web/` — Next.js 14 chat UI with Entra SSO
- `infra/` — Azure provisioning (`az` CLI; Bicep later)
- `docs/` — specs and plans

## Next phases

- Phase 2a (done): People pillar (Cosmos Gremlin), query-time ACL re-check,
  personalized ranker (Content + People). Same query → different ranking per user.
- Phase 2b (done): Activity pillar (Azure Data Explorer free cluster) + engagement
  signal as the ranker's third weighted term. /feedback ingests events; recent
  engagement lifts ranking.
- Phase 3 (done): Live Fetch via Microsoft Graph /search — freshness queries merge
  live results into ranking. Heuristic trigger; DefaultAzureCredential (single-identity).
- Phase 4 (done, pure-code Zone 4 completion): LLM plan-step classifier, ranker
  recency signal, per-event-type engagement weighting, ACL freshness gate, eval
  isolation.
- Phase 5 (infra / needs Entra): per-user OBO for Live Fetch, APIM gateway,
  OpenTelemetry, Event Hubs ingest path, per-tenant index isolation, JWKS caching.

Each gets its own plan in `docs/superpowers/plans/`.
