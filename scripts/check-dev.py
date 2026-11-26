#!/usr/bin/env python3
"""Run the quick repository-wide checks before a branch lands on dev.

Targeted tests for a change can pass while a check that reads the whole repository
fails: the CLI parity manifest, the website address, no credential on the command
line, the phone protocol. This script runs those checks together and prints one
PASS or FAIL line for each, then the output of any check that failed:

- ``ruff check src tests``
- ``mypy src tests``
- the repository-wide test files in ``REPO_WIDE_TESTS``
- ``scripts/check-website-docs.py``
- ``scripts/check-commit-messages.py``: no commit message credits an AI tool
- ``scripts/check-dead-code.py``: no function, class, constant, export, frontend module,
  dependency or CSS class without users
- ``mobile/android/scripts/check-contract.py``: the phone protocol's schemas and fixtures
- ``scripts/check-protocol-compat.py``: messages from the last release still fit the
  protocol. A clone without v* tags prints SKIP and the reason instead.

It doesn't replace the full suite, which is still the rule for changes to shared
workflow execution, scheduling or CLI behavior. ``tests/unit/test_check_dev.py``
fails when a test file that reads ``git ls-files`` or ``docs/cli-parity.json``, or a
Python command or test file in ``.github/workflows/contract-check.yml``, is missing
here, so the lists live here and nowhere else. The phone's Gradle interop tests in
that workflow stay CI-only.

The tools run with the first Python found among ``--python``, this checkout's
``.venv``, the main checkout's ``.venv`` (when run from a git worktree) and the
Python running this script. The script itself needs only the standard library.

Usage:
    python3 scripts/check-dev.py                  # website docs compared with dev
    python3 scripts/check-dev.py --base main

Exit codes: 0 when every check passes, 1 when any check fails.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Test files (or single tests) that check the whole repository rather than one area.
REPO_WIDE_TESTS = (
    "tests/unit/test_cli_parity.py",
    "tests/unit/test_cli_scripting_contract.py",
    "tests/unit/test_cli_agent_safety.py",
    "tests/unit/test_cli_api_commands.py",
    "tests/unit/test_website_address.py",
    "tests/unit/test_website_mobile_download.py",
    "tests/unit/test_device_protocol.py",
    "tests/unit/test_mobile_contract.py",
    "tests/unit/test_protocol_released_compat.py",
    "tests/unit/test_device_desktop_control.py",
    "tests/unit/test_check_dead_code.py",
    "tests/unit/test_cli_client.py::test_no_command_accepts_a_credential_on_the_command_line",
)

PYTEST_WORKERS = "4"
CHECK_TIMEOUT_SECONDS = 15 * 60
FAILED_OUTPUT_LINES = 80
# A passing check whose output has a line starting with this didn't check anything.
SKIPPED = "Skipped: "


@dataclass(frozen=True)
class Check:
    name: str
    command: list[str]


@dataclass(frozen=True)
class Result:
    check: Check
    passed: bool
    seconds: float
    output: str

    @property
    def skip_reason(self) -> str | None:
        if not self.passed:
            return None
        lines = self.output.splitlines()
        return next((line for line in lines if line.startswith(SKIPPED)), None)


def _venv_python(folder: Path) -> Path | None:
    for candidate in (folder / ".venv/bin/python", folder / ".venv/Scripts/python.exe"):
        if candidate.is_file():
            return candidate
    return None


def _main_checkout(root: Path) -> Path | None:
    """The main checkout when ``root`` is a linked git worktree, else None."""
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    main = Path(common).parent
    return None if main.resolve() == root.resolve() else main


def find_python(root: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    for folder in (root, _main_checkout(root)):
        if folder is not None and (found := _venv_python(folder)) is not None:
            return str(found)
    return sys.executable


def _has_module(python: str, module: str) -> bool:
    try:
        probe = subprocess.run([python, "-c", f"import {module}"], capture_output=True, check=False)
    except OSError:
        return False  # run_check reports the missing interpreter on each line.
    return probe.returncode == 0


def build_checks(python: str, base: str, *, parallel_tests: bool) -> list[Check]:
    workers = ["-n", PYTEST_WORKERS, "--dist", "worksteal"] if parallel_tests else []
    return [
        Check("ruff check src tests", [python, "-m", "ruff", "check", "src", "tests"]),
        Check("mypy src tests", [python, "-m", "mypy", "src", "tests"]),
        Check(
            f"repo-wide tests ({len(REPO_WIDE_TESTS)} entries)",
            [
                python,
                "-m",
                "pytest",
                *workers,
                "-q",
                "-p",
                "no:cacheprovider",
                "--no-header",
                "-rf",
                *REPO_WIDE_TESTS,
            ],
        ),
        Check(
            "website docs check",
            [python, "scripts/check-website-docs.py", "--base", base],
        ),
        Check(
            "commit messages",
            [python, "scripts/check-commit-messages.py", "--base", base],
        ),
        Check("dead code", [python, "scripts/check-dead-code.py"]),
        Check("phone contract", [python, "mobile/android/scripts/check-contract.py"]),
        Check(
            "protocol compat with last release",
            [python, "scripts/check-protocol-compat.py", "--skip-without-tags"],
        ),
    ]


def check_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("FORCE_COLOR", None)
    env["NO_COLOR"] = "1"
    source = str(ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (source, env.get("PYTHONPATH")) if p)
    return env


def run_check(check: Check, root: Path, env: dict[str, str]) -> Result:
    started = time.monotonic()
    try:
        done = subprocess.run(
            check.command,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=CHECK_TIMEOUT_SECONDS,
            check=False,
        )
        passed, output = done.returncode == 0, done.stdout
    except subprocess.TimeoutExpired as error:
        captured = error.stdout or ""
        if isinstance(captured, bytes):
            captured = captured.decode(errors="replace")
        passed = False
        output = f"{captured}\nStopped after {CHECK_TIMEOUT_SECONDS} seconds."
    except OSError as error:
        passed, output = False, f"Couldn't start {check.command[0]}: {error}"
    return Result(check, passed, time.monotonic() - started, output)


def summary_line(result: Result) -> str:
    status = "SKIP" if result.skip_reason else "PASS" if result.passed else "FAIL"
    return f"{status}  {result.check.name:<34} {result.seconds:6.1f} s"


def run_all(
    checks: Sequence[Check],
    runner: Callable[[Check], Result],
    write: Callable[[str], None],
) -> int:
    """Run the checks side by side and print their lines in list order."""
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = [pool.submit(runner, check) for check in checks]
        results = []
        for future in futures:
            result = future.result()
            results.append(result)
            write(summary_line(result))
    for result in results:
        if result.skip_reason:
            write(f"{result.check.name}: {result.skip_reason}")
    failed = [result for result in results if not result.passed]
    for result in failed:
        lines = result.output.rstrip().splitlines()
        shown = lines[-FAILED_OUTPUT_LINES:]
        write("")
        write(f"--- {result.check.name}: {' '.join(result.check.command)}")
        if len(lines) > len(shown):
            write(f"(last {len(shown)} of {len(lines)} lines)")
        for line in shown:
            write(line)
    write("")
    write(f"check-dev: {len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--base", default="dev", help="branch the website docs check compares against"
    )
    parser.add_argument("--python", help="Python with ruff, mypy and pytest installed")
    args = parser.parse_args(argv)

    python = find_python(ROOT, args.python)
    checks = build_checks(python, args.base, parallel_tests=_has_module(python, "xdist"))
    env = check_env()
    print(f"Checking {ROOT} with {python}", flush=True)
    return run_all(
        checks,
        lambda check: run_check(check, ROOT, env),
        lambda line: print(line, flush=True),
    )


if __name__ == "__main__":
    sys.exit(main())
