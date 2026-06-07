"""Mock Stripe refund connector — the Act step behind a clean seam.

The refund is mocked (no real Stripe call), but the act now goes through a
connector interface rather than being a narrated string in the flow, so the real
connector can drop in later with no flow change.
"""

from __future__ import annotations

from pydantic import BaseModel


class RefundResult(BaseModel):
    refund_id: str
    order_id: str
    amount_usd: float


class StripeRefundConnector:
    """Issues a (mock) refund and returns an identifiable receipt."""

    def __init__(self) -> None:
        self._seq = 90000

    async def refund(self, *, order_id: str | None, amount_usd: float | None) -> RefundResult:
        self._seq += 1
        return RefundResult(
            refund_id=f"re_{self._seq}",
            order_id=order_id or "unknown",
            amount_usd=amount_usd or 0.0,
        )
