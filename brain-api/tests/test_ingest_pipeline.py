from datetime import UTC, datetime

import pytest

from app.domain.chunk import SourceDoc
from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
from app.retrieval.ai_search_client import AISearchClient


@pytest.mark.integration
async def test_pipeline_chunks_embeds_indexes_and_query_finds_it() -> None:
    pipeline = IngestPipeline(
        embedder=AzureOpenAIClient(),
        search=AISearchClient(),
    )
    now = datetime.now(UTC)
    doc = SourceDoc(
        doc_id="up:pipeline-test-1",
        tenant_id="t-test",
        source="uploaded",
        source_url="local://pipeline-test-1",
        title="Pipeline Test Doc",
        body=(
            "# Pipeline Test\n\nThe quick brown fox jumps over the lazy dog.\n\n"
            "Our PTO policy allows 20 days per year."
        ),
        author_id=None,
        acl_principals=["t-test:everyone"],
        created_at=now,
        modified_at=now,
        mime="text/markdown",
    )
    result = await pipeline.process(doc)
    assert result.chunks_indexed >= 1

    # Query the index and confirm we can find the doc
    user = User(
        user_id="u-x",
        tenant_id="t-test",
        email="x@y",
        display_name="X",
        group_ids={"t-test:everyone"},
    )
    embedder = AzureOpenAIClient()
    vec = await embedder.embed("PTO policy")
    search = AISearchClient()
    hits = await search.hybrid_search(query="PTO policy", user=user, vector=vec, top=10)
    assert any(h.doc_id == "up:pipeline-test-1" for h in hits)
