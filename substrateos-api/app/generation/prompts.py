from __future__ import annotations

import re

from app.domain.query import Candidate, Citation

SYSTEM_PROMPT = (
    "You answer questions strictly from the provided CONTEXT. "
    "Cite every factual claim with bracketed indices like [1] [2]. "
    "If the answer is not present in the context, say "
    "'I don't have information about that.' Do not invent facts or sources."
)


def build_grounded_messages(
    *, query: str, candidates: list[Candidate], skill_prompt: str | None = None
) -> list[dict[str, str]]:
    system = SYSTEM_PROMPT
    if skill_prompt:
        system = f"{skill_prompt}\n\n{system}"
    blocks: list[str] = []
    for i, c in enumerate(candidates, start=1):
        blocks.append(
            f"[{i}] {c.chunk.title} — {c.chunk.source_url}\n{c.chunk.content}"
        )
    user = f"QUESTION: {query}\n\nCONTEXT:\n" + "\n\n".join(blocks)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


_MARKER = re.compile(r"\[(\d+)\]")


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
