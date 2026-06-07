"""Own-orders gate: customers only ever see their own order records."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.directory import DirectoryUser
from app.domain.query import Candidate
from app.retrieval.order_scope import (
    is_order_chunk,
    order_customer_email,
    scope_order_chunks,
)

_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")
_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     manager_email="diana@x", groups=["Support Agent"], role="agent")

_PRIYA_ORDER = ("# Order #48213\n\n- **Customer:** Priya Sharma (priya@x)\n"
                "- **Order total:** $1,200.00\n")
_MARCUS_ORDER = ("# Order #48190\n\n- **Customer:** Marcus Lee (marcus.lee@example.com)\n"
                 "- **Order total:** $89.00\n")
_ORPHAN_ORDER = "# Order #99999\n\n- **Customer:** Unknown Person\n- **Order total:** $10\n"
_POLICY = "# Refund Policy\n\nAuto-approve refunds up to $500 within 30 days.\n"


def _cand(content: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(chunk=Chunk(
        chunk_id=f"c#{hash(content) & 0xffff}", doc_id="d", tenant_id="t-test",
        source="uploaded", source_url="local://d", title="t", content=content,
        acl_principals=["t-test:everyone"], created_at=now, modified_at=now,
        chunk_index=0,
    ))


def test_is_order_chunk_detection():
    assert is_order_chunk(_PRIYA_ORDER) is True
    assert is_order_chunk(_ORPHAN_ORDER) is True
    assert is_order_chunk(_POLICY) is False


def test_order_customer_email_extraction():
    assert order_customer_email(_PRIYA_ORDER) == "priya@x"
    assert order_customer_email(_MARCUS_ORDER) == "marcus.lee@example.com"
    assert order_customer_email(_ORPHAN_ORDER) is None
    assert order_customer_email(_POLICY) is None


def test_customer_keeps_own_order_and_policy_drops_others():
    cands = [_cand(_PRIYA_ORDER), _cand(_MARCUS_ORDER), _cand(_POLICY)]
    out = scope_order_chunks(cands, _PRIYA)
    contents = [c.chunk.content for c in out]
    assert _PRIYA_ORDER in contents and _POLICY in contents
    assert _MARCUS_ORDER not in contents


def test_fail_closed_on_unparseable_order_for_customer():
    out = scope_order_chunks([_cand(_ORPHAN_ORDER)], _PRIYA)
    assert out == []


def test_staff_and_anonymous_pass_through():
    cands = [_cand(_PRIYA_ORDER), _cand(_MARCUS_ORDER), _cand(_ORPHAN_ORDER)]
    assert len(scope_order_chunks(cands, _TOM)) == 3
    assert len(scope_order_chunks(cands, None)) == 3


def test_email_match_is_case_insensitive():
    upper = _PRIYA_ORDER.replace("(priya@x)", "(PRIYA@X)")
    out = scope_order_chunks([_cand(upper)], _PRIYA)
    assert len(out) == 1
