"""Project setup for new worktrees: the setup file, the user's approval, and running it."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import worktree_setup
from gofer.ui.headless import agent_request_refusal
from gofer.ui.organizations import OrganizationManager
from gofer.ui.rem_home_worktree import ensure_worktree
from gofer.ui.swarm_workspaces import integrate, prepare_attempt, prepare_run
from gofer.ui.worktree_setup import (
    SetupConfigError,
    effective_config,
    parse_config,
    prepare,
)
from tests.helpers.organizations import act


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=T", "-c", "user.email=t@e.st", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def python(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def project(tmp_path: Path, setup: dict[str, Any] | None, ignore: str = "") -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("deps\n.env\nsetup-ran.txt\n" + ignore)
    (root / "README.md").write_text("hello\n")
    if setup is not None:
        (root / ".raticode").mkdir()
        (root / ".raticode" / "worktree-setup.json").write_text(json.dumps(setup, indent=2))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    (root / "deps").mkdir()
    (root / "deps" / "lib.txt").write_text("installed\n")
    (root / ".env").write_text("TOKEN=local\n")
    return root


def worktree(root: Path, tmp_path: Path, name: str = "wt") -> Path:
    path = tmp_path / name
    git(root, "worktree", "add", "-q", "-b", name, str(path), "main")
    return path


RECORD = python(
    "import os, pathlib; pathlib.Path('setup-ran.txt').write_text("
    "os.environ['RATICODE_PROJECT_ROOT'] + '|' + os.environ['RATICODE_WORKTREE_SETUP'])"
)
SETUP = {"setup": RECORD, "link": ["deps"], "copy": [".env"]}


def approve_current(data: Path, root: Path) -> None:
    state = worktree_setup.status(data, root)
    worktree_setup.approve(data, root, state["file"]["sha256"])


# The setup file ---------------------------------------------------------------------


def test_setup_file_validates_paths_fields_and_os_overrides() -> None:
    config = parse_config(
        json.dumps(
            {
                "setup": ["npm ci", " "],
                "link": ["frontend/node_modules/"],
                "copy": [".env"],
                "timeoutSeconds": 60,
                "os": {"windows": {"setup": "npm.cmd ci", "link": []}},
            }
        )
    )
    linux = effective_config(config, "linux")
    windows = effective_config(config, "windows")
    assert linux["setup"] == ["npm ci"] and linux["link"] == ["frontend/node_modules"]
    assert windows["setup"] == ["npm.cmd ci"] and windows["link"] == []
    # Fields an override doesn't set come from the shared part.
    assert windows["copy"] == [".env"] and windows["timeoutSeconds"] == 60
    assert effective_config(parse_config("{}"), "macos")["timeoutSeconds"] == 900
    bad: dict[str, Any]
    for bad in (
        {"link": ["../outside"]},
        {"link": ["/etc/passwd"]},
        {"copy": ["C:/Users/x"]},
        {"copy": [".git/config"]},
        {"link": "deps"},
        {"setup": 3},
        {"timeoutSeconds": 0},
        {"timeoutSeconds": True},
        {"os": {"solaris": {}}},
        {"os": {"linux": {"os": {}}}},
        {"stup": "typo"},
    ):
        with pytest.raises(SetupConfigError):
            parse_config(json.dumps(bad))
    with pytest.raises(SetupConfigError, match="valid JSON"):
        parse_config("{")


# Approval ---------------------------------------------------------------------------


def test_unapproved_setup_does_not_run_and_waits_for_approval(tmp_path: Path) -> None:
    root = project(tmp_path, SETUP)
    data = tmp_path / "data"
    checkout = worktree(root, tmp_path)
    result = prepare(data, source=root, worktree=checkout, surface="rem")
    assert result is not None and result["status"] == "needs_approval"
    assert "haven't approved" in result["summary"]
    assert not (checkout / "setup-ran.txt").exists()
    assert not (checkout / "deps").exists() and not (checkout / ".env").exists()
    pending = worktree_setup.Approvals(data).pending()
    assert [(p["projectPath"], p["source"]) for p in pending] == [(str(root), "rem")]
    state = worktree_setup.status(data, root)
    assert state["state"] == "unapproved" and state["approved"] is None
    assert state["file"]["effective"]["link"] == ["deps"]


def test_approved_setup_links_copies_and_runs_in_the_new_worktree(tmp_path: Path) -> None:
    root = project(tmp_path, SETUP)
    data = tmp_path / "data"
    approve_current(data, root)
    assert worktree_setup.status(data, root)["state"] == "approved"
    # Approvals are private to this account.
    assert (data / worktree_setup.APPROVALS_FILE).stat().st_mode & 0o077 == 0
    checkout = worktree(root, tmp_path)
    result = prepare(data, source=root, worktree=checkout, surface="organization")
    assert result is not None and result["status"] == "passed", result
    assert result["version"] == "current"
    assert (checkout / "deps").is_symlink()
    assert (checkout / "deps" / "lib.txt").read_text() == "installed\n"
    assert not (checkout / ".env").is_symlink()
    assert (checkout / ".env").read_text() == "TOKEN=local\n"
    assert (checkout / "setup-ran.txt").read_text() == f"{root}|1"
    # Nothing the setup placed shows up as a change to commit.
    assert git(checkout, "status", "--porcelain") == ""
    # A project without a setup file has nothing to report.
    other = tmp_path / "plain"
    other.mkdir()
    git(other, "init", "-q")
    git(other, "commit", "-q", "--allow-empty", "-m", "init")
    assert prepare(data, source=other, worktree=other, surface="rem") is None


def test_approval_covers_every_worktree_of_the_project(tmp_path: Path) -> None:
    root = project(tmp_path, SETUP)
    data = tmp_path / "data"
    existing = worktree(root, tmp_path, "existing")
    # Approving from a worktree of the project approves the project itself.
    approve_current(data, existing)
    assert worktree_setup.status(data, root)["state"] == "approved"
    assert worktree_setup.project_key(existing) == worktree_setup.project_key(root)


def test_changed_setup_runs_only_the_approved_version_until_reapproved(tmp_path: Path) -> None:
    root = project(tmp_path, SETUP)
    data = tmp_path / "data"
    approve_current(data, root)
    approved_text = (root / ".raticode" / "worktree-setup.json").read_text()
    changed = {"setup": python("open('changed.txt', 'w').write('x')")}
    (root / ".raticode" / "worktree-setup.json").write_text(json.dumps(changed))
    git(root, "commit", "-qam", "change setup")
    state = worktree_setup.status(data, root)
    assert state["state"] == "changed"
    assert state["approved"]["text"] == approved_text

    checkout = worktree(root, tmp_path)
    result = prepare(data, source=root, worktree=checkout, surface="swarm")
    assert result is not None and result["status"] == "passed", result
    assert result["version"] == "approved-earlier"
    assert "changed and isn't approved yet" in result["summary"]
    assert (checkout / "setup-ran.txt").exists() and not (checkout / "changed.txt").exists()

    # Reviewing the new version and approving it replaces the old one.
    worktree_setup.approve(data, root, worktree_setup.sha256_text(json.dumps(changed)))
    assert worktree_setup.status(data, root)["state"] == "approved"
    assert worktree_setup.Approvals(data).pending() == []
    second = worktree(root, tmp_path, "second")
    again = prepare(data, source=root, worktree=second, surface="rem")
    assert again is not None and again["version"] == "current"
    assert (second / "changed.txt").exists()


def test_approval_must_match_what_the_user_reviewed(tmp_path: Path) -> None:
    root = project(tmp_path, SETUP)
    data = tmp_path / "data"
    with pytest.raises(ValueError, match="changed since you reviewed"):
        worktree_setup.approve(data, root, "0" * 64)
    with pytest.raises(ValueError, match="sha256"):
        worktree_setup.approve(data, root, "short")
    (root / ".raticode" / "worktree-setup.json").write_text('{"link": ["../x"]}')
    state = worktree_setup.status(data, root)
    assert state["state"] == "invalid" and "stay inside the project" in state["file"]["error"]
    with pytest.raises(ValueError, match="stay inside"):
        worktree_setup.approve(data, root, state["file"]["sha256"])
    (tmp_path / "again").mkdir()
    other = project(tmp_path / "again", SETUP)
    approve_current(data, other)
    assert worktree_setup.revoke(data, other)["state"] == "unapproved"


def test_version_a_new_worktree_found_can_be_approved_from_the_project(tmp_path: Path) -> None:
    """The committed file a worktree got can differ from the project folder's copy."""
    root = project(tmp_path, SETUP)
    data = tmp_path / "data"
    checkout = worktree(root, tmp_path)
    (root / ".raticode" / "worktree-setup.json").write_text('{"setup": "echo local edit"}')
    prepare(data, source=root, worktree=checkout, surface="rem")
    state = worktree_setup.status(data, root)
    assert state["pending"]["text"] == json.dumps(SETUP, indent=2)
    worktree_setup.approve(data, root, state["pending"]["sha256"])
    record = worktree_setup.Approvals(data).get(worktree_setup.project_key(root))
    assert record is not None and record["text"] == json.dumps(SETUP, indent=2)


