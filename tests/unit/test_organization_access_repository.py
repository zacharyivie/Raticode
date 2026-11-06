"""Per-project ticket permissions for workers, and each organization's shared repository."""

from __future__ import annotations

import gzip
import json
import os
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import organization_repository as repository
from gofer.ui.organization_knowledge import run_context
from gofer.ui.organizations import OrganizationManager

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
    "PATH": "/usr/bin:/bin",
}


async def final(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def start(tmp_path: Path, stream: Any = final) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, env=GIT_ENV, capture_output=True, text=True
    ).stdout.strip()


def repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "commit", "-q", "--allow-empty", "-m", "base")
    return path


def company(manager: OrganizationManager, root: Path, name: str = "Studio", **config: Any) -> Any:
    employees = config.pop(
        "employees",
        [
            {"id": "lead", "name": "Avery Lane"},
            {
                "id": "builder",
                "name": "Mira Santos",
                "reportsTo": "lead",
                "ticketAccess": {"desktop": ["create", "cancel"], "docs": []},
            },
            {
                "id": "writer",
                "name": "Noor Haddad",
                "reportsTo": "lead",
                "ticketAccess": {"docs": ["create", "move"]},
            },
        ],
    )
    projects = config.pop(
        "projects", [{"id": "desktop", "name": "Desktop"}, {"id": "docs", "name": "Docs"}]
    )
    return manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": name,
                    "slug": name.lower(),
                    "employees": employees,
                    "projects": projects,
                    **config,
                }
            },
        },
    )


def act(
    manager: OrganizationManager,
    org: dict[str, Any],
    action: str,
    actor: str = "user",
    **params: Any,
) -> Any:
    return manager.call(
        org["projectRoot"], actor, {"organizationId": org["id"], "action": action, "params": params}
    )


def as_worker(
    manager: OrganizationManager, org: dict[str, Any], worker: str, action: str, **params: Any
) -> Any:
    state = manager.store.get("", org["id"])
    return manager.call(
        org["projectRoot"],
        f"employee:{worker}",
        {"organizationId": org["id"], "action": action, "params": params},
        employee_id=worker,
        bound_org=org["id"],
        generation=state["runtime"]["generation"],
    )


