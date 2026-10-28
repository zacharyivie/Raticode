from __future__ import annotations

import json
import os
import stat
from collections import Counter
from pathlib import Path

import anyio
import pytest

from gofer.core.approvals import ApprovalStore
from gofer.rattish.run_service import RattishRunArtifactError, _write_json_atomic, run_rattish_file
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
        "node_started",
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
    if os.name != "nt":
        assert stat.S_IMODE(result.path.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_run_artifact_publication_refuses_linked_log_directory(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_bash_source(), encoding="utf-8")
    result = await run_rattish_file(source, data_dir=tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / result.path.name
    sentinel.write_bytes(b"private")
    result.path.parent.rename(tmp_path / "original-logs")
    result.path.parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RattishRunArtifactError, match="Could not publish"):
        _write_json_atomic(result.path, result.document)

    assert sentinel.read_bytes() == b"private"
    assert list(outside.iterdir()) == [sentinel]


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
async def test_read_file_may_read_a_file_an_upstream_node_creates(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Write then read
Node write:
  type: bash-command
  command: printf hello > report.txt
  to: read
Node read:
  type: read-file
  path: report.txt
  needs: write
""",
        encoding="utf-8",
    )

    result = await run_rattish_file(source, data_dir=tmp_path / "app-data")

    assert result.status == "passed"
    assert result.document["latest_node_outputs"]["read"]["content"] == "hello"


_GATE_ROUTES = """Rattish: 1
Workflow:
  name: Gate routes
Node gate:
  type: approval-gate
  message: Ship it?
{timeout}  to:
    - shipped when node.gate.output.approved == true
    - held otherwise
Node shipped:
  type: bash-command
  command: printf shipped
  needs: gate
Node held:
  type: bash-command
  command: printf held
  needs: gate
"""


@pytest.mark.anyio
async def test_rejected_approval_routes_on_approved_false(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_GATE_ROUTES.format(timeout=""), encoding="utf-8")
    data_dir = tmp_path / "app-data"
    store = ApprovalStore(data_dir)
    holder: dict[str, object] = {}

    async def run() -> None:
        holder["result"] = await run_rattish_file(source, data_dir=data_dir)

    async with anyio.create_task_group() as group:
        group.start_soon(run)
        while not store.list_pending():
            await anyio.sleep(0.01)
        pending = store.list_pending()[0]
        store.decide(pending.workflow_id, pending.run_id, "gate", "rejected", decided_by="owner")

    result = holder["result"]
    assert result.status == "passed"
    outputs = result.document["latest_node_outputs"]
    assert outputs["gate"]["decision"] == "rejected"
    assert outputs["gate"]["approved"] is False
    assert outputs["held"]["stdout"] == "held"
    assert "shipped" not in outputs


@pytest.mark.anyio
async def test_timed_out_approval_routes_on_approved_false(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        _GATE_ROUTES.format(timeout="  decision-timeout: 200ms\n  timeout-decision: reject\n"),
        encoding="utf-8",
    )

    result = await run_rattish_file(source, data_dir=tmp_path / "app-data")

    assert result.status == "passed"
    outputs = result.document["latest_node_outputs"]
    assert outputs["gate"]["decision"] == "rejected"
    assert outputs["held"]["stdout"] == "held"


@pytest.mark.anyio
async def test_pending_approval_is_cancelled_when_its_run_fails(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Gate outlived
Node gate:
  type: approval-gate
  message: Ship it?
  decision-timeout: 1s
  timeout-decision: reject
Node boom:
  type: bash-command
  command: sleep 0.2; exit 3
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "app-data"

    result = await run_rattish_file(source, data_dir=data_dir)

    assert result.status == "failed"
    store = ApprovalStore(data_dir)
    assert store.list_pending() == []
    [request] = store.list_requests()
    assert request.status == "cancelled"
    # The run's failure, not the gate's own stop, explains why the gate closed.
    assert request.cancelled_reason == "The workflow run ended (failed)."
    await anyio.sleep(1.1)
    # The closed gate neither times out later nor accepts a late decision.
    assert store.get(request.workflow_id, request.run_id, "gate").decision is None
    with pytest.raises(ValueError, match="cancelled"):
        store.decide(request.workflow_id, request.run_id, "gate", "rejected")


@pytest.mark.anyio
async def test_file_nodes_may_use_paths_an_upstream_node_creates(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Create then use
Node make:
  type: bash-command
  command: mkdir -p made && printf hi > made/a.txt
  to: inspect
Node inspect:
  type: folder
  path: made
  needs: make
  to: write
Node write:
  type: write-file
  path: made/new/b.txt
  content: written
  needs: inspect
  to: copy
Node copy:
  type: copy-file
  source-path: made/a.txt
  destination-path: made/c.txt
  needs: write
  to: move
Node move:
  type: move-file
  source-path: made/c.txt
  destination-path: made/d.txt
  needs: copy
  to: remove
Node remove:
  type: delete-file
  path: made/d.txt
  needs: move
""",
        encoding="utf-8",
    )

    result = await run_rattish_file(source, data_dir=tmp_path / "app-data")

    assert result.status == "passed", result.document["diagnostics"]
    assert (tmp_path / "made" / "new" / "b.txt").read_text(encoding="utf-8") == "written"
    assert not (tmp_path / "made" / "d.txt").exists()


@pytest.mark.anyio
async def test_scripts_templates_and_folders_may_be_created_upstream(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Generated inputs
Node make-script:
  type: write-file
  start: true
  path: gen/hello.sh
  content: echo hello from a generated script
  to: run-script
Node run-script:
  type: shell-script
  script-path: gen/hello.sh
  needs: make-script
Node make-template:
  type: write-file
  start: true
  path: gen/template.md
  content: Hello from a generated template
  to: render
Node render:
  type: prompt-file
  template-path: gen/template.md
  output-path: gen/rendered.md
  needs: make-template
Node make-dir:
  type: write-file
  start: true
  path: gen/workdir/keep.txt
  content: keep
  to: in-dir
Node in-dir:
  type: bash-command
  command: ls
  working-dir: gen/workdir
  needs: make-dir
Node make-docs:
  type: write-file
  start: true
  path: gen/docs/rockets.md
  content: rockets fly
  to: index
Node index:
  type: local-vectorize
  source-path: gen/docs
  index-path: gen/index.json
  needs: make-docs
""",
        encoding="utf-8",
    )

    result = await run_rattish_file(source, data_dir=tmp_path / "app-data")

    assert result.status == "passed", result.document["diagnostics"]
    outputs = result.document["latest_node_outputs"]
    assert "hello from a generated script" in outputs["run-script"]["stdout"]
    assert "keep.txt" in outputs["in-dir"]["stdout"]
    assert (tmp_path / "gen" / "index.json").is_file()


@pytest.mark.anyio
async def test_deferred_path_that_is_never_created_fails_its_node(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Never created
Node noop:
  type: bash-command
  command: "true"
  to: in-dir
Node in-dir:
  type: bash-command
  command: ls
  working-dir: missing/workdir
  needs: noop
""",
        encoding="utf-8",
    )

    result = await run_rattish_file(source, data_dir=tmp_path / "app-data")

    assert result.status == "failed"
    assert "missing/workdir" in json.dumps(result.document)


@pytest.mark.anyio
async def test_default_local_search_metadata_is_saved_in_the_run_record(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "alpha.md").write_text("alpha search words", encoding="utf-8")
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Index then search
Node index:
  type: local-vectorize
  source-path: notes
  index-path: index/notes.json
  to: search
Node search:
  type: local-search
  index-path: index/notes.json
  query: alpha
  needs: index
""",
        encoding="utf-8",
    )

    result = await run_rattish_file(source, data_dir=tmp_path / "app-data")

    assert result.status == "passed", result.document["error"]
    metadata = result.document["latest_node_outputs"]["search"]["results"][0]["metadata"]
    assert metadata["size"] == len("alpha search words")
    assert metadata["modified_at"].endswith("+00:00")
    assert json.loads(result.path.read_text(encoding="utf-8")) == result.document


@pytest.mark.anyio
async def test_unsaveable_node_output_fails_the_run_with_a_reason(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_bash_source(), encoding="utf-8")

    async def handler(node, context, bindings):
        return HandlerResult(True, {**_output(), "exit_code": 2**60})

    result = await run_rattish_file(
        source,
        data_dir=tmp_path / "app-data",
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )

    assert result.status == "failed"
    assert result.document["error"]["code"] == "RATTISH_RUN_RECORD_INVALID"
    assert "execute" in result.document["error"]["message"]
    assert result.document["events"][-1] == {
        **result.document["events"][-1],
        "type": "workflow_completed",
        "status": "failed",
    }
    assert json.loads(result.path.read_text(encoding="utf-8"))["status"] == "failed"


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
        "outcome": "failure",
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
    payload = workflow_run_log_payload(
        timed_out.document["workflow"]["id"], timed_out.document["run_id"], tmp_path / "app-data"
    )
    assert payload["runNodes"]["slow"]["status"] == "stopped"
    assert any(event["type"] == "node_stopped" for event in payload["runEvents"])


@pytest.mark.anyio
async def test_stdout_failure_is_visible_in_receipt_events_and_older_log_projection(tmp_path):
    source = tmp_path / "workflow.rattish"
    failure = json.dumps(
        {"ok": False, "reports": [{"failures": ["DNS resolution failed for www.youtube.com"]}]}
    )
    source.write_text(_bash_source("printf '%s' '" + failure + "'; exit 1"))
    failed = await run_rattish_file(source, data_dir=tmp_path / "app-data")
    assert "DNS resolution failed" in failed.document["error"]["message"]
    completed = next(
        event for event in failed.document["events"] if event["type"] == "node_completed"
    )
    assert "DNS resolution failed" in completed["message"]
    # Old receipts only kept a generic exit message; the reader can recover stdout.
    document = json.loads(failed.path.read_text())
    document["runs"][0]["error"]["message"] = "Command exited with status 1."
    document["events"][2]["message"] = "Command exited with status 1."
    failed.path.write_text(json.dumps(document))
    payload = workflow_run_log_payload(
        document["workflow"]["id"], document["run_id"], tmp_path / "app-data"
    )
    assert "DNS resolution failed" in payload["runNodes"]["execute"]["message"]
    assert any(
        "DNS resolution failed" in event.get("message", "") for event in payload["runEvents"]
    )