def test_agent_tokens_can_read_but_never_approve_setup() -> None:
    assert agent_request_refusal("GET", "/api/worktree-setup") is None
    assert agent_request_refusal("GET", "/api/worktree-setup/pending") is None
    assert agent_request_refusal("POST", "/api/worktree-setup/approve")
    assert agent_request_refusal("POST", "/api/worktree-setup/revoke")


# Failures ---------------------------------------------------------------------------


def test_failures_stop_setup_and_say_what_failed(tmp_path: Path) -> None:
    fail = python("import sys; print('boom'); sys.exit(3)")
    never = python("open('never.txt', 'w').write('x')")
    root = project(tmp_path, {"setup": [fail, never], "link": ["missing", "deps"]})
    data = tmp_path / "data"
    approve_current(data, root)
    checkout = worktree(root, tmp_path)
    result = prepare(data, source=root, worktree=checkout, surface="rem")
    assert result is not None and result["status"] == "failed"
    steps = result["steps"]
    assert steps[0]["ok"] is False and "doesn't exist in the project" in steps[0]["detail"]
    assert steps[1]["ok"] is True
    command = steps[2]
    assert command["exitCode"] == 3 and "boom" in command["output"]
    assert len(steps) == 3 and not (checkout / "never.txt").exists()
    assert result["summary"].startswith("Project setup failed at missing")


