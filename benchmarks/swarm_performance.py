"""Synthetic, provider-free before/after probes. Run with PYTHONPATH=src."""
import asyncio
import json
import tempfile
import time
from pathlib import Path

from gofer.ui.swarms import SwarmManager

CONFIG = {"name": "Fixture", "agents": [{"id": "lead", "name": "Lead", "role": "Test", "isOrchestrator": True}]}

async def measure(count):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manager = SwarmManager(root / "data", start_runtime=False)
        for _ in range(count):
            swarm = manager.create(root, CONFIG)
            swarm["history"] = [{"id": "archive", "padding": "x" * (512 * 1024)}]
            manager._save(swarm)
        reads = 0
        def trace(sql):
            nonlocal reads
            if sql.startswith("SELECT"): reads += 1
        manager._db.set_trace_callback(trace)
        started = time.process_time()
        loop = asyncio.create_task(manager._loop())
        await asyncio.sleep(1.2)
        manager._closed.set()
        if hasattr(manager, "_wake"): manager._wake.set()
        await loop
        cpu = (time.process_time() - started) * 1000
        manager._db.set_trace_callback(None)
        started = time.perf_counter()
        payload = manager.list(root)
        list_ms = (time.perf_counter() - started) * 1000
        manager.close()
        return {"archives": count, "cpu_ms": round(cpu, 3), "selects": reads, "list_ms": round(list_ms, 3), "list_bytes": len(json.dumps(payload))}

async def main():
    print(json.dumps([await measure(n) for n in [0, 20, 100]], indent=2))

if __name__ == "__main__": asyncio.run(main())
