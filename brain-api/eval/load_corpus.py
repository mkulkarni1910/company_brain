"""Walk eval/corpus/ and POST each .md file to /admin/ingest on a running brain-api."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

CORPUS = Path(__file__).parent / "corpus"
API = "http://localhost:8000"
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def main() -> None:
    paths = sorted(CORPUS.rglob("*.md"))
    if not paths:
        print("No corpus files found.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(UTC).isoformat()
    with httpx.Client(timeout=60.0) as client:
        for p in paths:
            rel = p.relative_to(CORPUS)
            # AI Search doc keys cannot contain '/', '.', or '\'.
            # Build a key-safe doc_id from the relative path stem.
            safe = rel.with_suffix("").as_posix().replace("/", "-")
            doc_id = f"up:{safe}"
            payload = {
                "doc_id": doc_id,
                "tenant_id": "t-test",
                "source": "uploaded",
                "source_url": f"local://{rel.as_posix()}",
                "title": p.stem.replace("-", " ").title(),
                "body": p.read_text(),
                "author_id": None,
                "acl_principals": ["t-test:everyone"],
                "created_at": now,
                "modified_at": now,
                "mime": "text/markdown",
            }
            r = client.post(
                f"{API}/admin/ingest",
                json=payload,
                headers={"x-admin-key": ADMIN_KEY},
            )
            r.raise_for_status()
            print(f"{rel}: {r.json()['chunks_indexed']} chunks")
    print("Done.")


if __name__ == "__main__":
    main()
