"""Runs of a ``workspace: worktree`` workflow, each in its own git worktree.

Each run gets a worktree of the project's repository at the project's current commit, on
branch ``raticode/run/<workflow>-<run>``, in Raticode's data folder. The project's
approved ``.raticode/worktree-setup.json`` runs there (see ``gofer.ui.worktree_setup``),
and every node resolves its paths and working folder inside it. When the run ends,
Raticode commits whatever the nodes changed. A run that changed nothing removes its
worktree and branch. Otherwise both stay until the user lands the branch in the branch
the project was on (``gofer.ui.ticket_patch``: a fast-forward, or a merge checked for
conflicts first, refused when that branch's folder has uncommitted changes, never
pushed) or discards them.

A worktree folder that still exists is a run waiting for that decision. A workflow keeps
at most ``MAX_UNLANDED`` of them: past that, triggered runs are skipped and other runs
fail before any node runs.

Deleting the workflow removes its waiting worktrees, their branches and its folder
(``remove_workflow_worktrees``). At backend start, folders of workflows that are no
longer registered and that nothing touched for a day go too (``sweep_unregistered``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.core.commit_attribution import clean_commit_message
from gofer.rattish.project_paths import normalize_project_path
from gofer.utils.logging import get_logger
from gofer.utils.process import (
    NO_REPOSITORY_PROGRAMS,
    build_subprocess_env,
    untrusted_repository_git_env,
)

log = get_logger(__name__)

FOLDER = "rattish-worktrees"
BRANCH_PREFIX = "raticode/run/"
MAX_UNLANDED = 10
GIT_TIMEOUT_SECONDS = 120
# The startup sweep leaves an unregistered workflow's folder alone until it is this old.
STALE_FOLDER_SECONDS = 24 * 60 * 60
# Run documents store one of these under worktree.state.
STATES = ("active", "pending", "no_changes", "landed", "discarded")
# Who gets the commit when the repository names nobody.
FALLBACK_IDENTITY = ("Raticode", "raticode@localhost")

_record_lock = threading.Lock()


class RunWorktreeError(RuntimeError):
    """The run's worktree couldn't be made, read, landed or removed."""


def uses_worktree(ir: Mapping[str, Any]) -> bool:
    return bool(ir.get("workflow", {}).get("workspace") == "worktree")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git(cwd: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        env = untrusted_repository_git_env(cwd, build_subprocess_env())
        result = subprocess.run(
            ["git", *NO_REPOSITORY_PROGRAMS, "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env={**env, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunWorktreeError(str(exc)) from exc
    if check and result.returncode:
        message = result.stderr.strip() or result.stdout.strip()
        raise RunWorktreeError(message or f"git {args[0]} failed")
    return result


def _out(cwd: Path | str, *args: str) -> str:
    return _git(cwd, *args).stdout.strip()


# Where worktrees live and how many wait ----------------------------------------------


def _slug(text: str, limit: int = 60) -> str:
    """A folder and branch name part. Run IDs keep their case, so a folder names its run."""
    text = re.sub(r"[^A-Za-z0-9._-]+|\.{2,}", "-", text).removesuffix(".lock")
    return text.strip("-.")[:limit].strip("-.") or "run"


def worktrees_dir(data_dir: Path, workflow_id: str) -> Path:
    return data_dir / FOLDER / _slug(workflow_id, 120)


def branch_name(workflow_id: str, run_id: str) -> str:
    return f"{BRANCH_PREFIX}{_slug(workflow_id)}-{_slug(run_id, 80)}"


def unlanded_run_ids(data_dir: Path, workflow_id: str) -> list[str]:
    """Runs of this workflow whose worktree is still waiting to be landed or discarded."""
    folder = worktrees_dir(data_dir, workflow_id)
    try:
        return sorted(item.name for item in folder.iterdir() if item.is_dir())
    except OSError:
        return []


def limit_message(data_dir: Path, workflow_id: str) -> str | None:
    """Why a new run can't get a worktree, or None while there is room."""
    waiting = len(unlanded_run_ids(data_dir, workflow_id))
    if waiting < MAX_UNLANDED:
        return None
    return (
        f"This workflow already has {waiting} run worktrees waiting to be landed or "
        f"discarded, the most it keeps. Land or discard some in a run's Changes tab, or "
        "with `Raticode rattish runs land RUN_ID` or `discard RUN_ID`."
    )


