"""user_is_sme: group-claim match, Graph member-email fallback, fail-closed, admin implies SME."""
import pytest

import app.api._sme_guard as guard
from app.config import get_settings
from app.domain.identity import User


class FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    async def get_json(self, key: str):
        return self.store.get(key)

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        self.store[key] = value


def _user(email: str = "deepa@example.com", groups: set[str] | None = None) -> User:
    return User(user_id="u1", tenant_id="t1", email=email,
                display_name="Deepa", group_ids=groups or set())


@pytest.fixture()
def _prod_like(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "false")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_group_claim_match_needs_no_graph(_prod_like) -> None:  # noqa: ANN001
    assert await guard.user_is_sme(_user(groups={"Finance SME"}), cache=None) is True


@pytest.mark.asyncio
async def test_admin_group_implies_sme(_prod_like, monkeypatch) -> None:  # noqa: ANN001
    # SME graph lookup misses; the Admin group claim still grants access.
    async def no_members(token, group_name, **kw):  # noqa: ANN001
        return set()

    async def fake_token(tenant):  # noqa: ANN001
        return "tok"

    monkeypatch.setattr(guard, "graph_token", fake_token)
    monkeypatch.setattr(guard, "group_member_emails", no_members)
    assert await guard.user_is_sme(_user(groups={"Admin"}), FakeCache()) is True


@pytest.mark.asyncio
async def test_graph_member_email_grants(monkeypatch, _prod_like) -> None:  # noqa: ANN001
    async def fake_token(tenant):  # noqa: ANN001
        return "tok"

    async def fake_members(token, group_name, **kw):  # noqa: ANN001
        return {"deepa@example.com"} if group_name == "Finance SME" else set()

    monkeypatch.setattr(guard, "graph_token", fake_token)
    monkeypatch.setattr(guard, "group_member_emails", fake_members)
    # _admin_guard does its own Graph round-trip for the admin fallback — patch it too.
    import app.api._admin_guard as admin_guard
    monkeypatch.setattr(admin_guard, "graph_token", fake_token)
    monkeypatch.setattr(admin_guard, "group_member_emails", fake_members)
    assert await guard.user_is_sme(_user("Deepa@Example.com"), FakeCache()) is True
    assert await guard.user_is_sme(_user("tom@example.com"), FakeCache()) is False


@pytest.mark.asyncio
async def test_graph_failure_fails_closed_and_uncached(monkeypatch, _prod_like) -> None:  # noqa: ANN001
    async def boom(tenant):  # noqa: ANN001
        raise RuntimeError("graph down")

    monkeypatch.setattr(guard, "graph_token", boom)
    import app.api._admin_guard as admin_guard
    monkeypatch.setattr(admin_guard, "graph_token", boom)
    cache = FakeCache()
    assert await guard.user_is_sme(_user(), cache) is False
    assert cache.store == {}  # transient error must not pin a deny for the TTL
