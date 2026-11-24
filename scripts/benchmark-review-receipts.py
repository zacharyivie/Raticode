"""Compare review receipts sent as raw traces with one entry per tool call.

Run with the development environment:
    PYTHONPATH=src .venv/bin/python scripts/benchmark-review-receipts.py [rem-home.sqlite3]

The database is opened read-only and never changed. Without a path it reads the
standard Raticode data folder. Sizes are characters of the JSON the prompt sends.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from gofer.subscriptions.tool_trace import per_call_receipts
from gofer.ui.rem_home_store import encode


def default_database() -> Path:
    from gofer.utils.paths import get_data_dir

    return get_data_dir() / "rem-home.sqlite3"


def recent_traces(path: Path, runs: int) -> list[list[dict[str, Any]]]:
    import sqlite3

    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT json_extract(body, '$.worker_traces') FROM runs "
            "WHERE json_array_length(json_extract(body, '$.worker_traces')) > 0 "
            "ORDER BY json_extract(body, '$.created_at') DESC LIMIT ?",
            (runs,),
        ).fetchall()
    finally:
        db.close()
    return [json.loads(row[0]) for row in rows]


def calls(entries: list[dict[str, Any]]) -> int:
    return len({str(e["id"]) for e in entries if e.get("id")})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("database", nargs="?", type=Path)
    parser.add_argument("--runs", type=int, default=60, help="newest runs with traces")
    args = parser.parse_args()
    path = args.database or default_database()
    if not path.exists():
        print(f"No database at {path}", file=sys.stderr)
        return 1
    samples = recent_traces(path, args.runs)
    if not samples:
        print("No runs with worker traces.", file=sys.stderr)
        return 1
    before = [len(encode(t[-80:])) for t in samples]
    after = [len(encode(per_call_receipts(t))) for t in samples]
    covered = [calls(t[-80:]) for t in samples]
    report = {
        "runs": len(samples),
        "runs_with_80_or_more_traces": sum(len(t) >= 80 for t in samples),
        "median_chars_before": statistics.median(before),
        "median_chars_after": statistics.median(after),
        "median_saving": round(1 - statistics.median(after) / statistics.median(before), 3),
        "total_saving": round(1 - sum(after) / sum(before), 3),
        "median_calls_in_window": statistics.median(covered),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
