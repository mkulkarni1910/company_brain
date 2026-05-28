"""Phase 1: synthesize ACLs for ingested docs.

Replaced in later phases by the Permissions Crawler reading source-side ACLs.
"""

from __future__ import annotations

from app.config import get_settings


def resolve_synthetic_acl(
    *, source: str, source_id: str, overrides: list[str] | None
) -> list[str]:
    if overrides:
        return list(overrides)
    tenant = get_settings().brain_tenant_id
    return [f"{tenant}:everyone"]
