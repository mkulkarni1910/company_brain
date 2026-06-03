from datetime import UTC, datetime

from fastapi.testclient import TestClient

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


def test_ingest_rejects_without_admin_key(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    from app.config import get_settings

    get_settings.cache_clear()

    app.dependency_overrides[get_ingest_pipeline] = lambda: _FakeIngestPipeline()
    try:
        client = TestClient(app)
        resp = client.post("/admin/ingest", json=_payload())
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_ingest_rejects_wrong_admin_key(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    from app.config import get_settings

    get_settings.cache_clear()

    app.dependency_overrides[get_ingest_pipeline] = lambda: _FakeIngestPipeline()
    try:
        client = TestClient(app)
        resp = client.post(
            "/admin/ingest", json=_payload(), headers={"x-admin-key": "wrong"}
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_ingest_accepts_correct_admin_key(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    from app.config import get_settings

    get_settings.cache_clear()

    app.dependency_overrides[get_ingest_pipeline] = lambda: _FakeIngestPipeline()
    try:
        client = TestClient(app)
        resp = client.post(
            "/admin/ingest", json=_payload(), headers={"x-admin-key": "secret"}
        )
        assert resp.status_code == 200
        assert resp.json()["chunks_indexed"] == 1
    finally:
        app.dependency_overrides.clear()
