"""Live Fetch via Microsoft Graph /search.

Authenticates with a DefaultAzureCredential Graph token (same pattern as the
People seeder) — single-identity for Phase 3; per-user OBO is a Phase 4 swap
localized to _token(). Maps each Graph hit to a synthetic Candidate so live
results rank and cite uniformly with indexed chunks. Never raises: returns []
on any error so the orchestrator answer path is never blocked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://graph.microsoft.com/v1.0/search/query"
_RRF_K = 60


class MSGraphSearchFetcher:
    async def _token(self) -> str:
        # Phase 3: single-identity (the DefaultAzureCredential principal).
        # Phase 4 OBO swap touches only this method.
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token("https://graph.microsoft.com/.default")
            return tok.token
        finally:
            await cred.close()

    async def fetch(self, *, query: str, user: User) -> list[Candidate]:
        try:
            token = await self._token()
            body = {
                "requests": [
                    {
                        "entityTypes": ["driveItem", "listItem"],
                        "query": {"queryString": query},
                        "from": 0,
                        "size": 10,
                    }
                ]
            }
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.post(
                    _SEARCH_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning("Live Fetch (Graph /search) failed; returning no live results: %s", e)
            return []

        now = datetime.now(UTC)
        candidates: list[Candidate] = []
        for req in data.get("value", []):
            for container in req.get("hitsContainers", []):
                for i, hit in enumerate(container.get("hits", [])):
                    resource = hit.get("resource", {}) or {}
                    name = resource.get("name") or resource.get("subject") or "Untitled"
                    url = (
                        resource.get("webUrl")
                        or (resource.get("webLink") or {}).get("href")
                        or ""
                    )
                    summary = hit.get("summary") or resource.get("description") or ""
                    hit_id = hit.get("hitId") or f"{name}-{i}"
                    doc_id = f"graph:{hit_id}"
                    chunk = Chunk(
                        chunk_id=f"{doc_id}#live",
                        doc_id=doc_id,
                        tenant_id=user.tenant_id,
                        source="graph",
                        source_url=url,
                        title=name,
                        content=summary,
                        content_vector=[],
                        acl_principals=[],  # Graph already permission-trimmed this hit
                        author_id=None,
                        entities=[],
                        created_at=now,
                        modified_at=now,
                        chunk_index=0,
                    )
                    candidates.append(
                        Candidate(
                            chunk=chunk,
                            sources_hit={"live"},
                            raw_scores={"content_rrf": 1.0 / (_RRF_K + i)},
                            live_payload=hit,
                        )
                    )
        return candidates
