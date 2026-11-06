"""Each organization's shared Git repository.

Raticode keeps one repository per organization under its data folder, outside every project.
It holds what the whole organization shares: knowledge entries as Markdown (written by
Raticode), decisions, notes and other files. A run gets its own worktree of it on a
``shared/`` branch; accepting the ticket merges that branch into ``main``. Nothing is pushed:
the repository has no remote, and git push is blocked for every command Raticode runs here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from gofer.utils.process import build_subprocess_env, git_push_blocked_environment

FOLDER = "organization-repositories"
WORKTREES = "organization-shared-worktrees"
MAIN = "main"
AUTHOR = ("Raticode", "raticode@localhost")
KNOWLEDGE_DIR = "knowledge"
README = """# {name}

This is {name}'s shared repository. Raticode keeps it for the whole organization, so every
project and worker can use the same notes, decisions and files.

- `knowledge/` mirrors the organization's knowledge entries. Raticode writes it; change notes
  in the Knowledge tab or with `knowledge_save`, not by editing these files.
- `decisions/` and `notes/` are for shared decisions and working notes.
- Anything else the organization needs to share can live here too.

Workers change this repository on their ticket's own `shared/` branch. The changes merge into
`main` when you accept the ticket.
"""

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class RepositoryError(ValueError):
    pass


def _lock(oid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(oid, threading.Lock())


def path_for(data_dir: Path, oid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{1,64}", oid):
        raise RepositoryError("Invalid organization")
    return data_dir / FOLDER / oid


def _env() -> dict[str, str]:
    env = build_subprocess_env()
    env.update(git_push_blocked_environment(env))
    env.update(
        GIT_AUTHOR_NAME=AUTHOR[0],
        GIT_AUTHOR_EMAIL=AUTHOR[1],
        GIT_COMMITTER_NAME=AUTHOR[0],
        GIT_COMMITTER_EMAIL=AUTHOR[1],
        GIT_TERMINAL_PROMPT="0",
    )
    return env


def git(cwd: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git with no hooks, no signing and push blocked."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(cwd),
            *args,
        ],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if check and result.returncode:
        raise RepositoryError(result.stderr.strip() or result.stdout.strip() or "git failed")
    return result


def exists(path: Path) -> bool:
    return (path / ".git").is_dir()


def ensure(data_dir: Path, org: dict[str, Any]) -> Path:
    """Create the organization's repository if it doesn't exist yet."""
    path = path_for(data_dir, org["id"])
    with _lock(org["id"]):
        if exists(path):
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        path.mkdir(mode=0o700, exist_ok=True)
        git(path, "init", "-q", "-b", MAIN)
        (path / "README.md").write_text(README.format(name=org["config"]["name"]))
        for folder in (KNOWLEDGE_DIR, "decisions", "notes"):
            (path / folder).mkdir(exist_ok=True)
            (path / folder / ".gitkeep").write_text("")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "Start the shared organization repository")
    return path


def commit_all(path: Path, message: str) -> str | None:
    git(path, "add", "-A")
    if git(path, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    git(path, "commit", "-q", "-m", message[:200])
    return git(path, "rev-parse", "HEAD").stdout.strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-") or "note"


def entry_file(entry: dict[str, Any]) -> str:
    return f"{KNOWLEDGE_DIR}/{entry['kind']}s/{_slug(entry['title'])}-{entry['id'][-6:]}.md"


def sync_entry(data_dir: Path, org: dict[str, Any], entry: dict[str, Any]) -> str | None:
    """Write one knowledge entry into the repository and commit it; archived ones are removed."""
    path = path_for(data_dir, org["id"])
    if not org["config"].get("sharedRepository") or not exists(path):
        return None
    with _lock(org["id"]):
        for old in (path / KNOWLEDGE_DIR).glob(f"*/*-{entry['id'][-6:]}.md"):
            old.unlink()
        if not entry.get("archived"):
            target = path / entry_file(entry)
            target.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "---",
                f"title: {entry['title']}",
                f"kind: {entry['kind']}",
                f"topic: {entry.get('topic') or ''}",
                f"project: {entry.get('projectId') or ''}",
                f"raticodeKnowledgeId: {entry['id']}",
                f"version: {entry['version']}",
                "---",
                "",
                f"# {entry['title']}",
                "",
                entry.get("summary") or "",
                "",
                entry.get("body") or "",
                "",
            ]
            target.write_text("\n".join(lines))
        verb = "Archive" if entry.get("archived") else "Save"
        return commit_all(path, f"{verb} {entry['kind']}: {entry['title']}")


def state(data_dir: Path, org: dict[str, Any], limit: int = 12) -> dict[str, Any]:
    """What the UI shows: where the repository is, recent commits and its top-level files."""
    path = path_for(data_dir, org["id"])
    enabled = bool(org["config"].get("sharedRepository"))
    if not exists(path):
        return {"enabled": enabled, "exists": False, "path": str(path)}
    log = git(path, "log", "--format=%H%x09%cI%x09%an%x09%s", "-n", str(limit), MAIN, check=False)
    commits = [line.split("\t", 3) for line in log.stdout.splitlines() if line.count("\t") == 3]
    files = sorted(
        (item.name + ("/" if item.is_dir() else ""))
        for item in path.iterdir()
        if not item.name.startswith(".git")
    )
    branches = git(path, "branch", "--format=%(refname:short)", check=False).stdout.split()
    return {
        "enabled": enabled,
        "exists": True,
        "path": str(path),
        "head": commits[0][0] if commits else None,
        "commits": commits,
        "files": files,
        "openBranches": [b for b in branches if b != MAIN],
        "count": int(git(path, "rev-list", "--count", MAIN, check=False).stdout.strip() or 0),
    }


def plan_worktree(data_dir: Path, org: dict[str, Any], task: dict[str, Any]) -> dict[str, str]:
    path = path_for(data_dir, org["id"])
    base = git(path, "rev-parse", MAIN).stdout.strip()
    checkout = data_dir / WORKTREES / org["id"] / task["id"]
    return {
        "checkout": str(checkout),
        "root": str(checkout),
        "repository": str(path),
        "branch": f"shared/{_slug(task['title'])[:40]}-{task['id'][:8]}",
        "base": base,
    }


def ensure_worktree(worktree: dict[str, str], oid: str) -> None:
    checkout = Path(worktree["checkout"])
    if checkout.exists():
        return
    checkout.parent.mkdir(parents=True, exist_ok=True)
    repository = worktree["repository"]
    with _lock(oid):
        git(repository, "worktree", "prune")
        if (
            git(
                repository,
                "rev-parse",
                "--verify",
                "-q",
                f"refs/heads/{worktree['branch']}",
                check=False,
            ).returncode
            == 0
        ):
            git(repository, "worktree", "add", str(checkout), worktree["branch"])
        else:
            git(
                repository,
                "worktree",
                "add",
                "-b",
                worktree["branch"],
                str(checkout),
                worktree["base"],
            )


def changes(worktree: dict[str, str]) -> dict[str, Any]:
    """Files a ticket's shared branch changed, committed or not."""
    repository, branch = worktree["repository"], worktree["branch"]
    if not exists(Path(repository)):
        return {"branch": branch, "files": [], "missing": True}
    files: dict[str, list[Any]] = {}
    diff = git(repository, "diff", "--numstat", f"{MAIN}...{branch}", check=False)
    for line in diff.stdout.splitlines()[:300]:
        parts = line.split("\t")
        if len(parts) == 3:
            files[parts[2]] = [parts[2], *(int(x) if x.isdigit() else 0 for x in parts[:2])]
    checkout = Path(worktree["checkout"])
    if checkout.exists():
        pending = git(checkout, "status", "--porcelain", check=False).stdout.splitlines()
        for line in pending[:300]:
            name = line[3:].strip()
            files.setdefault(name, [name, 0, 0])
    return {"branch": branch, "files": sorted(files.values()), "uncommitted": checkout.exists()}


