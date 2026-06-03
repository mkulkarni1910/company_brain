"""Outlook calendar connector — org-wide calendar crawl with per-participant ACLs.

App-only via client-credentials against an admin-consented tenant (Calendars.Read
+ User.Read.All). Each event is stamped with the *calendar owner's* Entra object
ID; events are deduped across attendees' calendars by `iCalUId` with an ACL union,
so an event is retrievable by its organizer and every internal attendee.
Never raises — degrades to partial/empty.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.connectors.graph import (
    GRAPH,
    _dt,
    _strip_html,
    graph_get_json,
    graph_token,
    parse_users,
)
from app.connectors.outlook_mail import _merge_into

logger = logging.getLogger(__name__)
_PAGE_LIMIT = 50

_EVENT_SELECT = (
    "$select=subject,body,bodyPreview,start,end,location,organizer,attendees,"
    "iCalUId,lastModifiedDateTime,webLink"
)


def _event_body(ev: dict) -> str:
    """Compose a searchable body: subject, agenda, location, attendees, time."""
    parts: list[str] = []
    subject = (ev.get("subject") or "").strip()
    if subject:
        parts.append(subject)

    body = ev.get("body") or {}
    content = body.get("content", "") or ""
    agenda = _strip_html(content) if body.get("contentType") == "html" else content.strip()
    if not agenda:
        agenda = (ev.get("bodyPreview") or "").strip()
    if agenda:
        parts.append(agenda)

    loc = ((ev.get("location") or {}).get("displayName") or "").strip()
    if loc:
        parts.append(f"Location: {loc}")

    names = [
        ((a.get("emailAddress") or {}).get("name")
         or (a.get("emailAddress") or {}).get("address") or "")
        for a in (ev.get("attendees") or [])
    ]
    names = [n for n in names if n]
    if names:
        parts.append("Attendees: " + ", ".join(names))

    start = ((ev.get("start") or {}).get("dateTime") or "").strip()
    end = ((ev.get("end") or {}).get("dateTime") or "").strip()
    if start:
        parts.append(f"When: {start}" + (f" – {end}" if end else ""))

    return "\n".join(parts).strip()


def _parse_events(data: dict, owner_oid: str, brain_tenant: str) -> list:  # -> list[SourceDoc]
    """Parse a /calendarView (or delta) response → list[SourceDoc], ACL'd to owner_oid.
    Skips empty events and @removed delta tombstones."""
    from app.domain.chunk import SourceDoc

    now = datetime.now(UTC)
    docs = []
    for ev in data.get("value", []):
        if "@removed" in ev:
            continue
        body = _event_body(ev)
        if not body:
            continue

        ical = ev.get("iCalUId") or ev.get("id", "")
        if not ical:
            continue
        organizer = (((ev.get("organizer") or {}).get("emailAddress")) or {}).get("address")
        start_dt = _dt((ev.get("start") or {}).get("dateTime"))

        docs.append(SourceDoc(
            doc_id=f"outlookcal:{ical}",
            tenant_id=brain_tenant,
            source="outlook_calendar",
            source_url=ev.get("webLink", ""),
            title=(ev.get("subject") or "(no subject)").strip() or "(no subject)",
            body=body,
            author_id=organizer,
            acl_principals=[owner_oid],
            created_at=start_dt or now,
            modified_at=_dt(ev.get("lastModifiedDateTime")) or start_dt or now,
            mime="text/plain",
        ))
    return docs


def _window() -> tuple[str, str]:
    """calendarView [start, end] window from config, as ISO8601 UTC strings."""
    s = get_settings()
    now = datetime.now(UTC)
    start = now - timedelta(days=s.outlook_calendar_past_days)
    end = now + timedelta(days=s.outlook_calendar_future_days)
    return start.isoformat(), end.isoformat()


class OutlookCalendarConnector:
    """MS Graph calendar reader (app-only). Never raises; degrades to empty."""

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    async def _token(self) -> str:
        return await graph_token(self._tenant_id)

    async def _get_json(self, url: str) -> dict:
        return await graph_get_json(await self._token(), url)

    async def list_users(self) -> list[dict]:
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

    async def _list_events_raw(self, user_id: str) -> dict:
        """Paged calendarView for one calendar over the window, capped per user."""
        cap = get_settings().outlook_max_per_user
        start, end = _window()
        url = (
            f"{GRAPH}/users/{user_id}/calendarView"
            f"?startDateTime={start}&endDateTime={end}&{_EVENT_SELECT}&$top=50"
        )
        collected: list[dict] = []
        pages = 0
        try:
            while url and pages < _PAGE_LIMIT and len(collected) < cap:
                data = await self._get_json(url)
                collected.extend(data.get("value", []))
                url = data.get("@odata.nextLink", "")
                pages += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("_list_events_raw failed for %s: %s", user_id, e)
        return {"value": collected[:cap]}

    async def collect_documents(self, cap: int):  # -> CollectResult
        from app.connectors.sync import CollectResult

        brain_tenant = get_settings().substrateos_tenant_id
        by_id: dict = {}
        skipped = 0
        truncated = False
        try:
            for u in await self.list_users():
                if len(by_id) >= cap:
                    truncated = True
                    break
                owner = u["user_id"]
                raw = await self._list_events_raw(owner)
                parsed = _parse_events(raw, owner, brain_tenant)
                skipped += len(raw.get("value", [])) - len(parsed)
                for doc in parsed:
                    if doc.doc_id not in by_id and len(by_id) >= cap:
                        truncated = True
                        break
                    _merge_into(by_id, [doc])
        except Exception as e:  # noqa: BLE001
            logger.warning("outlook_calendar collect_documents failed: %s", e)
        return CollectResult(docs=list(by_id.values()), skipped=skipped, truncated=truncated)

    async def delta(self, user_id: str, token: str | None = None):
        """Incremental events for one calendar. Returns (docs, new_delta_link)."""
        brain_tenant = get_settings().substrateos_tenant_id
        if token:
            url = token
        else:
            start, end = _window()
            url = (
                f"{GRAPH}/users/{user_id}/calendarView/delta"
                f"?startDateTime={start}&endDateTime={end}"
            )
        docs: list = []
        delta_link: str | None = None
        pages = 0
        try:
            while url and pages < _PAGE_LIMIT:
                data = await self._get_json(url)
                docs.extend(_parse_events(data, user_id, brain_tenant))
                nxt = data.get("@odata.nextLink")
                if nxt:
                    url = nxt
                    pages += 1
                    continue
                delta_link = data.get("@odata.deltaLink")
                break
        except Exception as e:  # noqa: BLE001
            logger.warning("outlook_calendar delta failed for %s: %s", user_id, e)
        return docs, delta_link

    async def fetch_event(self, user_id: str, event_id: str):  # -> SourceDoc | None
        brain_tenant = get_settings().substrateos_tenant_id
        try:
            data = await self._get_json(
                f"{GRAPH}/users/{user_id}/events/{event_id}?{_EVENT_SELECT}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("fetch_event failed for %s/%s: %s", user_id, event_id, e)
            return None
        docs = _parse_events({"value": [data]}, user_id, brain_tenant)
        return docs[0] if docs else None
