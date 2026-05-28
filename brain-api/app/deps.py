from functools import lru_cache

from app.cache.redis_cache import RedisCache
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@lru_cache(maxsize=1)
def get_embedder() -> AzureOpenAIClient:
    return AzureOpenAIClient()


@lru_cache(maxsize=1)
def get_ai_search() -> AISearchClient:
    return AISearchClient()


@lru_cache(maxsize=1)
def get_ingest_pipeline() -> IngestPipeline:
    return IngestPipeline(embedder=get_embedder(), search=get_ai_search())


@lru_cache(maxsize=1)
def get_retriever() -> HybridRetriever:
    return HybridRetriever(search=get_ai_search(), embedder=get_embedder())


@lru_cache(maxsize=1)
def get_cache() -> RedisCache:
    return RedisCache()


@lru_cache(maxsize=1)
def get_orchestrator() -> SemanticKernelOrchestrator:
    return SemanticKernelOrchestrator(
        retriever=get_retriever(),
        llm=get_embedder(),
        cache=get_cache(),
    )
