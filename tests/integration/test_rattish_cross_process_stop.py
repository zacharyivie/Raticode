"""Stop a run that a separate `Raticode rattish triggers start` process owns.

The scheduler here is a real second process with its own file watcher, and the
workflow's node runs a real command. The app's Stop (and the phone's, which uses
the same call) runs in this test's process, and `rattish stop` runs in a third
process with no backend anywhere.

On Windows the workflow's command runs in PowerShell, where `sleep` is
Start-Sleep, so the same test runs there too.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

import gofer
import gofer.ui.api as api
from gofer.rattish.run_service import run_stop_marker
from gofer.rattish.workspaces import create_registered_workflow

CLI = "from gofer.cli.main import app; app()"


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait(read: Callable[[], Any], timeout: float = 30, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"Nothing after {timeout}s")


def _record(path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # Windows can refuse a read while the owner swaps in a new record.
        return {}


def _in_scheduler(document: dict[str, Any], scheduler: subprocess.Popen[str]) -> bool:
    owner = document.get("runner_pid")
    if not isinstance(owner, int) or owner == os.getpid():
        return False
    # A virtual environment's python.exe on Windows starts the interpreter as a
    # child, so the run's process ID isn't the one Popen saw.
    return sys.platform == "win32" or owner == scheduler.pid


@pytest.fixture
def scheduler(tmp_path: Path) -> Iterator[dict[str, Any]]:
    base = tmp_path / "data"
    project = tmp_path / "project"
    inbox = project / "inbox"
    inbox.mkdir(parents=True)
    workflow = create_registered_workflow(project, "Slow job", registry_dir=base)
    workflow.entrypoint.write_text(
        "Rattish: 1\nWorkflow:\n  name: Slow job\n"
        "Node work:\n  type: bash-command\n  command: sleep 120\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "GOFER_DATA_DIR": str(base),
        "NO_COLOR": "1",
        "PYTHONPATH": os.pathsep.join(
            [str(Path(gofer.__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")]
        ),
        # Nothing listens here, so the CLI finds no backend.
        "RATICODE_URL": f"http://127.0.0.1:{_unused_port()}",
    }

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", CLI, *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    added = cli(
        "rattish", "triggers", "add", "file", str(workflow.entrypoint),
        "--path", str(inbox), "--debounce", "0.2", "--overlap", "parallel", "--format", "json",
    )  # fmt: skip
    assert added.returncode == 0, added.stdout + added.stderr
    log = (tmp_path / "scheduler.log").open("w+", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-c", CLI, "rattish", "triggers", "start", "--data-dir", str(base)],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    logs = workflow.workflow_root / "logs"

    def fire(name: str) -> Path:
        before = set(logs.glob("*.json")) if logs.exists() else set()
        (inbox / name).write_text(name, encoding="utf-8")
        record = _wait(lambda: next(iter(set(logs.glob("*.json")) - before), None))
        # Running in the scheduler's process, not this one.
        _wait(lambda: _in_scheduler(_record(record), process))
        return Path(record)

    try:
        _wait(lambda: "Running triggers" in (tmp_path / "scheduler.log").read_text("utf-8"))
        yield {
            "base": base,
            "workflow": workflow,
            "fire": fire,
            "cli": cli,
            "process": process,
        }
        # Windows can't send Ctrl+C to another console process, so `finally` ends it.
        if sys.platform != "win32":
            process.send_signal(signal.SIGINT)
            assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        log.seek(0)
        output = log.read()
        log.close()
        print(output)


def _ended(record: Path) -> dict[str, Any] | None:
    document = _record(record)
    return document if document.get("status") not in (None, "running") else None


def test_runs_the_trigger_scheduler_started_stop_from_the_app_and_the_cli(
    scheduler: dict[str, Any],
) -> None:
    base, workflow, fire, cli = (scheduler[key] for key in ("base", "workflow", "fire", "cli"))
    workflow_id = workflow.workflow_id

    # The app's Stop button and the phone's Stop both call this in the backend.
    record = fire("first.txt")
    run_id = record.stem
    started = time.monotonic()
    answer = api.stop_workflow_run_payload(workflow_id, base, run_id)
    assert answer == {
        "workflowId": workflow_id,
        "runId": run_id,
        "stopped": True,
        "message": "Stop requested",
    }
    document = _wait(lambda: _ended(record), timeout=10)
    assert document["status"] == "stopped"
    assert time.monotonic() - started < 10
    assert document["events"][-1]["status"] == "stopped"
    assert not run_stop_marker(record).exists()
    row = next(
        run
        for run in api.list_workflow_run_logs_payload(workflow_id, base)["runs"]
        if run["id"] == run_id
    )
    assert (row["status"], row["stopRequested"]) == ("stopped", False)

    # `rattish stop` in its own process, with no backend running.
    record = fire("second.txt")
    stopped = cli("rattish", "stop", workflow_id, "--run", record.stem)
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    data = json.loads(stopped.stdout)["data"]
    assert data == {
        "workflowId": workflow_id,
        "runId": record.stem,
        "stopped": True,
        "runIds": [record.stem],
        "backend": False,
        "message": "Stop requested",
    }
    assert _wait(lambda: _ended(record), timeout=10)["status"] == "stopped"
    assert not run_stop_marker(record).exists()

    # Without --run it stops every run of the workflow; a finished run is left alone.
    third, fourth = fire("third.txt"), fire("fourth.txt")
    stopped = cli("rattish", "stop", workflow_id)
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert sorted(json.loads(stopped.stdout)["data"]["runIds"]) == sorted([third.stem, fourth.stem])
    for each in (third, fourth):
        assert _wait(partial(_ended, each), timeout=10)["status"] == "stopped"
    again = cli("rattish", "stop", workflow_id, "--run", record.stem)
    assert json.loads(again.stdout)["data"]["stopped"] is False
    assert not list(workflow.workflow_root.joinpath("logs").glob("*.stop"))
