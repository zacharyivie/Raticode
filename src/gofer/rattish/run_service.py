"""Compile, preflight, execute, and persist one Rattish workflow run."""

from __future__ import annotations

import hashlib
import json
import os
import sys
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

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from gofer.core.approvals import ApprovalStore, NotificationAdapter
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
from gofer.utils.atomic_output import atomic_binary_output
from gofer.utils.paths import get_data_dir

RUN_ARTIFACT_VERSION = 1
_active_rattish_ids: set[str] = set()
_active_rattish_ids_lock = threading.Lock()
RunStatus = Literal[
    "passed", "failed", "preflight_failed", "invalid_inputs", "running", "stopped", "interrupted"
]


class RattishRunArtifactError(RuntimeError):
    """Raised when a completed run artifact cannot be published."""


class RattishResumeRefused(RuntimeError):
    """Raised with a sentence for the user when a run can't be resumed."""


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


def effective_run_status(document: Mapping[str, Any]) -> str:
    """A saved status, with a run whose runner is gone reported as interrupted."""
    status = str(document.get("status"))
    if status != "running":
        return status
    pid = document.get("runner_pid")
    if not isinstance(pid, int) or pid <= 0:
        return "interrupted"
    if pid == os.getpid():
        return (
            "running" if is_rattish_run_active(str(document.get("run_id", ""))) else "interrupted"
        )
    if os.name == "nt":
        # Windows os.kill(pid, 0) terminates a process. A foreign owner
        # cannot be reconciled by this runner; report unknown instead.
        return "interrupted"
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return "interrupted"
    return "running"


# Resume semantics. A resumed run is a new run of the same saved source. It
# replays the earlier run's finished node results in their saved order, so the
# scheduler reaches the state the earlier run was in, then runs for real only
# what had not finished: the failed activation, activations a stop or crash cut
# off (these may have partly run), and everything they lead to. The earlier run
# is never changed. Anything replay can't rebuild exactly is refused.
RESUMABLE_STATUSES = frozenset({"failed", "stopped", "interrupted"})
# Errors the reused results themselves cause; replaying them ends the same way.
_DETERMINISTIC_ERROR_KINDS = frozenset({"workflow_control", "budget", "output_validation"})
_resuming: set[tuple[str, str]] = set()


def resume_refusal(document: Mapping[str, Any], ir: Mapping[str, Any] | None) -> str | None:
    """Why this run can't be resumed against the current compiled source, or None.

    ``document`` is a run record as read, with ``effective_run_status`` applied
    then. Its liveness is never checked again here: a copy read while the run was
    going must not count as interrupted once that run has finished.
    """
    status = str(document.get("status"))
    if status == "running":
        return "This run is still going. Stop it before resuming."
    if status == "passed":
        return "This run finished successfully, so there is nothing to resume."
    if status in NEVER_STARTED_STATUSES:
        return "This run never started its steps. Start a new run instead."
    if status not in RESUMABLE_STATUSES:
        return "This run can't be resumed."
    error = document.get("error") or {}
    if (
        error.get("kind") in _DETERMINISTIC_ERROR_KINDS
        or error.get("code") == "RATTISH_RUN_RECORD_INVALID"
    ):
        return (
            "This run ended because of how the workflow is built, so resuming it would "
            "end the same way. Fix the workflow and start a new run."
        )
    if document.get("input_names"):
        return (
            "This run was started with inputs, and their values aren't saved. "
            "Start a new run instead."
        )
    if document.get("trigger_event_count"):
        return (
            "This run was started by an event whose details aren't saved. Start a new run instead."
        )
    snapshot = document.get("compiled_snapshot")
    if not isinstance(snapshot, Mapping):
        return "This run was saved by an older version of Raticode and can't be resumed."
    if ir is None:
        return "The workflow has errors now. Fix it, then start a new run."
    saved = document.get("source") or {}
    if ir["source"]["source_fingerprint"] != saved.get("source_fingerprint") or ir["source"][
        "compilation_fingerprint"
    ] != saved.get("compilation_fingerprint"):
        return (
            "The workflow changed after this run, so its saved results may not fit. "
            "Start a new run instead."
        )
    reused = {
        record["node_id"]
        for record in document.get("runs") or []
        if record.get("outcome") in {"success", "allowed_failure"}
    }
    for node in ir["nodes"]:
        if node["id"] in reused and (node.get("configuration") or {}).get("memory") == "run":
            return (
                f"Agent {node['id']!r} remembers earlier turns only while its run is going, "
                "and those turns aren't saved, so this run can't be resumed."
            )
    return None


