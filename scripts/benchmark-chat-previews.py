"""Measure chat preview work on a temporary copy of a repository's tracked files.

Run with the development environment: python scripts/benchmark-chat-previews.py
The source repository is never edited. RSS measures the Python worker, not Electron.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def peak_rss_bytes():
    if sys.platform == "win32":
        return None
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def worker(project, mode, iterations):
    from gofer.ui import chat

    tracker = chat._ChatProjectTracker(project) if mode == "incremental" else None
    started = time.perf_counter()
    before = tracker.start() if tracker else chat._capture_chat_project(project)
    if not getattr(before, "complete", True):
        raise RuntimeError("Baseline snapshot exceeded its budget")
    baseline_ms = (time.perf_counter() - started) * 1000
    if tracker:
        if tracker.observer is None:
            raise RuntimeError("Native watcher unavailable for benchmark")
        tracker.capture()
    counters = {"project_bytes_read": 0, "spool_bytes_read": 0, "walks": 0, "lstats": 0}
    real_open, real_walk, real_lstat = chat.open_binary_input, os.walk, Path.lstat

    @contextmanager
    def counted_open(path):
        category = "project_bytes_read" if path.is_relative_to(project) else "spool_bytes_read"
        with real_open(path) as handle:

            class Reader:
                def read(self, *args):
                    content = handle.read(*args)
                    counters[category] += len(content)
                    return content

                def fileno(self):
                    return handle.fileno()

            yield Reader()

    def counted_walk(*args, **kwargs):
        counters["walks"] += 1
        return real_walk(*args, **kwargs)

    def counted_lstat(path, *args, **kwargs):
        counters["lstats"] += 1
        return real_lstat(path, *args, **kwargs)

    chat.open_binary_input, os.walk, Path.lstat = counted_open, counted_walk, counted_lstat
    target = project / "src/gofer/ui/chat.py"
    if not target.is_file():
        target = project / next(iter(before))
    original = target.read_bytes()
    durations, delivery = [], []
    try:
        for index in range(iterations):
            if index % 5 == 0:
                written_at = time.perf_counter()
                target.write_bytes(original + f"\n# Benchmark edit {index}\n".encode())
                if tracker:
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        with tracker.lock:
                            if target.relative_to(project).as_posix() in tracker.pending:
                                break
                        time.sleep(0.001)
                    else:
                        raise RuntimeError("Native watcher missed benchmark edit")
                    delivery.append((time.perf_counter() - written_at) * 1000)
            started = time.perf_counter()
            preview = chat._preview_chat_changes(project, before, tracker)
            durations.append((time.perf_counter() - started) * 1000)
            if preview is None or preview["fileCount"] != 1:
                raise RuntimeError("Benchmark preview did not preserve expected change")
        return {
            "mode": mode,
            "files": len(before),
            "project_bytes": sum(state.size for state in before.values()),
            "iterations": iterations,
            "baseline_ms": round(baseline_ms, 3),
            "median_preview_ms": round(statistics.median(durations), 3),
            "max_preview_ms": round(max(durations), 3),
            "median_event_delivery_ms": round(statistics.median(delivery), 3) if delivery else None,
            "peak_python_worker_rss_bytes": peak_rss_bytes(),
            **counters,
        }
    finally:
        chat.open_binary_input, os.walk, Path.lstat = real_open, real_walk, real_lstat
        if tracker:
            tracker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--worker", choices=["full", "incremental"])
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    if args.worker:
        print(json.dumps(worker(args.project.resolve(), args.worker, args.iterations)))
        return
    source = args.project.resolve()
    tracked = subprocess.check_output(["git", "-C", str(source), "ls-files", "-z"]).split(b"\0")
    results = []
    for mode in ("full", "incremental"):
        with tempfile.TemporaryDirectory(prefix="raticode-preview-benchmark-") as temporary:
            project = Path(temporary)
            for raw in tracked:
                if not raw:
                    continue
                relative = Path(os.fsdecode(raw))
                file = source / relative
                if file.is_symlink() or not file.is_file():
                    continue
                destination = project / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, destination)
            output = subprocess.check_output(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    mode,
                    "--project",
                    str(project),
                    "--iterations",
                    str(args.iterations),
                ],
                text=True,
            )
            results.append(json.loads(output))
    print(
        json.dumps(
            {
                "source": str(source),
                "method": (
                    "Temporary copies of tracked working-tree files; one source edit every five "
                    "previews; separate Python workers; warm filesystem cache. "
                    "Source repository unchanged."
                ),
                "limitations": (
                    "Python worker RSS only, not peak desktop RSS. Watcher delivery measured "
                    "separately from preview calls; full scan and native-event incremental modes "
                    "use identical fixture contents."
                ),
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
