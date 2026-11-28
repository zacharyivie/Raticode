"""Compile, preflight, execute, and persist one Rattish workflow run."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache, partial
from pathlib import Path
from typing import Any, Literal, cast

import anyio
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]
from referencing import Registry
from referencing.jsonschema import DRAFT202012

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from gofer.core.approvals import ApprovalStore, NotificationAdapter
from gofer.rattish import run_worktrees
from gofer.rattish.agent_sandbox import input_files_visible, run_worktree, triggered_run
from gofer.rattish.approved_files import ApprovedFiles, check_compiled, guarding
from gofer.rattish.artifacts import (
    CompiledArtifact,
    RattishArtifactError,
    compile_rattish_file,
    rattish_asset_root,
)
from gofer.rattish.contracts import canonical_json_bytes
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.file_inputs import (
    FileInputError,
    missing_copies,
    missing_copies_message,
    remove_orphaned_files,
    run_files_dir,
    stage_file_inputs,
    sweep_uploads,
)
from gofer.rattish.model_overrides import (
    ModelOverride,
    check_overrides,
    compile_variant,
    variant_record,
)
from gofer.rattish.preflight import PreflightResult, run_preflight
from gofer.rattish.provider_runtime import default_provider_subscriptions
from gofer.rattish.result_cache import ResultCache, RunPaths, stable_run_paths
from gofer.rattish.run_from import RunFromPlan, RunFromRefused, plan_run_from, reusable_count
from gofer.rattish.run_journal import (
    RunJournal,
    fold_into,
    journal_line,
    journal_path,
    read_entries,
    remove_journal,
)
from gofer.rattish.run_usage import USAGE_LIMIT_CODES, RunUsageMeter
from gofer.rattish.run_values import (
    SavedRunValues,
    SavedValuesUnavailable,
    merge_withheld_inputs,
    plan_saved_values,
    read_saved_values,
    remove_orphaned_values,
    saved_values_refusal,
    withheld_input_names,
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
from gofer.rattish.workflow_id import validate_workflow_id
from gofer.rattish.workflow_runtime import (
    NodeRunRecord,
    WorkflowExecutionResult,
    execute_workflow,
)
from gofer.subscriptions.base import Subscription
from gofer.utils.atomic_output import atomic_binary_output, unlink_without_links
from gofer.utils.paths import get_data_dir
from gofer.utils.pids import process_start_token as pids_start_token
from gofer.utils.pids import windows_process_state as _windows_process_state

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


class RattishRunFromRefused(RattishResumeRefused):
    """Raised with a sentence for the user when a run can't start from a node."""


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


