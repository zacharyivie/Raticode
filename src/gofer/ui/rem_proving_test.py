"""Raticode runs a task's proving test itself, before the change and after it.

A develop or maintain worker names the test that proves its change (``Proving test: X``,
``rem_home.PROVING_TEST_GUIDANCE``). Its own claim that X fails before the change and
passes after isn't the proof: Raticode runs X itself (``run``) in two fresh worktrees made
the way its project check makes one (``rem_project_checks._make_fresh``), inside the same
sandbox (no network, writes only to the worktree and a scratch folder, credentials and
the data folder hidden):

- **before**: the work's base, the landing branch's fork point
  (``ticket_patch.fork_point``), plus only the test files the task added or changed, so
  the new test is there without the change it tests;
- **after**: the task's commit.

The pair (``failed_before``, ``passes_after``) and each run's output tail are saved on the
run (``proving_test``) and shown to the reviewer as ``raticode_proving_test``
(``receipt``). X runs only when it is a pytest node ID (``path.py::name``) or a file for
``node --test``. Anything else, or a pair that can't be made (no sandbox, no git, no base,
the test's file not in the work's commit), is recorded as not run by Raticode with the
reason, and the review judges from the worker's own receipts as before.
"""

from __future__ import annotations

import re
import shlex
import threading
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from gofer.ui import rem_project_checks as checks

TIMEOUT_SECONDS = 600
# Each run's output tail; the pair together stays within a project check's.
OUTPUT_CHARS = 3000
# More changed test files than this and the base wouldn't be "the base plus a few tests".
MAX_TEST_FILES = 500
# The Python that runs pytest when no project environment is found. Tests point it at
# their own interpreter; Raticode's own executable is never one.
FALLBACK_PYTHON = "python3"
NOT_RUN = "not run by Raticode"

PROVING_LINE = re.compile(r"^[\s>*_-]*proving test[*_\s]*:[*_\s]*(.+)$", re.IGNORECASE | re.M)
NODE_FILE = re.compile(r"\.(?:[cm]?js|[cm]?ts)$")
# Words before the test's name that only say how the worker ran it.
RUNNERS = frozenset(
    {"node", "--test", "pytest", "python", "python3", "-m", "uv", "run", "npx", "npm", "test"}
)
# Test file names and folders: what the base gets from the work besides the named test.
TEST_FOLDERS = frozenset({"test", "tests", "__tests__", "testing", "androidTest", "testFixtures"})
TEST_FILE = re.compile(
    r"^(?:test_.*\.py|.*_test\.py|conftest\.py|.*\.(?:test|spec)\.[cm]?[jt]sx?)$"
)


def named_test(answer: str) -> str | None:
    """The test the worker's result names on its last ``Proving test:`` line, or None.

    A name in backticks wins; otherwise the first word. Words that only say how it ran
    (``pytest``, ``node --test``) are dropped, so ``node --test web/a.test.js`` names
    ``web/a.test.js``.
    """
    found = PROVING_LINE.findall(answer or "")
    if not found:
        return None
    rest = found[-1].strip()
    quoted = re.search(r"`([^`]+)`", rest)
    words = (quoted.group(1) if quoted else rest).split()
    while len(words) > 1 and words[0] in RUNNERS:
        words.pop(0)
    if not words:
        return None
    name = words[0].strip("\"'").rstrip(".,;:)")
    return name or None


def _safe_path(path: str) -> bool:
    """A relative path inside the folder with no option, parent step or odd character."""
    if not path or path.startswith(("-", "/")) or "\\" in path:
        return False
    if any(c.isspace() or ord(c) < 32 for c in path):
        return False
    return ".." not in PurePosixPath(path).parts


def target(name: str) -> dict[str, str] | str:
    """How Raticode runs ``name``: ``{kind, file, test}``, or why it can't."""
    if "::" in name:
        file, test = name.split("::", 1)
        if not file.endswith(".py") or not _safe_path(file):
            return f"{name} isn't a pytest node ID with a relative path to a .py file"
        if not test or any(c.isspace() or ord(c) < 32 for c in test):
            return f"{name} isn't a pytest node ID (path.py::test_name)"
        return {"kind": "pytest", "file": file, "test": name}
    if NODE_FILE.search(name) and _safe_path(name):
        return {"kind": "node", "file": name, "test": name}
    return (
        f"{name} isn't a pytest node ID (path.py::test_name) or a file node --test runs, "
        "so Raticode can't run it on its own"
    )


