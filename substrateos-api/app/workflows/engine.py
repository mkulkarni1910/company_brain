from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.workflow import RefundDecision
from app.orchestrator.timing import StageTimer
from app.retrieval.order_scope import scope_order_chunks

logger = logging.getLogger(__name__)

_POLICY_QUERY = "refund policy auto-approve limits manager approval"

DECISION_PROMPT = (
    "You are SubstrateOS running the Acme refund playbook (refund_v1). "
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

    async def evaluate(self, text: str, *, user: User,
                       requester: DirectoryUser | None = None) -> RefundDecision:
        timer = StageTimer()
        order_query = text
        if requester is not None:
            who = f"{requester.display_name or ''} {requester.email or ''}".strip()
            order_query = f"{text} customer {who}"
        order_hits = await self._retriever.retrieve(
            query=order_query, user=user, k=6, timer=timer
        )
        order_hits = scope_order_chunks(list(order_hits), requester)
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
        requester_line = ""
        if requester is not None:
            requester_line = (
                f"Requester: {requester.display_name or requester.email} "
                f"({requester.email}), role {requester.role} — "
                "'my order' refers to them.\n"
            )
        messages = [
            {"role": "system", "content": DECISION_PROMPT},
            {"role": "user", "content": (
                f"Today's date: {today}\n{requester_line}\n"
                f"Context documents:\n{context}\n\n"
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
