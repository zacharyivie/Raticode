from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gofer.core.agent import AgentConfig
from gofer.core.operations import (
    AgentOperation,
    ApprovalGateOperation,
    BashCommandOperation,
    CommonLlmTaskOperation,
    CopyFileOperation,
    DeleteFileOperation,
    DirectoryFanSource,
    FileOperation,
    FolderOperation,
    LocalSearchOperation,
    LocalVectorizeOperation,
    LoopOperation,
    MoveFileOperation,
    NotificationOperation,
    OpenResourceOperation,
    Operation,
    PromptFileOperation,
    PythonScriptOperation,
    ReadFileOperation,
    ShellScriptOperation,
    TabularFanSource,
    WriteFileOperation,
)
from gofer.core.provider_capabilities import ProviderId, resolve_provider_executable
from gofer.core.provider_profiles import (
    DIRECT_API_SUBSCRIPTIONS,
    load_provider_profiles,
    resolve_provider_settings,
    sensitive_plaintext_env,
    unresolved_provider_secret_refs,
    validate_provider_settings,
)
from gofer.core.workflow import AgenticWorkflow
from gofer.utils.paths import get_data_dir

HealthSeverity = Literal["ok", "warning", "error"]

PROVIDER_BINARIES = {
    "claude_code": "claude",
    "codex": "codex",
    "cursor": "cursor-agent",
    "copilot": "copilot",
    "opencode": "opencode",
}
MIN_PYTHON_VERSION = (3, 11)


@dataclass(frozen=True)
class HealthDiagnostic:
    id: str
    severity: HealthSeverity
    message: str
    subject: str | None = None
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "severity": self.severity,
            "message": self.message,
        }
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    diagnostics: list[HealthDiagnostic]
    data_dir: Path
    workflow_path: Path | None = None

    @property
    def errors(self) -> list[HealthDiagnostic]:
        return [item for item in self.diagnostics if item.severity == "error"]

    @property
    def warnings(self) -> list[HealthDiagnostic]:
        return [item for item in self.diagnostics if item.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "dataDir": str(self.data_dir),
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }
        if self.workflow_path is not None:
            payload["workflowPath"] = str(self.workflow_path)
        return payload


def run_health_checks(
    *,
    data_dir: Path | None = None,
    workflow: str | Path | None = None,
) -> HealthReport:
    base = data_dir or get_data_dir()
    diagnostics: list[HealthDiagnostic] = []
    diagnostics.extend(global_health_diagnostics(base))

    workflow_path = None
    if workflow is not None:
        workflow_path = _resolve_workflow_path(workflow, base)
        if workflow_path is None:
            diagnostics.append(
                HealthDiagnostic(
                    id="workflow.not_found",
                    severity="error",
                    subject=str(workflow),
                    message=f"Workflow '{workflow}' was not found.",
                )
            )
        else:
            diagnostics.extend(workflow_health_diagnostics(workflow_path))

    return HealthReport(
        ok=not any(item.severity == "error" for item in diagnostics),
        diagnostics=diagnostics,
        data_dir=base,
        workflow_path=workflow_path,
    )


def global_health_diagnostics(data_dir: Path) -> list[HealthDiagnostic]:
    diagnostics = [
        _python_version_diagnostic(),
        _data_dir_diagnostic(data_dir),
        _scheduler_db_diagnostic(data_dir / "schedules.db"),
        _shell_diagnostic(),
        _openpyxl_diagnostic(),
        _desktop_notification_diagnostic(),
        _workflow_assistant_cli_diagnostic(data_dir),
    ]
    diagnostics.extend(_configured_provider_diagnostics(data_dir))
    return diagnostics


def workflow_health_diagnostics(workflow_path: Path) -> list[HealthDiagnostic]:
    diagnostics: list[HealthDiagnostic] = []
    try:
        workflow = AgenticWorkflow.from_file(workflow_path)
    except Exception as exc:  # noqa: BLE001
        return [
            HealthDiagnostic(
                id="workflow.load_failed",
                severity="error",
                subject=str(workflow_path),
                message=f"Workflow could not be loaded: {exc}",
            )
        ]

    path_base = workflow_path.parent
    diagnostics.extend(_workflow_provider_diagnostics(workflow))
    diagnostics.extend(_workflow_agent_diagnostics(workflow, path_base))
    diagnostics.extend(_workflow_node_diagnostics(workflow, path_base))
    diagnostics.extend(_workflow_schedule_diagnostics(workflow))
    return diagnostics


