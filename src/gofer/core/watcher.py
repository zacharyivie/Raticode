from __future__ import annotations

import asyncio
import json
import stat
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from gofer.core.executor import WorkflowExecutor
from gofer.core.resources import DEFAULT_RESOURCE_LIMITS, ResourceLimitError, ResourceLimits
from gofer.core.run_outputs import write_run_node_outputs_payload
from gofer.core.workflow import AgenticWorkflow, WatchConfig
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.utils.logging import get_logger
from gofer.utils.run_state import workflow_stop_path

log = get_logger(__name__)

_subscriptions = {
    "claude_code": ClaudeCodeSubscription(),
    "codex": CodexSubscription(),
}

Snapshot = dict[str, tuple[int, int]]
WatchEventKind = Literal["created", "modified", "deleted"]


@dataclass(frozen=True)
class WatchEvent:
    kind: WatchEventKind
    path: str
    name: str
    directory: str
    size: int | None = None
    mtime_ns: int | None = None

    def to_context(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "name": self.name,
            "directory": self.directory,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }


class _DirtyEvents(FileSystemEventHandler):
    def __init__(self, dirty: threading.Event) -> None:
        self.dirty = dirty

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in {"created", "deleted", "modified", "moved"}:
            self.dirty.set()


@dataclass
class WatchedWorkflow:
    workflow_id: str
    workflow_path: Path
    config: WatchConfig
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    snapshot: Snapshot = field(default_factory=dict)
    last_triggered_at: float = 0.0
    running_count: int = 0
    queued_events: list[list[WatchEvent]] = field(default_factory=list)
    dropped_event_batches: int = 0
    dirty: threading.Event = field(default_factory=threading.Event)
    observer: BaseObserver | None = None
    observer_identity: tuple[int, int] | None = None
    reconciled_at: float = 0.0
    observer_retry_at: float = 0.0


