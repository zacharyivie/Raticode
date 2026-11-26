"""A trigger's Time limit stops a hung triggered run, and busy starts name the live run."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.rattish.run_service import new_run_id, run_rattish_file
from gofer.rattish.trigger_defaults import format_run_time, parse_run_time
from gofer.rattish.trigger_service import TriggerEngine, run_in_process
from gofer.rattish.triggers import (
    DEFAULT_MAX_RUN_SECONDS,
    TriggerError,
    TriggerStore,
    utc_now,
)
from tests.unit.test_rattish_triggers import (
    START,
    FakeRuns,
    _engine,
    _settled,
    _statuses,
    _wait_for,
)

runner = CliRunner()
EVERY_MINUTE = {"expression": "* * * * *", "timezone": "UTC"}
HUNG_WORKFLOW = """Rattish: 1

Workflow:
  name: Hung Trigger

Node wait:
  type: bash-command
  command: sleep 600
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    path = project / "workflow.rattish"
    path.write_text(HUNG_WORKFLOW, encoding="utf-8")
    return path


def _cron(store: TriggerStore, source: Path, **options: Any) -> str:
    record, _ = store.create(
        kind="cron",
        source_path=source,
        config=EVERY_MINUTE,
        now=START - timedelta(seconds=20),
        **options,
    )
    return record.id


def _run_record(data_dir: Path, run_id: str) -> dict[str, Any]:
    records = [
        path
        for path in data_dir.rglob("*.json")
        if path.stem == run_id and path.parent.name != "ir"
    ]
    assert len(records) == 1
    return dict(json.loads(records[0].read_text(encoding="utf-8")))


def test_durations_read_and_print_as_people_write_them() -> None:
    assert parse_run_time("2h") == 7200
    assert parse_run_time("1h30m") == 5400
    assert parse_run_time("90s") == 90
    assert parse_run_time(" 30 min ") == 1800
    for nothing in ("none", "None", "off", "0"):
        assert parse_run_time(nothing) is None
    for bad in ("30", "2 weeks", "soon", ""):
        with pytest.raises(ValueError):
            parse_run_time(bad)
    assert format_run_time(2) == "2 s"
    assert format_run_time(6 * 3600) == "6 h"
    assert format_run_time(7 * 3600 + 12 * 60 + 40) == "7 h 12 min"
    assert format_run_time(90) == "1 min 30 s"


def test_triggers_get_a_six_hour_time_limit_unless_changed(data_dir: Path, source: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, source)
    record = store.get(trigger_id)
    assert record.max_run_seconds == DEFAULT_MAX_RUN_SECONDS == 6 * 3600
    assert record.to_payload()["maxRunSeconds"] == 6 * 3600

    assert store.update(trigger_id, label="Nightly").max_run_seconds == 6 * 3600
    assert store.update(trigger_id, max_run_seconds=120).max_run_seconds == 120
    assert store.update(trigger_id, max_run_seconds=None, actor="user").max_run_seconds is None
    assert store.get(trigger_id).max_run_seconds is None
    assert _cron(store, source, max_run_seconds=0)
    with pytest.raises(TriggerError, match="whole number"):
        store.update(trigger_id, max_run_seconds=1.5)
    with pytest.raises(TriggerError, match="at most 30 days"):
        store.update(trigger_id, max_run_seconds=31 * 86400)
    with pytest.raises(TriggerError, match="negative"):
        store.update(trigger_id, max_run_seconds=-5)


def test_triggers_saved_before_time_limits_get_the_default(data_dir: Path, source: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, source)
    # Turn the database back into one from before the Time limit existed.
    db = sqlite3.connect(store.path)
    db.execute("ALTER TABLE triggers DROP COLUMN max_run_seconds")
    db.execute("PRAGMA user_version = 5")
    db.commit()
    db.close()

    upgraded = TriggerStore(data_dir)
    assert upgraded.get(trigger_id).max_run_seconds == 6 * 3600


def test_a_hung_triggered_run_stops_at_the_time_limit(data_dir: Path, source: Path) -> None:
    engine, _, clock = _engine(data_dir)
    engine._runner = run_in_process
    trigger_id = _cron(engine.store, source, max_run_seconds=2)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _statuses(engine.store, trigger_id) == ["running"], timeout=30)

    _wait_for(lambda: _statuses(engine.store, trigger_id) == ["failed"], timeout=60)
    event = engine.store.events(trigger_id=trigger_id)[0]
    assert event.message == (
        "Stopped after 2 s, this trigger's time limit. Raise the limit or set a timeout in "
        "the workflow."
    )
    assert event.run_id is not None
    _wait_for(lambda: not engine._threads, timeout=30)
    assert _run_record(data_dir, event.run_id)["status"] == "stopped"
    assert not engine._time_limits and not engine._timed_out


