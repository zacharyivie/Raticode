"""Measure bounded indexing and complete searches on a generated knowledge tree.

Run with the project's Python environment:
    python scripts/benchmark-second-brain.py --output audit-evidence/second-brain-benchmark.json

All notes and SQLite files live in a temporary directory. Results describe this
synthetic fixture and local machine, not production workloads or a hard deadline.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from gofer.ui import second_brain_index as indexing
from gofer.ui.second_brain import SecondBrain


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=int, default=2000)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if not 1 <= arguments.notes <= indexing.MAX_NOTES or arguments.queries < 1:
        parser.error("Use 1-10000 notes and at least one query.")

    read_bytes = 0
    passes: list[dict[str, float | int]] = []
    original_read = indexing.read_note_bytes
    original_reconcile = indexing.NoteIndex.reconcile

    def measured_read(root, relative):
        nonlocal read_bytes
        result = original_read(root, relative)
        if result is not None:
            read_bytes += len(result[0])
        return result

    def measured_reconcile(index, connection):
        before = read_bytes
        started = time.perf_counter()
        result = original_reconcile(index, connection)
        passes.append({"seconds": time.perf_counter() - started, "read_bytes": read_bytes - before})
        return result

    indexing.read_note_bytes = measured_read
    indexing.NoteIndex.reconcile = measured_reconcile
    try:
        with tempfile.TemporaryDirectory(prefix="raticode-brain-benchmark-") as raw:
            root = Path(raw)
            corpus_bytes = 0
            for number in range(arguments.notes):
                folder = root / f"topic-{number % 25:02d}"
                folder.mkdir(exist_ok=True)
                content = (
                    f"# Finding {number}\ncategory{number % 17} recovery workflow security\n"
                    + "local evidence testing performance notes. " * 250
                )
                corpus_bytes += len(content.encode())
                (folder / f"note-{number:04d}.md").write_text(content)
            brain = SecondBrain(root)
            started, cpu_started = time.perf_counter(), time.process_time()
            assert brain.search("category0")
            cold_seconds = time.perf_counter() - started
            cold_cpu_seconds = time.process_time() - cpu_started
            cold_passes = list(passes)
            warm_times = []
            bytes_before = read_bytes
            for number in range(arguments.queries):
                started = time.perf_counter()
                assert brain.search(f"category{number % min(arguments.notes, 17)}")
                warm_times.append(time.perf_counter() - started)
            warm_read_bytes = read_bytes - bytes_before
            edit_count = min(100, arguments.notes)
            index = indexing.note_index(root)
            with index.lock:
                for note in sorted(root.rglob("*.md"))[:edit_count]:
                    note.write_text(note.read_text() + "\nbackgroundrecovered")
                    index.invalidate(note)
            started = time.perf_counter()
            deadline = started + 30
            while True:
                with index.lock:
                    with closing(
                        sqlite3.connect(root / ".raticode/second-brain.sqlite3")
                    ) as connection:
                        count = connection.execute(
                            "SELECT COUNT(*) FROM notes WHERE notes MATCH 'backgroundrecovered'"
                        ).fetchone()[0]
                    ready = count == edit_count and not index._needs_work()
                if ready:
                    break
                if time.perf_counter() >= deadline:
                    raise RuntimeError("Background recovery did not complete within 30 seconds")
                time.sleep(0.005)
            recovery_seconds = time.perf_counter() - started
            started = time.perf_counter()
            assert len(brain.search("backgroundrecovered")) == min(edit_count, 30)
            recovery_query_seconds = time.perf_counter() - started
            indexing.close_note_indexes()
            result = {
                "timestamp": datetime.now(UTC).isoformat(),
                "python": platform.python_version(),
                "platform": platform.platform(),
                "fixture": "Synthetic UTF-8 Markdown notes in up to 25 topic folders",
                "notes": arguments.notes,
                "corpus_bytes": corpus_bytes,
                "cold_complete_search_seconds": cold_seconds,
                "cold_cpu_seconds": cold_cpu_seconds,
                "cold_passes": len(cold_passes),
                "max_pass_seconds": max(item["seconds"] for item in cold_passes),
                "max_pass_read_bytes": max(item["read_bytes"] for item in cold_passes),
                "configured_pass_seconds": indexing.MAX_BATCH_SECONDS,
                "configured_pass_read_bytes": indexing.MAX_BATCH_BYTES,
                "warm_queries": arguments.queries,
                "warm_p50_ms": statistics.median(warm_times) * 1000,
                "warm_p95_ms": sorted(warm_times)[int((len(warm_times) - 1) * 0.95)] * 1000,
                "warm_note_read_bytes": warm_read_bytes,
                "background_edits": edit_count,
                "background_complete_seconds": recovery_seconds,
                "query_after_background_recovery_ms": recovery_query_seconds * 1000,
                "limitations": [
                    "A cold search waits for a complete index; it never returns partial results.",
                    "Pass deadlines are cooperative; an OS call or SQLite operation may overrun.",
                    "Unchanged queries still pay FTS ranking, connection and result costs.",
                    "Native events may arrive after a query; recovery catches missed events.",
                ],
            }
    finally:
        indexing.close_note_indexes()
        indexing.read_note_bytes = original_read
        indexing.NoteIndex.reconcile = original_reconcile
    rendered = json.dumps(result, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
