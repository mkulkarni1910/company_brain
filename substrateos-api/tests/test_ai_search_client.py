from datetime import UTC, datetime

import pytest

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.retrieval.ai_search_client import AISearchClient


def _make_chunk(chunk_id: str, content: str, vec: list[float]) -> Chunk:
    now = datetime.now(UTC)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=chunk_id.split("#")[0],
        tenant_id="t-test",
        source="uploaded",
        source_url="local://test",
        title="Test",
        content=content,
        content_vector=vec,
        acl_principals=["u-test", "g-test"],
        author_id=None,
        entities=[],
        created_at=now,
        modified_at=now,
        chunk_index=int(chunk_id.split("-")[-1]),
    )


@pytest.mark.integration
async def test_upsert_then_hybrid_search_returns_chunk() -> None:
    client = AISearchClient()
    vec = [0.01] * 3072
    chunk = _make_chunk("test-doc-1#chunk-0", "Travel reimbursement policy details.", vec)
    await client.upsert_chunks([chunk])

    user = User(
        user_id="u-test",
        tenant_id="t-test",
        email="t@x",
        display_name="T",
        group_ids={"g-test"},
    )
    results = await client.hybrid_search(query="travel reimbursement", user=user, vector=vec, top=5)
    ids = [r.chunk_id for r in results]
    assert "test-doc-1#chunk-0" in ids


@pytest.mark.integration
async def test_acl_filter_excludes_other_tenant() -> None:
    client = AISearchClient()
    vec = [0.02] * 3072
    chunk = _make_chunk("test-doc-2#chunk-0", "Engineering on-call runbook.", vec)
    # Override tenant via direct mutation in test
    chunk = chunk.model_copy(update={"tenant_id": "other-tenant"})
    await client.upsert_chunks([chunk])

    user = User(
        user_id="u-test",
        tenant_id="t-test",
        email="t@x",
        display_name="T",
        group_ids={"g-test"},
    )
    results = await client.hybrid_search(query="on-call runbook", user=user, vector=vec, top=5)
    assert all(r.tenant_id == "t-test" for r in results)
