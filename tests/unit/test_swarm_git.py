"""Managed Git works across worktrees without widening provider sandboxes."""

import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from gofer.ui import swarm_git
from gofer.ui.swarm_workspaces import git, prepare_attempt, prepare_run
from gofer.ui.swarms import SwarmManager


@pytest.fixture
def assignment(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    (root / "code.txt").write_text("original\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    (root / "user.txt").write_text("staged\n")
    git(root, "add", "user.txt")
    (root / "user.txt").write_text("unstaged\n")
    before = (git(root, "status", "--porcelain"), git(root, "diff", "--cached"))
    workspace = prepare_attempt(prepare_run(root, tmp_path / "run"), "worker")
    yield root, workspace
    assert (git(root, "status", "--porcelain"), git(root, "diff", "--cached")) == before


def test_local_stage_commit_preserves_user_index_and_disables_hooks(
    assignment, tmp_path, monkeypatch
):
    root, workspace = assignment
    cwd = Path(workspace["path"])
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o700)
    git(root, "config", "core.hooksPath", str(hooks))
    (cwd / "code.txt").write_text("implementation\n")
    # Caller environment must not redirect the app-owned staging operation.
    monkeypatch.setenv("GIT_INDEX_FILE", str(root / ".git" / "index"))
    swarm_git.execute(workspace, None, {"operation": "stage", "paths": ["."]})
    diff = swarm_git.execute(workspace, None, {"operation": "diff", "staged": True})
    assert "+implementation" in diff["output"]
    result = swarm_git.execute(workspace, None, {"operation": "commit", "message": "implement"})
    assert result["revision"] != workspace["baseRevision"]
    assert swarm_git.execute(workspace, None, {"operation": "status"})["output"] == ""
    assert (root / "code.txt").read_text() == "original\n"


@pytest.mark.parametrize("operation", ["stage", "commit", "push", "create_pr"])
def test_disabled_permissions_reject_before_launch(assignment, operation):
    _, workspace = assignment
    settings = {"local": False} if operation in {"stage", "commit"} else {}
    with patch.object(swarm_git, "git") as launch:
        with pytest.raises(ValueError, match="disabled"):
            swarm_git.execute(workspace, settings, {"operation": operation})
        launch.assert_not_called()


@pytest.mark.parametrize("paths", [["../code.txt"], ["/tmp/file"], [".git/config"], [], [True]])
def test_stage_rejects_paths_outside_assignment(assignment, paths):
    _, workspace = assignment
    with pytest.raises(ValueError):
        swarm_git.execute(workspace, None, {"operation": "stage", "paths": paths})


def test_remote_opt_in_publishes_only_assignment_branch(assignment, tmp_path):
    root, workspace = assignment
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    git(root, "remote", "add", "origin", str(remote))
    swarm_git.execute(workspace, {"remote": True}, {"operation": "push"})
    assert git(remote, "rev-parse", workspace["branch"]) == workspace["baseRevision"]
    assert git(remote, "for-each-ref", "--format=%(refname)") == f"refs/heads/{workspace['branch']}"


def test_pr_opt_in_uses_explicit_repository_and_literal_body(assignment):
    root, workspace = assignment
    git(root, "remote", "add", "origin", "git@github.com:example/project.git")
    real_run = subprocess.run
    calls = []

    def run(argv, **kwargs):
        if argv[0] == "gh":
            calls.append(argv)
            return subprocess.CompletedProcess(
                argv, 0, "https://github.com/example/project/pull/1\n", ""
            )
        return real_run(argv, **kwargs)

    with patch("gofer.ui.swarm_git.subprocess.run", side_effect=run):
        result = swarm_git.execute(
            workspace,
            {"remote": True},
            {
                "operation": "create_pr",
                "base": "main",
                "title": "Feature",
                "body": "Two lines\nLiteral $(echo no) and `code`.",
            },
        )
    assert calls == [
        [
            "gh",
            "pr",
            "create",
            "--repo",
            "github.com/example/project",
            "--head",
            workspace["branch"],
            "--base",
            "main",
            "--title",
            "Feature",
            "--body",
            "Two lines\nLiteral $(echo no) and `code`.",
        ]
    ]
    assert result["output"].endswith("/pull/1")


@pytest.mark.parametrize("multiple", [False, True])
def test_pr_rejects_split_or_multiple_push_destinations(assignment, multiple):
    root, workspace = assignment
    url = "https://github.com/upstream/project.git"
    git(root, "remote", "add", "origin", url)
    git(
        root,
        "remote",
        "set-url",
        "--push",
        "origin",
        url if multiple else "git@github.com:fork/project.git",
    )
    if multiple:
        git(
            root,
            "remote",
            "set-url",
            "--add",
            "--push",
            "origin",
            "git@github.com:fork/project.git",
        )
    with pytest.raises(ValueError, match="matching fetch and push URL"):
        swarm_git.execute(
            workspace,
            {"remote": True},
            {
                "operation": "create_pr",
                "base": "main",
                "title": "Feature",
                "body": "Description",
            },
        )


def test_push_does_not_publish_submodule_commits(tmp_path):
    parent, remote, child, child_remote = [
        tmp_path / name for name in ("parent", "remote.git", "child", "child.git")
    ]
    for path in (parent, remote, child, child_remote):
        path.mkdir()
    for path in (remote, child_remote):
        git(path, "init", "--bare")
    for path in (parent, child):
        git(path, "init", "-b", "main")
        (path / "file").write_text("initial")
        git(path, "add", ".")
        git(path, "commit", "-m", "initial")
    git(child, "remote", "add", "origin", str(child_remote))
    git(child, "push", "-u", "origin", "main")
    git(parent, "remote", "add", "origin", str(remote))
    git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-b",
        "main",
        str(child_remote),
        "child",
    )
    git(parent, "commit", "-am", "add child")
    branch = "taskurotta/swarm/worker"
    git(parent, "checkout", "-b", branch)
    checkout = parent / "child"
    git(checkout, "checkout", "-b", branch)
    git(checkout, "push", "-u", "origin", branch)
    before = git(child_remote, "rev-parse", branch)
    (checkout / "file").write_text("private child change")
    git(checkout, "commit", "-am", "child change")
    git(parent, "commit", "-am", "child pointer")
    git(parent, "config", "push.recurseSubmodules", "on-demand")
    swarm_git.execute(
        {"path": str(parent), "branch": branch}, {"remote": True}, {"operation": "push"}
    )
    assert git(remote, "rev-parse", branch) == git(parent, "rev-parse", "HEAD")
    assert git(child_remote, "rev-parse", branch) == before
    assert git(child_remote, "rev-parse", branch) != git(checkout, "rev-parse", "HEAD")


