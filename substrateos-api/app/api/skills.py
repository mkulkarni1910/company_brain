from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.api._auth_resolve import resolve_user
from app.api.admin import require_admin_key
from app.deps import get_skill_store, get_token_store
from app.domain.skill import Skill, SkillCreate, SkillSummary, SkillUpdate

router = APIRouter(tags=["skills"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


# ---------------------------------------------------------------------------
# User-facing endpoints (auth-gated via resolve_user)
# ---------------------------------------------------------------------------

@router.get("/skills")
async def list_skills(
    store=Depends(get_skill_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[SkillSummary]:
    """List enabled skills — returns summaries (no system_prompt)."""
    await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    if store is None:
        return []
    skills = await store.list_enabled()
    return [SkillSummary.from_skill(s) for s in skills]


@router.post("/skills/{skill_id}/run", status_code=204)
async def run_skill(
    skill_id: str,
    store=Depends(get_skill_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Response:
    """Increment run count for a skill."""
    await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    if store is not None:
        await store.increment_run_count(skill_id)
    return Response(status_code=204)


@router.post("/skills/{skill_id}/rate")
async def rate_skill(
    skill_id: str,
    body: dict,
    store=Depends(get_skill_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> SkillSummary:
    """Rate a skill (1–5). Returns the updated SkillSummary."""
    await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )
    rating = body.get("rating")
    if rating is None or not (1 <= float(rating) <= 5):
        raise HTTPException(status_code=422, detail="rating must be between 1 and 5")
    if store is None:
        raise HTTPException(status_code=404, detail="skill not found")
    updated = await store.update_rating(skill_id, float(rating))
    if updated is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return SkillSummary.from_skill(updated)


# ---------------------------------------------------------------------------
# Admin endpoints (require_admin_key, prefix=/admin)
# ---------------------------------------------------------------------------

@admin_router.get("/skills")
async def admin_list_skills(store=Depends(get_skill_store)) -> list[Skill]:
    """List all skills including disabled ones. Returns full Skill (with system_prompt)."""
    if store is None:
        return []
    return await store.list_all()


@admin_router.post("/skills", status_code=201)
async def admin_create_skill(data: SkillCreate, store=Depends(get_skill_store)) -> Skill:
    """Create a new skill. Returns 409 on duplicate slug."""
    if store is None:
        raise HTTPException(status_code=503, detail="skill store unavailable")
    try:
        return await store.create(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@admin_router.patch("/skills/{skill_id}")
async def admin_update_skill(
    skill_id: str, data: SkillUpdate, store=Depends(get_skill_store)
) -> Skill:
    """Partially update a skill. Returns 404 if skill not found."""
    if store is None:
        raise HTTPException(status_code=404, detail="skill not found")
    updated = await store.update(skill_id, data)
    if updated is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return updated


@admin_router.delete("/skills/{skill_id}", status_code=204)
async def admin_delete_skill(skill_id: str, store=Depends(get_skill_store)) -> Response:
    """Delete a skill. Returns 404 if skill not found."""
    if store is None:
        raise HTTPException(status_code=404, detail="skill not found")
    deleted = await store.delete(skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="skill not found")
    return Response(status_code=204)
