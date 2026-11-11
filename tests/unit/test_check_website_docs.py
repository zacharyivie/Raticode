from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def gate() -> Any:
    spec = importlib.util.spec_from_file_location(
        "check_website_docs", ROOT / "scripts/check-website-docs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("frontend/src/components/RemHome.jsx", True),
        ("frontend/electron/main.cjs", True),
        ("src/gofer/ui/rem_home.py", True),
        ("mobile/android/app/src/main/java/dev/raticode/mobile/MainActivity.kt", True),
        ("frontend/src/lib/rattishTriggers.test.mjs", False),
        ("frontend/electron/tests/background-display.cjs", False),
        ("frontend/electron/rem-pane.browser.cjs", False),
        ("mobile/android/app/src/test/java/dev/raticode/mobile/FooTest.kt", False),
        ("mobile/android/app/src/androidTest/java/dev/raticode/mobile/FooTest.kt", False),
        ("tests/unit/test_rem.py", False),
        ("docs/rem.md", False),
        ("website/docs/rem/chat.md", False),
        ("mobile/android/docs/architecture.md", False),
    ],
)
def test_app_paths(gate: Any, path: str, expected: bool) -> None:
    assert gate.is_app_change(path) is expected


def test_feature_without_website_fails(gate: Any) -> None:
    passed, text = gate.evaluate(
        ["src/gofer/ui/rem_home.py"], ["feat: Rem can do a new thing\n\n - detail"]
    )
    assert not passed
    assert "website/" in text
    assert "src/gofer/ui/rem_home.py" in text


def test_feature_with_website_passes(gate: Any) -> None:
    passed, _ = gate.evaluate(
        ["src/gofer/ui/rem_home.py", "website/docs/rem/chat.md"], ["feat: new thing"]
    )
    assert passed


def test_marker_in_commit_or_pr_skips(gate: Any) -> None:
    files = ["frontend/src/App.jsx"]
    assert gate.evaluate(files, ["feat: x\n\nDocs-not-needed: internal flag"])[0]
    assert gate.evaluate(files, ["feat: x"], "Title\n\ndocs-not-needed: refactor only")[0]
    # An empty reason does not count.
    assert not gate.evaluate(files, ["feat: x\n\nDocs-not-needed:"])[0]


def test_feature_title_in_pull_request_counts(gate: Any) -> None:
    assert not gate.evaluate(["frontend/src/App.jsx"], ["wip"], "feat: new board")[0]


def test_fix_and_tests_only_pass(gate: Any) -> None:
    passed, text = gate.evaluate(["src/gofer/ui/api.py"], ["fix: handle a missing file"])
    assert passed and "Reminder" in text
    assert gate.evaluate(["tests/unit/test_api.py"], ["feat: more tests"])[0]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_main_compares_branch_with_base(gate: Any, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "T")
    (repo / "README").write_text("x\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-qm", "chore: start")
    _git(repo, "checkout", "-qb", "feature")
    app = repo / "src/gofer/new.py"
    app.parent.mkdir(parents=True)
    app.write_text("x = 1\n")
    _git(repo, "add", "src")
    _git(repo, "commit", "-qm", "feat: a new feature")
    assert gate.main(["--repo", str(repo)]) == 1

    page = repo / "website/docs/new.md"
    page.parent.mkdir(parents=True)
    page.write_text("# New\n")
    _git(repo, "add", "website")
    _git(repo, "commit", "-qm", "chore: document it")
    assert gate.main(["--repo", str(repo)]) == 0
    assert gate.main(["--repo", str(repo), "--base", "missing-branch"]) == 2
