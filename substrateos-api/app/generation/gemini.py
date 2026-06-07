"""Gemini 2.5 Pro generation client (Google AI Studio / generativelanguage API).

Drop-in for the generation LLM: exposes the same `complete(*, messages, ...)` shape
the orchestrator + planner already call, so embeddings stay on Azure OpenAI while
answer generation moves to Gemini. Uses httpx directly (no extra SDK dependency).
"""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

# Gemini 2.5 is a "thinking" model: thinking tokens count against maxOutputTokens AND
# dominate latency (a default Pro call can spend ~1300 thinking tokens / ~13s for a
# 40-token answer). We cap the budget per call and route the plan step to flash.


def _translate(messages: list[dict[str, str]]) -> tuple[str | None, list[dict]]:
    """OpenAI-style messages → (system_instruction, Gemini contents)."""
    system: str | None = None
    contents: list[dict] = []
    for m in messages:
        role = m.get("role")
        text = m.get("content", "")
        if role == "system":
            system = f"{system}\n{text}" if system else text
        else:
            contents.append(
                {"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]}
            )
    return system, contents


class GeminiClient:
    def __init__(self) -> None:
        s = get_settings()
        self._key = s.gemini_api_key
        self._model = s.gemini_model
        self._plan_model = s.gemini_plan_model
        self._ack_model = s.gemini_ack_model
        self._thinking_budget = s.gemini_thinking_budget
        self._base = s.gemini_endpoint.rstrip("/")
        self._http = httpx.AsyncClient(timeout=s.gemini_timeout_s)

    async def aclose(self) -> None:
        await self._http.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),  # only transient HTTP, not config errors
    )
    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        deployment: str | None = None,  # accepted for interface parity; ignored
        temperature: float = 0.0,
        max_tokens: int = 800,
    ) -> str:
        if not self._key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        # The plan step (callers pass a `deployment`) is a quick classifier → use the
        # fast model with thinking off. Answers use the configured model with a capped
        # thinking budget. Thinking tokens count against maxOutputTokens, so size the
        # cap to fit both the thinking budget and the visible answer.
        is_plan = deployment is not None
        # Every deployment-tagged call is a quick step on a fast model with thinking
        # off: the ack line on the ack model, the planner/skill-router on the plan
        # model (both default to gemini-3.1-flash-lite, but stay separately tunable).
        # Only the untagged answer path uses the strong model + capped thinking budget.
        if deployment == "ack":
            model = self._ack_model
        else:
            model = self._plan_model if is_plan else self._model
        thinking = 0 if is_plan else self._thinking_budget
        system, contents = _translate(messages)
        body: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens + thinking + 64,
                "thinkingConfig": {"thinkingBudget": thinking},
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        resp = await self._http.post(
            f"{self._base}/models/{model}:generateContent",
            params={"key": self._key},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()
