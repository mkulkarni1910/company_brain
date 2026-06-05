from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate
from app.workflows.engine import RefundEngine, RefundEngineError


def _candidate(title: str, content: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(chunk=Chunk(
        chunk_id=f"c-{title}", doc_id=f"d-{title}", tenant_id="t-test", source="uploaded",
        source_url="https://example.com", title=title, content=content,
        acl_principals=["t-test:everyone"], created_at=now, modified_at=now, chunk_index=0,
    ))


class _FakeRetriever:
    def __init__(self):
        self.queries: list[str] = []

    async def retrieve(self, *, query, user, k=30, timer=None):
        self.queries.append(query)
        return [
            _candidate("Order #48213", "Order #48213 · Priya Sharma · $1,200 · placed 45 days ago"),
            _candidate("Refund Policy v3", "Auto-approve only when amount <= $500 AND age <= 30 days"),
        ]


class _FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.messages = None

    async def complete(self, *, messages, deployment=None, temperature=0.0, max_tokens=800):
        self.messages = messages
        return self.reply


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@substrateos",
                display_name="Bot", group_ids={"t-test:everyone"})


_DECISION = {
    "found": True, "order_id": "48213", "customer": "Priya Sharma",
    "amount_usd": 1200, "order_age_days": 45,
    "policy_limit_usd": 500, "policy_limit_days": 30,
    "auto_approve": False,
    "reasoning": "Amount and age exceed the auto-approve limits in refund policy v3.",
}


@pytest.mark.asyncio
async def test_evaluate_parses_decision():
    llm = _FakeLLM(json.dumps(_DECISION))
    retriever = _FakeRetriever()
    engine = RefundEngine(retriever=retriever, llm=llm)
    d = await engine.evaluate("refund $1,200 on order #48213", user=_user())
    assert d.found is True
    assert d.auto_approve is False
    assert d.order_id == "48213"
    assert d.amount_usd == 1200
    # two retrievals: the request text and the policy lookup
    assert len(retriever.queries) == 2
    assert "refund policy" in retriever.queries[1].lower()
    # context + today's date reach the LLM
    user_msg = llm.messages[-1]["content"]
    assert "Order #48213" in user_msg
    assert "Today's date" in user_msg


@pytest.mark.asyncio
async def test_evaluate_handles_json_wrapped_in_prose():
    llm = _FakeLLM("Sure! Here is the result:\n```json\n" + json.dumps(_DECISION) + "\n```")
    engine = RefundEngine(retriever=_FakeRetriever(), llm=llm)
    d = await engine.evaluate("refund order 48213", user=_user())
    assert d.found is True


@pytest.mark.asyncio
async def test_evaluate_raises_on_garbage():
    llm = _FakeLLM("I cannot help with that.")
    engine = RefundEngine(retriever=_FakeRetriever(), llm=llm)
    with pytest.raises(RefundEngineError):
        await engine.evaluate("refund order 48213", user=_user())


@pytest.mark.asyncio
async def test_evaluate_dedupes_chunks():
    class _DupRetriever(_FakeRetriever):
        async def retrieve(self, *, query, user, k=30, timer=None):
            c = _candidate("Refund Policy v3", "policy text")
            return [c, c]

    llm = _FakeLLM(json.dumps(_DECISION))
    engine = RefundEngine(retriever=_DupRetriever(), llm=llm)
    await engine.evaluate("refund", user=_user())
    assert llm.messages[-1]["content"].count("[Refund Policy v3]") == 1
