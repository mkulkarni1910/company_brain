# SubstrateOS — Tech Stack Tracker

The canonical list of what we already run, so features reuse the stack instead of
adding redundant dependencies. **Check here before adding a library; update here
when you genuinely introduce one** (with a one-line why/where).

## Backend — `substrateos-api/`

| Tech | Used for |
|------|----------|
| **Python 3.12** + **uv** | language + dependency/venv manager (`uv sync`, `uv run`) |
| **FastAPI** + **uvicorn** | HTTP API, routers under `app/api/`, wired in `app/main.py` |
| **pydantic** / **pydantic-settings** | domain models (`app/domain/`) + config from env/Key Vault (`app/config.py`) |
| **semantic-kernel** | LLM orchestration (`app/orchestrator/`) |
| **openai** (Azure OpenAI) + **Gemini** | generation, grounded with citations (`app/generation/`) |
| **azure-search-documents** | Azure AI Search hybrid retrieval (`app/retrieval/`) |
| **gremlinpython** (Cosmos DB Gremlin) | People proximity graph (`app/people/`) |
| **azure-kusto-data** (Azure Data Explorer) | Activity signals (`app/activity/`) |
| **redis** | answer cache + ACL store (`app/cache/`, `app/acl/`) |
| **azure-identity** (`DefaultAzureCredential`) + **msal** | managed identity in cloud, `az login` locally, OBO to Graph |
| **azure-keyvault-secrets** | secrets that can't use managed identity |
| **python-jose** | Entra JWT validation (`app/auth.py`) |
| **httpx** + **tenacity** | outbound HTTP with retries — Microsoft Graph live fetch; GitHub REST API + OAuth token exchange (`app/connectors/github.py`, `app/api/github.py`) |
| **mcp** | MCP server surface (`app/mcp/`) |
| **tiktoken** | token accounting (`app/tokens/`) |
| **python-docx** / **pypdf** | document text extraction on ingest (`app/ingest/`) |
| **structlog** + **OpenTelemetry** → **Azure Monitor / App Insights** | structured logs + one trace per request |
| **pytest** (+ asyncio, cov) · **respx** | tests under `tests/` |
| **PyYAML** | policy-as-code specs (`policies/*.yaml`), loaded + validated by `app/policy/store.py` (governed act layer) |

## Frontend — `web/` (User Web Chat + Admin Panel, one Next.js app)

| Tech | Used for |
|------|----------|
| **Next.js 14** (App Router) | both frontends; user routes in `app/`, admin in `app/admin/` |
| **React 18** + **TypeScript** | components (`web/components/`), helpers (`web/lib/`) |
| **Tailwind CSS** + **PostCSS/Autoprefixer** | styling (`tailwind.config.ts`, `app/globals.css`) |
| **@azure/msal-browser** + **@azure/msal-react** | Entra SSO / bearer tokens to the API |
| **react-markdown** + **remark-gfm** | rendering grounded answers with citations |
| **pnpm** | package manager (`pnpm dev/build/lint/typecheck`) |

## Mockups — `mockups/`

Static HTML, no build step. Design system: **Fraunces** (serif headings),
**Archivo** (body), **JetBrains Mono** (labels); warm-paper palette via CSS
variables defined at the top of each file. Reuse those tokens — don't invent new
ones. Files: `user-web-chat.html`, `admin-portal.html`, `architecture.html`.

## Infra & deploy

| Tech | Used for |
|------|----------|
| **Azure Container Apps** (`rg-company-brain-india`, centralindia) | hosts `substrateos-api` + `substrateos-web` |
| **Azure Container Registry** (`cbrainindiaacr`) | images |
| **Docker** (local `linux/amd64` build) | deploy images |
| **Easy Auth** (Entra) | gate in front of the web app |
| `az` CLI provision scripts (`infra/`) | provisioning (no Bicep/Terraform yet) |
| **`substrateos-deploy` skill** | the deploy pipeline itself |
