"""Outlook mail connector — org-wide mailbox crawl with per-participant ACLs.

App-only via client-credentials against an admin-consented tenant (Mail.Read +
User.Read.All). Each message is stamped with the *mailbox owner's* Entra object
ID; because every mailbox is crawled, a message that exists in N participants'
mailboxes is deduped by `internetMessageId` and its `acl_principals` becomes the
union of those owners. Never raises — degrades to partial/empty.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.config import get_settings
from app.connectors.graph import (
    GRAPH,
    _dt,
    _strip_html,
    graph_get_json,
    graph_token,
    parse_users,
)

logger = logging.getLogger(__name__)
_PAGE_LIMIT = 50

_MSG_SELECT = (
    "$select=subject,bodyPreview,body,from,toRecipients,ccRecipients,"
    "internetMessageId,receivedDateTime,lastModifiedDateTime,webLink"
)


def _parse_messages(data: dict, owner_oid: str, brain_tenant: str) -> list:  # -> list[SourceDoc]
    """Parse a /messages (or delta) response → list[SourceDoc], ACL'd to owner_oid.
    Skips messages that have neither subject nor body. Skips @removed delta tombstones."""
    from app.domain.chunk import SourceDoc

    now = datetime.now(UTC)
    docs = []
    for msg in data.get("value", []):
        if "@removed" in msg:
            continue
        body = msg.get("body") or {}
        content = body.get("content", "") or ""
        ctype = body.get("contentType", "text")
        text = _strip_html(content) if ctype == "html" else content.strip()
        if not text:
            text = (msg.get("bodyPreview") or "").strip()
        subject = (msg.get("subject") or "").strip()
        if not text and not subject:
            continue

        imid = msg.get("internetMessageId") or f"{owner_oid}:{msg.get('id', '')}"
        sender = (((msg.get("from") or {}).get("emailAddress")) or {}).get("address")
        full_body = f"{subject}\n\n{text}".strip() if subject and text else (text or subject)

        docs.append(SourceDoc(
            doc_id=f"outlookmail:{imid}",
            tenant_id=brain_tenant,
            source="outlook_mail",
            source_url=msg.get("webLink", ""),
            title=subject or "(no subject)",
            body=full_body,
            author_id=sender,
            acl_principals=[owner_oid],
            created_at=_dt(msg.get("receivedDateTime")) or now,
            modified_at=_dt(msg.get("lastModifiedDateTime"))
            or _dt(msg.get("receivedDateTime")) or now,
            mime="text/plain",
        ))
    return docs


def _merge_into(by_id: dict, docs: list) -> None:
    """Insert docs into by_id keyed on doc_id; on collision, union acl_principals."""
    for doc in docs:
        existing = by_id.get(doc.doc_id)
        if existing is None:
            by_id[doc.doc_id] = doc
            continue
        existing.acl_principals = list(
            dict.fromkeys([*existing.acl_principals, *doc.acl_principals])
        )


class OutlookMailConnector:
    """MS Graph mailbox reader (app-only). Never raises; degrades to empty."""

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    async def _token(self) -> str:
        return await graph_token(self._tenant_id)

    async def _get_json(self, url: str) -> dict:
        return await graph_get_json(await self._token(), url)

    async def list_users(self) -> list[dict]:
        """All users in the tenant. Returns [] on any error."""
        try:
            url = f"{GRAPH}/users?$select=id,mail,userPrincipalName&$top=100"
            out: list[dict] = []
            pages = 0
            while url and pages < _PAGE_LIMIT:
                data = await self._get_json(url)
                out.extend(parse_users(data))
                url = data.get("@odata.nextLink", "")
                pages += 1
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("list_users failed: %s", e)
            return []

    async def _list_messages_raw(self, user_id: str) -> dict:
        """Paged messages for one mailbox, capped at outlook_max_per_user.
        Returns {'value': [...]}; empty on any error (e.g. 403)."""
        cap = get_settings().outlook_max_per_user
        url = f"{GRAPH}/users/{user_id}/messages?{_MSG_SELECT}&$top=50"
        collected: list[dict] = []
        pages = 0
        try:
            while url and pages < _PAGE_LIMIT and len(collected) < cap:
                data = await self._get_json(url)
                collected.extend(data.get("value", []))
                url = data.get("@odata.nextLink", "")
                pages += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("_list_messages_raw failed for %s: %s", user_id, e)
        return {"value": collected[:cap]}

    async def collect_documents(self, cap: int):  # -> CollectResult
        """BFS users → messages → deduped SourceDocs (ACL union). Caps unique docs."""
        from app.connectors.sync import CollectResult

        brain_tenant = get_settings().brain_tenant_id
        by_id: dict = {}
        skipped = 0
        truncated = False
        try:
            for u in await self.list_users():
                if len(by_id) >= cap:
                    truncated = True
                    break
                owner = u["user_id"]
                raw = await self._list_messages_raw(owner)
                parsed = _parse_messages(raw, owner, brain_tenant)
                skipped += len(raw.get("value", [])) - len(parsed)
                # respect cap on *new* unique docs (collisions only union ACLs)
                for doc in parsed:
                    if doc.doc_id not in by_id and len(by_id) >= cap:
                        truncated = True
                        break
                    _merge_into(by_id, [doc])
        except Exception as e:  # noqa: BLE001
            logger.warning("outlook_mail collect_documents failed: %s", e)
        return CollectResult(docs=list(by_id.values()), skipped=skipped, truncated=truncated)

    async def delta(self, user_id: str, token: str | None = None):
        """Incremental messages for one mailbox. Returns (docs, new_delta_link).
        `token` is a previously stored @odata.deltaLink (a full URL) or None."""
        brain_tenant = get_settings().brain_tenant_id
        url = token or f"{GRAPH}/users/{user_id}/mailFolders('inbox')/messages/delta?{_MSG_SELECT}"
        docs: list = []
        delta_link: str | None = None
        pages = 0
        try:
            while url and pages < _PAGE_LIMIT:
                data = await self._get_json(url)
                docs.extend(_parse_messages(data, user_id, brain_tenant))
                nxt = data.get("@odata.nextLink")
                if nxt:
                    url = nxt
                    pages += 1
                    continue
                delta_link = data.get("@odata.deltaLink")
                break
        except Exception as e:  # noqa: BLE001
            logger.warning("outlook_mail delta failed for %s: %s", user_id, e)
        return docs, delta_link

    async def fetch_message(self, user_id: str, message_id: str):  # -> SourceDoc | None
        """Fetch a single message (used by the realtime webhook). None on error/empty."""
        brain_tenant = get_settings().brain_tenant_id
        try:
            data = await self._get_json(
                f"{GRAPH}/users/{user_id}/messages/{message_id}?{_MSG_SELECT}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("fetch_message failed for %s/%s: %s", user_id, message_id, e)
            return None
        docs = _parse_messages({"value": [data]}, user_id, brain_tenant)
        return docs[0] if docs else None
