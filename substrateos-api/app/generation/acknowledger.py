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
    "You are SubstrateOS, a company assistant. The user just sent a message that will take "
    "a few seconds to handle. Reply with ONE short sentence (under 20 words) acknowledging "
    "it and saying what you're about to do. End with an ellipsis (…). "
    "CRITICAL: only reference details — names, order numbers, topics — that ACTUALLY APPEAR "
    "in the user's message. NEVER invent an order number, a policy, a topic, or a task. "
    "GREETING RULE: greet by name ONLY when an '(asked by …)' line names the asker, or the "
    "user introduces THEMSELVES ('my name is …'). People mentioned inside the message "
    "(customers, colleagues) are third parties — NEVER greet or address them; with no asker "
    "name, use no name at all. If the message has no specific task (a greeting, small talk), "
    "keep it generic. VARY your opening phrase between requests — 'Checking…', "
    "'Looking into…', 'Pulling up…', 'Sure —', 'Right —', 'On it —' — never settle on one "
    "opener. Do NOT answer, do NOT promise an outcome, do NOT copy these examples "
    "literally — they only show the format:\n"
    "  • 'what is the status of my order?' → 'Checking your order status…'\n"
    "  • 'refund $1,200 on order 48213' → 'Pulling up order #48213…'\n"
    "  • '(asked by Tom) can we help Priya with this refund' → 'Sure, Tom — looking into Priya's refund…'\n"
    "  • 'can we help Priya with this refund' → 'Looking into Priya's refund…'\n"
    "  • 'my name is Priya' → 'Thanks, Priya — one moment…'\n"
    "  • 'hi there' → 'Hi! One sec…'"
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