def workflow_health_payload(
    workflow_id_or_path: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    return run_health_checks(data_dir=data_dir, workflow=workflow_id_or_path).to_dict()


def _python_version_diagnostic() -> HealthDiagnostic:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] < MIN_PYTHON_VERSION:
        return HealthDiagnostic(
            id="python.version",
            severity="error",
            subject=version,
            message="Python 3.11 or newer is required.",
        )
    return HealthDiagnostic(
        id="python.version",
        severity="ok",
        subject=version,
        message=f"Python {version} is supported.",
    )


def _data_dir_diagnostic(data_dir: Path) -> HealthDiagnostic:
    try:
        if not data_dir.exists():
            return HealthDiagnostic(
                id="data_dir.access",
                severity="error",
                subject=str(data_dir),
                message="Data directory does not exist.",
            )
        if not data_dir.is_dir():
            return HealthDiagnostic(
                id="data_dir.access",
                severity="error",
                subject=str(data_dir),
                message="Data directory is not a directory.",
            )
        readable = os.access(data_dir, os.R_OK)
        writable = os.access(data_dir, os.W_OK)
    except OSError as exc:
        return HealthDiagnostic(
            id="data_dir.access",
            severity="error",
            subject=str(data_dir),
            message=f"Data directory is not accessible: {exc}",
        )
    if not readable or not writable:
        return HealthDiagnostic(
            id="data_dir.access",
            severity="error",
            subject=str(data_dir),
            message="Data directory must be readable and writable.",
            detail={"readable": readable, "writable": writable},
        )
    return HealthDiagnostic(
        id="data_dir.access",
        severity="ok",
        subject=str(data_dir),
        message="Data directory is readable and writable.",
    )


def _scheduler_db_diagnostic(db_path: Path) -> HealthDiagnostic:
    try:
        if db_path.exists():
            with closing(sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)) as connection:
                connection.execute("PRAGMA user_version")
        else:
            parent = db_path.parent
            if parent.exists():
                readable = os.access(parent, os.R_OK)
                writable = os.access(parent, os.W_OK)
            else:
                readable = os.access(parent.parent, os.R_OK)
                writable = os.access(parent.parent, os.W_OK)
            if not readable or not writable:
                return HealthDiagnostic(
                    id="scheduler.db",
                    severity="error",
                    subject=str(db_path),
                    message="Scheduler database location must be readable and writable.",
                    detail={"readable": readable, "writable": writable},
                )
    except sqlite3.Error as exc:
        return HealthDiagnostic(
            id="scheduler.db",
            severity="error",
            subject=str(db_path),
            message=f"Scheduler database is not accessible: {exc}",
        )
    except OSError as exc:
        return HealthDiagnostic(
            id="scheduler.db",
            severity="error",
            subject=str(db_path),
            message=f"Scheduler database directory is not accessible: {exc}",
        )
    return HealthDiagnostic(
        id="scheduler.db",
        severity="ok",
        subject=str(db_path),
        message="Scheduler database is accessible.",
    )


def _shell_diagnostic() -> HealthDiagnostic:
    binary = _command_shell_binary()
    path = shutil.which(binary)
    if path is None:
        return HealthDiagnostic(
            id="shell.available",
            severity="warning",
            message=f"Shell executable '{binary}' is not on PATH.",
            detail={"binary": binary},
        )
    return HealthDiagnostic(
        id="shell.available",
        severity="ok",
        subject=binary,
        message=f"Shell executable '{binary}' is available.",
        detail={"binary": binary, "path": path},
    )


def _command_shell_binary() -> str:
    return "powershell.exe" if sys.platform == "win32" else "bash"


def _openpyxl_diagnostic() -> HealthDiagnostic:
    if importlib.util.find_spec("openpyxl") is None:
        return HealthDiagnostic(
            id="optional.openpyxl",
            severity="warning",
            message="openpyxl is not installed; .xlsx inputs require gofer-flow[xlsx].",
        )
    return HealthDiagnostic(
        id="optional.openpyxl",
        severity="ok",
        message="openpyxl is available for .xlsx support.",
    )


