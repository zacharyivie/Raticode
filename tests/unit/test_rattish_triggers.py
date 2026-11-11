from __future__ import annotations

import json
import shlex
import socket
import stat
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.rattish.run_service import RattishRunResult
from gofer.rattish.trigger_login import login_status, scheduler_command, set_login_enabled
from gofer.rattish.trigger_service import (
    SELF_TRIGGER_LIMIT,
    TriggerEngine,
    TriggerRun,
    run_in_process,
    scheduler_status,
)
from gofer.rattish.triggers import (
    TriggerError,
    TriggerStore,
    occurrences_between,
    parse_cron,
    preview_cron,
)

runner = CliRunner()
START = datetime(2026, 11, 9, 12, 0, 30, tzinfo=UTC)
WORKFLOW = """Rattish: 1

Workflow:
  name: Trigger Test

Node hello:
  type: bash-command
  command: echo hello
"""
WORKFLOW_WITH_INPUT = """Rattish: 1

Workflow:
  name: Trigger Input
  inputs:
    topic:
      schema: {"type": "string"}
      required: true

Node hello:
  type: bash-command
  command: echo hello
"""


_EVERY_SEVEN_MINUTES = {"expression": "*/7 * * * *", "timezone": "UTC"}


class Clock:
    def __init__(self, moment: datetime = START) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> datetime:
        self.moment += timedelta(seconds=seconds)
        return self.moment


class FakeRuns:
    """Stands in for the real runner, optionally holding runs open."""

    def __init__(self, status: str = "passed", hold: bool = False) -> None:
        self.status = status
        self.hold = hold
        self.release = threading.Event()
        self.started: list[TriggerRun] = []
        self.finished = 0

    def __call__(self, run: TriggerRun) -> RattishRunResult:
        self.started.append(run)
        if self.hold:
            assert self.release.wait(10)
        self.finished += 1
        error = None if self.status == "passed" else {"message": "Node hello exited with 1."}
        return cast(
            RattishRunResult,
            SimpleNamespace(document={"status": self.status, "error": error}),
        )


def _wait_for(condition: Callable[[], bool], *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met in time")


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
    path.write_text(WORKFLOW, encoding="utf-8")
    return path


def _engine(
    data_dir: Path,
    *,
    runs: FakeRuns | None = None,
    clock: Clock | None = None,
    **options: Any,
) -> tuple[TriggerEngine, FakeRuns, Clock]:
    actual_runs = runs or FakeRuns()
    actual_clock = clock or Clock()
    options.setdefault("enable_webhooks", False)
    options.setdefault("enable_watchers", False)
    return (
        TriggerEngine(data_dir, runner=actual_runs, clock=actual_clock, **options),
        actual_runs,
        actual_clock,
    )


def _cron(store: TriggerStore, source: Path, **options: Any) -> str:
    options.setdefault("now", START - timedelta(seconds=20))
    config = options.pop("config", {"expression": "* * * * *", "timezone": "UTC"})
    record, _ = store.create(kind="cron", source_path=source, config=config, **options)
    return record.id


def _statuses(store: TriggerStore, trigger_id: str | None = None) -> list[str]:
    return [item.status for item in store.events(trigger_id=trigger_id)]


def _settled(engine: TriggerEngine, runs: FakeRuns, expected: int) -> None:
    _wait_for(lambda: runs.finished >= expected and not engine._threads)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_crontab_weekdays_mean_what_cron_means() -> None:
    # 2026-11-09 is a Monday. APScheduler alone would read 1 as Tuesday.
    monday = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    sunday = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
    before = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    weekly = occurrences_between(
        parse_cron("0 9 * * 1", "UTC"), before, monday + timedelta(hours=1)
    )
    assert weekly == [monday]

    for expression in ("0 9 * * 0", "0 9 * * 7", "0 9 * * sun"):
        found = occurrences_between(
            parse_cron(expression, "UTC"), before, monday + timedelta(hours=1)
        )
        assert found == [sunday], expression


def test_cron_runs_in_the_chosen_time_zone_across_daylight_saving() -> None:
    after_fall_back = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    assert preview_cron("0 9 * * *", "America/New_York", count=1, now=after_fall_back) == [
        "2026-11-02T14:00:00+00:00"
    ]
    before_fall_back = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
    assert preview_cron("0 9 * * *", "America/New_York", count=1, now=before_fall_back) == [
        "2026-10-30T13:00:00+00:00"
    ]


@pytest.mark.parametrize(
    ("expression", "timezone", "fragment"),
    [
        ("* * * *", "UTC", "five fields"),
        ("99 * * * *", "UTC", "isn't valid"),
        ("* * * * *", "Mars/Olympus", "Unknown time zone"),
        ("0 0 13 * 5", "UTC", "not both"),
        ("0 0 * * 9", "UTC", "Weekday"),
        ("0 0 * * 5-1", "UTC", "backwards"),
    ],
)
def test_bad_schedules_are_refused_with_a_reason(
    expression: str, timezone: str, fragment: str
) -> None:
    with pytest.raises(TriggerError, match=fragment):
        parse_cron(expression, timezone)


def test_cron_fires_once_for_each_scheduled_time(data_dir: Path, source: Path) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, source)
    engine.become_owner()

    engine.tick()
    assert runs.started == []

    clock.advance(31)  # 12:01:01, just past the 12:01 occurrence
    engine.tick()
    engine.tick()
    clock.advance(10)
    engine.tick()
    _settled(engine, runs, 1)

    assert len(runs.started) == 1
    assert runs.started[0].events == []  # A cron run stays resumable.
    assert _statuses(engine.store, trigger_id) == ["passed"]
    assert engine.store.events(trigger_id=trigger_id)[0].run_id == runs.started[0].run_id

    clock.advance(60)
    engine.tick()
    _settled(engine, runs, 2)
    assert len(runs.started) == 2


