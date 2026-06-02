from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime

import httpx
from azure.identity.aio import DefaultAzureCredential

from app.config import get_settings
from app.connectors.extract import extract_text, is_supported
from app.connectors.models import RemoteFile

logger = logging.getLogger(__name__)
_GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"


def _parse_sites(data: dict) -> list[dict]:
    out = []
    for s in data.get("value", []):
        out.append({"site_id": s.get("id", ""),
                    "name": s.get("displayName") or s.get("name") or "Untitled",
                    "web_url": s.get("webUrl", "")})
    return [s for s in out if s["site_id"]]


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _parse_drive_children(data: dict, drive_id: str) -> tuple[list[RemoteFile], list[str]]:
    files: list[RemoteFile] = []
    folders: list[str] = []
    for it in data.get("value", []):
        if "folder" in it:
            folders.append(it.get("id", ""))
            continue
        if "file" not in it:
            continue
        name = it.get("name", "")
        author = ((it.get("createdBy") or {}).get("user") or {}).get("id") \
            or ((it.get("lastModifiedBy") or {}).get("user") or {}).get("id")
        files.append(RemoteFile(
            drive_id=drive_id, item_id=it.get("id", ""), name=name,
            mime=(it.get("file") or {}).get("mimeType"),
            web_url=it.get("webUrl", ""), size=int(it.get("size") or 0),
            created_at=_dt(it.get("createdDateTime")),
            modified_at=_dt(it.get("lastModifiedDateTime")),
            author_id=author))
    return files, [f for f in folders if f]


class SharePointConnector:
    """MS Graph SharePoint reader. When `tenant_id` is set, authenticates app-only
    via client-credentials against that (admin-consented) tenant; otherwise falls
    back to DefaultAzureCredential (home tenant / legacy). Never raises."""

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    async def _token(self) -> str:
        s = get_settings()
        if self._tenant_id and s.azure_client_id and s.azure_client_secret:
            from app.connectors.oauth import token_url
            async with httpx.AsyncClient(timeout=15.0) as http:
                r = await http.post(token_url(self._tenant_id), data={
                    "client_id": s.azure_client_id,
                    "client_secret": s.azure_client_secret,
                    "scope": _SCOPE,
                    "grant_type": "client_credentials",
                })
                r.raise_for_status()
                return r.json()["access_token"]
        cred = DefaultAzureCredential()
        try:
            tok = await cred.get_token(_SCOPE)
            return tok.token
        finally:
            await cred.close()

    async def _get_json(self, url: str) -> dict:
        token = await self._token()
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.get(url, headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            return r.json()

    async def list_sites(self) -> list[dict]:
        try:
            data = await self._get_json(f"{_GRAPH}/sites?search=*&$top=50")
            return _parse_sites(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("list_sites failed (Graph perm pending?): %s", e)
            return []

    async def list_files(self, site_id: str, max_items: int | None = None) -> list[RemoteFile]:
        """Enumerate files across the site's default drive (recursive, BFS). Caps at
        connector_max_items. Returns [] on any error."""
        cap = max_items or get_settings().connector_max_items
        try:
            drive = await self._get_json(f"{_GRAPH}/sites/{site_id}/drive")
            drive_id = drive.get("id", "")
            if not drive_id:
                return []
            files: list[RemoteFile] = []
            queue: deque[str] = deque([f"{_GRAPH}/drives/{drive_id}/root/children"])
            while queue and len(files) < cap:
                data = await self._get_json(queue.popleft())
                fs, folder_ids = _parse_drive_children(data, drive_id)
                files.extend(f for f in fs if is_supported(f.name, f.mime))
                for fid in folder_ids:
                    queue.append(f"{_GRAPH}/drives/{drive_id}/items/{fid}/children")
            return files[:cap]
        except Exception as e:  # noqa: BLE001
            logger.warning("list_files failed for site %s: %s", site_id, e)
            return []

    async def fetch_content(self, drive_id: str, item_id: str) -> bytes | None:
        try:
            token = await self._token()
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                r = await http.get(f"{_GRAPH}/drives/{drive_id}/items/{item_id}/content",
                                   headers={"Authorization": f"Bearer {token}"})
                r.raise_for_status()
                return r.content
        except Exception as e:  # noqa: BLE001
            logger.warning("fetch_content failed for %s: %s", item_id, e)
            return None

    async def collect_documents(self, cap: int):  # -> CollectResult
        """Enumerate all sites, list files, fetch and extract text, build SourceDocs.
        Returns CollectResult. Degrades gracefully on any error (never raises)."""
        from app.connectors.sync import CollectResult
        from app.domain.chunk import SourceDoc

        brain_tenant = get_settings().brain_tenant_id
        docs: list[SourceDoc] = []
        skipped = 0
        truncated = False

        try:
            sites = await self.list_sites()
            remaining = cap
            all_files: list[tuple[str, RemoteFile]] = []  # (site_id, file)
            for site in sites:
                if remaining <= 0:
                    break
                site_files = await self.list_files(site["site_id"], max_items=remaining)
                for f in site_files:
                    all_files.append((site["site_id"], f))
                remaining -= len(site_files)

            truncated = len(all_files) >= cap

            for site_id, f in all_files:
                data = await self.fetch_content(f.drive_id, f.item_id)
                text = extract_text(data, f.mime, f.name) if data is not None else None
                if not text:
                    skipped += 1
                    continue
                now = datetime.now(UTC)
                doc = SourceDoc(
                    doc_id=f"sp:{site_id}:{f.item_id}",
                    tenant_id=brain_tenant,
                    source="sharepoint",
                    source_url=f.web_url,
                    title=f.name,
                    body=text,
                    author_id=f.author_id,
                    acl_principals=[f"{brain_tenant}:everyone"],
                    created_at=f.created_at or now,
                    modified_at=f.modified_at or now,
                    mime=f.mime or "text/plain",
                )
                docs.append(doc)
        except Exception as e:  # noqa: BLE001
            logger.warning("sharepoint collect_documents failed: %s", e)
            return CollectResult(docs=docs, skipped=skipped, truncated=truncated)

        return CollectResult(docs=docs, skipped=skipped, truncated=truncated)
