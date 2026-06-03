from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.api._auth_resolve import resolve_user
from app.deps import get_token_store
from app.domain.token import TokenCreated, TokenMeta

router = APIRouter(tags=["tokens"])


class CreateTokenRequest(BaseModel):
    name: str = "token"


async def _interactive_user(authorization, x_debug_bypass_auth, x_ms_client_principal):
    # NOTE: no token_store passed — a PAT can never manage tokens.
    return await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )


@router.post("/tokens", response_model=TokenCreated)
async def create_token(
    body: CreateTokenRequest,
    store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> TokenCreated:
    user = await _interactive_user(authorization, x_debug_bypass_auth, x_ms_client_principal)
    if store is None:
        raise HTTPException(status_code=503, detail="token store unavailable")
    meta, plaintext = await store.create(user=user, name=body.name.strip() or "token")
    if not plaintext:
        raise HTTPException(status_code=503, detail="token store unavailable")
    return TokenCreated(token=plaintext, meta=meta)


@router.get("/tokens", response_model=list[TokenMeta])
async def list_tokens(
    store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[TokenMeta]:
    user = await _interactive_user(authorization, x_debug_bypass_auth, x_ms_client_principal)
    if store is None:
        return []
    return await store.list(user=user)


@router.delete("/tokens/{token_id}")
async def revoke_token(
    token_id: str,
    store=Depends(get_token_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> dict[str, bool]:
    user = await _interactive_user(authorization, x_debug_bypass_auth, x_ms_client_principal)
    if store is None:
        return {"revoked": False}
    return {"revoked": await store.revoke(user=user, token_id=token_id)}
