from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api._auth_resolve import resolve_user
from app.deps import get_conversation_store
from app.domain.conversation import Conversation, ConversationSummary

router = APIRouter(tags=["conversations"])


async def _user(x_ms_client_principal, authorization, x_debug_bypass_auth):
    return await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth)


@router.get("/conversations", response_model=list[ConversationSummary])
async def conversations(
    store=Depends(get_conversation_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[ConversationSummary]:
    user = await _user(x_ms_client_principal, authorization, x_debug_bypass_auth)
    if store is None:
        return []
    return await store.list(user=user)


@router.get("/conversations/{conversation_id}", response_model=Conversation)
async def conversation(
    conversation_id: str,
    store=Depends(get_conversation_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Conversation:
    user = await _user(x_ms_client_principal, authorization, x_debug_bypass_auth)
    conv = None if store is None else await store.get(user=user, conversation_id=conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv
