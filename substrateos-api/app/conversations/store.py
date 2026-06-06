# app/conversations/store.py
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from gremlin_python.driver import client, serializer

from app.config import get_settings
from app.domain.conversation import Conversation, ConversationSummary, ConversationTurn
from app.domain.identity import User
from app.domain.query import Answer, Citation

logger = logging.getLogger(__name__)
_MAX_TURNS = 50


def _one(vm: dict, key: str):
    v = vm.get(key)
    return v[0] if isinstance(v, list) else v


class ConversationStore:
    """Persistent per-user conversation history in Cosmos Gremlin (label `conversation`,
    partition key tenant_id). All methods are best-effort: a Cosmos failure never breaks
    /query (append) and read paths degrade to []/None."""

    def __init__(self, gremlin_client: Any | None = None) -> None:
        if gremlin_client is not None:
            self._client = gremlin_client
        else:
            s = get_settings()
            if not s.cosmos_gremlin_endpoint or not s.cosmos_gremlin_key:
                raise RuntimeError("Cosmos Gremlin settings are not configured")
            self._client = client.Client(
                s.cosmos_gremlin_endpoint,
                "g",
                username=f"/dbs/{s.cosmos_gremlin_database}/colls/{s.cosmos_gremlin_conversations_graph}",
                password=s.cosmos_gremlin_key,
                message_serializer=serializer.GraphSONSerializersV2d0(),
            )

    async def _submit(self, query: str, bindings: dict[str, Any] | None = None) -> list[Any]:
        def _run() -> list[Any]:
            return self._client.submit(query, bindings or {}).all().result()
        return await asyncio.to_thread(_run)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._client.close)

    async def append(self, *, user: User, conversation_id: str, query: str, answer: Answer) -> None:
        now = datetime.now(UTC).isoformat()
        turn = {
            "q": query,
            "a": {"text": answer.text, "citations": [c.model_dump() for c in answer.citations]},
            "ts": now,
        }
        try:
            rows = await self._submit(
                "g.V().has('conversation','conv_id', cid).has('tenant_id', tid).has('user_id', uid)"
                ".valueMap('turns_json')",
                {"cid": conversation_id, "tid": user.tenant_id, "uid": user.user_id},
            )
            existing: list = []
            if rows:
                raw = _one(rows[0], "turns_json")
                if raw:
                    existing = json.loads(raw)
            turns = (existing + [turn])[-_MAX_TURNS:]
            await self._submit(
                "g.V().has('conversation','conv_id', cid).has('tenant_id', tid).has('user_id', uid).fold()"
                ".coalesce(unfold(),"
                " addV('conversation').property('conv_id', cid).property('tenant_id', tid)"
                "  .property('user_id', uid).property('title', title).property('created_at', now))"
                ".property('updated_at', now).property('turn_count', tc).property('turns_json', tj)",
                {"cid": conversation_id, "tid": user.tenant_id, "uid": user.user_id,
                 "title": (query[:80] or "Untitled"), "now": now,
                 "tc": len(turns), "tj": json.dumps(turns)},
            )
        except Exception as e:  # noqa: BLE001 - best-effort; never break /query
            logger.warning("conversation append failed (cid=%s): %s", conversation_id, e)

    async def list(self, *, user: User, limit: int = 100) -> list[ConversationSummary]:
        try:
            rows = await self._submit(
                "g.V().has('conversation','tenant_id', tid).has('user_id', uid)"
                ".order().by('updated_at', decr).limit(lim)"  # decr is Cosmos Gremlin token (not standard TinkerPop Order.desc); validated only against real cluster
                ".project('id','title','updated_at','turn_count')"
                ".by('conv_id').by('title').by('updated_at').by('turn_count')",
                {"tid": user.tenant_id, "uid": user.user_id, "lim": limit},
            )
        except Exception as e:  # noqa: BLE001 - degrade to empty
            logger.warning("conversation list failed: %s", e)
            return []
        out: list[ConversationSummary] = []
        for r in rows:
            try:
                out.append(ConversationSummary(
                    id=r["id"], title=r["title"], updated_at=r["updated_at"],
                    turn_count=int(r["turn_count"])))
            except Exception:  # noqa: BLE001 - skip malformed
                continue
        return out

    @staticmethod
    def _conv_from_vm(vm: dict) -> Conversation:
        """Build a Conversation from a Gremlin valueMap row (shared by get/get_any)."""
        updated = _one(vm, "updated_at")
        raw = _one(vm, "turns_json")
        turns: list[ConversationTurn] = []
        for t in (json.loads(raw) if raw else []):
            a = t.get("a", {})
            turns.append(ConversationTurn(
                query=t.get("q", ""),
                answer=Answer(
                    text=a.get("text", ""),
                    citations=[Citation(**c) for c in a.get("citations", [])],
                    query_id=""),
                ts=t.get("ts") or updated))  # older turns predate per-turn ts
        return Conversation(
            id=_one(vm, "conv_id"), title=_one(vm, "title"),
            created_at=_one(vm, "created_at"), updated_at=updated, turns=turns)

    async def get(self, *, user: User, conversation_id: str) -> Conversation | None:
        try:
            rows = await self._submit(
                "g.V().has('conversation','conv_id', cid).has('tenant_id', tid).has('user_id', uid)"
                ".valueMap('conv_id','title','created_at','updated_at','turns_json')",
                {"cid": conversation_id, "tid": user.tenant_id, "uid": user.user_id},
            )
        except Exception as e:  # noqa: BLE001 - degrade to None
            logger.warning("conversation get failed (cid=%s): %s", conversation_id, e)
            return None
        return self._conv_from_vm(rows[0]) if rows else None

    async def list_all(self, *, tenant_id: str, limit: int = 50) -> list[dict]:
        """Admin org-wide list — every conversation in the tenant (no user filter).
        Returns dicts with the asker's user_id so the caller can resolve a name."""
        try:
            rows = await self._submit(
                "g.V().has('conversation','tenant_id', tid)"
                ".order().by('updated_at', decr).limit(lim)"
                ".project('id','title','updated_at','turn_count','user_id')"
                ".by('conv_id').by('title').by('updated_at').by('turn_count').by('user_id')",
                {"tid": tenant_id, "lim": limit},
            )
        except Exception as e:  # noqa: BLE001 - degrade to empty
            logger.warning("conversation list_all failed: %s", e)
            return []
        out: list[dict] = []
        for r in rows:
            try:
                out.append({"id": r["id"], "title": r["title"], "updated_at": r["updated_at"],
                            "turn_count": int(r["turn_count"]), "user_id": r.get("user_id", "")})
            except Exception:  # noqa: BLE001 - skip malformed
                continue
        return out

    async def get_any(self, *, tenant_id: str, conversation_id: str) -> dict | None:
        """Admin fetch — any conversation in the tenant (no user filter). Returns the
        Conversation plus the asker's user_id."""
        try:
            rows = await self._submit(
                "g.V().has('conversation','conv_id', cid).has('tenant_id', tid)"
                ".valueMap('conv_id','title','created_at','updated_at','turns_json','user_id')",
                {"cid": conversation_id, "tid": tenant_id},
            )
        except Exception as e:  # noqa: BLE001 - degrade to None
            logger.warning("conversation get_any failed (cid=%s): %s", conversation_id, e)
            return None
        if not rows:
            return None
        return {"conversation": self._conv_from_vm(rows[0]), "user_id": _one(rows[0], "user_id") or ""}