class WorkflowWatcher:
    def __init__(
        self,
        poll_interval_seconds: float = 1.0,
        resource_limits: ResourceLimits | None = None,
        reconciliation_seconds: float = 30.0,
    ) -> None:
        self._reconciliation_seconds = max(poll_interval_seconds, reconciliation_seconds)
        self._poll_interval_seconds = poll_interval_seconds
        self._resource_limits = resource_limits or DEFAULT_RESOURCE_LIMITS
        self._workflows: dict[str, WatchedWorkflow] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._global_running_count = 0

    def add_workflow(self, workflow: AgenticWorkflow, workflow_path: Path) -> None:
        if workflow.config.watch is None:
            raise ValueError(f"Workflow '{workflow.config.id}' has no watcher configured")

        with self._lock:
            existing = self._workflows.get(workflow.config.id)
            if (
                existing is not None
                and existing.workflow_path == workflow_path
                and existing.config == workflow.config.watch
                and existing.resource_limits == workflow.config.resource_limits
            ):
                return

        watched = WatchedWorkflow(
            workflow_id=workflow.config.id,
            workflow_path=workflow_path,
            config=workflow.config.watch,
            resource_limits=workflow.config.resource_limits,
            snapshot=self._snapshot(
                workflow_path,
                workflow.config.watch,
                workflow.config.resource_limits,
            ),
        )
        with self._lock:
            if existing is not None:
                self._stop_observer(existing)
            self._workflows[workflow.config.id] = watched
        log.info("Watching workflow '%s' path='%s'", workflow.config.id, workflow.config.watch.path)

    def remove_workflow(self, workflow_id: str) -> None:
        with self._lock:
            watched = self._workflows.pop(workflow_id, None)
            if watched is not None:
                self._stop_observer(watched)

    def list_workflows(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {
                    "id": watched.workflow_id,
                    "path": str(watched.config.path),
                    "workflow_path": str(watched.workflow_path),
                }
                for watched in self._workflows.values()
            ]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def shutdown(self, wait: bool = True) -> None:
        self._stop_event.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=2)

        with self._lock:
            for watched in self._workflows.values():
                self._stop_observer(watched)

    @staticmethod
    def _stop_observer(watched: WatchedWorkflow) -> None:
        if watched.observer is not None:
            watched.observer.stop()
            watched.observer.join(timeout=2)
            watched.observer = None
            watched.observer_identity = None

    def _ensure_observer(self, watched: WatchedWorkflow) -> None:
        if watched.observer is None and time.monotonic() < watched.observer_retry_at:
            return
        target = watched.config.path
        if not target.is_absolute():
            target = watched.workflow_path.parent / target
        try:
            info = target.stat()
            identity = (info.st_dev, info.st_ino)
            if watched.observer is not None:
                if watched.observer.is_alive() and watched.observer_identity == identity:
                    return
                self._stop_observer(watched)
            # UNC shares retain polling. Native observers on local Linux/macOS stay native.
            if str(target).startswith(("\\\\", "//")):
                return
            watched.observer_retry_at = time.monotonic() + self._reconciliation_seconds
            observer = Observer()
            handler = _DirtyEvents(watched.dirty)
            observer.schedule(handler, str(target.parent), recursive=False)
            if stat.S_ISDIR(info.st_mode):
                observer.schedule(
                    handler,
                    str(target),
                    recursive=(
                        watched.config.recursive
                        or "**" in watched.config.glob
                        or "/" in watched.config.glob
                        or "\\" in watched.config.glob
                    ),
                )
            try:
                observer.start()
            except (OSError, RuntimeError):
                observer.stop()
                if observer.is_alive():
                    observer.join(timeout=2)
                raise
            watched.observer = observer
            watched.observer_identity = identity
            watched.dirty.set()
        except (OSError, RuntimeError):
            self._stop_observer(watched)
            watched.observer_retry_at = time.monotonic() + self._reconciliation_seconds
            # Missing directories, unsupported filesystems and exhausted watches poll.

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._poll_interval_seconds):
            self.poll_once(force=False)

    def poll_once(self, *, force: bool = True) -> None:
        with self._lock:
            watched_items = list(self._workflows.values())

        for watched in watched_items:
            if not force:
                with self._lock:
                    if (
                        self._stop_event.is_set()
                        or self._workflows.get(watched.workflow_id) is not watched
                    ):
                        continue
                    self._ensure_observer(watched)
                reconcile = time.monotonic() - watched.reconciled_at >= self._reconciliation_seconds
                if watched.observer is not None and not watched.dirty.is_set() and not reconcile:
                    self._start_queued_runs(watched)
                    continue
            # Clear before scanning so notifications received during the scan survive.
            watched.dirty.clear()
            try:
                next_snapshot = self._snapshot(
                    watched.workflow_path,
                    watched.config,
                    watched.resource_limits,
                )
            except Exception as exc:  # noqa: BLE001
                watched.dirty.set()
                log.warning("Could not scan watcher for '%s': %s", watched.workflow_id, exc)
                continue

            watched.reconciled_at = time.monotonic()
            if next_snapshot == watched.snapshot:
                self._start_queued_runs(watched)
                continue

            def record_overflow(dropped: int, watched: WatchedWorkflow = watched) -> None:
                self._record_diff_overflow(watched, dropped)

            events = self._diff_events(
                watched.snapshot,
                next_snapshot,
                watched.resource_limits,
                on_drop=record_overflow,
            )
            now = time.monotonic()
            watched.snapshot = next_snapshot
            if now - watched.last_triggered_at < watched.config.debounce_seconds:
                self._enqueue_events(watched, events)
                continue
            watched.last_triggered_at = now
            self._trigger(watched, events)

    def _trigger(self, watched: WatchedWorkflow, events: list[WatchEvent]) -> None:
        self._enqueue_events(watched, events)
        self._start_queued_runs(watched)

    def _enqueue_events(self, watched: WatchedWorkflow, events: list[WatchEvent]) -> None:
        if not events:
            return
        with self._lock:
            queue_depth = max(0, watched.resource_limits.max_watcher_queue_depth)
            if queue_depth == 0:
                watched.dropped_event_batches += len(events)
                log.warning(
                    "Watcher queue for '%s' has limit 0; dropped %s event(s)",
                    watched.workflow_id,
                    len(events),
                )
                return
            if len(events) > queue_depth:
                dropped = len(events) - queue_depth
                events = events[-queue_depth:]
                watched.dropped_event_batches += dropped
                log.warning(
                    "Watcher event batch for '%s' exceeded limit %s; dropped %s oldest event(s)",
                    watched.workflow_id,
                    queue_depth,
                    dropped,
                )
            if watched.config.mode == "queue":
                watched.queued_events.extend([[event] for event in events])
            else:
                watched.queued_events.append(events)
            overflow = len(watched.queued_events) - queue_depth
            if overflow > 0:
                del watched.queued_events[:overflow]
                watched.dropped_event_batches += overflow
                log.warning(
                    "Watcher queue for '%s' exceeded limit %s; dropped %s oldest event batch(es)",
                    watched.workflow_id,
                    queue_depth,
                    overflow,
                )

    def _record_diff_overflow(self, watched: WatchedWorkflow, dropped: int) -> None:
        if dropped <= 0:
            return
        with self._lock:
            watched.dropped_event_batches += dropped
        log.warning(
            "Watcher diff for '%s' exceeded limit %s; dropped %s oldest event(s)",
            watched.workflow_id,
            watched.resource_limits.max_watcher_queue_depth,
            dropped,
        )

    def _start_queued_runs(self, watched: WatchedWorkflow) -> None:
        threads: list[threading.Thread] = []
        with self._lock:
            max_concurrency = max(
                1,
                min(
                    watched.config.max_concurrency,
                    watched.resource_limits.max_watcher_concurrency,
                ),
            )
            global_concurrency = self._global_concurrency_limit()
            while (
                watched.queued_events
                and watched.running_count < max_concurrency
                and self._global_running_count < global_concurrency
            ):
                events = watched.queued_events.pop(0)
                watched.running_count += 1
                self._global_running_count += 1
                thread = threading.Thread(
                    target=self._run_workflow,
                    args=(watched, events),
                    daemon=True,
                )
                threads.append(thread)

        for thread in threads:
            thread.start()

    def _global_concurrency_limit(self) -> int:
        return self._resource_limits.max_watcher_concurrency

    def _run_workflow(self, watched: WatchedWorkflow, events: list[WatchEvent]) -> None:
        try:
            workflow = AgenticWorkflow.from_file(watched.workflow_path)
            workflow.validate(watched.workflow_path)
            for warning in workflow.resource_warnings(watched.workflow_path.parent):
                log.warning("%s", warning)
            trigger_context: dict[str, Any] = {
                "type": "file_watch",
                "mode": watched.config.mode,
                "watch_path": str(watched.config.path),
                "glob": watched.config.glob,
                "events": [event.to_context() for event in events],
                "events_json": json.dumps([event.to_context() for event in events]),
            }
            if len(events) == 1:
                event_context = events[0].to_context()
                trigger_context["event"] = event_context
                trigger_context["event_json"] = json.dumps(event_context)

            async def execute() -> None:
                result = (
                    await WorkflowExecutor(
                        workflow,
                        _subscriptions,
                        log_base_dir=watched.workflow_path.parent / "logs",
                        workflow_path=watched.workflow_path,
                        stop_file=workflow_stop_path(
                            watched.workflow_id,
                            watched.workflow_path.parent,
                        ),
                    )
                    .with_trigger_context(trigger_context)
                    .with_parameters(
                        workflow.config.watch.invocation_inputs if workflow.config.watch else {}
                    )
                    .run()
                )
                log.info(
                    "Watched workflow %s finished: success=%s",
                    watched.workflow_id,
                    result.success,
                )
                write_run_node_outputs_payload(result, workflow.config.resource_limits)

            asyncio.run(execute())
        except Exception as exc:  # noqa: BLE001
            log.exception("Watched workflow '%s' failed: %s", watched.workflow_id, exc)
        finally:
            with self._lock:
                current = self._workflows.get(watched.workflow_id)
                if current is watched:
                    current.running_count = max(0, current.running_count - 1)
                    self._global_running_count = max(0, self._global_running_count - 1)
                    should_start_more = bool(current.queued_events)
                else:
                    self._global_running_count = max(0, self._global_running_count - 1)
                    should_start_more = False
            if should_start_more:
                with self._lock:
                    watched_items = list(self._workflows.values())
                for queued in watched_items:
                    self._start_queued_runs(queued)

    def _snapshot(
        self,
        workflow_path: Path,
        config: WatchConfig,
        limits: ResourceLimits,
    ) -> Snapshot:
        snapshot: Snapshot = {}
        scanned = 0
        for path in self._watched_paths(workflow_path, config):
            scanned += 1
            if scanned > limits.max_files_scanned:
                raise ResourceLimitError(
                    "watcher scan exceeded limit "
                    f"{limits.max_files_scanned} files for path '{config.path}'"
                )
            try:
                info = path.stat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            if stat.S_ISREG(info.st_mode):
                snapshot[str(path)] = (info.st_mtime_ns, info.st_size)
        return snapshot

    def _watched_paths(self, workflow_path: Path, config: WatchConfig) -> Iterator[Path]:
        target = config.path if config.path.is_absolute() else workflow_path.parent / config.path
        if target.is_dir():
            iterator = target.rglob(config.glob) if config.recursive else target.glob(config.glob)
            yield from iterator
            return
        yield target

    def _diff_events(
        self,
        previous: Snapshot,
        current: Snapshot,
        limits: ResourceLimits,
        on_drop: Callable[[int], None] | None = None,
    ) -> list[WatchEvent]:
        events: list[WatchEvent] = []
        event_limit = max(0, limits.max_watcher_queue_depth)
        if event_limit == 0:
            if on_drop is not None:
                dropped = (
                    len(current.keys() - previous.keys())
                    + len(previous.keys() - current.keys())
                    + sum(
                        1
                        for path_str in current.keys() & previous.keys()
                        if current[path_str] != previous[path_str]
                    )
                )
                if dropped:
                    on_drop(dropped)
            return events
        dropped = 0

        def append_event(event: WatchEvent) -> None:
            nonlocal dropped
            events.append(event)
            overflow = len(events) - event_limit
            if overflow > 0:
                del events[:overflow]
                dropped += overflow

        for path_str in sorted(current.keys() - previous.keys()):
            mtime_ns, size = current[path_str]
            append_event(self._event("created", path_str, size=size, mtime_ns=mtime_ns))
        for path_str in sorted(previous.keys() - current.keys()):
            append_event(self._event("deleted", path_str))
        for path_str in sorted(current.keys() & previous.keys()):
            if current[path_str] != previous[path_str]:
                mtime_ns, size = current[path_str]
                append_event(self._event("modified", path_str, size=size, mtime_ns=mtime_ns))
        if dropped and on_drop is not None:
            on_drop(dropped)
        return events

    def _event(
        self,
        kind: WatchEventKind,
        path_str: str,
        *,
        size: int | None = None,
        mtime_ns: int | None = None,
    ) -> WatchEvent:
        path = Path(path_str)
        return WatchEvent(
            kind=kind,
            path=path_str,
            name=path.name,
            directory=str(path.parent),
            size=size,
            mtime_ns=mtime_ns,
        )
