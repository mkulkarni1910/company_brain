"""Tests for surface-aware rendering (Slack mrkdwn / Teams) of answers."""

from __future__ import annotations

from app.bots.formatting import (
    answer_to_slack_blocks,
    to_slack_mrkdwn,
    to_teams_text,
)
from app.domain.query import Answer, Citation


def _cite(n: int) -> Citation:
    return Citation(
        doc_id=f"d{n}", chunk_id=f"c{n}",
        source_url=f"https://x/{n}", title=f"Doc {n}", snippet="…",
    )


# ── Slack mrkdwn conversion ───────────────────────────────────────────────────

def test_slack_bold_and_headers():
    out = to_slack_mrkdwn("## PTO\nYou get **20 days** off.")
    assert "*PTO*" in out          # header → bold line
    assert "*20 days*" in out      # ** → *
    assert "**" not in out
    assert "#" not in out


def test_slack_links_become_anglebracket():
    out = to_slack_mrkdwn("See the [HR portal](https://hr.example.com).")
    assert "<https://hr.example.com|HR portal>" in out
    assert "](" not in out


def test_slack_bullets_become_dots():
    out = to_slack_mrkdwn("- one\n- two")
    assert "• one" in out and "• two" in out
    assert "\n- " not in out


def test_slack_citations_link_to_sources():
    out = to_slack_mrkdwn("Accrue 20 days [1] per year.", [_cite(1)])
    assert "<https://x/1|¹>" in out
    assert "[1]" not in out


def test_slack_table_degrades_readably():
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    out = to_slack_mrkdwn(md)
    assert "---" not in out
    assert "A" in out and "B" in out and "1" in out


# ── Teams conversion ──────────────────────────────────────────────────────────

def test_teams_headers_become_bold_keeps_links():
    out = to_teams_text("# Title\nSee [docs](https://d).")
    assert "**Title**" in out      # header → bold (Teams supports **)
    assert "[docs](https://d)" in out  # links kept
    assert not out.lstrip().startswith("#")


# ── Slack block assembly ──────────────────────────────────────────────────────

def test_slack_blocks_with_citations_have_divider_buttons_footer():
    ans = Answer(text="Answer body [1].", citations=[_cite(1), _cite(2)], query_id="q")
    blocks = answer_to_slack_blocks(ans)
    types = [b["type"] for b in blocks]
    assert types[0] == "section"
    assert "divider" in types
    # sources are url buttons (parity with Teams)
    actions = next(b for b in blocks if b["type"] == "actions")
    btns = actions["elements"]
    assert len(btns) == 2
    assert all(b["type"] == "button" and b["url"] for b in btns)
    assert "Doc 1" in btns[0]["text"]["text"] and "Doc 2" in btns[1]["text"]["text"]
    # last context block is the "grounded · N sources" footer
    assert blocks[-1]["type"] == "context"
    assert "2 sources" in blocks[-1]["elements"][0]["text"]


def test_slack_blocks_without_citations_are_plain():
    # the immediate ack / smalltalk: just the body, no divider/sources/footer
    ack = Answer(text="On it — looking into that now…", citations=[], query_id="ack")
    blocks = answer_to_slack_blocks(ack)
    assert [b["type"] for b in blocks] == ["section"]
