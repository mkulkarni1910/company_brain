"""TeamsConnector — pure parser + collect_documents degradation tests."""
from datetime import UTC, datetime

import pytest

from app.connectors.sync import CollectResult
from app.connectors.teams import (
    TeamsConnector,
    _parse_channels,
    _parse_messages,
    _parse_teams,
)


# ---- Pure parser tests ----

def test_parse_teams_returns_ids_and_names():
    data = {
        "value": [
            {"id": "t1", "displayName": "Engineering"},
            {"id": "t2", "displayName": "Sales"},
            {"id": "", "displayName": "Ghost"},   # empty id → dropped
        ]
    }
    teams = _parse_teams(data)
    assert teams == [
        {"team_id": "t1", "name": "Engineering"},
        {"team_id": "t2", "name": "Sales"},
    ]


def test_parse_teams_empty():
    assert _parse_teams({}) == []
    assert _parse_teams({"value": []}) == []


def test_parse_channels_keeps_only_standard():
    data = {
        "value": [
            {"id": "c1", "displayName": "General", "membershipType": "standard"},
            {"id": "c2", "displayName": "Private Chan", "membershipType": "private"},
            {"id": "c3", "displayName": "Shared", "membershipType": "shared"},
            {"id": "c4", "displayName": "Another", "membershipType": "standard"},
        ]
    }
    channels = _parse_channels(data)
    assert [ch["channel_id"] for ch in channels] == ["c1", "c4"]
    assert channels[0]["name"] == "General"


def test_parse_channels_empty():
    assert _parse_channels({"value": []}) == []
    assert _parse_channels({}) == []


def test_parse_messages_html_stripped():
    data = {
        "value": [
            {
                "id": "m1",
                "messageType": "message",
                "body": {"contentType": "html", "content": "<p>Hello <b>world</b></p>"},
                "from": {"user": {"id": "u1"}},
                "webUrl": "https://teams/m1",
                "createdDateTime": "2026-01-01T12:00:00Z",
                "lastModifiedDateTime": "2026-01-01T12:01:00Z",
            }
        ]
    }
    docs = _parse_messages(data, "t1", "c1", "Engineering", "General", "brain-t")
    assert len(docs) == 1
    doc = docs[0]
    assert doc.doc_id == "teams:t1:c1:m1"
    assert doc.source == "teams"
    assert doc.tenant_id == "brain-t"
    assert doc.acl_principals == ["brain-t:everyone"]
    assert doc.title == "Engineering / General"
    assert "Hello" in doc.body and "<p>" not in doc.body
    assert doc.author_id == "u1"
    assert doc.mime == "text/plain"


def test_parse_messages_plain_text():
    data = {
        "value": [
            {
                "id": "m2",
                "messageType": "message",
                "body": {"contentType": "text", "content": "Plain message"},
                "from": {"user": {"id": "u2"}},
                "createdDateTime": "2026-01-01T12:00:00Z",
            }
        ]
    }
    docs = _parse_messages(data, "t1", "c1", "T", "C", "brain-t")
    assert len(docs) == 1
    assert docs[0].body == "Plain message"


def test_parse_messages_skips_system_messages():
    data = {
        "value": [
            {
                "id": "sys1",
                "messageType": "systemEventMessage",
                "body": {"contentType": "text", "content": "User joined"},
                "createdDateTime": "2026-01-01T12:00:00Z",
            },
            {
                "id": "m1",
                "messageType": "message",
                "body": {"contentType": "text", "content": "Hello"},
                "createdDateTime": "2026-01-01T12:00:00Z",
            },
        ]
    }
    docs = _parse_messages(data, "t1", "c1", "T", "C", "brain-t")
    assert len(docs) == 1
    assert docs[0].doc_id == "teams:t1:c1:m1"


def test_parse_messages_skips_empty_body():
    data = {
        "value": [
            {
                "id": "m1",
                "messageType": "message",
                "body": {"contentType": "text", "content": "   "},
                "createdDateTime": "2026-01-01T12:00:00Z",
            },
            {
                "id": "m2",
                "messageType": "message",
                "body": {"contentType": "html", "content": "<p> </p>"},
                "createdDateTime": "2026-01-01T12:00:00Z",
            },
        ]
    }
    docs = _parse_messages(data, "t1", "c1", "T", "C", "brain-t")
    assert docs == []


def test_parse_messages_doc_id_format():
    data = {
        "value": [
            {
                "id": "msg-abc",
                "messageType": "message",
                "body": {"contentType": "text", "content": "Hi"},
                "createdDateTime": "2026-01-01T12:00:00Z",
            }
        ]
    }
    docs = _parse_messages(data, "team-1", "chan-2", "T", "C", "brain-t")
    assert docs[0].doc_id == "teams:team-1:chan-2:msg-abc"


def test_parse_messages_acl_uses_brain_tenant():
    data = {
        "value": [
            {
                "id": "m1",
                "messageType": "message",
                "body": {"contentType": "text", "content": "Hello"},
                "createdDateTime": "2026-01-01T12:00:00Z",
            }
        ]
    }
    docs = _parse_messages(data, "t1", "c1", "T", "C", "my-brain-tenant")
    assert docs[0].acl_principals == ["my-brain-tenant:everyone"]
    assert docs[0].tenant_id == "my-brain-tenant"