# On Windows the process is only queried: os.kill terminates it whatever the signal.
_WINDOWS = sys.platform == "win32"


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
    record only names a live runner when this matches too. Where the start time
    can't be read, the PID check stands alone.
    """
    if _WINDOWS:
        return _windows_process_state(pid)[1]
    return pids_start_token(pid)


def run_started_by(document: Mapping[str, Any]) -> str:
    """How a run started. Runs saved before this was recorded count trigger events."""
    saved = document.get("started_by")
    if isinstance(saved, str) and saved:
        return saved
    return "trigger" if document.get("trigger_event_count") else "manual"


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


def fold_run_journal(record: Path) -> dict[str, Any] | None:
    """Add an interrupted run's journal to its record; the saved record, or None.

    Call it when a record reads as interrupted, before ``resume_refusal`` and
    ``resume_rerun_nodes`` look at it. It reads the record again, folds only while
    that record still says ``running`` and its runner is gone, saves the result and
    deletes the journal. Returns None when there was no journal to fold. The record it
    returns keeps its saved status; apply ``effective_run_status`` to it.
    """
    journal = journal_path(record)
    if not journal.exists():
        return None
    try:
        document = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    if document.get("status") != "running":
        # A finished run's journal is left only by a crash right after its final save.
        remove_journal(journal)
        return None
    if effective_run_status(document) != "interrupted":
        return None
    # Read after the runner is known gone: a run in this process stops counting as
    # active only after its final save deleted the journal.
    entries = read_entries(journal)
    if entries is None:
        return None
    if fold_into(document, entries):
        try:
            _write_json_atomic(record, document)
        except RattishRunArtifactError:
            return None  # Keep both as they are; the record alone still resumes.
    remove_journal(journal)
    return document


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
            "This run ended at its max-tokens or max-cost-usd limit, or at what was left of "
            "its trigger's daily limit. Raise or remove the limit, then start a new run."
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
    record = _run_path(
        resolved_data_dir, workflow_id, run_id, source_path=source_path.expanduser().resolve()
    )
    directory = record.parent
    if document.get("status") == "interrupted":
        # Steps its record hadn't saved yet are in its journal; resume reuses them too.
        folded = fold_run_journal(record)
        if folded is not None:
            document = {**folded, "status": effective_run_status(folded)}
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
    gone = missing_copies(None, merged, record)
    if gone:
        raise RattishResumeRefused(missing_copies_message(gone))
    # A rerun is started by whoever asked for it, even when it replays trigger events.
    options.setdefault("started_by", "manual")
    return await run_rattish_file(
        source_path,
        data_dir=resolved_data_dir,
        workflow_inputs=merged,
        trigger_events=list(saved.trigger_events) or None,
        rerun_of=str(document["run_id"]),
        file_inputs_from=record,
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


# Run from a node. Reads at most this many of the newest run records to find one to reuse.
_RUN_FROM_SCAN_LIMIT = 200


def plan_rattish_run_from(
    compiled: CompiledArtifact,
    node_id: str,
    *,
    data_dir: Path,
    reuse_run_id: str | None = None,
) -> RunFromPlan:
    """The earlier run a run from ``node_id`` reuses. See ``run_from``.

    Raises ``RattishRunFromRefused`` with the reason when no run fits.
    """
    directory = _run_path(
        data_dir, compiled.ir["workflow"]["id"], "probe", source_path=compiled.source_path
    ).parent
    if reuse_run_id is not None:
        reuse_run_id = reuse_run_id.removesuffix(".json")
        if not _RUN_ID_PATTERN.fullmatch(reuse_run_id):
            raise RattishRunFromRefused(f"Invalid run ID {reuse_run_id!r}.")
        paths = [directory / f"{reuse_run_id}.json"]
    else:
        try:
            paths = sorted(directory.glob("*.json"), reverse=True)[:_RUN_FROM_SCAN_LIMIT]
        except OSError:
            paths = []

    def documents() -> Iterator[dict[str, Any]]:
        for path in paths:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(document, dict) and document.get("run_id"):
                document["status"] = effective_run_status(document)
                if document["status"] == "interrupted":
                    document = fold_run_journal(path) or document
                    document["status"] = effective_run_status(document)
                yield document

    try:
        return plan_run_from(compiled.ir, node_id, documents(), reuse_run_id=reuse_run_id)
    except RunFromRefused as exc:
        raise RattishRunFromRefused(str(exc)) from None


def rattish_run_from_preview(
    source_path: Path,
    node_id: str,
    *,
    data_dir: Path | None = None,
    reuse_run_id: str | None = None,
) -> dict[str, Any]:
    """What a run from ``node_id`` would reuse, without running anything.

    ``needsInputs`` lists the password inputs the reused run didn't save; the run
    asks for them again, as Resume does. ``askAgainNodes`` lists the approval gates
    the reused run reached: none is reused, so each asks again when the run gets to it.
    """
    resolved_data_dir = data_dir or get_data_dir()
    compiled = compile_rattish_file(source_path, data_dir=resolved_data_dir)
    plan = plan_rattish_run_from(
        compiled, node_id, data_dir=resolved_data_dir, reuse_run_id=reuse_run_id
    )
    return {
        "fromNode": plan.from_node,
        "reuseRunId": plan.source_run_id,
        "reusedNodes": list(plan.reused_nodes),
        "askAgainNodes": list(plan.ask_again),
        "needsInputs": withheld_input_names(plan.source),
        "revision": compiled.ir["source"]["source_fingerprint"],
    }


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


# A running run's record is saved at most this often, unless something needs it saved at once.
_LIVE_SAVE_SECONDS = 1.0


class _LiveRecord:
    """The record of a run while it runs, saved at most once every ``_LIVE_SAVE_SECONDS``.

    Saving the whole record on every step made a long loop slow down with the square of
    its length. The first save checks the whole record; later ones save only after
    ``add`` checked each new event and node run on its own, and the final record is
    checked whole by the caller. So that a crash loses at most the step that was
    running, ``add`` also appends each checked event and node run to the run's journal
    (``run_journal``) at once; an interrupted run is found by its ``runner_pid`` and
    ``fold_run_journal`` adds the journal's entries to its record.
    """

    def __init__(self, path: Path, document: dict[str, Any]) -> None:
        self.path = path
        self.document = document
        # The worktree is saved from a worker thread while the loop may flush.
        self._lock = threading.Lock()
        self._pending = False
        self._saved_at = 0.0
        self._journal: RunJournal | None = None

    def save_first(self) -> None:
        with self._lock:
            _write_json_atomic(self.path, self.document)
            self._pending = False
            self._saved_at = time.monotonic()
        try:
            self._journal = RunJournal(self.path)
        except OSError:
            self._journal = None  # The record still saves once a second.

    def add(self, event: dict[str, Any], node_run: dict[str, Any] | None) -> None:
        """Check one new event and its node run, then journal them.

        Raises ``RattishRunArtifactError`` if they can't be saved.
        """
        _check_run_part("event", event)
        if node_run is not None:
            _check_run_part("node_run", node_run)
        try:
            # Values the schema allows but JSON can't hold, such as integers past 2**53.
            line = journal_line(event, node_run)
        except (TypeError, ValueError) as exc:
            raise RattishRunArtifactError(f"Could not publish Rattish run artifact: {exc}") from exc
        if self._journal is not None:
            self._journal.append(line)

    def publish(
        self,
        event: dict[str, Any],
        record: NodeRunRecord | None,
        *,
        live: dict[str, Any],
        usage: RunUsageMeter,
        started_clock: float,
        approval_nodes: set[str],
    ) -> None:
        """Add one node event, and the node run it finished, to ``live``, the record kept here.

        Saves at once when a node failed, stopped or starts waiting for a person on one
        of ``approval_nodes``. An event that can't be saved is dropped, so the final
        record can still be saved, and raises ``_RunRecordError`` to fail the run.
        """
        previous = (
            len(live["events"]),
            len(live["runs"]),
            dict(live["latest_node_outputs"]),
        )
        entry = {"sequence": len(live["events"]) + 1, **event}
        node_run = _node_run_document(record) if record is not None else None
        try:
            self.add(entry, node_run)
            live["events"].append(entry)
            if record is not None:
                live["runs"].append(node_run)
                if record.result.outcome == "success":
                    live["latest_node_outputs"][record.node_id] = record.result.output
            live["usage"] = usage.snapshot()
            live["duration_ms"] = round((time.monotonic() - started_clock) * 1000)
            kind = event["type"]
            failed = record is not None and record.result.outcome != "success"
            waits = kind == "node_started" and event["node_id"] in approval_nodes
            self.changed(at_once=failed or waits or kind == "node_stopped")
        except RattishRunArtifactError as exc:
            del live["events"][previous[0] :]
            del live["runs"][previous[1] :]
            live["latest_node_outputs"] = previous[2]
            node = event.get("node_id") or "workflow"
            raise _RunRecordError(
                f"Node '{node}' produced output that cannot be saved to the run record: {exc}"
            ) from exc

    def finish(self) -> None:
        """Delete the journal once the final record is saved."""
        if self._journal is not None:
            self._journal.finish()

    def close(self) -> None:
        """Stop journaling but keep the journal, for a run whose final record wasn't saved."""
        if self._journal is not None:
            self._journal.close()

    def set_worktree(self, worktree: dict[str, Any]) -> None:
        _check_run_part("worktree", worktree)
        with self._lock:
            self.document["worktree"] = worktree
        self.changed(at_once=True)

    def changed(self, *, at_once: bool = False) -> None:
        """Save now if ``at_once`` or a save is due; otherwise ``keep_saved`` saves it soon."""
        self._pending = True
        if at_once or time.monotonic() - self._saved_at >= _LIVE_SAVE_SECONDS:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            _publish_json(self.path, self.document)
            self._pending = False
            self._saved_at = time.monotonic()

    async def keep_saved(self) -> None:
        """Save changes no later event brought to disk, once their second is up."""
        while True:
            await anyio.sleep(_LIVE_SAVE_SECONDS / 10)
            if self._pending and time.monotonic() - self._saved_at >= _LIVE_SAVE_SECONDS:
                try:
                    self.flush()
                except RattishRunArtifactError as exc:
                    raise _RunRecordError(f"The run record could not be saved: {exc}") from exc


