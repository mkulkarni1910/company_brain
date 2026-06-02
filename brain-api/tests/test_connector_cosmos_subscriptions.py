"""CosmosSubscriptionStore round-trips via the same in-memory Gremlin fake used
for CosmosConnectionStore — covers the no-Redis (India) realtime path."""
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.connectors.models import SubscriptionRecord
from app.connectors.subscriptions import CosmosSubscriptionStore


class FakeGraph:
    def __init__(self):
        self.store = {}  # (label, tenant, key) -> data

    async def submit(self, query, bindings=None):
        b = bindings or {}
        label = re.search(r"has\('([^']+)'", query).group(1)
        tid = b.get("tid")
        if "addV" in query and "property('data'" in query:
            self.store[(label, tid, b["k"])] = b["d"]
            return []
        if ".drop()" in query:
            self.store.pop((label, tid, b["k"]), None)
            return []
        if ".values('data')" in query:
            if "k" in b:
                v = self.store.get((label, tid, b["k"]))
                return [v] if v is not None else []
            return [v for (lbl, t, _), v in self.store.items() if lbl == label and t == tid]
        return []


def _rec(sub_id, user="u1", provider="outlook_mail", exp_minutes=4000, tenant="t-eval"):
    return SubscriptionRecord(
        subscription_id=sub_id, tenant_id=tenant, connection_id="c1",
        provider=provider, user_id=user, resource=f"users/{user}/messages",
        expiration=datetime.now(UTC) + timedelta(minutes=exp_minutes),
    )


@pytest.mark.asyncio
async def test_put_list_delete_roundtrip():
    st = CosmosSubscriptionStore(graph=FakeGraph())
    await st.put(_rec("s1"))
    await st.put(_rec("s2", user="u2"))
    assert {s.subscription_id for s in await st.list("t-eval")} == {"s1", "s2"}
    await st.delete("t-eval", "s1")
    assert {s.subscription_id for s in await st.list("t-eval")} == {"s2"}


@pytest.mark.asyncio
async def test_list_expiring():
    st = CosmosSubscriptionStore(graph=FakeGraph())
    await st.put(_rec("soon", exp_minutes=10))
    await st.put(_rec("later", exp_minutes=5000))
    threshold = datetime.now(UTC) + timedelta(minutes=60)
    assert [s.subscription_id for s in await st.list_expiring("t-eval", threshold)] == ["soon"]


@pytest.mark.asyncio
async def test_delta_token_roundtrip():
    st = CosmosSubscriptionStore(graph=FakeGraph())
    assert await st.get_delta("t-eval", "c1", "u1", "users/u1/messages") is None
    await st.set_delta("t-eval", "c1", "u1", "users/u1/messages", "LINK")
    assert await st.get_delta("t-eval", "c1", "u1", "users/u1/messages") == "LINK"


@pytest.mark.asyncio
async def test_reads_degrade_on_error():
    class Boom:
        async def submit(self, q, b=None):
            raise RuntimeError("cosmos down")

    st = CosmosSubscriptionStore(graph=Boom())
    assert await st.list("t-eval") == []
    assert await st.get_delta("t-eval", "c1", "u1", "users/u1/messages") is None
    await st.put(_rec("s1"))  # must not raise
