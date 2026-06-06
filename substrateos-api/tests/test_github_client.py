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


# Finding 1 — get_file on a directory path must raise GithubApiError
@pytest.mark.asyncio
@respx.mock
async def test_get_file_on_directory_raises_api_error():
    c = GithubClient("gho_xyz")
    respx.get(f"{API}/repos/acme/policies/contents/docs").mock(
        return_value=Response(200, json=[{"path": "docs/a.md"}, {"path": "docs/b.md"}]))
    with pytest.raises(GithubApiError, match="directory"):
        await c.get_file("acme", "policies", "docs", ref="main")


# Finding 2 — put_file and create_pr must send the correct JSON bodies
@pytest.mark.asyncio
@respx.mock
async def test_put_file_and_create_pr_send_correct_bodies():
    import json
    c = GithubClient("gho_xyz")
    put_route = respx.put(f"{API}/repos/acme/policies/contents/docs/refund-policy.md").mock(
        return_value=Response(200, json={"commit": {"sha": "new"}}))
    pr_route = respx.post(f"{API}/repos/acme/policies/pulls").mock(
        return_value=Response(201, json={"html_url": "https://github.com/acme/policies/pull/7"}))

    await c.put_file("acme", "policies", "docs/refund-policy.md",
                     content="# 30 days", message="Update window",
                     branch="substrateos/rb-1", sha="file-sha")
    sent = json.loads(put_route.calls.last.request.content)
    assert sent["message"] == "Update window" and sent["branch"] == "substrateos/rb-1"
    assert sent["sha"] == "file-sha"
    assert base64.b64decode(sent["content"]).decode() == "# 30 days"

    await c.create_pr("acme", "policies", title="T", body="B",
                      head="substrateos/rb-1", base="main")
    pr_sent = json.loads(pr_route.calls.last.request.content)
    assert pr_sent == {"title": "T", "body": "B", "head": "substrateos/rb-1", "base": "main"}
