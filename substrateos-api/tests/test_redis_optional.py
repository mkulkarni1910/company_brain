import pytest

from app.cache.redis_cache import RedisCache
from app.history.store import HistoryStore


@pytest.mark.asyncio
async def test_cache_noop_without_host(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_REDIS_HOST", "")
    from app.config import get_settings
    get_settings.cache_clear()
    c = RedisCache()
    assert c._r is None
    await c.set_json("k", {"a": 1}, ttl_seconds=10)  # no-op, no raise
    assert await c.get_json("k") is None
    assert await c.get_embedding("x") is None
    await c.aclose()


@pytest.mark.asyncio
async def test_history_noop_without_host(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_REDIS_HOST", "")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.domain.identity import User
    u = User(user_id="u", tenant_id="t", email="", display_name="U", group_ids=set())
    s = HistoryStore()
    assert s._r is None
    await s.add(user=u, query="q", query_id="i")  # no-op
    assert await s.recent(user=u) == []
