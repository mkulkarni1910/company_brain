from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid

from app.acl.store import ACLStore
from app.activity.signal import ActivitySignal
from app.cache.redis_cache import RedisCache
from app.config import get_settings
from app.domain.identity import User
from app.domain.query import Answer, Candidate, QueryRequest, RankedResult
from app.generation.azure_openai import AzureOpenAIClient
from app.generation.prompts import build_grounded_messages, parse_citations_from_answer
from app.live_fetch.base import LiveFetcher, needs_live_fetch
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
    """Phase 3: cache -> (retrieve || live-fetch) -> ACL re-check (indexed) ->
    merge -> proximity -> activity -> rank -> answer.

    Plan step is still a heuristic (needs_live_fetch); LLM plan step deferred.
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
        live_fetcher: LiveFetcher,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._cache = cache
        self._acl_store = acl_store
        self._proximity = proximity
        self._ranker = ranker
        self._activity = activity
        self._live_fetcher = live_fetcher

    async def aclose(self) -> None:
        return None

    async def retrieve_ranked(self, request: QueryRequest, *, user: User) -> list[Candidate]:
        settings = get_settings()
        # Fan out indexed retrieval and (conditionally) live fetch concurrently.
        retrieve_task = asyncio.create_task(
            self._retriever.retrieve(query=request.query, user=user, k=max(request.k, 10))
        )
        live: list[Candidate] = []
        if settings.live_fetch_enabled and needs_live_fetch(request.query):
            try:
                live = await asyncio.wait_for(
                    self._live_fetcher.fetch(query=request.query, user=user),
                    timeout=settings.live_fetch_timeout_ms / 1000.0,
                )
            except Exception as e:  # timeout or any error: never block the answer on live fetch
                logger.warning("Live Fetch unavailable; continuing index-only: %s", e)
                live = []

        indexed = await retrieve_task
        if not indexed and not live:
            return []

        # Query-time ACL re-check applies ONLY to indexed candidates; live results
        # were already permission-trimmed by Graph's user-scoped token.
        if indexed:
            indexed = await self._acl_store.recheck(candidates=indexed, user=user)

        candidates = indexed + live
        if not candidates:
            return []

        doc_ids = [c.chunk.doc_id for c in candidates]
        # People proximity over the surviving candidate docs.
        # Spec §3.2: Cosmos down -> skip People signal (proximity=0); ranker still runs.
        # A Cosmos/Gremlin failure can surface as various exception types from
        # gremlinpython/aiohttp, so catch broad Exception (excludes CancelledError).
        try:
            proximity = await self._proximity.score(user=user, doc_ids=doc_ids)
        except Exception as e:
            logger.warning("People graph (Cosmos) unavailable; degrading to proximity=0: %s", e)
            proximity = {}
        # Activity engagement signal. Spec §3.2: ADX down -> skip Activity (activity=0).
        try:
            activity = await self._activity.score(user=user, doc_ids=doc_ids)
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
