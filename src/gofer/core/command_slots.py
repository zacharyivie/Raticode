"""Share expensive agent commands (tests, builds, browser runs) between threads.

Each provider turn gets small wrapper scripts at the front of PATH. A wrapper
hands ordinary commands straight to the real program and sends recognised
tests, builds and browser runs through ``gof slot run``, which waits for one of
a few lock files per class. ``flock`` releases a lock when its holder exits, so
a crashed agent never leaves a slot taken. This is cooperative throttling, not
a security boundary: an agent can still start a program another way.

The module uses only the standard library because every gated command and every
Claude Code shell call (through the hook) starts a fresh process that imports it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.platform != "win32":
    import fcntl

CLASSES = ("browser", "test", "build")
# Commands that hold several classes at once take the heaviest one.
CLASS_RANK = {"build": 1, "test": 2, "browser": 3}
CLASS_LABELS = {"test": "test run", "build": "build", "browser": "browser test run"}
MAX_SLOTS = 16
MAX_WORKERS = 16

ROOT_ENV = "RATICODE_COMMAND_SLOTS"
BIN_ENV = "RATICODE_COMMAND_SLOTS_BIN"
HELD_ENV = "RATICODE_COMMAND_SLOT_HELD"
LABEL_ENV = "RATICODE_COMMAND_SLOT_LABEL"
ACTIVITY_ENV = "RATICODE_COMMAND_ACTIVITY"
# Set when the provider process runs inside Raticode's systemd slice.
SLICE_ENV = "RATICODE_AGENT_SLICE"
SLICE = "raticode.slice"
PATH_ENV = "PATH"

WAIT_LIMIT_SECONDS = 30 * 60
WAIT_NOTICE_SECONDS = 60
HEARTBEAT_SECONDS = 30
POLL_SECONDS = 0.5
# Hold back new slots while tasks stall on memory for this share of the last
# ten seconds, or while less than this much memory is free.
MEMORY_PRESSURE_LIMIT = 20.0
MEMORY_AVAILABLE_FLOOR = 1024**3
# A new Rem worker needs about half a gigabyte for its provider and MCP servers.
WORKER_MEMORY_FLOOR = 3 * 1024**3 // 2
# Claude Code's Bash tool stops commands after two minutes unless told otherwise.
# Queued commands get its ten-minute maximum.
CLAUDE_GATED_TIMEOUT_MS = 600_000
SLICE_CPU_WEIGHT = 50
SLICE_IO_WEIGHT = 50
SLICE_MEMORY_HIGH = "85%"


@dataclass(frozen=True)
class ToolRule:
    """How one program's arguments map to a slot class.

    The rule sees the first two positional arguments joined by one space
    (``"run build:web"``). ``patterns`` use ``*`` as the only wildcard, and
    the first match wins. ``value_options`` take the next argument as their
    value, so it is not read as a positional.
    """

    patterns: tuple[tuple[str, str | None], ...] = ()
    default: str | None = None
    value_options: tuple[str, ...] = ()
    flags: tuple[tuple[str, str], ...] = ()
    # Whether a lone ``-v`` prints the version. For pytest or mypy it means verbose.
    short_version: bool = False


def _words(*names: str) -> tuple[str, ...]:
    return tuple(f"{name} *" for name in names)


_PACKAGE_MANAGER = (
    *(
        (pattern, "browser")
        for pattern in (
            "run test:browser*",
            "run-script test:browser*",
            "run test:e2e*",
            "run e2e*",
            "run playwright*",
            "run cypress*",
            "exec playwright*",
            "test:browser*",
            "test:e2e*",
            "e2e*",
            "playwright test*",
        )
    ),
    *(
        (pattern, "test")
        for pattern in (
            *_words("test", "t", "tst", "vitest", "jest", "mocha"),
            "test:*",
            "run test*",
            "run-script test*",
            "run vitest*",
            "run jest*",
            "exec vitest*",
            "exec jest*",
        )
    ),
    *(
        (pattern, "build")
        for pattern in (
            *_words("build", "tsc", "typecheck"),
            "build:*",
            "run build*",
            "run-script build*",
            "run typecheck*",
            "run compile*",
            "run tsc*",
            "exec tsc*",
        )
    ),
)
_PACKAGE_RUNNER = (
    *((pattern, "browser") for pattern in ("playwright test*", "cypress run*")),
    *((pattern, "test") for pattern in _words("vitest", "jest", "mocha")),
    *((pattern, "build") for pattern in (*_words("tsc", "webpack"), "vite build*", "next build*")),
)
_PYTHON_MODULES = (
    *((pattern, "test") for pattern in _words("pytest", "tox", "nox")),
    *((pattern, "build") for pattern in _words("mypy", "pyright")),
)
_TEST_OR_BUILD = (("test *", "test"), ("build *", "build"))

TOOLS: dict[str, ToolRule] = {
    "npm": ToolRule(
        _PACKAGE_MANAGER, value_options=("--prefix", "-C", "--workspace", "-w"), short_version=True
    ),
    "pnpm": ToolRule(
        _PACKAGE_MANAGER, value_options=("--dir", "-C", "--filter", "-F"), short_version=True
    ),
    "yarn": ToolRule(_PACKAGE_MANAGER, value_options=("--cwd",), short_version=True),
    "bun": ToolRule(_PACKAGE_MANAGER, value_options=("--cwd",), short_version=True),
    "npx": ToolRule(_PACKAGE_RUNNER, value_options=("-p", "--package"), short_version=True),
    "pnpx": ToolRule(_PACKAGE_RUNNER, short_version=True),
    "bunx": ToolRule(_PACKAGE_RUNNER, short_version=True),
    "vitest": ToolRule(default="test", short_version=True),
    "jest": ToolRule(default="test", short_version=True),
    "mocha": ToolRule(default="test"),
    "playwright": ToolRule((("test *", "browser"),)),
    "cypress": ToolRule((("run *", "browser"),)),
    "tsc": ToolRule(default="build", short_version=True),
    "vite": ToolRule((("build *", "build"),), short_version=True),
    "next": ToolRule((("build *", "build"),), short_version=True),
    "webpack": ToolRule(default="build", short_version=True),
    "pytest": ToolRule(default="test"),
    "py.test": ToolRule(default="test"),
    "tox": ToolRule(default="test"),
    "nox": ToolRule(default="test"),
    "mypy": ToolRule(default="build"),
    "pyright": ToolRule(default="build"),
    "python": ToolRule(_PYTHON_MODULES, value_options=("-c", "-W", "-X")),
    "python3": ToolRule(_PYTHON_MODULES, value_options=("-c", "-W", "-X")),
    "cargo": ToolRule(
        (
            *((pattern, "test") for pattern in _words("test", "t", "nextest", "bench")),
            *(
                (pattern, "build")
                for pattern in _words("build", "b", "check", "c", "clippy", "doc", "install")
            ),
        ),
        value_options=("--manifest-path", "-p", "--package", "--target-dir", "--config", "-Z"),
    ),
    "go": ToolRule(
        (("test *", "test"), *((pattern, "build") for pattern in _words("build", "install", "vet")))
    ),
    "make": ToolRule(
        (
            *((pattern, None) for pattern in _words("clean", "help", "fmt", "format")),
            *((pattern, "test") for pattern in _words("test", "tests", "check")),
        ),
        default="build",
        value_options=("-C", "-f", "--directory", "--file"),
        short_version=True,
    ),
    "ninja": ToolRule(default="build", value_options=("-C", "-f", "-j", "-l", "-k", "-d", "-w")),
    "cmake": ToolRule(flags=(("--build", "build"),)),
    "gradle": ToolRule(
        (
            *((pattern, "test") for pattern in _words("test", "check")),
            *((pattern, "build") for pattern in (*_words("build"), "assemble*", "compile*")),
        ),
        value_options=("-p", "--project-dir"),
    ),
    "mvn": ToolRule(
        (
            *((pattern, "test") for pattern in _words("test", "verify")),
            *((pattern, "build") for pattern in _words("package", "install", "compile")),
        ),
        value_options=("-f", "-pl", "-P"),
    ),
    "dotnet": ToolRule((*_TEST_OR_BUILD, ("publish *", "build"))),
    "bazel": ToolRule(_TEST_OR_BUILD),
    "bazelisk": ToolRule(_TEST_OR_BUILD),
    "swift": ToolRule(_TEST_OR_BUILD),
}
# Project-local launchers are always called by path, so only the Claude Code hook
# can see them. PATH wrappers are not installed for these names.
ALIASES = {"gradlew": "gradle", "mvnw": "mvn"}
TRIVIAL_ONLY_ARGUMENTS = {"-V", "-h"}
TRIVIAL_ARGUMENTS = {"--version", "--help"}


def _match(pattern: str, value: str) -> bool:
    expression = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(expression, value, re.DOTALL) is not None


def classify(tool: str, args: list[str]) -> str | None:
    """Return the slot class for running ``tool args``, or None to run it directly.

    The generated wrapper scripts implement the same steps in POSIX shell.
    """
    rule = TOOLS.get(ALIASES.get(tool, tool))
    if rule is None:
        return None
    if len(args) == 1 and (
        args[0] in TRIVIAL_ONLY_ARGUMENTS or (rule.short_version and args[0] == "-v")
    ):
        return None
    positionals: list[str] = []
    flag_class: str | None = None
    skip = False
    for arg in args:
        if arg in TRIVIAL_ARGUMENTS:
            return None
        if skip:
            skip = False
            continue
        for flag, flag_kind in rule.flags:
            if arg == flag and flag_class is None:
                flag_class = flag_kind
        if len(positionals) >= 2:
            continue
        if arg in rule.value_options:
            skip = True
            continue
        if arg.startswith("-"):
            continue
        positionals.append(arg)
    if flag_class is not None:
        return flag_class
    words = (positionals + ["", ""])[:2]
    subject = f"{words[0]} {words[1]}"
    for pattern, kind in rule.patterns:
        if _match(pattern, subject):
            return kind
    return rule.default


# Settings ------------------------------------------------------------------


def cpu_count() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def memory_total_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def memory_available_bytes() -> int | None:
    """Memory the kernel can hand out without swapping, when it reports one."""
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def memory_pressure() -> float | None:
    """Share of the last ten seconds some task stalled waiting for memory (Linux PSI)."""
    try:
        with open("/proc/pressure/memory", encoding="ascii") as pressure:
            for line in pressure:
                if line.startswith("some "):
                    fields = dict(item.split("=", 1) for item in line.split()[1:])
                    return float(fields["avg10"])
    except (OSError, ValueError, KeyError):
        pass
    return None


def default_limits(cpus: int | None = None, memory: int | None = None) -> dict[str, int]:
    """Automatic limits for this computer.

    Workers mostly wait on the model, so memory sets their limit: about 0.5 GB
    idle each, kept to a quarter of RAM. Tests and builds use several cores
    each, so their slots follow the core count.
    """
    cpus = cpus or cpu_count()
    memory = memory if memory is not None else memory_total_bytes()
    memory_gb = (memory or 8 * 1024**3) // 1024**3
    return {
        "maxWorkers": max(1, min(8, cpus // 2, memory_gb // 4)),
        "test": max(1, min(4, cpus // 8)),
        "build": max(1, min(8, cpus // 4)),
        "browser": 1,
    }


@dataclass(frozen=True)
class SlotSettings:
    enabled: bool = True
    max_workers: int = 0
    test: int = 0
    build: int = 0
    browser: int = 0

    def resolved(self) -> dict[str, int]:
        """Limits with automatic (0) values replaced by this computer's defaults."""
        defaults = default_limits()
        return {
            "maxWorkers": self.max_workers or defaults["maxWorkers"],
            "test": self.test or defaults["test"],
            "build": self.build or defaults["build"],
            "browser": self.browser or defaults["browser"],
        }

    def capacity(self, kind: str) -> int:
        return self.resolved()[kind]

    def as_json(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "maxWorkers": self.max_workers,
            "test": self.test,
            "build": self.build,
            "browser": self.browser,
        }