class _StopWatch:
    """Whether a running run was asked to stop, by ``cancel_event`` or its stop marker.

    The cancel event is this process's Stop; the marker beside ``record`` is anyone
    else's. The first stop seen sets ``setup_cancel``, which stops a worktree's project
    setup running in a thread, and saves the live record: the steps it stops save the
    rest, and the final record follows.
    """

    def __init__(
        self, cancel_event: threading.Event | None, record: Path, live_record: _LiveRecord
    ) -> None:
        self.stop_seen = False
        self.cancel_event = cancel_event
        self.record = record
        self.setup_cancel = threading.Event()
        self.live_record = live_record

    def wanted(self, *, check_marker: bool) -> bool:
        """True once a stop was seen; ``check_marker`` also reads the stop marker."""
        if not self.stop_seen and (
            (self.cancel_event is not None and self.cancel_event.is_set())
            or (check_marker and run_stop_requested(self.record))
        ):
            self.stop_seen = True
            self.setup_cancel.set()
            self.live_record.flush()
        return self.stop_seen

    async def watch(self, scope: anyio.CancelScope) -> None:
        """Cancel ``scope`` once a stop is seen, reading the marker every poll interval."""
        next_marker_check = 0.0
        while True:
            now = time.monotonic()
            check_marker = now >= next_marker_check
            if check_marker:
                next_marker_check = now + _STOP_MARKER_POLL_SECONDS
            if self.wanted(check_marker=check_marker):
                break
            await anyio.sleep(0.05)
        scope.cancel()


