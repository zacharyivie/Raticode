"""Backend side of the Studio Tests panel: read, save, estimate and run test sets.

Test runs go to a background thread like background workflow runs. The panel polls
the saved result, which ``run_test_set`` rewrites after each case.

Try another model sends ``models`` (``[{node, provider, model}]``) to the estimate and
the run. The panel's Use this model doesn't come here: it edits the node through the
graph editor like any other edit, so the open editor stays in step.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import anyio

from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.model_overrides import (
    ModelOverrideError,
    model_nodes,
    model_overrides_from_json,
)
from gofer.rattish.run_service import new_run_id
from gofer.rattish.test_sets import (
    WorkflowTestError,
    enforce_cost_limit,
    estimate_test_set,
    latest_result,
    list_test_results,
    prepare_test_run,
    read_test_result,
    read_test_set,
    result_summary,
    results_directory,
    run_test_set,
    save_test_set,
    with_comparison,
    workflow_tests_file,
)
from gofer.rattish.workspaces import (
    RattishWorkspaceError,
    RegisteredWorkflow,
    find_registered_workflow,
)

log = logging.getLogger(__name__)
_active: dict[tuple[str, str], tuple[str, threading.Event]] = {}
_active_lock = threading.Lock()
MAX_ACTIVE_TEST_RUNS = 4


def _workflow(workflow_id: str, data_dir: Path) -> RegisteredWorkflow:
    try:
        return find_registered_workflow(workflow_id, registry_dir=data_dir)
    except RattishWorkspaceError as exc:
        raise WorkflowTestError(f"Workflow {workflow_id!r} is not registered.") from exc


def _key(data_dir: Path, workflow_id: str) -> tuple[str, str]:
    return (str(data_dir.expanduser().resolve()), workflow_id.lower())


def _active_result_id(data_dir: Path, workflow_id: str) -> str | None:
    with _active_lock:
        entry = _active.get(_key(data_dir, workflow_id))
    return entry[0] if entry else None


def workflow_tests_payload(workflow_id: str, data_dir: Path) -> dict[str, Any]:
    """The test set, its expected cost, and saved results for the Tests panel."""
    workflow = _workflow(workflow_id, data_dir)
    path = workflow_tests_file(workflow.entrypoint)
    payload: dict[str, Any] = {
        "workflowId": workflow.workflow_id,
        "testSetPath": str(path),
        "exists": path.exists(),
        "testSet": None,
        "error": None,
        "raw": None,
        "revision": None,
        "estimate": None,
        "results": [],
        "latest": None,
        "activeResultId": _active_result_id(data_dir, workflow.workflow_id),
        "modelNodes": [],
    }
    try:
        compiled = compile_rattish_file(
            workflow.entrypoint,
            data_dir=data_dir,
            workflow_id=workflow.workflow_id,
            project_root=workflow.project_root,
        )
    except (RattishError, RattishArtifactError, OSError) as exc:
        payload["error"] = f"The workflow doesn't compile, so its tests can't run: {exc}"
        compiled = None
    directory = results_directory(workflow.entrypoint, workflow.workflow_id, data_dir)
    history = list_test_results(directory)
    payload["results"] = [result_summary(item) for item in history]
    latest = latest_result(history)
    if latest is not None:
        payload["latest"] = with_comparison(latest, history)
    try:
        test_set = read_test_set(path)
    except WorkflowTestError as exc:
        payload["error"] = str(exc)
        try:
            payload["raw"] = path.read_text(encoding="utf-8")[:200_000]
        except (OSError, UnicodeError):
            payload["raw"] = None
        return payload
    payload["testSet"] = test_set or {"testSetVersion": 1, "cases": []}
    if compiled is not None:
        payload["modelNodes"] = model_nodes(compiled.ir)
        payload["revision"] = compiled.ir["source"]["source_fingerprint"]
        payload["estimate"] = estimate_test_set(compiled.ir, payload["testSet"], history)
        payload["judgeEstimateSkipped"] = estimate_test_set(
            compiled.ir, payload["testSet"], history, skip_judge=True
        )["totals"]
    return payload


def save_workflow_tests_payload(
    workflow_id: str, data_dir: Path, body: dict[str, Any]
) -> dict[str, Any]:
    workflow = _workflow(workflow_id, data_dir)
    test_set = body.get("testSet")
    if test_set is None:
        raise WorkflowTestError("The request needs a testSet object.")
    save_test_set(workflow_tests_file(workflow.entrypoint), test_set)
    return workflow_tests_payload(workflow.workflow_id, data_dir)


def _prepare(workflow: RegisteredWorkflow, data_dir: Path, body: dict[str, Any]) -> Any:
    case_ids = body.get("caseIds")
    if case_ids is not None and (
        not isinstance(case_ids, list) or not all(isinstance(item, str) for item in case_ids)
    ):
        raise WorkflowTestError("caseIds must be a list of case IDs.")
    skip_judge = body.get("skipJudge", False)
    if not isinstance(skip_judge, bool):
        raise WorkflowTestError("skipJudge must be true or false.")
    try:
        models = model_overrides_from_json(body.get("models"))
        prepared = prepare_test_run(
            workflow.entrypoint,
            case_ids=case_ids or None,
            skip_judge=skip_judge,
            data_dir=data_dir,
            workflow_id=workflow.workflow_id,
            project_root=workflow.project_root,
            models=models,
        )
    except ModelOverrideError as exc:
        raise WorkflowTestError(str(exc)) from exc
    except (RattishError, RattishArtifactError, OSError) as exc:
        raise WorkflowTestError(f"The workflow can't be tested: {exc}") from exc
    expected_revision = body.get("expectedRevision")
    if expected_revision and expected_revision != prepared.ir["source"]["source_fingerprint"]:
        raise WorkflowTestError(
            "The workflow changed since the panel loaded. Check the expected cost again."
        )
    return prepared


def estimate_workflow_tests_payload(
    workflow_id: str, data_dir: Path, body: dict[str, Any]
) -> dict[str, Any]:
    """The expected cost of a run, for both models when ``models`` tries another one."""
    workflow = _workflow(workflow_id, data_dir)
    prepared = _prepare(workflow, data_dir, body)
    return {
        "revision": prepared.ir["source"]["source_fingerprint"],
        "estimate": prepared.cost_estimate,
        "variant": prepared.variant,
    }


def start_workflow_tests_payload(
    workflow_id: str, data_dir: Path, body: dict[str, Any]
) -> dict[str, Any]:
    """Start a test run in the background and return its first saved state."""
    workflow = _workflow(workflow_id, data_dir)
    max_cost = body.get("maxCostUsd")
    if max_cost is not None and (
        isinstance(max_cost, bool) or not isinstance(max_cost, (int, float)) or max_cost < 0
    ):
        raise WorkflowTestError("maxCostUsd must be a number of dollars.")
    prepared = _prepare(workflow, data_dir, body)
    enforce_cost_limit(prepared.cost_estimate, float(max_cost) if max_cost is not None else None)
    if not prepared.cases:
        raise WorkflowTestError("The test set has no cases to run.")
    key = _key(data_dir, workflow.workflow_id)
    result_id = new_run_id()
    cancel_event = threading.Event()
    with _active_lock:
        if key in _active:
            raise WorkflowTestError("This workflow's tests are already running.")
        if len(_active) >= MAX_ACTIVE_TEST_RUNS:
            raise WorkflowTestError("Too many test runs are going. Wait for one to finish.")
        _active[key] = (result_id, cancel_event)
    started = threading.Event()
    first: dict[str, Any] = {}

    def progress(document: dict[str, Any]) -> None:
        if not started.is_set():
            first["result"] = document
            started.set()

    async def execute() -> None:
        try:
            await run_test_set(
                prepared, cancel_event=cancel_event, result_id=result_id, on_progress=progress
            )
        except Exception as exc:
            if started.is_set():
                log.exception("Workflow test run %s failed", result_id)
            first.setdefault("error", str(exc))
        finally:
            with _active_lock:
                if _active.get(key, ("",))[0] == result_id:
                    _active.pop(key, None)
            started.set()

    thread = threading.Thread(
        target=lambda: anyio.run(execute), name=f"rattish-tests-{result_id}", daemon=True
    )
    thread.start()
    if not started.wait(30):
        cancel_event.set()
        raise WorkflowTestError("The test run did not start in time and was cancelled.")
    if "result" not in first:
        raise WorkflowTestError(first.get("error") or "The test run could not start.")
    return {"result": first["result"]}


def workflow_test_result_payload(
    workflow_id: str, data_dir: Path, result_id: str
) -> dict[str, Any]:
    workflow = _workflow(workflow_id, data_dir)
    directory = results_directory(workflow.entrypoint, workflow.workflow_id, data_dir)
    document = read_test_result(directory, result_id)
    return {"result": with_comparison(document, list_test_results(directory))}


def stop_workflow_tests_payload(workflow_id: str, data_dir: Path, result_id: str) -> dict[str, Any]:
    workflow = _workflow(workflow_id, data_dir)
    with _active_lock:
        entry = _active.get(_key(data_dir, workflow.workflow_id))
    if entry is None or entry[0] != result_id:
        raise WorkflowTestError("That test run is not going.")
    entry[1].set()
    return {"stopped": True, "resultId": result_id}


def stop_all_workflow_tests() -> None:
    """Ask every running test set to stop, for backend shutdown."""
    with _active_lock:
        events = [event for _, event in _active.values()]
    for event in events:
        event.set()
