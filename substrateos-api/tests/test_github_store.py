"""GithubStore: repo config, per-user tokens, one-shot OAuth states."""
import pytest

from app.connectors.github_store import GithubStore
from app.connectors.models import GithubConfig


@pytest.mark.asyncio
async def test_config_roundtrip():
    store = GithubStore(client=None, force_memory=True)
    assert await store.get_config("t-test") is None
    await store.put_config("t-test", GithubConfig(owner="acme", repo="policies", base_branch="dev"))
    cfg = await store.get_config("t-test")
    assert (cfg.owner, cfg.repo, cfg.base_branch) == ("acme", "policies", "dev")


@pytest.mark.asyncio
async def test_user_token_roundtrip_and_isolation():
    store = GithubStore(client=None, force_memory=True)
    assert await store.get_user_token("t-test", "tom@x") is None
    await store.put_user_token("t-test", "tom@x", "gho_abc")
    assert await store.get_user_token("t-test", "tom@x") == "gho_abc"
    assert await store.get_user_token("t-test", "diana@x") is None


@pytest.mark.asyncio
async def test_oauth_state_is_one_shot():
    store = GithubStore(client=None, force_memory=True)
    state = await store.mint_connect_state("t-test", "tom@x")
    assert isinstance(state, str) and len(state) >= 20
    assert await store.peek_connect_state(state) == ("t-test", "tom@x")   # /start: not consumed
    assert await store.consume_connect_state(state) == ("t-test", "tom@x")  # callback: consumed
    assert await store.consume_connect_state(state) is None              # reuse rejected
    assert await store.consume_connect_state("bogus") is None
