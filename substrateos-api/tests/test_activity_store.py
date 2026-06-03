from datetime import UTC, datetime, timedelta

import pytest

from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent


@pytest.mark.integration
async def test_create_ingest_and_score() -> None:
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        # 3 recent views of doc-hot by u-act; doc-cold gets nothing.
        for i in range(3):
            await store.ingest_event(ActivityEvent(
                timestamp=now - timedelta(hours=i),
                tenant_id="t-test", user_id="u-act", doc_id="adoc-hot",
                event_type="view", source="uploaded",
            ))
        # ADX inline ingestion is near-immediate but allow brief settle.
        scores = await store.engagement_scores(
            tenant_id="t-test", user_id="u-act", doc_ids=["adoc-hot", "adoc-cold"]
        )
        assert scores.get("adoc-hot", 0.0) > scores.get("adoc-cold", 0.0)
    finally:
        await store.aclose()