def _desktop_notification_diagnostic() -> HealthDiagnostic:
    if sys.platform == "darwin":
        path = shutil.which("osascript")
        if path is None:
            return HealthDiagnostic(
                id="desktop.notifications",
                severity="warning",
                message="Desktop notifications require 'osascript' on macOS.",
                detail={"binary": "osascript"},
            )
        return HealthDiagnostic(
            id="desktop.notifications",
            severity="ok",
            subject="macOS",
            message="Desktop notifications are available through 'osascript'.",
            detail={"binary": "osascript", "path": path},
        )
    if sys.platform == "win32":
        path = _windows_powershell_path()
        if path is None:
            return HealthDiagnostic(
                id="desktop.notifications",
                severity="warning",
                subject="Windows",
                message="Desktop notifications require PowerShell on Windows.",
                detail={"binary": "powershell.exe"},
            )
        return HealthDiagnostic(
            id="desktop.notifications",
            severity="ok",
            subject="Windows",
            message="Desktop notifications are available through PowerShell.",
            detail={"binary": "powershell.exe", "path": path},
        )

    display = os.environ.get("DISPLAY")
    dbus_session = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    path = shutil.which("notify-send")
    if not display:
        return HealthDiagnostic(
            id="desktop.notifications",
            severity="warning",
            subject=sys.platform,
            message="Desktop notifications require DISPLAY on Unix desktop sessions.",
            detail={"display": display, "binary": "notify-send", "path": path},
        )
    if not dbus_session and not runtime_dir:
        return HealthDiagnostic(
            id="desktop.notifications",
            severity="warning",
            subject=sys.platform,
            message="Desktop notifications require a user D-Bus session on Unix desktops.",
            detail={
                "display": display,
                "dbusSessionBusAddress": dbus_session,
                "xdgRuntimeDir": runtime_dir,
                "binary": "notify-send",
                "path": path,
            },
        )
    if path is None:
        return HealthDiagnostic(
            id="desktop.notifications",
            severity="warning",
            subject=sys.platform,
            message="Desktop notifications require 'notify-send' on Unix desktop sessions.",
            detail={"display": display, "binary": "notify-send"},
        )
    return HealthDiagnostic(
        id="desktop.notifications",
        severity="ok",
        subject=sys.platform,
        message="Desktop notifications are available through 'notify-send'.",
        detail={
            "display": display,
            "dbusSessionBusAddress": dbus_session,
            "xdgRuntimeDir": runtime_dir,
            "binary": "notify-send",
            "path": path,
        },
    )


def _windows_powershell_path() -> str | None:
    return (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh.exe")
        or shutil.which("pwsh")
    )


def _workflow_assistant_cli_diagnostic(data_dir: Path) -> HealthDiagnostic:
    try:
        from gofer.ui.chat import _gofer_cli_source_path, trusted_gofer_cli_dir

        source = _gofer_cli_source_path()
        trusted_dir = trusted_gofer_cli_dir(data_dir)
    except Exception as exc:  # noqa: BLE001
        return HealthDiagnostic(
            id="packaging.gofer_cli",
            severity="warning",
            message=f"Workflow assistant CLI helper readiness could not be checked: {exc}",
        )

    detail = {"trustedDir": str(trusted_dir)}
    if source is None:
        return HealthDiagnostic(
            id="packaging.gofer_cli",
            severity="warning",
            message=(
                "Workflow assistant CLI automation is unavailable because no authoritative "
                "'gof' executable was found."
            ),
            detail=detail,
        )
    detail["source"] = str(source)
    if not source.exists():
        return HealthDiagnostic(
            id="packaging.gofer_cli",
            severity="warning",
            subject=str(source),
            message=(
                "Workflow assistant CLI automation is unavailable because the configured "
                "'gof' executable does not exist."
            ),
            detail=detail,
        )
    try:
        if source.resolve().is_relative_to(data_dir.resolve()):
            return HealthDiagnostic(
                id="packaging.gofer_cli",
                severity="warning",
                subject=str(source),
                message=(
                    "Workflow assistant CLI source is inside the mutable Raticode data directory."
                ),
                detail=detail,
            )
    except OSError as exc:
        return HealthDiagnostic(
            id="packaging.gofer_cli",
            severity="warning",
            subject=str(source),
            message=f"Workflow assistant CLI source could not be resolved: {exc}",
            detail=detail,
        )
    return HealthDiagnostic(
        id="packaging.gofer_cli",
        severity="ok",
        subject=str(source),
        message=(
            "Workflow assistant CLI helper has an authoritative source outside the "
            "Raticode data directory."
        ),
        detail=detail,
    )


