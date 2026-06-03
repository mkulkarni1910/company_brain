"""Live Fetch interface + the freshness trigger heuristic.

needs_live_fetch decides, cheaply and deterministically, whether a query is
time-sensitive enough to warrant a query-time Graph /search call. The
orchestrator's LLM plan-step (deferred) would eventually replace this heuristic.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.domain.identity import User
from app.domain.query import Candidate

# Freshness markers: presence of any of these in the query triggers Live Fetch.
_FRESHNESS_TERMS = (
    "right now", "on call", "on-call", "today", "this week", "this morning",
    "currently", "current", "latest", "recent", "recently", "now", "as of",
    "this month", "live", "up to date", "up-to-date",
)
_WORD = re.compile(r"[a-z0-9'-]+")


def needs_live_fetch(query: str) -> bool:
    q = " ".join(_WORD.findall(query.lower()))
    padded = f" {q} "
    return any(f" {term} " in padded for term in _FRESHNESS_TERMS)


class LiveFetcher(Protocol):
    async def fetch(
        self, *, query: str, user: User, user_token: str | None = None
    ) -> list[Candidate]:
        """Return fresh candidates from source systems, or [] on failure/empty."""
        ...
