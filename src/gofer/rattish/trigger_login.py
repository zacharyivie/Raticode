"""Start the trigger scheduler when the user logs in.

This installs a per-user login entry and nothing else: no system service, no
elevated rights. The entry runs ``Raticode rattish triggers start``, which holds
the scheduler lock until logout, so triggers keep firing with no window open.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from gofer.utils.atomic_output import atomic_binary_output
from gofer.utils.version import CLI_NAME


class LoginEntryError(RuntimeError):
    """Raised with a sentence for the user when the login entry can't be changed."""


def _trusted_cli(data_dir: Path) -> Path:
    name = f"{CLI_NAME}.exe" if sys.platform == "win32" else CLI_NAME
    return data_dir.parent / ".gofer-trusted-bin" / name


def scheduler_command(data_dir: Path) -> list[str]:
    """The command a login entry runs, using a path that survives app updates."""
    base = Path(data_dir).expanduser().resolve()
    suffix = ["rattish", "triggers", "start", "--data-dir", str(base)]
    trusted = _trusted_cli(base)
    if trusted.is_file():
        return [str(trusted), *suffix]
    if getattr(sys, "frozen", False):
        return [sys.executable, *suffix]
    installed = shutil.which(CLI_NAME)
    if installed:
        return [installed, *suffix]
    return [sys.executable, "-c", "from gofer.cli.main import app; app()", *suffix]


def _entry_id(data_dir: Path) -> str:
    # The desktop and development profiles each get their own entry.
    digest = hashlib.sha256(str(Path(data_dir).expanduser().resolve()).encode("utf-8"))
    return digest.hexdigest()[:10]


def login_entry_path(data_dir: Path) -> Path:
    entry = _entry_id(data_dir)
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        startup = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return startup / f"raticode-triggers-{entry}.vbs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"com.raticode.triggers.{entry}.plist"
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "autostart" / f"raticode-triggers-{entry}.desktop"


def _desktop_exec_argument(value: str) -> str:
    """Quote one argument as the XDG desktop entry Exec key requires.

    The spec escapes twice: once for the quoted argument, then once more because
    the Exec value is itself a string value with backslash escapes.
    """
    quoted = value.replace("\\", "\\\\")
    for character in ('"', "`", "$"):
        quoted = quoted.replace(character, f"\\{character}")
    return f'"{quoted}"'.replace("\\", "\\\\").replace("%", "%%")


def _login_path() -> str:
    """The PATH to run with at login.

    Login sessions often have a shorter PATH than the one providers were set up
    in, so the entry keeps the PATH of the process that switched it on, minus
    wrappers that only work inside a running agent session.
    """
    parts = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(part for part in parts if "command-slots" not in Path(part).parts)


def _desktop_entry(command: list[str]) -> bytes:
    environment = ["/usr/bin/env", f"PATH={_login_path()}"]
    arguments = " ".join(_desktop_exec_argument(item) for item in [*environment, *command])
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Raticode triggers\n"
        "Comment=Runs Raticode workflow triggers in the background\n"
        f"Exec={arguments}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    ).encode()


def _launch_agent(command: list[str], label: str) -> bytes:
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": command,
            "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": _login_path()},
        }
    )


def _startup_script(command: list[str]) -> bytes:
    # Running through WScript keeps a console window from opening at every login.
    line = subprocess.list2cmdline(command).replace('"', '""')
    return f'CreateObject("WScript.Shell").Run "{line}", 0, False\r\n'.encode()


def _render_entry(data_dir: Path) -> bytes:
    command = scheduler_command(data_dir)
    if sys.platform == "win32":
        return _startup_script(command)
    if sys.platform == "darwin":
        return _launch_agent(command, f"com.raticode.triggers.{_entry_id(data_dir)}")
    return _desktop_entry(command)


def login_status(data_dir: Path) -> dict[str, Any]:
    path = login_entry_path(data_dir)
    return {"supported": True, "enabled": path.is_file(), "path": str(path)}


def set_login_enabled(data_dir: Path, enabled: bool) -> dict[str, Any]:
    path = login_entry_path(data_dir)
    try:
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_binary_output(path) as output:
                output.write(_render_entry(data_dir))
            if sys.platform != "win32":
                path.chmod(0o600)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        action = "turn on" if enabled else "turn off"
        raise LoginEntryError(f"Couldn't {action} Start at login: {exc}") from exc
    return login_status(data_dir)
