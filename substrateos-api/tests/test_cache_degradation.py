from redis.exceptions import RedisError

from app.cache.redis_cache import RedisCache


class _FakeRedisRaising:
    async def get(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RedisError("boom")

    async def set(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RedisError("boom")


async def test_get_json_returns_none_on_redis_error() -> None:
    cache = RedisCache()
    cache._r = _FakeRedisRaising()
    assert await cache.get_json("k") is None


async def test_set_json_swallows_redis_error() -> None:
    cache = RedisCache()
    cache._r = _FakeRedisRaising()
    # Must not raise.
    await cache.set_json("k", {}, 60)