def validate_settings(value: Any) -> SlotSettings:
    """Check settings sent by the desktop. Zero means automatic."""
    if value is None:
        return SlotSettings()
    if not isinstance(value, dict) or set(value) - {
        "enabled",
        "maxWorkers",
        "test",
        "build",
        "browser",
    }:
        raise ValueError("Unknown command slot settings")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("Invalid command slot setting")
    numbers: dict[str, int] = {}
    for key, limit in (
        ("maxWorkers", MAX_WORKERS),
        ("test", MAX_SLOTS),
        ("build", MAX_SLOTS),
        ("browser", MAX_SLOTS),
    ):
        number = value.get(key, 0)
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= limit:
            raise ValueError(f"{key} must be a whole number from 0 to {limit}")
        numbers[key] = number
    return SlotSettings(
        enabled=enabled,
        max_workers=numbers["maxWorkers"],
        test=numbers["test"],
        build=numbers["build"],
        browser=numbers["browser"],
    )


def slots_root(data_dir: Path) -> Path:
    return data_dir / "command-slots"


def read_settings(root: Path) -> SlotSettings:
    try:
        return validate_settings(json.loads((root / "config.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return SlotSettings()


def write_settings(root: Path, settings: SlotSettings) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_if_changed(root / "config.json", json.dumps(settings.as_json(), sort_keys=True) + "\n")


def _write_if_changed(path: Path, content: str, mode: int = 0o600) -> None:
    try:
        if path.read_text(encoding="utf-8") == content:
            return
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


# Wrapper scripts -------------------------------------------------------------


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _shell_pattern(pattern: str) -> str:
    return "*".join(_shell_quote(part) if part else "" for part in pattern.split("*"))


def _shell_alternatives(values: tuple[str, ...]) -> str:
    return "|".join(_shell_quote(value) for value in values)


def wrapper_script(tool: str, bin_dir: Path, gof: Path) -> str:
    rule = TOOLS[tool]
    trivial = TRIVIAL_ONLY_ARGUMENTS | ({"-v"} if rule.short_version else set())
    lines = [
        "#!/bin/sh",
        f"# Raticode command slot wrapper for {tool}. Raticode rewrites this file.",
        f"self={_shell_quote(str(bin_dir))}",
        "real=",
        "set -f",
        "old_ifs=$IFS",
        "IFS=:",
        "for dir in $PATH; do",
        '  if [ -z "$dir" ] || [ "$dir" = "$self" ]; then continue; fi',
        f'  if [ -f "$dir/{tool}" ] && [ -x "$dir/{tool}" ]; then real="$dir/{tool}"; break; fi',
        "done",
        "IFS=$old_ifs",
        "set +f",
        'if [ -z "$real" ]; then',
        f'  echo "{tool}: command not found" >&2',
        "  exit 127",
        "fi",
        f'if [ -n "${{{HELD_ENV}:-}}" ]; then exec "$real" "$@"; fi',
        'if [ "$#" -eq 1 ]; then',
        f'  case "$1" in {_shell_alternatives(tuple(sorted(trivial)))}) exec "$real" "$@";; esac',
        "fi",
        "sub1= sub2= count=0 skip= class=",
        'for arg in "$@"; do',
        f'  case "$arg" in {_shell_alternatives(tuple(sorted(TRIVIAL_ARGUMENTS)))})'
        ' exec "$real" "$@";; esac',
        '  if [ -n "$skip" ]; then skip=; continue; fi',
    ]
    for flag, flag_kind in rule.flags:
        lines.append(
            f'  if [ "$arg" = {_shell_quote(flag)} ] && [ -z "$class" ]; then class={flag_kind}; fi'
        )
    lines += ['  if [ "$count" -ge 2 ]; then continue; fi', '  case "$arg" in']
    if rule.value_options:
        lines.append(f"    {_shell_alternatives(rule.value_options)}) skip=1; continue;;")
    lines += [
        "    -*) continue;;",
        "  esac",
        "  count=$((count + 1))",
        '  if [ "$count" -eq 1 ]; then sub1=$arg; else sub2=$arg; fi',
        "done",
        'if [ -z "$class" ]; then',
        '  case "$sub1 $sub2" in',
    ]
    for pattern, kind in rule.patterns:
        action = 'exec "$real" "$@"' if kind is None else f"class={kind}"
        lines.append(f"    {_shell_pattern(pattern)}) {action};;")
    if rule.default:
        lines.append(f"    *) class={rule.default};;")
    lines += [
        "  esac",
        "fi",
        'if [ -z "$class" ]; then exec "$real" "$@"; fi',
        f'exec {_shell_quote(str(gof))} slot run --class "$class" -- "$real" "$@"',
        "",
    ]
    return "\n".join(lines)


# Words that may start a gated command. The hook only starts Python for these.
HOOK_WORDS = (
    *sorted(
        {*TOOLS, *ALIASES}
        - {"npm", "pnpm", "yarn", "bun", "npx", "pnpx", "bunx", "go", "python", "python3"}
        - {"next", "vite"}
    ),
    " test",
    "build",
    "e2e",
    "go ",
)


def hook_script(gof: Path) -> str:
    words = "|".join(f"*{_shell_quote(word)}*" for word in HOOK_WORDS)
    return "\n".join(
        [
            "#!/bin/sh",
            "# Raticode command slot hook for Claude Code. Raticode rewrites this file.",
            "input=$(cat)",
            f'case "$input" in {words})',
            f"  printf '%s' \"$input\" | exec {_shell_quote(str(gof))} slot hook claude;;",
            "esac",
            "exit 0",
            "",
        ]
    )


def install(root: Path, gof: Path, search_path: str | None = None) -> Path:
    """Write wrappers, the hook and lock files. Returns the wrapper directory.

    Only programs found on ``search_path`` get a wrapper, so ``command -v`` keeps
    telling agents the truth about what is installed. Lock files exist up front
    so turns in read-only sandboxes can still open them; ``flock`` works on a
    read-only descriptor.
    """
    root = root.resolve()
    bin_dir = root / "bin"
    for folder in (bin_dir, root / "hooks", root / "slots", root / "queue", root / "activity"):
        folder.mkdir(parents=True, exist_ok=True)
    folders = [
        folder
        for folder in (
            search_path if search_path is not None else os.environ.get("PATH", "")
        ).split(os.pathsep)
        if folder and Path(folder) != bin_dir
    ]
    for tool in TOOLS:
        target = bin_dir / tool
        if any(os.access(Path(folder) / tool, os.X_OK) for folder in folders):
            _write_if_changed(target, wrapper_script(tool, bin_dir, gof), 0o700)
        else:
            with contextlib.suppress(OSError):
                target.unlink()
    _write_if_changed(root / "hooks" / "claude-pre-tool-use", hook_script(gof), 0o700)
    for kind in CLASSES:
        for index in range(MAX_SLOTS):
            path = _slot_path(root, kind, index)
            if not path.exists():
                with contextlib.suppress(OSError):
                    path.touch(mode=0o600)
    return bin_dir


def activity_path(root: Path, thread_id: str) -> Path:
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:32]
    return root / "activity" / digest


def last_activity(root: Path, thread_id: str) -> float | None:
    """Wall-clock time a gated command in this thread last reported progress."""
    try:
        return activity_path(root, thread_id).stat().st_mtime
    except OSError:
        return None


def claude_settings(root: Path) -> str:
    """Claude Code ``--settings`` JSON that routes shell calls through the hook."""
    hook = root / "hooks" / "claude-pre-tool-use"
    return json.dumps(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": str(hook), "timeout": 30}],
                    }
                ]
            }
        },
        separators=(",", ":"),
    )


