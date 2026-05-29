from datetime import UTC, datetime

import pytest

from app.acl.store import ACLStore
from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate


def _candidate(doc_id: str, acl: list[str]) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0", doc_id=doc_id, tenant_id="t-test",
            source="uploaded", source_url=f"local://{doc_id}", title="T",
            content="c", content_vector=[], acl_principals=acl, author_id=None,
            entities=[], created_at=now, modified_at=now, chunk_index=0,
        ),
        sources_hit={"vector"},
    )


class _FakeStore(ACLStore):
    """Override the Redis read with an in-memory map for the unit test."""

    def __init__(self, mapping: dict[str, set[str] | None]) -> None:
        self._mapping = mapping

    async def doc_principals(self, *, tenant_id: str, doc_id: str) -> set[str] | None:
        return self._mapping.get(doc_id)


def test_recheck_keeps_allowed_drops_revoked() -> None:
    import asyncio

    user = User(user_id="u1", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"g-sales"})
    store = _FakeStore({
        "doc-allow": {"g-sales"},      # live ACL still allows the group
        "doc-revoked": {"g-other"},    # live ACL no longer includes the user
    })
    cands = [_candidate("doc-allow", ["g-sales"]), _candidate("doc-revoked", ["g-sales"])]
    kept = asyncio.run(store.recheck(candidates=cands, user=user))
    kept_ids = {c.chunk.doc_id for c in kept}
    assert "doc-allow" in kept_ids
    assert "doc-revoked" not in kept_ids


def test_recheck_falls_back_to_index_acl_on_key_miss() -> None:
    import asyncio

    user = User(user_id="u1", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"g-sales"})
    store = _FakeStore({})  # no live ACL for any doc -> fall back to chunk.acl_principals
    cands = [_candidate("doc-x", ["g-sales"]), _candidate("doc-y", ["g-other"])]
    kept = {c.chunk.doc_id for c in asyncio.run(store.recheck(candidates=cands, user=user))}
    assert kept == {"doc-x"}  # index-time ACL allows g-sales on doc-x only


@pytest.mark.integration
async def test_acl_store_round_trip() -> None:
    store = ACLStore()
    try:
        await store.set_doc_principals(tenant_id="t-test", doc_id="doc-rt", principals=["g-sales", "u9"])
        got = await store.doc_principals(tenant_id="t-test", doc_id="doc-rt")
        assert got == {"g-sales", "u9"}
    finally:
        await store.aclose()
