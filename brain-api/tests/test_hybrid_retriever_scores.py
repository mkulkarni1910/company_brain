import pytest

from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_candidates_carry_content_rank() -> None:
    retriever = HybridRetriever(search=AISearchClient(), embedder=AzureOpenAIClient())
    user = User(
        user_id="u-x", tenant_id="t-test", email="x@y", display_name="X",
        group_ids={"t-test:everyone"},
    )
    candidates = await retriever.retrieve(query="PTO policy", user=user, k=10)
    assert len(candidates) > 0
    # every candidate has a content_rank (0-based position) and a content_rrf score
    ranks = [c.raw_scores.get("content_rank") for c in candidates]
    assert ranks == sorted(ranks)  # ascending, gap-free order
    assert ranks[0] == 0
    assert all("content_rrf" in c.raw_scores for c in candidates)
    # RRF score strictly decreases with rank
    assert candidates[0].raw_scores["content_rrf"] > candidates[-1].raw_scores["content_rrf"]
