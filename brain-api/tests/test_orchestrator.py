import pytest

from app.acl.store import ACLStore
from app.activity.signal import ActivitySignal
from app.activity.store import ActivityStore
from app.cache.redis_cache import RedisCache
from app.domain.identity import User
from app.domain.query import QueryRequest
from app.generation.azure_openai import AzureOpenAIClient
from app.live_fetch.graph_search import MSGraphSearchFetcher
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


def _build() -> tuple[SemanticKernelOrchestrator, list]:
    embedder = AzureOpenAIClient()
    search = AISearchClient()
    cache = RedisCache()
    acl_store = ACLStore()
    graph = PeopleGraphClient()
    activity_store = ActivityStore()
    closeables = [embedder, search, cache, acl_store, graph, activity_store]
    orch = SemanticKernelOrchestrator(
        retriever=HybridRetriever(search=search, embedder=embedder),
        llm=embedder,
        cache=cache,
        acl_store=acl_store,
        proximity=PeopleProximity(graph=graph),
        ranker=PersonalizedRanker(weight_content=0.5, weight_people=0.3, weight_activity=0.2),
        activity=ActivitySignal(store=activity_store),
        live_fetcher=MSGraphSearchFetcher(),
    )
    return orch, closeables


async def _aclose_all(closeables: list) -> None:
    for c in closeables:
        await c.aclose()


@pytest.mark.integration
async def test_orchestrator_returns_answer_with_citations() -> None:
    orch, closeables = _build()
    try:
        user = User(user_id="u-orch", tenant_id="t-test", email="u@x",
                    display_name="U", group_ids={"t-test:everyone"})
        answer = await orch.answer(QueryRequest(query="what is the PTO policy?"), user=user)
        assert isinstance(answer.text, str) and len(answer.text) > 0
        assert any("pto" in c.doc_id.lower() for c in answer.citations)
    finally:
        await _aclose_all(closeables)


@pytest.mark.integration
async def test_orchestrator_refuses_out_of_corpus() -> None:
    orch, closeables = _build()
    try:
        user = User(user_id="u-orch", tenant_id="t-test", email="u@x",
                    display_name="U", group_ids={"t-test:everyone"})
        answer = await orch.answer(
            QueryRequest(query="what is the recipe for chocolate chip cookies?"), user=user
        )
        assert "don't have" in answer.text.lower() or "do not have" in answer.text.lower()
    finally:
        await _aclose_all(closeables)
