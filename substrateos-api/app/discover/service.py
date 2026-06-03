from __future__ import annotations

import logging

from app.activity.store import ActivityStore
from app.cache.redis_cache import RedisCache
from app.domain.discover import DiscoverResult, SourceActivity, TrendingDoc
from app.domain.identity import User
from app.retrieval.ai_search_client import AISearchClient

logger = logging.getLogger(__name__)
_CACHE_TTL = 300


class DiscoverService:
    """Tenant-wide Discover surface: trending docs + activity-by-source, ACL-scoped
    to the requesting user. Every sub-step degrades to empty rather than raising."""

    def __init__(
        self, *, activity: ActivityStore, search: AISearchClient, cache: RedisCache
    ) -> None:
        self._activity = activity
        self._search = search
        self._cache = cache

    async def result(
        self, *, user: User, window_days: int = 14, limit: int = 8
    ) -> DiscoverResult:
        key = f"discover:{user.tenant_id}:{user.user_id}"
        cached = await self._cache.get_json(key)
        if cached:
            try:
                return DiscoverResult.model_validate(cached)
            except Exception:  # noqa: BLE001 - ignore corrupt cache
                pass

        # over-fetch trending so ACL filtering still leaves `limit` docs
        scored = await self._activity.trending(
            tenant_id=user.tenant_id, window_days=window_days, limit=limit * 3
        )
        score_by_id = dict(scored)

        docs = {}
        if score_by_id:
            try:
                docs = await self._search.lookup_docs(
                    doc_ids=list(score_by_id), user=user
                )
            except Exception as e:  # noqa: BLE001 - degrade
                logger.warning("discover lookup_docs failed: %s", e)

        trending: list[TrendingDoc] = []
        for doc_id, _ in sorted(score_by_id.items(), key=lambda kv: kv[1], reverse=True):
            c = docs.get(doc_id)
            if c is None:
                continue
            trending.append(
                TrendingDoc(
                    doc_id=doc_id,
                    title=c.title,
                    source=c.source,
                    source_url=c.source_url,
                    snippet=c.content[:160].strip(),
                    score=round(score_by_id[doc_id], 3),
                )
            )
            if len(trending) >= limit:
                break

        by_source: list[SourceActivity] = []
        if trending:
            rows = await self._activity.source_breakdown(
                tenant_id=user.tenant_id,
                doc_ids=[t.doc_id for t in trending],
                window_days=window_days,
            )
            by_source = [
                SourceActivity(source=s, events=e, score=round(sc, 3))
                for s, e, sc in rows
            ]

        res = DiscoverResult(trending=trending, by_source=by_source, window_days=window_days)
        await self._cache.set_json(key, res.model_dump(mode="json"), ttl_seconds=_CACHE_TTL)
        return res
