"""Own-orders gate for customer requesters.

Order records in the corpus carry a `Customer: Name (email)` line. When the
requester's directory role is "customer", any order-record chunk whose
embedded email doesn't match theirs is dropped before it can reach the
prompt — the prompt rule (generation/prompts.requester_note_for) is the
backstop, this filter is the gate. Fail closed: an order-looking chunk with
no parseable customer email is dropped for customers. Staff (agent/manager)
and anonymous requests pass through untouched.
"""

from __future__ import annotations

import re

from app.domain.directory import DirectoryUser
from app.domain.query import Candidate

_ORDER_ID_RE = re.compile(r"Order\s*#\d+", re.IGNORECASE)
_CUSTOMER_EMAIL_RE = re.compile(r"Customer:[^\n(]*\(([^()\s]+@[^()\s]+)\)", re.IGNORECASE)


def is_order_chunk(content: str) -> bool:
    """An order record mentions an order id AND has a Customer: line."""
    return bool(_ORDER_ID_RE.search(content)) and "customer:" in content.lower()


def order_customer_email(content: str) -> str | None:
    m = _CUSTOMER_EMAIL_RE.search(content)
    return m.group(1).lower() if m else None


def scope_order_chunks(
    candidates: list[Candidate], requester: DirectoryUser | None
) -> list[Candidate]:
    if requester is None or requester.role != "customer":
        return list(candidates)
    email = (requester.email or "").lower()
    return [
        c for c in candidates
        if not is_order_chunk(c.chunk.content)
        or order_customer_email(c.chunk.content) == email
    ]
