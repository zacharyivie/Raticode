"""Dispatch durable device requests through the existing backend Rem job owner."""

from __future__ import annotations

import asyncio
import base64
import sqlite3
import tempfile
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol

from gofer.core.resources import ResourceLimits
from gofer.devices.registry import device_id
from gofer.ui.chat import _messages_with_attachment_paths, stream_workflow_chat
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_media import store_chat_attachments
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.device_tools import FleetControl, stream_with_fleet_tools


def validate_context(context: dict[str, Any]) -> dict[str, Any]:
    """Validate a target-local grant before storage and again before execution."""
    if not isinstance(context.get("allow_thread_create", False), bool):
        raise ValueError("invalid_thread_creation_permission")
    if not isinstance(context.get("fleet_execute", False), bool):
        raise ValueError("invalid_fleet_permission")
    title = context.get("title", "Remote Rem")
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 160
        or any(ord(c) < 32 for c in title)
    ):
        raise ValueError("invalid_thread_title")
    if context.get("project_id") is not None:
        device_id(context["project_id"])
    resources = context.get("resource_ids", [])
    if not isinstance(resources, list) or len(resources) > 32:
        raise ValueError("invalid_thread_resources")
    for resource in resources:
        device_id(resource)
    if len(set(resources)) != len(resources):
        raise ValueError("invalid_thread_resources")
    provider = context.get("provider")
    model = context.get("model")
    if not isinstance(provider, str):
        raise ValueError("unsupported_provider_permission")
    from gofer.core.provider_permissions import provider_permission_args

    # The authenticated desktop supplies its existing provider policy. A phone
    # payload never supplies flags, credentials, or filesystem paths.
    mode = context.get("permission_mode") or (
        "workspace-write"
        if provider == "codex"
        else "dontAsk"
        if provider == "claude_code"
        else "cli-managed"
        if provider == "grok"
        else "default"
    )
    try:
        provider_permission_args(provider, mode)
    except ValueError as exc:
        raise ValueError("unsupported_provider_permission") from exc
    if not isinstance(model, str) or not model:
        raise ValueError("missing_local_model")
    project = context.get("project_path")
    if not isinstance(project, str) or not Path(project).is_absolute():
        raise ValueError("missing_local_project")
    working_dir = Path(project).resolve(strict=True)
    if not working_dir.is_dir():
        raise ValueError("missing_local_project")
    metadata = working_dir.stat()
    identity = [metadata.st_dev, metadata.st_ino]
    if "project_identity" in context and (
        context["project_identity"] != identity or str(working_dir) != project
    ):
        raise ValueError("local_project_changed_since_authorization")
    return {
        **context,
        "provider": provider,
        "model": model,
        "permission_mode": mode,
        "project_path": str(working_dir),
        "project_identity": identity,
    }


class DeviceWork(Protocol):
    def claim(self) -> dict[str, Any] | None: ...

    def authorized(self, peer: str, thread: str) -> bool: ...

    def runnable(self, peer: str, request_id: str) -> bool: ...

    def complete(self, peer: str, request_id: str, text: str, error: str | None = None) -> None: ...


