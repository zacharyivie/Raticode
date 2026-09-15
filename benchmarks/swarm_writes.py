"""Durable event-write scaling, using synthetic archived runs only."""
import copy
import json
import tempfile
import time
import tracemalloc
from pathlib import Path
from gofer.ui.swarms import SwarmManager

CONFIG = {"name": "Fixture", "agents": [{"id": "lead", "name": "Lead", "role": "Test", "isOrchestrator": True}]}
results = []
for count in (0, 20, 100):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manager = SwarmManager(root / "data", start_runtime=False)
        swarm = manager.create(root, CONFIG)
        swarm = manager.start(root, swarm["id"], "Synthetic test")
        swarm["history"] = [dict(copy.deepcopy(swarm["run"]), id=f"archive-{i}", padding="x" * 524288) for i in range(count)]
        manager._save(swarm)
        del swarm
        sid = manager.list(root)[0]["id"]
        manager._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        manager._db.execute("PRAGMA wal_autocheckpoint=0")
        tracemalloc.start()
        cpu = time.process_time()
        start = time.perf_counter()
        for i in range(10):
            current = manager._get(root, sid)
            current["run"]["agentStates"]["lead"]["activity"] = f"Thought {i}"
            manager._save(current)
        elapsed = time.perf_counter() - start
        cpu = time.process_time() - cpu
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        wal = (root / "data" / "swarms.sqlite3-wal").stat().st_size
        results.append({"archives": count, "events": 10, "elapsed_ms": elapsed * 1000, "cpu_ms": cpu * 1000, "peak_python_bytes": peak, "wal_bytes": wal})
        manager.close()
print(json.dumps(results, indent=2))
