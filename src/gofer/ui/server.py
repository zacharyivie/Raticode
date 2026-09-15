from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from gofer.core.provider_capabilities import provider_capabilities_payload
from gofer.core.resources import ResourceLimits, bundle_resource_limits_from_env
from gofer.core.scheduler import WorkflowScheduler
from gofer.core.usage import summarize_node_outputs
from gofer.core.watcher import WorkflowWatcher
from gofer.core.workflow import AgenticWorkflow
from gofer.rattish.editor import RattishEditorError, RattishRevisionConflict
from gofer.rattish.workspaces import list_registered_workflows
from gofer.ui.api import (
    ProviderProfileError,
    RunnerQueueError,
    WorkflowAlreadyExistsError,
    WorkflowApprovalError,
    WorkflowBundleError,
    WorkflowCreateError,
    WorkflowHistoryError,
    WorkflowLogError,
    WorkflowPlanError,
    WorkflowRunError,
    WorkflowTriggerError,
    WorkflowUpdateError,
    analyze_rattish_document_payload,
    apply_workflow_validation_fix_payload,
    cancel_queued_run_payload,
    create_registered_workflow_payload,
    create_workflow_payload,
    decide_workflow_approval_payload,
    delete_provider_profile_payload,
    delete_workflow_chat_payload,
    delete_workflow_payload,
    duplicate_workflow_payload,
    export_rattish_bundle_payload,
    export_workflow_bundle_payload,
    health_payload,
    import_rattish_bundle_payload,
    import_workflow_bundle_payload,
    import_workflow_payload,
    latest_workflow_log_payload,
    list_workflow_approvals_payload,
    list_workflow_history_payload,
    list_workflow_payloads,
    list_workflow_run_logs_payload,
    list_workflow_templates_payload,
    mutate_rattish_document_payload,
    open_project_payload,
    open_rattish_document_payload,
    preview_rattish_bundle_payload,
    preview_workflow_bundle_payload,
    provider_profiles_payload,
    prune_workflow_run_logs_payload,
    queue_workflow_run_payload,
    rename_workflow_payload,
    replay_workflow_trigger_payload,
    restore_workflow_revision_payload,
    resume_workflow_payload,
    retention_settings_payload,
    run_workflow_payload,
    runner_queue_payload,
    save_rattish_document_payload,
    save_rattish_metadata_payload,
    stop_rattish_runs_for_shutdown,
    stop_workflow_run_payload,
    trigger_workflow_payload,
    update_retention_settings_payload,
    update_workflow_payload,
    upsert_provider_profile_payload,
    validate_workflow_draft_payload,
    validate_workflow_payload,
    workflow_plan_payload,
    workflow_revision_diff_payload,
    workflow_run_events_payload,
    workflow_run_log_payload,
    workflow_template_payload,
)
from gofer.ui.chat import (
    ChatChangeError,
    ChatProviderError,
    ensure_local_gofer_cli,
    provider_payload,
    redo_chat_changes,
    run_workflow_chat,
    stream_workflow_chat,
    undo_chat_changes,
)
from gofer.ui.chat_media import (
    CHAT_ATTACHMENT_MAX_TOTAL_BYTES,
    CHAT_AUDIO_CHUNK_MAX_BYTES,
    CHAT_AUDIO_MAX_BYTES,
    ChatMediaError,
    attachment_thread_dir,
    cancel_chat_transcription,
    finish_chat_transcription,
    start_chat_transcription,
    store_chat_attachments,
    stream_chat_transcription,
    transcribe_chat_audio,
)
from gofer.ui.swarms import SwarmManager
from gofer.utils.logging import get_logger
from gofer.utils.paths import get_data_dir

log = get_logger(__name__)
CONTINUOUS_RUN_POLL_SECONDS = 1.0
PATH_GRANT_TTL_SECONDS = 15 * 60
UI_MAX_HANDLERS = 32
UI_MAX_EXPENSIVE_REQUESTS = 8
UI_READ_TIMEOUT_SECONDS = 15.0
UI_REQUEST_READ_DEADLINE_SECONDS = 30.0
UI_JOB_DEADLINE_SECONDS = 24 * 60 * 60
COMMIT_MESSAGE_MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_DEV_FRONTEND_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


def _optional_query(query: dict[str, list[str]], name: str) -> str | None:
    value = query.get(name, [None])[0]
    return value if value not in {None, ""} else None


def _int_query(query: dict[str, list[str]], name: str, default: int) -> int:
    value = _optional_query(query, name)
    if value is None:
        return default
    return int(value)


def _optional_int_query(query: dict[str, list[str]], name: str) -> int | None:
    value = _optional_query(query, name)
    return int(value) if value is not None else None


def _optional_datetime_query(query: dict[str, list[str]], name: str) -> datetime | None:
    value = _optional_query(query, name)
    return datetime.fromisoformat(value) if value is not None else None


def _optional_body_int(body: dict[str, Any], name: str) -> int | None:
    value = body.get(name)
    if value in {None, ""}:
        return None
    return int(str(value))


def _optional_body_str(body: dict[str, Any], name: str) -> str | None:
    value = body.get(name)
    if value in {None, ""}:
        return None
    return str(value)