def test_a_run_that_finishes_in_time_is_not_touched(data_dir: Path, source: Path) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, source, max_run_seconds=1)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _settled(engine, runs, 1)
    assert _statuses(engine.store, trigger_id) == ["passed"]
    assert runs.started[0].cancel_event.is_set() is False
    assert not engine._time_limits


def test_a_trigger_with_no_time_limit_starts_no_timer(data_dir: Path, source: Path) -> None:
    runs = FakeRuns(hold=True)
    engine, _, clock = _engine(data_dir, runs=runs)
    _cron(engine.store, source, max_run_seconds=None)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: len(runs.started) == 1)
    assert engine._time_limits == {}
    runs.release.set()
    _settled(engine, runs, 1)


def test_a_skipped_start_names_the_live_run_and_how_long_it_has_run(
    data_dir: Path, source: Path
) -> None:
    runs = FakeRuns(hold=True)
    engine, _, clock = _engine(data_dir, runs=runs)
    trigger_id = _cron(engine.store, source, overlap="skip")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: len(runs.started) == 1)
    live = runs.started[0].run_id

    clock.advance(7 * 3600 + 12 * 60)
    engine.tick()

    skipped = engine.store.events(trigger_id=trigger_id)[0]
    assert skipped.status == "skipped"
    assert skipped.message == f"Skipped because run {live} has been running for 7 h 12 min."
    runs.release.set()
    _settled(engine, runs, 1)


def test_a_queued_start_names_the_run_it_waits_for(data_dir: Path, source: Path) -> None:
    runs = FakeRuns(hold=True)
    engine, _, clock = _engine(data_dir, runs=runs)
    trigger_id = _cron(engine.store, source, overlap="queue")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: len(runs.started) == 1)
    live = runs.started[0].run_id

    clock.advance(60)
    engine.tick()
    queued = engine.store.events(trigger_id=trigger_id)[0]
    assert queued.status == "queued"
    assert queued.message == f"Waiting for run {live} to finish. It has run for 1 min."
    clock.advance(60)
    engine.tick()
    folded = engine.store.events(trigger_id=trigger_id)[0]
    assert folded.message == (
        f"Waiting for run {live} to finish. It has run for 2 min. 2 starts folded into this one."
    )
    runs.release.set()
    _wait_for(lambda: runs.finished == 1 and not engine._active)
    clock.advance(1)
    engine.tick()
    _settled(engine, runs, 2)


def test_a_skipped_start_names_a_run_started_by_hand(data_dir: Path, source: Path) -> None:
    """A run started outside the scheduler is found from its run record."""
    run_id = new_run_id()
    cancel = threading.Event()
    manual = threading.Thread(
        target=lambda: asyncio.run(
            run_rattish_file(
                source,
                data_dir=data_dir,
                run_id=run_id,
                cancel_event=cancel,
                started_by="manual",
            )
        ),
        daemon=True,
    )
    manual.start()
    try:
        engine = TriggerEngine(
            data_dir,
            runner=FakeRuns(),
            clock=utc_now,
            enable_webhooks=False,
            enable_watchers=False,
        )
        store = engine.store
        record, _ = store.create(kind="webhook", source_path=source, overlap="skip")
        engine.become_owner()
        _wait_for(lambda: engine._workflow_busy(record.source_path), timeout=30)

        outcome = engine.receive_webhook(record, None)

        assert outcome.status == "skipped"
        message = store.events(trigger_id=record.id)[0].message
        assert message.startswith(f"Skipped because run {run_id} has been running for ")
        assert message.endswith(" s.")
    finally:
        cancel.set()
        manual.join(timeout=30)


def test_cli_sets_and_lists_the_time_limit(data_dir: Path, source: Path) -> None:
    base = ("--data-dir", str(data_dir))

    def cli(*arguments: str) -> dict[str, Any]:
        result = runner.invoke(app, ["rattish", "triggers", *arguments, "--format", "json"])
        assert result.exit_code == 0, result.output
        return dict(json.loads(result.output))

    created = cli("add", "webhook", str(source), *base)["trigger"]
    assert created["maxRunSeconds"] == 6 * 3600
    assert (
        cli("add", "webhook", str(source), "--max-run-time", "none", *base)["trigger"][
            "maxRunSeconds"
        ]
        is None
    )

    updated = cli("update", created["id"], "--max-run-time", "30m", *base)["trigger"]
    assert updated["maxRunSeconds"] == 1800
    listed = cli("list", *base)["triggers"]
    assert next(item for item in listed if item["id"] == created["id"])["maxRunSeconds"] == 1800

    text = runner.invoke(app, ["rattish", "triggers", "list", *base])
    assert "time-limit=30min" in text.output
    assert "time-limit=none" in text.output

    bad = runner.invoke(
        app,
        ["rattish", "triggers", "update", created["id"], "--max-run-time", "30", *base],
    )
    assert bad.exit_code == 1
    assert "isn't a duration" in bad.output
    assert TriggerStore(data_dir).get(created["id"]).max_run_seconds == 1800
