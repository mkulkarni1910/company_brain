import pytest
from app.connectors.sharepoint import _parse_sites, _parse_drive_children, SharePointConnector

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
