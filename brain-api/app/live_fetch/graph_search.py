"""Live Fetch via Microsoft Graph /search.

Token acquisition (_token):
  * Per-user OBO (Phase 4): when a requesting-user token is present AND
    live_fetch_obo_enabled is set AND the API app's client id+secret are
    configured, exchange the user assertion for a Graph token via msal's
    on-behalf-of flow. Graph then permission-trims hits to the requesting user.
  * Otherwise fall back to the single-identity DefaultAzureCredential principal
    (Phase 3 behaviour). Any OBO failure also falls back — never raises.

Maps each Graph hit to a synthetic Candidate so live results rank and cite
uniformly with indexed chunks. Never raises: returns [] on any error so the
orchestrator answer path is never blocked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.config import get_settings
from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.query import Candidate

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://graph.microsoft.com/v1.0/search/query"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_RRF_K = 60


class MSGraphSearchFetcher:
    async def _obo_token(self, user_token: str) -> str | None:
        """Exchange the requesting user's token for a Graph token (OBO).

        Returns None if OBO is not configured/enabled or on any failure, so the
        caller falls back to the single service identity. Never raises.
        """
        settings = get_settings()
        if not (
            settings.live_fetch_obo_enabled
            and settings.azure_api_client_id
            and settings.azure_api_client_secret
        ):
            return None
        try:
            import msal

            app = msal.ConfidentialClientApplication(
                settings.azure_api_client_id,
                authority=f"https://login.microsoftonline.com/{settings.azure_tenant_id}",
                client_credential=settings.azure_api_client_secret,
            )
            result = app.acquire_token_on_behalf_of(
                user_assertion=user_token, scopes=[_GRAPH_SCOPE]
            )
            token = result.get("access_token") if isinstance(result, dict) else None
            if not token:
                logger.warning(
                    "OBO token exchange returned no access_token (%s); "
                    "falling back to service identity",
                    (result or {}).get("error") if isinstance(result, dict) else None,
                )
                return None
            return token
        except Exception as e:
            logger.warning(
                "OBO token exchange failed; falling back to service identity: %s", e
            )
            return None

    async def _token(self, user_token: str | None = None) -> str:
        # Per-user OBO when available, else the single-identity service principal.
        if user_token:
            obo = await self._obo_token(user_token)
            if obo:
                return obo
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token(_GRAPH_SCOPE)
            return tok.token
        finally:
            await cred.close()

    async def fetch(
        self, *, query: str, user: User, user_token: str | None = None
    ) -> list[Candidate]:
        try:
            token = await self._token(user_token)
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
