from datetime import UTC, datetime

import pytest

from app.domain.identity import User
from app.domain.search import PersonHit, SearchHit, SearchPage, SourceFacet
from app.search.service import SearchService


def _hit(doc_id, author):
    return SearchHit(doc_id=doc_id, title=doc_id, source="sharepoint", source_url="http://x",
                     author_id=author, modified_at=datetime(2026, 5, 31, tzinfo=UTC), snippet="s")


class FakeEmbedder:
    async def embed(self, text):
        return [0.1, 0.2]


class FakeSearch:
    def __init__(self, page):
        self._page = page
        self.kwargs = None

    async def search_page(self, **kwargs):
        self.kwargs = kwargs
        return self._page


class FakePeople:
    def __init__(self, people):
        self._p = people
        self.asked = None

    async def resolve_people(self, user_ids, tenant_id):
        self.asked = user_ids
        return self._p


def _user():
    return User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids={"t1:everyone"})


def _svc(page, *, people=None):
    return SearchService(embedder=FakeEmbedder(), search=FakeSearch(page),
                         people=FakePeople(people or []))


@pytest.mark.asyncio
async def test_assembles_results_facets_people() -> None:
    page = SearchPage(results=[_hit("d1", "u1"), _hit("d2", "u2")],
                      facets=[SourceFacet(source="sharepoint", count=2)], total=2)
    svc = _svc(page, people=[PersonHit(user_id="u1", display_name="Priya")])
    resp = await svc.result(user=_user(), query="vision")
    # No AI Overview here — the client fetches it separately via /query.
    assert not hasattr(resp, "answer") or getattr(resp, "answer", None) is None
    assert [h.doc_id for h in resp.results] == ["d1", "d2"]
    assert resp.total == 2 and resp.facets[0].count == 2
    assert resp.people[0].display_name == "Priya"


@pytest.mark.asyncio
async def test_empty_query_returns_empty() -> None:
    page = SearchPage(results=[], facets=[], total=0)
    resp = await _svc(page).result(user=_user(), query="   ")
    assert resp.results == [] and resp.total == 0


@pytest.mark.asyncio
async def test_authors_facet_resolved_with_names_and_counts() -> None:
    page = SearchPage(results=[_hit("d1", "u1")], facets=[SourceFacet(source="sharepoint", count=1)],
                      author_facets=[("u1", 4), ("u2", 2), ("u3", 1)], total=1)
    svc = _svc(page, people=[PersonHit(user_id="u1", display_name="Priya"),
                             PersonHit(user_id="u2", display_name="Sam")])
    resp = await svc.result(user=_user(), query="vision")
    assert [(a.user_id, a.display_name, a.count) for a in resp.authors] == [
        ("u1", "Priya", 4), ("u2", "Sam", 2)]
