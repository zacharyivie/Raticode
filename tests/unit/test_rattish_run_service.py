from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import anyio
import pytest

from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.runtime import HandlerResult, NodeHandlerRegistry, RuntimeErrorInfo
from gofer.rattish.workspaces import create_registered_workflow
from gofer.ui.api import (
    latest_workflow_log_payload,
    list_workflow_run_logs_payload,
    prune_workflow_run_logs_payload,
    workflow_run_log_payload,
)


@pytest.mark.anyio
async def test_run_graph_snapshot_keeps_executed_nodes_routes_and_configuration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Executed revision
Node start:
  type: bash-command
  command: original-command
  to:
    - next when node.start.output["exit_code"] == 0
Node next:
  type: bash-command
  command: original-next
  needs: start
""",
        encoding="utf-8",
    )

    async def handler(node, context, bindings):
        return HandlerResult(True, _output(node["id"]))

    data_dir = tmp_path / "app-data"
    result = await run_rattish_file(
        source,
        data_dir=data_dir,
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )
    assert result.ok
    original_ir = result.document["compiled_snapshot"]
    original_nodes = {node["id"]: node for node in original_ir["nodes"]}
    assert original_nodes["start"]["configuration"]["command"] == "original-command"
    source.write_text(_bash_source("changed-command"), encoding="utf-8")

    payload = workflow_run_log_payload(
        result.document["workflow"]["id"], result.document["run_id"], data_dir
    )
    snapshot = payload["graphSnapshot"]
    assert snapshot["name"] == "Executed revision"
    assert snapshot["runId"] == result.document["run_id"]
    assert snapshot["sourceFingerprint"] == result.document["source"]["source_fingerprint"]
    assert snapshot["compilationFingerprint"] == original_ir["source"]["compilation_fingerprint"]
    assert snapshot["sourcePath"] == str(source)
    assert snapshot["readOnly"] is True
    snapshot_nodes = {node["id"]: node for node in snapshot["nodes"]}
    assert set(snapshot_nodes) == {"start", "next"}
    assert snapshot_nodes["start"]["operation"]["command"] == "original-command"
    assert snapshot_nodes["next"]["rattish"]["needs"] == ["start"]
    assert snapshot["edges"][0]["from"] == "start"
    assert snapshot["edges"][0]["to"] == "next"
    assert snapshot["edges"][0]["mode"] == "when"
    assert snapshot["edges"][0]["predicate"] == original_nodes["start"]["routes"][0]["predicate"]
    # Inspection also succeeds when the original source is removed entirely.
    source.unlink()
    assert (
        workflow_run_log_payload(
            result.document["workflow"]["id"], result.document["run_id"], data_dir
        )["graphSnapshot"]
        == snapshot
    )


@pytest.mark.anyio
async def test_old_run_artifact_explicitly_has_no_graph_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_bash_source(), encoding="utf-8")

    async def handler(node, context, bindings):
        return HandlerResult(True, _output())

    data_dir = tmp_path / "app-data"
    result = await run_rattish_file(
        source,
        data_dir=data_dir,
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )
    legacy = {key: value for key, value in result.document.items() if key != "compiled_snapshot"}
    result.path.write_text(json.dumps(legacy), encoding="utf-8")
    source.unlink()
    payload = workflow_run_log_payload(legacy["workflow"]["id"], legacy["run_id"], data_dir)
    assert payload["graphSnapshot"] is None
    assert payload["runId"] == legacy["run_id"]


def _bash_source(command: str = "printf ready") -> str:
    return f"""Rattish: 1

Workflow:
  name: Persisted run

Node execute:
  type: bash-command
  command: {json.dumps(command)}
