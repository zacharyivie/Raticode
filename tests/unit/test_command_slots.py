from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gofer.core import command_slots
from gofer.core.command_slots import (
    SlotSettings,
    acquire,
    classify,
    classify_shell,
    claude_hook,
    holders,
    install,
    read_settings,
    turn_environment,
    validate_settings,
    write_settings,
)
from gofer.utils.process import env_with_executable_on_path

pytestmark = pytest.mark.skipif(os.name == "nt", reason="command slots are POSIX only")

SRC = Path(__file__).resolve().parents[2] / "src"

CASES = [
    ("npm", ["--version"], None),
    ("npm", ["-v"], None),
    ("npm", ["install"], None),
    ("npm", ["ci"], None),
    ("npm", ["run"], None),
    ("npm", ["run", "lint"], None),
    ("npm", ["test"], "test"),
    ("npm", ["t"], "test"),
    ("npm", ["test", "--", "--run"], "test"),
    ("npm", ["--prefix", "frontend", "test"], "test"),
    ("npm", ["--prefix", "frontend", "run", "build"], "build"),
    ("npm", ["run", "build:web"], "build"),
    ("npm", ["run", "test:browser"], "browser"),
    ("npm", ["run", "test:unit"], "test"),
    ("npm", ["run", "typecheck"], "build"),
    ("npm", ["test", "--help"], None),
    ("pnpm", ["-C", "web", "build"], "build"),
    ("pnpm", ["test:e2e"], "browser"),
    ("yarn", ["jest", "src"], "test"),
    ("bun", ["test"], "test"),
    ("npx", ["vitest", "run"], "test"),
    ("npx", ["-y", "tsc", "--noEmit"], "build"),
    ("npx", ["playwright", "test"], "browser"),
    ("npx", ["playwright", "install"], None),
    ("npx", ["prettier", "--check", "."], None),
    ("vitest", [], "test"),
    ("vitest", ["--version"], None),
    ("tsc", ["--noEmit"], "build"),
    ("tsc", ["-v"], None),
    ("vite", ["build"], "build"),
    ("vite", [], None),
    ("pytest", ["-q", "tests/unit"], "test"),
    ("pytest", [], "test"),
    ("pytest", ["-v"], "test"),
    ("pytest", ["-V"], None),
    ("mypy", ["-v"], "build"),
    ("make", ["-v"], None),
    ("mypy", ["src"], "build"),
    ("python", ["-m", "pytest", "-q"], "test"),
    ("python", ["-m", "mypy", "src"], "build"),
    ("python", ["-c", "import pytest"], None),
    ("python", ["script.py"], None),
    ("python", ["-m", "http.server"], None),
    ("cargo", ["build", "--release"], "build"),
    ("cargo", ["test"], "test"),
    ("cargo", ["fmt"], None),
    ("cargo", ["run"], None),
    ("cargo", ["--manifest-path", "a/Cargo.toml", "test"], "test"),
    ("go", ["test", "./..."], "test"),
    ("go", ["build", "./cmd/x"], "build"),
    ("go", ["run", "."], None),
    ("go", ["mod", "tidy"], None),
    ("make", [], "build"),
    ("make", ["-C", "lib"], "build"),
    ("make", ["test"], "test"),
    ("make", ["-C", "lib", "check"], "test"),
    ("make", ["clean"], None),
    ("ninja", ["-C", "out"], "build"),
    ("cmake", ["-S", ".", "-B", "build"], None),
    ("cmake", ["--build", "build"], "build"),
    ("gradle", ["test"], "test"),
    ("gradle", ["assembleDebug"], "build"),
    ("mvn", ["-q", "package"], "build"),
    ("playwright", ["test"], "browser"),
    ("cypress", ["run"], "browser"),
    ("cypress", ["open"], None),
    ("dotnet", ["test"], "test"),
    ("unknown-tool", ["test"], None),
]


@pytest.mark.parametrize(("tool", "args", "expected"), CASES)
def test_classify(tool: str, args: list[str], expected: str | None) -> None:
    assert classify(tool, args) == expected


