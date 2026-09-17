"""Git workspaces and application-owned checks for swarm assignment attempts.

These directories isolate ordinary edits, not hostile processes. Nothing here
checks out, resets, stages, or merges into the user's working directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from gofer.utils.process import build_subprocess_env


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Taskurotta",
            "-c",
            "user.email=swarm@localhost",
            *args,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git operation failed")
    return result.stdout.strip()


def prepare_run(root: Path, directory: Path) -> dict[str, Any]:
    try:
        top = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    except (ValueError, FileNotFoundError):
        return {"mode": "serial", "path": str(root)}
    if top != root.resolve():
        raise ValueError("Start a Git swarm at the repository root")
    try:
        head = git(root, "rev-parse", "--verify", "HEAD")
    except ValueError:
        head = None
    directory.mkdir(parents=True, exist_ok=True)
    # Snapshot dirty tracked and untracked files with a private index. The
    # user's staging area and branch are untouched, including partial staging.
    with tempfile.TemporaryDirectory(prefix="swarm-index-") as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        git(root, "read-tree", head if head else "--empty", env=env)
        paths = ["."]
        if directory.is_relative_to(root):
            paths.append(f":(exclude){directory.parent.parent.relative_to(root)}")
        git(root, "add", "-A", "--", *paths, env=env)
        tree = git(root, "write-tree", env=env)
        baseline = head
        if head is None or tree != git(root, "rev-parse", f"{head}^{{tree}}"):
            parent = ["-p", head] if head else []
            baseline = git(root, "commit-tree", tree, *parent, "-m", "Swarm input snapshot")
    assert baseline is not None
    path = directory / "integration"
    branch = f"raticode/swarm/{directory.name}/parent"
    git(root, "worktree", "add", "-b", branch, str(path), baseline)
    return {
        "mode": "git",
        "path": str(path),
        "baseRevision": baseline,
        "sourceRevision": head,
        "revision": baseline,
        "directory": str(directory),
        "branch": branch,
    }


def prepare_attempt(workspace: dict[str, Any], attempt_id: str) -> dict[str, str]:
    if workspace["mode"] != "git":
        return {"path": workspace["path"]}
    base = git(Path(workspace["path"]), "rev-parse", "HEAD")
    path = Path(workspace["directory"]) / attempt_id
    if path.exists():
        if git(path, "rev-parse", "--git-common-dir") != git(
            Path(workspace["path"]), "rev-parse", "--git-common-dir"
        ):
            raise ValueError("Existing attempt workspace belongs to another repository")
        return {
            "path": str(path),
            "branch": git(path, "branch", "--show-current"),
            "baseRevision": git(path, "merge-base", base, "HEAD"),
        }
    branch = f"raticode/swarm/{Path(workspace['directory']).name}/attempt/{attempt_id}"
    git(Path(workspace["path"]), "worktree", "add", "-b", branch, str(path), base)
    return {"path": str(path), "branch": branch, "baseRevision": base}


def cleanup_completed_turn(workspace: dict[str, Any], attempt: dict[str, Any]) -> bool:
    """Remove an unused conversational checkout, preserving all authored work.

    Milestone checkouts remain available for verification and review. A plain
    completed turn with no new commits or files has nothing left to preserve.
    """
    if workspace.get("mode") != "git" or attempt.get("milestoneId"):
        return False
    checkout = attempt.get("workspace", {})
    if attempt.get("state") != "succeeded" or not checkout.get("baseRevision"):
        return False
    path = Path(checkout["path"])
    if path.parent != Path(workspace["directory"]) or path.name != attempt["id"]:
        return False
    try:
        if git(path, "rev-parse", "HEAD") != checkout["baseRevision"]:
            return False
        if git(path, "status", "--porcelain", "--ignored"):
            return False
        git(Path(workspace["path"]), "worktree", "remove", str(path))
        _delete_branch(Path(workspace["path"]), checkout.get("branch"), checkout["baseRevision"])
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return False
    return True


def cleanup_accepted_attempt(workspace: dict[str, Any], attempt: dict[str, Any]) -> bool:
    """Remove an accepted, integrated assignment checkout without losing files.

    The caller must validate and persist milestone acceptance while excluding
    active writers. Keep verification records and commits in the parent branch.
    Cleanup is best effort and must never invalidate successful acceptance.
    """
    if workspace.get("mode") != "git" or attempt.get("state") != "verified":
        return False
    checkout = attempt.get("workspace", {})
    result = attempt.get("result") or {}
    integration = attempt.get("integration") or {}
    revision = result.get("revision")
    if (
        not result.get("passed")
        or not revision
        or not integration.get("passed")
        or integration.get("submittedRevision") != revision
    ):
        return False
    try:
        path = Path(checkout["path"])
        directory = Path(workspace["directory"])
        root = Path(workspace["path"])
        if (
            not path.exists()
            or path.is_symlink()
            or path.parent.resolve() != directory.resolve()
            or path.name != attempt["id"]
            or path.resolve() == root.resolve()
        ):
            return False
        if git(path, "rev-parse", "HEAD") != revision:
            return False
        git(root, "merge-base", "--is-ancestor", revision, "HEAD")
        if git(path, "status", "--porcelain", "--untracked-files=all", "--ignored"):
            return False
        git(root, "worktree", "remove", str(path))
        _delete_branch(root, checkout.get("branch"), revision)
    except (KeyError, ValueError, OSError, subprocess.TimeoutExpired):
        return False
    return True


def _delete_branch(root: Path, branch: str | None, revision: str) -> None:
    if branch and branch.startswith(("raticode/swarm/", "taskurotta/swarm/")):
        # Compare-and-delete: never remove a ref that moved after inspection.
        git(root, "update-ref", "-d", f"refs/heads/{branch}", revision)


def cleanup_run(workspace: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain one parent checkout, archiving unfinished work before removing children.

    Run only after all providers and checks have drained. Archives are outside
    the parent checkout, so rejected work cannot change the verified result.
    A failed archive or Git operation prevents successful completion.
    """
    if workspace.get("mode") != "git":
        return {"passed": True, "removed": [], "archives": []}
    root = Path(workspace["path"]).resolve()
    directory = Path(workspace["directory"]).resolve()
    prefix = f"raticode/swarm/{directory.name}/"
    parent_branch = workspace.get("branch")
    if not parent_branch:
        parent_branch = prefix + "parent"
        if git(root, "branch", "--show-current") != parent_branch:
            git(root, "switch", "-c", parent_branch)
    if git(root, "branch", "--show-current") != parent_branch:
        raise ValueError("Swarm parent branch changed; cleanup refused")
    recorded = {a.get("workspace", {}).get("branch") for a in attempts}
    removed: list[str] = []
    archives: list[str] = []
    records = git(root, "worktree", "list", "--porcelain", "-z").split("\0\0")
    for record in records:
        fields = dict(line.split(" ", 1) for line in record.split("\0") if " " in line)
        if "worktree" not in fields:
            continue
        path = Path(fields["worktree"])
        if path.resolve() == root or path.parent.resolve() != directory:
            continue
        branch = fields.get("branch", "").removeprefix("refs/heads/")
        if path.is_symlink() or (branch and not (branch.startswith(prefix) or branch in recorded)):
            raise ValueError(f"Unrecognized checkout in swarm directory: {path}")
        if (
            git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
            != git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
            or Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve()
        ):
            raise ValueError(f"Child checkout belongs to another repository: {path}")
        revision = git(path, "rev-parse", "HEAD")
        archive = directory / "retained" / path.name
        archive.mkdir(parents=True, exist_ok=True, mode=0o700)
        # HEAD includes unique commits, including detached conflict candidates.
        bundle = archive / "commits.bundle"
        git(path, "bundle", "create", str(bundle), "HEAD")
        git(path, "bundle", "verify", str(bundle))
        patch = subprocess.run(
            ["git", "diff", "--binary", "HEAD"], cwd=path, capture_output=True, check=True
        ).stdout
        (archive / "working.patch").write_bytes(patch)
        files = git(path, "ls-files", "-z", "--modified", "--others").split("\0")
        # Git collapses untracked nested repositories to a directory entry.
        # Preserve their contents and metadata without following symlinks.
        archive_files = set(files) - {""}
        for name in files:
            nested = path / name
            if name and nested.is_dir() and not nested.is_symlink():
                for parent, directories, names in os.walk(nested, followlinks=False):
                    archive_files.update(
                        str((Path(parent) / item).relative_to(path))
                        for item in directories + names
                    )

        def stamp(name: str) -> tuple[int, int, int, int, int]:
            value = (path / name).lstat()
            return (
                value.st_mode,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        stamps = {
            name: stamp(name)
            for name in sorted(archive_files)
            if name and ((path / name).exists() or (path / name).is_symlink())
        }
        # Preserve ignored files too. They may contain authored data, not just caches.
        with tarfile.open(archive / "files.tar.gz", "w:gz", dereference=False) as output:
            for name in stamps:
                output.add(path / name, arcname=name, recursive=False)
        (archive / "manifest.json").write_text(
            json.dumps({"path": str(path), "branch": branch, "revision": revision}),
            encoding="utf-8",
        )
        if (
            git(path, "rev-parse", "HEAD") != revision
            or git(path, "branch", "--show-current") != branch
            or git(path, "ls-files", "-z", "--modified", "--others").split("\0") != files
            or any(stamp(name) != before for name, before in stamps.items())
        ):
            raise ValueError("Child checkout changed during cleanup")
        git(root, "worktree", "remove", "--force", str(path))
        _delete_branch(root, branch, revision)
        removed.append(str(path))
        archives.append(str(archive))
    # Cover branches whose checkout was removed earlier, including crash recovery.
    for line in git(
        root, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"
    ).splitlines():
        branch, revision = line.split(" ")
        if branch == parent_branch or not (branch.startswith(prefix) or branch in recorded):
            continue
        if f"branch refs/heads/{branch}\0" in git(root, "worktree", "list", "--porcelain", "-z"):
            raise ValueError(f"Swarm branch is checked out outside its run directory: {branch}")
        archive = directory / "retained" / "branches"
        archive.mkdir(parents=True, exist_ok=True, mode=0o700)
        bundle = archive / f"{hashlib.sha256(branch.encode()).hexdigest()}.bundle"
        git(root, "bundle", "create", str(bundle), branch)
        git(root, "bundle", "verify", str(bundle))
        _delete_branch(root, branch, revision)
        archives.append(str(bundle))
    return {"passed": True, "branch": parent_branch, "removed": removed, "archives": archives}


def validate_checks(value: Any) -> list[list[str]]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("Checks must be a list of at most 20 command argument arrays")
    if any(
        not isinstance(argv, list)
        or not argv
        or len(argv) > 100
        or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)
        for argv in value
    ):
        raise ValueError("Each check must be a nonempty array of command arguments")
    return value