def test_times_missed_while_nothing_ran_are_recorded_not_run(data_dir: Path, source: Path) -> None:
    first, _, clock = _engine(data_dir)
    trigger_id = _cron(first.store, source, config=_EVERY_SEVEN_MINUTES)
    first.become_owner()
    first.tick()
    first._heartbeat(clock())

    clock.advance(3600 + 240)
    restarted, runs, _ = _engine(data_dir, clock=clock)
    restarted.become_owner()
    restarted.tick()

    assert runs.started == []
    missed = restarted.store.events(trigger_id=trigger_id)[0]
    assert missed.status == "missed"
    assert missed.count == 9
    assert "weren't running" in missed.message
    assert missed.scheduled_for is not None


def test_run_once_catches_up_a_single_run(data_dir: Path, source: Path) -> None:
    first, _, clock = _engine(data_dir)
    trigger_id = _cron(first.store, source, missed="run_once", config=_EVERY_SEVEN_MINUTES)
    first.become_owner()
    first.tick()
    first._heartbeat(clock())

    clock.advance(3600 + 240)
    restarted, runs, _ = _engine(data_dir, clock=clock)
    restarted.become_owner()
    restarted.tick()
    _settled(restarted, runs, 1)

    assert len(runs.started) == 1
    events = restarted.store.events(trigger_id=trigger_id)
    assert {item.status for item in events} == {"missed", "passed"}
    assert next(item for item in events if item.status == "passed").message.startswith(
        "Started to catch up"
    )


def test_a_trigger_that_was_off_missed_nothing(data_dir: Path, source: Path) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, source)
    engine.become_owner()
    engine.store.update(trigger_id, enabled=False, now=clock())
    engine.tick()

    clock.advance(3600)
    engine.tick()
    engine.store.update(trigger_id, enabled=True, now=clock())
    clock.advance(1)
    engine.tick()

    assert runs.started == []
    assert _statuses(engine.store, trigger_id) == []


def test_skip_overlap_records_the_skipped_run(data_dir: Path, source: Path) -> None:
    runs = FakeRuns(hold=True)
    engine, _, clock = _engine(data_dir, runs=runs)
    trigger_id = _cron(engine.store, source, overlap="skip")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: len(runs.started) == 1)

    clock.advance(60)
    engine.tick()

    assert len(runs.started) == 1
    assert _statuses(engine.store, trigger_id) == ["skipped", "running"]
    assert "already running" in engine.store.events(trigger_id=trigger_id)[0].message
    runs.release.set()
    _settled(engine, runs, 1)


