from __future__ import annotations

import ast
import importlib.util
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
HELPERS = TESTS / "helpers"
CONTRACT_WORKFLOW = ROOT / ".github/workflows/contract-check.yml"

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


def _contract_workflow_python() -> tuple[set[str], set[str]]:
    """Scripts run with python and test files run with pytest by the contract workflow.

    Paths are relative to the repository root, like check-dev's commands.
    """
    scripts: set[str] = set()
    tests: set[str] = set()
    workflow = yaml.safe_load(CONTRACT_WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            folder = step.get("working-directory", ".")
            for line in str(step.get("run", "")).splitlines():
                words = shlex.split(line)
                for runner, found in (("python", scripts), ("pytest", tests)):
                    if runner in words:
                        after = words[words.index(runner) + 1 :]
                        found.update(
                            (Path(folder) / word).as_posix().removeprefix("./")
                            for word in after
                            if word.endswith(".py")
                        )
    return scripts, tests


def test_check_dev_runs_the_python_checks_of_the_contract_workflow(check_dev: Any) -> None:
    scripts, tests = _contract_workflow_python()
    checks = check_dev.build_checks("py", "dev", parallel_tests=False)
    run_scripts = {word for check in checks for word in check.command if word.endswith(".py")}
    missing = sorted((scripts - run_scripts) | (tests - _listed_files(check_dev)))
    assert not missing, (
        f"{CONTRACT_WORKFLOW.relative_to(ROOT)} runs these, so scripts/check-dev.py must run "
        f"them too (scripts in build_checks, test files in REPO_WIDE_TESTS): {missing}"
    )


def test_the_contract_workflow_scan_finds_its_python_steps() -> None:
    # Guards the guard: if the parsing stops matching, the test above passes vacuously.
    scripts, tests = _contract_workflow_python()
    assert {
        "mobile/android/scripts/check-contract.py",
        "scripts/check-protocol-compat.py",
    } <= scripts
    assert {"tests/unit/test_device_protocol.py", "tests/unit/test_mobile_contract.py"} <= tests


def test_protocol_compat_skips_with_a_reason_when_no_tag_exists(tmp_path: Path) -> None:
    script = ROOT / "scripts/check-protocol-compat.py"
    copy = tmp_path / "scripts/check-protocol-compat.py"
    copy.parent.mkdir()
    copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(copy), *args], capture_output=True, text=True, check=False
        )

    skipped = run("--skip-without-tags")
    assert skipped.returncode == 0, skipped.stderr
    assert skipped.stdout.startswith("Skipped: No v* tags found")
    strict = run()
    assert strict.returncode == 1
    assert "No v* tags found" in strict.stderr
    assert "Traceback" not in strict.stderr + skipped.stderr


def test_checks_cover_lint_types_tests_and_website_docs(check_dev: Any) -> None:
    checks = check_dev.build_checks("py", "main", parallel_tests=True)
    commands = [check.command for check in checks]
    assert ["py", "-m", "ruff", "check", "src", "tests"] in commands
    assert ["py", "-m", "mypy", "src", "tests"] in commands
    assert ["py", "scripts/check-website-docs.py", "--base", "main"] in commands
    assert ["py", "scripts/check-commit-messages.py", "--base", "main"] in commands
    assert ["py", "scripts/check-dead-code.py"] in commands
    assert ["py", "mobile/android/scripts/check-contract.py"] in commands
    assert ["py", "scripts/check-protocol-compat.py", "--skip-without-tags"] in commands
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


