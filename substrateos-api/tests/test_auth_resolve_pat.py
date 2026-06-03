import pytest

from app.api._auth_resolve import resolve_user
from app.config import get_settings
from app.domain.identity import User


class FakeTokenStore:
    def __init__(self, user):
        self._user = user
    async def resolve(self, plaintext):
        return self._user if plaintext == "sbx_live_good" else None


def _pilot_on():
    s = get_settings()
    s.pilot_single_tenant = True
    s.substrateos_tenant_id = "t-eval"


@pytest.mark.asyncio
async def test_pat_bearer_resolves_via_token_store_and_pilot_maps(monkeypatch) -> None:
    _pilot_on()
    raw = User(user_id="u9", tenant_id="aad-guid", email="u9@x", display_name="U9", group_ids=set())
    store = FakeTokenStore(raw)
    user = await resolve_user(
        easy_auth=None, authorization="Bearer sbx_live_good",
        debug_header=None, token_store=store,
    )
    assert user.user_id == "u9"
    assert user.tenant_id == "t-eval"                      # pilot remap applied
    assert "t-eval:everyone" in user.group_ids


@pytest.mark.asyncio
async def test_bad_pat_is_401() -> None:
    from fastapi import HTTPException
    store = FakeTokenStore(None)
    with pytest.raises(HTTPException) as ei:
        await resolve_user(easy_auth=None, authorization="Bearer sbx_live_bad",
                           debug_header=None, token_store=store)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_non_pat_bearer_skips_token_store(monkeypatch) -> None:
    # A non-sbx_ bearer must NOT hit the token store; it falls through to JWT.
    seen = {"called": False}

    class Spy(FakeTokenStore):
        async def resolve(self, plaintext):
            seen["called"] = True
            return None

    async def fake_jwt(token):
        return User(user_id="jwtuser", tenant_id="t-eval", email="j@x",
                    display_name="J", group_ids=set())

    monkeypatch.setattr("app.api._auth_resolve.user_from_bearer", fake_jwt)
    user = await resolve_user(easy_auth=None, authorization="Bearer eyJhbGci...",
                              debug_header=None, token_store=Spy(None))
    assert user.user_id == "jwtuser"
    assert seen["called"] is False
