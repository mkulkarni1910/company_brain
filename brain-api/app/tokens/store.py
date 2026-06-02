"""Personal Access Tokens stored on Cosmos DB (Gremlin), reusing the people graph.

Mirrors CosmosConnectionStore: vertices `cbrain_token` carry indexed props
`tid`(=token_id, vertex key), `tenant_id`(partition), `user_id`, `hash`(sha256
of the plaintext) plus a JSON `data` blob. The plaintext is shown once at
creation and never stored. Reads degrade to []/None; writes log on failure. The
shared Gremlin client is owned elsewhere (app.state.people_graph) — never closed
here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.domain.identity import User
from app.domain.token import TokenMeta

logger = logging.getLogger(__name__)

_LABEL = "cbrain_token"


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


# Only re-write last_used_at after this many seconds, so an authenticated PAT
# doesn't trigger a Cosmos write on every Context API / MCP request.
_LAST_USED_THROTTLE_S = 300


def _stale(last_used_at: str | None) -> bool:
    if not last_used_at:
        return True
    try:
        prev = datetime.fromisoformat(last_used_at)
    except (TypeError, ValueError):
        return True
    return (datetime.now(UTC) - prev).total_seconds() >= _LAST_USED_THROTTLE_S


def _meta_from_data(d: dict) -> TokenMeta:
    return TokenMeta(
        token_id=d["token_id"],
        name=d["name"],
        masked=d["masked"],
        created_at=d["created_at"],
        last_used_at=d.get("last_used_at"),
    )


class CosmosTokenStore:
    def __init__(self, graph) -> None:
        # `graph` exposes async submit(query, bindings) -> list (PeopleGraphClient).
        self._g = graph

    async def aclose(self) -> None:
        return  # shared client owned elsewhere

    async def _upsert(self, *, token_id: str, tenant: str, user_id: str,
                      token_hash: str, data: str) -> None:
        try:
            await self._g.submit(
                f"g.V().has('{_LABEL}','tid', k).has('tenant_id', tid).fold()"
                f".coalesce(unfold(),"
                f" addV('{_LABEL}').property('tid', k).property('tenant_id', tid))"
                f".property('user_id', uid).property('hash', h).property('data', d)",
                {"k": token_id, "tid": tenant, "uid": user_id, "h": token_hash, "d": data},
            )
        except Exception as e:  # noqa: BLE001 — token writes are best-effort
            logger.warning("cosmos token upsert failed: %s", e)

    async def create(self, *, user: User, name: str) -> tuple[TokenMeta, str]:
        prefix = get_settings().token_prefix  # "sbx_live_"
        plaintext = f"{prefix}{secrets.token_urlsafe(32)}"
        token_id = uuid.uuid4().hex
        masked = f"{prefix}••••{plaintext[-4:]}"
        record = {
            "token_id": token_id, "tenant_id": user.tenant_id, "user_id": user.user_id,
            "email": user.email, "display_name": user.display_name,
            "name": name, "masked": masked, "created_at": _now(), "last_used_at": None,
        }
        await self._upsert(
            token_id=token_id, tenant=user.tenant_id, user_id=user.user_id,
            token_hash=_hash(plaintext), data=json.dumps(record),
        )
        return _meta_from_data(record), plaintext

    async def list(self, *, user: User) -> list[TokenMeta]:
        try:
            rows = await self._g.submit(
                f"g.V().has('{_LABEL}','tenant_id', tid).has('user_id', uid).values('data')",
                {"tid": user.tenant_id, "uid": user.user_id},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos token list failed: %s", e)
            return []
        out: list[TokenMeta] = []
        for data in rows:
            try:
                out.append(_meta_from_data(json.loads(data)))
            except Exception:  # noqa: BLE001 — skip corrupt rows
                continue
        out.sort(key=lambda m: m.created_at, reverse=True)
        return out

    async def revoke(self, *, user: User, token_id: str) -> bool:
        try:
            await self._g.submit(
                f"g.V().has('{_LABEL}','tid', k).has('tenant_id', tid)"
                f".has('user_id', uid).drop()",
                {"k": token_id, "tid": user.tenant_id, "uid": user.user_id},
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos token revoke failed: %s", e)
            return False

    async def resolve(self, plaintext: str) -> User | None:
        try:
            rows = await self._g.submit(
                f"g.V().has('{_LABEL}','hash', h).values('data')",
                {"h": _hash(plaintext)},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos token resolve failed: %s", e)
            return None
        if not rows:
            return None
        try:
            d = json.loads(rows[0])
        except Exception:  # noqa: BLE001
            return None
        # Best-effort last_used_at bump, throttled so we don't write to Cosmos on
        # every programmatic call (the MCP middleware resolves on each request).
        if _stale(d.get("last_used_at")):
            d["last_used_at"] = _now()
            await self._upsert(
                token_id=d["token_id"], tenant=d["tenant_id"], user_id=d["user_id"],
                token_hash=_hash(plaintext), data=json.dumps(d),
            )
        return User(
            user_id=d["user_id"], tenant_id=d["tenant_id"],
            email=d.get("email", f"{d['user_id']}@token"),
            display_name=d.get("display_name", d["user_id"]),
            group_ids=set(),
        )


class NullTokenStore:
    """Fallback when Cosmos is unconfigured — cannot mint or resolve tokens."""

    async def aclose(self) -> None:
        return

    async def create(self, *, user: User, name: str) -> tuple[TokenMeta, str]:
        meta = TokenMeta(token_id="", name=name, masked="(unavailable)", created_at=datetime.now(UTC))
        return meta, ""

    async def list(self, *, user: User) -> list[TokenMeta]:
        return []

    async def revoke(self, *, user: User, token_id: str) -> bool:
        return False

    async def resolve(self, plaintext: str) -> User | None:
        return None