def _configured_provider_diagnostics(data_dir: Path) -> list[HealthDiagnostic]:
    providers = _configured_providers_in_data_dir(data_dir)
    diagnostics = _provider_profile_secret_storage_diagnostics(data_dir)
    if not providers:
        return diagnostics + [
            HealthDiagnostic(
                id="provider.cli",
                severity="ok",
                message="No configured workflow provider CLIs were found.",
            )
        ]

    for provider in sorted(providers):
        if provider in DIRECT_API_SUBSCRIPTIONS:
            diagnostics.append(
                HealthDiagnostic(
                    id="provider.direct_api",
                    severity="ok",
                    subject=provider,
                    message=f"Direct API provider '{provider}' is supported.",
                    detail={"provider": provider},
                )
            )
            continue
        binary = PROVIDER_BINARIES[provider]
        path = resolve_provider_executable(cast(ProviderId, provider))
        diagnostics.append(
            HealthDiagnostic(
                id="provider.cli",
                severity="ok" if path else "warning",
                subject=provider,
                message=(
                    f"Configured provider CLI '{binary}' is available."
                    if path
                    else f"Configured provider CLI '{binary}' was not found on PATH or in nvm."
                ),
                detail={"binary": binary, "path": path},
            )
        )
    return diagnostics


def _provider_profile_secret_storage_diagnostics(data_dir: Path) -> list[HealthDiagnostic]:
    try:
        profiles = load_provider_profiles(data_dir)
    except (OSError, ValueError) as exc:
        return [
            HealthDiagnostic(
                id="provider_profile.store",
                severity="warning",
                subject=str(data_dir / "provider-profiles.json"),
                message=f"Provider profile store could not be inspected: {exc}",
            )
        ]

    diagnostics: list[HealthDiagnostic] = []
    for profile in profiles.values():
        plaintext = sensitive_plaintext_env(profile)
        if plaintext:
            names = sorted(plaintext)
            diagnostics.append(
                HealthDiagnostic(
                    id="provider_profile.plaintext_secret",
                    severity="warning",
                    subject=profile.name,
                    message=(
                        f"Provider profile '{profile.name}' stores sensitive env "
                        f"value(s) in plaintext: {', '.join(names)}. Move them to "
                        "secret_refs."
                    ),
                    detail={"profile": profile.name, "env": names},
                )
            )
    return diagnostics


def _configured_providers_in_data_dir(data_dir: Path) -> set[str]:
    if not data_dir.exists() or not data_dir.is_dir():
        return set()

    providers: set[str] = set()
    for workflow_path in sorted(data_dir.glob("*.toml")):
        try:
            workflow = AgenticWorkflow.from_file(workflow_path)
        except Exception:  # noqa: BLE001
            continue
        providers.update(
            agent.subscription
            for agent in workflow.agents.values()
            if agent.subscription in PROVIDER_BINARIES
            or agent.subscription in DIRECT_API_SUBSCRIPTIONS
        )
    return providers


def _workflow_provider_diagnostics(workflow: AgenticWorkflow) -> list[HealthDiagnostic]:
    providers = {agent.subscription for agent in workflow.agents.values()}
    diagnostics: list[HealthDiagnostic] = []
    for provider in sorted(providers):
        if provider in DIRECT_API_SUBSCRIPTIONS:
            diagnostics.append(
                HealthDiagnostic(
                    id="workflow.provider_direct_api",
                    severity="ok",
                    subject=provider,
                    message=f"Workflow direct API provider '{provider}' is supported.",
                    detail={"provider": provider},
                )
            )
            continue
        binary = PROVIDER_BINARIES[provider]
        path = resolve_provider_executable(cast(ProviderId, provider))
        diagnostics.append(
            HealthDiagnostic(
                id="workflow.provider_cli",
                severity="ok" if path else "error",
                subject=provider,
                message=(
                    f"Workflow provider CLI '{binary}' is available."
                    if path
                    else f"Workflow requires provider CLI '{binary}', "
                    "but it was not found on PATH or in nvm."
                ),
                detail={"binary": binary, "path": path},
            )
        )
    return diagnostics


def _workflow_agent_diagnostics(
    workflow: AgenticWorkflow,
    path_base: Path,
) -> list[HealthDiagnostic]:
    diagnostics: list[HealthDiagnostic] = []
    for agent in workflow.agents.values():
        diagnostics.extend(
            _agent_config_diagnostics(
                agent,
                path_base,
                f"agent:{agent.agent_id}",
                include_prompt_path=False,
            )
        )
        if agent.subscription in DIRECT_API_SUBSCRIPTIONS:
            diagnostics.extend(_direct_provider_profile_diagnostics(agent, path_base))
    return diagnostics


