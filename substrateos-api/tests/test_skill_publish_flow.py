"""SkillPublishFlow: submit → manager routing → decide. Runs use RunStore(force_memory=True)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.identity import User
from app.domain.skill import Skill, SkillCreate
from app.domain.workflow import RefundRun
from app.workflows.skill_publish import (
    AlreadyDecidedError,
    NotEditableError,
    SkillPublishFlow,
    SlugConflictError,
)
from app.workflows.store import RunStore


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


def _user(email: str = "deepa@example.com") -> User:
    return User(user_id="u-deepa", tenant_id="t-test", email=email,
                display_name="Deepa Rao", group_ids={"Finance SME"})


class _FakeSkillStore:
    def __init__(self, skills: list[Skill] | None = None):
        self._skills = {s.id: s for s in (skills or [])}
        self.created: list = []

    async def get_by_slug(self, slug, *, enabled_only=False):
        return next((s for s in self._skills.values() if s.slug == slug), None)

    async def create(self, data):
        if await self.get_by_slug(data.slug):
            raise ValueError(f"slug '{data.slug}' already exists")
        now = datetime.now(UTC)
        skill = Skill(id=f"id-{data.slug}", created_at=now, updated_at=now,
                      **data.model_dump())
        self._skills[skill.id] = skill
        self.created.append(skill)
        return skill


def _flow(skills=None) -> tuple[SkillPublishFlow, RunStore, _FakeSkillStore]:
    store = RunStore(force_memory=True)
    skill_store = _FakeSkillStore(skills)
    return SkillPublishFlow(store=store, skill_store=skill_store, people=None), store, skill_store


@pytest.mark.asyncio
async def test_submit_creates_pending_run_without_touching_skill_store() -> None:
    flow, store, skills = _flow()
    run = await flow.submit(draft=_draft(), source_text="refunds under $500…", user=_user())
    assert run.kind == "skill_publish" and run.status == "pending_approval"
    assert run.requester_email == "deepa@example.com"
    assert run.skill_draft.slug == "refund-approvals"
    assert skills.created == []  # nothing live yet
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Skill submitted" in steps
    assert "Approver not resolved" in steps  # people=None → no manager, still succeeds


@pytest.mark.asyncio
async def test_submit_rejects_slug_already_in_catalog() -> None:
    now = datetime.now(UTC)
    live = Skill(id="s1", created_at=now, updated_at=now, **_draft().model_dump())
    flow, _, _ = _flow([live])
    with pytest.raises(SlugConflictError):
        await flow.submit(draft=_draft(), source_text="x", user=_user())


@pytest.mark.asyncio
async def test_submit_rejects_slug_already_pending() -> None:
    flow, _, _ = _flow()
    await flow.submit(draft=_draft(), source_text="x", user=_user())
    with pytest.raises(SlugConflictError):
        await flow.submit(draft=_draft(), source_text="x", user=_user())


@pytest.mark.asyncio
async def test_approve_creates_live_skill_and_records() -> None:
    flow, store, skills = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    decided = await flow.decide(run_id=run.id, approve=True, actor_name="Diana")
    assert decided.status == "approved"
    assert [s.slug for s in skills.created] == ["refund-approvals"]
    assert any(e.step == "Approved" for e in await store.list_events(run.id))


@pytest.mark.asyncio
async def test_reject_records_note_and_keeps_catalog_clean() -> None:
    flow, store, skills = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    decided = await flow.decide(run_id=run.id, approve=False, actor_name="Diana",
                                note="Limit should be $250, not $500.")
    assert decided.status == "rejected"
    assert decided.rejection_note == "Limit should be $250, not $500."
    assert skills.created == []


@pytest.mark.asyncio
async def test_second_decision_raises_already_decided() -> None:
    flow, _, _ = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    await flow.decide(run_id=run.id, approve=True, actor_name="Diana")
    with pytest.raises(AlreadyDecidedError):
        await flow.decide(run_id=run.id, approve=False, actor_name="Tom")


@pytest.mark.asyncio
async def test_unknown_run_raises_keyerror() -> None:
    flow, _, _ = _flow()
    with pytest.raises(KeyError):
        await flow.decide(run_id="RB-9999", approve=True, actor_name="Diana")


@pytest.mark.asyncio
async def test_submit_routes_manager_card_when_resolvable(monkeypatch) -> None:
    """Spec: 'submit creates run + events + card attempted' — the resolved path."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()

    class _People:
        async def manager_of(self, *, email, tenant_id):
            assert email == "deepa@example.com"
            return {"user_id": "u-diana", "email": "diana@example.com",
                    "display_name": "Diana Prince"}

    slack_calls: list[tuple[str, dict]] = []

    async def fake_slack_get(token, method, params):
        slack_calls.append((method, params))
        return {"ok": True, "user": {"id": "U_DIANA"}}

    async def fake_slack_call(token, method, payload):
        slack_calls.append((method, payload))
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "171.001"}
        return {"ok": True}

    monkeypatch.setattr("app.workflows.skill_publish.slack_get", fake_slack_get)
    monkeypatch.setattr("app.workflows.skill_publish.slack_call", fake_slack_call)

    store = RunStore(force_memory=True)
    flow = SkillPublishFlow(store=store, skill_store=_FakeSkillStore(), people=_People())
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())

    assert run.approver_name == "Diana Prince"
    assert run.approver_slack_id == "U_DIANA"
    assert run.dm_channel == "D_DIANA" and run.dm_ts == "171.001"
    assert ("users.lookupByEmail", {"email": "diana@example.com"}) in slack_calls
    posted = next(p for m, p in slack_calls if m == "chat.postMessage")
    assert posted["channel"] == "D_DIANA"
    assert any(e.step == "Routed for approval" for e in await store.list_events(run.id))


