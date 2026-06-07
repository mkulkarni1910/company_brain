"""slack_users_list: paginated GET wrapper over Slack users.list."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.bots.slack import slack_users_list

_URL = "https://slack.com/api/users.list"


@pytest.mark.asyncio
@respx.mock
async def test_paginates_until_cursor_empty():
    page1 = {"ok": True, "members": [{"id": "U1"}, {"id": "U2"}],
             "response_metadata": {"next_cursor": "abc"}}
    page2 = {"ok": True, "members": [{"id": "U3"}],
             "response_metadata": {"next_cursor": ""}}
    route = respx.get(_URL).mock(side_effect=[Response(200, json=page1),
                                              Response(200, json=page2)])
    members = await slack_users_list("xoxb-test")
    assert [m["id"] for m in members] == ["U1", "U2", "U3"]
    assert route.call_count == 2
    # second call carries the cursor
    assert route.calls[1].request.url.params["cursor"] == "abc"


@pytest.mark.asyncio
@respx.mock
async def test_api_error_returns_none():
    respx.get(_URL).mock(return_value=Response(200, json={"ok": False, "error": "invalid_auth"}))
    assert (await slack_users_list("xoxb-bad")) is None


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_returns_none():
    respx.get(_URL).mock(side_effect=ConnectionError)
    assert (await slack_users_list("xoxb-test")) is None


@pytest.mark.asyncio
@respx.mock
async def test_slack_call_routes_get_methods_via_get():
    """users.info & co. reject JSON POST bodies (Slack ignores the body and
    errors user_not_found) — slack_call must delegate them to slack_get."""
    from app.bots.slack import slack_call

    route = respx.get("https://slack.com/api/users.info").mock(
        return_value=Response(200, json={"ok": True, "user": {"id": "U1"}}))
    body = await slack_call("xoxb-test", "users.info", {"user": "U1"})
    assert body is not None and body["user"]["id"] == "U1"
    assert route.calls[0].request.url.params["user"] == "U1"
