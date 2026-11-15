"""Review and land from the ticket: the patch, the user's Accept, and change requests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_store import OrganizationConflict
from gofer.ui.rem_kanban import KanbanRefused, get_ticket, put_ticket
from tests.unit.test_organization_plans import _repo, act, company, read, settle, start
from tests.unit.test_rem_kanban import add
from tests.unit.test_rem_kanban import board as board

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
    "PATH": "/usr/bin:/bin",
}


@pytest.fixture(autouse=True)
def identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, env=ENV, capture_output=True, text=True
    ).stdout.strip()


def commit(cwd: Path, name: str, text: str, message: str) -> str:
    (cwd / name).write_text(text)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-qm", message)
    return git(cwd, "rev-parse", "HEAD")


# Organizations -----------------------------------------------------------------------------


def test_only_the_user_lands_an_organization_ticket_after_a_clean_check(tmp_path: Path) -> None:
    root = _repo(tmp_path / "project")
    holder: dict[str, Any] = {}

    async def employee(**options: Any) -> Any:
        work = Path(options["working_dir"])
        commit(work, "board.js", "export const columns = 5;\n", "Board")
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, employee)
    holder["manager"] = manager
    org = company(manager, root)
    task = act(manager, org, "task_create", title="Drag cards", assignee="builder")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    act(manager, org, "control", state="paused")

    def current() -> dict[str, Any]:
        return next(t for t in read(manager, org)["runtime"]["tasks"] if t["id"] == task["id"])

    assert current()["status"] == "in_review"
    patch = act(manager, org, "ticket_patch", taskId=task["id"])
    assert patch["baseBranch"] == "main" and patch["baseBranchGuessed"] is False
    assert [(f["path"], f["original"], f["modified"]) for f in patch["files"]] == [
        ("board.js", "", "export const columns = 5;\n")
    ]
    assert patch["merge"] == {"state": "fast_forward", "conflicts": []}

    params = {
        "taskId": task["id"],
        "expectedRevision": current()["revision"],
        "patchRevision": patch["revision"],
    }
    # Rem, its consented requests and workers can't land; only the user's own click can.
    for actor in ("Rem", "Rem:kanban"):
        with pytest.raises(ValueError, match="Only you"):
            act(manager, org, "land", actor, **params)
    with pytest.raises(ValueError, match="Only you"):
        manager.call(
            org["projectRoot"],
            "Rem",
            {"organizationId": org["id"], "action": "land", "params": params},
            consented=True,
        )
    with pytest.raises(OrganizationConflict, match="moved"):
        act(manager, org, "land", **{**params, "patchRevision": "stale"})

    # A competing change on main: nothing lands, the conflict is named.
    commit(root, "board.js", "export const columns = 3;\n", "Competing")
    conflicted = act(manager, org, "land", **params)
    assert conflicted["conflict"] is True and conflicted["conflicts"] == ["board.js"]
    assert current()["status"] == "in_review"
    assert (root / "board.js").read_text() == "export const columns = 3;\n"

    git(root, "reset", "-q", "--hard", "HEAD~1")
    landed = act(manager, org, "land", **params, body="Looks right")
    assert landed["landed"] is True and landed["how"] == "fast_forward"
    assert git(root, "rev-parse", "main") == landed["commit"] == patch["tip"]
    assert (root / "board.js").read_text() == "export const columns = 5;\n"
    done = current()
    assert done["status"] == "done" and done["landed"]["baseBranch"] == "main"
    bodies = [c["body"] for c in done["comments"] if c.get("kind") != "status"]
    assert "Looks right" in bodies
    assert f"Landed {patch['branch']} in main (fast-forwarded" in bodies[-1]

    # Removing the finished worktree no longer tells the user to merge it by hand.
    manager.release_worktrees(read(manager, org))
    last = [c["body"] for c in current()["comments"] if c.get("kind") != "status"][-1]
    assert "already in main" in last
    manager.close()


# Rem's board ---------------------------------------------------------------------------------


def rem_ticket_with_branch(
    home: Any, project: Path, tmp_path: Path
) -> tuple[dict[str, Any], str, str]:
    git(project, "init", "-q", "-b", "dev")
    base = commit(project, "app.py", "one\ntwo\n", "base")
    checkout = tmp_path / "wt"
    git(project, "worktree", "add", "-q", "-b", "rem/fix-1234", str(checkout), "dev")
    tip = commit(checkout, "app.py", "one\nTWO\n", "Fix")
    git(project, "worktree", "remove", str(checkout))
    created = add(home, "Fix it", project_dir=str(project))
    run = {
        "id": "run-1",
        "task_id": "task-1",
        "occurrence": "o1",
        "state": "succeeded",
        "thread_id": "thread-1",
        "recipe": {"project_dir": str(project), "title": "Fix it"},
        "released_worktree": {
            "branch": "rem/fix-1234",
            "base": base,
            "repository": str(project),
            "base_branch": "dev",
        },
    }
    with home.store.connect() as db:
        home.store.put(db, "runs", run)
        ticket = get_ticket(db, created["id"])
        ticket.update(run_id="run-1", thread_id="thread-1", status="done")
        put_ticket(db, ticket)
    return created, base, tip


def test_a_rem_tickets_patch_lands_only_by_the_user(board, tmp_path: Path) -> None:
    home, _, project = board
    created, _, tip = rem_ticket_with_branch(home, project, tmp_path)
    address = {"ticket_id": created["id"], "scope": "rem"}
    patch = home.kanban.patch(address)
    assert patch["branches"] == ["rem/fix-1234"] and patch["baseBranch"] == "dev"
    assert patch["files"][0]["original"] == "one\ntwo\n"
    assert patch["files"][0]["modified"] == "one\nTWO\n"

    def revision() -> int:
        with home.store.read() as db:
            return int(get_ticket(db, created["id"])["revision"])

    land = {
        **address,
        "action": "land",
        "expected_revision": revision(),
        "patch_revision": patch["revision"],
    }
    with pytest.raises(KanbanRefused, match="Only you"):
        home.kanban.act("rem", land, "r1")

    commit(project, "app.py", "one\nzwei\n", "Competing")
    conflict = home.kanban.act("user", land, "r2")
    assert conflict["conflict"] is True and conflict["conflicts"] == ["app.py"]

    # The conflict offers asking the same worker to rebase; it goes to the ticket's thread.
    sent: list[tuple[str, str, str]] = []
    home.kanban.follow_up = lambda thread, body, request: sent.append((thread, body, request))
    rebase = {
        **address,
        "action": "request_changes",
        "expected_revision": revision(),
        "rebase": {"branch": "rem/fix-1234", "base_branch": "dev", "conflicts": ["app.py"]},
    }
    with pytest.raises(KanbanRefused, match="Only you"):
        home.kanban.act("rem", rebase, "r3")
    home.kanban.act("user", rebase, "r4")
    assert sent[0][0] == "thread-1" and "Rebase rem/fix-1234 onto the current dev" in sent[0][1]

    git(project, "reset", "-q", "--hard", "HEAD~1")
    landed = home.kanban.act("user", {**land, "expected_revision": revision()}, "r5")
    assert landed["landed"] is True and git(project, "rev-parse", "dev") == tip
    with home.store.read() as db:
        saved = get_ticket(db, created["id"])
    assert saved["landed"]["commit"] == tip and saved["status"] == "done"
    timeline = home.kanban.timeline(address)["comments"]
    assert any("Landed rem/fix-1234 in dev" in c["body"] for c in timeline)
    assert any(c.get("title") == "Requested changes" for c in timeline)


def test_line_comments_go_back_to_the_same_thread(board, tmp_path: Path) -> None:
    home, _, project = board
    created, _, _ = rem_ticket_with_branch(home, project, tmp_path)
    sent: list[tuple[str, str, str]] = []
    home.kanban.follow_up = lambda thread, body, request: sent.append((thread, body, request))
    with home.store.read() as db:
        revision = get_ticket(db, created["id"])["revision"]
    home.kanban.act(
        "user",
        {
            "ticket_id": created["id"],
            "scope": "rem",
            "action": "request_changes",
            "expected_revision": revision,
            "note": "Nearly there.",
            "comments": [{"path": "app.py", "line": 2, "side": "modified", "body": "Lowercase"}],
        },
        "c1",
    )
    assert sent == [
        ("thread-1", "Nearly there.\nLine comments on the patch:\n- app.py:2: Lowercase", "c1")
    ]
    with pytest.raises(KanbanRefused, match="Write a note"):
        home.kanban.act(
            "user",
            {
                "ticket_id": created["id"],
                "scope": "rem",
                "action": "request_changes",
                "expected_revision": revision,
            },
            "c2",
        )
