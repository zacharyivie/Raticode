from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from gofer.core.executor import WorkflowExecutor
from gofer.core.operations import AgentOperation, CommonLlmTaskOperation
from gofer.core.provider_capabilities import CLI_PROVIDERS, resolve_provider_executable
from gofer.core.workflow import AgenticWorkflow
from gofer.subscriptions.acp_providers import AcpSubscription
from gofer.subscriptions.antigravity import AntigravitySubscription
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.cli_providers import CliSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription
from gofer.utils.paths import get_data_dir
from gofer.utils.run_state import workflow_run_stop_path

RunStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "canceled",
    "cancel_requested",
    "lost_runner",
]

RUNNER_STALE_AFTER_SECONDS = 60

_SUBSCRIPTIONS = {
    "claude_code": ClaudeCodeSubscription(),
    "codex": CodexSubscription(),
    **{provider: CliSubscription(provider) for provider in ("cursor", "copilot", "opencode")},
    "antigravity": AntigravitySubscription(),
    "grok": AcpSubscription("grok"),
    "openai_api": OpenAiApiSubscription(),
    "anthropic_api": AnthropicApiSubscription(),
}


@dataclass(frozen=True)
class QueuedRun:
    id: str
    workflow_id: str
    workflow_path: str
    status: RunStatus
    priority: int
    trigger: str
    parameters: dict[str, Any]
    target_labels: list[str]
    required_capabilities: dict[str, Any]
    runner_id: str | None
    run_log_path: str | None
    message: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflowId": self.workflow_id,
            "workflowPath": self.workflow_path,
            "status": self.status,
            "priority": self.priority,
            "trigger": self.trigger,
            "parameters": self.parameters,
            "targetLabels": self.target_labels,
            "requiredCapabilities": self.required_capabilities,
            "runnerId": self.runner_id,
            "runLogPath": self.run_log_path,
            "message": self.message,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
        }


@dataclass(frozen=True)
class RunnerRecord:
    id: str
    name: str
    labels: list[str]
    capabilities: dict[str, Any]
    status: str
    last_seen_at: str
    current_run_id: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "labels": self.labels,
            "capabilities": self.capabilities,
            "status": self.status,
            "lastSeenAt": self.last_seen_at,
            "currentRunId": self.current_run_id,
        }


def default_runner_capabilities(workspace_roots: list[str] | None = None) -> dict[str, Any]:
    provider_clis = [
        provider for provider in CLI_PROVIDERS if resolve_provider_executable(provider)
    ]
    return {
        "os": platform.system().lower(),
        "hostname": socket.gethostname(),
        "provider_clis": provider_clis,
        "direct_providers": ["anthropic_api", "openai_api"],
        "workspace_roots": workspace_roots or [os.getcwd()],
    }


def workflow_required_capabilities(workflow: AgenticWorkflow) -> dict[str, Any]:
    provider_clis: set[str] = set()
    direct_providers: set[str] = set()
    for node in workflow.graph.nodes_in_order():
        op = node.operation
        if not isinstance(op, (AgentOperation, CommonLlmTaskOperation)):
            continue
        agent = workflow.agents.get(op.agent_id)
        if agent is not None:
            if agent.subscription in {"openai_api", "anthropic_api"}:
                direct_providers.add(agent.subscription)
            else:
                provider_clis.add(agent.subscription)
    return {
        "provider_clis": sorted(provider_clis),
        "direct_providers": sorted(direct_providers),
    }


def capabilities_match(
    runner_labels: list[str],
    runner_capabilities: Mapping[str, Any],
    target_labels: list[str],
    required_capabilities: Mapping[str, Any],
) -> tuple[bool, str | None]:
    missing_labels = sorted(set(target_labels) - set(runner_labels))
    if missing_labels:
        return False, f"Runner missing label(s): {', '.join(missing_labels)}"
    required_providers = set(required_capabilities.get("provider_clis") or [])
    available_providers = set(runner_capabilities.get("provider_clis") or [])
    missing_providers = sorted(required_providers - available_providers)
    if missing_providers:
        return False, f"Runner missing provider CLI(s): {', '.join(missing_providers)}"
    required_direct = set(required_capabilities.get("direct_providers") or [])
    available_direct = set(runner_capabilities.get("direct_providers") or [])
    missing_direct = sorted(required_direct - available_direct)
    if missing_direct:
        return False, f"Runner missing direct provider support: {', '.join(missing_direct)}"
    return True, None


class RunnerQueueStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or get_data_dir()
        self.db_path = self.data_dir / "runner-queue.db"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def register_runner(
        self,
        runner_id: str,
        name: str,
        labels: list[str],
        capabilities: dict[str, Any],
    ) -> RunnerRecord:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runners (
                    id, name, labels_json, capabilities_json, status,
                    last_seen_at, current_run_id
                )
                VALUES (?, ?, ?, ?, 'idle', ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    labels_json=excluded.labels_json,
                    capabilities_json=excluded.capabilities_json,
                    status=CASE
                        WHEN runners.current_run_id IS NULL THEN 'idle'
                        ELSE runners.status
                    END,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    runner_id,
                    name,
                    _dump_json(labels),
                    _dump_json(capabilities),
                    now,
                ),
            )
        return self.get_runner(runner_id)  # type: ignore[return-value]

    def heartbeat(
        self,
        runner_id: str,
        status: str | None = None,
        current_run_id: str | None = None,
    ) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runners
                SET last_seen_at = ?,
                    status = COALESCE(?, status),
                    current_run_id = ?
                WHERE id = ?
                """,
                (now, status, current_run_id, runner_id),
            )

    def get_runner(self, runner_id: str) -> RunnerRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runners WHERE id = ?", (runner_id,)).fetchone()
        return _runner_from_row(row) if row is not None else None

    def list_runners(self) -> list[RunnerRecord]:
        self.mark_lost_runs()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runners ORDER BY name, id").fetchall()
        return [_runner_from_row(row) for row in rows]

    def enqueue(
        self,
        workflow_id: str,
        workflow_path: Path,
        *,
        priority: int = 0,
        trigger: str = "manual",
        parameters: dict[str, Any] | None = None,
        target_labels: list[str] | None = None,
        required_capabilities: dict[str, Any] | None = None,
    ) -> QueuedRun:
        now = _now()
        run_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, workflow_id, workflow_path, status, priority, trigger,
                    parameters_json, target_labels_json, required_capabilities_json,
                    runner_id, run_log_path, message, created_at, updated_at,
                    started_at, finished_at
                )
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, NULL)
                """,
                (
                    run_id,
                    workflow_id,
                    str(workflow_path),
                    priority,
                    trigger,
                    _dump_json(parameters or {}),
                    _dump_json(target_labels or []),
                    _dump_json(required_capabilities or {}),
                    now,
                    now,
                ),
            )
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> QueuedRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run_from_row(row) if row is not None else None

    def list_runs(self, limit: int = 50) -> list[QueuedRun]:
        self.mark_lost_runs()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runs
                ORDER BY
                    CASE status
                        WHEN 'running' THEN 0
                        WHEN 'cancel_requested' THEN 1
                        WHEN 'queued' THEN 2
                        ELSE 3
                    END,
                    priority DESC,
                    created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def claim_next(self, runner_id: str) -> QueuedRun | None:
        runner = self.get_runner(runner_id)
        if runner is None:
            raise ValueError(f"Runner '{runner_id}' is not registered")
        now = _now()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE status = 'queued'
                ORDER BY priority DESC, created_at ASC
                """
            ).fetchall()
            for row in rows:
                queued = _run_from_row(row)
                matches, message = capabilities_match(
                    runner.labels,
                    runner.capabilities,
                    queued.target_labels,
                    queued.required_capabilities,
                )
                if not matches:
                    conn.execute(
                        "UPDATE runs SET message = ?, updated_at = ? WHERE id = ?",
                        (message, now, queued.id),
                    )
                    continue
                conn.execute(
                    """
                    UPDATE runs
                    SET status = 'running',
                        runner_id = ?,
                        message = NULL,
                        updated_at = ?,
                        started_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (runner_id, now, now, queued.id),
                )
                conn.execute(
                    """
                    UPDATE runners
                    SET status = 'running',
                        current_run_id = ?,
                        last_seen_at = ?
                    WHERE id = ?
                    """,
                    (queued.id, now, runner_id),
                )
                return self.get_run(queued.id)
        return None

    def cancel_run(self, run_id: str) -> QueuedRun:
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"Queued run '{run_id}' not found")
        now = _now()
        if run.status == "queued":
            status: RunStatus = "canceled"
            finished_at: str | None = now
            message = "Canceled before dispatch"
        elif run.status in {"running", "cancel_requested"}:
            status = "cancel_requested"
            finished_at = run.finished_at
            message = "Cancel requested"
        else:
            return run
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, message = ?, updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, message, now, finished_at, run_id),
            )
        return self.get_run(run_id)  # type: ignore[return-value]

    def run_cancel_requested(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        return run is not None and run.status == "cancel_requested"

    def finish_run(
        self,
        run_id: str,
        status: Literal["completed", "failed", "canceled"],
        *,
        message: str | None = None,
        run_log_path: Path | None = None,
    ) -> QueuedRun:
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"Queued run '{run_id}' not found")
        now = _now()
        final_status: RunStatus = "canceled" if self.run_cancel_requested(run_id) else status
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    message = ?,
                    run_log_path = COALESCE(?, run_log_path),
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    final_status,
                    "Canceled" if final_status == "canceled" and message is None else message,
                    str(run_log_path) if run_log_path is not None else None,
                    now,
                    now,
                    run_id,
                ),
            )
            if run.runner_id is not None:
                conn.execute(
                    """
                    UPDATE runners
                    SET status = 'idle',
                        current_run_id = NULL,
                        last_seen_at = ?
                    WHERE id = ?
                    """,
                    (now, run.runner_id),
                )
        return self.get_run(run_id)  # type: ignore[return-value]

    def mark_lost_runs(self) -> None:
        stale_before = (
            datetime.now(UTC) - timedelta(seconds=RUNNER_STALE_AFTER_SECONDS)
        ).isoformat()
        now = _now()
        with self._connect() as conn:
            stale_runner_rows = conn.execute(
                """
                SELECT id FROM runners
                WHERE current_run_id IS NOT NULL AND last_seen_at < ?
                """,
                (stale_before,),
            ).fetchall()
            stale_runner_ids = [str(row["id"]) for row in stale_runner_rows]
            for runner_id in stale_runner_ids:
                conn.execute(
                    """
                    UPDATE runs
                    SET status = 'lost_runner',
                        message = 'Runner heartbeat expired',
                        updated_at = ?,
                        finished_at = ?
                    WHERE runner_id = ? AND status IN ('running', 'cancel_requested')
                    """,
                    (now, now, runner_id),
                )
                conn.execute(
                    """
                    UPDATE runners
                    SET status = 'lost',
                        current_run_id = NULL
                    WHERE id = ?
                    """,
                    (runner_id,),
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runners (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    current_run_id TEXT
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workflow_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    trigger TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    target_labels_json TEXT NOT NULL,
                    required_capabilities_json TEXT NOT NULL,
                    runner_id TEXT,
                    run_log_path TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_runs_status_priority
                ON runs(status, priority, created_at);
                """
            )


async def execute_queued_run(
    store: RunnerQueueStore,
    queued_run: QueuedRun,
    *,
    data_dir: Path | None = None,
) -> QueuedRun:
    base = data_dir or store.data_dir
    cancel_event = threading.Event()
    stop_monitor = threading.Event()
    monitor = threading.Thread(
        target=_monitor_cancel_request,
        args=(store, queued_run.id, cancel_event, stop_monitor),
        daemon=True,
    )
    monitor.start()
    try:
        if cancel_event.is_set():
            return store.finish_run(queued_run.id, "canceled")
        workflow_path = Path(queued_run.workflow_path)
        workflow = AgenticWorkflow.from_file(workflow_path)
        workflow.validate(workflow_path, base)
        executor = WorkflowExecutor(
            workflow,
            _SUBSCRIPTIONS,
            log_base_dir=base / "logs",
            workflow_path=workflow_path,
            data_dir=base,
            cancel_event=cancel_event,
            stop_file=workflow_run_stop_path(workflow.config.id, queued_run.id, base),
        ).with_trigger_context(dict(queued_run.parameters.get("triggerContext") or {}))
        executor = executor.with_parameters(
            dict(
                queued_run.parameters.get(
                    "workflowInputs", queued_run.parameters.get("workflowParams")
                )
                or {}
            )
        )
        result = await executor.run()
        return store.finish_run(
            queued_run.id,
            "completed" if result.success else "failed",
            run_log_path=result.log_path,
        )
    except Exception as exc:  # noqa: BLE001
        return store.finish_run(queued_run.id, "failed", message=str(exc))
    finally:
        stop_monitor.set()
        monitor.join(timeout=1)


def run_worker_once(
    store: RunnerQueueStore,
    runner_id: str,
    *,
    data_dir: Path | None = None,
) -> QueuedRun | None:
    queued = store.claim_next(runner_id)
    if queued is None:
        store.heartbeat(runner_id, status="idle", current_run_id=None)
        return None
    return asyncio.run(execute_queued_run(store, queued, data_dir=data_dir))


def _monitor_cancel_request(
    store: RunnerQueueStore,
    run_id: str,
    cancel_event: threading.Event,
    stop_monitor: threading.Event,
) -> None:
    while not stop_monitor.wait(0.2):
        if store.run_cancel_requested(run_id):
            cancel_event.set()
            return


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load_json(value: str) -> Any:
    return json.loads(value)


def _run_from_row(row: sqlite3.Row) -> QueuedRun:
    return QueuedRun(
        id=str(row["id"]),
        workflow_id=str(row["workflow_id"]),
        workflow_path=str(row["workflow_path"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        priority=int(row["priority"]),
        trigger=str(row["trigger"]),
        parameters=dict(_load_json(str(row["parameters_json"]))),
        target_labels=list(_load_json(str(row["target_labels_json"]))),
        required_capabilities=dict(_load_json(str(row["required_capabilities_json"]))),
        runner_id=row["runner_id"],
        run_log_path=row["run_log_path"],
        message=row["message"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _runner_from_row(row: sqlite3.Row) -> RunnerRecord:
    return RunnerRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        labels=list(_load_json(str(row["labels_json"]))),
        capabilities=dict(_load_json(str(row["capabilities_json"]))),
        status=str(row["status"]),
        last_seen_at=str(row["last_seen_at"]),
        current_run_id=row["current_run_id"],
    )
