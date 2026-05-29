from datetime import UTC, datetime

from app.domain.activity import ActivityEvent


def test_activity_event_defaults_and_fields() -> None:
    now = datetime.now(UTC)
    e = ActivityEvent(
        timestamp=now,
        tenant_id="t-test",
        user_id="u-1",
        doc_id="up:policy-pto",
        event_type="view",
        source="uploaded",
    )
    assert e.query_id is None
    assert e.chunk_id is None
    assert e.duration_ms is None
    assert e.event_type == "view"


def test_activity_event_rejects_bad_event_type() -> None:
    import pytest
    from pydantic import ValidationError

    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        ActivityEvent(
            timestamp=now, tenant_id="t", user_id="u", doc_id="d",
            event_type="not-a-real-type", source="uploaded",
        )