def test_queue_overlap_folds_starts_into_one_run_after_the_current_one(
    data_dir: Path, source: Path
) -> None:
    runs = FakeRuns(hold=True)
    engine, _, clock = _engine(data_dir, runs=runs)
    trigger_id = _cron(engine.store, source, overlap="queue")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: len(runs.started) == 1)

    for _ in range(3):
        clock.advance(60)
        engine.tick()
    assert len(runs.started) == 1
    assert _statuses(engine.store, trigger_id) == ["queued", "running"]
    assert engine.store.events(trigger_id=trigger_id)[0].count == 3

    runs.release.set()
    _wait_for(lambda: runs.finished == 1 and not engine._active)
    clock.advance(1)
    engine.tick()
    _settled(engine, runs, 2)
    assert len(runs.started) == 2
    assert _statuses(engine.store, trigger_id) == ["passed", "passed"]


def test_parallel_overlap_starts_another_run(data_dir: Path, source: Path) -> None:
    runs = FakeRuns(hold=True)
    engine, _, clock = _engine(data_dir, runs=runs)
    _cron(engine.store, source, overlap="parallel")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: len(runs.started) == 1)
    clock.advance(60)
    engine.tick()
    _wait_for(lambda: len(runs.started) == 2)
    runs.release.set()
    _settled(engine, runs, 2)


def test_a_failed_run_shows_why_in_trigger_history(data_dir: Path, source: Path) -> None:
    engine, runs, clock = _engine(data_dir, runs=FakeRuns(status="failed"))
    trigger_id = _cron(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _settled(engine, runs, 1)
    _wait_for(lambda: _statuses(engine.store, trigger_id) == ["failed"])
    assert engine.store.events(trigger_id=trigger_id)[0].message == "Node hello exited with 1."


def test_runs_use_the_same_path_as_rattish_run(data_dir: Path, source: Path) -> None:
    engine, _, clock = _engine(data_dir)
    engine._runner = run_in_process
    trigger_id = _cron(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _statuses(engine.store, trigger_id) == ["passed"], timeout=60)

    run_id = engine.store.events(trigger_id=trigger_id)[0].run_id
    records = [
        path
        for path in data_dir.rglob("*.json")
        if path.stem == run_id and path.parent.name != "ir"
    ]
    assert len(records) == 1
    document = json.loads(records[0].read_text(encoding="utf-8"))
    assert document["status"] == "passed"
    assert document["runs"][0]["node_id"] == "hello"


def test_a_missing_workflow_is_a_visible_failure(data_dir: Path, source: Path) -> None:
    engine, _, clock = _engine(data_dir)
    engine._runner = run_in_process
    trigger_id = _cron(engine.store, source)
    source.unlink()
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _statuses(engine.store, trigger_id) == ["failed"], timeout=60)
    assert engine.store.events(trigger_id=trigger_id)[0].message


def test_file_changes_wait_for_quiet_then_start_one_run(
    data_dir: Path, source: Path, tmp_path: Path
) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    engine, runs, clock = _engine(data_dir)
    record, _ = engine.store.create(
        kind="file",
        source_path=source,
        config={"path": str(watched), "debounceSeconds": 5},
        now=START,
    )
    engine.become_owner()
    for second in range(4):
        engine.note_file_change(record.id, "modified", watched / f"file-{second}.txt")
        clock.advance(2)
        engine.tick()
    assert runs.started == []

    clock.advance(5)
    engine.tick()
    _settled(engine, runs, 1)

    assert len(runs.started) == 1
    assert sorted(item["name"] for item in runs.started[0].events) == [
        "file-0.txt",
        "file-1.txt",
        "file-2.txt",
        "file-3.txt",
    ]


def test_constant_changes_still_start_a_run_eventually(
    data_dir: Path, source: Path, tmp_path: Path
) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    engine, runs, clock = _engine(data_dir)
    record, _ = engine.store.create(
        kind="file",
        source_path=source,
        config={"path": str(watched), "debounceSeconds": 5},
        now=START,
    )
    engine.become_owner()
    for _ in range(70):
        engine.note_file_change(record.id, "modified", watched / "busy.log")
        clock.advance(1)
        engine.tick()
    _settled(engine, runs, 1)
    assert len(runs.started) == 1


