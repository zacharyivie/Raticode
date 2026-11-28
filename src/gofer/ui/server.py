from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import AsyncGenerator, Callable, Coroutine, Iterable, Iterator
from contextlib import aclosing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import ParseResult, parse_qs, urlparse

from gofer.core.command_slots import start_leftover_reclaimer
from gofer.core.provider_auth import ProviderAuthSessions
from gofer.core.provider_capabilities import (
    CLI_PROVIDERS,
    provider_capabilities_payload,
    provider_capability_service,
)
from gofer.core.provider_preferences import (
    commit_message_preference,
    save_commit_message_preference,
    save_provider_preference,
    save_speech_preference,
    save_voice_tidy_preference,
    speech_preference,
    voice_tidy_preference,
)
from gofer.core.resources import ResourceLimits, bundle_resource_limits_from_env
from gofer.core.secrets_folder import CREDENTIALS_DIRECTORY_ENV
from gofer.core.usage_credentials import (
    UsageCredentialError,
    save_usage_credentials,
    usage_credentials_payload,
)
from gofer.core.usage_service import refresh_usage, start_background_refresh, usage_overview
from gofer.rattish.editor import RattishEditorError, RattishRevisionConflict
from gofer.rattish.mcp_tool import McpToolError
from gofer.rattish.test_sets import WorkflowTestError
from gofer.rattish.trigger_login import LoginEntryError
from gofer.rattish.trigger_service import TriggerEngine
from gofer.rattish.triggers import (
    ApprovalConflict,
    TriggerError,
    TriggerNotFound,
    UnseenChanges,
)
from gofer.rattish.workflow_secrets import WorkflowSecretError
from gofer.rattish.workflow_shortcuts import ApprovalConflict as ShortcutApprovalConflict
from gofer.rattish.workflow_shortcuts import (
    ShortcutChanged,
    ShortcutConflict,
    ShortcutError,
    ShortcutNotFound,
)
from gofer.rattish.workflow_shortcuts import UnseenChanges as ShortcutUnseenChanges
from gofer.ui import headless as headless_support
from gofer.ui import (
    rattish_cache,
    rattish_secrets,
    rattish_triggers,
    rem_project_checks,
    system_speech,
    workflow_shortcuts,
    worktree_setup,
)
from gofer.ui.api import (
    DiscoveryCancelled,
    ProviderProfileError,
    WorkflowAlreadyExistsError,
    WorkflowBundleError,
    WorkflowCreateError,
    WorkflowLogError,
    WorkflowPlanError,
    WorkflowRunError,
    WorkflowUpdateError,
    analyze_rattish_document_payload,
    check_provider_profile_payload,
    convert_toml_workflow_payload,
    create_registered_workflow_payload,
    create_workflow_from_starter_payload,
    decide_workflow_approval_payload,
    delete_provider_profile_payload,
    delete_workflow_chat_payload,
    delete_workflow_payload,
    duplicate_workflow_payload,
    export_rattish_bundle_payload,
    health_payload,
    import_rattish_bundle_payload,
    latest_workflow_log_payload,
    list_workflow_approvals_payload,
    list_workflow_payloads,
    list_workflow_run_logs_payload,
    mutate_rattish_document_payload,
    open_project_payload,
    open_rattish_document_payload,
    preview_rattish_bundle_payload,
    provider_profiles_payload,
    prune_workflow_run_logs_payload,
    rename_workflow_payload,
    resume_workflow_payload,
    retention_settings_payload,
    run_triggered_rattish,
    run_workflow_payload,
    save_rattish_document_payload,
    save_rattish_metadata_payload,
    starter_workflows_payload,
    stop_rattish_runs_for_shutdown,
    stop_workflow_run_payload,
    update_retention_settings_payload,
    upsert_provider_profile_payload,
    workflow_plan_payload,
    workflow_run_events_payload,
    workflow_run_log_payload,
    workflow_run_worktrees_payload,
)
from gofer.ui.brain_privacy import (
    AGENT_BRAIN_PRIVATE,
    AGENT_DOCUMENT_LEFT_OUT,
    agent_brain_refusal,
    agent_brain_result,
    agent_left_out_refusal,
    agent_left_out_result,
    agent_snapshot,
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
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_media import (
    CHAT_ATTACHMENT_MAX_TOTAL_BYTES,
    CHAT_AUDIO_CHUNK_MAX_BYTES,
    CHAT_AUDIO_MAX_BYTES,
    ChatMediaError,
    attachment_thread_dir,
    cancel_chat_transcription,
    clean_legacy_speech_models,
    finish_chat_transcription,
    locate_chat_attachment,
    offline_model_status,
    start_chat_transcription,
    start_offline_model_download,
    store_chat_attachments,
    stream_chat_transcription,
    transcribe_chat_audio,
    transcription_engines,
)
from gofer.ui.chat_steering import ChatSteering, ChatSteeringConflict, ChatTurn
from gofer.ui.file_mentions import with_file_mentions, with_thread_linked_files
from gofer.ui.generation_jobs import GenerationJobs
from gofer.ui.headless import (
    AGENT_SECOND_BRAIN_ACTIONS,
    AgentGrant,
    AgentTokenStore,
    agent_request_refusal,
)
from gofer.ui.link_previews import LinkPreviews, PreviewError, chat_image
from gofer.ui.organization_auto import rem_reviewer
from gofer.ui.organization_operations import READ_OPERATIONS
from gofer.ui.organization_packages import read_directory
from gofer.ui.organization_store import OrganizationConflict
from gofer.ui.organizations import OrganizationManager
from gofer.ui.rem_decisions import DecisionRefused
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import RemConflict
from gofer.ui.rem_kanban import KanbanRefused, message_ticket_keys, task_ticket_keys
from gofer.ui.rem_objectives import ObjectiveRefused
from gofer.ui.rem_thread_listing import desktop_listing
from gofer.ui.routes import ANSWERED, ErrorMap, Mount, RouteRequest, RouteTable
from gofer.ui.run_failure import RunFailureError
from gofer.ui.swarms import SwarmManager
from gofer.ui.temp_cleanup import start_temp_cleanup
from gofer.ui.workflow_tests import (
    estimate_workflow_tests_payload,
    save_workflow_tests_payload,
    start_workflow_tests_payload,
    stop_all_workflow_tests,
    stop_workflow_tests_payload,
    workflow_test_result_payload,
    workflow_tests_payload,
)
from gofer.utils.logging import configure_backend_logging, get_logger, reset_backend_logging
from gofer.utils.paths import get_data_dir, restrict_to_owner
from gofer.utils.process import seal_credential_env

log = get_logger(__name__)
PATH_GRANT_TTL_SECONDS = 15 * 60
UI_MAX_HANDLERS = 32
UI_MAX_EXPENSIVE_REQUESTS = 8
UI_READ_TIMEOUT_SECONDS = 15.0
UI_REQUEST_READ_DEADLINE_SECONDS = 30.0
# How long the browser may reuse a CORS preflight answer for one address.
CORS_PREFLIGHT_MAX_AGE_SECONDS = 600
UI_JSON_MAX_DEPTH = 64
UI_JOB_DEADLINE_SECONDS = 24 * 60 * 60
COMMIT_MESSAGE_MAX_BODY_BYTES = 2 * 1024 * 1024
# A Rem message may carry one screenshot of up to 5 MB, sent as base64.
REM_MESSAGE_MAX_BODY_BYTES = 8 * 1024 * 1024
DEFAULT_DEV_FRONTEND_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}

# Readable reasons for device refusals the desktop window acts on.
DEVICE_REFUSAL_MESSAGES = {
    "workspace_history_quota": (
        "The phone's share of desktop thread history is full (64 MB per phone). Threads "
        "that don't fit stay off the phone; the others keep syncing."
    ),
    "workspace_thread_too_large": (
        "A thread is too long to share with the phone (8 MB or 10,000 messages per "
        "thread). It stays off the phone; the others keep syncing."
    ),
}