def _direct_provider_profile_diagnostics(
    agent: AgentConfig,
    data_dir: Path,
) -> list[HealthDiagnostic]:
    try:
        settings = resolve_provider_settings(
            agent_subscription=agent.subscription,
            profile_name=agent.profile,
            agent_model=agent.model,
            agent_effort=agent.effort,
            data_dir=data_dir,
        )
        validate_provider_settings(settings)
    except ValueError as exc:
        return [
            HealthDiagnostic(
                id="workflow.provider_profile",
                severity="error",
                subject=agent.agent_id,
                message=str(exc),
            )
        ]
    missing = unresolved_provider_secret_refs(settings)
    if missing:
        return [
            HealthDiagnostic(
                id="workflow.provider_secret",
                severity="error",
                subject=agent.agent_id,
                message=(
                    f"Direct provider '{settings.subscription}' is missing API "
                    f"secret(s): {', '.join(missing)}."
                ),
                detail={
                    "provider": settings.subscription,
                    "profile": settings.profile_name,
                    "model": settings.model,
                    "missingSecrets": missing,
                },
            )
        ]
    return [
        HealthDiagnostic(
            id="workflow.provider_secret",
            severity="ok",
            subject=agent.agent_id,
            message=f"Direct provider '{settings.subscription}' API secret is configured.",
            detail={
                "provider": settings.subscription,
                "profile": settings.profile_name,
                "model": settings.model,
                "apiBaseUrl": settings.api_base_url,
            },
        )
    ]


def _agent_config_diagnostics(
    agent: AgentConfig,
    path_base: Path,
    subject: str,
    *,
    include_prompt_path: bool = True,
) -> list[HealthDiagnostic]:
    diagnostics = [
        _directory_dependency(
            agent.working_dir,
            path_base,
            diagnostic_id="workflow.working_dir",
            subject=subject,
            label="Agent working directory",
        )
    ]
    if include_prompt_path and agent.prompt_path is not None:
        diagnostics.append(
            _file_dependency(
                agent.prompt_path,
                path_base,
                diagnostic_id="workflow.prompt_path",
                subject=subject,
                label="Agent prompt file",
            )
        )
    for extra_path in agent.extra_paths:
        diagnostics.append(
            _directory_dependency(
                extra_path,
                path_base,
                diagnostic_id="workflow.extra_path",
                subject=subject,
                label="Agent extra path",
            )
        )
    return diagnostics


def _workflow_node_diagnostics(
    workflow: AgenticWorkflow,
    path_base: Path,
) -> list[HealthDiagnostic]:
    diagnostics: list[HealthDiagnostic] = []
    for node in workflow.graph.nodes_in_order():
        subject = f"node:{node.node_id}"
        op = node.operation
        diagnostics.extend(_operation_diagnostics(op, workflow, path_base, subject))
    return diagnostics


