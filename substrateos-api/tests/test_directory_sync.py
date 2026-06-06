"""DirectorySync: Slack users.list + Graph users/groups → merged directory."""

from __future__ import annotations

import pytest

from app.directory.store import DirectoryStore
from app.directory.sync import DirectorySync

_GRAPH = "https://graph.microsoft.com/v1.0"

_SLACK_MEMBERS = [
    {"id": "USLACKBOT", "profile": {"email": ""}},
    {"id": "U_BOT", "is_bot": True, "profile": {"email": "bot@x"}},
    {"id": "U_GONE", "deleted": True, "profile": {"email": "gone@x"}},
    {"id": "U_TOM", "profile": {"email": "Tom@X", "real_name": "Tom Reyes"}},
    {"id": "U_DIANE", "profile": {"email": "diane@x", "real_name": "Diane Foster"}},
    {"id": "U_PRIYA", "profile": {"email": "priya@x", "real_name": "Priya Sharma"}},
]

_GRAPH_USERS = {"value": [
    {"id": "g-tom", "displayName": "Tom", "mail": "tom@x",
     "manager": {"mail": "Diane@X"}},
    {"id": "g-diane", "displayName": "Diane", "mail": "diane@x"},
    {"id": "g-manoj", "displayName": "Manoj", "mail": "manoj@x"},  # Entra-only
]}


def _graph_fake(group_pages: dict[str, dict]):
    """graph_get_json fake keyed by substring of the URL."""
    async def get(token: str, url: str) -> dict:
        if "/users?" in url:
            return _GRAPH_USERS
        if "$filter=" in url and "Managers" in url:
            return {"value": [{"id": "gid-managers"}]}
        if "$filter=" in url:
            return {"value": [{"id": "gid-agents"}]}
        if "gid-managers/members" in url:
            return group_pages["managers"]
        if "gid-agents/members" in url:
            return group_pages["agents"]
        raise AssertionError(f"unexpected graph url {url}")
    return get


async def _token(tenant_id):  # noqa: ANN001
    return "tok"


def _sync(store, *, slack=_SLACK_MEMBERS, managers=None, agents=None):
    pages = {"managers": {"value": [{"mail": m} for m in (managers or [])]},
             "agents": {"value": [{"mail": a} for a in (agents or [])]}}

    async def slack_users(token):  # noqa: ANN001
        return slack

    return DirectorySync(store=store, slack_users=slack_users,
                         token_fn=_token, get_fn=_graph_fake(pages))


@pytest.mark.asyncio
async def test_merge_roles_and_manager(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    sync = _sync(store, managers=["diane@x"], agents=["tom@x", "diane@x"])
    summary = await sync.run()

    assert summary["slack_users"] == 3      # bot, slackbot, deleted skipped
    assert summary["entra_users"] == 3
    assert summary["errors"] == []

    tom = await store.get_by_email("tom@x")
    assert tom.role == "agent" and tom.slack_id == "U_TOM"
    assert tom.manager_email == "diane@x"   # lowercased
    diane = await store.get_by_email("diane@x")
    assert diane.role == "manager"          # manager wins over agent
    assert sorted(diane.groups) == ["Managers", "Support Agent"]
    priya = await store.get_by_email("priya@x")
    assert priya.role == "customer" and priya.entra_id is None
    manoj = await store.get_by_email("manoj@x")  # Entra-only, no Slack
    assert manoj.role == "customer" and manoj.slack_id is None


@pytest.mark.asyncio
async def test_slack_failure_keeps_old_data(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    await _sync(store, managers=["diane@x"], agents=["tom@x"]).run()  # seed

    async def broken(token):  # noqa: ANN001
        return None

    sync2 = DirectorySync(store=store, slack_users=broken,
                          token_fn=_token, get_fn=_graph_fake(
                              {"managers": {"value": []}, "agents": {"value": []}}))
    summary = await sync2.run()
    assert summary["errors"] == ["slack: users.list failed"]
    assert (await store.get_by_email("tom@x")).role == "agent"  # untouched


@pytest.mark.asyncio
async def test_graph_failure_keeps_old_data(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    await _sync(store, managers=["diane@x"], agents=["tom@x"]).run()  # seed

    async def boom(token, url):  # noqa: ANN001
        raise RuntimeError("graph down")

    async def slack_users(token):  # noqa: ANN001
        return _SLACK_MEMBERS

    sync2 = DirectorySync(store=store, slack_users=slack_users,
                          token_fn=_token, get_fn=boom)
    summary = await sync2.run()
    assert len(summary["errors"]) == 1 and "graph" in summary["errors"][0]
    assert (await store.get_by_email("diane@x")).role == "manager"  # untouched
