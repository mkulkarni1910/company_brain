import pytest

from app.generation.gemini import GeminiClient, _translate


def test_translate_splits_system_and_roles() -> None:
    system, contents = _translate([
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "pto?"},
    ])
    assert system == "be terse"
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert contents[0]["parts"][0]["text"] == "hi"


def _client(post):
    c = GeminiClient.__new__(GeminiClient)
    c._key = "k"
    c._model = "gemini-2.5-pro"
    c._plan_model = "gemini-2.5-flash"
    c._thinking_budget = 256
    c._base = "https://x/v1beta"

    class _Http:
        async def post(self, url, params=None, json=None):
            return post(url, params, json)

    c._http = _Http()
    return c


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


@pytest.mark.asyncio
async def test_complete_parses_text_and_sends_body() -> None:
    captured = {}

    def post(url, params, json):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return _Resp({"candidates": [{"content": {"parts": [{"text": "20 "}, {"text": "days"}]}}]})

    out = await _client(post).complete(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "pto?"}],
        max_tokens=200,
    )
    assert out == "20 days"
    # no deployment → answer path → pro model + capped thinking budget
    assert captured["url"].endswith("/models/gemini-2.5-pro:generateContent")
    assert captured["params"] == {"key": "k"}
    assert captured["json"]["systemInstruction"]["parts"][0]["text"] == "s"
    gen = captured["json"]["generationConfig"]
    assert gen["thinkingConfig"]["thinkingBudget"] == 256
    assert gen["maxOutputTokens"] == 200 + 256 + 64


@pytest.mark.asyncio
async def test_plan_step_uses_flash_with_no_thinking() -> None:
    captured = {}

    def post(url, params, json):
        captured["url"] = url
        captured["json"] = json
        return _Resp({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    await _client(post).complete(
        messages=[{"role": "user", "content": "classify"}],
        deployment="plan", max_tokens=200,
    )
    assert captured["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert captured["json"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0


@pytest.mark.asyncio
async def test_complete_empty_candidates_returns_empty() -> None:
    out = await _client(lambda u, p, j: _Resp({"candidates": []})).complete(
        messages=[{"role": "user", "content": "x"}])
    assert out == ""


@pytest.mark.asyncio
async def test_complete_raises_without_key() -> None:
    c = GeminiClient.__new__(GeminiClient)
    c._key = None
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await c.complete(messages=[{"role": "user", "content": "x"}])
