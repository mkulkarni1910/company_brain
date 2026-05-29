"""Integration test: the production ingest path must write the live ACL store.

Without acl_store wired into the IngestPipeline (deps.get_ingest_pipeline),
set_doc_principals is never called in prod, so the query-time ACL store has no
data -> every recheck is a key-miss -> the "live re-check" half of
double-enforcement is inert. This test proves ingest writes the live ACL.

Real AI Search + OpenAI + Redis.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.acl.store import ACLStore
from app.config import get_settings
from app.main import app


@pytest.mark.integration
async def test_ingest_writes_live_acl() -> None:
    now = datetime.now(UTC).isoformat()
    payload = {
        "doc_id": "up:acl-wire-test",
        "tenant_id": "t-test",
        "source": "uploaded",
        "source_url": "local://acl-wire-test",
        "title": "ACL Wire Test",
        "body": "# Test\n\nHello world. This is an ACL wiring test document.",
        "author_id": None,
        "acl_principals": ["t-test:everyone", "g-acltest"],
        "created_at": now,
        "modified_at": now,
        "mime": "text/markdown",
    }
    with TestClient(app) as client:
        resp = client.post(
            "/admin/ingest",
            json=payload,
            headers={"x-admin-key": get_settings().admin_api_key or ""},
        )
    assert resp.status_code == 200

    store = ACLStore()
    try:
        principals = await store.doc_principals(tenant_id="t-test", doc_id="up:acl-wire-test")
    finally:
        await store.aclose()

    assert principals == {"t-test:everyone", "g-acltest"}
