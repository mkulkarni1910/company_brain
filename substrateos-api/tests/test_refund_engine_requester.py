"""RefundEngine with a requester: query augmented, order hits scoped, prompt
tells the LLM who is asking."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.domain.chunk import Chunk
from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.query import Candidate
from app.workflows.engine import RefundEngine

_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")

_PRIYA_ORDER = ("# Order #48213\n\n- **Customer:** Priya Sharma (priya@x)\n"
                "- **Order total:** $1,200.00\n")
_MARCUS_ORDER = ("# Order #48190\n\n- **Customer:** Marcus Lee (marcus.lee@example.com)\n"
                 "- **Order total:** $89.00\n")
_POLICY = "# Refund Policy\n\nAuto-approve refunds up to $500 within 30 days.\n"

_DECISION_JSON = json.dumps({
    "found": True, "order_id": "48213", "customer": "Priya Sharma",
    "amount_usd": 1200, "order_age_days": 45, "policy_limit_usd": 500,
    "policy_limit_days": 30, "auto_approve": False, "reasoning": "over limit",
})


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@x",
                display_name="Bot", group_ids={"t-test:everyone"})


def _cand(doc_id: str, content: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(chunk=Chunk(
        chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test",
        source="uploaded", source_url=f"local://{doc_id}", title=doc_id,
        content=content, acl_principals=["t-test:everyone"],
        created_at=now, modified_at=now, chunk_index=0,
    ))


class _Retriever:
    def __init__(self):
        self.queries: list[str] = []

    async def retrieve(self, *, query, user, k, timer=None):
        self.queries.append(query)
        if "policy" in query.lower():
            return [_cand("refund-policy", _POLICY)]
        return [_cand("order-48213", _PRIYA_ORDER), _cand("order-48190", _MARCUS_ORDER)]


class _LLM:
    def __init__(self):
        self.messages = None

    async def complete(self, *, messages, temperature, max_tokens):
        self.messages = messages
        return _DECISION_JSON


@pytest.mark.asyncio
async def test_requester_augments_query_and_prompt_and_scopes_orders():
    retriever, llm = _Retriever(), _LLM()
    engine = RefundEngine(retriever=retriever, llm=llm)
    decision = await engine.evaluate("I want a refund for my order",
                                     user=_user(), requester=_PRIYA)
    assert decision.found is True
    # order-retrieval query carries the requester's name and email
    assert "Priya Sharma" in retriever.queries[0] and "priya@x" in retriever.queries[0]
    user_msg = llm.messages[-1]["content"]
    # identity line present, own order in context, other customer's order scoped out
    assert "Requester: Priya Sharma (priya@x)" in user_msg
    assert "48213" in user_msg
    assert "Marcus Lee" not in user_msg


@pytest.mark.asyncio
async def test_no_requester_is_unchanged():
    retriever, llm = _Retriever(), _LLM()
    engine = RefundEngine(retriever=retriever, llm=llm)
    await engine.evaluate("refund order 48190", user=_user())
    assert retriever.queries[0] == "refund order 48190"
    user_msg = llm.messages[-1]["content"]
    assert "Requester:" not in user_msg
    assert "Marcus Lee" in user_msg  # staff/anonymous: nothing scoped out


@pytest.mark.asyncio
async def test_decision_carries_customer_email():
    retriever, llm = _Retriever(), _LLM()
    # the fake decision JSON gains the email, as the prompt now requests
    global _DECISION_JSON
    payload = json.loads(_DECISION_JSON)
    payload["customer_email"] = "priya@x"

    class _EmailLLM(_LLM):
        async def complete(self, *, messages, temperature, max_tokens):
            self.messages = messages
            return json.dumps(payload)

    engine = RefundEngine(retriever=retriever, llm=_EmailLLM())
    decision = await engine.evaluate("refund my order", user=_user(), requester=_PRIYA)
    assert decision.customer_email == "priya@x"


def test_decision_prompt_requests_customer_email():
    from app.workflows.engine import DECISION_PROMPT

    assert '"customer_email"' in DECISION_PROMPT
    assert "email" in DECISION_PROMPT.lower()