def is_test_path(path: str) -> bool:
    """Whether a repository path is a test file or lives in a test folder."""
    parts = PurePosixPath(path).parts
    return bool(parts) and (
        bool(TEST_FILE.match(parts[-1])) or any(p in TEST_FOLDERS for p in parts[:-1])
    )


def changed_test_files(folder: Path, base: str, commit: str) -> list[str]:
    """Test files added or changed from ``base`` to ``commit``, from the repository's top."""
    raw = checks._git(folder, "diff", "--name-status", "--no-renames", "-z", base, commit)
    fields = raw.split("\0")
    found = []
    for status, path in zip(fields[0::2], fields[1::2], strict=False):
        if status[:1] in {"A", "M", "T"} and is_test_path(path):
            found.append(path)
    return found


def _venv_python(folder: Path) -> Path | None:
    for candidate in (folder / ".venv/bin/python", folder / ".venv/Scripts/python.exe"):
        if candidate.is_file():
            return candidate
    return None


def _python(source: Path, folder: Path) -> str:
    """The project's own Python for pytest: the project folder's ``.venv``, the main
    repository's, else ``FALLBACK_PYTHON``.

    Never one the worker could have put there: not in a fresh checkout of the work's
    commit, and not in the work's own worktree (``folder``) unless that is the main
    repository itself, where ``.venv`` is the user's own environment (``run`` refuses a
    commit that tracks files in ``.venv``). Both sides of the pair use this one.
    """
    places = [source]
    main: Path | None = None
    try:
        common = checks._git(folder, "rev-parse", "--path-format=absolute", "--git-common-dir")
        main = Path(common).parent.resolve()
        places.append(main)
    except (checks.NoFreshWorktree, OSError):
        pass
    own = folder.resolve()
    for place in places:
        try:
            resolved = place.resolve()
        except OSError:
            continue
        if resolved.is_relative_to(own) and resolved != main:
            continue
        found = _venv_python(place)
        if found is not None:
            return str(found)
    return FALLBACK_PYTHON


# ``python -m pytest`` puts the working folder first on the import path, so a ``pytest``
# folder committed in the project would run in place of the real one and print whatever
# it likes. The real pytest is imported from the environment first; then the folder goes
# back on the path, as ``python -m pytest`` has it, so tests still import the project.
PYTEST_START = (
    "import sys; first = sys.path.pop(0); import pytest; sys.path.insert(0, first); "
    "sys.exit(pytest.main())"
)
# Results bigger than this aren't read: one named test writes a few hundred bytes.
JUNIT_MAX_BYTES = 2_000_000


def _junit_path(box: Path) -> Path:
    """Where pytest writes its results for one side: the side's own scratch folder."""
    return box / checks.BOX_SCRATCH / "proving-test-results.xml"


def _command(plan: dict[str, str], checkout: Path, python: str, junit: Path | None = None) -> str:
    if plan["kind"] == "pytest":
        # The results name each test from the rootdir; fixing it at the checkout makes
        # them match the planned node ID exactly (``ran_tests``). The xunit1 format adds
        # each result's real file, which a dotted class path can't tell apart: a root
        # ``tests.test_calc.py`` gives the same class path as ``tests/test_calc.py``.
        # These come after the project's ``addopts``, so they win over its settings.
        report = (
            ["--rootdir=.", "-o", "junit_family=xunit1", f"--junitxml={junit}"]
            if junit is not None
            else []
        )
        return shlex.join(
            [python, "-c", PYTEST_START, "-p", "no:cacheprovider", *report, plan["test"]]
        )
    # node --test from the file's own package folder, as its npm test would.
    file = PurePosixPath(plan["file"])
    home = PurePosixPath(".")
    for parent in file.parents:
        if (checkout / parent / "package.json").is_file():
            home = parent
            break
    run = shlex.join(["node", "--test", "--test-force-exit", str(file.relative_to(home))])
    return run if str(home) == "." else f"cd {shlex.quote(str(home))} && {run}"


