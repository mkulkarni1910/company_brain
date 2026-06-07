"""/studio + /admin/skill-submissions: SME-gated drafting/submission, admin decisions."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.deps import get_run_store, get_skill_drafter, get_skill_publish_flow
from app.domain.skill import Skill, SkillCreate
from app.main import app
from app.skills.drafter import SkillDraftError
from app.workflows.skill_publish import SkillPublishFlow
from app.workflows.store import RunStore

_SME = {"x-debug-bypass-auth": "t-test,u-deepa,t-test:everyone,Finance SME"}
_SME2 = {"x-debug-bypass-auth": "t-test,u-raj,t-test:everyone,Finance SME"}
_ADMIN = {"x-debug-bypass-auth": "t-test,u-diana,t-test:everyone,Admin"}
_PLAIN = {"x-debug-bypass-auth": "t-test,u-bob,t-test:everyone"}

_DRAFT = dict(slug="refund-approvals", name="Refund approvals",
              description="Auto-approve small refunds.", team="Finance",
              run_scope="org", enabled=True, steps=["Check", "Stop", "Record"],
              data_feeds=["Orders"], system_prompt="Enforce the refund policy.")


class _FakeSkillStore:
    def __init__(self):
        self._by_slug: dict[str, Skill] = {}

    async def get_by_slug(self, slug, *, enabled_only=False):
        return self._by_slug.get(slug)

    async def create(self, data: SkillCreate) -> Skill:
        if data.slug in self._by_slug:
            raise ValueError(f"slug '{data.slug}' already exists")
        now = datetime.now(UTC)
        skill = Skill(id=f"id-{data.slug}", created_at=now, updated_at=now,
                      **data.model_dump())
        self._by_slug[data.slug] = skill
        return skill


class _FakeDrafter:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    async def draft(self, text: str) -> SkillCreate:
        if self._error:
            raise self._error
        return self._result


@pytest.fixture()
def harness():
    run_store = RunStore(force_memory=True)
    skills = _FakeSkillStore()
    flow = SkillPublishFlow(store=run_store, skill_store=skills, people=None)
    app.dependency_overrides[get_run_store] = lambda: run_store
    app.dependency_overrides[get_skill_publish_flow] = lambda: flow
    app.dependency_overrides[get_skill_drafter] = lambda: _FakeDrafter(SkillCreate(**_DRAFT))
    yield run_store, skills
    app.dependency_overrides.clear()


def test_studio_requires_sme_group(harness) -> None:
    with TestClient(app) as client:
        assert client.post("/studio/draft", json={"text": "x"}, headers=_PLAIN).status_code == 403
        assert client.get("/studio/submissions", headers=_PLAIN).status_code == 403


def test_admin_passes_the_sme_gate(harness) -> None:
    with TestClient(app) as client:
        assert client.get("/studio/submissions", headers=_ADMIN).status_code == 200


def test_draft_returns_populated_skill(harness) -> None:
    with TestClient(app) as client:
        r = client.post("/studio/draft", json={"text": "refunds under $500…"}, headers=_SME)
    assert r.status_code == 200
    assert r.json()["slug"] == "refund-approvals"


def test_draft_failure_maps_to_502(harness) -> None:
    app.dependency_overrides[get_skill_drafter] = (
        lambda: _FakeDrafter(error=SkillDraftError("no json")))
    with TestClient(app) as client:
        r = client.post("/studio/draft", json={"text": "x"}, headers=_SME)
    assert r.status_code == 502


def test_submit_then_own_submissions_only(harness) -> None:
    with TestClient(app) as client:
        r = client.post("/studio/submit",
                        json={"skill": _DRAFT, "source_text": "refunds…"}, headers=_SME)
        assert r.status_code == 201
        run_id = r.json()["run_id"]
        mine = client.get("/studio/submissions", headers=_SME).json()
        assert [s["run_id"] for s in mine] == [run_id]
        assert mine[0]["status"] == "pending_approval"
        assert mine[0]["skill"]["slug"] == "refund-approvals"  # SME gets the draft back (edit)
        assert mine[0]["source_text"] == "refunds…"
        assert client.get("/studio/submissions", headers=_SME2).json() == []


def test_duplicate_pending_slug_is_409(harness) -> None:
    with TestClient(app) as client:
        assert client.post("/studio/submit", json={"skill": _DRAFT},
                           headers=_SME).status_code == 201
        assert client.post("/studio/submit", json={"skill": _DRAFT},
                           headers=_SME).status_code == 409


def test_admin_queue_approve_creates_live_skill(harness) -> None:
    _, skills = harness
    with TestClient(app) as client:
        run_id = client.post("/studio/submit", json={"skill": _DRAFT},
                             headers=_SME).json()["run_id"]
        assert client.get("/admin/skill-submissions", headers=_SME).status_code == 403
        queue = client.get("/admin/skill-submissions", headers=_ADMIN).json()
        assert queue[0]["run_id"] == run_id and queue[0]["skill"]["slug"] == "refund-approvals"
        r = client.post(f"/admin/skill-submissions/{run_id}/approve", headers=_ADMIN)
        assert r.status_code == 200 and r.json()["status"] == "approved"
        assert skills._by_slug["refund-approvals"].enabled is True
        assert client.post(f"/admin/skill-submissions/{run_id}/reject",
                           json={"note": "late"}, headers=_ADMIN).status_code == 409


def test_admin_reject_records_note_visible_to_sme(harness) -> None:
    with TestClient(app) as client:
        run_id = client.post("/studio/submit", json={"skill": _DRAFT},
                             headers=_SME).json()["run_id"]
        r = client.post(f"/admin/skill-submissions/{run_id}/reject",
                        json={"note": "Limit is $250."}, headers=_ADMIN)
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        mine = client.get("/studio/submissions", headers=_SME).json()
        assert mine[0]["rejection_note"] == "Limit is $250."


def test_unknown_run_is_404(harness) -> None:
    with TestClient(app) as client:
        assert client.post("/admin/skill-submissions/RB-0/approve",
                           headers=_ADMIN).status_code == 404


def test_resubmit_resets_status_and_clears_note(harness) -> None:
    with TestClient(app) as client:
        run_id = client.post("/studio/submit", json={"skill": _DRAFT},
                             headers=_SME).json()["run_id"]
        client.post(f"/admin/skill-submissions/{run_id}/reject",
                    json={"note": "too high"}, headers=_ADMIN)
        edited = {**_DRAFT, "name": "Refund approvals v2"}
        r = client.patch(f"/studio/submissions/{run_id}",
                         json={"skill": edited, "source_text": "new text"}, headers=_SME)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending_approval"
        assert body["rejection_note"] is None
        assert body["name"] == "Refund approvals v2"


def test_resubmit_by_other_sme_is_403(harness) -> None:
    with TestClient(app) as client:
        run_id = client.post("/studio/submit", json={"skill": _DRAFT},
                             headers=_SME).json()["run_id"]
        r = client.patch(f"/studio/submissions/{run_id}",
                         json={"skill": _DRAFT}, headers=_SME2)
        assert r.status_code == 403


def test_resubmit_of_approved_run_is_409(harness) -> None:
    with TestClient(app) as client:
        run_id = client.post("/studio/submit", json={"skill": _DRAFT},
                             headers=_SME).json()["run_id"]
        client.post(f"/admin/skill-submissions/{run_id}/approve", headers=_ADMIN)
        r = client.patch(f"/studio/submissions/{run_id}",
                         json={"skill": _DRAFT}, headers=_SME)
        assert r.status_code == 409


def test_withdraw_hides_from_list_and_blocks_decisions(harness) -> None:
    with TestClient(app) as client:
        run_id = client.post("/studio/submit", json={"skill": _DRAFT},
                             headers=_SME).json()["run_id"]
        assert client.delete(f"/studio/submissions/{run_id}", headers=_SME).status_code == 204
        assert client.get("/studio/submissions", headers=_SME).json() == []
        assert client.post(f"/admin/skill-submissions/{run_id}/approve",
                           headers=_ADMIN).status_code == 409
