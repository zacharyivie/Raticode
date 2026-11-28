"""Runs from every registered workflow in one list: order, filters, totals and the cache."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import gofer.ui.workflow_runs as module
from gofer.rattish.workspaces import create_registered_workflow
from gofer.ui.api import _rattish_run_directory
from gofer.ui.workflow_runs import WorkflowRunsError, all_workflow_runs_payload, runs_line

NOW = datetime.now(UTC).replace(microsecond=0)


def _run_id(at: datetime, suffix: str) -> str:
    return f"{at.strftime('%Y%m%dT%H%M%S%fZ')}-{suffix}"


def _usage(cost: float | None, tokens: int | None = 100, calls: int = 1) -> dict[str, Any]:
    return {
        "calls": calls,
        "reported_calls": calls,
        "partial_calls": 0,
        "unknown_calls": 0 if tokens is not None else calls,
        "input_tokens": tokens,
        "output_tokens": 0 if tokens is not None else None,
        "total_tokens": tokens,
        "cost_usd": cost,
        "cost_reported_calls": calls if cost is not None else 0,
        "cost_unknown_calls": 0 if cost is not None else calls,
        "nodes": {"step": {"calls": calls}},
    }


def _write(
    directory: Path,
    name: str,
    *,
    ago: timedelta,
    status: str = "passed",
    started_by: str | None = "manual",
    usage: dict[str, Any] | None = None,
    **extra: Any,
) -> str:
    started = NOW - ago
    run_id = _run_id(started, os.urandom(6).hex())
    document: dict[str, Any] = {
        "run_artifact_version": 1,
        "run_id": run_id,
        "status": status,
        "workflow": {"id": "ignored", "name": name},
        "started_at": started.isoformat(),
        "finished_at": (started + timedelta(seconds=3)).isoformat(),
        "duration_ms": 3000,
        "compiled_snapshot": {"large": "x" * 2000},
        "error": {"message": "Node 'step' failed"} if status == "failed" else None,
        **extra,
    }
    if started_by is not None:
        document["started_by"] = started_by
    if usage is not None:
        document["usage"] = usage
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{run_id}.json").write_text(json.dumps(document), encoding="utf-8")
    return run_id


@pytest.fixture
def data(tmp_path: Path) -> dict[str, Any]:
    base = tmp_path / "data"
    base.mkdir()
    alpha_project = tmp_path / "alpha"
    beta_project = tmp_path / "beta"
    alpha_project.mkdir()
    beta_project.mkdir()
    alpha = create_registered_workflow(alpha_project, "Nightly report", registry_dir=base)
    beta = create_registered_workflow(beta_project, "Release check", registry_dir=base)
    alpha_runs = _rattish_run_directory(base, alpha.workflow_id)
    beta_runs = _rattish_run_directory(base, beta.workflow_id)
    ids = {
        "old": _write(alpha_runs, "Nightly report", ago=timedelta(days=3), usage=_usage(0.1)),
        "triggered": _write(
            alpha_runs,
            "Nightly report",
            ago=timedelta(hours=5),
            started_by="trigger",
            usage=_usage(0.30, 1200),
        ),
        "failed": _write(
            beta_runs,
            "Release check",
            ago=timedelta(hours=2),
            status="failed",
            started_by="phone",
            usage=_usage(0.12, 300),
        ),
        "legacy": _write(
            beta_runs,
            "Release check",
            ago=timedelta(hours=1),
            started_by=None,
            trigger_event_count=1,
        ),
    }
    return {"base": base, "alpha": alpha, "beta": beta, "ids": ids, "alpha_runs": alpha_runs}


def test_lists_every_workflows_runs_newest_first_with_how_they_started(data) -> None:
    payload = all_workflow_runs_payload(data["base"])
    ids = data["ids"]
    assert [run["id"] for run in payload["runs"]] == [
        ids["legacy"],
        ids["failed"],
        ids["triggered"],
        ids["old"],
    ]
    first = payload["runs"][1]
    assert first["workflowId"] == data["beta"].workflow_id
    assert first["workflowName"] == "Release check"
    assert first["projectName"] == "beta"
    assert first["status"] == "error"
    assert first["startedBy"] == "phone"
    assert first["durationSeconds"] == 3.0
    assert first["error"] == "Node 'step' failed"
    assert first["usage"]["costLabel"] == "$0.12"
    assert "nodes" not in first["usage"]
    # A run saved before started_by was recorded counts its trigger events.
    assert payload["runs"][0]["startedBy"] == "trigger"
    assert payload["runs"][0]["usage"] is None
    assert [w["name"] for w in payload["workflows"]] == ["Nightly report", "Release check"]
    assert payload["pagination"] == {"offset": 0, "limit": 100, "total": 4}


def test_filters_by_status_workflow_time_and_start(data) -> None:
    base, ids = data["base"], data["ids"]
    assert [r["id"] for r in all_workflow_runs_payload(base, status="failed")["runs"]] == [
        ids["failed"]
    ]
    alpha = all_workflow_runs_payload(base, workflow_ids=[data["alpha"].workflow_id.upper()])
    assert [r["id"] for r in alpha["runs"]] == [ids["triggered"], ids["old"]]
    day = all_workflow_runs_payload(base, since="24h")
    assert [r["id"] for r in day["runs"]] == [ids["legacy"], ids["failed"], ids["triggered"]]
    iso = (NOW - timedelta(hours=3)).isoformat()
    assert [r["id"] for r in all_workflow_runs_payload(base, since=iso)["runs"]] == [
        ids["legacy"],
        ids["failed"],
    ]
    until = (NOW - timedelta(hours=4)).isoformat()
    assert [r["id"] for r in all_workflow_runs_payload(base, until=until)["runs"]] == [
        ids["triggered"],
        ids["old"],
    ]
    assert [r["id"] for r in all_workflow_runs_payload(base, started_by="phone")["runs"]] == [
        ids["failed"]
    ]
    page = all_workflow_runs_payload(base, limit=1, offset=1)
    assert [r["id"] for r in page["runs"]] == [ids["failed"]]
    assert page["totals"]["runs"] == 4
    with pytest.raises(WorkflowRunsError, match="isn't a time"):
        all_workflow_runs_payload(base, since="yesterday-ish")
    with pytest.raises(WorkflowRunsError, match="Unknown status"):
        all_workflow_runs_payload(base, status="exploded")


def test_totals_give_one_line_for_the_away_card(data) -> None:
    totals = all_workflow_runs_payload(data["base"], since="24h")["totals"]
    assert totals["byStatus"] == {"success": 2, "error": 1}
    assert totals["totalTokens"] == 1500
    assert totals["costUsd"] == pytest.approx(0.42)
    assert totals["summary"] == "3 workflow runs: 2 passed, 1 failed · est. $0.42"
    assert runs_line({"runs": 1, "byStatus": {"stopped": 1}, "calls": 0}) == (
        "1 workflow run: 1 stopped"
    )
    assert runs_line({"runs": 0}) == "No workflow runs"
    unknown = {"runs": 1, "byStatus": {"success": 1}, "calls": 2, "costUsd": None}
    assert runs_line(unknown).endswith("· est. cost unknown")
    partial = {**unknown, "costUsd": 0.5, "unreportedCostRuns": 1}
    assert runs_line(partial).endswith("· est. at least $0.50")


def test_reads_each_run_record_once_until_it_changes(data, monkeypatch) -> None:
    base = data["base"]
    read: list[str] = []
    original = module._summary_of

    def counting(document):
        read.append(str(document["run_id"]))
        return original(document)

    monkeypatch.setattr(module, "_summary_of", counting)
    all_workflow_runs_payload(base)
    assert len(read) == 4
    summaries = sorted((base / module.SUMMARY_DIRECTORY).glob("*.json"))
    assert len(summaries) == 2
    read.clear()
    again = all_workflow_runs_payload(base)
    assert read == [] and again["totals"]["runs"] == 4

    # A changed record is read again; a removed one leaves the summary file.
    record = data["alpha_runs"] / f"{data['ids']['triggered']}.json"
    document = json.loads(record.read_text())
    document["status"] = "stopped"
    record.write_text(json.dumps(document) + " ", encoding="utf-8")
    (data["alpha_runs"] / f"{data['ids']['old']}.json").unlink()
    listed = all_workflow_runs_payload(base)
    assert read == [data["ids"]["triggered"]]
    assert {r["id"]: r["status"] for r in listed["runs"]}[data["ids"]["triggered"]] == "stopped"
    saved = json.loads(
        (base / module.SUMMARY_DIRECTORY / f"{data['alpha'].workflow_id}.json").read_text()
    )
    assert set(saved["runs"]) == {data["ids"]["triggered"]}

    # A time range skips older records by their ID, before reading anything.
    read.clear()
    (base / module.SUMMARY_DIRECTORY).joinpath(f"{data['beta'].workflow_id}.json").unlink()
    all_workflow_runs_payload(base, since="90m")
    assert read == [data["ids"]["legacy"]]


def test_a_running_record_whose_runner_is_gone_reads_as_interrupted(data) -> None:
    run_id = _write(
        data["alpha_runs"],
        "Nightly report",
        ago=timedelta(minutes=1),
        status="running",
        runner_pid=2**22 + 12345,
    )
    run = all_workflow_runs_payload(data["base"])["runs"][0]
    assert run["id"] == run_id
    assert run["status"] == "disconnected"


def test_damaged_records_and_scoping_to_open_projects(data) -> None:
    (data["alpha_runs"] / "20261101T000000000000Z-broken.json").write_text("{not json")
    payload = all_workflow_runs_payload(
        data["base"], project_roots=[str(data["beta"].project_root)]
    )
    assert {r["workflowId"] for r in payload["runs"]} == {data["beta"].workflow_id}
    assert [w["id"] for w in payload["workflows"]] == [data["beta"].workflow_id]
    assert all_workflow_runs_payload(data["base"], project_roots=[])["runs"] == []
    assert all_workflow_runs_payload(data["base"])["totals"]["runs"] == 4


def test_runs_record_how_they_started(tmp_path) -> None:
    import anyio

    from gofer.rattish.run_service import run_rattish_file
    from gofer.rattish.runtime import HandlerResult, NodeHandlerRegistry

    base = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Origins", registry_dir=base)
    workflow.entrypoint.write_text(
        "Rattish: 1\nWorkflow:\n  name: Origins\nNode one:\n  type: bash-command\n"
        "  command: fake-only\n",
        encoding="utf-8",
    )

    async def handler(node, run_context, bindings):
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0, "truncated": False})

    handlers = NodeHandlerRegistry({"raticode.bash_command": handler})

    async def start(**options: Any) -> dict[str, Any]:
        result = await run_rattish_file(
            workflow.entrypoint, data_dir=base, handlers=handlers, **options
        )
        return result.document

    assert anyio.run(lambda: start(started_by="phone"))["started_by"] == "phone"
    assert anyio.run(lambda: start())["started_by"] == "manual"
    assert anyio.run(lambda: start(trigger_events=[{"type": "manual"}]))["started_by"] == "trigger"
    with pytest.raises(ValueError, match="Unknown run origin"):
        anyio.run(lambda: start(started_by="robot"))
    listed = all_workflow_runs_payload(base)
    assert sorted(run["startedBy"] for run in listed["runs"]) == ["manual", "phone", "trigger"]
