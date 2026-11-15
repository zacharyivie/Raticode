"""Find every workflow.rattish in a project folder with a bounded, pruned walk.

Opening a project lists the workflows in its .raticode folder and every other
workflow.rattish below it, such as rattish/starters/jev-demo/workflow.rattish. The walk
never follows links, skips dependency, build, cache, virtualenv, version control and
Raticode-managed folders, and stops at fixed depth and folder limits so opening a home
folder can't hang the app.

Scans are not cached. The walk runs only when a project is opened and on the desktop's
30-second discovery refresh, and takes about 16 ms on the Raticode repository; the workflow
list the app polls every few seconds reads the registry, not the folders. A cache would
hide a workflow file added a moment ago when someone presses Refresh.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath

from gofer.utils.brand_compat import LEGACY_WORKSPACE_DIRECTORIES
from gofer.utils.data_folders import is_managed_data_folder

logger = logging.getLogger(__name__)

WORKFLOW_ENTRYPOINT = "workflow.rattish"
MANAGED_WORKSPACE_DIRECTORY = ".raticode"

# Folder names skipped anywhere in a project, compared without case. They hold
# dependencies, build output, caches, version control, editor state, or Raticode's own
# copies of projects (git worktrees), never a project's own workflows.
SKIPPED_DIRECTORY_NAMES = frozenset(
    {
        # Version control
        ".git",
        ".hg",
        ".svn",
        # Raticode: the managed workspace is listed by its own rules, and these hold
        # whole worktree copies of projects.
        MANAGED_WORKSPACE_DIRECTORY,
        *(name.lower() for name in LEGACY_WORKSPACE_DIRECTORIES),
        ".worktrees",
        "rem-worktrees",
        "organization-worktrees",
        "organization-shared-worktrees",
        "organization-repositories",
        # Dependencies and virtual environments
        "node_modules",
        "bower_components",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        ".pnpm-store",
        ".yarn",
        "site-packages",
        "pods",
        # Build output
        "build",
        "dist",
        "out-tsc",
        ".gradle",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".vitepress",
        ".docusaurus",
        ".turbo",
        ".parcel-cache",
        ".angular",
        ".expo",
        ".terraform",
        "deriveddata",
        # Caches and editor state
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".hypothesis",
        ".cache",
        ".idea",
        ".vscode",
        ".direnv",
        "htmlcov",
        "coverage",
    }
)
# A folder holding one of these files is skipped: a virtual environment (pyvenv.cfg) or a
# cache that marks itself the standard way (CACHEDIR.TAG, used by Rust's target folder).
SKIP_MARKER_FILES = frozenset({"pyvenv.cfg", "CACHEDIR.TAG"})
# Raticode's data folder holds a registry at this path; its managed folders are skipped.
DATA_FOLDER_MARKER = ("radish", "workspace-registry.json")

MAX_DEPTH = 12
MAX_DIRECTORIES = 20_000
MAX_WORKFLOWS = 500


@dataclass(frozen=True, slots=True)
class ProjectScan:
    """What one walk found. limited is True when a depth, folder or workflow cap hit."""

    entrypoints: tuple[Path, ...]
    limited: bool
    directories: int


def is_skipped_directory_name(name: str) -> bool:
    return name.lower() in SKIPPED_DIRECTORY_NAMES


def path_is_in_skipped_directory(relative: PurePath) -> bool:
    """Whether any folder of a project-relative path is one the walk skips."""
    return any(is_skipped_directory_name(part) for part in relative.parts[:-1])


def scan_project_workflows(
    project_root: Path,
    *,
    data_dir: Path | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
) -> ProjectScan:
    """Every workflow.rattish below a resolved project folder, outside .raticode.

    project_root must already be resolved. Results are sorted by depth, then path, so the
    shallowest copy of a folder name gets the plain workflow ID.
    """
    started = time.perf_counter()
    scan = _walk(project_root, data_dir, check_cancelled)
    elapsed = time.perf_counter() - started
    if scan.limited:
        logger.warning(
            "Workflow discovery in %s stopped at its limit after %d folders "
            "(depth %d, %d folders, %d workflows)",
            project_root,
            scan.directories,
            MAX_DEPTH,
            MAX_DIRECTORIES,
            MAX_WORKFLOWS,
        )
    logger.debug(
        "Found %d workflows in %s across %d folders in %.1f ms",
        len(scan.entrypoints),
        project_root,
        scan.directories,
        elapsed * 1000,
    )
    return scan


def _walk(
    project_root: Path,
    data_dir: Path | None,
    check_cancelled: Callable[[], None],
) -> ProjectScan:
    root_text = str(project_root)
    root_depth = len(project_root.parts)
    data_dir_text = str(data_dir) if data_dir is not None else None
    found: list[Path] = []
    directories = 0
    limited = False
    # followlinks=False never enters a linked folder; links are also pruned below so a
    # link to a folder is not even listed. Unreadable folders are skipped silently.
    for directory, folders, files in os.walk(root_text, topdown=True, followlinks=False):
        check_cancelled()
        directories += 1
        current = Path(directory)
        is_root = directory == root_text
        if not is_root and (directory == data_dir_text or not SKIP_MARKER_FILES.isdisjoint(files)):
            folders[:] = []
            continue
        if not is_root and ".git" in files and _is_linked_worktree(current / ".git"):
            # Another checkout of a repository (git worktree add) nested in this one.
            folders[:] = []
            continue
        if WORKFLOW_ENTRYPOINT in files:
            entrypoint = current / WORKFLOW_ENTRYPOINT
            if not entrypoint.is_symlink() and entrypoint.is_file():
                found.append(entrypoint)
                if len(found) >= MAX_WORKFLOWS:
                    limited = True
                    break
        data_folder = "radish" in folders and current.joinpath(*DATA_FOLDER_MARKER).is_file()
        kept = []
        for name in folders:
            if is_skipped_directory_name(name) or (data_folder and is_managed_data_folder(name)):
                continue
            if (current / name).is_symlink():
                continue
            kept.append(name)
        depth = len(current.parts) - root_depth
        if kept and depth >= MAX_DEPTH:
            limited = True
            kept = []
        folders[:] = sorted(kept)
        if directories >= MAX_DIRECTORIES:
            limited = True
            break
    found.sort(key=lambda path: (len(path.parts), str(path)))
    return ProjectScan(tuple(found), limited, directories)


def _is_linked_worktree(git_file: Path) -> bool:
    """Whether a .git file points at a linked worktree's private git folder."""
    try:
        if git_file.is_symlink() or not git_file.is_file() or git_file.stat().st_size > 4096:
            return False
        content = git_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    first = content.strip().splitlines()[0] if content.strip() else ""
    if not first.lower().startswith("gitdir:"):
        return False
    target = first.split(":", 1)[1].strip().replace("\\", "/")
    return "/worktrees/" in target
