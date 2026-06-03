"""Realtime + reconcile service for the Outlook connectors.

Three entry points, all best-effort (never raise into the caller):
- `bootstrap_subscriptions` — after a backfill, create per-user Graph subscriptions.
- `ingest_notifications` — handle a webhook notification batch (fetch → ACL-union → ingest).
- `run_maintenance` — renew expiring subs, reconcile joiners/leavers, delta-sweep.

ACL union: a message/event lives in N mailboxes. We keep the index-time
`acl_principals` (and the live ACL store) as the *union* of every internal owner
who holds it, so retrieval surfaces it to all participants. Each realtime/delta
update reads the existing union from the ACL store and adds its own owner.
"""
from __future__ import annotations

import logging

from app.acl.store import ACLStore, ACLStoreError
from app.config import get_settings
from app.connectors.factory import connector_for
from app.connectors.models import Connection
from app.connectors.store import ConnectionStore
from app.connectors.subscriptions import (
    SubscriptionStore,
    create_subscription,
    delete_subscription,
    parse_notification_resource,
    provider_for_resource,
    renew_subscription,
    resource_for,
)

logger = logging.getLogger(__name__)
_OUTLOOK = ("outlook_mail", "outlook_calendar")


async def _ingest_with_union(doc, owner_oid, *, pipeline, acl_store: ACLStore | None) -> None:
    """Ingest a single doc, unioning its ACL with any existing principals."""
    principals = {owner_oid, *doc.acl_principals}
    if acl_store is not None:
        try:
            existing = await acl_store.doc_principals(tenant_id=doc.tenant_id, doc_id=doc.doc_id)
            if existing:
                principals |= existing
        except ACLStoreError:
            pass  # store unreachable — proceed with what we have
    doc.acl_principals = sorted(principals)
    await pipeline.process(doc)


async def bootstrap_subscriptions(*, conn: Connection, sub_store: SubscriptionStore) -> int:
    """Create per-user change-notification subscriptions for an Outlook connection.
    Returns the count created. Safe to call repeatedly (the maintenance reconcile
    skips users that already have a live sub)."""
    if conn.type not in _OUTLOOK:
        return 0
    connector = connector_for(conn)
    users = await connector.list_users()
    existing = {(s.user_id, s.provider) for s in await sub_store.list(conn.tenant_id)}
    created = 0
    for u in users:
        if (u["user_id"], conn.type) in existing:
            continue
        rec = await create_subscription(
            tenant_id=conn.tenant_id, connection_id=conn.connection_id,
            provider=conn.type, user_id=u["user_id"],
            connected_tenant_id=conn.connected_tenant_id,
        )
        if rec is not None:
            await sub_store.put(rec)
            created += 1
    logger.info("bootstrap_subscriptions: %s subs for %s", created, conn.type)
    return created


def _find_connection(conns: list[Connection], provider: str) -> Connection | None:
    live = [c for c in conns if c.type == provider and c.status in ("live", "syncing")]
    return live[0] if live else (
        next((c for c in conns if c.type == provider), None)
    )


async def ingest_notifications(
    *, payload: dict, conn_store: ConnectionStore, pipeline, acl_store: ACLStore | None,
) -> int:
    """Process a Graph notification batch. Verifies clientState; fetches each changed
    resource and ingests it (ACL-union). Returns count ingested. Never raises."""
    expected = get_settings().graph_webhook_client_state
    brain_tenant = get_settings().brain_tenant_id
    conns = await conn_store.list_connections(brain_tenant)
    processed = 0
    for note in payload.get("value", []):
        try:
            if note.get("clientState") != expected:
                logger.warning("webhook: clientState mismatch — ignoring")
                continue
            resource = note.get("resource", "")
            provider = provider_for_resource(resource)
            if provider is None:
                continue
            user_id, res_id = parse_notification_resource(resource)
            res_id = res_id or ((note.get("resourceData") or {}).get("id"))
            if not user_id or not res_id:
                continue
            change = note.get("changeType", "")
            if change == "deleted":
                # v1 limitation: can't map the Graph resource id → doc_id for a
                # deleted item; the periodic delta/recrawl reconciles removals.
                logger.info("webhook: delete for %s/%s — deferred to reconcile", provider, res_id)
                continue
            conn = _find_connection(conns, provider)
            if conn is None:
                continue
            connector = connector_for(conn)
            doc = (
                await connector.fetch_message(user_id, res_id)
                if provider == "outlook_mail"
                else await connector.fetch_event(user_id, res_id)
            )
            if doc is None:
                continue
            await _ingest_with_union(doc, user_id, pipeline=pipeline, acl_store=acl_store)
            processed += 1
        except Exception as e:  # noqa: BLE001 — one bad note never sinks the batch
            logger.warning("webhook note failed: %s", e)
    return processed


async def run_maintenance(
    *, conn_store: ConnectionStore, sub_store: SubscriptionStore, pipeline,
    acl_store: ACLStore | None,
) -> dict:
    """Renew expiring subs, reconcile joiners/leavers, and delta-sweep each user.
    Returns a small summary dict. Never raises."""
    from datetime import UTC, datetime, timedelta

    s = get_settings()
    brain_tenant = s.brain_tenant_id
    summary = {"renewed": 0, "created": 0, "deleted": 0, "ingested": 0}
    try:
        conns = [c for c in await conn_store.list_connections(brain_tenant) if c.type in _OUTLOOK]
    except Exception as e:  # noqa: BLE001
        logger.warning("maintenance: list_connections failed: %s", e)
        return summary

    threshold = datetime.now(UTC) + timedelta(minutes=s.subscription_renew_threshold_minutes)
    for conn in conns:
        connector = connector_for(conn)
        try:
            users = await connector.list_users()
        except Exception:  # noqa: BLE001
            users = []
        user_ids = {u["user_id"] for u in users}
        subs = [x for x in await sub_store.list(brain_tenant) if x.provider == conn.type]
        have = {x.user_id for x in subs}

        # renew near-expiry subs
        for x in subs:
            if x.expiration <= threshold:
                new_exp = await renew_subscription(x.subscription_id, conn.connected_tenant_id)
                if new_exp is not None:
                    x.expiration = new_exp
                    await sub_store.put(x)
                    summary["renewed"] += 1
            # leaver cleanup
            if x.user_id not in user_ids:
                await delete_subscription(x.subscription_id, conn.connected_tenant_id)
                await sub_store.delete(brain_tenant, x.subscription_id)
                summary["deleted"] += 1

        # joiner subscriptions
        for uid in user_ids - have:
            rec = await create_subscription(
                tenant_id=brain_tenant, connection_id=conn.connection_id,
                provider=conn.type, user_id=uid, connected_tenant_id=conn.connected_tenant_id,
            )
            if rec is not None:
                await sub_store.put(rec)
                summary["created"] += 1

        # delta sweep (authoritative reconcile / safety net)
        res = resource_for(conn.type, "{uid}")  # template label for the delta key
        for uid in user_ids:
            token = await sub_store.get_delta(brain_tenant, conn.connection_id, uid, res)
            docs, link = await connector.delta(uid, token)
            for doc in docs:
                try:
                    await _ingest_with_union(doc, uid, pipeline=pipeline, acl_store=acl_store)
                    summary["ingested"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("delta ingest failed for %s: %s", doc.doc_id, e)
            if link:
                await sub_store.set_delta(brain_tenant, conn.connection_id, uid, res, link)
    return summary
