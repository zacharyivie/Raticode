"""Resource and recovery invariants, without provider processes."""

import asyncio
import copy
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from gofer.ui.swarms import SwarmManager

CONFIG = {
    "name": "Fixture",
    "agents": [{"id": "lead", "name": "Lead", "role": "Test", "isOrchestrator": True}],
}


def test_legacy_migration_retains_archives_and_recovers_deliveries(tmp_path):
    manager = SwarmManager(tmp_path / "source", start_runtime=False)
    swarm = manager.create(tmp_path, CONFIG)
    swarm = manager.start(tmp_path, swarm["id"], "Test")
    swarm["history"] = [dict(copy.deepcopy(swarm["run"]), id="archive", state="stopped")]
    swarm["run"]["messages"][0]["deliveries"][0]["state"] = "starting"
    manager.close()
    data = tmp_path / "legacy"
    data.mkdir()
    db = sqlite3.connect(data / "swarms.sqlite3")
    db.execute("CREATE TABLE swarms(id TEXT PRIMARY KEY, project TEXT, body TEXT)")
    db.execute("INSERT INTO swarms VALUES (?,?,?)", (swarm["id"], str(tmp_path), json.dumps(swarm)))
    db.commit()
    db.close()
    manager = SwarmManager(data, start_runtime=False)
    recovered = manager.get(tmp_path, swarm["id"])
    assert recovered["run"]["state"] == "paused"
    assert recovered["run"]["messages"][0]["deliveries"][0]["state"] == "uncertain"
    assert recovered["history"] == swarm["history"]
    manager.close()
    manager = SwarmManager(data, start_runtime=False)
    assert manager.get(tmp_path, swarm["id"])["history"] == swarm["history"]
    manager.close()


def test_live_write_does_not_touch_archives_and_rolls_back(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    swarm = manager.create(tmp_path, CONFIG)
    swarm = manager.start(tmp_path, swarm["id"], "Test")
    swarm["history"] = [dict(copy.deepcopy(swarm["run"]), id="archive", padding="x" * 1024 * 1024)]
    manager._save(swarm)
    live = manager.get(tmp_path, swarm["id"], include_history=False)
    assert "history" not in live
    before = manager.get(tmp_path, swarm["id"])
    revision = live["updatedAt"]
    assert manager.get(tmp_path, swarm["id"], since=revision) == {}
    statements: list[str] = []
    manager._db.set_trace_callback(statements.append)
    live["run"]["agentStates"]["lead"]["activity"] = "Thinking"
    manager._save(live)
    manager._db.set_trace_callback(None)
    assert not any("'archive'" in sql for sql in statements)
    assert sum(map(len, statements)) < 15000
    original_save = manager._store.pack

    def fail(*args):
        original_save(*args)
        raise RuntimeError("interrupted transaction")

    live["run"]["task"] = "Must roll back"
    with patch.object(manager._store, "pack", side_effect=fail), pytest.raises(RuntimeError):
        manager._save(live)
    assert manager.get(tmp_path, swarm["id"])["run"]["task"] == before["run"]["task"]
    assert manager.get(tmp_path, swarm["id"])["history"] == before["history"]
    manager.close()


@pytest.mark.asyncio
async def test_idle_runtime_waits_and_global_budget_is_fair(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False, max_concurrency=1)
    swarms = [manager.create(tmp_path, CONFIG) for _ in range(3)]
    reads = []
    manager._db.set_trace_callback(
        lambda sql: reads.append(sql) if sql.startswith("SELECT") else None
    )
    loop = asyncio.create_task(manager._loop())
    await asyncio.sleep(0.02)
    initial = len(reads)
    await asyncio.sleep(0.25)
    assert len(reads) == initial
    started = []
    release = asyncio.Event()

    async def turn(root, sid, aid, cancel):
        started.append(sid)
        assert len(manager._active) <= 1
        await release.wait()
        release.clear()
        manager.control(root, sid, "stop")
        manager._active.pop(f"{sid}:{aid}")
        manager._wake.set()

    with patch.object(manager, "_turn", side_effect=turn):
        for swarm in swarms:
            manager.start(tmp_path, swarm["id"], "Test")
        for count in range(1, 4):
            async with asyncio.timeout(1):
                while len(started) < count:
                    await asyncio.sleep(0.005)
            release.set()
            await asyncio.sleep(0.01)
        assert len(set(started)) == 3
    manager._closed.set()
    manager._wake.set()
    await loop
    manager.close()


def test_history_pages_remain_complete(tmp_path: Path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    swarm = manager.create(tmp_path, CONFIG)
    swarm["history"] = [{"id": str(i), "task": str(i)} for i in range(45)]
    manager._save(swarm)
    pages = [
        run for offset in (0, 20, 40) for run in manager.history(tmp_path, swarm["id"], offset)
    ]
    assert [run["id"] for run in pages] == list(map(str, reversed(range(45))))
    manager.close()
