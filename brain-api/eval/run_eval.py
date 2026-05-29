"""Phase 1 eval harness — retrieval mode only.

Computes Recall@10 and MRR@10 over the golden Q&A file by calling /query
with the debug-bypass header. Generates a JSON report.

Usage:
    uv run python eval/run_eval.py --mode retrieval --report eval/reports/<date>.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

API = "http://localhost:8000"
DEBUG_USER = "t-eval,u-eval,t-eval:everyone"


def _load_golden(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _hit_rank(expected: list[str], doc_ids: list[str]) -> int | None:
    """Return 1-based rank of first expected doc_id, or None."""
    for i, did in enumerate(doc_ids, start=1):
        if did in expected:
            return i
    return None


def run_retrieval(golden: list[dict]) -> dict:
    recalls: list[float] = []
    rrs: list[float] = []
    latencies: list[float] = []
    failures: list[str] = []

    with httpx.Client(timeout=30.0) as client:
        for q in golden:
            t0 = time.perf_counter()
            resp = client.post(
                f"{API}/admin/retrieve",
                json={"query": q["query"], "k": 10},
                headers={"x-debug-bypass-auth": DEBUG_USER},
            )
            latencies.append(time.perf_counter() - t0)
            if resp.status_code != 200:
                failures.append(f"{q['qid']}: HTTP {resp.status_code}")
                recalls.append(0.0)
                rrs.append(0.0)
                continue
            doc_ids = resp.json().get("doc_ids", [])
            rank = _hit_rank(q["expected_doc_ids"], doc_ids)
            recalls.append(1.0 if rank else 0.0)
            rrs.append(1.0 / rank if rank else 0.0)

    return {
        "n": len(golden),
        "recall_at_10": round(statistics.mean(recalls), 3) if recalls else 0.0,
        "mrr_at_10": round(statistics.mean(rrs), 3) if rrs else 0.0,
        "p50_latency_s": round(statistics.median(latencies), 3) if latencies else 0.0,
        "p95_latency_s": round(
            sorted(latencies)[int(0.95 * len(latencies))] if latencies else 0.0, 3
        ),
        "failures": failures,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["retrieval"], default="retrieval")
    p.add_argument("--golden", default="eval/golden.jsonl")
    p.add_argument("--report", default=None)
    args = p.parse_args()

    golden = _load_golden(Path(args.golden))
    if args.mode == "retrieval":
        report = run_retrieval(golden)
    else:
        raise SystemExit(f"mode {args.mode} not implemented in Phase 1")

    print(json.dumps(report, indent=2))
    if args.report:
        outp = Path(args.report)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, indent=2))
    # Gate: Recall@10 must be >= 0.7 for Phase 1
    return 0 if report["recall_at_10"] >= 0.7 else 1


if __name__ == "__main__":
    sys.exit(main())
