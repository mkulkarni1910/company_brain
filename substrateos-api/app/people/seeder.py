"""Seed the People pillar from Microsoft Graph (app-only token).

Reads users, groups, group memberships, and manager relationships from Entra
and writes them into the Cosmos Gremlin graph under a fixed tenant_id partition.
The Graph tenant's real directory is materialized; tenant_id is SubStrateOS's
logical tenant (Phase 2a uses the single demo tenant 't-test').
"""

from __future__ import annotations

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.people.graph_client import PeopleGraphClient

_GRAPH = "https://graph.microsoft.com/v1.0"


class PeopleSeeder:
    def __init__(self, *, graph: PeopleGraphClient, tenant_id: str) -> None:
        self._graph = graph
        self._tenant_id = tenant_id

    async def _token(self) -> str:
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token("https://graph.microsoft.com/.default")
            return tok.token
        finally:
            await cred.close()

    async def seed_users(self, *, limit: int = 100) -> dict[str, int]:
        token = await self._token()
        users = 0
        managers = 0
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.get(
                f"{_GRAPH}/users",
                params={"$top": str(min(limit, 999)), "$select": "id,displayName,mail,userPrincipalName"},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            people = r.json().get("value", [])[:limit]
            for p in people:
                await self._graph.upsert_user(
                    user_id=p["id"],
                    tenant_id=self._tenant_id,
                    email=p.get("mail") or p.get("userPrincipalName") or "",
                    display_name=p.get("displayName") or "",
                )
                users += 1
            # manager edges (best-effort; a user may have no manager)
            for p in people:
                mr = await http.get(
                    f"{_GRAPH}/users/{p['id']}/manager",
                    params={"$select": "id"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if mr.status_code == 200:
                    mgr_id = mr.json().get("id")
                    if mgr_id:
                        await self._graph.upsert_edge(
                            label="manages",
                            from_id=mgr_id,
                            to_id=p["id"],
                            tenant_id=self._tenant_id,
                        )
                        managers += 1
        return {"users": users, "manager_edges": managers}

    async def seed_groups(self, *, limit: int = 100) -> dict[str, int]:
        token = await self._token()
        groups = 0
        memberships = 0
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.get(
                f"{_GRAPH}/groups",
                params={"$top": str(min(limit, 999)), "$select": "id,displayName"},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            gl = r.json().get("value", [])[:limit]
            for g in gl:
                await self._graph.upsert_group(
                    group_id=g["id"],
                    tenant_id=self._tenant_id,
                    name=g.get("displayName") or "",
                )
                groups += 1
                mr = await http.get(
                    f"{_GRAPH}/groups/{g['id']}/members",
                    params={"$select": "id", "$top": "100"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if mr.status_code == 200:
                    for m in mr.json().get("value", []):
                        await self._graph.upsert_edge(
                            label="member_of",
                            from_id=m["id"],
                            to_id=g["id"],
                            tenant_id=self._tenant_id,
                        )
                        memberships += 1
        return {"groups": groups, "membership_edges": memberships}
