from datetime import UTC, datetime, timedelta

import pytest

from app.activity.signal import ActivitySignal
from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent
from app.domain.identity import User


@pytest.mark.integration
async def test_signal_normalizes_to_unit_interval() -> None:
    store = ActivityStore()
    try:
        await store.ensure_table()
        now = datetime.now(UTC)
        for i in range(4):
            await store.ingest_event(ActivityEvent(
                timestamp=now - timedelta(hours=i),
                tenant_id="t-test", user_id="u-sig", doc_id="sdoc-hot",
                event_type="view", source="uploaded",
            ))
        signal = ActivitySignal(store=store)
        user = User(user_id="u-sig", tenant_id="t-test", email="s@x",
                    display_name="S", group_ids=set())
        scores = await signal.score(user=user, doc_ids=["sdoc-hot", "sdoc-cold"])
        assert scores["sdoc-hot"] == 1.0      # max normalizes to 1.0
        assert scores["sdoc-cold"] == 0.0     # no engagement
        assert all(0.0 <= v <= 1.0 for v in scores.values())
    finally:
        await store.aclose()


def test_empty_doc_ids_returns_empty() -> None:
    import asyncio

    class _FakeStore:
        async def engagement_scores(self, **_):
            return {}

    sig = ActivitySignal(store=_FakeStore())
    from app.domain.identity import User as U
    u = U(user_id="u", tenant_id="t", email="a@b", display_name="A", group_ids=set())
    assert asyncio.run(sig.score(user=u, doc_ids=[])) == {}
