"""Remove sandbox scratch folders that a stopped or crashed turn left behind.

Rem's sandboxed turns keep temp files and caches in ``rem-work/turn-scratch``
(named ``<run id>-<role>``) and Codex reviews in ``rem-work/review-scratch``
(named by turn ID). Sandboxed Rattish agent steps use ``run-scratch/<run id>``.
Each owner removes its folder when it finishes, but a process that is killed
can't. When the backend or the trigger scheduler starts, a folder is removed
only when nothing shows its turn or run is still going and nothing in it has
changed for a day, so a folder another live Raticode process uses is kept.

A worker turn's folder keeps its ``evidence`` subfolder: the screenshots, recordings
and logs a worker links in its reply, which the chat shows as attachments. Evidence
stays for :data:`EVIDENCE_SECONDS` after its run ends, or until the run and its
thread are deleted.
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
from datetime import UTC, datetime
from pathlib import Path

from gofer.utils.logging import get_logger

log = get_logger(__name__)

STALE_SECONDS = 24 * 60 * 60
TURN_SCRATCH = ("rem-work", "turn-scratch")
REVIEW_SCRATCH = ("rem-work", "review-scratch")
REM_STORE_FILE = "rem-home.sqlite3"
# A worker turn's evidence files, inside its turn scratch folder.
EVIDENCE_FOLDER = "evidence"
EVIDENCE_SECONDS = 30 * 24 * 60 * 60
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
    # Runs that aren't going, with when each finished (None when it has no time).
    ended: dict[str, float | None]


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
            if owner == "review":
                _settle(folder, folder.name in rem.turns, clock, max_age, result)
                continue
            run = folder.name.rpartition("-")[0]
            if run in rem.ended:
                _settle_turn(folder, rem.ended[run], clock, max_age, result)
            else:
                # A run that is going keeps all of its folder; a deleted one's goes.
                _settle(folder, run in rem.runs, clock, max_age, result)
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


def _settle_turn(
    folder: Path, ended: float | None, clock: float, max_age: float, result: CleanupResult
) -> None:
    """A finished run's turn folder: its evidence stays for 30 days after the run ends."""
    evidence = folder / EVIDENCE_FOLDER
    if evidence.is_symlink() or not evidence.is_dir():
        _settle(folder, False, clock, max_age, result)
        return
    if ended is None:
        # A run that stopped without finishing, such as a paused one, counts from the
        # last change to its evidence.
        ended = _tree_stats(evidence)[0]
    if clock - ended > EVIDENCE_SECONDS:
        _settle(folder, False, clock, max_age, result)
        return
    # The rest of the folder goes as any idle scratch does.
    result.kept += 1
    try:
        with os.scandir(folder) as entries:
            rest = [Path(entry.path) for entry in entries if entry.name != EVIDENCE_FOLDER]
    except OSError:
        return
    stats = [_tree_stats(path) for path in rest]
    if not rest or clock - max(newest for newest, _ in stats) <= max_age:
        return
    for path, (_, size) in zip(rest, stats, strict=True):
        _remove(path)
        if not os.path.lexists(path):
            result.freed_bytes += size


def evidence_folder(data_dir: Path, run_id: str) -> Path:
    """Where a run's worker saves the evidence files it links."""
    return data_dir.joinpath(*TURN_SCRATCH, f"{run_id}-execute", EVIDENCE_FOLDER)


def clear_turn_scratch(folder: Path) -> None:
    """Remove a worker turn's scratch folder when the turn ends, keeping its evidence.

    The folder goes as a whole when its evidence folder is empty or isn't a real folder.
    """
    evidence = folder / EVIDENCE_FOLDER
    try:
        keep = not evidence.is_symlink() and evidence.is_dir() and any(evidence.iterdir())
    except OSError:
        keep = False
    if not keep:
        shutil.rmtree(folder, ignore_errors=True)
        return
    try:
        with os.scandir(folder) as entries:
            rest = [Path(entry.path) for entry in entries if entry.name != EVIDENCE_FOLDER]
    except OSError:
        return
    for path in rest:
        _remove(path)


def _remove(path: Path) -> None:
    """Remove a file, link or folder without following links."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        log.warning("Couldn't remove %s from a scratch folder", path)


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
        return _RemActivity(frozenset(), frozenset(), {})
    try:
        # Read only: this may run beside a backend that owns the database.
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=15)
        try:
            marks = ",".join("?" * len(ACTIVE))
            rows = db.execute(
                f"SELECT id, body FROM runs WHERE state IN ({marks})", sorted(ACTIVE)
            ).fetchall()
            finished = db.execute(
                "SELECT id, CASE WHEN json_valid(body) THEN json_extract(body, '$.finished_at') "
                f"END FROM runs WHERE state NOT IN ({marks})",
                sorted(ACTIVE),
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
    ended = {str(identity): _instant(moment) for identity, moment in finished}
    return _RemActivity(frozenset(runs), frozenset(turns), ended)


def _instant(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


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
