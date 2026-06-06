"""PolicyEngine — pure, deterministic evaluation of policy-as-code.

The model may produce the *facts* (extract amount, order_age_days, ...). It must
never decide the outcome. ``evaluate()`` is pure code over typed facts:

    * any referenced fact missing/None  → ``on_missing_data`` (fail closed)
    * a comparison that cannot be typed  → ``on_missing_data`` (ambiguous → stop)
    * all conditions pass                → ``on_pass``
    * any condition fails                → ``on_fail`` (+ required_role)
"""

from __future__ import annotations

import logging

from app.domain.policy import Condition, Decision, Policy

logger = logging.getLogger(__name__)

_MISSING = object()


class PolicyEngine:
    """Decide allow | require_approval | deny | stop deterministically, in code."""

    def evaluate(self, policy: Policy, facts: dict) -> Decision:
        # 1. fail closed on missing/None facts
        missing = [
            c.fact
            for c in policy.all
            if facts.get(c.fact, _MISSING) is _MISSING or facts.get(c.fact) is None
        ]
        if missing:
            return self._decision(
                policy,
                policy.on_missing_data,
                f"Missing required fact(s): {', '.join(sorted(set(missing)))}",
                facts,
            )

        # 2. evaluate each condition in pure code; an un-typed comparison is ambiguous → stop
        failed: list[Condition] = []
        for cond in policy.all:
            ok = self._check(cond, facts[cond.fact])
            if ok is None:  # ambiguous / un-comparable types
                return self._decision(
                    policy,
                    policy.on_missing_data,
                    f"Ambiguous fact {cond.fact!r}: cannot compare {facts[cond.fact]!r} "
                    f"with {cond.op} {cond.value!r}",
                    facts,
                )
            if not ok:
                failed.append(cond)

        if failed:
            reason = "; ".join(
                f"{c.fact} {c.op} {c.value!r} failed (got {facts[c.fact]!r})" for c in failed
            )
            return self._decision(policy, policy.on_fail, reason, facts)

        return self._decision(policy, policy.on_pass, "all conditions passed", facts)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _decision(self, policy: Policy, result, reason: str, facts: dict) -> Decision:
        return Decision(
            result=result,
            reason=reason,
            rule_id=policy.id,
            rule_version=policy.version,
            # a required_role is only meaningful when the outcome routes to a human
            required_role=policy.required_role if result == "require_approval" else None,
            evidence=dict(facts),
        )

    @staticmethod
    def _check(cond: Condition, actual) -> bool | None:
        """Return True/False, or None when the comparison is type-ambiguous."""
        op, expected = cond.op, cond.value
        try:
            if op == "<=":
                return actual <= expected
            if op == ">=":
                return actual >= expected
            if op == "<":
                return actual < expected
            if op == ">":
                return actual > expected
            if op == "==":
                return actual == expected
            if op == "!=":
                return actual != expected
            if op == "in":
                return actual in expected  # expected is a container
        except TypeError:
            return None
        return None
