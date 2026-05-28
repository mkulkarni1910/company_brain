"""Structure-aware markdown chunker.

Strategy:
1. Split text by H1/H2 headings into sections.
2. For each section, if token count <= max_tokens, emit as one chunk.
3. Otherwise, split paragraphs greedily into chunks of <= max_tokens with overlap.

Tokens counted via tiktoken (cl100k_base — close enough for text-embedding-3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")
_HEADING = re.compile(r"^(#{1,2})\s+.+$", re.MULTILINE)


@dataclass(frozen=True)
class ChunkText:
    content: str
    chunk_index: int


def _token_count(s: str) -> int:
    return len(_ENC.encode(s))


def _split_sections(md: str) -> list[str]:
    # Split before each H1/H2 heading; keep the heading with its body
    indices = [m.start() for m in _HEADING.finditer(md)]
    if not indices:
        return [md] if md.strip() else []
    sections: list[str] = []
    starts = [0, *indices] if indices[0] != 0 else indices
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(md)
        sec = md[start:end].strip()
        if sec:
            sections.append(sec)
    return sections


def _greedy_chunk(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for para in paragraphs:
        pt = _token_count(para)
        if buf and buf_tokens + pt > max_tokens:
            chunks.append("\n\n".join(buf))
            # overlap: keep tail of previous chunk
            tail: list[str] = []
            tail_tokens = 0
            for p in reversed(buf):
                pt2 = _token_count(p)
                if tail_tokens + pt2 > overlap_tokens:
                    break
                tail.insert(0, p)
                tail_tokens += pt2
            buf = tail.copy()
            buf_tokens = tail_tokens
        # paragraph itself larger than max_tokens — hard-split by words
        if pt > max_tokens:
            words = para.split()
            cur: list[str] = []
            cur_tokens = 0
            for w in words:
                wt = _token_count(w + " ")
                if cur_tokens + wt > max_tokens:
                    chunks.append((" ".join(buf) + "\n\n" if buf else "") + " ".join(cur))
                    cur = cur[-max(1, overlap_tokens // 2):]
                    cur_tokens = _token_count(" ".join(cur))
                    buf = []
                    buf_tokens = 0
                cur.append(w)
                cur_tokens += wt
            if cur:
                buf.append(" ".join(cur))
                buf_tokens += _token_count(" ".join(cur))
        else:
            buf.append(para)
            buf_tokens += pt
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def chunk_markdown(md: str, max_tokens: int = 600, overlap_tokens: int = 75) -> list[ChunkText]:
    out: list[ChunkText] = []
    idx = 0
    for section in _split_sections(md):
        if _token_count(section) <= max_tokens:
            out.append(ChunkText(content=section, chunk_index=idx))
            idx += 1
            continue
        for piece in _greedy_chunk(section, max_tokens, overlap_tokens):
            out.append(ChunkText(content=piece, chunk_index=idx))
            idx += 1
    return out
