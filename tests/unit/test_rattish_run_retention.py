"""Automatic and manual clean-up of old Rattish run records."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import anyio
import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.rattish import run_retention
from gofer.rattish.run_service import _resume_claim, new_run_id, run_rattish_file
from gofer.rattish.trigger_service import TriggerRun, run_in_process
from gofer.rattish.workspaces import RegisteredWorkflow, create_registered_workflow
from gofer.ui.api import (
    latest_workflow_log_payload,
    list_workflow_run_logs_payload,
    prune_workflow_run_logs_payload,
    update_retention_settings_payload,
)

SOURCE = """Rattish: 1

Workflow:
  name: Retained runs

Node execute:
  type: bash-command
  command: "printf ready"
"""
# A PID that no process has, so a "running" record that names it is interrupted.
DEAD_PID = 2**22 + 12345


@pytest.fixture
def workflow(tmp_path: Path) -> tuple[RegisteredWorkflow, Path]:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "app-data"
    registered = create_registered_workflow(project, "Retained runs", registry_dir=data_dir)
    registered.entrypoint.write_text(SOURCE, encoding="utf-8")
    return registered, data_dir


def _logs(registered: RegisteredWorkflow) -> Path:
    return registered.workflow_root / "logs"


def _record(
    registered: RegisteredWorkflow,
    run_id: str,
    *,
    status: str = "passed",
    started_at: str = "2020-01-01T00:00:00+00:00",
    age_seconds: int = 0,
    **extra: Any,
) -> Path:
    """Save a minimal old run record, ordered by its modification time."""
    path = _logs(registered) / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "run_artifact_version": 1,
        "run_id": run_id,
        "status": status,
        "workflow": {"id": registered.workflow_id, "name": "Retained runs"},
        "started_at": started_at,
        "finished_at": started_at,
        "duration_ms": 0,
        "events": [],
        "runs": [],
        **extra,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    stamp = time.time() - 10_000 - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def _remaining(registered: RegisteredWorkflow) -> set[str]:
    return {path.stem for path in _logs(registered).glob("*.json")}


def test_triggered_run_applies_saved_retention(workflow) -> None:
    registered, data_dir = workflow
    for index in range(3):
        _record(registered, f"old-{index}", age_seconds=index)
    _record(registered, "old-failed", status="failed", age_seconds=5)
    update_retention_settings_payload(
        data_dir,
        workflow_id=registered.workflow_id,
        settings={"keepLast": 1, "keepDays": 7, "keepFailedDays": 7},
    )

    result = run_in_process(
        TriggerRun(
            source=registered.entrypoint,
            workflow_id=registered.workflow_id,
            inputs={},
            events=[],
            data_dir=data_dir,
            run_id=new_run_id(),
            cancel_event=threading.Event(),
        )
    )

    assert result.ok
    assert _remaining(registered) == {result.document["run_id"]}


def test_retention_keeps_runs_within_saved_limits(workflow) -> None:
    registered, data_dir = workflow
    _record(registered, "old-passed")
    _record(registered, "old-failed", status="failed")
    update_retention_settings_payload(
        data_dir,
        workflow_id=registered.workflow_id,
        # Failed runs are kept for 100 years, other runs for none.
        settings={"keepLast": 0, "keepDays": 0, "keepFailedDays": 36500},
    )

    result = anyio.run(lambda: run_rattish_file(registered.entrypoint, data_dir=data_dir))

    # The run that just ended stays for its caller, whatever the settings say.
    assert _remaining(registered) == {"old-failed", result.document["run_id"]}


def test_default_settings_skip_reading_when_under_keep_last(workflow, monkeypatch) -> None:
    registered, data_dir = workflow
    _record(registered, "ancient")

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("records were read")

    monkeypatch.setattr(run_retention, "load_run_records", fail)
    monkeypatch.setattr(run_retention, "summary_run_records", fail)

    assert (
        run_retention.apply_run_retention(data_dir, registered.workflow_id, _logs(registered)) == []
    )
    assert _remaining(registered) == {"ancient"}


def test_failed_clean_up_does_not_change_the_run(workflow, monkeypatch) -> None:
    registered, data_dir = workflow

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("disk unplugged")

    monkeypatch.setattr(run_retention, "run_record_count", broken)

    result = anyio.run(lambda: run_rattish_file(registered.entrypoint, data_dir=data_dir))

    assert result.ok
    assert result.path.is_file()


def test_prune_never_picks_running_waiting_or_resumed_runs(workflow) -> None:
    registered, data_dir = workflow
    live_pid = os.getppid()
    _record(registered, "running", status="running", runner_pid=live_pid)
    _record(registered, "resume-parent", status="failed")
    _record(
        registered,
        "resume-child",
        status="running",
        runner_pid=live_pid,
        resumed_from={"run_id": "resume-parent", "rerun_nodes": []},
    )
    # Its runner is gone, but its approval request is still open.
    _record(registered, "waiting", status="running", runner_pid=DEAD_PID)
    ApprovalStore(data_dir).create_or_update(
        ApprovalRequest(registered.workflow_id, "waiting", "gate", "Ship it?")
    )
    _record(registered, "being-resumed", status="stopped")
    _record(registered, "old", status="passed")

    with _resume_claim(data_dir, (registered.workflow_id, "being-resumed")):
        preview = prune_workflow_run_logs_payload(
            registered.workflow_id,
            data_dir,
            keep_last=0,
            keep_days=0,
            keep_failed_days=0,
            dry_run=True,
        )
        applied = prune_workflow_run_logs_payload(
            registered.workflow_id,
            data_dir,
            keep_last=0,
            keep_days=0,
            keep_failed_days=0,
            dry_run=False,
        )

    assert [run["id"] for run in preview["runs"]] == ["old"]
    assert applied["deleted"] == ["old"]
    assert _remaining(registered) == {
        "running",
        "resume-parent",
        "resume-child",
        "waiting",
        "being-resumed",
    }


def test_resumed_parent_is_prunable_once_the_resume_ends(workflow) -> None:
    registered, data_dir = workflow
    _record(registered, "resume-parent", status="failed")
    _record(
        registered,
        "resume-child",
        status="passed",
        resumed_from={"run_id": "resume-parent", "rerun_nodes": []},
    )

    payload = prune_workflow_run_logs_payload(
        registered.workflow_id,
        data_dir,
        keep_last=0,
        keep_days=0,
        keep_failed_days=0,
        dry_run=False,
    )

    assert sorted(payload["deleted"]) == ["resume-child", "resume-parent"]


def test_failed_runs_fall_back_to_keep_days_without_a_failed_limit(workflow) -> None:
    registered, data_dir = workflow
    _record(registered, "old-failed", status="failed")

    payload = prune_workflow_run_logs_payload(
        registered.workflow_id, data_dir, keep_days=1, dry_run=True
    )

    assert [run["id"] for run in payload["runs"]] == ["old-failed"]


def test_unreadable_record_does_not_hide_the_run_list(workflow) -> None:
    registered, data_dir = workflow

    result = anyio.run(lambda: run_rattish_file(registered.entrypoint, data_dir=data_dir))
    bad = _logs(registered) / "broken.json"
    bad.write_text("{", encoding="utf-8")
    newest = time.time() + 60
    os.utime(bad, (newest, newest))

    listed = list_workflow_run_logs_payload(registered.workflow_id, data_dir)
    latest = latest_workflow_log_payload(registered.workflow_id, data_dir)
    pruned = prune_workflow_run_logs_payload(
        registered.workflow_id,
        data_dir,
        keep_last=0,
        keep_days=0,
        keep_failed_days=0,
        dry_run=False,
    )

    assert [run["id"] for run in listed["runs"]] == [result.document["run_id"]]
    assert listed["skippedRuns"] == 1
    assert listed["pagination"]["total"] == 1
    assert latest["runId"] == result.document["run_id"]
    assert latest["skippedRuns"] == 1
    assert pruned["deleted"] == [result.document["run_id"]]
    assert pruned["skippedRuns"] == 1
    assert bad.is_file()


def test_only_unreadable_records_give_an_empty_latest(workflow) -> None:
    registered, data_dir = workflow
    _logs(registered).mkdir(parents=True, exist_ok=True)
    (_logs(registered) / "broken.json").write_text("[]", encoding="utf-8")

    latest = latest_workflow_log_payload(registered.workflow_id, data_dir)
    listed = list_workflow_run_logs_payload(registered.workflow_id, data_dir)

    assert latest["logPath"] is None
    assert latest["skippedRuns"] == 1
    assert listed["runs"] == []
    assert listed["skippedRuns"] == 1


def test_rattish_prune_command_previews_then_applies(workflow) -> None:
    registered, data_dir = workflow
    _record(registered, "old", status="passed")
    _record(registered, "running", status="running", runner_pid=os.getppid())
    runner = CliRunner()
    base = ["rattish", "prune", str(registered.entrypoint), "--data-dir", str(data_dir)]

    preview = runner.invoke(app, [*base, "--keep-last", "0", "--format", "json"])
    applied = runner.invoke(app, [*base, "--keep-last", "0", "--keep-days", "0", "--apply"])

    assert preview.exit_code == 0, preview.output
    payload = json.loads(preview.output)
    assert payload["dryRun"] is True
    assert payload["workflowId"] == registered.workflow_id
    # Only --keep-last was given, so other limits are unset and keep every run.
    assert payload["runs"] == []
    assert applied.exit_code == 0, applied.output
    assert "Removed 1 run(s)" in applied.output
    assert _remaining(registered) == {"running"}


def test_rattish_prune_command_uses_saved_settings(workflow) -> None:
    registered, data_dir = workflow
    _record(registered, "old", status="passed")
    update_retention_settings_payload(
        data_dir,
        workflow_id=registered.workflow_id,
        settings={"keepLast": 0, "keepDays": 1, "keepFailedDays": 1},
    )

    preview = CliRunner().invoke(
        app,
        [
            "rattish",
            "prune",
            str(registered.entrypoint),
            "--data-dir",
            str(data_dir),
            "--format",
            "json",
        ],
    )

    assert preview.exit_code == 0, preview.output
    payload = json.loads(preview.output)
    assert [run["id"] for run in payload["runs"]] == ["old"]
    assert payload["keep"] == {"keepLast": 0, "keepDays": 1, "keepFailedDays": 1}
    assert _remaining(registered) == {"old"}
