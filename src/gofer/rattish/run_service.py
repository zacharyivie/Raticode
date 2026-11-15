"""Compile, preflight, execute, and persist one Rattish workflow run."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
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
    RattishArtifactError,
    compile_rattish_file,
    rattish_asset_root,
)
from gofer.rattish.contracts import canonical_json_bytes
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.preflight import PreflightRegistry, PreflightResult, run_preflight
from gofer.rattish.provider_runtime import default_provider_subscriptions
from gofer.rattish.result_cache import ResultCache
from gofer.rattish.run_usage import USAGE_LIMIT_CODES, RunUsageMeter
from gofer.rattish.run_values import (
    SavedValuesUnavailable,
    merge_withheld_inputs,
    plan_saved_values,
    read_saved_values,
    remove_orphaned_values,
    saved_values_refusal,
    write_saved_values,
)
from gofer.rattish.runtime import (
    DEFAULT_NODE_HANDLERS,
    InvalidRattishWorkflowInputError,
    NodeHandlerRegistry,
    RuntimeErrorInfo,
    _prepare_workflow_inputs,
)
from gofer.rattish.storage import (
    migrate_legacy_directory,
    registered_source_root,
    workflow_owned_directory,
)
from gofer.rattish.workflow_runtime import (
    NodeRunRecord,
    WorkflowExecutionResult,
    execute_workflow,
)
from gofer.subscriptions.base import Subscription
from gofer.utils.atomic_output import atomic_binary_output, unlink_without_links
from gofer.utils.paths import get_data_dir

RUN_ARTIFACT_VERSION = 1
_active_rattish_ids: set[str] = set()
_active_rattish_ids_lock = threading.Lock()
RunStatus = Literal[
    "passed", "failed", "preflight_failed", "invalid_inputs", "running", "stopped", "interrupted"
]
# How a run started: the app or command line, a trigger, the paired phone, or Rem
# (including its workers and organization agents). Saved as ``started_by``.
StartedBy = Literal["manual", "trigger", "phone", "rem"]
STARTED_BY: frozenset[str] = frozenset({"manual", "trigger", "phone", "rem"})


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


_WINDOWS = sys.platform == "win32"
# Enough to read a process's exit code and times, and granted for most processes,
# including elevated ones the same user started.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


@functools.cache
def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
    # Handles are pointer-sized; the default int result type would cut them in half.
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    filetime = ctypes.POINTER(wintypes.FILETIME)
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, filetime, filetime, filetime, filetime]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _windows_process_state(pid: int) -> tuple[bool, str | None]:
    """Whether a Windows process is going, and a token for when it started.

    Only queries the process. ``os.kill`` must never be used for this on Windows,
    where any signal number terminates the target.
    """
    if not _WINDOWS or pid <= 0 or pid > 0xFFFFFFFF:
        return False, None
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # No such process, or one this user may not query, which a runner never is.
        return False, None
    try:
        code = wintypes.DWORD()
        # A process that exited with code 259 reads as going while another
        # program still holds a handle to it; Raticode runners never exit so.
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False, None
        if code.value != _STILL_ACTIVE:
            return False, None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return True, None
        # Creation time in 100 ns steps since 1601, the same for the life of
        # the process and different for any later process given its PID.
        return True, f"win:{(created.dwHighDateTime << 32) | created.dwLowDateTime}"
    finally:
        kernel32.CloseHandle(handle)


def process_is_running(pid: int, start_token: object = None) -> bool:
    """Whether ``pid`` names a process that is still going, and the same one.

    When ``start_token`` is the string ``process_start_token`` gave for the
    runner, a different process that has since been given the PID counts as
    gone. Never signals the process.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if _WINDOWS:
        alive, current = _windows_process_state(pid)
        if not alive:
            return False
    else:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        current = process_start_token(pid) if isinstance(start_token, str) else None
    # Another program got the runner's PID, usually after a restart.
    return not (isinstance(start_token, str) and current is not None and current != start_token)


def process_start_token(pid: int) -> str | None:
    """Something that tells this process apart from a later one given the same PID.

    A PID is reused once its process ends, often right after a restart, so a run
    record only names a live runner when this matches too. Linux and Windows;
    elsewhere the PID check stands alone.
    """
    if _WINDOWS:
        return _windows_process_state(pid)[1]
    if not sys.platform.startswith("linux"):
        return None
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Field 22, the start time in clock ticks since boot, comes after the
    # parenthesized command name, which may itself contain spaces or brackets.
    fields = stat[stat.rfind(")") + 2 :].split()
    if len(fields) < 20 or not boot_id:
        return None
    return f"{boot_id}:{fields[19]}"


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
    if not process_is_running(pid, document.get("runner_start")):
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


