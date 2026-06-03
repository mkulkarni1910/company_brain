from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from html import unescape

from app.config import get_settings
from app.connectors.graph import graph_get_json, graph_token

logger = logging.getLogger(__name__)
_GRAPH = "https://graph.microsoft.com/v1.0"
_PAGE_LIMIT = 50  # max @odata.nextLink pages for messages


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


def _parse_teams(data: dict) -> list[dict]:
    """Parse /groups response → [{team_id, name}]."""
    out = []
    for g in data.get("value", []):
        team_id = g.get("id", "")
        if not team_id:
            continue
        out.append({"team_id": team_id, "name": g.get("displayName", "")})
    return out


def _parse_channels(data: dict) -> list[dict]:
    """Parse /teams/{id}/channels response → standard-only [{channel_id, name}]."""
    out = []
    for ch in data.get("value", []):
        if ch.get("membershipType") != "standard":
            continue
        channel_id = ch.get("id", "")
        if not channel_id:
            continue
        out.append({"channel_id": channel_id, "name": ch.get("displayName", "")})
    return out


def _parse_messages(
    data: dict,
    team: str,
    channel: str,
    team_name: str,
    channel_name: str,
    substrateos_tenant: str,
) -> list:  # list[SourceDoc]
    """Parse /messages response → list[SourceDoc]. Skips system/empty messages."""
    from app.domain.chunk import SourceDoc

    now = datetime.now(UTC)
    docs = []
    for msg in data.get("value", []):
        # Skip system/event messages
        msg_type = msg.get("messageType")
        if msg_type is not None and msg_type != "message":
            continue

        body = msg.get("body") or {}
        content = body.get("content", "") or ""
        content_type = body.get("contentType", "text")

        text = _strip_html(content) if content_type == "html" else content.strip()

        if not text:
            continue

        msg_id = msg.get("id", "")
        author = ((msg.get("from") or {}).get("user") or {}).get("id")

        docs.append(SourceDoc(
            doc_id=f"teams:{team}:{channel}:{msg_id}",
            tenant_id=substrateos_tenant,
            source="teams",
            source_url=msg.get("webUrl", ""),
            title=f"{team_name} / {channel_name}",
            body=text,
            author_id=author,
            acl_principals=[f"{substrateos_tenant}:everyone"],
            created_at=_dt(msg.get("createdDateTime")) or now,
            modified_at=_dt(msg.get("lastModifiedDateTime")) or now,
            mime="text/plain",
        ))
    return docs


class TeamsConnector:
    """MS Graph Teams channel-message reader. Uses client-credentials against
    an admin-consented tenant. Never raises — degrades to empty on any error."""

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    async def _token(self) -> str:
        return await graph_token(self._tenant_id)

    async def _get_json(self, url: str) -> dict:
        return await graph_get_json(await self._token(), url)

    async def list_teams(self) -> list[dict]:
        """List all teams in the tenant. Returns [] on any error."""
        try:
            url = (
                f"{_GRAPH}/groups"
                "?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')"
                "&$select=id,displayName"
            )
            data = await self._get_json(url)
            return _parse_teams(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("list_teams failed: %s", e)
            return []

    async def list_channels(self, team_id: str) -> list[dict]:
        """List standard channels for a team. Returns [] on any error."""
        try:
            url = f"{_GRAPH}/teams/{team_id}/channels?$select=id,displayName,membershipType"
            data = await self._get_json(url)
            return _parse_channels(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("list_channels failed for team %s: %s", team_id, e)
            return []

    async def _list_messages_raw(self, team_id: str, channel_id: str) -> dict:
        """Fetch messages with @odata.nextLink paging; returns dict with 'value' list.
        Returns empty dict on any error (e.g. 403 — protected API not approved)."""
        url = f"{_GRAPH}/teams/{team_id}/channels/{channel_id}/messages"
        all_messages: list[dict] = []
        pages = 0
        try:
            while url and pages < _PAGE_LIMIT:
                data = await self._get_json(url)
                all_messages.extend(data.get("value", []))
                url = data.get("@odata.nextLink", "")
                pages += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("_list_messages_raw failed for %s/%s: %s", team_id, channel_id, e)
        return {"value": all_messages}

    async def collect_documents(self, cap: int):  # -> CollectResult
        """BFS: teams → standard channels → messages → SourceDocs.
        Caps at `cap` docs. Degrades to CollectResult() on any error (never raises)."""
        from app.connectors.sync import CollectResult

        substrateos_tenant = get_settings().substrateos_tenant_id
        docs = []
        skipped = 0
        truncated = False

        try:
            teams = await self.list_teams()
            for team in teams:
                if len(docs) >= cap:
                    truncated = True
                    break
                team_id = team["team_id"]
                team_name = team["name"]
                channels = await self.list_channels(team_id)
                for channel in channels:
                    if len(docs) >= cap:
                        truncated = True
                        break
                    channel_id = channel["channel_id"]
                    channel_name = channel["name"]
                    raw = await self._list_messages_raw(team_id, channel_id)
                    parsed = _parse_messages(
                        raw, team_id, channel_id, team_name, channel_name, substrateos_tenant
                    )
                    # Count system/empty messages as skipped
                    total_in_page = len(raw.get("value", []))
                    skipped += total_in_page - len(parsed)
                    remaining = cap - len(docs)
                    docs.extend(parsed[:remaining])
                    if len(parsed) > remaining:
                        truncated = True

        except Exception as e:  # noqa: BLE001
            logger.warning("teams collect_documents failed: %s", e)
            return CollectResult(docs=docs, skipped=skipped, truncated=truncated)

        return CollectResult(docs=docs, skipped=skipped, truncated=truncated)
