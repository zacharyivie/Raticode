"""Review and land a board ticket's work: its patch, the user's Accept, and change requests.

For an organization ticket every step goes through the organization (``ticket_patch``,
``land``, ``review``), so its own rules apply. For a Rem ticket the work is on a branch
its last run recorded: Rem's temporary worktree branch, or the feature branches a worktree
skill had the worker create. Accept is the user's alone, never Rem's or self-work's, and
a request for changes goes back to the same thread as a follow-up.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from gofer.ui import organization_landing, ticket_patch
from gofer.ui.rem_home_landing import LEGACY_LANDING_BRANCH

ONLY_YOU = "Only you can accept and land a ticket's work."


class ReviewRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def run_sources(run: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The branches a Rem run left its work on, each with the branch it should land in."""
    if not run:
        return []
    project = str((run.get("recipe") or {}).get("project_dir") or "")
    sources: list[dict[str, Any]] = []
    temporary = run.get("worktree") or run.get("released_worktree")
    if temporary and temporary.get("branch"):
        repository = temporary.get("repository") or project
        base_branch = temporary.get("base_branch") or ""
        guessed = False
        if not base_branch and repository and Path(repository).is_dir():
            # Runs from before the base branch was recorded: the project's branch now,
            # when it still contains the commit the work started from.
            current = ticket_patch.current_branch(repository) or ""
            tip = ticket_patch.branch_commit(repository, current) if current else None
            base = temporary.get("base")
            if tip and base and ticket_patch.is_ancestor(repository, base, tip):
                base_branch, guessed = current, True
        sources.append(
            {
                "branch": temporary["branch"],
                "repository": repository,
                "baseBranch": base_branch or None,
                "baseBranchGuessed": guessed,
                "base": temporary.get("base"),
                "checkout": (run.get("worktree") or {}).get("checkout"),
            }
        )
    tips = run.get("branch_tips") or {}
    # The branch the run had to land on; runs from before it was a decision used dev.
    landing = run.get("landing_branch") or (
        LEGACY_LANDING_BRANCH if "landing_branch" not in run else None
    )
    for branch in run.get("work_branches") or []:
        if project and Path(project).is_dir() and ticket_patch.branch_commit(project, branch):
            sources.append(
                {
                    "branch": branch,
                    "repository": project,
                    "baseBranch": landing,
                    "baseBranchGuessed": False,
                    "base": tips.get(landing) if landing else None,
                    "checkout": None,
                }
            )
    return sources


def _pick(sources: list[dict[str, Any]], branch: Any) -> dict[str, Any]:
    if not sources:
        raise ReviewRefused("invalid", "This ticket's work isn't on a branch.")
    if branch in (None, ""):
        return sources[0]
    found = next((s for s in sources if s["branch"] == branch), None)
    if found is None:
        raise ReviewRefused("invalid", "That branch isn't this ticket's work.")
    return found


def read_rem_patch(run: dict[str, Any] | None, branch: Any = None) -> dict[str, Any]:
    sources = run_sources(run)
    if not sources:
        return {"branch": None, "files": [], "branches": []}
    source = _pick(sources, branch)
    try:
        view = organization_landing.patch_view(source)
    except ticket_patch.PatchError as exc:
        raise ReviewRefused("unavailable", str(exc)) from None
    return {**view, "branches": [s["branch"] for s in sources]}


def land_rem(run: dict[str, Any] | None, payload: dict[str, Any], title: str) -> dict[str, Any]:
    """Land a Rem ticket's branch if it's still exactly what the user reviewed."""
    source = _pick(run_sources(run), payload.get("branch"))
    if not source.get("baseBranch"):
        raise ReviewRefused(
            "invalid",
            "Raticode doesn't know which branch this work came from, so it can't land it.",
        )
    reviewed = payload.get("patch_revision")
    if not isinstance(reviewed, str) or not reviewed:
        raise ReviewRefused("invalid", "Open the ticket's changes before accepting them.")
    repository, branch = source["repository"], source["branch"]
    try:
        current = organization_landing.patch_view(source)
        if current["revision"] != reviewed:
            raise ReviewRefused(
                "conflict", "The ticket's changes moved since you opened them. Review them again."
            )
        tip = current["tip"]
        if current["uncommitted"] and source.get("checkout"):
            ticket_patch.commit_leftovers(source["checkout"], f"Rem: {title}", branch=branch)
            tip = ticket_patch.branch_commit(repository, branch) or tip
        result = ticket_patch.land(
            repository,
            branch,
            source["baseBranch"],
            expected_tip=tip,
            message=f"Merge {branch}: {title}",
        )
    except ticket_patch.LandConflict as exc:
        return {
            "landed": False,
            "conflict": True,
            "conflicts": exc.files,
            "branch": branch,
            "baseBranch": source["baseBranch"],
            "message": str(exc),
        }
    except ticket_patch.PatchError as exc:
        raise ReviewRefused("conflict", str(exc)) from None
    return {
        "landed": True,
        "branch": branch,
        "baseBranch": source["baseBranch"],
        "commit": result["commit"],
        "how": result["state"],
    }


def change_request(payload: dict[str, Any]) -> str:
    """The message the worker gets: the user's note and line comments, or a rebase request."""
    if payload.get("rebase"):
        rebase = payload["rebase"]
        if not isinstance(rebase, dict):
            raise ReviewRefused("invalid", "Say which branch to rebase.")
        conflicts = [str(p)[:500] for p in (rebase.get("conflicts") or [])][:200]
        message = ticket_patch.rebase_message(
            str(rebase.get("branch") or "")[:300],
            str(rebase.get("base_branch") or "")[:300],
            conflicts,
        )
        note = str(payload.get("note") or "").strip()
        return f"{message}\n\n{note}" if note else message
    try:
        return ticket_patch.review_message(str(payload.get("note") or ""), payload.get("comments"))
    except ValueError as exc:
        raise ReviewRefused("invalid", str(exc)) from None


def organization_request_changes(
    call: Callable[[str, dict[str, Any]], Any],
    task: dict[str, Any],
    revision: int,
    body: str,
    request_id: str,
) -> None:
    """Send an organization ticket back to its worker with the user's request."""
    status = task.get("status")
    if task.get("claim") or status == "in_progress":
        raise ReviewRefused("conflict", "This ticket is running. Wait for the run to finish.")
    if status == "in_review":
        call(
            "review",
            {
                "taskId": task["id"],
                "expectedRevision": revision,
                "decision": "revise",
                "body": body,
            },
        )
        return
    if status in {"done", "blocked", "cancelled"}:
        call("reopen", {"taskId": task["id"], "expectedRevision": revision, "status": "todo"})
    call("comment", {"taskId": task["id"], "body": body, "idempotencyKey": request_id})


def landed_note(result: dict[str, Any]) -> str:
    return organization_landing.landed_sentence(result)