@pytest.mark.asyncio
async def test_resubmit_replaces_draft_and_resets_status() -> None:
    flow, store, _ = _flow()
    run = await flow.submit(draft=_draft(), source_text="old text", user=_user())
    await flow.decide(run_id=run.id, approve=False, actor_name="Diana", note="too high")
    edited = _draft(name="Refund approvals v2", system_prompt="Limit is $250.")
    out = await flow.resubmit(run_id=run.id, draft=edited, source_text="new text", user=_user())
    assert out.status == "pending_approval"
    assert out.rejection_note is None
    assert out.skill_draft.name == "Refund approvals v2"
    assert out.request_text == "new text"
    assert any(e.step == "Resubmitted" for e in await store.list_events(run.id))


@pytest.mark.asyncio
async def test_resubmit_by_other_user_is_forbidden() -> None:
    flow, _, _ = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    with pytest.raises(PermissionError):
        await flow.resubmit(run_id=run.id, draft=_draft(), source_text="x",
                            user=_user(email="raj@example.com"))


@pytest.mark.asyncio
async def test_resubmit_of_approved_run_is_not_editable() -> None:
    flow, _, _ = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    await flow.decide(run_id=run.id, approve=True, actor_name="Diana")
    with pytest.raises(NotEditableError):
        await flow.resubmit(run_id=run.id, draft=_draft(), source_text="x", user=_user())


@pytest.mark.asyncio
async def test_withdraw_cancels_and_keeps_audit() -> None:
    flow, store, skills = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    out = await flow.withdraw(run_id=run.id, user=_user())
    assert out.status == "cancelled"
    assert skills.created == []
    assert any(e.step == "Withdrawn" for e in await store.list_events(run.id))
    with pytest.raises(AlreadyDecidedError):  # cancelled runs can't be decided
        await flow.decide(run_id=run.id, approve=True, actor_name="Diana")


@pytest.mark.asyncio
async def test_losing_click_repaints_card_with_actual_outcome(monkeypatch) -> None:
    """The AlreadyDecided repaint must reflect the REAL status, not the stale local."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    updates: list[dict] = []

    async def fake_slack_call(token, method, payload):
        if method == "chat.update":
            updates.append(payload)
        return {"ok": True}

    monkeypatch.setattr("app.workflows.skill_publish.slack_call", fake_slack_call)
    flow, store, _ = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    run.dm_channel, run.dm_ts = "D1", "1.0"  # pretend a card was posted
    run.approver_slack_id = "U_DIANA"
    await store.save(run)
    await flow.decide(run_id=run.id, approve=True, actor_name="Diana")
    # losing click arrives after the decision
    await flow.handle_action({"user": {"id": "U_DIANA", "name": "diana"},
                              "actions": [{"action_id": "skillpub_reject", "value": run.id}]})
    assert updates, "card should have been repainted"
    assert "Skill approved" in str(updates[-1]) or "approved" in updates[-1].get("text", "")


@pytest.mark.asyncio
async def test_resubmit_invalidates_old_card_and_reroutes(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    calls: list[tuple[str, dict]] = []

    async def fake_slack_get(token, method, params):
        calls.append((method, params))
        return {"ok": True, "user": {"id": "U_DIANA"}}

    async def fake_slack_call(token, method, payload):
        calls.append((method, payload))
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D_DIANA"}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "171.002"}
        return {"ok": True}

    monkeypatch.setattr("app.workflows.skill_publish.slack_get", fake_slack_get)
    monkeypatch.setattr("app.workflows.skill_publish.slack_call", fake_slack_call)

    class _People:
        async def manager_of(self, *, email, tenant_id):
            return {"user_id": "u-diana", "email": "diana@example.com",
                    "display_name": "Diana Prince"}

    store = RunStore(force_memory=True)
    flow = SkillPublishFlow(store=store, skill_store=_FakeSkillStore(), people=_People())
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    old_ts = run.dm_ts
    calls.clear()
    out = await flow.resubmit(run_id=run.id, draft=_draft(name="v2"),
                              source_text="y", user=_user())
    # old card invalidated…
    invalidations = [p for m, p in calls if m == "chat.update"]
    assert invalidations and invalidations[0]["ts"] == old_ts
    assert "No decision needed" in str(invalidations[0])
    # …and a fresh card posted
    assert any(m == "chat.postMessage" for m, _ in calls)
    assert out.dm_ts == "171.002"


@pytest.mark.asyncio
async def test_withdraw_of_cancelled_run_not_editable() -> None:
    flow, _, _ = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    await flow.withdraw(run_id=run.id, user=_user())
    with pytest.raises(NotEditableError):
        await flow.withdraw(run_id=run.id, user=_user())


@pytest.mark.asyncio
async def test_approve_after_slug_landed_in_catalog_raises_valueerror() -> None:
    flow, _, skills = _flow()
    run = await flow.submit(draft=_draft(), source_text="x", user=_user())
    # the slug lands in the catalog while the submission is pending
    await skills.create(_draft())
    with pytest.raises(ValueError):
        await flow.decide(run_id=run.id, approve=True, actor_name="Diana")
