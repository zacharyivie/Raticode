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
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any


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
    git(root, "worktree", "add", "--detach", str(path), baseline)
    return {
        "mode": "git",
        "path": str(path),
        "baseRevision": baseline,
        "sourceRevision": head,
        "revision": baseline,
        "directory": str(directory),
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
    branch = f"taskurotta/swarm/{uuid.uuid4().hex}"
    git(Path(workspace["path"]), "worktree", "add", "-b", branch, str(path), base)
    return {"path": str(path), "branch": branch, "baseRevision": base}


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
    candidate = Path(workspace["directory"]) / f"integration-{uuid.uuid4().hex}"
    git(root, "worktree", "add", "--detach", str(candidate), before)
    outcome: dict[str, Any] = {
        "path": str(candidate),
        "before": before,
        "submittedRevision": revision,
        "passed": False,
    }
    try:
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
    return outcome
