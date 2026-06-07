"""Admin directory endpoints: manual sync trigger + redacted listing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.admin_directory import _redact
from app.deps import get_directory_store, get_directory_sync
from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser
from app.main import app


def test_redact():
    assert _redact("tom@omkar.com") == "t***@omkar.com"
    assert _redact("no-at-sign") == "***"


class _Sync:
    async def run(self):
        return {"slack_users": 4, "entra_users": 6, "matched": 4,
                "managers": 1, "agents": 2, "customers": 3, "errors": []}


@pytest.fixture()
def _client(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    app.dependency_overrides[get_directory_store] = lambda: store
    app.dependency_overrides[get_directory_sync] = lambda: _Sync()
    yield TestClient(app), store
    app.dependency_overrides.clear()


def test_sync_requires_admin_key(_client):
    client, _ = _client
    assert client.post("/admin/directory/sync").status_code == 403


def test_sync_returns_summary(_client):
    client, _ = _client
    r = client.post("/admin/directory/sync", headers={"x-admin-key": "secret"})
    assert r.status_code == 200
    assert r.json()["managers"] == 1


@pytest.mark.asyncio
async def test_list_redacts_emails(_client):
    client, store = _client
    await store.upsert(DirectoryUser(email="diane@omkar.com", slack_id="U_D",
                                     display_name="Diane", manager_email=None,
                                     groups=["Managers"], role="manager"))
    r = client.get("/admin/directory", headers={"x-admin-key": "secret"})
    assert r.status_code == 200
    [row] = r.json()
    assert row["email"] == "d***@omkar.com"
    assert row["role"] == "manager" and row["slack_id"] == "U_D"