def outcome(result: dict[str, Any], kind: str) -> str:
    """``passed``, ``failed``, ``not_found`` (pytest found no such test), ``cannot_run``
    (126/127) or ``stopped`` (time limit, cancel)."""
    if result.get("passed"):
        return "passed"
    code = result.get("exit_code")
    if not isinstance(code, int) or isinstance(code, bool) or code < 0:
        return "stopped"
    if code in checks.BROKEN_COMMAND:
        return "cannot_run"
    if kind == "pytest" and code in {4, 5}:
        return "not_found"
    return "failed"


def _result_name(node: str) -> tuple[str, str, str]:
    """The ``(file, classname, name)`` pytest's xunit1 results give the test at ``node``
    when the rootdir is the checkout: ``tests/a/test_b.py::TestC::test_d[1]`` is
    ``("tests/a/test_b.py", "tests.a.test_b.TestC", "test_d[1]")``."""
    file, rest = node.split("::", 1)
    while file.startswith("./"):
        file = file[2:]
    parts = rest.split("::")
    module = file.removesuffix(".py").replace("/", ".")
    return file, ".".join([module, *parts[:-1]]), parts[-1]


@dataclass(frozen=True)
class Counted:
    """What pytest's results say about the named test: how many of its runs there were,
    how many passed, and how many results were some other test."""

    ran: int
    passed: int
    others: int

    @property
    def proven(self) -> bool:
        return self.passed > 0 and self.passed == self.ran and self.others == 0


def ran_tests(results: bytes | None, node: str) -> Counted | None:
    """Pytest's results for ``node``; None when there is no readable file.

    A pass counts only from here: an exit code of 0 also comes from a run that only
    listed the tests (``addopts = --co``), or one that deselected the named test and ran
    another. A result is the named test only when its real file, class path and name all
    match; a node named without parameters matches each parametrized run
    (``test_d[1]``). Any other result in the file means the run wasn't the named test
    alone, so it proves nothing (``Counted.proven``)."""
    if not results:
        return None
    try:
        root = ElementTree.fromstring(results)
    except ElementTree.ParseError:
        return None
    file, classname, name = _result_name(node)
    ran = passed = others = 0
    for case in root.iter("testcase"):
        found = case.get("name") or ""
        where = (case.get("file") or "").replace("\\", "/")
        same_name = found == name or ("[" not in name and found.startswith(name + "["))
        if where != file or case.get("classname") != classname or not same_name:
            others += 1
            continue
        ran += 1
        if not any(child.tag in {"failure", "error", "skipped"} for child in case):
            passed += 1
    return Counted(ran, passed, others)


def not_run(test: str, reason: str, **extra: Any) -> dict[str, Any]:
    """The record for a proving test Raticode didn't run, with why."""
    return {
        "test": test,
        "ran": False,
        "status": NOT_RUN,
        "not_run_reason": reason,
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **extra,
    }


