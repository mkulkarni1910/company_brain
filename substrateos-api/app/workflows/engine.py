from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.workflow import RefundFacts
from app.orchestrator.timing import StageTimer
from app.retrieval.order_scope import scope_order_chunks

logger = logging.getLogger(__name__)

# The model EXTRACTS FACTS only. It does not decide the verdict — the deterministic
# PolicyEngine does, in code, outside the model (the governed-act-layer invariant).
FACTS_PROMPT = (
    "You are SubstrateOS running the Acme refund playbook. "
    "Use ONLY the provided context documents (order records) to EXTRACT FACTS about "
    "the refund request. You do NOT decide whether the refund is approved — a "
    "deterministic policy engine does that, in code. Compute the order age in days "
    "from the order date and today's date when it is not stated explicitly.\n"
    "Respond ONLY with valid JSON, no other text:\n"
    '{"found": true, "order_id": "...", "customer": "...", "customer_email": "...", '
    '"amount_usd": 0, "order_age_days": 0, '
    '"reasoning": "one sentence describing the extracted facts"}\n'
    "Copy the customer's email address from the order record into customer_email "
    "when present, else use null. "
    "If the order cannot be found in the context documents, respond with "
    '{"found": false, "reasoning": "..."}.'
)


class RefundEngineError(Exception):
    """The LLM reply could not be parsed into RefundFacts."""


class RefundEngine:
    """Gathers grounded order context and extracts typed facts (no verdict)."""

    def __init__(self, *, retriever, llm) -> None:
        self._retriever = retriever
        self._llm = llm

    async def evaluate(self, text: str, *, user: User,
                       requester: DirectoryUser | None = None) -> RefundFacts:
        timer = StageTimer()
        order_query = text
        if requester is not None:
            who = f"{requester.display_name or ''} {requester.email or ''}".strip()
            order_query = f"{text} customer {who}"
        order_hits = await self._retriever.retrieve(
            query=order_query, user=user, k=6, timer=timer
        )
        order_hits = scope_order_chunks(list(order_hits), requester)
        seen: set[str] = set()
        parts: list[str] = []
        for cand in order_hits:
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
            {"role": "system", "content": FACTS_PROMPT},
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
            return RefundFacts.model_validate(json.loads(match.group(0)))
        except Exception as e:  # noqa: BLE001
            logger.warning("Refund engine: unparseable facts: %s", e)
            raise RefundEngineError(str(e)) from e