def _operation_diagnostics(
    op: Operation,
    workflow: AgenticWorkflow,
    path_base: Path,
    subject: str,
) -> list[HealthDiagnostic]:
    diagnostics: list[HealthDiagnostic] = []
    if isinstance(op, ShellScriptOperation):
        diagnostics.append(_workflow_shell_dependency("bash", subject))
        diagnostics.append(
            _file_dependency(
                op.script_path,
                path_base,
                diagnostic_id="workflow.script_path",
                subject=subject,
                label="Script path",
            )
        )
    elif isinstance(op, PythonScriptOperation):
        diagnostics.append(
            _file_dependency(
                op.script_path,
                path_base,
                diagnostic_id="workflow.script_path",
                subject=subject,
                label="Script path",
            )
        )
    elif isinstance(op, BashCommandOperation):
        diagnostics.append(_workflow_shell_dependency(_command_shell_binary(), subject))
        if op.working_dir is not None:
            diagnostics.append(
                _directory_dependency(
                    op.working_dir,
                    path_base,
                    diagnostic_id="workflow.working_dir",
                    subject=subject,
                    label="Command working directory",
                )
            )
    elif isinstance(op, AgentOperation):
        agent = workflow.agents.get(op.agent_id)
        if agent is None:
            diagnostics.append(
                HealthDiagnostic(
                    id="workflow.agent_missing",
                    severity="error",
                    subject=subject,
                    message=f"Agent node references unknown agent '{op.agent_id}'.",
                )
            )
        else:
            effective_agent = agent.model_copy(
                update={
                    "working_dir": op.working_dir,
                    "prompt_path": op.prompt_path or agent.prompt_path,
                }
            )
            diagnostics.extend(_agent_config_diagnostics(effective_agent, path_base, subject))
            diagnostics.extend(_fan_source_diagnostics(op.fan_source, path_base, subject))
    elif isinstance(op, CommonLlmTaskOperation):
        agent = workflow.agents.get(op.agent_id)
        if agent is None:
            diagnostics.append(
                HealthDiagnostic(
                    id="workflow.agent_missing",
                    severity="error",
                    subject=subject,
                    message=f"Common LLM task references unknown agent '{op.agent_id}'.",
                )
            )
        diagnostics.append(
            _directory_dependency(
                op.working_dir,
                path_base,
                diagnostic_id="workflow.working_dir",
                subject=subject,
                label="LLM task working directory",
            )
        )
    elif isinstance(op, LoopOperation):
        diagnostics.extend(_fan_source_diagnostics(op.source, path_base, subject))
    elif isinstance(op, ReadFileOperation | FileOperation):
        diagnostics.append(
            _file_dependency(
                op.path,
                path_base,
                diagnostic_id="workflow.file_path",
                subject=subject,
                label="File path",
            )
        )
    elif isinstance(op, WriteFileOperation):
        diagnostics.append(
            _writable_file_dependency(
                op.path,
                path_base,
                diagnostic_id="workflow.write_file_path",
                subject=subject,
                label="Write file path",
                create_dirs=op.create_dirs,
                overwrite=op.overwrite,
                append=op.append,
            )
        )
    elif isinstance(op, CopyFileOperation | MoveFileOperation):
        operation_label = "Copy file" if isinstance(op, CopyFileOperation) else "Move file"
        diagnostics.append(
            _file_dependency(
                op.source_path,
                path_base,
                diagnostic_id="workflow.file_source_path",
                subject=subject,
                label=f"{operation_label} source path",
            )
        )
        diagnostics.append(
            _writable_file_dependency(
                op.destination_path,
                path_base,
                diagnostic_id="workflow.file_destination_path",
                subject=subject,
                label=f"{operation_label} destination path",
                create_dirs=op.create_dirs,
                overwrite=op.overwrite,
                append=False,
            )
        )
    elif isinstance(op, DeleteFileOperation):
        if not op.missing_ok:
            diagnostics.append(
                _path_dependency(
                    op.path,
                    path_base,
                    diagnostic_id="workflow.delete_file_path",
                    subject=subject,
                    label="Delete file path",
                )
            )
    elif isinstance(op, FolderOperation):
        diagnostics.append(
            _directory_dependency(
                op.path,
                path_base,
                diagnostic_id="workflow.folder_path",
                subject=subject,
                label="Folder path",
            )
        )
    elif isinstance(op, PromptFileOperation):
        diagnostics.append(
            _writable_file_dependency(
                op.output_path,
                path_base,
                diagnostic_id="workflow.prompt_output_path",
                subject=subject,
                label="Prompt output path",
                create_dirs=op.create_dirs,
                overwrite=op.overwrite,
                append=False,
            )
        )
        if op.template_path is not None:
            diagnostics.append(
                _file_dependency(
                    op.template_path,
                    path_base,
                    diagnostic_id="workflow.prompt_template_path",
                    subject=subject,
                    label="Prompt template path",
                )
            )
    elif isinstance(op, LocalVectorizeOperation):
        diagnostics.append(
            _path_dependency(
                op.source_path,
                path_base,
                diagnostic_id="workflow.local_vector_source",
                subject=subject,
                label="Local vector source path",
            )
        )
        diagnostics.append(
            _writable_file_dependency(
                op.index_path,
                path_base,
                diagnostic_id="workflow.local_vector_index",
                subject=subject,
                label="Local vector index path",
                create_dirs=True,
                overwrite=True,
                append=False,
            )
        )
    elif isinstance(op, LocalSearchOperation):
        diagnostics.append(
            _file_dependency(
                op.index_path,
                path_base,
                diagnostic_id="workflow.local_search_index",
                subject=subject,
                label="Local search index path",
            )
        )
    elif isinstance(op, OpenResourceOperation):
        diagnostics.extend(_open_resource_diagnostics(op, path_base, subject))
    elif isinstance(op, NotificationOperation):
        diagnostics.extend(_notification_operation_diagnostics(op, subject))
    elif isinstance(op, ApprovalGateOperation) and op.notify:
        diagnostics.extend(_notification_operation_diagnostics(op, subject))
    return diagnostics