# ---- collect_documents degradation tests ----

@pytest.mark.asyncio
async def test_collect_documents_degrades_on_token_error(monkeypatch):
    c = TeamsConnector(tenant_id="tenantX")

    async def boom(*a, **k):
        raise RuntimeError("token failed")

    monkeypatch.setattr(c, "_token", boom)
    result = await c.collect_documents(cap=100)
    assert isinstance(result, CollectResult)
    assert result.docs == []


@pytest.mark.asyncio
async def test_collect_documents_degrades_on_get_json_error(monkeypatch):
    c = TeamsConnector(tenant_id="tenantX")

    async def boom(*a, **k):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(c, "_get_json", boom)
    result = await c.collect_documents(cap=100)
    assert isinstance(result, CollectResult)
    assert result.docs == []


@pytest.mark.asyncio
async def test_collect_documents_returns_docs_from_fake_graph(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("BRAIN_TENANT_ID", "brain-t")
    get_settings.cache_clear()

    c = TeamsConnector(tenant_id="tenantX")

    async def fake_list_teams(self=None):
        return [{"team_id": "t1", "name": "Engineering"}]

    async def fake_list_channels(team_id, self=None):
        return [{"channel_id": "c1", "name": "General"}]

    async def fake_messages_raw(team_id, channel_id, self=None):
        return {
            "value": [
                {
                    "id": "m1",
                    "messageType": "message",
                    "body": {"contentType": "text", "content": "Hello team"},
                    "from": {"user": {"id": "u1"}},
                    "createdDateTime": "2026-01-01T12:00:00Z",
                    "lastModifiedDateTime": "2026-01-01T12:01:00Z",
                }
            ]
        }

    monkeypatch.setattr(c, "list_teams", fake_list_teams)
    monkeypatch.setattr(c, "list_channels", fake_list_channels)
    monkeypatch.setattr(c, "_list_messages_raw", fake_messages_raw)

    result = await c.collect_documents(cap=100)
    get_settings.cache_clear()

    assert isinstance(result, CollectResult)
    assert len(result.docs) == 1
    doc = result.docs[0]
    assert doc.doc_id == "teams:t1:c1:m1"
    assert doc.tenant_id == "brain-t"
    assert doc.source == "teams"
    assert doc.acl_principals == ["brain-t:everyone"]
    assert doc.body == "Hello team"
    assert result.skipped == 0
    assert result.truncated is False


@pytest.mark.asyncio
async def test_collect_documents_skips_system_messages_increments_skipped(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("BRAIN_TENANT_ID", "brain-t")
    get_settings.cache_clear()

    c = TeamsConnector(tenant_id="tenantX")

    async def fake_list_teams(self=None):
        return [{"team_id": "t1", "name": "T"}]

    async def fake_list_channels(team_id, self=None):
        return [{"channel_id": "c1", "name": "C"}]

    async def fake_messages_raw(team_id, channel_id, self=None):
        return {
            "value": [
                {
                    "id": "sys1",
                    "messageType": "systemEventMessage",
                    "body": {"contentType": "text", "content": "Joined"},
                    "createdDateTime": "2026-01-01T12:00:00Z",
                },
                {
                    "id": "m1",
                    "messageType": "message",
                    "body": {"contentType": "text", "content": "Real message"},
                    "createdDateTime": "2026-01-01T12:00:00Z",
                },
            ]
        }

    monkeypatch.setattr(c, "list_teams", fake_list_teams)
    monkeypatch.setattr(c, "list_channels", fake_list_channels)
    monkeypatch.setattr(c, "_list_messages_raw", fake_messages_raw)

    result = await c.collect_documents(cap=100)
    get_settings.cache_clear()

    assert len(result.docs) == 1
    assert result.skipped == 1


@pytest.mark.asyncio
async def test_collect_documents_truncates_at_cap(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("BRAIN_TENANT_ID", "brain-t")
    get_settings.cache_clear()

    c = TeamsConnector(tenant_id="tenantX")

    async def fake_list_teams(self=None):
        return [{"team_id": "t1", "name": "T"}]

    async def fake_list_channels(team_id, self=None):
        return [{"channel_id": "c1", "name": "C"}]

    async def fake_messages_raw(team_id, channel_id, self=None):
        msgs = [
            {
                "id": f"m{i}",
                "messageType": "message",
                "body": {"contentType": "text", "content": f"msg {i}"},
                "createdDateTime": "2026-01-01T12:00:00Z",
            }
            for i in range(5)
        ]
        return {"value": msgs}

    monkeypatch.setattr(c, "list_teams", fake_list_teams)
    monkeypatch.setattr(c, "list_channels", fake_list_channels)
    monkeypatch.setattr(c, "_list_messages_raw", fake_messages_raw)

    result = await c.collect_documents(cap=3)
    get_settings.cache_clear()

    assert len(result.docs) == 3
    assert result.truncated is True
