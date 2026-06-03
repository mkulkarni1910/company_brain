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
    assert s.substrateos_tenant_id == "t-test"               # default
    assert s.azure_redis_port == 6380                  # default
