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
    azure_openai_plan_deployment: str = "gpt-4-1-mini"
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

    # App Insights (optional in dev)
    applicationinsights_connection_string: str | None = None

    # Brain
    brain_tenant_id: str = "t-test"
    brain_log_level: str = "INFO"
    enable_debug_auth: bool = False
    admin_api_key: str | None = None

    # Personalized ranker weights (Phase 2b: content + people + activity)
    rank_weight_content: float = 0.5
    rank_weight_people: float = 0.3
    rank_weight_activity: float = 0.2


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