def read(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return act(manager, org, "read")


def task(manager: OrganizationManager, org: dict[str, Any], tid: str) -> Any:
    return next(t for t in read(manager, org)["runtime"]["tasks"] if t["id"] == tid)


def settle(manager: OrganizationManager) -> None:
    until = time.monotonic() + 10
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


# ------------------------------------------------------------------------ permissions


def test_ticket_permissions_are_enforced_per_project_and_action(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    # Running lets workers act, but nothing is dispatched in this test.
    act(manager, org, "control", state="running")

    made = as_worker(manager, org, "builder", "task_create", title="Board", project="desktop")
    assert made["project"] == "desktop"
    with pytest.raises(ValueError, match="Mira Santos can't create tickets in Docs"):
        as_worker(manager, org, "builder", "task_create", title="Guide", project="docs")
    with pytest.raises(ValueError, match="can't create tickets outside a project"):
        as_worker(manager, org, "builder", "task_create", title="Loose")

    mine = act(manager, org, "task_create", title="Drag", project="desktop", assignee="builder")
    with pytest.raises(ValueError, match="can't change the status of tickets in Desktop"):
        as_worker(
            manager,
            org,
            "builder",
            "task_update",
            taskId=mine["id"],
            expectedRevision=mine["revision"],
            changes={"status": "backlog"},
        )
    with pytest.raises(ValueError, match="can't edit tickets in Desktop"):
        as_worker(
            manager,
            org,
            "builder",
            "task_update",
            taskId=mine["id"],
            expectedRevision=mine["revision"],
            changes={"title": "Renamed"},
        )
    # Evidence is the run's own output, not an edit of the plan.
    noted = as_worker(
        manager,
        org,
        "builder",
        "task_update",
        taskId=mine["id"],
        expectedRevision=mine["revision"],
        changes={"evidence": "Tests pass"},
    )
    cancelled = as_worker(
        manager,
        org,
        "builder",
        "task_update",
        taskId=noted["id"],
        expectedRevision=noted["revision"],
        changes={"status": "cancelled"},
    )
    assert cancelled["status"] == "cancelled"

    # Noor may create and move tickets in Docs, and nothing in Desktop.
    guide = as_worker(
        manager, org, "writer", "task_create", title="Guide", project="docs", assignee="writer"
    )
    moved = as_worker(
        manager,
        org,
        "writer",
        "task_update",
        taskId=guide["id"],
        expectedRevision=guide["revision"],
        changes={"status": "backlog"},
    )
    assert moved["status"] == "backlog"
    with pytest.raises(ValueError, match="can't cancel tickets in Docs"):
        as_worker(
            manager,
            org,
            "writer",
            "task_update",
            taskId=guide["id"],
            expectedRevision=moved["revision"],
            changes={"status": "cancelled"},
        )
    with pytest.raises(ValueError, match="Noor Haddad can't create tickets in Desktop"):
        as_worker(manager, org, "writer", "task_create", title="Board", project="desktop")

    # Workers saved before permissions existed keep the organization-wide rules.
    legacy = as_worker(manager, org, "lead", "task_create", title="Anything", project="docs")
    assert legacy["project"] == "docs"
    # Refused actions changed nothing.
    titles = [t["title"] for t in read(manager, org)["runtime"]["tasks"]]
    assert titles == ["Board", "Drag", "Guide", "Anything"]
    manager.close()


def test_moving_a_ticket_between_projects_needs_permission_in_both(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(
        manager,
        tmp_path,
        employees=[
            {"id": "lead", "name": "Avery Lane"},
            {
                "id": "builder",
                "name": "Mira Santos",
                "reportsTo": "lead",
                "ticketAccess": {"desktop": ["create", "edit"], "docs": []},
            },
        ],
    )
    act(manager, org, "control", state="running")
    made = act(manager, org, "task_create", title="Shared", project="desktop", assignee="builder")
    with pytest.raises(ValueError, match="can't edit tickets in Docs"):
        as_worker(
            manager,
            org,
            "builder",
            "task_update",
            taskId=made["id"],
            expectedRevision=made["revision"],
            changes={"project": "docs"},
        )
    assert task(manager, org, made["id"])["project"] == "desktop"
    manager.close()


def test_rem_needs_consent_to_widen_ticket_permissions(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    config = read(manager, org)["config"]

    def with_access(access: Any) -> dict[str, Any]:
        employees = [
            {**e, "ticketAccess": access} if e["id"] == "builder" else e
            for e in config["employees"]
        ]
        return {**config, "employees": employees}

    widened = act(
        manager,
        org,
        "configure",
        "Rem:thread",
        config=with_access({"desktop": ["create", "cancel", "move"], "docs": []}),
        expectedRevision=org["revision"],
        reason="More",
    )
    assert "let Mira Santos do more with tickets" in widened["summary"]
    lifted = act(
        manager,
        org,
        "configure",
        "Rem:thread",
        config=with_access(None),
        expectedRevision=org["revision"],
        reason="Lift",
    )
    assert lifted["needsUserConsent"] is True
    narrowed = act(
        manager,
        org,
        "configure",
        "Rem:thread",
        config=with_access({"desktop": ["create"]}),
        expectedRevision=org["revision"],
        reason="Less",
    )
    builder = next(e for e in narrowed["config"]["employees"] if e["id"] == "builder")
    assert builder["ticketAccess"] == {"desktop": ["create"]}
    with pytest.raises(ValueError, match="unknown project"):
        act(
            manager,
            org,
            "configure",
            config=with_access({"ghost": ["create"]}),
            expectedRevision=narrowed["revision"],
            reason="Bad",
        )
    # Deleting a project removes it from every worker's permissions.
    revision = read(manager, org)["revision"]
    act(manager, org, "plan_delete", kind="project", id="desktop", expectedRevision=revision)
    builder = next(e for e in read(manager, org)["config"]["employees"] if e["id"] == "builder")
    assert builder["ticketAccess"] == {}
    manager.close()


def test_runs_are_told_their_ticket_permissions(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Board", project="desktop", assignee="builder")
    state = read(manager, org)
    ticket = next(t for t in state["runtime"]["tasks"] if t["id"] == made["id"])
    assert run_context(state, ticket)["ticketPermissions"] == {
        "Desktop": ["create", "cancel"],
        "Docs": [],
    }
    lead = act(manager, org, "task_create", title="Plan", assignee="lead")
    lead_ticket = next(t for t in read(manager, org)["runtime"]["tasks"] if t["id"] == lead["id"])
    assert run_context(state, lead_ticket)["ticketPermissions"] == "unrestricted"
    manager.close()


# ------------------------------------------------------------------------- repository


def files_on_main(path: Path) -> list[str]:
    return git(path, "ls-tree", "-r", "--name-only", "main").splitlines()


def test_turning_on_the_shared_repository_creates_it_and_mirrors_knowledge(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    before = act(
        manager,
        org,
        "knowledge_save",
        entry={"kind": "decision", "title": "Five columns", "body": "Backlog to Done."},
    )
    off = act(manager, org, "repository")
    assert off == {"enabled": False, "exists": False, "path": off["path"]}
    request = act(
        manager,
        org,
        "plan_save",
        "Rem:thread",
        kind="repository",
        item={"enabled": True},
        expectedRevision=read(manager, org)["revision"],
    )
    assert "shared organization repository" in request["summary"]
    assert not Path(off["path"]).exists()
    act(
        manager,
        org,
        "plan_save",
        kind="repository",
        item={"enabled": True},
        expectedRevision=read(manager, org)["revision"],
    )
    path = Path(off["path"])
    assert path.is_relative_to(tmp_path / "data" / "organization-repositories")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert git(path, "remote") == ""
    names = files_on_main(path)
    assert "README.md" in names and "decisions/.gitkeep" in names
    assert f"knowledge/decisions/five-columns-{before['id'][-6:]}.md" in names
    note = act(
        manager,
        org,
        "knowledge_save",
        entry={"kind": "note", "title": "Card anatomy", "body": "Key, title, owner."},
    )
    assert f"knowledge/notes/card-anatomy-{note['id'][-6:]}.md" in files_on_main(path)
    act(
        manager,
        org,
        "knowledge_save",
        entry={"id": note["id"], "archived": True},
        expectedVersion=1,
    )
    assert not any("card-anatomy" in name for name in files_on_main(path))
    state = act(manager, org, "repository")
    assert state["exists"] and state["commits"][0][3] == "Archive note: Card anatomy"
    assert state["files"] == ["README.md", "decisions/", "knowledge/", "notes/"]
    manager.close()


def test_runs_change_the_shared_repository_on_their_branch_and_accepting_merges(
    tmp_path: Path,
) -> None:
    project = repo(tmp_path / "project")
    seen: dict[str, Any] = {}

    async def worker(**options: Any) -> Any:
        paths = options["workflow"]["organizationWorkspacePaths"]
        shared = Path(next(p for p in paths if "organization-shared-worktrees" in p))
        seen.setdefault("paths", []).append(paths)
        seen["instructions"] = options["agent_instructions"]
        title = json.loads(options["messages"][0]["body"])["task"]["title"]
        (shared / "decisions" / f"{title}.md").write_text(f"# {title}\n")
        git(shared, "add", ".")
        git(shared, "commit", "-qm", title)
        (shared / "notes" / f"{title}-draft.md").write_text("left uncommitted\n")
        push = subprocess.run(
            ["git", "-C", str(shared), "push", "origin", "HEAD"],
            capture_output=True,
            text=True,
            env={**os.environ},
        )
        seen["push"] = push.returncode
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, worker)
    org = company(manager, project)
    act(
        manager,
        org,
        "plan_save",
        kind="repository",
        item={"enabled": True},
        expectedRevision=read(manager, org)["revision"],
    )
    path = Path(act(manager, org, "repository")["path"])
    first = act(manager, org, "task_create", title="pricing", assignee="lead")
    second = act(manager, org, "task_create", title="hiring", assignee="lead")
    act(manager, org, "control", state="running")
    for _ in range(2):
        manager.dispatch()
        settle(manager)
    act(manager, org, "control", state="paused")
    assert "shared repository is checked out for this ticket" in seen["instructions"]
    assert seen["push"] != 0  # No remote, and push is blocked anyway.

    ticket = task(manager, org, first["id"])
    assert ticket["sharedWorktree"]["branch"].startswith("shared/pricing-")
    changes = act(manager, org, "ticket_changes", taskId=first["id"])
    assert [f[0] for f in changes["shared"]["files"]] == [
        "decisions/pricing.md",
        "notes/pricing-draft.md",
    ]
    # Nothing reaches main before review.
    assert "decisions/pricing.md" not in files_on_main(path)

    accepted = act(
        manager,
        org,
        "review",
        taskId=first["id"],
        expectedRevision=ticket["revision"],
        decision="accept",
        body="Good",
    )
    assert accepted["sharedWorktree"] is None
    names = files_on_main(path)
    assert "decisions/pricing.md" in names and "notes/pricing-draft.md" in names
    assert not Path(ticket["sharedWorktree"]["checkout"]).exists()
    assert "Merged this ticket's shared repository changes" in accepted["comments"][-1]["body"]

    # A cancelled ticket's shared work stays on its branch, unmerged.
    other = task(manager, org, second["id"])
    act(
        manager,
        org,
        "task_update",
        taskId=second["id"],
        expectedRevision=other["revision"],
        changes={"status": "cancelled"},
    )
    manager.release_shared(manager.store.get("", org["id"]))
    kept = task(manager, org, second["id"])
    assert kept["sharedWorktree"] is None
    assert "stay on branch" in kept["comments"][-1]["body"]
    assert "decisions/hiring.md" not in files_on_main(path)
    assert other["sharedWorktree"]["branch"] in act(manager, org, "repository")["openBranches"]

    # The run input records the shared checkout, so it's inspectable later.
    attempt = next(
        a for a in read(manager, org)["runtime"]["attempts"].values() if a["taskId"] == first["id"]
    )
    stored = json.loads(gzip.open(manager.run_input_path(org["id"], attempt["id"])).read())
    assert "shared repository" in stored["instructions"]
    manager.close()


def test_conflicting_shared_changes_are_kept_on_their_branch(tmp_path: Path) -> None:
    project = repo(tmp_path / "project")

    async def worker(**options: Any) -> Any:
        paths = options["workflow"]["organizationWorkspacePaths"]
        shared = Path(next(p for p in paths if "organization-shared-worktrees" in p))
        title = json.loads(options["messages"][0]["body"])["task"]["title"]
        (shared / "notes" / "plan.md").write_text(f"{title}\n")
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, worker)
    org = company(manager, project)
    act(
        manager,
        org,
        "plan_save",
        kind="repository",
        item={"enabled": True},
        expectedRevision=read(manager, org)["revision"],
    )
    path = Path(act(manager, org, "repository")["path"])
    ids = [act(manager, org, "task_create", title=t, assignee="lead")["id"] for t in ("a", "b")]
    act(manager, org, "control", state="running")
    for _ in range(2):
        manager.dispatch()
        settle(manager)
    act(manager, org, "control", state="paused")
    for tid in ids:
        current = task(manager, org, tid)
        act(
            manager,
            org,
            "review",
            taskId=tid,
            expectedRevision=current["revision"],
            decision="accept",
            body="ok",
        )
    assert (path / "notes" / "plan.md").read_text() == "a\n"
    conflicted = task(manager, org, ids[1])
    assert "conflict with main and were not merged" in conflicted["comments"][-1]["body"]
    assert git(path, "status", "--porcelain") == ""
    manager.close()


def test_each_organization_has_its_own_repository(tmp_path: Path) -> None:
    first_root, second_root = repo(tmp_path / "one"), repo(tmp_path / "two")
    seen: dict[str, list[str]] = {}

    async def worker(**options: Any) -> Any:
        oid = options["workflow"]["organizationRun"]["organizationId"]
        seen[oid] = options["workflow"]["organizationWorkspacePaths"]
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, worker)
    orgs = [company(manager, first_root, "One"), company(manager, second_root, "Two")]
    for org in orgs:
        act(
            manager,
            org,
            "plan_save",
            kind="repository",
            item={"enabled": True},
            expectedRevision=read(manager, org)["revision"],
        )
        act(manager, org, "task_create", title="Work", assignee="lead")
        act(manager, org, "control", state="running")
    for _ in range(2):
        manager.dispatch()
        settle(manager)
    paths = [Path(act(manager, org, "repository")["path"]) for org in orgs]
    assert paths[0] != paths[1]
    assert not paths[0].is_relative_to(first_root) and not paths[0].is_relative_to(second_root)
    for org, other in ((orgs[0], orgs[1]), (orgs[1], orgs[0])):
        given = " ".join(seen[org["id"]])
        assert org["id"] in given and other["id"] not in given
    with pytest.raises(repository.RepositoryError):
        repository.path_for(tmp_path, "../escape")
    manager.close()


def test_removing_a_worker_blocks_their_open_tickets_with_a_reason(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Guide", project="docs", assignee="writer")
    config = read(manager, org)["config"]
    config["employees"] = [e for e in config["employees"] if e["id"] != "writer"]
    act(
        manager,
        org,
        "configure",
        config=config,
        expectedRevision=read(manager, org)["revision"],
        reason="Removed Noor Haddad",
    )
    blocked = task(manager, org, made["id"])
    assert blocked["status"] == "blocked"
    assert blocked["blockReason"].startswith("Its worker, project, goal or reviewer was removed")
    manager.close()