@dataclass(frozen=True, slots=True)
class _PreparedRun:
    """What ``_prepare_run`` worked out before a run's preflight: the compile and its basis.

    ``workflow_inputs``, ``trigger_events`` and ``file_inputs_from`` are the earlier
    run's for a resume or a run from a node, and the caller's otherwise.
    """

    resolved_data_dir: Path
    compiled: CompiledArtifact
    model_variant: dict[str, Any] | None
    workflow_inputs: Mapping[str, Any] | None
    trigger_events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None
    file_inputs_from: Path | None
    run_from: RunFromPlan | None
    replayed: dict[str, Any] | None
    resumed: dict[str, Any] | None


def _earlier_run_values(
    compiled: CompiledArtifact,
    data_dir: Path,
    document: Mapping[str, Any],
    withheld: Mapping[str, Any] | None,
    *,
    run_id: str,
    saved: SavedRunValues | None = None,
    refused: type[RattishResumeRefused],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...] | None, Path]:
    """The earlier run's inputs, trigger events and record, for a resume or a run from a node.

    Reads the values saved beside its record unless ``saved`` already holds them, adds
    the withheld inputs given again, and checks its file copies are still there. Raises
    ``refused`` with the reason when any of that fails.
    """
    record = _run_path(
        data_dir, compiled.ir["workflow"]["id"], run_id, source_path=compiled.source_path
    )
    try:
        values = saved if saved is not None else read_saved_values(record, document)
        inputs = merge_withheld_inputs(values, withheld)
    except SavedValuesUnavailable as exc:
        raise refused(str(exc)) from None
    gone = missing_copies(compiled.ir, inputs, record)
    if gone:
        raise refused(missing_copies_message(gone))
    return inputs, values.trigger_events or None, record


def _prepare_run(
    source_path: Path,
    *,
    data_dir: Path | None,
    workflow_inputs: Mapping[str, Any] | None,
    trigger_events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None,
    expected_revision: str | None,
    approved_files: ApprovedFiles | None,
    model_overrides: Sequence[ModelOverride] | None,
    resume_from: Mapping[str, Any] | None,
    resume_inputs: Mapping[str, Any] | None,
    resume_values: SavedRunValues | None,
    rerun_of: str | None,
    from_node: str | None,
    reuse_run_id: str | None,
    file_inputs_from: Path | None,
) -> _PreparedRun:
    """Compile ``source_path`` and plan what kind of run this is, for ``run_rattish_file``.

    Checks the approved files and the expected revision, applies model overrides, and
    plans a resume or a run from a node against this one compile. It only reads and
    raises: nothing is written to the run folder until the run itself starts.
    """
    resolved_data_dir = data_dir or get_data_dir()
    compiled = compile_rattish_file(source_path, data_dir=resolved_data_dir)
    if approved_files is not None:
        check_compiled(compiled.source_path, compiled.ir, "workflow", files=approved_files)
    if expected_revision and compiled.ir["source"]["source_fingerprint"] != expected_revision:
        raise RattishRunArtifactError(
            "Workflow source changed after run preparation. "
            "Review and run the saved revision again."
        )
    model_variant: dict[str, Any] | None = None
    if model_overrides:
        if (
            resume_from is not None
            or from_node is not None
            or rerun_of is not None
            or approved_files is not None
        ):
            raise ValueError(
                "A run with another model can't resume, rerun, start from a node or "
                "run from a trigger."
            )
        checked = check_overrides(compiled.ir, model_overrides)
        model_variant = variant_record(compiled.ir, checked)
        compiled = compile_variant(compiled, checked, data_dir=resolved_data_dir)
    if resume_from is not None:
        if workflow_inputs or trigger_events:
            raise RattishResumeRefused("A resumed run uses the saved run's inputs only.")
        # Checked against this compile, so the source can't change in between.
        refusal = resume_refusal(resume_from, compiled.ir)
        if refusal is not None:
            raise RattishResumeRefused(refusal)
        workflow_inputs, trigger_events, file_inputs_from = _earlier_run_values(
            compiled,
            resolved_data_dir,
            resume_from,
            resume_inputs,
            run_id=str(resume_from["run_id"]),
            saved=resume_values,
            refused=RattishResumeRefused,
        )
    run_from: RunFromPlan | None = None
    if from_node is not None:
        if resume_from is not None or rerun_of is not None:
            raise ValueError("A run from a node can't also resume or rerun a run.")
        if workflow_inputs or trigger_events:
            raise RattishRunFromRefused("A run from a node uses the reused run's inputs only.")
        # Planned against this compile, so the source can't change in between.
        run_from = plan_rattish_run_from(
            compiled, from_node, data_dir=resolved_data_dir, reuse_run_id=reuse_run_id
        )
        workflow_inputs, trigger_events, file_inputs_from = _earlier_run_values(
            compiled,
            resolved_data_dir,
            run_from.source,
            resume_inputs,
            run_id=run_from.source_run_id,
            refused=RattishRunFromRefused,
        )
    elif reuse_run_id is not None:
        raise ValueError("reuse_run_id only applies to a run from a node.")
    if resume_inputs and resume_from is None and from_node is None:
        raise ValueError("resume_inputs only applies to a resumed run or a run from a node.")
    if resume_values is not None and resume_from is None:
        raise ValueError("resume_values only applies to a resumed run.")
    replayed: dict[str, Any] | None = (
        {
            "run_id": run_from.source_run_id,
            "node_id": run_from.from_node,
            "reused_nodes": list(run_from.reused_nodes),
        }
        if run_from is not None
        else None
    )
    resumed: dict[str, Any] | None = (
        {"run_id": str(resume_from["run_id"]), "rerun_nodes": resume_rerun_nodes(resume_from)}
        if resume_from is not None
        else None
    )
    return _PreparedRun(
        resolved_data_dir=resolved_data_dir,
        compiled=compiled,
        model_variant=model_variant,
        workflow_inputs=workflow_inputs,
        trigger_events=trigger_events,
        file_inputs_from=file_inputs_from,
        run_from=run_from,
        replayed=replayed,
        resumed=resumed,
    )