class RepeatedNotice:
    """Say when each kind of event first happens, then again at most once per window."""

    def __init__(self, window_seconds: float) -> None:
        self.window = window_seconds
        self.seen: dict[str, float] = {}
        self.lock = threading.Lock()

    def first(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            last = self.seen.get(key)
            if last is not None and now - last < self.window:
                return False
            if len(self.seen) > 256:
                self.seen.clear()
            self.seen[key] = now
            return True


_device_refusals = RepeatedNotice(10 * 60)


def _device_provider_catalog() -> list[dict[str, Any]]:
    """Enabled desktop providers as the phone's model catalog. No credentials or paths."""
    return [
        {
            "id": p["id"],
            "permission_modes": [mode["id"] for mode in p.get("permissionModes", [])],
            "default_permission": p.get("defaultPermissionMode", "default"),
            "efforts": {
                m["id"]: [e["id"] for e in m.get("efforts", [])] for m in p.get("models", [])[:100]
            },
            "models": list(
                dict.fromkeys([m["id"] for m in p.get("models", []) if m.get("listed", True)])
            )[:100],
        }
        for p in provider_capabilities_payload()["providers"]
        if p.get("available") and p.get("enabled")
    ][:32]


def _optional_query(query: dict[str, list[str]], name: str) -> str | None:
    value = query.get(name, [None])[0]
    return value if value not in {None, ""} else None


def _usage_with_accounts(overview: dict[str, Any], data_dir: Path, rem_home: Any) -> dict[str, Any]:
    """Settings > Usage cards: account sign-in states and Rem's next account under "auto"."""
    from gofer.core.provider_accounts import adapters
    from gofer.core.usage_accounts import with_account_glance
    from gofer.ui.rem_claude_accounts import usage_next_account

    pick = None
    picks: dict[str, dict[str, Any] | None] = {}
    if rem_home is not None:
        try:
            config = rem_home._current_config()
            pick = usage_next_account(config, data_dir)
            for provider in adapters():
                if provider != "claude_code":
                    picks[provider] = usage_next_account(config, data_dir, provider=provider)
        except Exception as exc:  # The cards still show without the badge.
            log.info("Could not work out Rem's next account (%s)", type(exc).__name__)
    return with_account_glance(overview, data_dir, rem_pick=pick, rem_picks=picks)


def _usage_period(query: dict[str, list[str]]) -> dict[str, Any]:
    """Settings > Usage period options: range (or the older days), start, end and tz."""
    return {
        "period": _optional_query(query, "range"),
        "days": _optional_query(query, "days"),
        "start": _optional_query(query, "start"),
        "end": _optional_query(query, "end"),
        "tz": _optional_query(query, "tz"),
    }


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
    if value is None or value == "":
        return None
    if type(value) not in {str, int}:
        raise ValueError(f"{name} must be an integer")
    return int(str(value))


def _optional_body_str(body: dict[str, Any], name: str) -> str | None:
    value = body.get(name)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _api_started_by(body: dict[str, Any]) -> Literal["manual", "rem"]:
    """How an API caller says it started a run: ``rem`` from Rem's sessions, else manual.

    The phone and triggers start runs inside the backend and say so there; an API
    caller can't claim to be either.
    """
    value = body.get("startedBy")
    if value is None or value == "manual":
        return "manual"
    if value == "rem":
        return "rem"
    raise WorkflowRunError("startedBy must be manual or rem")


def _run_changes_workflow_id(request: RouteRequest) -> str | None:
    """The workflowId a land or discard names in its body, else in its query."""
    workflow_id = (request.body or {}).get("workflowId") or _optional_query(
        request.query, "workflowId"
    )
    if workflow_id is not None and not isinstance(workflow_id, str):
        raise WorkflowRunError("workflowId must be a string")
    return workflow_id


def _finite_json_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Request body numbers must be finite")
    return number


def rem_poll_result(
    result: dict[str, Any], known: str, keep: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Tag a view Rem's clients poll with a content version.

    A client that sends the version it already has gets ``unchanged`` back, plus
    the ``keep`` fields, instead of the whole view. Polls while nothing happens
    then cost no download, no parsing and no re-render.
    """
    version = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]
    if known == version:
        return {"unchanged": True, "version": version} | {k: result[k] for k in keep if k in result}
    return {**result, "version": version}


@dataclass(frozen=True)
class DesktopPathGrant:
    root: Path
    expires_at: float


class DesktopPathGrantStore:
    def __init__(self) -> None:
        self._grants: dict[str, DesktopPathGrant] = {}
        self._lock = threading.Lock()

    def register(self, root: Path, grant_id: str | None = None, *, persistent: bool = False) -> str:
        """Grant access below [root]. Desktop grants expire unless renewed; a
        ``persistent`` grant is the backend's own, for a project Rem opened."""
        grant_id = grant_id or str(uuid.uuid4())
        grant = DesktopPathGrant(
            root=_canonical_path_for_containment(root),
            expires_at=math.inf if persistent else time.monotonic() + PATH_GRANT_TTL_SECONDS,
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


def library_brain(server: Any, config: Any) -> Any:
    """A folder-only Second Brain setting, served by the library brain registered there.

    See second_brain.library_brain_config. The folder's old SecondBrain index is deleted
    once the library serves it, since nothing writes it any more. Takes any server-like
    object, so test doubles without a library get the setting back unchanged.
    """
    from gofer.ui.second_brain import (
        MEMORY_FOLDER_KEY,
        library_brain_config,
        retire_folder_index,
    )

    library = getattr(server, "second_brains", None)
    data_dir = getattr(server, "data_dir", None)
    if library is None or data_dir is None:
        return config
    filled = library_brain_config(config, library.brains(), Path(data_dir))
    if isinstance(filled, dict) and filled.get(MEMORY_FOLDER_KEY):
        retire_folder_index(Path(filled["root"]))
    return filled


def with_library_brain(server: Any, options: dict[str, Any]) -> dict[str, Any]:
    """Turn options whose Second Brain names only a folder use the library brain there.

    Organization runs and Rem tasks take their setting from stored configuration, which
    keeps the folder as the user saved it; this fills it in for one run.
    """
    workflow = options.get("workflow")
    if not isinstance(workflow, dict) or "remSecondBrain" not in workflow:
        return options
    brain = library_brain(server, workflow["remSecondBrain"])
    return {**options, "workflow": {**workflow, "remSecondBrain": brain}}


class GoferUiServer(ThreadingHTTPServer):
    _social_service: Any = None

    @property
    def social(self) -> Any:
        """The social posts of this data folder; the publisher runs in the trigger owner."""
        if self._social_service is None:
            from gofer.social.service import SocialService
            from gofer.ui.rem_kanban import open_project_roots

            def media_roots() -> list[Path]:
                return [Path(root) for root in open_project_roots(self.rem_home._current_config())]

            self._social_service = SocialService(self.data_dir, media_roots=media_roots)
        return self._social_service

    async def _rem_task_source(self, **options: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Run a Rem task with the fleet status tools desktop threads have.

        Sending work to another desktop would delegate the task again, so Rem
        tasks only read fleet status, like a read-only desktop thread. Self-work
        runs with nobody watching get neither fleet nor Organizations tools. Every Rem
        task gets the propose-only ``social`` tools; proposals wait for the user.
        """
        from gofer.social.mcp import stream_with_social_tools
        from gofer.ui.device_tools import stream_with_fleet_tools

        options = with_library_brain(self, options)
        if (options.get("workflow") or {}).get("remUnattended") is True:
            # Self-work may propose posts too; they wait for the user like any other.
            async with aclosing(
                stream_with_social_tools(
                    stream_workflow_chat, getattr(self, "social", None), **options
                )
            ) as stream:
                async for event in stream:
                    yield event
            return

        async def fleet_source(**fleet_options: Any) -> AsyncGenerator[dict[str, Any], None]:
            async with aclosing(
                stream_with_fleet_tools(
                    self._device_chat_source,
                    self.devices,
                    read_only_override=True,
                    **fleet_options,
                )
            ) as fleet_stream:
                async for event in fleet_stream:
                    yield event

        async with aclosing(
            stream_with_social_tools(fleet_source, getattr(self, "social", None), **options)
        ) as stream:
            async for event in stream:
                yield event

    async def _device_chat_source(self, **options: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Use the desktop's provider, resource and swarm services for shared turns."""
        options = with_library_brain(self, options)
        workflow = dict(options.get("workflow") or {})
        if workflow.get("organizationRun"):
            async with aclosing(stream_workflow_chat(**options)) as stream:
                async for event in stream:
                    yield event
            return
        config = workflow.get("remSwarmAccess") or {}
        project = workflow.get("projectRoot")
        if config.get("enabled") is not True or not project:
            async with aclosing(
                self.organizations.rem_stream(stream_workflow_chat, **options)
            ) as stream:
                async for event in stream:
                    yield event
            return
        root = Path(project).resolve(strict=True)
        if not root.is_relative_to(
            self.data_dir.resolve()
        ) and not self.path_grants.covers_canonical(root, str(config.get("grantId") or "")):
            raise ValueError("desktop_project_grant_expired")
        with self.swarms.rem_session(
            root,
            read_only=options.get("permission_mode") in {"read-only", "plan"}
            or (workflow.get("remManagedTask") or {}).get("readOnly") is True,
            workspace_grants=config.get("workspaceGrants") or {},
        ) as url:
            resources = dict(workflow.get("remResources") or {})
            resources["mcpServers"] = [
                item for item in resources.get("mcpServers", []) if item.get("name") != "swarm"
            ] + [{"name": "swarm", "type": "http", "url": url}]
            workflow["remResources"] = resources
            async with aclosing(
                self.organizations.rem_stream(
                    stream_workflow_chat,
                    **{**options, "workflow": workflow, "trusted_swarm_url": url},
                )
            ) as stream:
                async for event in stream:
                    yield event

    def __init__(
        self,
        server_address: tuple[str, int],
        data_dir: Path,
        resource_limits: ResourceLimits | None = None,
        api_token: str | None = None,
        allowed_origins: set[str] | None = None,
    ) -> None:
        # Writers open each folder without following links, so a link above the data
        # folder (such as /home -> var/home) is followed once here.
        data_dir = data_dir.expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        # Transcripts and databases below it may quote credentials; other accounts on
        # this computer must not read them.
        restrict_to_owner(data_dir)
        super().__init__(server_address, GoferUiRequestHandler)
        self.data_dir = data_dir
        from gofer.ui.brain_library import BrainLibrary

        self.second_brains = BrainLibrary(data_dir)
        self.resource_limits = resource_limits or bundle_resource_limits_from_env()
        self.chat_steering = ChatSteering(data_dir)
        self.chat_jobs = ChatJobs(data_dir, UI_MAX_EXPENSIVE_REQUESTS)
        self.generation_jobs = GenerationJobs(data_dir)
        self.provider_auth = ProviderAuthSessions()
        # Optional device dependencies must not prevent normal local UI startup.
        self.devices: Any = None
        try:
            from gofer.devices.control import DeviceControl

            self.devices = DeviceControl(data_dir)
        except ImportError:
            pass
        self.link_previews = LinkPreviews()
        self.api_token = api_token or os.environ.get("GOFER_UI_API_TOKEN") or _new_ui_api_token()
        # Limited tokens for scripts and agents; see gofer.ui.headless.
        self.agent_tokens = AgentTokenStore(data_dir)
        self.headless = False
        self.emit_ready_token = os.environ.get("GOFER_UI_EMIT_READY_TOKEN") == "1"
        self.allowed_origins = (
            _default_allowed_origins() if allowed_origins is None else set(allowed_origins)
        )
        self._handler_slots = threading.BoundedSemaphore(UI_MAX_HANDLERS)
        self._expensive_slots = threading.BoundedSemaphore(UI_MAX_EXPENSIVE_REQUESTS)
        self.request_job_timeout = max(
            1.0, float(os.environ.get("GOFER_UI_JOB_TIMEOUT_SECONDS", UI_JOB_DEADLINE_SECONDS))
        )
        self.path_grant_secret = os.environ.get("GOFER_DESKTOP_GRANT_SECRET", "")
        self.path_grants = DesktopPathGrantStore()
        self.gofer_cli_path = ensure_local_gofer_cli(data_dir)
        self.swarms = SwarmManager(
            data_dir,
            stream=self._swarm_chat_source,
            resource_limits=self.resource_limits,
            authorize_workspace=self._authorize_swarm_workspace,
            max_concurrency=int(os.environ.get("GOFER_SWARM_MAX_CONCURRENCY", "8")),
        )

        from gofer.core.worker_claims import WorkerClaims

        # Every worker that starts on this computer, Rem's, a phone turn's or an
        # organization's, holds a claim here, taken under one lock against the limit.
        self.worker_claims = WorkerClaims()
        self.organizations = OrganizationManager(
            data_dir,
            stream=self._employee_chat_source,
            swarms=self.swarms,
            fleet=self.devices,
            enabled=os.environ.get("RATICODE_EXPERIMENTAL_ORGANIZATIONS") == "1",
        )
        self.organizations.placement.claims = self.worker_claims
        # Swarm turns take their workers from the same claims, on this computer.
        self.swarms.claims = self.worker_claims
        self.swarms.computer = lambda: (self._this_computer_id(), self._computer_name())

        if self.devices is not None:
            self.devices.worker_claims = self.worker_claims
            self.devices.computer_id = self._this_computer_id
            self.devices.attach_services(
                providers=_device_provider_catalog,
                swarms=self.swarms,
                data_dir=data_dir,
                decide_workflow=decide_workflow_approval_payload,
            )
            self.devices.attach_chat(
                self.chat_jobs,
                self.chat_steering,
                data_dir,
                self.resource_limits,
                source=self._device_chat_source,
            )

        self.rem_home = RemHome(
            data_dir,
            source=self._rem_task_source,
            jobs=self.chat_jobs,
            steering=self.chat_steering,
            authorize=self._authorize_rem_home,
            context=self._rem_home_context,
            # Rem opens project folders the user's request needs (rem_projects).
            grant=lambda root, grant_id: self.path_grants.register(root, grant_id, persistent=True),
        )
        # Reports Rem tasks save into a brain get a report record with how they were made.
        self.rem_home.brain_library = self.second_brains
        # The global Kanban shows every organization's tickets beside Rem's own.
        self.rem_home.organizations = self.organizations
        # Organizations in Auto mode ask Rem's own model to decide their requests.
        self.organizations.auto_review = rem_reviewer(self.rem_home._current_config, data_dir)
        organizations = self.organizations
        self.rem_home.placement.claims = self.worker_claims
        # The Second Brain shows which organization tickets read or quoted a note.
        self.second_brains.organization_source = lambda: (
            organizations.store.list_organizations() if organizations.enabled else []
        )
        from gofer.core.computers import ComputerService

        # This computer and its paired desktops, each with its worker limit and load.
        self.computers = ComputerService(
            data_dir,
            name=self._computer_name,
            device_id=self._computer_device_id,
            running=self._running_workers,
            running_work=lambda: self.worker_claims.running_work(),
            fleet=self._computer_fleet,
            organizations=lambda: organizations.store.names() if organizations.enabled else [],
        )
        # Each Rem run's start, and each organization worker's, is placed on a computer
        # from this registry.
        self.rem_home.placement.computers = self.computers
        self.organizations.placement.computers = self.computers
        # A research or conversation task pinned to a paired desktop runs there, and
        # tasks paired desktops hand here run as this computer's own Rem tasks.
        self.rem_home.computer_id = self._this_computer_id
        self.rem_home.remote.fleet = self.devices
        if self.devices is not None:
            self.devices.attach_desktop(
                rem=self.rem_home,
                organizations=self.organizations,
                computers=self.computers,
                usage=lambda **period: _usage_with_accounts(
                    usage_overview(data_dir, **period), data_dir, self.rem_home
                ),
                social=self.social,
            )

    def _computer_name(self) -> str:
        application = getattr(self.devices, "application", None)
        name = getattr(application, "name", None)
        if isinstance(name, str) and name:
            return name
        import socket

        return socket.gethostname()[:80] or "This computer"

    def _computer_device_id(self) -> str | None:
        registry = getattr(self.devices, "registry", None)
        identity = getattr(registry, "identity", None)
        value = getattr(identity, "device_id", None)
        return value if isinstance(value, str) else None

    def _computer_fleet(self) -> list[dict[str, Any]]:
        application = getattr(self.devices, "application", None)
        return list(application.fleet()) if application is not None else []

    def _this_computer_id(self) -> str:
        from gofer.core.computers import this_computer_id

        return this_computer_id(self.data_dir, self._computer_device_id())

    def _running_workers(self) -> dict[str, int]:
        """Workers running on this computer now: Rem tasks, phone turns, organizations and swarms.

        Counted from the worker claims every start takes. Paired desktops run nothing
        from here yet, so every claim is a worker on this computer.
        """
        return self.worker_claims.running()

    async def _swarm_chat_source(self, **options: Any) -> AsyncGenerator[dict[str, Any], None]:
        from gofer.ui.organization_operations import resolve_secrets

        workflow = dict(options.get("workflow") or {})
        scope = workflow.get("organizationRun")
        if scope:
            org = self.organizations.store.get("", scope["organizationId"])
            if org["runtime"]["state"] != "running" or not any(
                t["claim"] == scope["parentRunId"] for t in org["runtime"]["tasks"]
            ):
                raise ValueError("Organization revoked this swarm run")
            employee = next(e for e in org["config"]["employees"] if e["id"] == scope["employeeId"])
            resources = copy.deepcopy(workflow.get("remResources") or {})
            values = resolve_secrets(org, employee, resources)
            with self.organizations._lock:
                self.organizations._secret_values.setdefault(scope["parentRunId"], []).extend(
                    values
                )
            workflow["remResources"] = resources
        async with aclosing(stream_workflow_chat(**{**options, "workflow": workflow})) as stream:
            async for event in stream:
                yield self.organizations._clean(event) if scope else event

    async def _employee_chat_source(self, **options: Any) -> AsyncGenerator[dict[str, Any], None]:
        from gofer.ui.device_tools import stream_with_fleet_tools
        from gofer.ui.rem_threads import stream_with_thread_tools

        # The organization's knowledge folder is searched through its library brain.
        options = with_library_brain(self, options)
        workflow = dict(options["workflow"])
        project = Path(workflow["projectRoot"])
        # Kept in the workflow: a Codex turn may write the ones inside the data folder
        # (chat._codex_data_writes).
        workspace_paths = workflow.get("organizationWorkspacePaths", [str(project)])
        workspace_grants = {}
        for value in workspace_paths:
            path = Path(value)
            if not path.is_dir() or str(path.resolve()) != value:
                raise ValueError("An organization workspace moved or is no longer available")
            workspace_grants[value] = self.path_grants.register(path)
        from gofer.ui.brain_privacy import turn_private_brains

        # Workers reach every workspace through their thread and swarm tools, so none may
        # be or sit inside a private brain. The turn itself hides or refuses brains
        # inside its own project (chat._private_brains_in_turn).
        knowledge = workflow.get("remSecondBrain") or {}
        library = options.get("library_data_dir") or getattr(self, "data_dir", None)
        if library is not None:
            turn_private_brains(
                workspace_paths,
                provider={
                    "id": options.get("provider"),
                    "profile": options.get("provider_profile"),
                },
                data_dir=Path(library),
                approved=[knowledge.get("root")] if knowledge.get("enabled") else [],
            )
        with self.swarms.rem_session(
            project,
            read_only=True,
            workspace_grants=workspace_grants,
        ) as url:
            resources = dict(workflow.get("remResources") or {})
            resources["mcpServers"] = [
                s for s in resources.get("mcpServers", []) if s["name"] != "swarm"
            ] + [{"name": "swarm", "type": "http", "url": url}]
            workflow["remResources"] = resources
            workflow["remThreads"] = {
                "projects": [
                    {"root": value, "name": Path(value).name, "grantId": grant}
                    for value, grant in workspace_grants.items()
                ]
            }

            async def fleet_source(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
                async with aclosing(
                    stream_with_fleet_tools(
                        stream_workflow_chat, self.devices, read_only_override=True, **kwargs
                    )
                ) as stream:
                    async for event in stream:
                        yield event

            async with aclosing(
                stream_with_thread_tools(
                    fleet_source,
                    **{
                        **options,
                        "workflow": workflow,
                        "trusted_swarm_url": url,
                        "resource_limits": self.resource_limits,
                    },
                )
            ) as stream:
                async for event in stream:
                    yield event

    def _authorize_swarm_workspace(self, path: Path, grant_id: str | None) -> None:
        canonical = _canonical_path_for_containment(path)
        if not (
            _is_path_inside(canonical, _canonical_path_for_containment(self.data_dir))
            or self.path_grants.covers_canonical(canonical, str(grant_id or ""))
        ):
            raise WorkflowBundleError(
                "Agent workspace is outside the approved Raticode desktop roots. "
                "Open the project and select it again."
            )

    def _private_rem_projects(
        self, config: dict[str, Any], recipe: dict[str, Any], roots: Iterable[str]
    ) -> dict[str, str]:
        """The given Rem project folders a cloud turn may not open, each with the reason.

        Each is, or sits inside, a brain with Allow cloud processing off. A provider on
        localhost may open them all. Brains inside a project aren't counted here: each
        turn hides or refuses those itself (chat._private_brains_in_turn).
        """
        from gofer.ui.brain_privacy import (
            PrivateBrainError,
            memory_folder,
            private_brain_folders,
            provider_is_local,
            registered_brains,
        )

        provider = {"id": recipe.get("provider"), "profile": recipe.get("provider_profile")}
        checked = [root for root in roots if root and os.path.lexists(root)]
        if not checked or provider_is_local(provider, self.data_dir):
            return {}
        brains = registered_brains(self.data_dir)
        brain = config.get("brain") or {}
        approved = [
            brain.get("root") if isinstance(brain, dict) and brain.get("enabled") else None,
            memory_folder(self.data_dir),
        ]
        found: dict[str, str] = {}
        for root in checked:
            try:
                private_brain_folders([root], brains, approved=approved)
            except PrivateBrainError as exc:
                found[root] = str(exc)
        return found

    def _authorize_rem_home(self, config: dict[str, Any], recipe: dict[str, Any]) -> None:
        from gofer.ui.brain_privacy import PrivateBrainError

        projects = config.get("projects", [])
        if not isinstance(projects, list) or len(projects) > 100:
            raise ValueError("Choose at most 100 Rem projects")
        project_dir = recipe.get("project_dir")
        brain = config.get("brain") or {}
        if not isinstance(brain, dict):
            raise ValueError("Invalid Second Brain configuration")
        # Rem's tasks run on cloud providers, which may not open a private brain's
        # folder, so a task whose own folder is or sits inside one is refused. Any other
        # open project that became private is skipped, the same as a deleted one: it
        # must not stop Rem's chat or unrelated tasks. Rem's context leaves it out, and
        # the window context drops it with the reason (_private_rem_projects).
        if recipe and project_dir:
            reason = self._private_rem_projects(config, recipe, [project_dir]).get(project_dir)
            if reason:
                raise PrivateBrainError(reason)
        for project in projects:
            if not isinstance(project, dict) or not isinstance(project.get("root"), str):
                raise ValueError("Invalid Rem project")
            root = Path(project["root"])
            if not root.is_absolute():
                raise ValueError("Rem projects must be canonical existing directories")
            if not os.path.lexists(root):
                # An open project folder that was deleted, such as a worktree
                # removed while it was open, grants nothing. Only the task's own
                # folder matters; Rem drops vanished folders on the next window
                # context update. Rejecting the whole list here used to stop
                # every running task, including the one that removed the folder.
                if project["root"] == project_dir:
                    raise ValueError(f"This task's project folder no longer exists: {root}")
                continue
            if not root.is_dir() or str(root.resolve()) != str(root):
                raise ValueError("Rem projects must be canonical existing directories")
            self._authorize_swarm_workspace(root, project.get("grantId"))
        if project_dir and not any(p["root"] == project_dir for p in projects):
            raise ValueError("Select an approved project for this task")
        if brain.get("enabled"):
            root = Path(str(brain.get("root", "")))
            if not root.is_absolute() or not root.is_dir():
                raise ValueError("Select an existing Second Brain")
            self._authorize_swarm_workspace(root, brain.get("grantId"))
            for identity in brain.get("brainIds", []):
                registered = self.second_brains.brain(identity)
                if not registered["settings"].get("allowCloud"):
                    raise ValueError("Second Brain cloud access was revoked")
                self._authorize_swarm_workspace(
                    Path(registered["root"]),
                    (brain.get("workspaceGrants") or {}).get(registered["root"]),
                )
        if recipe.get("type") == "research" and not project_dir and not brain.get("enabled"):
            raise ValueError(
                "Research needs an approved project or enabled Second Brain destination"
            )

    def _rem_home_context(self, query: str = "") -> dict[str, Any]:
        """What Rem sees of the desktop, with up to 5 Second Brain notes matching ``query``.

        Each note is its path, title, summary and matched passage, the list capped at
        CONTEXT_KNOWLEDGE_CHARS; the chat's note lookup reads the rest of one.
        """
        with self.rem_home.store.connect() as db:
            config = self.rem_home.store.setting(db, "config")
        self._authorize_rem_home(config, {})
        roots = {p["root"] for p in config.get("projects", [])}
        # A project that became a private brain stays out of what Rem sees.
        roots -= set(self._private_rem_projects(config, {}, sorted(roots)))
        workflows = [
            {key: workflow.get(key) for key in ("id", "name", "projectRoot", "status", "latestRun")}
            for workflow in list_workflow_payloads(self.data_dir).get("workflows", [])
            if workflow.get("projectRoot") in roots
        ]
        organizations = [
            item
            for item in self.organizations.store.summaries()
            if not item.get("projectRoots") or roots.intersection(item["projectRoots"])
        ]
        notes: list[dict[str, Any]] = []
        brain = config.get("brain") or {}
        if brain.get("enabled") and brain.get("root"):
            from gofer.ui.second_brain import (
                SecondBrain,
                add_context_note,
                document_context_note,
                raw_context_note,
            )

            # The Settings > Memory folder is searched through its library brain.
            brain = library_brain(self, brain)
            if brain.get("brainIds"):
                hits = self.second_brains.search(
                    {
                        "brainIds": brain["brainIds"],
                        "query": query,
                        "mode": "exact",
                    }
                )["hits"]
                seen: set[str] = set()
                for hit in hits[:10]:
                    if len(notes) == 5 or hit["docId"] in seen:
                        continue
                    seen.add(hit["docId"])
                    try:
                        # The indexed passages, starting where the search matched.
                        doc = self.second_brains.entity("documents", hit["docId"])
                        if doc.get("excluded") or doc.get("missing"):
                            continue
                        root = self.second_brains.brain(doc["brainId"])["root"]
                        build = partial(
                            document_context_note, doc, root, hit.get("blockId"), query=query
                        )
                        if not add_context_note(notes, build):
                            break
                    except (ValueError, OSError):
                        continue
            else:
                root = Path(brain["root"])
                for hit in SecondBrain(root, "html").search(query)[:3]:
                    try:
                        build = partial(raw_context_note, root, hit["path"], query=query)
                        if not add_context_note(notes, build):
                            break
                    except (ValueError, OSError):
                        continue
        return {
            "at": datetime.now(UTC).isoformat(),
            "workflows": workflows,
            "swarms": [s for root in roots for s in self.swarms.list(root)],
            "organizations": organizations,
            "running_threads": self.chat_jobs.active_snapshot(),
            "brains": {
                "enabled": bool(config.get("brain", {}).get("enabled")),
                "ids": config.get("brain", {}).get("brainIds", []),
            },
            # The newest threads and a count; Rem's threads lookup finds the rest.
            "desktop": desktop_listing(config.get("workspace_snapshot", {})),
            "knowledge": notes,
        }

    def server_close(self) -> None:
        rem_home = getattr(self, "rem_home", None)
        if rem_home is not None:
            rem_home.close()
        second_brains = getattr(self, "second_brains", None)
        if second_brains is not None:
            second_brains.close()
        generation_jobs = getattr(self, "generation_jobs", None)
        if generation_jobs is not None:
            generation_jobs.close()
        organizations = getattr(self, "organizations", None)
        if organizations is not None:
            organizations.close()
        devices = getattr(self, "devices", None)
        if devices is not None:
            devices.close()
        auth = getattr(self, "provider_auth", None)
        if auth is not None:
            auth.close()
        # Account sign-ins run in their own session and would outlive the backend,
        # keeping their one-time code and link in the account's scratch folder.
        from gofer.core.provider_accounts import sign_ins

        sign_ins.cancel_all()
        jobs = getattr(self, "chat_jobs", None)
        if jobs is not None:
            jobs.close()
        steering = getattr(self, "chat_steering", None)
        if steering is not None:
            steering.close()
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


# Routes answered from the table (gofer.ui.routes). _dispatch_request runs it only after
# the host, origin, auth, body framing and expensive-slot checks. Each Mount keeps the
# answer its old if-chain gave a request no route matches, and maps its handlers'
# exceptions to answers in the order the old except clauses checked them.
ROUTES = RouteTable()
ROUTES.mount(
    Mount(
        "/api/rattish/triggers",
        miss=(405, "Method not allowed"),
        errors=(
            (TriggerNotFound, 404),
            (ApprovalConflict, "_answer_approval_conflict"),
            (UnseenChanges, "_answer_unseen_changes"),
            (TriggerError, 400),
            (LoginEntryError, 500),
        ),
    )
)
ROUTES.mount(
    Mount(
        "/api/rattish/secrets",
        miss=(405, "Method not allowed"),
        # The body may hold the secret's value, so a parser's message is never echoed.
        errors=((WorkflowSecretError, 400), (ValueError, "_answer_unreadable_secret")),
    )
)
ROUTES.mount(
    Mount(
        "/api/workflow-shortcuts",
        miss=(404, "Unknown workflow shortcut request"),
        errors=(
            (ShortcutNotFound, 404),
            (ShortcutChanged, "_answer_shortcut_changed"),
            (ShortcutApprovalConflict, "_answer_approval_conflict"),
            (ShortcutUnseenChanges, "_answer_unseen_changes"),
            (ShortcutConflict, 409),
            (ShortcutError, 400),
            (WorkflowRunError, 400),
        ),
    )
)
# Every workflow error is a ValueError, which the central handler answers with 400 and
# its message, so a workflow route names only the errors it answers another way.
ROUTES.mount(Mount("/api/workflows", miss=(404, "Not found")))
_WORKFLOW_EXISTS: ErrorMap = ((WorkflowAlreadyExistsError, 409),)
_WORKFLOW_MISSING: ErrorMap = ((WorkflowUpdateError, 404),)
_REVISION_CONFLICT: ErrorMap = ((RattishRevisionConflict, "_answer_revision_conflict"),)
_RUN_LOG_MISSING: ErrorMap = ((WorkflowLogError, 404),)
_TESTS_MISSING: ErrorMap = ((WorkflowTestError, 404),)
ROUTES.mount(Mount("/api/worktree-setup", miss=(404, "Unknown worktree setup request")))
ROUTES.mount(
    Mount(
        "/api/rem",
        miss=(404, "Unknown Rem endpoint"),
        errors=((KeyError, "_answer_rem_record_missing"),),
    )
)
# Inside /api/rem: a kanban request no route matches got the missing-record answer.
ROUTES.mount(
    Mount(
        "/api/rem/kanban",
        miss=(404, "Rem record was not found"),
        errors=((KanbanRefused, "_answer_refused"), (KeyError, "_answer_rem_record_missing")),
    )
)
# Rem's chat: turns, streams, steering, speech, attachments and change undo. Each route
# maps its own errors, as its if-block did; the slot check for POST stays in
# _dispatch_request.
ROUTES.mount(Mount("/api/chat", miss=(404, "Not found")))
# The MCP tool picker only reads; its old dispatcher answered other methods with 405.
ROUTES.mount(Mount("/api/rem-resources/mcp-tools", miss=(405, "Method not allowed")))
# A routine's webhook URL names one organization and one webhook.
ROUTES.mount(Mount("/api/organization-webhooks", miss=(400, "Invalid organization webhook URL")))
# Settings, devices, usage, generation jobs and Rattish bundles have no Mount: a request
# under their paths that no route matches gets _dispatch_request's 404 "Not found".
_JOB_NOT_STARTED: ErrorMap = (
    (ValueError, "_answer_job_not_started"),
    (OSError, "_answer_job_not_started"),
    (subprocess.SubprocessError, "_answer_job_not_started"),
)
_ACCOUNT_REFUSED: ErrorMap = ((OSError, 400),)  # AccountError is a ValueError
_JEV_REFUSED: ErrorMap = ((ValueError, "_answer_jev_refused"),)


class RemChatPageGone(Exception):
    """GET /api/rem/history asked for messages the store no longer keeps."""


class WebhookRefused(Exception):
    """An organization webhook delivery was refused: organizations are off, or receive
    rejected it (signature, timestamp, rate or a disabled routine)."""


class GoferUiRequestHandler(BaseHTTPRequestHandler):
    # Set per request when it carries a limited agent token instead of the owner's.
    _agent_token_name: str | None = None
    # Whether that agent token may read brains with Allow cloud processing off.
    _agent_private_brains: bool = False

    def _check_discovery_cancelled(self) -> None:
        """Observe a fetch abort without consuming any HTTP request bytes."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            disconnected = bool(readable) and not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:
            disconnected = True
        if disconnected:
            raise DiscoveryCancelled()

    server: GoferUiServer
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

    def do_PATCH(self) -> None:
        self._dispatch_request("PATCH")

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
        if parsed.path in {"/api/chat/steer", "/api/chat/stop"}:
            expensive = False
        acquired = False
        if expensive and slots is not None:
            acquired = slots.acquire(blocking=False)
            if not acquired:
                self._send_json({"error": "Too many active requests; retry shortly"}, status=503)
                return
        try:
            if self._answer_route(method, parsed):
                return
            if method == "OPTIONS":
                # The same preflight answer for every path; the table never matches it.
                self._answer_preflight()
                return
            if parsed.path == "/api/second-brains":
                self._dispatch_second_brain_request(method)
                return
            if parsed.path == "/api/rem-resources":
                self._dispatch_rem_resources_request(method)
                return
            if parsed.path == "/api/organizations":
                self._dispatch_organization_request(method)
                return
            if parsed.path == "/api/social" or parsed.path.startswith("/api/social/"):
                self._dispatch_social_request(method, parsed)
                return
            if parsed.path == "/api/swarms" or parsed.path.startswith("/api/swarms/"):
                self._dispatch_swarm_request(method)
                return
            # No table route, Mount or body-action dispatcher covers the path.
            self._send_json({"error": "Not found"}, status=404)
        except (OrganizationConflict, RemConflict) as exc:
            self._send_json({"error": str(exc)}, status=409)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            # Usually a request the user or app got wrong. The message can quote the
            # request, so only its type is logged, and only for debugging.
            log.debug("Rejected %s %s with 400 (%s)", method, parsed.path, type(exc).__name__)
            self._send_json({"error": str(exc)}, status=400)
        except TimeoutError:
            log.warning("%s %s exceeded its execution deadline", method, parsed.path)
            self._send_json({"error": "Request exceeded its execution deadline"}, status=408)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            raise  # The client went away; handle() ends the request quietly.
        except Exception:
            # A bug or an unexpected failure. Without this the app got no response
            # at all, and Python printed an unlabeled traceback to stderr.
            log.exception("Unexpected error answering %s %s", method, parsed.path)
            if getattr(self, "_response_started", False):
                self.close_connection = True
            else:
                self._send_json(
                    {
                        "error": "Raticode hit an unexpected error. The details are in "
                        "Settings > Developer > Application log."
                    },
                    status=500,
                )
        finally:
            if acquired and slots is not None:
                slots.release()

    def _answer_route(self, method: str, parsed: ParseResult) -> bool:
        """Answer the request from ROUTES; False when no route or Mount covers it.

        The body read is inside the same try as the handler, so a Mount's error map
        covers a bad body too. Exceptions the map doesn't name go to _dispatch_request's
        central handler, as they did from the old if-chains.
        """
        found = ROUTES.match(method, parsed.path)
        if found is None:
            return False
        if found.route is None:
            status, message = found.miss or (404, "Not found")
            self._send_json({"error": message}, status=status)
            return True
        route = found.route
        try:
            body = None if route.body is None else self._read_json(limit=route.body)
            query = parse_qs(parsed.query)
            # Clients send the version they have in a header; older ones in the query.
            known = self.headers.get("X-Gofer-Known") or query.get("known", [""])[0]
            request = RouteRequest(found.params, query, body, known)
            result = getattr(self, route.handler)(request)
        except Exception as exc:
            answer = found.answer_for(exc)
            if answer is None:
                raise
            if isinstance(answer, int):
                self._send_json({"error": str(exc)}, status=answer)
            else:
                getattr(self, answer)(exc)
            return True
        if result is not ANSWERED:
            self._send_json(result, status=route.status)
        return True

    # Rem home: chat, tasks, runs, memories, decisions and self-work objectives. Agent
    # tokens can read some of these but write none (agent_request_refusal). Polled reads
    # answer {"unchanged": true, "version"} when request.known is still current.

    def _answer_rem_record_missing(self, exc: KeyError) -> None:
        # Usually a task or thread that was removed meanwhile, which is routine. The
        # traceback is kept at debug so a lookup bug can still be traced.
        path = urlparse(self.path).path
        log.debug("Rem %s %s: record not found", self._request_method, path, exc_info=exc)
        self._send_json({"error": "Rem record was not found"}, status=404)

    def _answer_refused(self, exc: KanbanRefused | ObjectiveRefused | DecisionRefused) -> None:
        status = {"conflict": 409, "not_found": 404, "unauthorized": 403}.get(exc.code, 400)
        self._send_json({"error": str(exc), "code": exc.code}, status=status)

    def _answer_decision_read_refused(self, exc: DecisionRefused) -> None:
        # A scan or a path count changes nothing, so only a missing record has its own status.
        status = {"not_found": 404}.get(exc.code, 400)
        self._send_json({"error": str(exc), "code": exc.code}, status=status)

    def _answer_chat_page_gone(self, _exc: RemChatPageGone) -> None:
        self._send_json({"error": "That part of Rem's chat is no longer stored."}, status=404)

    @ROUTES.route("GET", "/api/rem/conversation")
    def _rem_conversation(self, request: RouteRequest) -> Any:
        # A restarted backend has no desktop context until the renderer sends it again;
        # queued tasks wait for it. The newest CHAT_WINDOW messages and how many are
        # earlier, so a poll costs the same however long the chat is. The version covers
        # only this window.
        service = self.server.rem_home
        snapshot = service.store.conversation()
        # Full file paths in Rem's text become links, and linked files attachments.
        snapshot["messages"] = with_file_mentions(snapshot["messages"], service.data_dir)
        # Board tickets named in chat, as they are now: chat shows these keys as pills,
        # and a renamed or deleted ticket changes the version clients poll.
        snapshot["tickets"] = service.kanban.resolve(message_ticket_keys(snapshot["messages"]))
        result = rem_poll_result(
            {**snapshot, "context_ready": service.has_context},
            request.known,
            keep=("context_ready",),
        )
        # Folders Rem opened that the window should add to its projects.
        opened = service.pending_projects()
        if opened:
            result["opened_projects"] = [{"root": p["root"], "name": p["name"]} for p in opened]
        return result

    @ROUTES.route("GET", "/api/rem/history", errors=((RemChatPageGone, "_answer_chat_page_gone"),))
    def _rem_history(self, request: RouteRequest) -> Any:
        # Earlier chat messages, a page before message `before`, as the phone's
        # rem.history reads them. `through` reaches back to a search result.
        service = self.server.rem_home
        before = _optional_query(request.query, "before") or ""
        through = _optional_query(request.query, "through") or ""
        if not before or len(before) > 128 or len(through) > 128:
            raise ValueError("Choose where the earlier messages end")
        limit = _optional_query(request.query, "limit") or ""
        if limit and not limit.isdigit():
            raise ValueError("Invalid page size")
        try:
            page = service.store.page(before, through=through or None, limit=int(limit or 40))
        except KeyError as exc:
            raise RemChatPageGone() from exc
        page["messages"] = with_file_mentions(page["messages"], service.data_dir)
        page["tickets"] = service.kanban.resolve(message_ticket_keys(page["messages"]))
        return page

    @ROUTES.route("GET", "/api/rem/tasks")
    def _rem_tasks(self, request: RouteRequest) -> Any:
        # The tickets each task's runs worked on, for the task cards and details, and the
        # self-work objectives the side pane lists above the tasks. Built again only after
        # a write or a time-based wait comes due (rem_task_list); prompts are left out,
        # and the task sheet reads one from GET tasks/{id}.
        listed, version = self.server.rem_home.task_list.view()
        if request.known == version:
            return {"unchanged": True, "version": version}
        return {**listed, "version": version}

    @ROUTES.route("GET", "/api/rem/tasks/{task_id}")
    def _rem_task(self, request: RouteRequest) -> Any:
        # One task with its prompt and recent runs, for the task sheet and the CLI. A
        # deleted task is a KeyError, answered 404.
        service = self.server.rem_home
        task = service.store.task_detail(request.params["task_id"], summary=True)
        return {"task": task, "tickets": service.kanban.resolve(task_ticket_keys([task]))}

    @ROUTES.route("GET", "/api/rem/objectives")
    def _rem_objectives(self, request: RouteRequest) -> Any:
        # The first page by default; the side pane asks for more as the user scrolls, and
        # the CLI pages with offset.
        from gofer.ui.rem_objectives import PAGE

        try:
            page_size = int(request.query.get("limit", [str(PAGE)])[0])
            page_start = int(request.query.get("offset", ["0"])[0])
        except ValueError:
            raise ValueError("limit and offset are whole numbers.") from None
        # project pages one project's own objectives; without it, the global ones.
        project = request.query.get("project", [None])[0]
        objectives = self.server.rem_home.self_work.objectives(page_size, page_start, project)
        return rem_poll_result(objectives, request.known)

    @ROUTES.route(
        "POST", "/api/rem/objectives", body=0, errors=((ObjectiveRefused, "_answer_refused"),)
    )
    def _rem_change_objectives(self, request: RouteRequest) -> Any:
        # The user's change: agent tokens can't write Rem routes, and Rem's own changes go
        # through its chat decisions and self-work, under Rem's rules.
        return self.server.rem_home.self_work.change_objectives(request.body or {})

    @ROUTES.route(
        "POST",
        "/api/rem/objectives/ask",
        body=0,
        errors=((ObjectiveRefused, "_answer_refused"),),
    )
    def _rem_ask_objectives(self, request: RouteRequest) -> Any:
        # Ask Rem in a project's Objectives section: Rem's model writes three to five
        # objectives for it in the background; the view's ``asking`` shows how it went.
        return self.server.rem_home.self_work.ask_objectives(request.body or {})

    @ROUTES.route("GET", "/api/rem/threads/{thread_id}")
    def _rem_thread(self, request: RouteRequest) -> Any:
        service = self.server.rem_home
        thread = service.store.thread(request.params["thread_id"])
        # Files a worker's reply links to show under it as attachments, as in Rem's chat.
        thread["messages"] = with_thread_linked_files(thread["messages"], service.data_dir)
        if self._agent_token_name:
            # Thread tools carry MCP server URLs and arguments, which can hold keys; agent
            # tokens can't read them on /api/rem-resources either.
            thread["metadata"] = {
                key: value for key, value in thread["metadata"].items() if key != "resources"
            }
        return rem_poll_result(thread, request.known)

    @ROUTES.route("GET", "/api/rem/threads/{thread_id}/context")
    def _rem_thread_context(self, request: RouteRequest) -> Any:
        # How big each part of the thread's newest turns was; sizes only.
        from gofer.core.turn_context import thread_context

        return thread_context(
            request.params["thread_id"],
            self._request_data_dir(request.query),
            limit=int(request.query.get("limit", ["10"])[0] or 10),
        )

    @ROUTES.route("GET", "/api/rem/calls")
    def _rem_calls(self, request: RouteRequest) -> Any:
        # How big each part of Rem's own newest model calls was; sizes only.
        from gofer.core.turn_context import rem_calls

        query = request.query
        return rem_calls(
            self._request_data_dir(query),
            limit=int(query.get("limit", ["10"])[0] or 10),
            call=query.get("call", [""])[0] or None,
            run_id=query.get("run", [""])[0] or None,
        )

    @ROUTES.route("GET", "/api/rem/runs/{run_id}")
    def _rem_run(self, request: RouteRequest) -> Any:
        service = self.server.rem_home
        with service.store.connect() as db:
            run = service.store.get(db, "runs", request.params["run_id"])
            changes = service.store.provider_version_changes(db, run)
        if changes:
            # Computed when read: a failed run's provider apps that changed version since
            # the task's last passing run.
            run["provider_version_changes"] = changes
        if self._agent_token_name:
            # A run keeps Rem's whole configuration: projects with their folder grants,
            # MCP servers and Second Brain grants, all denied to agent tokens on
            # /api/rem/context.
            run = {key: value for key, value in run.items() if key != "config"}
        return {"run": run}

    @ROUTES.route("GET", "/api/rem/search")
    def _rem_search(self, request: RouteRequest) -> Any:
        # Words to find in the whole saved chat; no model is involved.
        query = request.query
        before = query.get("before", [""])[0]
        if before and not before.isdigit():
            raise ValueError("Invalid search position")
        return self.server.rem_home.store.search(
            query.get("q", [""])[0],
            before=int(before) if before else None,
            limit=int(query.get("limit", ["50"])[0] or 50),
        )

    @ROUTES.route("GET", "/api/rem/messages")
    def _rem_messages(self, request: RouteRequest) -> Any:
        # Chat messages by position, for scripts that follow Rem's replies.
        from gofer.ui.rem_notices import messages_page

        return messages_page(self.server.rem_home.store, request.query)

    @ROUTES.route("GET", "/api/rem/notices")
    def _rem_notices(self, request: RouteRequest) -> Any:
        from gofer.ui.rem_notices import parse_include, read_notices

        return read_notices(
            self.server.rem_home,
            getattr(self.server, "organizations", None),
            request.query.get("cursor", [""])[0],
            parse_include(request.query.get("include", [""])[0]),
        )

    @ROUTES.route("GET", "/api/rem/context")
    def _rem_context(self, request: RouteRequest) -> Any:
        # The settings the window (or `Raticode rem settings apply`) last sent.
        from gofer.ui.rem_provider_accounts import self_work_choices

        service = self.server.rem_home
        with service.store.read() as db:
            stored = service.store.setting(db, "config")
            # Kept with self-work's settings; listed here so `rem settings export` and
            # `apply` carry it.
            stored["self_work_accounts"] = self_work_choices(service.store.setting(db, "self_work"))
        return {"config": stored, "context_ready": service.has_context}

    @ROUTES.route("POST", "/api/rem/context", body=0)
    def _rem_configure(self, request: RouteRequest) -> Any:
        body = request.body or {}
        warnings = self._prune_rem_context(body)
        return {**self.server.rem_home.configure(body), "warnings": warnings}

    @ROUTES.route("GET", "/api/rem/decisions")
    def _rem_decisions(self, request: RouteRequest) -> Any:
        project = _optional_query(request.query, "project") or None
        # Reads the projects' decision record files in first; never commits.
        decisions = self.server.rem_home.decisions(project, sync=True)
        return rem_poll_result(decisions, request.known)

    @ROUTES.route(
        "POST",
        "/api/rem/decisions/scan",
        body=0,
        errors=((DecisionRefused, "_answer_decision_read_refused"),),
    )
    def _rem_scan_decisions(self, request: RouteRequest) -> Any:
        # Reads the project's files only; what it finds goes through the store.
        return self.server.rem_home.scan_decisions(request.body or {})

    @ROUTES.route(
        "POST",
        "/api/rem/decisions/paths",
        body=0,
        errors=((DecisionRefused, "_answer_decision_read_refused"),),
    )
    def _rem_decision_paths(self, request: RouteRequest) -> Any:
        # Counts tracked files for typed record paths; changes nothing.
        return self.server.rem_home.decision_paths(request.body or {})

    @ROUTES.route(
        "POST", "/api/rem/decisions", body=0, errors=((DecisionRefused, "_answer_refused"),)
    )
    def _rem_change_decisions(self, request: RouteRequest) -> Any:
        # The user's change: agent tokens can't write Rem routes, and Rem itself only files
        # requests through rem_decisions.file_request, never changing one.
        return self.server.rem_home.change_decisions(request.body or {})

    @ROUTES.route("GET", "/api/rem/memories")
    def _rem_memories(self, request: RouteRequest) -> Any:
        return self.server.rem_home.memories()

    @ROUTES.route("POST", "/api/rem/memories", body=0)
    def _rem_add_memory(self, request: RouteRequest) -> Any:
        return self.server.rem_home.add_memory(request.body or {})

    @ROUTES.route("PATCH", "/api/rem/memories/{memory_id}", body=0)
    def _rem_edit_memory(self, request: RouteRequest) -> Any:
        return self.server.rem_home.edit_memory(request.params["memory_id"], request.body or {})

    @ROUTES.route("DELETE", "/api/rem/memories/{memory_id}")
    def _rem_forget_memory(self, request: RouteRequest) -> Any:
        return self.server.rem_home.forget_memory(request.params["memory_id"])

    @ROUTES.route("PUT", "/api/rem/memory-mode", body=0)
    def _rem_memory_mode(self, request: RouteRequest) -> Any:
        return self.server.rem_home.memory_mode(request.body or {})

    @ROUTES.route("POST", "/api/rem/messages/{message_id}/undo-memory")
    def _rem_undo_memory(self, request: RouteRequest) -> Any:
        return self.server.rem_home.undo_memory(request.params["message_id"])

    @ROUTES.route("GET", "/api/rem/events")
    def _rem_events(self, request: RouteRequest) -> Any:
        after = int(request.query.get("after", ["0"])[0])
        if after < 0:
            raise ValueError("Invalid event cursor")
        return {"events": self.server.rem_home.store.events(after)}

    # Room for one screenshot (Show Rem your screen), at most 5 MB as base64. Agent
    # tokens never get here: agent_request_refusal refuses this route.
    @ROUTES.route("POST", "/api/rem/messages", body=REM_MESSAGE_MAX_BODY_BYTES)
    def _rem_send_message(self, request: RouteRequest) -> Any:
        return self.server.rem_home.submit_message(request.body or {})

    @ROUTES.route("POST", "/api/rem/reset", body=0)
    def _rem_reset(self, request: RouteRequest) -> Any:
        return self.server.rem_home.clear_history(request.body or {})

    @ROUTES.route("POST", "/api/rem/threads/{thread_id}/messages", body=0)
    def _rem_instruct_thread(self, request: RouteRequest) -> Any:
        return self.server.rem_home.instruct(request.params["thread_id"], request.body or {})

    @ROUTES.route("POST", "/api/rem/tasks", body=0)
    def _rem_create_task(self, request: RouteRequest) -> Any:
        return self.server.rem_home.create_task(request.body or {})

    @ROUTES.route("PATCH", "/api/rem/tasks/{task_id}", body=0)
    def _rem_edit_task(self, request: RouteRequest) -> Any:
        return self.server.rem_home.edit_task(request.params["task_id"], request.body or {}, "edit")

    @ROUTES.route("POST", "/api/rem/tasks/{task_id}/run", body=0)
    def _rem_run_task(self, request: RouteRequest) -> Any:
        return self.server.rem_home.edit_task(request.params["task_id"], request.body or {}, "run")

    @ROUTES.route("POST", "/api/rem/tasks/{task_id}/pause", body=0)
    def _rem_pause_task(self, request: RouteRequest) -> Any:
        task_id = request.params["task_id"]
        return self.server.rem_home.edit_task(task_id, request.body or {}, "pause")

    # Make it a workflow (draft), and Switch over (preview, then switch).
    @ROUTES.route(
        "POST",
        "/api/rem/tasks/{task_id}/workflow",
        body=0,
        errors=(
            (ApprovalConflict, "_answer_approval_conflict"),
            (UnseenChanges, "_answer_unseen_changes"),
        ),
    )
    def _rem_task_workflow(self, request: RouteRequest) -> Any:
        return self.server.rem_home.task_workflow(request.params["task_id"], request.body or {})

    @ROUTES.route("DELETE", "/api/rem/tasks/{task_id}")
    def _rem_delete_task(self, request: RouteRequest) -> Any:
        request_id = request.query.get("request_id", [""])[0]
        task_id = request.params["task_id"]
        return self.server.rem_home.edit_task(task_id, {"request_id": request_id}, "delete")

    @ROUTES.route("POST", "/api/rem/runs/{run_id}/cancel", body=0)
    def _rem_cancel_run(self, request: RouteRequest) -> Any:
        run_id = request.params["run_id"]
        return self.server.rem_home.store.mutate_run(run_id, request.body or {}, "cancel")

    @ROUTES.route("POST", "/api/rem/runs/{run_id}/acknowledge", body=0)
    def _rem_acknowledge_run(self, request: RouteRequest) -> Any:
        run_id = request.params["run_id"]
        return self.server.rem_home.store.mutate_run(run_id, request.body or {}, "acknowledge")

    # Dismiss all: every task update at once. Runs still waiting for the user's answer
    # and held tasks stay.
    @ROUTES.route("POST", "/api/rem/runs/acknowledge", body=0)
    def _rem_dismiss_updates(self, request: RouteRequest) -> Any:
        return self.server.rem_home.store.dismiss_updates(request.body or {})

    @ROUTES.route("POST", "/api/rem/runs/{run_id}/retry_review", body=0)
    def _rem_retry_review(self, request: RouteRequest) -> Any:
        return self.server.rem_home.retry_review(request.params["run_id"], request.body or {})

    # Stops the run's turn and keeps it paused with its thread and worktree.
    @ROUTES.route("POST", "/api/rem/runs/{run_id}/pause", body=0)
    def _rem_pause_run(self, request: RouteRequest) -> Any:
        return self.server.rem_home.pause_run(request.params["run_id"], request.body or {})

    # Picks a paused run back up in the same thread, never as a fresh run.
    @ROUTES.route("POST", "/api/rem/runs/{run_id}/resume", body=0)
    def _rem_resume_run(self, request: RouteRequest) -> Any:
        return self.server.rem_home.resume_run(request.params["run_id"], request.body or {})

    # Wake now: a sleeping worker resumes in its thread without waiting out its time.
    @ROUTES.route("POST", "/api/rem/runs/{run_id}/wake", body=0)
    def _rem_wake_run(self, request: RouteRequest) -> Any:
        return self.server.rem_home.wake_run(request.params["run_id"], request.body or {})

    # Run there: the user's pick of the paired desktop a waiting task suggests.
    @ROUTES.route("POST", "/api/rem/runs/{run_id}/run_there", body=0)
    def _rem_run_there(self, request: RouteRequest) -> Any:
        return self.server.rem_home.run_there(request.params["run_id"], request.body or {})

    # The desktop's global Kanban and self-work controls; the phone's mirror them. Each
    # change answers with the whole board and self-work view.

    def _kanban_view(self) -> dict[str, Any]:
        service = self.server.rem_home
        return {"board": service.kanban.board(), "self_work": service.self_work.view()}

    @ROUTES.route("GET", "/api/rem/kanban")
    def _kanban(self, request: RouteRequest) -> Any:
        return rem_poll_result(self._kanban_view(), request.known)

    @ROUTES.route("POST", "/api/rem/kanban/timeline", body=0)
    def _kanban_timeline(self, request: RouteRequest) -> Any:
        # One ticket's comments and status history; a read, sent with its address.
        return self.server.rem_home.kanban.timeline(request.body or {})

    @ROUTES.route("POST", "/api/rem/kanban/patch", body=0)
    def _kanban_patch(self, request: RouteRequest) -> Any:
        # A ticket's full patch, where it lands and whether it merges; a read.
        return self.server.rem_home.kanban.patch(request.body or {})

    @ROUTES.route("POST", "/api/rem/kanban/ticket", body=0)
    def _kanban_ticket(self, request: RouteRequest) -> Any:
        body = request.body or {}
        request_id = str(body.get("request_id") or uuid.uuid4())
        actor = "rem" if self._agent_token_name else "user"
        result = self.server.rem_home.kanban.act(actor, body, request_id)
        # Accept says whether the branch landed or which files conflict.
        if body.get("action") == "land":
            return {**self._kanban_view(), "result": result}
        return self._kanban_view()

    @ROUTES.route("POST", "/api/rem/kanban/participant", body=0)
    def _kanban_participant(self, request: RouteRequest) -> Any:
        self.server.rem_home.kanban.set_participant_projects("user", request.body or {})
        return self._kanban_view()

    @ROUTES.route("PUT", "/api/rem/kanban/settings", body=0)
    def _kanban_settings(self, request: RouteRequest) -> Any:
        self.server.rem_home.self_work.update_settings(request.body or {})
        return self._kanban_view()

    @ROUTES.route("POST", "/api/rem/kanban/report/seen", body=0)
    def _kanban_report_seen(self, request: RouteRequest) -> Any:
        self.server.rem_home.self_work.mark_seen((request.body or {}).get("updated_at"))
        return self._kanban_view()

    @ROUTES.route("POST", "/api/rem/kanban/window", body=0)
    def _kanban_window(self, request: RouteRequest) -> Any:
        # A timed self-work window: schedule one, end one at a time, or cancel one.
        self.server.rem_home.self_work.change_window(request.body or {})
        return self._kanban_view()

    @ROUTES.route("POST", "/api/rem/kanban/stop", body=0)
    def _kanban_stop(self, request: RouteRequest) -> Any:
        # When self-work stops: the user's condition and latest time, set or cleared.
        self.server.rem_home.self_work.change_stop(request.body or {})
        return self._kanban_view()

    # Rem's chat: the routes under Mount("/api/chat") above.

    def _answer_chat_turn_missing(self, _exc: FileNotFoundError) -> None:
        self._send_json({"error": "Rem turn was not found"}, status=404)

    def _answer_steering_unreadable(self, exc: OSError) -> None:
        log.error("Could not read or persist steering receipts", exc_info=exc)
        self._send_json({"error": "Could not read or persist steering receipts"}, status=500)

    def _answer_steering_unsaved(self, exc: OSError) -> None:
        log.error("Could not persist steering receipt", exc_info=exc)
        self._send_json({"error": "Could not persist steering receipt"}, status=500)

    def _answer_transcription_failed(self, exc: Exception) -> None:
        log.error("Rem transcription failed (%s)", urlparse(self.path).path, exc_info=exc)
        self._send_json({"error": f"Transcription failed: {exc}"}, status=502)

    def _answer_speech_settings_failed(self, exc: Exception) -> None:
        self._request_failed("Saving speech settings", exc)

    def _answer_offline_model_failed(self, exc: Exception) -> None:
        self._request_failed("Downloading the offline speech model", exc)

    def _answer_speech_test_failed(self, exc: Exception) -> None:
        self._request_failed("Testing speech recognition", exc)

    def _answer_commit_message_failed(self, exc: Exception) -> None:
        self._request_failed("Generating a commit message", exc)

    def _answer_rem_chat_failed(self, exc: Exception) -> None:
        log.error("Unhandled Rem error", exc_info=exc)
        self._send_json({"error": f"Rem failed: {exc}"}, status=500)

    @ROUTES.route(
        "POST",
        "/api/chat",
        body=0,
        errors=(
            (ValueError, 400),
            (Exception, "_answer_rem_chat_failed"),
        ),
    )
    def _chat_turn(self, request: RouteRequest) -> Any:
        body = request.body or {}
        self._validate_second_brain(body)
        self._validate_rem_thread_projects(body)
        with self._rem_swarm_session(body) as (chat_body, swarm_url):
            return self._run_async(
                run_workflow_chat(
                    provider=str(body.get("provider", "codex")),
                    model=str(body.get("model", "cli-default")),
                    effort=_optional_body_str(body, "effort"),
                    permission_mode=_optional_body_str(body, "permissionMode"),
                    messages=body.get("messages") or [],
                    conversation_id=body.get("conversationId"),
                    reset_session=body.get("resetSession") is True,
                    workflow=chat_body.get("workflow"),
                    trusted_swarm_url=swarm_url,
                    data_dir=self._request_data_dir(request.query),
                    resource_limits=self._resource_limits(),
                )
            )

    @ROUTES.route(
        "POST",
        "/api/chat/stream",
        body=0,
        errors=((ValueError, 400), (FileExistsError, 400)),
    )
    def _chat_stream(self, request: RouteRequest) -> Any:
        body = request.body or {}
        manager = getattr(self.server, "rem_home", None)
        if manager is not None and manager.manages(body.get("conversationId", "")):
            raise ValueError("Managed task turns are executed by Rem")
        self._validate_second_brain(body)
        self._validate_rem_thread_projects(body)
        data_dir = self._request_data_dir(request.query)
        if "conversationId" not in body and "turnId" not in body:
            # Legacy clients retain their request-scoped behavior.
            with self._rem_swarm_session(body, defer_threads=True) as (chat_body, swarm_url):
                self._send_stream_headers()
                self._run_async(self._stream_chat_response(chat_body, data_dir, swarm_url))
            return ANSWERED

        def run(emit: Callable[[dict[str, Any]], None]) -> None:
            # Resource grants and swarm sessions live as long as the job,
            # independently of its HTTP subscribers.
            with self._rem_swarm_session(body, defer_threads=True) as (chat_body, swarm_url):
                asyncio.run(self._stream_chat_response(chat_body, data_dir, swarm_url, emit=emit))

        conversation_id = body.get("conversationId", "")
        turn_id = body.get("turnId", "")
        self.server.chat_jobs.start(conversation_id, turn_id, run)
        self._follow_chat_job(conversation_id, turn_id)
        return ANSWERED

    @ROUTES.route(
        "GET",
        "/api/chat/events",
        errors=((FileNotFoundError, "_answer_chat_turn_missing"), (ValueError, 400)),
    )
    def _chat_events(self, request: RouteRequest) -> Any:
        self._follow_chat_job(
            request.query.get("conversationId", [""])[0],
            request.query.get("turnId", [""])[0],
            int(request.query.get("after", ["0"])[0]),
        )
        return ANSWERED

    @ROUTES.route("DELETE", "/api/chat", errors=((WorkflowUpdateError, 400),))
    def _chat_delete(self, request: RouteRequest) -> Any:
        return delete_workflow_chat_payload(
            "workflow-assistant", self._request_data_dir(request.query)
        )

    @ROUTES.route("DELETE", "/api/chat/threads/{thread_id}", errors=((WorkflowUpdateError, 400),))
    def _chat_thread_delete(self, request: RouteRequest) -> Any:
        thread_id = request.params["thread_id"]
        data_dir = self._request_data_dir(request.query)
        payload = delete_workflow_chat_payload(f"workflow-assistant:{thread_id}", data_dir)
        shutil.rmtree(attachment_thread_dir(data_dir, thread_id), ignore_errors=True)
        return payload

    @ROUTES.route("GET", "/api/chat/providers")
    def _chat_providers(self, request: RouteRequest) -> Any:
        return provider_payload()

    # Steering: steer and stop never wait for an expensive slot (_dispatch_request).

    @ROUTES.route(
        "GET",
        "/api/chat/steering",
        errors=((ValueError, 400), (OSError, "_answer_steering_unreadable")),
    )
    def _chat_steering_receipts(self, request: RouteRequest) -> Any:
        conversation_id = request.query.get("conversationId", [""])[0]
        return {"receipts": self.server.chat_steering.receipts(conversation_id)}

    @ROUTES.route(
        "POST",
        "/api/chat/steer",
        body=0,
        errors=(
            (ChatSteeringConflict, 409),
            (ValueError, 400),
            (OSError, "_answer_steering_unsaved"),
        ),
    )
    def _chat_steer(self, request: RouteRequest) -> Any:
        body = request.body or {}
        manager = getattr(self.server, "rem_home", None)
        if manager is not None and manager.manages(body.get("conversationId", "")):
            raise ValueError("Send managed task instructions through Rem")
        return {"receipt": self.server.chat_steering.steer(body)}

    @ROUTES.route(
        "POST",
        "/api/chat/stop",
        body=0,
        errors=(
            (ChatSteeringConflict, 409),
            (ValueError, 400),
            (OSError, "_answer_steering_unsaved"),
        ),
    )
    def _chat_stop(self, request: RouteRequest) -> Any:
        body = request.body or {}
        self.server.chat_steering.stop(body.get("conversationId", ""), body.get("turnId", ""))
        rem_home = getattr(self.server, "rem_home", None)
        if rem_home is not None:
            rem_home.manual_stop(body.get("conversationId", ""))
        return {"stopped": True}

    # Attachments and the changes a turn made.

    @ROUTES.route("GET", "/api/chat/attachments/locate", errors=((ChatMediaError, 404),))
    def _chat_attachment_locate(self, request: RouteRequest) -> Any:
        # The window previews a sent attachment from where the upload stored it.
        path = locate_chat_attachment(
            request.query.get("storageName", [""])[0],
            data_dir=self._request_data_dir(request.query),
            thread_id=request.query.get("threadId", [""])[0] or None,
        )
        return {"path": str(path)}

    @ROUTES.route(
        "POST",
        "/api/chat/attachments",
        body=CHAT_ATTACHMENT_MAX_TOTAL_BYTES * 2,
        status=201,
        errors=((ChatMediaError, 400), (json.JSONDecodeError, 400)),
    )
    def _chat_attachments_store(self, request: RouteRequest) -> Any:
        return store_chat_attachments(request.body or {}, self._request_data_dir(request.query))

    @ROUTES.route(
        "POST",
        "/api/chat/attachments/copy",
        body=0,
        status=201,
        errors=((ChatMediaError, 400), (OSError, 400), (json.JSONDecodeError, 400)),
    )
    def _chat_attachments_copy(self, request: RouteRequest) -> Any:
        from gofer.ui.chat_media import copy_chat_attachments

        return copy_chat_attachments(request.body or {}, self._request_data_dir(request.query))

    @ROUTES.route(
        "POST",
        "/api/chat/changes/undo",
        body=0,
        errors=((ChatChangeError, 409), (json.JSONDecodeError, 409)),
    )
    def _chat_changes_undo(self, request: RouteRequest) -> Any:
        body = request.body or {}
        return undo_chat_changes(
            str(body.get("changeSetId") or ""), self._request_data_dir(request.query)
        )

    @ROUTES.route(
        "POST",
        "/api/chat/changes/redo",
        body=0,
        errors=((ChatChangeError, 409), (json.JSONDecodeError, 409)),
    )
    def _chat_changes_redo(self, request: RouteRequest) -> Any:
        body = request.body or {}
        return redo_chat_changes(
            str(body.get("changeSetId") or ""), self._request_data_dir(request.query)
        )

    # Speech: engines, the offline model, recordings and live transcription sessions.

    @ROUTES.route("GET", "/api/chat/transcribe/engines")
    def _chat_speech_engines(self, request: RouteRequest) -> Any:
        refresh = request.query.get("refresh", ["0"])[0] in {"1", "true"}
        preference = speech_preference()
        return {
            **preference,
            **transcription_engines(
                data_dir=self._request_data_dir(request.query),
                refresh=refresh,
                preference=preference,
            ),
        }

    @ROUTES.route(
        "POST",
        "/api/chat/transcribe/engines",
        body=0,
        errors=(
            (ValueError, "_answer_speech_settings_failed"),
            (OSError, "_answer_speech_settings_failed"),
        ),
    )
    def _chat_speech_engines_save(self, request: RouteRequest) -> Any:
        save_speech_preference(request.body or {})
        return {"saved": True, **speech_preference()}

    @ROUTES.route(
        "POST",
        "/api/chat/transcribe/engines/test",
        body=0,
        errors=(
            (ValueError, "_answer_speech_test_failed"),
            (OSError, "_answer_speech_test_failed"),
        ),
    )
    def _chat_speech_engines_test(self, request: RouteRequest) -> Any:
        tried = system_speech.try_system_engine()
        preference = speech_preference()
        engines = transcription_engines(
            data_dir=self._request_data_dir(request.query), refresh=True, preference=preference
        )
        return {"test": tried, **preference, **engines}

    @ROUTES.route("GET", "/api/chat/transcribe/model")
    def _chat_speech_model(self, request: RouteRequest) -> Any:
        return offline_model_status(data_dir=self._request_data_dir(request.query))

    @ROUTES.route(
        "POST",
        "/api/chat/transcribe/model",
        body=0,
        status=202,
        errors=(
            (ValueError, "_answer_offline_model_failed"),
            (OSError, "_answer_offline_model_failed"),
        ),
    )
    def _chat_speech_model_download(self, request: RouteRequest) -> Any:
        return start_offline_model_download(data_dir=self._request_data_dir(request.query))

    @ROUTES.route(
        "POST",
        "/api/chat/transcribe",
        body=CHAT_AUDIO_MAX_BYTES * 2,
        errors=(
            (ChatMediaError, 400),
            (json.JSONDecodeError, 400),
            (Exception, "_answer_transcription_failed"),
        ),
    )
    def _chat_transcribe(self, request: RouteRequest) -> Any:
        return transcribe_chat_audio(
            request.body or {}, data_dir=self._request_data_dir(request.query)
        )

    @ROUTES.route(
        "POST",
        "/api/chat/transcribe/start",
        body=0,
        status=201,
        errors=(
            (ChatMediaError, 400),
            (json.JSONDecodeError, 400),
            (Exception, "_answer_transcription_failed"),
        ),
    )
    def _chat_transcribe_start(self, request: RouteRequest) -> Any:
        preference = speech_preference()
        return start_chat_transcription(
            data_dir=self._request_data_dir(request.query),
            engine=preference["engine"],
            engine_chosen=preference["chosen"],
        )

    @ROUTES.route(
        "POST",
        "/api/chat/transcribe/chunk",
        body=CHAT_AUDIO_CHUNK_MAX_BYTES * 2,
        errors=(
            (ChatMediaError, 400),
            (json.JSONDecodeError, 400),
            (Exception, "_answer_transcription_failed"),
        ),
    )
    def _chat_transcribe_chunk(self, request: RouteRequest) -> Any:
        return stream_chat_transcription(request.body or {})

    @ROUTES.route(
        "POST",
        "/api/chat/transcribe/finish",
        body=0,
        errors=(
            (ChatMediaError, 400),
            (json.JSONDecodeError, 400),
            (Exception, "_answer_transcription_failed"),
        ),
    )
    def _chat_transcribe_finish(self, request: RouteRequest) -> Any:
        return finish_chat_transcription(request.body or {})

    @ROUTES.route(
        "POST",
        "/api/chat/transcribe/cancel",
        body=0,
        errors=((ChatMediaError, 400), (json.JSONDecodeError, 400)),
    )
    def _chat_transcribe_cancel(self, request: RouteRequest) -> Any:
        return cancel_chat_transcription(request.body or {})

    # Text the chat box asks a provider to write.

    @ROUTES.route("POST", "/api/chat/voice-tidy", body=0, errors=((ValueError, 400),))
    def _chat_voice_tidy(self, request: RouteRequest) -> Any:
        from gofer.ui import voice_tidy

        body = request.body or {}
        heard = body.get("text")
        if not isinstance(heard, str):
            raise ValueError("text must be a string")
        before = body.get("before", "")
        if not isinstance(before, str):
            raise ValueError("before must be a string")
        default = body.get("defaultProvider")
        chosen = voice_tidy.current(
            default if isinstance(default, str) and default else None, self.server.rem_home
        )
        context = (
            voice_tidy.chat_context(self.server.rem_home)
            if chosen.get("available") and chosen.get("context")
            else ""
        )
        data_dir = self._request_data_dir(request.query)
        return voice_tidy.tidy(heard, chosen, data_dir, context=context, before=before)

    @ROUTES.route(
        "POST",
        "/api/chat/commit-message",
        body=COMMIT_MESSAGE_MAX_BODY_BYTES,
        errors=(
            (ValueError, "_answer_commit_message_failed"),
            (OSError, "_answer_commit_message_failed"),
        ),
    )
    def _chat_commit_message(self, request: RouteRequest) -> Any:
        from gofer.ui.commit_message import generate_commit_message

        body = request.body or {}
        project_root: Path | None = None
        if body.get("projectRoot"):
            project_root = self._assert_bundle_path_allowed(
                Path(str(body["projectRoot"])), body.get("grantId"), must_exist=True
            )
        return self._run_async(
            generate_commit_message(
                provider=str(body.get("provider", "codex")),
                model=str(body.get("model", "cli-default")),
                effort=_optional_body_str(body, "effort"),
                permission_mode=_optional_body_str(body, "permissionMode"),
                diff=body.get("diff", ""),
                project_root=project_root,
                inspect_staged=body.get("inspectStaged") is True,
            )
        )

    def _dispatch_rem_resources_request(self, method: str) -> None:
        """Read or edit the Raticode-wide skill and MCP server library."""
        from pydantic import ValidationError

        from gofer.core import rem_library

        if method not in {"GET", "POST"}:
            self._send_json({"error": "Method not allowed"}, status=405)
            return
        data_dir = self._default_data_dir()
        extra: dict[str, Any] = {}
        try:
            if method == "POST":
                body = self._read_json(limit=256 * 1024)
                action = body.get("action")
                if action == "clear_mcp_cache":
                    # A server's downloaded packages; it fetches them again on its next start.
                    name = body.get("name")
                    extra["cacheCleared"] = rem_library.clear_mcp_cache(
                        data_dir, str(name) if name else None
                    )
                elif action == "add_skill":
                    rem_library.add_skill(data_dir, str(body.get("path", "")))
                elif action == "remove_skill":
                    rem_library.remove_skill(data_dir, str(body.get("path", "")))
                elif action == "save_mcp":
                    server = body.get("server")
                    if not isinstance(server, dict):
                        raise ValueError("Expected an MCP server definition")
                    replace = body.get("replace")
                    rem_library.save_mcp_server(
                        data_dir, server, replace=str(replace) if replace else None
                    )
                elif action == "remove_mcp":
                    rem_library.remove_mcp_server(data_dir, str(body.get("name", "")))
                else:
                    raise ValueError("Unknown resource library action")
            self._send_json({**rem_library.snapshot(data_dir), **extra})
        except ValidationError as exc:
            message = exc.errors()[0].get("msg", "Invalid MCP server") if exc.errors() else ""
            self._send_json({"error": str(message).removeprefix("Value error, ")}, status=400)
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _answer_mcp_tool_error(self, exc: McpToolError) -> None:
        status = 404 if exc.code == "RATTISH_MCP_SERVER_UNKNOWN" else 502
        self._send_json({"error": exc.message, "code": exc.code}, status=status)

    @ROUTES.route(
        "GET",
        "/api/rem-resources/mcp-tools",
        errors=((McpToolError, "_answer_mcp_tool_error"),),
    )
    def _mcp_server_tools(self, request: RouteRequest) -> Any:
        """Start an MCP server from Rem's list and return its tools, for the Studio picker.

        ``network`` (repeatable) and ``sandbox=off`` match the mcp-tool step being edited.
        A local server gets no working folder of its own here: it writes only to scratch.
        """
        from gofer.rattish import mcp_tool

        query = request.query
        return mcp_tool.server_tools(
            self._default_data_dir(),
            (query.get("name") or [""])[0],
            network=mcp_tool.parse_network(query.get("network")),
            sandbox=(query.get("sandbox") or ["on"])[0] != "off",
        )

    def _dispatch_second_brain_request(self, method: str) -> None:
        if method not in {"GET", "POST"}:
            self._send_json({"error": "Method not allowed"}, status=405)
            return
        query = parse_qs(urlparse(self.path).query)
        body = self._read_json(limit=8 * 1024 * 1024) if method == "POST" else {}
        action = str(body.get("action") or _optional_query(query, "action") or "snapshot")
        if method == "GET" and action != "snapshot":
            raise ValueError("Second Brain actions require POST")
        if self._agent_token_name and action not in AGENT_SECOND_BRAIN_ACTIONS:
            self._send_json(
                {
                    "error": "Agent tokens can read Second Brain but can't change it.",
                    "code": "agent_token_forbidden",
                },
                status=403,
            )
            return
        params = body.get("params", {})
        if method == "GET":
            # The renderer's poll sends the revision it has and gets {unchanged, revision}
            # while it is current; recheck=1 runs Library health's file checks again now.
            params = {
                "revision": _optional_query(query, "revision") or "",
                "recheck": _optional_query(query, "recheck") == "1",
                # Every document's passages too: the view's fallback when "passages" fails.
                "passages": _optional_query(query, "passages") == "1",
            }
        if not isinstance(params, dict):
            raise ValueError("Expected Second Brain parameters")
        if self._agent_token_name and action == "snapshot":
            # The full snapshot is the view's fallback. It carries every document's text,
            # left-out ones too, so an agent token gets the light one and asks "passages".
            params = {**params, "passages": False}
        grants = body.get("workspaceGrants", {})
        if not isinstance(grants, dict):
            raise ValueError("Expected folder access grants")
        paths: list[tuple[str, bool]] = []
        if action == "open":
            paths.append((str(params.get("root", "")), not params.get("create", False)))
        if action == "update_brain" and params.get("patch", {}).get("root"):
            paths.append((params["patch"]["root"], True))
        if action == "update_brain" and isinstance(params.get("patch", {}).get("backup"), dict):
            # Scheduled backups write here later without asking, so the folder needs the
            # user's approval when it is chosen.
            folder = params["patch"]["backup"].get("folder")
            if folder:
                paths.append((str(folder), False))
        if action == "import_preview":
            paths.append((str(params.get("path", "")), True))
        if action == "task":
            kind = params.get("kind")
            if kind == "add-files":
                paths.extend((str(path), True) for path in params.get("paths", []))
            if kind in {"import", "bibliography"}:
                paths.append((str(params.get("path", "")), not params.get("export", False)))
            if kind in {"import", "export"}:
                paths.append((str(params.get("destination", "")), False))
        # An agent token reads only brains that allow cloud processing, unless the owner
        # created it with `Raticode tokens create --private-brains`.
        private_closed = bool(self._agent_token_name) and not self._agent_private_brains
        try:
            for value, must_exist in paths:
                self._assert_bundle_path_allowed(
                    Path(value), grants.get(value), must_exist=must_exist
                )
            library = self.server.second_brains
            if private_closed:
                refusal = agent_brain_refusal(library, action, params)
                if refusal is not None:
                    self._send_json({"error": refusal, "code": AGENT_BRAIN_PRIVATE}, status=403)
                    return
            # No agent token reads a document left out of search, as in read_note.
            if self._agent_token_name:
                refusal = agent_left_out_refusal(library, action, params)
                if refusal is not None:
                    self._send_json({"error": refusal, "code": AGENT_DOCUMENT_LEFT_OUT}, status=403)
                    return
            result = library.request(action, params)
            if private_closed:
                result = (
                    agent_snapshot(result)
                    if action == "snapshot"
                    else agent_brain_result(library, action, result)
                )
            if self._agent_token_name:
                result = agent_left_out_result(library, action, result)
        except WorkflowBundleError as exc:
            self._send_json({"error": str(exc)}, status=403)
            return
        except (
            zipfile.BadZipFile,
            FileNotFoundError,
            NotADirectoryError,
            IsADirectoryError,
            KeyError,
        ) as exc:
            # A missing file, a bad archive or an unknown item: the request's problem.
            log.debug("Second Brain action %s rejected (%s)", action, type(exc).__name__)
            self._send_json({"error": str(exc)}, status=400)
            return
        except (OSError, sqlite3.Error, TypeError) as exc:
            log.exception("Second Brain action %s failed", action)
            self._send_json({"error": str(exc)}, status=500)
            return
        self._send_json({"result": result})

    # A project's worktree setup and the user's approval of it. Approving lets the setup
    # run commands in every new worktree, so agent tokens can read it but never approve
    # it (agent_request_refusal).

    @ROUTES.route("GET", "/api/worktree-setup")
    def _worktree_setup_status(self, request: RouteRequest) -> Any:
        project = _optional_query(request.query, "project")
        if project is None:
            raise ValueError("project is required")
        return worktree_setup.status(self._request_data_dir(request.query), project)

    @ROUTES.route("GET", "/api/worktree-setup/pending")
    def _worktree_setup_pending(self, request: RouteRequest) -> Any:
        base = self._request_data_dir(request.query)
        return {"pending": worktree_setup.Approvals(base).pending()}

    @ROUTES.route("POST", "/api/worktree-setup/approve", body=8192)
    def _worktree_setup_approve(self, request: RouteRequest) -> Any:
        body = request.body or {}
        return worktree_setup.approve(
            self._request_data_dir(request.query),
            str(body.get("project") or ""),
            str(body.get("sha256") or ""),
        )

    @ROUTES.route("POST", "/api/worktree-setup/revoke", body=8192)
    def _worktree_setup_revoke(self, request: RouteRequest) -> Any:
        body = request.body or {}
        return worktree_setup.revoke(
            self._request_data_dir(request.query), str(body.get("project") or "")
        )

    # Rattish runs across workflows. A run's workflowId is optional: the run ID finds it.
    # Their errors are ValueErrors, which the central handler answers with 400.

    @ROUTES.route("GET", "/api/rattish/runs")
    def _rattish_runs(self, request: RouteRequest) -> Any:
        from gofer.ui.workflow_runs import all_workflow_runs_payload

        query = request.query
        return all_workflow_runs_payload(
            self._request_data_dir(query),
            status=_optional_query(query, "status"),
            workflow_ids=[item for item in query.get("workflow", []) if item],
            since=_optional_query(query, "since"),
            until=_optional_query(query, "until"),
            started_by=_optional_query(query, "startedBy"),
            limit=_optional_int_query(query, "limit"),
            offset=_int_query(query, "offset", 0),
        )

    @ROUTES.route("POST", "/api/rattish/runs/{run_id}/rerun", body=0)
    def _rattish_run_rerun(self, request: RouteRequest) -> Any:
        """Run a workflow again with one run's saved inputs and trigger events.

        The body may name ``workflowId``, give ``inputs`` for the password inputs the run
        didn't save, and ask for ``background``.
        """
        from gofer.ui.api import rerun_workflow_payload

        body = request.body or {}
        inputs = body.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            raise WorkflowRunError("inputs must be an object of input names to values")
        workflow_id = body.get("workflowId")
        if workflow_id is not None and not isinstance(workflow_id, str):
            raise WorkflowRunError("workflowId must be a string")
        payload = self._run_async(
            rerun_workflow_payload(
                request.params["run_id"],
                self._request_data_dir(request.query),
                workflow_id=workflow_id or None,
                inputs=inputs,
                background=body.get("background") is True,
                started_by=_api_started_by(body),
            )
        )
        return {"run": payload}

    @ROUTES.route("GET", "/api/rattish/runs/{run_id}/failure", errors=((RunFailureError, 404),))
    def _rattish_run_failure(self, request: RouteRequest) -> Any:
        """What Ask Rem about this failure attaches for one run (``gofer.ui.run_failure``).

        ``nodeId`` names the node to describe, ``workflow`` the run itself; without it,
        the node that failed.
        """
        from gofer.ui.run_failure import run_failure_payload

        query = request.query
        return run_failure_payload(
            request.params["run_id"],
            self._request_data_dir(query),
            workflow_id=_optional_query(query, "workflowId"),
            node_id=_optional_query(query, "nodeId"),
        )

    # A worktree run's changes: GET reads them, land and discard act on them. A land
    # conflict is an answer, not a failure: nothing changed and it names the files.

    @ROUTES.route("GET", "/api/rattish/runs/{run_id}/changes")
    def _rattish_run_changes(self, request: RouteRequest) -> Any:
        from gofer.ui.api import run_changes_payload

        return run_changes_payload(
            request.params["run_id"],
            self._request_data_dir(request.query),
            workflow_id=_optional_query(request.query, "workflowId"),
        )

    @ROUTES.route("POST", "/api/rattish/runs/{run_id}/land", body=0)
    def _rattish_run_land(self, request: RouteRequest) -> Any:
        """Land the run's branch. ``expectedTip`` is the branch tip the user reviewed."""
        from gofer.ui.api import land_run_payload

        base = self._request_data_dir(request.query)
        workflow_id = _run_changes_workflow_id(request)
        tip = (request.body or {}).get("expectedTip")
        if tip is not None and not isinstance(tip, str):
            raise WorkflowRunError("expectedTip must be a commit hash")
        return land_run_payload(
            request.params["run_id"], base, workflow_id=workflow_id, expected_tip=tip
        )

    @ROUTES.route("POST", "/api/rattish/runs/{run_id}/discard", body=0)
    def _rattish_run_discard(self, request: RouteRequest) -> Any:
        from gofer.ui.api import discard_run_payload

        base = self._request_data_dir(request.query)
        workflow_id = _run_changes_workflow_id(request)
        return discard_run_payload(request.params["run_id"], base, workflow_id=workflow_id)

    # Workflow secrets. No response or error ever contains a saved value.

    @ROUTES.route("GET", "/api/rattish/secrets")
    def _secrets_list(self, request: RouteRequest) -> Any:
        return rattish_secrets.secrets_payload(
            self._request_data_dir(request.query), _optional_query(request.query, "workflowId")
        )

    @ROUTES.route("POST", "/api/rattish/secrets", body=rattish_secrets.MAX_BODY_BYTES)
    def _secret_save(self, request: RouteRequest) -> Any:
        return rattish_secrets.save_secret_payload(
            self._request_data_dir(request.query),
            request.body or {},
            _optional_query(request.query, "workflowId"),
        )

    @ROUTES.route("DELETE", "/api/rattish/secrets/{name}")
    def _secret_remove(self, request: RouteRequest) -> Any:
        return rattish_secrets.remove_secret_payload(
            self._request_data_dir(request.query),
            request.params["name"],
            _optional_query(request.query, "workflowId"),
        )

    def _answer_unreadable_secret(self, _exc: Exception) -> None:
        self._send_json({"error": "Send a secret name and value as JSON."}, status=400)

    # Workflow shortcuts: system-wide keys that run a workflow (Settings > Devices). Only
    # the owner adds, removes or runs one; agent tokens can only read the list.

    @ROUTES.route("GET", "/api/workflow-shortcuts")
    def _shortcuts_list(self, request: RouteRequest) -> Any:
        return workflow_shortcuts.list_payload(
            self._request_data_dir(request.query),
            details=_optional_query(request.query, "details") == "1",
            workflow_id=_optional_query(request.query, "workflowId"),
        )

    @ROUTES.route("POST", "/api/workflow-shortcuts", body=16384)
    def _shortcut_set(self, request: RouteRequest) -> Any:
        return workflow_shortcuts.set_payload(
            self._request_data_dir(request.query), request.body or {}
        )

    @ROUTES.route("GET", "/api/workflow-shortcuts/target")
    def _shortcut_target(self, request: RouteRequest) -> Any:
        return workflow_shortcuts.target_payload(
            self._request_data_dir(request.query), _optional_query(request.query, "workflowId")
        )

    @ROUTES.route("GET", "/api/workflow-shortcuts/{shortcut_id}")
    def _shortcut_detail(self, request: RouteRequest) -> Any:
        return workflow_shortcuts.detail_payload(
            self._request_data_dir(request.query), request.params["shortcut_id"]
        )

    @ROUTES.route("DELETE", "/api/workflow-shortcuts/{shortcut_id}")
    def _shortcut_remove(self, request: RouteRequest) -> Any:
        return workflow_shortcuts.remove_payload(
            self._request_data_dir(request.query), request.params["shortcut_id"]
        )

    @ROUTES.route(
        "POST", "/api/workflow-shortcuts/{shortcut_id}/run", body=workflow_shortcuts.MAX_BODY_BYTES
    )
    def _shortcut_run(self, request: RouteRequest) -> Any:
        base = self._request_data_dir(request.query)
        return self._run_async(
            workflow_shortcuts.run_payload(base, request.params["shortcut_id"], request.body or {})
        )

    @ROUTES.route("GET", "/api/workflow-shortcuts/{shortcut_id}/changes")
    def _shortcut_changes(self, request: RouteRequest) -> Any:
        return workflow_shortcuts.changes_payload(
            self._request_data_dir(request.query), request.params["shortcut_id"]
        )

    @ROUTES.route("POST", "/api/workflow-shortcuts/{shortcut_id}/approve", body=16384)
    def _shortcut_approve(self, request: RouteRequest) -> Any:
        # Agent tokens never get here (agent_request_refusal), so this is the user's own
        # Approve this version, as for a trigger.
        return workflow_shortcuts.approve_payload(
            self._request_data_dir(request.query), request.params["shortcut_id"], request.body or {}
        )

    def _answer_shortcut_changed(self, exc: ShortcutChanged) -> None:
        self._send_json(
            {
                "error": str(exc),
                "code": exc.code,
                "state": exc.state,
                "shortcutId": exc.shortcut_id,
                "workflowName": exc.workflow_name,
            },
            status=409,
        )

    # Workflows: the routes under Mount("/api/workflows") above. A literal segment wins
    # over a parameter, so /api/workflows/convert-toml never reaches /{workflow_id}.

    @ROUTES.route("GET", "/api/workflows", errors=((DiscoveryCancelled, "_answer_nothing"),))
    def _workflows_list(self, request: RouteRequest) -> Any:
        return list_workflow_payloads(
            self._request_data_dir(request.query), check_cancelled=self._check_discovery_cancelled
        )

    def _answer_nothing(self, _exc: Exception) -> None:
        """The client closed the request, so there is nobody to answer."""

    @ROUTES.route("POST", "/api/workflows", body=0, status=201, errors=_WORKFLOW_EXISTS)
    def _workflow_create(self, request: RouteRequest) -> Any:
        body = request.body or {}
        project_root = self._granted_project_root(body)
        registry_dir = self._request_data_dir(request.query)
        name = str(body.get("name", ""))
        if body.get("starter"):
            workflow = create_workflow_from_starter_payload(
                str(body.get("starter")),
                name,
                project_root,
                provider=str(body.get("provider") or "") or None,
                registry_dir=registry_dir,
            )
        else:
            workflow = create_registered_workflow_payload(
                name, project_root, registry_dir=registry_dir
            )
        return {"workflow": workflow}

    @ROUTES.route(
        "POST", "/api/workflows/convert-toml", body=0, status=201, errors=_WORKFLOW_EXISTS
    )
    def _workflow_convert_toml(self, request: RouteRequest) -> Any:
        body = request.body or {}
        project_root = self._granted_project_root(body)
        return convert_toml_workflow_payload(
            Path(str(body.get("path", ""))), project_root, self._request_data_dir(request.query)
        )

    @ROUTES.route("DELETE", "/api/workflows/{workflow_id}", errors=((WorkflowUpdateError, 404),))
    def _workflow_delete(self, request: RouteRequest) -> Any:
        return delete_workflow_payload(
            request.params["workflow_id"], self._request_data_dir(request.query)
        )

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/rename", body=0, errors=_WORKFLOW_EXISTS)
    def _workflow_rename(self, request: RouteRequest) -> Any:
        body = request.body or {}
        workflow = rename_workflow_payload(
            request.params["workflow_id"],
            str(body.get("name", "")),
            self._request_data_dir(request.query),
        )
        return {"workflow": workflow}

    @ROUTES.route(
        "POST",
        "/api/workflows/{workflow_id}/duplicate",
        body=0,
        status=201,
        errors=_WORKFLOW_EXISTS,
    )
    def _workflow_duplicate(self, request: RouteRequest) -> Any:
        workflow = duplicate_workflow_payload(
            request.params["workflow_id"],
            (request.body or {}).get("name"),
            self._request_data_dir(request.query),
        )
        return {"workflow": workflow}

    @ROUTES.route("GET", "/api/workflows/{workflow_id}/run-worktrees", errors=_WORKFLOW_MISSING)
    def _workflow_run_worktrees(self, request: RouteRequest) -> Any:
        return workflow_run_worktrees_payload(
            request.params["workflow_id"], self._request_data_dir(request.query)
        )

    # The Rattish document the Studio window edits.

    @ROUTES.route(
        "GET", "/api/workflows/{workflow_id}/document", errors=((RattishEditorError, 404),)
    )
    def _workflow_document(self, request: RouteRequest) -> Any:
        document = open_rattish_document_payload(
            request.params["workflow_id"], self._request_data_dir(request.query)
        )
        return {"document": document}

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/document/analyze", body=0)
    def _workflow_document_analyze(self, request: RouteRequest) -> Any:
        source = (request.body or {}).get("source")
        if not isinstance(source, str):
            raise ValueError("Rattish document analysis requires string source.")
        document = analyze_rattish_document_payload(
            request.params["workflow_id"], source, self._request_data_dir(request.query)
        )
        return {"document": document}

    @ROUTES.route(
        "POST", "/api/workflows/{workflow_id}/document/save", body=0, errors=_REVISION_CONFLICT
    )
    def _workflow_document_save(self, request: RouteRequest) -> Any:
        body = request.body or {}
        source = body.get("source")
        expected_revision = body.get("expectedRevision")
        if not isinstance(source, str) or not isinstance(expected_revision, str):
            raise ValueError("Rattish document save requires source and expectedRevision strings.")
        workflow_id = request.params["workflow_id"]
        base = self._request_data_dir(request.query)
        document = save_rattish_document_payload(workflow_id, source, expected_revision, base)
        self._approve_after_studio_save(base, workflow_id)
        return {"document": document}

    @ROUTES.route(
        "POST", "/api/workflows/{workflow_id}/document/mutate", body=0, errors=_REVISION_CONFLICT
    )
    def _workflow_document_mutate(self, request: RouteRequest) -> Any:
        body = request.body or {}
        mutations = body.get("mutations")
        expected_revision = body.get("expectedRevision")
        if (
            not isinstance(mutations, list)
            or not all(isinstance(item, dict) for item in mutations)
            or not isinstance(expected_revision, str)
        ):
            raise ValueError("Rattish document mutation requires mutations and expectedRevision.")
        workflow_id = request.params["workflow_id"]
        base = self._request_data_dir(request.query)
        document = mutate_rattish_document_payload(workflow_id, mutations, expected_revision, base)
        self._approve_after_studio_save(base, workflow_id)
        return {"document": document}

    def _approve_after_studio_save(self, base: Path, workflow_id: str) -> None:
        # Saving in the Studio window approves the workflow file it wrote, and only that
        # file, for the workflow's triggers and its shortcut.
        rattish_triggers.approve_after_studio_save(base, workflow_id)
        workflow_shortcuts.approve_after_studio_save(base, workflow_id)

    @ROUTES.route(
        "POST", "/api/workflows/{workflow_id}/metadata", body=0, errors=_REVISION_CONFLICT
    )
    def _workflow_metadata_save(self, request: RouteRequest) -> Any:
        body = request.body or {}
        metadata = body.get("metadata")
        expected_revision = body.get("expectedRevision")
        if not isinstance(metadata, dict) or not isinstance(expected_revision, str):
            raise ValueError("Rattish metadata save requires metadata and expectedRevision.")
        return save_rattish_metadata_payload(
            request.params["workflow_id"],
            metadata,
            expected_revision,
            self._request_data_dir(request.query),
        )

    def _answer_revision_conflict(self, exc: RattishRevisionConflict) -> None:
        self._send_json(exc.to_payload(), status=409)

    # Plan, run, resume and stop a workflow.

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/plan", body=0)
    def _workflow_plan(self, request: RouteRequest) -> Any:
        body = request.body or {}
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
            request.params["workflow_id"],
            self._request_data_dir(request.query),
            trigger_context=trigger_context,
            parameters=parameters,
            inputs=inputs,
        )
        return {"plan": plan}

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/run", body=0)
    def _workflow_run(self, request: RouteRequest) -> Any:
        body = request.body or {}
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
        run_options["started_by"] = _api_started_by(body)
        # Run from a node, reusing an earlier run's upstream results.
        for key, option in (("fromNode", "from_node"), ("reuseRunId", "reuse_run_id")):
            if body.get(key) is not None:
                if not isinstance(body[key], str) or not body[key]:
                    raise WorkflowRunError(f"{key} must be a non-empty string")
                run_options[option] = body[key]
        trigger_context = body.get("triggerContext")
        if trigger_context is not None and not isinstance(trigger_context, dict):
            raise WorkflowRunError("triggerContext must be an object")
        parameters = body.get("parameters")
        inputs = body.get("inputs")
        if parameters is not None and not isinstance(parameters, dict):
            raise WorkflowRunError("parameters must be an object")
        if inputs is not None and not isinstance(inputs, dict):
            raise WorkflowRunError("inputs must be an object")
        if parameters is not None or inputs is not None:
            run_options["parameters"] = parameters
            run_options["inputs"] = inputs
        result = self._run_async(
            run_workflow_payload(
                request.params["workflow_id"],
                self._request_data_dir(request.query),
                dry_run=dry_run,
                trigger_context=trigger_context,
                **run_options,
            )
        )
        return {"plan" if dry_run else "run": result}

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/runs/{run_id}/resume", body=0)
    def _workflow_run_resume(self, request: RouteRequest) -> Any:
        body = request.body or {}
        trigger_context = body.get("triggerContext")
        if trigger_context is not None and not isinstance(trigger_context, dict):
            raise WorkflowRunError("triggerContext must be an object")
        resume_inputs = body.get("inputs")
        if resume_inputs is not None and not isinstance(resume_inputs, dict):
            raise WorkflowRunError("inputs must be an object of input names to values")
        run = self._run_async(
            resume_workflow_payload(
                request.params["workflow_id"],
                self._request_data_dir(request.query),
                run_id=request.params["run_id"],
                from_node=body.get("fromNode"),
                only_node=body.get("onlyNode"),
                skip_cache=bool(body.get("skipCache", False)),
                force=bool(body.get("force", False)),
                trigger_context=trigger_context,
                background=body.get("background") is True,
                started_by=_api_started_by(body),
                inputs=resume_inputs,
            )
        )
        return {"run": run}

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/runs/{run_id}/stop")
    def _workflow_run_stop(self, request: RouteRequest) -> Any:
        return stop_workflow_run_payload(
            request.params["workflow_id"],
            self._request_data_dir(request.query),
            run_id=request.params["run_id"],
        )

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/stop")
    def _workflow_stop(self, request: RouteRequest) -> Any:
        return stop_workflow_run_payload(
            request.params["workflow_id"], self._request_data_dir(request.query)
        )

    # Approval steps waiting in a workflow's runs.

    @ROUTES.route("GET", "/api/workflows/{workflow_id}/approvals")
    def _workflow_approvals(self, request: RouteRequest) -> Any:
        return list_workflow_approvals_payload(
            request.params["workflow_id"],
            self._request_data_dir(request.query),
            include_decided=request.query.get("all", ["1"])[0] != "0",
        )

    @ROUTES.route(
        "POST",
        "/api/workflows/{workflow_id}/approvals/{run_id}/{node_id}/approve",
        body=0,
        parity_key="POST /api/workflows/*/approvals/*/approve",
    )
    def _workflow_approval_approve(self, request: RouteRequest) -> Any:
        return self._decide_workflow_approval(request, "approved")

    @ROUTES.route(
        "POST",
        "/api/workflows/{workflow_id}/approvals/{run_id}/{node_id}/reject",
        body=0,
        parity_key="POST /api/workflows/*/approvals/*/reject",
    )
    def _workflow_approval_reject(self, request: RouteRequest) -> Any:
        return self._decide_workflow_approval(request, "rejected")

    def _decide_workflow_approval(self, request: RouteRequest, decision: str) -> Any:
        body = request.body or {}
        return decide_workflow_approval_payload(
            request.params["workflow_id"],
            request.params["run_id"],
            request.params["node_id"],
            decision,
            self._request_data_dir(request.query),
            decided_by=str(body.get("by") or "ui"),
            notes=str(body.get("notes") or ""),
            choice=(None if body.get("choice") is None else str(body["choice"])),
            reply=str(body.get("reply") or ""),
        )

    # Run logs, their events and how long they are kept.

    @ROUTES.route("GET", "/api/workflows/{workflow_id}/logs")
    def _workflow_logs(self, request: RouteRequest) -> Any:
        query = request.query
        return list_workflow_run_logs_payload(
            request.params["workflow_id"],
            self._request_data_dir(query),
            offset=_int_query(query, "offset", 0),
            limit=_optional_int_query(query, "limit"),
            status=_optional_query(query, "status"),
            trigger_type=_optional_query(query, "trigger"),
            search=_optional_query(query, "q"),
            started_after=_optional_datetime_query(query, "startedAfter"),
            started_before=_optional_datetime_query(query, "startedBefore"),
        )

    @ROUTES.route("GET", "/api/workflows/{workflow_id}/logs/latest")
    def _workflow_log_latest(self, request: RouteRequest) -> Any:
        log_payload = latest_workflow_log_payload(
            request.params["workflow_id"], self._request_data_dir(request.query)
        )
        return {"log": log_payload}

    @ROUTES.route("GET", "/api/workflows/{workflow_id}/logs/{run_id}", errors=_RUN_LOG_MISSING)
    def _workflow_log(self, request: RouteRequest) -> Any:
        query = request.query
        log_payload = workflow_run_log_payload(
            request.params["workflow_id"],
            request.params["run_id"],
            self._request_data_dir(query),
            offset=_optional_int_query(query, "offset"),
            limit=_optional_int_query(query, "limit"),
            tail_bytes=_optional_int_query(query, "tailBytes"),
            include_details=query.get("details", ["1"])[0] != "0",
        )
        return {"log": log_payload}

    @ROUTES.route(
        "GET",
        "/api/workflows/{workflow_id}/logs/{run_id}/events",
        errors=_RUN_LOG_MISSING,
        parity_key="GET /api/workflows/*/events",
    )
    def _workflow_log_events(self, request: RouteRequest) -> Any:
        events = workflow_run_events_payload(
            request.params["workflow_id"],
            request.params["run_id"],
            self._request_data_dir(request.query),
        )
        return {"events": events}

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/logs/prune", body=0)
    def _workflow_logs_prune(self, request: RouteRequest) -> Any:
        body = request.body or {}
        return prune_workflow_run_logs_payload(
            request.params["workflow_id"],
            self._request_data_dir(request.query),
            keep_last=_optional_body_int(body, "keepLast"),
            keep_days=_optional_body_int(body, "keepDays"),
            keep_failed_days=_optional_body_int(body, "keepFailedDays"),
            dry_run=bool(body.get("dryRun", True)),
        )

    @ROUTES.route("GET", "/api/workflows/{workflow_id}/retention")
    def _workflow_retention(self, request: RouteRequest) -> Any:
        return retention_settings_payload(
            self._request_data_dir(request.query), request.params["workflow_id"]
        )

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/retention", body=0)
    def _workflow_retention_save(self, request: RouteRequest) -> Any:
        return update_retention_settings_payload(
            self._request_data_dir(request.query),
            workflow_id=request.params["workflow_id"],
            settings=request.body or {},
        )

    # A workflow's test sets and their results.

    @ROUTES.route("GET", "/api/workflows/{workflow_id}/tests", errors=_TESTS_MISSING)
    def _workflow_tests(self, request: RouteRequest) -> Any:
        return workflow_tests_payload(
            request.params["workflow_id"], self._request_data_dir(request.query)
        )

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/tests", body=0)
    def _workflow_tests_save(self, request: RouteRequest) -> Any:
        return save_workflow_tests_payload(
            request.params["workflow_id"], self._request_data_dir(request.query), request.body or {}
        )

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/tests/estimate", body=0)
    def _workflow_tests_estimate(self, request: RouteRequest) -> Any:
        return estimate_workflow_tests_payload(
            request.params["workflow_id"], self._request_data_dir(request.query), request.body or {}
        )

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/tests/run", body=0, status=202)
    def _workflow_tests_run(self, request: RouteRequest) -> Any:
        return start_workflow_tests_payload(
            request.params["workflow_id"], self._request_data_dir(request.query), request.body or {}
        )

    @ROUTES.route(
        "GET", "/api/workflows/{workflow_id}/tests/results/{result_id}", errors=_TESTS_MISSING
    )
    def _workflow_test_result(self, request: RouteRequest) -> Any:
        return workflow_test_result_payload(
            request.params["workflow_id"],
            self._request_data_dir(request.query),
            request.params["result_id"],
        )

    @ROUTES.route("POST", "/api/workflows/{workflow_id}/tests/results/{result_id}/stop")
    def _workflow_test_result_stop(self, request: RouteRequest) -> Any:
        return stop_workflow_tests_payload(
            request.params["workflow_id"],
            self._request_data_dir(request.query),
            request.params["result_id"],
        )

    # A workflow's result cache. A bad workflowId is a ValueError, answered with 400.

    def _answer_cache_unreadable(self, exc: OSError) -> None:
        self._send_json({"error": f"Could not read or clear the result cache: {exc}"}, status=500)

    @ROUTES.route("GET", "/api/rattish/cache", errors=((OSError, "_answer_cache_unreadable"),))
    def _rattish_cache_size(self, request: RouteRequest) -> Any:
        return rattish_cache.cache_payload(
            self._request_data_dir(request.query), _optional_query(request.query, "workflowId")
        )

    @ROUTES.route("DELETE", "/api/rattish/cache", errors=((OSError, "_answer_cache_unreadable"),))
    def _rattish_cache_clear(self, request: RouteRequest) -> Any:
        return rattish_cache.clear_cache_payload(
            self._request_data_dir(request.query), _optional_query(request.query, "workflowId")
        )

    @ROUTES.route("GET", "/api/rattish/starters", errors=((WorkflowCreateError, 500),))
    def _rattish_starters(self, _request: RouteRequest) -> Any:
        return starter_workflows_payload()

    # Rattish triggers: the routes under Mount("/api/rattish/triggers") above.

    @ROUTES.route("GET", "/api/rattish/triggers")
    def _triggers_list(self, request: RouteRequest) -> Any:
        return rattish_triggers.triggers_payload(
            self._request_data_dir(request.query), _optional_query(request.query, "workflowId")
        )

    @ROUTES.route("POST", "/api/rattish/triggers", body=0, status=201)
    def _trigger_create(self, request: RouteRequest) -> Any:
        return rattish_triggers.create_trigger_payload(
            self._request_data_dir(request.query), request.body or {}
        )

    @ROUTES.route("GET", "/api/rattish/triggers/pause")
    def _triggers_pause_state(self, request: RouteRequest) -> Any:
        return rattish_triggers.trigger_pause_payload(self._request_data_dir(request.query))

    @ROUTES.route("POST", "/api/rattish/triggers/pause", body=0)
    def _triggers_set_pause(self, request: RouteRequest) -> Any:
        return rattish_triggers.set_trigger_pause_payload(
            self._request_data_dir(request.query), request.body or {}
        )

    @ROUTES.route("GET", "/api/rattish/triggers/budget")
    def _triggers_budget(self, request: RouteRequest) -> Any:
        return rattish_triggers.daily_budget_payload(self._request_data_dir(request.query))

    @ROUTES.route("POST", "/api/rattish/triggers/budget", body=0)
    def _triggers_set_budget(self, request: RouteRequest) -> Any:
        return rattish_triggers.set_daily_budget_payload(
            self._request_data_dir(request.query), request.body or {}
        )

    @ROUTES.route("POST", "/api/rattish/triggers/preview", body=0)
    def _trigger_preview(self, request: RouteRequest) -> Any:
        return rattish_triggers.preview_trigger_payload(request.body or {})

    @ROUTES.route("POST", "/api/rattish/triggers/login", body=0)
    def _triggers_start_at_login(self, request: RouteRequest) -> Any:
        return rattish_triggers.set_start_at_login_payload(
            self._request_data_dir(request.query), request.body or {}
        )

    @ROUTES.route("GET", "/api/rattish/triggers/{trigger_id}/changes")
    def _trigger_changes(self, request: RouteRequest) -> Any:
        return rattish_triggers.trigger_changes_payload(
            self._request_data_dir(request.query), request.params["trigger_id"]
        )

    @ROUTES.route("POST", "/api/rattish/triggers/{trigger_id}/approve", body=0)
    def _trigger_approve(self, request: RouteRequest) -> Any:
        # Agent tokens never get here (agent_request_refusal), so this is the user's own
        # Approve this version, as with a ticket's Accept.
        return rattish_triggers.approve_trigger_payload(
            self._request_data_dir(request.query), request.params["trigger_id"], request.body or {}
        )

    @ROUTES.route("POST", "/api/rattish/triggers/{trigger_id}/rotate-token")
    def _trigger_rotate_token(self, request: RouteRequest) -> Any:
        return rattish_triggers.rotate_trigger_token_payload(
            self._request_data_dir(request.query), request.params["trigger_id"]
        )

    @ROUTES.route("POST", "/api/rattish/triggers/{trigger_id}", body=0)
    def _trigger_update(self, request: RouteRequest) -> Any:
        return rattish_triggers.update_trigger_payload(
            self._request_data_dir(request.query), request.params["trigger_id"], request.body or {}
        )

    @ROUTES.route("DELETE", "/api/rattish/triggers/{trigger_id}")
    def _trigger_delete(self, request: RouteRequest) -> Any:
        return rattish_triggers.delete_trigger_payload(
            self._request_data_dir(request.query), request.params["trigger_id"]
        )

    # 409 answers that carry more than the message, shared by triggers and shortcuts.

    def _answer_approval_conflict(self, exc: Exception) -> None:
        self._send_json({"error": str(exc), "conflict": True}, status=409)

    def _answer_unseen_changes(self, exc: UnseenChanges | ShortcutUnseenChanges) -> None:
        self._send_json({"error": str(exc), "unseen": exc.files}, status=409)

    def _dispatch_social_request(self, method: str, parsed: Any) -> None:
        """Social posts. An agent token acts as Rem, which can only propose and revise."""
        from gofer.social.api import SocialApiError, handle

        parts = [part for part in parsed.path.removeprefix("/api/social").split("/") if part]
        body = self._read_json(limit=256 * 1024) if method in {"POST", "PUT", "PATCH"} else {}
        if not isinstance(body, dict):
            self._send_json({"error": "Expected a JSON object."}, status=400)
            return
        actor = "rem" if self._agent_token_name else "user"
        proposer = body.pop("proposer", None)
        if (
            actor == "user"
            and isinstance(proposer, str)
            and re.fullmatch(r"cli|rem|workflow:[\w.@:-]{1,80}", proposer)
        ):
            # The CLI names who is proposing. It can only lower its own authority.
            actor = proposer
        try:
            payload = handle(self.server.social, method, parts, actor, body, parse_qs(parsed.query))
        except SocialApiError as exc:
            self._send_json({"error": str(exc), "code": exc.code}, status=exc.status)
            return
        self._send_json(payload)

    @ROUTES.route(
        "POST",
        "/api/organization-webhooks/{organization_id}/{webhook_id}",
        status=202,
        errors=((WebhookRefused, 403),),
    )
    def _organization_webhook(self, request: RouteRequest) -> Any:
        """A routine's webhook delivery. _requires_ui_api_auth lets it in without the UI
        token, so receive checks its signature before anything else reads it.

        The body is read here, after the organizations check, so a delivery while
        organizations are off gets 403 whatever it sent.
        """
        if not self.server.organizations.enabled:
            raise WebhookRefused("Experimental organizations is disabled")
        from gofer.ui.organization_webhooks import receive

        self._read_json(limit=65536)
        try:
            return receive(
                self.server.organizations,
                request.params["organization_id"],
                request.params["webhook_id"],
                self.headers,
                self._raw_body,
            )
        except ValueError as exc:
            raise WebhookRefused(str(exc)) from exc

    def _dispatch_organization_request(self, method: str) -> None:
        if method not in {"GET", "POST"}:
            self._send_json({"error": "Method not allowed"}, status=405)
            return
        if (
            method == "GET"
            and _optional_query(parse_qs(urlparse(self.path).query), "action") == "availability"
        ):
            self._send_json(
                {"result": {"enabled": self.server.organizations.enabled, "experimental": True}}
            )
            return
        if not self.server.organizations.enabled:
            self._send_json({"error": "Experimental organizations is disabled"}, status=403)
            return
        query = parse_qs(urlparse(self.path).query)
        body = self._read_json(limit=24 * 1024 * 1024) if method == "POST" else {}
        project = body.get("projectRoot") or _optional_query(query, "projectRoot")
        project = project or ""
        if not isinstance(project, str) or (project and not Path(project).is_absolute()):
            raise ValueError("Project folder must be an absolute path")
        action = body.get("action") or _optional_query(query, "action") or "list"
        read_actions = {
            "list",
            "read",
            "history",
            "events",
            "export",
            "help",
            "providers",
            "run_events",
            "audit_verify",
            "run_input",
            "knowledge_search",
            "knowledge_read",
            "ticket_changes",
            "ticket_patch",
            "repository",
        } | READ_OPERATIONS
        if method == "GET" and action not in read_actions:
            raise ValueError("Mutations require POST")
        if method == "POST" and project:
            try:
                self._assert_bundle_path_allowed(
                    Path(project), body.get("grantId"), must_exist=True
                )
            except WorkflowBundleError as exc:
                self._send_json({"error": str(exc)}, status=403)
                return
        params = body.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Expected params object")
        if action in {"import", "import_preview"}:
            if params.get("path"):
                try:
                    root = self._assert_bundle_path_allowed(
                        Path(params["path"]), params.get("grantId"), must_exist=True
                    )
                except WorkflowBundleError as exc:
                    self._send_json({"error": str(exc)}, status=403)
                    return
                params = {
                    **{key: value for key, value in params.items() if key != "path"},
                    "files": read_directory(root),
                }
        if action in {"create", "configure"} and isinstance(params.get("config"), dict):
            # Saved as entered: runs find the folder's library brain each time, so the
            # stored folder doesn't change and raise a new approval request.
            self._validate_second_brain(
                {"workflow": {"remSecondBrain": params["config"].get("remSecondBrain", {})}},
                fill=False,
            )
        if action in {"create", "configure"} and isinstance(params.get("config"), dict):
            oid = body.get("organizationId") or _optional_query(query, "organizationId")
            if action == "configure" and not isinstance(oid, str):
                raise ValueError("organizationId is required")
            current = (
                self.server.organizations.store.get(project, str(oid))["config"]
                if action == "configure"
                else {}
            )
            previous = set(current.get("projectRoots", [])) | {
                p["workspacePath"] for p in current.get("projects", []) if p.get("workspacePath")
            }
            paths = list(params["config"].get("projectRoots", [])) + [
                p["workspacePath"]
                for p in params["config"].get("projects", [])
                if p.get("workspacePath")
            ]
            for path in paths:
                if path in previous:
                    continue
                if self._agent_token_name and _is_path_inside(
                    _canonical_path_for_containment(Path(path)),
                    _canonical_path_for_containment(self._default_data_dir()),
                ):
                    # Folders in Raticode's own data need no grant from the desktop, which
                    # is for the owner's session; an agent token never hands them out.
                    self._send_json(
                        {
                            "error": "Agent tokens can't give an organization folders inside "
                            "Raticode's data folder",
                            "code": "agent_token_forbidden",
                        },
                        status=403,
                    )
                    return
                try:
                    resolved = self._assert_bundle_path_allowed(
                        Path(path),
                        body.get("workspaceGrants", {}).get(path),
                        must_exist=True,
                    )
                except WorkflowBundleError as exc:
                    self._send_json({"error": str(exc)}, status=403)
                    return
                if not resolved.is_dir():
                    raise ValueError("Organization workspaces must be directories")
        result = self.server.organizations.call(
            str(Path(project).resolve()) if project else "",
            # An agent token acts with Rem's authority: consent waits for the owner.
            f"Rem:token:{self._agent_token_name}" if self._agent_token_name else "user",
            {
                "action": action,
                "organizationId": body.get("organizationId")
                or _optional_query(query, "organizationId"),
                "params": params
                if method == "POST"
                else {
                    key: values[0]
                    for key, values in query.items()
                    if key not in {"action", "projectRoot", "organizationId"}
                },
            },
        )
        self._send_json({"result": result})

    def _dispatch_swarm_request(self, method: str) -> None:
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
        elif method == "DELETE" and swarm_id and not action:
            self._send_json(manager.delete(project_root, swarm_id))
            return
        elif method == "POST" and action == "start":
            expected = body.get("expectedConfigurationHash")
            result = manager.start(
                project_root,
                swarm_id,
                body.get("task", ""),
                workspace_grants=body.get("workspaceGrants"),
                expected_configuration_hash=expected if isinstance(expected, str) else None,
                confirm_full_access=body.get("confirmFullAccess") is True,
            )
        elif method == "POST" and action == "control":
            result = manager.control(
                project_root,
                swarm_id,
                body.get("action", ""),
                workspace_grants=body.get("workspaceGrants"),
            )
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

    # Health, instance proof, computers, devices, previews and the doctor.

    @ROUTES.route("GET", "/api/health")
    def _health(self, _request: RouteRequest) -> Any:
        return {"ok": True}

    @ROUTES.route("GET", "/api/instance/proof")
    def _instance_proof(self, request: RouteRequest) -> Any:
        # Unauthenticated: the CLI asks before it sends the owner token.
        nonce = request.query.get("nonce", [""])[0]
        proof = headless_support.instance_proof(self.server.api_token, nonce)
        if proof is None:
            self._send_json(
                {"error": "nonce must be 32 random bytes as 64 lowercase hex characters"},
                status=400,
            )
            return ANSWERED
        return {"proof": proof}

    @ROUTES.route("GET", "/api/computers")
    def _computers(self, _request: RouteRequest) -> Any:
        return self.server.computers.read()

    @ROUTES.route(
        "POST", "/api/computers", body=0, errors=((ValueError, "_answer_worker_limit_failed"),)
    )
    def _computers_set_max_threads(self, request: RouteRequest) -> Any:
        from gofer.core.computers import ComputerError

        body = request.body or {}
        if set(body) - {"computer_id", "max_threads"}:
            raise ComputerError("Send computer_id and max_threads")
        return self.server.computers.set_max_threads(
            body.get("computer_id"), body.get("max_threads")
        )

    def _answer_worker_limit_failed(self, exc: ValueError) -> None:
        self._request_failed("Changing a computer's worker limit", exc)

    @ROUTES.route("GET", "/api/computers/placement")
    def _computers_placement(self, _request: RouteRequest) -> Any:
        return self.server.computers.placement()

    @ROUTES.route(
        "POST",
        "/api/computers/placement",
        body=0,
        errors=((ValueError, "_answer_placement_failed"),),
    )
    def _computers_set_placement(self, request: RouteRequest) -> Any:
        body = request.body or {}
        if set(body) == {"order"}:
            # The user's order of computers, which overflow fills in turn.
            return self.server.computers.set_order(body["order"])
        if set(body) - {"consumer", "strategy", "computers", "shared"}:
            raise ValueError("Send order alone, or consumer, strategy, and for manual, computers")
        return self.server.computers.set_placement(
            body.get("consumer"), body.get("strategy"), body.get("computers"), body.get("shared")
        )

    def _answer_placement_failed(self, exc: ValueError) -> None:
        self._request_failed("Changing where work runs", exc)

    @ROUTES.route("GET", "/api/devices")
    def _devices_status(self, _request: RouteRequest) -> Any:
        if self.server.devices:
            return self.server.devices.status()
        return {
            "enabled": False,
            "network_release": False,
            "peers": [],
            "error": "Install the desktop devices dependencies to configure pairing.",
        }

    # The body's action field picks what happens. The route reads no body itself, so a
    # missing device service answers 503 before the body is read.
    @ROUTES.route("POST", "/api/devices")
    def _device_action(self, _request: RouteRequest) -> Any:
        if self.server.devices is None:
            self._send_json({"error": "Device service dependencies unavailable"}, status=503)
            return ANSWERED
        body = {}
        try:
            body = self._read_json(limit=10 * 1024 * 1024)
            if body.get("action") == "workspace_exchange":
                from gofer.ui.device_context import shared_context

                metadata = body.get("metadata")
                if not isinstance(metadata, dict) or not isinstance(body.get("messages"), list):
                    raise ValueError("Missing desktop thread")
                workflow = body.get("workflow") or {}
                self._validate_rem_thread_projects({"workflow": workflow})
                self._validate_second_brain({"workflow": workflow})
                project = metadata.get("projectRoot")
                canonical = None
                if project:
                    try:
                        canonical = self._assert_bundle_path_allowed(
                            Path(str(project)), body.get("grantId"), must_exist=True
                        )
                    except (WorkflowBundleError, OSError):
                        # Deleted/closed scopes remain searchable. They must be
                        # reassigned before dispatch, not block every other thread.
                        canonical = None
                body["context"] = shared_context(
                    metadata, workflow, provider_capabilities_payload()["providers"], canonical
                )
            if body.get("action") == "host_template":
                from gofer.ui.device_context import shared_context

                workflow = body.get("workflow") or {}
                self._validate_rem_thread_projects({"workflow": workflow})
                self._validate_second_brain({"workflow": workflow})
                template = body.get("metadata")
                body["context"] = shared_context(
                    template if isinstance(template, dict) else {},
                    workflow,
                    provider_capabilities_payload()["providers"],
                    None,
                )
            if (
                body.get("action") in {"workspace_poll", "workspace_exchange", "host_template"}
                and self.server.devices.application is not None
            ):
                self.server.devices.application.workspace.providers = _device_provider_catalog()
            if body.get("action") == "authorize_thread":
                context = body.get("context")
                if not isinstance(context, dict):
                    raise ValueError("Missing local thread context")
                canonical_project = self._assert_bundle_path_allowed(
                    Path(str(context.get("project_path", ""))),
                    body.get("grantId"),
                    must_exist=True,
                )
                context["project_path"] = str(canonical_project)
            self._send_json(self.server.devices.action(body))
        except (ValueError, OSError, sqlite3.Error, WorkflowBundleError) as exc:
            # Do not expose raw storage, TLS, or user-input exceptions. Pairing refusals
            # carry fixed codes; storage and keyring failures keep their traceback.
            device_action = str(body.get("action") or "unknown")[:64]
            refusal = (
                str(exc)
                if type(exc).__name__ == "PairingError" and re.fullmatch(r"[a-z_]{1,64}", str(exc))
                else ""
            )
            if isinstance(exc, (OSError, sqlite3.Error)):
                log.warning("Device action %s failed", device_action, exc_info=True)
            elif refusal:
                # The desktop's phone sync retries every few seconds, so one
                # lasting refusal used to log a line every 5 seconds.
                log.log(
                    logging.INFO
                    if _device_refusals.first(f"{device_action}:{refusal}")
                    else logging.DEBUG,
                    "Device action %s refused: %s",
                    device_action,
                    refusal,
                )
            else:
                log.debug("Device action %s rejected (%s)", device_action, type(exc).__name__)
            self._send_json(
                {
                    "error": DEVICE_REFUSAL_MESSAGES.get(
                        refusal,
                        "Device action failed. Check identity, expiry "
                        "and the unlocked OS credential store.",
                    ),
                    # Pairing refusals are fixed codes, never user data or secrets.
                    **({"code": refusal} if refusal else {}),
                },
                status=400,
            )
        return ANSWERED

    # Inline images in chat load through here, so sites never see the renderer's own
    # request (its address, cookies or referrer).
    @ROUTES.route("GET", "/api/media-image", errors=((PreviewError, 422),))
    def _media_image(self, request: RouteRequest) -> Any:
        return {"image": chat_image(request.query.get("url", [""])[0])}

    @ROUTES.route("GET", "/api/link-preview", errors=((PreviewError, 422),))
    def _link_preview(self, request: RouteRequest) -> Any:
        return {"preview": self.server.link_previews.get(request.query.get("url", [""])[0])}

    @ROUTES.route("GET", "/api/doctor")
    def _doctor(self, request: RouteRequest) -> Any:
        return health_payload(self._request_data_dir(request.query))

    # Generation jobs (themes and commit messages run in the background) and themes.

    @ROUTES.route(
        "GET",
        "/api/generation-jobs",
        errors=((ValueError, "_answer_jobs_unlisted"), (OSError, "_answer_jobs_unlisted")),
    )
    def _generation_jobs(self, request: RouteRequest) -> Any:
        query = request.query
        kind = query.get("kind", ["theme"])[0]
        root = ""
        if kind == "commit":
            root = str(
                self._assert_bundle_path_allowed(
                    Path(query.get("projectRoot", [""])[0]),
                    query.get("grantId", [None])[0],
                    must_exist=False,
                )
            )
            # A worktree can disappear after the desktop renews its grant.
            # Do not restore jobs or report a polling failure for that root.
            if not Path(root).is_dir():
                return {"jobs": []}
        return {"jobs": self.server.generation_jobs.list(kind, root, query.get("branch", [""])[0])}

    def _answer_jobs_unlisted(self, exc: Exception) -> None:
        self._request_failed("Listing generation jobs", exc)

    @ROUTES.route(
        "POST",
        "/api/generation-jobs",
        body=CHAT_ATTACHMENT_MAX_TOTAL_BYTES * 2,
        status=202,
        errors=_JOB_NOT_STARTED,
    )
    def _generation_job_start(self, request: RouteRequest) -> Any:
        body = request.body or {}
        root = None
        if body.get("kind") == "commit":
            root = self._assert_bundle_path_allowed(
                Path(str(body.get("projectRoot", ""))), body.get("grantId"), must_exist=True
            )
        return self.server.generation_jobs.start(str(body.get("kind", "")), body, root)

    @ROUTES.route(
        "POST",
        "/api/generation-jobs/dismiss",
        body=CHAT_ATTACHMENT_MAX_TOTAL_BYTES * 2,
        errors=_JOB_NOT_STARTED,
    )
    def _generation_job_dismiss(self, request: RouteRequest) -> Any:
        self.server.generation_jobs.dismiss(str((request.body or {}).get("id", "")))
        return {"ok": True}

    def _answer_job_not_started(self, exc: Exception) -> None:
        self._request_failed("Starting a generation job", exc)

    @ROUTES.route(
        "POST",
        "/api/report-themes/generate",
        body=CHAT_ATTACHMENT_MAX_TOTAL_BYTES * 2,
        errors=(
            (ValueError, "_answer_theme_failed"),
            (OSError, "_answer_theme_failed"),  # TimeoutError is an OSError
        ),
    )
    def _report_theme_generate(self, request: RouteRequest) -> Any:
        from gofer.ui.report_theme_generation import generate_report_theme

        body = request.body or {}
        if "application/x-ndjson" not in self.headers.get("Accept", ""):
            return asyncio.run(generate_report_theme(body))
        self._send_stream_headers()
        asyncio.run(self._stream_report_theme(body))
        return ANSWERED

    def _answer_theme_failed(self, exc: Exception) -> None:
        # ChatProviderError is a ValueError.
        self._request_failed(
            "Generating a report theme", exc, fallback="Rem could not generate the theme."
        )

    # Settings › Providers: sign-in, capabilities, voice tidy, commit messages, accounts
    # and local model server profiles.

    @ROUTES.route("GET", "/api/provider/auth")
    def _provider_auth(self, request: RouteRequest) -> Any:
        return self.server.provider_auth.status(request.query.get("provider", [""])[0])

    @ROUTES.route(
        "POST",
        "/api/provider/auth",
        body=0,
        errors=((ValueError, "_answer_sign_in_failed"), (OSError, "_answer_sign_in_failed")),
    )
    def _provider_auth_action(self, request: RouteRequest) -> Any:
        body = request.body or {}
        provider = str(body.get("provider", ""))
        action = body.get("action", "start")
        if action not in ("start", "cancel"):
            raise ValueError("Unknown authentication action")
        if action == "cancel":
            return self.server.provider_auth.cancel(provider)
        return self.server.provider_auth.start(provider)

    def _answer_sign_in_failed(self, exc: Exception) -> None:
        self._request_failed("Provider sign-in", exc)

    @ROUTES.route("GET", "/api/provider/capabilities")
    def _provider_capabilities(self, request: RouteRequest) -> Any:
        query = request.query
        refresh = query.get("refresh", ["0"])[0] in {"1", "true"}
        if "provider" in query:
            provider = query["provider"][0]
            if provider not in CLI_PROVIDERS:
                self._send_json({"error": "Unknown provider"}, status=400)
                return ANSWERED
            capability = provider_capability_service().provider(provider, refresh=refresh)
            return {"providers": [capability.to_ui_payload()]}
        if query.get("snapshot", ["0"])[0] in {"1", "true"}:
            return provider_capability_service().snapshot_payload()
        return provider_capabilities_payload(refresh=refresh)

    @ROUTES.route(
        "POST",
        "/api/provider/settings",
        body=0,
        errors=(
            (ValueError, "_answer_provider_settings_failed"),
            (OSError, "_answer_provider_settings_failed"),
        ),
    )
    def _provider_settings_save(self, request: RouteRequest) -> Any:
        body = request.body or {}
        provider = body.pop("provider", "")
        save_provider_preference(provider, body)
        return {"saved": True}

    def _answer_provider_settings_failed(self, exc: Exception) -> None:
        self._request_failed("Saving provider settings", exc)

    @ROUTES.route("GET", "/api/provider/voice-tidy")
    def _voice_tidy_settings(self, request: RouteRequest) -> Any:
        if request.query.get("resolve", ["1"])[0] in {"0", "false"}:
            # The chat box only needs to know whether to hide words while listening.
            return {**voice_tidy_preference(), "selection": None}
        from gofer.ui import voice_tidy

        default = request.query.get("defaultProvider", [""])[0] or None
        return {
            **voice_tidy_preference(),
            "selection": voice_tidy.current(default, self.server.rem_home),
        }

    @ROUTES.route(
        "POST",
        "/api/provider/voice-tidy",
        body=0,
        errors=((ValueError, "_answer_voice_tidy_failed"), (OSError, "_answer_voice_tidy_failed")),
    )
    def _voice_tidy_settings_save(self, request: RouteRequest) -> Any:
        save_voice_tidy_preference(request.body or {})
        return {"saved": True, **voice_tidy_preference()}

    def _answer_voice_tidy_failed(self, exc: Exception) -> None:
        self._request_failed("Saving voice tidy settings", exc)

    @ROUTES.route("GET", "/api/provider/commit-settings")
    def _commit_settings(self, _request: RouteRequest) -> Any:
        from gofer.core.commit_attribution import status as co_author_status
        from gofer.core.commit_message_format import DEFAULT_COMMIT_MESSAGE_TEMPLATE

        return {
            **commit_message_preference(),
            "defaultTemplate": DEFAULT_COMMIT_MESSAGE_TEMPLATE,
            "stripCoAuthorsStatus": co_author_status(),
        }

    @ROUTES.route(
        "POST",
        "/api/provider/commit-settings",
        body=0,
        errors=(
            (ValueError, "_answer_commit_settings_failed"),
            (OSError, "_answer_commit_settings_failed"),
        ),
    )
    def _commit_settings_save(self, request: RouteRequest) -> Any:
        save_commit_message_preference(request.body or {})
        return {"saved": True, **commit_message_preference()}

    def _answer_commit_settings_failed(self, exc: Exception) -> None:
        self._request_failed("Saving commit message settings", exc)

    # The Source Control panel commits in the desktop window; it sends the message here
    # first so the same Python check cleans it.
    @ROUTES.route(
        "POST",
        "/api/provider/commit-message/clean",
        body=0,
        errors=(
            (ValueError, "_answer_commit_message_unread"),
            (OSError, "_answer_commit_message_unread"),
        ),
    )
    def _commit_message_clean(self, request: RouteRequest) -> Any:
        from gofer.core.commit_attribution import clean_commit_message

        message = (request.body or {}).get("message")
        if not isinstance(message, str) or len(message) > 72000:
            self._send_json({"error": "Send the commit message as text"}, status=400)
            return ANSWERED
        return {"message": clean_commit_message(message)}

    def _answer_commit_message_unread(self, exc: Exception) -> None:
        self._request_failed("Checking the commit message", exc)

    # Every provider's accounts, or one provider's with ?provider=. The claude-accounts
    # path is the first version's and covers Claude Code only.

    @ROUTES.route("GET", "/api/provider/accounts")
    def _provider_accounts(self, request: RouteRequest) -> Any:
        return self._accounts_overview(request, request.query.get("provider", [""])[0] or None)

    @ROUTES.route("GET", "/api/provider/claude-accounts")
    def _claude_accounts(self, request: RouteRequest) -> Any:
        return self._accounts_overview(request, "claude_code")

    def _accounts_overview(self, request: RouteRequest, provider: str | None) -> Any:
        from gofer.core.provider_accounts import accounts_overview

        return accounts_overview(
            self._request_data_dir(request.query),
            refresh=request.query.get("refresh", [""])[0] == "1",
            provider=provider,
        )

    @ROUTES.route("POST", "/api/provider/accounts", body=0, errors=_ACCOUNT_REFUSED)
    def _provider_account_action(self, request: RouteRequest) -> Any:
        return self._account_action(request, None)

    @ROUTES.route("POST", "/api/provider/claude-accounts", body=0, errors=_ACCOUNT_REFUSED)
    def _claude_account_action(self, request: RouteRequest) -> Any:
        return self._account_action(request, "claude_code")

    def _account_action(self, request: RouteRequest, provider: str | None) -> Any:
        from gofer.core.provider_accounts import account_action

        return account_action(
            request.body or {}, self._request_data_dir(request.query), provider=provider
        )

    @ROUTES.route("GET", "/api/provider/profiles")
    def _provider_profiles(self, request: RouteRequest) -> Any:
        return provider_profiles_payload(self._request_data_dir(request.query))

    @ROUTES.route("POST", "/api/provider/profiles", body=0)
    def _provider_profile_save(self, request: RouteRequest) -> Any:
        return upsert_provider_profile_payload(
            request.body or {}, self._request_data_dir(request.query)
        )

    @ROUTES.route("POST", "/api/provider/profiles/{profile_name}/check", body=16 * 1024)
    def _provider_profile_check(self, request: RouteRequest) -> Any:
        return check_provider_profile_payload(
            request.params["profile_name"],
            (request.body or {}).get("model"),
            self._request_data_dir(request.query),
        )

    @ROUTES.route(
        "DELETE", "/api/provider/profiles/{profile_name}", errors=((ProviderProfileError, 404),)
    )
    def _provider_profile_delete(self, request: RouteRequest) -> Any:
        return delete_provider_profile_payload(
            request.params["profile_name"], self._request_data_dir(request.query)
        )

    # Classifiers: the Jev key, its switch and a key check. Answers never hold the key.

    @ROUTES.route("GET", "/api/classifiers/jev")
    def _jev_status(self, request: RouteRequest) -> Any:
        from gofer.classifiers.jev_settings import jev_status

        return jev_status(self._request_data_dir(request.query))

    @ROUTES.route("POST", "/api/classifiers/jev/key", body=16 * 1024, errors=_JEV_REFUSED)
    def _jev_key_save(self, request: RouteRequest) -> Any:
        from gofer.classifiers.jev_settings import save_jev_key

        body = request.body or {}
        return save_jev_key(body.get("key"), self._request_data_dir(request.query))

    @ROUTES.route("POST", "/api/classifiers/jev/settings", body=16 * 1024, errors=_JEV_REFUSED)
    def _jev_settings_save(self, request: RouteRequest) -> Any:
        from gofer.classifiers.jev_settings import save_jev_settings

        return save_jev_settings(request.body or {}, self._request_data_dir(request.query))

    @ROUTES.route("POST", "/api/classifiers/jev/check", body=16 * 1024, errors=_JEV_REFUSED)
    def _jev_key_check(self, request: RouteRequest) -> Any:
        from gofer.classifiers.jev_settings import check_jev_key

        return self._run_async(check_jev_key(self._request_data_dir(request.query)))

    def _answer_jev_refused(self, exc: ValueError) -> None:
        from gofer.classifiers.jev_settings import JevSettingsError

        message = str(exc) if isinstance(exc, JevSettingsError) else "Invalid Jev settings."
        self._send_json({"error": message}, status=400)

    @ROUTES.route("DELETE", "/api/classifiers/jev/key")
    def _jev_key_remove(self, request: RouteRequest) -> Any:
        from gofer.classifiers.jev_settings import remove_jev_key

        return remove_jev_key(self._request_data_dir(request.query))

    # Settings › Usage: reporting accounts, alerts, the overview and verified work.

    @ROUTES.route(
        "GET", "/api/usage/credentials", errors=((ValueError, "_answer_bad_usage_account"),)
    )
    def _usage_credentials(self, request: RouteRequest) -> Any:
        query = request.query
        return usage_credentials_payload(
            query.get("provider", [""])[0],
            query.get("profile", [None])[0],
            data_dir=self._request_data_dir(query),
        )

    def _answer_bad_usage_account(self, _exc: ValueError) -> None:
        self._send_json({"error": "Invalid usage reporting account."}, status=400)

    @ROUTES.route(
        "POST",
        "/api/usage/credentials",
        body=64 * 1024,
        errors=(
            (UsageCredentialError, 400),
            (ValueError, "_answer_bad_reporting_settings"),
        ),
    )
    def _usage_credentials_save(self, request: RouteRequest) -> Any:
        body = request.body or {}
        return save_usage_credentials(
            body.get("provider", ""),
            body.get("profile"),
            body.get("values", {}),
            body.get("remove", []),
            data_dir=self._request_data_dir(request.query),
        )

    def _answer_bad_reporting_settings(self, _exc: ValueError) -> None:
        self._send_json({"error": "Invalid reporting settings."}, status=400)

    @ROUTES.route("GET", "/api/usage/alerts")
    def _usage_alerts(self, request: RouteRequest) -> Any:
        from gofer.core.usage_alerts import alert_settings

        return alert_settings(self._request_data_dir(request.query))

    @ROUTES.route(
        "POST",
        "/api/usage/alerts",
        body=4 * 1024,
        errors=((ValueError, "_answer_alert_settings_refused"),),
    )
    def _usage_alerts_save(self, request: RouteRequest) -> Any:
        from gofer.core.usage_alerts import save_alert_settings

        body = request.body or {}
        return save_alert_settings(
            self._request_data_dir(request.query),
            enabled=body.get("enabled"),
            thresholds=body.get("thresholds"),
        )

    def _answer_alert_settings_refused(self, exc: ValueError) -> None:
        from gofer.core.usage_alerts import UsageAlertError

        message = str(exc) if isinstance(exc, UsageAlertError) else "Invalid alert settings."
        self._send_json({"error": message}, status=400)

    @ROUTES.route("GET", "/api/usage/overview")
    def _usage_overview(self, request: RouteRequest) -> Any:
        data_dir = self._request_data_dir(request.query)
        return _usage_with_accounts(
            usage_overview(data_dir, **_usage_period(request.query)),
            data_dir,
            getattr(self.server, "rem_home", None),
        )

    @ROUTES.route("POST", "/api/usage/refresh", body=0)
    def _usage_refresh(self, request: RouteRequest) -> Any:
        data_dir = self._request_data_dir(request.query)
        refreshed = self._run_async(
            refresh_usage(data_dir, query=_usage_period(request.query), explicit=True)
        )
        return _usage_with_accounts(refreshed, data_dir, getattr(self.server, "rem_home", None))

    # Settings › Usage › Verified work on its own, for `Raticode usage verified`.
    @ROUTES.route("GET", "/api/usage/verified-work")
    def _usage_verified_work(self, request: RouteRequest) -> Any:
        from gofer.core.usage_history import resolve_range
        from gofer.ui.rem_verified_work import verified_work

        window = resolve_range(**_usage_period(request.query))
        return verified_work(self._request_data_dir(request.query), window)

    # Projects, retention, desktop path grants and Rattish bundles. Their errors are
    # ValueErrors, which the central handler answers with 400 and the message.

    @ROUTES.route(
        "POST", "/api/projects/open", body=0, errors=((DiscoveryCancelled, "_answer_nothing"),)
    )
    def _project_open(self, request: RouteRequest) -> Any:
        body = request.body or {}
        project_root_value = str(body.get("projectRoot", "")).strip()
        if not project_root_value:
            raise WorkflowCreateError("Project folder is required")
        project_root = Path(project_root_value).expanduser()
        try:
            self._assert_bundle_path_allowed(
                project_root, body.get("projectGrantId"), must_exist=True
            )
        except WorkflowBundleError as exc:
            raise WorkflowCreateError(str(exc)) from exc
        return open_project_payload(
            project_root,
            registry_dir=self._request_data_dir(request.query),
            check_cancelled=self._check_discovery_cancelled,
        )

    @ROUTES.route("GET", "/api/retention")
    def _retention(self, request: RouteRequest) -> Any:
        return retention_settings_payload(self._request_data_dir(request.query))

    @ROUTES.route("POST", "/api/retention", body=0)
    def _retention_save(self, request: RouteRequest) -> Any:
        return update_retention_settings_payload(
            self._request_data_dir(request.query), settings=request.body or {}
        )

    @ROUTES.route("POST", "/api/desktop/path-grants", body=0, status=201)
    def _desktop_path_grant(self, request: RouteRequest) -> Any:
        return self._register_desktop_path_grant(request.body or {})

    @ROUTES.route("POST", "/api/rattish/workflows/import/preview", body=0)
    def _rattish_import_preview(self, request: RouteRequest) -> Any:
        body = request.body or {}
        with _bundle_path_from_body(body) as bundle_path:
            if bundle_path is None:
                raise WorkflowBundleError("A .raticode bundle is required")
            if body.get("bundlePath"):
                self._assert_bundle_path_allowed(bundle_path, body.get("grantId"), must_exist=True)
            payload = preview_rattish_bundle_payload(
                bundle_path, resource_limits=self._resource_limits()
            )
        return {"bundle": payload}

    @ROUTES.route("POST", "/api/rattish/workflows/import", body=0, status=201)
    def _rattish_import(self, request: RouteRequest) -> Any:
        body = request.body or {}
        project_root_value = str(body.get("projectRoot", "")).strip()
        if not project_root_value:
            raise WorkflowBundleError("Choose a project folder for the imported workflow")
        project_root = Path(project_root_value).expanduser()
        self._assert_bundle_path_allowed(project_root, body.get("projectGrantId"), must_exist=True)
        with _bundle_path_from_body(body) as bundle_path:
            if bundle_path is None:
                raise WorkflowBundleError("A .raticode bundle is required")
            if body.get("bundlePath"):
                self._assert_bundle_path_allowed(bundle_path, body.get("grantId"), must_exist=True)
            workflow = import_rattish_bundle_payload(
                bundle_path,
                project_root,
                self._request_data_dir(request.query),
                resource_limits=self._resource_limits(),
            )
        return {"workflow": workflow}

    @ROUTES.route("POST", "/api/rattish/workflows/{workflow_id}/export", body=0, status=201)
    def _rattish_export(self, request: RouteRequest) -> Any:
        body = request.body or {}
        data_dir = self._request_data_dir(request.query)
        output_path = _resolve_ui_bundle_path(Path(str(body.get("outputPath", ""))), data_dir)
        output_path = self._assert_bundle_path_allowed(
            output_path, body.get("grantId"), must_exist=False
        )
        return export_rattish_bundle_payload(request.params["workflow_id"], output_path, data_dir)

    def _answer_preflight(self) -> None:
        self.send_response(204)
        self._send_common_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        # The desktop window calls this API from another origin with a bearer
        # token, so each request needs this check first. Without a cache time the
        # browser repeats it about every five seconds per address, nearly doubling
        # the requests polling makes.
        self.send_header("Access-Control-Max-Age", str(CORS_PREFLIGHT_MAX_AGE_SECONDS))
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return

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

    def _follow_chat_job(self, conversation_id: str, turn_id: str, after: int = 0) -> None:
        events = self.server.chat_jobs.events(conversation_id, turn_id, after)
        try:
            self._send_stream_headers()
            for event in events:
                self._write_stream_event(event)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # Disconnecting a subscriber never stops provider work.
            pass
        finally:
            events.close()

    async def _stream_report_theme(self, body: dict[str, Any]) -> None:
        from gofer.ui.report_theme_generation import generate_report_theme

        cancel_event = threading.Event()
        task = asyncio.create_task(
            generate_report_theme(body, self._write_stream_event, cancel_event)
        )
        try:
            while not task.done():
                # Keep quiet generations connected and observe cancelled requests.
                await asyncio.wait({task}, timeout=1)
                self._check_discovery_cancelled()
                if not task.done():
                    self._write_stream_event({"type": "heartbeat"})
            self._write_stream_event({"type": "final", "theme": task.result()})
        except (BrokenPipeError, ConnectionResetError, DiscoveryCancelled):
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("Report theme generation failed", exc_info=True)
            self._write_stream_event(
                {"type": "error", "error": str(exc) or "Rem could not generate the theme."}
            )
        finally:
            cancel_event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _stream_chat_response(
        self,
        body: dict[str, Any],
        data_dir: Path,
        trusted_swarm_url: str | None = None,
        *,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        from gofer.ui.device_tools import stream_with_fleet_tools
        from gofer.ui.rem_threads import stream_with_thread_tools

        async def scoped_source(**options: Any) -> AsyncGenerator[dict[str, Any], None]:
            scoped_body = {**body, "workflow": options.get("workflow")}
            with self._rem_swarm_session(scoped_body) as (prepared, swarm_url):
                async with aclosing(
                    stream_workflow_chat(
                        **{
                            **options,
                            "workflow": prepared.get("workflow"),
                            "trusted_swarm_url": swarm_url,
                        }
                    )
                ) as stream:
                    async for event in stream:
                        yield event

        async def thread_stream(**options: Any) -> AsyncGenerator[dict[str, Any], None]:
            factory = (
                scoped_source
                if (options.get("workflow") or {}).get("remThreads")
                else stream_workflow_chat
            )

            async def organization_source(
                **org_options: Any,
            ) -> AsyncGenerator[dict[str, Any], None]:
                manager = getattr(self.server, "organizations", None)
                source = (
                    manager.rem_stream(
                        factory,
                        actor=f"Rem:{body.get('conversationId', 'conversation')}",
                        management_read_only=options.get("permission_mode")
                        in {"read-only", "plan"},
                        **org_options,
                    )
                    if manager is not None
                    else factory(**org_options)
                )
                async with aclosing(source) as stream:
                    async for event in stream:
                        yield event

            async def social_source(
                **social_options: Any,
            ) -> AsyncGenerator[dict[str, Any], None]:
                from gofer.social.mcp import stream_with_social_tools

                social = getattr(self.server, "social", None)
                async with aclosing(
                    stream_with_social_tools(organization_source, social, **social_options)
                ) as social_stream:
                    async for event in social_stream:
                        yield event

            async def fleet_source(**fleet_options: Any) -> AsyncGenerator[dict[str, Any], None]:
                async with aclosing(
                    stream_with_fleet_tools(
                        social_source, getattr(self.server, "devices", None), **fleet_options
                    )
                ) as fleet_stream:
                    async for event in fleet_stream:
                        yield event

            # Desktop threads may switch to a worktree the agent creates mid-turn.
            async with aclosing(
                stream_with_thread_tools(fleet_source, worktrees=True, **options)
            ) as stream:
                async for event in stream:
                    yield event

        machine = body.get("machine")
        if machine:
            from gofer.devices.remote_threads import RemoteThreads

            devices = getattr(self.server, "devices", None)

            async def remote_stream(**options: Any) -> AsyncGenerator[dict[str, Any], None]:
                # The chosen paired desktop runs the turn with its own projects and tools.
                if devices is None:
                    yield {"type": "error", "error": "Device pairing is off on this computer."}
                    return
                async with aclosing(
                    RemoteThreads(devices).stream(machine=str(machine), **options)
                ) as stream:
                    async for event in stream:
                        yield event

            thread_stream = remote_stream

        write_event = emit or self._write_stream_event
        cancel_event = threading.Event()
        turn: ChatTurn | None = None
        source = None
        try:
            kwargs: dict[str, Any] = dict(
                provider=str(body.get("provider", "codex")),
                model=str(body.get("model", "cli-default")),
                effort=_optional_body_str(body, "effort"),
                permission_mode=_optional_body_str(body, "permissionMode"),
                messages=body.get("messages") or [],
                conversation_id=body.get("conversationId"),
                reset_session=body.get("resetSession") is True,
                fork_from=_optional_body_str(body, "forkedFrom"),
                workflow=body.get("workflow"),
                trusted_swarm_url=trusted_swarm_url,
                data_dir=data_dir,
                resource_limits=self._resource_limits(),
            )
            if (account := _optional_body_str(body, "providerProfile")) and not machine:
                # The account of this provider the user picked for this thread, or
                # "auto" for Rem's pick. Another desktop has its own accounts, so a
                # remote turn never names one.
                from gofer.ui.rem_provider_accounts import thread_account

                if chosen := await asyncio.to_thread(
                    thread_account,
                    body.get("conversationId"),
                    account,
                    data_dir,
                    provider=kwargs["provider"],
                ):
                    kwargs["provider_profile"] = chosen
            if "conversationId" in body or "turnId" in body:
                turn = self.server.chat_steering.begin(
                    body.get("conversationId", ""),
                    body.get("turnId", ""),
                    str(body.get("provider", "codex")),
                    str(body.get("model", "cli-default")),
                    data_dir=data_dir,
                )
                source = self.server.chat_steering.stream(turn, thread_stream, **kwargs)
            else:
                source = thread_stream(cancel_event=cancel_event, **kwargs)
            async for event in source:
                write_event(event)
        except (BrokenPipeError, ConnectionResetError):
            cancel_event.set()
        except ChatProviderError as exc:
            write_event({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            cancel_event.set()
            log.exception("Unhandled Rem stream error")
            write_event(
                {
                    "type": "error",
                    "error": f"Rem failed: {exc}",
                }
            )
        finally:
            cancel_event.set()
            if turn is not None:
                turn.stopped = True
                turn.cancel.set()
            close_stream = getattr(source, "aclose", None)
            if close_stream is not None:
                await close_stream()

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
        self, body: dict[str, Any], *, defer_threads: bool = False
    ) -> Iterator[tuple[dict[str, Any], str | None]]:
        workflow = body.get("workflow") or {}
        if defer_threads and workflow.get("remThreads"):
            yield body, None
            return
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
        workspace_grants = config.get("workspaceGrants") or {}
        if not isinstance(workspace_grants, dict):
            raise ValueError("Workspace grants must be an object")
        with server.swarms.rem_session(
            project, read_only=read_only, workspace_grants=workspace_grants
        ) as url:
            resources = dict(workflow.get("remResources") or {})
            resources["mcpServers"] = [
                item for item in resources.get("mcpServers", []) if item.get("name") != "swarm"
            ] + [{"name": "swarm", "type": "http", "url": url}]
            yield {**body, "workflow": {**workflow, "remResources": resources}}, url

    def _validate_rem_thread_projects(self, body: dict[str, Any]) -> None:
        config = (body.get("workflow") or {}).get("remThreads") or {}
        if not isinstance(config, dict):
            raise ValueError("Invalid Rem thread configuration")
        projects = config.get("projects", [])
        if not isinstance(projects, list) or len(projects) > 100:
            raise ValueError("Expected at most 100 open projects")
        for project in projects:
            if not isinstance(project, dict) or not isinstance(project.get("root"), str):
                raise ValueError("Invalid Rem project")
            root = Path(project["root"])
            if not root.is_absolute() or not root.is_dir():
                raise ValueError("Rem projects must be existing absolute directories")
            self._assert_bundle_path_allowed(root, project.get("grantId"), must_exist=True)
            project["name"] = str(project.get("name") or root.name)

    def _prune_rem_context(self, body: dict[str, Any]) -> list[str]:
        """Drop the projects or Second Brain that Rem can't use, and say why.

        One moved project or a revoked Second Brain used to reject the whole
        context, which stopped Rem from replying or starting any task.
        """
        projects = body.get("projects", [])
        if not isinstance(projects, list) or len(projects) > 100:
            raise ValueError("Expected at most 100 open projects")
        brain = body.get("brain", {})
        if not isinstance(brain, dict):
            raise ValueError("Invalid Second Brain configuration")
        authorize = self.server._authorize_rem_home
        warnings: list[str] = []
        kept = []
        for project in projects:
            name = (project.get("name") or project.get("root")) if isinstance(project, dict) else ""
            try:
                self._validate_rem_thread_projects(
                    {"workflow": {"remThreads": {"projects": [project]}}}
                )
                authorize({"projects": [project]}, {})
                # Rem's own turns run on a cloud provider (_private_rem_projects).
                root = str(project.get("root") or "")
                reason = self.server._private_rem_projects({"brain": brain}, {}, [root]).get(root)
                if reason:
                    raise ValueError(reason)
            except ValueError as exc:
                warnings.append(f"Rem can't use {name or 'a project'}: {exc}")
                continue
            kept.append(project)
        body["projects"] = kept
        if brain.get("enabled"):
            try:
                # Rem's saved settings keep the folder; tasks find its brain when they run.
                self._validate_second_brain({"workflow": {"remSecondBrain": brain}}, fill=False)
                authorize({"projects": [], "brain": brain}, {})
            except ValueError as exc:
                body["brain"] = {**brain, "enabled": False}
                warnings.append(f"Rem can't use Second Brain: {exc}")
        return warnings

    def _validate_second_brain(self, body: dict[str, Any], *, fill: bool = True) -> None:
        """Check the turn's Second Brain setting and complete it for the run.

        With ``fill``, a folder without brainIds that a library brain is registered at
        gets that brain, so Rem searches the library instead of the folder's own index.
        Callers that save the setting pass ``fill=False`` and keep it as entered.
        """
        config = (body.get("workflow") or {}).get("remSecondBrain") or {}
        if not isinstance(config, dict):
            raise ValueError("Invalid Second Brain configuration")
        from gofer.ui.second_brain import MEMORY_FOLDER_KEY

        # Only the fill below may mark a brain as the approved folder.
        if config.pop(MEMORY_FOLDER_KEY, None):
            config.pop("brainIds", None)
            config.pop("libraryDataDir", None)
        if config.get("enabled") is not True:
            return
        if "brainIds" in config:
            selected = config["brainIds"]
            if not isinstance(selected, list) or not selected or len(selected) > 100:
                raise ValueError("Choose between 1 and 100 registered Second Brains")
            grants = config.get("workspaceGrants", {})
            registered = [self.server.second_brains.brain(str(item)) for item in selected]
            for brain in registered:
                if not brain["settings"].get("allowCloud"):
                    raise ValueError(
                        f"Allow cloud processing for {brain['name']} before attaching it to Rem"
                    )
                self._assert_bundle_path_allowed(
                    Path(brain["root"]), grants.get(brain["root"]), must_exist=True
                )
            config["root"] = registered[0]["root"]
            config["grantId"] = grants.get(config["root"])
            config["libraryDataDir"] = str(self.server.data_dir)
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
        if config.get("format", "md") not in {"md", "html", "slides", "pdf"}:
            raise ValueError("Choose Markdown, HTML, Slides, or PDF for reports.")
        if fill and "brainIds" not in config:
            # The folder was approved above; its library brain needs no cloud permission
            # (the documented Settings > Memory rule). The grant stays the folder's own.
            config.update(library_brain(self.server, config))

    def _granted_project_root(self, body: dict[str, Any]) -> Path:
        """The ``projectRoot`` a request names, once the desktop has granted that folder."""
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
        return project_root

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
        supplied = self.headers.get("X-Gofer-Desktop-Grant-Secret", "")
        if not secret or not hmac.compare_digest(supplied.encode(), secret.encode()):
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
        self._raw_body = raw_body
        if len(raw_body) != content_length:
            raise json.JSONDecodeError("Incomplete request body", "", 0)
        try:
            payload = json.loads(
                raw_body.decode("utf-8") or "{}",
                parse_float=_finite_json_number,
                parse_constant=_finite_json_number,
            )
        except RecursionError:
            raise json.JSONDecodeError("Request body nesting is too deep", "", 0) from None
        if not isinstance(payload, dict):
            raise WorkflowCreateError("Request body must be a JSON object")
        pending: list[tuple[object, int]] = [(payload, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > UI_JSON_MAX_DEPTH:
                raise json.JSONDecodeError("Request body nesting is too deep", "", 0)
            if isinstance(item, dict):
                pending.extend((value, depth + 1) for value in item.values())
            elif isinstance(item, list):
                pending.extend((value, depth + 1) for value in item)
        return payload

    def _request_failed(
        self, what: str, exc: BaseException, status: int = 400, fallback: str = ""
    ) -> None:
        """Answer a failed request, logging it when the failure is not the request's fault.

        Bad input and refused paths are routine and noted only at debug, by type, because
        their messages can quote the request. Anything else (a file that couldn't be
        written, a process that couldn't start, a provider error, a timeout) is a real
        failure and keeps its traceback.
        """
        if isinstance(exc, (ValueError, WorkflowBundleError, FileNotFoundError)):
            log.debug("%s rejected (%s)", what, type(exc).__name__)
        else:
            log.warning("%s failed", what, exc_info=exc)
        self._send_json({"error": str(exc) or fallback or f"{what} failed"}, status=status)

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
        # Every response passes through here, so an error after this point can't
        # replace the response with a 500.
        self._response_started = True
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
            "X-Gofer-Known, X-Gofer-UI-Token, X-Gofer-Webhook-Token",
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
        return bool(
            expected and supplied and hmac.compare_digest(supplied.encode(), expected.encode())
        )

    def _authorize_ui_request(self, method: str, path: str) -> bool:
        self._agent_token_name = None
        self._agent_private_brains = False
        if not _requires_ui_api_auth(method, path) or self._has_ui_api_token():
            return True
        agent = self._agent_token()
        if agent is not None:
            refusal = agent_request_refusal(method, path)
            if refusal is not None:
                self._send_json({"error": refusal, "code": "agent_token_forbidden"}, status=403)
                return False
            self._agent_token_name = agent.name
            self._agent_private_brains = agent.private_brains
            return True
        self._send_json({"error": "UI API authentication required"}, status=401)
        return False

    def _agent_token(self) -> AgentGrant | None:
        """The agent token this request carries, if it is valid."""
        store = getattr(self.server, "agent_tokens", None)
        if store is None:
            return None
        headers = getattr(self, "headers", {})
        supplied = headers.get("X-Gofer-UI-Token")
        authorization = headers.get("Authorization", "")
        if supplied is None and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ").strip()
        return store.grant(supplied) if supplied else None


def ready_payload(server: GoferUiServer) -> dict[str, Any]:
    host, port = server.server_address[:2]
    payload = {
        "host": host,
        "port": port,
        "dataDir": str(server.data_dir),
        "goferCliAvailable": server.gofer_cli_path is not None,
    }
    # Only the desktop window reads this line, from a pipe. A headless backend's stdout
    # goes to a terminal or the journal, so it never carries the token, even when the
    # flag was inherited from a desktop app's environment.
    if getattr(server, "emit_ready_token", False) and not getattr(server, "headless", False):
        payload["apiToken"] = server.api_token
    return payload


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
        data_dir.expanduser().resolve() if data_dir else get_data_dir(),
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
    if method == "POST" and path.startswith("/api/organization-webhooks/"):
        return False  # Routine HMAC authentication is mandatory in its receiver.
    if method == "OPTIONS" or (method == "GET" and path in {"/api/health", "/api/instance/proof"}):
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
        temp_file = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        temp_path = Path(temp_file.name)
        try:
            with temp_file:
                temp_file.write(base64.b64decode(str(body["bundleContent"]), validate=True))
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


def _sweep_run_worktrees(data_dir: Path) -> None:
    from gofer.rattish import run_worktrees

    try:
        run_worktrees.sweep_at_start(data_dir)
    except Exception:  # noqa: BLE001 - a clean-up failure must not stop startup
        log.warning("Couldn't sweep run worktrees of deleted workflows", exc_info=True)


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    data_dir: Path | None = None,
    *,
    headless: bool = False,
    token_file: Path | None = None,
    rem_settings: Path | None = None,
    secrets_dir: Path | None = None,
) -> None:
    configure_backend_logging()
    # Undo the logging setup if startup fails, such as on a bad secrets folder, so a
    # handler isn't left writing to a stream the caller closes.
    session: HeadlessSession | None = None
    desktop_lock: int | None = None
    try:
        log.info(
            "Starting Raticode backend (Python %s, %s %s)",
            sys.version.split()[0],
            sys.platform,
            os.uname().machine if hasattr(os, "uname") else "",
        )
        if headless:
            # Check the secrets folder first, so a bad one stops startup before any file is written.
            folder = headless_support.prepare_secrets_dir(secrets_dir)
        # The folder is resolved; nothing spawned from here on may be pointed at it. The
        # desktop's backend reads no secrets folder, but a variable it inherited goes too.
        seal_credential_env((CREDENTIALS_DIRECTORY_ENV,))
        if headless:
            session = HeadlessSession.prepare(host, data_dir or get_data_dir(), token_file)
            session.secrets_dir = folder
        else:
            desktop_lock = _hold_desktop_lock(data_dir or get_data_dir())
        if not headless and not headless_support.is_loopback_host(host):
            # A developer option (browser development); the API then answers other computers.
            log.warning(
                "Listening on %s, which other computers can reach. Raticode's API holds your "
                "workflows, folder grants and Rem; use 127.0.0.1 unless you need this.",
                host,
            )
            print(
                f"Warning: Raticode is listening on {host}, which other computers can reach. "
                "Use --host 127.0.0.1 unless you need this.",
                file=sys.stderr,
            )
        server = create_server(
            host=host,
            port=port,
            data_dir=data_dir,
            api_token=session.token if session else None,
        )
        # The server has read its tokens; nothing spawned from here on may inherit them.
        seal_credential_env()
        if session is not None:
            session.attach(server, rem_settings)
            for warning, line in headless_support.secret_feature_log_lines():
                (log.warning if warning else log.info)("%s", line)
        triggers = TriggerEngine(server.data_dir, owner="app", runner=run_triggered_rattish)
        triggers.start()
        stop_usage_refresh = start_background_refresh(server.data_dir)
        stop_reclaimer = start_leftover_reclaimer()
        # Scratch folders a stopped or crashed Rem turn or workflow run left behind,
        # and workers' temp files in RAM-backed /tmp: now, hourly and after turns end.
        stop_temp_cleanup = start_temp_cleanup(server.data_dir)
        # Fresh worktrees of project checks a cancel, crash or restart left behind.
        rem_project_checks.start_sweep(server.data_dir)
        # Run worktrees of workflows deleted while the backend wasn't running.
        threading.Thread(
            target=_sweep_run_worktrees,
            args=(server.data_dir,),
            name="run-worktree-sweep",
            daemon=True,
        ).start()
        # The Vosk model earlier versions downloaded is unusable now; delete it once.
        threading.Thread(
            target=clean_legacy_speech_models,
            args=(server.data_dir,),
            name="legacy-speech-models",
            daemon=True,
        ).start()
        _install_shutdown_handlers(server)
        print(f"GOFER_UI_READY {json.dumps(ready_payload(server), sort_keys=True)}", flush=True)
    except BaseException:
        if session is not None:
            # Free the data folder's backend lock and the token this start wrote.
            session.close()
        headless_support.release_backend_lock(desktop_lock)
        reset_backend_logging()
        raise
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if session is not None:
            session.close()
            from gofer.core.secrets_folder import configure_secrets_dir

            configure_secrets_dir(None)
        stop_reclaimer()
        stop_temp_cleanup()
        stop_usage_refresh()
        triggers.stop()
        stop_rattish_runs_for_shutdown(server.data_dir)
        stop_all_workflow_tests()
        server.server_close()
        headless_support.release_backend_lock(desktop_lock)
        log.info("Raticode backend stopped")
        print("GOFER_UI_STOPPED", file=sys.stderr, flush=True)
        reset_backend_logging()


# How long the desktop app's backend waits for the one it replaces to free the data
# folder. Electron kills a backend that hasn't stopped after 3 seconds.
DESKTOP_LOCK_WAIT_SECONDS = 10.0


def _hold_desktop_lock(data_dir: Path) -> int:
    """Take the data-folder lock headless mode takes, so neither starts beside the other."""
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    restrict_to_owner(data_dir)
    return headless_support.hold_backend_lock(
        data_dir, kind="desktop", wait=DESKTOP_LOCK_WAIT_SECONDS
    )


class HeadlessSession:
    """The owner token, endpoint file and Rem settings of a backend with no window."""

    def __init__(self, data_dir: Path, token: str, written_token: Path | None) -> None:
        self.data_dir = data_dir
        self.token = token
        self.written_token = written_token
        self.endpoint_text = ""
        self.secrets_dir: Path | None = None
        self.lock: int | None = None

    @classmethod
    def prepare(cls, host: str, data_dir: Path, token_file: Path | None) -> HeadlessSession:
        """Check the bind address and choose the owner token before the port opens."""
        if not headless_support.is_loopback_host(host):
            raise headless_support.HeadlessError(
                f"Headless mode listens only on this computer; {host!r} isn't a loopback "
                "address. Use 127.0.0.1 or ::1, and reach it remotely through SSH forwarding."
            )
        data_dir = data_dir.expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        restrict_to_owner(data_dir)
        headless_support.ensure_private_dir(headless_support.headless_dir(data_dir))
        lock = headless_support.hold_backend_lock(data_dir)
        try:
            session = cls._with_token(data_dir, token_file)
        except BaseException:
            headless_support.release_backend_lock(lock)
            raise
        session.lock = lock
        return session

    @classmethod
    def _with_token(cls, data_dir: Path, token_file: Path | None) -> HeadlessSession:
        if token_file is not None:
            return cls(data_dir, headless_support.read_token_file(token_file), None)
        if os.environ.get("GOFER_UI_API_TOKEN"):
            # Whoever set the variable manages the token; no file is written.
            return cls(data_dir, os.environ["GOFER_UI_API_TOKEN"], None)
        token = headless_support.new_owner_token()
        path = headless_support.owner_token_path(data_dir)
        headless_support.write_private_file(path, token + "\n")
        return cls(data_dir, token, path)

    def attach(self, server: GoferUiServer, rem_settings: Path | None) -> None:
        """Load Rem's settings, then publish the endpoint so clients find a ready backend."""
        server.headless = True
        settings_path = rem_settings or headless_support.default_rem_settings_path(self.data_dir)
        if settings_path.exists() or rem_settings is not None:
            apply_rem_settings(server, settings_path)
            log.info("Loaded Rem settings from %s", settings_path)
        else:
            server.rem_home.context_hint = f"Waiting for Rem settings in {settings_path}"
            log.warning(
                "Rem has no settings yet, so its tasks wait. Write %s and restart.", settings_path
            )
        host, port = server.server_address[:2]
        endpoint = headless_support.publish_endpoint(
            self.data_dir,
            host=str(host),
            port=int(port),
            token_file=self.written_token,
            secrets_dir=self.secrets_dir,
        )
        self.endpoint_text = endpoint.read_text(encoding="utf-8")

    def close(self) -> None:
        if self.endpoint_text:
            headless_support.remove_if_unchanged(
                headless_support.endpoint_path(self.data_dir), self.endpoint_text
            )
        if self.written_token is not None:
            headless_support.remove_if_unchanged(self.written_token, self.token + "\n")
        headless_support.release_backend_lock(self.lock)
        self.lock = None


def apply_rem_settings(server: GoferUiServer, path: Path) -> None:
    """Configure Rem from a settings file, granting the folders it lists.

    The desktop window normally sends these settings and grants each open project.
    Here the owner's file stands in for both, so Rem's queue no longer waits for a
    window to connect.
    """
    config = headless_support.load_rem_settings(path)
    try:
        for project in config.get("projects", []):
            project["grantId"] = server.path_grants.register(Path(project["root"]), persistent=True)
        brain = config.get("brain") or {}
        if brain.get("enabled"):
            brain["grantId"] = server.path_grants.register(Path(brain["root"]), persistent=True)
            grants: dict[str, str] = {}
            for identity in brain.get("brainIds", []) or []:
                root = str(server.second_brains.brain(identity)["root"])
                grants[root] = server.path_grants.register(Path(root), persistent=True)
            if grants:
                brain["workspaceGrants"] = grants
        server.rem_home.configure(config)
    except (KeyError, ValueError, WorkflowBundleError) as exc:
        raise headless_support.HeadlessError(f"Rem settings in {path} were refused: {exc}") from exc
    server.rem_home.context_hint = "Waiting for Rem settings"


if __name__ == "__main__":
    serve()
