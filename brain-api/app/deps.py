from functools import lru_cache

from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
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