async def run_rattish_file(
    source_path: Path,
    *,
    workflow_inputs: Mapping[str, Any] | None = None,
    trigger_events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
    data_dir: Path | None = None,
    handlers: NodeHandlerRegistry | None = None,
    subscriptions: Mapping[str, Subscription] | None = None,
    notification_adapter: NotificationAdapter | None = None,
    run_id: str | None = None,
    cancel_event: threading.Event | None = None,
    on_started: Callable[[RattishRunResult], None] | None = None,
    expected_revision: str | None = None,
    resume_from: Mapping[str, Any] | None = None,
    started_by: StartedBy | None = None,
    resume_inputs: Mapping[str, Any] | None = None,
    resume_values: SavedRunValues | None = None,
    rerun_of: str | None = None,
    use_result_cache: bool = True,
    daily_max_cost_usd: float | None = None,
    daily_max_tokens: int | None = None,
    from_node: str | None = None,
    reuse_run_id: str | None = None,
    approved_files: ApprovedFiles | None = None,
    use_worktree: bool = True,
    file_inputs_from: Path | None = None,
    model_overrides: Sequence[ModelOverride] | None = None,
) -> RattishRunResult:
    """Run one source file through the same boundary used by CLI and Studio.

    Use ``resume_rattish_run`` to pass ``resume_from``, the saved run to resume, and
    ``resume_inputs``, the password inputs that run didn't save. ``resume_values`` is
    that run's saved values when the caller already read and checked them, as a
    triggered resume does; without it they are read from beside the run's record.
    ``rerun_rattish_run``
    passes ``rerun_of``. Every run saves its inputs and trigger events beside its
    record (see ``run_values``) so it can be resumed or run again.
    ``started_by`` records how the run started (see ``STARTED_BY``); without it, a
    run with trigger events counts as triggered and any other as manual.
    ``use_result_cache=False`` runs every node even when it has ``cache:``; test sets
    use it so a case always checks fresh answers.
    ``daily_max_cost_usd`` and ``daily_max_tokens`` are what is left of a trigger's
    daily limit: the run's cap is the smaller of these and the workflow's own limits.
    ``from_node`` starts the run from that node, reusing an earlier run's results for
    the nodes upstream of it (see ``run_from``): the newest run that fits, or
    ``reuse_run_id``. It uses that run's saved inputs; ``resume_inputs`` gives its
    password inputs again. The run records ``replayed_from``.
    ``approved_files`` is what a trigger's user approved, for a triggered run that needs
    approval: each covered file is checked right before it is used (``approved_files``).
    A workflow with ``workspace: worktree`` runs in a git worktree of its own (see
    ``run_worktrees``) unless ``use_worktree`` is False, as for test sets. Its steps read
    the worktree's copies of the approved files, so those are checked instead.
    File inputs are copied into the run folder before the run starts (``file_inputs``).
    ``file_inputs_from`` is the record of the run whose saved inputs these are, as Run
    again passes it: its copies are linked rather than checked again.
    ``model_overrides`` runs some nodes with another provider and model, compiled from
    an edited copy of the source held in memory (``model_overrides``); the source file
    is never changed. The run records the variant as ``model_overrides``, and its
    ``compiled_snapshot`` and fingerprints are the variant's. Test sets use it to try
    another model; it can't be combined with a resume, a rerun or a triggered run.
    """
    origin: StartedBy = started_by or ("trigger" if trigger_events else "manual")
    if origin not in STARTED_BY:
        raise ValueError(f"Unknown run origin {origin!r}.")
    started_at = datetime.now(UTC).isoformat()
    started_clock = time.monotonic()
    runtime_handlers = handlers or DEFAULT_NODE_HANDLERS
    runtime_subscriptions = (
        subscriptions if subscriptions is not None else default_provider_subscriptions()
    )
    prepared = _prepare_run(
        source_path,
        data_dir=data_dir,
        workflow_inputs=workflow_inputs,
        trigger_events=trigger_events,
        expected_revision=expected_revision,
        approved_files=approved_files,
        model_overrides=model_overrides,
        resume_from=resume_from,
        resume_inputs=resume_inputs,
        resume_values=resume_values,
        rerun_of=rerun_of,
        from_node=from_node,
        reuse_run_id=reuse_run_id,
        file_inputs_from=file_inputs_from,
    )
    deployment = run_preflight(
        prepared.compiled.ir,
        data_dir=prepared.resolved_data_dir,
        subscriptions=runtime_subscriptions,
        handlers=runtime_handlers,
        triggered=origin == "trigger",
        worktree=use_worktree,
    )
    resolved_data_dir = prepared.resolved_data_dir
    compiled = prepared.compiled
    workflow_inputs = prepared.workflow_inputs
    trigger_events = prepared.trigger_events
    file_inputs_from = prepared.file_inputs_from
    run_from = prepared.run_from
    model_variant = prepared.model_variant
    replayed = prepared.replayed
    resumed = prepared.resumed
    run_id = run_id or new_run_id()
    usage = RunUsageMeter.for_ir(
        compiled.ir,
        run_id,
        daily_max_cost_usd=daily_max_cost_usd,
        daily_max_tokens=daily_max_tokens,
    )
    path = _run_path(
        resolved_data_dir,
        compiled.ir["workflow"]["id"],
        run_id,
        source_path=compiled.source_path,
    )
    # What the live record and the final record both say about this run.
    record_fields: dict[str, Any] = {
        "run_id": run_id,
        "compiled": compiled,
        "deployment": deployment,
        "started_at": started_at,
        "resumed": resumed,
        "trigger_event_count": len(trigger_events or ()),
        "usage": usage,
        "started_by": origin,
        "rerun_of": rerun_of,
        "replayed": replayed,
        "model_variant": model_variant,
    }
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
        live_record: _LiveRecord | None = None
        input_error: RuntimeErrorInfo | None = None
        stopped = False
        worktree: dict[str, Any] | None = None
        executed = False
        in_worktree = use_worktree and run_worktrees.uses_worktree(compiled.ir)
        if deployment.ready and in_worktree:
            refused = run_worktrees.limit_message(resolved_data_dir, compiled.ir["workflow"]["id"])
            if refused is not None:
                input_error = RuntimeErrorInfo("workspace", "RATTISH_WORKTREE_LIMIT", refused)
        if deployment.ready and input_error is None:
            try:
                _prepare_workflow_inputs(compiled.ir, supplied_inputs)
                # Each file input becomes this run's own copy, and the saved inputs
                # record the copy's path so Resume and Run again reuse it.
                staged = stage_file_inputs(
                    compiled.ir,
                    supplied_inputs,
                    path,
                    data_dir=resolved_data_dir,
                    reuse_from=file_inputs_from,
                    base=Path(compiled.ir["source"]["project_root"]),
                )
                supplied_inputs = staged.inputs
                values, values_summary = plan_saved_values(
                    compiled.ir, supplied_inputs, trigger_events
                )
                save_values()
                live = _run_document(
                    **record_fields,
                    status="running",
                    execution=None,
                    input_error=None,
                    input_names=sorted(supplied_inputs),
                    finished_at=started_at,
                    duration_ms=0,
                    saved_values=values_summary,
                )
                live["events"] = live["events"][:1]
                live["runner_pid"] = os.getpid()
                runner_start = process_start_token(os.getpid())
                if runner_start is not None:
                    live["runner_start"] = runner_start
                live_record = _LiveRecord(path, live)
                live_record.save_first()
                if on_started is not None:
                    on_started(RattishRunResult(live, path, compiled, deployment))
                # Starting one of these waits for a person, so the record shows it at once.
                approval_nodes = {
                    node["id"] for node in compiled.ir["nodes"] if "approval" in node["effects"]
                }

                on_node_event = partial(
                    live_record.publish,
                    live=live,
                    usage=usage,
                    started_clock=started_clock,
                    approval_nodes=approval_nodes,
                )
                stop = _StopWatch(cancel_event, path, live_record)

                run_ir: Any = compiled.ir
                async with anyio.create_task_group() as group:
                    group.start_soon(stop.watch, group.cancel_scope)
                    group.start_soon(live_record.keep_saved)
                    if in_worktree and not stop.wanted(check_marker=True):
                        run_ir, worktree, input_error = await anyio.to_thread.run_sync(
                            run_worktrees.start_for_run,
                            resolved_data_dir,
                            compiled.ir,
                            run_id,
                            stop.setup_cancel,
                            live_record.set_worktree,
                        )
                        if stop.wanted(check_marker=True):
                            input_error = None  # Setup ended because the run was stopped.
                    run_files = approved_files
                    if approved_files is not None and worktree is not None:
                        run_files = run_worktrees.approved_in_worktree(approved_files, worktree)
                    if input_error is None and not stop.wanted(check_marker=True):
                        executed = True
                        with (
                            triggered_run(origin == "trigger"),
                            run_worktree(worktree),
                            guarding(run_files),
                            input_files_visible(run_files_dir(path)),
                            # A cached step's key names files by contents and a worktree
                            # by its project, not by this run's folders.
                            stable_run_paths(RunPaths.for_run(staged.contents, worktree)),
                        ):
                            execution = await execute_workflow(
                                run_ir,
                                workflow_inputs=supplied_inputs,
                                trigger_events=trigger_events,
                                handlers=runtime_handlers,
                                subscriptions=runtime_subscriptions,
                                data_dir=resolved_data_dir,
                                notification_adapter=notification_adapter,
                                run_id=run_id,
                                on_node_event=on_node_event,
                                replay=(
                                    list(run_from.replay)
                                    if run_from is not None
                                    else list((resume_from or {}).get("runs") or [])
                                ),
                                replay_source=(
                                    run_from.source_run_id
                                    if run_from is not None
                                    else resumed["run_id"]
                                    if resumed
                                    else None
                                ),
                                usage_meter=usage,
                                result_cache=(
                                    ResultCache(resolved_data_dir) if use_result_cache else None
                                ),
                            )
                    group.cancel_scope.cancel()
                stopped = execution is None and stop.stop_seen
            except (InvalidRattishWorkflowInputError, FileInputError) as exc:
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

        if worktree is None and live_record is not None:
            # start_for_run raised after making the worktree; the live record holds it.
            worktree = live_record.document.get("worktree")
        if worktree is not None:
            worktree = await _finish_worktree(
                worktree, compiled.ir["workflow"]["name"], run_id, discard=not executed
            )
        finished_at = datetime.now(UTC).isoformat()
        status = "stopped" if stopped else _run_status(deployment, execution, input_error)
        if status == "preflight_failed":
            # Its inputs were never checked; keep them so it can run again once fixed.
            save_values()
        _cancel_pending_approvals(resolved_data_dir, compiled.ir["workflow"]["id"], run_id, status)
        document = _run_document(
            **record_fields,
            status=status,
            execution=execution,
            input_error=input_error,
            input_names=sorted(supplied_inputs),
            finished_at=finished_at,
            duration_ms=round((time.monotonic() - started_clock) * 1000),
            # Invalid inputs aren't kept: there is nothing to resume or run again.
            saved_values=None if status == "invalid_inputs" else values_summary,
        )
        if worktree is not None:
            document["worktree"] = worktree
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
            reusable = sum(
                1
                for record in resume_from.get("runs") or []
                if record.get("outcome") in {"success", "allowed_failure"}
            )
            document["resumed_from"].update(_reuse_counts(document, reusable))
        if run_from is not None and "replayed_from" in document:
            document["replayed_from"].update(_reuse_counts(document, reusable_count(run_from)))
        path = _run_path(
            resolved_data_dir,
            compiled.ir["workflow"]["id"],
            run_id,
            source_path=compiled.source_path,
        )
        document = _save_final_record(path, document, live_record)
        await _apply_retention_after_run(
            resolved_data_dir, compiled.ir["workflow"]["id"], path, run_id
        )
        # Saved inputs and files of runs deleted without delete_run_files go now.
        remove_orphaned_values(path.parent)
        remove_orphaned_files(path.parent)
        sweep_uploads(resolved_data_dir)
        return RattishRunResult(document, path, compiled, deployment)