def is_rattish_run_being_resumed(data_dir: Path, workflow_id: str, run_id: str) -> bool:
    """Whether any Raticode process is resuming that run now.

    Before the resumed run saves its first record, only its resume claim shows it.
    """
    key = (workflow_id, run_id)
    with _active_rattish_ids_lock:
        if key in _resuming:
            return True
    path = _resume_lock_path(data_dir, key)
    if sys.platform == "win32" or not path.exists():
        return False
    try:
        descriptor = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        # flock locks belong to one open file, so this fails while any holder,
        # this process included, keeps the claim.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(descriptor)
    return False


def _resume_lock_path(data_dir: Path, key: tuple[str, str]) -> Path:
    name = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest()
    return data_dir / "radish" / "resume-locks" / f"{name}.lock"


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
    if error.get("code") in USAGE_LIMIT_CODES:
        return (
            "This run ended at its max-tokens or max-cost-usd limit. Raise or remove the "
            "limit, then start a new run."
        )
    if (
        error.get("kind") in _DETERMINISTIC_ERROR_KINDS
        or error.get("code") == "RATTISH_RUN_RECORD_INVALID"
    ):
        return (
            "This run ended because of how the workflow is built, so resuming it would "
            "end the same way. Fix the workflow and start a new run."
        )
    # Inputs and trigger events come from the run's saved values (see run_values).
    # Withheld password inputs don't refuse: whoever resumes gives them again.
    values_refusal = saved_values_refusal(document)
    if values_refusal is not None:
        return values_refusal
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
        if event.get("type") in {"node_started", "node_retried", "node_fell_back"}:
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


def rerun_refusal(document: Mapping[str, Any]) -> str | None:
    """Why this run can't be run again with the same inputs, or None.

    Run again starts a new run of the workflow as it is now, with the run's saved
    inputs and trigger events. Withheld password inputs don't refuse; the caller
    gives them again (``withheld_input_names``).
    """
    if str(document.get("status")) == "invalid_inputs":
        return "This run's inputs were invalid, so they weren't saved. Start a new run instead."
    return saved_values_refusal(document)


async def rerun_rattish_run(
    source_path: Path,
    document: Mapping[str, Any],
    *,
    data_dir: Path | None = None,
    inputs: Mapping[str, Any] | None = None,
    **options: Any,
) -> RattishRunResult:
    """Run a workflow again with a saved run's inputs and trigger events.

    ``inputs`` holds only the password inputs the run didn't save. The new run
    uses the current source and records ``rerun_of``. Raises
    ``RattishResumeRefused`` with the reason when the values can't be used.
    """
    refusal = rerun_refusal(document)
    if refusal is not None:
        raise RattishResumeRefused(refusal)
    resolved_data_dir = data_dir or get_data_dir()
    record = _run_path(
        resolved_data_dir,
        str(document["workflow"]["id"]),
        str(document["run_id"]),
        source_path=source_path.expanduser().resolve(),
    )
    try:
        saved = read_saved_values(record, document)
        merged = merge_withheld_inputs(saved, inputs)
    except SavedValuesUnavailable as exc:
        raise RattishResumeRefused(str(exc)) from None
    # A rerun is started by whoever asked for it, even when it replays trigger events.
    options.setdefault("started_by", "manual")
    return await run_rattish_file(
        source_path,
        data_dir=resolved_data_dir,
        workflow_inputs=merged,
        trigger_events=list(saved.trigger_events) or None,
        rerun_of=str(document["run_id"]),
        **options,
    )


@contextmanager
def _resume_claim(data_dir: Path, key: tuple[str, str]) -> Iterator[None]:
    """Hold one run's resume against every Raticode process, not only this one.

    The CLI, the desktop, a paired phone and schedules can each start a resume.
    The OS releases a file lock when its holder exits, so a crash never leaves
    the run impossible to resume.
    """
    path = _resume_lock_path(data_dir, key)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
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


# Cross-process stop. The CLI, triggers (`rattish triggers start` or the app's own
# scheduler), the desktop backend and `rattish run` can each own a run, and only the
# owner holds its cancel event. Anyone else asks it to stop with a marker file beside
# the run record; the owner's stop watcher sees it and ends the run as "stopped".
_STOP_MARKER_POLL_SECONDS = 0.25
_STOP_SCAN_LIMIT = 200
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def run_stop_marker(record: Path) -> Path:
    """The stop request file for the run saved at ``record``."""
    # Writes and removals refuse linked parents, so name the folder by its real path.
    return record.parent.resolve() / f"{record.stem}.stop"


