"""GitHub REST client for the raise-PR tool. Every call carries the requesting
user's own OAuth token — attribution is structural: GitHub sees who acted."""

from __future__ import annotations

import base64
import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_OAUTH_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN = "https://github.com/login/oauth/access_token"


class GithubApiError(Exception):
    """GitHub returned an unexpected error."""


class GithubAuthError(GithubApiError):
    """Token rejected (revoked/expired) — the user must reconnect."""


async def exchange_code(*, client_id: str, client_secret: str, code: str) -> str | None:
    """Authorization-code → user access token. None on any failure."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GITHUB_OAUTH_TOKEN,
                json={"client_id": client_id, "client_secret": client_secret, "code": code},
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
        data = resp.json()
        return data.get("access_token")
    except Exception:  # noqa: BLE001
        logger.exception("github code exchange failed")
        return None


class GithubClient:
    def __init__(self, token: str, *, timeout: float = 10.0) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(base_url=GITHUB_API, headers=self._headers,
                                     timeout=self._timeout) as client:
            resp = await client.request(method, path, **kw)
        if resp.status_code == 401:
            raise GithubAuthError("GitHub rejected the token (reconnect needed)")
        return resp

    async def branch_sha(self, owner: str, repo: str, branch: str) -> str:
        r = await self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe='')}")
        if r.status_code != 200:
            raise GithubApiError(f"ref lookup failed ({r.status_code})")
        return r.json()["object"]["sha"]

    async def create_branch(self, owner: str, repo: str, name: str, sha: str) -> bool:
        """True on created; False when the ref already exists (422)."""
        r = await self._request("POST", f"/repos/{owner}/{repo}/git/refs",
                                json={"ref": f"refs/heads/{name}", "sha": sha})
        if r.status_code == 201:
            return True
        if r.status_code == 422:
            return False
        raise GithubApiError(f"create branch failed ({r.status_code})")

    async def list_paths(self, owner: str, repo: str, branch: str, *, limit: int = 400) -> list[str]:
        r = await self._request("GET", f"/repos/{owner}/{repo}/git/trees/{quote(branch, safe='')}",
                                params={"recursive": "1"})
        if r.status_code != 200:
            raise GithubApiError(f"tree listing failed ({r.status_code})")
        blobs = [t["path"] for t in r.json().get("tree", []) if t.get("type") == "blob"]
        return blobs[:limit]

    async def get_file(self, owner: str, repo: str, path: str, *, ref: str) -> tuple[str, str]:
        """Returns (decoded_content, blob_sha)."""
        r = await self._request("GET", f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}",
                                params={"ref": ref})
        if r.status_code != 200:
            raise GithubApiError(f"get file failed ({r.status_code})")
        d = r.json()
        content = base64.b64decode(d.get("content") or "").decode("utf-8", errors="replace")
        return content, d["sha"]

    async def put_file(self, owner: str, repo: str, path: str, *, content: str,
                       message: str, branch: str, sha: str) -> None:
        r = await self._request("PUT", f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}",
                                json={
                                    "message": message, "branch": branch, "sha": sha,
                                    "content": base64.b64encode(content.encode()).decode(),
                                })
        if r.status_code not in (200, 201):
            raise GithubApiError(f"commit failed ({r.status_code})")

    async def create_pr(self, owner: str, repo: str, *, title: str, body: str,
                        head: str, base: str) -> str:
        r = await self._request("POST", f"/repos/{owner}/{repo}/pulls",
                                json={"title": title, "body": body, "head": head, "base": base})
        if r.status_code != 201:
            raise GithubApiError(f"create PR failed ({r.status_code})")
        return r.json()["html_url"]
