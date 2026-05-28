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
