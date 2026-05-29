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
    # Substring false-positives that must NOT trigger:
    "what is our knowledge base policy",   # "know" contains "now" but not " now "
    "concurrent users limit",              # "concurrent" contains "current"
    "the recurrent meeting cadence",       # "recurrent" contains "current"/"recent"
    "who are the known contacts",          # "known" contains "now"
])
def test_static_queries_do_not_trigger(query: str) -> None:
    assert needs_live_fetch(query) is False