def _side(
    plan: dict[str, str],
    folder: Path,
    *,
    data_dir: Path,
    source: Path,
    commit: str,
    overlay: tuple[str, list[str]] | None,
    cancel: threading.Event | None,
    timeout: int,
    platform: str | None,
    python: str,
) -> dict[str, Any]:
    """One run of the test in a fresh worktree of ``commit``; ``skipped`` says why not.
    The caller holds ``rem_project_checks._running``."""
    box = checks._new_box(data_dir)
    try:
        try:
            checkout, made, setup = checks._make_fresh(
                checks.folder_commit(folder, commit),
                box,
                data_dir=data_dir,
                source=source,
                cancel=cancel,
                overlay=overlay,
            )
        except (checks.NoFreshWorktree, OSError) as exc:
            return {"skipped": f"no fresh worktree of commit {commit[:12]}: {exc}"}
        junit = _junit_path(box) if plan["kind"] == "pytest" else None
        command = _command(plan, checkout, python, junit)
        result = checks._run_in(
            command,
            folder,
            data_dir=data_dir,
            box=box,
            cancel=cancel,
            timeout=timeout,
            platform=platform,
            ran_folder=checkout,
            keep=junit,
            keep_limit=JUNIT_MAX_BYTES,
        )
        if result.get("skipped"):
            return {"skipped": str(result["skipped"])}
        found = outcome(result, plan["kind"])
        detail = str(result.get("detail") or "")
        if junit is not None and found == "passed":
            counted = ran_tests(result.get("kept"), plan["test"])
            if counted is None or not counted.proven:
                # Exit 0 without the named test passing alone: collect-only settings, or the
                # test deselected while another one ran.
                found = "not_found"
                if counted is None:
                    detail = "pytest exited 0 but wrote no results Raticode could read"
                elif counted.others:
                    detail = (
                        f"pytest exited 0 but its results include {counted.others} other "
                        f"test{'s' if counted.others != 1 else ''} besides {plan['test']}"
                    )
                else:
                    detail = (
                        f"pytest exited 0 but its results don't show {plan['test']} ran and passed"
                    )
        # Shown without this side's own scratch path, so both sides read the same.
        shown = command.replace(str(junit), "<scratch>/" + junit.name) if junit else command
        return {
            "commit": made,
            "command": shown,
            "exit_code": result.get("exit_code"),
            "outcome": found,
            "seconds": result.get("seconds"),
            "detail": detail,
            "output": str(result.get("output") or "")[-OUTPUT_CHARS:],
            **({"setup": setup} if setup else {}),
        }
    finally:
        checks._remove_box(box)


def run(
    test: str,
    folder: str | Path,
    *,
    data_dir: Path,
    base: str,
    source: str | Path | None = None,
    cancel: threading.Event | None = None,
    timeout: int = TIMEOUT_SECONDS,
    platform: str | None = None,
) -> dict[str, Any]:
    """Run ``test`` on ``base`` plus the work's test files, then on ``folder``'s commit.

    ``base`` is the commit the work started from (the landing branch's fork point).
    The result has ``ran``; when true, ``before`` and ``after`` hold each run and
    ``failed_before`` / ``passes_after`` the pair, else ``not_run_reason`` says why.
    """
    plan = target(test)
    if isinstance(plan, str):
        return not_run(test, plan)
    problem = checks.sandbox_problem(platform)
    if problem:
        return not_run(test, f"its sandbox can't start on this computer: {problem}")
    folder = Path(folder).resolve()
    origin = Path(source) if source else folder
    try:
        commit = checks._git(folder, "rev-parse", "--verify", "HEAD^{commit}")
        prefix = checks._git(folder, "rev-parse", "--show-prefix")
    except checks.NoFreshWorktree as exc:
        return not_run(test, f"the work's commit can't be read ({exc})")
    try:
        start = checks._git(folder, "rev-parse", "--verify", f"{base}^{{commit}}")
    except checks.NoFreshWorktree as exc:
        return not_run(test, f"the work's base commit {base[:12]} can't be read ({exc})")
    found = {"kind": plan["kind"], "folder": str(folder), "commit": commit, "base_commit": start}
    if start == commit:
        return not_run(
            test, f"the work has no commit of its own after its base {start[:12]}", **found
        )
    own = prefix + plan["file"]
    try:
        checks._git(folder, "cat-file", "-e", f"{commit}:{own}")
    except checks.NoFreshWorktree:
        return not_run(
            test,
            f"its file {plan['file']} isn't in the work's commit {commit[:12]}, so the "
            "committed work doesn't contain the test",
            **found,
        )
    try:
        files = sorted({*changed_test_files(folder, start, commit), own})
    except checks.NoFreshWorktree as exc:
        return not_run(test, f"git couldn't list the work's test files ({exc})", **found)
    try:
        tracked = checks._git(folder, "ls-tree", "-r", "--name-only", commit, "--", ".venv")
    except checks.NoFreshWorktree as exc:
        return not_run(test, f"git couldn't list the work's files ({exc})", **found)
    if tracked.strip():
        return not_run(
            test,
            "the work's commit has files in .venv, which could replace the Python the test "
            "runs with",
            **found,
        )
    if len(files) > MAX_TEST_FILES:
        return not_run(
            test,
            f"the work changed {len(files)} test files, more than the {MAX_TEST_FILES} "
            "Raticode adds to its base",
            **found,
        )
    ran_at = datetime.now(UTC).isoformat(timespec="seconds")
    settings: dict[str, Any] = {
        "data_dir": data_dir,
        "source": origin,
        "cancel": cancel,
        "timeout": timeout,
        "platform": platform,
        "python": _python(origin, folder),
    }
    with checks._running:
        checks._sweep(data_dir)
        before = _side(plan, folder, commit=start, overlay=(commit, files), **settings)
        if before.get("skipped"):
            return not_run(test, f"before the change: {before['skipped']}", **found)
        if cancel is not None and cancel.is_set():
            return not_run(test, "the run was cancelled", **found)
        after = _side(plan, folder, commit=commit, overlay=None, **settings)
    if after.get("skipped"):
        return not_run(test, f"after the change: {after['skipped']}", **found)
    return {
        "test": test,
        "ran": True,
        **found,
        "ran_at": ran_at,
        "base_test_files": files,
        "before": before,
        "after": after,
        "failed_before": before["outcome"] == "failed",
        "passes_after": after["outcome"] == "passed",
        "status": status({"ran": True, "before": before, "after": after}),
    }


