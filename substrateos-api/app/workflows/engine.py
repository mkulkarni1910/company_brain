from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from app.domain.identity import User
from app.domain.workflow import RefundDecision
from app.orchestrator.timing import StageTimer

logger = logging.getLogger(__name__)

_POLICY_QUERY = "refund policy auto-approve limits manager approval"

DECISION_PROMPT = (
    "You are SubStrateOS running the Acme refund playbook (refund_v1). "
    "Use ONLY the provided context documents (order records and the refund policy) to "
    "evaluate the refund request. Extract the facts and decide whether the refund can be "
    "auto-approved under the policy. Compute the order age in days from the order date "
    "and today's date when the age is not stated explicitly.\n"
    "Respond ONLY with valid JSON, no other text:\n"
    '{"found": true, "order_id": "...", "customer": "...", "amount_usd": 0, '
    '"order_age_days": 0, "policy_limit_usd": 0, "policy_limit_days": 0, '
    '"auto_approve": true, "reasoning": "one sentence citing the policy"}\n'
    'If the order cannot be found in the context documents, respond with '
    '{"found": false, "reasoning": "..."}.'
)


class RefundEngineError(Exception):
    """The LLM reply could not be parsed into a RefundDecision."""


class RefundEngine:
    """Gathers grounded facts and makes the (LLM-driven) refund decision."""

    def __init__(self, *, retriever, llm) -> None:
        self._retriever = retriever
        self._llm = llm

    async def evaluate(self, text: str, *, user: User) -> RefundDecision:
        timer = StageTimer()
        order_hits = await self._retriever.retrieve(query=text, user=user, k=6, timer=timer)
        policy_hits = await self._retriever.retrieve(
            query=_POLICY_QUERY, user=user, k=4, timer=timer
        )
        seen: set[str] = set()
        parts: list[str] = []
        for cand in [*order_hits, *policy_hits]:
            ch = cand.chunk
            if ch.chunk_id in seen:
                continue
            seen.add(ch.chunk_id)
            parts.append(f"[{ch.title}]\n{ch.content}")
        context = "\n\n".join(parts[:8]) or "(no documents found)"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        messages = [
            {"role": "system", "content": DECISION_PROMPT},
            {"role": "user", "content": (
                f"Today's date: {today}\n\nContext documents:\n{context}\n\n"
                f"Refund request: {text}"
            )},
        ]
        raw = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=500)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("Refund engine: no JSON in LLM reply: %r", raw[:200])
            raise RefundEngineError("no JSON in LLM reply")
        try:
            return RefundDecision.model_validate(json.loads(match.group(0)))
        except Exception as e:  # noqa: BLE001
            logger.warning("Refund engine: unparseable decision: %s", e)
            raise RefundEngineError(str(e)) from e
