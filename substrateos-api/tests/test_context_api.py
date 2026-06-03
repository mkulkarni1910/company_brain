from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.context import router as context_router
from app.config import get_settings
from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate, RankedResult


def _ranked():
    chunk = Chunk(
        chunk_id="c1", doc_id="d1", tenant_id="t-eval", source="sharepoint",
        source_url="https://x/d1", title="Travel Policy",
        content="Economy fares for flights under 6 hours. " * 10,
        acl_principals=["t-eval:everyone"],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc), chunk_index=0,
    )
    cand = Candidate(chunk=chunk)
    return [RankedResult(candidate=cand, final_score=0.91,
                         signal_breakdown={"content": 0.5, "people": 0.2}, rank=0)]


class FakeOrch:
    def __init__(self, ranked):
        self._ranked = ranked
        self.seen_user = None
    async def retrieve_ranked(self, request, *, user, user_token=None):
        self.seen_user = user
        return self._ranked


class FakePATStore:
    async def resolve(self, plaintext):
        if plaintext == "sbx_live_ok":
            return User(user_id="u9", tenant_id="t-eval", email="u9@x",
                        display_name="U9", group_ids=set())
        return None


@pytest.fixture
def app():
    get_settings().enable_debug_auth = True
    get_settings().pilot_single_tenant = False
    a = FastAPI()
    a.state.orchestrator = FakeOrch(_ranked())
    a.state.token_store = FakePATStore()
    a.include_router(context_router)
    return a


@pytest.mark.asyncio
async def test_context_with_pat_returns_hits(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/context", json={"query": "travel policy", "top": 5},
                          headers={"Authorization": "Bearer sbx_live_ok"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "travel policy"
    hit = body["hits"][0]
    assert hit["doc_id"] == "d1"
    assert hit["title"] == "Travel Policy"
    assert hit["source_url"] == "https://x/d1"
    assert len(hit["snippet"]) <= 240
    assert hit["score"] == pytest.approx(0.91)
    assert hit["signals"]["content"] == 0.5


@pytest.mark.asyncio
async def test_context_requires_auth(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/context", json={"query": "x"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_context_empty_on_no_results(app) -> None:
    app.state.orchestrator = FakeOrch([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/context", json={"query": "x"},
                          headers={"Authorization": "Bearer sbx_live_ok"})
    assert r.status_code == 200
    assert r.json()["hits"] == []
