from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Azure identity
    azure_tenant_id: str
    azure_client_id: str
    azure_api_client_id: str | None = None
    azure_api_scope: str | None = None

    # AI Search
    azure_ai_search_endpoint: str
    azure_ai_search_index: str

    # Azure OpenAI
    azure_openai_endpoint: str
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_plan_deployment: str = "gpt-4o"
    azure_openai_embed_deployment: str = "text-embedding-3-large"

    # Redis
    azure_redis_host: str
    azure_redis_port: int = 6380
    azure_redis_ssl: bool = True
    redis_key: str | None = None

    # Cosmos DB Gremlin (People pillar)
    cosmos_gremlin_endpoint: str | None = None
    cosmos_gremlin_key: str | None = None
    cosmos_gremlin_database: str = "brain"
    cosmos_gremlin_graph: str = "people"

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
    admin_api_key: str | None = None
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
