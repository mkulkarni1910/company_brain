import pytest

from app.domain.identity import User
from app.live_fetch.graph_search import MSGraphSearchFetcher


@pytest.mark.integration
async def test_graph_search_returns_candidates_or_empty() -> None:
    fetcher = MSGraphSearchFetcher()
    user = User(user_id="u-live", tenant_id="t-test", email="l@x",
                display_name="L", group_ids=set())
    # A broad query so a non-empty tenant likely returns hits. Must NOT raise.
    results = await fetcher.fetch(query="plan", user=user)
    assert isinstance(results, list)
    # If the tenant has searchable content, every live candidate is shaped right.
    for c in results:
        assert "live" in c.sources_hit
        assert c.chunk.source == "graph"
        assert c.chunk.doc_id.startswith("graph:")
        assert "content_rrf" in c.raw_scores
    # Empty tenant -> empty list is a valid pass (no assertion on non-emptiness).


@pytest.mark.integration
async def test_graph_search_never_raises_on_gibberish() -> None:
    fetcher = MSGraphSearchFetcher()
    user = User(user_id="u-live", tenant_id="t-test", email="l@x",
                display_name="L", group_ids=set())
    results = await fetcher.fetch(query="zzqxwv-nonexistent-term-9981", user=user)
    assert results == [] or all("live" in c.sources_hit for c in results)
