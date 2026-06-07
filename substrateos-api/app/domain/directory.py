from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DirectoryRole = Literal["manager", "agent", "customer"]


class DirectoryUser(BaseModel):
    """One person in the synced user directory — the email-keyed join of a
    Slack member and an Entra ID user, carrying the role playbooks route by."""

    email: str
    slack_id: str | None = None
    display_name: str | None = None
    entra_id: str | None = None
    manager_email: str | None = None
    groups: list[str] = []
    role: DirectoryRole = "customer"
    synced_at: datetime | None = None
