from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.acl.store import ACLStore
from app.activity.signal import ActivitySignal
from app.activity.store import ActivityStore
from app.api.admin import router as admin_router
from app.api.feedback import router as feedback_router
from app.api.query import router as query_router
from app.api.retrieve import router as retrieve_router
from app.cache.redis_cache import RedisCache
from app.config import get_settings, load_secrets_from_keyvault
from app.generation.azure_openai import AzureOpenAIClient
from app.live_fetch.graph_search import MSGraphSearchFetcher
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlanner
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_secrets_from_keyvault(get_settings())
    app.state.embedder = AzureOpenAIClient()
    app.state.ai_search = AISearchClient()
    app.state.cache = RedisCache()
    app.state.retriever = HybridRetriever(
        search=app.state.ai_search, embedder=app.state.embedder
    )
    app.state.acl_store = ACLStore()
    app.state.people_graph = PeopleGraphClient()
    app.state.proximity = PeopleProximity(graph=app.state.people_graph)
    app.state.ranker = PersonalizedRanker(
        weight_content=get_settings().rank_weight_content,
        weight_people=get_settings().rank_weight_people,
        weight_activity=get_settings().rank_weight_activity,
        weight_recency=get_settings().rank_weight_recency,
    )
    app.state.activity_store = ActivityStore()
    app.state.activity = ActivitySignal(store=app.state.activity_store)
    app.state.live_fetcher = MSGraphSearchFetcher()
    app.state.planner = QueryPlanner(llm=app.state.embedder)
    app.state.orchestrator = SemanticKernelOrchestrator(
        retriever=app.state.retriever,
        llm=app.state.embedder,
        cache=app.state.cache,
        acl_store=app.state.acl_store,
        proximity=app.state.proximity,
        ranker=app.state.ranker,
        activity=app.state.activity,
        live_fetcher=app.state.live_fetcher,
        planner=app.state.planner,
    )
    try:
        yield
    finally:
        await app.state.orchestrator.aclose()
        await app.state.acl_store.aclose()
        await app.state.people_graph.aclose()
        await app.state.activity_store.aclose()
        await app.state.cache.aclose()
        await app.state.ai_search.aclose()
        await app.state.embedder.aclose()


app = FastAPI(title="brain-api", version="0.1.0", lifespan=lifespan)

# CORS origins are configurable via CORS_ALLOW_ORIGINS (comma-separated).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(query_router)
app.include_router(retrieve_router)
app.include_router(feedback_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "brain-api"}
