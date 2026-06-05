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


def _text(blocks: list[dict]) -> str:
    return json.dumps(blocks)


def test_needs_approval_blocks_mentions_facts_and_approver():
    blocks = needs_approval_blocks(_D, approver_label="Diana Foster", run_id="RB-4471")
    s = _text(blocks)
    assert "Needs approval" in s
    assert "$1,200" in s
    assert "Diana Foster" in s
    assert "RB-4471" in s


def test_approval_dm_blocks_have_buttons_with_run_id():
    blocks = approval_dm_blocks(_D, requester_name="Tom Reyes", run_id="RB-4471")
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions) == 1
    ids = {e["action_id"]: e["value"] for e in actions[0]["elements"]}
    assert ids == {"refund_approve": "RB-4471", "refund_reject": "RB-4471"}
    assert "Tom Reyes" in _text(blocks)


def test_decided_dm_blocks_no_buttons():
    blocks = decided_dm_blocks(_D, approved=True, approver_name="Diana Foster")
    s = _text(blocks)
    assert "Approved by Diana Foster" in s
    assert not [b for b in blocks if b.get("type") == "actions"]


def test_outcome_blocks_approved_and_rejected():
    ok = _text(outcome_blocks(_D, approved=True, approver_name="Diana Foster"))
    assert "Approved" in ok and "issued" in ok
    no = _text(outcome_blocks(_D, approved=False, approver_name="Diana Foster"))
    assert "Rejected" in no


def test_auto_approved_blocks():
    d = _D.model_copy(update={"auto_approve": True, "amount_usd": 89.0, "order_id": "48190"})
    s = _text(auto_approved_blocks(d, run_id="RB-4472"))
    assert "Auto-approved" in s and "$89" in s