def status(result: dict[str, Any]) -> str:
    """The pair in a few words, as the receipt's ``status``."""
    if not result.get("ran"):
        return NOT_RUN
    if (result.get("after") or {}).get("outcome") != "passed":
        return "does not pass after the change"
    if (result.get("before") or {}).get("outcome") != "failed":
        return "did not fail before the change"
    return "fails before the change and passes after it"


def _how(side: dict[str, Any]) -> str:
    code = side.get("exit_code")
    return {
        "passed": "passed",
        "failed": f"failed (exit code {code})",
        "not_found": f"wasn't found or collected (pytest exit code {code})",
        "cannot_run": f"couldn't run (exit code {code})",
    }.get(str(side.get("outcome")), f"was stopped ({side.get('detail') or 'no exit code'})")


def summary(result: dict[str, Any]) -> str:
    """One line for the reviewer and the run's record."""
    test = result.get("test") or ""
    if not result.get("ran"):
        return f"Proving test `{test}` was {NOT_RUN}: {result.get('not_run_reason')}."
    before, after = result["before"], result["after"]
    line = (
        f"Raticode ran `{test}` in its sandbox: on the base commit "
        f"{str(result.get('base_commit'))[:12]} with only the task's test files added it "
        f"{_how(before)}; on the work's commit {str(result.get('commit'))[:12]} it "
        f"{_how(after)}."
    )
    state = status(result)
    if state == "did not fail before the change":
        return line + " It did not fail before the change."
    if state == "does not pass after the change":
        return line + " It does not pass after the change."
    return line + " It fails before the change and passes after it."


def receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Raticode's own run of the proving test as the reviewer sees it."""
    shown: dict[str, Any] = {
        "produced_by": "Raticode, not the worker",
        "test": result.get("test") or "",
        "ran": bool(result.get("ran")),
        "status": result.get("status") or status(result),
        "summary": summary(result),
    }
    if not result.get("ran"):
        shown["not_run_reason"] = result.get("not_run_reason") or ""
        return shown

    def side(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "commit": run.get("commit") or "",
            "command": run.get("command") or "",
            "exit_code": run.get("exit_code"),
            "outcome": run.get("outcome"),
            "seconds": run.get("seconds"),
            "output_tail": str(run.get("output") or ""),
        }

    return {
        **shown,
        "failed_before": bool(result.get("failed_before")),
        "passes_after": bool(result.get("passes_after")),
        "before": {**side(result["before"]), "test_files_added": result.get("base_test_files")},
        "after": side(result["after"]),
        "ran_at": result.get("ran_at") or "",
    }


def reusable(previous: dict[str, Any], test: str, folder: str, commit: str, base: str) -> bool:
    """Whether a stored run stands for the same test on the same work and base."""
    return bool(
        previous.get("ran")
        and previous.get("test") == test
        and previous.get("folder") == str(Path(folder).resolve())
        and previous.get("commit") == commit
        and previous.get("base_commit") == base
    )
