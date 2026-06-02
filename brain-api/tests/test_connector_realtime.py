"""Realtime service: webhook ingest (clientState, union, delete-skip), bootstrap, maintenance."""
from datetime import UTC, datetime, timedelta

import pytest

import app.connectors.realtime as rt
from app.connectors.models import Connection, SubscriptionRecord
from app.connectors.subscriptions import SubscriptionStore
from app.domain.chunk import SourceDoc


def _doc(doc_id="outlookmail:<m@x>", owner="u1"):
    now = datetime.now(UTC)
    return SourceDoc(
        doc_id=doc_id, tenant_id="t-eval", source="outlook_mail", source_url="",
        title="t", body="b", author_id="a@x", acl_principals=[owner],
        created_at=now, modified_at=now, mime="text/plain",
    )


class FakePipeline:
    def __init__(self):
        self.processed = []

    async def process(self, doc):
        self.processed.append(doc)


class FakeACL:
    def __init__(self, existing=None):
        self._existing = existing or {}

    async def doc_principals(self, *, tenant_id, doc_id):
        return self._existing.get(doc_id)


class FakeConnStore:
    def __init__(self, conns):
        self._conns = conns

    async def list_connections(self, tenant):
        return self._conns


class FakeConnector:
    def __init__(self, *, users=None, doc=None, delta_docs=None):
        self._users = users or []
        self._doc = doc
        self._delta = delta_docs or []

    async def list_users(self):
        return self._users

    async def fetch_message(self, user_id, res_id):
        return self._doc

    async def fetch_event(self, user_id, res_id):
        return self._doc

    async def delta(self, user_id, token):
        return list(self._delta), "NEWLINK"


def _conn(provider="outlook_mail", status="live"):
    return Connection(
        connection_id="c1", tenant_id="t-eval", type=provider, site_id="tid",
        name="Outlook", web_url="", status=status, connected_tenant_id="tid",
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("BRAIN_TENANT_ID", "t-eval")
    monkeypatch.setenv("GRAPH_WEBHOOK_CLIENT_STATE", "secret123")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---- ingest_notifications ----

@pytest.mark.asyncio
async def test_ingest_ignores_bad_client_state(monkeypatch):
    pipe = FakePipeline()
    payload = {"value": [{"clientState": "WRONG", "resource": "users/u1/messages/m1",
                          "changeType": "created", "resourceData": {"id": "m1"}}]}
    n = await rt.ingest_notifications(
        payload=payload, conn_store=FakeConnStore([_conn()]), pipeline=pipe, acl_store=None)
    assert n == 0
    assert pipe.processed == []


@pytest.mark.asyncio
async def test_ingest_created_fetches_and_unions_acl(monkeypatch):
    pipe = FakePipeline()
    acl = FakeACL(existing={"outlookmail:<m@x>": {"u9"}})
    monkeypatch.setattr(rt, "connector_for", lambda conn: FakeConnector(doc=_doc(owner="u1")))
    payload = {"value": [{"clientState": "secret123", "resource": "users/u1/messages/m1",
                          "changeType": "created", "resourceData": {"id": "m1"}}]}
    n = await rt.ingest_notifications(
        payload=payload, conn_store=FakeConnStore([_conn()]), pipeline=pipe, acl_store=acl)
    assert n == 1
    assert len(pipe.processed) == 1
    # owner u1 unioned with the pre-existing principal u9
    assert pipe.processed[0].acl_principals == ["u1", "u9"]


@pytest.mark.asyncio
async def test_ingest_delete_is_skipped(monkeypatch):
    pipe = FakePipeline()
    monkeypatch.setattr(rt, "connector_for", lambda conn: FakeConnector(doc=_doc()))
    payload = {"value": [{"clientState": "secret123", "resource": "users/u1/messages/m1",
                          "changeType": "deleted", "resourceData": {"id": "m1"}}]}
    n = await rt.ingest_notifications(
        payload=payload, conn_store=FakeConnStore([_conn()]), pipeline=pipe, acl_store=None)
    assert n == 0
    assert pipe.processed == []


# ---- bootstrap_subscriptions ----

@pytest.mark.asyncio
async def test_bootstrap_creates_subs_for_each_user(monkeypatch):
    sub_store = SubscriptionStore(client=_FakeRedis())
    monkeypatch.setattr(rt, "connector_for",
                        lambda conn: FakeConnector(users=[{"user_id": "u1"}, {"user_id": "u2"}]))

    async def fake_create(**kw):
        return SubscriptionRecord(
            subscription_id=f"sub-{kw['user_id']}", tenant_id=kw["tenant_id"],
            connection_id=kw["connection_id"], provider=kw["provider"],
            user_id=kw["user_id"], resource=f"users/{kw['user_id']}/messages",
            expiration=datetime.now(UTC) + timedelta(minutes=4000))

    monkeypatch.setattr(rt, "create_subscription", fake_create)
    created = await rt.bootstrap_subscriptions(conn=_conn(), sub_store=sub_store)
    assert created == 2
    assert {s.subscription_id for s in await sub_store.list("t-eval")} == {"sub-u1", "sub-u2"}


# ---- run_maintenance ----

@pytest.mark.asyncio
async def test_maintenance_delta_sweep_and_joiner(monkeypatch):
    sub_store = SubscriptionStore(client=_FakeRedis())
    pipe = FakePipeline()
    conn = _conn()

    monkeypatch.setattr(rt, "connector_for",
                        lambda c: FakeConnector(users=[{"user_id": "u1"}], delta_docs=[_doc()]))

    async def fake_create(**kw):
        return SubscriptionRecord(
            subscription_id="sub-u1", tenant_id="t-eval", connection_id="c1",
            provider="outlook_mail", user_id="u1", resource="users/u1/messages",
            expiration=datetime.now(UTC) + timedelta(minutes=4000))

    monkeypatch.setattr(rt, "create_subscription", fake_create)
    summary = await rt.run_maintenance(
        conn_store=FakeConnStore([conn]), sub_store=sub_store, pipeline=pipe, acl_store=None)
    assert summary["created"] == 1       # u1 had no sub → joiner created
    assert summary["ingested"] == 1      # delta swept one doc
    assert len(pipe.processed) == 1
    # delta link persisted
    assert await sub_store.get_delta("t-eval", "c1", "u1", "users/{uid}/messages") == "NEWLINK"


class _FakeRedis:
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
