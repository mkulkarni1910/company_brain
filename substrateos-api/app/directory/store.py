from __future__ import annotations

import contextlib
import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.domain.directory import DirectoryUser

logger = logging.getLogger(__name__)
_ERRORS = (RedisError, ConnectionError, TimeoutError, OSError)

_EMAILS_KEY = "directory:emails"  # SET of every known (lowercase) email


def _user_key(email: str) -> str:
    return f"directory:user:{email}"


def _slack_key(slack_id: str) -> str:
    return f"directory:slack:{slack_id}"


class DirectoryStore:
    """Redis-backed user directory with an in-process mirror (RunStore pattern):
    routing keeps working within a single process when Redis is unavailable."""

    def __init__(self, client: redis.Redis | None = None, *, force_memory: bool = False) -> None:
        self._mem_users: dict[str, str] = {}
        self._mem_slack: dict[str, str] = {}
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

    async def upsert(self, user: DirectoryUser) -> None:
        email = user.email.lower()
        blob = user.model_dump_json()
        self._mem_users[email] = blob
        if user.slack_id:
            self._mem_slack[user.slack_id] = email
        if self._r is None:
            return
        try:
            await self._r.set(_user_key(email), blob)
            await self._r.sadd(_EMAILS_KEY, email)
            if user.slack_id:
                await self._r.set(_slack_key(user.slack_id), email)
        except _ERRORS as e:
            logger.warning("DirectoryStore.upsert redis failed: %s", e)

    async def get_by_email(self, email: str | None) -> DirectoryUser | None:
        if not email:
            return None
        email = email.lower()
        raw: str | None = None
        if self._r is not None:
            try:
                raw = await self._r.get(_user_key(email))
            except _ERRORS as e:
                logger.warning("DirectoryStore.get_by_email redis failed: %s", e)
        raw = raw or self._mem_users.get(email)
        if not raw:
            return None
        with contextlib.suppress(Exception):
            return DirectoryUser.model_validate_json(raw)
        return None

    async def get_by_slack_id(self, slack_id: str | None) -> DirectoryUser | None:
        if not slack_id:
            return None
        email: str | None = None
        if self._r is not None:
            try:
                email = await self._r.get(_slack_key(slack_id))
            except _ERRORS as e:
                logger.warning("DirectoryStore.get_by_slack_id redis failed: %s", e)
        email = email or self._mem_slack.get(slack_id)
        return await self.get_by_email(email) if email else None

    async def list_all(self) -> list[DirectoryUser]:
        emails: list[str] = []
        if self._r is not None:
            try:
                emails = sorted(await self._r.smembers(_EMAILS_KEY))
            except _ERRORS as e:
                logger.warning("DirectoryStore.list_all redis failed: %s", e)
        if not emails:
            emails = sorted(self._mem_users)
        users = [u for e in emails if (u := await self.get_by_email(e)) is not None]
        return users