def resume_rerun_nodes(document: Mapping[str, Any]) -> list[str]:
    """Nodes a resume runs again because they failed or were cut off mid-run."""
    nodes = {
        record["node_id"]
        for record in document.get("runs") or []
        if record.get("outcome") == "failure"
    }
    open_activations: dict[tuple[Any, ...], str] = {}
    for event in document.get("events") or []:
        key = (
            event.get("node_id"),
            event.get("activation_group_id"),
            event.get("activation_lineage_id"),
            event.get("run_number"),
        )
        if event.get("type") in {"node_started", "node_retried"}:
            open_activations[key] = str(event.get("node_id"))
        elif event.get("type") == "node_completed":
            open_activations.pop(key, None)
    return sorted(nodes | set(open_activations.values()))


# Statuses of runs whose steps never started, so they redid nothing.
NEVER_STARTED_STATUSES = frozenset({"preflight_failed", "invalid_inputs"})


def resumed_run_id(document: Mapping[str, Any]) -> str | None:
    """The run this record resumed, if it counts as resuming that run.

    A resume whose steps never started (its pre-run checks failed, for example)
    reused and redid nothing, so it doesn't stop the earlier run from resuming.
    """
    earlier = (document.get("resumed_from") or {}).get("run_id")
    if not earlier or document.get("status") in NEVER_STARTED_STATUSES:
        return None
    return str(earlier)


def resumed_child(directory: Path, run_id: str) -> str | None:
    """The run that already resumed ``run_id``, if one exists."""
    if not directory.is_dir():
        return None
    for path in directory.glob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and resumed_run_id(document) == run_id:
            return str(document.get("run_id"))
    return None


async def resume_rattish_run(
    source_path: Path,
    document: Mapping[str, Any],
    *,
    data_dir: Path | None = None,
    **options: Any,
) -> RattishRunResult:
    """Resume a saved run as a new run. See the resume semantics above.

    Refuses a run that another run already resumed, so finished work is never
    redone by resuming the same run twice. Resume the newer run instead.
    """
    resolved_data_dir = data_dir or get_data_dir()
    workflow_id = str(document["workflow"]["id"])
    run_id = str(document["run_id"])
    directory = _run_path(
        resolved_data_dir, workflow_id, run_id, source_path=source_path.expanduser().resolve()
    ).parent
    key = (workflow_id, run_id)
    with _active_rattish_ids_lock:
        if key in _resuming:
            raise RattishResumeRefused("This run is already being resumed.")
        _resuming.add(key)
    try:
        with _resume_claim(resolved_data_dir, key):
            child = resumed_child(directory, run_id)
            if child is not None:
                raise RattishResumeRefused(
                    f"This run was already resumed as run {child}. Resume that run instead."
                )
            return await run_rattish_file(
                source_path, data_dir=resolved_data_dir, resume_from=document, **options
            )
    finally:
        with _active_rattish_ids_lock:
            _resuming.discard(key)