def test_unignored_link_or_copy_is_removed_so_it_is_never_committed(tmp_path: Path) -> None:
    root = project(tmp_path, {"copy": ["secrets.txt"]})
    (root / "secrets.txt").write_text("private\n")
    data = tmp_path / "data"
    approve_current(data, root)
    checkout = worktree(root, tmp_path)
    result = prepare(data, source=root, worktree=checkout, surface="rem")
    assert result is not None and result["status"] == "failed"
    assert "isn't ignored by git" in result["steps"][0]["detail"]
    assert not (checkout / "secrets.txt").exists()


def test_setup_stops_at_its_time_limit(tmp_path: Path) -> None:
    slow = python("import time; time.sleep(30)")
    root = project(tmp_path, {"setup": slow, "timeoutSeconds": 1})
    data = tmp_path / "data"
    approve_current(data, root)
    checkout = worktree(root, tmp_path)
    started = time.monotonic()
    result = prepare(data, source=root, worktree=checkout, surface="rem")
    assert time.monotonic() - started < 15
    assert result is not None and result["status"] == "failed"
    assert "time limit" in result["summary"]


# Where worktrees are made -----------------------------------------------------------


def test_rem_worktree_reports_only_new_checkouts(tmp_path: Path) -> None:
    root = project(tmp_path, None)
    plan = {
        "checkout": str(tmp_path / "rem" / "run"),
        "root": str(tmp_path / "rem" / "run"),
        "repository": str(root),
        "branch": "rem/run",
        "base": git(root, "rev-parse", "HEAD"),
    }
    assert ensure_worktree(plan) is True
    assert ensure_worktree(plan) is False


