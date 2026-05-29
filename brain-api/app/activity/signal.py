"""Activity engagement ranking signal.

Wraps ActivityStore.engagement_scores and normalizes the raw recency-weighted
sums to [0,1] across the candidate set — same shape as PeopleProximity.score,
so the ranker treats People and Activity uniformly.
"""

from __future__ import annotations

from app.domain.identity import User


class ActivitySignal:
    def __init__(self, *, store) -> None:
        self._store = store

    async def score(self, *, user: User, doc_ids: list[str]) -> dict[str, float]:
        if not doc_ids:
            return {}
        raw = await self._store.engagement_scores(
            tenant_id=user.tenant_id, user_id=user.user_id, doc_ids=doc_ids
        )
        if not raw:
            return {d: 0.0 for d in doc_ids}
        hi = max(raw.values())
        if hi <= 0:
            return {d: 0.0 for d in doc_ids}
        return {d: (raw.get(d, 0.0) / hi) for d in doc_ids}
