"""PolicyStore — loads versioned policy specs from ``policies/<id>.yaml``.

Policies are referenced by id (which embeds the version, e.g. ``refund.v1``), so a
running playbook always evaluates against a named, immutable rule file. Changing a
threshold in the file changes behavior with no code or prompt edit.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.domain.policy import Policy

logger = logging.getLogger(__name__)

# substrateos-api/app/policy/store.py → parents[2] == substrateos-api/
DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[2] / "policies"


class PolicyNotFound(KeyError):
    """Raised when a referenced policy file does not exist. Callers should treat a
    missing policy as fail-closed, never as 'allow'."""


class PolicyStore:
    """Loads + caches policy specs, invalidated by file mtime.

    The cache means we don't re-parse on every request, but an edit to the YAML is
    picked up on the next request (no process restart) — so "flip 500 → 300 in the
    file" actually takes effect live, as advertised.
    """

    def __init__(self, policy_dir: Path | None = None) -> None:
        self._dir = policy_dir or DEFAULT_POLICY_DIR
        self._cache: dict[str, tuple[float, Policy]] = {}  # id -> (mtime, policy)

    def load(self, policy_id: str) -> Policy:
        path = self._dir / f"{policy_id}.yaml"
        if not path.exists():
            self._cache.pop(policy_id, None)
            raise PolicyNotFound(f"policy {policy_id!r} not found at {path}")
        mtime = path.stat().st_mtime
        cached = self._cache.get(policy_id)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        policy = Policy.model_validate(data)  # value/op types validated here — fail loudly
        self._cache[policy_id] = (mtime, policy)
        return policy
