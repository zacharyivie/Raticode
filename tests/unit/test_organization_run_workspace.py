"""The workspace step of a worker run: which folders it may open and where it works."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_store import Task
from gofer.ui.organizations import OrganizationManager
from tests.helpers.organizations import act, company


async def quick(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def start(tmp_path: Path) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=quick, start_runtime=False)


def chosen_for(
    manager: OrganizationManager, made: dict[str, Any], eid: str, root: str
) -> dict[str, Any]:
    """What dispatch hands a run: the organization, the claimed ticket and its worker."""
    state = manager.store.get("", made["id"])
    employee = next(e for e in state["config"]["employees"] if e["id"] == eid)
    task = Task(
        title="Work", assignee=eid, status="in_progress", claim="run-1", workspacePath=root
    ).model_dump()
    return {
        "org": state,
        "task": task,
        "employee": employee,
        "working_root": root,
        "isolated": False,
    }


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True)


def test_a_worker_with_assigned_projects_gets_only_those_folders(tmp_path: Path) -> None:
    manager = start(tmp_path)
    root = tmp_path.resolve()
    (root / "other").mkdir()
    first, second = str(root), str(root / "other")
    made = company(
        manager,
        root,
        projectRoots=[first, second],
        employees=[{"id": "lead", "name": "Avery"}, {"id": "cal", "name": "Cal"}],
    )
    act(
        manager, made, "self_work_projects", employeeId="lead", projects=[first], expectedRevision=0
    )

    assigned = manager._run_workspace(chosen_for(manager, made, "lead", first))
    assert assigned.workspace_paths == [first]
    assert assigned.assigned_roots == [first]
    assert assigned.run_root == first
    # A worker saved before assignments keeps every organization folder.
    unassigned = manager._run_workspace(chosen_for(manager, made, "cal", first))
    assert unassigned.workspace_paths == [first, second]
    manager.close()


def test_blocking_git_push_tells_the_worker(tmp_path: Path) -> None:
    manager = start(tmp_path)
    made = company(manager, tmp_path.resolve())
    chosen = chosen_for(manager, made, "lead", str(tmp_path.resolve()))
    note = "git push is blocked in this run. Leave publishing to the user.\n"
    assert note in manager._run_workspace(chosen).workspace_note
    chosen["org"]["config"]["blockGitPush"] = False
    assert note not in manager._run_workspace(chosen).workspace_note
    manager.close()


def test_a_dirty_git_project_is_refused_without_worktree_isolation(tmp_path: Path) -> None:
    repo = tmp_path.resolve() / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("project\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "initial")
    manager = start(tmp_path)
    made = company(manager, repo, workspaceIsolation="checkout")
    chosen = chosen_for(manager, made, "lead", str(repo))
    assert manager._run_workspace(chosen).run_root == str(repo)
    (repo / "README.md").write_text("user edit in progress\n")
    with pytest.raises(ValueError, match="uncommitted changes"):
        manager._run_workspace(chosen)
    manager.close()
