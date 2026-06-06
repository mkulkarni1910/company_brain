"""DirectoryService.resolve: store hit → done; miss → live Slack+Graph fallback."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.directory.service import DirectoryService
from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser


def _slack_fake(known: dict[str, str]):
    async def fake(token, method, payload):  # noqa: ANN001
        assert method == "users.lookupByEmail"
        sid = known.get(payload["email"])
        if not sid:
            return None  # slack_call returns None on users_not_found
        return {"ok": True, "user": {"id": sid, "profile": {"real_name": "Live Person"}}}
    return fake


async def _token(tenant_id):  # noqa: ANN001
    return "tok"


def _graph_fake(*, found: bool, group_names: list[str], manager_mail: str | None = None):
    async def get(token, url):  # noqa: ANN001
        if "$filter=" in url:
            if not found:
                return {"value": []}
            user = {"id": "g-live", "displayName": "Live Person", "mail": "live@x"}
            if manager_mail:
                user["manager"] = {"mail": manager_mail}
            return {"value": [user]}
        if "/memberOf" in url:
            return {"value": [{"displayName": n} for n in group_names]}
        raise AssertionError(f"unexpected url {url}")
    return get


@pytest.mark.asyncio
async def test_store_hit_skips_live_lookup(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(DirectoryUser(email="tom@x", slack_id="U_TOM", role="agent"))
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=False, group_names=[]))
    with patch("app.directory.service.slack_call") as nope:
        got = await svc.resolve("TOM@X")
    assert got.role == "agent"
    nope.assert_not_called()


@pytest.mark.asyncio
async def test_miss_resolves_live_and_writes_through(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=True, group_names=["Managers"],
                                              manager_mail="Boss@X"))
    with patch("app.directory.service.slack_call", new=_slack_fake({"live@x": "U_LIVE"})):
        got = await svc.resolve("live@x")
    assert got.slack_id == "U_LIVE" and got.role == "manager"
    assert got.manager_email == "boss@x"
    # write-through: second call hits the store
    with patch("app.directory.service.slack_call") as nope:
        again = await svc.resolve("live@x")
    assert again.role == "manager"
    nope.assert_not_called()


@pytest.mark.asyncio
async def test_slack_unknown_email_returns_none(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=False, group_names=[]))
    with patch("app.directory.service.slack_call", new=_slack_fake({})):
        assert (await svc.resolve("ghost@x")) is None
    assert (await svc.resolve(None)) is None


@pytest.mark.asyncio
async def test_entra_unknown_is_customer(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=False, group_names=[]))
    with patch("app.directory.service.slack_call", new=_slack_fake({"ext@x": "U_EXT"})):
        got = await svc.resolve("ext@x")
    assert got.role == "customer" and got.entra_id is None


@pytest.mark.asyncio
async def test_graph_error_degrades_to_customer(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)

    async def boom(token, url):  # noqa: ANN001
        raise RuntimeError("graph down")

    svc = DirectoryService(store=store, token_fn=_token, get_fn=boom)
    with patch("app.directory.service.slack_call", new=_slack_fake({"x@x": "U_X"})):
        got = await svc.resolve("x@x")
    assert got is not None and got.role == "customer"


@pytest.mark.asyncio
async def test_get_by_slack_id_delegates_to_store(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    from app.config import get_settings
    get_settings.cache_clear()
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(DirectoryUser(email="d@x", slack_id="U_D", role="manager"))
    svc = DirectoryService(store=store, token_fn=_token,
                           get_fn=_graph_fake(found=False, group_names=[]))
    assert (await svc.get_by_slack_id("U_D")).role == "manager"
    assert (await svc.get_by_slack_id("U_NOPE")) is None