"""


def _output(stdout: str = "") -> dict[str, object]:
    return {"stdout": stdout, "stderr": "", "exit_code": 0}


@pytest.mark.anyio
async def test_run_service_compiles_preflights_executes_and_persists_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_bash_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"

    result = await run_rattish_file(source, data_dir=data_dir)

    assert result.ok
    assert result.status == "passed"
    assert result.path.parent == (data_dir / "radish" / "runs" / result.document["workflow"]["id"])
    assert result.document["source"]["source_fingerprint"].startswith("sha256:")
    assert result.document["source"]["compilation_fingerprint"].startswith("sha256:")
    assert result.document["latest_node_outputs"]["execute"]["stdout"] == "ready"
    assert result.document["runs"][0]["duration_ms"] >= 0
    assert [event["type"] for event in result.document["events"]] == [
        "workflow_started",
        "node_completed",
        "workflow_completed",
    ]
    assert json.loads(result.path.read_text(encoding="utf-8")) == result.document


@pytest.mark.anyio
async def test_registered_workflow_run_is_stored_in_its_workspace(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "app-data"
    workflow = create_registered_workflow(project, "Persisted run", registry_dir=data_dir)
    workflow.entrypoint.write_text(_bash_source(), encoding="utf-8")

    result = await run_rattish_file(workflow.entrypoint, data_dir=data_dir)

    assert result.ok
    assert result.path.parent == workflow.workflow_root / "logs"
    assert not (data_dir / "radish" / "runs" / workflow.workflow_id).exists()


@pytest.mark.anyio
async def test_registered_workflow_legacy_runs_move_out_of_app_data(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "app-data"
    workflow = create_registered_workflow(project, "Legacy run", registry_dir=data_dir)
    workflow.entrypoint.write_text(_bash_source(), encoding="utf-8")
    result = await run_rattish_file(workflow.entrypoint, data_dir=data_dir)
    legacy_directory = data_dir / "radish" / "runs" / workflow.workflow_id
    legacy_directory.parent.mkdir(parents=True, exist_ok=True)
    os.replace(result.path.parent, legacy_directory)

    runs = list_workflow_run_logs_payload(workflow.workflow_id, data_dir)["runs"]

    assert runs[0]["id"] == result.document["run_id"]
    assert result.path.is_file()
    assert not legacy_directory.exists()


@pytest.mark.anyio
async def test_registered_workflow_run_pruning_uses_workspace_logs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "app-data"
    workflow = create_registered_workflow(project, "Pruned run", registry_dir=data_dir)
    workflow.entrypoint.write_text(_bash_source(), encoding="utf-8")
    result = await run_rattish_file(workflow.entrypoint, data_dir=data_dir)

    payload = prune_workflow_run_logs_payload(
        workflow.workflow_id,
        data_dir,
        keep_last=0,
        keep_days=0,
        keep_failed_days=0,
        dry_run=False,
    )

    assert payload["deleted"] == [result.document["run_id"]]
    assert not result.path.exists()


@pytest.mark.anyio
async def test_rattish_run_artifacts_feed_studio_history_and_timeline(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_bash_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"
    result = await run_rattish_file(source, data_dir=data_dir)

    runs = list_workflow_run_logs_payload(result.document["workflow"]["id"], data_dir)["runs"]
    latest = latest_workflow_log_payload(result.document["workflow"]["id"], data_dir)
    selected = workflow_run_log_payload(
        result.document["workflow"]["id"], result.document["run_id"], data_dir
    )

    assert runs[0]["id"] == result.document["run_id"]
    assert runs[0]["status"] == "success"
    assert latest["nodeOutputs"]["execute"]["data"]["stdout"] == "ready"
    assert selected["runEvents"][1]["nodeId"] == "execute"
    assert selected["runEvents"][1]["attempt"] == 1
    assert selected["runEvents"][1]["activationLineageId"]
    assert selected["runNodes"]["execute"]["durationMs"] >= 0


@pytest.mark.anyio
async def test_run_service_persists_public_workflow_outputs(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Persisted public output
  outputs:
    text:
      from: node.execute.output.stdout
      schema: {"type": "string"}
Node execute:
  type: bash-command
  command: "printf ready"
""",
        encoding="utf-8",
    )

    result = await run_rattish_file(source, data_dir=tmp_path / "app-data")

    assert result.status == "passed"
    assert result.document["outputs"] == {"text": "ready"}
    assert json.loads(result.path.read_text(encoding="utf-8"))["outputs"] == {"text": "ready"}


@pytest.mark.anyio
async def test_run_service_persists_preflight_failure_without_executing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Missing input file
Node read:
  type: read-file
  path: missing.txt
""",
        encoding="utf-8",
    )

    result = await run_rattish_file(source, data_dir=tmp_path / "app-data")

    assert result.status == "preflight_failed"
    assert result.document["runs"] == []
    assert result.document["error"] is None
    assert [item["code"] for item in result.document["diagnostics"]] == [
        "RATTISH_PREFLIGHT_RESOURCE_MISSING"
    ]
    assert result.path.is_file()


@pytest.mark.anyio
async def test_run_service_persists_runtime_failure_and_invalid_inputs(
    tmp_path: Path,
) -> None:
    failed_source = tmp_path / "failed.rattish"
    failed_source.write_text(_bash_source("printf 'not a cat' >&2; exit 7"), encoding="utf-8")

    failed = await run_rattish_file(failed_source, data_dir=tmp_path / "app-data")

    assert failed.status == "failed"
    assert failed.document["runs"][0]["output"]["exit_code"] == 7
    assert failed.document["error"]["code"] == "RATTISH_RUNTIME_COMMAND_FAILED"
    failed_payload = workflow_run_log_payload(
        failed.document["workflow"]["id"], failed.document["run_id"], tmp_path / "app-data"
    )
    failed_output = failed_payload["nodeOutputs"]["execute"]
    assert {key: value for key, value in failed_output.items() if key != "output"} == {
        "success": False,
        "data": {"stdout": "", "stderr": "not a cat", "exit_code": 7},
        "exitCode": 7,
        "error": "not a cat",
    }
    assert json.loads(failed_output["output"]) == failed_output["data"]
    failed_node = failed_payload["runNodes"]["execute"]
    assert failed_node["exitCode"] == 7
    assert failed_node["message"] == "not a cat"
    assert failed_node["attempts"][0]["stderr"] == "not a cat"

    input_source = tmp_path / "inputs.rattish"
    input_source.write_text(
        """Rattish: 1
