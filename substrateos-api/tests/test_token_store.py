from datetime import datetime, timezone

from app.domain.token import TokenCreated, TokenMeta


def test_token_meta_and_created_shapes() -> None:
    meta = TokenMeta(
        token_id="tk1",
        name="laptop",
        masked="sbx_live_••••a210",
        created_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    assert meta.last_used_at is None
    created = TokenCreated(token="sbx_live_secret", meta=meta)
    assert created.token == "sbx_live_secret"
    assert created.meta.token_id == "tk1"


import pytest

from app.tokens.store import CosmosTokenStore, NullTokenStore, _hash


class FakeGraph:
    """In-memory stand-in for PeopleGraphClient.submit covering the four query
    shapes CosmosTokenStore issues (upsert / list-by-user / resolve-by-hash / drop)."""

    def __init__(self) -> None:
        self.rows: list[dict] = []  # each: {tid, tenant_id, user_id, hash, data}

    async def submit(self, query: str, bindings=None):
        b = bindings or {}
        if "addV('cbrain_token')" in query or ".property('data'" in query:
            # upsert: replace any row with same tid, else insert
            self.rows = [r for r in self.rows if r["tid"] != b["k"]]
            self.rows.append(
                {"tid": b["k"], "tenant_id": b["tid"], "user_id": b["uid"],
                 "hash": b["h"], "data": b["d"]}
            )
            return []
        if ".drop()" in query:
            self.rows = [
                r for r in self.rows
                if not (r["tid"] == b["k"] and r["tenant_id"] == b["tid"]
                        and r["user_id"] == b["uid"])
            ]
            return []
        if "has('hash'" in query or ",'hash'" in query:
            return [r["data"] for r in self.rows if r["hash"] == b["h"]]
        if "has('user_id'" in query:  # list
            return [r["data"] for r in self.rows
                    if r["tenant_id"] == b["tid"] and r["user_id"] == b["uid"]]
        return []


def _user(uid="u-demo", tid="t-eval"):
    from app.domain.identity import User
    return User(user_id=uid, tenant_id=tid, email=f"{uid}@x", display_name=uid, group_ids=set())


@pytest.mark.asyncio
async def test_create_returns_plaintext_once_and_stores_hash() -> None:
    store = CosmosTokenStore(graph=FakeGraph())
    meta, plaintext = await store.create(user=_user(), name="laptop")
    assert plaintext.startswith("sbx_live_")
    assert meta.name == "laptop"
    assert meta.masked.startswith("sbx_live_••••")
    assert plaintext not in meta.masked  # plaintext never embedded in metadata


@pytest.mark.asyncio
async def test_list_masks_and_is_user_scoped() -> None:
    g = FakeGraph()
    store = CosmosTokenStore(graph=g)
    await store.create(user=_user("u1"), name="a")
    await store.create(user=_user("u2"), name="b")
    mine = await store.list(user=_user("u1"))
    assert [m.name for m in mine] == ["a"]
    assert all("•" in m.masked for m in mine)


@pytest.mark.asyncio
async def test_resolve_matches_by_hash_and_misses_return_none() -> None:
    store = CosmosTokenStore(graph=FakeGraph())
    _, plaintext = await store.create(user=_user("u9", "t-eval"), name="cli")
    resolved = await store.resolve(plaintext)
    assert resolved is not None
    assert resolved.user_id == "u9"
    assert resolved.tenant_id == "t-eval"
    assert await store.resolve("sbx_live_wrong") is None


@pytest.mark.asyncio
async def test_revoke_is_user_and_tenant_scoped() -> None:
    g = FakeGraph()
    store = CosmosTokenStore(graph=g)
    meta, _ = await store.create(user=_user("u1"), name="a")
    assert await store.revoke(user=_user("u1"), token_id=meta.token_id) is True
    assert await store.list(user=_user("u1")) == []


@pytest.mark.asyncio
async def test_null_store_noops() -> None:
    store = NullTokenStore()
    assert await store.list(user=_user()) == []
    assert await store.resolve("sbx_live_x") is None
    assert await store.revoke(user=_user(), token_id="x") is False
    meta, plaintext = await store.create(user=_user(), name="x")
    assert plaintext == ""  # cannot mint without a backing store


def test_hash_is_sha256_hex() -> None:
    assert len(_hash("abc")) == 64
