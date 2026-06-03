"""Azure AI Search rejects document keys containing dot/slash/backslash (even with
allowUnsafeKeys). Connector doc_ids embed SharePoint hostnames and email addresses,
which contain dots — so the chunk_id used as the index key must be sanitized.
Regression test for SharePoint sync failing with "Invalid document key … Keys
cannot contain dot (.), slash (/), or backslash (\\)."."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.domain.chunk import SourceDoc
from app.ingest.pipeline import IngestPipeline
from app.retrieval.ai_search_client import safe_doc_key


class _FakeEmbedder:
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.1, 0.2] for _ in texts]


class _FakeSearch:
    def __init__(self) -> None:
        self.upserted: list = []

    async def upsert_chunks(self, chunks: list) -> None:
        self.upserted.extend(chunks)


def _sharepoint_doc() -> SourceDoc:
    now = datetime.now(UTC)
    # The SharePoint composite site_id embeds the hostname, so the doc_id has dots.
    return SourceDoc(
        doc_id=(
            "sp:omkarconsultancy1910.sharepoint.com,"
            "fe18c05c-88c7-4856-b2fb-a2344348e3d4,"
            "8d434c14-a600-40ed-875f-0f89b1fba5ee:01LCNNYJGJ3VJNMZGSS5AZD73TJTNTRIAC"
        ),
        tenant_id="t-eval",
        source="sharepoint",
        source_url="https://omkarconsultancy1910.sharepoint.com/sites/x/doc.docx",
        title="SP Doc",
        body="# Quarterly Plan\n\nSome content about the plan and the PTO policy.",
        author_id=None,
        acl_principals=["t-eval:everyone"],
        created_at=now,
        modified_at=now,
        mime="text/markdown",
    )


def test_safe_doc_key_strips_dot_slash_backslash() -> None:
    assert safe_doc_key("a.b/c\\d#chunk-0") == "a_b_c_d#chunk-0"
    # Comma/colon/hash are permitted (allowUnsafeKeys is set on upsert) -> preserved.
    assert safe_doc_key("sp:host,guid:item#chunk-1") == "sp:host,guid:item#chunk-1"


def test_pipeline_produces_search_safe_chunk_keys() -> None:
    search = _FakeSearch()
    pipe = IngestPipeline(embedder=_FakeEmbedder(), search=search)  # type: ignore[arg-type]
    result = asyncio.run(pipe.process(_sharepoint_doc()))

    assert result.chunks_indexed >= 1
    assert search.upserted, "expected chunks to be upserted"
    for c in search.upserted:
        assert not any(ch in c.chunk_id for ch in "./\\"), f"unsafe key: {c.chunk_id}"
        # The doc_id FIELD keeps the original (dots are fine in a field value).
        assert c.doc_id.startswith("sp:omkarconsultancy1910.sharepoint.com")
