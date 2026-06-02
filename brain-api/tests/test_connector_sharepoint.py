from datetime import UTC, datetime

import pytest

from app.connectors.sharepoint import SharePointConnector, _parse_drive_children, _parse_sites
from app.connectors.sync import CollectResult

def test_parse_sites():
    data={"value":[{"id":"s1","displayName":"Sales","webUrl":"https://x/sales"},
                   {"id":"s2","name":"Eng","webUrl":"https://x/eng"}]}
    sites=_parse_sites(data)
    assert sites[0]=={"site_id":"s1","name":"Sales","web_url":"https://x/sales"}
    assert sites[1]["name"]=="Eng"

def test_parse_drive_children_splits_files_and_folders():
    data={"value":[
        {"id":"f1","name":"plan.docx","file":{"mimeType":"application/vnd...docx"},
         "size":10,"webUrl":"https://x/plan","createdBy":{"user":{"id":"u1"}}},
        {"id":"d1","name":"sub","folder":{"childCount":2}},
    ]}
    files, folders = _parse_drive_children(data, drive_id="dr1")
    assert len(files)==1 and files[0].name=="plan.docx" and files[0].author_id=="u1"
    assert folders==["d1"]

@pytest.mark.asyncio
async def test_list_sites_degrades_on_error(monkeypatch):
    c=SharePointConnector()
    async def boom(*a, **k): raise RuntimeError("401")
    monkeypatch.setattr(c, "_get_json", boom)
    assert await c.list_sites()==[]

@pytest.mark.asyncio
async def test_list_files_degrades_on_error(monkeypatch):
    c = SharePointConnector()
    async def boom(*a, **k): raise RuntimeError("401")
    monkeypatch.setattr(c, "_get_json", boom)
    assert await c.list_files("site-1") == []

@pytest.mark.asyncio
async def test_fetch_content_degrades_on_error(monkeypatch):
    c = SharePointConnector()
    async def boom(*a, **k): raise RuntimeError("token failed")
    monkeypatch.setattr(c, "_token", boom)
    assert await c.fetch_content("d", "i") is None


@pytest.mark.asyncio
async def test_token_uses_connected_tenant(monkeypatch):
    import app.connectors.sharepoint as sp
    from app.config import get_settings
    monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "sec")
    get_settings.cache_clear()
    captured = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"access_token": "tok"}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, data=None): captured["url"] = url; return FakeResp()

    monkeypatch.setattr(sp.httpx, "AsyncClient", FakeClient)
    c = sp.SharePointConnector(tenant_id="tenantX")
    tok = await c._token()
    get_settings.cache_clear()
    assert tok == "tok" and captured["url"].endswith("/tenantX/oauth2/v2.0/token")


@pytest.mark.asyncio
async def test_collect_documents_builds_source_docs(monkeypatch):
    """collect_documents crawls sites → files → content → SourceDocs."""
    from app.config import get_settings
    from app.connectors.models import RemoteFile

    monkeypatch.setenv("BRAIN_TENANT_ID", "brain-t")
    get_settings.cache_clear()

    now = datetime.now(UTC)
    fake_sites = [{"site_id": "s1", "name": "Sales", "web_url": "https://x/sales"}]
    fake_files = [
        RemoteFile(drive_id="d1", item_id="f1", name="plan.md",
                   web_url="https://x/plan", size=10,
                   created_at=now, modified_at=now, author_id="u1"),
        RemoteFile(drive_id="d1", item_id="f2", name="notes.md",
                   web_url="https://x/notes", size=5,
                   created_at=now, modified_at=now, author_id=None),
    ]
    fake_contents = {"f1": b"# Sales Plan", "f2": None}  # f2 has no content → skipped

    c = SharePointConnector(tenant_id="tenantX")

    async def fake_list_sites(self=None):
        return fake_sites

    async def fake_list_files(site_id, max_items=None, self=None):
        return fake_files

    async def fake_fetch_content(drive_id, item_id, self=None):
        return fake_contents.get(item_id)

    monkeypatch.setattr(c, "list_sites", fake_list_sites)
    monkeypatch.setattr(c, "list_files", fake_list_files)
    monkeypatch.setattr(c, "fetch_content", fake_fetch_content)

    result = await c.collect_documents(cap=100)
    get_settings.cache_clear()

    assert isinstance(result, CollectResult)
    assert len(result.docs) == 1
    assert result.skipped == 1
    assert result.truncated is False
    doc = result.docs[0]
    assert doc.doc_id == "sp:s1:f1"
    assert doc.tenant_id == "brain-t"
    assert doc.source == "sharepoint"
    assert doc.acl_principals == ["brain-t:everyone"]
    assert doc.title == "plan.md"


@pytest.mark.asyncio
async def test_collect_documents_degrades_on_error(monkeypatch):
    """If list_sites raises, collect_documents returns an empty CollectResult."""
    c = SharePointConnector(tenant_id="tenantX")

    async def boom(*a, **k):
        raise RuntimeError("graph down")

    monkeypatch.setattr(c, "list_sites", boom)
    result = await c.collect_documents(cap=100)
    assert isinstance(result, CollectResult)
    assert result.docs == []
