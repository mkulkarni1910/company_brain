from __future__ import annotations

from app.domain.identity import User
from app.domain.query import Candidate
from app.generation.azure_openai import AzureOpenAIClient
from app.orchestrator.timing import StageTimer, maybe_stage
from app.retrieval.ai_search_client import AISearchClient

# Reciprocal Rank Fusion constant (standard default; dampens top-rank dominance).
_RRF_K = 60


class HybridRetriever:
    """Phase 2a: fan-out to AI Search (hybrid: vector + BM25 + semantic).

    Records each candidate's content rank and RRF contribution in raw_scores so
    the PersonalizedRanker can fuse it with the People-proximity signal. The
    Activity signal (ADX) is added in Phase 2b.
    """

    def __init__(self, *, search: AISearchClient, embedder: AzureOpenAIClient) -> None:
        self._search = search
        self._embedder = embedder

    async def retrieve(
        self, *, query: str, user: User, k: int = 30, timer: StageTimer | None = None
    ) -> list[Candidate]:
        async with maybe_stage(timer, "embed"):
            vec = await self._embedder.embed(query)
        async with maybe_stage(timer, "search"):
            chunks = await self._search.hybrid_search(query=query, user=user, vector=vec, top=k)
        return [
            Candidate(
                chunk=c,
                sources_hit={"vector", "bm25", "semantic"},
                raw_scores={
                    "content_rank": float(i),
                    "content_rrf": 1.0 / (_RRF_K + i),
                },
            )
            for i, c in enumerate(chunks)
        ]
