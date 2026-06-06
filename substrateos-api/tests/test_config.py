import pytest

from app.config import Settings


def test_settings_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_TENANT_ID", "tid-1")
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid-1")
    monkeypatch.setenv("AZURE_AI_SEARCH_ENDPOINT", "https://x.search.windows.net")
    monkeypatch.setenv("AZURE_AI_SEARCH_INDEX", "substrateos-content-t-test")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")
    monkeypatch.setenv("AZURE_OPENAI_PLAN_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-large")
    monkeypatch.setenv("AZURE_REDIS_HOST", "x.redis.cache.windows.net")

    s = Settings()
    assert s.azure_tenant_id == "tid-1"
    assert s.azure_ai_search_index == "substrateos-content-t-test"
    assert s.azure_openai_api_version == "2024-10-21"  # default
    assert s.substrateos_tenant_id == "t-test"               # default
    assert s.azure_redis_port == 6380                  # default


def test_github_settings_default_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    from app.config import Settings
    s = Settings()
    assert s.github_client_id is None
    assert s.github_client_secret is None


def test_github_secret_loaded_from_keyvault():
    from app.config import Settings, load_secrets_from_keyvault

    class _FakeSecret:
        def __init__(self, value): self.value = value

    class _FakeKV:
        def get_secret(self, name):
            if name == "github-client-secret":
                return _FakeSecret("gh-secret")
            raise KeyError(name)

    s = Settings()
    s.use_key_vault = True
    s.azure_key_vault_url = "https://kv.example"
    load_secrets_from_keyvault(s, client=_FakeKV())
    assert s.github_client_secret == "gh-secret"
