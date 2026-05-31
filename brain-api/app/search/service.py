from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.domain.identity import User
from app.domain.query import Answer, QueryRequest
from app.domain.search import SearchResponse

logger = logging.getLogger(__name__)


class SearchService:
    """Glean-style search: faceted result page + grounded AI Overview + people-from-authors.
    Each part degrades independently; the endpoint never 500s on a data-layer failure."""

    def __init__(self, *, embedder, search, orchestrator, people) -> None:
        self._embedder = embedder
        self._search = search
        self._orchestrator = orchestrator
        self._people = people

    async def _overview(self, *, user: User, query: str) -> Answer | None:
        try:
            return await self._orchestrator.answer(QueryRequest(query=query), user=user)
        except Exception as e:  # noqa: BLE001 - overview is optional
            logger.warning("search overview failed: %s", e)
            return None

    async def result(
        self, *, user: User, query: str, top: int = 10, skip: int = 0,
        sources: list[str] | None = None, date_from: datetime | None = None,
        author_id: str | None = None,
    ) -> SearchResponse:
        q = query.strip()
        if not q:
            return SearchResponse(query=query, answer=None, results=[], facets=[], people=[], total=0)

        vector = await self._embedder.embed(q)
        page, answer = await asyncio.gather(
            self._search.search_page(
                query=q, user=user, vector=vector, top=top, skip=skip,
                sources=sources, date_from=date_from, author_id=author_id,
            ),
            self._overview(user=user, query=q),
        )

        author_ids = list(dict.fromkeys(h.author_id for h in page.results if h.author_id))
        try:
            people = await self._people.resolve_people(author_ids, user.tenant_id)
        except Exception as e:  # noqa: BLE001 - people block is best-effort
            logger.warning("search people resolve failed: %s", e)
            people = []

        return SearchResponse(
            query=q, answer=answer, results=page.results,
            facets=page.facets, people=people, total=page.total,
        )
