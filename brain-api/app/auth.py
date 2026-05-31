"""Entra JWT validation + user-claims expansion.

Phase 1: JWT verification + claims extraction. Group expansion (via Graph
`/users/{id}/transitiveMemberOf` with app-only `Directory.Read.All`) is
folded in here as well; the result is cached in Redis (Task 22) on a
10-minute TTL.

For tests, a `x-debug-bypass-auth` header still works (see api/query.py).
"""

from __future__ import annotations

import base64
import json as _json
from functools import lru_cache

import httpx
from azure.identity.aio import DefaultAzureCredential
from jose import jwt
from jose.exceptions import JWTError

from app.config import get_settings
from app.domain.identity import User


class InvalidToken(Exception):
    pass


def _audience_for_scope(scope: str) -> str:
    # "api://<client-id>/Query.Read" → "api://<client-id>"
    return scope.rsplit("/", 1)[0]


@lru_cache(maxsize=1)
def _jwks_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"


def _validate_jwt(token: str, *, audience: str, tenant: str) -> dict:
    try:
        jwks = httpx.get(_jwks_url(tenant), timeout=5.0).json()
        unverified_header = jwt.get_unverified_header(token)
        key = next((k for k in jwks["keys"] if k["kid"] == unverified_header["kid"]), None)
        if not key:
            raise InvalidToken("kid not found in JWKS")
        return jwt.decode(
            token,
            key=key,
            algorithms=[unverified_header["alg"]],
            audience=audience,
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
        )
    except (JWTError, KeyError, ValueError, httpx.HTTPError) as e:
        raise InvalidToken(str(e)) from e


async def _expand_groups(user_id: str, tenant: str) -> set[str]:
    """App-only Graph call: get transitive group memberships for user_id."""
    cred = DefaultAzureCredential()
    tok = (await cred.get_token("https://graph.microsoft.com/.default")).token
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{user_id}/transitiveMemberOf?$select=id",
            headers={"Authorization": f"Bearer {tok}"},
        )
        r.raise_for_status()
        return {item["id"] for item in r.json().get("value", []) if "id" in item}


async def user_from_bearer(token: str) -> User:
    s = get_settings()
    if not s.azure_api_scope:
        raise InvalidToken("AZURE_API_SCOPE not configured")
    claims = _validate_jwt(
        token, audience=_audience_for_scope(s.azure_api_scope), tenant=s.azure_tenant_id
    )
    user_id = claims["oid"]
    tenant_id = claims["tid"]
    groups = await _expand_groups(user_id, tenant_id)
    return User(
        user_id=user_id,
        tenant_id=tenant_id,
        email=claims.get("preferred_username") or claims.get("email") or "",
        display_name=claims.get("name") or claims.get("preferred_username") or user_id,
        group_ids=groups,
    )


def user_from_easy_auth_header(principal_b64: str) -> User:
    """Build a User from the Container Apps Easy Auth X-MS-CLIENT-PRINCIPAL header."""
    try:
        payload = _json.loads(base64.b64decode(principal_b64).decode())
        claims = {(_TYPE_ALIASES.get(c["typ"], c["typ"])): c["val"] for c in payload.get("claims", [])}
        groups = {
            c["val"]
            for c in payload.get("claims", [])
            if c["typ"] in ("groups", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role")
        }
        oid = claims.get("oid") or claims.get("objectidentifier")
        tid = claims.get("tid") or claims.get("tenantid")
        if not oid or not tid:
            raise InvalidToken("missing oid/tid in principal")
        return User(
            user_id=oid,
            tenant_id=tid,
            email=claims.get("preferred_username") or claims.get("email") or "",
            display_name=claims.get("name") or oid,
            group_ids=groups,
        )
    except InvalidToken:
        raise
    except Exception as e:
        raise InvalidToken(f"bad easy-auth principal: {e}") from e


_TYPE_ALIASES = {
    "http://schemas.microsoft.com/identity/claims/objectidentifier": "oid",
    "http://schemas.microsoft.com/identity/claims/tenantid": "tid",
}
