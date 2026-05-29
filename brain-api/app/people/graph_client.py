"""Thin async wrapper over Cosmos DB Gremlin via gremlinpython.

gremlinpython ships a synchronous Client; we run submits in a worker thread
(asyncio.to_thread) so the event loop is never blocked. Vertices carry a
`tenant_id` property which is also the Cosmos partition key.
"""

from __future__ import annotations

import asyncio
from typing import Any

from gremlin_python.driver import client, serializer

from app.config import get_settings


class PeopleGraphClient:
    def __init__(self) -> None:
        s = get_settings()
        if not s.cosmos_gremlin_endpoint or not s.cosmos_gremlin_key:
            raise RuntimeError("Cosmos Gremlin settings are not configured")
        self._client = client.Client(
            s.cosmos_gremlin_endpoint,
            "g",
            username=f"/dbs/{s.cosmos_gremlin_database}/colls/{s.cosmos_gremlin_graph}",
            password=s.cosmos_gremlin_key,
            message_serializer=serializer.GraphSONSerializersV2d0(),
        )

    async def submit(self, query: str, bindings: dict[str, Any] | None = None) -> list[Any]:
        def _run() -> list[Any]:
            return self._client.submit(query, bindings or {}).all().result()

        return await asyncio.to_thread(_run)

    async def upsert_user(
        self, *, user_id: str, tenant_id: str, email: str, display_name: str
    ) -> None:
        # Cosmos Gremlin upsert idiom: coalesce(existing, addV).
        await self.submit(
            "g.V().has('user','user_id', uid).has('tenant_id', tid).fold()"
            ".coalesce(unfold(),"
            " addV('user').property('user_id', uid).property('tenant_id', tid))"
            ".property('email', em).property('display_name', dn)",
            {"uid": user_id, "tid": tenant_id, "em": email, "dn": display_name},
        )

    async def upsert_group(self, *, group_id: str, tenant_id: str, name: str) -> None:
        await self.submit(
            "g.V().has('group','group_id', gid).has('tenant_id', tid).fold()"
            ".coalesce(unfold(),"
            " addV('group').property('group_id', gid).property('tenant_id', tid))"
            ".property('name', nm)",
            {"gid": group_id, "tid": tenant_id, "nm": name},
        )

    async def upsert_document(self, *, doc_id: str, tenant_id: str) -> None:
        await self.submit(
            "g.V().has('document','doc_id', did).has('tenant_id', tid).fold()"
            ".coalesce(unfold(),"
            " addV('document').property('doc_id', did).property('tenant_id', tid))",
            {"did": doc_id, "tid": tenant_id},
        )

    async def upsert_edge(
        self, *, label: str, from_id: str, to_id: str, tenant_id: str
    ) -> None:
        # Match by the *_id property on either end (user_id / group_id / doc_id).
        await self.submit(
            "g.V().has('tenant_id', tid).or(has('user_id', a), has('group_id', a),"
            " has('doc_id', a)).as('src')"
            ".V().has('tenant_id', tid).or(has('user_id', b), has('group_id', b),"
            " has('doc_id', b)).as('dst')"
            ".coalesce("
            "  inE(lbl).where(outV().as('src')),"
            "  addE(lbl).from('src').to('dst'))",
            {"tid": tenant_id, "a": from_id, "b": to_id, "lbl": label},
        )

    async def aclose(self) -> None:
        def _close() -> None:
            self._client.close()

        await asyncio.to_thread(_close)
