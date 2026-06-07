"""SkillPublishFlow: submit → manager routing → decide. Runs use RunStore(force_memory=True)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.skill import SkillCreate
from app.domain.workflow import RefundRun


def _draft(**over) -> SkillCreate:
    base = dict(slug="refund-approvals", name="Refund approvals",
                description="Auto-approve small refunds, route big ones.",
                team="Finance", run_scope="org", enabled=True,
                steps=["Check amount", "Stop if over limit", "Record"],
                data_feeds=["Orders"], system_prompt="You enforce the refund policy.")
    base.update(over)
    return SkillCreate(**base)


def test_run_round_trips_skill_draft() -> None:
    now = datetime.now(UTC)
    run = RefundRun(id="RB-1", kind="skill_publish", status="pending_approval",
                    requester_name="Deepa Rao", requester_email="deepa@example.com",
                    skill_draft=_draft(), rejection_note=None,
                    created_at=now, updated_at=now)
    parsed = RefundRun.model_validate_json(run.model_dump_json())
    assert parsed.kind == "skill_publish"
    assert parsed.skill_draft is not None and parsed.skill_draft.slug == "refund-approvals"