@contextmanager
def _resume_claim(data_dir: Path, key: tuple[str, str]) -> Iterator[None]:
    """Hold one run's resume against every Raticode process, not only this one.

    The CLI, the desktop, a paired phone and schedules can each start a resume.
    The OS releases a file lock when its holder exits, so a crash never leaves
    the run impossible to resume.
    """
    root = data_dir / "radish" / "resume-locks"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest()
    descriptor = os.open(root / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            if sys.platform == "win32":
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RattishResumeRefused("This run is already being resumed.") from error
        try:
            yield
        finally:
            if sys.platform == "win32":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(descriptor)


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
    resume_from: Mapping[str, Any] | None = None,
) -> RattishRunResult:
    """Run one source file through the same boundary used by CLI and Studio.

    Use ``resume_rattish_run`` to pass ``resume_from``, the saved run to resume.
    """
    started_at = datetime.now(UTC).isoformat()
    started_clock = time.monotonic()
    resolved_data_dir = data_dir or get_data_dir()
    compiled = compile_rattish_file(source_path, data_dir=resolved_data_dir)
    if expected_revision and compiled.ir["source"]["source_fingerprint"] != expected_revision:
        raise RattishRunArtifactError(
            "Workflow source changed after run preparation. "
            "Review and run the saved revision again."
        )
    if resume_from is not None:
        if workflow_inputs or trigger_events:
            raise RattishResumeRefused("A resumed run uses the saved run's inputs only.")
        # Checked against this compile, so the source can't change in between.
        refusal = resume_refusal(resume_from, compiled.ir)
        if refusal is not None:
            raise RattishResumeRefused(refusal)
    resumed: dict[str, Any] | None = (
        {"run_id": str(resume_from["run_id"]), "rerun_nodes": resume_rerun_nodes(resume_from)}
        if resume_from is not None
        else None
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
    run_id = run_id or new_run_id()
    with _track_rattish_run(run_id):
        supplied_inputs = dict(workflow_inputs or {})
        path = _run_path(
            resolved_data_dir,
            compiled.ir["workflow"]["id"],
            run_id,
            source_path=compiled.source_path,
        )

        execution: WorkflowExecutionResult | None = None
        live: dict[str, Any] | None = None
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
                    resumed=resumed,
                    trigger_event_count=len(trigger_events or ()),
                )
                live["events"] = live["events"][:1]
                live["runner_pid"] = os.getpid()
                _write_json_atomic(path, live)
                if on_started is not None:
                    on_started(RattishRunResult(live, path, compiled, deployment))

                def publish_node_event(event: dict[str, Any], record: NodeRunRecord | None) -> None:
                    assert live is not None
                    previous = (
                        len(live["events"]),
                        len(live["runs"]),
                        dict(live["latest_node_outputs"]),
                    )
                    live["events"].append({"sequence": len(live["events"]) + 1, **event})
                    if record is not None:
                        live["runs"].append(_node_run_document(record))
                        if record.result.outcome == "success":
                            live["latest_node_outputs"][record.node_id] = record.result.output
                    live["duration_ms"] = round((time.monotonic() - started_clock) * 1000)
                    try:
                        _write_json_atomic(path, live)
                    except RattishRunArtifactError as exc:
                        # Drop the unpublishable event so the final record can still be saved.
                        del live["events"][previous[0] :]
                        del live["runs"][previous[1] :]
                        live["latest_node_outputs"] = previous[2]
                        node = event.get("node_id") or "workflow"
                        raise _RunRecordError(
                            f"Node '{node}' produced output that cannot be saved "
                            f"to the run record: {exc}"
                        ) from exc

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
                            on_node_event=publish_node_event,
                            replay=list((resume_from or {}).get("runs") or []),
                            replay_source=resumed["run_id"] if resumed else None,
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
                record_error = _find_run_record_error(exc)
                if record_error is not None:
                    input_error = RuntimeErrorInfo(
                        "runtime", "RATTISH_RUN_RECORD_INVALID", str(record_error)
                    )
                else:
                    input_error = RuntimeErrorInfo(
                        "runtime", "RATTISH_RUNTIME_UNEXPECTED", str(exc)
                    )

        finished_at = datetime.now(UTC).isoformat()
        status = "stopped" if stopped else _run_status(deployment, execution, input_error)
        _cancel_pending_approvals(resolved_data_dir, compiled.ir["workflow"]["id"], run_id, status)
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
            resumed=resumed,
            trigger_event_count=len(trigger_events or ()),
        )
        if live is not None:
            # Keep the observed event order and completed work even after cancellation.
            document["events"] = [
                *live["events"],
                {
                    "sequence": len(live["events"]) + 1,
                    "type": "workflow_completed",
                    "at": finished_at,
                    "status": status,
                },
            ]
            if execution is None:
                document["runs"] = live["runs"]
                document["latest_node_outputs"] = live["latest_node_outputs"]
        if resumed is not None and resume_from is not None:
            reused = sum(1 for record in document["runs"] if record.get("reused_from_run_id"))
            reusable = sum(
                1
                for record in resume_from.get("runs") or []
                if record.get("outcome") in {"success", "allowed_failure"}
            )
            document["resumed_from"].update(
                reused_runs=reused, not_reused_runs=max(0, reusable - reused)
            )
        path = _run_path(
            resolved_data_dir,
            compiled.ir["workflow"]["id"],
            run_id,
            source_path=compiled.source_path,
        )
        try:
            _write_json_atomic(path, document)
        except RattishRunArtifactError as exc:
            # Never leave a run looking active: save a failed record with JSON-safe values.
            document = _json_safe(document)
            document["status"] = "failed"
            document["events"][-1]["status"] = "failed"
            document["error"] = _runtime_error_document(
                RuntimeErrorInfo(
                    "runtime",
                    "RATTISH_RUN_RECORD_INVALID",
                    f"The run record could not be saved as produced: {exc}",
                )
            )
            _write_json_atomic(path, document)
        return RattishRunResult(document, path, compiled, deployment)


