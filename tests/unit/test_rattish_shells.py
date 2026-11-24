"""The shell field of bash-command, shell-script and test-set command checks.

Windows lookups run here on any platform: ``find_git_bash`` takes the environment, a file
check and the registry roots, and ``shells._on_windows`` is patched for the rest.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

from gofer.rattish import shells
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node
from gofer.rattish.starters import list_starters
from gofer.rattish.test_sets import prepare_test_run, run_test_set

RATTISH_ROOT = Path(__file__).parents[2] / "rattish"
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
WSL_BASH = r"C:\Windows\System32\bash.exe"
BASH = shells.find_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed")


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[
            RATTISH_ROOT / "contracts" / "bash-command.json",
            RATTISH_ROOT / "contracts" / "shell-script.json",
        ],
    )


def windows_env(path: str, **extra: str) -> dict[str, str]:
    return {"SystemRoot": r"C:\Windows", "Path": path, **extra}


def lookup(
    environ: dict[str, str], files: Iterable[str], registry: Iterable[str] = ()
) -> str | None:
    present = {item.lower() for item in files}
    return shells.find_git_bash(
        environ,
        is_file=lambda candidate: candidate.lower() in present,
        registry_roots=lambda: list(registry),
    )


def test_git_bash_beside_git_on_path_wins_over_the_wsl_launcher() -> None:
    environ = windows_env(r"C:\Windows\System32;C:\Program Files\Git\cmd")
    files = [WSL_BASH, r"C:\Program Files\Git\cmd\git.exe", GIT_BASH]
    assert lookup(environ, files) == GIT_BASH


def test_only_the_wsl_launcher_means_no_bash() -> None:
    apps = r"C:\Users\ada\AppData\Local\Microsoft\WindowsApps"
    environ = windows_env(rf"C:\Windows\System32;{apps};C:\WINDOWS\Sysnative")
    files = [WSL_BASH, rf"{apps}\bash.exe", r"C:\WINDOWS\Sysnative\bash.exe"]
    assert lookup(environ, files) is None


def test_git_bash_is_found_in_the_registry_and_default_install_folders() -> None:
    portable = r"D:\Tools\PortableGit"
    assert lookup(windows_env(""), [rf"{portable}\bin\bash.exe"], [portable]) == (
        rf"{portable}\bin\bash.exe"
    )
    environ = windows_env("", ProgramFiles=r"C:\Program Files")
    assert lookup(environ, [GIT_BASH]) == GIT_BASH
    user = r"C:\Users\ada\AppData\Local"
    environ = windows_env("", LOCALAPPDATA=user)
    assert lookup(environ, [rf"{user}\Programs\Git\bin\bash.exe"]) == (
        rf"{user}\Programs\Git\bin\bash.exe"
    )


def test_git_roots_follow_cmd_bin_and_mingw_folders() -> None:
    for folder in (r"C:\Git\cmd", r"C:\Git\bin", r"C:\Git\mingw64\bin"):
        environ = windows_env(folder)
        assert lookup(environ, [rf"{folder}\git.exe", r"C:\Git\bin\bash.exe"]) == (
            r"C:\Git\bin\bash.exe"
        ), folder


def test_other_bash_on_path_is_used_and_git_usr_bin_maps_to_its_launcher() -> None:
    environ = windows_env(r"C:\Windows\System32;C:\Git\usr\bin")
    files = [WSL_BASH, r"C:\Git\usr\bin\bash.exe", r"C:\Git\bin\bash.exe"]
    assert lookup(environ, files) == r"C:\Git\bin\bash.exe"
    environ = windows_env(r"C:\msys64\usr\bin")
    assert lookup(environ, [r"C:\msys64\usr\bin\bash.exe"]) == r"C:\msys64\usr\bin\bash.exe"


def test_environment_names_ignore_case_like_windows() -> None:
    environ = {"SYSTEMROOT": r"D:\Win", "PATH": r"D:\Win\System32", "programfiles": r"C:\PF"}
    assert lookup(environ, [r"D:\Win\System32\bash.exe"]) is None
    assert lookup(environ, [r"C:\PF\Git\bin\bash.exe"]) == r"C:\PF\Git\bin\bash.exe"


def on_windows(monkeypatch: pytest.MonkeyPatch, bash: str | None) -> None:
    monkeypatch.setattr(shells, "_on_windows", lambda: True)
    monkeypatch.setattr(shells, "find_git_bash", lambda: bash)
    monkeypatch.setattr(
        shells.shutil, "which", lambda name: name if name == "powershell.exe" else None
    )


def test_windows_argument_lists_for_each_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    on_windows(monkeypatch, GIT_BASH)
    assert shells.command_args("bash", "ls") == [GIT_BASH, "-c", "ls"]
    assert shells.script_args("bash", "a.sh", ["x"]) == [GIT_BASH, "a.sh", "x"]
    powershell = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    assert shells.command_args("auto", "ls") == [*powershell, "-Command", "ls"]
    assert shells.command_args("powershell", "ls") == [*powershell, "-Command", "ls"]
    assert shells.script_args("powershell", "a.ps1", []) == [*powershell, "-File", "a.ps1"]
    # auto keeps the original shell-script behavior.
    assert shells.script_args("auto", "a.sh", []) == ["bash", "a.sh"]


def test_missing_git_bash_names_the_install_page(monkeypatch: pytest.MonkeyPatch) -> None:
    on_windows(monkeypatch, None)
    with pytest.raises(shells.ShellUnavailableError) as caught:
        shells.command_args("bash", "ls")
    assert shells.GIT_FOR_WINDOWS_URL in str(caught.value)
    assert caught.value.details["install_url"] == shells.GIT_FOR_WINDOWS_URL
    assert shells.shell_missing("powershell") is None
    assert shells.shell_missing("auto") is None


def test_pwsh_runs_powershell_steps_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shells, "_on_windows", lambda: False)
    monkeypatch.setattr(
        shells.shutil, "which", lambda name: "/usr/bin/pwsh" if name == "pwsh" else None
    )
    assert shells.command_args("powershell", "ls") == [
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "ls",
    ]
    monkeypatch.setattr(shells.shutil, "which", lambda name: None)
    missing = shells.shell_missing("powershell")
    assert missing is not None and "PowerShell" in str(missing)


def bash_source(shell: str = "bash") -> str:
    return f"""Rattish: 1

