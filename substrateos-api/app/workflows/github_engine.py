"""Drafts the PR content for the raise-PR playbook in two grounded LLM steps:
1) pick the target file from the real repo tree; 2) given the file's actual
current content, produce the full new content + PR metadata. If either step
can't ground the change, it returns a clarifying question — never guesses."""

from __future__ import annotations

import json
import logging
import re

from app.connectors.models import GithubConfig
from app.domain.workflow import PrDraft

logger = logging.getLogger(__name__)

_FALLBACK_QUESTION = (
    "I couldn't work out which file this change applies to — "
    "tell me the file (or the doc name) and I'll draft the PR."
)

TARGET_PROMPT = (
    "You are SubstrateOS running the raise-PR playbook. Given a user's change request "
    "and the list of file paths in the repository, pick the SINGLE file the change applies to. "
    "Respond ONLY with valid JSON, no other text:\n"
    '{"found": true, "path": "docs/example.md", "reasoning": "one sentence"}\n'
    "If no file clearly matches, respond with "
    '{"found": false, "question": "one clarifying question for the user"}.'
)

EDIT_PROMPT = (
    "You are SubstrateOS drafting a pull request. Given the CURRENT content of the file and "
    "the requested change, produce the FULL new file content with the change applied — keep "
    "all unrelated content byte-identical. Respond ONLY with valid JSON, no other text:\n"
    '{"new_content": "...", "summary": "one line describing what changed", '
    '"title": "PR title", "body": "PR description in markdown"}\n'
    "If the request cannot be applied to this file or is ambiguous, respond with "
    '{"new_content": "", "question": "one clarifying question for the user"}.'
)


def _json_or_none(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


class PrDraftEngine:
    def __init__(self, *, llm) -> None:
        self._llm = llm

    async def draft(self, text: str, *, client, config: GithubConfig
                    ) -> tuple[PrDraft | None, str | None]:
        """Returns (draft, clarify_question) — exactly one is non-None."""
        paths = await client.list_paths(config.owner, config.repo, config.base_branch)
        raw = await self._llm.complete(
            messages=[
                {"role": "system", "content": TARGET_PROMPT},
                {"role": "user", "content": (
                    "Repository files:\n" + "\n".join(paths) +
                    f"\n\nChange request: {text}"
                )},
            ],
            temperature=0.0, max_tokens=300,
        )
        target = _json_or_none(raw)
        if not target:
            logger.warning("raise-pr target step: unparseable reply %r", raw[:200])
            return None, _FALLBACK_QUESTION
        if not target.get("found") or not target.get("path"):
            return None, target.get("question") or _FALLBACK_QUESTION

        path = target["path"]
        content, sha = await client.get_file(config.owner, config.repo, path,
                                             ref=config.base_branch)
        raw = await self._llm.complete(
            messages=[
                {"role": "system", "content": EDIT_PROMPT},
                {"role": "user", "content": (
                    f"File: {path}\n\nCurrent content:\n{content}\n\n"
                    f"Change request: {text}"
                )},
            ],
            temperature=0.0, max_tokens=8000,
        )
        edit = _json_or_none(raw)
        if not edit:
            logger.warning("raise-pr edit step: unparseable reply %r", raw[:200])
            return None, _FALLBACK_QUESTION
        if not edit.get("new_content"):
            return None, edit.get("question") or _FALLBACK_QUESTION
        return PrDraft(
            path=path, base_sha=sha, new_content=edit["new_content"],
            summary=edit.get("summary") or "Drafted change",
            title=edit.get("title") or f"Update {path}",
            body=edit.get("body") or "",
        ), None