def run_stop_requested(record: Path) -> bool:
    """Whether some process has asked the run saved at ``record`` to stop."""
    try:
        return run_stop_marker(record).is_file()
    except OSError:
        return False


def _clear_run_stop(record: Path) -> None:
    try:
        unlink_without_links(run_stop_marker(record))
    except OSError:
        pass


def _record_is_running(record: Path) -> bool:
    try:
        document = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(document, dict) and effective_run_status(document) == "running"


def request_run_stop(record: Path) -> bool:
    """Ask the run saved at ``record`` to stop, whichever process runs it.

    Returns False when the run isn't going. The owner removes the marker after it
    saves its final record, so a request that races the run's own ending is
    withdrawn here rather than left behind.
    """
    if not _record_is_running(record):
        return False
    with atomic_binary_output(run_stop_marker(record)) as output:
        output.write(b"stop requested\n")
    if _record_is_running(record):
        return True
    _clear_run_stop(record)
    return False


def rattish_run_directory(data_dir: Path, workflow_id: str) -> Path:
    """Where a workflow's run records are saved, found by workflow ID alone."""
    from gofer.core.workflow import validate_workflow_id

    safe_id = validate_workflow_id(workflow_id)
    base = data_dir.expanduser().resolve()
    directory = workflow_owned_directory(safe_id, base, "logs")
    return directory if directory is not None else base / "radish" / "runs" / safe_id


def stop_rattish_runs(data_dir: Path, workflow_id: str, run_id: str | None = None) -> list[str]:
    """Ask one run, or every running run, of a workflow to stop; return the IDs asked.

    Works without a backend: it only reads run records and writes stop markers.
    Raises ValueError for an invalid workflow or run ID.
    """
    directory = rattish_run_directory(data_dir, workflow_id)
    if run_id is not None:
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError(f"Invalid run ID {run_id!r}.")
        records = [directory / f"{run_id}.json"]
    else:
        try:
            records = sorted(directory.glob("*.json"), reverse=True)[:_STOP_SCAN_LIMIT]
        except OSError:
            records = []
    return [record.stem for record in records if request_run_stop(record)]


