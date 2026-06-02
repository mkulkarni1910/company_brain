"""Microsoft Graph change-notification (webhook) subscriptions for realtime
Outlook mail/calendar, plus the Redis store that tracks them and per-user delta
tokens. Subscriptions are per-user-resource (/users/{id}/messages|events); the
maintenance pass renews them and the delta sweep reconciles any gaps.

All network/store calls degrade gracefully — they never raise into the runner.
"""
from __future__ import annotations

import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.connectors.graph import GRAPH, graph_token
from app.connectors.models import SubscriptionRecord

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)

# provider → the Graph resource template + which change source it maps to
_RESOURCE = {
    "outlook_mail": "users/{uid}/messages",
    "outlook_calendar": "users/{uid}/events",
}


def resource_for(provider: str, user_id: str) -> str:
    return _RESOURCE[provider].format(uid=user_id)


def provider_for_resource(resource: str) -> str | None:
    r = resource.lower()
    if "/messages" in r or r.endswith("messages"):
        return "outlook_mail"
    if "/events" in r or r.endswith("events"):
        return "outlook_calendar"
    return None


def parse_notification_resource(resource: str) -> tuple[str | None, str | None]:
    """('users/{uid}/messages/{mid}') → (user_id, resource_id). Either may be None."""
    m = re.search(r"[Uu]sers[/(]'?([^/')]+)'?[/)]", resource)
    user_id = m.group(1) if m else None
    last = resource.rstrip("/").split("/")[-1]
    res_id = last if last and last.lower() not in ("messages", "events") else None
    return user_id, res_id


def _expiry_iso(minutes: int) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=minutes)
    return exp.isoformat().replace("+00:00", "Z")


