"""Redis-backed state for the GitHub tool: admin repo config, per-user OAuth
tokens, and one-shot connect states. Mirrors writes to an in-process dict so
the flow keeps working within a single process when Redis is unavailable
(same degradation philosophy as RunStore)."""

from __future__ import annotations

import contextlib
import logging
import secrets

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.connectors.models import GithubConfig

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)


def _config_key(tenant: str) -> str: return f"github:config:{tenant}"
def _token_key(tenant: str, email: str) -> str: return f"github:token:{tenant}:{email.lower()}"
def _state_key(state: str) -> str: return f"github:oauth:{state}"


class GithubStore:
    def __init__(self, client: redis.Redis | None = None, *, force_memory: bool = False) -> None:
        self._mem: dict[str, str] = {}
        if force_memory:
            self._r = None
            return
        if client is not None:
            self._r = client
            return
        s = get_settings()
        if not s.azure_redis_host:
            self._r = None
            return
        self._r = redis.Redis(
            host=s.azure_redis_host, port=s.azure_redis_port,
            ssl=s.azure_redis_ssl, password=s.redis_key,
            decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
        )

    async def aclose(self) -> None:
        if self._r is not None:
            with contextlib.suppress(Exception):
                await self._r.aclose()

    # ── shared get/set with memory mirror ──────────────────────────────────────

    async def _set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self._mem[key] = value
        if self._r is None:
            return
        try:
            await self._r.set(key, value, ex=ex)
        except _ERRORS as e:
            logger.warning("GithubStore set failed: %s", e)

    async def _get(self, key: str) -> str | None:
        if self._r is not None:
            try:
                v = await self._r.get(key)
                if v is not None:
                    return v
            except _ERRORS as e:
                logger.warning("GithubStore get failed: %s", e)
        return self._mem.get(key)

    async def _getdel(self, key: str) -> str | None:
        """One-shot read: removes from BOTH redis and the memory mirror."""
        redis_val: str | None = None
        if self._r is not None:
            try:
                redis_val = await self._r.getdel(key)
            except _ERRORS as e:
                logger.warning("GithubStore getdel failed: %s", e)
        mem_val = self._mem.pop(key, None)
        return redis_val if redis_val is not None else mem_val

    # ── admin repo config ───────────────────────────────────────────────────────

    async def get_config(self, tenant: str) -> GithubConfig | None:
        raw = await self._get(_config_key(tenant))
        if not raw:
            return None
        with contextlib.suppress(Exception):
            return GithubConfig.model_validate_json(raw)
        return None

    async def put_config(self, tenant: str, cfg: GithubConfig) -> None:
        await self._set(_config_key(tenant), cfg.model_dump_json())

    # ── per-user tokens ─────────────────────────────────────────────────────────

    async def get_user_token(self, tenant: str, email: str | None) -> str | None:
        if not email:
            return None
        return await self._get(_token_key(tenant, email))

    async def put_user_token(self, tenant: str, email: str, token: str) -> None:
        await self._set(_token_key(tenant, email), token)

    # ── one-shot connect states (CSRF) ──────────────────────────────────────────

    async def mint_connect_state(self, tenant: str, email: str) -> str:
        state = secrets.token_urlsafe(24)
        ttl = get_settings().oauth_state_ttl_seconds
        await self._set(_state_key(state), f"{tenant}|{email}", ex=ttl)
        return state

    async def peek_connect_state(self, state: str) -> tuple[str, str] | None:
        raw = await self._get(_state_key(state))
        if not raw or "|" not in raw:
            return None
        tenant, email = raw.split("|", 1)
        return tenant, email

    async def consume_connect_state(self, state: str) -> tuple[str, str] | None:
        raw = await self._getdel(_state_key(state))
        if not raw or "|" not in raw:
            return None
        tenant, email = raw.split("|", 1)
        return tenant, email