def _cancel_pending_approvals(data_dir: Path, workflow_id: str, run_id: str, status: str) -> None:
    """Close approval requests that can no longer affect a finished run."""
    try:
        ApprovalStore(data_dir).cancel_pending_for_run(
            workflow_id, run_id, f"The workflow run ended ({status})."
        )
    except OSError:
        pass


class _RunRecordError(RuntimeError):
    """Raised when a node event cannot be persisted to the live run record."""


def _find_run_record_error(exc: BaseException) -> _RunRecordError | None:
    if isinstance(exc, _RunRecordError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            found = _find_run_record_error(item)
            if found is not None:
                return found
    cause = exc.__cause__ or exc.__context__
    return _find_run_record_error(cause) if cause is not None else None


_MAX_EXACT_INTEGER = 9_007_199_254_740_991


def _json_safe(value: Any) -> Any:
    """Convert values that RFC 8785 cannot represent into strings."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value if abs(value) <= _MAX_EXACT_INTEGER else str(value)
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _run_status(
    deployment: PreflightResult,
    execution: WorkflowExecutionResult | None,
    input_error: RuntimeErrorInfo | None,
) -> RunStatus:
    if not deployment.ready:
        return "preflight_failed"
    if input_error is not None:
        if input_error.code != "RATTISH_WORKFLOW_INPUT_INVALID":
            return "failed"
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
    resumed: dict[str, Any] | None = None,
    trigger_event_count: int = 0,
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
    document: dict[str, Any] = {
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
        "trigger_event_count": trigger_event_count,
    }
    if resumed is not None:
        document["resumed_from"] = dict(resumed)
    return document


def _node_run_document(record: NodeRunRecord) -> dict[str, Any]:
    document: dict[str, Any] = {
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
    if record.reused_from is not None:
        document["reused_from_run_id"] = record.reused_from
    return document


def _runtime_error_document(error: RuntimeErrorInfo | None) -> dict[str, Any] | None:
    if error is None:
        return None
    return {
        "kind": error.kind,
        "code": error.code,
        "message": error.message,
        "details": error.details,
    }


def new_run_id() -> str:
    """Return a sortable run ID shared by every surface that starts Rattish runs."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid.uuid4().hex[:12]}"


def run_artifact_path(source_path: Path, run_id: str, *, data_dir: Path | None = None) -> Path:
    """Where a run of this source file is saved."""
    resolved_data_dir = data_dir or get_data_dir()
    compiled = compile_rattish_file(source_path, data_dir=resolved_data_dir)
    return _run_path(
        resolved_data_dir,
        compiled.ir["workflow"]["id"],
        run_id,
        source_path=compiled.source_path,
    )


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
    try:
        with atomic_binary_output(path) as output:
            output.write(canonical_json_bytes(document) + b"\n")
    except (OSError, TypeError, ValueError) as exc:
        raise RattishRunArtifactError(f"Could not publish Rattish run artifact: {exc}") from exc
