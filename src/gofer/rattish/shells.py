"""Which program runs a shell step: the ``shell`` field of bash-command and shell-script.

``auto`` keeps the original behavior: bash-command runs PowerShell on Windows and bash
elsewhere, and shell-script runs ``bash`` from PATH. ``bash`` asks for bash on every
platform. On Windows that is Git for Windows' ``bash.exe``, never the WSL launcher that
Windows keeps in System32 (and as an app alias in WindowsApps), which would run the step
inside a Linux distribution with different paths, or fail when none is installed.
``powershell`` asks for Windows PowerShell on Windows and ``pwsh`` elsewhere.

Test-set command checks take the same field.
"""

from __future__ import annotations

import ntpath
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any

SHELL_CHOICES = ("auto", "bash", "powershell")
GIT_FOR_WINDOWS_URL = "https://git-scm.com/downloads/win"
WINDOWS_POWERSHELL_FLAGS = ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass")
PWSH_FLAGS = ("-NoProfile", "-NonInteractive")

_GIT_ROOT_REGISTRY_KEYS = (r"SOFTWARE\GitForWindows", r"SOFTWARE\WOW6432Node\GitForWindows")


class ShellUnavailableError(RuntimeError):
    """The shell a step asks for isn't installed on this computer."""

    def __init__(self, shell: str, message: str, details: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.shell = shell
        self.details = dict(details)


def configured_shell(configuration: Mapping[str, Any]) -> str:
    """The step's or command check's shell choice; ``auto`` when it doesn't name one."""
    value = configuration.get("shell")
    return str(value).strip().lower() if value else "auto"


def command_args(shell: str, command: str) -> list[str]:
    """The argument list that runs one bash-command ``command``."""
    if shell == "bash":
        return [require_bash(), "-c", command]
    if shell == "powershell":
        return [*_powershell_prefix(), "-Command", command]
    if _on_windows():
        return ["powershell.exe", *WINDOWS_POWERSHELL_FLAGS, "-Command", command]
    return ["bash", "-c", command]


def script_args(shell: str, script: str, args: Iterable[str]) -> list[str]:
    """The argument list that runs a shell-script file."""
    if shell == "bash":
        return [require_bash(), script, *args]
    if shell == "powershell":
        return [*_powershell_prefix(), "-File", script, *args]
    return ["bash", script, *args]


def script_reader(shell: str) -> str:
    """How the interpreter reads a script, for the approved-file check.

    Bash reads a script while it runs it, so it needs a checked copy. PowerShell parses
    the whole file before it runs any of it, like Python.
    """
    return "powershell" if shell == "powershell" else "bash"


def require_bash() -> str:
    path = find_bash()
    if path is None:
        raise ShellUnavailableError("bash", bash_missing_message(), bash_missing_details())
    return path


def find_bash() -> str | None:
    """The bash a ``shell: bash`` step runs, or None when there isn't one."""
    if _on_windows():
        return find_git_bash()
    return shutil.which("bash")


def bash_missing_message() -> str:
    if _on_windows():
        return (
            "This step asks for bash, and Git for Windows isn't installed. Install it from "
            f"{GIT_FOR_WINDOWS_URL}, then run the workflow again. Raticode doesn't use the "
            "WSL bash in System32."
        )
    return "This step asks for bash, and no bash is on PATH. Install bash and run it again."


def bash_missing_details() -> dict[str, Any]:
    details: dict[str, Any] = {"shell": "bash", "executable": "bash"}
    if _on_windows():
        details["install_url"] = GIT_FOR_WINDOWS_URL
    return details


def find_powershell() -> str | None:
    names = ("powershell.exe", "pwsh.exe") if _on_windows() else ("pwsh",)
    for name in names:
        if shutil.which(name) is not None:
            return name
    return None


def _powershell_prefix() -> list[str]:
    executable = find_powershell()
    if executable is None:
        name = "PowerShell" if _on_windows() else "PowerShell (pwsh)"
        raise ShellUnavailableError(
            "powershell",
            f"This step asks for PowerShell, and {name} isn't installed.",
            {"shell": "powershell", "executable": "pwsh"},
        )
    if _on_windows():
        # Both Windows PowerShell and pwsh.exe apply the execution policy to -File.
        return [executable, *WINDOWS_POWERSHELL_FLAGS]
    return [executable, *PWSH_FLAGS]


def shell_missing(shell: str) -> ShellUnavailableError | None:
    """Preflight: why a ``bash`` or ``powershell`` step can't run here, if it can't."""
    try:
        if shell == "bash":
            require_bash()
        elif shell == "powershell":
            _powershell_prefix()
    except ShellUnavailableError as exc:
        return exc
    return None


def find_git_bash(
    environ: Mapping[str, str] | None = None,
    *,
    is_file: Callable[[str], bool] = os.path.isfile,
    registry_roots: Callable[[], Iterable[str]] | None = None,
) -> str | None:
    """Git for Windows' ``bin\\bash.exe``, or another non-WSL bash on PATH.

    Looked up in this order: next to ``git.exe`` on PATH, the install folder Git for
    Windows records in the registry, the default install folders, then ``bash.exe`` on
    PATH. A bash inside the Windows folder or WindowsApps is the WSL launcher and is
    never used. Windows paths are handled with ``ntpath`` so tests can run anywhere.
    """
    env = _CaseInsensitiveEnv(os.environ if environ is None else environ)
    windows_dir = ntpath.normcase(ntpath.normpath(env.get("SystemRoot") or r"C:\Windows"))
    path_dirs = [entry.strip().strip('"') for entry in env.get("PATH", "").split(";")]
    path_dirs = [entry for entry in path_dirs if entry]

    def usable(candidate: str) -> bool:
        normalized = ntpath.normcase(ntpath.normpath(candidate))
        if _is_inside(normalized, windows_dir):
            return False
        parts = normalized.split("\\")
        return "windowsapps" not in parts and is_file(candidate)

    roots: list[str] = []
    for folder in path_dirs:
        if is_file(ntpath.join(folder, "git.exe")):
            root = _git_root_for(folder)
            if root:
                roots.append(root)
    roots.extend((registry_roots or _registry_git_roots)())
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = env.get(variable)
        if base:
            roots.append(ntpath.join(base, "Git"))
    local = env.get("LOCALAPPDATA")
    if local:
        roots.append(ntpath.join(local, "Programs", "Git"))
    for root in roots:
        candidate = ntpath.join(root, "bin", "bash.exe")
        if usable(candidate):
            return candidate
    for folder in path_dirs:
        candidate = ntpath.join(folder, "bash.exe")
        if not usable(candidate):
            continue
        # Git's usr\bin\bash.exe on PATH: prefer the bin\bash.exe launcher, which sets up
        # PATH so grep, sed and the other tools a script calls are found.
        head, tail = ntpath.split(ntpath.normpath(folder))
        if tail.lower() == "bin" and ntpath.basename(head).lower() == "usr":
            launcher = ntpath.join(ntpath.dirname(head), "bin", "bash.exe")
            if usable(launcher):
                return launcher
        return candidate
    return None


def _git_root_for(folder: str) -> str | None:
    """The Git for Windows install folder for a folder that holds ``git.exe``."""
    parts = ntpath.normpath(folder).rstrip("\\").split("\\")
    lowered = [part.lower() for part in parts]
    if lowered[-1:] == ["cmd"]:
        return "\\".join(parts[:-1])
    if lowered[-1:] == ["bin"]:
        if len(parts) >= 3 and lowered[-2] in {"mingw64", "mingw32", "clangarm64"}:
            return "\\".join(parts[:-2])
        return "\\".join(parts[:-1])
    return None


def _is_inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("\\") + "\\")


def _on_windows() -> bool:
    return sys.platform == "win32"


def _registry_git_roots() -> list[str]:
    if sys.platform != "win32":
        return []
    import winreg

    roots: list[str] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_name in _GIT_ROOT_REGISTRY_KEYS:
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    value, _kind = winreg.QueryValueEx(key, "InstallPath")
            except OSError:
                continue
            if isinstance(value, str) and value:
                roots.append(value)
    return roots


class _CaseInsensitiveEnv:
    """Windows environment names ignore case; a plain dict in tests doesn't."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._values = {key.upper(): value for key, value in environ.items()}

    def get(self, name: str, default: str = "") -> str:
        return self._values.get(name.upper(), default)
