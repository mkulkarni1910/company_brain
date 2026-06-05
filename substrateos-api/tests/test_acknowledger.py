"""Tests for the immediate-acknowledgement helper (fast-model 'On it…' line)."""

from __future__ import annotations

import pytest

from app.generation.acknowledger import Acknowledger, _template_ack, first_name


class _FakeLLM:
    """Minimal stand-in for GeminiClient.complete."""

    def __init__(self, *, reply: str | None = None, raises: bool = False) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[dict] = []

    async def complete(self, *, messages, deployment=None, temperature=0.0, max_tokens=800) -> str:
        self.calls.append({"deployment": deployment, "max_tokens": max_tokens, "messages": messages})
        if self.raises:
            raise RuntimeError("model down")
        return self.reply or ""


# ---- helpers ---------------------------------------------------------------

def test_first_name_extracts_first_token():
    assert first_name("Tom Cook") == "Tom"
    assert first_name("Priya") == "Priya"


def test_first_name_skips_blanks_emails_and_ids():
    assert first_name(None) is None
    assert first_name("   ") is None
    assert first_name("bot@substrateos") is None


def test_template_ack_pulls_order_id_and_greets():
    ack = _template_ack("refund on order #48213 please", "Tom")
    assert "Tom" in ack
    assert "#48213" in ack
    assert ack.endswith("…")


def test_template_ack_generic_without_id():
    ack = _template_ack("can we do this refund?", None)
    assert ack.endswith("…")
    assert "#" not in ack


# ---- make_ack --------------------------------------------------------------

@pytest.mark.asyncio
async def test_make_ack_uses_fast_model_and_enforces_ellipsis():
    llm = _FakeLLM(reply="On it, Tom — pulling up order #48213 and checking the refund policy")
    ack = await Acknowledger(llm=llm).make_ack(
        "Customer Priya wants a refund of $1,200 on order #48213.", name="Tom Cook"
    )
    # routed to the fast model (deployment is non-None) and ellipsis appended
    assert llm.calls[0]["deployment"] is not None
    assert ack.endswith("…")
    assert "#48213" in ack


@pytest.mark.asyncio
async def test_make_ack_falls_back_to_template_on_error():
    llm = _FakeLLM(raises=True)
    ack = await Acknowledger(llm=llm).make_ack("refund on order #48213", name="Tom")
    # never raises; deterministic template carries the id + name
    assert ack == "On it, Tom — looking into #48213 now…"


@pytest.mark.asyncio
async def test_make_ack_falls_back_on_empty_reply():
    llm = _FakeLLM(reply="   ")
    ack = await Acknowledger(llm=llm).make_ack("what is our PTO policy?", name=None)
    assert ack.endswith("…")
