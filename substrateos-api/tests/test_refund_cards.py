from __future__ import annotations

import json

from app.bots.refund_cards import (
    approval_dm_blocks,
    auto_approved_blocks,
    decided_dm_blocks,
    needs_approval_blocks,
    outcome_blocks,
)
from app.domain.workflow import RefundDecision

_D = RefundDecision(
    found=True, order_id="48213", customer="Priya Sharma", amount_usd=1200,
    order_age_days=45, policy_limit_usd=500, policy_limit_days=30,
    auto_approve=False, reasoning="Over the auto-approve limit of $500 / 30 days.",
)


def _text(card: dict) -> str:
    return json.dumps(card)


def _all_blocks(card: dict) -> list[dict]:
    """Flatten top-level blocks + every attachment's blocks."""
    out = list(card.get("blocks", []))
    for att in card.get("attachments", []):
        out.extend(att.get("blocks", []))
    return out


def test_needs_approval_blocks_mentions_reason_and_approver():
    card = needs_approval_blocks(_D, approver_label="Diana Foster", run_id="RB-4471")
    s = _text(card)
    assert "Needs approval" in s
    assert "Diana Foster" in s
    assert "RB-4471" in s
    assert "auto-approve limit" in s  # the WHY box carries the reasoning
    # the colored left-bar comes from an amber attachment
    assert any(a.get("color") for a in card["attachments"])


def test_approval_dm_blocks_have_buttons_with_run_id():
    card = approval_dm_blocks(_D, requester_name="Tom Reyes", run_id="RB-4471")
    actions = [b for b in _all_blocks(card) if b.get("type") == "actions"]
    assert len(actions) == 1
    ids = {e["action_id"]: e["value"] for e in actions[0]["elements"]}
    assert ids == {"refund_approve": "RB-4471", "refund_reject": "RB-4471"}
    s = _text(card)
    assert "Tom Reyes" in s and "Priya Sharma" in s and "$1,200" in s  # facts live on the DM card


def test_decided_dm_blocks_no_buttons():
    card = decided_dm_blocks(_D, approved=True, approver_name="Diana Foster")
    assert "Approved by Diana Foster" in _text(card)
    assert not [b for b in _all_blocks(card) if b.get("type") == "actions"]


def test_outcome_blocks_approved_and_rejected():
    ok = _text(outcome_blocks(_D, approved=True, approver_name="Diana Foster"))
    assert "Approved" in ok and "issued" in ok
    no = _text(outcome_blocks(_D, approved=False, approver_name="Diana Foster"))
    assert "Rejected" in no


def test_auto_approved_blocks():
    d = _D.model_copy(update={"auto_approve": True, "amount_usd": 89.0, "order_id": "48190"})
    s = _text(auto_approved_blocks(d, run_id="RB-4472"))
    assert "Auto-approved" in s and "$89" in s


def test_customer_request_blocks():
    from app.bots.refund_cards import customer_request_blocks

    card = customer_request_blocks(
        request_text="I want a refund for order 48213",
        customer_name="Priya Sharma", run_id="RB-4480",
    )
    assert "RB-4480" in card["blocks"][0]["text"]["text"]
    body = str(card["attachments"])
    assert "Priya Sharma" in body and "order 48213" in body
    assert card["attachments"][0]["color"] == "#c8860d"  # amber: waiting on a human


def test_customer_request_blocks_with_decision_facts():
    from app.bots.refund_cards import customer_request_blocks
    from app.domain.workflow import RefundDecision

    d = RefundDecision(found=True, order_id="48213", customer="Priya Sharma",
                       amount_usd=1200, order_age_days=45, policy_limit_usd=500,
                       policy_limit_days=30, auto_approve=False,
                       reasoning="Over the limit.")
    card = customer_request_blocks(
        request_text="I want a refund", customer_name="Priya Sharma",
        run_id="RB-1", decision=d,
    )
    body = str(card["attachments"])
    assert "#48213" in body and "$1,200" in body and "45 days" in body
    assert "over the auto-approve limit" in body

    bare = customer_request_blocks(
        request_text="I want a refund", customer_name="Priya Sharma", run_id="RB-1",
    )
    assert "#48213" not in str(bare["attachments"])


def _decision_for_cards():
    from app.domain.workflow import RefundDecision
    return RefundDecision(found=True, order_id="48213", customer="Priya Sharma",
                          amount_usd=1200, order_age_days=45, policy_limit_usd=500,
                          policy_limit_days=30, auto_approve=False, reasoning="over limit")


def test_outcome_blocks_mentions_agent():
    from app.bots.refund_cards import outcome_blocks

    card = outcome_blocks(_decision_for_cards(), approved=True,
                          approver_name="Diana Foster", mention="<@U_TOM>")
    body = str(card["attachments"])
    assert "Hello <@U_TOM>" in body and "Diana Foster" in body

    plain = outcome_blocks(_decision_for_cards(), approved=False,
                           approver_name="Diana Foster")
    assert "Hello" not in str(plain["attachments"])  # no mention → old header


def test_customer_outcome_blocks_approved_and_rejected():
    from app.bots.refund_cards import customer_outcome_blocks

    ok = customer_outcome_blocks(_decision_for_cards(), approved=True)
    body = str(ok["attachments"])
    assert "Hello Priya" in body and "$1,200" in body and "#48213" in body
    assert "approved" in body and ok["attachments"][0]["color"] == "#2f8f5b"

    no = customer_outcome_blocks(_decision_for_cards(), approved=False)
    body = str(no["attachments"]).lower()
    assert "hello priya" in body and "refund policy" in body and "$500" in body
    # customer copy never leaks internal mechanics
    for banned in ("exception", "manager", "approv"):
        assert banned not in body
    assert no["attachments"][0]["color"] == "#c8546a"
