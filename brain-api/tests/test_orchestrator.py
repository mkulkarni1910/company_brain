import pytest

from app.cache.redis_cache import RedisCache
from app.domain.identity import User
from app.domain.query import QueryRequest
from app.generation.azure_openai import AzureOpenAIClient
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_orchestrator_returns_answer_with_citations() -> None:
    embedder = AzureOpenAIClient()
    retriever = HybridRetriever(search=AISearchClient(), embedder=embedder)
    orch = SemanticKernelOrchestrator(
        retriever=retriever,
        llm=embedder,
        cache=RedisCache(),
    )
    user = User(
        user_id="u-orch",
        tenant_id="t-test",
        email="u@x",
        display_name="U",
        group_ids={"t-test:everyone"},
    )
    answer = await orch.answer(QueryRequest(query="what is the PTO policy?"), user=user)
    assert isinstance(answer.text, str) and len(answer.text) > 0
    assert len(answer.citations) >= 1
    assert any("pto" in c.doc_id.lower() for c in answer.citations)


@pytest.mark.integration
async def test_orchestrator_refuses_out_of_corpus() -> None:
    embedder = AzureOpenAIClient()
    retriever = HybridRetriever(search=AISearchClient(), embedder=embedder)
    orch = SemanticKernelOrchestrator(
        retriever=retriever,
        llm=embedder,
        cache=RedisCache(),
    )
    user = User(
        user_id="u-orch",
        tenant_id="t-test",
        email="u@x",
        display_name="U",
        group_ids={"t-test:everyone"},
    )
    answer = await orch.answer(
        QueryRequest(query="what is the recipe for chocolate chip cookies?"),
        user=user,
    )
    assert "don't have" in answer.text.lower() or "do not have" in answer.text.lower()