def _open_resource_diagnostics(
    op: OpenResourceOperation,
    path_base: Path,
    subject: str,
) -> list[HealthDiagnostic]:
    if op.resource_type == "url" or _has_url_scheme(op.target):
        return []
    if op.resource_type == "app":
        return []

    target = Path(op.target)
    if op.resource_type == "file":
        return [
            _file_dependency(
                target,
                path_base,
                diagnostic_id="workflow.open_resource",
                subject=subject,
                label="Open resource file",
            )
        ]
    if op.resource_type == "folder":
        return [
            _directory_dependency(
                target,
                path_base,
                diagnostic_id="workflow.open_resource",
                subject=subject,
                label="Open resource folder",
            )
        ]
    if op.resource_type == "auto":
        return [
            _path_dependency(
                target,
                path_base,
                diagnostic_id="workflow.open_resource",
                subject=subject,
                label="Open resource target",
            )
        ]
    return []


def _notification_operation_diagnostics(
    op: NotificationOperation | ApprovalGateOperation,
    subject: str,
) -> list[HealthDiagnostic]:
    if isinstance(op, NotificationOperation) and op.channel != "desktop":
        return []
    diagnostic = _desktop_notification_diagnostic()
    if diagnostic.severity == "ok":
        return [
            HealthDiagnostic(
                id="workflow.desktop_notifications",
                severity="ok",
                subject=subject,
                message="Desktop notification support is available for this workflow.",
                detail=diagnostic.detail,
            )
        ]
    return [
        HealthDiagnostic(
            id="workflow.desktop_notifications",
            severity="warning",
            subject=subject,
            message=diagnostic.message,
            detail=diagnostic.detail,
        )
    ]


def _has_url_scheme(target: str) -> bool:
    parsed = urlparse(target)
    return bool(parsed.scheme and (parsed.netloc or parsed.scheme in {"mailto", "tel"}))


def _fan_source_diagnostics(
    source: TabularFanSource | DirectoryFanSource | Any | None,
    path_base: Path,
    subject: str,
) -> list[HealthDiagnostic]:
    diagnostics: list[HealthDiagnostic] = []
    if isinstance(source, TabularFanSource):
        diagnostics.append(
            _file_dependency(
                source.path,
                path_base,
                diagnostic_id="workflow.fanout_path",
                subject=subject,
                label="Tabular fan-out path",
            )
        )
        if source.path.suffix.lower() == ".xlsx" and importlib.util.find_spec("openpyxl") is None:
            diagnostics.append(
                HealthDiagnostic(
                    id="workflow.openpyxl_required",
                    severity="error",
                    subject=subject,
                    message="Tabular .xlsx fan-out requires openpyxl.",
                )
            )
    elif isinstance(source, DirectoryFanSource):
        diagnostics.append(
            _directory_dependency(
                source.path,
                path_base,
                diagnostic_id="workflow.fanout_path",
                subject=subject,
                label="Directory fan-out path",
            )
        )
    return diagnostics


def _workflow_schedule_diagnostics(workflow: AgenticWorkflow) -> list[HealthDiagnostic]:
    schedule = workflow.config.schedule
    if schedule is None:
        return []
    try:
        ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError:
        return [
            HealthDiagnostic(
                id="workflow.schedule_timezone",
                severity="error",
                subject=schedule.timezone,
                message=f"Schedule timezone '{schedule.timezone}' is not available.",
            )
        ]
    return [
        HealthDiagnostic(
            id="workflow.schedule_timezone",
            severity="ok",
            subject=schedule.timezone,
            message=f"Schedule timezone '{schedule.timezone}' is available.",
        )
    ]


def _workflow_shell_dependency(binary: str, subject: str) -> HealthDiagnostic:
    path = shutil.which(binary)
    if path is None:
        return HealthDiagnostic(
            id="workflow.shell_available",
            severity="error",
            subject=subject,
            message=(
                f"Shell executable '{binary}' is not on PATH; this workflow node "
                "will fail at runtime."
            ),
            detail={"binary": binary},
        )
    return HealthDiagnostic(
        id="workflow.shell_available",
        severity="ok",
        subject=subject,
        message=f"Shell executable '{binary}' is available for this workflow node.",
        detail={"binary": binary, "path": path},
    )


