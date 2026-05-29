"""Personalized multi-signal ranker (Phase 4: Content + People + Activity + Recency).

final = w_content  * normalize(content_rrf)
      + w_people   * proximity
      + w_activity  * activity
      + w_recency  * recency        (recency = exp(-Δdays / 30) from modified_at)

Recency lets fresh content — and Live Fetch results (modified_at = now) — surface.
Weights are injected (sourced from Settings by the orchestrator).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.domain.query import Candidate, RankedResult

_RECENCY_TAU_DAYS = 30.0


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    hi = max(values.values())
    if hi <= 0:
        return {k: 0.0 for k in values}
    return {k: v / hi for k, v in values.items()}


def _recency(modified_at: datetime, now: datetime) -> float:
    days = max(0.0, (now - modified_at).total_seconds() / 86400.0)
    return math.exp(-days / _RECENCY_TAU_DAYS)


class PersonalizedRanker:
    def __init__(
        self,
        *,
        weight_content: float,
        weight_people: float,
        weight_activity: float = 0.0,
        weight_recency: float = 0.0,
    ) -> None:
        self._wc = weight_content
        self._wp = weight_people
        self._wa = weight_activity
        self._wr = weight_recency

    def rank(
        self,
        *,
        candidates: list[Candidate],
        proximity: dict[str, float],
        activity: dict[str, float] | None = None,
    ) -> list[RankedResult]:
        if not candidates:
            return []
        activity = activity or {}
        now = datetime.now(UTC)
        content_norm = _normalize(
            {c.chunk.chunk_id: c.raw_scores.get("content_rrf", 0.0) for c in candidates}
        )
        scored: list[RankedResult] = []
        for c in candidates:
            content = content_norm.get(c.chunk.chunk_id, 0.0)
            people = proximity.get(c.chunk.doc_id, 0.0)
            engagement = activity.get(c.chunk.doc_id, 0.0)
            recency = _recency(c.chunk.modified_at, now)
            final = (
                self._wc * content
                + self._wp * people
                + self._wa * engagement
                + self._wr * recency
            )
            scored.append(
                RankedResult(
                    candidate=c,
                    final_score=final,
                    signal_breakdown={
                        "content": content,
                        "people": people,
                        "activity": engagement,
                        "recency": recency,
                    },
                    rank=0,
                )
            )
        scored.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(scored):
            r.rank = i
        return scored
