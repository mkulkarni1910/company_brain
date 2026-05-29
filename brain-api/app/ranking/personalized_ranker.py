"""Personalized multi-signal ranker (Phase 2a: Content + People).

final = w_content * normalize(content_rrf) + w_people * proximity

Content uses the retriever's RRF score (already rank-derived); proximity is the
People-pillar signal in [0,1]. Activity (ADX) is added as a third weighted term
in Phase 2b. Weights are injected (sourced from Settings by the orchestrator).
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
    def __init__(self, *, weight_content: float, weight_people: float) -> None:
        self._wc = weight_content
        self._wp = weight_people

    def rank(
        self, *, candidates: list[Candidate], proximity: dict[str, float]
    ) -> list[RankedResult]:
        if not candidates:
            return []
        content_norm = _normalize(
            {c.chunk.chunk_id: c.raw_scores.get("content_rrf", 0.0) for c in candidates}
        )
        scored: list[RankedResult] = []
        for c in candidates:
            content = content_norm.get(c.chunk.chunk_id, 0.0)
            people = proximity.get(c.chunk.doc_id, 0.0)
            final = self._wc * content + self._wp * people
            scored.append(
                RankedResult(
                    candidate=c,
                    final_score=final,
                    signal_breakdown={"content": content, "people": people},
                    rank=0,
                )
            )
        scored.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(scored):
            r.rank = i
        return scored
