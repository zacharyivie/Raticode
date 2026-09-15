"""Synthetic idle-history and live-update scaling probe. No providers are started.

Run with PYTHONPATH=src .venv/bin/python scripts/benchmark-swarm-performance.py.
Set GOFER_PERF_BASELINE to a Git revision to compare the original manager.
"""

import asyncio
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import tracemalloc
from pathlib import Path

from gofer.ui.swarms import SwarmManager

if os.environ.get("GOFER_PERF_BASELINE"):
    with tempfile.TemporaryDirectory() as baseline_dir:
        source = Path(baseline_dir) / "baseline.py"
        source.write_bytes(
            subprocess.check_output(
                ["git", "show", os.environ["GOFER_PERF_BASELINE"] + ":src/gofer/ui/swarms.py"]
            )
        )
        spec = importlib.util.spec_from_file_location("baseline_swarms", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        SwarmManager = module.SwarmManager


def sample(n):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        m = SwarmManager(root / "data", start_runtime=False)
        for i in range(n):
            s = m.create(
                root,
                {
                    "name": str(i),
                    "agents": [
                        {"id": "lead", "name": "Lead", "role": "Lead", "isOrchestrator": True}
                    ],
                },
            )
            s["history"] = [{"id": "old", "padding": "x" * (512 * 1024)}]
            m._save(s)
        reads = []
        m._db.set_trace_callback(lambda sql: reads.append(1) if sql.startswith("SELECT") else None)

        async def run():
            task = asyncio.create_task(m._loop())
            await asyncio.sleep(1.2)
            m._closed.set()
            getattr(m, "_wake", m._closed).set()
            await task

        wall = time.perf_counter()
        t = time.process_time()
        asyncio.run(run())
        cpu = (time.process_time() - t) * 1000
        elapsed = (time.perf_counter() - wall) * 1000
        m._db.set_trace_callback(None)
        result = {"teams": n, "idle_cpu_ms": cpu, "wall_ms": elapsed, "selects": len(reads)}
        if n:
            m._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            m._db.execute("PRAGMA wal_autocheckpoint=0")
            tracemalloc.start()
            t = time.process_time()
            for i in range(100):
                s = m._get(root, s["id"])
                s["charter"] = str(i)
                m._save(s)
            result.update(
                update_cpu_ms=(time.process_time() - t) * 1000,
                update_peak_bytes=tracemalloc.get_traced_memory()[1],
            )
            tracemalloc.stop()
            result["wal_bytes_for_100_updates"] = (
                (root / "data" / "swarms.sqlite3-wal").stat().st_size
            )
        m.close()
        return result


print(json.dumps([sample(n) for n in (0, 20, 100)], indent=2))
