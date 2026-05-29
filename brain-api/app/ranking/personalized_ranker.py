"""Personalized multi-signal ranker (Phase 2b: Content + People + Activity).

final = w_content * normalize(content_rrf)
      + w_people  * proximity
      + w_activity * activity

Content uses the retriever's RRF score (rank-derived); proximity is the People
pillar signal; activity is the engagement signal — both in [0,1]. Weights are
injected (sourced from Settings by the orchestrator).
"""

from __future__ import annotations

from app.domain.query import Candidate, RankedResult


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    hi = max(values.values())
    if hi <= 0:
        return {k: 0.0 for k in values}
    return {k: v / hi for k, v in values.items()}


class PersonalizedRanker:
    def __init__(
        self, *, weight_content: float, weight_people: float, weight_activity: float = 0.0
    ) -> None:
        self._wc = weight_content
        self._wp = weight_people
        self._wa = weight_activity

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
        content_norm = _normalize(
            {c.chunk.chunk_id: c.raw_scores.get("content_rrf", 0.0) for c in candidates}
        )
        scored: list[RankedResult] = []
        for c in candidates:
            content = content_norm.get(c.chunk.chunk_id, 0.0)
            people = proximity.get(c.chunk.doc_id, 0.0)
            engagement = activity.get(c.chunk.doc_id, 0.0)
            final = self._wc * content + self._wp * people + self._wa * engagement
            scored.append(
                RankedResult(
                    candidate=c,
                    final_score=final,
                    signal_breakdown={
                        "content": content,
                        "people": people,
                        "activity": engagement,
                    },
                    rank=0,
                )
            )
        scored.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(scored):
            r.rank = i
        return scored
