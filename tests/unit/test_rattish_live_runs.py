from __future__ import annotations

import json
import threading
from pathlib import Path

import anyio
import pytest

import gofer.ui.api as api
from gofer.rattish.editor import source_revision
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.runtime import HandlerResult, NodeHandlerRegistry
from gofer.rattish.workspaces import create_registered_workflow


@pytest.mark.anyio
async def test_synchronous_run_stays_live_with_matching_source_revision(tmp_path: Path) -> None:
    base, workflow = setup_workflow(tmp_path)
    release = anyio.Event()
    started = anyio.Event()
    live = {}
    final = {}

    async def handler(node, context, bindings):
        await release.wait()
        return HandlerResult(True, {"stdout": "done", "stderr": "", "exit_code": 0})

    def on_started(run):
        live.update(run.document)
        started.set()

    async def execute():
        run = await run_rattish_file(
            workflow.entrypoint,
            data_dir=base,
            handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
            on_started=on_started,
            expected_revision=source_revision(workflow.entrypoint.read_text()),
        )
        final.update(run.document)

    async with anyio.create_task_group() as group:
        group.start_soon(execute)
        await started.wait()
        try:
            payload = api.workflow_run_log_payload(workflow.workflow_id, live["run_id"], base)
            assert payload["status"] == "running"
            assert payload["success"] is None
            assert payload["workflowId"] == workflow.workflow_id
        finally:
            release.set()
    assert final["status"] == "passed"


def setup_workflow(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    base = tmp_path / "data"
    workflow = create_registered_workflow(project, "Live run", registry_dir=base)
    workflow.entrypoint.write_text(
        "Rattish: 1\nWorkflow:\n  name: Live run\n"
        "Node work:\n  type: bash-command\n  command: fake-only\n",
        encoding="utf-8",
    )
    return base, workflow


@pytest.mark.anyio
async def test_background_runs_have_exact_ids_and_stop_only_one_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, workflow = setup_workflow(tmp_path)
    release = threading.Event()
    cleaned: set[str] = set()

    async def handler(node, context, bindings):
        try:
            while not release.is_set():
                await anyio.sleep(0.01)
            return HandlerResult(True, {"stdout": "done", "stderr": "", "exit_code": 0})
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.sleep(0.02)
                cleaned.add(context.run_id)

    async def fake_run(*args, **kwargs):
        return await run_rattish_file(
            *args, **kwargs, handlers=NodeHandlerRegistry({"raticode.bash_command": handler})
        )

    monkeypatch.setattr(api, "run_rattish_file", fake_run)
    first = await api.run_workflow_payload(workflow.workflow_id, base, background=True)
    second = await api.run_workflow_payload(workflow.workflow_id, base, background=True)
    try:
        assert first["workflowId"] == second["workflowId"] == workflow.workflow_id
        assert first["runId"] != second["runId"]
        assert first["status"] == second["status"] == "running"
        assert first["success"] is None
        assert first["graphSnapshot"]["nodes"][0]["id"] == "work"
        assert (
            len(
                api.list_workflow_run_logs_payload(workflow.workflow_id, base, status="running")[
                    "runs"
                ]
            )
            == 2
        )
        acknowledgement = api.stop_workflow_run_payload(workflow.workflow_id, base, first["runId"])
        assert acknowledgement["stopped"] is True
        with anyio.fail_after(5):
            while (
                api.workflow_run_log_payload(workflow.workflow_id, first["runId"], base)["status"]
                == "running"
            ):
                await anyio.sleep(0.01)
        stopped = api.workflow_run_log_payload(workflow.workflow_id, first["runId"], base)
        assert stopped["status"] == "stopped"
        assert first["runId"] in cleaned
        assert (
            api.workflow_run_log_payload(workflow.workflow_id, second["runId"], base)["status"]
            == "running"
        )
        assert second["runId"] not in cleaned
        assert (
            api.stop_workflow_run_payload(workflow.workflow_id, base, "wrong-id")["stopped"]
            is False
        )
    finally:
        release.set()
        with anyio.fail_after(5):
            while (
                api.workflow_run_log_payload(workflow.workflow_id, second["runId"], base)["status"]
                == "running"
            ):
                await anyio.sleep(0.01)
    assert (
        api.workflow_run_log_payload(workflow.workflow_id, second["runId"], base)["status"]
        == "success"
    )


@pytest.mark.anyio
async def test_background_run_rejects_changed_source_and_reports_preflight_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, workflow = setup_workflow(tmp_path)
    invoked = []

    async def handler(node, context, bindings):
        invoked.append(node["id"])
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0})

    async def fake_run(*args, **kwargs):
        return await run_rattish_file(
            *args, **kwargs, handlers=NodeHandlerRegistry({"raticode.bash_command": handler})
        )

    monkeypatch.setattr(api, "run_rattish_file", fake_run)
    old_revision = source_revision(workflow.entrypoint.read_text())
    workflow.entrypoint.write_text(
        "Rattish: 1\nWorkflow:\n  name: Changed\n"
        "Node missing:\n  type: read-file\n  path: missing.txt\n"
    )
    with pytest.raises(api.WorkflowRunError, match="source changed"):
        await api.run_workflow_payload(
            workflow.workflow_id, base, background=True, expected_revision=old_revision
        )
    assert invoked == []
    result = await api.run_workflow_payload(workflow.workflow_id, base, background=True)
    assert result["status"] == "error"
    assert result["rattishRun"]["status"] == "preflight_failed"
    assert invoked == []


@pytest.mark.anyio
async def test_orphaned_live_artifact_is_disconnected_never_succeeded(
    tmp_path: Path,
) -> None:
    base, workflow = setup_workflow(tmp_path)

    async def handler(node, context, bindings):
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0})

    run = await run_rattish_file(
        workflow.entrypoint,
        data_dir=base,
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )
    orphan = {**run.document, "status": "running", "runner_pid": 2147483647}
    run.path.write_text(json.dumps(orphan))
    result = api.workflow_run_log_payload(workflow.workflow_id, run.document["run_id"], base)
    assert result["status"] == "disconnected"
    assert result["success"] is None
    assert (
        api.stop_workflow_run_payload(workflow.workflow_id, base, run.document["run_id"])["stopped"]
        is False
    )
