"""DirectoryStore: Redis-backed (memory fallback) email↔Slack-id↔role records."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.directory.store import DirectoryStore
from app.domain.directory import DirectoryUser


def _tom() -> DirectoryUser:
    return DirectoryUser(
        email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
        entra_id="guid-tom", manager_email="diane@x",
        groups=["Support Agent"], role="agent", synced_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_upsert_and_get_by_email_roundtrip():
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(_tom())
    got = await store.get_by_email("tom@x")
    assert got is not None
    assert got.slack_id == "U_TOM" and got.role == "agent"
    assert got.manager_email == "diane@x"


@pytest.mark.asyncio
async def test_email_lookup_is_case_insensitive():
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(_tom())
    assert (await store.get_by_email("TOM@X")) is not None


@pytest.mark.asyncio
async def test_get_by_slack_id_reverse_index():
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(_tom())
    got = await store.get_by_slack_id("U_TOM")
    assert got is not None and got.email == "tom@x"
    assert (await store.get_by_slack_id("U_NOBODY")) is None


@pytest.mark.asyncio
async def test_upsert_overwrites_and_list_all():
    store = DirectoryStore(client=None, force_memory=True)
    await store.upsert(_tom())
    promoted = _tom().model_copy(update={"role": "manager", "groups": ["Managers"]})
    await store.upsert(promoted)
    users = await store.list_all()
    assert len(users) == 1
    assert users[0].role == "manager"


@pytest.mark.asyncio
async def test_missing_email_returns_none():
    store = DirectoryStore(client=None, force_memory=True)
    assert (await store.get_by_email("ghost@x")) is None
    assert (await store.get_by_email(None)) is None