def test_a_skipped_check_says_skip_and_why(check_dev: Any) -> None:
    check = check_dev.Check("compat", ["c"])
    reason = "Skipped: No v* tags found."
    result = check_dev.Result(check, True, 0.5, f"{reason}\n")
    lines: list[str] = []
    assert check_dev.run_all([check], lambda _: result, lines.append) == 0
    assert lines[0].split()[:2] == ["SKIP", "compat"]
    assert f"compat: {reason}" in lines
    failing = check_dev.Result(check, False, 0.5, reason)
    assert check_dev.summary_line(failing).startswith("FAIL")


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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _branch_changing(tmp_path: Path, changed: str) -> Path:
    """A repository whose branch adds ``changed`` on top of a branch named base."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("start\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "start")
    _git(repo, "branch", "base")
    _git(repo, "checkout", "-q", "-b", "work")
    path = repo / changed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n")
    _git(repo, "add", changed)
    _git(repo, "commit", "-q", "-m", "change")
    return repo


def test_frontend_lint_runs_npm_run_lint_in_frontend_when_a_frontend_file_changed(
    check_dev: Any, tmp_path: Path
) -> None:
    repo = _branch_changing(tmp_path, "frontend/src/App.jsx")
    (repo / "frontend/node_modules").mkdir()
    check = check_dev.frontend_lint_check(repo, "base")
    assert check.command == ["npm", "run", "lint"]
    assert check.folder == "frontend"
    assert check.outcome is None

    # A stand-in npm records where it ran and with which arguments.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text('#!/bin/sh\necho "ran $* in $(pwd)"\n')
    npm.chmod(0o755)
    result = check_dev.run_check(check, repo, {"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert result.passed, result.output
    assert result.output.strip() == f"ran run lint in {repo / 'frontend'}"
    lines: list[str] = []
    assert check_dev.run_all([check], lambda _: result, lines.append) == 0
    assert lines[0].split()[:3] == ["PASS", "frontend", "lint"]


def test_frontend_lint_skips_with_a_reason_when_no_frontend_file_changed(
    check_dev: Any, tmp_path: Path
) -> None:
    repo = _branch_changing(tmp_path, "src/gofer/module.py")
    check = check_dev.frontend_lint_check(repo, "base")
    result = check_dev.run_check(check, repo, {})
    assert result.passed
    lines: list[str] = []
    assert check_dev.run_all([check], lambda _: result, lines.append) == 0
    assert lines[0].split()[:3] == ["SKIP", "frontend", "lint"]
    assert "frontend lint: Skipped: no frontend files changed since base." in lines


def test_frontend_lint_fails_and_names_npm_ci_when_node_modules_is_missing(
    check_dev: Any, tmp_path: Path
) -> None:
    repo = _branch_changing(tmp_path, "frontend/src/App.jsx")
    check = check_dev.frontend_lint_check(repo, "base")
    result = check_dev.run_check(check, repo, {})
    assert not result.passed
    assert "run npm ci in frontend first" in result.output
    lines: list[str] = []
    assert check_dev.run_all([check], lambda _: result, lines.append) == 1
    assert lines[0].split()[:3] == ["FAIL", "frontend", "lint"]


def test_frontend_lint_fails_when_the_base_branch_is_unknown(
    check_dev: Any, tmp_path: Path
) -> None:
    repo = _branch_changing(tmp_path, "frontend/src/App.jsx")
    result = check_dev.run_check(check_dev.frontend_lint_check(repo, "no-such-base"), repo, {})
    assert not result.passed
    assert "Couldn't list the files changed since no-such-base" in result.output


def test_the_check_dev_test_itself_is_in_the_repo_wide_list(check_dev: Any) -> None:
    # It scans the whole tests/ tree, so drift it finds must fail the landing check.
    assert "tests/unit/test_check_dev.py" in check_dev.REPO_WIDE_TESTS


def _repo_with(tmp_path: Path, files: dict[str, str], changed: Sequence[str]) -> Path:
    """A repository holding ``files`` whose branch changes ``changed`` on top of base."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "start")
    _git(repo, "branch", "base")
    _git(repo, "checkout", "-q", "-b", "work")
    for name in changed:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{path.read_text() if path.exists() else ''}# changed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return repo


def _selected(check: Any) -> list[str]:
    return [word for word in check.command if word.startswith("tests/")]


def test_module_names_follow_the_source_path(check_dev: Any) -> None:
    name = check_dev.module_name
    assert name("src/gofer/cli/commands/rattish_shortcuts.py") == (
        "gofer.cli.commands.rattish_shortcuts"
    )
    assert name("src/gofer/cli/__init__.py") == "gofer.cli"
    assert name("src/gofer/cli/data.json") is None
    assert name("scripts/check-dev.py") is None
    assert name("tests/unit/test_cli_output.py") is None


def test_changing_rattish_shortcuts_selects_test_cli_output(check_dev: Any, tmp_path: Path) -> None:
    changed = "src/gofer/cli/commands/rattish_shortcuts.py"
    output_test = "tests/unit/test_cli_output.py"
    repo = _repo_with(
        tmp_path,
        {
            changed: "",
            output_test: (ROOT / output_test).read_text(encoding="utf-8"),
            "tests/unit/test_other.py": "from gofer.cli.commands import rattish_secrets\n",
        },
        [changed],
    )
    check = check_dev.changed_module_tests_check(repo, "base", "py", parallel_tests=True)
    assert check.name == "tests of changed modules"
    assert check.outcome is None
    assert _selected(check) == [output_test]
    # Same pytest flags and workers as the repo-wide check, only the files differ.
    repo_wide = check_dev.build_checks("py", "base", parallel_tests=True)[2].command
    assert check.command[: -len(_selected(check))] == repo_wide[: -len(check_dev.REPO_WIDE_TESTS)]
    # And the real tests/ tree has that import too.
    assert output_test in check_dev.tests_importing(ROOT, {"gofer.cli.commands.rattish_shortcuts"})


