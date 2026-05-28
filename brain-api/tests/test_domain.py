from datetime import UTC, datetime

from app.domain.chunk import Chunk, SourceDoc
from app.domain.identity import User
from app.domain.query import QueryRequest


def test_user_principal_set_includes_self_and_groups() -> None:
    u = User(
        user_id="u-1",
        tenant_id="t-test",
        email="alex@contoso.com",
        display_name="Alex",
        group_ids={"g-sales", "g-central"},
        manager_id=None,
    )
    assert u.principals() == {"u-1", "g-sales", "g-central"}


def test_chunk_id_is_doc_id_plus_chunk_index() -> None:
    now = datetime.now(UTC)
    c = Chunk(
        chunk_id="sp:doc-1#chunk-0",
        doc_id="sp:doc-1",
        tenant_id="t-test",
        source="sharepoint",
        source_url="https://contoso.sharepoint.com/x",
        title="Q3 Plan",
        content="ARR target 42M",
        content_vector=[0.1] * 3072,
        acl_principals=["u-1", "g-sales"],
        author_id="u-100",
        entities=["Q3"],
        created_at=now,
        modified_at=now,
        chunk_index=0,
    )
    assert c.chunk_id.startswith(c.doc_id)
    assert len(c.content_vector) == 3072


def test_source_doc_round_trip() -> None:
    now = datetime.now(UTC)
    d = SourceDoc(
        doc_id="up:abc",
        tenant_id="t-test",
        source="uploaded",
        source_url="local://abc.md",
        title="Notes",
        body="hello world",
        author_id=None,
        acl_principals=["g-eng"],
        created_at=now,
        modified_at=now,
        mime="text/markdown",
    )
    assert d.source == "uploaded"


def test_query_request_defaults() -> None:
    q = QueryRequest(query="what is our Q3 plan?")
    assert q.k == 5
    assert q.session_id is None