def sync_workflow_schedules(data_dir: Path, scheduler: WorkflowScheduler) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    active_schedule_ids: set[str] = set()

    for path in sorted(data_dir.glob("*.toml")):
        try:
            workflow = AgenticWorkflow.from_file(path)
        except Exception:
            continue
        if workflow.config.run_continuously or workflow.config.schedule is None:
            continue
        try:
            scheduler.add_workflow(workflow, path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Skipping invalid schedule in %s: %s", path, exc)
            continue
        active_schedule_ids.add(workflow.config.id)

    for job in scheduler.list_workflows():
        if job["id"] not in active_schedule_ids:
            scheduler.remove_workflow(job["id"])


def sync_workflow_watchers(data_dir: Path, watcher: WorkflowWatcher) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    active_watch_ids: set[str] = set()

    for path in sorted(data_dir.glob("*.toml")):
        try:
            workflow = AgenticWorkflow.from_file(path)
        except Exception:
            continue
        if workflow.config.run_continuously or workflow.config.watch is None:
            continue
        try:
            watcher.add_workflow(workflow, path)
        except Exception as exc:  # noqa: BLE001
            log.warning("Skipping invalid watcher in %s: %s", path, exc)
            continue
        active_watch_ids.add(workflow.config.id)

    for watched in watcher.list_workflows():
        if watched["id"] not in active_watch_ids:
            watcher.remove_workflow(watched["id"])


@dataclass(frozen=True)
class DesktopPathGrant:
    root: Path
    expires_at: float


class DesktopPathGrantStore:
    def __init__(self) -> None:
        self._grants: dict[str, DesktopPathGrant] = {}
        self._lock = threading.Lock()

    def register(self, root: Path, grant_id: str | None = None) -> str:
        grant_id = grant_id or str(uuid.uuid4())
        grant = DesktopPathGrant(
            root=_canonical_path_for_containment(root),
            expires_at=time.monotonic() + PATH_GRANT_TTL_SECONDS,
        )
        with self._lock:
            self._prune_locked()
            self._grants[grant_id] = grant
        return grant_id

    def covers(self, target: Path, grant_id: str | None) -> bool:
        if not grant_id:
            return False
        canonical_target = _canonical_path_for_containment(target)
        return self.covers_canonical(canonical_target, grant_id)

    def covers_canonical(self, canonical_target: Path, grant_id: str) -> bool:
        """Check the already resolved target that the caller will use for I/O."""
        with self._lock:
            self._prune_locked()
            grant = self._grants.get(grant_id)
        if grant is None:
            return False
        return _is_path_inside(canonical_target, grant.root)

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [grant_id for grant_id, grant in self._grants.items() if grant.expires_at <= now]
        for grant_id in expired:
            self._grants.pop(grant_id, None)


class GoferUiServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        data_dir: Path,
        resource_limits: ResourceLimits | None = None,
        api_token: str | None = None,
        allowed_origins: set[str] | None = None,
    ) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(server_address, GoferUiRequestHandler)
        self.data_dir = data_dir
        self.resource_limits = resource_limits or bundle_resource_limits_from_env()
        self.api_token = api_token or os.environ.get("GOFER_UI_API_TOKEN") or _new_ui_api_token()
        self.emit_ready_token = os.environ.get("GOFER_UI_EMIT_READY_TOKEN") == "1"
        self.allowed_origins = (
            _default_allowed_origins() if allowed_origins is None else set(allowed_origins)
        )
        self._handler_slots = threading.BoundedSemaphore(UI_MAX_HANDLERS)
        self._expensive_slots = threading.BoundedSemaphore(UI_MAX_EXPENSIVE_REQUESTS)
        self.request_job_timeout = max(
            1.0, float(os.environ.get("GOFER_UI_JOB_TIMEOUT_SECONDS", UI_JOB_DEADLINE_SECONDS))
        )
        self._schedule_lock = threading.Lock()
        self._schedule_signature: tuple[tuple[str, int, int], ...] | None = None
        self.path_grant_secret = os.environ.get("GOFER_DESKTOP_GRANT_SECRET", "")
        self.path_grants = DesktopPathGrantStore()
        # Snapshot startup trust. Re-reading a renderer-writable registry during
        # grant renewal would let a renderer launder an arbitrary new root.
        self.trusted_project_roots = tuple(
            sorted(
                {
                    str(workflow.project_root.resolve())
                    for workflow in list_registered_workflows(registry_dir=data_dir)
                }
            )
        )
        self.gofer_cli_path = ensure_local_gofer_cli(data_dir)
        self.scheduler = WorkflowScheduler(db_path=data_dir / "schedules.db")
        self.watcher = WorkflowWatcher(resource_limits=self.resource_limits)
        self._continuous_runs: dict[str, threading.Thread] = {}
        self._continuous_lock = threading.Lock()
        self._continuous_stop = threading.Event()
        self._continuous_thread: threading.Thread | None = None
        self.swarms = SwarmManager(
            data_dir,
            resource_limits=self.resource_limits,
            max_concurrency=int(os.environ.get("GOFER_SWARM_MAX_CONCURRENCY", "8")),
        )

    def server_close(self) -> None:
        swarms = getattr(self, "swarms", None)
        if swarms is not None:
            swarms.close()
        super().server_close()

    def process_request(self, request: Any, client_address: Any) -> None:
        # Reject before ThreadingMixIn allocates a thread. The accept loop never waits.
        if not self._handler_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(UI_READ_TIMEOUT_SECONDS)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._handler_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handler_slots.release()

    def sync_schedules(self) -> None:
        # Mutations reconcile immediately; the monitor also notices external edits.
        # Polling GETs never rewrite scheduler jobs or restart filesystem watchers.
        with self._schedule_lock:
            signature = []
            for path in sorted(self.data_dir.glob("*.toml")):
                try:
                    info = path.stat()
                except FileNotFoundError:
                    continue
                signature.append((str(path), info.st_mtime_ns, info.st_size))
            current = tuple(signature)
            if current == self._schedule_signature:
                return
            sync_workflow_schedules(self.data_dir, self.scheduler)
            sync_workflow_watchers(self.data_dir, self.watcher)
            self._schedule_signature = current

    def start_continuous_monitor(self) -> None:
        if self._continuous_thread and self._continuous_thread.is_alive():
            return
        self._continuous_stop.clear()
        self._continuous_thread = threading.Thread(
            target=self._continuous_monitor_loop,
            name="gofer-continuous-workflow-monitor",
            daemon=True,
        )
        self._continuous_thread.start()

    def stop_continuous_monitor(self) -> None:
        self._continuous_stop.set()
        if self._continuous_thread:
            self._continuous_thread.join(timeout=3)

    def _continuous_monitor_loop(self) -> None:
        while not self._continuous_stop.is_set():
            try:
                self.sync_schedules()
                self.ensure_continuous_runs()
            except Exception:  # noqa: BLE001
                log.exception("Continuous workflow monitor failed")
            self._continuous_stop.wait(CONTINUOUS_RUN_POLL_SECONDS)

    def ensure_continuous_runs(self) -> None:
        active_ids: set[str] = set()
        continuous_workflows: list[AgenticWorkflow] = []
        for path in sorted(self.data_dir.glob("*.toml")):
            try:
                workflow = AgenticWorkflow.from_file(path)
            except Exception:
                continue
            if not workflow.config.run_continuously:
                continue
            continuous_workflows.append(workflow)

        global_limit = self.resource_limits.max_watcher_concurrency

        for workflow in continuous_workflows:
            active_ids.add(workflow.config.id)
            with self._continuous_lock:
                active_count = sum(
                    1 for thread in self._continuous_runs.values() if thread.is_alive()
                )
            if active_count >= global_limit:
                log.warning(
                    "Skipping continuous workflow '%s'; global concurrency limit %s reached",
                    workflow.config.id,
                    global_limit,
                )
                continue
            self._start_continuous_run_if_needed(workflow.config.id)

        with self._continuous_lock:
            for workflow_id in list(self._continuous_runs):
                thread = self._continuous_runs[workflow_id]
                if workflow_id not in active_ids or not thread.is_alive():
                    self._continuous_runs.pop(workflow_id, None)

    def _start_continuous_run_if_needed(self, workflow_id: str) -> None:
        with self._continuous_lock:
            existing = self._continuous_runs.get(workflow_id)
            if existing and existing.is_alive():
                return

            thread = threading.Thread(
                target=self._run_continuous_workflow_once,
                args=(workflow_id,),
                name=f"gofer-continuous-{workflow_id}",
                daemon=True,
            )
            self._continuous_runs[workflow_id] = thread
            thread.start()

    def _run_continuous_workflow_once(self, workflow_id: str) -> None:
        try:
            asyncio.run(run_workflow_payload(workflow_id, self.data_dir, dry_run=False))
        except Exception as exc:  # noqa: BLE001
            log.warning("Continuous workflow '%s' run failed: %s", workflow_id, exc)
            time.sleep(CONTINUOUS_RUN_POLL_SECONDS)
        finally:
            with self._continuous_lock:
                current = self._continuous_runs.get(workflow_id)
                if current is threading.current_thread():
                    self._continuous_runs.pop(workflow_id, None)