_systemd_lock = threading.Lock()
_systemd_ready: bool | None = None


def systemd_slice_ready() -> bool:
    """Create Raticode's lower-priority systemd slice once per backend process.

    Agents then compete with the user's own apps at half their CPU and disk
    weight, and the whole slice slows down (rather than being killed) near 85%
    of memory.
    """
    global _systemd_ready
    if sys.platform != "linux":
        return False
    with _systemd_lock:
        if _systemd_ready is None:
            try:
                completed = subprocess.run(
                    [
                        *scope_prefix(),
                        "systemctl",
                        "--user",
                        "set-property",
                        "--runtime",
                        SLICE,
                        f"CPUWeight={SLICE_CPU_WEIGHT}",
                        f"IOWeight={SLICE_IO_WEIGHT}",
                        f"MemoryHigh={SLICE_MEMORY_HIGH}",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                _systemd_ready = completed.returncode == 0
            except (OSError, subprocess.SubprocessError):
                _systemd_ready = False
        return _systemd_ready


def scope_prefix() -> list[str]:
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--slice={SLICE}", "--"]


def turn_environment(
    data_dir: Path,
    gof: Path | None,
    *,
    thread_id: str,
    label: str,
) -> dict[str, str]:
    """Environment for one provider turn, or nothing when queuing is off."""
    if os.name == "nt" or gof is None:
        return {}
    root = slots_root(data_dir)
    if not read_settings(root).enabled:
        return {}
    search_path = os.environ.get(PATH_ENV, "")
    try:
        bin_dir = install(root, gof, search_path)
    except OSError:
        return {}
    env = {
        ROOT_ENV: str(root),
        BIN_ENV: str(bin_dir),
        PATH_ENV: path_with_wrappers_first({BIN_ENV: str(bin_dir)}, search_path),
        LABEL_ENV: " ".join(label.split())[:120] or "Rem thread",
        ACTIVITY_ENV: str(activity_path(root, thread_id or label)),
    }
    if systemd_slice_ready():
        env[SLICE_ENV] = SLICE
    return env


def path_with_wrappers_first(env: dict[str, str], current_path: str) -> str:
    """Keep the wrapper directory ahead of folders added for a provider binary."""
    bin_dir = env.get(BIN_ENV)
    entries = [entry for entry in current_path.split(os.pathsep) if entry]
    if not bin_dir:
        return current_path
    return os.pathsep.join([bin_dir, *(entry for entry in entries if entry != bin_dir)])


def scoped_command(command: list[str], env: dict[str, str] | None) -> list[str]:
    """Start a provider inside Raticode's slice when its turn environment asks for it."""
    if not command or not env or env.get(SLICE_ENV) != SLICE or command[0] == "systemd-run":
        return command
    return [*scope_prefix(), *command]


# Acquiring a slot ------------------------------------------------------------


def _slot_path(root: Path, kind: str, index: int) -> Path:
    return root / "slots" / f"{kind}-{index}.lock"


def _open_lock(path: Path) -> tuple[int, bool] | None:
    """Open a lock file, writable when the sandbox allows it."""
    try:
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o600), True
    except OSError:
        pass
    try:
        return os.open(path, os.O_RDONLY), False
    except OSError:
        return None


def _try_lock(fd: int, *, shared: bool = False) -> bool:
    try:
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _read_holder(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def holders(root: Path, kind: str, capacity: int) -> list[dict[str, Any]]:
    """Commands holding this class's slots. Unreadable slots count as free."""
    result: list[dict[str, Any]] = []
    for index in range(capacity):
        path = _slot_path(root, kind, index)
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            if _try_lock(fd, shared=True):
                fcntl.flock(fd, fcntl.LOCK_UN)
                continue
        finally:
            os.close(fd)
        result.append({"slot": index, **_read_holder(path)})
    return result


def _any_held(root: Path) -> bool:
    return any(holders(root, kind, MAX_SLOTS) for kind in CLASSES)


@dataclass
class Lease:
    kind: str
    slot: int
    fd: int

    def release(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self.fd)


class _QueueTicket:
    """A waiter's place in line, held with ``flock`` so stale entries are visible."""

    def __init__(self, root: Path, kind: str, record: dict[str, Any]) -> None:
        self.path: Path | None = None
        self.fd: int | None = None
        folder = root / "queue" / kind
        name = f"{time.time_ns():020d}-{os.getpid()}.wait"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            fd = os.open(folder / name, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            # Read-only sandboxes wait without a place in line.
            return
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, json.dumps(record).encode("utf-8"))
        self.path, self.fd = folder / name, fd

    def ahead(self) -> list[dict[str, Any]]:
        """Live waiters that arrived first. Entries of exited waiters are removed."""
        if self.path is None:
            return []
        waiting = []
        for entry in sorted(self.path.parent.glob("*.wait")):
            if entry.name >= self.path.name:
                break
            try:
                fd = os.open(entry, os.O_RDONLY)
            except OSError:
                continue
            try:
                if _try_lock(fd, shared=True):
                    with contextlib.suppress(OSError):
                        entry.unlink()
                    continue
            finally:
                os.close(fd)
            waiting.append(_read_holder(entry))
        return waiting

    def close(self) -> None:
        if self.path is not None:
            with contextlib.suppress(OSError):
                self.path.unlink()
        if self.fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.fd)
        self.path = self.fd = None


def _memory_blocked() -> str | None:
    pressure = memory_pressure()
    if pressure is not None and pressure >= MEMORY_PRESSURE_LIMIT:
        return f"memory is under pressure ({pressure:.0f}% stalled)"
    available = memory_available_bytes()
    if available is not None and available < MEMORY_AVAILABLE_FLOOR:
        return f"only {available / 1024**3:.1f} GB of memory is free"
    return None


def _describe(entries: list[dict[str, Any]]) -> str:
    parts = []
    for entry in entries[:3]:
        command = str(entry.get("command") or "a command")
        label = str(entry.get("label") or "")
        parts.append(f"{command} ({label})" if label else command)
    if len(entries) > 3:
        parts.append(f"{len(entries) - 3} more")
    return ", ".join(parts)


def acquire(
    root: Path,
    kind: str,
    *,
    command: str,
    label: str,
    notify: Callable[[str], None],
    limit_seconds: float = WAIT_LIMIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Lease | None:
    """Wait for a free slot of ``kind``. Returns None if queuing turned off or the wait ran out."""
    record = {"pid": os.getpid(), "command": command, "label": label, "since": time.time()}
    with contextlib.suppress(OSError):
        (root / "slots").mkdir(parents=True, exist_ok=True)
    ticket = _QueueTicket(root, kind, record)
    started = clock()
    notified = 0.0
    try:
        while True:
            settings = read_settings(root)
            if not settings.enabled:
                return None
            capacity = settings.capacity(kind)
            ahead = ticket.ahead()
            busy = holders(root, kind, capacity)
            reason = None
            if len(ahead) < capacity - len(busy):
                memory = _memory_blocked()
                if memory and _any_held(root):
                    reason = memory
                else:
                    usable = False
                    for index in range(capacity):
                        opened = _open_lock(_slot_path(root, kind, index))
                        if opened is None:
                            continue
                        usable = True
                        fd, writable = opened
                        if not _try_lock(fd):
                            os.close(fd)
                            continue
                        if writable:
                            with contextlib.suppress(OSError):
                                os.ftruncate(fd, 0)
                                os.pwrite(fd, json.dumps(record).encode("utf-8"), 0)
                        if notified:
                            notify(
                                f"Raticode: starting {command} after {_waited(clock() - started)}."
                            )
                        return Lease(kind, index, fd)
                    if not usable:
                        # Waiting can't help when no lock file can be opened at all.
                        return None
            waited = clock() - started
            if waited >= limit_seconds:
                notify(
                    f"Raticode: {command} waited {_waited(waited)} for a free "
                    f"{CLASS_LABELS[kind]} slot and is starting anyway."
                )
                return None
            if not notified or clock() - notified >= WAIT_NOTICE_SECONDS:
                if reason is None:
                    queue = busy + ahead
                    reason = f"all {capacity} {CLASS_LABELS[kind]} slots are in use" + (
                        f": {_describe(queue)}" if queue else ""
                    )
                prefix = "is still waiting" if notified else "is waiting"
                notify(
                    f"Raticode: {command} {prefix} because {reason}. It starts by itself "
                    "when a slot frees up; don't cancel or rerun it."
                )
                notified = clock()
            sleep(POLL_SECONDS)
    finally:
        ticket.close()


def _waited(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes} min" if minutes else f"{int(seconds)} s"


@contextlib.contextmanager
def heartbeat(path: str | None) -> Iterator[None]:
    """Touch the turn's activity file so Rem doesn't stop a turn that is waiting or testing."""
    if not path:
        yield
        return
    stop = threading.Event()

    def touch() -> None:
        with contextlib.suppress(OSError):
            with open(path, "a", encoding="utf-8"):
                pass
            os.utime(path)

    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            touch()

    # Report activity before the command starts, so even a command that ends
    # before the background thread first runs is seen as recent activity.
    touch()
    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def _short_command(argv: list[str]) -> str:
    if not argv:
        return "command"
    words = [Path(argv[0]).name, *argv[1:]]
    text = " ".join(shlex.quote(word) for word in words)
    return text if len(text) <= 80 else text[:77] + "..."


def run(kind: str, argv: list[str]) -> int:
    """Run ``argv`` while holding a slot of ``kind``; return its exit status."""
    if kind not in CLASSES or not argv:
        print("usage: gof slot run --class test|build|browser -- COMMAND...", file=sys.stderr)
        return 2
    root_value = os.environ.get(ROOT_ENV)
    if os.environ.get(HELD_ENV) or not root_value:
        os.execvp(argv[0], argv)
    root = Path(root_value)

    def notify(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    with heartbeat(os.environ.get(ACTIVITY_ENV)):
        lease = acquire(
            root,
            kind,
            command=_short_command(argv),
            label=os.environ.get(LABEL_ENV, ""),
            notify=notify,
        )
        env = {**os.environ, HELD_ENV: kind}
        if os.environ.get(SLICE_ENV) != SLICE:
            # Without the systemd slice, a lower priority keeps the desktop responsive.
            with contextlib.suppress(OSError):
                os.nice(5)
        try:
            return _run_child(argv, env)
        finally:
            if lease is not None:
                lease.release()


def _run_child(argv: list[str], env: dict[str, str]) -> int:
    try:
        child = subprocess.Popen(argv, env=env)
    except OSError as exc:
        print(f"{argv[0]}: {exc.strerror or exc}", file=sys.stderr)
        return 127 if isinstance(exc, FileNotFoundError) else 126

    def forward(signum: int, _frame: Any) -> None:
        with contextlib.suppress(OSError):
            child.send_signal(signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)
    code = child.wait()
    return 128 - code if code < 0 else code


# Claude Code hook --------------------------------------------------------------

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n", ";;", "|&"}
_PREFIX_COMMANDS = {"env", "time", "nice", "timeout", "xvfb-run", "command", "exec", "nohup"}
_PREFIX_VALUE_OPTIONS = {"-n", "-s", "-k", "--signal", "--kill-after", "-u", "--server-args"}
_RUNNERS = {"uv", "poetry", "pipenv", "pdm", "hatch", "rye"}
_RUNNER_VALUE_OPTIONS = {
    "--with",
    "--python",
    "-p",
    "--project",
    "--directory",
    "--group",
    "--extra",
    "--env",
    "-e",
}


def _tool_name(word: str) -> str:
    name = Path(word).name
    return "python" if re.fullmatch(r"python3(\.\d+)?", name) else name


def _commands(text: str) -> list[list[str]]:
    commands: list[list[str]] = [[]]
    for line in text.splitlines():
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            continue
        commands.append([])
        for token in tokens:
            if token in _SEPARATORS or set(token) <= set(";&|()"):
                commands.append([])
            else:
                commands[-1].append(token)
    return [command for command in commands if command]


def _strip_prefixes(words: list[str]) -> list[str]:
    while words:
        if _ASSIGNMENT.match(words[0]):
            words = words[1:]
            continue
        if words[0] in _PREFIX_COMMANDS:
            words = words[1:]
            while words and (words[0].startswith("-") or _ASSIGNMENT.match(words[0])):
                option = words[0]
                words = words[1:]
                if option in _PREFIX_VALUE_OPTIONS and words:
                    words = words[1:]
            # timeout's duration and nice's adjustment are plain words.
            if words and re.fullmatch(r"\d+(\.\d+)?[smhd]?", words[0]):
                words = words[1:]
            continue
        return words
    return words


def classify_shell(text: str, depth: int = 0) -> tuple[str | None, bool]:
    """Slot class for a shell command line and whether PATH wrappers would miss it.

    Wrappers catch ``npm test`` but not ``.venv/bin/pytest``, ``uv run pytest``
    or ``node node_modules/.bin/vitest``. Only those need the hook to rewrite them.
    """
    best: str | None = None
    hidden = False

    def note(kind: str | None, indirect: bool) -> None:
        nonlocal best, hidden
        if kind is None:
            return
        if best is None or CLASS_RANK[kind] > CLASS_RANK[best]:
            best = kind
        hidden = hidden or indirect

    # Activating a virtualenv or changing PATH puts other folders ahead of the
    # wrappers for the rest of the command line.
    path_changed = False
    for words in _commands(text):
        if words[0] in {"source", "."} or (
            words[0] == "export" and any(word.startswith("PATH=") for word in words[1:])
        ):
            path_changed = True
            continue
        indirect = path_changed or any(word.startswith("PATH=") for word in words[:3])
        words = _strip_prefixes(words)
        if not words:
            continue
        tool = _tool_name(words[0])
        if tool in {"bash", "sh", "zsh"} and len(words) >= 3 and words[1] == "-c" and depth < 3:
            note(*classify_shell(words[2], depth + 1))
            continue
        if tool in _RUNNERS and len(words) >= 2 and words[1] == "run":
            rest = words[2:]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in _RUNNER_VALUE_OPTIONS else rest[1:]
            words, indirect = rest, True
            if not words:
                continue
            tool = _tool_name(words[0])
        elif tool == "node" and len(words) >= 2 and "/" in words[1]:
            words, indirect = words[1:], True
            tool = _tool_name(words[0])
        if "/" in words[0]:
            indirect = True
        note(classify(tool, words[1:]), indirect or tool in ALIASES)
    return best, hidden


def claude_hook(payload: dict[str, Any], gof: str) -> dict[str, Any] | None:
    """PreToolUse response that queues hidden gated commands and lengthens their timeout."""
    if payload.get("tool_name") != "Bash" or os.environ.get(HELD_ENV):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return None
    command = tool_input["command"]
    if command.startswith(shlex.quote(gof) + " slot ") or command.startswith(gof + " slot "):
        return None
    kind, hidden = classify_shell(command)
    if kind is None:
        return None
    updated = dict(tool_input)
    if hidden:
        updated["command"] = (
            f"{shlex.quote(gof)} slot run --class {kind} -- bash -c {shlex.quote(command)}"
        )
    timeout = tool_input.get("timeout")
    if not isinstance(timeout, (int, float)) or timeout < CLAUDE_GATED_TIMEOUT_MS:
        updated["timeout"] = CLAUDE_GATED_TIMEOUT_MS
    if updated == tool_input:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}


# Command line ------------------------------------------------------------------


def status(root: Path) -> dict[str, Any]:
    settings = read_settings(root)
    limits = settings.resolved()
    return {
        "enabled": settings.enabled,
        "limits": limits,
        "slots": {kind: holders(root, kind, limits[kind]) for kind in CLASSES},
    }


def main(argv: list[str]) -> int:
    """``gof slot`` entry point; kept free of the Typer CLI so it starts quickly."""
    if argv[:1] == ["run"]:
        rest = argv[1:]
        kind = ""
        if rest[:1] == ["--class"] and len(rest) >= 2:
            kind, rest = rest[1], rest[2:]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        return run(kind, rest)
    if argv == ["hook", "claude"]:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            return 0
        response = claude_hook(payload, sys.argv[0]) if isinstance(payload, dict) else None
        if response is not None:
            print(json.dumps(response))
        return 0
    if argv == ["status"]:
        root = os.environ.get(ROOT_ENV)
        if not root:
            print(f"{ROOT_ENV} is not set; run this from a Rem turn.", file=sys.stderr)
            return 1
        print(json.dumps(status(Path(root)), indent=2))
        return 0
    print(
        "usage: gof slot run --class test|build|browser -- COMMAND...\n"
        "       gof slot status\n"
        "       gof slot hook claude",
        file=sys.stderr,
    )
    return 2
