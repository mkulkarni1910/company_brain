"""OutlookCalendarConnector — pure parsers, body composition, dedup, degradation."""
import pytest

from app.connectors.outlook_calendar import (
    OutlookCalendarConnector,
    _event_body,
    _parse_events,
)
from app.connectors.sync import CollectResult


def _event(eid, ical=None, subject="Sync", html=False, content="Quarterly plan",
           organizer="org@x.com", attendees=("Bob", "Carol")):
    return {
        "id": eid,
        "iCalUId": ical,
        "subject": subject,
        "body": {"contentType": "html" if html else "text", "content": content},
        "location": {"displayName": "Room 1"},
        "organizer": {"emailAddress": {"address": organizer, "name": "Org"}},
        "attendees": [{"emailAddress": {"name": n, "address": f"{n}@x"}} for n in attendees],
        "start": {"dateTime": "2026-02-01T09:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-02-01T10:00:00", "timeZone": "UTC"},
        "lastModifiedDateTime": "2026-01-15T08:00:00Z",
        "webLink": f"https://outlook/cal/{eid}",
    }


def test_event_body_composes_fields():
    body = _event_body(_event("e1"))
    assert "Sync" in body
    assert "Quarterly plan" in body
    assert "Location: Room 1" in body
    assert "Attendees: Bob, Carol" in body
    assert "When: 2026-02-01T09:00:00" in body


def test_parse_events_fields_and_acl():
    docs = _parse_events({"value": [_event("e1", ical="ICAL-1")]}, "owner-1", "sos-t")
    assert len(docs) == 1
    d = docs[0]
    assert d.doc_id == "outlookcal:ICAL-1"
    assert d.source == "outlook_calendar"
    assert d.acl_principals == ["owner-1"]
    assert d.author_id == "org@x.com"
    assert d.title == "Sync"
    assert d.tenant_id == "sos-t"


def test_parse_events_falls_back_to_event_id():
    docs = _parse_events({"value": [_event("e9", ical=None)]}, "o", "sos-t")
    assert docs[0].doc_id == "outlookcal:e9"


def test_parse_events_skips_removed_and_empty():
    data = {"value": [
        {"id": "e1", "@removed": {"reason": "deleted"}},
        {"id": "e2", "subject": "", "body": {"contentType": "text", "content": ""},
         "start": {}, "attendees": []},
    ]}
    assert _parse_events(data, "o", "sos-t") == []


@pytest.mark.asyncio
async def test_collect_dedups_across_calendars(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("SUBSTRATEOS_TENANT_ID", "sos-t")
    get_settings.cache_clear()
    c = OutlookCalendarConnector(tenant_id="tenantX")

    async def fake_users():
        return [{"user_id": "u1"}, {"user_id": "u2"}]

    async def fake_raw(user_id):
        return {"value": [_event("e1", ical="SHARED")]}

    monkeypatch.setattr(c, "list_users", fake_users)
    monkeypatch.setattr(c, "_list_events_raw", fake_raw)
    result = await c.collect_documents(cap=100)
    get_settings.cache_clear()
    assert isinstance(result, CollectResult)
    assert len(result.docs) == 1
    assert sorted(result.docs[0].acl_principals) == ["u1", "u2"]


@pytest.mark.asyncio
async def test_collect_degrades_on_error(monkeypatch):
    c = OutlookCalendarConnector(tenant_id="tenantX")

    async def boom(*a, **k):
        raise RuntimeError("403")

    monkeypatch.setattr(c, "_get_json", boom)
    result = await c.collect_documents(cap=100)
    assert result.docs == []
