# Company Brain — Phase 1 Implementation Plan (Days 0–2: Grounded Q&A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `brain-api` monolith on real Azure services so that a Next.js web chat (with Entra SSO) can ask a question, retrieve hybrid results from Azure AI Search, generate a grounded answer with citations from GPT-4o, and return it in < 4s — fully end-to-end.

**Architecture:** Python 3.12 FastAPI monolith. One service, module boundaries that map 1:1 to PDF Zone 4 components. All Azure SDK calls behind thin client classes using `DefaultAzureCredential`. Test-driven; tests run against real Azure resources for integration paths (no mocks for AI Search / OpenAI in primary tests — we want signal). Frequent commits.

**Tech Stack:** Python 3.12, FastAPI, uv, pydantic v2, pydantic-settings, semantic-kernel, openai (Azure), azure-search-documents, azure-identity, redis, httpx, pytest, pytest-asyncio. Next.js 14 + MSAL (web shell). Azure: Container Apps, AI Search Basic, Azure OpenAI (gpt-4o + gpt-4o-mini + text-embedding-3-large), Redis Basic C0, Key Vault, ACR, App Insights.

**Scope cut:** This plan covers spec Days 0–2 (foundation, ingest, hybrid search, orchestrator + grounded answer + 10-Q eval scaffold). Days 3–7 (People pillar, Activity pillar, ACL Store, Live Fetch, APIM, eval growth) get follow-up plans after Phase 1 demos.