# One run's worktree ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunWorktree:
    repository: Path  # The project's repository folder (git top level).
    checkout: Path  # The worktree's top folder.
    root: Path  # The project folder inside the worktree.
    project_root: Path  # The project folder the run came from.
    branch: str
    base_commit: str
    base_branch: str | None

    def document(self, state: str = "active") -> dict[str, Any]:
        return {
            "state": state,
            "branch": self.branch,
            "base_commit": self.base_commit,
            "base_branch": self.base_branch,
            "repository": str(self.repository),
            "checkout": str(self.checkout),
            "root": str(self.root),
            "project_root": str(self.project_root),
            "commit": None,
            "files_changed": 0,
            "setup": None,
            "message": "",
        }

    @classmethod
    def from_document(cls, record: Mapping[str, Any]) -> RunWorktree:
        return cls(
            repository=Path(str(record["repository"])),
            checkout=Path(str(record["checkout"])),
            root=Path(str(record["root"])),
            project_root=Path(str(record["project_root"])),
            branch=str(record["branch"]),
            base_commit=str(record["base_commit"]),
            base_branch=record.get("base_branch") or None,
        )


def plan(data_dir: Path, project_root: Path, workflow_id: str, run_id: str) -> RunWorktree:
    """Where this run's worktree goes. Raises when the project isn't in a git repository."""
    project = project_root.resolve()
    probe = _git(project, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode or not probe.stdout.strip():
        raise RunWorktreeError(
            f"The project folder {project} isn't in a git repository, so this run can't get "
            "a worktree. Run `git init` and commit the project, or set workspace: project."
        )
    top = Path(probe.stdout.strip()).resolve()
    head = _git(project, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False)
    if head.returncode or not head.stdout.strip():
        raise RunWorktreeError(
            f"The repository at {top} has no commits yet, so there is nothing to make a "
            "worktree from. Commit the project first."
        )
    branch = _git(project, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    checkout = worktrees_dir(data_dir.resolve(), workflow_id) / _slug(run_id, 120)
    return RunWorktree(
        repository=top,
        checkout=checkout,
        root=checkout / project.relative_to(top),
        project_root=project,
        branch=branch_name(workflow_id, run_id),
        base_commit=head.stdout.strip(),
        base_branch=branch or None,
    )


def create(worktree: RunWorktree) -> None:
    """Add the worktree on its new branch at the project's current commit."""
    if worktree.checkout.exists():
        raise RunWorktreeError(f"{worktree.checkout} already exists.")
    worktree.checkout.parent.mkdir(parents=True, exist_ok=True)
    _git(worktree.repository, "worktree", "prune", check=False)
    _git(
        worktree.repository,
        "worktree",
        "add",
        "-q",
        "-b",
        worktree.branch,
        str(worktree.checkout),
        worktree.base_commit,
    )


def prepare_setup(
    data_dir: Path, worktree: RunWorktree, cancel: threading.Event | None = None
) -> dict[str, Any] | None:
    """Run the project's approved worktree setup; None when the project has no setup file."""
    from gofer.ui import worktree_setup

    return worktree_setup.prepare_quietly(
        data_dir,
        source=worktree.project_root,
        worktree=worktree.root,
        surface="workflow",
        cancel=cancel,
    )


def working_dirs(ir: Mapping[str, Any], worktree: RunWorktree) -> dict[str, str]:
    """Nodes whose authored working folder is an absolute path inside the project's
    repository, moved to the same place in the worktree. Relative ones follow the new
    project root on their own."""
    moved: dict[str, str] = {}
    for node in ir["nodes"]:
        value = node.get("configuration", {}).get("working_dir")
        if not isinstance(value, str) or not value:
            continue
        try:
            path = Path(normalize_project_path(value)).expanduser()
        except ValueError:
            continue
        if not path.is_absolute():
            continue
        resolved = path.resolve()
        if resolved.is_relative_to(worktree.repository):
            moved[node["id"]] = str(worktree.checkout / resolved.relative_to(worktree.repository))
    return moved


def relocate(ir: Any, worktree: RunWorktree) -> Any:
    """The run's IR with every node working inside the worktree."""
    from gofer.rattish.ir_validation import relocated_ir

    return relocated_ir(ir, str(worktree.root), working_dirs(ir, worktree))


def approved_in_worktree(files: Any, record: Mapping[str, Any]) -> Any:
    """A triggered run's approved files, also covering their copies in the run's worktree.

    The approval names files in the project folder, but the run's steps read the
    worktree's copies, which hold the committed text. Each covered file inside the
    repository gets the same approved digest at its worktree path, so a copy that differs
    from what the user approved fails its step instead of running unchecked.
    """
    import dataclasses

    worktree = RunWorktree.from_document(record)
    digests = dict(files.digests)
    for key, digest in files.digests.items():
        try:
            path = Path(key).resolve()
        except (OSError, RuntimeError):
            continue
        if path.is_relative_to(worktree.repository):
            digests[str(worktree.checkout / path.relative_to(worktree.repository))] = digest
    return dataclasses.replace(files, digests=digests)


def _uncommitted(checkout: Path) -> bool:
    status = _git(checkout, "status", "--porcelain", "--untracked-files=all", check=False)
    return bool(status.stdout.strip())


def _identity(checkout: Path) -> list[str]:
    """``-c`` settings that name a committer when the repository and user name nobody."""
    email = _git(checkout, "config", "user.email", check=False).stdout.strip()
    name = _git(checkout, "config", "user.name", check=False).stdout.strip()
    if email and name:
        return []
    return ["-c", f"user.name={name or FALLBACK_IDENTITY[0]}"] + [
        "-c",
        f"user.email={email or FALLBACK_IDENTITY[1]}",
    ]


def commit_changes(checkout: Path, message: str, *, branch: str) -> bool:
    """Commit everything the run changed on ``branch``. True when it made a commit.

    A step may rewrite its worktree's HEAD file, which the sandbox leaves writable, to
    name another branch such as ``main``. This commit runs outside the sandbox, so it
    refuses with RunWorktreeError unless HEAD still names the run's own branch.
    """
    if not checkout.is_dir() or not _uncommitted(checkout):
        return False
    head = _git(checkout, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip()
    if head != f"refs/heads/{branch}":
        named = head.removeprefix("refs/heads/") if head else "no branch"
        raise RunWorktreeError(
            f"the worktree is on {named}, not the run's branch {branch}, so Raticode won't "
            "commit there"
        )
    _git(checkout, "add", "-A")
    _git(
        checkout,
        *_identity(checkout),
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--no-verify",
        "-q",
        "-m",
        clean_commit_message(message[:2000]),
    )
    return True


def remove(worktree: RunWorktree, *, delete_branch: bool = True) -> None:
    """Remove the worktree folder and, unless told otherwise, its branch."""
    if worktree.checkout.exists():
        removed = _git(
            worktree.repository,
            "worktree",
            "remove",
            "--force",
            str(worktree.checkout),
            check=False,
        )
        if removed.returncode and worktree.checkout.exists():
            shutil.rmtree(worktree.checkout, ignore_errors=True)
    _git(worktree.repository, "worktree", "prune", check=False)
    if worktree.checkout.exists():
        raise RunWorktreeError(f"Couldn't remove the worktree at {worktree.checkout}.")
    if delete_branch and _branch_tip(worktree) is not None:
        _git(worktree.repository, "branch", "-D", "-q", worktree.branch)


def _branch_tip(worktree: RunWorktree) -> str | None:
    found = _git(
        worktree.repository,
        "rev-parse",
        "--verify",
        "-q",
        f"refs/heads/{worktree.branch}^{{commit}}",
        check=False,
    )
    return found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else None


def finish(record: dict[str, Any], message: str) -> dict[str, Any]:
    """Commit the run's changes when it ends; clean up when there are none.

    Returns the run document's updated ``worktree`` record. Never raises: a failure to
    commit keeps the worktree as it is, with the reason in ``message``.
    """
    worktree = RunWorktree.from_document(record)
    result = dict(record)
    try:
        commit_changes(worktree.checkout, message, branch=worktree.branch)
    except RunWorktreeError as exc:
        result.update(
            state="pending",
            message=f"Raticode couldn't commit the run's changes ({exc}). They are still in "
            f"the worktree at {worktree.checkout}.",
        )
        return result
    try:
        tip = _branch_tip(worktree)
        changed = (
            _out(worktree.repository, "diff", "--name-only", worktree.base_commit, tip, "--")
            if tip
            else ""
        )
    except RunWorktreeError as exc:
        result.update(state="pending", message=f"Raticode couldn't read the run's changes: {exc}")
        return result
    files = [line for line in changed.splitlines() if line]
    if not files:
        # Commits that add up to no change land nothing either.
        try:
            remove(worktree)
        except RunWorktreeError as exc:
            result.update(state="no_changes", message=str(exc))
            return result
        result.update(state="no_changes", commit=None, files_changed=0)
        return result
    result.update(state="pending", commit=tip, files_changed=len(files))
    return result


# Changes, land and discard, from a run record ---------------------------------------


def _effective_state(document: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    from gofer.rattish.run_service import effective_run_status

    state = str(record.get("state") or "")
    if state == "active" and effective_run_status(document) != "running":
        # The runner stopped before it could commit: the changes still wait.
        return "pending"
    return state


def worktree_summary(document: Mapping[str, Any]) -> dict[str, Any] | None:
    """What a client shows about a run's worktree, or None for a run without one."""
    record = document.get("worktree")
    if not isinstance(record, Mapping):
        return None
    return {
        "runId": document.get("run_id"),
        "state": _effective_state(document, record),
        "branch": record.get("branch"),
        "baseBranch": record.get("base_branch"),
        "baseCommit": record.get("base_commit"),
        "repository": record.get("repository"),
        "checkout": record.get("checkout"),
        "commit": record.get("commit"),
        "filesChanged": record.get("files_changed", 0),
        "setup": record.get("setup"),
        "message": record.get("message") or "",
        "landedCommit": record.get("landed_commit"),
        "decidedAt": record.get("decided_at"),
    }


def _require(document: Mapping[str, Any]) -> dict[str, Any]:
    record = document.get("worktree")
    if not isinstance(record, Mapping):
        raise RunWorktreeError(
            f"Run {document.get('run_id')} didn't run in a worktree, so it has no changes "
            "to land or discard."
        )
    return dict(record)


def _refuse_while_running(document: Mapping[str, Any], what: str) -> None:
    from gofer.rattish.run_service import effective_run_status

    if effective_run_status(document) == "running":
        raise RunWorktreeError(f"Run {document.get('run_id')} is still running. {what}")


def changes(document: Mapping[str, Any]) -> dict[str, Any]:
    """The run's patch in the commit diff view's format, with whether it lands cleanly."""
    from gofer.ui import ticket_patch

    record = _require(document)
    summary = worktree_summary(document)
    assert summary is not None
    payload: dict[str, Any] = {"worktree": summary, "files": [], "branch": None}
    if summary["state"] not in {"pending", "active"}:
        return payload
    try:
        patch = ticket_patch.read_patch(
            str(record["repository"]),
            str(record["branch"]),
            base_branch=record.get("base_branch") or None,
            base_commit=str(record["base_commit"]),
            checkout=str(record["checkout"]),
        )
    except ticket_patch.PatchError as exc:
        raise RunWorktreeError(str(exc)) from exc
    payload.update(patch)
    payload["repository"] = str(record["repository"])
    if record.get("base_branch") and summary["state"] == "pending":
        try:
            merge = ticket_patch.merge_state(
                str(record["repository"]), str(record["base_branch"]), patch["tip"]
            )
            payload["merge"] = {
                "state": merge["state"],
                "conflicts": merge.get("conflicts", []),
                "baseTip": merge.get("baseTip"),
            }
        except ticket_patch.PatchError as exc:
            payload["merge"] = {"state": "unknown", "conflicts": [], "error": str(exc)}
    return payload


def land(
    document: Mapping[str, Any],
    save: Callable[[dict[str, Any]], None],
    *,
    expected_tip: str | None = None,
) -> dict[str, Any]:
    """Land the run's branch in the branch the project was on, then remove the worktree.

    ``save`` stores the run's updated worktree record. Raises ``RunWorktreeError`` when
    the run has no pending changes, and ``ticket_patch.LandConflict`` (a PatchError) when
    the branches conflict; nothing changes then.
    """
    from gofer.ui import ticket_patch

    record = _require(document)
    _refuse_while_running(document, "Land its changes once it ends.")
    state = _effective_state(document, record)
    if state != "pending":
        raise RunWorktreeError(_not_pending(document, state))
    worktree = RunWorktree.from_document(record)
    if not worktree.base_branch:
        raise RunWorktreeError(
            f"The project wasn't on a branch when run {document.get('run_id')} started, so "
            f"Raticode doesn't know where to land {worktree.branch}. Merge it yourself, then "
            "discard the run's worktree."
        )
    name = str(document.get("workflow", {}).get("name") or "workflow")
    try:
        ticket_patch.commit_leftovers(
            str(worktree.checkout), f"Raticode run of {name}", branch=worktree.branch
        )
    except ticket_patch.PatchError as exc:
        raise RunWorktreeError(f"Couldn't commit the run's remaining changes: {exc}") from exc
    tip = _branch_tip(worktree)
    if tip is None:
        raise RunWorktreeError(f"Branch {worktree.branch} no longer exists.")
    result = ticket_patch.land(
        str(worktree.repository),
        worktree.branch,
        worktree.base_branch,
        expected_tip=expected_tip or tip,
        message=f"Land Raticode run of {name} ({document.get('run_id')})\n\n"
        f"Merge {worktree.branch} into {worktree.base_branch}.",
    )
    record.update(state="landed", landed_commit=result["commit"], decided_at=_now(), message="")
    try:
        remove(worktree)
    except RunWorktreeError as exc:
        record["message"] = f"Landed, but {exc} Remove it yourself."
    save(record)
    return {
        "landed": True,
        "how": result["state"],
        "commit": result["commit"],
        "branch": worktree.branch,
        "baseBranch": worktree.base_branch,
        "worktree": worktree_summary({**document, "worktree": record}),
    }


def discard(document: Mapping[str, Any], save: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Remove the run's worktree and branch, dropping its changes."""
    record = _require(document)
    _refuse_while_running(document, "Stop it first, or discard its changes once it ends.")
    state = _effective_state(document, record)
    if state not in {"pending", "active"}:
        raise RunWorktreeError(_not_pending(document, state))
    worktree = RunWorktree.from_document(record)
    remove(worktree)
    record.update(state="discarded", decided_at=_now(), message="")
    save(record)
    return {
        "discarded": True,
        "branch": worktree.branch,
        "worktree": worktree_summary({**document, "worktree": record}),
    }


def _not_pending(document: Mapping[str, Any], state: str) -> str:
    run_id = document.get("run_id")
    return {
        "landed": f"Run {run_id}'s changes were already landed.",
        "discarded": f"Run {run_id}'s changes were already discarded.",
        "no_changes": f"Run {run_id} changed nothing, so its worktree was already removed.",
    }.get(state, f"Run {run_id} has no changes waiting.")


# A deleted workflow's worktrees --------------------------------------------------------


def _from_folder(checkout: Path, workflow_folder: str) -> RunWorktree:
    """A run worktree described by git and its folder names, for a run with no record.

    Only the branch this workflow and run would have made is deleted, whatever the
    worktree has checked out now. A folder git no longer knows is just removed.
    """
    common = ""
    if (checkout / ".git").exists():
        found = _git(
            checkout, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False
        )
        common = found.stdout.strip() if found.returncode == 0 else ""
    if common:
        repository = Path(common)
        if repository.name == ".git":
            repository = repository.parent
    else:
        repository = checkout
    return RunWorktree(
        repository=repository,
        checkout=checkout,
        root=checkout,
        project_root=repository,
        branch=f"{BRANCH_PREFIX}{_slug(workflow_folder)}-{_slug(checkout.name, 80)}",
        base_commit="",
        base_branch=None,
    )


def _remove_any(worktree: RunWorktree, *, known_repository: bool) -> None:
    if known_repository:
        remove(worktree)
        return
    # Git couldn't name the repository, so there is no worktree or branch to unregister.
    shutil.rmtree(worktree.checkout, ignore_errors=True)
    if worktree.checkout.exists():
        raise RunWorktreeError(f"Couldn't remove the worktree at {worktree.checkout}.")


def remove_workflow_worktrees(
    data_dir: Path,
    workflow_id: str,
    records: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[str], list[str]]:
    """Remove every run worktree of a workflow, each with its branch, then the folder.

    ``records`` are the ``worktree`` records of the workflow's runs. A folder with a
    record is removed through it; one without is read from git. A pending record whose
    folder is already gone still has its branch deleted. Returns the run folders removed
    and a message for each that couldn't be. Never raises.
    """
    return _remove_folder(worktrees_dir(data_dir, workflow_id), records)


def _resolved(path: Path | str) -> str:
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError):
        return os.path.normpath(str(path))


def _remove_folder(
    folder: Path, records: Iterable[Mapping[str, Any]] = ()
) -> tuple[list[str], list[str]]:
    by_checkout: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if isinstance(record, Mapping) and record.get("checkout"):
            by_checkout[_resolved(str(record["checkout"]))] = record
    removed: list[str] = []
    errors: list[str] = []
    try:
        runs = sorted(item.name for item in folder.iterdir() if item.is_dir())
    except OSError:
        runs = []
    for run_id in runs:
        checkout = folder / run_id
        known = by_checkout.pop(_resolved(checkout), None)
        try:
            if known is not None:
                remove(RunWorktree.from_document(known))
            else:
                worktree = _from_folder(checkout, folder.name)
                _remove_any(worktree, known_repository=worktree.repository != checkout)
        except (RunWorktreeError, KeyError) as exc:
            errors.append(f"{run_id}: {exc}")
            continue
        removed.append(run_id)
    for left in by_checkout.values():
        if left.get("state") not in {"active", "pending"}:
            continue
        try:
            remove(RunWorktree.from_document(left))
        except (RunWorktreeError, KeyError) as exc:
            errors.append(f"{left.get('branch')}: {exc}")
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        errors.append(f"Couldn't remove {folder}.")
    return removed, errors


def _newest_change(folder: Path) -> float:
    """When the folder or a run folder in it last changed."""
    newest = folder.lstat().st_mtime
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    return newest


def _has_unregistered_runs(data_dir: Path, folder_name: str) -> bool:
    """Whether a workflow file that was never registered keeps run records here.

    Such runs can still be landed with ``--workflow``, so their worktrees stay.
    """
    runs = data_dir / "radish" / "runs" / folder_name
    try:
        return runs.is_dir() and any(runs.glob("*.json"))
    except OSError:
        return True


def sweep_unregistered(
    data_dir: Path,
    registered_ids: Iterable[str],
    *,
    now: float | None = None,
    min_age_seconds: float = STALE_FOLDER_SECONDS,
) -> list[Path]:
    """Remove worktree folders of workflows that are no longer registered.

    A folder is left alone until nothing in it has changed for ``min_age_seconds``, and
    while a workflow file that was never registered still keeps run records for it. Each
    run worktree goes with its branch, read from git. Returns the folders removed.
    """
    root = data_dir / FOLDER
    if root.is_symlink() or not root.is_dir():
        return []
    keep = {_slug(workflow_id, 120).lower() for workflow_id in registered_ids}
    current = time.time() if now is None else now
    removed: list[Path] = []
    try:
        with os.scandir(root) as entries:
            folders = sorted(
                Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)
            )
    except OSError:
        return []
    for folder in folders:
        if folder.name.lower() in keep or _has_unregistered_runs(data_dir, folder.name):
            continue
        try:
            if current - _newest_change(folder) < min_age_seconds:
                continue
        except OSError:
            continue
        runs, errors = _remove_folder(folder)
        for error in errors:
            log.warning("Couldn't remove a deleted workflow's run worktree: %s", error)
        if not folder.exists():
            removed.append(folder)
            log.info(
                "Removed run worktrees of unregistered workflow %s: %s",
                folder.name,
                ", ".join(runs) if runs else "an empty folder",
            )
    return removed


def sweep_at_start(data_dir: Path) -> list[Path]:
    """The backend's startup sweep. Skipped when the registry can't be read."""
    from gofer.rattish.workspaces import list_registered_workflows

    try:
        registered = [item.workflow_id for item in list_registered_workflows(registry_dir=data_dir)]
    except Exception:  # noqa: BLE001 - an unreadable registry must not delete anything
        log.warning("Skipped the run worktree sweep: the workflow registry couldn't be read")
        return []
    return sweep_unregistered(data_dir, registered)


def update_record(path: Path, record: dict[str, Any]) -> None:
    """Save a run's new worktree record into its run document."""
    import json

    from gofer.rattish.run_service import write_run_document

    with _record_lock:
        document = json.loads(path.read_text(encoding="utf-8"))
        document["worktree"] = record
        write_run_document(path, document)
