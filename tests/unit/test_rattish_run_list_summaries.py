"""A workflow's Runs list and its clean-up read cached run summaries, not every record.

Each test compares the result with the implementation these paths had before, which
read every run record in full, on the same folder.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.rattish import run_retention, run_service
from gofer.rattish.run_service import _resume_claim
from gofer.rattish.run_values import SAVED_VALUES_SUFFIX
from gofer.rattish.workspaces import RegisteredWorkflow, create_registered_workflow
from gofer.ui import api
from gofer.ui.api import (
    list_workflow_run_logs_payload,
    prune_workflow_run_logs_payload,
    update_retention_settings_payload,
)

SOURCE = """Rattish: 1

Workflow:
  name: Busy workflow

Node execute:
  type: bash-command
  command: "printf ready"
"""
DEAD_PID = 2**22 + 12345
NOW = datetime.now(UTC).replace(microsecond=0)
STATUSES = ["passed", "failed", "stopped", "passed", "preflight_failed", "invalid_inputs"]


@pytest.fixture
def workflow(tmp_path: Path) -> tuple[RegisteredWorkflow, Path]:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "app-data"
    registered = create_registered_workflow(project, "Busy workflow", registry_dir=data_dir)
    registered.entrypoint.write_text(SOURCE, encoding="utf-8")
    return registered, data_dir


def _logs(registered: RegisteredWorkflow) -> Path:
    return registered.workflow_root / "logs"


def _record(
    registered: RegisteredWorkflow,
    index: int,
    *,
    status: str = "passed",
    hours_ago: float,
    **extra: Any,
) -> str:
    started = NOW - timedelta(hours=hours_ago)
    run_id = f"{started.strftime('%Y%m%dT%H%M%S%fZ')}-{index:05d}"
    document: dict[str, Any] = {
        "run_artifact_version": 1,
        "run_id": run_id,
        "status": status,
        "workflow": {"id": registered.workflow_id, "name": "Busy workflow"},
        "started_at": started.isoformat(),
        "finished_at": (started + timedelta(seconds=2)).isoformat(),
        "duration_ms": 2000,
        "events": [],
        "runs": [],
        "compiled_snapshot": {"large": "x" * 400},
        **extra,
    }
    path = _logs(registered) / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    # Older runs have older records; the list orders by modification time.
    stamp = int((NOW.timestamp() - hours_ago * 3600) * 1e9)
    os.utime(path, ns=(stamp, stamp))
    return run_id


def _busy_folder(registered: RegisteredWorkflow, data_dir: Path, count: int) -> dict[str, Any]:
    """``count`` runs, one every 5 minutes, with every kind of run the lists treat apart."""
    ids = [
        _record(
            registered,
            index,
            status=STATUSES[index % len(STATUSES)],
            hours_ago=index / 12,
            started_by="trigger" if index % 3 == 0 else "manual",
        )
        for index in range(10, count)
    ]
    # A live run waiting on an approval, which resumed an older failed run.
    resumed = ids[40]
    live = _record(
        registered,
        0,
        status="running",
        hours_ago=0.001,
        runner_pid=os.getpid(),
        resumed_from={"run_id": resumed},
    )
    run_service._active_rattish_ids.add(live)
    ApprovalStore(data_dir).create_or_update(
        ApprovalRequest(registered.workflow_id, live, "gate", "Ship it?")
    )
    # A run whose runner died, a finished resume of an old run, and a resume that never
    # started, which doesn't count as resuming its run.
    _record(registered, 1, status="running", hours_ago=0.01, runner_pid=DEAD_PID)
    _record(registered, 2, status="passed", hours_ago=0.02, resumed_from={"run_id": ids[3003]})
    _record(
        registered,
        3,
        status="preflight_failed",
        hours_ago=0.03,
        resumed_from={"run_id": ids[60]},
    )
    _record(
        registered,
        4,
        status="failed",
        hours_ago=0.04,
        saved_values={"saved": True, "withheld_inputs": ["password"]},
    )
    _record(registered, 5, status="passed", hours_ago=0.05, trigger_event_count=2)
    damaged = _logs(registered) / "20200101T000000000000Z-damaged.json"
    damaged.write_text("{not json", encoding="utf-8")
    return {"ids": ids, "live": live}


@pytest.fixture
def busy(workflow) -> Iterator[dict[str, Any]]:
    registered, data_dir = workflow
    folder = _busy_folder(registered, data_dir, 4000)
    try:
        yield {"registered": registered, "data_dir": data_dir, **folder}
    finally:
        run_service._active_rattish_ids.discard(folder["live"])


@pytest.fixture
def reads(monkeypatch) -> list[str]:
    """Every run record read, by name."""
    read: list[str] = []
    original = Path.read_text

    def counting(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.parent.name == "logs" and self.suffix == ".json":
            read.append(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    return read


def _old_list(
    workflow_id: str,
    base: Path,
    *,
    offset: int = 0,
    limit: int | None = None,
    status: str | None = None,
    trigger_type: str | None = None,
    search: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
) -> dict[str, Any]:
    """The Runs list as it was: every record read and summarised, then filtered."""
    base = base.resolve()
    paths = api._rattish_run_artifact_paths(base, workflow_id)
    documents = []
    for path in paths:
        try:
            documents.append((api._read_rattish_run_artifact(path), path))
        except api.WorkflowLogError:
            continue
    awaiting = (
        {request.run_id for request in ApprovalStore(base).list_pending(workflow_id)}
        if any(document["status"] == "running" for document, _ in documents)
        else set()
    )
    resume = api._rattish_resume_states(base, workflow_id, [d for d, _ in documents])
    runs = [
        api._rattish_run_summary(
            document,
            path,
            awaiting_approval=str(document["run_id"]) in awaiting,
            resume=resume[str(document["run_id"])],
        )
        for document, path in documents
    ]
    if status:
        runs = [run for run in runs if run["status"] == status]
    if trigger_type:
        runs = [run for run in runs if run["triggerType"] == trigger_type]
    if search:
        query = search.lower()
        runs = [r for r in runs if query in str(r["id"]).lower() or query in r["status"].lower()]
    if started_after or started_before:
        runs = [
            run for run in runs if api._run_started_at_in_range(run, started_after, started_before)
        ]
    page_limit = max(0, limit) if limit is not None else None
    selected = (
        runs[max(0, offset) : max(0, offset) + page_limit]
        if page_limit is not None
        else runs[max(0, offset) :]
    )
    return {
        "workflowId": workflow_id,
        "runs": selected,
        "pagination": {"offset": max(0, offset), "limit": page_limit, "total": len(runs)},
        "skippedRuns": len(paths) - len(documents),
    }


def _old_candidates(
    registered: RegisteredWorkflow, data_dir: Path, **limits: int | None
) -> list[str]:
    """The runs clean-up picked before: every record read, then selected."""
    records = []
    paths = _logs(registered).glob("*.json")
    for _, path in sorted(((path.stat().st_mtime_ns, path) for path in paths), reverse=True):
        try:
            records.append(run_retention.read_run_record(path))
        except run_retention.RunRecordError:
            pass
    workflow_id = registered.workflow_id
    protected = run_retention._protected_by_status_and_approvals(records, data_dir, workflow_id)
    protected.update(
        record.run_id
        for record in records
        if run_service.is_rattish_run_being_resumed(data_dir, workflow_id, record.run_id)
    )
    return [
        record.run_id
        for record in run_retention.select_prune_candidates(records, protected=protected, **limits)
    ]


LIST_CASES: list[dict[str, Any]] = [
    {"limit": 100},
    {"status": "running"},
    {"status": "error", "limit": 25, "offset": 50},
    {"status": "disconnected"},
    {"limit": 100, "offset": 3950},
    {"trigger_type": "manual", "limit": 10},
    {"trigger_type": "webhook"},
    {"search": "0001", "limit": 40},
    {"search": "stopped", "limit": 5},
    {"started_after": NOW - timedelta(hours=30), "started_before": NOW - timedelta(hours=10)},
    {"offset": 3990},
    {},
]


@pytest.mark.parametrize("resumable", [False, True])
def test_list_matches_reading_every_record(busy, monkeypatch, resumable) -> None:
    if resumable:
        # These records can't really be resumed; pretend they can, so a run that was
        # already resumed is told apart only by the run that resumed it.
        monkeypatch.setattr(api, "resume_refusal", lambda document, ir: None)
    workflow_id = busy["registered"].workflow_id
    for case in LIST_CASES:
        expected = _old_list(workflow_id, busy["data_dir"], **case)
        actual = list_workflow_run_logs_payload(workflow_id, busy["data_dir"], **case)
        assert actual == expected, case
    if not resumable:
        return
    # The resumed run is far past the first page, and still reads as resumed on its own.
    deep = list_workflow_run_logs_payload(workflow_id, busy["data_dir"], search=busy["ids"][3003])
    assert deep["runs"][0]["resume"]["allowed"] is False
    assert "already resumed" in deep["runs"][0]["resume"]["reason"]
    # A resume that never started its steps doesn't count.
    never = list_workflow_run_logs_payload(workflow_id, busy["data_dir"], search=busy["ids"][60])
    assert never["runs"][0]["resume"]["allowed"] is True
    page = list_workflow_run_logs_payload(workflow_id, busy["data_dir"], limit=100)
    first = page["runs"][0]
    assert first["id"] == busy["live"]
    assert first["status"] == "running" and first["awaitingApproval"] is True
    assert page["skippedRuns"] == 1 and page["pagination"]["total"] == 3996
    assert any("password" in run["rerun"]["needsInputs"] for run in page["runs"])


def test_list_reads_only_the_page_and_changed_records(busy, reads) -> None:
    workflow_id = busy["registered"].workflow_id
    data_dir = busy["data_dir"]
    # The first listing builds the summaries: every record once, the damaged one again.
    list_workflow_run_logs_payload(workflow_id, data_dir, limit=100)
    reads.clear()

    page = list_workflow_run_logs_payload(workflow_id, data_dir, limit=100)
    assert len(page["runs"]) == 100
    assert len(reads) <= 100 + 1  # The page, and the damaged record, which never caches.
    assert reads.count("20200101T000000000000Z-damaged.json") == 1

    reads.clear()
    assert list_workflow_run_logs_payload(workflow_id, data_dir, status="running")["runs"]
    assert len(reads) <= 1 + 1

    # Changed records are read again for their summary; the rest only for the page.
    reads.clear()
    changed = [busy["ids"][index] for index in (500, 1500, 2500)]
    for run_id in changed:
        path = _logs(busy["registered"]) / f"{run_id}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["status"] = "stopped"
        stat = path.stat()
        path.write_text(json.dumps(document) + " ", encoding="utf-8")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    reads.clear()
    page = list_workflow_run_logs_payload(workflow_id, data_dir, limit=100)
    assert len(reads) <= 100 + 1 + len(changed)
    assert all(f"{run_id}.json" in reads for run_id in changed)
    stopped = list_workflow_run_logs_payload(workflow_id, data_dir, search=changed[1])
    assert stopped["runs"][0]["status"] == "stopped"


def test_cleanup_that_deletes_nothing_reads_no_record(busy, reads) -> None:
    registered, data_dir = busy["registered"], busy["data_dir"]
    list_workflow_run_logs_payload(registered.workflow_id, data_dir, limit=1)
    reads.clear()

    # The defaults keep every run younger than 14 days; these span about 14 days.
    assert (
        _old_candidates(registered, data_dir, keep_last=100, keep_days=30, keep_failed_days=30)
        == []
    )
    reads.clear()
    update_retention_settings_payload(
        data_dir,
        workflow_id=registered.workflow_id,
        settings={"keepLast": 100, "keepDays": 30, "keepFailedDays": 30},
    )
    deleted = run_retention.apply_run_retention(data_dir, registered.workflow_id, _logs(registered))
    assert deleted == []
    # Only the damaged record, which has no summary to reuse.
    assert reads == ["20200101T000000000000Z-damaged.json"]


def test_cleanup_picks_the_same_runs_and_deletes_their_saved_inputs(busy, reads) -> None:
    registered, data_dir = busy["registered"], busy["data_dir"]
    limits = {"keep_last": 100, "keep_days": 7, "keep_failed_days": 10}
    with _resume_claim(data_dir, (registered.workflow_id, busy["ids"][3500])):
        expected = _old_candidates(registered, data_dir, **limits)
        preview = prune_workflow_run_logs_payload(registered.workflow_id, data_dir, **limits)
    assert busy["ids"][3500] not in expected
    assert busy["ids"][40] not in expected  # The live run resumed it.
    assert [run["id"] for run in preview["runs"]] == expected
    assert preview["skippedRuns"] == 1 and len(expected) > 1000

    for run_id in expected[:5]:
        (_logs(registered) / f"{run_id}{SAVED_VALUES_SUFFIX}").write_text("{}", encoding="utf-8")
    update_retention_settings_payload(
        data_dir,
        workflow_id=registered.workflow_id,
        settings={"keepLast": 100, "keepDays": 7, "keepFailedDays": 10},
    )
    # The resume claim is gone now, so that run goes too.
    expected = _old_candidates(registered, data_dir, **limits)
    assert busy["ids"][3500] in expected
    reads.clear()
    deleted = run_retention.apply_run_retention(data_dir, registered.workflow_id, _logs(registered))
    assert deleted == expected
    # Deleting doesn't read the deleted records either.
    assert reads == ["20200101T000000000000Z-damaged.json"]
    remaining = {path.name for path in _logs(registered).iterdir()}
    assert not any(run_id in name for run_id in expected for name in remaining)
    assert _old_candidates(registered, data_dir, **limits) == []


def test_records_changed_after_their_summary_are_picked_by_their_new_state(workflow) -> None:
    registered, data_dir = workflow
    old = _record(registered, 1, status="passed", hours_ago=24 * 40)
    list_workflow_run_logs_payload(registered.workflow_id, data_dir)
    path = _logs(registered) / f"{old}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(status="running", runner_pid=os.getpid())
    path.write_text(json.dumps(document) + "  ", encoding="utf-8")
    run_service._active_rattish_ids.add(old)
    try:
        result = run_retention.prune_run_records(
            data_dir, registered.workflow_id, _logs(registered), keep_last=0, keep_days=0
        )
    finally:
        run_service._active_rattish_ids.discard(old)
    assert result.candidates == []


def test_runs_list_and_clean_up_share_one_summary_file(workflow, tmp_path) -> None:
    from gofer.ui import workflow_runs

    registered, data_dir = workflow
    _record(registered, 1, hours_ago=1)
    list_workflow_run_logs_payload(registered.workflow_id, data_dir)
    summary_file = data_dir / workflow_runs.SUMMARY_DIRECTORY / f"{registered.workflow_id}.json"
    before = summary_file.read_bytes()
    # Another caller names the same run folder through a symlink.
    linked = tmp_path / "linked-logs"
    linked.symlink_to(_logs(registered), target_is_directory=True)
    entries, _ = workflow_runs.workflow_run_summaries(data_dir, registered.workflow_id, linked)
    assert len(entries) == 1
    assert summary_file.read_bytes() == before


def test_a_known_run_a_folder_listing_misses_is_still_listed_and_protected(
    workflow, monkeypatch
) -> None:
    """A running run replaces its record on every step, and a listing taken during a
    replace can miss its name on btrfs, as the approvals list did (self-work report,
    21 November afternoon). A run the summary file knows is looked up by name instead."""
    from gofer.ui import workflow_runs

    registered, data_dir = workflow
    parent = _record(registered, 1, status="passed", hours_ago=24 * 40)
    running = _record(
        registered,
        2,
        status="running",
        hours_ago=1,
        runner_pid=os.getpid(),
        resumed_from={"run_id": parent},
    )
    run_service._active_rattish_ids.add(running)
    try:
        list_workflow_run_logs_payload(registered.workflow_id, data_dir)
        hidden = f"{running}.json"
        real_scandir, real_glob = os.scandir, Path.glob

        class Listing(list[Any]):
            def __enter__(self) -> Listing:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

        def scandir(path: Any) -> Any:
            with real_scandir(path) as entries:
                return Listing(e for e in entries if e.name != hidden)

        def glob(self: Path, pattern: str, **kwargs: Any) -> Any:
            return [p for p in real_glob(self, pattern, **kwargs) if p.name != hidden]

        monkeypatch.setattr(workflow_runs.os, "scandir", scandir)
        monkeypatch.setattr(Path, "glob", glob)
        entries, _ = workflow_runs.workflow_run_summaries(
            data_dir, registered.workflow_id, _logs(registered)
        )
        assert [e.run_id for e in entries] == [running, parent]
        paths = api._rattish_run_artifact_paths(data_dir, registered.workflow_id)
        assert [p.stem for p in paths] == [running, parent]
        # The run it resumed from stays protected, so clean-up leaves both alone.
        result = run_retention.prune_run_records(
            data_dir, registered.workflow_id, _logs(registered), keep_last=0, keep_days=0
        )
        assert result.candidates == []
    finally:
        run_service._active_rattish_ids.discard(running)


def test_a_summary_naming_a_run_with_a_nul_byte_doesnt_break_the_runs_list(
    tmp_path: Path, monkeypatch
) -> None:
    """Nightly audit, 22 November: os.stat raises ValueError for a name with a NUL byte,
    and only OSError was caught, so one damaged summary entry broke the whole list."""
    from gofer.ui import workflow_runs

    folder = tmp_path / "runs"
    folder.mkdir()
    (folder / "good.json").write_text("{}")

    class Summaries:
        def __init__(self, *_: Any) -> None:
            self.entries = ["bad\x00id", "good"]

    monkeypatch.setattr(workflow_runs, "_WorkflowSummaries", Summaries)
    found = workflow_runs.missed_records(tmp_path, "wf", folder, [])
    assert [Path(location).name for location, _ in found] == ["good.json"]


def test_list_shows_why_a_run_failed(workflow) -> None:
    registered, data_dir = workflow
    reason = "Node test exited with code 1\n" + "x" * 400
    failed = _record(registered, 1, status="failed", hours_ago=1, error={"message": reason})
    passed = _record(registered, 2, status="passed", hours_ago=2)
    runs = {
        run["id"]: run
        for run in list_workflow_run_logs_payload(registered.workflow_id, data_dir)["runs"]
    }
    assert runs[failed]["status"] == "error"
    # Capped like the Workflow runs view's list, so one long error can't bloat the list.
    assert runs[failed]["error"] == reason[:300]
    assert runs[passed]["error"] == ""