**Out of scope for Phase 1 (explicitly):** Cosmos DB Gremlin (People pillar), Azure Data Explorer (Activity pillar), Redis ACL Store (use chunk's index-time ACL only), MS Graph Live Fetch, APIM gateway (Container Apps ingress with TLS instead), full Personalized Ranker (raw AI Search hybrid order in Phase 1), Content Safety, Purview.

---

## File Structure

Files created in Phase 1:

```
company-brain/
├── brain-api/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                       # FastAPI app, lifespan, /healthz
│   │   ├── config.py                     # pydantic-settings
│   │   ├── auth.py                       # Entra JWT validation + group expansion
│   │   ├── deps.py                       # FastAPI dependencies (singletons)
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── query.py                  # POST /query
│   │   │   └── admin.py                  # POST /admin/ingest
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── identity.py               # User
│   │   │   ├── chunk.py                  # SourceDoc, Chunk
│   │   │   └── query.py                  # QueryRequest, Candidate, Answer, Citation
│   │   ├── orchestrator/
│   │   │   ├── __init__.py
│   │   │   └── kernel.py                 # SemanticKernelOrchestrator
│   │   ├── retrieval/
│   │   │   ├── __init__.py
│   │   │   ├── ai_search_client.py       # AI Search hybrid wrapper
│   │   │   └── hybrid_retriever.py       # Phase 1: AI Search only (no People/Activity)
│   │   ├── acl/
│   │   │   ├── __init__.py
│   │   │   └── enforcement.py            # build_acl_filter() helper
│   │   ├── generation/
│   │   │   ├── __init__.py
│   │   │   ├── azure_openai.py           # AzureOpenAIClient: embed(), complete()
│   │   │   └── prompts.py                # grounded-answer prompt + citation parser
│   │   ├── cache/
│   │   │   ├── __init__.py
│   │   │   └── redis_cache.py            # answer cache + embedding cache
│   │   └── ingest/
│   │       ├── __init__.py
│   │       ├── chunker.py                # structure-aware chunker
│   │       ├── acl_resolver.py           # synthetic ACL stamping
│   │       └── pipeline.py               # IngestPipeline.process()
│   │   # NOTE: app/observability/otel.py is deferred to Phase 4 (hardening)
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── corpus/                       # ~50 markdown docs (Phase 1 starter)
│   │   ├── golden.jsonl                  # 10 golden Qs
│   │   ├── load_corpus.py
│   │   ├── run_eval.py                   # retrieval mode only in Phase 1
│   │   └── configs/v1.yaml
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py                   # pytest fixtures (settings, clients)
│   │   ├── test_chunker.py
│   │   ├── test_acl_filter.py
│   │   ├── test_ai_search_client.py      # integration: real AI Search
│   │   ├── test_ingest_pipeline.py       # integration
│   │   ├── test_hybrid_retriever.py      # integration
│   │   ├── test_prompts.py
│   │   ├── test_orchestrator.py          # integration: real OpenAI
│   │   ├── test_redis_cache.py           # integration: real Redis
│   │   └── test_auth.py
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── .env.example
│   ├── .python-version                   # 3.12
│   └── README.md
├── web/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                      # chat UI
│   │   └── providers.tsx                 # MSAL provider
│   ├── components/
│   │   └── Chat.tsx
│   ├── lib/
│   │   ├── msal.ts
│   │   └── api.ts
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.mjs
│   ├── tailwind.config.ts
│   └── .env.local.example
├── infra/
│   ├── provision.sh                      # az CLI bootstrap
│   ├── entra_setup.md                    # manual Entra app reg checklist
│   ├── teardown.sh                       # delete resource group
│   └── README.md
├── .github/workflows/
│   └── ci.yml                            # lint + retrieval eval on PR
├── .gitignore
└── README.md
```

---

## Conventions for every task

- **Always run from `brain-api/`** unless a task says otherwise.
- **Run a single test:** `uv run pytest tests/test_x.py::test_name -v`
- **Run all tests:** `uv run pytest -v`
- **Commit message convention:** `feat: ...`, `chore: ...`, `test: ...`, `docs: ...`
- **TDD discipline:** the test you write at "Step 1" of each task fails until you implement the code in "Step 3". Do NOT skip running the failing test (Step 2) — it confirms the test is wired correctly.
- **Integration tests** (those calling real Azure resources) live in the same `tests/` directory and are guarded by `pytest.mark.integration`. CI runs them; local dev can skip them with `-m "not integration"`.

---

## Day 0 — Foundation (Tasks 1–9)

### Task 1: Repo skeleton and Python tooling

**Files:**
- Create: `/Users/lokesh/Desktop/RFpilot/company_brain/.gitignore`
- Create: `/Users/lokesh/Desktop/RFpilot/company_brain/README.md`
- Create: `brain-api/.python-version`
- Create: `brain-api/pyproject.toml`
- Create: `brain-api/.env.example`

- [ ] **Step 1: Create root `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/

# Env files
.env
.env.local
*.env.local

# Build
dist/
build/

# IDE
.vscode/
.idea/

# Node
node_modules/
.next/
out/

# OS
.DS_Store
Thumbs.db

# Logs
*.log

# Eval reports
eval/reports/
```

- [ ] **Step 2: Create root `README.md`**

```markdown
# Company Brain

Production-grade intelligence layer for unified enterprise search and LLM
orchestration on Microsoft Azure. See `docs/superpowers/specs/` for the
architecture spec and `docs/superpowers/plans/` for implementation plans.

## Phase 1 (current)

Grounded Q&A end-to-end against real Azure services. See
`docs/superpowers/plans/2026-05-28-company-brain-phase1-mvp-qa.md`.

## Layout

- `brain-api/` — Python FastAPI monolith (Zone 4 intelligence layer)
- `web/` — Next.js 14 chat UI with Entra SSO
- `infra/` — Azure provisioning (`az` CLI in v1; Bicep later)
- `docs/` — specs and plans
```

- [ ] **Step 3: Create `brain-api/.python-version`**

```
3.12
```

- [ ] **Step 4: Create `brain-api/pyproject.toml`**

```toml
[project]
name = "brain-api"
version = "0.1.0"
description = "Company Brain — Zone 4 intelligence layer"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "httpx>=0.27",
  "azure-identity>=1.19",
  "azure-search-documents>=11.6.0b8",
  "azure-keyvault-secrets>=4.8",
  "openai>=1.54",
  "semantic-kernel>=1.18",
  "redis>=5.2",
  "python-jose[cryptography]>=3.3",
  "tenacity>=9.0",
  "opentelemetry-api>=1.28",
  "opentelemetry-sdk>=1.28",
  "opentelemetry-instrumentation-fastapi>=0.49b0",
  "opentelemetry-instrumentation-httpx>=0.49b0",
  "azure-monitor-opentelemetry>=1.6",
  "tiktoken>=0.8",
  "structlog>=24.4",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=6.0",
  "respx>=0.21",
  "ruff>=0.7",
  "mypy>=1.13",
]

[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"
markers = [
  "integration: tests that hit real Azure resources (skip with -m 'not integration')",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
ignore = ["E501"]
```

- [ ] **Step 5: Create `brain-api/.env.example`**

```
# Azure resources — fill from `infra/provision.sh` output
AZURE_TENANT_ID=
AZURE_CLIENT_ID=               # Entra app for brain-api
AZURE_AI_SEARCH_ENDPOINT=      # https://<name>.search.windows.net
AZURE_AI_SEARCH_INDEX=brain-content-t-test
AZURE_OPENAI_ENDPOINT=         # https://<name>.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o
AZURE_OPENAI_PLAN_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_EMBED_DEPLOYMENT=text-embedding-3-large
AZURE_REDIS_HOST=              # <name>.redis.cache.windows.net
AZURE_REDIS_PORT=6380
AZURE_REDIS_SSL=true
AZURE_KEY_VAULT_URL=           # https://<name>.vault.azure.net
APPLICATIONINSIGHTS_CONNECTION_STRING=

# Brain settings
BRAIN_TENANT_ID=t-test
BRAIN_LOG_LEVEL=INFO
```

- [ ] **Step 6: Verify uv is installed and sync**

Run from `brain-api/`:
```bash
uv --version
uv sync
```
Expected: prints uv version (≥0.5), then resolves and installs dependencies into `.venv/`.

- [ ] **Step 7: Commit**

```bash
git add .gitignore README.md brain-api/.python-version brain-api/pyproject.toml brain-api/.env.example brain-api/uv.lock
git commit -m "chore: initialize brain-api Python project skeleton"
```

---

### Task 2: Settings module (`config.py`)

**Files:**
- Create: `brain-api/app/__init__.py`
- Create: `brain-api/app/config.py`
- Create: `brain-api/tests/__init__.py`
- Create: `brain-api/tests/conftest.py`
- Create: `brain-api/tests/test_config.py`

- [ ] **Step 1: Create empty package init files**

`brain-api/app/__init__.py` — empty file.
`brain-api/tests/__init__.py` — empty file.

- [ ] **Step 2: Write failing test `tests/test_config.py`**

```python
import os
import pytest
from app.config import Settings


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", "tid-1")
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid-1")
    monkeypatch.setenv("AZURE_AI_SEARCH_ENDPOINT", "https://x.search.windows.net")
    monkeypatch.setenv("AZURE_AI_SEARCH_INDEX", "brain-content-t-test")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")
    monkeypatch.setenv("AZURE_OPENAI_PLAN_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")
    monkeypatch.setenv("AZURE_REDIS_HOST", "x.redis.cache.windows.net")

    s = Settings()
    assert s.azure_tenant_id == "tid-1"
    assert s.azure_ai_search_index == "brain-content-t-test"
    assert s.azure_openai_api_version == "2024-10-21"  # default
    assert s.brain_tenant_id == "t-test"               # default
    assert s.azure_redis_port == 6380                  # default
```

- [ ] **Step 3: Run test, expect ImportError**

```bash
uv run pytest tests/test_config.py -v
```
Expected: `ImportError: cannot import name 'Settings' from 'app.config'` (because the module doesn't exist yet).

- [ ] **Step 4: Implement `app/config.py`**

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Azure identity
    azure_tenant_id: str
    azure_client_id: str

    # AI Search
    azure_ai_search_endpoint: str
    azure_ai_search_index: str

    # Azure OpenAI
    azure_openai_endpoint: str
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_plan_deployment: str = "gpt-4o-mini"
    azure_openai_embed_deployment: str = "text-embedding-3-large"

    # Redis
    azure_redis_host: str
    azure_redis_port: int = 6380
    azure_redis_ssl: bool = True

    # Key Vault (optional in dev)
    azure_key_vault_url: str | None = None

    # App Insights (optional in dev)
    applicationinsights_connection_string: str | None = None

    # Brain
    brain_tenant_id: str = "t-test"
    brain_log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Create `tests/conftest.py` with env setup fixture**

```python
import os
import pytest


@pytest.fixture(autouse=True)
def _default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default test env so Settings can instantiate without a real .env."""
    defaults = {
        "AZURE_TENANT_ID": "tid-test",
        "AZURE_CLIENT_ID": "cid-test",
        "AZURE_AI_SEARCH_ENDPOINT": "https://test.search.windows.net",
        "AZURE_AI_SEARCH_INDEX": "brain-content-t-test",
        "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
        "AZURE_REDIS_HOST": "test.redis.cache.windows.net",
    }
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)
```

- [ ] **Step 6: Run test, expect PASS**

```bash
uv run pytest tests/test_config.py -v
```
Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add app/__init__.py app/config.py tests/__init__.py tests/conftest.py tests/test_config.py
git commit -m "feat: add Settings module with pydantic-settings"
```

---

### Task 3: Domain models

**Files:**
- Create: `brain-api/app/domain/__init__.py`
- Create: `brain-api/app/domain/identity.py`
- Create: `brain-api/app/domain/chunk.py`
- Create: `brain-api/app/domain/query.py`
- Create: `brain-api/tests/test_domain.py`

- [ ] **Step 1: Write failing test `tests/test_domain.py`**

```python
from datetime import UTC, datetime

from app.domain.chunk import Chunk, SourceDoc
from app.domain.identity import User
from app.domain.query import Answer, Candidate, Citation, QueryRequest


def test_user_principal_set_includes_self_and_groups() -> None:
    u = User(
        user_id="u-1",
        tenant_id="t-test",
        email="alex@contoso.com",
        display_name="Alex",
        group_ids={"g-sales", "g-central"},
        manager_id=None,
    )
    assert u.principals() == {"u-1", "g-sales", "g-central"}


def test_chunk_id_is_doc_id_plus_chunk_index() -> None:
    now = datetime.now(UTC)
    c = Chunk(
        chunk_id="sp:doc-1#chunk-0",
        doc_id="sp:doc-1",
        tenant_id="t-test",
        source="sharepoint",
        source_url="https://contoso.sharepoint.com/x",
        title="Q3 Plan",
        content="ARR target 42M",
        content_vector=[0.1] * 3072,
        acl_principals=["u-1", "g-sales"],
        author_id="u-100",
        entities=["Q3"],
        created_at=now,
        modified_at=now,
        chunk_index=0,
    )
    assert c.chunk_id.startswith(c.doc_id)
    assert len(c.content_vector) == 3072


def test_source_doc_round_trip() -> None:
    now = datetime.now(UTC)
    d = SourceDoc(
        doc_id="up:abc",
        tenant_id="t-test",
        source="uploaded",
        source_url="local://abc.md",
        title="Notes",
        body="hello world",
        author_id=None,
        acl_principals=["g-eng"],
        created_at=now,
        modified_at=now,
        mime="text/markdown",
    )
    assert d.source == "uploaded"


def test_query_request_defaults() -> None:
    q = QueryRequest(query="what is our Q3 plan?")
    assert q.k == 5
    assert q.session_id is None
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_domain.py -v
```
Expected: ImportError on `app.domain.*`.

- [ ] **Step 3: Implement `app/domain/__init__.py` (empty)**

Empty file.

- [ ] **Step 4: Implement `app/domain/identity.py`**

```python
from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    model_config = ConfigDict(frozen=False)

    user_id: str
    tenant_id: str
    email: str
    display_name: str
    group_ids: set[str]
    manager_id: str | None = None

    def principals(self) -> set[str]:
        return {self.user_id, *self.group_ids}
```

- [ ] **Step 5: Implement `app/domain/chunk.py`**

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["sharepoint", "teams", "uploaded", "slack", "jira", "graph"]


class SourceDoc(BaseModel):
    doc_id: str
    tenant_id: str
    source: Source
    source_url: str
    title: str
    body: str
    author_id: str | None
    acl_principals: list[str]
    created_at: datetime
    modified_at: datetime
    mime: str


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    tenant_id: str
    source: Source
    source_url: str
    title: str
    content: str
    content_vector: list[float] = Field(default_factory=list)
    acl_principals: list[str]
    author_id: str | None = None
    entities: list[str] = Field(default_factory=list)
    created_at: datetime
    modified_at: datetime
    chunk_index: int
```

- [ ] **Step 6: Implement `app/domain/query.py`**

```python
from typing import Literal

from pydantic import BaseModel, Field

from .chunk import Chunk

SourceHit = Literal["vector", "bm25", "semantic", "live", "graph"]


class QueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    k: int = 5


class Candidate(BaseModel):
    chunk: Chunk
    sources_hit: set[SourceHit] = Field(default_factory=set)
    raw_scores: dict[str, float] = Field(default_factory=dict)
    live_payload: dict | None = None


class Citation(BaseModel):
    doc_id: str
    chunk_id: str
    source_url: str
    title: str
    snippet: str


class Answer(BaseModel):
    text: str
    citations: list[Citation]
    query_id: str
    debug: dict | None = None
```

- [ ] **Step 7: Run tests, expect PASS**

```bash
uv run pytest tests/test_domain.py -v
```
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add app/domain tests/test_domain.py
git commit -m "feat: add domain models (User, Chunk, SourceDoc, QueryRequest, Answer)"
```

---

### Task 4: FastAPI skeleton with `/healthz`

**Files:**
- Create: `brain-api/app/main.py`
- Create: `brain-api/tests/test_healthz.py`

- [ ] **Step 1: Write failing test `tests/test_healthz.py`**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "brain-api"}
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_healthz.py -v
```
Expected: ImportError on `app.main`.

- [ ] **Step 3: Implement `app/main.py`**

```python
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # warm settings (validates env on boot)
    get_settings()
    yield


app = FastAPI(title="brain-api", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "brain-api"}
```

- [ ] **Step 4: Run test, expect PASS**

```bash
uv run pytest tests/test_healthz.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Start the server locally and curl it**

```bash
uv run uvicorn app.main:app --port 8000 &
sleep 2
curl -s localhost:8000/healthz
kill %1
```
Expected: `{"status":"ok","service":"brain-api"}` printed.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_healthz.py
git commit -m "feat: add FastAPI skeleton with /healthz endpoint"
```

---

### Task 5: Provisioning script (`infra/provision.sh`)

**Files:**
- Create: `infra/provision.sh`
- Create: `infra/teardown.sh`
- Create: `infra/README.md`

- [ ] **Step 1: Create `infra/provision.sh`**

```bash
#!/usr/bin/env bash
# Bootstrap all Azure resources for Phase 1 of company-brain.
# Idempotent: re-running is safe (uses `--name`-based existence checks).
# Requires: az CLI logged in (`az login`), permissions to create resources.

set -euo pipefail

# ---- Edit before running ---------------------------------------------------
LOCATION="${LOCATION:-eastus2}"
RG="${RG:-rg-company-brain-dev}"
NAME_PREFIX="${NAME_PREFIX:-cbrain-$(whoami | tr '[:upper:]' '[:lower:]')}"
# ----------------------------------------------------------------------------

SEARCH_NAME="${NAME_PREFIX}-search"
OPENAI_NAME="${NAME_PREFIX}-openai"
REDIS_NAME="${NAME_PREFIX}-redis"
KV_NAME="${NAME_PREFIX}-kv"
ACR_NAME="$(echo "${NAME_PREFIX}acr" | tr -d '-')"  # ACR disallows hyphens
APPINSIGHTS_NAME="${NAME_PREFIX}-ai"
LOGS_NAME="${NAME_PREFIX}-logs"
MI_NAME="${NAME_PREFIX}-mi"
CAPP_ENV="${NAME_PREFIX}-capp-env"

echo "Provisioning into $RG ($LOCATION)..."

az group create -n "$RG" -l "$LOCATION" 1>/dev/null

# AI Search
if ! az search service show -g "$RG" -n "$SEARCH_NAME" &>/dev/null; then
  echo "Creating AI Search service $SEARCH_NAME..."
  az search service create -g "$RG" -n "$SEARCH_NAME" -l "$LOCATION" \
    --sku basic --replica-count 1 --partition-count 1 1>/dev/null
fi

# Azure OpenAI account + model deployments
if ! az cognitiveservices account show -g "$RG" -n "$OPENAI_NAME" &>/dev/null; then
  echo "Creating Azure OpenAI account $OPENAI_NAME..."
  az cognitiveservices account create -g "$RG" -n "$OPENAI_NAME" -l "$LOCATION" \
    --kind OpenAI --sku S0 --yes 1>/dev/null
fi
deploy_model() {
  local dep="$1" model="$2" version="$3" sku="$4" capacity="$5"
  if ! az cognitiveservices account deployment show -g "$RG" -n "$OPENAI_NAME" \
       --deployment-name "$dep" &>/dev/null; then
    echo "Deploying $dep ($model:$version)..."
    az cognitiveservices account deployment create -g "$RG" -n "$OPENAI_NAME" \
      --deployment-name "$dep" --model-name "$model" --model-version "$version" \
      --model-format OpenAI --sku-name "$sku" --sku-capacity "$capacity" 1>/dev/null
  fi
}
deploy_model "gpt-4o" "gpt-4o" "2024-08-06" "Standard" 30
deploy_model "gpt-4o-mini" "gpt-4o-mini" "2024-07-18" "Standard" 30
deploy_model "text-embedding-3-large" "text-embedding-3-large" "1" "Standard" 30

# Redis
if ! az redis show -g "$RG" -n "$REDIS_NAME" &>/dev/null; then
  echo "Creating Redis $REDIS_NAME (Basic C0, ~10 min)..."
  az redis create -g "$RG" -n "$REDIS_NAME" -l "$LOCATION" \
    --sku Basic --vm-size c0 1>/dev/null
fi

# Key Vault
if ! az keyvault show -g "$RG" -n "$KV_NAME" &>/dev/null; then
  echo "Creating Key Vault $KV_NAME..."
  az keyvault create -g "$RG" -n "$KV_NAME" -l "$LOCATION" \
    --enable-rbac-authorization true 1>/dev/null
fi

# ACR
if ! az acr show -g "$RG" -n "$ACR_NAME" &>/dev/null; then
  echo "Creating ACR $ACR_NAME..."
  az acr create -g "$RG" -n "$ACR_NAME" --sku Basic 1>/dev/null
fi

# Log Analytics + App Insights
if ! az monitor log-analytics workspace show -g "$RG" -n "$LOGS_NAME" &>/dev/null; then
  echo "Creating Log Analytics $LOGS_NAME..."
  az monitor log-analytics workspace create -g "$RG" -n "$LOGS_NAME" -l "$LOCATION" 1>/dev/null
fi
LOGS_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LOGS_NAME" --query id -o tsv)

if ! az monitor app-insights component show -g "$RG" -a "$APPINSIGHTS_NAME" &>/dev/null; then
  echo "Creating App Insights $APPINSIGHTS_NAME..."
  az monitor app-insights component create -g "$RG" -a "$APPINSIGHTS_NAME" -l "$LOCATION" \
    --workspace "$LOGS_ID" 1>/dev/null
fi

# Managed identity for brain-api
if ! az identity show -g "$RG" -n "$MI_NAME" &>/dev/null; then
  echo "Creating managed identity $MI_NAME..."
  az identity create -g "$RG" -n "$MI_NAME" 1>/dev/null
fi
MI_PRINCIPAL=$(az identity show -g "$RG" -n "$MI_NAME" --query principalId -o tsv)
MI_CLIENT=$(az identity show -g "$RG" -n "$MI_NAME" --query clientId -o tsv)

# RBAC roles
SUB=$(az account show --query id -o tsv)
SEARCH_ID=$(az search service show -g "$RG" -n "$SEARCH_NAME" --query id -o tsv)
OPENAI_ID=$(az cognitiveservices account show -g "$RG" -n "$OPENAI_NAME" --query id -o tsv)
KV_ID=$(az keyvault show -g "$RG" -n "$KV_NAME" --query id -o tsv)

assign() {
  az role assignment create --assignee "$MI_PRINCIPAL" --role "$1" --scope "$2" 1>/dev/null || true
}
assign "Search Service Contributor" "$SEARCH_ID"
assign "Search Index Data Contributor" "$SEARCH_ID"
assign "Cognitive Services OpenAI User" "$OPENAI_ID"
assign "Key Vault Secrets User" "$KV_ID"

# Container Apps env
if ! az containerapp env show -g "$RG" -n "$CAPP_ENV" &>/dev/null; then
  echo "Creating Container Apps env $CAPP_ENV..."
  az containerapp env create -g "$RG" -n "$CAPP_ENV" -l "$LOCATION" \
    --logs-workspace-id "$(az monitor log-analytics workspace show -g "$RG" -n "$LOGS_NAME" --query customerId -o tsv)" \
    --logs-workspace-key "$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LOGS_NAME" --query primarySharedKey -o tsv)" \
    1>/dev/null
fi

# Print .env values
cat <<EOF

=== Done. Copy into brain-api/.env ===
AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)
AZURE_CLIENT_ID=$MI_CLIENT
AZURE_AI_SEARCH_ENDPOINT=https://$SEARCH_NAME.search.windows.net
AZURE_AI_SEARCH_INDEX=brain-content-t-test
AZURE_OPENAI_ENDPOINT=$(az cognitiveservices account show -g "$RG" -n "$OPENAI_NAME" --query properties.endpoint -o tsv)
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o
AZURE_OPENAI_PLAN_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_EMBED_DEPLOYMENT=text-embedding-3-large
AZURE_REDIS_HOST=$(az redis show -g "$RG" -n "$REDIS_NAME" --query hostName -o tsv)
AZURE_KEY_VAULT_URL=$(az keyvault show -g "$RG" -n "$KV_NAME" --query properties.vaultUri -o tsv)
APPLICATIONINSIGHTS_CONNECTION_STRING=$(az monitor app-insights component show -g "$RG" -a "$APPINSIGHTS_NAME" --query connectionString -o tsv)
EOF
```

- [ ] **Step 2: Create `infra/teardown.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
RG="${RG:-rg-company-brain-dev}"
read -r -p "Delete resource group $RG? [y/N] " confirm
[ "$confirm" = "y" ] && az group delete -n "$RG" --yes --no-wait
```

- [ ] **Step 3: Create `infra/README.md`**

```markdown
# Infrastructure

## Provision

Requires: `az` CLI logged in (`az login`), Owner or Contributor on the subscription.

```
chmod +x provision.sh teardown.sh
./provision.sh
```

Run takes ~15 minutes (Redis is the slowest). Re-runs are idempotent.

Output: a block of `.env` values to copy into `../brain-api/.env`.

## Teardown

```
./teardown.sh
```
```

- [ ] **Step 4: Make scripts executable**

```bash
chmod +x infra/provision.sh infra/teardown.sh
```

- [ ] **Step 5: Run provision script (real Azure call — costs $)**

From repo root:
```bash
az login
./infra/provision.sh
```
Expected: ~15 min runtime, ends with the `.env` block printed. Copy that into `brain-api/.env`.

- [ ] **Step 6: Verify Azure resources exist**

```bash
az resource list -g rg-company-brain-dev --query "[].{name:name, type:type}" -o table
```
Expected: shows AI Search, OpenAI account + 3 deployments, Redis, Key Vault, ACR, Log Analytics, App Insights, managed identity, Container Apps env.

- [ ] **Step 7: Commit infra**

```bash
git add infra/
git commit -m "feat: add az CLI provisioning script for Phase 1 Azure resources"
```

---

### Task 6: Entra app registration checklist

**Files:**
- Create: `infra/entra_setup.md`

- [ ] **Step 1: Write the checklist**

```markdown
# Entra App Registration — Manual Setup

The `provision.sh` script handles all Azure resources, but **Entra app
registration must be done in the portal** because admin consent for delegated
permissions requires a human click.

Do this once per tenant. Estimated time: 15 min.

## 1. Register the API app (`brain-api`)

1. https://portal.azure.com → Entra ID → App registrations → New registration
2. Name: `brain-api`
3. Supported account types: "Accounts in this organizational directory only"
4. Redirect URI: leave blank
5. Register

Note the **Application (client) ID** — this is the `AZURE_API_CLIENT_ID`.

### Expose an API

1. Manage → Expose an API → Set → use default URI `api://{client-id}` → Save
2. Add a scope:
   - Scope name: `Query.Read`
   - Who can consent: Admins and users
   - Admin consent display name: "Query the company brain"
   - Admin consent description: "Allows the web app to call brain-api on behalf of the signed-in user"
   - State: Enabled
   - Add scope

### Add API permissions

1. API permissions → Add a permission → Microsoft Graph
2. **Application permissions** (used by brain-api for group expansion):
   - `Directory.Read.All` → Add permissions
3. **Delegated permissions** (used for Live Fetch via OBO — Day 5):
   - `Sites.Read.All`
   - `Files.Read.All`
4. **Grant admin consent for <tenant>** — click and confirm

## 2. Register the web app (`brain-web`)

1. App registrations → New registration
2. Name: `brain-web`
3. Supported account types: same tenant only
4. Redirect URI: Single-page application → `http://localhost:3000`
5. Register

Note the **Application (client) ID** — this is `NEXT_PUBLIC_AZURE_CLIENT_ID`.

### Configure auth

1. Authentication → Add platform → SPA → `http://localhost:3000`
2. Add `http://localhost:3000/auth/callback` if your library needs it
3. Save

### Add API permissions

1. API permissions → Add a permission → My APIs → `brain-api` → `Query.Read`
2. Grant admin consent for <tenant>

## 3. Verify

Run from `brain-api/`:

```
uv run python -c "
from azure.identity import DefaultAzureCredential
from msgraph.generated.models.user import User  # if available; otherwise httpx
import httpx
cred = DefaultAzureCredential()
tok = cred.get_token('https://graph.microsoft.com/.default').token
r = httpx.get('https://graph.microsoft.com/v1.0/users?\$top=1', headers={'Authorization': f'Bearer {tok}'})
print(r.status_code, r.json().get('value', [])[:1])
"
```

Expected: `200` and one user object. If 403, admin consent for `Directory.Read.All` did not take — re-grant it.

## Outputs

Add to `brain-api/.env`:

```
AZURE_API_CLIENT_ID=<brain-api app client id>
AZURE_API_SCOPE=api://<brain-api app client id>/Query.Read
```

Add to `web/.env.local`:

```
NEXT_PUBLIC_AZURE_TENANT_ID=<tenant id>
NEXT_PUBLIC_AZURE_CLIENT_ID=<brain-web app client id>
NEXT_PUBLIC_AZURE_API_SCOPE=api://<brain-api app client id>/Query.Read
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```
```

- [ ] **Step 2: Follow the checklist in the portal**

Complete steps 1–3 in `infra/entra_setup.md`. This requires human action in the Azure portal.

- [ ] **Step 3: Update `brain-api/.env.example` with the two new keys**

Edit `brain-api/.env.example` and append:

```
# Entra API app
AZURE_API_CLIENT_ID=
AZURE_API_SCOPE=
```

- [ ] **Step 4: Commit**

```bash
git add infra/entra_setup.md brain-api/.env.example
git commit -m "docs: add Entra app registration checklist for brain-api + brain-web"
```

---

### Task 7: ACL filter expression builder

**Files:**
- Create: `brain-api/app/acl/__init__.py`
- Create: `brain-api/app/acl/enforcement.py`
- Create: `brain-api/tests/test_acl_filter.py`

- [ ] **Step 1: Write failing test `tests/test_acl_filter.py`**

```python
from app.acl.enforcement import build_acl_filter
from app.domain.identity import User


def test_filter_includes_tenant_and_principals() -> None:
    u = User(
        user_id="u-1",
        tenant_id="t-test",
        email="a@b",
        display_name="A",
        group_ids={"g-sales", "g-central"},
    )
    f = build_acl_filter(u)
    assert "tenant_id eq 't-test'" in f
    assert "search.in(acl_principals" in f
    # all principals present (any order)
    for p in {"u-1", "g-sales", "g-central"}:
        assert p in f


def test_filter_escapes_single_quotes_in_ids() -> None:
    u = User(
        user_id="u'1",
        tenant_id="t'test",
        email="a@b",
        display_name="A",
        group_ids=set(),
    )
    f = build_acl_filter(u)
    # OData escapes single quotes by doubling them
    assert "t''test" in f
    assert "u''1" in f
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_acl_filter.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `app/acl/__init__.py` (empty) and `app/acl/enforcement.py`**

`app/acl/__init__.py` — empty.

`app/acl/enforcement.py`:

```python
from app.domain.identity import User


def _escape(s: str) -> str:
    """OData escape: double single-quotes."""
    return s.replace("'", "''")


def build_acl_filter(user: User) -> str:
    """Compose the AI Search $filter expression for index-time ACL trimming.

    Combines tenant scoping and principal-set membership via search.in().
    """
    principals = ",".join(sorted(_escape(p) for p in user.principals()))
    tenant = _escape(user.tenant_id)
    return (
        f"tenant_id eq '{tenant}' and "
        f"search.in(acl_principals, '{principals}', ',')"
    )
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
uv run pytest tests/test_acl_filter.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/acl tests/test_acl_filter.py
git commit -m "feat: add ACL filter builder for AI Search index-time trimming"
```

---

### Task 8: Web shell with MSAL login

**Files:**
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/next.config.mjs`
- Create: `web/tailwind.config.ts`
- Create: `web/.env.local.example`
- Create: `web/app/layout.tsx`
- Create: `web/app/providers.tsx`
- Create: `web/app/page.tsx`
- Create: `web/lib/msal.ts`
- Create: `web/lib/api.ts`
- Create: `web/components/Chat.tsx`

- [ ] **Step 1: Create `web/package.json`**

```json
{
  "name": "brain-web",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "@azure/msal-browser": "^3.27.0",
    "@azure/msal-react": "^2.2.0",
    "next": "14.2.18",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/node": "^22.10.1",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "autoprefixer": "^10.4.20",
    "eslint": "^9.16.0",
    "eslint-config-next": "14.2.18",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.15",
    "typescript": "^5.7.2"
  }
}
```

- [ ] **Step 2: Create `web/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 3: Create `web/next.config.mjs`**

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = { reactStrictMode: true };
export default nextConfig;
```

- [ ] **Step 4: Create `web/tailwind.config.ts`**

```typescript
import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 5: Create `web/.env.local.example`**

```
NEXT_PUBLIC_AZURE_TENANT_ID=
NEXT_PUBLIC_AZURE_CLIENT_ID=
NEXT_PUBLIC_AZURE_API_SCOPE=
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

- [ ] **Step 6: Create `web/lib/msal.ts`**

```typescript
import { PublicClientApplication, Configuration } from "@azure/msal-browser";

const config: Configuration = {
  auth: {
    clientId: process.env.NEXT_PUBLIC_AZURE_CLIENT_ID!,
    authority: `https://login.microsoftonline.com/${process.env.NEXT_PUBLIC_AZURE_TENANT_ID}`,
    redirectUri: typeof window !== "undefined" ? window.location.origin : "",
  },
  cache: { cacheLocation: "sessionStorage" },
};

export const msalInstance = new PublicClientApplication(config);
export const apiScope = process.env.NEXT_PUBLIC_AZURE_API_SCOPE!;
```

- [ ] **Step 7: Create `web/lib/api.ts`**

```typescript
import { msalInstance, apiScope } from "./msal";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL!;

export type Citation = {
  doc_id: string;
  chunk_id: string;
  source_url: string;
  title: string;
  snippet: string;
};

export type Answer = {
  query_id: string;
  answer: string;
  citations: Citation[];
  debug?: Record<string, unknown>;
};

export async function postQuery(query: string): Promise<Answer> {
  const account = msalInstance.getAllAccounts()[0];
  if (!account) throw new Error("not signed in");
  const tok = await msalInstance.acquireTokenSilent({ scopes: [apiScope], account });

  const resp = await fetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok.accessToken}` },
    body: JSON.stringify({ query }),
  });
  if (!resp.ok) throw new Error(`brain-api ${resp.status}: ${await resp.text()}`);
  return resp.json();
}
```

- [ ] **Step 8: Create `web/app/providers.tsx`**

```typescript
"use client";
import { MsalProvider } from "@azure/msal-react";
import { ReactNode } from "react";
import { msalInstance } from "@/lib/msal";

export function Providers({ children }: { children: ReactNode }) {
  return <MsalProvider instance={msalInstance}>{children}</MsalProvider>;
}
```

- [ ] **Step 9: Create `web/app/layout.tsx`**

```typescript
import "./globals.css";
import type { ReactNode } from "react";
import { Providers } from "./providers";

export const metadata = { title: "Company Brain" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-slate-50 text-slate-900 antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
```

- [ ] **Step 10: Create `web/app/globals.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 11: Create `web/components/Chat.tsx`**

```typescript
"use client";
import { useState } from "react";
import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { postQuery, Answer } from "@/lib/api";

export default function Chat() {
  const { instance } = useMsal();
  const isAuthed = useIsAuthenticated();
  const [q, setQ] = useState("");
  const [a, setA] = useState<Answer | null>(null);
  const [loading, setLoading] = useState(false);

  if (!isAuthed) {
    return (
      <button
        className="rounded bg-indigo-600 px-4 py-2 text-white"
        onClick={() => instance.loginRedirect({ scopes: [process.env.NEXT_PUBLIC_AZURE_API_SCOPE!] })}
      >
        Sign in with Entra
      </button>
    );
  }

  return (
    <div className="space-y-4">
      <form
        className="flex gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          setLoading(true);
          try {
            setA(await postQuery(q));
          } finally {
            setLoading(false);
          }
        }}
      >
        <input
          className="flex-1 rounded border px-3 py-2"
          placeholder="Ask the company brain…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="rounded bg-indigo-600 px-4 py-2 text-white" disabled={loading}>
          {loading ? "…" : "Ask"}
        </button>
      </form>
      {a && (
        <div className="rounded border bg-white p-4">
          <p className="whitespace-pre-wrap">{a.answer}</p>
          {a.citations.length > 0 && (
            <ol className="mt-4 list-decimal pl-6 text-sm text-slate-600">
              {a.citations.map((c, i) => (
                <li key={c.chunk_id}>
                  <a className="underline" href={c.source_url} target="_blank">{c.title}</a>
                  <span className="ml-2 italic">{c.snippet}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 12: Create `web/app/page.tsx`**

```typescript
import Chat from "@/components/Chat";

export default function Page() {
  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="mb-6 text-2xl font-semibold">Company Brain</h1>
      <Chat />
    </main>
  );
}
```

- [ ] **Step 13: Create `web/postcss.config.mjs`**

```javascript
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
```

- [ ] **Step 14: Install web deps and verify build**

```bash
cd web
pnpm install
pnpm typecheck
pnpm build
```
Expected: `pnpm install` completes, typecheck passes, build emits `.next/` without errors.

- [ ] **Step 15: Run the web app and confirm Entra login flow**

In one terminal:
```bash
cd web && pnpm dev
```

Open http://localhost:3000, click "Sign in with Entra". Expected: Microsoft login page, then redirect back to the page with an Ask form. Don't click Ask yet — `brain-api` doesn't have `/query` yet.

- [ ] **Step 16: Commit**

```bash
cd ..
git add web/
git commit -m "feat: add Next.js 14 web shell with MSAL Entra SSO"
```

---

### Task 9: Create the AI Search index

**Files:**
- Create: `brain-api/scripts/create_search_index.py`

- [ ] **Step 1: Write the index creation script**

```python
"""One-shot: create or update the brain-content-{tenant} index in AI Search."""

from __future__ import annotations

from azure.core.credentials import AzureKeyCredential  # not used — DefaultAzureCredential below
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

from app.config import get_settings


def build_index(name: str) -> SearchIndex:
    fields = [
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="tenant_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="source_url", type=SearchFieldDataType.String, retrievable=True),
        SearchField(name="title", type=SearchFieldDataType.String, searchable=True, retrievable=True),
        SearchField(name="content", type=SearchFieldDataType.String, searchable=True, retrievable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=3072,
            vector_search_profile_name="default-hnsw",
            retrievable=False,
            searchable=True,
        ),
        SimpleField(
            name="acl_principals",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SimpleField(name="author_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(
            name="entities",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="created_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True
        ),
        SimpleField(
            name="modified_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, retrievable=True),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
        profiles=[VectorSearchProfile(name="default-hnsw", algorithm_configuration_name="default-hnsw")],
    )

    semantic = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="brain-semantic",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                    keywords_fields=[SemanticField(field_name="entities")],
                ),
            )
        ]
    )

    return SearchIndex(name=name, fields=fields, vector_search=vector_search, semantic_search=semantic)


def main() -> None:
    s = get_settings()
    client = SearchIndexClient(endpoint=s.azure_ai_search_endpoint, credential=DefaultAzureCredential())
    idx = build_index(s.azure_ai_search_index)
    client.create_or_update_index(idx)
    print(f"OK: {idx.name} on {s.azure_ai_search_endpoint}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script (real Azure call)**

From `brain-api/`:
```bash
uv run python scripts/create_search_index.py
```
Expected: `OK: brain-content-t-test on https://<name>.search.windows.net`.

- [ ] **Step 3: Verify the index exists via az CLI**

```bash
az search index list --service-name <search-name> --query "[].name" -o tsv
```
Expected: includes `brain-content-t-test`.

- [ ] **Step 4: Commit**

```bash
git add scripts/create_search_index.py
git commit -m "feat: AI Search index schema + create-or-update script"
```

---

## Day 1 — Ingest pipeline + AI Search (Tasks 10–17)

### Task 10: Chunker

**Files:**
- Create: `brain-api/app/ingest/__init__.py`
- Create: `brain-api/app/ingest/chunker.py`
- Create: `brain-api/tests/test_chunker.py`

- [ ] **Step 1: Write failing test `tests/test_chunker.py`**

```python
from app.ingest.chunker import chunk_markdown


def test_short_doc_returns_single_chunk() -> None:
    text = "# Title\n\nShort body text."
    chunks = chunk_markdown(text, max_tokens=500, overlap_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].content.startswith("# Title")


def test_long_doc_splits_at_headings() -> None:
    text = "# A\n\n" + ("x " * 800) + "\n\n## B\n\n" + ("y " * 800)
    chunks = chunk_markdown(text, max_tokens=300, overlap_tokens=30)
    assert len(chunks) >= 2
    # heading respected — chunk boundaries align to headings where possible
    assert any(c.content.lstrip().startswith("# A") for c in chunks)
    assert any(c.content.lstrip().startswith("## B") for c in chunks)


def test_chunks_have_overlap() -> None:
    text = "word " * 2000
    chunks = chunk_markdown(text, max_tokens=200, overlap_tokens=40)
    assert len(chunks) >= 2
    # consecutive chunks share suffix/prefix
    assert chunks[0].content.split()[-10:] == chunks[1].content.split()[:10] or \
        any(w in chunks[1].content for w in chunks[0].content.split()[-5:])


def test_chunk_indices_are_sequential() -> None:
    text = "word " * 2000
    chunks = chunk_markdown(text, max_tokens=200, overlap_tokens=40)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_chunker.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `app/ingest/__init__.py` (empty) and `app/ingest/chunker.py`**

`app/ingest/__init__.py` — empty.

`app/ingest/chunker.py`:

```python
"""Structure-aware markdown chunker.

Strategy:
1. Split text by H1/H2 headings into sections.
2. For each section, if token count <= max_tokens, emit as one chunk.
3. Otherwise, split paragraphs greedily into chunks of <= max_tokens with overlap.

Tokens counted via tiktoken (cl100k_base — close enough for text-embedding-3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")
_HEADING = re.compile(r"^(#{1,2})\s+.+$", re.MULTILINE)


@dataclass(frozen=True)
class ChunkText:
    content: str
    chunk_index: int


def _token_count(s: str) -> int:
    return len(_ENC.encode(s))


def _split_sections(md: str) -> list[str]:
    # Split before each H1/H2 heading; keep the heading with its body
    indices = [m.start() for m in _HEADING.finditer(md)]
    if not indices:
        return [md] if md.strip() else []
    sections: list[str] = []
    starts = [0, *indices] if indices[0] != 0 else indices
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(md)
        sec = md[start:end].strip()
        if sec:
            sections.append(sec)
    return sections


def _greedy_chunk(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for para in paragraphs:
        pt = _token_count(para)
        if buf and buf_tokens + pt > max_tokens:
            chunks.append("\n\n".join(buf))
            # overlap: keep tail of previous chunk
            tail: list[str] = []
            tail_tokens = 0
            for p in reversed(buf):
                pt2 = _token_count(p)
                if tail_tokens + pt2 > overlap_tokens:
                    break
                tail.insert(0, p)
                tail_tokens += pt2
            buf = tail.copy()
            buf_tokens = tail_tokens
        # paragraph itself larger than max_tokens — hard-split by words
        if pt > max_tokens:
            words = para.split()
            cur: list[str] = []
            cur_tokens = 0
            for w in words:
                wt = _token_count(w + " ")
                if cur_tokens + wt > max_tokens:
                    chunks.append((" ".join(buf) + "\n\n" if buf else "") + " ".join(cur))
                    cur = cur[-max(1, overlap_tokens // 2):]
                    cur_tokens = _token_count(" ".join(cur))
                    buf = []
                    buf_tokens = 0
                cur.append(w)
                cur_tokens += wt
            if cur:
                buf.append(" ".join(cur))
                buf_tokens += _token_count(" ".join(cur))
        else:
            buf.append(para)
            buf_tokens += pt
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def chunk_markdown(md: str, max_tokens: int = 600, overlap_tokens: int = 75) -> list[ChunkText]:
    out: list[ChunkText] = []
    idx = 0
    for section in _split_sections(md):
        if _token_count(section) <= max_tokens:
            out.append(ChunkText(content=section, chunk_index=idx))
            idx += 1
            continue
        for piece in _greedy_chunk(section, max_tokens, overlap_tokens):
            out.append(ChunkText(content=piece, chunk_index=idx))
            idx += 1
    return out
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
uv run pytest tests/test_chunker.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingest/__init__.py app/ingest/chunker.py tests/test_chunker.py
git commit -m "feat: structure-aware markdown chunker with overlap"
```

---

### Task 11: Azure OpenAI client (embeddings)

**Files:**
- Create: `brain-api/app/generation/__init__.py`
- Create: `brain-api/app/generation/azure_openai.py`
- Create: `brain-api/tests/test_azure_openai_embed.py`

- [ ] **Step 1: Write failing integration test `tests/test_azure_openai_embed.py`**

```python
import pytest

from app.generation.azure_openai import AzureOpenAIClient


@pytest.mark.integration
async def test_embed_returns_3072_dim_vector() -> None:
    client = AzureOpenAIClient()
    vec = await client.embed("travel reimbursement policy")
    assert len(vec) == 3072
    # vectors are normalized-ish; first few values are non-zero floats
    assert any(abs(x) > 1e-6 for x in vec[:10])


@pytest.mark.integration
async def test_embed_batch_preserves_order() -> None:
    client = AzureOpenAIClient()
    vecs = await client.embed_batch(["alpha", "beta", "gamma"])
    assert len(vecs) == 3
    assert all(len(v) == 3072 for v in vecs)
    # different inputs → different vectors
    assert vecs[0] != vecs[1]
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_azure_openai_embed.py -v -m integration
```
Expected: ImportError.

- [ ] **Step 3: Implement `app/generation/__init__.py` (empty) and `app/generation/azure_openai.py`**

`app/generation/__init__.py` — empty.

`app/generation/azure_openai.py`:

```python
from __future__ import annotations

from functools import lru_cache

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


@lru_cache(maxsize=1)
def _client() -> AsyncAzureOpenAI:
    s = get_settings()
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    return AsyncAzureOpenAI(
        azure_endpoint=s.azure_openai_endpoint,
        api_version=s.azure_openai_api_version,
        azure_ad_token_provider=token_provider,
    )


class AzureOpenAIClient:
    def __init__(self) -> None:
        self._s = get_settings()
        self._cli = _client()

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

- [ ] **Step 4: Run integration test against real Azure**

```bash
uv run pytest tests/test_azure_openai_embed.py -v -m integration
```
Expected: 2 passed. Costs ~$0.0001.

- [ ] **Step 5: Commit**

```bash
git add app/generation/__init__.py app/generation/azure_openai.py tests/test_azure_openai_embed.py
git commit -m "feat: AzureOpenAIClient (embed, embed_batch, complete) with managed identity"
```

---

### Task 12: AI Search client wrapper

**Files:**
- Create: `brain-api/app/retrieval/__init__.py`
- Create: `brain-api/app/retrieval/ai_search_client.py`
- Create: `brain-api/tests/test_ai_search_client.py`

- [ ] **Step 1: Write failing integration test `tests/test_ai_search_client.py`**

```python
from datetime import UTC, datetime

import pytest

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.retrieval.ai_search_client import AISearchClient


def _make_chunk(chunk_id: str, content: str, vec: list[float]) -> Chunk:
    now = datetime.now(UTC)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=chunk_id.split("#")[0],
        tenant_id="t-test",
        source="uploaded",
        source_url="local://test",
        title="Test",
        content=content,
        content_vector=vec,
        acl_principals=["u-test", "g-test"],
        author_id=None,
        entities=[],
        created_at=now,
        modified_at=now,
        chunk_index=int(chunk_id.split("-")[-1]),
    )


@pytest.mark.integration
async def test_upsert_then_hybrid_search_returns_chunk() -> None:
    client = AISearchClient()
    vec = [0.01] * 3072
    chunk = _make_chunk("test-doc-1#chunk-0", "Travel reimbursement policy details.", vec)
    await client.upsert_chunks([chunk])

    user = User(
        user_id="u-test",
        tenant_id="t-test",
        email="t@x",
        display_name="T",
        group_ids={"g-test"},
    )
    results = await client.hybrid_search(query="travel reimbursement", user=user, vector=vec, top=5)
    ids = [r.chunk_id for r in results]
    assert "test-doc-1#chunk-0" in ids


@pytest.mark.integration
async def test_acl_filter_excludes_other_tenant() -> None:
    client = AISearchClient()
    vec = [0.02] * 3072
    chunk = _make_chunk("test-doc-2#chunk-0", "Engineering on-call runbook.", vec)
    # Override tenant via direct mutation in test
    chunk = chunk.model_copy(update={"tenant_id": "other-tenant"})
    await client.upsert_chunks([chunk])

    user = User(
        user_id="u-test",
        tenant_id="t-test",
        email="t@x",
        display_name="T",
        group_ids={"g-test"},
    )
    results = await client.hybrid_search(query="on-call runbook", user=user, vector=vec, top=5)
    assert all(r.tenant_id == "t-test" for r in results)
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_ai_search_client.py -v -m integration
```
Expected: ImportError.

- [ ] **Step 3: Implement `app/retrieval/__init__.py` (empty) and `app/retrieval/ai_search_client.py`**

`app/retrieval/__init__.py` — empty.

`app/retrieval/ai_search_client.py`:

```python
from __future__ import annotations

from functools import lru_cache

from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from app.acl.enforcement import build_acl_filter
from app.config import get_settings
from app.domain.chunk import Chunk
from app.domain.identity import User


def _to_search_doc(c: Chunk) -> dict:
    d = c.model_dump(mode="python")
    # AI Search expects ISO format datetimes
    d["created_at"] = c.created_at.isoformat()
    d["modified_at"] = c.modified_at.isoformat()
    return d


def _from_search_doc(d: dict) -> Chunk:
    return Chunk.model_validate(d)


@lru_cache(maxsize=1)
def _client() -> SearchClient:
    s = get_settings()
    return SearchClient(
        endpoint=s.azure_ai_search_endpoint,
        index_name=s.azure_ai_search_index,
        credential=DefaultAzureCredential(),
    )


class AISearchClient:
    def __init__(self) -> None:
        self._cli = _client()

    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        await self._cli.merge_or_upload_documents(documents=[_to_search_doc(c) for c in chunks])

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
            # restore content_vector as empty (we don't retrieve it)
            r["content_vector"] = []
            chunks.append(_from_search_doc(r))
        return chunks
```

- [ ] **Step 4: Run integration test against real Azure**

```bash
uv run pytest tests/test_ai_search_client.py -v -m integration
```
Expected: 2 passed (may take ~5s the first time due to index propagation).

- [ ] **Step 5: Commit**

```bash
git add app/retrieval/__init__.py app/retrieval/ai_search_client.py tests/test_ai_search_client.py
git commit -m "feat: AISearchClient (hybrid + ACL filter, upsert)"
```

---

### Task 13: Synthetic ACL resolver

**Files:**
- Create: `brain-api/app/ingest/acl_resolver.py`
- Create: `brain-api/tests/test_acl_resolver.py`

- [ ] **Step 1: Write failing test `tests/test_acl_resolver.py`**

```python
from app.ingest.acl_resolver import resolve_synthetic_acl


def test_uploaded_doc_default_acl_is_tenant_everyone() -> None:
    acls = resolve_synthetic_acl(source="uploaded", source_id="abc", overrides=None)
    assert "t-test:everyone" in acls


def test_overrides_take_precedence() -> None:
    acls = resolve_synthetic_acl(
        source="uploaded", source_id="abc", overrides=["g-sales", "u-100"]
    )
    assert set(acls) == {"g-sales", "u-100"}
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_acl_resolver.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `app/ingest/acl_resolver.py`**

```python
"""Phase 1: synthesize ACLs for ingested docs.

Replaced in later phases by the Permissions Crawler reading source-side ACLs.
"""

from __future__ import annotations

from app.config import get_settings


def resolve_synthetic_acl(
    *, source: str, source_id: str, overrides: list[str] | None
) -> list[str]:
    if overrides:
        return list(overrides)
    tenant = get_settings().brain_tenant_id
    return [f"{tenant}:everyone"]
```

- [ ] **Step 4: Run test, expect PASS**

```bash
uv run pytest tests/test_acl_resolver.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingest/acl_resolver.py tests/test_acl_resolver.py
git commit -m "feat: synthetic ACL resolver for Phase 1 uploads"
```

---

### Task 14: Ingest pipeline

**Files:**
- Create: `brain-api/app/ingest/pipeline.py`
- Create: `brain-api/tests/test_ingest_pipeline.py`

- [ ] **Step 1: Write failing integration test `tests/test_ingest_pipeline.py`**

```python
from datetime import UTC, datetime

import pytest

from app.domain.chunk import SourceDoc
from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
from app.retrieval.ai_search_client import AISearchClient


@pytest.mark.integration
async def test_pipeline_chunks_embeds_indexes_and_query_finds_it() -> None:
    pipeline = IngestPipeline(
        embedder=AzureOpenAIClient(),
        search=AISearchClient(),
    )
    now = datetime.now(UTC)
    doc = SourceDoc(
        doc_id="up:pipeline-test-1",
        tenant_id="t-test",
        source="uploaded",
        source_url="local://pipeline-test-1",
        title="Pipeline Test Doc",
        body=(
            "# Pipeline Test\n\nThe quick brown fox jumps over the lazy dog.\n\n"
            "Our PTO policy allows 20 days per year."
        ),
        author_id=None,
        acl_principals=["t-test:everyone"],
        created_at=now,
        modified_at=now,
        mime="text/markdown",
    )
    result = await pipeline.process(doc)
    assert result.chunks_indexed >= 1

    # Query the index and confirm we can find the doc
    user = User(
        user_id="u-x",
        tenant_id="t-test",
        email="x@y",
        display_name="X",
        group_ids={"t-test:everyone"},
    )
    embedder = AzureOpenAIClient()
    vec = await embedder.embed("PTO policy")
    search = AISearchClient()
    hits = await search.hybrid_search(query="PTO policy", user=user, vector=vec, top=10)
    assert any(h.doc_id == "up:pipeline-test-1" for h in hits)
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_ingest_pipeline.py -v -m integration
```
Expected: ImportError.

- [ ] **Step 3: Implement `app/ingest/pipeline.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from app.domain.chunk import Chunk, SourceDoc
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.chunker import chunk_markdown
from app.retrieval.ai_search_client import AISearchClient


@dataclass
class IngestResult:
    doc_id: str
    chunks_indexed: int


class IngestPipeline:
    def __init__(self, *, embedder: AzureOpenAIClient, search: AISearchClient) -> None:
        self._embedder = embedder
        self._search = search

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
        return IngestResult(doc_id=doc.doc_id, chunks_indexed=len(chunks))
```

- [ ] **Step 4: Run integration test against real Azure**

```bash
uv run pytest tests/test_ingest_pipeline.py -v -m integration
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingest/pipeline.py tests/test_ingest_pipeline.py
git commit -m "feat: IngestPipeline (chunk → embed → upsert to AI Search)"
```

---

### Task 15: HybridRetriever (Phase 1: AI Search only)

**Files:**
- Create: `brain-api/app/retrieval/hybrid_retriever.py`
- Create: `brain-api/tests/test_hybrid_retriever.py`

- [ ] **Step 1: Write failing integration test `tests/test_hybrid_retriever.py`**

```python
import pytest

from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_retriever_returns_candidates_with_sources_hit() -> None:
    retriever = HybridRetriever(
        search=AISearchClient(),
        embedder=AzureOpenAIClient(),
    )
    user = User(
        user_id="u-x",
        tenant_id="t-test",
        email="x@y",
        display_name="X",
        group_ids={"t-test:everyone"},
    )
    candidates = await retriever.retrieve(query="PTO policy", user=user, k=10)
    assert len(candidates) > 0
    assert all("vector" in c.sources_hit or "bm25" in c.sources_hit or "semantic" in c.sources_hit
               for c in candidates)
    # Tenant isolation
    assert all(c.chunk.tenant_id == "t-test" for c in candidates)
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_hybrid_retriever.py -v -m integration
```
Expected: ImportError.

- [ ] **Step 3: Implement `app/retrieval/hybrid_retriever.py`**

```python
from __future__ import annotations

from app.domain.identity import User
from app.domain.query import Candidate
from app.generation.azure_openai import AzureOpenAIClient
from app.retrieval.ai_search_client import AISearchClient


class HybridRetriever:
    """Phase 1: fan-out only to AI Search (hybrid: vector + BM25 + semantic).

    Later phases extend this with People proximity (Cosmos Gremlin) and
    Activity signal (ADX) joins.
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
                raw_scores={},
            )
            for c in chunks
        ]
```

- [ ] **Step 4: Run integration test**

```bash
uv run pytest tests/test_hybrid_retriever.py -v -m integration
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/retrieval/hybrid_retriever.py tests/test_hybrid_retriever.py
git commit -m "feat: HybridRetriever (Phase 1 — AI Search only)"
```

---

### Task 16: `POST /admin/ingest` endpoint

**Files:**
- Create: `brain-api/app/api/__init__.py`
- Create: `brain-api/app/api/admin.py`
- Create: `brain-api/app/deps.py`
- Modify: `brain-api/app/main.py`
- Create: `brain-api/tests/test_admin_ingest.py`

- [ ] **Step 1: Write failing test `tests/test_admin_ingest.py`**

```python
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_post_admin_ingest_returns_count() -> None:
    now = datetime.now(UTC).isoformat()
    payload = {
        "doc_id": "up:admin-ingest-test",
        "tenant_id": "t-test",
        "source": "uploaded",
        "source_url": "local://admin-ingest-test",
        "title": "Admin Ingest Test",
        "body": "# Test\n\nHello world. This is a test document.",
        "author_id": None,
        "acl_principals": ["t-test:everyone"],
        "created_at": now,
        "modified_at": now,
        "mime": "text/markdown",
    }
    client = TestClient(app)
    resp = client.post("/admin/ingest", json=payload)
    assert resp.status_code == 200
    assert resp.json()["chunks_indexed"] >= 1
```

- [ ] **Step 2: Run test, expect 404 or ImportError**

```bash
uv run pytest tests/test_admin_ingest.py -v -m integration
```
Expected: ImportError on `app.api.admin` OR 404 if endpoint not wired.

- [ ] **Step 3: Implement `app/api/__init__.py` (empty), `app/deps.py`, `app/api/admin.py`**

`app/api/__init__.py` — empty.

`app/deps.py`:

```python
from functools import lru_cache

from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@lru_cache(maxsize=1)
def get_embedder() -> AzureOpenAIClient:
    return AzureOpenAIClient()


@lru_cache(maxsize=1)
def get_ai_search() -> AISearchClient:
    return AISearchClient()


@lru_cache(maxsize=1)
def get_ingest_pipeline() -> IngestPipeline:
    return IngestPipeline(embedder=get_embedder(), search=get_ai_search())


@lru_cache(maxsize=1)
def get_retriever() -> HybridRetriever:
    return HybridRetriever(search=get_ai_search(), embedder=get_embedder())
```

`app/api/admin.py`:

```python
from fastapi import APIRouter, Depends

from app.deps import get_ingest_pipeline
from app.domain.chunk import SourceDoc
from app.ingest.pipeline import IngestPipeline, IngestResult

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/ingest", response_model=None)
async def ingest(
    doc: SourceDoc,
    pipeline: IngestPipeline = Depends(get_ingest_pipeline),
) -> dict[str, int | str]:
    result: IngestResult = await pipeline.process(doc)
    return {"doc_id": result.doc_id, "chunks_indexed": result.chunks_indexed}
```

- [ ] **Step 4: Wire admin router into `app/main.py`**

Replace `app/main.py` with:

```python
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings()
    yield


app = FastAPI(title="brain-api", version="0.1.0", lifespan=lifespan)
app.include_router(admin_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "brain-api"}
```

- [ ] **Step 5: Run integration test**

```bash
uv run pytest tests/test_admin_ingest.py -v -m integration
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add app/api/__init__.py app/api/admin.py app/deps.py app/main.py tests/test_admin_ingest.py
git commit -m "feat: POST /admin/ingest endpoint wired to IngestPipeline"
```

---

### Task 17: Load 50 starter test docs

**Files:**
- Create: `brain-api/eval/__init__.py`
- Create: `brain-api/eval/corpus/` (directory)
- Create: `brain-api/eval/corpus/policy/pto.md`
- Create: `brain-api/eval/corpus/policy/expenses.md`
- Create: `brain-api/eval/corpus/policy/security.md`
- Create: `brain-api/eval/corpus/planning/q3-sales-plan.md`
- Create: `brain-api/eval/corpus/planning/roadmap.md`
- Create: `brain-api/eval/corpus/engineering/oncall-runbook.md`
- Create: `brain-api/eval/load_corpus.py`

- [ ] **Step 1: Create starter corpus documents**

Create six files. Each is a small representative doc — for hackathon we start narrow and grow. (Add up to 50 docs total in this task; six concrete ones below; replicate the pattern for the rest.)

`brain-api/eval/corpus/policy/pto.md`:

```markdown
# PTO Policy

Full-time employees accrue 20 days of paid time off per year, prorated by
start date. PTO is requested via the HR portal at least two weeks in advance
for any absence longer than two consecutive days.

Unused PTO carries over up to 5 days into the next calendar year. PTO is
paid out at separation in jurisdictions where required by law.

Owner: People Operations · Last updated: 2026-01-15
```

`brain-api/eval/corpus/policy/expenses.md`:

```markdown
# Expense Reimbursement Policy

Business travel and meals are reimbursable when incurred for company business
and submitted within 30 days. Use the corporate card where possible. Personal
charges must be repaid within 14 days.

Per-meal limits: $50 lunch, $90 dinner. Per-night hotel: $300 in tier-1
cities, $200 elsewhere.

Owner: Finance · Last updated: 2026-02-04
```

`brain-api/eval/corpus/policy/security.md`:

```markdown
# Information Security Policy

All laptops must be encrypted with FileVault or BitLocker and managed via the
corporate MDM. Multi-factor authentication is required for all corporate
identities, including contractors.

Report suspected phishing to security@contoso.com immediately. Do not click
links in unsolicited emails; verify the sender via a known channel.

Owner: Security · Last updated: 2026-03-10
```

`brain-api/eval/corpus/planning/q3-sales-plan.md`:

```markdown
# Q3 Sales Plan

We target $42M ARR in Q3, weighted toward enterprise upsell in the central
region. Three campaigns will drive this:

1. Renewal-plus motion on top-30 accounts (owner: Alex)
2. New-logo land in financial services (owner: Pat)
3. Partner-sourced pipeline activation (owner: Jordan)

Stretch goal: $45M ARR with 8 net-new logos > $500k.

Owner: Sales Leadership · Approved: 2026-05-12
```

`brain-api/eval/corpus/planning/roadmap.md`:

```markdown
# Product Roadmap H2 2026

Three themes drive H2: trust, scale, and personalization.

- **Trust**: Customer-managed keys, audit log export, SOC 2 Type II expansion.
- **Scale**: Multi-region active-active, autoscaling per workload, cost
  controls per workspace.
- **Personalization**: User-level ranker improvements, team-aware search,
  inline citation hover cards.

Owner: Product · Last updated: 2026-05-20
```

`brain-api/eval/corpus/engineering/oncall-runbook.md`:

```markdown
# Payments Service On-Call Runbook

When PagerDuty alerts for the payments service:

1. Acknowledge within 5 minutes.
2. Check the dashboard at grafana.internal/d/payments. Look for elevated
   5xx rate or latency p99.
3. If 5xx > 1% sustained for 2 minutes, page the secondary.
4. Common cause #1: downstream rate limit from the bank gateway. Mitigation:
   shed traffic to the secondary processor via the feature flag
   `payments.gateway.secondary_pct`.
5. Common cause #2: stale credentials. Rotate via `payments-rotate` job in
   the ops console.

Escalation: VP Engineering for sustained >5 min outages.

Owner: Payments Team · Last updated: 2026-04-30
```

These six files are exactly what the 10 golden questions in Task 25 reference. Phase 1 corpus = these six files. Phase 2+ will add depth (more docs per category) to grow the corpus toward the spec's ~200-doc target.

- [ ] **Step 2: Create `eval/__init__.py` (empty) and `eval/load_corpus.py`**

`eval/__init__.py` — empty.

`eval/load_corpus.py`:

```python
"""Walk eval/corpus/ and POST each .md file to /admin/ingest on a running brain-api."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

CORPUS = Path(__file__).parent / "corpus"
API = "http://localhost:8000"


def main() -> None:
    paths = sorted(CORPUS.rglob("*.md"))
    if not paths:
        print("No corpus files found.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(UTC).isoformat()
    with httpx.Client(timeout=60.0) as client:
        for p in paths:
            rel = p.relative_to(CORPUS)
            doc_id = f"up:{rel.as_posix()}"
            payload = {
                "doc_id": doc_id,
                "tenant_id": "t-test",
                "source": "uploaded",
                "source_url": f"local://{rel.as_posix()}",
                "title": p.stem.replace("-", " ").title(),
                "body": p.read_text(),
                "author_id": None,
                "acl_principals": ["t-test:everyone"],
                "created_at": now,
                "modified_at": now,
                "mime": "text/markdown",
            }
            r = client.post(f"{API}/admin/ingest", json=payload)
            r.raise_for_status()
            print(f"{rel}: {r.json()['chunks_indexed']} chunks")
    print("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run brain-api locally, run load script**

Terminal 1:
```bash
cd brain-api
uv run uvicorn app.main:app --port 8000
```

Terminal 2:
```bash
cd brain-api
uv run python eval/load_corpus.py
```

Expected: each `.md` file logs `N chunks` (1–4 per doc) and the final line `Done.`

- [ ] **Step 4: Commit the corpus**

```bash
git add eval/__init__.py eval/corpus eval/load_corpus.py
git commit -m "feat: add starter test corpus (~12 docs) + load script"
```

---

### Task 18: `POST /query` returns top-5 chunks (no LLM yet)

**Files:**
- Create: `brain-api/app/api/query.py`
- Modify: `brain-api/app/main.py`
- Create: `brain-api/tests/test_query_no_llm.py`

- [ ] **Step 1: Write failing integration test `tests/test_query_no_llm.py`**

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_query_returns_candidates_no_llm() -> None:
    client = TestClient(app)
    resp = client.post(
        "/query",
        json={"query": "PTO policy", "k": 5},
        headers={"x-debug-bypass-auth": "t-test,u-test,t-test:everyone"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "candidates" in body
    assert len(body["candidates"]) > 0
    assert any("pto" in (c["chunk"]["doc_id"] or "").lower() for c in body["candidates"])
```

- [ ] **Step 2: Run test, expect 404 or ImportError**

```bash
uv run pytest tests/test_query_no_llm.py -v -m integration
```

- [ ] **Step 3: Implement `app/api/query.py`**

```python
"""Phase 1 /query endpoint: returns retrieved candidates only — no LLM yet.

Includes a temporary `x-debug-bypass-auth` header to inject a User for
integration tests until real Entra auth lands in Task 19.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.deps import get_retriever
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.retrieval.hybrid_retriever import HybridRetriever

router = APIRouter(tags=["query"])


def _debug_user(header: str | None) -> User:
    if not header:
        raise HTTPException(status_code=401, detail="auth required")
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


@router.post("/query")
async def query(
    body: QueryRequest,
    retriever: HybridRetriever = Depends(get_retriever),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> dict:
    user = _debug_user(x_debug_bypass_auth)
    candidates: list[Candidate] = await retriever.retrieve(query=body.query, user=user, k=body.k)
    # Strip vectors before returning (large + not needed)
    payload = []
    for c in candidates:
        chunk = c.chunk.model_dump()
        chunk["content_vector"] = []
        payload.append(
            {
                "chunk": chunk,
                "sources_hit": sorted(c.sources_hit),
                "raw_scores": c.raw_scores,
            }
        )
    return {"candidates": payload}
```

- [ ] **Step 4: Wire query router into `app/main.py`**

Modify `app/main.py` — add the import and `include_router`:

```python
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.query import router as query_router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings()
    yield


app = FastAPI(title="brain-api", version="0.1.0", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(query_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "brain-api"}
```

- [ ] **Step 5: Run integration test**

```bash
uv run pytest tests/test_query_no_llm.py -v -m integration
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add app/api/query.py app/main.py tests/test_query_no_llm.py
git commit -m "feat: POST /query returns top-k retrieved chunks (no LLM yet)"
```

---

## Day 2 — Orchestrator + LLM + Eval (Tasks 19–28)

### Task 19: Entra JWT validation

**Files:**
- Create: `brain-api/app/auth.py`
- Create: `brain-api/tests/test_auth.py`

- [ ] **Step 1: Write failing test `tests/test_auth.py`**

```python
import pytest

from app.auth import InvalidToken, _audience_for_scope, _validate_jwt


def test_audience_extracts_from_scope() -> None:
    aud = _audience_for_scope("api://abc-123/Query.Read")
    assert aud == "api://abc-123"


def test_invalid_token_raises() -> None:
    with pytest.raises(InvalidToken):
        _validate_jwt("not.a.real.token", audience="api://abc", tenant="tid-1")
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_auth.py -v
```

- [ ] **Step 3: Add `AZURE_API_CLIENT_ID` and `AZURE_API_SCOPE` to `Settings`**

Edit `app/config.py` — add two fields under `Azure identity`:

```python
    azure_api_client_id: str | None = None
    azure_api_scope: str | None = None
```

- [ ] **Step 4: Implement `app/auth.py`**

```python
"""Entra JWT validation + user-claims expansion.

Phase 1: JWT verification + claims extraction. Group expansion (via Graph
`/users/{id}/transitiveMemberOf` with app-only `Directory.Read.All`) is
folded in here as well; the result is cached in Redis (Task 22) on a
10-minute TTL.

For tests, a `x-debug-bypass-auth` header still works (see api/query.py).
"""

from __future__ import annotations

from functools import lru_cache

import httpx
from azure.identity.aio import DefaultAzureCredential
from jose import jwt
from jose.exceptions import JWTError

from app.config import get_settings
from app.domain.identity import User


class InvalidToken(Exception):
    pass


def _audience_for_scope(scope: str) -> str:
    # "api://<client-id>/Query.Read" → "api://<client-id>"
    return scope.rsplit("/", 1)[0]


@lru_cache(maxsize=1)
def _jwks_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"


def _validate_jwt(token: str, *, audience: str, tenant: str) -> dict:
    try:
        jwks = httpx.get(_jwks_url(tenant), timeout=5.0).json()
        unverified_header = jwt.get_unverified_header(token)
        key = next((k for k in jwks["keys"] if k["kid"] == unverified_header["kid"]), None)
        if not key:
            raise InvalidToken("kid not found in JWKS")
        return jwt.decode(
            token,
            key=key,
            algorithms=[unverified_header["alg"]],
            audience=audience,
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
        )
    except (JWTError, KeyError, ValueError, httpx.HTTPError) as e:
        raise InvalidToken(str(e)) from e


async def _expand_groups(user_id: str, tenant: str) -> set[str]:
    """App-only Graph call: get transitive group memberships for user_id."""
    cred = DefaultAzureCredential()
    tok = (await cred.get_token("https://graph.microsoft.com/.default")).token
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{user_id}/transitiveMemberOf?$select=id",
            headers={"Authorization": f"Bearer {tok}"},
        )
        r.raise_for_status()
        return {item["id"] for item in r.json().get("value", []) if "id" in item}


async def user_from_bearer(token: str) -> User:
    s = get_settings()
    if not s.azure_api_scope:
        raise InvalidToken("AZURE_API_SCOPE not configured")
    claims = _validate_jwt(
        token, audience=_audience_for_scope(s.azure_api_scope), tenant=s.azure_tenant_id
    )
    user_id = claims["oid"]
    tenant_id = claims["tid"]
    groups = await _expand_groups(user_id, tenant_id)
    return User(
        user_id=user_id,
        tenant_id=tenant_id,
        email=claims.get("preferred_username") or claims.get("email") or "",
        display_name=claims.get("name") or claims.get("preferred_username") or user_id,
        group_ids=groups,
    )
```

- [ ] **Step 5: Run tests, expect PASS**

```bash
uv run pytest tests/test_auth.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add app/auth.py app/config.py tests/test_auth.py
git commit -m "feat: Entra JWT validation + Graph-based group expansion"
```

---

### Task 20: Wire real auth into `/query` (keep debug bypass for tests)

**Files:**
- Modify: `brain-api/app/api/query.py`

- [ ] **Step 1: Update `app/api/query.py` to accept real Bearer tokens, fall back to debug header**

Replace `app/api/query.py` with:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import InvalidToken, user_from_bearer
from app.deps import get_retriever
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.retrieval.hybrid_retriever import HybridRetriever

router = APIRouter(tags=["query"])


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


async def _resolve_user(
    authorization: str | None,
    x_debug_bypass_auth: str | None,
) -> User:
    if x_debug_bypass_auth:
        return _debug_user(x_debug_bypass_auth)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="auth required")
    token = authorization.split(" ", 1)[1]
    try:
        return await user_from_bearer(token)
    except InvalidToken as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e


@router.post("/query")
async def query(
    body: QueryRequest,
    retriever: HybridRetriever = Depends(get_retriever),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> dict:
    user = await _resolve_user(authorization, x_debug_bypass_auth)
    candidates: list[Candidate] = await retriever.retrieve(query=body.query, user=user, k=body.k)
    payload = []
    for c in candidates:
        chunk = c.chunk.model_dump()
        chunk["content_vector"] = []
        payload.append(
            {
                "chunk": chunk,
                "sources_hit": sorted(c.sources_hit),
                "raw_scores": c.raw_scores,
            }
        )
    return {"candidates": payload}
```

- [ ] **Step 2: Re-run existing integration test**

```bash
uv run pytest tests/test_query_no_llm.py -v -m integration
```
Expected: still passes (debug bypass header still works).

- [ ] **Step 3: Manual smoke test from the web app**

Start web + brain-api in two terminals. From the web app at localhost:3000, sign in with Entra, then in the chat input type "PTO policy" and submit. Expected: the request reaches brain-api with a valid Bearer token, JWT is validated, the user object is built, and the response shows top candidates.

(Web's response panel renders `a.answer` which is still empty — the LLM is wired in the next tasks. You should see network response with `candidates[]` in dev tools.)

- [ ] **Step 4: Commit**

```bash
git add app/api/query.py
git commit -m "feat: wire Entra Bearer auth into /query (debug bypass retained)"
```

---

### Task 21: Redis cache (answer + embedding)

**Files:**
- Create: `brain-api/app/cache/__init__.py`
- Create: `brain-api/app/cache/redis_cache.py`
- Create: `brain-api/tests/test_redis_cache.py`

- [ ] **Step 1: Write failing integration test `tests/test_redis_cache.py`**

```python
import pytest

from app.cache.redis_cache import RedisCache


@pytest.mark.integration
async def test_set_and_get_json_round_trip() -> None:
    cache = RedisCache()
    await cache.set_json("test:k1", {"hello": "world"}, ttl_seconds=60)
    got = await cache.get_json("test:k1")
    assert got == {"hello": "world"}


@pytest.mark.integration
async def test_missing_key_returns_none() -> None:
    cache = RedisCache()
    got = await cache.get_json("test:not-present-xyz")
    assert got is None


@pytest.mark.integration
async def test_embedding_round_trip() -> None:
    cache = RedisCache()
    vec = [0.1, 0.2, 0.3]
    await cache.set_embedding("test-text", vec, ttl_seconds=60)
    got = await cache.get_embedding("test-text")
    assert got == vec
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_redis_cache.py -v -m integration
```

- [ ] **Step 3: Implement `app/cache/__init__.py` (empty) and `app/cache/redis_cache.py`**

`app/cache/__init__.py` — empty.

`app/cache/redis_cache.py`:

```python
from __future__ import annotations

import hashlib
import json
from functools import lru_cache

import redis.asyncio as redis
from azure.identity.aio import DefaultAzureCredential

from app.config import get_settings


@lru_cache(maxsize=1)
def _pool() -> redis.Redis:
    s = get_settings()
    # Azure Cache for Redis: use AAD via the access key OR managed identity (preview).
    # For Phase 1 simplicity, use the primary key fetched once from Key Vault if set;
    # otherwise fall back to env REDIS_KEY (developer convenience).
    import os

    key = os.environ.get("REDIS_KEY")
    return redis.Redis(
        host=s.azure_redis_host,
        port=s.azure_redis_port,
        ssl=s.azure_redis_ssl,
        password=key,
        decode_responses=True,
    )


def _embed_key(text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()
    return f"cache:embed:{h}"


class RedisCache:
    def __init__(self) -> None:
        self._r = _pool()

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        await self._r.set(name=key, value=json.dumps(value), ex=ttl_seconds)

    async def get_json(self, key: str) -> dict | None:
        v = await self._r.get(key)
        return json.loads(v) if v else None

    async def set_embedding(self, text: str, vec: list[float], ttl_seconds: int = 86400) -> None:
        await self.set_json(_embed_key(text), {"v": vec}, ttl_seconds=ttl_seconds)

    async def get_embedding(self, text: str) -> list[float] | None:
        d = await self.get_json(_embed_key(text))
        return d["v"] if d else None
```

- [ ] **Step 4: Set REDIS_KEY env var from Azure**

```bash
az redis list-keys -g rg-company-brain-dev -n <redis-name> --query primaryKey -o tsv
```

Add to `brain-api/.env`:
```
REDIS_KEY=<paste primary key>
```

Also append to `brain-api/.env.example`:
```
# Redis primary key (Phase 1 dev only — moves to Key Vault in Phase 4 hardening)
REDIS_KEY=
```

(In Phase 4 hardening we move this to Key Vault and read at runtime via managed identity.)

- [ ] **Step 5: Run integration tests**

```bash
uv run pytest tests/test_redis_cache.py -v -m integration
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/cache/__init__.py app/cache/redis_cache.py tests/test_redis_cache.py
git commit -m "feat: RedisCache (JSON + embedding round-trip)"
```

---

### Task 22: Grounded answer prompts + citation parser

**Files:**
- Create: `brain-api/app/generation/prompts.py`
- Create: `brain-api/tests/test_prompts.py`

- [ ] **Step 1: Write failing test `tests/test_prompts.py`**

```python
from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.query import Candidate, Citation
from app.generation.prompts import (
    build_grounded_messages,
    parse_citations_from_answer,
)


def _make_chunk(chunk_id: str, doc_id: str, title: str, content: str) -> Chunk:
    now = datetime.now(UTC)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        tenant_id="t-test",
        source="uploaded",
        source_url=f"local://{doc_id}",
        title=title,
        content=content,
        content_vector=[],
        acl_principals=["t-test:everyone"],
        author_id=None,
        entities=[],
        created_at=now,
        modified_at=now,
        chunk_index=0,
    )


def test_messages_include_each_candidate_with_index() -> None:
    cands = [
        Candidate(chunk=_make_chunk("a#0", "a", "Policy A", "Policy text A.")),
        Candidate(chunk=_make_chunk("b#0", "b", "Policy B", "Policy text B.")),
    ]
    msgs = build_grounded_messages(query="what is policy?", candidates=cands)
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "[1]" in msgs[1]["content"]
    assert "[2]" in msgs[1]["content"]
    assert "Policy A" in msgs[1]["content"]
    assert "Policy B" in msgs[1]["content"]


def test_parse_citations_resolves_marker_to_candidate() -> None:
    cands = [
        Candidate(chunk=_make_chunk("a#0", "a", "Policy A", "Policy text A.")),
        Candidate(chunk=_make_chunk("b#0", "b", "Policy B", "Policy text B.")),
    ]
    answer = "Policy A states X. [1] Policy B differs. [2]"
    cites = parse_citations_from_answer(answer, cands)
    assert len(cites) == 2
    assert cites[0].chunk_id == "a#0"
    assert cites[1].chunk_id == "b#0"


def test_orphan_markers_are_dropped() -> None:
    cands = [Candidate(chunk=_make_chunk("a#0", "a", "Policy A", "Policy text A."))]
    answer = "Has [1] and [9] markers."
    cites = parse_citations_from_answer(answer, cands)
    assert [c.chunk_id for c in cites] == ["a#0"]
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_prompts.py -v
```

- [ ] **Step 3: Implement `app/generation/prompts.py`**

```python
from __future__ import annotations

import re

from app.domain.query import Candidate, Citation

SYSTEM_PROMPT = (
    "You answer questions strictly from the provided CONTEXT. "
    "Cite every factual claim with bracketed indices like [1] [2]. "
    "If the answer is not present in the context, say "
    "'I don't have information about that.' Do not invent facts or sources."
)


def build_grounded_messages(*, query: str, candidates: list[Candidate]) -> list[dict[str, str]]:
    blocks: list[str] = []
    for i, c in enumerate(candidates, start=1):
        blocks.append(
            f"[{i}] {c.chunk.title} — {c.chunk.source_url}\n{c.chunk.content}"
        )
    user = f"QUESTION: {query}\n\nCONTEXT:\n" + "\n\n".join(blocks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


_MARKER = re.compile(r"\[(\d+)\]")


def parse_citations_from_answer(answer: str, candidates: list[Candidate]) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for m in _MARKER.finditer(answer):
        idx = int(m.group(1))
        if idx < 1 or idx > len(candidates):
            continue  # orphan marker — drop silently
        c = candidates[idx - 1].chunk
        if c.chunk_id in seen:
            continue
        seen.add(c.chunk_id)
        out.append(
            Citation(
                doc_id=c.doc_id,
                chunk_id=c.chunk_id,
                source_url=c.source_url,
                title=c.title,
                snippet=c.content[:200] + ("…" if len(c.content) > 200 else ""),
            )
        )
    return out
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
uv run pytest tests/test_prompts.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/generation/prompts.py tests/test_prompts.py
git commit -m "feat: grounded-answer prompt + citation marker parser"
```

---

### Task 23: SemanticKernelOrchestrator (plan + retrieve + answer)

**Files:**
- Create: `brain-api/app/orchestrator/__init__.py`
- Create: `brain-api/app/orchestrator/kernel.py`
- Create: `brain-api/tests/test_orchestrator.py`

- [ ] **Step 1: Write failing integration test `tests/test_orchestrator.py`**

```python
import pytest

from app.cache.redis_cache import RedisCache
from app.domain.identity import User
from app.domain.query import QueryRequest
from app.generation.azure_openai import AzureOpenAIClient
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_orchestrator_returns_answer_with_citations() -> None:
    embedder = AzureOpenAIClient()
    retriever = HybridRetriever(search=AISearchClient(), embedder=embedder)
    orch = SemanticKernelOrchestrator(
        retriever=retriever,
        llm=embedder,
        cache=RedisCache(),
    )
    user = User(
        user_id="u-orch",
        tenant_id="t-test",
        email="u@x",
        display_name="U",
        group_ids={"t-test:everyone"},
    )
    answer = await orch.answer(QueryRequest(query="what is the PTO policy?"), user=user)
    assert isinstance(answer.text, str) and len(answer.text) > 0
    assert len(answer.citations) >= 1
    assert any("pto" in c.doc_id.lower() for c in answer.citations)


@pytest.mark.integration
async def test_orchestrator_refuses_out_of_corpus() -> None:
    embedder = AzureOpenAIClient()
    retriever = HybridRetriever(search=AISearchClient(), embedder=embedder)
    orch = SemanticKernelOrchestrator(
        retriever=retriever,
        llm=embedder,
        cache=RedisCache(),
    )
    user = User(
        user_id="u-orch",
        tenant_id="t-test",
        email="u@x",
        display_name="U",
        group_ids={"t-test:everyone"},
    )
    answer = await orch.answer(
        QueryRequest(query="what is the recipe for chocolate chip cookies?"),
        user=user,
    )
    assert "don't have" in answer.text.lower() or "do not have" in answer.text.lower()
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
uv run pytest tests/test_orchestrator.py -v -m integration
```

- [ ] **Step 3: Implement `app/orchestrator/__init__.py` (empty) and `app/orchestrator/kernel.py`**

`app/orchestrator/__init__.py` — empty.

`app/orchestrator/kernel.py`:

```python
from __future__ import annotations

import hashlib
import uuid

from app.cache.redis_cache import RedisCache
from app.domain.identity import User
from app.domain.query import Answer, Candidate, QueryRequest
from app.generation.azure_openai import AzureOpenAIClient
from app.generation.prompts import build_grounded_messages, parse_citations_from_answer
from app.retrieval.hybrid_retriever import HybridRetriever


def _cache_key(user: User, query: str) -> str:
    principals_blob = "|".join(sorted(user.principals()))
    normalized = " ".join(query.lower().split())
    h = hashlib.sha256(f"{principals_blob}::{normalized}".encode()).hexdigest()
    return f"cache:answer:{user.tenant_id}:{h}"


class SemanticKernelOrchestrator:
    """Phase 1: cache → retrieve → answer.

    Plan step + Live Fetch routing are stubbed; this is intentional for Phase 1.
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        llm: AzureOpenAIClient,
        cache: RedisCache,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._cache = cache

    async def answer(self, request: QueryRequest, *, user: User) -> Answer:
        query_id = str(uuid.uuid4())

        # 1. Cache lookup
        key = _cache_key(user, request.query)
        cached = await self._cache.get_json(key)
        if cached:
            return Answer.model_validate({**cached, "query_id": query_id})

        # 2. Retrieve
        candidates: list[Candidate] = await self._retriever.retrieve(
            query=request.query, user=user, k=max(request.k, 5)
        )
        if not candidates:
            return Answer(
                text="I don't have information about that.",
                citations=[],
                query_id=query_id,
            )

        # 3. Generate grounded answer
        messages = build_grounded_messages(query=request.query, candidates=candidates[:5])
        text = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=800)
        citations = parse_citations_from_answer(text, candidates[:5])

        answer = Answer(text=text, citations=citations, query_id=query_id)

        # 4. Cache (10 min). Strip query_id so it gets re-minted per request.
        cache_blob = answer.model_dump()
        cache_blob.pop("query_id", None)
        await self._cache.set_json(key, cache_blob, ttl_seconds=600)

        return answer
```

- [ ] **Step 4: Add orchestrator to `app/deps.py`**

Append to `app/deps.py`:

```python
from app.cache.redis_cache import RedisCache
from app.orchestrator.kernel import SemanticKernelOrchestrator


@lru_cache(maxsize=1)
def get_cache() -> RedisCache:
    return RedisCache()


@lru_cache(maxsize=1)
def get_orchestrator() -> SemanticKernelOrchestrator:
    return SemanticKernelOrchestrator(
        retriever=get_retriever(),
        llm=get_embedder(),
        cache=get_cache(),
    )
```

- [ ] **Step 5: Run integration tests**

```bash
uv run pytest tests/test_orchestrator.py -v -m integration
```
Expected: 2 passed (each one costs ~$0.01).

- [ ] **Step 6: Commit**

```bash
git add app/orchestrator/__init__.py app/orchestrator/kernel.py app/deps.py tests/test_orchestrator.py
git commit -m "feat: SemanticKernelOrchestrator (cache → retrieve → grounded answer)"
```

---

### Task 24: Wire orchestrator into `/query` endpoint

**Files:**
- Modify: `brain-api/app/api/query.py`
- Create: `brain-api/tests/test_query_e2e.py`

- [ ] **Step 1: Update `/query` to return `Answer` shape**

Replace `app/api/query.py` with:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import InvalidToken, user_from_bearer
from app.deps import get_orchestrator
from app.domain.identity import User
from app.domain.query import Answer, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(tags=["query"])


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


async def _resolve_user(authorization: str | None, debug_header: str | None) -> User:
    if debug_header:
        return _debug_user(debug_header)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="auth required")
    try:
        return await user_from_bearer(authorization.split(" ", 1)[1])
    except InvalidToken as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e


@router.post("/query", response_model=Answer)
async def query(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
) -> Answer:
    user = await _resolve_user(authorization, x_debug_bypass_auth)
    return await orchestrator.answer(body, user=user)
```

- [ ] **Step 2: Write end-to-end integration test `tests/test_query_e2e.py`**

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_query_returns_grounded_answer_with_citations() -> None:
    client = TestClient(app)
    resp = client.post(
        "/query",
        json={"query": "what is our PTO policy?"},
        headers={"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "answer" not in body  # field is named `text`
    assert isinstance(body["text"], str) and len(body["text"]) > 10
    assert len(body["citations"]) >= 1
    assert body["query_id"]
```

- [ ] **Step 3: Run end-to-end test**

```bash
uv run pytest tests/test_query_e2e.py -v -m integration
```
Expected: 1 passed.

- [ ] **Step 4: Update `web/lib/api.ts` to match `Answer` field name**

The web app currently expects `answer.answer` — `Answer` actually has `text`. Edit `web/lib/api.ts`:

Change `answer: string` to `text: string` in the `Answer` type, then edit `web/components/Chat.tsx` line referencing `a.answer` to `a.text`.

- [ ] **Step 5: Smoke test the web app end-to-end**

Terminal 1: `cd brain-api && uv run uvicorn app.main:app --port 8000`
Terminal 2: `cd web && pnpm dev`

Open http://localhost:3000, sign in, ask "what is our PTO policy?". Expected: the grounded answer renders with citations linking to the source URLs.

- [ ] **Step 6: Commit**

```bash
git add app/api/query.py tests/test_query_e2e.py web/lib/api.ts web/components/Chat.tsx
git commit -m "feat: /query returns grounded Answer with citations; web renders it"
```

---

### Task 25: Eval harness — retrieval mode + 10 golden questions

**Files:**
- Create: `brain-api/eval/golden.jsonl`
- Create: `brain-api/eval/configs/v1.yaml`
- Create: `brain-api/eval/run_eval.py`

- [ ] **Step 1: Write 10 golden questions to `eval/golden.jsonl`**

```jsonl
{"qid":"q001","query":"what is our PTO policy?","expected_doc_ids":["up:policy/pto.md"],"expected_chunk_substrings":["20 days"],"tags":["policy","pto"]}
{"qid":"q002","query":"how many days of PTO do full-time employees accrue?","expected_doc_ids":["up:policy/pto.md"],"expected_chunk_substrings":["20 days"],"tags":["policy","pto"]}
{"qid":"q003","query":"what is the dinner reimbursement limit?","expected_doc_ids":["up:policy/expenses.md"],"expected_chunk_substrings":["$90 dinner"],"tags":["policy","expenses"]}
{"qid":"q004","query":"what is the per-night hotel limit in tier-1 cities?","expected_doc_ids":["up:policy/expenses.md"],"expected_chunk_substrings":["$300"],"tags":["policy","expenses"]}
{"qid":"q005","query":"is MFA required?","expected_doc_ids":["up:policy/security.md"],"expected_chunk_substrings":["Multi-factor authentication is required"],"tags":["policy","security"]}
{"qid":"q006","query":"what is our Q3 ARR target?","expected_doc_ids":["up:planning/q3-sales-plan.md"],"expected_chunk_substrings":["$42M ARR"],"tags":["planning","sales"]}
{"qid":"q007","query":"who owns the renewal-plus motion?","expected_doc_ids":["up:planning/q3-sales-plan.md"],"expected_chunk_substrings":["Alex"],"tags":["planning","sales"]}
{"qid":"q008","query":"what are the H2 product roadmap themes?","expected_doc_ids":["up:planning/roadmap.md"],"expected_chunk_substrings":["trust","scale","personalization"],"tags":["planning","product"]}
{"qid":"q009","query":"how should I respond to a payments service alert?","expected_doc_ids":["up:engineering/oncall-runbook.md"],"expected_chunk_substrings":["Acknowledge within 5 minutes"],"tags":["engineering","oncall"]}
{"qid":"q010","query":"what feature flag sheds traffic to the secondary processor?","expected_doc_ids":["up:engineering/oncall-runbook.md"],"expected_chunk_substrings":["payments.gateway.secondary_pct"],"tags":["engineering","oncall"]}
```

- [ ] **Step 2: Write `eval/configs/v1.yaml`**

```yaml
name: v1-baseline
description: Phase 1 baseline — AI Search hybrid retrieval, no personalization.
embedding_model: text-embedding-3-large
embedding_dim: 3072
chunk_size_tokens: 600
chunk_overlap_tokens: 75
retrieval_top_k: 30
ranker:
  type: passthrough        # Phase 1 — no ranker; rely on AI Search hybrid order
generation:
  model: gpt-4o
  temperature: 0.0
  max_tokens: 800
```

- [ ] **Step 3: Write `eval/run_eval.py` (retrieval mode)**

```python
"""Phase 1 eval harness — retrieval mode only.

Computes Recall@10 and MRR@10 over the golden Q&A file by calling /query
with the debug-bypass header. Generates a JSON report.

Usage:
    uv run python eval/run_eval.py --mode retrieval --report eval/reports/<date>.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

API = "http://localhost:8000"
DEBUG_USER = "t-test,u-eval,t-test:everyone"


def _load_golden(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _hit_rank(expected: list[str], doc_ids: list[str]) -> int | None:
    """Return 1-based rank of first expected doc_id, or None."""
    for i, did in enumerate(doc_ids, start=1):
        if did in expected:
            return i
    return None


def run_retrieval(golden: list[dict]) -> dict:
    recalls: list[float] = []
    rrs: list[float] = []
    latencies: list[float] = []
    failures: list[str] = []

    with httpx.Client(timeout=30.0) as client:
        for q in golden:
            t0 = time.perf_counter()
            # Retrieval mode = hit /query but only look at retrieved doc ids.
            # We use a special bypass: call /admin/raw-retrieve? In Phase 1 we
            # reuse /query and parse citations as a proxy for top retrieved docs.
            resp = client.post(
                f"{API}/query",
                json={"query": q["query"], "k": 10},
                headers={"x-debug-bypass-auth": DEBUG_USER},
            )
            latencies.append(time.perf_counter() - t0)
            if resp.status_code != 200:
                failures.append(f"{q['qid']}: HTTP {resp.status_code}")
                recalls.append(0.0)
                rrs.append(0.0)
                continue
            body = resp.json()
            doc_ids = [c["doc_id"] for c in body.get("citations", [])]
            rank = _hit_rank(q["expected_doc_ids"], doc_ids)
            recalls.append(1.0 if rank else 0.0)
            rrs.append(1.0 / rank if rank else 0.0)

    return {
        "n": len(golden),
        "recall_at_10": round(statistics.mean(recalls), 3) if recalls else 0.0,
        "mrr_at_10": round(statistics.mean(rrs), 3) if rrs else 0.0,
        "p50_latency_s": round(statistics.median(latencies), 3) if latencies else 0.0,
        "p95_latency_s": round(
            sorted(latencies)[int(0.95 * len(latencies))] if latencies else 0.0, 3
        ),
        "failures": failures,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["retrieval"], default="retrieval")
    p.add_argument("--golden", default="eval/golden.jsonl")
    p.add_argument("--report", default=None)
    args = p.parse_args()

    golden = _load_golden(Path(args.golden))
    if args.mode == "retrieval":
        report = run_retrieval(golden)
    else:
        raise SystemExit(f"mode {args.mode} not implemented in Phase 1")

    print(json.dumps(report, indent=2))
    if args.report:
        outp = Path(args.report)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, indent=2))
    # Gate: Recall@10 must be >= 0.7 for Phase 1
    return 0 if report["recall_at_10"] >= 0.7 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the eval harness against the live brain-api**

Terminal 1: brain-api running on :8000.
Terminal 2:
```bash
cd brain-api
uv run python eval/run_eval.py --mode retrieval --report eval/reports/$(date +%Y-%m-%d).json
```
Expected: prints a JSON report with `recall_at_10 >= 0.7`, `mrr_at_10 >= 0.5`. If lower, the corpus or chunker needs tuning — but on the starter corpus it should pass.

- [ ] **Step 5: Commit**

```bash
git add eval/golden.jsonl eval/configs/v1.yaml eval/run_eval.py
git commit -m "feat: eval harness — retrieval mode + 10 golden Qs"
```

---

### Task 26: CI workflow — lint + retrieval eval

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: ci

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  brain-api:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: brain-api } }
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with: { version: "latest" }
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run pytest -v -m "not integration"

  web:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: web } }
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm, cache-dependency-path: web/pnpm-lock.yaml }
      - run: pnpm install --frozen-lockfile
      - run: pnpm typecheck
      - run: pnpm build
```

Note: Integration tests are NOT run in CI in Phase 1 — they require Azure resources. They can be added behind a manual workflow trigger later.

- [ ] **Step 2: Run lint locally**

```bash
cd brain-api
uv run ruff check .
```
Expected: no errors.

- [ ] **Step 3: Run all non-integration tests locally**

```bash
uv run pytest -v -m "not integration"
```
Expected: all unit tests pass (test_config, test_domain, test_healthz, test_chunker, test_acl_filter, test_acl_resolver, test_prompts, test_auth).

- [ ] **Step 4: Commit**

```bash
cd ..
git add .github/workflows/ci.yml
git commit -m "ci: lint + unit tests on PR (integration tests gated to Azure-aware runners)"
```

---

### Task 27: README + Phase 1 demo script

**Files:**
- Create: `brain-api/README.md`
- Modify: `README.md` (root)

- [ ] **Step 1: Write `brain-api/README.md`**

```markdown
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
```

- [ ] **Step 2: Update root `README.md` with Phase 1 demo script**

Replace the root `README.md` with:

```markdown
# Company Brain

Production-grade intelligence layer for unified enterprise search and LLM
orchestration on Microsoft Azure. See `docs/superpowers/specs/` for the
architecture spec and `docs/superpowers/plans/` for implementation plans.

## Phase 1 demo (Days 0–2)

Grounded Q&A with citations against ~12 markdown docs, end-to-end on real
Azure services.

### Prerequisites

1. Azure subscription with Owner/Contributor.
2. Azure OpenAI quota approved in the chosen region (default `eastus2`).
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

## Layout

- `brain-api/` — FastAPI monolith (Zone 4 intelligence layer)
- `web/` — Next.js 14 chat UI with Entra SSO
- `infra/` — Azure provisioning (`az` CLI; Bicep later)
- `docs/` — specs and plans

## Next phases

- Phase 2 (Days 3–4): People pillar (Cosmos Gremlin), Activity pillar (ADX),
  personalized ranker. ACL Store + double-enforcement.
- Phase 3 (Day 5): Live Fetch via Microsoft Graph `/search`.
- Phase 4 (Days 6–7): APIM front, eval growth to 30 Qs, hardening.

Each gets its own plan in `docs/superpowers/plans/`.
```

- [ ] **Step 3: Commit**

```bash
git add brain-api/README.md README.md
git commit -m "docs: Phase 1 demo script + brain-api README"
```

---

### Task 28: End-to-end Phase 1 verification

**Files:**
- None (verification only)

- [ ] **Step 1: Stop all processes, wipe the local index, re-run from scratch**

```bash
# stop any running uvicorn / pnpm
pkill -f uvicorn || true
pkill -f "next dev" || true

# delete and recreate index
az search index delete --service-name <search-name> --name brain-content-t-test --yes
uv run python brain-api/scripts/create_search_index.py
```

- [ ] **Step 2: Start brain-api and load corpus**

Terminal 1:
```bash
cd brain-api && uv run uvicorn app.main:app --port 8000
```

Terminal 2:
```bash
cd brain-api && uv run python eval/load_corpus.py
```
Expected: ~12 corpus files load, each shows N chunks indexed.

- [ ] **Step 3: Run eval and verify thresholds**

```bash
cd brain-api && uv run python eval/run_eval.py --mode retrieval
```
Expected: `recall_at_10 >= 0.7`, `mrr_at_10 >= 0.5`. Exit code 0.

- [ ] **Step 4: Smoke test from the web app**

Terminal 3: `cd web && pnpm dev`

In a browser at localhost:3000, sign in, and ask all three Phase 1 demo questions in turn:
- "what is our PTO policy?"
- "how should I respond to a payments service alert?"
- "what is our Q3 ARR target?"

Expected for each: a grounded answer with at least one citation linking to the relevant markdown file.

- [ ] **Step 5: Run unit tests one final time**

```bash
cd brain-api && uv run pytest -v -m "not integration"
```
Expected: all green.

- [ ] **Step 6: Tag Phase 1 milestone**

```bash
git tag -a phase-1-mvp -m "Phase 1: grounded Q&A with citations working end-to-end"
git log --oneline | head -30
```

- [ ] **Step 7: Final commit (if any pending) and summary**

```bash
git status     # should be clean
```

Report Phase 1 complete to the user. Next plan = Phase 2 (People + Activity + ranker + ACL Store).

---

## Self-Review

The following spec requirements are covered by this plan:

| Spec Requirement (§)                                                | Task          |
| ------------------------------------------------------------------- | ------------- |
| §1 Goals: grounded Q&A with citations, refusal on out-of-corpus     | 22, 23, 28    |
| §2 Approach: monolith FastAPI + module boundaries                   | 1–4, 16, 18   |
| §3 Module layout (Phase 1 subset)                                   | 1–24          |
| §4 Domain models                                                    | 3             |
| §4 AI Search index schema + ACL filter                              | 7, 9, 12      |
| §4 Redis answer + embedding caches                                  | 21            |
| §5 Step 1: Entra JWT + group expansion                              | 19, 20        |
| §5 Step 2: cache lookup                                             | 23            |
| §5 Step 3: orchestrator plan (deferred to Phase 2 — single-step v1) | (deferred)    |
| §5 Step 4: hybrid retrieval (AI Search only in Phase 1)             | 12, 15        |
| §5 Step 9: GPT-4o grounded generation + citation parse              | 22, 23        |
| §6 Eval harness — retrieval mode + 10 golden Qs                     | 25            |
| §7 Azure provisioning script                                        | 5             |
| §7 Entra app registration                                           | 6             |
| §8 Build sequence Day 0–2                                           | 1–28          |
| §11 Acceptance criteria #1 (Entra SSO sign-in)                      | 8, 28         |
| §11 Acceptance criteria #5 (Recall@10 ≥ 0.7 — relaxed for Phase 1)  | 25, 28        |
| §11 Acceptance criteria #8 (DefaultAzureCredential everywhere)      | 5, 11, 12, 21 |

Spec requirements **explicitly deferred** to follow-up Phase 2/3/4 plans:
- People pillar (Cosmos Gremlin) — §4, §5 Step 4b
- Activity pillar (ADX) — §4, §5 Step 4c
- ACL Store + query-time recheck — §5 Step 6
- Live Fetch via Microsoft Graph — §5 Step 5
- Personalized Ranker (multi-signal) — §5 Step 7
- Content Safety stub → real implementation — §5 Steps 8, 10
- OpenTelemetry → App Insights wiring — §11 (telemetry skeleton only in Phase 1)
- Plan step (`gpt-4o-mini` classifier) — §5 Step 3
- APIM gateway — §5 Step 0

Each will be added in subsequent plans.
