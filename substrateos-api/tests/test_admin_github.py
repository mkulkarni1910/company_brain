"""Admin endpoints for the GitHub tool (config + surface registration)."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.admin import router
from app.connectors.github_store import GithubStore
from app.connectors.store import _DEFAULT_SURFACES
from app.deps import get_connection_store, get_github_store

ADMIN = {"x-admin-key": "k"}


class _Surfaces:
    def __init__(self): self.saved = []
    async def list_surfaces(self, tenant): return list(_DEFAULT_SURFACES)
    async def put_surface(self, tenant, surface): self.saved.append(surface)


def _app(github: GithubStore) -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    a.dependency_overrides[get_github_store] = lambda: github
    a.dependency_overrides[get_connection_store] = lambda: _Surfaces()
    return a


@pytest.fixture(autouse=True)
def _admin_key(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "k")
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_github_in_default_surfaces():
    assert any(s.name == "github" for s in _DEFAULT_SURFACES)
    from app.connectors.cosmos_store import _DEFAULT_SURFACES as COSMOS_DEFAULTS
    assert any(s.name == "github" for s in COSMOS_DEFAULTS)


@pytest.mark.asyncio
async def test_get_config_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    github = GithubStore(client=None, force_memory=True)
    async with AsyncClient(transport=ASGITransport(app=_app(github)), base_url="http://t") as c:
        r = await c.get("/admin/github/config", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["repo_configured"] is False and body["app_configured"] is False


@pytest.mark.asyncio
async def test_put_config_roundtrip(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    github = GithubStore(client=None, force_memory=True)
    async with AsyncClient(transport=ASGITransport(app=_app(github)), base_url="http://t") as c:
        r = await c.put("/admin/github/config", headers=ADMIN,
                        json={"owner": "acme", "repo": "policies", "base_branch": "main"})
        assert r.status_code == 200
        assert r.json() == {"owner": "acme", "repo": "policies", "base_branch": "main",
                            "app_configured": True, "repo_configured": True}
        r2 = await c.put("/admin/github/config", headers=ADMIN,
                         json={"owner": "  ", "repo": "x"})
        assert r2.status_code == 400
        r3 = await c.get("/admin/github/config", headers=ADMIN)
        assert r3.json()["owner"] == "acme"  # bad PUT didn't clobber


@pytest.mark.asyncio
async def test_put_config_rejects_invalid_charset(monkeypatch):
    """owner/repo with path-traversal chars must return 400 and not clobber existing config."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "sec")
    from app.config import get_settings
    get_settings.cache_clear()
    github = GithubStore(client=None, force_memory=True)
    async with AsyncClient(transport=ASGITransport(app=_app(github)), base_url="http://t") as c:
        # Seed a good config first
        r0 = await c.put("/admin/github/config", headers=ADMIN,
                         json={"owner": "acme", "repo": "policies"})
        assert r0.status_code == 200
        # owner with a slash (path traversal attempt) must be rejected
        r1 = await c.put("/admin/github/config", headers=ADMIN,
                         json={"owner": "acme/evil", "repo": "x"})
        assert r1.status_code == 400
        # repo with a slash must also be rejected
        r2 = await c.put("/admin/github/config", headers=ADMIN,
                         json={"owner": "acme", "repo": "x/y"})
        assert r2.status_code == 400
        # Confirm the original config was not clobbered
        r3 = await c.get("/admin/github/config", headers=ADMIN)
        assert r3.json()["owner"] == "acme"
        assert r3.json()["repo"] == "policies"


@pytest.mark.asyncio
async def test_admin_key_required(monkeypatch):
    github = GithubStore(client=None, force_memory=True)
    async with AsyncClient(transport=ASGITransport(app=_app(github)), base_url="http://t") as c:
        r = await c.get("/admin/github/config")  # no header
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_patch_surface_accepts_github(monkeypatch):
    github = GithubStore(client=None, force_memory=True)
    async with AsyncClient(transport=ASGITransport(app=_app(github)), base_url="http://t") as c:
        r = await c.patch("/admin/surfaces/github", headers=ADMIN, json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