def test_real_file_events_start_the_workflow(data_dir: Path, source: Path, tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    (watched / "sub").mkdir(parents=True)
    (watched / ".git").mkdir()
    runs = FakeRuns()
    engine = TriggerEngine(
        data_dir, runner=runs, poll_seconds=0.02, standby_seconds=0.02, enable_webhooks=False
    )
    record, _ = engine.store.create(
        kind="file",
        source_path=source,
        config={"path": str(watched), "debounceSeconds": 0.1, "glob": "*.txt"},
    )
    engine.start()
    try:
        _wait_for(lambda: engine.is_owner and record.id in engine._watches)
        (watched / "ignored.log").write_text("no", encoding="utf-8")
        (watched / ".git" / "HEAD.txt").write_text("no", encoding="utf-8")
        time.sleep(0.4)
        assert runs.started == []

        (watched / "sub" / "wanted.txt").write_text("yes", encoding="utf-8")
        _wait_for(lambda: len(runs.started) == 1, timeout=10)
        assert runs.started[0].events[0]["name"] == "wanted.txt"
        assert runs.started[0].events[0]["type"] == "file"
    finally:
        engine.stop()


def test_two_triggers_can_watch_one_folder(data_dir: Path, source: Path, tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    runs = FakeRuns()
    engine = TriggerEngine(
        data_dir, runner=runs, poll_seconds=0.02, standby_seconds=0.02, enable_webhooks=False
    )
    config = {"path": str(watched), "debounceSeconds": 0.1}
    first, _ = engine.store.create(kind="file", source_path=source, config=config)
    second, _ = engine.store.create(
        kind="file", source_path=source, config=config, overlap="parallel"
    )
    engine.start()
    try:
        _wait_for(lambda: first.id in engine._watches and second.id in engine._watches)
        engine.store.delete(first.id)
        _wait_for(lambda: first.id not in engine._watches)

        (watched / "still-seen.txt").write_text("yes", encoding="utf-8")
        _wait_for(lambda: len(runs.started) == 1, timeout=10)
    finally:
        engine.stop()


def test_a_workflow_that_keeps_starting_itself_gets_turned_off(
    data_dir: Path, source: Path, tmp_path: Path
) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    runs = FakeRuns(hold=True)
    engine, _, clock = _engine(data_dir, runs=runs)
    record, _ = engine.store.create(
        kind="file",
        source_path=source,
        config={"path": str(watched), "debounceSeconds": 1},
        overlap="parallel",
        now=START,
    )
    engine.become_owner()
    # The first change comes from outside; every later one lands during a run.
    for attempt in range(SELF_TRIGGER_LIMIT + 1):
        engine.note_file_change(record.id, "modified", watched / "output.txt")
        clock.advance(2)
        engine.tick()
        _wait_for(lambda: len(runs.started) == min(attempt + 1, SELF_TRIGGER_LIMIT))

    runs.release.set()
    _settled(engine, runs, SELF_TRIGGER_LIMIT)

    assert engine.store.get(record.id).enabled is False
    assert len(runs.started) == SELF_TRIGGER_LIMIT
    newest = engine.store.events(trigger_id=record.id, limit=200)
    problem = next(item for item in newest if item.status == "failed")
    assert "changing the files it watches" in problem.message


def _post(
    url: str, *, body: bytes = b"{}", headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def webhook(data_dir: Path, source: Path) -> Iterator[tuple[TriggerEngine, FakeRuns, str, str]]:
    runs = FakeRuns()
    engine = TriggerEngine(
        data_dir, runner=runs, poll_seconds=0.02, standby_seconds=0.02, enable_watchers=False
    )
    port = _free_port()
    engine.store.set_webhook_port(port)
    record, token = engine.store.create(kind="webhook", source_path=source)
    assert token is not None
    engine.start()
    try:
        _wait_for(lambda: engine._webhook_server is not None)
        yield engine, runs, f"http://127.0.0.1:{port}/rattish-hooks/{record.id}", token
    finally:
        engine.stop()


def test_webhook_requires_its_token(webhook: tuple[TriggerEngine, FakeRuns, str, str]) -> None:
    engine, runs, url, token = webhook
    bearer = {"Authorization": f"Bearer {token}"}

    assert _post(url)[0] == 401
    assert _post(url, headers={"Authorization": "Bearer wrong"})[0] == 401
    assert _post(f"{url}x", headers=bearer)[0] == 401
    assert _post(url, headers={**bearer, "Origin": "http://evil.example"})[0] == 403
    assert _post(url, body=b"{not json", headers=bearer)[0] == 400
    assert runs.started == []

    status, payload = _post(url, body=b'{"ref": "main"}', headers=bearer)
    assert status == 202
    assert payload["status"] == "started"
    _wait_for(lambda: len(runs.started) == 1)
    assert runs.started[0].events[0]["payload"] == {"ref": "main"}
    assert engine.store.events()[0].run_id == payload["runId"]


def test_rotating_a_webhook_token_retires_the_old_one(
    webhook: tuple[TriggerEngine, FakeRuns, str, str],
) -> None:
    engine, _, url, token = webhook
    trigger_id = url.rsplit("/", 1)[1]
    replacement = engine.store.rotate_token(trigger_id)

    assert _post(url, headers={"Authorization": f"Bearer {token}"})[0] == 401
    assert _post(url, headers={"Authorization": f"Bearer {replacement}"})[0] == 202


def test_webhook_tokens_are_not_stored_and_the_database_is_private(
    webhook: tuple[TriggerEngine, FakeRuns, str, str], data_dir: Path
) -> None:
    engine, _, _, token = webhook
    stored = b"".join(path.read_bytes() for path in engine.store.root.iterdir() if path.is_file())
    assert token.encode() not in stored
    assert stat.S_IMODE(engine.store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(engine.store.path.stat().st_mode) == 0o600


def test_a_turned_off_webhook_refuses_calls(
    webhook: tuple[TriggerEngine, FakeRuns, str, str],
) -> None:
    engine, runs, url, token = webhook
    trigger = engine.store.get(url.rsplit("/", 1)[1])
    # A second webhook keeps the listener open, so this reaches the turned-off one.
    engine.store.create(kind="webhook", source_path=trigger.source_path)
    engine.store.update(trigger.id, enabled=False)
    assert _post(url, headers={"Authorization": f"Bearer {token}"})[0] == 409
    assert runs.started == []


def test_webhook_listener_only_opens_when_a_webhook_exists(data_dir: Path, source: Path) -> None:
    engine, _, _ = _engine(data_dir, enable_webhooks=True)
    engine.store.set_webhook_port(_free_port())
    _cron(engine.store, source)
    engine.become_owner()
    assert engine._webhook_server is None
    engine._resign(wait_seconds=0)


def test_a_busy_webhook_port_is_reported(data_dir: Path, source: Path) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        engine, _, clock = _engine(data_dir, enable_webhooks=True)
        engine.store.set_webhook_port(port)
        engine.store.create(kind="webhook", source_path=source)
        engine.become_owner()
        try:
            status = scheduler_status(engine.store, now=clock())
            assert status["webhookListening"] is False
            assert f"127.0.0.1:{port}" in status["webhookError"]
        finally:
            engine._resign(wait_seconds=0)


def test_only_one_scheduler_owns_triggers_and_a_standby_takes_over(
    data_dir: Path, source: Path
) -> None:
    fast = {"poll_seconds": 0.02, "standby_seconds": 0.02}
    first = TriggerEngine(
        data_dir, owner="background", runner=FakeRuns(), enable_webhooks=False, **fast
    )
    second = TriggerEngine(data_dir, owner="app", runner=FakeRuns(), enable_webhooks=False, **fast)
    first.start()
    try:
        _wait_for(lambda: first.is_owner)
        second.start()
        time.sleep(0.15)
        assert second.is_owner is False
        status = scheduler_status(first.store)
        assert status["running"] is True
        assert status["owner"] == "background"

        first.stop()
        _wait_for(lambda: second.is_owner)
        _wait_for(lambda: scheduler_status(second.store)["owner"] == "app")
    finally:
        first.stop()
        second.stop()
    assert scheduler_status(second.store)["running"] is False


def test_status_says_not_running_after_a_crash(data_dir: Path) -> None:
    engine, _, clock = _engine(data_dir)
    engine.become_owner()
    assert scheduler_status(engine.store, now=clock())["running"] is True
    assert scheduler_status(engine.store, now=clock() + timedelta(seconds=300))["running"] is False


def test_runs_cut_off_by_a_crash_are_marked_interrupted(data_dir: Path, source: Path) -> None:
    first, runs, clock = _engine(data_dir, runs=FakeRuns(hold=True))
    trigger_id = _cron(first.store, source)
    first.become_owner()
    clock.advance(31)
    first.tick()
    _wait_for(lambda: len(runs.started) == 1)

    restarted, _, _ = _engine(data_dir, clock=clock)
    restarted.become_owner()

    newest = restarted.store.events(trigger_id=trigger_id)[0]
    assert newest.status == "interrupted"
    assert "before this run finished" in newest.message
    runs.release.set()
    _settled(first, runs, 1)


def test_downtime_is_shown_for_file_and_webhook_triggers(
    data_dir: Path, source: Path, tmp_path: Path
) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    first, _, clock = _engine(data_dir)
    file_trigger, _ = first.store.create(
        kind="file", source_path=source, config={"path": str(watched)}, now=clock()
    )
    webhook_trigger, _ = first.store.create(kind="webhook", source_path=source, now=clock())
    first.become_owner()
    assert _statuses(first.store) == []

    clock.advance(600)
    restarted, _, _ = _engine(data_dir, clock=clock)
    restarted.become_owner()

    assert _statuses(restarted.store, file_trigger.id) == ["missed"]
    assert "File changes" in restarted.store.events(trigger_id=file_trigger.id)[0].message
    assert "Webhook calls" in restarted.store.events(trigger_id=webhook_trigger.id)[0].message


def test_a_quick_restart_reports_no_downtime(data_dir: Path, source: Path, tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    first, _, clock = _engine(data_dir)
    record, _ = first.store.create(
        kind="file", source_path=source, config={"path": str(watched)}, now=clock()
    )
    first.become_owner()
    clock.advance(30)
    restarted, _, _ = _engine(data_dir, clock=clock)
    restarted.become_owner()
    assert _statuses(restarted.store, record.id) == []


def test_store_refuses_triggers_that_could_not_run(
    data_dir: Path, source: Path, tmp_path: Path
) -> None:
    store = TriggerStore(data_dir)
    needs_input = tmp_path / "needs" / "workflow.rattish"
    needs_input.parent.mkdir()
    needs_input.write_text(WORKFLOW_WITH_INPUT, encoding="utf-8")

    with pytest.raises(TriggerError, match="topic"):
        store.create(kind="webhook", source_path=needs_input)
    record, _ = store.create(kind="webhook", source_path=needs_input, inputs={"topic": "news"})
    assert record.inputs == {"topic": "news"}

    with pytest.raises(TriggerError, match="Nothing to watch"):
        store.create(kind="file", source_path=source, config={"path": "does/not/exist"})
    with pytest.raises(TriggerError, match="Debounce"):
        store.create(
            kind="file", source_path=source, config={"path": str(tmp_path), "debounceSeconds": 0}
        )
    with pytest.raises(TriggerError, match="Overlap"):
        store.create(kind="webhook", source_path=source, overlap="whenever")
    with pytest.raises(TriggerError, match="not found"):
        store.create(kind="webhook", source_path=tmp_path / "missing.rattish")


def test_relative_watch_paths_start_at_the_project(data_dir: Path, source: Path) -> None:
    (source.parent / "inbox").mkdir()
    record, _ = TriggerStore(data_dir).create(
        kind="file", source_path=source, config={"path": "inbox"}
    )
    assert record.config["path"] == str(source.parent / "inbox")


def test_broken_workflow_can_still_be_switched_off(data_dir: Path, source: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, source)
    source.write_text("not rattish", encoding="utf-8")

    assert store.update(trigger_id, enabled=False).enabled is False
    with pytest.raises(TriggerError, match="Fix this workflow"):
        store.update(trigger_id, config={"expression": "0 0 * * *"})


def test_start_at_login_writes_and_removes_a_login_entry(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PATH", "/run/agent/command-slots/bin:/opt/my tools:/usr/bin")
    assert login_status(data_dir)["enabled"] is False

    status = set_login_enabled(data_dir, True)

    assert status["enabled"] is True
    entry = Path(status["path"])
    assert entry.parent == tmp_path / "config" / "autostart"
    assert stat.S_IMODE(entry.stat().st_mode) == 0o600
    lines = entry.read_text(encoding="utf-8").splitlines()
    assert "Type=Application" in lines
    exec_line = next(line.removeprefix("Exec=") for line in lines if line.startswith("Exec="))
    arguments = shlex.split(exec_line)
    assert arguments[:2] == ["/usr/bin/env", "PATH=/opt/my tools:/usr/bin"]
    assert arguments[2:] == scheduler_command(data_dir)
    assert arguments[-5:] == ["rattish", "triggers", "start", "--data-dir", str(data_dir.resolve())]

    assert set_login_enabled(data_dir, False)["enabled"] is False
    assert not entry.exists()
    assert set_login_enabled(data_dir, False)["enabled"] is False


def test_login_entries_for_different_data_folders_do_not_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    first = set_login_enabled(tmp_path / "profile-a", True)
    second = set_login_enabled(tmp_path / "profile-b", True)
    assert first["path"] != second["path"]
    assert Path(first["path"]).is_file() and Path(second["path"]).is_file()


def test_login_entry_prefers_the_app_managed_command(tmp_path: Path) -> None:
    root = tmp_path / "state"
    data = root / "gofer"
    trusted = root / ".gofer-trusted-bin" / "Raticode"
    trusted.parent.mkdir(parents=True)
    data.mkdir()
    trusted.write_text("#!/bin/sh\n", encoding="utf-8")
    assert scheduler_command(data)[0] == str(trusted)


def _json_cli(*arguments: str) -> dict[str, Any]:
    result = runner.invoke(app, ["rattish", "triggers", *arguments, "--format", "json"])
    assert result.exit_code == 0, result.output
    return cast(dict[str, Any], json.loads(result.output))


def test_cli_manages_triggers(
    data_dir: Path, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    base = ("--data-dir", str(data_dir))
    created = _json_cli(
        "add", "cron", str(source), "--cron", "0 9 * * 1-5", "--timezone", "UTC",
        "--overlap", "queue", "--missed", "run_once", *base,
    )  # fmt: skip
    trigger = created["trigger"]
    assert trigger["config"] == {"expression": "0 9 * * 1-5", "timezone": "UTC"}
    assert trigger["overlap"] == "queue"
    assert trigger["missed"] == "run_once"
    assert trigger["nextRunAt"]
    assert created["scheduler"]["running"] is False
    assert created["token"] is None

    watched = tmp_path / "watched"
    watched.mkdir()
    file_trigger = _json_cli(
        "add", "file", str(source), "--path", str(watched), "--debounce", "1.5", *base
    )["trigger"]
    assert file_trigger["config"]["debounceSeconds"] == 1.5

    hook = _json_cli("add", "webhook", str(source), *base)
    assert hook["token"].startswith("rtw_")
    assert hook["trigger"]["webhookUrl"].startswith("http://127.0.0.1:")

    listed = _json_cli("list", str(source), *base)
    assert [item["kind"] for item in listed["triggers"]] == ["cron", "file", "webhook"]

    assert (
        _json_cli("update", trigger["id"], "--cron", "0 10 * * *", *base)["trigger"]["config"][
            "expression"
        ]
        == "0 10 * * *"
    )
    assert _json_cli("disable", trigger["id"], *base)["trigger"]["enabled"] is False
    assert _json_cli("enable", trigger["id"], *base)["trigger"]["enabled"] is True
    assert _json_cli("rotate-token", hook["trigger"]["id"], *base)["token"] != hook["token"]
    assert _json_cli("remove", trigger["id"], *base)["removed"] == trigger["id"]
    assert len(_json_cli("list", *base)["triggers"]) == 2

    assert _json_cli("login", "on", *base)["login"]["enabled"] is True
    assert _json_cli("status", *base)["login"]["enabled"] is True
    assert _json_cli("login", "off", *base)["login"]["enabled"] is False
    assert _json_cli("webhook-port", "9123", *base)["port"] == 9123


def test_cli_text_states_when_triggers_can_fire(data_dir: Path, source: Path) -> None:
    result = runner.invoke(
        app,
        [
            "rattish",
            "triggers",
            "add",
            "webhook",
            str(source),
            "--data-dir",
            str(data_dir),
        ],  # fmt: skip
    )
    assert result.exit_code == 0, result.output
    assert "Save this token now" in result.output
    assert "not running" in result.output
    assert "only while Raticode or its background scheduler is running" in result.output


def test_cli_reports_a_bad_schedule(data_dir: Path, source: Path) -> None:
    result = runner.invoke(
        app,
        [
            "rattish",
            "triggers",
            "add",
            "cron",
            str(source),
            "--cron",
            "nonsense",
            "--data-dir",
            str(data_dir),
        ],  # fmt: skip
    )
    assert result.exit_code == 1
    assert "five fields" in result.output


def test_cli_history_shows_problems(data_dir: Path, source: Path) -> None:
    engine, runs, clock = _engine(data_dir, runs=FakeRuns(status="failed"))
    trigger_id = _cron(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _settled(engine, runs, 1)
    _wait_for(lambda: _statuses(engine.store, trigger_id) == ["failed"])

    result = runner.invoke(
        app, ["rattish", "triggers", "history", "--problems", "--data-dir", str(data_dir)]
    )

    assert result.exit_code == 0, result.output
    assert "failed" in result.output
    assert "Node hello exited with 1." in result.output
