"""Compile, preflight, execute, and persist one Rattish workflow run."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import anyio
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from gofer.core.approvals import NotificationAdapter
from gofer.rattish.artifacts import (
    CompiledArtifact,
    compile_rattish_file,
    rattish_asset_root,
)
from gofer.rattish.contracts import canonical_json_bytes
from gofer.rattish.preflight import PreflightRegistry, PreflightResult, run_preflight
from gofer.rattish.provider_runtime import default_provider_subscriptions
from gofer.rattish.runtime import (
    DEFAULT_NODE_HANDLERS,
    InvalidRattishWorkflowInputError,
    NodeHandlerRegistry,
    RuntimeErrorInfo,
    _prepare_workflow_inputs,
)
from gofer.rattish.storage import migrate_legacy_directory, registered_source_root
from gofer.rattish.workflow_runtime import (
    NodeRunRecord,
    WorkflowExecutionResult,
    execute_workflow,
)
from gofer.subscriptions.base import Subscription
from gofer.utils.paths import get_data_dir

RUN_ARTIFACT_VERSION = 1
_active_rattish_ids: set[str] = set()
_active_rattish_ids_lock = threading.Lock()
RunStatus = Literal[
    "passed", "failed", "preflight_failed", "invalid_inputs", "running", "stopped", "interrupted"
]


class RattishRunArtifactError(RuntimeError):
    """Raised when a completed run artifact cannot be published."""


@dataclass(frozen=True, slots=True)
class RattishRunResult:
    document: dict[str, Any]
    path: Path
    compiled: CompiledArtifact
    preflight: PreflightResult

    @property
    def status(self) -> RunStatus:
        return cast(RunStatus, self.document["status"])

    @property
    def ok(self) -> bool:
        return self.status == "passed"


def is_rattish_run_active(run_id: str) -> bool:
    with _active_rattish_ids_lock:
        return run_id in _active_rattish_ids


@contextmanager
def _track_rattish_run(run_id: str) -> Iterator[None]:
    with _active_rattish_ids_lock:
        _active_rattish_ids.add(run_id)
    try:
        yield
    finally:
        with _active_rattish_ids_lock:
            _active_rattish_ids.discard(run_id)


async def run_rattish_file(
    source_path: Path,
    *,
    workflow_inputs: Mapping[str, Any] | None = None,
    trigger_events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
    data_dir: Path | None = None,
    handlers: NodeHandlerRegistry | None = None,
    subscriptions: Mapping[str, Subscription] | None = None,
    preflight_registry: PreflightRegistry | None = None,
    notification_adapter: NotificationAdapter | None = None,
    run_id: str | None = None,
    cancel_event: threading.Event | None = None,
    on_started: Callable[[RattishRunResult], None] | None = None,
    expected_revision: str | None = None,
) -> RattishRunResult:
    """Run one source file through the same boundary used by CLI and Studio."""
    started_at = datetime.now(UTC).isoformat()
    started_clock = time.monotonic()
    resolved_data_dir = data_dir or get_data_dir()
    compiled = compile_rattish_file(source_path, data_dir=resolved_data_dir)
    if expected_revision and compiled.ir["source"]["source_fingerprint"] != expected_revision:
        raise RattishRunArtifactError(
            "Workflow source changed after run preparation. "
            "Review and run the saved revision again."
        )
    runtime_handlers = handlers or DEFAULT_NODE_HANDLERS
    runtime_subscriptions = (
        subscriptions if subscriptions is not None else default_provider_subscriptions()
    )
    deployment = run_preflight(
        compiled.ir,
        registry=preflight_registry,
        data_dir=resolved_data_dir,
        subscriptions=runtime_subscriptions,
        handlers=runtime_handlers,
    )
    run_id = run_id or _new_run_id()
    with _track_rattish_run(run_id):
        supplied_inputs = dict(workflow_inputs or {})
        path = _run_path(
            resolved_data_dir,
            compiled.ir["workflow"]["id"],
            run_id,
            source_path=compiled.source_path,
        )

        execution: WorkflowExecutionResult | None = None
        input_error: RuntimeErrorInfo | None = None
        stopped = False
        if deployment.ready:
            try:
                _prepare_workflow_inputs(compiled.ir, supplied_inputs)
                live = _run_document(
                    run_id=run_id,
                    status="running",
                    compiled=compiled,
                    deployment=deployment,
                    execution=None,
                    input_error=None,
                    input_names=sorted(supplied_inputs),
                    started_at=started_at,
                    finished_at=started_at,
                    duration_ms=0,
                )
                live["events"] = live["events"][:1]
                live["runner_pid"] = os.getpid()
                _write_json_atomic(path, live)
                if on_started is not None:
                    on_started(RattishRunResult(live, path, compiled, deployment))

                async def watch_stop(scope: anyio.CancelScope) -> None:
                    while cancel_event is not None and not cancel_event.is_set():
                        await anyio.sleep(0.05)
                    if cancel_event is not None:
                        scope.cancel()

                async with anyio.create_task_group() as group:
                    if cancel_event is not None:
                        group.start_soon(watch_stop, group.cancel_scope)
                    if cancel_event is None or not cancel_event.is_set():
                        execution = await execute_workflow(
                            compiled.ir,
                            workflow_inputs=supplied_inputs,
                            trigger_events=trigger_events,
                            handlers=runtime_handlers,
                            subscriptions=runtime_subscriptions,
                            data_dir=resolved_data_dir,
                            notification_adapter=notification_adapter,
                            run_id=run_id,
                        )
                    group.cancel_scope.cancel()
                stopped = execution is None and cancel_event is not None and cancel_event.is_set()
            except InvalidRattishWorkflowInputError as exc:
                input_error = RuntimeErrorInfo(
                    "configuration",
                    "RATTISH_WORKFLOW_INPUT_INVALID",
                    str(exc),
                )
            except anyio.get_cancelled_exc_class():
                stopped = True
            except Exception as exc:
                input_error = RuntimeErrorInfo("runtime", "RATTISH_RUNTIME_UNEXPECTED", str(exc))

        finished_at = datetime.now(UTC).isoformat()
        status = "stopped" if stopped else _run_status(deployment, execution, input_error)
        document = _run_document(
            run_id=run_id,
            status=status,
            compiled=compiled,
            deployment=deployment,
            execution=execution,
            input_error=input_error,
            input_names=sorted(supplied_inputs),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=round((time.monotonic() - started_clock) * 1000),
        )
        path = _run_path(
            resolved_data_dir,
            compiled.ir["workflow"]["id"],
            run_id,
            source_path=compiled.source_path,
        )
        _write_json_atomic(path, document)
        return RattishRunResult(document, path, compiled, deployment)


def _run_status(
    deployment: PreflightResult,
    execution: WorkflowExecutionResult | None,
    input_error: RuntimeErrorInfo | None,
) -> RunStatus:
    if not deployment.ready:
        return "preflight_failed"
    if input_error is not None:
        return "invalid_inputs"
    if execution is not None and execution.outcome == "pass":
        return "passed"
    return "failed"


def _run_document(
    *,
    run_id: str,
    status: RunStatus,
    compiled: CompiledArtifact,
    deployment: PreflightResult,
    execution: WorkflowExecutionResult | None,
    input_error: RuntimeErrorInfo | None,
    input_names: list[str],
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> dict[str, Any]:
    ir = compiled.ir
    runs = [_node_run_document(record) for record in execution.runs] if execution else []
    error = input_error or (execution.error if execution is not None else None)
    events: list[dict[str, Any]] = [{"sequence": 1, "type": "workflow_started", "at": started_at}]
    for sequence, run in enumerate(runs, start=2):
        events.append(
            {
                "sequence": sequence,
                "type": "node_completed",
                "at": run["finished_at"],
                "node_id": run["node_id"],
                "run_number": run["run_number"],
                "outcome": run["outcome"],
            }
        )
    events.append(
        {
            "sequence": len(events) + 1,
            "type": "workflow_completed",
            "at": finished_at,
            "status": status,
        }
    )
    return {
        "run_artifact_version": RUN_ARTIFACT_VERSION,
        "run_id": run_id,
        "status": status,
        "workflow": {
            "id": ir["workflow"]["id"],
            "name": ir["workflow"]["name"],
        },
        "source": {
            "path": str(compiled.source_path),
            "source_fingerprint": ir["source"]["source_fingerprint"],
            "compilation_fingerprint": ir["source"]["compilation_fingerprint"],
            "cache_hit": compiled.cache_hit,
        },
        "compiled_snapshot": deepcopy(ir),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "input_names": input_names,
        "diagnostics": [
            *compiled.diagnostics,
            *(item.to_json() for item in deployment.diagnostics),
        ],
        "events": events,
        "runs": runs,
        "outputs": execution.outputs if execution is not None else {},
        "latest_node_outputs": execution.latest_node_outputs if execution is not None else {},
        "error": _runtime_error_document(error),
    }


def _node_run_document(record: NodeRunRecord) -> dict[str, Any]:
    return {
        "node_id": record.node_id,
        "run_number": record.run_number,
        "activation_lineage_id": record.activation_lineage_id,
        "activation_group_id": record.activation_group_id,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "duration_ms": record.duration_ms,
        "outcome": record.result.outcome,
        "output": record.result.output,
        "error": _runtime_error_document(record.result.error),
    }


def _runtime_error_document(error: RuntimeErrorInfo | None) -> dict[str, Any] | None:
    if error is None:
        return None
    return {
        "kind": error.kind,
        "code": error.code,
        "message": error.message,
        "details": error.details,
    }


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid.uuid4().hex[:12]}"


def _run_path(
    data_dir: Path,
    workflow_id: str,
    run_id: str,
    *,
    source_path: Path,
) -> Path:
    workflow_root = registered_source_root(source_path, data_dir)
    if workflow_root is not None:
        directory = workflow_root / "logs"
        migrate_legacy_directory(data_dir / "radish" / "runs" / workflow_id, directory)
        return directory / f"{run_id}.json"
    return data_dir / "radish" / "runs" / workflow_id / f"{run_id}.json"


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    try:
        schema = json.loads(
            (rattish_asset_root() / "schemas" / "run.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(document)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise RattishRunArtifactError(f"Invalid Rattish run artifact: {exc}") from exc
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(canonical_json_bytes(document) + b"\n")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RattishRunArtifactError(f"Could not publish Rattish run artifact: {exc}") from exc
