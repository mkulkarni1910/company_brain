import pytest

from app.cache.redis_cache import RedisCache


@pytest.mark.integration
async def test_set_and_get_json_round_trip() -> None:
    cache = RedisCache()
    await cache.set_json("test:k1", {"hello": "world"}, ttl_seconds=60)
    got = await cache.get_json("test:k1")
    assert got == {"hello": "world"}


@pytest.mark.integration
async def test_missing_key_returns_none() -> None:
    cache = RedisCache()
    got = await cache.get_json("test:not-present-xyz")
    assert got is None


@pytest.mark.integration
async def test_embedding_round_trip() -> None:
    cache = RedisCache()
    vec = [0.1, 0.2, 0.3]
    await cache.set_embedding("test-text", vec, ttl_seconds=60)
    got = await cache.get_embedding("test-text")
    assert got == vec
