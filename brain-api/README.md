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

See `docs/superpowers/specs/2026-05-28-company-brain-zone4-design.md` for the
full architecture and `docs/superpowers/plans/2026-05-28-company-brain-phase1-mvp-qa.md`
for the implementation plan.
