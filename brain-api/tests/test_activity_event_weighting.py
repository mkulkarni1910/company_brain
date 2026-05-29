from datetime import UTC, datetime, timedelta

import pytest

from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent


@pytest.mark.integration
async def test_thumbs_down_lowers_score_below_thumbs_up() -> None:
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        await store.ingest_event(ActivityEvent(
            timestamp=now, tenant_id="t-test", user_id="u-w", doc_id="wdoc-up",
            event_type="thumbs_up", source="uploaded"))
        await store.ingest_event(ActivityEvent(
            timestamp=now - timedelta(minutes=1), tenant_id="t-test", user_id="u-w",
            doc_id="wdoc-down", event_type="thumbs_down", source="uploaded"))
        scores = await store.engagement_scores(
            tenant_id="t-test", user_id="u-w", doc_ids=["wdoc-up", "wdoc-down"])
        # thumbs_up is positive; thumbs_down is negative
        assert scores.get("wdoc-up", 0.0) > 0.0
        assert scores.get("wdoc-down", 0.0) < 0.0
    finally:
        await store.aclose()
