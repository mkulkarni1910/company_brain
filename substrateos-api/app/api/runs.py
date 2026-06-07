from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api._auth_resolve import resolve_user
from app.deps import get_audit_log, get_run_store, get_token_store

router = APIRouter(tags=["runs"])


async def _require_user(
    authorization: str | None,
    x_debug_bypass_auth: str | None,
    x_ms_client_principal: str | None,
    token_store,
):
    return await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
        token_store=token_store,
    )


@router.get("/runs")
async def list_runs(
    run_store=Depends(get_run_store),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[dict]:
    await _require_user(authorization, x_debug_bypass_auth, x_ms_client_principal, token_store)
    if run_store is None:
        return []
    runs = await run_store.list_runs(limit=50)
    return [r.model_dump(mode="json") for r in runs]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    run_store=Depends(get_run_store),
    audit_log=Depends(get_audit_log),
    token_store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> dict:
    await _require_user(authorization, x_debug_bypass_auth, x_ms_client_principal, token_store)
    if run_store is None:
        raise HTTPException(status_code=404, detail="run not found")
    run = await run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = await run_store.list_events(run_id)
    audit = await audit_log.query(run_id) if audit_log is not None else []
    return {"run": run.model_dump(mode="json"),
            "events": [e.model_dump(mode="json") for e in events],
            "audit": [a.model_dump(mode="json") for a in audit]}
