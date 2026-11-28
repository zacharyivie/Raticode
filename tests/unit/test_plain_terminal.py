"""Tests see plain text whatever terminal colors the shell that starts them asks for."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer
from typer.testing import CliRunner

from gofer.cli.commands import workflow
from gofer.cli.main import app

ROOT = Path(__file__).resolve().parents[2]
FORCED_COLORS = {
    "FORCE_COLOR": "3",
    "CLICOLOR_FORCE": "1",
    "PY_COLORS": "1",
    "TTY_COMPATIBLE": "1",
    "GITHUB_ACTIONS": "true",
}


def test_cli_output_is_plain_under_the_tests() -> None:
    printer = typer.Typer()

    @printer.command()
    def show() -> None:
        workflow.console.print("[green]Deleted[/green] changer and 2 run worktrees")

    shown = CliRunner().invoke(printer, [])
    assert shown.exit_code == 0, shown.output
    assert shown.output == "Deleted changer and 2 run worktrees\n"

    # Typer's own error panel, which GitHub Actions would otherwise draw in color.
    unknown = CliRunner().invoke(app, ["no-such-command"])
    assert unknown.exit_code != 0
    assert "No such command" in unknown.output
    assert "\x1b" not in unknown.output


def test_suite_ignores_forced_colors_from_its_shell(tmp_path: Path) -> None:
    env = {**os.environ, **FORCED_COLORS}
    # This run's own conftest set these, so the inner run starts without them.
    for name in ("NO_COLOR", "_TYPER_FORCE_DISABLE_TERMINAL"):
        env.pop(name, None)
    inner = test_cli_output_is_plain_under_the_tests.__name__
    test = f"{Path(__file__).relative_to(ROOT).as_posix()}::{inner}"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--no-header",
            "--basetemp",
            str(tmp_path / "inner"),
            test,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