def test_swarm_integration_candidate_gets_setup_before_checks(tmp_path: Path) -> None:
    root = project(tmp_path, {"link": ["deps"]})
    data = tmp_path / "data"
    approve_current(data, root)
    workspace = prepare_run(root, tmp_path / "run")
    checkout = prepare_attempt(workspace, "worker")
    assert checkout["setup"] == "pending"
    path = Path(checkout["path"])
    (path / "feature.txt").write_text("feature")
    git(path, "add", ".")
    git(path, "commit", "-qm", "Feature")
    attempt = {"result": {"revision": git(path, "rev-parse", "HEAD")}}
    checks = [
        [sys.executable, "-c", "import pathlib; assert pathlib.Path('deps/lib.txt').is_file()"]
    ]
    without = integrate(workspace, attempt, checks, tmp_path / "logs")
    assert not without["passed"] and "setup" not in without
    result = integrate(
        workspace,
        {**attempt},
        checks,
        tmp_path / "logs",
        setup=lambda candidate: prepare(data, source=root, worktree=candidate, surface="swarm"),
    )
    assert result["setup"]["status"] == "passed"
    assert result["passed"], result


def test_swarm_integration_without_checks_never_runs_setup(tmp_path: Path) -> None:
    root = project(tmp_path, {"link": ["deps"]})
    workspace = prepare_run(root, tmp_path / "run")
    checkout = prepare_attempt(workspace, "worker")
    path = Path(checkout["path"])
    # An agent's change to what setup would run arrives with the merge itself.
    (path / "feature.txt").write_text("feature")
    git(path, "add", ".")
    git(path, "commit", "-qm", "Feature")
    attempt = {"result": {"revision": git(path, "rev-parse", "HEAD")}}
    calls: list[Path] = []

    def setup(candidate: Path) -> dict[str, Any]:
        calls.append(candidate)
        return {"status": "passed"}

    result = integrate(workspace, attempt, [], tmp_path / "logs", setup=setup)
    assert calls == []
    assert "setup" not in result


def test_organization_worker_runs_in_a_set_up_worktree(tmp_path: Path) -> None:
    root = project(tmp_path, SETUP)
    seen: list[dict[str, Any]] = []

    async def worker(**options: Any) -> Any:
        cwd = Path(options["working_dir"])
        seen.append(
            {
                "cwd": cwd,
                "ran": (cwd / "setup-ran.txt").exists(),
                "linked": (cwd / "deps").is_symlink(),
                "instructions": options.get("agent_instructions") or "",
            }
        )
        await asyncio.sleep(0)
        yield {"type": "final", "message": {"body": "Done."}}

    def run_once(data: str) -> dict[str, Any]:
        manager = OrganizationManager(tmp_path / data, stream=worker, start_runtime=False)
        try:
            org = manager.call(
                str(root),
                "user",
                {
                    "action": "create",
                    "params": {
                        "config": {"name": "Studio", "employees": [{"id": "lead", "name": "A"}]}
                    },
                },
            )

            act(manager, org, "task_create", title=f"Work {data}", assignee="lead")
            act(manager, org, "control", state="running")
            manager.dispatch()
            until = time.monotonic() + 15
            while manager._active and time.monotonic() < until:
                time.sleep(0.02)
            state = manager.store.get("", org["id"])
            events = manager.store.events("", org["id"])
            act(manager, org, "control", state="paused")
            return {"state": state, "events": events}
        finally:
            manager.close()

    first = run_once("unapproved")
    task = first["state"]["runtime"]["tasks"][0]
    assert task["worktreeSetup"]["status"] == "needs_approval"
    assert seen[0]["cwd"] != root and not seen[0]["ran"]
    assert "Project setup didn't run" in seen[0]["instructions"]
    assert any(e["kind"] == "worktree_setup" for e in first["events"])

    approve_current(tmp_path / "approved", root)
    second = run_once("approved")
    task = second["state"]["runtime"]["tasks"][0]
    assert task["worktreeSetup"]["status"] == "passed", task["worktreeSetup"]
    assert seen[1]["ran"] and seen[1]["linked"]