class GoferUiRequestHandler(BaseHTTPRequestHandler):
    server_version = "GoferUi/0.1"

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, TimeoutError):
            # Browsers cancel stale requests during navigation. The response may
            # finish after the client has already closed its socket.
            return

    def handle_one_request(self) -> None:
        with self._read_deadline():
            super().handle_one_request()

    @contextmanager
    def _read_deadline(self) -> Iterator[None]:
        connection = getattr(self, "connection", None)
        timer = None
        if connection is not None:

            def expire() -> None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            timer = threading.Timer(UI_REQUEST_READ_DEADLINE_SECONDS, expire)
            timer.daemon = True
            timer.start()
            self._read_timer = timer
        try:
            yield
        finally:
            if timer is not None:
                timer.cancel()

    def do_GET(self) -> None:
        self._dispatch_request("GET")

    def do_POST(self) -> None:
        self._dispatch_request("POST")

    def do_PUT(self) -> None:
        self._dispatch_request("PUT")

    def do_DELETE(self) -> None:
        self._dispatch_request("DELETE")

    def do_OPTIONS(self) -> None:
        self._dispatch_request("OPTIONS")

    def _dispatch_request(self, method: str) -> None:
        # Header parsing has completed. Body reads get their own absolute deadline.
        timer = getattr(self, "_read_timer", None)
        if timer is not None:
            timer.cancel()
        try:
            parsed = urlparse(self.path)
        except ValueError:
            self._send_json({"error": "Invalid request path"}, status=400)
            return
        self._request_method = method
        if not self._allow_host():
            self._send_json({"error": "Host is not allowed"}, status=403)
            return
        if not self._allow_origin(parsed.path):
            self._send_json({"error": "Origin is not allowed"}, status=403)
            return
        if not self._authorize_ui_request(method, parsed.path):
            return
        headers = self.headers
        if headers.get("Transfer-Encoding") or len(headers.get_all("Content-Length", [])) > 1:
            self.close_connection = True
            self._send_json({"error": "Ambiguous request body framing"}, status=400)
            return
        slots = getattr(self.server, "_expensive_slots", None)
        expensive = method == "POST" and (
            parsed.path.startswith("/api/chat")
            or parsed.path.endswith(("/run", "/resume", "/trigger", "/replay"))
        )
        acquired = False
        if expensive and slots is not None:
            acquired = slots.acquire(blocking=False)
            if not acquired:
                self._send_json({"error": "Too many active requests; retry shortly"}, status=503)
                return
        try:
            if parsed.path == "/api/swarms" or parsed.path.startswith("/api/swarms/"):
                self._dispatch_swarm_request(method)
                return
            getattr(self, f"_dispatch_{method}")()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)
        except TimeoutError:
            self._send_json({"error": "Request exceeded its execution deadline"}, status=408)
        finally:
            if acquired and slots is not None:
                slots.release()

    def _dispatch_swarm_request(self, method: str) -> None:
        if method == "OPTIONS":
            self._dispatch_OPTIONS()
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        body = self._read_json() if method in {"POST", "PUT"} else {}
        project_value = body.get("projectRoot") or _optional_query(query, "projectRoot")
        if not isinstance(project_value, str) or not project_value.strip():
            raise ValueError("Choose a project for this swarm.")
        project = Path(project_value).expanduser()
        if not project.is_absolute():
            raise ValueError("The swarm project must be an absolute folder path.")
        parts = parsed.path.removeprefix("/api/swarms").strip("/").split("/")
        swarm_id = parts[0]
        action = parts[1] if len(parts) == 2 else ""
        if len(parts) > 2:
            self._send_json({"error": "Not found"}, status=404)
            return
        starts_work = method == "POST" and (
            not swarm_id
            or action == "start"
            or (action == "execution" and body.get("action") in {"verify", "integrate"})
            or (action == "control" and body.get("action") == "resume")
        )
        if starts_work:
            try:
                project = self._assert_bundle_path_allowed(
                    project, body.get("grantId"), must_exist=True
                )
            except WorkflowBundleError as exc:
                self._send_json({"error": str(exc)}, status=403)
                return
            if not project.is_dir():
                raise ValueError("Choose an existing project folder.")
        server = self.server
        if not isinstance(server, GoferUiServer):
            raise ValueError("Swarms are unavailable on this server.")
        manager = server.swarms
        project_root = str(project.resolve())
        if method == "GET" and not swarm_id:
            self._send_json({"swarms": manager.list(project_root)})
            return
        if method == "GET" and action == "history":
            self._send_json(
                {"run": manager.history_run(project_root, swarm_id, query["runId"][0])}
                if query.get("runId", [""])[0]
                else {
                    "history": manager.history(
                        project_root, swarm_id, int(_optional_query(query, "offset") or "0")
                    )
                }
            )
            return
        if method == "GET" and not action:
            result = manager.get(
                project_root,
                swarm_id,
                include_history=query.get("history", ["1"])[0] not in {"0", "false"},
                history_summaries=query.get("history", [""])[0] == "summary",
                since=query.get("since", [""])[0],
            )
        elif method == "POST" and not swarm_id:
            result = manager.create(project_root, body)
        elif method == "PUT" and swarm_id and not action:
            result = manager.update(project_root, swarm_id, body)
        elif method == "POST" and action == "start":
            result = manager.start(project_root, swarm_id, body.get("task", ""))
        elif method == "POST" and action == "control":
            result = manager.control(project_root, swarm_id, body.get("action", ""))
        elif method == "POST" and action == "messages":
            result = manager.message(project_root, swarm_id, body)
        elif method == "POST" and action == "deliveries":
            result = manager.resolve_delivery(project_root, swarm_id, body)
        elif method == "POST" and action == "execution":
            manager.execution(project_root, swarm_id, body)
            result = manager.get(project_root, swarm_id, include_history=False)
        elif method == "POST" and action == "objectives":
            result = manager.objectives(project_root, swarm_id, body)
        else:
            self._send_json({"error": "Not found"}, status=404)
            return
        self._send_json({"swarm": result or None})

    def _dispatch_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/desktop/trusted-roots":
            secret = getattr(self.server, "path_grant_secret", "")
            supplied = self.headers.get("X-Gofer-Desktop-Grant-Secret", "")
            if not secret or not hmac.compare_digest(secret, supplied):
                self._send_json({"error": "Desktop authentication required"}, status=403)
                return
            self._send_json({"roots": list(getattr(self.server, "trusted_project_roots", ()))})
            return

        if parsed.path == "/api/session":
            self._send_json(ui_session_payload(self.server))
            return

        if parsed.path == "/api/doctor":
            query = parse_qs(parsed.query)
            self._send_json(health_payload(self._request_data_dir(query)))
            return

        if parsed.path.startswith("/workflows/") and parsed.path.endswith("/usage"):
            workflow_id = parsed.path.removeprefix("/workflows/").removesuffix("/usage")
            query = parse_qs(parsed.query)
            try:
                payload = workflow_usage_page_payload(
                    workflow_id,
                    self._request_data_dir(query),
                )
            except WorkflowLogError as exc:
                self._send_html(f"<p>{html.escape(str(exc))}</p>", status=404)
                return

            self._send_html(render_workflow_usage_html(payload))
            return

        if parsed.path.startswith("/workflows/") and "/logs/" in parsed.path:
            remainder = parsed.path.removeprefix("/workflows/")
            workflow_id, run_part = remainder.split("/logs/", 1)
            if not run_part.endswith("/usage"):
                self._send_json({"error": "Not found"}, status=404)
                return
            run_id = run_part.removesuffix("/usage")
            query = parse_qs(parsed.query)
            try:
                payload = workflow_run_log_payload(
                    workflow_id,
                    run_id,
                    self._request_data_dir(query),
                    offset=_optional_int_query(query, "offset"),
                    limit=_optional_int_query(query, "limit"),
                    tail_bytes=_optional_int_query(query, "tailBytes"),
                    include_details=query.get("details", ["1"])[0] != "0",
                )
            except WorkflowLogError as exc:
                self._send_html(f"<p>{html.escape(str(exc))}</p>", status=404)
                return

            self._send_html(render_usage_summary_html(payload.get("usageSummary") or {}))
            return

        if parsed.path == "/api/workflows":
            query = parse_qs(parsed.query)
            payload = list_workflow_payloads(self._request_data_dir(query))
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/document"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/document")
            query = parse_qs(parsed.query)
            try:
                payload = open_rattish_document_payload(
                    workflow_id,
                    self._request_data_dir(query),
                )
            except RattishEditorError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            self._send_json({"document": payload})
            return

        if parsed.path == "/api/workflow-templates":
            self._send_json(list_workflow_templates_payload())
            return

        if parsed.path.startswith("/api/workflow-templates/"):
            template_name = parsed.path.removeprefix("/api/workflow-templates/")
            try:
                self._send_json(workflow_template_payload(template_name))
            except WorkflowCreateError as exc:
                self._send_json({"error": str(exc)}, status=404)
            return

        if parsed.path == "/api/chat/providers":
            self._send_json(provider_payload())
            return

        if parsed.path == "/api/provider/capabilities":
            query = parse_qs(parsed.query)
            refresh = query.get("refresh", ["0"])[0] in {"1", "true"}
            self._send_json(provider_capabilities_payload(refresh=refresh))
            return

        if parsed.path == "/api/provider/profiles":
            query = parse_qs(parsed.query)
            try:
                self._send_json(provider_profiles_payload(self._request_data_dir(query)))
            except ProviderProfileError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path in {"/api/runners", "/api/queue"}:
            query = parse_qs(parsed.query)
            try:
                self._send_json(runner_queue_payload(self._request_data_dir(query)))
            except RunnerQueueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/retention":
            query = parse_qs(parsed.query)
            try:
                self._send_json(retention_settings_payload(self._request_data_dir(query)))
            except WorkflowLogError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/retention"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/retention")
            query = parse_qs(parsed.query)
            try:
                self._send_json(
                    retention_settings_payload(self._request_data_dir(query), workflow_id)
                )
            except WorkflowLogError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/history"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/history")
            query = parse_qs(parsed.query)
            try:
                self._send_json(
                    list_workflow_history_payload(
                        workflow_id,
                        self._request_data_dir(query),
                        limit=_optional_int_query(query, "limit"),
                    )
                )
            except WorkflowHistoryError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if (
            parsed.path.startswith("/api/workflows/")
            and "/history/" in parsed.path
            and parsed.path.endswith("/diff")
        ):
            remainder = parsed.path.removeprefix("/api/workflows/").removesuffix("/diff")
            workflow_id, revision_id = remainder.split("/history/", 1)
            query = parse_qs(parsed.query)
            try:
                self._send_json(
                    workflow_revision_diff_payload(
                        workflow_id,
                        revision_id,
                        self._request_data_dir(query),
                    )
                )
            except WorkflowHistoryError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/doctor"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/doctor")
            query = parse_qs(parsed.query)
            self._send_json(health_payload(self._request_data_dir(query), workflow_id))
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/validate"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/validate")
            query = parse_qs(parsed.query)
            try:
                self._send_json(
                    validate_workflow_payload(workflow_id, self._request_data_dir(query))
                )
            except WorkflowUpdateError as exc:
                self._send_json({"error": str(exc)}, status=404)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/logs/latest"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/logs/latest")
            query = parse_qs(parsed.query)
            try:
                payload = latest_workflow_log_payload(
                    workflow_id,
                    self._request_data_dir(query),
                )
            except (WorkflowLogError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._send_json({"log": payload})
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/events"):
            remainder = parsed.path.removeprefix("/api/workflows/").removesuffix("/events")
            if "/logs/" not in remainder:
                self._send_json({"error": "Invalid events path"}, status=404)
                return
            workflow_id, run_id = remainder.split("/logs/", 1)
            query = parse_qs(parsed.query)
            try:
                payload = workflow_run_events_payload(
                    workflow_id,
                    run_id,
                    self._request_data_dir(query),
                )
            except WorkflowLogError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return

            self._send_json({"events": payload})
            return

        if parsed.path.startswith("/api/workflows/") and "/logs/" in parsed.path:
            remainder = parsed.path.removeprefix("/api/workflows/")
            workflow_id, run_id = remainder.split("/logs/", 1)
            query = parse_qs(parsed.query)
            try:
                payload = workflow_run_log_payload(
                    workflow_id,
                    run_id,
                    self._request_data_dir(query),
                    offset=_optional_int_query(query, "offset"),
                    limit=_optional_int_query(query, "limit"),
                    tail_bytes=_optional_int_query(query, "tailBytes"),
                    include_details=query.get("details", ["1"])[0] != "0",
                )
            except WorkflowLogError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return

            self._send_json({"log": payload})
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/logs"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/logs")
            query = parse_qs(parsed.query)
            try:
                payload = list_workflow_run_logs_payload(
                    workflow_id,
                    self._request_data_dir(query),
                    offset=_int_query(query, "offset", 0),
                    limit=_optional_int_query(query, "limit"),
                    status=_optional_query(query, "status"),
                    trigger_type=_optional_query(query, "trigger"),
                    search=_optional_query(query, "q"),
                    started_after=_optional_datetime_query(query, "startedAfter"),
                    started_before=_optional_datetime_query(query, "startedBefore"),
                )
            except (WorkflowLogError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/approvals"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/approvals")
            query = parse_qs(parsed.query)
            try:
                payload = list_workflow_approvals_payload(
                    workflow_id,
                    self._request_data_dir(query),
                    include_decided=query.get("all", ["1"])[0] != "0",
                )
            except WorkflowApprovalError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._send_json(payload)
            return

        self._send_json({"error": "Not found"}, status=404)

    def _dispatch_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/projects/open":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                project_root_value = str(body.get("projectRoot", "")).strip()
                if not project_root_value:
                    raise WorkflowCreateError("Project folder is required")
                project_root = Path(project_root_value).expanduser()
                try:
                    self._assert_bundle_path_allowed(
                        project_root,
                        body.get("projectGrantId"),
                        must_exist=True,
                    )
                except WorkflowBundleError as exc:
                    raise WorkflowCreateError(str(exc)) from exc
                payload = open_project_payload(
                    project_root,
                    registry_dir=self._request_data_dir(query),
                )
            except (WorkflowCreateError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._sync_schedules()
            self._send_json(payload)
            return

        if parsed.path == "/api/retention":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                payload = update_retention_settings_payload(
                    self._request_data_dir(query),
                    settings=body,
                )
            except (WorkflowLogError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/document/analyze"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix(
                "/document/analyze"
            )
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                source = body.get("source")
                if not isinstance(source, str):
                    raise ValueError("Rattish document analysis requires string source.")
                payload = analyze_rattish_document_payload(
                    workflow_id,
                    source,
                    self._request_data_dir(query),
                )
            except (RattishEditorError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"document": payload})
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/document/save"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/document/save")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                source = body.get("source")
                expected_revision = body.get("expectedRevision")
                if not isinstance(source, str) or not isinstance(expected_revision, str):
                    raise ValueError(
                        "Rattish document save requires source and expectedRevision strings."
                    )
                payload = save_rattish_document_payload(
                    workflow_id,
                    source,
                    expected_revision,
                    self._request_data_dir(query),
                )
            except RattishRevisionConflict as exc:
                self._send_json(exc.to_payload(), status=409)
                return
            except (RattishEditorError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"document": payload})
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/document/mutate"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix(
                "/document/mutate"
            )
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                mutations = body.get("mutations")
                expected_revision = body.get("expectedRevision")
                if (
                    not isinstance(mutations, list)
                    or not all(isinstance(item, dict) for item in mutations)
                    or not isinstance(expected_revision, str)
                ):
                    raise ValueError(
                        "Rattish document mutation requires mutations and expectedRevision."
                    )
                payload = mutate_rattish_document_payload(
                    workflow_id,
                    mutations,
                    expected_revision,
                    self._request_data_dir(query),
                )
            except RattishRevisionConflict as exc:
                self._send_json(exc.to_payload(), status=409)
                return
            except (RattishEditorError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"document": payload})
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/metadata"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/metadata")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                metadata = body.get("metadata")
                expected_revision = body.get("expectedRevision")
                if not isinstance(metadata, dict) or not isinstance(expected_revision, str):
                    raise ValueError(
                        "Rattish metadata save requires metadata and expectedRevision."
                    )
                payload = save_rattish_metadata_payload(
                    workflow_id,
                    metadata,
                    expected_revision,
                    self._request_data_dir(query),
                )
            except RattishRevisionConflict as exc:
                self._send_json(exc.to_payload(), status=409)
                return
            except (RattishEditorError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/retention"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/retention")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                payload = update_retention_settings_payload(
                    self._request_data_dir(query),
                    workflow_id=workflow_id,
                    settings=body,
                )
            except (WorkflowLogError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path == "/api/rattish/workflows/import/preview":
            try:
                body = self._read_json()
                with _bundle_path_from_body(body) as bundle_path:
                    if bundle_path is None:
                        raise WorkflowBundleError("A .raticode bundle is required")
                    if body.get("bundlePath"):
                        self._assert_bundle_path_allowed(
                            bundle_path,
                            body.get("grantId"),
                            must_exist=True,
                        )
                    payload = preview_rattish_bundle_payload(
                        bundle_path,
                        resource_limits=self._resource_limits(),
                    )
            except (WorkflowBundleError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"bundle": payload})
            return

        if parsed.path == "/api/rattish/workflows/import":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                project_root_value = str(body.get("projectRoot", "")).strip()
                if not project_root_value:
                    raise WorkflowBundleError("Choose a project folder for the imported workflow")
                project_root = Path(project_root_value).expanduser()
                self._assert_bundle_path_allowed(
                    project_root,
                    body.get("projectGrantId"),
                    must_exist=True,
                )
                with _bundle_path_from_body(body) as bundle_path:
                    if bundle_path is None:
                        raise WorkflowBundleError("A .raticode bundle is required")
                    if body.get("bundlePath"):
                        self._assert_bundle_path_allowed(
                            bundle_path,
                            body.get("grantId"),
                            must_exist=True,
                        )
                    workflow = import_rattish_bundle_payload(
                        bundle_path,
                        project_root,
                        self._request_data_dir(query),
                        resource_limits=self._resource_limits(),
                    )
            except (WorkflowBundleError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._sync_schedules()
            self._send_json({"workflow": workflow}, status=201)
            return

        if parsed.path.startswith("/api/rattish/workflows/") and parsed.path.endswith("/export"):
            workflow_id = parsed.path.removeprefix("/api/rattish/workflows/").removesuffix(
                "/export"
            )
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                output_path = _resolve_ui_bundle_path(
                    Path(str(body.get("outputPath", ""))),
                    self._request_data_dir(query),
                )
                output_path = self._assert_bundle_path_allowed(
                    output_path,
                    body.get("grantId"),
                    must_exist=False,
                )
                payload = export_rattish_bundle_payload(
                    workflow_id,
                    output_path,
                    self._request_data_dir(query),
                )
            except (WorkflowBundleError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload, status=201)
            return

        if parsed.path == "/api/workflows/import":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                with _bundle_path_from_body(body) as bundle_path:
                    if bundle_path and body.get("bundlePath"):
                        bundle_path = _resolve_ui_bundle_path(
                            bundle_path,
                            self._request_data_dir(query),
                        )
                        self._assert_bundle_path_allowed(
                            bundle_path,
                            body.get("grantId"),
                            must_exist=True,
                        )
                        plan = import_workflow_bundle_payload(
                            bundle_path,
                            self._request_data_dir(query),
                            replace=bool(body.get("replace", False)),
                            dry_run=bool(body.get("dryRun", False)),
                            resource_limits=self._resource_limits(),
                        )
                        self._sync_schedules()
                        self._send_json(
                            {"import": plan},
                            status=200 if body.get("dryRun") else 201,
                        )
                        return
                workflow = import_workflow_payload(
                    str(body.get("content", "")),
                    self._request_data_dir(query),
                )
            except WorkflowAlreadyExistsError as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            except (
                WorkflowBundleError,
                WorkflowCreateError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._sync_schedules()
            self._send_json({"workflow": workflow}, status=201)
            return

        if parsed.path == "/api/workflows/import/preview":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                with _bundle_path_from_body(body) as bundle_path:
                    if bundle_path and body.get("bundlePath"):
                        bundle_path = _resolve_ui_bundle_path(
                            bundle_path,
                            self._request_data_dir(query),
                        )
                        self._assert_bundle_path_allowed(
                            bundle_path,
                            body.get("grantId"),
                            must_exist=True,
                        )
                    payload = preview_workflow_bundle_payload(
                        bundle_path or Path(""),
                        self._request_data_dir(query),
                        resource_limits=self._resource_limits(),
                    )
            except (WorkflowBundleError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"import": payload})
            return

        if parsed.path == "/api/workflows":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                if body.get("template"):
                    workflow = create_workflow_payload(
                        str(body.get("name", "")),
                        self._request_data_dir(query),
                        template=body.get("template"),
                    )
                else:
                    project_root = Path(str(body.get("projectRoot", ""))).expanduser()
                    if not str(body.get("projectRoot", "")).strip():
                        raise WorkflowCreateError("Project folder is required")
                    try:
                        self._assert_bundle_path_allowed(
                            project_root,
                            body.get("projectGrantId"),
                            must_exist=True,
                        )
                    except WorkflowBundleError as exc:
                        raise WorkflowCreateError(str(exc)) from exc
                    workflow = create_registered_workflow_payload(
                        str(body.get("name", "")),
                        project_root,
                        registry_dir=self._request_data_dir(query),
                    )
            except WorkflowAlreadyExistsError as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            except (WorkflowCreateError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._sync_schedules()
            self._send_json({"workflow": workflow}, status=201)
            return

        if parsed.path == "/api/chat/attachments":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json(limit=CHAT_ATTACHMENT_MAX_TOTAL_BYTES * 2)
                payload = store_chat_attachments(body, self._request_data_dir(query))
            except (ChatMediaError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload, status=201)
            return

        if parsed.path == "/api/chat/transcribe":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json(limit=CHAT_AUDIO_MAX_BYTES * 2)
                payload = transcribe_chat_audio(
                    body,
                    data_dir=self._request_data_dir(query),
                )
            except (ChatMediaError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Rem transcription failed")
                self._send_json({"error": f"Transcription failed: {exc}"}, status=502)
                return
            self._send_json(payload)
            return

        if parsed.path == "/api/chat/transcribe/start":
            query = parse_qs(parsed.query)
            try:
                self._read_json()
                payload = start_chat_transcription(data_dir=self._request_data_dir(query))
            except (ChatMediaError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Rem transcription session failed to start")
                self._send_json({"error": f"Transcription failed: {exc}"}, status=502)
                return
            self._send_json(payload, status=201)
            return

        if parsed.path == "/api/chat/transcribe/chunk":
            try:
                body = self._read_json(limit=CHAT_AUDIO_CHUNK_MAX_BYTES * 2)
                payload = stream_chat_transcription(body)
            except (ChatMediaError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Rem transcription chunk failed")
                self._send_json({"error": f"Transcription failed: {exc}"}, status=502)
                return
            self._send_json(payload)
            return

        if parsed.path == "/api/chat/transcribe/finish":
            try:
                payload = finish_chat_transcription(self._read_json())
            except (ChatMediaError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Rem transcription finalization failed")
                self._send_json({"error": f"Transcription failed: {exc}"}, status=502)
                return
            self._send_json(payload)
            return

        if parsed.path == "/api/chat/transcribe/cancel":
            try:
                payload = cancel_chat_transcription(self._read_json())
            except (ChatMediaError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path == "/api/chat/changes/undo":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                payload = undo_chat_changes(
                    str(body.get("changeSetId") or ""),
                    self._request_data_dir(query),
                )
            except (ChatChangeError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            self._sync_schedules()
            self._send_json(payload)
            return

        if parsed.path == "/api/chat/changes/redo":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                payload = redo_chat_changes(
                    str(body.get("changeSetId") or ""),
                    self._request_data_dir(query),
                )
            except (ChatChangeError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            self._sync_schedules()
            self._send_json(payload)
            return

        if parsed.path == "/api/chat/commit-message":
            from gofer.ui.commit_message import generate_commit_message

            try:
                body = self._read_json(limit=COMMIT_MESSAGE_MAX_BODY_BYTES)
                commit_project_root: Path | None = None
                if body.get("projectRoot"):
                    commit_project_root = self._assert_bundle_path_allowed(
                        Path(str(body["projectRoot"])), body.get("grantId"), must_exist=True
                    )
                result = self._run_async(
                    generate_commit_message(
                        provider=str(body.get("provider", "codex")),
                        model=str(body.get("model", "cli-default")),
                        effort=_optional_body_str(body, "effort"),
                        diff=body.get("diff", ""),
                        project_root=commit_project_root,
                        inspect_staged=body.get("inspectStaged") is True,
                    )
                )
                self._send_json(result)
            except (
                ValueError,
                WorkflowBundleError,
                ChatProviderError,
                OSError,
                TimeoutError,
            ) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/chat/stream":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                self._validate_second_brain(body)
                with self._rem_swarm_session(body) as (chat_body, swarm_url):
                    self._send_stream_headers()
                    self._run_async(
                        self._stream_chat_response(
                            body=chat_body,
                            data_dir=self._request_data_dir(query),
                            trusted_swarm_url=swarm_url,
                        )
                    )
            except (json.JSONDecodeError, WorkflowBundleError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/chat":
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                self._validate_second_brain(body)
                with self._rem_swarm_session(body) as (chat_body, swarm_url):
                    response = self._run_async(
                        run_workflow_chat(
                            provider=str(body.get("provider", "codex")),
                            model=str(body.get("model", "cli-default")),
                            effort=_optional_body_str(body, "effort"),
                            permission_mode=_optional_body_str(body, "permissionMode"),
                            messages=body.get("messages") or [],
                            workflow=chat_body.get("workflow"),
                            trusted_swarm_url=swarm_url,
                            data_dir=self._request_data_dir(query),
                            resource_limits=self._resource_limits(),
                        )
                    )
            except (
                ChatProviderError,
                json.JSONDecodeError,
                WorkflowBundleError,
                ValueError,
            ) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Unhandled Rem error")
                self._send_json({"error": f"Rem failed: {exc}"}, status=500)
                return

            self._send_json(response)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/validate"):
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                self._send_json(
                    validate_workflow_draft_payload(
                        body,
                        self._request_data_dir(query),
                    )
                )
            except (WorkflowUpdateError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/validate/fix"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/validate/fix")
            query = parse_qs(parsed.query)
            try:
                payload = apply_workflow_validation_fix_payload(
                    workflow_id,
                    self._read_json(),
                    self._request_data_dir(query),
                )
            except (WorkflowUpdateError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path == "/api/provider/profiles":
            query = parse_qs(parsed.query)
            try:
                payload = upsert_provider_profile_payload(
                    self._read_json(),
                    self._request_data_dir(query),
                )
            except (ProviderProfileError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if (
            parsed.path.startswith("/api/workflows/")
            and "/runs/" in parsed.path
            and parsed.path.endswith("/resume")
        ):
            remainder = parsed.path.removeprefix("/api/workflows/")
            workflow_id, run_id = remainder.removesuffix("/resume").split("/runs/", 1)
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                trigger_context = body.get("triggerContext")
                if trigger_context is not None and not isinstance(trigger_context, dict):
                    raise WorkflowRunError("triggerContext must be an object")
                payload = self._run_async(
                    resume_workflow_payload(
                        workflow_id,
                        self._request_data_dir(query),
                        run_id=run_id,
                        from_node=body.get("fromNode"),
                        only_node=body.get("onlyNode"),
                        skip_cache=bool(body.get("skipCache", False)),
                        force=bool(body.get("force", False)),
                        trigger_context=trigger_context,
                    )
                )
            except (WorkflowRunError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"run": payload})
            return

        if (
            parsed.path.startswith("/api/workflows/")
            and "/runs/" in parsed.path
            and parsed.path.endswith("/stop")
        ):
            remainder = parsed.path.removeprefix("/api/workflows/")
            workflow_id, run_id = remainder.removesuffix("/stop").split("/runs/", 1)
            query = parse_qs(parsed.query)
            try:
                payload = stop_workflow_run_payload(
                    workflow_id,
                    self._request_data_dir(query),
                    run_id=run_id,
                )
            except WorkflowUpdateError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/stop"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/stop")
            query = parse_qs(parsed.query)
            try:
                payload = stop_workflow_run_payload(workflow_id, self._request_data_dir(query))
            except WorkflowUpdateError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._sync_schedules()
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/rename"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/rename")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                workflow = rename_workflow_payload(
                    workflow_id,
                    str(body.get("name", "")),
                    self._request_data_dir(query),
                )
            except WorkflowAlreadyExistsError as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            except (WorkflowUpdateError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._sync_schedules()
            self._sync_continuous_runs()
            self._send_json({"workflow": workflow})
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/logs/prune"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/logs/prune")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                payload = prune_workflow_run_logs_payload(
                    workflow_id,
                    self._request_data_dir(query),
                    keep_last=_optional_body_int(body, "keepLast"),
                    keep_days=_optional_body_int(body, "keepDays"),
                    keep_failed_days=_optional_body_int(body, "keepFailedDays"),
                    dry_run=bool(body.get("dryRun", True)),
                )
            except (WorkflowLogError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/duplicate"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/duplicate")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                workflow = duplicate_workflow_payload(
                    workflow_id,
                    body.get("name"),
                    self._request_data_dir(query),
                )
            except WorkflowAlreadyExistsError as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            except (WorkflowCreateError, WorkflowUpdateError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._sync_schedules()
            self._send_json({"workflow": workflow}, status=201)
            return

        if (
            parsed.path.startswith("/api/workflows/")
            and "/history/" in parsed.path
            and parsed.path.endswith("/restore")
        ):
            remainder = parsed.path.removeprefix("/api/workflows/").removesuffix("/restore")
            workflow_id, revision_id = remainder.split("/history/", 1)
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                payload = restore_workflow_revision_payload(
                    workflow_id,
                    revision_id,
                    self._request_data_dir(query),
                    as_copy=bool(body.get("asCopy", False)),
                )
            except (WorkflowHistoryError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._sync_schedules()
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/export"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/export")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                output_path = _resolve_ui_bundle_path(
                    Path(str(body.get("outputPath", ""))),
                    self._request_data_dir(query),
                )
                output_path = self._assert_bundle_path_allowed(
                    output_path,
                    body.get("grantId"),
                    must_exist=False,
                )
                payload = export_workflow_bundle_payload(
                    workflow_id,
                    output_path,
                    self._request_data_dir(query),
                    notes=body.get("notes"),
                )
            except (WorkflowBundleError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload, status=201)
            return

        if parsed.path == "/api/desktop/path-grants":
            try:
                body = self._read_json()
                payload = self._register_desktop_path_grant(body)
            except (json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload, status=201)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/plan"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/plan")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                trigger_context = body.get("triggerContext")
                if trigger_context is not None and not isinstance(trigger_context, dict):
                    raise WorkflowPlanError("triggerContext must be an object")
                parameters = body.get("parameters")
                inputs = body.get("inputs")
                if parameters is not None and not isinstance(parameters, dict):
                    raise WorkflowPlanError("parameters must be an object")
                if inputs is not None and not isinstance(inputs, dict):
                    raise WorkflowPlanError("inputs must be an object")
                plan = workflow_plan_payload(
                    workflow_id,
                    self._request_data_dir(query),
                    trigger_context=trigger_context,
                    parameters=parameters,
                    inputs=inputs,
                )
            except (WorkflowPlanError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._send_json({"plan": plan})
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/queue"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/queue")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                parameters = body.get("parameters")
                if parameters is not None and not isinstance(parameters, dict):
                    raise RunnerQueueError("parameters must be an object")
                target_labels = body.get("targetLabels")
                if target_labels is not None and not isinstance(target_labels, list):
                    raise RunnerQueueError("targetLabels must be an array")
                payload = queue_workflow_run_payload(
                    workflow_id,
                    self._request_data_dir(query),
                    priority=int(body.get("priority") or 0),
                    trigger=str(body.get("trigger") or "ui"),
                    parameters=parameters,
                    target_labels=[str(label) for label in (target_labels or [])],
                )
            except (RunnerQueueError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._send_json(payload, status=202)
            return

        if parsed.path.startswith("/api/queue/") and parsed.path.endswith("/cancel"):
            run_id = parsed.path.removeprefix("/api/queue/").removesuffix("/cancel")
            query = parse_qs(parsed.query)
            try:
                payload = cancel_queued_run_payload(run_id, self._request_data_dir(query))
            except RunnerQueueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/run"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/run")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                dry_run = bool(body.get("dryRun", False))
                run_options: dict[str, Any] = {}
                if "background" in body:
                    if not isinstance(body["background"], bool):
                        raise WorkflowRunError("background must be a boolean")
                    run_options["background"] = body["background"]
                if "expectedRevision" in body:
                    if not isinstance(body["expectedRevision"], str):
                        raise WorkflowRunError("expectedRevision must be a string")
                    run_options["expected_revision"] = body["expectedRevision"]
                trigger_context = body.get("triggerContext")
                if trigger_context is not None and not isinstance(trigger_context, dict):
                    raise WorkflowRunError("triggerContext must be an object")
                parameters = body.get("parameters")
                inputs = body.get("inputs")
                if parameters is not None and not isinstance(parameters, dict):
                    raise WorkflowRunError("parameters must be an object")
                if inputs is not None and not isinstance(inputs, dict):
                    raise WorkflowRunError("inputs must be an object")
                if parameters is None and inputs is None:
                    result = self._run_async(
                        run_workflow_payload(
                            workflow_id,
                            self._request_data_dir(query),
                            dry_run=dry_run,
                            trigger_context=trigger_context,
                            **run_options,
                        )
                    )
                else:
                    result = self._run_async(
                        run_workflow_payload(
                            workflow_id,
                            self._request_data_dir(query),
                            dry_run=dry_run,
                            trigger_context=trigger_context,
                            parameters=parameters,
                            inputs=inputs,
                            **run_options,
                        )
                    )
            except (WorkflowRunError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._send_json({"plan" if dry_run else "run": result})
            return

        if (
            parsed.path.startswith("/api/workflows/")
            and "/webhooks/" in parsed.path
            and parsed.path.endswith("/replay")
        ):
            remainder = parsed.path.removeprefix("/api/workflows/")
            workflow_id, trigger_part = remainder.split("/webhooks/", 1)
            trigger_id = trigger_part.removesuffix("/replay")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                run_id = str(body.get("runId") or "")
                if not run_id:
                    raise WorkflowTriggerError("runId is required")
                payload = self._run_async(
                    replay_workflow_trigger_payload(
                        workflow_id,
                        run_id,
                        self._request_data_dir(query),
                        trigger_id=trigger_id,
                        token=self._webhook_token(),
                        require_token=True,
                    )
                )
            except (WorkflowTriggerError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"trigger": payload}, status=202)
            return

        if (
            parsed.path.startswith("/api/workflows/")
            and "/webhooks/" in parsed.path
            and parsed.path.endswith("/trigger")
        ):
            remainder = parsed.path.removeprefix("/api/workflows/")
            workflow_id, trigger_part = remainder.split("/webhooks/", 1)
            trigger_id = trigger_part.removesuffix("/trigger")
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                payload = self._run_async(
                    trigger_workflow_payload(
                        workflow_id,
                        trigger_id,
                        self._request_data_dir(query),
                        payload=body,
                        headers={str(key): str(value) for key, value in self.headers.items()},
                        source=str(self.headers.get("X-Gofer-Webhook-Source") or "http"),
                        token=self._webhook_token(),
                    )
                )
            except (WorkflowTriggerError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"trigger": payload}, status=202)
            return

        if (
            parsed.path.startswith("/api/workflows/")
            and "/approvals/" in parsed.path
            and (parsed.path.endswith("/approve") or parsed.path.endswith("/reject"))
        ):
            remainder = parsed.path.removeprefix("/api/workflows/")
            workflow_id, approval_part = remainder.split("/approvals/", 1)
            approval_key, action = approval_part.rsplit("/", 1)
            run_id, node_id = approval_key.split("/", 1)
            query = parse_qs(parsed.query)
            try:
                body = self._read_json()
                payload = decide_workflow_approval_payload(
                    workflow_id,
                    run_id,
                    node_id,
                    "approved" if action == "approve" else "rejected",
                    self._request_data_dir(query),
                    decided_by=str(body.get("by") or "ui"),
                    notes=str(body.get("notes") or ""),
                )
            except (WorkflowApprovalError, json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._send_json(payload)
            return

        self._send_json({"error": "Not found"}, status=404)

    def _dispatch_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/workflows/"):
            workflow_id = parsed.path.removeprefix("/api/workflows/")
            query = parse_qs(parsed.query)
            try:
                workflow = update_workflow_payload(
                    workflow_id,
                    self._read_json(),
                    self._request_data_dir(query),
                )
            except (WorkflowUpdateError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            self._sync_schedules()
            self._send_json({"workflow": workflow})
            return

        self._send_json({"error": "Not found"}, status=404)

    def _dispatch_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            query = parse_qs(parsed.query)
            try:
                payload = delete_workflow_chat_payload(
                    "workflow-assistant",
                    self._request_data_dir(query),
                )
            except WorkflowUpdateError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/chat/threads/"):
            thread_id = parsed.path.removeprefix("/api/chat/threads/")
            query = parse_qs(parsed.query)
            try:
                payload = delete_workflow_chat_payload(
                    f"workflow-assistant:{thread_id}",
                    self._request_data_dir(query),
                )
            except WorkflowUpdateError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            shutil.rmtree(
                attachment_thread_dir(self._request_data_dir(query), thread_id),
                ignore_errors=True,
            )
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/provider/profiles/"):
            profile_name = parsed.path.removeprefix("/api/provider/profiles/")
            query = parse_qs(parsed.query)
            try:
                payload = delete_provider_profile_payload(
                    profile_name,
                    self._request_data_dir(query),
                )
            except ProviderProfileError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/chat"):
            workflow_id = parsed.path.removeprefix("/api/workflows/").removesuffix("/chat")
            query = parse_qs(parsed.query)
            try:
                payload = delete_workflow_chat_payload(
                    workflow_id,
                    self._request_data_dir(query),
                )
            except WorkflowUpdateError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return

        if parsed.path.startswith("/api/workflows/"):
            workflow_id = parsed.path.removeprefix("/api/workflows/")
            query = parse_qs(parsed.query)
            try:
                payload = delete_workflow_payload(
                    workflow_id,
                    self._request_data_dir(query),
                    source_format=query.get("sourceFormat", [None])[0],
                )
            except WorkflowUpdateError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return

            self._sync_schedules()
            self._send_json(payload)
            return

        self._send_json({"error": "Not found"}, status=404)

    def _dispatch_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_common_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _sync_schedules(self) -> None:
        server = self.server
        if isinstance(server, GoferUiServer):
            server.sync_schedules()

    def _sync_continuous_runs(self) -> None:
        server = self.server
        if isinstance(server, GoferUiServer):
            server.ensure_continuous_runs()

    def _run_async(self, operation: Coroutine[Any, Any, Any]) -> Any:
        async def bounded() -> Any:
            timeout = getattr(self.server, "request_job_timeout", UI_JOB_DEADLINE_SECONDS)
            return await asyncio.wait_for(operation, timeout=timeout)

        try:
            return asyncio.run(bounded())
        except TimeoutError:
            if getattr(self, "_stream_started", False):
                self._write_stream_event(
                    {"type": "error", "error": "Request exceeded its execution deadline"}
                )
                return None
            raise

    async def _stream_chat_response(
        self, body: dict[str, Any], data_dir: Path, trusted_swarm_url: str | None = None
    ) -> None:
        cancel_event = threading.Event()
        try:
            async for event in stream_workflow_chat(
                provider=str(body.get("provider", "codex")),
                model=str(body.get("model", "cli-default")),
                effort=_optional_body_str(body, "effort"),
                permission_mode=_optional_body_str(body, "permissionMode"),
                messages=body.get("messages") or [],
                workflow=body.get("workflow"),
                trusted_swarm_url=trusted_swarm_url,
                cancel_event=cancel_event,
                data_dir=data_dir,
                resource_limits=self._resource_limits(),
            ):
                self._write_stream_event(event)
        except (BrokenPipeError, ConnectionResetError):
            cancel_event.set()
        except ChatProviderError as exc:
            self._write_stream_event({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            cancel_event.set()
            log.exception("Unhandled Rem stream error")
            self._write_stream_event(
                {
                    "type": "error",
                    "error": f"Rem failed: {exc}",
                }
            )
        finally:
            cancel_event.set()

    def _default_data_dir(self) -> Path:
        server = self.server
        if isinstance(server, GoferUiServer):
            return server.data_dir
        return get_data_dir()

    def _resource_limits(self) -> ResourceLimits:
        server = getattr(self, "server", None)
        if isinstance(server, GoferUiServer):
            return server.resource_limits
        return bundle_resource_limits_from_env()

    def _request_data_dir(self, _query: dict[str, list[str]]) -> Path:
        return self._default_data_dir()

    @contextmanager
    def _rem_swarm_session(
        self, body: dict[str, Any]
    ) -> Iterator[tuple[dict[str, Any], str | None]]:
        workflow = body.get("workflow") or {}
        config = workflow.get("remSwarmAccess") or {}
        if config.get("enabled") is not True:
            yield body, None
            return
        project_value = workflow.get("projectRoot")
        if not project_value:
            # Project-less conversations stay usable with the global toggle enabled.
            yield body, None
            return
        if not isinstance(project_value, str) or not Path(project_value).is_absolute():
            raise ValueError("Swarm access requires an absolute project folder")
        project = self._assert_bundle_path_allowed(
            Path(project_value), config.get("grantId"), must_exist=True
        )
        if not project.is_dir():
            raise ValueError("Swarm access requires an existing project folder")
        server = self.server
        if not isinstance(server, GoferUiServer):
            raise ValueError("Swarms are unavailable on this server")
        read_only = body.get("permissionMode") in {"read-only", "plan"}
        with server.swarms.rem_session(project, read_only=read_only) as url:
            resources = dict(workflow.get("remResources") or {})
            resources["mcpServers"] = [
                item for item in resources.get("mcpServers", []) if item.get("name") != "swarm"
            ] + [{"name": "swarm", "type": "http", "url": url}]
            yield {**body, "workflow": {**workflow, "remResources": resources}}, url

    def _validate_second_brain(self, body: dict[str, Any]) -> None:
        config = (body.get("workflow") or {}).get("remSecondBrain") or {}
        if config.get("enabled") is not True:
            return
        root = Path(str(config.get("root", ""))).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("Choose an existing absolute Second Brain folder.")
        try:
            self._assert_bundle_path_allowed(root, config.get("grantId"), must_exist=True)
        except WorkflowBundleError as exc:
            log.warning("SECOND_BRAIN_PATH_DENIED root=%r", str(root))
            raise WorkflowBundleError(
                "Raticode could not confirm access to the Second Brain folder. "
                "Retry your message to renew folder access. If it keeps failing, "
                "reselect the folder in Settings > Rem or restart Raticode."
            ) from exc
        if config.get("format", "md") not in {"md", "html"}:
            raise ValueError("Choose Markdown or HTML for Second Brain reports.")

    def _assert_bundle_path_allowed(
        self,
        target_path: Path,
        grant_id: object,
        *,
        must_exist: bool,
    ) -> Path:
        server = self.server
        grants = (
            server.path_grants if isinstance(server, GoferUiServer) else DesktopPathGrantStore()
        )
        data_dir = self._default_data_dir()
        try:
            canonical_target = _canonical_path_for_containment(target_path)
            canonical_data_dir = _canonical_path_for_containment(data_dir)
        except (OSError, RuntimeError) as exc:
            raise WorkflowBundleError(str(exc)) from exc
        if must_exist and not target_path.exists():
            raise WorkflowBundleError(f"Bundle path does not exist: {target_path}")
        if _is_path_inside(canonical_target, canonical_data_dir):
            return canonical_target
        grant_value = str(grant_id or "").strip()
        if grants.covers_canonical(canonical_target, grant_value):
            return canonical_target
        raise WorkflowBundleError("Bundle path is outside the approved Raticode desktop roots")

    def _register_desktop_path_grant(self, body: dict[str, Any]) -> dict[str, str]:
        server = self.server
        if not isinstance(server, GoferUiServer):
            raise ValueError("Desktop path grants are unavailable")
        secret = server.path_grant_secret
        if not secret or not hmac.compare_digest(
            self.headers.get("X-Gofer-Desktop-Grant-Secret", ""), secret
        ):
            raise ValueError("Desktop path grant registration is unauthorized")
        target_path = str(body.get("path") or "").strip()
        grant_id = str(body.get("grantId") or "").strip()
        if not target_path:
            raise ValueError("A path is required")
        if not grant_id:
            raise ValueError("A grant id is required")
        registered_id = server.path_grants.register(Path(target_path), grant_id)
        return {
            "grantId": registered_id,
            "path": str(_canonical_path_for_containment(Path(target_path))),
        }

    def _read_json(self, *, limit: int | None = None) -> dict[str, Any]:
        limit = limit or self._resource_limits().max_api_request_body_bytes
        content_length_header = self.headers.get("Content-Length")
        if content_length_header is None:
            raise json.JSONDecodeError("Content-Length is required", "", 0)
        try:
            content_length = int(content_length_header)
        except ValueError as exc:
            raise json.JSONDecodeError("Invalid Content-Length", "", 0) from exc
        if content_length < 0:
            raise json.JSONDecodeError("Invalid Content-Length", "", 0)
        if content_length > limit:
            raise json.JSONDecodeError(
                f"Request body exceeds limit {limit} bytes",
                "",
                0,
            )
        with self._read_deadline():
            raw_body = self.rfile.read(content_length)
        if len(raw_body) != content_length:
            raise json.JSONDecodeError("Incomplete request body", "", 0)
        payload = json.loads(raw_body.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise WorkflowCreateError("Request body must be a JSON object")
        return payload

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if getattr(self, "_request_method", "") == "GET" and "/logs" in self.path:
            self.send_header("ETag", '"' + hashlib.sha256(body).hexdigest() + '"')
            self.send_header("Access-Control-Expose-Headers", "ETag")
        self._send_common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _webhook_token(self) -> str | None:
        token = self.headers.get("X-Gofer-Webhook-Token")
        authorization = self.headers.get("Authorization", "")
        if token is None and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ").strip()
        return token

    def _send_html(self, markup: str, status: int = 200) -> None:
        body = markup.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_stream_headers(self) -> None:
        self._stream_started = True
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self._send_common_headers()
        self.end_headers()

    def _write_stream_event(self, event: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(event).encode("utf-8") + b"\n")
        self.wfile.flush()

    def _send_common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Vary", "Origin")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Gofer-Desktop-Grant-Secret, "
            "X-Gofer-UI-Token, X-Gofer-Webhook-Token",
        )

    def _allow_host(self) -> bool:
        values = self.headers.get_all("Host", [])
        if len(values) != 1:
            return False
        try:
            parsed = urlparse("http://" + values[0])
            address = self.server.server_address
            if not isinstance(address, tuple) or len(address) < 2:
                return False
            host, port = address[:2]
            allowed_hosts = {str(host).lower()}
            if host in {"127.0.0.1", "::1", "0.0.0.0", "::"}:
                allowed_hosts.update({"localhost", "127.0.0.1", "::1"})
            return (
                parsed.hostname in allowed_hosts
                and (parsed.port or 80) == int(port)
                and not parsed.username
                and not parsed.password
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            return False

    def _allow_origin(self, path: str) -> bool:
        origin = getattr(self, "headers", {}).get("Origin")
        if not origin:
            return True
        return self._is_allowed_origin(origin)

    def _cors_origin(self) -> str | None:
        origin_value = getattr(self, "headers", {}).get("Origin")
        origin = str(origin_value) if origin_value else None
        return origin if origin and self._is_allowed_origin(origin) else None

    def _is_allowed_origin(self, origin: str) -> bool:
        # file:// desktop documents have an opaque origin. Only a credentialed
        # desktop request or its token-header preflight can use that origin.
        if origin == "null":
            if self._has_ui_api_token():
                return True
            requested = {
                value.strip().lower()
                for value in self.headers.get("Access-Control-Request-Headers", "").split(",")
            }
            return (
                getattr(self, "_request_method", "") == "OPTIONS"
                and self.headers.get("Access-Control-Request-Method")
                in {"GET", "POST", "PUT", "DELETE"}
                and bool(requested & {"authorization", "x-gofer-ui-token"})
            )
        server = self.server
        allowed = server.allowed_origins if isinstance(server, GoferUiServer) else set()
        if origin in allowed:
            return True
        try:
            parsed_origin = urlparse(origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or parsed_origin.username
                or parsed_origin.password
                or parsed_origin.path
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                return False
            address = self.server.server_address
            if not isinstance(address, tuple) or len(address) < 2:
                return False
            host, port = address[:2]
            if parsed_origin.hostname not in {host, "localhost"}:
                return False
            origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
            return origin_port == int(port)
        except ValueError:
            return False

    def _has_ui_api_token(self) -> bool:
        expected = getattr(self.server, "api_token", "")
        supplied = getattr(self, "headers", {}).get("X-Gofer-UI-Token")
        authorization = getattr(self, "headers", {}).get("Authorization", "")
        if supplied is None and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ").strip()
        return bool(expected and supplied and hmac.compare_digest(supplied, expected))

    def _authorize_ui_request(self, method: str, path: str) -> bool:
        if not _requires_ui_api_auth(method, path) or self._has_ui_api_token():
            return True
        self._send_json({"error": "UI API authentication required"}, status=401)
        return False


def ready_payload(server: GoferUiServer) -> dict[str, Any]:
    host, port = server.server_address[:2]
    payload = {
        "host": host,
        "port": port,
        "dataDir": str(server.data_dir),
        "goferCliAvailable": server.gofer_cli_path is not None,
    }
    if getattr(server, "emit_ready_token", False):
        payload["apiToken"] = server.api_token
    return payload


def ui_session_payload(server: Any) -> dict[str, Any]:
    host, port = server.server_address[:2]
    api_token = server.api_token if isinstance(server, GoferUiServer) else ""
    return {
        "apiBaseUrl": f"http://{host}:{port}/api",
        "apiToken": api_token,
    }


def workflow_usage_page_payload(
    workflow_id: str,
    data_dir: Path,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    runs_payload = list_workflow_run_logs_payload(workflow_id, data_dir)
    run_summaries: list[dict[str, Any]] = []
    totals = {
        "agent_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
        "agent_time_seconds": 0.0,
    }
    for run_log in (runs_payload.get("runs") or [])[:limit]:
        run_id = str(run_log["id"])
        try:
            payload = workflow_run_log_payload(workflow_id, run_id, data_dir)
        except WorkflowLogError:
            continue
        summary = payload.get("usageSummary")
        if not isinstance(summary, dict):
            summary = summarize_node_outputs(payload.get("nodeOutputs") or {})
        run_summaries.append(
            {
                "runId": run_id,
                "status": payload.get("status"),
                "startedAt": payload.get("startedAt"),
                "summary": summary,
            }
        )
        run_totals = summary.get("totals") if isinstance(summary, dict) else {}
        if not isinstance(run_totals, dict):
            continue
        for key in ("agent_calls", "input_tokens", "output_tokens", "total_tokens"):
            totals[key] += int(run_totals.get(key) or 0)
        totals["estimated_cost"] += float(run_totals.get("estimated_cost") or 0.0)
        totals["agent_time_seconds"] += float(run_totals.get("agent_time_seconds") or 0.0)
    return {"workflowId": workflow_id, "runs": run_summaries, "totals": totals}


def render_workflow_usage_html(payload: dict[str, Any]) -> str:
    workflow_id = html.escape(str(payload.get("workflowId") or "workflow"))
    totals_value = payload.get("totals")
    totals: dict[str, Any] = totals_value if isinstance(totals_value, dict) else {}
    rows = []
    for run in payload.get("runs") or []:
        if not isinstance(run, dict):
            continue
        summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
        run_totals = summary.get("totals") if isinstance(summary, dict) else {}
        if not isinstance(run_totals, dict):
            run_totals = {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(run.get('runId') or ''))}</td>"
            f"<td>{html.escape(str(run.get('status') or 'unknown'))}</td>"
            f"<td>{html.escape(str(run_totals.get('agent_calls') or 0))}</td>"
            f"<td>{html.escape(str(run_totals.get('total_tokens') or 0))}</td>"
            f"<td>${float(run_totals.get('estimated_cost') or 0.0):.6f}</td>"
            f"<td>{float(run_totals.get('agent_time_seconds') or 0.0):.2f}s</td>"
            "</tr>"
        )
    table = "".join(rows) or "<tr><td colspan='6'>No usage records found.</td></tr>"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>LLM usage for {workflow_id}</title></head><body>"
        f"<h1>LLM usage for {workflow_id}</h1>"
        f"{_usage_totals_html(totals)}"
        "<h2>Recent runs</h2><table>"
        "<thead><tr><th>Run</th><th>Status</th><th>Calls</th><th>Tokens</th>"
        "<th>Cost</th><th>Agent time</th></tr></thead>"
        f"<tbody>{table}</tbody></table></body></html>"
    )


def render_usage_summary_html(summary: dict[str, Any]) -> str:
    totals = summary.get("totals") if isinstance(summary, dict) else {}
    if not isinstance(totals, dict):
        totals = {}
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>LLM run usage</title></head><body>"
        "<h1>LLM run usage</h1>"
        f"{_usage_totals_html(totals)}"
        f"{_usage_nodes_html('Most expensive nodes', summary.get('most_expensive_nodes'))}"
        f"{_usage_nodes_html('Slowest nodes', summary.get('slowest_nodes'))}"
        "</body></html>"
    )


def _usage_totals_html(totals: dict[str, Any]) -> str:
    return (
        "<section><h2>Summary</h2><dl>"
        f"<dt>Agent calls</dt><dd>{html.escape(str(totals.get('agent_calls') or 0))}</dd>"
        f"<dt>Total tokens</dt><dd>{html.escape(str(totals.get('total_tokens') or 0))}</dd>"
        f"<dt>Estimated cost</dt><dd>${float(totals.get('estimated_cost') or 0.0):.6f}</dd>"
        f"<dt>Agent time</dt><dd>{float(totals.get('agent_time_seconds') or 0.0):.2f}s</dd>"
        "</dl></section>"
    )


def _usage_nodes_html(title: str, nodes: Any) -> str:
    rows = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(node.get('node_id') or ''))}</td>"
            f"<td>{html.escape(str(node.get('model') or node.get('provider') or ''))}</td>"
            f"<td>{html.escape(str(node.get('total_tokens') or 0))}</td>"
            f"<td>${float(node.get('estimated_cost') or 0.0):.6f}</td>"
            f"<td>{float(node.get('duration_seconds') or 0.0):.2f}s</td>"
            "</tr>"
        )
    body = "".join(rows) or "<tr><td colspan='5'>None</td></tr>"
    return (
        f"<section><h2>{html.escape(title)}</h2><table>"
        "<thead><tr><th>Node</th><th>Model</th><th>Tokens</th><th>Cost</th>"
        "<th>Duration</th></tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    data_dir: Path | None = None,
    resource_limits: ResourceLimits | None = None,
    api_token: str | None = None,
    allowed_origins: set[str] | None = None,
) -> GoferUiServer:
    return GoferUiServer(
        (host, port),
        data_dir or get_data_dir(),
        resource_limits=resource_limits,
        api_token=api_token,
        allowed_origins=allowed_origins,
    )


def _new_ui_api_token() -> str:
    return secrets.token_urlsafe(32)


def _default_allowed_origins() -> set[str]:
    origins = set(DEFAULT_DEV_FRONTEND_ORIGINS)
    configured = os.environ.get("GOFER_UI_ALLOWED_ORIGINS", "")
    for origin in configured.split(","):
        normalized = origin.strip().rstrip("/")
        if normalized:
            origins.add(normalized)
    for env_name in ("GOFER_VITE_DEV_SERVER_URL", "VITE_DEV_SERVER_URL"):
        value = os.environ.get(env_name)
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return origins


def _requires_ui_api_auth(method: str, path: str) -> bool:
    if method == "OPTIONS" or (method == "GET" and path == "/api/health"):
        return False
    # Webhooks have a workflow-specific token verified by trigger/replay handlers.
    if method == "POST" and path.startswith("/api/workflows/"):
        parts = path.removeprefix("/api/workflows/").split("/")
        if len(parts) == 4 and parts[1] == "webhooks" and parts[3] in {"trigger", "replay"}:
            return False
    return True


def _canonical_path_for_containment(target_path: Path) -> Path:
    path = target_path.expanduser()
    if path.exists() or path.is_symlink():
        return path.resolve(strict=True)

    missing_parts: list[str] = []
    current = path
    while not current.exists() and not current.is_symlink() and current != current.parent:
        missing_parts.insert(0, current.name)
        current = current.parent

    if current.exists() or current.is_symlink():
        parent = current.resolve(strict=True)
    else:
        parent = current.resolve(strict=False)
    return Path(os.path.normpath(parent.joinpath(*missing_parts)))


def _resolve_ui_bundle_path(target_path: Path, data_dir: Path) -> Path:
    expanded = target_path.expanduser()
    if expanded.is_absolute():
        return expanded
    return data_dir / expanded


def _is_path_inside(child: Path, root: Path) -> bool:
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


@contextmanager
def _bundle_path_from_body(body: dict[str, Any]) -> Iterator[Path | None]:
    if body.get("bundleContent"):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_file:
            temp_file.write(base64.b64decode(str(body["bundleContent"])))
            temp_path = Path(temp_file.name)
        try:
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)
        return
    bundle_path = body.get("bundlePath")
    yield Path(str(bundle_path)) if bundle_path else None


def _install_shutdown_handlers(server: GoferUiServer) -> None:
    def request_shutdown(signum: int, _frame: object) -> None:
        log.info("Received signal %s; shutting down Raticode UI server", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, request_shutdown)
        except (OSError, ValueError):
            log.debug("Could not install handler for signal %s", signum)


def serve(host: str = "127.0.0.1", port: int = 8765, data_dir: Path | None = None) -> None:
    server = create_server(host=host, port=port, data_dir=data_dir)
    server.scheduler.start(paused=True)
    server.sync_schedules()
    server.scheduler.resume()
    server.watcher.start()
    server.start_continuous_monitor()
    _install_shutdown_handlers(server)
    print(f"GOFER_UI_READY {json.dumps(ready_payload(server), sort_keys=True)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop_continuous_monitor()
        stop_rattish_runs_for_shutdown(server.data_dir)
        server.watcher.shutdown(wait=False)
        server.scheduler.shutdown(wait=False)
        server.server_close()
        print("GOFER_UI_STOPPED", file=sys.stderr, flush=True)


if __name__ == "__main__":
    serve()
