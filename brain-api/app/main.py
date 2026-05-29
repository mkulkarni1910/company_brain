from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.query import router as query_router
from app.api.retrieve import router as retrieve_router
from app.cache.redis_cache import RedisCache
from app.config import get_settings
from app.generation.azure_openai import AzureOpenAIClient
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings()
    app.state.embedder = AzureOpenAIClient()
    app.state.ai_search = AISearchClient()
    app.state.cache = RedisCache()
    app.state.retriever = HybridRetriever(
        search=app.state.ai_search, embedder=app.state.embedder
    )
    app.state.orchestrator = SemanticKernelOrchestrator(
        retriever=app.state.retriever,
        llm=app.state.embedder,
        cache=app.state.cache,
    )
    try:
        yield
    finally:
        await app.state.orchestrator.aclose()
        await app.state.cache.aclose()
        await app.state.ai_search.aclose()
        await app.state.embedder.aclose()


app = FastAPI(title="brain-api", version="0.1.0", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(query_router)
app.include_router(retrieve_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "brain-api"}
