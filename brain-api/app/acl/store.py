"""Query-time ACL store: the current allowed-principal set per document.

Written at ingest time; read during the orchestrator's query-time re-check.
Fail-closed: if Redis errors, treat as "cannot verify" and drop the candidate.
On a key MISS (no entry yet), the caller falls back to the chunk's index-time
acl_principals (handled in recheck()).
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.identity import User
from app.domain.query import Candidate

logger = logging.getLogger(__name__)


class ACLStoreError(Exception):
    """Raised when the ACL store cannot be reached (forces fail-closed)."""


def _doc_key(tenant_id: str, doc_id: str) -> str:
    return f"acl:doc:{tenant_id}:{doc_id}"


class ACLStore:
    def __init__(self) -> None:
        s = get_settings()
        self._r = redis.Redis(
            host=s.azure_redis_host,
            port=s.azure_redis_port,
            ssl=s.azure_redis_ssl,
            password=s.redis_key,
            decode_responses=True,
        )

    async def aclose(self) -> None:
        await self._r.aclose()

    async def set_doc_principals(
        self, *, tenant_id: str, doc_id: str, principals: list[str], ttl_seconds: int | None = None
    ) -> None:
        try:
            key = _doc_key(tenant_id, doc_id)
            value = json.dumps(sorted(principals))
            if ttl_seconds is None:
                await self._r.set(key, value)  # persistent: live ACL is authoritative
            else:
                await self._r.set(key, value, ex=ttl_seconds)
        except (RedisError, ConnectionError, TimeoutError, OSError) as e:
            logger.warning("ACLStore write failed (doc=%s): %s", doc_id, e)

    async def doc_principals(self, *, tenant_id: str, doc_id: str) -> set[str] | None:
        """Return the live allowed-principal set, or None on key miss.

        Raises ACLStoreError if Redis is unreachable (caller fails closed).
        """
        try:
            v = await self._r.get(_doc_key(tenant_id, doc_id))
        except (RedisError, ConnectionError, TimeoutError, OSError) as e:
            raise ACLStoreError(str(e)) from e
        if v is None:
            return None
        return set(json.loads(v))

    async def recheck(self, *, candidates: list[Candidate], user: User) -> list[Candidate]:
        fail_closed_on_missing = getattr(self, "_fail_closed", None)
        if fail_closed_on_missing is None:
            fail_closed_on_missing = get_settings().acl_fail_closed_on_missing
        principals = user.principals()
        kept: list[Candidate] = []
        for c in candidates:
            try:
                live = await self.doc_principals(tenant_id=user.tenant_id, doc_id=c.chunk.doc_id)
            except ACLStoreError:
                logger.warning("ACL store unreachable; dropping doc %s (fail-closed)", c.chunk.doc_id)
                continue
            if live is None:
                # No live entry. Strict mode fails closed; otherwise fall back to
                # the chunk's index-time ACL (covers docs ingested before the store).
                if fail_closed_on_missing:
                    continue
                allowed = set(c.chunk.acl_principals)
            else:
                allowed = live  # live entry is authoritative (persistent; revocation propagates)
            if principals & allowed:
                kept.append(c)
        return kept
