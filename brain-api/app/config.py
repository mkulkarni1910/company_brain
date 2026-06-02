from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Azure identity
    azure_tenant_id: str
    azure_client_id: str
    # Client-credentials secret for the connector app (env AZURE_CLIENT_SECRET) —
    # used app-only against an admin-consented tenant for the SharePoint connector.
    azure_client_secret: str | None = None
    azure_api_client_id: str | None = None
    azure_api_scope: str | None = None
    # OBO (on-behalf-of) confidential-client secret for the API app registration.
    # Loaded from Key Vault in prod (secret name: azure-api-client-secret).
    azure_api_client_secret: str | None = None

    # AI Search
    azure_ai_search_endpoint: str
    azure_ai_search_index: str
    azure_ai_search_key: str | None = None  # admin key; else DefaultAzureCredential (MI)

    # Azure OpenAI (embeddings; chat deployments kept as fallback)
    azure_openai_endpoint: str
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_plan_deployment: str = "gpt-4o"
    azure_openai_embed_deployment: str = "text-embedding-3-large"
    azure_openai_key: str | None = None  # api key; else AAD token provider (MI)

    # Gemini (answer generation). API key from Key Vault (secret: gemini-api-key).
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-pro"          # answer generation
    gemini_plan_model: str = "gemini-2.5-flash"   # fast model for the plan-step classifier
    gemini_thinking_budget: int = 256             # cap "thinking" tokens on the answer model (latency)
    gemini_endpoint: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_s: float = 60.0

    # Redis (optional — caching degrades to a no-op when host is unset)
    azure_redis_host: str | None = None
    azure_redis_port: int = 6380
    azure_redis_ssl: bool = True
    redis_key: str | None = None

    # Cosmos DB Gremlin (People pillar)
    cosmos_gremlin_endpoint: str | None = None
    cosmos_gremlin_key: str | None = None
    cosmos_gremlin_database: str = "brain"
    cosmos_gremlin_graph: str = "people"
    cosmos_gremlin_conversations_graph: str = "conversations"

    # Azure Data Explorer (Activity pillar)
    adx_cluster_uri: str | None = None
    adx_database: str = "brain"

    # Key Vault (optional in dev)
    azure_key_vault_url: str | None = None
    use_key_vault: bool = False

    # App Insights (optional in dev)
    applicationinsights_connection_string: str | None = None

    # Brain
    brain_tenant_id: str = "t-test"
    brain_log_level: str = "INFO"
    enable_debug_auth: bool = False
    # Single-org pilot: map every authenticated real user (Easy Auth / Bearer)
    # onto `brain_tenant_id` and grant the `<brain_tenant_id>:everyone` group, so
    # the loaded corpus is reachable without per-user provisioning. Off in tests.
    pilot_single_tenant: bool = False
    admin_api_key: str | None = None
    # SharePoint connector: hard cap on files ingested per site sync (no silent truncation).
    connector_max_items: int = 500
    # SharePoint admin-consent OAuth
    web_base_url: str = "http://localhost:3000"        # post-callback redirect (env WEB_BASE_URL)
    brain_api_base_url: str = "http://localhost:8000"  # our public base, for the OAuth redirect_uri (env BRAIN_API_BASE_URL)
    oauth_state_ttl_seconds: int = 600
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Programmatic access (Context API + MCP)
    token_prefix: str = "sbx_live_"
    mcp_enabled: bool = True
    public_base_url: str | None = None  # brain-api URL surfaced to the UI for snippets

    # ACL store
    acl_doc_ttl_seconds: int | None = None  # None = persistent (live ACL is authoritative)
    acl_fail_closed_on_missing: bool = False  # strict: drop docs with no live ACL entry

    # Personalized ranker weights (Phase 4: content + people + activity + recency; sum 1.0)
    rank_weight_content: float = 0.45
    rank_weight_people: float = 0.25
    rank_weight_activity: float = 0.15
    rank_weight_recency: float = 0.15

    # Live Fetch (Phase 3)
    live_fetch_enabled: bool = True
    live_fetch_timeout_ms: int = 600
    # When False (single-identity mode), live results are NOT trusted as per-user:
    # they go through the same fail-closed ACL recheck as indexed candidates (and
    # are dropped, since they carry no acl_principals). Set True ONLY when fetch()
    # uses a genuine per-user OBO token (Phase 4) — then Graph has already trimmed
    # hits to the requesting user and the recheck may be bypassed for them.
    live_fetch_obo_enabled: bool = False


def load_secrets_from_keyvault(settings: "Settings", client=None) -> None:
    """Overlay secrets from Key Vault onto settings (prod). Secret names:
    redis-key, cosmos-gremlin-key, admin-api-key, adx is AAD (no secret)."""
    if not settings.use_key_vault or not settings.azure_key_vault_url:
        return
    if client is None:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(
            vault_url=settings.azure_key_vault_url, credential=DefaultAzureCredential()
        )

    def _get(name):
        try:
            return client.get_secret(name).value
        except Exception:
            return None

    settings.redis_key = _get("redis-key") or settings.redis_key
    settings.cosmos_gremlin_key = _get("cosmos-gremlin-key") or settings.cosmos_gremlin_key
    settings.admin_api_key = _get("admin-api-key") or settings.admin_api_key
    settings.azure_api_client_secret = (
        _get("azure-api-client-secret") or settings.azure_api_client_secret
    )
    settings.gemini_api_key = _get("gemini-api-key") or settings.gemini_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
