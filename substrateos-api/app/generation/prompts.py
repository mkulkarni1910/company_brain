from __future__ import annotations

import re

from app.domain.conversation import ConversationTurn
from app.domain.directory import DirectoryUser
from app.domain.query import Candidate, Citation

SYSTEM_PROMPT = (
    "You answer questions from the provided CONTEXT and the conversation so far. "
    "Cite every factual claim drawn from CONTEXT with bracketed indices like [1] [2]. "
    "Facts the user stated earlier in this conversation (such as their name or "
    "preferences) may be used without citations. "
    "If neither the context nor the conversation contains the answer, say "
    "'I don't have information about that.' Do not invent facts or sources."
)

_MARKER = re.compile(r"\[(\d+)\]")


def requester_note_for(requester: DirectoryUser) -> str:
    """Identity block injected into the system prompt for known requesters.
    The own-orders rule here is the BACKSTOP — the enforced gate is
    retrieval.order_scope.scope_order_chunks."""
    who = requester.display_name or requester.email
    base = f"Requester: {who} ({requester.email}), role: {requester.role}."
    if requester.role == "customer":
        return base + (
            " 'My order' or 'my refund' refers to orders belonging to that email. "
            "Never reveal another customer's order details to them; if asked, say "
            "you can only discuss their own orders."
        )
    return base + (
        " 'My …' refers to them; they may ask about any customer's order."
    )


def build_grounded_messages(
    *,
    query: str,
    candidates: list[Candidate],
    skill_prompt: str | None = None,
    history: list[ConversationTurn] | None = None,
    requester_note: str | None = None,
) -> list[dict[str, str]]:
    system = SYSTEM_PROMPT
    if skill_prompt:
        system = f"{skill_prompt}\n\n{system}"
    if requester_note:
        system = f"{system}\n\n{requester_note}"
    blocks: list[str] = []
    for i, c in enumerate(candidates, start=1):
        blocks.append(
            f"[{i}] {c.chunk.title} — {c.chunk.source_url}\n{c.chunk.content}"
        )
    user = f"QUESTION: {query}\n\nCONTEXT:\n" + "\n\n".join(blocks)
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for t in history or []:
        messages.append({"role": "user", "content": t.query})
        messages.append({"role": "assistant", "content": _MARKER.sub("", t.answer.text).strip()})
    messages.append({"role": "user", "content": user})
    return messages


def parse_citations_from_answer(answer: str, candidates: list[Candidate]) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for m in _MARKER.finditer(answer):
        idx = int(m.group(1))
        if idx < 1 or idx > len(candidates):
            continue  # orphan marker — drop silently
        c = candidates[idx - 1].chunk
        if c.chunk_id in seen:
            continue
        seen.add(c.chunk_id)
        out.append(
            Citation(
                doc_id=c.doc_id,
                chunk_id=c.chunk_id,
                source_url=c.source_url,
                title=c.title,
                snippet=c.content[:200] + ("…" if len(c.content) > 200 else ""),
            )
        )
    return out
