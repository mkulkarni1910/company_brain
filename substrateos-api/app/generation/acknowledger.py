"""Immediate acknowledgement — a fast, context-aware "On it…" line produced by the
fast model before the strong model generates the grounded answer.

This lowers *perceived* latency across Slack, Teams, and the web chat: the user sees
the brain name what it's about to do (the IDs, the topic) within a beat, while the
strong model (gemini-2.5-pro) does the real retrieval + grounding. It mirrors the
two-tier model routing the planner already uses (flash for the quick step, pro for
the answer). It must never block or break a query, so any failure degrades to a
deterministic template.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are SubstrateOS, a company assistant. The user just asked something that will "
    "take a few seconds to research. Reply with ONE short sentence (under 20 words) that "
    "acknowledges the request and names the concrete thing you're about to do — for "
    "example: 'On it, Tom — pulling up order #48213 and checking the refund policy…'. "
    "Greet by first name ONLY if one is given. Mention specific IDs or the topic from the "
    "question. Do NOT answer the question, do NOT promise an outcome, do NOT add anything "
    "else. End with an ellipsis (…)."
)


def first_name(name: str | None) -> str | None:
    """Best-effort first name for the greeting. Returns None for blanks, emails, or
    ids that aren't real names (so the ack just doesn't greet rather than greeting
    'bot@substrateos')."""
    if not name:
        return None
    token = name.strip().split()[0] if name.strip() else ""
    return token if token and "@" not in token and len(token) <= 40 else None


def _ensure_ellipsis(text: str) -> str:
    return text if text.endswith(("…", "...")) else text.rstrip(". ") + "…"


def _template_ack(question: str, name: str | None) -> str:
    """Deterministic fallback — pulls an order/ticket id if present, else generic."""
    who = f", {name}" if name else ""
    m = re.search(r"#\s*([A-Za-z0-9][\w-]{2,})", question)
    if m:
        return f"On it{who} — looking into #{m.group(1)} now…"
    return f"On it{who} — looking into that now…"


class Acknowledger:
    """Generates the interim 'On it…' line with the fast model, never raising."""

    def __init__(self, *, llm) -> None:
        self._llm = llm

    async def make_ack(self, question: str, name: str | None = None) -> str:
        fn = first_name(name)
        prompt = f"(asked by {fn})\n{question}" if fn else question
        try:
            text = await self._llm.complete(
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                # A non-None deployment routes GeminiClient to the fast model with
                # thinking off — the whole point is a sub-second acknowledgement.
                deployment="ack",
                temperature=0.5,
                max_tokens=60,
            )
            text = (text or "").strip().strip('"').strip()
            if not text:
                raise ValueError("empty ack")
            return _ensure_ellipsis(text)
        except Exception as e:  # noqa: BLE001
            logger.warning("Acknowledger failed; using template: %s", e)
            return _template_ack(question, fn)
