from __future__ import annotations

import logging
from datetime import datetime

from app.domain.identity import User
from app.domain.search import PersonFacet, PersonHit, SearchResponse

logger = logging.getLogger(__name__)


class SearchService:
    """Enterprise search: a fast faceted result page + people/authors resolved from
    result authors. The AI Overview is intentionally NOT produced here — the client
    requests it separately (via /query) so the slow LLM call never blocks the result
    list. Every part degrades independently; the endpoint never 500s on a data-layer
    failure."""

    def __init__(self, *, embedder, search, people) -> None:
        self._embedder = embedder
        self._search = search
        self._people = people

    async def result(
        self, *, user: User, query: str, top: int = 10, skip: int = 0,
        sources: list[str] | None = None, date_from: datetime | None = None,
        author_id: str | None = None,
    ) -> SearchResponse:
        q = query.strip()
        if not q:
            return SearchResponse(query=query, results=[], facets=[],
                                  people=[], authors=[], total=0)

        vector = await self._embedder.embed(q)
        page = await self._search.search_page(
            query=q, user=user, vector=vector, top=top, skip=skip,
            sources=sources, date_from=date_from, author_id=author_id,
        )

        result_author_ids = [h.author_id for h in page.results if h.author_id]
        facet_author_ids = [a for a, _ in page.author_facets]
        all_ids = list(dict.fromkeys([*result_author_ids, *facet_author_ids]))
        try:
            resolved = await self._people.resolve_people(all_ids, user.tenant_id)
        except Exception as e:  # noqa: BLE001 - people block is best-effort
            logger.warning("search people resolve failed: %s", e)
            resolved = []
        name_by_id = {p.user_id: p.display_name for p in resolved}

        seen: set[str] = set()
        people: list[PersonHit] = []
        for uid in result_author_ids:
            if uid in name_by_id and uid not in seen:
                seen.add(uid)
                people.append(PersonHit(user_id=uid, display_name=name_by_id[uid]))
        authors = [
            PersonFacet(user_id=uid, display_name=name_by_id[uid], count=count)
            for uid, count in page.author_facets
            if uid in name_by_id
        ]

        return SearchResponse(
            query=q, results=page.results, facets=page.facets,
            people=people, authors=authors, total=page.total,
        )
