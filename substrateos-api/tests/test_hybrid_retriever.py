import pytest

from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_retriever_returns_candidates_with_sources_hit() -> None:
    retriever = HybridRetriever(
        search=AISearchClient(),
        embedder=AzureOpenAIClient(),
    )
    user = User(
        user_id="u-x",
        tenant_id="t-test",
        email="x@y",
        display_name="X",
        group_ids={"t-test:everyone"},
    )
    candidates = await retriever.retrieve(query="PTO policy", user=user, k=10)
    assert len(candidates) > 0
    assert all("vector" in c.sources_hit or "bm25" in c.sources_hit or "semantic" in c.sources_hit
               for c in candidates)
    # Tenant isolation
    assert all(c.chunk.tenant_id == "t-test" for c in candidates)
