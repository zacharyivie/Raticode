"""A queued triggered start outlasts the scheduler that queued it."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.run_values import MAX_SAVED_EVENTS
from gofer.rattish.trigger_approval import APPROVAL_SKIP_MESSAGE, UNAPPROVED_SKIP_MESSAGE
from gofer.rattish.trigger_service import TriggerEngine, _DroppedFiles, _Occurrence
from gofer.rattish.triggers import TriggerRecord
from tests.unit.test_rattish_triggers import (
    WORKFLOW,
    Clock,
    FakeRuns,
    _engine,
    _settled,
    _wait_for,
)

# Spelled out, so these tests also run against a scheduler from before this was kept.
NEVER_STARTED = "The scheduler stopped before this queued run started."
ACROSS_STOP = "The scheduler stopped, so this queued run starts when the scheduler runs again."
RESTORED = (
    "Queued before the scheduler restarted. Starts when no other run of this workflow is going."
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "project" / "workflow.rattish"
    path.parent.mkdir()
    path.write_text(WORKFLOW, encoding="utf-8")
    return path


def _queue_behind_a_held_run(
    data_dir: Path, source: Path
) -> tuple[TriggerEngine, FakeRuns, Clock, TriggerRecord, int]:
    """A webhook run that holds, and two more calls queued behind it.

    Returns the engine, its runner, the clock, the trigger and the queued entry's ID.
    """
    first, runs, clock = _engine(data_dir, runs=FakeRuns(hold=True))
    record, _ = first.store.create(kind="webhook", source_path=source, overlap="queue")
    first.become_owner()
    assert first.receive_webhook(record, {"call": 1}).status == "started"
    _wait_for(lambda: len(runs.started) == 1)
    queued = first.receive_webhook(record, {"call": 2})
    assert queued.status == "queued"
    # A later call folds in; a webhook's queued run takes the latest one.
    assert first.receive_webhook(record, {"call": 3}).event_id == queued.event_id
    return first, runs, clock, record, queued.event_id


@pytest.mark.parametrize("how", ["stopped", "crashed"])
def test_a_queued_webhook_start_runs_after_another_scheduler_takes_over(
    data_dir: Path, source: Path, how: str
) -> None:
    first, runs, clock, record, event_id = _queue_behind_a_held_run(data_dir, source)
    runs.release.set()
    _settled(first, runs, 1)
    if how == "stopped":
        first.stop(wait_seconds=1)
        entry = first.store.events(trigger_id=record.id)[0]
        assert (entry.id, entry.status) == (event_id, "queued")
        assert entry.message == ACROSS_STOP

    second, later_runs, _ = _engine(data_dir, clock=clock)
    second.become_owner()
    entry = second.store.events(trigger_id=record.id)[0]
    assert (entry.id, entry.status) == (event_id, "queued")
    assert entry.message == RESTORED
    assert entry.count == 2

    clock.advance(1)
    second.tick()
    _settled(second, later_runs, 1)

    assert len(later_runs.started) == 1
    (event,) = later_runs.started[0].events
    assert event["type"] == "webhook"
    assert event["trigger_id"] == record.id
    assert event["payload"] == {"call": 3}
    started = second.store.events(trigger_id=record.id)[0]
    assert started.id == event_id
    assert started.status == "passed"
    assert started.run_id == later_runs.started[0].run_id
    second._resign(wait_seconds=1)


def _drop_occurrence_column(store_path: Path) -> None:
    """Make the trigger store look like one saved before queued starts were kept."""
    db = sqlite3.connect(store_path)
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
        if "occurrence" in columns:
            db.execute("ALTER TABLE events DROP COLUMN occurrence")
        db.execute("PRAGMA user_version = 8")
        db.commit()
    finally:
        db.close()


def test_a_queued_entry_with_no_saved_start_closes_as_never_started(
    data_dir: Path, source: Path
) -> None:
    old, _, clock = _engine(data_dir)
    record, _ = old.store.create(kind="webhook", source_path=source, overlap="queue")
    old.store.add_event(record, status="running", at=clock(), run_id="run-1")
    old.store.add_event(record, status="queued", at=clock(), message="Waiting for run run-1.")
    _drop_occurrence_column(old.store.path)

    restarted, runs, _ = _engine(data_dir, clock=clock)
    restarted.become_owner()
    clock.advance(1)
    restarted.tick()

    queued, running = restarted.store.events(trigger_id=record.id)
    assert queued.status == "interrupted"
    assert queued.message == NEVER_STARTED
    assert "before this run finished" not in queued.message
    assert running.status == "interrupted"
    assert running.message == "The scheduler stopped before this run finished."
    assert runs.started == []
    restarted._resign(wait_seconds=1)


@pytest.mark.parametrize("change", ["turned off", "removed", "paused"])
def test_a_queued_start_whose_trigger_cant_run_closes_as_never_started(
    data_dir: Path, source: Path, change: str
) -> None:
    first, runs, clock, record, event_id = _queue_behind_a_held_run(data_dir, source)
    runs.release.set()
    _settled(first, runs, 1)
    first.stop(wait_seconds=1)
    if change == "turned off":
        first.store.update(record.id, enabled=False)
    elif change == "removed":
        first.store.delete(record.id)
    else:
        first.store.set_paused(True, now=clock())

    second, later_runs, _ = _engine(data_dir, clock=clock)
    second.become_owner()
    clock.advance(1)
    second.tick()

    assert later_runs.started == []
    assert second.store.queued_occurrences() == []
    if change != "removed":
        entry = next(item for item in second.store.events() if item.id == event_id)
        assert entry.status == "interrupted"
        assert entry.message == NEVER_STARTED
    second._resign(wait_seconds=1)


def test_a_restored_queued_start_still_needs_the_approved_workflow(
    data_dir: Path, source: Path
) -> None:
    first, runs, clock, record, event_id = _queue_behind_a_held_run(data_dir, source)
    runs.release.set()
    _settled(first, runs, 1)
    first.stop(wait_seconds=1)
    source.write_text(WORKFLOW.replace("echo hello", "echo changed"), encoding="utf-8")

    second, later_runs, _ = _engine(data_dir, clock=clock)
    second.become_owner()
    clock.advance(1)
    second.tick()

    assert later_runs.started == []
    entry = second.store.events(trigger_id=record.id)[0]
    assert entry.id == event_id
    assert entry.status == "skipped"
    assert entry.message in {APPROVAL_SKIP_MESSAGE, UNAPPROVED_SKIP_MESSAGE}
    second._resign(wait_seconds=1)


def test_a_queued_start_is_held_to_the_saved_values_limits(data_dir: Path, source: Path) -> None:
    from gofer.rattish.trigger_service import _occurrence_json

    engine, _, _ = _engine(data_dir)
    files, _ = engine.store.create(
        kind="file", source_path=source, config={"path": ".", "glob": "*"}
    )
    events = [{"type": "file", "path": f"/x/{index}.txt"} for index in range(MAX_SAVED_EVENTS + 5)]
    saved = _occurrence_json(_Occurrence(files, events=events, dropped=_DroppedFiles(unnamed=2)))
    assert saved is not None
    kept = json.loads(saved)
    assert len(kept["events"]) == MAX_SAVED_EVENTS
    assert kept["events"][0]["path"] == "/x/0.txt"
    assert kept["dropped"] == 7

    hook, _ = engine.store.create(kind="webhook", source_path=source)
    too_big = {"type": "webhook", "payload": "x" * (1024 * 1024 + 1)}
    assert _occurrence_json(_Occurrence(hook, events=[too_big])) is None
    assert _occurrence_json(_Occurrence(hook, events=[{"payload": float("nan")}])) is None


def test_a_queued_start_that_failed_once_still_runs_after_a_scheduler_restart(
    data_dir: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, runs, clock, record, event_id = _queue_behind_a_held_run(data_dir, source)
    runs.release.set()
    _settled(first, runs, 1)
    real_start_spend = first.store.start_spend
    raised: list[int] = []

    def fail_once(*args: Any, **kwargs: Any) -> None:
        if not raised:
            raised.append(1)
            raise sqlite3.OperationalError("database is locked")
        real_start_spend(*args, **kwargs)

    # The entry turns running, then the start fails and the entry goes back to queued.
    monkeypatch.setattr(first.store, "start_spend", fail_once)
    clock.advance(1)
    first.tick()
    assert raised == [1]
    entry = first.store.events(trigger_id=record.id)[0]
    assert (entry.id, entry.status) == (event_id, "queued")
    first.stop(wait_seconds=1)
    entry = first.store.events(trigger_id=record.id)[0]
    assert (entry.id, entry.status, entry.message) == (event_id, "queued", ACROSS_STOP)

    second, later_runs, _ = _engine(data_dir, clock=clock)
    second.become_owner()
    entry = second.store.events(trigger_id=record.id)[0]
    assert (entry.id, entry.status, entry.message) == (event_id, "queued", RESTORED)
    clock.advance(1)
    second.tick()
    _settled(second, later_runs, 1)

    (event,) = later_runs.started[0].events
    assert event["payload"] == {"call": 3}
    started = second.store.events(trigger_id=record.id)[0]
    assert (started.id, started.status) == (event_id, "passed")
    second._resign(wait_seconds=1)