Workflow:
  name: Shell choice

Node run:
  type: bash-command
  shell: {shell}
  command: printf '%s' "$0"
"""


def test_shell_compiles_with_auto_default_and_rejects_other_values(tmp_path: Path) -> None:
    compiled = compiler().compile(bash_source("BASH"), CompileContext("shell", tmp_path))
    assert compiled.ir["nodes"][0]["configuration"]["shell"] == "bash"
    plain = bash_source().replace("  shell: bash\n", "")
    compiled = compiler().compile(plain, CompileContext("shell", tmp_path))
    assert compiled.ir["nodes"][0]["configuration"]["shell"] == "auto"
    with pytest.raises(RattishCompileError) as caught:
        compiler().compile(bash_source("zsh"), CompileContext("shell", tmp_path))
    assert any("shell" in str(item.details) for item in caught.value.diagnostics), [
        (item.code, item.message, item.details) for item in caught.value.diagnostics
    ]


def test_preflight_fails_with_the_install_link_when_git_bash_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiled = compiler().compile(bash_source(), CompileContext("shell", tmp_path))
    on_windows(monkeypatch, None)
    preflight = run_preflight(compiled.ir)
    assert not preflight.ready
    [diagnostic] = preflight.diagnostics
    assert diagnostic.code == "RATTISH_PREFLIGHT_RESOURCE_MISSING"
    assert diagnostic.details["check"] == "shell-available"
    assert diagnostic.details["install_url"] == shells.GIT_FOR_WINDOWS_URL
    assert "Git for Windows" in diagnostic.message
    assert shells.GIT_FOR_WINDOWS_URL in diagnostic.message

    on_windows(monkeypatch, GIT_BASH)
    assert run_preflight(compiled.ir).ready


@pytest.mark.anyio
@needs_bash
async def test_bash_step_runs_bash_and_reports_a_missing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiled = compiler().compile(bash_source(), CompileContext("shell", tmp_path))
    execution = await execute_node(compiled.ir, "run")
    assert execution.outcome == "success", execution.error
    # $0 is the bash Raticode chose; Git bash on Windows may print it as a POSIX path.
    assert "bash" in execution.output["stdout"].lower()

    on_windows(monkeypatch, None)
    execution = await execute_node(compiled.ir, "run")
    assert execution.outcome == "failure"
    assert execution.error is not None
    assert execution.error.kind == "configuration"
    assert execution.error.code == "RATTISH_RUNTIME_SHELL_UNAVAILABLE"
    assert shells.GIT_FOR_WINDOWS_URL in execution.error.message


@pytest.mark.anyio
@needs_bash
async def test_shell_script_with_bash_runs_the_script(tmp_path: Path) -> None:
    (tmp_path / "hello.sh").write_text('printf "hi %s" "$1"\n', encoding="utf-8")
    source = """Rattish: 1

Workflow:
  name: Script shell

Node run:
  type: shell-script
  shell: bash
  script-path: hello.sh
  args:
    - Ada
"""
    compiled = compiler().compile(source, CompileContext("script-shell", tmp_path))
    assert run_preflight(compiled.ir).ready
    execution = await execute_node(compiled.ir, "run")
    assert execution.outcome == "success", execution.error
    assert execution.output["stdout"] == "hi Ada"


@pytest.mark.anyio
@needs_bash
async def test_command_checks_take_a_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "workflow.rattish").write_text(bash_source(), encoding="utf-8")
    checks = [
        {
            "type": "command",
            "shell": "bash",
            "target": "nodes.run.stdout",
            "command": "grep -q bash",
        },
        {"type": "command", "shell": "powershell", "command": "exit 0"},
    ]
    tests = {"testSetVersion": 1, "cases": [{"id": "one", "checks": checks}]}
    (project / "workflow.tests.json").write_text(json.dumps(tests), encoding="utf-8")
    monkeypatch.setattr(shells, "find_powershell", lambda: None)
    result = await run_test_set(
        prepare_test_run(project / "workflow.rattish", data_dir=tmp_path / "data")
    )
    by_command = {item["label"]: item for item in result["cases"][0]["checks"]}
    bash_check = next(item for label, item in by_command.items() if "grep -q bash" in label)
    powershell_check = next(item for label, item in by_command.items() if "exit 0" in label)
    assert bash_check["status"] == "passed", bash_check
    assert powershell_check["status"] == "error"
    assert "asks for PowerShell" in powershell_check["message"]


def test_every_starter_asks_for_bash_and_says_when_windows_lacks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starters = list_starters()
    assert starters and all(starter.uses_bash for starter in starters)
    assert not any(starter.to_payload()["needsGitForWindows"] for starter in starters)
    on_windows(monkeypatch, None)
    assert all(starter.to_payload()["needsGitForWindows"] for starter in starters)
    on_windows(monkeypatch, GIT_BASH)
    assert not any(starter.to_payload()["needsGitForWindows"] for starter in starters)
