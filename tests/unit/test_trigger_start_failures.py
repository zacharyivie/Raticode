"""A trigger start that hits an error is recorded and tried again, never lost or left running."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.trigger_service import LATE_GRACE_SECONDS, TriggerEngine
from gofer.rattish.triggers import TriggerStore
from tests.unit.test_rattish_triggers import (
    START,
    WORKFLOW,
    FakeRuns,
    _engine,
    _post,
    _settled,
    _statuses,
    _wait_for,
    webhook,  # noqa: F401 - a fixture
)

LOCKED = "database is locked"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return _workflow(tmp_path, "project")


def _workflow(tmp_path: Path, name: str) -> Path:
    project = tmp_path / name
    project.mkdir()
    path = project / "workflow.rattish"
    path.write_text(WORKFLOW, encoding="utf-8")
    return path


def _cron(store: TriggerStore, source: Path, expression: str = "* * * * *", **options: Any) -> str:
    record, _ = store.create(
        kind="cron",
        source_path=source,
        config={"expression": expression, "timezone": "UTC"},
        now=START - timedelta(seconds=20),
        **options,
    )
    return record.id


def _fail(
    monkeypatch: pytest.MonkeyPatch,
    store: TriggerStore,
    method: str,
    *,
    times: int = 1,
    when: Callable[..., bool] = lambda *args, **kwargs: True,
    before: Callable[[], None] = lambda: None,
) -> list[int]:
    """Make one store method raise like a busy database, ``times`` times where ``when`` holds."""
    real = getattr(store, method)
    raised: list[int] = []

    def failing(*args: Any, **kwargs: Any) -> Any:
        if len(raised) < times and when(*args, **kwargs):
            raised.append(1)
            before()
            raise sqlite3.OperationalError(LOCKED)
        return real(*args, **kwargs)

    monkeypatch.setattr(store, method, failing)
    return raised


def _forgot_the_run(engine: TriggerEngine) -> bool:
    return not (engine._active or engine._cancels or engine._started or engine._threads) and not (
        engine._time_limits or engine._carried_after
    )


@pytest.mark.parametrize(
    "method",
    ["add_event", "start_spend"],
    ids=["before-the-history-entry", "after-the-entry-turned-running"],
)
def test_a_cron_start_that_hits_a_store_error_starts_on_a_later_tick(
    data_dir: Path, source: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, source, overlap="skip")
    engine.become_owner()
    raised = _fail(monkeypatch, engine.store, method)

    clock.advance(31)  # 12:01:01, just past the 12:01 time
    engine.tick()

    assert raised == [1]
    assert runs.started == []
    assert _forgot_the_run(engine)
    if method == "start_spend":
        # The entry had turned running, so it says why that run never existed.
        failed = engine.store.events(trigger_id=trigger_id)[0]
        assert (failed.status, failed.message) == ("failed", f"Couldn't start the run: {LOCKED}")
        assert failed.run_id is not None
    else:
        assert _statuses(engine.store, trigger_id) == []

    clock.advance(1)
    engine.tick()
    _settled(engine, runs, 1)
    retried = engine.store.events(trigger_id=trigger_id)[0]
    assert retried.status == "passed"
    assert retried.scheduled_for == (START + timedelta(seconds=30)).isoformat()
    assert retried.run_id == runs.started[0].run_id

    # The next time starts normally: nothing left over from the start that failed.
    clock.advance(60)
    engine.tick()
    _settled(engine, runs, 2)
    statuses = _statuses(engine.store, trigger_id)
    assert statuses[:2] == ["passed", "passed"]
    assert "skipped" not in statuses
    assert engine.store.cursor(trigger_id) == clock()


def test_a_cron_start_that_keeps_failing_is_missed_after_the_grace_with_few_entries(
    data_dir: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, source, "0 * * * *")
    engine.become_owner()
    raised = _fail(monkeypatch, engine.store, "start_spend", times=1000)

    clock.advance(3600 - 29)  # 13:00:01, just past the 13:00 time
    for _ in range(LATE_GRACE_SECONDS + 5):
        engine.tick()
        clock.advance(1)

    events = engine.store.events(trigger_id=trigger_id)
    failed = [item for item in events if item.status == "failed"]
    # Tried again on the next tick, then waiting longer each time: not once a second.
    assert 2 <= len(failed) == len(raised) <= 7
    assert all(item.message == f"Couldn't start the run: {LOCKED}" for item in failed)
    missed = events[0]
    assert missed.status == "missed"
    assert missed.message == "1 scheduled run did not happen because its start kept failing."
    assert runs.started == []
    assert _forgot_the_run(engine)


def test_a_file_batch_that_fails_to_start_waits_while_other_triggers_start(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_source = _workflow(tmp_path, "first")
    second_source = _workflow(tmp_path, "second")
    watched = tmp_path / "watched"
    watched.mkdir()
    engine, runs, clock = _engine(data_dir)
    config = {"path": str(watched), "debounceSeconds": 1}
    first, _ = engine.store.create(kind="file", source_path=first_source, config=config, now=START)
    second, _ = engine.store.create(
        kind="file", source_path=second_source, config=config, now=START
    )
    engine.become_owner()
    engine.note_file_change(first.id, "created", watched / "a.txt")
    engine.note_file_change(second.id, "created", watched / "b.txt")

    def newer_change() -> None:
        # A file changes while the first trigger's start is going wrong.
        engine.note_file_change(first.id, "created", watched / "c.txt")

    raised = _fail(
        monkeypatch,
        engine.store,
        "start_spend",
        when=lambda run_id, trigger_id, *args, **kwargs: trigger_id == first.id,
        before=newer_change,
    )

    clock.advance(2)
    engine.tick()
    _settled(engine, runs, 1)

    assert raised == [1]
    assert [run.source for run in runs.started] == [second_source.resolve()]
    assert [item["name"] for item in runs.started[0].events] == ["b.txt"]
    assert _statuses(engine.store, second.id) == ["passed"]
    assert _statuses(engine.store, first.id) == ["failed"]
    assert list(engine._changes[first.id].paths) == [
        str(watched / "a.txt"),
        str(watched / "c.txt"),
    ]

    clock.advance(2)
    engine.tick()
    _settled(engine, runs, 2)
    assert runs.started[1].source == first_source.resolve()
    assert [item["name"] for item in runs.started[1].events] == ["a.txt", "c.txt"]
    assert _statuses(engine.store, first.id) == ["passed", "failed"]
    assert first.id not in engine._changes


def test_a_queued_start_that_fails_stays_queued_and_starts_later(
    data_dir: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = FakeRuns(hold=True)
    engine, _, clock = _engine(data_dir, runs=runs)
    trigger_id = _cron(engine.store, source, overlap="queue")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: len(runs.started) == 1)
    clock.advance(60)
    engine.tick()
    assert _statuses(engine.store, trigger_id) == ["queued", "running"]

    runs.release.set()
    _wait_for(lambda: runs.finished == 1 and not engine._active)
    raised = _fail(monkeypatch, engine.store, "start_spend")
    clock.advance(1)
    engine.tick()

    assert raised == [1]
    assert len(runs.started) == 1
    queued = engine.store.events(trigger_id=trigger_id)[0]
    assert queued.status == "queued"
    assert queued.message == f"Couldn't start the run: {LOCKED}. Trying again."
    assert trigger_id in engine._waiting
    assert _forgot_the_run(engine)

    clock.advance(1)
    engine.tick()
    _settled(engine, runs, 2)
    assert _statuses(engine.store, trigger_id) == ["passed", "passed"]


def test_a_result_the_store_could_not_take_is_written_on_a_later_tick(
    data_dir: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, source)
    engine.become_owner()
    raised = _fail(
        monkeypatch,
        engine.store,
        "update_event",
        when=lambda *args, **kwargs: kwargs.get("status") == "passed",
    )

    clock.advance(31)
    engine.tick()
    _settled(engine, runs, 1)
    assert raised == [1]
    assert _statuses(engine.store, trigger_id) == ["running"]
    assert not engine._active

    clock.advance(1)
    engine.tick()
    assert _statuses(engine.store, trigger_id) == ["passed"]
    assert engine._unsaved_results == {}


def test_a_webhook_call_that_fails_to_start_is_refused_so_the_caller_can_retry(
    webhook: tuple[TriggerEngine, FakeRuns, str, str],  # noqa: F811 - the imported fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, runs, url, token = webhook
    bearer = {"Authorization": f"Bearer {token}"}
    raised = _fail(monkeypatch, engine.store, "start_spend")

    status, payload = _post(url, headers=bearer)
    assert (status, payload) == (503, {"error": "Couldn't start the run. Send the call again."})
    assert raised == [1]
    _wait_for(lambda: _forgot_the_run(engine))
    assert engine.store.events()[0].status == "failed"

    status, payload = _post(url, headers=bearer)
    assert (status, payload["status"]) == (202, "started")
    _wait_for(lambda: len(runs.started) == 1)
