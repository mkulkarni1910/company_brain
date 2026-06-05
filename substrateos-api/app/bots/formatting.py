"""Surface-aware rendering of a grounded answer.

The answer text is model **markdown** — which the web chat renders richly via
react-markdown. But Slack *mrkdwn* and Teams *Adaptive Cards* are NOT markdown:
Slack wants ``*bold*`` / ``<url|text>`` / ``•`` and has no headers; Adaptive Card
TextBlocks support a small markdown subset but no headers or tables. The bot layer
already knows which surface a message is going to, so it just calls the matching
renderer here. Web is unchanged (full markdown).
"""

from __future__ import annotations

import re

from app.domain.query import Answer, Citation

_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _superscript(n: int) -> str:
    return str(n).translate(_SUP)


def _degrade_tables(text: str) -> str:
    """GFM tables render in neither Slack nor Teams. Drop the ``|---|`` separator
    row and collapse pipes into spaced columns so the data stays readable."""
    out: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?", s):
            continue  # alignment/separator row
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|")]
            out.append("   ".join(cells))
        else:
            out.append(line)
    return "\n".join(out)


def _headers_to_bold(text: str, bold: str) -> str:
    """ATX headers (``## Heading``) → a bold line; neither surface renders #."""
    return re.sub(
        r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$",
        lambda m: f"{bold}{m.group(1).strip()}{bold}",
        text,
    )


# ── Teams ────────────────────────────────────────────────────────────────────

def to_teams_text(text: str) -> str:
    """Adaptive Card TextBlock markdown: keep bold/italic/links/bullets, convert
    headers to bold, degrade tables."""
    return _headers_to_bold(_degrade_tables(text), "**").strip()


# ── Slack ────────────────────────────────────────────────────────────────────

def _link_citations(text: str, citations: list[Citation]) -> str:
    """Inline ``[n]`` markers → a clickable superscript linked to the source."""
    def repl(m: re.Match) -> str:
        n = int(m.group(1))
        if 1 <= n <= len(citations) and citations[n - 1].source_url:
            return f"<{citations[n - 1].source_url}|{_superscript(n)}>"
        return _superscript(n)
    return re.sub(r"\[(\d{1,2})\]", repl, text)


def to_slack_mrkdwn(text: str, citations: list[Citation] | None = None) -> str:
    """Convert model markdown to Slack mrkdwn."""
    citations = citations or []
    text = _degrade_tables(text)
    text = _headers_to_bold(text, "*")
    # citations first, so the [n] brackets aren't mistaken for a markdown link
    text = _link_citations(text, citations)
    # markdown links [text](url) → <url|text>
    text = re.sub(r"\[([^\]]+)\]\((\S+?)\)", r"<\2|\1>", text)
    # bold: **x** / __x__ → *x*  (Slack bold is a single asterisk)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"*\1*", text)
    # unordered list markers → •
    text = re.sub(r"(?m)^(\s*)[-*+]\s+", r"\1• ", text)
    return text.strip()


def _chunk(text: str, size: int = 2900) -> list[str]:
    """Slack section text caps at 3000 chars — split on paragraph boundaries."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    cur = ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > size:
            chunks.append(cur.rstrip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        chunks.append(cur.rstrip())
    return chunks or [text[:size]]


def answer_to_slack_blocks(answer: Answer) -> list[dict]:
    """Render an Answer as a Slack Block Kit card. Short, citation-less replies
    (the immediate ack, smalltalk) get just the body — no divider/sources/footer."""
    body = to_slack_mrkdwn(answer.text, answer.citations)
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in _chunk(body)
    ]
    if answer.citations:
        n = len(answer.citations)
        blocks.append({"type": "divider"})
        # Sources as Block Kit url buttons (parity with the Teams Adaptive Card).
        blocks.append({"type": "actions", "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f"{_superscript(i + 1)} {c.title[:40]}"[:75]},
                "url": c.source_url,
                "action_id": f"src_{i}",
            }
            for i, c in enumerate(answer.citations[:5])
        ]})
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"grounded · {n} source{'' if n == 1 else 's'}"}
        ]})
    return blocks
