import pytest

from app.live_fetch.base import needs_live_fetch


@pytest.mark.parametrize("query", [
    "who is on call right now?",
    "what changed this week?",
    "current pipeline coverage",
    "latest deployment status",
    "what's happening today",
    "recent incidents",
])
def test_freshness_queries_trigger(query: str) -> None:
    assert needs_live_fetch(query) is True


@pytest.mark.parametrize("query", [
    "what is our PTO policy?",
    "how do I claim travel expenses",
    "Q3 sales plan ARR target",
])
def test_static_queries_do_not_trigger(query: str) -> None:
    assert needs_live_fetch(query) is False
