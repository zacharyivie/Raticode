"""Remove sandbox scratch folders that a stopped or crashed turn left behind.

Rem's sandboxed turns keep temp files and caches in ``rem-work/turn-scratch``
(named ``<run id>-<role>``) and Codex reviews in ``rem-work/review-scratch``
(named by turn ID). Sandboxed Rattish agent steps use ``run-scratch/<run id>``.
Each owner removes its folder when it finishes, but a process that is killed
can't. When the backend or the trigger scheduler starts, a folder is removed
only when nothing shows its turn or run is still going and nothing in it has
changed for a day, so a folder another live Raticode process uses is kept.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from gofer.utils.logging import get_logger

log = get_logger(__name__)

STALE_SECONDS = 24 * 60 * 60
TURN_SCRATCH = ("rem-work", "turn-scratch")
REVIEW_SCRATCH = ("rem-work", "review-scratch")
REM_STORE_FILE = "rem-home.sqlite3"
# rem_project_checks.BOX_PREFIX, kept here so this module needn't import Rem's code.
PROJECT_CHECK_PREFIX = "check-"


@dataclass(slots=True)
class CleanupResult:
    removed: int = 0
    freed_bytes: int = 0
    # Folders kept because their turn or run is going, or they changed recently.
    kept: int = 0


@dataclass(frozen=True, slots=True)
class _RemActivity:
    runs: frozenset[str]
    turns: frozenset[str]


def clean_stale_scratch(
    data_dir: Path, *, now: float | None = None, max_age: float = STALE_SECONDS
) -> CleanupResult:
    """Remove scratch folders of turns and runs that aren't going and are a day old."""
    clock = time.time() if now is None else now
    result = CleanupResult()
    rem_folders = [
        (folder, owner)
        for parts, owner in ((TURN_SCRATCH, "turn"), (REVIEW_SCRATCH, "review"))
        for folder in _folders(data_dir.joinpath(*parts))
    ]
    if rem_folders:
        rem = _rem_activity(data_dir)
        for folder, owner in rem_folders:
            if rem is None:
                # Rem's records can't be read, so no folder can be shown idle.
                result.kept += 1
                continue
            active = (
                folder.name.rpartition("-")[0] in rem.runs
                if owner == "turn"
                else folder.name in rem.turns
            )
            _settle(folder, active, clock, max_age, result)
    run_records: Callable[[str], bool | None] | None = None
    for folder in _folders(data_dir / "run-scratch"):
        if folder.name.startswith(PROJECT_CHECK_PREFIX):
            # A project check's own folder: its worktree is registered in a repository,
            # so rem_project_checks.sweep removes it, never this.
            continue
        if run_records is None:
            run_records = _workflow_run_activity(data_dir)
        # None: the run's record exists but can't be read, so keep the folder.
        _settle(folder, run_records(folder.name) is not False, clock, max_age, result)
    if result.removed:
        log.info(
            "Removed %d leftover sandbox scratch folder(s), freeing %s",
            result.removed,
            _size_text(result.freed_bytes),
        )
    return result


def start_scratch_cleanup(data_dir: Path) -> threading.Thread:
    """Run :func:`clean_stale_scratch` in the background, so startup doesn't wait."""

    def run() -> None:
        try:
            clean_stale_scratch(data_dir)
        except Exception:  # noqa: BLE001 - a clean-up failure must not stop startup
            log.warning("Couldn't clean up leftover sandbox scratch folders", exc_info=True)

    thread = threading.Thread(target=run, name="scratch-cleanup", daemon=True)
    thread.start()
    return thread


def _folders(root: Path) -> Iterator[Path]:
    try:
        if root.is_symlink() or not root.is_dir():
            return
        with os.scandir(root) as entries:
            found = [Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return
    yield from sorted(found)


def _settle(
    folder: Path, active: bool, clock: float, max_age: float, result: CleanupResult
) -> None:
    if active:
        result.kept += 1
        return
    newest, size = _tree_stats(folder)
    if clock - newest <= max_age:
        result.kept += 1
        return
    # rmtree removes links inside the folder without following them.
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        # Part of it couldn't be removed, such as a file the owner can't delete.
        log.warning("Couldn't fully remove the scratch folder %s", folder)
        result.freed_bytes += max(0, size - _tree_stats(folder)[1])
        return
    result.removed += 1
    result.freed_bytes += size


def _tree_stats(folder: Path) -> tuple[float, int]:
    """The newest change time inside a folder and the disk space it uses."""
    newest = 0.0
    size = 0
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            info = current.lstat()
        except OSError:
            continue
        newest = max(newest, info.st_mtime)
        size += _disk_bytes(info)
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                        continue
                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    newest = max(newest, stat.st_mtime)
                    size += _disk_bytes(stat)
        except OSError:
            continue
    return newest, size


def _disk_bytes(info: os.stat_result) -> int:
    blocks = getattr(info, "st_blocks", None)
    return blocks * 512 if isinstance(blocks, int) else info.st_size


def _rem_activity(data_dir: Path) -> _RemActivity | None:
    """Rem's runs that are going and the turns they run, or None when unreadable."""
    from gofer.ui.rem_home_models import ACTIVE

    path = data_dir / REM_STORE_FILE
    if not path.exists():
        return _RemActivity(frozenset(), frozenset())
    try:
        # Read only: this may run beside a backend that owns the database.
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=15)
        try:
            marks = ",".join("?" * len(ACTIVE))
            rows = db.execute(
                f"SELECT id, body FROM runs WHERE state IN ({marks})", sorted(ACTIVE)
            ).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        log.warning("Couldn't read Rem's runs to clean up scratch folders", exc_info=True)
        return None
    runs = set()
    turns = set()
    for identity, body in rows:
        runs.add(str(identity))
        try:
            turn = json.loads(body).get("active_turn_id")
        except (ValueError, AttributeError):
            continue
        if turn:
            turns.add(str(turn))
    return _RemActivity(frozenset(runs), frozenset(turns))


def _workflow_run_activity(data_dir: Path) -> Callable[[str], bool | None]:
    """Answer whether a workflow run is going: True, False, or None when unsure."""
    from gofer.rattish.run_service import effective_run_status, is_rattish_run_active
    from gofer.rattish.workspaces import RattishWorkspaceError, list_registered_workflows

    directories: list[Path] | None = None

    def record_directories() -> list[Path] | None:
        nonlocal directories
        if directories is None:
            try:
                found = [path for path in (data_dir / "radish" / "runs").glob("*") if path.is_dir()]
                found += [
                    workflow.workflow_root / "logs"
                    for workflow in list_registered_workflows(registry_dir=data_dir)
                    if workflow.managed
                ]
            except (OSError, RattishWorkspaceError):
                log.warning(
                    "Couldn't list workflow run records to clean up scratch folders",
                    exc_info=True,
                )
                return None
            directories = found
        return directories

    def active(run_id: str) -> bool | None:
        if is_rattish_run_active(run_id):
            return True
        folders = record_directories()
        if folders is None:
            return None
        for folder in folders:
            record = folder / f"{run_id}.json"
            try:
                document = json.loads(record.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError, ValueError):
                return None
            if not isinstance(document, dict):
                return None
            if effective_run_status(document) == "running":
                return True
        return False

    return active


def _size_text(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} bytes"