def _path_dependency(
    path: Path,
    path_base: Path,
    *,
    diagnostic_id: str,
    subject: str,
    label: str,
) -> HealthDiagnostic:
    resolved = _resolve_config_path(path, path_base)
    if not resolved.exists():
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} does not exist: {resolved}",
        )
    if not os.access(resolved, os.R_OK):
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} is not readable: {resolved}",
        )
    return HealthDiagnostic(
        id=diagnostic_id,
        severity="ok",
        subject=subject,
        message=f"{label} is available: {resolved}",
    )


def _file_dependency(
    path: Path,
    path_base: Path,
    *,
    diagnostic_id: str,
    subject: str,
    label: str,
) -> HealthDiagnostic:
    resolved = _resolve_config_path(path, path_base)
    if not resolved.exists():
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} does not exist: {resolved}",
        )
    if not resolved.is_file():
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} is not a file: {resolved}",
        )
    if not os.access(resolved, os.R_OK):
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} is not readable: {resolved}",
        )
    return HealthDiagnostic(
        id=diagnostic_id,
        severity="ok",
        subject=subject,
        message=f"{label} is available: {resolved}",
    )


def _directory_dependency(
    path: Path,
    path_base: Path,
    *,
    diagnostic_id: str,
    subject: str,
    label: str,
) -> HealthDiagnostic:
    resolved = _resolve_config_path(path, path_base)
    if not resolved.exists():
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} does not exist: {resolved}",
        )
    if not resolved.is_dir():
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} is not a directory: {resolved}",
        )
    if not os.access(resolved, os.R_OK | os.W_OK):
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} is not readable and writable: {resolved}",
        )
    return HealthDiagnostic(
        id=diagnostic_id,
        severity="ok",
        subject=subject,
        message=f"{label} is available: {resolved}",
    )


def _writable_file_dependency(
    path: Path,
    path_base: Path,
    *,
    diagnostic_id: str,
    subject: str,
    label: str,
    create_dirs: bool,
    overwrite: bool,
    append: bool,
) -> HealthDiagnostic:
    resolved = _resolve_config_path(path, path_base)
    if resolved.exists():
        if not resolved.is_file():
            return HealthDiagnostic(
                id=diagnostic_id,
                severity="error",
                subject=subject,
                message=f"{label} is not a file: {resolved}",
            )
        if not os.access(resolved, os.W_OK):
            return HealthDiagnostic(
                id=diagnostic_id,
                severity="error",
                subject=subject,
                message=f"{label} is not writable: {resolved}",
            )
        if not overwrite and not append:
            return HealthDiagnostic(
                id=diagnostic_id,
                severity="error",
                subject=subject,
                message=f"{label} already exists and overwrite is disabled: {resolved}",
            )
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="ok",
            subject=subject,
            message=f"{label} is writable: {resolved}",
        )

    parent = resolved.parent
    existing_parent = parent
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent

    if not parent.exists() and not create_dirs:
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} parent directory does not exist: {parent}",
        )
    if not existing_parent.exists():
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} has no existing parent directory: {resolved}",
        )
    if not existing_parent.is_dir():
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} parent is not a directory: {existing_parent}",
        )
    if not os.access(existing_parent, os.W_OK):
        return HealthDiagnostic(
            id=diagnostic_id,
            severity="error",
            subject=subject,
            message=f"{label} parent directory is not writable: {existing_parent}",
        )
    return HealthDiagnostic(
        id=diagnostic_id,
        severity="ok",
        subject=subject,
        message=f"{label} can be created: {resolved}",
    )


def _resolve_config_path(path: Path, path_base: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return path_base / expanded


def _resolve_workflow_path(workflow: str | Path, data_dir: Path) -> Path | None:
    path = Path(workflow)
    if path.suffix == ".toml":
        return path if path.exists() else None

    candidate = data_dir / f"{workflow}.toml"
    if candidate.exists():
        return candidate

    for candidate in sorted(data_dir.glob("*.toml")) if data_dir.exists() else []:
        try:
            wf = AgenticWorkflow.from_file(candidate)
        except Exception:
            continue
        if wf.config.id == str(workflow):
            return candidate
    return None
