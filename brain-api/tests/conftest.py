import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Reset Settings lru_cache so each test sees its own env."""
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_ai_search_client_cache() -> None:
    """Reset the AI Search client lru_cache between tests.

    The cached SearchClient holds an aiohttp session bound to the event
    loop that first created it. pytest-asyncio uses a per-test event loop,
    so reusing the cached client across tests fails with
    "Event loop is closed". Clearing forces a fresh client per test.
    """
    from app.retrieval.ai_search_client import _client

    _client.cache_clear()

    # app.deps also caches AISearchClient/HybridRetriever/IngestPipeline
    # instances that transitively hold the same loop-bound session.
    from app import deps

    deps.get_embedder.cache_clear()
    deps.get_ai_search.cache_clear()
    deps.get_ingest_pipeline.cache_clear()
    deps.get_retriever.cache_clear()


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
        "AZURE_AI_SEARCH_INDEX": "brain-content-t-test",
        "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
        "AZURE_REDIS_HOST": "test.redis.cache.windows.net",
    }
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)
