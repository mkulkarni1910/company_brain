from fastapi import Request

from app.activity.store import ActivityStore
from app.cache.redis_cache import RedisCache
from app.discover.service import DiscoverService
from app.generation.azure_openai import AzureOpenAIClient
from app.history.store import HistoryStore
from app.ingest.pipeline import IngestPipeline
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever
from app.search.service import SearchService


def get_embedder(request: Request) -> AzureOpenAIClient:
    return request.app.state.embedder


def get_ai_search(request: Request) -> AISearchClient:
    return request.app.state.ai_search


def get_cache(request: Request) -> RedisCache:
    return request.app.state.cache


def get_retriever(request: Request) -> HybridRetriever:
    return request.app.state.retriever


def get_orchestrator(request: Request) -> SemanticKernelOrchestrator:
    return request.app.state.orchestrator


def get_activity_store(request: Request) -> ActivityStore:
    return request.app.state.activity_store


def get_ingest_pipeline(request: Request) -> IngestPipeline:
    return IngestPipeline(
        embedder=request.app.state.embedder,
        search=request.app.state.ai_search,
        acl_store=request.app.state.acl_store,
    )


def get_history_store(request: Request) -> "HistoryStore | None":
    return getattr(request.app.state, "history_store", None)


def get_discover_service(request: Request) -> "DiscoverService | None":
    return getattr(request.app.state, "discover_service", None)


def get_search_service(request: Request) -> "SearchService | None":
    return getattr(request.app.state, "search_service", None)