def _reuse_counts(document: Mapping[str, Any], reusable: int) -> dict[str, int]:
    """How many of the ``reusable`` earlier results this run reused, and how many it didn't."""
    reused = sum(1 for record in document["runs"] if record.get("reused_from_run_id"))
    return {"reused_runs": reused, "not_reused_runs": max(0, reusable - reused)}


def _save_final_record(
    path: Path, document: dict[str, Any], live_record: _LiveRecord | None
) -> dict[str, Any]:
    """Save a finished run's record and delete its journal; the document as saved."""
    try:
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
    except BaseException:
        # The record still says running, so its journal is kept for whoever reads it.
        if live_record is not None:
            live_record.close()
        raise
    if live_record is not None:
        live_record.finish()
    return document


async def _finish_worktree(
    worktree: dict[str, Any], workflow_name: str, run_id: str, *, discard: bool
) -> dict[str, Any]:
    """Commit what the run changed in its worktree, or clean up a worktree nothing used.

    Shielded, so a stopped run still commits its changes before its record is saved.
    """
    from gofer.rattish import run_worktrees

    def work() -> dict[str, Any]:
        if discard:
            # Setup failed or the run stopped first: no node ran there.
            try:
                run_worktrees.remove(run_worktrees.RunWorktree.from_document(worktree))
            except run_worktrees.RunWorktreeError as exc:
                return {**worktree, "state": "pending", "message": str(exc)}
            return {**worktree, "state": "no_changes"}
        return run_worktrees.finish(worktree, f"Raticode run of {workflow_name}\n\nRun {run_id}.")

    result = worktree
    with anyio.CancelScope(shield=True):
        result = await anyio.to_thread.run_sync(work)
    return result


