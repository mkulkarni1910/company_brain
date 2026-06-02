from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ConnStatus = Literal["pending", "syncing", "live", "error"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]


class Connection(BaseModel):
    connection_id: str
    tenant_id: str
    type: Literal["sharepoint"] = "sharepoint"
    site_id: str
    name: str
    web_url: str
    status: ConnStatus = "pending"
    item_count: int = 0
    last_sync: datetime | None = None
    last_job_id: str | None = None
    error: str | None = None
    connected_tenant_id: str | None = None  # the org's Entra tenant (admin-consent OAuth)


class SyncJob(BaseModel):
    job_id: str
    tenant_id: str
    connection_id: str
    status: JobStatus = "queued"
    total: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    truncated: bool = False
    message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RemoteFile(BaseModel):
    drive_id: str
    item_id: str
    name: str
    mime: str | None = None
    web_url: str = ""
    size: int = 0
    created_at: datetime | None = None
    modified_at: datetime | None = None
    author_id: str | None = None


class ActivityEntry(BaseModel):
    ts: datetime
    actor: str
    text: str
    kind: Literal["connect", "sync", "disconnect", "system"] = "system"