@pytest.fixture
def wrapped(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Wrappers in front of fake programs, with a fake gof that reports the class."""
    real = tmp_path / "real"
    real.mkdir()
    for tool in command_slots.TOOLS:
        script = real / tool
        script.write_text('#!/bin/sh\necho "real $*"\n')
        script.chmod(0o755)
    gof = tmp_path / "gof"
    gof.write_text('#!/bin/sh\nshift 3\nshift\necho "slot $CLASS_SEEN"\n')
    gof.write_text(
        "#!/bin/sh\n"
        '[ "$1" = slot ] && [ "$2" = run ] && [ "$3" = --class ] && [ "$5" = -- ] || exit 9\n'
        'echo "slot $4"\n'
    )
    gof.chmod(0o755)
    root = tmp_path / "slots"
    bin_dir = install(root, gof, str(real))
    env = {"PATH": f"{bin_dir}:{real}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return bin_dir, env


def test_shell_wrappers_match_python_rules(wrapped: tuple[Path, dict[str, str]]) -> None:
    bin_dir, env = wrapped
    for tool, args, expected in CASES:
        if tool not in command_slots.TOOLS:
            continue
        output = subprocess.run(
            [str(bin_dir / tool), *args], env=env, capture_output=True, text=True, check=True
        ).stdout.strip()
        if expected is None:
            assert output == f"real {' '.join(args)}".strip(), (tool, args, output)
        else:
            assert output == f"slot {expected}", (tool, args, output)


def test_wrappers_pass_through_inside_a_held_slot(wrapped: tuple[Path, dict[str, str]]) -> None:
    bin_dir, env = wrapped
    output = subprocess.run(
        [str(bin_dir / "vitest"), "run"],
        env={**env, command_slots.HELD_ENV: "test"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert output.strip() == "real run"


def test_wrappers_are_only_installed_for_programs_on_path(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "npm").write_text("#!/bin/sh\n")
    (real / "npm").chmod(0o755)
    root = tmp_path / "slots"
    bin_dir = install(root, tmp_path / "gof", str(real))
    assert sorted(path.name for path in bin_dir.iterdir()) == ["npm"]
    (real / "npm").unlink()
    install(root, tmp_path / "gof", str(real))
    assert list(bin_dir.iterdir()) == []
    # Read-only sandboxes open these with O_RDONLY, which can't create them.
    assert (root / "slots" / "test-0.lock").is_file()


def test_missing_program_reports_not_found(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "pytest").write_text("#!/bin/sh\n")
    (real / "pytest").chmod(0o755)
    bin_dir = install(tmp_path / "slots", tmp_path / "gof", str(real))
    result = subprocess.run(
        [str(bin_dir / "pytest")],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 127
    assert "pytest: command not found" in result.stderr


def test_settings_validation_and_defaults(tmp_path: Path) -> None:
    assert validate_settings(None) == SlotSettings()
    with pytest.raises(ValueError):
        validate_settings({"maxWorkers": 17})
    with pytest.raises(ValueError):
        validate_settings({"test": True})
    with pytest.raises(ValueError):
        validate_settings({"other": 1})
    settings = validate_settings({"enabled": False, "maxWorkers": 12, "test": 3})
    write_settings(tmp_path, settings)
    assert read_settings(tmp_path) == settings
    assert read_settings(tmp_path).resolved()["maxWorkers"] == 12
    assert read_settings(tmp_path / "missing") == SlotSettings()


@pytest.mark.parametrize(
    ("cpus", "memory_gb", "expected"),
    [
        (16, 32, {"maxWorkers": 8, "test": 2, "build": 4, "browser": 1}),
        (16, 31, {"maxWorkers": 7, "test": 2, "build": 4, "browser": 1}),
        (8, 16, {"maxWorkers": 4, "test": 1, "build": 2, "browser": 1}),
        (4, 8, {"maxWorkers": 2, "test": 1, "build": 1, "browser": 1}),
        (2, 2, {"maxWorkers": 1, "test": 1, "build": 1, "browser": 1}),
        (64, 256, {"maxWorkers": 8, "test": 4, "build": 8, "browser": 1}),
    ],
)
def test_default_limits_follow_cores_and_memory(
    cpus: int, memory_gb: int, expected: dict[str, int]
) -> None:
    assert command_slots.default_limits(cpus, memory_gb * 1024**3) == expected


def _no_memory_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_slots, "memory_pressure", lambda: 0.0)
    monkeypatch.setattr(command_slots, "memory_available_bytes", lambda: 8 * 1024**3)


def test_slots_are_exclusive_and_released(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _no_memory_pressure(monkeypatch)
    write_settings(tmp_path, SlotSettings(test=1))
    messages: list[str] = []
    first = acquire(tmp_path, "test", command="pytest", label="Thread A", notify=messages.append)
    assert first is not None
    assert holders(tmp_path, "test", 1) == [
        {"slot": 0, **json.loads((tmp_path / "slots" / "test-0.lock").read_text())}
    ]
    assert holders(tmp_path, "test", 1)[0]["label"] == "Thread A"

    clock = [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        if clock[0] >= 2:
            first.release()

    second = acquire(
        tmp_path,
        "test",
        command="npm test",
        label="Thread B",
        notify=messages.append,
        clock=lambda: clock[0],
        sleep=sleep,
    )
    assert second is not None
    assert "waiting because all 1 test run slots are in use: pytest (Thread A)" in messages[0]
    assert messages[-1].startswith("Raticode: starting npm test after")
    second.release()
    assert holders(tmp_path, "test", 1) == []


def test_other_classes_do_not_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_memory_pressure(monkeypatch)
    write_settings(tmp_path, SlotSettings(test=1, build=1))
    test = acquire(tmp_path, "test", command="pytest", label="", notify=print)
    build = acquire(tmp_path, "build", command="tsc", label="", notify=print, sleep=pytest.fail)
    assert test is not None and build is not None
    test.release()
    build.release()


def test_wait_limit_runs_the_command_anyway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _no_memory_pressure(monkeypatch)
    write_settings(tmp_path, SlotSettings(test=1))
    held = acquire(tmp_path, "test", command="pytest", label="", notify=print)
    clock = [0.0]
    messages: list[str] = []
    lease = acquire(
        tmp_path,
        "test",
        command="pytest",
        label="",
        notify=messages.append,
        limit_seconds=5,
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    assert lease is None
    assert "is starting anyway" in messages[-1]
    assert held is not None
    held.release()


def test_turning_queuing_off_releases_waiters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _no_memory_pressure(monkeypatch)
    write_settings(tmp_path, SlotSettings(test=1))
    held = acquire(tmp_path, "test", command="pytest", label="", notify=print)

    def sleep(_seconds: float) -> None:
        write_settings(tmp_path, SlotSettings(enabled=False))

    assert acquire(tmp_path, "test", command="pytest", label="", notify=print, sleep=sleep) is None
    assert held is not None
    held.release()


def test_memory_pressure_holds_new_slots_while_others_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_settings(tmp_path, SlotSettings(test=2, build=2))
    monkeypatch.setattr(command_slots, "memory_available_bytes", lambda: None)
    pressure = [50.0]
    monkeypatch.setattr(command_slots, "memory_pressure", lambda: pressure[0])
    # Nothing else is running, so the first command starts despite the pressure.
    build = acquire(tmp_path, "build", command="tsc", label="", notify=print)
    assert build is not None
    messages: list[str] = []

    def sleep(_seconds: float) -> None:
        pressure[0] = 0.0

    test = acquire(
        tmp_path, "test", command="pytest", label="", notify=messages.append, sleep=sleep
    )
    assert test is not None
    assert "memory is under pressure (50% stalled)" in messages[0]
    build.release()
    test.release()


def test_waiters_start_in_arrival_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_memory_pressure(monkeypatch)
    write_settings(tmp_path, SlotSettings(test=1))
    held = acquire(tmp_path, "test", command="first", label="", notify=lambda _m: None)
    assert held is not None
    order: list[str] = []
    errors: list[BaseException] = []

    def wait(name: str) -> None:
        try:
            lease = acquire(tmp_path, "test", command=name, label="", notify=lambda _m: None)
            order.append(name)
            time.sleep(0.2)
            assert lease is not None
            lease.release()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = []
    for name in ("second", "third"):
        thread = threading.Thread(target=wait, args=(name,))
        thread.start()
        threads.append(thread)
        time.sleep(0.3)
    held.release()
    for thread in threads:
        thread.join(10)
    assert not errors
    assert order == ["second", "third"]


def _gof_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC),
        command_slots.ROOT_ENV: str(tmp_path / "slots"),
        **extra,
    }


def _gof(*args: str) -> list[str]:
    return [sys.executable, "-c", "import gofer.cli.main", "slot", *args]


def test_slot_run_marks_the_slot_and_keeps_the_exit_status(tmp_path: Path) -> None:
    activity = tmp_path / "activity"
    result = subprocess.run(
        _gof(
            "run",
            "--class",
            "test",
            "--",
            "sh",
            "-c",
            'echo "held=$RATICODE_COMMAND_SLOT_HELD"; exit 3',
        ),
        env=_gof_env(tmp_path, **{command_slots.ACTIVITY_ENV: str(activity)}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 3
    assert result.stdout.strip() == "held=test"
    # The heartbeat tells Rem's idle check that the turn is busy.
    assert activity.exists()


def test_slot_run_waits_for_a_command_in_another_process(tmp_path: Path) -> None:
    write_settings(tmp_path / "slots", SlotSettings(test=1))
    env = _gof_env(tmp_path, **{command_slots.LABEL_ENV: "Thread A"})
    first = subprocess.Popen(
        _gof("run", "--class", "test", "--", "sleep", "1.5"), env=env, stderr=subprocess.PIPE
    )
    deadline = time.monotonic() + 10
    while not holders(tmp_path / "slots", "test", 1) and time.monotonic() < deadline:
        time.sleep(0.05)
    started = time.monotonic()
    second = subprocess.run(
        _gof("run", "--class", "test", "--", "true"),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    first.wait(10)
    assert second.returncode == 0
    assert time.monotonic() - started > 0.5
    assert "is waiting because all 1 test run slots are in use: sleep 1.5 (Thread A)" in (
        second.stderr
    )


def test_status_lists_limits(tmp_path: Path) -> None:
    write_settings(tmp_path / "slots", SlotSettings(build=3))
    result = subprocess.run(
        _gof("status"), env=_gof_env(tmp_path), capture_output=True, text=True, check=True
    )
    status = json.loads(result.stdout)
    assert status["limits"]["build"] == 3
    assert status["slots"]["build"] == []


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls -la", (None, False)),
        ("git commit -m 'build stuff'", (None, False)),
        ("npm test", ("test", False)),
        ("cd frontend && npm run build", ("build", False)),
        (".venv/bin/pytest -q tests", ("test", True)),
        ("env -u FORCE_COLOR NO_COLOR=1 ../.venv/bin/python -m pytest", ("test", True)),
        ("uv run pytest -x", ("test", True)),
        ("uv run --frozen --with rich mypy src", ("build", True)),
        ("node node_modules/.bin/vitest run", ("test", True)),
        ("./gradlew test", ("test", True)),
        ("timeout 600 pytest -q", ("test", False)),
        ("source .venv/bin/activate && pytest", ("test", True)),
        ("npm run build && npm run test:browser", ("browser", False)),
        ("bash -c '.venv/bin/pytest'", ("test", True)),
        ("echo one\n.venv/bin/mypy src", ("build", True)),
        ("PATH=/opt/bin:$PATH pytest", ("test", True)),
        ("xvfb-run -a npm run test:browser 2>&1 | tail", ("browser", False)),
    ],
)
def test_classify_shell(command: str, expected: tuple[str | None, bool]) -> None:
    assert classify_shell(command) == expected


def test_claude_hook_rewrites_only_hidden_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(command_slots.HELD_ENV, raising=False)
    gof = "/data/bin/gof"
    hidden = claude_hook(
        {"tool_name": "Bash", "tool_input": {"command": ".venv/bin/pytest -q", "description": "x"}},
        gof,
    )
    assert hidden == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {
                "command": "/data/bin/gof slot run --class test -- bash -c '.venv/bin/pytest -q'",
                "description": "x",
                "timeout": command_slots.CLAUDE_GATED_TIMEOUT_MS,
            },
        }
    }
    visible = claude_hook({"tool_name": "Bash", "tool_input": {"command": "npm test"}}, gof)
    assert visible is not None
    assert visible["hookSpecificOutput"]["updatedInput"] == {
        "command": "npm test",
        "timeout": command_slots.CLAUDE_GATED_TIMEOUT_MS,
    }
    assert (
        claude_hook(
            {"tool_name": "Bash", "tool_input": {"command": "npm test", "timeout": 900000}}, gof
        )
        is None
    )
    assert claude_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, gof) is None
    assert claude_hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}, gof) is None
    rewritten = hidden["hookSpecificOutput"]["updatedInput"]
    assert claude_hook({"tool_name": "Bash", "tool_input": rewritten}, gof) is None


def test_hook_script_skips_python_for_ordinary_commands(tmp_path: Path) -> None:
    gof = tmp_path / "gof"
    gof.write_text("#!/bin/sh\ncat >/dev/null\necho called\n")
    gof.chmod(0o755)
    install(tmp_path / "slots", gof, "")
    hook = tmp_path / "slots" / "hooks" / "claude-pre-tool-use"

    def call(command: str) -> str:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        return subprocess.run(
            [str(hook)], input=payload, capture_output=True, text=True, check=True
        ).stdout.strip()

    assert call("ls -la") == ""
    assert call(".venv/bin/pytest") == "called"
    assert call("npm test") == "called"


def test_turn_environment_puts_wrappers_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    real = tmp_path / "real"
    real.mkdir()
    (real / "npm").write_text("#!/bin/sh\n")
    (real / "npm").chmod(0o755)
    monkeypatch.setenv("PATH", f"{real}:/usr/bin")
    env = turn_environment(tmp_path, tmp_path / "gof", thread_id="thread-1", label="My thread")
    bin_dir = env[command_slots.BIN_ENV]
    assert env["PATH"].split(":")[0] == bin_dir
    assert env[command_slots.LABEL_ENV] == "My thread"
    assert env[command_slots.ACTIVITY_ENV] == str(
        command_slots.activity_path(tmp_path / "command-slots", "thread-1")
    )
    assert command_slots.SLICE_ENV not in env
    # A provider found outside PATH gets its folder added after the wrappers.
    provider = env_with_executable_on_path("/opt/node/bin/claude", env)
    assert provider["PATH"].split(":")[:2] == [bin_dir, "/opt/node/bin"]

    write_settings(tmp_path / "command-slots", SlotSettings(enabled=False))
    assert turn_environment(tmp_path, tmp_path / "gof", thread_id="t", label="") == {}
    assert turn_environment(tmp_path, None, thread_id="t", label="") == {}


def test_scoped_command_uses_the_slice_only_when_asked() -> None:
    command = ["claude", "--print"]
    assert command_slots.scoped_command(command, {}) == command
    scoped = command_slots.scoped_command(command, {command_slots.SLICE_ENV: "raticode.slice"})
    assert scoped == [*command_slots.scope_prefix(), *command]
    assert command_slots.scoped_command(scoped, {command_slots.SLICE_ENV: "raticode.slice"}) == (
        scoped
    )


def test_claude_settings_register_the_hook(tmp_path: Path) -> None:
    settings = json.loads(command_slots.claude_settings(tmp_path))
    (entry,) = settings["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0]["command"] == str(tmp_path / "hooks" / "claude-pre-tool-use")


def test_last_activity_reads_the_heartbeat(tmp_path: Path) -> None:
    assert command_slots.last_activity(tmp_path, "thread") is None
    path = command_slots.activity_path(tmp_path, "thread")
    path.parent.mkdir(parents=True)
    with command_slots.heartbeat(str(path)):
        pass
    assert abs((command_slots.last_activity(tmp_path, "thread") or 0) - time.time()) < 5