async def create_subscription(
    *, tenant_id: str, connection_id: str, provider: str, user_id: str,
    connected_tenant_id: str | None,
) -> SubscriptionRecord | None:
    """POST /subscriptions for one user resource. Returns the record or None on error."""
    s = get_settings()
    resource = resource_for(provider, user_id)
    body = {
        "changeType": "created,updated,deleted",
        "notificationUrl": f"{s.brain_api_base_url.rstrip('/')}/admin/connections/webhook",
        "resource": resource,
        "expirationDateTime": _expiry_iso(s.subscription_ttl_minutes),
        "clientState": s.graph_webhook_client_state,
    }
    try:
        token = await graph_token(connected_tenant_id)
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.post(f"{GRAPH}/subscriptions", json=body,
                                headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("create_subscription failed for %s %s: %s", provider, user_id, e)
        return None
    exp = data.get("expirationDateTime")
    return SubscriptionRecord(
        subscription_id=data.get("id", ""),
        tenant_id=tenant_id,
        connection_id=connection_id,
        provider=provider,  # type: ignore[arg-type]
        user_id=user_id,
        resource=resource,
        expiration=_parse_dt(exp) or datetime.now(UTC),
    )


async def renew_subscription(subscription_id: str, connected_tenant_id: str | None) -> datetime | None:
    """PATCH a subscription's expiration. Returns the new expiry or None on error."""
    s = get_settings()
    try:
        token = await graph_token(connected_tenant_id)
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.patch(
                f"{GRAPH}/subscriptions/{subscription_id}",
                json={"expirationDateTime": _expiry_iso(s.subscription_ttl_minutes)},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            return _parse_dt(r.json().get("expirationDateTime"))
    except Exception as e:  # noqa: BLE001
        logger.warning("renew_subscription failed for %s: %s", subscription_id, e)
        return None


async def delete_subscription(subscription_id: str, connected_tenant_id: str | None) -> None:
    try:
        token = await graph_token(connected_tenant_id)
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.delete(f"{GRAPH}/subscriptions/{subscription_id}",
                                  headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.warning("delete_subscription failed for %s: %s", subscription_id, e)


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _sub_key(tenant: str) -> str: return f"connector:subs:{tenant}"
def _delta_key(tenant: str, conn: str, user: str, res: str) -> str:
    return f"connector:delta:{tenant}:{conn}:{user}:{res}"


class SubscriptionStore:
    """Redis-backed subscription records + per-user delta tokens. No-op without Redis."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        if client is not None:
            self._r = client
            return
        s = get_settings()
        if not s.azure_redis_host:
            self._r = None
            return
        self._r = redis.Redis(host=s.azure_redis_host, port=s.azure_redis_port,
            ssl=s.azure_redis_ssl, password=s.redis_key, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2)

    async def aclose(self) -> None:
        if self._r is None:
            return
        with contextlib.suppress(Exception):
            await self._r.aclose()

    async def put(self, rec: SubscriptionRecord) -> None:
        if self._r is None:
            return
        with contextlib.suppress(*_ERRORS):
            await self._r.hset(_sub_key(rec.tenant_id), rec.subscription_id, rec.model_dump_json())

    async def list(self, tenant: str) -> list[SubscriptionRecord]:
        if self._r is None:
            return []
        try:
            raw = await self._r.hgetall(_sub_key(tenant))
        except _ERRORS as e:
            logger.warning("subs list failed: %s", e)
            return []
        out: list[SubscriptionRecord] = []
        for v in raw.values():
            with contextlib.suppress(Exception):
                out.append(SubscriptionRecord.model_validate_json(v))
        return out

    async def delete(self, tenant: str, subscription_id: str) -> None:
        if self._r is None:
            return
        with contextlib.suppress(*_ERRORS):
            await self._r.hdel(_sub_key(tenant), subscription_id)

    async def list_expiring(self, tenant: str, before: datetime) -> list[SubscriptionRecord]:
        return [s for s in await self.list(tenant) if s.expiration <= before]

    async def get_delta(self, tenant: str, conn: str, user: str, res: str) -> str | None:
        if self._r is None:
            return None
        try:
            return await self._r.get(_delta_key(tenant, conn, user, res))
        except _ERRORS as e:
            logger.warning("get_delta failed: %s", e)
            return None

    async def set_delta(self, tenant: str, conn: str, user: str, res: str, link: str) -> None:
        if self._r is None:
            return
        with contextlib.suppress(*_ERRORS):
            await self._r.set(_delta_key(tenant, conn, user, res), link)


class CosmosSubscriptionStore:
    """Cosmos (Gremlin) subscription records + delta tokens, for deployments without
    Redis (e.g. India). Mirrors SubscriptionStore's interface over the shared people
    graph (label-separated vertices, JSON `data`, tenant partition). Never raises.
    The Gremlin client is shared/owned elsewhere — not closed here."""

    _SUB = "cbrain_subscription"
    _DELTA = "cbrain_delta"

    def __init__(self, graph) -> None:
        self._g = graph  # async submit(query, bindings) -> list (PeopleGraphClient)

    async def aclose(self) -> None:
        return

    async def _upsert(self, label: str, keyprop: str, keyval: str, tenant: str, data: str) -> None:
        try:
            await self._g.submit(
                f"g.V().has('{label}','{keyprop}', k).has('tenant_id', tid).fold()"
                f".coalesce(unfold(),"
                f" addV('{label}').property('{keyprop}', k).property('tenant_id', tid))"
                f".property('data', d)",
                {"k": keyval, "tid": tenant, "d": data},
            )
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning("cosmos sub upsert %s failed: %s", label, e)

    async def put(self, rec: SubscriptionRecord) -> None:
        await self._upsert(self._SUB, "sid", rec.subscription_id, rec.tenant_id, rec.model_dump_json())

    async def list(self, tenant: str) -> list[SubscriptionRecord]:
        try:
            rows = await self._g.submit(
                f"g.V().has('{self._SUB}','tenant_id', tid).values('data')", {"tid": tenant})
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos sub list failed: %s", e)
            return []
        out: list[SubscriptionRecord] = []
        for data in rows:
            with contextlib.suppress(Exception):
                out.append(SubscriptionRecord.model_validate_json(data))
        return out

    async def delete(self, tenant: str, subscription_id: str) -> None:
        with contextlib.suppress(Exception):
            await self._g.submit(
                f"g.V().has('{self._SUB}','sid', k).has('tenant_id', tid).drop()",
                {"k": subscription_id, "tid": tenant})

    async def list_expiring(self, tenant: str, before: datetime) -> list[SubscriptionRecord]:
        return [s for s in await self.list(tenant) if s.expiration <= before]

    async def get_delta(self, tenant: str, conn: str, user: str, res: str) -> str | None:
        dk = f"{conn}:{user}:{res}"
        try:
            rows = await self._g.submit(
                f"g.V().has('{self._DELTA}','dk', k).has('tenant_id', tid).values('data')",
                {"k": dk, "tid": tenant})
        except Exception as e:  # noqa: BLE001
            logger.warning("cosmos get_delta failed: %s", e)
            return None
        return rows[0] if rows else None

    async def set_delta(self, tenant: str, conn: str, user: str, res: str, link: str) -> None:
        await self._upsert(self._DELTA, "dk", f"{conn}:{user}:{res}", tenant, link)
