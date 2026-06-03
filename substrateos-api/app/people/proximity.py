"""People-proximity ranking signal.

For each candidate doc, count how many of the document's authors are reachable
from the user within 2 hops over {manages, member_of, authored} (treated
undirected). Normalize counts to [0,1] across the candidate set. A user who
authored the doc, or whose manager/teammate authored it, scores high.
"""

from __future__ import annotations

from app.domain.identity import User
from app.people.graph_client import PeopleGraphClient


class PeopleProximity:
    def __init__(self, *, graph: PeopleGraphClient) -> None:
        self._graph = graph

    async def score(self, *, user: User, doc_ids: list[str]) -> dict[str, float]:
        if not doc_ids:
            return {}
        # Reachability count per doc: authors of each doc within 2 undirected hops.
        raw = await self._graph.submit(
            "g.V().has('user','user_id', uid).has('tenant_id', tid)"
            ".repeat(both('manages','member_of','collaborates_with').simplePath()).times(2)"
            ".dedup().in('authored').has('doc_id', within(dids))"
            ".groupCount().by('doc_id')",
            {"uid": user.user_id, "tid": user.tenant_id, "dids": doc_ids},
        )
        # groupCount returns a single map; default to empty.
        counts: dict[str, float] = {}
        if raw and isinstance(raw[0], dict):
            counts = {k: float(v) for k, v in raw[0].items()}
        # Self-authored docs: direct authored edge counts strongly too.
        self_authored = await self._graph.submit(
            "g.V().has('user','user_id', uid).has('tenant_id', tid)"
            ".out('authored').has('doc_id', within(dids)).values('doc_id')",
            {"uid": user.user_id, "tid": user.tenant_id, "dids": doc_ids},
        )
        for did in self_authored or []:
            counts[did] = counts.get(did, 0.0) + 2.0  # self-authorship weighted

        if not counts:
            return {d: 0.0 for d in doc_ids}
        hi = max(counts.values())
        return {d: (counts.get(d, 0.0) / hi if hi > 0 else 0.0) for d in doc_ids}
