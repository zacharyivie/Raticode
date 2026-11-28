"""A file trigger's "weren't kept" count counts each changed file once, not each event."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from gofer.rattish import trigger_service
from gofer.rattish.trigger_service import (
    _DroppedFiles,
    _FileChanges,
    _fold_occurrences,
    _Occurrence,
    _occurrence_json,
    _saved_occurrence,
)
from gofer.rattish.triggers import TriggerRecord
from tests.unit.test_rattish_triggers import START, WORKFLOW, TriggerEngine, _engine, _settled


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


def _file_trigger(engine: TriggerEngine, source: Path, watched: Path) -> TriggerRecord:
    watched.mkdir(parents=True, exist_ok=True)
    record, _ = engine.store.create(
        kind="file",
        source_path=source,
        config={"path": str(watched), "debounceSeconds": 1},
        now=START,
    )
    engine.become_owner()
    return record


def _first_message(engine: TriggerEngine, record: TriggerRecord) -> str:
    return min(engine.store.events(trigger_id=record.id), key=lambda item: item.id).message


def test_each_new_file_past_the_limit_counts_once_in_the_history(
    data_dir: Path, source: Path, tmp_path: Path
) -> None:
    assert trigger_service.MAX_PENDING_FILE_CHANGES == 5_000
    watched = tmp_path / "watched"
    engine, runs, clock = _engine(data_dir)
    record = _file_trigger(engine, source, watched)
    for index in range(6_000):
        path = watched / f"file-{index:05}.txt"
        engine.note_file_change(record.id, "created", path)
        engine.note_file_change(record.id, "modified", path)
    clock.advance(2)
    engine.tick()
    _settled(engine, runs, 1)

    assert len(runs.started[0].events) == trigger_service.MAX_FILE_CHANGES_PER_RUN
    assert _first_message(engine, record).endswith(
        "1,000 more changed files weren't kept: at most 5,000 wait to run."
    )


def test_a_file_written_many_times_past_the_limit_counts_once(
    data_dir: Path, source: Path, tmp_path: Path
) -> None:
    watched = tmp_path / "watched"
    engine, runs, clock = _engine(data_dir)
    record = _file_trigger(engine, source, watched)
    for index in range(5_000):
        engine.note_file_change(record.id, "created", watched / f"file-{index:05}.txt")
    for _ in range(20):
        engine.note_file_change(record.id, "modified", watched / "busy.log")
    clock.advance(2)
    engine.tick()
    _settled(engine, runs, 1)

    assert _first_message(engine, record).endswith(
        "1 more changed file weren't kept: at most 5,000 wait to run."
    )


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (
            ["a", "b", "c", "d", "e", "c", "d", "e"],
            "3 more changed files weren't kept: at most 2 wait to run.",
        ),
        (
            ["a", "b", "c", "d", "e", "f", "c"],
            "More than 3 changed files weren't kept: at most 2 wait to run.",
        ),
    ],
)
def test_past_the_remembered_bound_the_history_says_more_than(
    data_dir: Path,
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    names: list[str],
    message: str,
) -> None:
    monkeypatch.setattr(trigger_service, "MAX_FILE_CHANGES_PER_RUN", 2)
    monkeypatch.setattr(trigger_service, "MAX_PENDING_FILE_CHANGES", 2)
    monkeypatch.setattr(trigger_service, "MAX_DROPPED_FILES_REMEMBERED", 3)
    watched = tmp_path / "watched"
    engine, runs, clock = _engine(data_dir)
    record = _file_trigger(engine, source, watched)
    for name in names:
        engine.note_file_change(record.id, "modified", watched / name)
    clock.advance(2)
    engine.tick()
    _settled(engine, runs, 1)

    assert _first_message(engine, record) == message


def _changes(paths: list[str], dropped: list[str]) -> _FileChanges:
    changes = _FileChanges(first_at=START, last_at=START)
    for path in paths:
        changes.paths[path] = {"type": "file", "kind": "modified", "path": path}
    for path in dropped:
        changes.dropped.add(path)
    return changes


def test_a_batch_that_didnt_start_merges_without_counting_a_file_twice(
    data_dir: Path, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trigger_service, "MAX_PENDING_FILE_CHANGES", 2)
    engine, _, _ = _engine(data_dir)
    record = _file_trigger(engine, source, tmp_path / "watched")
    newer = _changes(["c", "d"], dropped=["e", "a"])
    newer.last_at = START + timedelta(seconds=5)
    engine._changes[record.id] = newer

    engine._put_back_changes(record.id, _changes(["a", "b"], dropped=["c"]))

    merged = engine._changes[record.id]
    assert list(merged.paths) == ["a", "b"]
    # c was dropped by both and left out again, d is left out, e was dropped, and a
    # is kept after all.
    assert merged.dropped.count == 3
    assert not merged.dropped.more


def test_folded_queued_runs_count_each_file_once(
    data_dir: Path, source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trigger_service, "MAX_PENDING_FILE_CHANGES", 2)
    engine, _, _ = _engine(data_dir)
    record = _file_trigger(engine, source, tmp_path / "watched")

    def occurrence(paths: list[str], dropped: list[str]) -> _Occurrence:
        changes = _changes(paths, dropped)
        return _Occurrence(record, events=list(changes.paths.values()), dropped=changes.dropped)

    folded = _fold_occurrences(occurrence(["a", "b"], ["c"]), occurrence(["c", "d"], ["c"]))
    assert [event["path"] for event in folded.events] == ["a", "b"]
    assert folded.dropped.count == 2

    kept_after_all = _fold_occurrences(occurrence(["a"], ["b"]), occurrence(["b"], []))
    assert [event["path"] for event in kept_after_all.events] == ["a", "b"]
    assert not kept_after_all.dropped


def test_a_saved_queued_start_keeps_more_than(data_dir: Path, source: Path, tmp_path: Path) -> None:
    engine, _, _ = _engine(data_dir)
    record = _file_trigger(engine, source, tmp_path / "watched")
    saved = _occurrence_json(_Occurrence(record, dropped=_DroppedFiles(more=True)))
    assert saved is not None
    assert json.loads(saved)["more_dropped"] is True
    restored = _saved_occurrence(record, saved)
    assert restored is not None
    assert restored.dropped.more
