"""Live e2e: real retrieval + real LLM decision, fake Slack.

Pre-req: scripts/seed_refund_demo.py has been run against the configured index.
Run with: .venv/bin/python -m pytest tests/test_refund_e2e_integration.py -v -m integration
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.config import get_settings
from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.workflows.engine import RefundEngine
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore


class _Directory:
    """In-memory DirectoryService stand-in: Tom (agent) reports to Diana (manager)."""

    _RECORDS = [
        DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                      manager_email="diana@x", groups=["Support Agent"], role="agent"),
        DirectoryUser(email="diana@x", slack_id="U_DIANA", display_name="Diana Foster",
                      groups=["Managers"], role="manager"),
    ]

    async def resolve(self, email):  # noqa: ANN001
        return next((r for r in self._RECORDS if r.email == (email or "").lower()), None)

    async def get_by_slack_id(self, slack_id):  # noqa: ANN001
        return next((r for r in self._RECORDS if r.slack_id == slack_id), None)

pytestmark = pytest.mark.integration


def _bot_user() -> User:
    tid = get_settings().substrateos_tenant_id
    return User(user_id="bot", tenant_id=tid, email="bot@substrateos",
                display_name="Bot", group_ids={f"{tid}:everyone"})


@pytest_asyncio.fixture
async def engine():
    from app.generation.azure_openai import AzureOpenAIClient
    from app.generation.gemini import GeminiClient
    from app.retrieval.ai_search_client import AISearchClient
    from app.retrieval.hybrid_retriever import HybridRetriever
    embedder = AzureOpenAIClient()
    search = AISearchClient()
    llm = GeminiClient()
    retriever = HybridRetriever(search=search, embedder=embedder)
    yield RefundEngine(retriever=retriever, llm=llm)
    await embedder.aclose()


@pytest.mark.asyncio
async def test_over_limit_refund_needs_approval(engine):
    d = await engine.evaluate(
        "customer Priya Sharma is asking for a refund of $1,200 on order #48213. "
        "It's been about 45 days. Can we do it?", user=_bot_user(),
    )
    assert d.found is True
    assert d.order_id == "48213"
    assert d.amount_usd == pytest.approx(1200, rel=0.01)
    assert d.auto_approve is False


@pytest.mark.asyncio
async def test_small_recent_refund_auto_approves(engine):
    d = await engine.evaluate(
        "Marcus Lee wants a refund of $89 on order #48190 from about two weeks ago.",
        user=_bot_user(),
    )
    assert d.found is True
    assert d.order_id == "48190"
    assert d.auto_approve is True


@pytest.mark.asyncio
async def test_full_flow_with_fake_slack(engine, monkeypatch):
    """Needs-approval flow end to end: request → DM card → approve click → audit."""
    calls: list[tuple[str, dict]] = []

    async def fake_slack(token, method, payload):
        calls.append((method, payload))
        if method == "users.info":
            uid = payload.get("user")
            email = {"U_TOM": "tom@x", "U_DIANA": "diana@x"}.get(uid, "")
            name = {"U_TOM": "Tom Reyes", "U_DIANA": "Diana Foster"}.get(uid, "Someone")
            return {"ok": True, "user": {"real_name": name, "profile": {"email": email}}}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "1.2", "channel": payload["channel"]}
        return {"ok": True}

    monkeypatch.setattr("app.workflows.flow.slack_call", fake_slack)
    get_settings.cache_clear()

    store = RunStore(client=None, force_memory=True)
    flow = RefundFlow(engine=engine, store=store, directory=_Directory())
    await flow.handle_request(
        text="refund of $1,200 on order #48213, about 45 days old",
        channel="C_REFUNDS", thread_ts=None, requester_slack_id="U_TOM", user=_bot_user(),
    )
    run = (await store.list_runs())[0]
    assert run.status == "pending_approval"

    await flow.handle_action({
        "type": "block_actions", "user": {"id": "U_DIANA", "name": "diana"},
        "container": {"channel_id": "D_DIANA", "message_ts": "1.2"},
        "actions": [{"action_id": "refund_approve", "value": run.id}],
    })
    final = await store.get(run.id)
    assert final.status == "completed"
    steps = [e.step for e in await store.list_events(run.id)]
    assert steps == ["Request received", "Identity checked", "Facts gathered",
                     "Rule evaluated", "Routed for approval", "Approved", "Refund issued"]
    get_settings.cache_clear()
