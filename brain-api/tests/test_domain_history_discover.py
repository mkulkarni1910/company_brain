from datetime import UTC, datetime

from app.domain.discover import DiscoverResult, SourceActivity, TrendingDoc
from app.domain.history import HistoryEntry


def test_history_entry_roundtrips_json() -> None:
    e = HistoryEntry(query="pto?", query_id="q1", ts=datetime(2026, 5, 31, tzinfo=UTC))
    assert HistoryEntry.model_validate_json(e.model_dump_json()).query == "pto?"


def test_discover_result_shape() -> None:
    r = DiscoverResult(
        trending=[TrendingDoc(doc_id="d1", title="T", source="uploaded",
                              source_url="http://x", snippet="s", score=1.5)],
        by_source=[SourceActivity(source="uploaded", events=3, score=2.0)],
        window_days=14,
    )
    assert r.trending[0].doc_id == "d1"
    assert r.by_source[0].events == 3
    assert r.window_days == 14
