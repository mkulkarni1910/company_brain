"""user_is_admin: group-claim match, cached Graph member-email fallback, fail-closed."""
import pytest

import app.api._admin_guard as guard
from app.config import get_settings
from app.domain.identity import User


class FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    async def get_json(self, key: str):
        return self.store.get(key)

    async def set_json(self, key: str, value: dict, ttl_seconds: int) -> None:
        self.store[key] = value


def _user(email: str = "diana@example.com", groups: set[str] | None = None) -> User:
    return User(user_id="u1", tenant_id="t1", email=email,
                display_name="Diana", group_ids=groups or set())


@pytest.fixture()
def _prod_like(monkeypatch) -> None:  # noqa: ANN001
    """Graph fallback only runs outside debug-auth mode (conftest turns it on)."""
    monkeypatch.setenv("ENABLE_DEBUG_AUTH", "false")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_group_claim_match_needs_no_graph(_prod_like) -> None:  # noqa: ANN001
    user = _user(groups={"Admin"})
    assert await guard.user_is_admin(user, cache=None) is True


@pytest.mark.asyncio
async def test_graph_member_email_grants(monkeypatch, _prod_like) -> None:  # noqa: ANN001
    async def fake_token(tenant):  # noqa: ANN001
        return "tok"

    async def fake_members(token, group_name, **kw):  # noqa: ANN001
        assert group_name == "Admin"
        return {"diana@example.com"}

    monkeypatch.setattr(guard, "graph_token", fake_token)
    monkeypatch.setattr(guard, "group_member_emails", fake_members)
    assert await guard.user_is_admin(_user("Diana@Example.com"), FakeCache()) is True
    assert await guard.user_is_admin(_user("tom@example.com"), FakeCache()) is False


@pytest.mark.asyncio
async def test_membership_is_cached(monkeypatch, _prod_like) -> None:  # noqa: ANN001
    calls: list[str] = []

    async def fake_token(tenant):  # noqa: ANN001
        return "tok"

    async def fake_members(token, group_name, **kw):  # noqa: ANN001
        calls.append(group_name)
        return {"diana@example.com"}

    monkeypatch.setattr(guard, "graph_token", fake_token)
    monkeypatch.setattr(guard, "group_member_emails", fake_members)
    cache = FakeCache()
    assert await guard.user_is_admin(_user(), cache) is True
    assert await guard.user_is_admin(_user(), cache) is True
    assert len(calls) == 1  # second check served from cache


@pytest.mark.asyncio
async def test_graph_failure_fails_closed_and_uncached(monkeypatch, _prod_like) -> None:  # noqa: ANN001
    async def boom(tenant):  # noqa: ANN001
        raise RuntimeError("graph down")

    monkeypatch.setattr(guard, "graph_token", boom)
    cache = FakeCache()
    assert await guard.user_is_admin(_user(), cache) is False
    assert cache.store == {}  # transient error must not pin a deny for the TTL


@pytest.mark.asyncio
async def test_no_email_is_denied(_prod_like) -> None:  # noqa: ANN001
    assert await guard.user_is_admin(_user(email=""), cache=None) is False