def test_changed_branch_and_symlink_parent_are_rejected(assignment, tmp_path):
    _, workspace = assignment
    cwd = Path(workspace["path"])
    (cwd / "outside").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        swarm_git.execute(workspace, None, {"operation": "stage", "paths": ["outside/file"]})
    git(cwd, "checkout", "--detach")
    with pytest.raises(ValueError, match="branch changed"):
        swarm_git.execute(workspace, None, {"operation": "stage", "paths": ["."]})


@pytest.mark.parametrize(
    "value",
    [{"local": "false"}, {"remote": 1}, {"remote": True, "local": False}, {"other": True}, []],
)
def test_invalid_permission_values_are_rejected(value):
    with pytest.raises(ValueError):
        swarm_git.permissions(value)


def test_permissions_persist_and_resume_keeps_run_snapshot(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    root = tmp_path / "project"
    root.mkdir()
    try:
        swarm = manager.create(
            root,
            {
                "name": "Git team",
                "agents": [
                    {"id": "lead", "name": "Lead", "role": "Implement", "isOrchestrator": True},
                ],
            },
        )
        assert swarm["gitPermissions"] == {"local": True, "remote": False}
        sid = swarm["id"]
        manager.start(root, sid, "Implement")
        manager.control(root, sid, "pause")
        manager.update(root, sid, {"gitPermissions": {"local": True, "remote": True}})
        resumed = manager.control(root, sid, "resume")
        assert resumed["run"]["configuration"]["gitPermissions"]["remote"] is False
        assert resumed["gitPermissions"]["remote"] is True
        manager.control(root, sid, "stop")
        assert (
            manager.start(root, sid, "Next")["run"]["configuration"]["gitPermissions"]["remote"]
            is True
        )
    finally:
        manager.close()


async def test_member_tool_is_bound_to_live_assignment(assignment, tmp_path):
    root, _ = assignment
    manager = SwarmManager(tmp_path / "manager", start_runtime=False)
    try:
        swarm = manager.create(
            root,
            {
                "name": "Git team",
                "agents": [
                    {"id": "lead", "name": "Lead", "role": "Implement", "isOrchestrator": True},
                ],
            },
        )
        sid = swarm["id"]
        run = manager.start(root, sid, "Implement")["run"]
        manager._tokens["test"] = (str(root), sid, "lead", run["id"])
        with pytest.raises(ValueError, match="active assignment"):
            manager.tool("test", {"action": "git", "operation": "status"})

        async def stream(**kwargs):
            path = kwargs["working_dir"]
            assert path != root
            (path / "code.txt").write_text("new implementation\n")
            manager.tool("test", {"action": "git", "operation": "stage", "paths": ["code.txt"]})
            result = manager.tool(
                "test", {"action": "git", "operation": "commit", "message": "implement"}
            )
            assert result["revision"] == git(path, "rev-parse", "HEAD")
            with pytest.raises(ValueError, match="disabled"):
                manager.tool("test", {"action": "git", "operation": "push"})
            yield {"type": "final", "message": {"body": "done"}}

        manager._stream = stream
        await manager._turn(str(root), sid, "lead", threading.Event())
        run = manager.get(root, sid)["run"]
        assert run["attempts"][0]["state"] == "succeeded"
        assert [
            event["payload"]["operation"]
            for event in run["events"]
            if event["kind"] == "git_operation"
        ] == ["stage", "commit"]
        with pytest.raises(ValueError, match="active assignment"):
            manager.tool("test", {"action": "git", "operation": "stage", "paths": ["."]})
    finally:
        manager.close()