def artifacts(root: Path, paths: Any) -> list[dict[str, str]]:
    if not isinstance(paths, list) or len(paths) > 100:
        raise ValueError("Artifacts must be a list of at most 100 relative file paths")
    result = []
    for name in paths:
        if not isinstance(name, str) or Path(name).is_absolute():
            raise ValueError("Artifact paths must be relative to the attempt workspace")
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Artifact is missing or outside the workspace: {name}")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        result.append({"path": name, "sha256": digest})
    return result


def run_checks(
    root: Path, checks: list[list[str]], logs: Path, *, cancel: threading.Event | None = None
) -> list[dict[str, Any]]:
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    for argv in checks:
        log = logs / f"{uuid.uuid4().hex}.log"
        started = time.monotonic()
        code = -1
        with log.open("wb") as output:
            try:
                if cancel and cancel.is_set():
                    raise InterruptedError("Check cancelled before launch")
                process = subprocess.Popen(
                    argv,
                    cwd=root,
                    env=build_subprocess_env(),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 120
                    while process.poll() is None:
                        if (
                            (cancel and cancel.is_set())
                            or time.monotonic() >= deadline
                            or log.stat().st_size > 16 * 1024 * 1024
                        ):
                            raise subprocess.TimeoutExpired(argv, 120)
                        time.sleep(0.05)
                    code = process.returncode
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
                    if log.stat().st_size > 16 * 1024 * 1024:
                        output.seek(16 * 1024 * 1024)
                        output.truncate()
                    output.write(b"\nCheck cancelled or exceeded the 120s / 16MiB limit.\n")
            except OSError as exc:
                output.write(str(exc).encode())
        results.append(
            {
                "command": argv,
                "exitCode": code,
                "logPath": str(log),
                "durationSeconds": round(time.monotonic() - started, 3),
            }
        )
    return results


def verify(
    attempt: dict[str, Any],
    milestone: dict[str, Any],
    submission: dict[str, Any],
    logs: Path,
    *,
    is_git: bool,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    root = Path(attempt["workspace"]["path"])
    revision = None
    if is_git:
        revision = git(root, "rev-parse", "HEAD")
        if submission.get("revision") != revision or git(root, "status", "--porcelain"):
            raise ValueError("Submit the current committed revision from a clean attempt workspace")
        git(root, "merge-base", "--is-ancestor", attempt["workspace"]["baseRevision"], revision)
    references = artifacts(root, submission.get("artifacts", []))
    if not revision and not references:
        raise ValueError(
            "Submit artifact paths or a committed revision; evidence text is insufficient"
        )
    checks = validate_checks(milestone.get("checks", []))
    if not references and not checks:
        raise ValueError("Verification requires artifact files or executable checks")
    if is_git and revision != attempt["workspace"]["baseRevision"] and not checks:
        raise ValueError("Code changes require at least one application-run check")
    results = run_checks(root, checks, logs, cancel=cancel)
    unchanged = artifacts(root, [r["path"] for r in references]) == references
    if is_git:
        unchanged = (
            unchanged
            and git(root, "rev-parse", "HEAD") == revision
            and not git(root, "status", "--porcelain")
        )
    return {
        "attemptId": attempt["id"],
        "revision": revision,
        "artifacts": references,
        "checks": results,
        "criteria": criteria(milestone),
        "passed": bool(unchanged and all(r["exitCode"] == 0 for r in results)),
    }


def criteria(milestone: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                key: milestone.get(key)
                for key in (
                    "title",
                    "acceptanceCriteria",
                    "objectiveCriteria",
                    "checks",
                    "dependsOn",
                )
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def result_current(attempt: dict[str, Any], milestone: dict[str, Any], *, is_git: bool) -> bool:
    result = attempt.get("result") or {}
    if not result.get("passed") or result.get("criteria") != criteria(milestone):
        return False
    root = Path(attempt["workspace"]["path"])
    try:
        if artifacts(root, [a["path"] for a in result["artifacts"]]) != result["artifacts"]:
            return False
        if is_git and (
            git(root, "rev-parse", "HEAD") != result["revision"]
            or git(root, "status", "--porcelain")
        ):
            return False
    except (ValueError, OSError):
        return False
    return True


def _retry_candidate(
    workspace: dict[str, Any], attempt: dict[str, Any], before: str, revision: str
) -> Path | None:
    """Reuse only a recorded failed-check checkout whose inputs are unchanged."""
    previous = attempt.get("integration") or {}
    if (
        previous.get("passed") is not False
        or previous.get("before") != before
        or previous.get("submittedRevision") != revision
        or not previous.get("revision")
        or previous.get("conflicts")
        or previous.get("error")
        or not any(check.get("exitCode", 0) != 0 for check in previous.get("checks", []))
    ):
        return None
    try:
        candidate = Path(previous["path"])
        directory = Path(workspace["directory"]).resolve()
        root = Path(workspace["path"])
        suffix = candidate.name.removeprefix("integration-")
        if (
            candidate.is_symlink()
            or candidate.parent.resolve() != directory
            or candidate.name != f"integration-{suffix}"
            or len(suffix) != 32
            or any(char not in "0123456789abcdef" for char in suffix)
            or Path(git(candidate, "rev-parse", "--show-toplevel")).resolve() != candidate.resolve()
            or git(candidate, "rev-parse", "--path-format=absolute", "--git-common-dir")
            != git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
            or git(candidate, "rev-parse", "HEAD") != previous["revision"]
            or git(candidate, "status", "--porcelain", "--untracked-files=all")
        ):
            return None
        git(candidate, "merge-base", "--is-ancestor", before, "HEAD")
        git(candidate, "merge-base", "--is-ancestor", revision, "HEAD")
    except (KeyError, ValueError, OSError, subprocess.TimeoutExpired):
        return None
    return candidate


def integrate(
    workspace: dict[str, Any],
    attempt: dict[str, Any],
    checks: list[list[str]],
    logs: Path,
    *,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    root = Path(workspace["path"])
    if git(root, "status", "--porcelain"):
        raise ValueError("Integration workspace has local edits; commit or resolve them first")
    before = git(root, "rev-parse", "HEAD")
    revision = attempt["result"]["revision"]
    candidate = _retry_candidate(workspace, attempt, before, revision)
    reused = candidate is not None
    if candidate is None:
        candidate = Path(workspace["directory"]) / f"integration-{uuid.uuid4().hex}"
        git(root, "worktree", "add", "--detach", str(candidate), before)
    outcome: dict[str, Any] = {
        "path": str(candidate),
        "before": before,
        "submittedRevision": revision,
        "passed": False,
    }
    try:
        if not reused:
            git(candidate, "merge", "--no-ff", "--no-edit", revision)
    except ValueError as exc:
        outcome["error"] = str(exc)
        outcome["conflicts"] = git(candidate, "diff", "--name-only", "--diff-filter=U").splitlines()
        return outcome
    merged = git(candidate, "rev-parse", "HEAD")
    results = run_checks(candidate, checks, logs, cancel=cancel)
    outcome.update(revision=merged, checks=results)
    if (
        all(r["exitCode"] == 0 for r in results)
        and not git(candidate, "status", "--porcelain")
        and git(candidate, "rev-parse", "HEAD") == merged
    ):
        # Caller serializes integration. Recheck to avoid moving over lead edits.
        if git(root, "rev-parse", "HEAD") != before or git(root, "status", "--porcelain"):
            outcome["error"] = "Integration workspace changed during checks"
            return outcome
        if cancel and cancel.is_set():
            outcome["error"] = "Integration cancelled"
            return outcome
        git(root, "merge", "--ff-only", merged)
        outcome["passed"] = True
        # The merged revision and check logs are retained. The disposable
        # checkout is no longer needed; never force removal over local edits.
        try:
            if not git(candidate, "status", "--porcelain", "--ignored"):
                git(root, "worktree", "remove", str(candidate))
                outcome["cleanedUp"] = True
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            outcome["cleanupError"] = str(exc)
    return outcome
