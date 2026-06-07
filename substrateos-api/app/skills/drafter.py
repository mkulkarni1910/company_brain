"""Plain English → a populated SkillCreate draft — the Studio's one LLM call.

The draft is a *suggestion*: the SME edits the form before submitting, and an
admin approves before anything goes live, so a mediocre draft costs nothing
but a failed draft must fail loudly (the API maps SkillDraftError to a 502
and the SME fills the form by hand).
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from app.domain.skill import SkillCreate

logger = logging.getLogger(__name__)


class SkillDraftError(RuntimeError):
    """LLM unavailable, or its reply isn't a usable skill draft."""


_SYSTEM_PROMPT = """\
You turn a subject-matter expert's plain-English description of a business
rule or process into a skill definition for SubstrateOS, the company brain.
Skills follow one shape: When → Check → Stop (a human approves if risky) →
Do it → Record.

Return ONLY a JSON object — no prose, no code fences — with exactly these keys:
  "name": short human title, e.g. "Refund approvals"
  "slug": kebab-case identifier derived from the name
  "description": 1-2 sentences — what the skill does and when to use it
  "team": the owning team inferred from the text (default "Finance")
  "steps": 3-6 short imperative strings following the When→Check→Stop→Do→Record shape
  "data_feeds": data sources the rule needs (e.g. "Orders", "Slack"); [] when none
  "system_prompt": second-person instructions the AI follows when running the
    skill, embedding every concrete threshold, limit, and rule from the text
"""


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "new-skill"


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    raise SkillDraftError("the model did not return JSON")


class SkillDrafter:
    def __init__(self, *, llm) -> None:
        self._llm = llm

    async def draft(self, text: str) -> SkillCreate:
        try:
            raw = await self._llm.complete(
                messages=[{"role": "system", "content": _SYSTEM_PROMPT},
                          {"role": "user", "content": text}],
                temperature=0.0, max_tokens=900)
        except Exception as e:  # noqa: BLE001 — any client failure is a draft failure
            raise SkillDraftError(f"draft call failed: {e}") from e
        data = _extract_json(raw)
        name = str(data.get("name") or "").strip()
        if not name:
            raise SkillDraftError("the draft is missing a name")
        try:
            return SkillCreate(
                slug=_slugify(str(data.get("slug") or name)),
                name=name,
                description=str(data.get("description") or "").strip(),
                team=str(data.get("team") or "Finance").strip(),
                run_scope="org", workflow=None, enabled=True,
                steps=[str(s) for s in (data.get("steps") or [])],
                data_feeds=[str(d) for d in (data.get("data_feeds") or [])],
                system_prompt=str(data.get("system_prompt") or "").strip(),
            )
        except ValidationError as e:
            raise SkillDraftError(f"draft failed validation: {e}") from e
