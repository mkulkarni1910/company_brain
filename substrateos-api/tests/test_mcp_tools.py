from datetime import datetime, timezone

import pytest

from app.domain.identity import User
from app.domain.query import Answer, Citation
from app.domain.search import SearchHit, SearchResponse
from app.mcp.server import _ask, _search


def _user():
    return User(user_id="u9", tenant_id="t-eval", email="u9@x",
                display_name="U9", group_ids=set())


class FakeOrch:
    def __init__(self):
        self.seen = None
    async def answer(self, request, *, user, user_token=None):
        self.seen = user
        return Answer(
            text="Economy fares only.",
            citations=[Citation(doc_id="d1", chunk_id="c1", source_url="https://x/d1",
                                title="Travel Policy", snippet="...")],
            query_id="q1",
        )


class FakeSearch:
    async def result(self, *, user, query, top=10, skip=0, sources=None,
                     date_from=None, author_id=None):
        return SearchResponse(
            query=query,
            results=[SearchHit(doc_id="d1", title="Travel Policy", source="sharepoint",
                               source_url="https://x/d1", author_id=None,
                               modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                               snippet="Economy fares")],
            facets=[], people=[], authors=[], total=1,
        )


@pytest.mark.asyncio
async def test_ask_calls_orchestrator_with_user_and_includes_sources() -> None:
    orch = FakeOrch()
    out = await _ask("travel policy", _user(), orchestrator=orch)
    assert orch.seen.user_id == "u9"
    assert "Economy fares only." in out
    assert "Travel Policy" in out and "https://x/d1" in out


@pytest.mark.asyncio
async def test_search_formats_titles_and_urls() -> None:
    out = await _search("travel", _user(), search=FakeSearch())
    assert "Travel Policy" in out
    assert "https://x/d1" in out
