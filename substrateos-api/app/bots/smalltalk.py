"""Small-talk detection + canned intro for bot surfaces.

Greetings routed into the RAG pipeline retrieve nothing relevant and earn a
truthful-but-unhelpful "I don't have information about that." — catch them
before the orchestrator and answer with a usage intro instead.
"""

from __future__ import annotations

import re

WELCOME_TEXT = (
    "Hi! 👋 I'm SubstrateOS — I answer questions from your company's connected "
    "knowledge (SharePoint, Teams, Outlook and more), scoped to what you can "
    "access.\n\n"
    "Try asking:\n"
    "• What RFPs do we have?\n"
    "• Summarize the latest project proposal\n"
    "• Who works on procurement?"
)

# Whole-message greetings/pleasantries only — anything with real content after
# the greeting ("hello, how do I file PTO") must still reach the pipeline.
_SMALLTALK_RE = re.compile(
    r"^(hi+|hello|hey|yo|hola|namaste|greetings|good\s+(morning|afternoon|evening|day)|"
    r"thanks|thank\s+you|thx|ty|ok|okay|cool|great|nice|bye|goodbye|see\s+you|help|start)"
    r"(\s+(there|bot|team|substrateos))?"
    r"[\s!.,?~🙂😊👋]*$",
    re.IGNORECASE,
)


def is_smalltalk(text: str) -> bool:
    """True for greetings/pleasantries that should not hit the RAG pipeline."""
    return bool(_SMALLTALK_RE.match(text.strip()))
