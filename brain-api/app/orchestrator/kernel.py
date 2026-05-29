from __future__ import annotations

import hashlib
import logging
import uuid

from app.acl.store import ACLStore
from app.activity.signal import ActivitySignal
from app.cache.redis_cache import RedisCache
from app.domain.identity import User
from app.domain.query import Answer, Candidate, QueryRequest, RankedResult
from app.generation.azure_openai import AzureOpenAIClient
from app.generation.prompts import build_grounded_messages, parse_citations_from_answer
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.hybrid_retriever import HybridRetriever

logger = logging.getLogger(__name__)


def _cache_key(user: User, query: str) -> str:
    principals_blob = "|".join(sorted(user.principals()))
    normalized = " ".join(query.lower().split())
    h = hashlib.sha256(f"{principals_blob}::{normalized}".encode()).hexdigest()
    return f"cache:answer:{user.tenant_id}:{h}"


class SemanticKernelOrchestrator:
    """Phase 2b: cache -> retrieve -> ACL re-check -> proximity -> activity -> rank -> answer.

    Plan step + Live Fetch are still stubbed (Phase 3).
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        llm: AzureOpenAIClient,
        cache: RedisCache,
        acl_store: ACLStore,
        proximity: PeopleProximity,
        ranker: PersonalizedRanker,
        activity: ActivitySignal,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._cache = cache
        self._acl_store = acl_store
        self._proximity = proximity
        self._ranker = ranker
        self._activity = activity

    async def aclose(self) -> None:
        return None

    async def retrieve_ranked(self, request: QueryRequest, *, user: User) -> list[Candidate]:
        candidates = await self._retriever.retrieve(
            query=request.query, user=user, k=max(request.k, 10)
        )
        if not candidates:
            return []
        # Query-time ACL re-check (double-enforcement, fail-closed on store error).
        candidates = await self._acl_store.recheck(candidates=candidates, user=user)
        if not candidates:
            return []
        # People proximity over the surviving candidate docs.
        # Spec §3.2: Cosmos down -> skip People signal (proximity=0); ranker still runs.
        # A Cosmos/Gremlin failure can surface as various exception types from
        # gremlinpython/aiohttp, so catch broad Exception (excludes CancelledError).
        try:
            proximity = await self._proximity.score(
                user=user, doc_ids=[c.chunk.doc_id for c in candidates]
            )
        except Exception as e:
            logger.warning("People graph (Cosmos) unavailable; degrading to proximity=0: %s", e)
            proximity = {}
        # Activity engagement signal. Spec §3.2: ADX down -> skip Activity (activity=0).
        try:
            activity = await self._activity.score(
                user=user, doc_ids=[c.chunk.doc_id for c in candidates]
            )
        except Exception as e:
            logger.warning("Activity store (ADX) unavailable; degrading to activity=0: %s", e)
            activity = {}
        ranked: list[RankedResult] = self._ranker.rank(
            candidates=candidates, proximity=proximity, activity=activity
        )
        return [r.candidate for r in ranked]

    async def answer(self, request: QueryRequest, *, user: User) -> Answer:
        query_id = str(uuid.uuid4())

        key = _cache_key(user, request.query)
        cached = await self._cache.get_json(key)
        if cached:
            return Answer.model_validate({**cached, "query_id": query_id})

        candidates = await self.retrieve_ranked(request, user=user)
        if not candidates:
            return Answer(
                text="I don't have information about that.",
                citations=[],
                query_id=query_id,
            )

        messages = build_grounded_messages(query=request.query, candidates=candidates[:5])
        text = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=800)
        citations = parse_citations_from_answer(text, candidates[:5])

        answer = Answer(text=text, citations=citations, query_id=query_id)

        cache_blob = answer.model_dump()
        cache_blob.pop("query_id", None)
        await self._cache.set_json(key, cache_blob, ttl_seconds=600)

        return answer
