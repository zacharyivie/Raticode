"""The CLI loads only what the command it runs needs.

Each check starts a fresh interpreter: inside pytest, other tests have already
imported the heavy modules.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import typer.main

import gofer
from gofer.cli.commands.second_brain import fast_main, option_names
from gofer.cli.main import app

SRC = Path(gofer.__file__).resolve().parents[1]
HEAVY = ("sqlalchemy", "apscheduler", "networkx")

_REPORT_MODULES = """
import json, sys
print(json.dumps(sorted(sys.modules)), file=sys.stderr)
"""


def _run(code: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, "-c", code],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def _loaded(result: subprocess.CompletedProcess[str]) -> set[str]:
    return set(json.loads(result.stderr.strip().splitlines()[-1]))


def test_importing_the_cli_skips_the_scheduler_and_graph_libraries() -> None:
    result = _run("import gofer.cli.main\n" + _REPORT_MODULES)

    assert result.returncode == 0, result.stderr
    loaded = _loaded(result)
    assert [name for name in HEAVY if name in loaded] == []


def test_version_builds_the_command_tree_without_heavy_libraries() -> None:
    # Typer reads every command's signature here, so this also catches an option
    # type that can only be resolved by importing the engine.
    code = (
        "import sys\n"
        "sys.argv = ['Raticode', '--version']\n"
        "from gofer.cli.main import app\n"
        "try:\n"
        "    app()\n"
        "except SystemExit:\n"
        "    pass\n" + _REPORT_MODULES
    )
    result = _run(code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Raticode ")
    assert [name for name in HEAVY if name in _loaded(result)] == []


def test_second_brain_starts_without_the_rest_of_the_cli(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        f"sys.argv = ['Raticode', 'ui', 'second-brain', '--root', {str(tmp_path)!r}]\n"
        "try:\n"
        "    import gofer.cli.main\n"
        "except SystemExit as exc:\n"
        "    assert exc.code == 0, exc.code\n"
        "else:\n"
        "    raise AssertionError('the short path did not run')\n" + _REPORT_MODULES
    )
    result = _run(code, stdin='{"id":1,"method":"tools/list"}\n')

    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["result"]["tools"]) == 4
    loaded = _loaded(result)
    assert "gofer.ui.second_brain" in loaded
    assert "typer" not in loaded
    assert not [name for name in loaded if name.startswith("gofer.cli.commands.workflow")]
    assert [name for name in HEAVY if name in loaded] == []


def test_second_brain_short_path_accepts_the_same_options_as_the_command() -> None:
    command = typer.main.get_command(app)
    ui_group = command.commands["ui"]  # type: ignore[attr-defined]
    second_brain = ui_group.commands["second-brain"]
    typer_options = {
        option
        for param in second_brain.params
        for option in param.opts
        if option.startswith("--") and option != "--help"
    }

    assert option_names() == typer_options


def test_second_brain_short_path_leaves_help_and_bad_arguments_to_typer(
    tmp_path: Path,
) -> None:
    assert fast_main(["--root", str(tmp_path), "--help"]) is None
    assert fast_main(["-h"]) is None
    assert fast_main([]) is None
    assert fast_main(["--root", str(tmp_path), "--unknown"]) is None
    assert fast_main(["--root", str(tmp_path), "extra"]) is None
    # Click takes "--thread-id -x" as a value; argparse doesn't, so Typer handles it.
    assert fast_main(["--root", str(tmp_path), "--thread-id", "-x"]) is None
    # No prefix matching: Click would reject "--repo", so the short path must too.
    assert fast_main(["--root", str(tmp_path), "--repo", "html"]) is None