def write_run_document(path: Path, document: dict[str, Any]) -> None:
    """Validate and save a run document in place, as the run itself does."""
    _write_json_atomic(path, document)


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
    replayed: Mapping[str, Any] | None = None,
    model_variant: Mapping[str, Any] | None = None,
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
    if replayed is not None:
        document["replayed_from"] = dict(replayed)
    if model_variant is not None:
        document["model_overrides"] = [dict(item) for item in model_variant["models"]]
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
    """Whether a run of this source file is going now, started from anywhere."""
    return live_run(source_path, data_dir=data_dir, recent=recent) is not None


@dataclass(frozen=True, slots=True)
class LiveRun:
    """A run of a workflow that is going now: its ID and when it started, if saved."""

    run_id: str
    started_at: datetime | None


def live_run(source_path: Path, *, data_dir: Path | None = None, recent: int = 8) -> LiveRun | None:
    """The longest-running run of this source file going now, started from anywhere.

    Run IDs sort by start time, so only the newest few records need reading.
    """
    try:
        directory = run_artifact_path(source_path, "probe", data_dir=data_dir).parent
        records = sorted(directory.glob("*.json"), reverse=True)[:recent]
    except (OSError, RattishArtifactError, RattishError):
        return None
    found: LiveRun | None = None
    for record in records:
        try:
            document = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict) or effective_run_status(document) != "running":
            continue
        started: datetime | None = None
        raw = document.get("started_at")
        if isinstance(raw, str):
            try:
                started = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                started = None
            if started is not None and started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
        # Newest first, so each later match started earlier.
        found = LiveRun(str(document.get("run_id") or record.stem), started)
    return found


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