def test_selects_each_import_form_and_nothing_else(check_dev: Any, tmp_path: Path) -> None:
    tests = {
        "tests/unit/test_import.py": "import gofer.app.store as store\n",
        "tests/unit/test_from_module.py": "from gofer.app.store import Store\n",
        "tests/unit/test_from_package.py": "from gofer.app import (\n    other,\n    store,\n)\n",
        "tests/unit/test_similar_names.py": (
            "import gofer.app.stores\nfrom gofer.app import store_two\n"
            "from gofer.app.store_two import x\n"
        ),
        "tests/unit/test_mentions_only.py": '"""gofer.app.store is named here."""\nstore = 1\n',
        "tests/unit/test_relative.py": "from .app import store\n",
        "tests/unit/helper_store.py": "from gofer.app import store\n",
        # Already run by the repo-wide check.
        "tests/unit/test_cli_parity.py": "from gofer.app import store\n",
    }
    repo = _repo_with(tmp_path, {"src/gofer/app/store.py": "", **tests}, ["src/gofer/app/store.py"])
    check = check_dev.changed_module_tests_check(repo, "base", "py", parallel_tests=False)
    assert _selected(check) == [
        "tests/unit/test_from_module.py",
        "tests/unit/test_from_package.py",
        "tests/unit/test_import.py",
    ]
    assert "-n" not in check.command


def test_runs_the_selected_tests_and_fails_when_one_fails(check_dev: Any, tmp_path: Path) -> None:
    repo = _repo_with(
        tmp_path,
        {
            "src/gofer/__init__.py": "",
            "src/gofer/sample.py": "VALUE = 1\n",
            "tests/test_sample.py": "from gofer import sample\n\n"
            "def test_value():\n    assert sample.VALUE == 2\n",
        },
        ["src/gofer/sample.py"],
    )
    check = check_dev.changed_module_tests_check(repo, "base", sys.executable, parallel_tests=False)
    env = {**os.environ, "PYTHONPATH": str(repo / "src")}
    env.pop("PYTEST_ADDOPTS", None)
    result = check_dev.run_check(check, repo, env)
    assert not result.passed
    assert "FAILED tests/test_sample.py::test_value" in result.output
    lines: list[str] = []
    assert check_dev.run_all([check], lambda _: result, lines.append) == 1
    assert lines[0].startswith("FAIL  tests of changed modules")

    (repo / "src/gofer/sample.py").write_text("VALUE = 2\n")
    assert check_dev.run_check(check, repo, env).passed


def test_more_than_150_selected_files_skip_with_a_pointer_to_the_full_suite(
    check_dev: Any, tmp_path: Path
) -> None:
    count = check_dev.MAX_CHANGED_MODULE_TESTS + 1
    tests = {f"tests/unit/test_{n}.py": "from gofer.app import store\n" for n in range(count)}
    repo = _repo_with(tmp_path, {"src/gofer/app/store.py": "", **tests}, ["src/gofer/app/store.py"])
    check = check_dev.changed_module_tests_check(repo, "base", "py", parallel_tests=True)
    result = check_dev.run_check(check, repo, {})
    assert result.passed
    assert result.output.startswith("Skipped: ")
    assert f"{count} test files" in result.output
    assert "AGENTS.md" in result.output and "full suite" in result.output
    lines: list[str] = []
    assert check_dev.run_all([check], lambda _: result, lines.append) == 0
    assert lines[0].startswith("SKIP  tests of changed modules")
    assert any(line.startswith("tests of changed modules: Skipped: ") for line in lines)
    # The guard reads the cap, so the cap is what the ticket asked for.
    assert check_dev.MAX_CHANGED_MODULE_TESTS == 150


def test_changed_module_tests_skip_when_no_source_module_changed(
    check_dev: Any, tmp_path: Path
) -> None:
    repo = _branch_changing(tmp_path, "frontend/src/App.jsx")
    result = check_dev.run_check(
        check_dev.changed_module_tests_check(repo, "base", "py", parallel_tests=False), repo, {}
    )
    assert result.passed
    assert result.skip_reason == "Skipped: no src/gofer Python files changed since base."


def test_changed_module_tests_skip_when_no_test_imports_the_module(
    check_dev: Any, tmp_path: Path
) -> None:
    repo = _branch_changing(tmp_path, "src/gofer/lonely.py")
    result = check_dev.run_check(
        check_dev.changed_module_tests_check(repo, "base", "py", parallel_tests=False), repo, {}
    )
    assert result.passed
    assert result.skip_reason is not None
    assert "no test file" in result.skip_reason


def test_changed_module_tests_fail_when_the_base_branch_is_unknown(
    check_dev: Any, tmp_path: Path
) -> None:
    repo = _branch_changing(tmp_path, "src/gofer/module.py")
    check = check_dev.changed_module_tests_check(repo, "no-such-base", "py", parallel_tests=False)
    result = check_dev.run_check(check, repo, {})
    assert not result.passed
    assert "Couldn't list the files changed since no-such-base" in result.output
