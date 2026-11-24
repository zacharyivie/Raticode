"""Project scans find the check command, landing branch and decision folder from the
project's own files and history, with evidence, no model call and no built-in answer."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_decisions as decisions
from gofer.ui import rem_project_scan as scan
from tests.unit.test_rem_decisions import Store
from tests.unit.test_rem_home import home as home

IDENTITY = ["-c", "user.name=T", "-c", "user.email=t@e", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *IDENTITY, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def make(root: Path, files: dict[str, str], *, branch: str = "main") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, "init", "-q", "-b", branch)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init", "--allow-empty")
    return root


def top(result: dict[str, Any], topic: str) -> dict[str, Any] | None:
    found = result["topics"][topic]
    return found[0] if found else None


def answers(result: dict[str, Any], topic: str) -> list[str]:
    return [c["answer"] for c in result["topics"][topic]]


def test_a_node_project_uses_its_package_scripts_and_lockfile(tmp_path: Path):
    root = make(
        tmp_path / "web",
        {
            "package.json": json.dumps(
                {"scripts": {"dev": "vite", "test": "vitest run", "lint": "eslint ."}}, indent=2
            ),
            "pnpm-lock.yaml": "lockfileVersion: 9\n",
            ".github/workflows/ci.yml": (
                "on:\n  pull_request:\n    branches: [main]\njobs:\n  test:\n    steps:\n"
                "      - run: pnpm install --frozen-lockfile\n      - run: pnpm run test\n"
            ),
        },
    )
    result = scan.scan(root)
    best = top(result, scan.CHECK_TOPIC)
    assert best is not None
    # CI runs it and package.json declares it: they agree, so it wins.
    assert best["answer"] == "pnpm run test"
    refs = {s["ref"] for s in best["sources"]}
    assert ".github/workflows/ci.yml:8" in refs and "package.json:4" in refs
    # Installs never count as a check.
    assert not any("install" in a for a in answers(result, scan.CHECK_TOPIC))
    assert "pnpm run dev" not in answers(result, scan.CHECK_TOPIC)


def test_a_python_project_with_a_documented_gate_prefers_it(tmp_path: Path):
    root = make(
        tmp_path / "py",
        {
            "pyproject.toml": "[project]\nname='x'\n\n[tool.pytest.ini_options]\naddopts='-q'\n",
            "CONTRIBUTING.md": (
                "# Contributing\n\nBefore opening a pull request, run `tox -e lint,py312`.\n"
                "\n```sh\npip install -e .\npytest -q\n```\n"
            ),
            "tox.ini": "[tox]\nenv_list = py312\n",
        },
    )
    result = scan.scan(root)
    assert answers(result, scan.CHECK_TOPIC)[0] == "tox -e lint,py312"
    best = top(result, scan.CHECK_TOPIC)
    assert best is not None
    assert best["sources"][0]["excerpt"] == (
        "Before opening a pull request, run `tox -e lint,py312`."
    )
    # pyproject.toml configures pytest: a weaker alternative, never the recommendation.
    assert "pytest" in answers(result, scan.CHECK_TOPIC)
    assert "pip install -e ." not in answers(result, scan.CHECK_TOPIC)


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"Cargo.toml": "[package]\nname = 'x'\n"}, "cargo test"),
        ({"go.mod": "module example.com/x\n\ngo 1.23\n"}, "go test ./..."),
        ({"Makefile": "build:\n\tcc x.c\n\ncheck: build\n\t./run-tests\n"}, "make check"),
        ({"justfile": "test:\n    ./t.sh\n"}, "just test"),
    ],
)
def test_build_files_name_the_tool_the_project_already_uses(
    tmp_path: Path, files: dict[str, str], expected: str
):
    result = scan.scan(make(tmp_path / "p", files))
    assert answers(result, scan.CHECK_TOPIC)[0] == expected


def test_an_empty_project_gets_no_recommendation_at_all(tmp_path: Path):
    root = make(tmp_path / "empty", {"notes.txt": "hello\n"})
    result = scan.scan(root)
    assert all(not found for found in result["topics"].values())
    assert scan.requests(result) == []
    # Not even a guessed branch: one branch with no landings and no remote isn't evidence.
    assert answers(result, scan.BRANCH_TOPIC) == []


def test_existing_decision_records_are_found_in_whatever_folder_they_live(tmp_path: Path):
    records = {
        f"architecture/adr/000{n}-choice-{n}.md": f"# {n}. Choice\n\n## Status\n\nAccepted\n"
        for n in range(1, 4)
    }
    madr = {
        "notes/decisions/0001-use-x.md": "---\nstatus: accepted\n---\n# Use X\n",
    }
    root = make(tmp_path / "adr", {**records, **madr, "README.md": "# x\n"})
    result = scan.scan(root)
    assert answers(result, scan.FOLDER_TOPIC) == ["architecture/adr", "notes/decisions"]
    best = top(result, scan.FOLDER_TOPIC)
    assert best is not None
    assert best["sources"][0]["ref"] == "architecture/adr/0001-choice-1.md"
    assert "Status section" in best["sources"][0]["excerpt"]


def raticode_like(tmp_path: Path) -> Path:
    root = make(
        tmp_path / "Raticode",
        {
            "AGENTS.md": (
                "# Agent Instructions\n\n```bash\npython -m pytest -n 8\n"
                "ruff check src tests --fix\n```\n\n### Before landing on `dev`\n\n"
                "- Before fast-forwarding `dev`, run `python3 scripts/check-dev.py` from the\n"
                "  repository root or worktree.\n"
            ),
            "scripts/check-dev.py": "#!/usr/bin/env python3\nprint('ok')\n",
            "scripts/check-branding.py": "#!/usr/bin/env python3\nprint('ok')\n",
            "pyproject.toml": "[project]\nname='gofer'\n[tool.pytest.ini_options]\n",
        },
        branch="test",
    )
    git(root, "branch", "dev")
    git(root, "branch", "main")
    folder = tmp_path / "dev-home"
    git(root, "worktree", "add", "-q", str(folder), "dev")
    for name in ("feature-a", "feature-b", "fix/c"):
        git(root, "branch", name, "dev")
        work = tmp_path / name.replace("/", "-")
        git(root, "worktree", "add", "-q", str(work), name)
        (work / f"{name.replace('/', '-')}.txt").write_text("x\n")
        git(work, "add", "-A")
        git(work, "commit", "-qm", name)
        git(folder, "merge", "-q", "--ff-only", name)
    return root


def test_the_raticode_layout_recommends_check_dev_and_dev(tmp_path: Path):
    root = raticode_like(tmp_path)
    result = scan.scan(root)
    assert answers(result, scan.CHECK_TOPIC)[0] == "python3 scripts/check-dev.py"
    best_branch = top(result, scan.BRANCH_TOPIC)
    assert best_branch is not None and best_branch["answer"] == "dev"
    labels = [s["label"] for s in best_branch["sources"]]
    assert "3 branches landed on dev in this repository" in labels
    assert "AGENTS.md says work lands there" in labels
    # A command the docs show with --fix changes files, so it's never a check.
    assert not any("--fix" in a for a in answers(result, scan.CHECK_TOPIC))


def test_requests_carry_one_recommendation_with_checkable_evidence(tmp_path: Path):
    root = raticode_like(tmp_path)
    result = scan.scan(root)
    made = {r.topic: r for r in scan.requests(result)}
    check = made[scan.CHECK_TOPIC]
    assert check.recommendation is not None
    assert check.recommendation.answer == "python3 scripts/check-dev.py"
    assert check.question == "Which command shows that work in Raticode is sound before it lands?"
    assert check.reversible and check.remember_as == "fact"
    # Raticode's own evidence check keeps every piece the scan cites.
    checked, dropped = decisions.check_evidence(check, str(root))
    assert dropped == []
    assert checked.recommendation is not None
    assert checked.recommendation.evidence[0].ref == "AGENTS.md:10"
    branch, dropped = decisions.check_evidence(made[scan.BRANCH_TOPIC], str(root))
    assert dropped == []


@pytest.fixture
def store() -> tuple[sqlite3.Connection, Store]:
    return sqlite3.connect(":memory:"), Store()


def test_scan_results_go_through_the_one_decision_store(tmp_path: Path, store):
    db, book_store = store
    root = raticode_like(tmp_path)
    result = scan.scan(root)
    # Auto mode off: the user confirms each one.
    outcomes = scan.file_results(db, book_store, result, decides_alone=False, only_missing=True)
    assert {o["topic"]: o["outcome"] for o in outcomes} == {
        scan.CHECK_TOPIC: "pending",
        scan.BRANCH_TOPIC: "pending",
    }
    # A second automatic scan never asks again about a topic that has an entry.
    again = scan.file_results(db, book_store, result, decides_alone=False, only_missing=True)
    assert {o["outcome"] for o in again} == {"skipped"}
    book = decisions.read(db, book_store)
    assert len(book["items"]) == 2
    assert all(i["source"]["via"] == "scan" for i in book["items"])


def test_with_auto_mode_rem_records_its_scan_answers_as_provisional(tmp_path: Path, store):
    db, book_store = store
    root = raticode_like(tmp_path)
    outcomes = scan.file_results(
        db, book_store, scan.scan(root), decides_alone=True, only_missing=True
    )
    assert {o["outcome"] for o in outcomes} == {"provisional"}
    item = decisions.in_force(decisions.read(db, book_store), str(root), scan.CHECK_TOPIC)
    assert item is not None and item["decided_by"] == "rem"
    # Raticode runs a provisional command only while auto mode is on.
    assert decisions.may_run(item, auto=True)
    assert not decisions.may_run(item, auto=False)


def test_a_rescan_never_changes_a_decision_and_only_refreshes_matching_evidence(
    tmp_path: Path, store
):
    db, book_store = store
    root = raticode_like(tmp_path)
    scan.file_results(db, book_store, scan.scan(root), decides_alone=False, only_missing=True)
    book = decisions.read(db, book_store)
    check = next(i for i in book["items"] if i["topic"] == scan.CHECK_TOPIC)
    decisions.change(db, book_store, {"action": "use", "id": check["id"]}, actor="user")
    # AGENTS.md is edited elsewhere: same answer, so only the evidence is refreshed.
    agents = root / "AGENTS.md"
    agents.write_text("# Agents\n\n" + agents.read_text())
    assert decisions.stale_reason(
        decisions.in_force(decisions.read(db, book_store), str(root), scan.CHECK_TOPIC) or {}
    )
    outcomes = scan.file_results(
        db, book_store, scan.scan(root), decides_alone=True, only_missing=False
    )
    assert {o["topic"]: o["outcome"] for o in outcomes}[scan.CHECK_TOPIC] == "rechecked"
    current = decisions.in_force(decisions.read(db, book_store), str(root), scan.CHECK_TOPIC)
    assert current is not None
    assert current["status"] == "confirmed" and current["decided_by"] == "user"
    assert decisions.stale_reason(current) == ""
    # The docs now name another command: even in auto mode the scan only proposes it,
    # and the user's decision stays in force until they choose.
    agents.write_text(agents.read_text().replace("scripts/check-dev.py", "scripts/check-all.py"))
    (root / "scripts" / "check-all.py").write_text("#!/usr/bin/env python3\n")
    outcomes = scan.file_results(
        db, book_store, scan.scan(root), decides_alone=True, only_missing=False
    )
    assert {o["topic"]: o["outcome"] for o in outcomes}[scan.CHECK_TOPIC] == "pending"
    book = decisions.read(db, book_store)
    proposal = next(
        i for i in book["items"] if i["status"] == "pending" and i["topic"] == scan.CHECK_TOPIC
    )
    assert proposal["supersedes"] == current["id"]
    still = decisions.in_force(book, str(root), scan.CHECK_TOPIC)
    assert still is not None and still["answer"] == "python3 scripts/check-dev.py"


def test_an_answer_the_user_turned_down_is_never_brought_back(tmp_path: Path, store):
    db, book_store = store
    root = raticode_like(tmp_path)
    scan.file_results(db, book_store, scan.scan(root), decides_alone=False, only_missing=True)
    book = decisions.read(db, book_store)
    branch = next(i for i in book["items"] if i["topic"] == scan.BRANCH_TOPIC)
    decisions.change(db, book_store, {"action": "reject", "id": branch["id"]}, actor="user")
    outcomes = scan.file_results(
        db, book_store, scan.scan(root), decides_alone=True, only_missing=False
    )
    assert {o["topic"]: o["outcome"] for o in outcomes}[scan.BRANCH_TOPIC] == ("turned down before")


def test_a_scan_runs_nothing_and_asks_no_model(tmp_path: Path, monkeypatch):
    """Only git reads: the check command a scan finds is never executed by the scan."""
    root = raticode_like(tmp_path)
    marker = tmp_path / "ran"
    (root / "scripts" / "check-dev.py").write_text(
        f"#!/usr/bin/env python3\nopen({str(marker)!r}, 'w').write('x')\n"
    )
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(args: Any, *a: Any, **kw: Any):
        calls.append(list(args))
        return real(args, *a, **kw)

    monkeypatch.setattr(scan.subprocess, "run", spy)
    scan.scan(root)
    assert not marker.exists()
    assert calls and all(c[0] == "git" for c in calls)
    # No git command that changes a repository, a branch or its config.
    changing = {
        "add",
        "commit",
        "push",
        "pull",
        "fetch",
        "checkout",
        "switch",
        "reset",
        "merge",
        "rebase",
        "worktree",
        "branch",
        "tag",
        "stash",
        "init",
        "clone",
        "--add",
        "--unset",
    }
    assert [c for c in calls if changing & set(c)] == []


def test_a_fresh_request_covers_one_scanned_topic_with_checked_evidence(tmp_path: Path):
    root = raticode_like(tmp_path)
    fresh = scan.fresh_request(root, scan.CHECK_TOPIC)
    assert fresh is not None and fresh.topic == scan.CHECK_TOPIC
    assert fresh.recommendation is not None
    assert fresh.recommendation.answer == "python3 scripts/check-dev.py"
    assert fresh.recommendation.evidence
    assert scan.fresh_request(root, "adr:workflow-format") is None
    assert scan.fresh_request(make(tmp_path / "empty", {}), scan.CHECK_TOPIC) is None


def test_ask_again_on_a_scanned_topic_offers_a_fresh_recommendation(tmp_path: Path, home):
    root = raticode_like(tmp_path)
    home.change_decisions(
        {"action": "add", "topic": scan.CHECK_TOPIC, "answer": "pytest -q", "project": str(root)}
    )
    home.change_decisions(
        {"action": "add", "topic": "deploy.target", "answer": "staging", "project": str(root)}
    )
    with home.store.read() as db:
        book = decisions.read(db, home.store)
    check = decisions.in_force(book, str(root), scan.CHECK_TOPIC)
    other = decisions.in_force(book, str(root), "deploy.target")
    assert check is not None and other is not None
    view = home.change_decisions({"action": "reopen", "id": check["id"]})
    asked = next(i for i in view["items"] if i.get("supersedes") == check["id"])
    assert asked["status"] == "pending" and asked["fresh"] is True
    assert asked["recommended"] == "python3 scripts/check-dev.py"
    assert asked["current_answer"] == "pytest -q"
    # The decision stays in force while the card waits.
    assert next(i for i in view["items"] if i["id"] == check["id"])["status"] == "confirmed"
    # A topic no scan finds gets no recommendation, only the current answer.
    view = home.change_decisions({"action": "reopen", "id": other["id"]})
    plain = next(i for i in view["items"] if i.get("supersedes") == other["id"])
    assert plain["recommended"] == "" and "fresh" not in plain
    assert plain["current_answer"] == "staging"


def test_keeping_the_current_answer_still_lets_a_rescan_refresh_its_evidence(tmp_path: Path, store):
    db, book_store = store
    root = raticode_like(tmp_path)
    scan.file_results(db, book_store, scan.scan(root), decides_alone=False, only_missing=True)
    book = decisions.read(db, book_store)
    check = next(i for i in book["items"] if i["topic"] == scan.CHECK_TOPIC)
    decisions.change(db, book_store, {"action": "use", "id": check["id"]}, actor="user")
    # Ask again; the fresh scan finds the same command, and the user keeps the current one.
    decisions.change(
        db,
        book_store,
        {"action": "reopen", "id": check["id"]},
        actor="user",
        offer=scan.fresh_request(root, scan.CHECK_TOPIC),
    )
    asked = next(
        i for i in decisions.read(db, book_store)["items"] if i.get("supersedes") == check["id"]
    )
    assert asked["recommended"] == check["answer"]
    decisions.change(db, book_store, {"action": "reject", "id": asked["id"]}, actor="user")
    # The cited file changes with the same answer: the rescan refreshes the evidence
    # instead of treating the answer in force as turned down.
    agents = root / "AGENTS.md"
    agents.write_text("# Agents\n\n" + agents.read_text())
    outcomes = scan.file_results(
        db, book_store, scan.scan(root), decides_alone=False, only_missing=False
    )
    assert {o["topic"]: o["outcome"] for o in outcomes}[scan.CHECK_TOPIC] == "rechecked"
