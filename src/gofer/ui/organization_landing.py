"""Review an organization ticket's patch and land its branch from the ticket.

``ticket_patch`` reads the ticket branch's full patch, where it would land, and whether it
merges cleanly. ``land`` is the user's Accept: it checks the branch is still what they
reviewed, commits the worker's leftovers, lands the branch in its base and accepts the
review. Only the user can land: Rem, Auto mode and workers are refused, and Rem can't
ask for it through a consent request either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.ui import ticket_patch
from gofer.ui.organization_knowledge import _task_root, find_task
from gofer.ui.organization_store import OrganizationConflict, now

USER_ONLY = "Only you can accept and land a ticket's branch, from the ticket"


def ticket_source(org: dict[str, Any], task: dict[str, Any]) -> dict[str, Any] | None:
    """The ticket's branch, its repository, the branch it came from and its live checkout."""
    worktree = task.get("worktree") or task.get("releasedWorktree") or {}
    branch = worktree.get("branch")
    if not branch:
        return None
    repository = worktree.get("repository") or _task_root(org, task)
    if not repository or not Path(repository).is_dir():
        return {"branch": branch, "missing": True}
    base_branch = worktree.get("base_branch") or ""
    guessed = False
    if not base_branch:
        # Tickets from before the base branch was recorded: the branch checked out in the
        # project now, when it still contains the commit the work started from.
        current = ticket_patch.current_branch(repository) or ""
        tip = ticket_patch.branch_commit(repository, current) if current else None
        base = worktree.get("base")
        if tip and base and ticket_patch.is_ancestor(repository, base, tip):
            base_branch, guessed = current, True
    return {
        "branch": branch,
        "repository": repository,
        "baseBranch": base_branch or None,
        "baseBranchGuessed": guessed,
        "base": worktree.get("base"),
        "checkout": (task.get("worktree") or {}).get("checkout"),
    }


def patch_view(source: dict[str, Any]) -> dict[str, Any]:
    """The branch's full patch plus whether it can land in its base right now."""
    if source.get("missing"):
        return {"branch": source["branch"], "files": [], "missing": True}
    patch = ticket_patch.read_patch(
        source["repository"],
        source["branch"],
        base_branch=source.get("baseBranch"),
        base_commit=source.get("base"),
        checkout=source.get("checkout"),
    )
    merge: dict[str, Any] | None = None
    if source.get("baseBranch"):
        state = ticket_patch.merge_state(source["repository"], source["baseBranch"], patch["tip"])
        merge = {"state": state["state"], "conflicts": state.get("conflicts") or []}
    return {
        **patch,
        "baseBranchGuessed": source.get("baseBranchGuessed", False),
        "repository": source["repository"],
        "merge": merge,
    }


def read(org: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    task = find_task(org, params.get("taskId"))
    source = ticket_source(org, task)
    if source is None:
        return {"branch": None, "files": [], "landed": task.get("landed")}
    try:
        view = patch_view(source)
    except ticket_patch.PatchError as exc:
        raise ValueError(str(exc)) from None
    return {**view, "landed": task.get("landed")}


def land(
    manager: Any, project: str, org: dict[str, Any], params: dict[str, Any], actor: str
) -> dict[str, Any]:
    """The user's Accept: land the ticket's branch in its base, then accept the review."""
    if actor != "user":
        raise ValueError(USER_ONLY)
    task = find_task(org, params.get("taskId"))
    if task["revision"] != params.get("expectedRevision"):
        raise OrganizationConflict("Task changed. Read it before updating.")
    if task["claim"] or task["status"] not in {"in_review", "done"}:
        raise ValueError("Only finished work waiting for review, or accepted work, can land")
    reviewed = params.get("patchRevision")
    if not isinstance(reviewed, str) or not reviewed:
        raise ValueError("Open the ticket's patch before accepting it")
    source = ticket_source(org, task)
    if source is None or source.get("missing"):
        raise ValueError("This ticket has no branch to land")
    if not source.get("baseBranch"):
        raise ValueError(
            "Raticode doesn't know which branch this work came from, so it can't land it"
        )
    repository, branch, base_branch = source["repository"], source["branch"], source["baseBranch"]
    try:
        current = patch_view(source)
        if current["revision"] != reviewed:
            raise OrganizationConflict(
                "The ticket's changes moved since you opened them. Review them again."
            )
        tip = current["tip"]
        if current["uncommitted"] and source.get("checkout"):
            # The user reviewed these files; commit them on the branch so they land too.
            ticket_patch.commit_leftovers(source["checkout"], f"Organization: {task['title']}")
            tip = ticket_patch.branch_commit(repository, branch) or tip
        result = ticket_patch.land(
            repository,
            branch,
            base_branch,
            expected_tip=tip,
            message=f"Merge {branch}: {task['title']}",
        )
    except ticket_patch.LandConflict as exc:
        return {
            "landed": False,
            "conflict": True,
            "conflicts": exc.files,
            "branch": branch,
            "baseBranch": base_branch,
            "message": str(exc),
            "taskId": task["id"],
        }
    except ticket_patch.PatchError as exc:
        raise ValueError(str(exc)) from None
    record = {
        "branch": branch,
        "baseBranch": base_branch,
        "commit": result["commit"],
        "how": result["state"],
        "at": now(),
    }
    note = str(params.get("body") or "").strip()
    accepted = None
    if task["status"] == "in_review":
        # Same rules and record as Accept in review, now that the work is in its base.
        accepted = manager.call(
            project,
            "user",
            {
                "action": "review",
                "organizationId": org["id"],
                "params": {
                    "taskId": task["id"],
                    "expectedRevision": task["revision"],
                    "decision": "accept",
                    "body": note or "Accepted",
                },
            },
        )

    def mark(current_org: dict[str, Any]) -> dict[str, Any]:
        found = next(t for t in current_org["runtime"]["tasks"] if t["id"] == task["id"])
        found["landed"] = record
        found["comments"].append({"actor": "system", "at": now(), "body": landed_sentence(record)})
        found["revision"] += 1
        return {"taskId": task["id"], "landed": record}

    manager.store.mutate(project, org["id"], "user", "ticket_landed", mark)
    return {"landed": True, **record, "taskId": task["id"], "accepted": bool(accepted)}


def landed_sentence(record: dict[str, Any]) -> str:
    short = str(record.get("commit") or "")[:10]
    if record.get("how") == "landed":
        return f"{record['branch']} was already in {record['baseBranch']}."
    how = "fast-forwarded" if record.get("how") == "fast_forward" else "merged"
    return f"Landed {record['branch']} in {record['baseBranch']} ({how}, {short})."
