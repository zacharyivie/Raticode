from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gofer.rattish import run_service
from gofer.rattish.run_service import process_start_token
from gofer.ui import scratch_cleanup
from gofer.ui.rem_home_store import RemHomeStore
from gofer.ui.scratch_cleanup import STALE_SECONDS, clean_stale_scratch

DAY = STALE_SECONDS
DEAD_PID = 2147483647


def _folder(path: Path, *, age: float, size: int = 4096) -> Path:
    """A scratch folder whose newest file changed ``age`` seconds ago."""
    (path / "cache").mkdir(parents=True)
    data = path / "cache" / "blob"
    data.write_bytes(b"x" * size)
    stamp = time.time() - age
    for item in (data, path / "cache", path):
        os.utime(item, (stamp, stamp))
    return path


def _rem_run(data: Path, run_id: str, state: str, turn: str | None = None) -> None:
    RemHomeStore(data)
    with sqlite3.connect(data / "rem-home.sqlite3") as db:
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?)",
            (
                run_id,
                "task",
                f"occurrence-{run_id}",
                state,
                json.dumps({"id": run_id, "state": state, "active_turn_id": turn}),
            ),
        )


def _workflow_record(data: Path, run_id: str, **fields: object) -> None:
    folder = data / "radish" / "runs" / "flow"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, **fields}))


def test_stale_folders_of_finished_turns_and_runs_are_removed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data = tmp_path / "data"
    _rem_run(data, "run-a", "succeeded", turn="turn-1")
    turn = _folder(data / "rem-work" / "turn-scratch" / "run-a-verify", age=2 * DAY)
    review = _folder(data / "rem-work" / "review-scratch" / "turn-1", age=2 * DAY)
    unknown = _folder(data / "rem-work" / "turn-scratch" / "gone-run-execute", age=2 * DAY)
    _workflow_record(data, "20261101T000000000000Z-aaaa", status="passed")
    finished = _folder(data / "run-scratch" / "20261101T000000000000Z-aaaa", age=2 * DAY)
    crashed_id = "20261101T000000000000Z-bbbb"
    _workflow_record(data, crashed_id, status="running", runner_pid=DEAD_PID)
    crashed = _folder(data / "run-scratch" / crashed_id, age=2 * DAY)
    orphan = _folder(data / "run-scratch" / "no-record", age=2 * DAY)

    with caplog.at_level(logging.INFO, logger=scratch_cleanup.log.name):
        result = clean_stale_scratch(data)

    for folder in (turn, review, unknown, finished, crashed, orphan):
        assert not folder.exists(), folder
    assert result.removed == 6
    assert result.kept == 0
    assert result.freed_bytes >= 6 * 4096
    assert "Removed 6 leftover sandbox scratch folder(s), freeing" in caplog.text
    # The parent folders stay for the next turns and runs.
    assert (data / "rem-work" / "turn-scratch").is_dir()
    assert (data / "run-scratch").is_dir()


def test_recent_folders_are_kept_even_without_a_live_owner(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _rem_run(data, "run-a", "failed", turn="turn-1")
    turn = _folder(data / "rem-work" / "turn-scratch" / "run-a-execute", age=DAY - 60)
    review = _folder(data / "rem-work" / "review-scratch" / "turn-1", age=60)
    run = _folder(data / "run-scratch" / "no-record", age=DAY - 60)
    # An old folder holding one recently changed file is still in use.
    touched = _folder(data / "run-scratch" / "touched", age=2 * DAY)
    (touched / "cache" / "fresh").write_text("new")
    os.utime(touched / "cache", (time.time() - 2 * DAY,) * 2)

    result = clean_stale_scratch(data)

    assert all(folder.exists() for folder in (turn, review, run, touched))
    assert result.removed == 0 and result.freed_bytes == 0
    assert result.kept == 4


def test_old_folders_of_active_turns_and_runs_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    _rem_run(data, "run-a", "executing", turn="turn-1")
    _rem_run(data, "run-b", "verifying", turn="turn-2")
    turn = _folder(data / "rem-work" / "turn-scratch" / "run-a-execute", age=3 * DAY)
    review = _folder(data / "rem-work" / "review-scratch" / "turn-2", age=3 * DAY)
    # A workflow run another live process runs (this test's parent process).
    parent = os.getppid()
    elsewhere = "20261101T000000000000Z-cccc"
    _workflow_record(
        data,
        elsewhere,
        status="running",
        runner_pid=parent,
        runner_start=process_start_token(parent),
    )
    other_process = _folder(data / "run-scratch" / elsewhere, age=3 * DAY)
    # A run this process is running, whose record isn't saved yet.
    here = "20261101T000000000000Z-dddd"
    monkeypatch.setattr(run_service, "_active_rattish_ids", {here})
    this_process = _folder(data / "run-scratch" / here, age=3 * DAY)

    result = clean_stale_scratch(data)

    assert all(folder.exists() for folder in (turn, review, other_process, this_process))
    assert result.removed == 0 and result.kept == 4


def test_a_managed_workflow_run_record_in_its_project_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    root = tmp_path / "project" / ".raticode" / "flow"
    (root / "logs").mkdir(parents=True)
    parent = os.getppid()
    run_id = "20261101T000000000000Z-eeee"
    (root / "logs" / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "running",
                "runner_pid": parent,
                "runner_start": process_start_token(parent),
            }
        )
    )
    workflow = SimpleNamespace(managed=True, workflow_root=root)
    monkeypatch.setattr(
        "gofer.rattish.workspaces.list_registered_workflows",
        lambda *, registry_dir=None: (workflow,),
    )
    active = _folder(data / "run-scratch" / run_id, age=3 * DAY)
    stale = _folder(data / "run-scratch" / "20261101T000000000000Z-ffff", age=3 * DAY)

    result = clean_stale_scratch(data)

    assert active.exists() and not stale.exists()
    assert result.removed == 1 and result.kept == 1


def test_unreadable_records_keep_folders(tmp_path: Path) -> None:
    data = tmp_path / "data"
    turn = _folder(data / "rem-work" / "turn-scratch" / "run-a-execute", age=3 * DAY)
    (data / "rem-home.sqlite3").write_text("not a database")
    run_id = "20261101T000000000000Z-gggg"
    folder = data / "radish" / "runs" / "flow"
    folder.mkdir(parents=True)
    (folder / f"{run_id}.json").write_text("{broken")
    run = _folder(data / "run-scratch" / run_id, age=3 * DAY)

    result = clean_stale_scratch(data)

    assert turn.exists() and run.exists()
    assert result.removed == 0 and result.kept == 2


def test_links_are_removed_without_following_them(tmp_path: Path) -> None:
    data = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("mine")
    stale = _folder(data / "run-scratch" / "no-record", age=3 * DAY)
    (stale / "link").symlink_to(outside, target_is_directory=True)
    os.utime(stale / "link", (time.time() - 3 * DAY,) * 2, follow_symlinks=False)
    os.utime(stale, (time.time() - 3 * DAY,) * 2)
    # A linked scratch root is left alone.
    (data / "rem-work").mkdir(parents=True)
    (data / "rem-work" / "turn-scratch").symlink_to(outside, target_is_directory=True)

    result = clean_stale_scratch(data)

    assert not stale.exists()
    assert (outside / "keep").read_text() == "mine"
    assert result.removed == 1


def test_no_scratch_folders_is_a_quiet_no_op(tmp_path: Path) -> None:
    result = clean_stale_scratch(tmp_path / "missing")
    assert (result.removed, result.kept, result.freed_bytes) == (0, 0, 0)
    assert not (tmp_path / "missing").exists()
