from __future__ import annotations

import ast
import importlib.util
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
HELPERS = TESTS / "helpers"

# Signs that a test reads the whole repository: the tracked file list (git ls-files,
# or git grep, which searches the same files) or the CLI parity manifest.
READS_WHOLE_REPO = re.compile(r"ls-files|[\"']git[\"'],\s*[\"']grep[\"']|cli-parity\.json")


@pytest.fixture(scope="module")
def check_dev() -> Iterator[Any]:
    spec = importlib.util.spec_from_file_location("check_dev", ROOT / "scripts/check-dev.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look up their module while the script loads.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(sys.modules, "check_dev", module)
    spec.loader.exec_module(module)
    yield module
    monkeypatch.undo()


def _reads_whole_repo(path: Path) -> bool:
    return bool(READS_WHOLE_REPO.search(path.read_text(encoding="utf-8")))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _repo_wide_test_files() -> set[str]:
    """Test files that read the whole repository directly or through a helper."""
    helpers = {
        "tests.helpers." + path.stem
        for path in HELPERS.glob("*.py")
        if path.stem != "__init__" and _reads_whole_repo(path)
    }
    found = set()
    for path in TESTS.rglob("test_*.py"):
        if path == Path(__file__).resolve() or HELPERS in path.parents:
            continue
        if _reads_whole_repo(path) or helpers & _imported_modules(path):
            found.add(path.relative_to(ROOT).as_posix())
    return found


def _listed_files(check_dev: Any) -> set[str]:
    return {entry.split("::")[0] for entry in check_dev.REPO_WIDE_TESTS}


def test_every_repo_wide_test_file_is_in_the_check_dev_list(check_dev: Any) -> None:
    missing = sorted(_repo_wide_test_files() - _listed_files(check_dev))
    assert not missing, (
        "These test files read git ls-files or docs/cli-parity.json, so they belong in "
        f"REPO_WIDE_TESTS in scripts/check-dev.py: {missing}"
    )


def test_the_scan_finds_the_known_repo_wide_tests() -> None:
    # Guards the guard: if the patterns stop matching, the test above passes vacuously.
    assert {
        "tests/unit/test_cli_parity.py",
        "tests/unit/test_cli_scripting_contract.py",
        "tests/unit/test_cli_agent_safety.py",
        "tests/unit/test_cli_api_commands.py",
        "tests/unit/test_website_address.py",
    } <= _repo_wide_test_files()


def test_every_listed_test_exists(check_dev: Any) -> None:
    for entry in check_dev.REPO_WIDE_TESTS:
        file, _, name = entry.partition("::")
        path = ROOT / file
        assert path.is_file(), entry
        if name:
            assert re.search(rf"^def {re.escape(name)}\(", path.read_text("utf-8"), re.M), entry


def test_the_list_keeps_the_repo_wide_checks_found_by_nightly_failures(check_dev: Any) -> None:
    assert {
        "tests/unit/test_website_mobile_download.py",
        "tests/unit/test_device_protocol.py",
        "tests/unit/test_cli_client.py::test_no_command_accepts_a_credential_on_the_command_line",
    } <= set(check_dev.REPO_WIDE_TESTS)


def test_checks_cover_lint_types_tests_and_website_docs(check_dev: Any) -> None:
    checks = check_dev.build_checks("py", "main", parallel_tests=True)
    commands = [check.command for check in checks]
    assert ["py", "-m", "ruff", "check", "src", "tests"] in commands
    assert ["py", "-m", "mypy", "src", "tests"] in commands
    assert ["py", "scripts/check-website-docs.py", "--base", "main"] in commands
    assert ["py", "scripts/check-commit-messages.py", "--base", "main"] in commands
    pytest_command = next(c for c in commands if c[1:3] == ["-m", "pytest"])
    assert pytest_command[-len(check_dev.REPO_WIDE_TESTS) :] == list(check_dev.REPO_WIDE_TESTS)
    assert "-n" in pytest_command
    serial = check_dev.build_checks("py", "dev", parallel_tests=False)
    assert all("-n" not in check.command for check in serial)


def test_prints_one_line_per_check_and_the_output_of_failures(check_dev: Any) -> None:
    checks = [
        check_dev.Check("first", ["a"]),
        check_dev.Check("second", ["b", "--flag"]),
        check_dev.Check("third", ["c"]),
    ]

    def runner(check: Any) -> Any:
        passed = check.name != "second"
        return check_dev.Result(check, passed, 1.25, "" if passed else "boom\nbad line")

    lines: list[str] = []
    assert check_dev.run_all(checks, runner, lines.append) == 1
    assert [line.split()[:2] for line in lines[:3]] == [
        ["PASS", "first"],
        ["FAIL", "second"],
        ["PASS", "third"],
    ]
    assert "--- second: b --flag" in lines
    assert "bad line" in lines
    assert lines[-1] == "check-dev: 2 passed, 1 failed"

    passing: list[str] = []
    assert check_dev.run_all(checks[:1], runner, passing.append) == 0
    assert passing[-1] == "check-dev: 1 passed, 0 failed"


def test_run_check_reports_a_missing_program(check_dev: Any, tmp_path: Path) -> None:
    check = check_dev.Check("missing", [str(tmp_path / "no-such-program")])
    result = check_dev.run_check(check, tmp_path, {})
    assert not result.passed
    assert "Couldn't start" in result.output


def test_finds_a_virtualenv_python_in_the_checkout(check_dev: Any, tmp_path: Path) -> None:
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert check_dev.find_python(tmp_path) == str(python)
    assert check_dev.find_python(tmp_path, "/opt/py") == "/opt/py"
