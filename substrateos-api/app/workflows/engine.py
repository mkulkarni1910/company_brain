from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from app.domain.identity import User
from app.domain.workflow import RefundFacts
from app.orchestrator.timing import StageTimer

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
    '{"found": true, "order_id": "...", "customer": "...", "amount_usd": 0, '
    '"order_age_days": 0, "reasoning": "one sentence describing the extracted facts"}\n'
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

    async def evaluate(self, text: str, *, user: User) -> RefundFacts:
        timer = StageTimer()
        hits = await self._retriever.retrieve(query=text, user=user, k=6, timer=timer)
        seen: set[str] = set()
        parts: list[str] = []
        for cand in hits:
            ch = cand.chunk
            if ch.chunk_id in seen:
                continue
            seen.add(ch.chunk_id)
            parts.append(f"[{ch.title}]\n{ch.content}")
        context = "\n\n".join(parts[:8]) or "(no documents found)"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        messages = [
            {"role": "system", "content": FACTS_PROMPT},
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
            return RefundFacts.model_validate(json.loads(match.group(0)))
        except Exception as e:  # noqa: BLE001
            logger.warning("Refund engine: unparseable facts: %s", e)
            raise RefundEngineError(str(e)) from e
