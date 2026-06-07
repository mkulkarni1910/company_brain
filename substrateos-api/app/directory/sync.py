"""Daily Slack+Entra directory sync.

Pulls every Slack workspace member and every Entra user (+manager, +the two
role groups), merges on lowercase email, and upserts DirectoryUser records.
Idempotent and fail-soft: any fetch failure aborts the upsert phase so the
previous day's data survives — never wipe on error.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.bots.slack import slack_users_list
from app.config import get_settings
from app.connectors.graph import GRAPH, graph_get_json, graph_token, group_member_emails
from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser

logger = logging.getLogger(__name__)


class DirectorySync:
    """Fetch + merge + upsert. Fetchers are injectable for tests."""

    def __init__(self, *, store: DirectoryStore,
                 slack_users=None, token_fn=None, get_fn=None) -> None:
        self._store = store
        self._slack_users = slack_users or slack_users_list
        self._token_fn = token_fn or graph_token
        self._get_fn = get_fn or graph_get_json

    async def run(self) -> dict:
        s = get_settings()
        summary: dict = {"slack_users": 0, "entra_users": 0, "matched": 0,
                         "managers": 0, "agents": 0, "customers": 0, "errors": []}

        members = await self._slack_users(s.slack_bot_token or "")
        if members is None:
            summary["errors"].append("slack: users.list failed")
            return summary
        slack_by_email: dict[str, dict] = {}
        for m in members:
            if m.get("deleted") or m.get("is_bot") or m.get("id") == "USLACKBOT":
                continue
            email = ((m.get("profile") or {}).get("email") or "").lower()
            if email:
                slack_by_email[email] = {
                    "slack_id": m.get("id"),
                    "display_name": (m.get("profile") or {}).get("real_name") or m.get("name"),
                }
        summary["slack_users"] = len(slack_by_email)

        try:
            token = await self._token_fn(s.azure_tenant_id)
            entra_by_email = await self._fetch_entra_users(token)
            manager_emails = await self._group_member_emails(token, s.entra_managers_group)
            agent_emails = await self._group_member_emails(token, s.entra_agents_group)
        except Exception as e:  # noqa: BLE001 — keep yesterday's data on any Graph failure
            logger.warning("directory sync: graph fetch failed: %s", e)
            summary["errors"].append(f"graph: {e}")
            return summary
        summary["entra_users"] = len(entra_by_email)

        now = datetime.now(UTC)
        for email in set(slack_by_email) | set(entra_by_email):
            sl = slack_by_email.get(email) or {}
            en = entra_by_email.get(email) or {}
            role = ("manager" if email in manager_emails
                    else "agent" if email in agent_emails else "customer")
            groups = [g for g, in_group in (
                (s.entra_managers_group, email in manager_emails),
                (s.entra_agents_group, email in agent_emails),
            ) if in_group]
            await self._store.upsert(DirectoryUser(
                email=email, slack_id=sl.get("slack_id"),
                display_name=sl.get("display_name") or en.get("display_name"),
                entra_id=en.get("entra_id"), manager_email=en.get("manager_email"),
                groups=groups, role=role, synced_at=now,
            ))
            summary[role + "s"] += 1
            if sl and en:
                summary["matched"] += 1
        logger.info("directory sync: %s", summary)
        return summary

    async def _fetch_entra_users(self, token: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        url = f"{GRAPH}/users?$select=id,displayName,mail&$expand=manager($select=mail)"
        while url:
            data = await self._get_fn(token, url)
            for u in data.get("value", []):
                email = (u.get("mail") or "").lower()
                if not email:
                    continue
                out[email] = {
                    "entra_id": u.get("id"),
                    "display_name": u.get("displayName"),
                    "manager_email": ((u.get("manager") or {}).get("mail") or "").lower() or None,
                }
            url = data.get("@odata.nextLink")
        return out

    async def _group_member_emails(self, token: str, group_name: str) -> set[str]:
        return await group_member_emails(token, group_name, get_fn=self._get_fn)