Workflow:
  name: Required input
  inputs:
    message:
      schema: {"type": "string"}
      required: true
Node execute:
  type: bash-command
  command: "true"
""",
        encoding="utf-8",
    )

    invalid = await run_rattish_file(input_source, data_dir=tmp_path / "app-data")

    assert invalid.status == "invalid_inputs"
    assert invalid.document["error"]["code"] == "RATTISH_WORKFLOW_INPUT_INVALID"
    assert invalid.document["runs"] == []


@pytest.mark.anyio
async def test_run_service_persists_branch_join_and_allowed_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Branch join
Node start:
  type: bash-command
  command: start
  to:
    - allowed
    - parallel
Node allowed:
  type: bash-command
  command: allowed
  needs: start
  allow-fail: true
  to: join
Node parallel:
  type: bash-command
  command: parallel
  needs: start
  to: join
Node join:
  type: bash-command
  command: join
  needs:
    - allowed
    - parallel
""",
        encoding="utf-8",
    )

    async def handler(node, context, bindings):
        _ = context, bindings
        if node["id"] == "allowed":
            return HandlerResult(
                False,
                _output(),
                RuntimeErrorInfo("command", "RATTISH_TEST_ALLOWED", "expected"),
            )
        return HandlerResult(True, _output(node["id"]))

    result = await run_rattish_file(
        source,
        data_dir=tmp_path / "app-data",
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )

    assert result.status == "passed"
    outcomes = {run["node_id"]: run["outcome"] for run in result.document["runs"]}
    assert outcomes == {
        "start": "success",
        "allowed": "allowed_failure",
        "parallel": "success",
        "join": "success",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "expected_code", "expected_runs"),
    [
        (
            """Node repeat:
  type: bash-command
  command: repeat
  max-runs: 2
  to: repeat
""",
            "RATTISH_RUN_LIMIT_EXCEEDED",
            2,
        ),
        (
            """Node a:
  type: bash-command
  command: a
  to: b
Node gate:
  type: bash-command
  command: gate
  to:
    - c when node.gate.output["exit_code"] == 1
Node c:
  type: bash-command
  command: c
  needs: gate
Node b:
  type: bash-command
  command: b
  needs: c
""",
            "RATTISH_JOIN_UNRESOLVED",
            2,
        ),
    ],
)
async def test_run_service_persists_cycle_and_unresolved_join_failures(
    tmp_path: Path,
    body: str,
    expected_code: str,
    expected_runs: int,
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        f"Rattish: 1\nWorkflow:\n  name: Control failure\n{body}",
        encoding="utf-8",
    )

    async def handler(node, context, bindings):
        _ = context, bindings
        return HandlerResult(True, _output(node["id"]))

    result = await run_rattish_file(
        source,
        data_dir=tmp_path / "app-data",
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )

    assert result.status == "failed"
    assert result.document["error"]["code"] == expected_code
    assert len(result.document["runs"]) == expected_runs
    assert json.loads(result.path.read_text(encoding="utf-8"))["error"]["code"] == expected_code


@pytest.mark.anyio
async def test_run_service_applies_retries_and_workflow_timeout(tmp_path: Path) -> None:
    retry_source = tmp_path / "retry.rattish"
    retry_source.write_text(
        """Rattish: 1
Workflow:
  name: Retry
Node flaky:
  type: bash-command
  command: flaky
  retry-count: 1
""",
        encoding="utf-8",
    )
    attempts: Counter[str] = Counter()

    async def retry_handler(node, context, bindings):
        _ = context, bindings
        attempts[node["id"]] += 1
        if attempts[node["id"]] == 1:
            return HandlerResult(
                False,
                _output(),
                RuntimeErrorInfo("command", "TEST_RETRY", "retry"),
            )
        return HandlerResult(True, _output("recovered"))

    retried = await run_rattish_file(
        retry_source,
        data_dir=tmp_path / "app-data",
        handlers=NodeHandlerRegistry({"raticode.bash_command": retry_handler}),
    )

    assert retried.status == "passed"
    assert attempts == {"flaky": 2}
    assert len(retried.document["runs"]) == 1

    timeout_source = tmp_path / "timeout.rattish"
    timeout_source.write_text(
        """Rattish: 1
Workflow:
  name: Timeout
  timeout: 10ms
Node slow:
  type: bash-command
  command: slow
""",
        encoding="utf-8",
    )

    async def slow_handler(node, context, bindings):
        _ = node, context, bindings
        await anyio.sleep(1)
        return HandlerResult(True, _output())

    timed_out = await run_rattish_file(
        timeout_source,
        data_dir=tmp_path / "app-data",
        handlers=NodeHandlerRegistry({"raticode.bash_command": slow_handler}),
    )

    assert timed_out.status == "failed"
    assert timed_out.document["error"]["code"] == "RATTISH_TIMEOUT"
