"""Identity checks for playbooks: directory-first, live Slack+Graph fallback.

resolve(email) is what request-time routing calls: a store hit costs one Redis
GET; a miss does users.lookupByEmail + two Graph calls and writes the result
through, so the next request is warm. Unknown to Entra ⇒ role 'customer'
(the spec's "rest are customers" rule); unknown to Slack ⇒ None (the flow
stops — we can't route to someone we can't reach).

Accepted trade-off: concurrent cold misses for the same email each do the live
lookup before the first write-through lands (idempotent upserts, no corruption);
the daily sync keeps the store warm, so the herd window is the first ~10s.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from app.bots.slack import slack_get
from app.config import get_settings
from app.connectors.graph import GRAPH, graph_get_json, graph_token
from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser

logger = logging.getLogger(__name__)


class DirectoryService:
    def __init__(self, *, store: DirectoryStore, token_fn=None, get_fn=None) -> None:
        self._store = store
        self._token_fn = token_fn or graph_token
        self._get_fn = get_fn or graph_get_json

    async def get_by_slack_id(self, slack_id: str | None) -> DirectoryUser | None:
        return await self._store.get_by_slack_id(slack_id)

    async def resolve(self, email: str | None) -> DirectoryUser | None:
        if not email:
            return None
        email = email.lower()
        hit = await self._store.get_by_email(email)
        if hit:
            return hit
        s = get_settings()
        body = await slack_get(s.slack_bot_token or "",
                               "users.lookupByEmail", {"email": email})
        slack_user = (body or {}).get("user") or {}
        if not slack_user.get("id"):
            return None
        user = DirectoryUser(
            email=email, slack_id=slack_user["id"],
            display_name=((slack_user.get("profile") or {}).get("real_name")
                          or slack_user.get("name")),
            role="customer", synced_at=datetime.now(UTC),
        )
        # Entra enrichment is best-effort: failures leave them a customer.
        # Guests' UPN ≠ email, so look up by mail filter, not /users/{email}.
        try:
            token = await self._token_fn(s.azure_tenant_id)
            safe = email.replace("'", "''")
            flt = quote(f"mail eq '{safe}'")
            data = await self._get_fn(
                token, f"{GRAPH}/users?$filter={flt}"
                       f"&$select=id,displayName,mail&$expand=manager($select=mail)")
            found = (data.get("value") or [None])[0]
            if found:
                user.entra_id = found.get("id")
                user.display_name = user.display_name or found.get("displayName")
                user.manager_email = (((found.get("manager") or {}).get("mail") or "")
                                      .lower() or None)
                member = await self._get_fn(
                    token, f"{GRAPH}/users/{found['id']}/memberOf?$select=displayName")
                names = {g.get("displayName") for g in member.get("value", [])}
                user.groups = [g for g in (s.entra_managers_group, s.entra_agents_group)
                               if g in names]
                user.role = ("manager" if s.entra_managers_group in names
                             else "agent" if s.entra_agents_group in names
                             else "customer")
        except Exception:  # noqa: BLE001 — enrichment must not block routing
            logger.warning("directory resolve: graph enrichment failed for %s", email)
        await self._store.upsert(user)
        return user
