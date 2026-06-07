"""Admin-route guard: Entra "Admin" group for people, x-admin-key for scripts.

Interactive requests (Easy Auth / bearer / debug header) must belong to the
Entra group named by ENTRA_ADMINS_GROUP. The shared x-admin-key header remains
valid for headless automation (seed scripts, eval loader) only.
"""
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import get_ingest_pipeline
from app.ingest.pipeline import IngestResult
from app.main import app


class _FakeIngestPipeline:
    async def process(self, doc) -> IngestResult:  # noqa: ANN001
        return IngestResult(doc_id="d", chunks_indexed=1)


def _payload() -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "doc_id": "up:admin-auth-test",
        "tenant_id": "t-test",
        "source": "uploaded",
        "source_url": "local://admin-auth-test",
        "title": "Admin Auth Test",
        "body": "# Test\n\nHello world.",
        "author_id": None,
        "acl_principals": ["t-test:everyone"],
        "created_at": now,
        "modified_at": now,
        "mime": "text/markdown",
    }


def _post_ingest(headers: dict | None = None):
    app.dependency_overrides[get_ingest_pipeline] = lambda: _FakeIngestPipeline()
    try:
        client = TestClient(app)
        return client.post("/admin/ingest", json=_payload(), headers=headers or {})
    finally:
        app.dependency_overrides.clear()


def test_ingest_rejects_without_any_credentials(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    get_settings.cache_clear()
    assert _post_ingest().status_code == 401  # no key, no signed-in user


def test_ingest_rejects_wrong_admin_key(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    get_settings.cache_clear()
    resp = _post_ingest({"x-admin-key": "wrong"})
    assert resp.status_code == 403


def test_ingest_rejects_key_when_none_configured(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    get_settings.cache_clear()
    resp = _post_ingest({"x-admin-key": "anything"})
    assert resp.status_code == 403  # closed by default


def test_ingest_accepts_correct_admin_key(monkeypatch) -> None:  # noqa: ANN001
    """Headless automation path (seed scripts, eval loader)."""
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    get_settings.cache_clear()
    resp = _post_ingest({"x-admin-key": "secret"})
    assert resp.status_code == 200
    assert resp.json()["chunks_indexed"] == 1


def test_ingest_accepts_member_of_admins_group() -> None:
    # Debug header carries group names; "Admin" is the configured default.
    resp = _post_ingest({"x-debug-bypass-auth": "t-test,u-admin,Admin"})
    assert resp.status_code == 200


def test_ingest_rejects_signed_in_non_admin() -> None:
    resp = _post_ingest({"x-debug-bypass-auth": "t-test,u-mortal,t-test:everyone"})
    assert resp.status_code == 403
    assert "Entra group" in resp.json()["detail"]


def test_admins_group_name_is_configurable(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ENTRA_ADMINS_GROUP", "Platform Owners")
    get_settings.cache_clear()
    ok = _post_ingest({"x-debug-bypass-auth": "t-test,u-x,Platform Owners"})
    assert ok.status_code == 200
    no = _post_ingest({"x-debug-bypass-auth": "t-test,u-y,Admin"})
    assert no.status_code == 403
