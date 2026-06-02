"""Subscription helpers + Redis-backed SubscriptionStore (fake Redis)."""
from datetime import UTC, datetime, timedelta

import pytest

from app.connectors.models import SubscriptionRecord
from app.connectors.subscriptions import (
    SubscriptionStore,
    parse_notification_resource,
    provider_for_resource,
    resource_for,
)


# ---- pure helpers ----

def test_resource_for():
    assert resource_for("outlook_mail", "u1") == "users/u1/messages"
    assert resource_for("outlook_calendar", "u1") == "users/u1/events"


def test_provider_for_resource():
    assert provider_for_resource("users/u1/messages/m1") == "outlook_mail"
    assert provider_for_resource("Users/u1/events/e1") == "outlook_calendar"
    assert provider_for_resource("users/u1/contacts") is None


def test_parse_notification_resource():
    uid, rid = parse_notification_resource("users/abc-123/messages/AAA=")
    assert uid == "abc-123"
    assert rid == "AAA="
    uid2, rid2 = parse_notification_resource("Users('xyz')/messages")
    assert uid2 == "xyz"
    assert rid2 is None  # collection, no specific id


# ---- SubscriptionStore (fake Redis) ----

class FakeRedis:
    def __init__(self):
        self.h = {}
        self.kv = {}

    async def hset(self, name, key, value):
        self.h.setdefault(name, {})[key] = value

    async def hgetall(self, name):
        return dict(self.h.get(name, {}))

    async def hdel(self, name, key):
        self.h.get(name, {}).pop(key, None)

    async def get(self, name):
        return self.kv.get(name)

    async def set(self, name, value, ex=None):
        self.kv[name] = value


def _rec(sub_id, user="u1", provider="outlook_mail", exp_minutes=4000):
    return SubscriptionRecord(
        subscription_id=sub_id, tenant_id="t-eval", connection_id="c1",
        provider=provider, user_id=user, resource=resource_for(provider, user),
        expiration=datetime.now(UTC) + timedelta(minutes=exp_minutes),
    )


@pytest.mark.asyncio
async def test_put_list_delete_roundtrip():
    st = SubscriptionStore(client=FakeRedis())
    await st.put(_rec("s1"))
    await st.put(_rec("s2", user="u2"))
    subs = await st.list("t-eval")
    assert {s.subscription_id for s in subs} == {"s1", "s2"}
    await st.delete("t-eval", "s1")
    assert {s.subscription_id for s in await st.list("t-eval")} == {"s2"}


@pytest.mark.asyncio
async def test_list_expiring():
    st = SubscriptionStore(client=FakeRedis())
    await st.put(_rec("soon", exp_minutes=10))
    await st.put(_rec("later", exp_minutes=5000))
    threshold = datetime.now(UTC) + timedelta(minutes=60)
    expiring = await st.list_expiring("t-eval", threshold)
    assert [s.subscription_id for s in expiring] == ["soon"]


@pytest.mark.asyncio
async def test_delta_token_roundtrip():
    st = SubscriptionStore(client=FakeRedis())
    assert await st.get_delta("t-eval", "c1", "u1", "users/u1/messages") is None
    await st.set_delta("t-eval", "c1", "u1", "users/u1/messages", "LINK")
    assert await st.get_delta("t-eval", "c1", "u1", "users/u1/messages") == "LINK"


@pytest.mark.asyncio
async def test_store_noop_without_redis():
    st = SubscriptionStore(client=None)
    st._r = None  # simulate no-Redis deploy
    await st.put(_rec("s1"))
    assert await st.list("t-eval") == []
