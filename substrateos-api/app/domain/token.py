from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TokenMeta(BaseModel):
    token_id: str
    name: str
    masked: str          # e.g. sbx_live_••••a210
    created_at: datetime
    last_used_at: datetime | None = None


class TokenCreated(BaseModel):
    token: str           # plaintext, shown to the caller exactly once
    meta: TokenMeta
