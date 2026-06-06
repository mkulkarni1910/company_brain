"""GithubClient against a mocked GitHub REST API."""
import base64

import pytest
import respx
from httpx import Response

from app.connectors.github import (
    GithubApiError,
    GithubAuthError,
    GithubClient,
    exchange_code,
)

API = "https://api.github.com"


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_returns_token():
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=Response(200, json={"access_token": "gho_xyz", "token_type": "bearer"}))
    tok = await exchange_code(client_id="cid", client_secret="sec", code="c0de")
    assert tok == "gho_xyz"


@pytest.mark.asyncio
@respx.mock
async def test_exchange_code_error_returns_none():
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=Response(200, json={"error": "bad_verification_code"}))
    assert await exchange_code(client_id="cid", client_secret="sec", code="bad") is None


@pytest.mark.asyncio
@respx.mock
async def test_repo_operations_happy_path():
    c = GithubClient("gho_xyz")
    respx.get(f"{API}/repos/acme/policies/git/ref/heads/main").mock(
        return_value=Response(200, json={"object": {"sha": "base-sha"}}))
    respx.post(f"{API}/repos/acme/policies/git/refs").mock(
        return_value=Response(201, json={"ref": "refs/heads/substrateos/rb-1"}))
    respx.get(f"{API}/repos/acme/policies/git/trees/main", params={"recursive": "1"}).mock(
        return_value=Response(200, json={"tree": [
            {"path": "docs/refund-policy.md", "type": "blob"},
            {"path": "docs", "type": "tree"},
        ]}))
    content_b64 = base64.b64encode(b"# Refund policy\n14 days").decode()
    respx.get(f"{API}/repos/acme/policies/contents/docs/refund-policy.md").mock(
        return_value=Response(200, json={"content": content_b64, "sha": "file-sha"}))
    respx.put(f"{API}/repos/acme/policies/contents/docs/refund-policy.md").mock(
        return_value=Response(200, json={"commit": {"sha": "new"}}))
    respx.post(f"{API}/repos/acme/policies/pulls").mock(
        return_value=Response(201, json={"html_url": "https://github.com/acme/policies/pull/7"}))

    assert await c.branch_sha("acme", "policies", "main") == "base-sha"
    assert await c.create_branch("acme", "policies", "substrateos/rb-1", "base-sha") is True
    assert await c.list_paths("acme", "policies", "main") == ["docs/refund-policy.md"]
    content, sha = await c.get_file("acme", "policies", "docs/refund-policy.md", ref="main")
    assert "14 days" in content and sha == "file-sha"
    await c.put_file("acme", "policies", "docs/refund-policy.md",
                     content="# Refund policy\n30 days", message="Update window",
                     branch="substrateos/rb-1", sha="file-sha")
    url = await c.create_pr("acme", "policies", title="t", body="b",
                            head="substrateos/rb-1", base="main")
    assert url.endswith("/pull/7")


@pytest.mark.asyncio
@respx.mock
async def test_branch_collision_returns_false():
    c = GithubClient("gho_xyz")
    respx.post(f"{API}/repos/acme/policies/git/refs").mock(
        return_value=Response(422, json={"message": "Reference already exists"}))
    assert await c.create_branch("acme", "policies", "dup", "sha") is False


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_auth_error():
    c = GithubClient("gho_revoked")
    respx.get(f"{API}/repos/acme/policies/git/ref/heads/main").mock(
        return_value=Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(GithubAuthError):
        await c.branch_sha("acme", "policies", "main")


@pytest.mark.asyncio
@respx.mock
async def test_other_errors_raise_api_error():
    c = GithubClient("gho_xyz")
    respx.get(f"{API}/repos/acme/nope/git/ref/heads/main").mock(
        return_value=Response(404, json={"message": "Not Found"}))
    with pytest.raises(GithubApiError):
        await c.branch_sha("acme", "nope", "main")
