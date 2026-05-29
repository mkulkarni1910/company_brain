from __future__ import annotations

import hashlib
import uuid

from app.cache.redis_cache import RedisCache
from app.domain.identity import User
from app.domain.query import Answer, Candidate, QueryRequest
from app.generation.azure_openai import AzureOpenAIClient
from app.generation.prompts import build_grounded_messages, parse_citations_from_answer
from app.retrieval.hybrid_retriever import HybridRetriever


def _cache_key(user: User, query: str) -> str:
    principals_blob = "|".join(sorted(user.principals()))
    normalized = " ".join(query.lower().split())
    h = hashlib.sha256(f"{principals_blob}::{normalized}".encode()).hexdigest()
    return f"cache:answer:{user.tenant_id}:{h}"


class SemanticKernelOrchestrator:
    """Phase 1: cache → retrieve → answer.

    Plan step + Live Fetch routing are stubbed; this is intentional for Phase 1.
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        llm: AzureOpenAIClient,
        cache: RedisCache,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._cache = cache

    async def aclose(self) -> None:
        # Orchestrator owns no sockets of its own; its collaborators are closed
        # by the lifespan. Method exists so shutdown can call it uniformly.
        return None

    async def answer(self, request: QueryRequest, *, user: User) -> Answer:
        query_id = str(uuid.uuid4())

        # 1. Cache lookup
        key = _cache_key(user, request.query)
        cached = await self._cache.get_json(key)
        if cached:
            return Answer.model_validate({**cached, "query_id": query_id})

        # 2. Retrieve
        candidates: list[Candidate] = await self._retriever.retrieve(
            query=request.query, user=user, k=max(request.k, 5)
        )
        if not candidates:
            return Answer(
                text="I don't have information about that.",
                citations=[],
                query_id=query_id,
            )

        # 3. Generate grounded answer
        messages = build_grounded_messages(query=request.query, candidates=candidates[:5])
        text = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=800)
        citations = parse_citations_from_answer(text, candidates[:5])

        answer = Answer(text=text, citations=citations, query_id=query_id)

        # 4. Cache (10 min). Strip query_id so it gets re-minted per request.
        cache_blob = answer.model_dump()
        cache_blob.pop("query_id", None)
        await self._cache.set_json(key, cache_blob, ttl_seconds=600)

        return answer

    async def retrieve_ranked(self, request: QueryRequest, *, user: User) -> list[Candidate]:
        """Return candidates in final rank order WITHOUT generating an answer.

        Phase 2a: this is the retriever's output. Task 10 enriches it with
        People proximity, ACL re-check, and the personalized ranker so the
        eval metric reflects personalization. Used by /admin/retrieve for the
        retrieval-quality eval gate.
        """
        return await self._retriever.retrieve(
            query=request.query, user=user, k=max(request.k, 10)
        )
