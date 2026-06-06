"""Seed the `raise-pr` workflow skill into SubstrateOS.

Creates or refreshes the ``raise-pr`` skill so the router correctly routes
"raise a PR …" and similar requests to the GitHub playbook.

Idempotent: if a skill with slug ``raise-pr`` already exists it is PATCHed
with the latest definition; otherwise it is POSTed as a new skill.

Usage (same conventions as seed_refund_demo.py):

    python scripts/seed_github_skill.py
    python scripts/seed_github_skill.py --api http://localhost:8000 \\
        --admin-key $ADMIN_KEY

Environment variables (used when flags are omitted):
    API_BASE    — base URL of the running SubstrateOS API  (default: http://localhost:8000)
    ADMIN_KEY   — value for the x-admin-key header        (required)
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

SKILL = {
    "slug": "raise-pr",
    "name": "Raise a PR",
    "description": (
        "Raise an AI-drafted pull request against the connected GitHub repository. "
        "Use when someone asks to open, raise, or create a PR, or to propose/apply a "
        "change to a doc, policy, or file in the repo."
    ),
    "team": "Platform",
    "run_scope": "org",
    "workflow": "github",
    "enabled": True,
    "steps": [
        "Find the target file in the repo",
        "Draft the change (grounded in the current file)",
        "Preview to the requester — Create PR / Cancel",
        "Create branch + commit + PR as the requester",
        "Record every step in the run log",
    ],
    "data_feeds": ["GitHub"],
    "system_prompt": "You are the raise-PR playbook.",
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default=os.environ.get("API_BASE", "http://localhost:8000"))
    p.add_argument("--admin-key", default=os.environ.get("ADMIN_KEY", ""))
    args = p.parse_args()
    if not args.admin_key:
        print("--admin-key is required (or set ADMIN_KEY env)")
        return 1
    headers = {"x-admin-key": args.admin_key}

    with httpx.Client(base_url=args.api, headers=headers, timeout=120.0) as client:
        skills = client.get("/admin/skills").json()
        existing = next((s for s in skills if s.get("slug") == SKILL["slug"]), None)
        if existing:
            patch = {k: v for k, v in SKILL.items() if k != "slug"}
            r = client.patch(f"/admin/skills/{existing['id']}", json=patch)
            r.raise_for_status()
            print(f"updated skill {SKILL['slug']} (id={existing['id']})")
        else:
            r = client.post("/admin/skills", json=SKILL)
            r.raise_for_status()
            print(f"created skill {SKILL['slug']} (id={r.json().get('id')})")

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
