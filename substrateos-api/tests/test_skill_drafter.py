"""SkillDrafter: one LLM call → validated SkillCreate; garbage → SkillDraftError."""
import json

import pytest

from app.skills.drafter import SkillDrafter, SkillDraftError

_GOOD = {
    "name": "Refund approvals", "slug": "Refund Approvals!",  # messy slug on purpose
    "description": "Auto-approve refunds under $500 and 30 days.",
    "team": "Finance",
    "steps": ["Check amount and age", "Stop if over limit", "Record the outcome"],
    "data_feeds": ["Orders"],
    "system_prompt": "You enforce the refund policy: under $500 and 30 days auto-approves.",
}


class _LLM:
    def __init__(self, reply):
        self._reply = reply
        self.calls: list[dict] = []

    async def complete(self, *, messages, deployment=None, temperature=0.0, max_tokens=800):
        self.calls.append({"messages": messages, "temperature": temperature})
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


@pytest.mark.asyncio
async def test_draft_happy_path_normalizes_slug() -> None:
    drafter = SkillDrafter(llm=_LLM(json.dumps(_GOOD)))
    skill = await drafter.draft("Refunds under $500 and 30 days auto-approve.")
    assert skill.slug == "refund-approvals"
    assert skill.name == "Refund approvals"
    assert skill.enabled is True and skill.run_scope == "org" and skill.workflow is None
    assert skill.steps == _GOOD["steps"]


@pytest.mark.asyncio
async def test_draft_strips_code_fences() -> None:
    drafter = SkillDrafter(llm=_LLM("```json\n" + json.dumps(_GOOD) + "\n```"))
    skill = await drafter.draft("whatever")
    assert skill.slug == "refund-approvals"


@pytest.mark.asyncio
async def test_non_json_reply_raises() -> None:
    drafter = SkillDrafter(llm=_LLM("I'm sorry, I can't help with that."))
    with pytest.raises(SkillDraftError):
        await drafter.draft("whatever")


@pytest.mark.asyncio
async def test_llm_failure_raises() -> None:
    drafter = SkillDrafter(llm=_LLM(RuntimeError("model down")))
    with pytest.raises(SkillDraftError):
        await drafter.draft("whatever")


@pytest.mark.asyncio
async def test_missing_name_raises() -> None:
    drafter = SkillDrafter(llm=_LLM(json.dumps({"description": "x"})))
    with pytest.raises(SkillDraftError):
        await drafter.draft("whatever")


@pytest.mark.asyncio
async def test_missing_system_prompt_raises() -> None:
    drafter = SkillDrafter(llm=_LLM(json.dumps({**_GOOD, "system_prompt": ""})))
    with pytest.raises(SkillDraftError):
        await drafter.draft("whatever")
