"""skill_publish_dm_blocks: SME-typed text is mrkdwn-escaped, steps capped cleanly."""
from app.bots.approval_cards import skill_publish_dm_blocks


def test_card_escapes_user_text_and_caps_steps() -> None:
    card = skill_publish_dm_blocks(
        skill_name="Refunds <@U123>", slug="refunds", description="See <http://x|here> & more",
        steps=["s" * 300] * 8, submitter_name="Deepa <Rao>", run_id="RB-1")
    text = str(card)
    assert "<@U123>" not in text and "&lt;@U123&gt;" in text
    assert "<http://x|here>" not in text
    assert "&lt;Rao&gt;" in text
    steps_block = card["blocks"][2]["text"]["text"]
    assert "7." not in steps_block          # capped at 6 steps
    assert "6. " + "s" * 120 in steps_block  # per-step cap, no mid-join slice