class DeviceChatBridge:
    """The application claims once before launch; uncertain starts never retry.

    Context comes only from a target-local grant, not from event payload fields.
    ChatJobs also exclusively creates the stable turn log before spawning a worker.
    """

    def __init__(
        self,
        application: DeviceWork,
        jobs: ChatJobs,
        steering: ChatSteering,
        data_dir: Path,
        resource_limits: ResourceLimits | None = None,
        *,
        source: Callable[..., AsyncIterator[dict[str, Any]]] = stream_workflow_chat,
        fleet_control: FleetControl | None = None,
    ) -> None:
        self.application = application
        self.jobs = jobs
        self.steering = steering
        self.data_dir = data_dir
        self.resource_limits = resource_limits
        self.source = source
        self.fleet_control = fleet_control
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], tuple[str, str]] = {}
        self.error: str | None = None

    async def run(self) -> None:
        while not self._closed.is_set():
            try:
                claimed = self._tick()
                self.error = None
            except (ValueError, OSError, sqlite3.Error):
                self.error = "device_dispatch_storage_unavailable"
                await asyncio.sleep(1)
                continue
            if not claimed:
                await asyncio.sleep(0.25)

    def _tick(self) -> bool:
        with self._lock:
            capacity = len(self._active) < 4
            active = list(self._active.items())
        for (thread, turn), (peer, request_id) in active:
            if not self.application.runnable(peer, request_id):
                try:
                    self.steering.stop(thread, turn)
                except ValueError:
                    pass
        if capacity:
            request = self.application.claim()
            if request is not None:
                self.dispatch(request)
                return True
        return False

    def _complete(self, request: dict[str, Any], text: str, error: str | None) -> None:
        try:
            self.application.complete(
                request["peer"], request["event"]["request_id"], text, error=error
            )
        except (ValueError, OSError, sqlite3.Error):
            # Revoked peers may no longer receive a result. The persisted running
            # boundary remains ambiguous if completion could not be committed.
            pass

    def dispatch(self, request: dict[str, Any]) -> None:
        event = request["event"]
        context = request["context"]
        thread, turn = context.get("desktop_thread_id", event["thread_id"]), request["turn_id"]
        key = (thread, turn)
        with self._lock:
            if key in self._active:
                return
            if self._closed.is_set():
                self._complete(request, "", "backend_stopping")
                return
            self._active[key] = (request["peer"], event["request_id"])

        def run(emit: Callable[[dict[str, Any]], None]) -> None:
            temporary: tempfile.TemporaryDirectory[str] | None = None

            async def consume() -> None:
                nonlocal temporary
                validated = validate_context(context)
                provider, model = validated["provider"], validated["model"]
                mode = validated["permission_mode"]
                working_dir = Path(validated["project_path"])
                messages = request.get("messages") or [
                    {"role": "user", "body": event["payload"]["text"]}
                ]
                messages = [
                    {"role": item["role"], "body": item.get("body", item.get("content", ""))}
                    for item in messages
                ]
                if not self.application.runnable(request["peer"], event["request_id"]):
                    raise ValueError("grant_revoked")
                local_workflow = dict(context.get("workflow") or {})
                local_workflow["projectRoot"] = (
                    ""
                    if (local_workflow.get("remThreads") or {}).get("global")
                    else str(working_dir)
                )
                local_workflow["chatThreadId"] = thread
                files = request.get("attachments") or []
                if files:
                    temporary = tempfile.TemporaryDirectory(
                        prefix="device-attachments-", dir=self.data_dir
                    )
                    attachment_dir = Path(temporary.name)
                    stored = store_chat_attachments(
                        {
                            "threadId": thread,
                            "files": [
                                {
                                    "name": item["name"],
                                    "type": item["mime"],
                                    "data": base64.b64encode(item["data"]).decode("ascii"),
                                }
                                for item in files
                            ],
                        },
                        attachment_dir,
                    )
                    # Existing media validation and attachment reference escaping
                    # apply equally to authenticated device uploads.
                    latest = next(item for item in reversed(messages) if item["role"] == "user")
                    latest["attachments"] = stored["attachments"]
                    messages, _ = _messages_with_attachment_paths(
                        messages, workflow=local_workflow, data_dir=attachment_dir
                    )
                chat_turn = self.steering.begin(
                    thread, turn, provider, model, data_dir=self.data_dir
                )
                terminal = False

                async def provider_source(**options: Any) -> AsyncGenerator[dict[str, Any], None]:
                    original = self.source(**options)
                    try:
                        async for output in original:
                            yield output
                    finally:
                        close = getattr(original, "aclose", None)
                        if close is not None:
                            await close()

                async def fleet_source(**options: Any) -> AsyncGenerator[dict[str, Any], None]:
                    wrapped = stream_with_fleet_tools(
                        provider_source,
                        self.fleet_control,
                        read_only_override=None
                        if context.get("desktop_parity")
                        else context.get("fleet_execute") is not True,
                        file_scope=(request["peer"], event["thread_id"], event["request_id"]),
                        **options,
                    )
                    try:
                        async for output in wrapped:
                            yield output
                    finally:
                        await wrapped.aclose()

                from gofer.ui.rem_threads import stream_with_thread_tools

                async def thread_source(**options: Any) -> AsyncGenerator[dict[str, Any], None]:
                    async for output in stream_with_thread_tools(fleet_source, **options):
                        if output.get("type") == "project-scope":
                            select = getattr(self.application, "select_project", None)
                            if select is not None:
                                select(request["peer"], event["thread_id"], output["projectRoot"])
                        elif output.get("type") == "new-thread":
                            spawn = getattr(self.application, "spawn_thread", None)
                            if spawn is not None:
                                spawn(request["peer"], event["request_id"], output)
                        yield output

                source = self.steering.stream(
                    chat_turn,
                    thread_source,
                    provider=provider,
                    model=model,
                    messages=messages,
                    workflow=local_workflow,
                    effort=context.get("effort"),
                    working_dir=working_dir,
                    permission_mode=mode,
                    data_dir=self.data_dir,
                    resource_limits=self.resource_limits,
                )
                try:
                    async for output in source:
                        kind = output.get("type")
                        if kind == "final":
                            body = output.get("message", {}).get("body", "")
                            if not isinstance(body, str) or len(body.encode()) > 256 * 1024:
                                raise ValueError("response_limit")
                            with self._lock:
                                self._active.pop(key, None)
                            self._complete(request, body, None)
                            terminal = True
                        elif kind in {"error", "stopped"}:
                            with self._lock:
                                self._active.pop(key, None)
                            self._complete(
                                request,
                                "",
                                "provider_stopped" if kind == "stopped" else "provider_failed",
                            )
                            terminal = True
                        # Provider diagnostics remain local. Application response
                        # storage returns only the final text or generic failure.
                        emit(output)
                    if not terminal:
                        self._complete(request, "", "provider_ended_without_result")
                finally:
                    close = getattr(source, "aclose", None)
                    if close is not None:
                        await close()

            try:
                asyncio.run(consume())
            except Exception:
                self._complete(request, "", "dispatch_failed")
                emit({"type": "error", "error": "Remote Rem dispatch failed; inspect desktop"})
            finally:
                if temporary is not None:
                    temporary.cleanup()
                with self._lock:
                    self._active.pop(key, None)

        try:
            self.jobs.start(thread, turn, run)
        except (ValueError, OSError):
            with self._lock:
                self._active.pop(key, None)
            self._complete(request, "", "dispatch_not_started")

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            active = list(self._active)
        for thread, turn in active:
            try:
                self.steering.stop(thread, turn)
            except ValueError:
                pass