@dataclass(frozen=True, slots=True)
class _RunValidators:
    """run.schema.json's checks: the whole record and the parts a live save adds."""

    record: Any
    event: Any
    node_run: Any
    worktree: Any


@cache
def _run_validators() -> _RunValidators:
    """Built once per process: reading and compiling the schema costs more than a check."""
    schema = json.loads(
        (rattish_asset_root() / "schemas" / "run.schema.json").read_text(encoding="utf-8")
    )
    registry = Registry().with_resource(schema["$id"], DRAFT202012.create_resource(schema))

    def part(pointer: str) -> Any:
        return Draft202012Validator({"$ref": f"{schema['$id']}#{pointer}"}, registry=registry)

    return _RunValidators(
        record=Draft202012Validator(schema),
        event=part("/$defs/event"),
        node_run=part("/$defs/node_run"),
        worktree=part("/properties/worktree"),
    )


def _check_run_part(kind: Literal["record", "event", "node_run", "worktree"], value: Any) -> None:
    """Check a run record, or one part of it, against run.schema.json."""
    try:
        getattr(_run_validators(), kind).validate(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise RattishRunArtifactError(f"Invalid Rattish run artifact: {exc}") from exc


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    _check_run_part("record", document)
    _publish_json(path, document)


def _publish_json(path: Path, document: dict[str, Any]) -> None:
    try:
        with atomic_binary_output(path) as output:
            output.write(canonical_json_bytes(document) + b"\n")
    except (OSError, TypeError, ValueError) as exc:
        raise RattishRunArtifactError(f"Could not publish Rattish run artifact: {exc}") from exc
