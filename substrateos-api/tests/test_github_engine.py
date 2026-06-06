"""PrDraftEngine: pick target file from the repo tree, then draft the edit."""
import json

import pytest

from app.connectors.models import GithubConfig
from app.workflows.github_engine import PrDraftEngine

CFG = GithubConfig(owner="acme", repo="policies", base_branch="main")


class _FakeClient:
    def __init__(self):
        self.paths = ["docs/refund-policy.md", "README.md"]

    async def list_paths(self, owner, repo, branch, **kw):
        return self.paths

    async def get_file(self, owner, repo, path, *, ref):
        return "# Refund policy\nWindow: 14 days\n", "file-sha"


class _FakeLLM:
    """Returns queued replies in order."""
    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls: list[list[dict]] = []

    async def complete(self, *, messages, temperature=0.0, max_tokens=0):
        self.calls.append(messages)
        return self._replies.pop(0)


@pytest.mark.asyncio
async def test_draft_happy_path():
    llm = _FakeLLM(
        json.dumps({"found": True, "path": "docs/refund-policy.md", "reasoning": "policy doc"}),
        json.dumps({"new_content": "# Refund policy\nWindow: 30 days\n",
                    "summary": "window 14→30 days", "title": "Update refund window",
                    "body": "Extends the refund window."}),
    )
    engine = PrDraftEngine(llm=llm)
    draft, clarify = await engine.draft("update the refund window to 30 days",
                                        client=_FakeClient(), config=CFG)
    assert clarify is None
    assert draft.path == "docs/refund-policy.md"
    assert draft.base_sha == "file-sha"
    assert "30 days" in draft.new_content
    assert draft.title == "Update refund window"


@pytest.mark.asyncio
async def test_no_target_file_returns_clarify_question():
    llm = _FakeLLM(json.dumps({"found": False, "question": "Which document should I change?"}))
    engine = PrDraftEngine(llm=llm)
    draft, clarify = await engine.draft("change the thing", client=_FakeClient(), config=CFG)
    assert draft is None
    assert "Which document" in clarify


@pytest.mark.asyncio
async def test_unparseable_llm_reply_returns_clarify():
    llm = _FakeLLM("I cannot answer in JSON, sorry")
    engine = PrDraftEngine(llm=llm)
    draft, clarify = await engine.draft("update policy", client=_FakeClient(), config=CFG)
    assert draft is None
    assert clarify  # stops and asks — never guesses


@pytest.mark.asyncio
async def test_edit_step_can_refuse():
    llm = _FakeLLM(
        json.dumps({"found": True, "path": "docs/refund-policy.md"}),
        json.dumps({"new_content": "", "question": "The policy has three windows — which one?"}),
    )
    engine = PrDraftEngine(llm=llm)
    draft, clarify = await engine.draft("update the window", client=_FakeClient(), config=CFG)
    assert draft is None
    assert "three windows" in clarify
