from fastapi import Request

from app.activity.store import ActivityStore
from app.cache.redis_cache import RedisCache
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


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
