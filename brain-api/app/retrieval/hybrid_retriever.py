from __future__ import annotations

from app.domain.identity import User
from app.domain.query import Candidate
from app.generation.azure_openai import AzureOpenAIClient
from app.retrieval.ai_search_client import AISearchClient


class HybridRetriever:
    """Phase 1: fan-out only to AI Search (hybrid: vector + BM25 + semantic).

    Later phases extend this with People proximity (Cosmos Gremlin) and
    Activity signal (ADX) joins.
    """

    def __init__(self, *, search: AISearchClient, embedder: AzureOpenAIClient) -> None:
        self._search = search
        self._embedder = embedder

    async def retrieve(self, *, query: str, user: User, k: int = 30) -> list[Candidate]:
        vec = await self._embedder.embed(query)
        chunks = await self._search.hybrid_search(query=query, user=user, vector=vec, top=k)
        return [
            Candidate(
                chunk=c,
                sources_hit={"vector", "bm25", "semantic"},
                raw_scores={},
            )
            for c in chunks
        ]
