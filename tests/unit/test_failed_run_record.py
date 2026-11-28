"""A failed unattended run names the step that failed, its exit code and its error.

The end-to-end test fires a real cron trigger whose bash step exits 3, then reads
every place the failure shows: the trigger's history, the run record, the Workflow
runs list and the phone's run row.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gofer.devices.workflows import PhoneWorkflows
from gofer.rattish.trigger_service import _summarize_result, run_in_process
from gofer.rattish.workspaces import create_registered_workflow
from gofer.ui.api import _rattish_run_directory
from gofer.ui.workflow_runs import (
    FailedStep,
    all_workflow_runs_payload,
    failed_step,
    run_error_message,
    run_failure_line,
)
from tests.unit.test_rattish_triggers import START, _engine, _statuses, _wait_for
from tests.unit.test_trigger_run_file_checks import _cron
from tests.unit.test_trigger_run_file_checks import as_user as as_user  # autouse fixture

LINE = "Step build failed (exit 3): cannot reach the database"
FAILS_AT_BUILD = """Rattish: 1

Workflow:
  name: Nightly build

Node prepare:
  type: bash-command
  command: echo ready
  to: build

Node build:
  type: bash-command
  command: echo cannot reach the database >&2; exit 3
  needs: prepare
"""


def test_a_failed_triggered_run_names_its_step_exit_code_and_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    data_dir.mkdir()
    project.mkdir()
    registered = create_registered_workflow(project, "Nightly build", registry_dir=data_dir)
    registered.entrypoint.write_text(FAILS_AT_BUILD, encoding="utf-8")
    engine, _, clock = _engine(data_dir)
    engine._runner = run_in_process
    trigger_id = _cron(engine.store, registered.entrypoint, label="Nightly")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _statuses(engine.store, trigger_id)[:1] == ["failed"], timeout=60)
    _wait_for(lambda: not engine._threads, timeout=10)

    [event] = engine.store.events(trigger_id=trigger_id)
    assert event.message == LINE
    assert event.at >= START.isoformat()

    record = _rattish_run_directory(data_dir, registered.workflow_id) / f"{event.run_id}.json"
    document = json.loads(record.read_text(encoding="utf-8"))
    assert document["status"] == "failed"
    [step] = [run for run in document["runs"] if run["outcome"] == "failure"]
    assert step["node_id"] == "build"
    assert step["output"]["exit_code"] == 3
    assert step["output"]["stderr"] == "cannot reach the database\n"
    assert document["error"]["message"] == "cannot reach the database"
    assert failed_step(document) == FailedStep("build", 3, "cannot reach the database")

    [row] = all_workflow_runs_payload(data_dir)["runs"]
    assert (row["id"], row["status"], row["startedBy"]) == (event.run_id, "error", "trigger")
    assert row["error"] == LINE
    # The phone shows the desktop's text as it is.
    assert PhoneWorkflows._run(row, row["error"])["error"] == LINE


def _failed(*runs: dict[str, Any], error: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "failed", "error": error, "runs": list(runs)}


def _step(node: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"node_id": node, "outcome": "failure", "error": {"message": message, **extra}}


def test_a_step_without_an_exit_code_is_named_without_one() -> None:
    error = {"kind": "provider", "message": "The agent gave up."}
    document = _failed(_step("review", "The agent gave up.", kind="provider"), error=error)

    assert run_failure_line(document) == "Step review failed: The agent gave up."
    assert _summarize_result(_result(document)) == (
        "failed",
        "Step review failed: The agent gave up.",
    )


def test_the_error_text_is_cut_but_the_step_part_is_kept() -> None:
    long = "x" * 400
    document = _failed(_step("build", long, details={"exit_code": 1}), error={"message": long})

    assert run_error_message(document) == "Step build failed (exit 1): " + "x" * 300
    assert run_failure_line(document) == "Step build failed (exit 1): " + long


def test_runs_that_did_not_fail_at_a_step_keep_their_lines() -> None:
    preflight: dict[str, Any] = {"status": "preflight_failed", "error": None, "runs": []}
    inputs = {"status": "invalid_inputs", "error": {"message": "topic is required"}, "runs": []}
    limited = "You've hit your usage limit. Resets at 5am."
    usage = _failed(
        _step("agent", limited, kind="provider", details={"exit_code": 1}),
        error={"kind": "provider", "message": limited, "details": {"exit_code": 1}},
    )
    # A step failure an error route handled, then the run failed for another reason.
    later = _failed(
        _step("build", "cannot reach the database"),
        error={"message": "The run passed its max-cost-usd limit."},
    )

    assert _summarize_result(_result(preflight)) == (
        "failed",
        "The workflow failed its preflight check.",
    )
    assert _summarize_result(_result(inputs)) == ("failed", "topic is required")
    assert run_failure_line(usage) == limited and failed_step(usage) is None
    assert run_failure_line(later) == "The run passed its max-cost-usd limit."


def _result(document: dict[str, Any]) -> Any:
    return SimpleNamespace(document=document)
