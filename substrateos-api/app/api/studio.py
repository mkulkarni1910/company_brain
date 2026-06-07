"""SME Skill Studio: plain-English drafting, submission, and admin decisions.

/studio/*                      — require_sme (Entra ENTRA_SME_GROUP; admins pass)
/admin/skill-submissions/*     — require_admin

A submission is a skill_publish run; the live SkillStore is only written by an
approval (see app/workflows/skill_publish.py and the 2026-06-07 design spec).
SMEs see and manage only their own submissions; withdrawn runs are cancelled,
never deleted, so the audit trail in /admin/runs stays whole.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel

from app.api._admin_guard import require_admin
from app.api._auth_resolve import resolve_user
from app.api._sme_guard import require_sme
from app.deps import get_run_store, get_skill_drafter, get_skill_publish_flow
from app.domain.identity import User
from app.domain.skill import SkillCreate
from app.domain.workflow import RefundRun
from app.skills.drafter import SkillDraftError
from app.skills.store import SkillStorePersistenceError
from app.workflows.skill_publish import (
    AlreadyDecidedError,
    NotEditableError,
    SlugConflictError,
)

router = APIRouter(prefix="/studio", tags=["studio"])
admin_router = APIRouter(prefix="/admin", tags=["admin"],
                         dependencies=[Depends(require_admin)])


class DraftRequest(BaseModel):
    text: str


class SubmitRequest(BaseModel):
    skill: SkillCreate
    source_text: str = ""


class RejectRequest(BaseModel):
    note: str = ""


class SubmissionSummary(BaseModel):
    run_id: str
    name: str
    slug: str
    status: str
    rejection_note: str | None = None
    submitted_by: str
    created_at: datetime
    source_text: str | None = None
    skill: SkillCreate | None = None  # full draft — the owner and admins only


def _summary(r: RefundRun) -> SubmissionSummary:
    d = r.skill_draft
    return SubmissionSummary(
        run_id=r.id, name=d.name, slug=d.slug, status=r.status,
        rejection_note=r.rejection_note, submitted_by=r.requester_name,
        created_at=r.created_at, source_text=r.request_text, skill=d)


# ── SME endpoints ─────────────────────────────────────────────────────────────

@router.post("/draft")
async def draft_skill(body: DraftRequest, user: User = Depends(require_sme),
                      drafter=Depends(get_skill_drafter)) -> SkillCreate:
    if drafter is None:
        raise HTTPException(status_code=503, detail="drafter unavailable")
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="describe the skill first")
    try:
        return await drafter.draft(body.text)
    except SkillDraftError as e:
        raise HTTPException(status_code=502,
                            detail=f"couldn't draft a skill: {e}") from e


@router.post("/submit", status_code=201)
async def submit_skill(body: SubmitRequest, user: User = Depends(require_sme),
                       flow=Depends(get_skill_publish_flow)) -> dict:
    if flow is None:
        raise HTTPException(status_code=503, detail="studio unavailable")
    try:
        run = await flow.submit(draft=body.skill, source_text=body.source_text, user=user)
    except SlugConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SkillStorePersistenceError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"run_id": run.id, "status": run.status}


@router.get("/submissions")
async def my_submissions(user: User = Depends(require_sme),
                         store=Depends(get_run_store)) -> list[SubmissionSummary]:
    """The caller's own submissions, newest first. Cancelled (withdrawn) runs
    are hidden here but stay in the run store for the admin audit trail."""
    if store is None:
        return []
    return [_summary(r)
            for r in await store.list_runs(limit=100)
            if (r.kind == "skill_publish" and r.skill_draft is not None
                and r.requester_email == user.email and r.status != "cancelled")]


@router.patch("/submissions/{run_id}")
async def resubmit_submission(run_id: str, body: SubmitRequest,
                              user: User = Depends(require_sme),
                              flow=Depends(get_skill_publish_flow)) -> SubmissionSummary:
    if flow is None:
        raise HTTPException(status_code=503, detail="studio unavailable")
    try:
        run = await flow.resubmit(run_id=run_id, draft=body.skill,
                                  source_text=body.source_text, user=user)
    except KeyError:
        raise HTTPException(status_code=404, detail="submission not found") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="not your submission") from None
    except NotEditableError as e:
        raise HTTPException(status_code=409, detail=f"submission is {e}") from e
    except SlugConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _summary(run)


@router.delete("/submissions/{run_id}", status_code=204)
async def withdraw_submission(run_id: str, user: User = Depends(require_sme),
                              flow=Depends(get_skill_publish_flow)) -> Response:
    if flow is None:
        raise HTTPException(status_code=503, detail="studio unavailable")
    try:
        await flow.withdraw(run_id=run_id, user=user)
    except KeyError:
        raise HTTPException(status_code=404, detail="submission not found") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="not your submission") from None
    except NotEditableError as e:
        raise HTTPException(status_code=409, detail=f"submission is {e}") from e
    return Response(status_code=204)


# ── Admin endpoints ───────────────────────────────────────────────────────────

@admin_router.get("/skill-submissions")
async def list_submissions(store=Depends(get_run_store)) -> list[SubmissionSummary]:
    if store is None:
        return []
    return [_summary(r)
            for r in await store.list_runs(limit=100)
            if r.kind == "skill_publish" and r.skill_draft is not None]


async def _actor_name(authorization, x_debug_bypass_auth, x_ms_client_principal) -> str:
    """Best-effort decision-maker name for the audit trail. The x-admin-key
    path has no signed-in user — record the decision as 'Admin'."""
    try:
        user = await resolve_user(
            easy_auth=x_ms_client_principal, authorization=authorization,
            debug_header=x_debug_bypass_auth)
        return user.display_name
    except HTTPException:
        return "Admin"


async def _decide(run_id: str, *, approve: bool, note: str | None, flow,
                  actor: str) -> SubmissionSummary:
    if flow is None:
        raise HTTPException(status_code=503, detail="studio unavailable")
    try:
        run = await flow.decide(run_id=run_id, approve=approve,
                                actor_name=actor, note=note)
    except KeyError:
        raise HTTPException(status_code=404, detail="submission not found") from None
    except AlreadyDecidedError as e:
        raise HTTPException(status_code=409, detail=f"already {e}") from e
    except SlugConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:  # slug landed in the catalog while this was pending
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SkillStorePersistenceError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return _summary(run)


@admin_router.post("/skill-submissions/{run_id}/approve")
async def approve_submission(
    run_id: str, flow=Depends(get_skill_publish_flow),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> SubmissionSummary:
    actor = await _actor_name(authorization, x_debug_bypass_auth, x_ms_client_principal)
    return await _decide(run_id, approve=True, note=None, flow=flow, actor=actor)


@admin_router.post("/skill-submissions/{run_id}/reject")
async def reject_submission(
    run_id: str, body: RejectRequest, flow=Depends(get_skill_publish_flow),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> SubmissionSummary:
    actor = await _actor_name(authorization, x_debug_bypass_auth, x_ms_client_principal)
    return await _decide(run_id, approve=False, note=body.note, flow=flow, actor=actor)
