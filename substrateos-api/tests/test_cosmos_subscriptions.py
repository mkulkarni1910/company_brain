"""CosmosSubscriptionStore round-trips over the people-graph (fake) — the no-Redis
(India) path for Outlook realtime subscription + delta-token persistence."""
from datetime import UTC, datetime

import pytest

from app.connectors.models import SubscriptionRecord
from app.connectors.subscriptions import CosmosSubscriptionStore
from tests.test_connector_cosmos_store import FakeGraph


def _rec(sid="s1", tenant="t", user="u1", provider="outlook_mail", exp=None):
    return SubscriptionRecord(
        subscription_id=sid, tenant_id=tenant, connection_id="c1",
        provider=provider, user_id=user, resource=f"users/{user}/messages",
        expiration=exp or datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_sub_crud_and_expiring():
    st = CosmosSubscriptionStore(graph=FakeGraph())
    await st.put(_rec("s1", exp=datetime(2026, 1, 1, tzinfo=UTC)))
    await st.put(_rec("s2", user="u2", exp=datetime(2027, 1, 1, tzinfo=UTC)))
    got = {r.subscription_id for r in await st.list("t")}
    assert got == {"s1", "s2"}
    soon = await st.list_expiring("t", datetime(2026, 6, 1, tzinfo=UTC))
    assert [r.subscription_id for r in soon] == ["s1"]
    await st.delete("t", "s1")
    assert {r.subscription_id for r in await st.list("t")} == {"s2"}


@pytest.mark.asyncio
async def test_delta_roundtrip():
    st = CosmosSubscriptionStore(graph=FakeGraph())
    assert await st.get_delta("t", "c1", "u1", "users/u1/messages") is None
    await st.set_delta("t", "c1", "u1", "users/u1/messages", "https://delta/link")
    assert await st.get_delta("t", "c1", "u1", "users/u1/messages") == "https://delta/link"


@pytest.mark.asyncio
async def test_degrades_on_error():
    class Boom:
        async def submit(self, q, b=None): raise RuntimeError("cosmos down")
    st = CosmosSubscriptionStore(graph=Boom())
    assert await st.list("t") == []
    assert await st.get_delta("t", "c", "u", "r") is None
    await st.put(_rec())  # must not raise
