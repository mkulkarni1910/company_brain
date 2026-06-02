"""OutlookMailConnector — pure parsers, dedup/ACL-union, collect degradation."""
import pytest

from app.connectors.outlook_mail import (
    OutlookMailConnector,
    _merge_into,
    _parse_messages,
)
from app.connectors.sync import CollectResult


def _msg(mid, imid=None, subject="Hi", html=False, content="Hello world", sender="a@x.com"):
    return {
        "id": mid,
        "internetMessageId": imid,
        "subject": subject,
        "body": {"contentType": "html" if html else "text", "content": content},
        "from": {"emailAddress": {"address": sender, "name": "A"}},
        "receivedDateTime": "2026-01-01T12:00:00Z",
        "lastModifiedDateTime": "2026-01-01T12:05:00Z",
        "webLink": f"https://outlook/{mid}",
    }


# ---- pure parser ----

def test_parse_messages_html_stripped_and_fields():
    data = {"value": [_msg("m1", imid="<abc@x>", html=True, content="<p>Hello <b>team</b></p>")]}
    docs = _parse_messages(data, "owner-1", "brain-t")
    assert len(docs) == 1
    d = docs[0]
    assert d.doc_id == "outlookmail:<abc@x>"
    assert d.source == "outlook_mail"
    assert d.tenant_id == "brain-t"
    assert d.acl_principals == ["owner-1"]
    assert d.title == "Hi"
    assert "Hello" in d.body and "team" in d.body and "<p>" not in d.body
    assert d.author_id == "a@x.com"
    assert d.mime == "text/plain"


def test_parse_messages_falls_back_to_message_id_when_no_internet_id():
    docs = _parse_messages({"value": [_msg("m9", imid=None)]}, "owner-1", "brain-t")
    assert docs[0].doc_id == "outlookmail:owner-1:m9"


def test_parse_messages_skips_when_no_subject_and_no_body():
    data = {"value": [{"id": "m1", "subject": "  ",
                       "body": {"contentType": "text", "content": "   "}}]}
    assert _parse_messages(data, "o", "brain-t") == []


def test_parse_messages_skips_removed_tombstones():
    data = {"value": [{"id": "m1", "@removed": {"reason": "deleted"}}]}
    assert _parse_messages(data, "o", "brain-t") == []


def test_parse_messages_subject_only_is_kept():
    data = {"value": [{"id": "m1", "subject": "Only subject",
                       "body": {"contentType": "text", "content": ""}}]}
    docs = _parse_messages(data, "o", "brain-t")
    assert len(docs) == 1
    assert "Only subject" in docs[0].body


# ---- merge / union ----

def test_merge_into_unions_acl_on_collision():
    a = _parse_messages({"value": [_msg("m1", imid="<same@x>")]}, "owner-A", "brain-t")
    b = _parse_messages({"value": [_msg("m1", imid="<same@x>")]}, "owner-B", "brain-t")
    by_id: dict = {}
    _merge_into(by_id, a)
    _merge_into(by_id, b)
    assert len(by_id) == 1
    assert by_id["outlookmail:<same@x>"].acl_principals == ["owner-A", "owner-B"]


# ---- collect_documents ----

@pytest.mark.asyncio
async def test_collect_dedups_across_mailboxes_with_acl_union(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("BRAIN_TENANT_ID", "brain-t")
    get_settings.cache_clear()
    c = OutlookMailConnector(tenant_id="tenantX")

    async def fake_users():
        return [{"user_id": "u1", "mail": "u1@x"}, {"user_id": "u2", "mail": "u2@x"}]

    async def fake_raw(user_id):
        # same message present in both mailboxes (sender + recipient)
        return {"value": [_msg("m1", imid="<shared@x>")]}

    monkeypatch.setattr(c, "list_users", fake_users)
    monkeypatch.setattr(c, "_list_messages_raw", fake_raw)
    result = await c.collect_documents(cap=100)
    get_settings.cache_clear()

    assert isinstance(result, CollectResult)
    assert len(result.docs) == 1
    assert sorted(result.docs[0].acl_principals) == ["u1", "u2"]


@pytest.mark.asyncio
async def test_collect_truncates_at_cap(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("BRAIN_TENANT_ID", "brain-t")
    get_settings.cache_clear()
    c = OutlookMailConnector(tenant_id="tenantX")

    async def fake_users():
        return [{"user_id": "u1", "mail": "u1@x"}]

    async def fake_raw(user_id):
        return {"value": [_msg(f"m{i}", imid=f"<m{i}@x>") for i in range(5)]}

    monkeypatch.setattr(c, "list_users", fake_users)
    monkeypatch.setattr(c, "_list_messages_raw", fake_raw)
    result = await c.collect_documents(cap=3)
    get_settings.cache_clear()
    assert len(result.docs) == 3
    assert result.truncated is True


@pytest.mark.asyncio
async def test_collect_degrades_on_token_error(monkeypatch):
    c = OutlookMailConnector(tenant_id="tenantX")

    async def boom(*a, **k):
        raise RuntimeError("token failed")

    monkeypatch.setattr(c, "_token", boom)
    result = await c.collect_documents(cap=100)
    assert isinstance(result, CollectResult)
    assert result.docs == []


@pytest.mark.asyncio
async def test_fetch_message_returns_sourcedoc(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("BRAIN_TENANT_ID", "brain-t")
    get_settings.cache_clear()
    c = OutlookMailConnector(tenant_id="tenantX")

    async def fake_get(url):
        return _msg("m1", imid="<one@x>")

    monkeypatch.setattr(c, "_get_json", fake_get)
    doc = await c.fetch_message("u1", "m1")
    get_settings.cache_clear()
    assert doc is not None
    assert doc.doc_id == "outlookmail:<one@x>"
    assert doc.acl_principals == ["u1"]


@pytest.mark.asyncio
async def test_delta_returns_docs_and_link(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("BRAIN_TENANT_ID", "brain-t")
    get_settings.cache_clear()
    c = OutlookMailConnector(tenant_id="tenantX")

    async def fake_get(url):
        return {"value": [_msg("m1", imid="<d1@x>")],
                "@odata.deltaLink": "https://graph/delta?token=next"}

    monkeypatch.setattr(c, "_get_json", fake_get)
    docs, link = await c.delta("u1", token=None)
    get_settings.cache_clear()
    assert len(docs) == 1
    assert link == "https://graph/delta?token=next"
