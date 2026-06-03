from datetime import UTC, datetime

from app.domain.search import PersonHit, SearchHit, SearchPage, SearchResponse, SourceFacet


def test_models_construct() -> None:
    hit = SearchHit(doc_id="d1", title="T", source="sharepoint", source_url="http://x",
                    author_id="u1", modified_at=datetime(2026, 5, 31, tzinfo=UTC), snippet="s")
    page = SearchPage(results=[hit], facets=[SourceFacet(source="sharepoint", count=3)], total=3)
    resp = SearchResponse(query="q", results=[hit],
                          facets=page.facets, people=[PersonHit(user_id="u1", display_name="Priya")],
                          total=3)
    assert resp.results[0].doc_id == "d1"
    assert resp.facets[0].count == 3
    assert resp.people[0].display_name == "Priya"
