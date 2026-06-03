"""Shared Microsoft Graph helpers for the admin-consent connectors.

`graph_token` extracts the client-credentials token flow that was duplicated
across the SharePoint and Teams connectors; new connectors (Outlook mail +
calendar) use it directly. `_dt` / `_strip_html` are the shared parse helpers.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from html import unescape

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.config import get_settings

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"


async def graph_token(tenant_id: str | None) -> str:
    """App-only token via client-credentials against an admin-consented tenant.
    Falls back to DefaultAzureCredential (home tenant / local) when no tenant or
    client secret is configured."""
    s = get_settings()
    if tenant_id and s.azure_client_id and s.azure_client_secret:
        from app.connectors.oauth import token_url
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.post(token_url(tenant_id), data={
                "client_id": s.azure_client_id,
                "client_secret": s.azure_client_secret,
                "scope": SCOPE,
                "grant_type": "client_credentials",
            })
            r.raise_for_status()
            return r.json()["access_token"]
    cred = DefaultAzureCredential()
    try:
        tok = await cred.get_token(SCOPE)
        return tok.token
    finally:
        await cred.close()


class GraphError(RuntimeError):
    """A Microsoft Graph HTTP error that preserves the response BODY.

    httpx's default `raise_for_status()` message includes the status and URL but
    drops the body — which is where Graph puts the actual reason (e.g. "Tenant does
    not have a SPO license", "Access denied"). Connectors log/surface this, so the
    admin sees why a sync found nothing instead of a bare "0 indexed".
    """

    def __init__(self, status_code: int, url: str, body: str) -> None:
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"Graph {status_code} for {url}: {body[:500]}")


async def graph_get_json(token: str, url: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.get(url, headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 400:
            raise GraphError(r.status_code, url, r.text)
        return r.json()


def parse_users(data: dict) -> list[dict]:
    """Parse a /users response → [{user_id, mail, upn}] (drops users with no id)."""
    out = []
    for u in data.get("value", []):
        uid = u.get("id", "")
        if not uid:
            continue
        out.append({
            "user_id": uid,
            "mail": u.get("mail") or u.get("userPrincipalName") or "",
            "upn": u.get("userPrincipalName") or "",
        })
    return out


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _strip_html(html: str) -> str:
    """Strip HTML tags and unescape entities; collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()
