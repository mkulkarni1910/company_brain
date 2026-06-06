"""Workstream 1 — Guardrail engine tests.

These prove governance is enforced in code, not a prompt:
  * within limits           → allow
  * over a threshold        → require_approval (+ required_role)
  * missing / None fact     → stop (FAIL CLOSED)
  * flipping the YAML rule  → flips the outcome with no code change
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.policy import Condition, Policy
from app.policy.engine import PolicyEngine
from app.policy.store import PolicyNotFound, PolicyStore

POLICY_DIR = Path(__file__).resolve().parents[1] / "policies"


def _refund_policy(amount_limit: int = 500, age_limit: int = 30) -> Policy:
    return Policy(
        id="refund.v1",
        version=1,
        owner="support_manager",
        description="test",
        all=[
            Condition(fact="amount_usd", op="<=", value=amount_limit),
            Condition(fact="order_age_days", op="<=", value=age_limit),
        ],
        on_pass="allow",
        on_fail="require_approval",
        required_role="support_manager",
        on_missing_data="stop",
    )


def test_within_limits_allows():
    d = PolicyEngine().evaluate(_refund_policy(), {"amount_usd": 300, "order_age_days": 20})
    assert d.result == "allow"
    assert d.rule_id == "refund.v1"
    assert d.rule_version == 1
    assert d.required_role is None  # not needed on allow
    assert d.evidence == {"amount_usd": 300, "order_age_days": 20}


def test_over_amount_requires_approval():
    d = PolicyEngine().evaluate(_refund_policy(), {"amount_usd": 600, "order_age_days": 20})
    assert d.result == "require_approval"
    assert d.required_role == "support_manager"
    assert "amount_usd" in d.reason


def test_over_age_requires_approval():
    d = PolicyEngine().evaluate(_refund_policy(), {"amount_usd": 100, "order_age_days": 45})
    assert d.result == "require_approval"
    assert "order_age_days" in d.reason


def test_missing_fact_fails_closed():
    d = PolicyEngine().evaluate(_refund_policy(), {"amount_usd": 100})  # no order_age_days
    assert d.result == "stop"
    assert "order_age_days" in d.reason


def test_none_fact_fails_closed():
    d = PolicyEngine().evaluate(
        _refund_policy(), {"amount_usd": 100, "order_age_days": None}
    )
    assert d.result == "stop"


def test_untyped_comparison_is_ambiguous_and_stops():
    d = PolicyEngine().evaluate(
        _refund_policy(), {"amount_usd": "not-a-number", "order_age_days": 10}
    )
    assert d.result == "stop"  # ambiguous → fail closed, never guess


def test_flipping_threshold_flips_outcome_no_code_change():
    facts = {"amount_usd": 400, "order_age_days": 10}
    assert PolicyEngine().evaluate(_refund_policy(amount_limit=500), facts).result == "allow"
    # same engine, same facts, only the rule value changed → different outcome
    assert (
        PolicyEngine().evaluate(_refund_policy(amount_limit=300), facts).result
        == "require_approval"
    )


def test_store_loads_refund_policy_and_evaluates():
    policy = PolicyStore(POLICY_DIR).load("refund.v1")
    assert policy.id == "refund.v1"
    assert policy.version == 1
    eng = PolicyEngine()
    assert eng.evaluate(policy, {"amount_usd": 500, "order_age_days": 30}).result == "allow"
    assert (
        eng.evaluate(policy, {"amount_usd": 501, "order_age_days": 30}).result
        == "require_approval"
    )


def test_store_missing_policy_raises():
    with pytest.raises(PolicyNotFound):
        PolicyStore(POLICY_DIR).load("does-not-exist")


def test_numeric_op_rejects_nonnumeric_value_at_load():
    # a mis-authored policy must fail loudly when validated, not 500 at request time
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Condition(fact="amount_usd", op="<=", value="500")  # string, not numeric


def test_in_op_requires_a_container_value():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Condition(fact="channel", op="in", value="sms")  # must be a list
    # a proper list is accepted
    assert Condition(fact="channel", op="in", value=["sms", "email"]).op == "in"


def test_store_hot_reloads_on_file_change_no_restart(tmp_path):
    # "flip 500 -> 300 in the file, no code change" must take effect without a restart.
    import os

    body = (
        "id: refund.v1\nversion: 1\nowner: x\n"
        "all:\n  - {{fact: amount_usd, op: '<=', value: {v} }}\n"
        "on_pass: allow\non_fail: require_approval\non_missing_data: stop\n"
    )
    p = tmp_path / "refund.v1.yaml"
    p.write_text(body.format(v=500))
    os.utime(p, (1000, 1000))
    store = PolicyStore(tmp_path)
    assert store.load("refund.v1").all[0].value == 500

    p.write_text(body.format(v=300))
    os.utime(p, (2000, 2000))  # new mtime → cache invalidated, next load reflects the edit
    assert store.load("refund.v1").all[0].value == 300
