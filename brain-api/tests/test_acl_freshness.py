import asyncio
from datetime import UTC, datetime

from app.acl.store import ACLStore
from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate


def _cand(doc_id: str, acl: list[str]) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test", source="uploaded",
            source_url=f"x://{doc_id}", title="T", content="c", content_vector=[],
            acl_principals=acl, author_id=None, entities=[], created_at=now,
            modified_at=now, chunk_index=0,
        ),
        sources_hit={"vector"},
    )


class _FakeStore(ACLStore):
    """In-memory ACL map; override doc_principals to avoid real Redis."""

    def __init__(self, mapping: dict[str, set[str] | None], fail_closed: bool) -> None:
        self._mapping = mapping
        self._fail_closed = fail_closed

    async def doc_principals(self, *, tenant_id: str, doc_id: str) -> set[str] | None:
        return self._mapping.get(doc_id)


def _user() -> User:
    return User(user_id="u1", tenant_id="t-test", email="a@b", display_name="A",
                group_ids={"g-sales"})


def test_missing_entry_falls_back_to_index_acl_when_not_strict() -> None:
    store = _FakeStore({}, fail_closed=False)
    kept = asyncio.run(store.recheck(candidates=[_cand("d-x", ["g-sales"])], user=_user()))
    assert {c.chunk.doc_id for c in kept} == {"d-x"}  # index-ACL fallback allows it


def test_missing_entry_dropped_when_strict() -> None:
    store = _FakeStore({}, fail_closed=True)
    kept = asyncio.run(store.recheck(candidates=[_cand("d-x", ["g-sales"])], user=_user()))
    assert kept == []  # strict: no live entry -> fail closed


def test_live_entry_authoritative_over_index_acl() -> None:
    # index ACL would allow (g-sales), but the live entry revoked it -> dropped
    store = _FakeStore({"d-x": {"g-other"}}, fail_closed=False)
    kept = asyncio.run(store.recheck(candidates=[_cand("d-x", ["g-sales"])], user=_user()))
    assert kept == []
