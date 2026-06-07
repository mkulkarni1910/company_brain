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
    # Arbitrary, distinct sentinels per tier so each dispatch branch is verifiable
    # independently (in prod several tiers may map to one model, e.g. flash-lite).
    c._model = "answer-model"
    c._plan_model = "plan-router-model"
    c._ack_model = "ack-model"
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
    # no deployment → answer path → strong answer model + capped thinking budget
    assert captured["url"].endswith("/models/answer-model:generateContent")
    assert captured["params"] == {"key": "k"}
    assert captured["json"]["systemInstruction"]["parts"][0]["text"] == "s"
    gen = captured["json"]["generationConfig"]
    assert gen["thinkingConfig"]["thinkingBudget"] == 256
    assert gen["maxOutputTokens"] == 200 + 256 + 64


@pytest.mark.asyncio
async def test_plan_step_routes_to_plan_model_with_no_thinking() -> None:
    captured = {}

    def post(url, params, json):
        captured["url"] = url
        captured["json"] = json
        return _Resp({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    await _client(post).complete(
        messages=[{"role": "user", "content": "classify"}],
        deployment="plan", max_tokens=200,
    )
    assert captured["url"].endswith("/models/plan-router-model:generateContent")
    assert captured["json"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0


@pytest.mark.asyncio
async def test_ack_deployment_uses_ack_model() -> None:
    # "ack" routes to the ack model; other deployment-tagged tiers route to the
    # plan model (in prod both default to gemini-3.1-flash-lite, separately tunable).
    captured = {}

    def post(url, params, json):
        captured["url"] = url
        captured["json"] = json
        return _Resp({"candidates": [{"content": {"parts": [{"text": "On it…"}]}}]})

    await _client(post).complete(
        messages=[{"role": "user", "content": "refund please"}],
        deployment="ack", max_tokens=60,
    )
    assert captured["url"].endswith("/models/ack-model:generateContent")
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
