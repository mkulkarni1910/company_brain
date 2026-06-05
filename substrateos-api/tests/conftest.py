import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Reset Settings lru_cache so each test sees its own env."""
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _default_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default test env so Settings can instantiate without a real .env.

    Integration tests need the real .env values (real Azure endpoints), so skip
    overriding env vars when the test is marked `integration`.
    """
    if request.node.get_closest_marker("integration"):
        return
    defaults = {
        "AZURE_TENANT_ID": "tid-test",
        "AZURE_CLIENT_ID": "cid-test",
        "AZURE_AI_SEARCH_ENDPOINT": "https://test.search.windows.net",
        "AZURE_AI_SEARCH_INDEX": "substrateos-content-t-test",
        "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
        "AZURE_REDIS_HOST": "test.redis.cache.windows.net",
        "AZURE_REDIS_PORT": "6380",
        "SUBSTRATEOS_TENANT_ID": "t-test",
        "ENABLE_DEBUG_AUTH": "true",
    }
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)
