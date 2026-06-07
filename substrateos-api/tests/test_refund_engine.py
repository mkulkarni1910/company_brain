from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.domain.chunk import Chunk
from app.domain.directory import DirectoryUser
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
    "customer_email": "dana@acme.test",
    "amount_usd": 1200, "order_age_days": 45,
    "reasoning": "Order #48213 is $1,200 placed 45 days ago by Priya Sharma.",
}


@pytest.mark.asyncio
async def test_evaluate_extracts_facts_only():
    # The model returns facts only; even if it leaks a verdict, the engine ignores it.
    llm = _FakeLLM(json.dumps({**_DECISION, "auto_approve": True, "policy_limit_usd": 500}))
    retriever = _FakeRetriever()
    engine = RefundEngine(retriever=retriever, llm=llm)
    facts = await engine.evaluate("refund $1,200 on order #48213", user=_user())
    assert facts.found is True
    assert facts.order_id == "48213"
    assert facts.amount_usd == 1200
    assert facts.order_age_days == 45
    assert facts.customer_email == "dana@acme.test"
    # the verdict is NOT the model's job — RefundFacts has no auto_approve field
    assert not hasattr(facts, "auto_approve")
    # one retrieval (order context); the policy is code now, not a retrieval
    assert len(retriever.queries) == 1
    # context + today's date reach the LLM
    user_msg = llm.messages[-1]["content"]
    assert "Order #48213" in user_msg
    assert "Today's date" in user_msg
    # the prompt instructs facts-only extraction
    assert "do not decide" in llm.messages[0]["content"].lower()


@pytest.mark.asyncio
async def test_evaluate_with_requester_adds_identity_line():
    llm = _FakeLLM(json.dumps(_DECISION))
    engine = RefundEngine(retriever=_FakeRetriever(), llm=llm)
    record = DirectoryUser(email="dana@acme.test", role="customer")
    await engine.evaluate("refund my order", user=_user(), requester=record)
    user_msg = llm.messages[-1]["content"]
    assert "role customer" in user_msg
    assert "dana@acme.test" in user_msg


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
