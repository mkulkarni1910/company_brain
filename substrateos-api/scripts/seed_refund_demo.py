"""Seed the Refund Experience demo: mock orders + refund policy into AI Search,
and create/refresh the `refund` workflow skill.

Usage:
    python scripts/seed_refund_demo.py --api http://localhost:8000 \
        --admin-key $ADMIN_KEY --tenant $TENANT_ID

The API must be running with real AI Search + OpenAI credentials (ingest embeds).
Idempotent: re-ingesting the same doc_id replaces its chunks; the skill is
created or patched.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx

POLICY_BODY = """# Acme Refund Policy (refund-policy v3)

**Version 3 · Owner: D. Rao · Applies to all customer orders**

## Auto-approval rule

A refund may be **auto-approved** by SubstrateOS only when **both** conditions hold:

- Refund amount is **$500 or less**, AND
- The order was placed **30 days ago or less**.

## Manager approval

Any refund **over $500** or on an order **older than 30 days** is OVER LIMIT and
**must be approved by a Support Manager** before it is issued. The approval and
the approver's identity are recorded in the audit log with the decision.

## Process

1. Verify who is asking (requester identity).
2. Gather the facts: order, amount, order age, customer.
3. Check the rules above.
4. Within limits → issue the refund and confirm to the customer.
5. Over limits → hold the action and route to a Support Manager for approval.
"""

ORDER_48213_BODY = """# Order #48213

- **Customer:** Priya Sharma (priya.sharma@example.com)
- **Order ID:** 48213
- **Order total:** $1,200.00
- **Order date:** {d48213}
- **Status:** Delivered
- **Items:** Aurora X2 Standing Desk ($950.00), Ergo Pro Chair Mat ($250.00)
- **Payment method:** Visa ending 4421
- **Notes:** Customer reports the desk motor failed after six weeks of use.
"""

ORDER_48190_BODY = """# Order #48190

- **Customer:** Marcus Lee (marcus.lee@example.com)
- **Order ID:** 48190
- **Order total:** $89.00
- **Order date:** {d48190}
- **Status:** Delivered
- **Items:** Lumen Desk Lamp ($89.00)
- **Payment method:** Mastercard ending 9087
- **Notes:** Customer says the lamp arrived with a cracked shade.
"""

SKILL = {
    "slug": "refund",
    "name": "Refund Processing",
    "description": (
        "Handles customer refund requests end-to-end: looks up the order, checks the "
        "refund policy, auto-approves within limits or routes to a Support Manager "
        "for approval. Use for any question about refunding a customer order."
    ),
    "team": "Support",
    "run_scope": "org",
    "enabled": True,
    "workflow": "refund",
    "steps": [
        "Who's asking — verify the requester",
        "Gather the facts — order, amount, age, customer, policy",
        "Check the rules — auto-approve limits ($500 / 30 days)",
        "Decide — issue the refund or route to a Support Manager",
    ],
    "data_feeds": ["Orders", "Refund policy"],
    "system_prompt": (
        "You are the Acme refund playbook (refund_v1). Ground every statement in the "
        "retrieved order records and refund policy. Never promise a refund that the "
        "policy does not allow."
    ),
}


APPROVAL_SKILL = {
    "slug": "request-approval",
    "name": "Request Approval",
    "description": (
        "Routes any action that needs human sign-off to the right approver. Use when a "
        "user asks to send something for approval, get something approved, or escalate a "
        "request to their manager (e.g. 'send this discount exception to my manager', "
        "'get this signed off'). Resolves the requester's manager, sends an Approve/Reject "
        "card, and records every step. NOT for answering questions — only for routing "
        "approvals."
    ),
    "team": "Platform",
    "run_scope": "org",
    "enabled": True,
    "workflow": "approval",
    "steps": [
        "Who's asking — verify the requester",
        "Capture the request — what needs sign-off",
        "Find the approver — the requester's manager",
        "Stop — route an Approve/Reject card and wait for a human",
    ],
    "data_feeds": ["People graph (manager edges)"],
    "system_prompt": "Route the request for human approval. Never act before sign-off.",
}


def _doc(doc_id: str, title: str, body: str, tenant: str) -> dict:
    now = datetime.now(UTC)
    return {
        "doc_id": doc_id,
        "tenant_id": tenant,
        "source": "uploaded",
        "source_url": f"https://internal.acme.example/{doc_id}",
        "title": title,
        "body": body,
        "author_id": None,
        "acl_principals": [f"{tenant}:everyone"],
        "created_at": now.isoformat(),
        "modified_at": now.isoformat(),
        "mime": "text/markdown",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default=os.environ.get("API_BASE", "http://localhost:8000"))
    p.add_argument("--admin-key", default=os.environ.get("ADMIN_KEY", ""))
    p.add_argument("--tenant", default=os.environ.get("TENANT_ID", ""))
    args = p.parse_args()
    if not args.admin_key or not args.tenant:
        print("--admin-key and --tenant are required (or ADMIN_KEY / TENANT_ID env)")
        return 1
    headers = {"x-admin-key": args.admin_key}
    today = datetime.now(UTC)
    d48213 = (today - timedelta(days=45)).strftime("%Y-%m-%d")
    d48190 = (today - timedelta(days=12)).strftime("%Y-%m-%d")

    docs = [
        _doc("refund-policy-v3", "Acme Refund Policy v3", POLICY_BODY, args.tenant),
        _doc(
            "order-48213",
            "Order #48213 — Priya Sharma",
            ORDER_48213_BODY.format(d48213=d48213),
            args.tenant,
        ),
        _doc(
            "order-48190",
            "Order #48190 — Marcus Lee",
            ORDER_48190_BODY.format(d48190=d48190),
            args.tenant,
        ),
    ]
    with httpx.Client(base_url=args.api, headers=headers, timeout=120.0) as client:
        for doc in docs:
            r = client.post("/admin/ingest", json=doc)
            r.raise_for_status()
            print(f"ingested {doc['doc_id']}: {r.json()}")

        skills = client.get("/admin/skills").json()
        for skill in (SKILL, APPROVAL_SKILL):
            existing = next((s for s in skills if s.get("slug") == skill["slug"]), None)
            if existing:
                patch = {k: v for k, v in skill.items() if k != "slug"}
                r = client.patch(f"/admin/skills/{existing['id']}", json=patch)
                r.raise_for_status()
                print(f"updated skill {skill['slug']} (id={existing['id']})")
            else:
                r = client.post("/admin/skills", json=skill)
                r.raise_for_status()
                print(f"created skill {skill['slug']} (id={r.json().get('id')})")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
