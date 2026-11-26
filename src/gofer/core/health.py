from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gofer.core.approvals import _windows_powershell_path
from gofer.core.provider_profiles import (
    load_provider_profiles,
    sensitive_plaintext_env,
)
from gofer.utils.paths import get_data_dir
from gofer.utils.xlsx import xlsx_reader_available

HealthSeverity = Literal["ok", "warning", "error"]

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
        return payload


def run_health_checks(*, data_dir: Path | None = None) -> HealthReport:
    base = data_dir or get_data_dir()
    diagnostics = global_health_diagnostics(base)
    return HealthReport(
        ok=not any(item.severity == "error" for item in diagnostics),
        diagnostics=diagnostics,
        data_dir=base,
    )


def global_health_diagnostics(data_dir: Path) -> list[HealthDiagnostic]:
    diagnostics = [
        _python_version_diagnostic(),
        _data_dir_diagnostic(data_dir),
        _shell_diagnostic(),
        _xlsx_diagnostic(),
        _desktop_notification_diagnostic(),
        _workflow_assistant_cli_diagnostic(data_dir),
        _organizations_preview_diagnostic(),
    ]
    diagnostics.extend(secret_storage_diagnostics())
    diagnostics.extend(_provider_profile_secret_storage_diagnostics(data_dir))
    return diagnostics


def secret_storage_diagnostics(keychain: bool | None = None) -> list[HealthDiagnostic]:
    """Where secret-backed features read their secrets, and which can't work here.

    Checks only whether the keychain can be reached, never reads or unlocks it, and
    reads secrets folder files only to check them.
    """
    from gofer.core.secrets_folder import configured_secrets_dir, secret_features

    folder = configured_secrets_dir()
    features = secret_features(keychain)
    diagnostics: list[HealthDiagnostic] = []
    if folder is not None:
        diagnostics.append(
            HealthDiagnostic(
                id="secrets.folder",
                severity="ok",
                subject=str(folder),
                message=f"Secrets folder {folder} is read when the keychain can't help.",
                detail={"path": str(folder)},
            )
        )
    if all(feature.source == "keychain" for feature in features):
        diagnostics.append(
            HealthDiagnostic(
                id="secrets.keychain",
                severity="ok",
                subject="System keychain",
                message="The system keychain is available for saved secrets.",
            )
        )
        return diagnostics
    for feature in features:
        if feature.available:
            message = f"{feature.label}: from the {feature.source}. {feature.detail}"
        else:
            message = f"{feature.label} unavailable. {feature.detail}"
        diagnostics.append(
            HealthDiagnostic(
                id=f"secrets.{feature.id}",
                severity="ok" if feature.available else "warning",
                subject=feature.label,
                message=message,
                detail=feature.to_dict(),
            )
        )
    return diagnostics


ORGANIZATIONS_PREVIEW_ENV = "RATICODE_EXPERIMENTAL_ORGANIZATIONS"


def _organizations_preview_diagnostic() -> HealthDiagnostic:
    """Report the Organizations Feature Preview switch this process sees.

    The desktop turns it on from Settings by setting the variable for its own backend.
    A headless backend reads only the environment it was started from.
    """
    enabled = os.environ.get(ORGANIZATIONS_PREVIEW_ENV) == "1"
    if enabled:
        message = f"Organizations (Feature Preview) are on: {ORGANIZATIONS_PREVIEW_ENV}=1."
    else:
        message = (
            "Organizations (Feature Preview) are off. A backend started from this "
            f"environment serves them only with {ORGANIZATIONS_PREVIEW_ENV}=1."
        )
    return HealthDiagnostic(
        id="feature_preview.organizations",
        severity="ok",
        subject="Organizations",
        message=message,
        detail={"env": ORGANIZATIONS_PREVIEW_ENV, "enabled": enabled},
    )


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


def _xlsx_diagnostic() -> HealthDiagnostic:
    if not xlsx_reader_available():
        return HealthDiagnostic(
            id="optional.python_calamine",
            severity="warning",
            message="python-calamine is not installed; .xlsx inputs require raticode[xlsx].",
        )
    return HealthDiagnostic(
        id="optional.python_calamine",
        severity="ok",
        message="python-calamine is available for .xlsx support.",
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
                "'Raticode' executable was found."
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
                "'Raticode' executable does not exist."
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