@contextmanager
def _clearing_run_stop(record: Path) -> Iterator[None]:
    try:
        yield
    finally:
        _clear_run_stop(record)


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
    started_by: StartedBy | None = None,
    resume_inputs: Mapping[str, Any] | None = None,
    rerun_of: str | None = None,
    use_result_cache: bool = True,
) -> RattishRunResult:
    """Run one source file through the same boundary used by CLI and Studio.

    Use ``resume_rattish_run`` to pass ``resume_from``, the saved run to resume, and
    ``resume_inputs``, the password inputs that run didn't save. ``rerun_rattish_run``
    passes ``rerun_of``. Every run saves its inputs and trigger events beside its
    record (see ``run_values``) so it can be resumed or run again.
    ``started_by`` records how the run started (see ``STARTED_BY``); without it, a
    run with trigger events counts as triggered and any other as manual.
    ``use_result_cache=False`` runs every node even when it has ``cache:``; test sets
    use it so a case always checks fresh answers.
    """
    origin: StartedBy = started_by or ("trigger" if trigger_events else "manual")
    if origin not in STARTED_BY:
        raise ValueError(f"Unknown run origin {origin!r}.")
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
        earlier_record = _run_path(
            resolved_data_dir,
            compiled.ir["workflow"]["id"],
            str(resume_from["run_id"]),
            source_path=compiled.source_path,
        )
        try:
            saved = read_saved_values(earlier_record, resume_from)
            workflow_inputs = merge_withheld_inputs(saved, resume_inputs)
        except SavedValuesUnavailable as exc:
            raise RattishResumeRefused(str(exc)) from None
        trigger_events = saved.trigger_events or None
    elif resume_inputs:
        raise ValueError("resume_inputs only applies to a resumed run.")
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
    usage = RunUsageMeter.for_ir(compiled.ir, run_id)
    path = _run_path(
        resolved_data_dir,
        compiled.ir["workflow"]["id"],
        run_id,
        source_path=compiled.source_path,
    )
    # Exits after the final record is saved, so a stop request never outlives its run.
    with _track_rattish_run(run_id), _clearing_run_stop(path):
        supplied_inputs = dict(workflow_inputs or {})
        values, values_summary = plan_saved_values(compiled.ir, supplied_inputs, trigger_events)
        values_written = False

        def save_values() -> None:
            # Before the live record, so an interrupted run can still be resumed.
            nonlocal values_written, values_summary
            if values is None or values_written:
                return
            try:
                write_saved_values(path, run_id, values)
                values_written = True
            except OSError as exc:
                assert values_summary is not None
                values_summary = {
                    **values_summary,
                    "saved": False,
                    "reason": f"They couldn't be written: {exc.strerror or exc}.",
                }

        execution: WorkflowExecutionResult | None = None
        live: dict[str, Any] | None = None
        input_error: RuntimeErrorInfo | None = None
        stopped = False
        if deployment.ready:
            try:
                _prepare_workflow_inputs(compiled.ir, supplied_inputs)
                save_values()
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
                    usage=usage,
                    started_by=origin,
                    saved_values=values_summary,
                    rerun_of=rerun_of,
                )
                live["events"] = live["events"][:1]
                live["runner_pid"] = os.getpid()
                runner_start = process_start_token(os.getpid())
                if runner_start is not None:
                    live["runner_start"] = runner_start
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
                    live["usage"] = usage.snapshot()
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

                stop_seen = False

                def stop_wanted(*, check_marker: bool) -> bool:
                    nonlocal stop_seen
                    if (cancel_event is not None and cancel_event.is_set()) or (
                        check_marker and run_stop_requested(path)
                    ):
                        stop_seen = True
                    return stop_seen

                async def watch_stop(scope: anyio.CancelScope) -> None:
                    # The cancel event is this process's Stop; the marker is anyone else's.
                    next_marker_check = 0.0
                    while True:
                        now = time.monotonic()
                        check_marker = now >= next_marker_check
                        if check_marker:
                            next_marker_check = now + _STOP_MARKER_POLL_SECONDS
                        if stop_wanted(check_marker=check_marker):
                            break
                        await anyio.sleep(0.05)
                    scope.cancel()

                async with anyio.create_task_group() as group:
                    group.start_soon(watch_stop, group.cancel_scope)
                    if not stop_wanted(check_marker=True):
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
                            usage_meter=usage,
                            result_cache=(
                                ResultCache(resolved_data_dir) if use_result_cache else None
                            ),
                        )
                    group.cancel_scope.cancel()
                stopped = execution is None and stop_seen
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
        if status == "preflight_failed":
            # Its inputs were never checked; keep them so it can run again once fixed.
            save_values()
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
            usage=usage,
            started_by=origin,
            # Invalid inputs aren't kept: there is nothing to resume or run again.
            saved_values=None if status == "invalid_inputs" else values_summary,
            rerun_of=rerun_of,
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
        await _apply_retention_after_run(
            resolved_data_dir, compiled.ir["workflow"]["id"], path, run_id
        )
        # Saved inputs of runs deleted without delete_run_files go now.
        remove_orphaned_values(path.parent)
        return RattishRunResult(document, path, compiled, deployment)


async def _apply_retention_after_run(
    data_dir: Path, workflow_id: str, path: Path, run_id: str
) -> None:
    """Clean up old runs of this workflow under its saved retention settings.

    Runs once the final record is saved, for runs started from anywhere. The run
    that just settled is kept, since its caller still reads its record. Shielded
    so a stopped run still settles and still returns its result.
    """
    from gofer.rattish.run_retention import apply_run_retention

    with anyio.CancelScope(shield=True):
        await anyio.to_thread.run_sync(
            lambda: apply_run_retention(data_dir, workflow_id, path.parent, keep_run_ids=(run_id,))
        )


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
    usage: RunUsageMeter | None = None,
    started_by: StartedBy | None = None,
    saved_values: Mapping[str, Any] | None = None,
    rerun_of: str | None = None,
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
    if usage is not None:
        document["usage"] = usage.snapshot()
    if started_by is not None:
        document["started_by"] = started_by
    if saved_values is not None:
        document["saved_values"] = dict(saved_values)
    if rerun_of is not None:
        document["rerun_of"] = rerun_of
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
    if record.result.cached_at is not None:
        document["cached_at"] = record.result.cached_at
    if record.result.provider is not None:
        document["provider"] = dict(record.result.provider)
    if record.result.fallbacks:
        document["fallbacks"] = [dict(item) for item in record.result.fallbacks]
    if record.usage is not None:
        document["usage"] = dict(record.usage)
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


def has_live_run(source_path: Path, *, data_dir: Path | None = None, recent: int = 8) -> bool:
    """Whether a run of this source file is going now, started from anywhere.

    Run IDs sort by start time, so only the newest few records need reading.
    """
    try:
        directory = run_artifact_path(source_path, "probe", data_dir=data_dir).parent
        records = sorted(directory.glob("*.json"), reverse=True)[:recent]
    except (OSError, RattishArtifactError, RattishError):
        return False
    for record in records:
        try:
            document = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and effective_run_status(document) == "running":
            return True
    return False


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
