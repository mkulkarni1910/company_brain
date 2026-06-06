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
    def __init__(self, policy_dir: Path | None = None) -> None:
        self._dir = policy_dir or DEFAULT_POLICY_DIR

    def load(self, policy_id: str) -> Policy:
        path = self._dir / f"{policy_id}.yaml"
        if not path.exists():
            raise PolicyNotFound(f"policy {policy_id!r} not found at {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Policy.model_validate(data)
