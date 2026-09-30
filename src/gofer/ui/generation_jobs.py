"""Durable Rem utility jobs, owned by the backend rather than a browser page."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gofer.core.provider_preferences import commit_message_preference
from gofer.ui.commit_message import generate_commit_message
from gofer.ui.report_theme_generation import generate_report_theme
from gofer.utils.atomic_output import atomic_binary_output, mkdir_without_links, open_binary_input
from gofer.utils.process import build_subprocess_env, run_subprocess


def _validate_stored_job(job: Any, path: Path) -> dict[str, Any]:
    """Reject incomplete records before recovery performs any I/O using their fields."""
    if not isinstance(job, dict):
        raise ValueError("Invalid generation job")
    job_id = job.get("id")
    created = job.get("createdAt")
    if (
        not isinstance(job_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id)
        or job_id != path.stem
        or job.get("kind") not in ("commit", "theme")
        or job.get("status")
        not in ("queued", "running", "completed", "needs_review", "interrupted", "failed")
        or type(created) not in (int, float)
        or (isinstance(created, float) and not math.isfinite(created))
        or ("result" in job and not isinstance(job["result"], dict))
    ):
        raise ValueError("Invalid generation job")
    if job["kind"] == "commit" and (
        any(
            not isinstance(job.get(key), str)
            for key in ("projectRoot", "branch", "ref", "head", "tree", "base")
        )
        or not Path(job["projectRoot"]).is_absolute()
        or not job["ref"].startswith("refs/heads/")
    ):
        raise ValueError("Invalid captured commit")
    if job.get("commitCandidate") and (
        job["kind"] != "commit"
        or not isinstance(job["commitCandidate"], str)
        or not isinstance(job.get("result"), dict)
    ):
        raise ValueError("Invalid commit recovery receipt")
    return job


def git(root: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        env=build_subprocess_env(),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git could not complete the operation.")
    return result.stdout.strip()


def commit_snapshot(root: Path) -> dict[str, str]:
    selected_root = root.resolve(strict=True)
    root = Path(git(selected_root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if root != selected_root:
        raise ValueError("Open the repository root to generate a commit for all staged changes.")
    branch = git(root, "symbolic-ref", "--quiet", "HEAD")
    try:
        head = git(root, "rev-parse", "--verify", "HEAD")
    except ValueError:
        head = ""
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        if Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", marker)).exists():
            raise ValueError("Finish the current Git operation before generating a commit.")
    tree = git(root, "write-tree")
    base = head or git(root, "hash-object", "-w", "-t", "tree", "--stdin", input_text="")
    if tree == git(root, "rev-parse", f"{base}^{{tree}}"):
        raise ValueError("Stage changes before generating a commit message.")
    current_head = git(root, "rev-parse", "--verify", "HEAD") if head else ""
    if git(root, "symbolic-ref", "--quiet", "HEAD") != branch or current_head != head:
        raise ValueError("The branch changed. Try generating again.")
    return {
        "projectRoot": str(root),
        "branch": branch.removeprefix("refs/heads/"),
        "ref": branch,
        "head": head,
        "tree": tree,
        "base": base,
    }


def commit_captured_changes(
    snapshot: dict[str, Any],
    message: str,
    prepare: Callable[[str], None],
) -> str:
    """Commit only the captured tree, with an atomic compare-and-swap of its branch."""
    root = Path(snapshot["projectRoot"])
    # Use a detached worktree so ordinary commit hooks and signing still run,
    # while the user's checkout and index remain untouched during generation.
    base = snapshot["head"] or git(
        root, "commit-tree", snapshot["base"], input_text="Temporary commit base\n"
    )
    with tempfile.TemporaryDirectory(prefix="raticode-commit-") as directory:
        worktree = Path(directory) / "worktree"
        git(root, "worktree", "add", "--detach", "--no-checkout", str(worktree), base)
        try:
            git(worktree, "read-tree", "--reset", "-u", snapshot["tree"])
            args = ["commit", "--file=-"]
            if not snapshot["head"]:
                args.append("--amend")
            git(worktree, *args, input_text=message + "\n")
            commit = git(worktree, "rev-parse", "HEAD")
            if git(worktree, "rev-parse", "HEAD^{tree}") != snapshot["tree"]:
                raise ValueError(
                    "A commit hook changed the staged snapshot. Review and commit manually."
                )
        finally:
            git(root, "worktree", "remove", "--force", str(worktree))
    prepare(commit)
    git(
        root,
        "update-ref",
        "-m",
        "commit: " + message.splitlines()[0],
        snapshot["ref"],
        commit,
        snapshot["head"] or "0" * len(commit),
    )
    return commit


class GenerationJobs:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir.resolve() / "generation-jobs"
        mkdir_without_links(self.path)
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.slots = threading.BoundedSemaphore(4)
        for path in self.path.glob("*.json"):
            try:
                with open_binary_input(path) as source:
                    job = _validate_stored_job(json.load(source), path)
                if job["status"] in {"queued", "running"}:
                    job.update(
                        status="interrupted",
                        error="Raticode stopped before this job finished. Generate again to retry.",
                    )
                    if job.get("commitCandidate"):
                        try:
                            if (
                                git(Path(job["projectRoot"]), "rev-parse", job["ref"])
                                == job["commitCandidate"]
                            ):
                                job.update(status="completed", error="")
                                job["result"]["commit"] = job["commitCandidate"]
                        except (ValueError, OSError, subprocess.SubprocessError):
                            pass
                    self._save(job)
                self.jobs[job["id"]] = job
            except (ValueError, KeyError, OSError):
                continue

    def close(self) -> None:
        self.stopped.set()

    def _check_running(self) -> None:
        if self.stopped.is_set():
            raise ValueError("Raticode stopped before this job finished. Generate again to retry.")

    def _prepare_commit(self, job_id: str, commit: str) -> None:
        self._check_running()
        self._update(job_id, commitCandidate=commit)

    def _save(self, job: dict[str, Any]) -> None:
        if not isinstance(job.get("id"), str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", job["id"]
        ):
            raise ValueError("Invalid generation job ID")
        target = self.path / f"{job['id']}.json"
        with atomic_binary_output(target) as output:
            output.write(json.dumps(job).encode("utf-8"))

    def _update(self, job_id: str, **patch: Any) -> None:
        with self.lock:
            self.jobs[job_id].update(copy.deepcopy(patch), updatedAt=time.time())
            self._save(self.jobs[job_id])

    def list(self, kind: str, root: str = "", branch: str = "") -> list[dict[str, Any]]:
        with self.lock:
            matches = [
                job
                for job in self.jobs.values()
                if job["kind"] == kind
                and (kind != "commit" or (job["projectRoot"] == root and job["branch"] == branch))
            ]
            latest = max(matches, key=lambda job: job["createdAt"], default=None)
            # Dismissing the latest result must not resurrect an older draft.
            return [copy.deepcopy(latest)] if latest and not latest.get("dismissed") else []

    def dismiss(self, job_id: str) -> None:
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("Job not found.")
            if self.jobs[job_id]["status"] in {"running", "queued"}:
                raise ValueError("This job is still running.")
            self._update(job_id, dismissed=True)

    def start(self, kind: str, body: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        self._check_running()
        if kind not in {"commit", "theme"}:
            raise ValueError("Unknown generation job.")
        snapshot: dict[str, Any] = {}
        if kind == "commit":
            if root is None:
                raise ValueError("Choose a project for this commit.")
            snapshot = commit_snapshot(root)
            if body.get("branch") is not None and body["branch"] != snapshot["branch"]:
                raise ValueError("The branch changed before generation started. Try again.")
        with self.lock:
            for existing in self.jobs.values():
                if existing["kind"] == kind and existing["status"] in {"running", "queued"}:
                    if kind == "theme" or (
                        existing["projectRoot"] == snapshot["projectRoot"]
                        and existing["branch"] == snapshot["branch"]
                    ):
                        return copy.deepcopy(existing)
            if sum(job["status"] in {"queued", "running"} for job in self.jobs.values()) >= 24:
                raise ValueError("Too many generation jobs. Wait for a job to finish.")
            job = {
                "id": uuid.uuid4().hex,
                "kind": kind,
                "status": "queued",
                "createdAt": time.time(),
                "description": str(body.get("description", "")),
                "progress": "",
                **snapshot,
                "autoCommit": kind == "commit"
                and commit_message_preference().get("autoCommit") is True,
            }
            self.jobs[job["id"]] = job
            self._save(job)
            threading.Thread(
                target=self._run, args=(job["id"], copy.deepcopy(body)), daemon=True
            ).start()
            return copy.deepcopy(job)

    def _run(self, job_id: str, body: dict[str, Any]) -> None:
        with self.slots:
            try:
                self._check_running()
                self._update(job_id, status="running")
                job = self.jobs[job_id]
                if job["kind"] == "theme":
                    result = asyncio.run(
                        generate_report_theme(
                            body,
                            lambda event: self._update(job_id, progress=event.get("text", "")),
                            cancel_event=self.stopped,
                        )
                    )
                else:
                    root = Path(job["projectRoot"])
                    # Immutable objects are independent of later checkouts or staging.
                    limit = 32 * 1024 * 1024
                    code, diff, stderr = asyncio.run(
                        run_subprocess(
                            [
                                "git",
                                "-c",
                                "core.fsmonitor=false",
                                "diff",
                                "--no-ext-diff",
                                "--no-textconv",
                                "--no-color",
                                job["base"],
                                job["tree"],
                                "--",
                            ],
                            cwd=root,
                            timeout=60,
                            max_output_bytes=limit,
                            cancel_event=self.stopped,
                        )
                    )
                    if code:
                        raise ValueError(stderr or "Could not read the captured staged changes.")
                    if diff.endswith(f"\n[subprocess output truncated at {limit} bytes]"):
                        raise ValueError(
                            "Staged changes exceed 32 MB. Split them into smaller commits."
                        )
                    # Large diffs use the same captured content, never the live index.
                    result = asyncio.run(self._generate_commit(body, diff))
                    self._update(job_id, result=result)
                    if job["autoCommit"]:
                        try:
                            self._check_running()
                            result["commit"] = commit_captured_changes(
                                job,
                                result["message"],
                                lambda commit: self._prepare_commit(job_id, commit),
                            )
                        except (ValueError, OSError, subprocess.SubprocessError) as exc:
                            self._update(
                                job_id,
                                status="needs_review",
                                error=f"Message ready. Auto-commit failed: {exc}",
                            )
                            return
                self._update(job_id, status="completed", result=result, progress="")
            except Exception as exc:
                self._update(
                    job_id,
                    status="interrupted" if self.stopped.is_set() else "failed",
                    error=str(exc) or "Generation failed.",
                )

    async def _generate_commit(self, body: dict[str, Any], diff: str) -> dict[str, str]:
        return await generate_commit_message(
            provider=str(body.get("provider", "codex")),
            model=str(body.get("model", "cli-default")),
            effort=body.get("effort"),
            permission_mode=body.get("permissionMode"),
            diff=diff,
            captured_diff=True,
            cancel_event=self.stopped,
        )