def finish(worktree: dict[str, str], oid: str, title: str, merge: bool) -> str:
    """Commit what's left, merge into main when accepted, and remove the checkout.

    Returns a sentence for the ticket's conversation. A conflicting merge is aborted and the
    branch kept, so nothing is lost.
    """
    checkout, repository = Path(worktree["checkout"]), Path(worktree["repository"])
    branch = worktree["branch"]
    if not exists(repository):
        return ""
    with _lock(oid):
        if checkout.exists():
            if git(checkout, "status", "--porcelain", check=False).stdout.strip():
                git(checkout, "add", "-A")
                git(checkout, "commit", "-q", "-m", f"Shared changes from {title}"[:200])
            git(repository, "worktree", "remove", "--force", str(checkout), check=False)
            if checkout.exists():
                shutil.rmtree(checkout, ignore_errors=True)
            git(repository, "worktree", "prune", check=False)
        if git(
            repository, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False
        ).returncode:
            return ""
        ahead = git(repository, "rev-list", "--count", f"{MAIN}..{branch}", check=False)
        if ahead.stdout.strip() == "0":
            git(repository, "branch", "-D", branch, check=False)
            return ""
        if not merge:
            return (
                f"Shared repository changes stay on branch {branch}; they were not merged "
                "because the ticket wasn't accepted."
            )
        merged = git(
            repository,
            "merge",
            "--no-ff",
            "-q",
            "-m",
            f"Merge shared changes from {title}"[:200],
            branch,
            check=False,
        )
        if merged.returncode:
            git(repository, "merge", "--abort", check=False)
            return (
                f"Shared repository changes on branch {branch} conflict with main and were "
                "not merged. Resolve them in the shared repository."
            )
        git(repository, "branch", "-d", branch, check=False)
        return f"Merged this ticket's shared repository changes into main ({branch})."
