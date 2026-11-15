"""Verified snapshots of Rem's database, and a safe start when it is damaged.

``rem-home.sqlite3`` holds Rem's chat, tasks, runs, threads and board. A copy is taken
with SQLite's backup API when Raticode starts and once a day, checked with
``PRAGMA quick_check``, and kept in a private ``rem-db-backups`` folder in the data
folder. Only copies that pass the check get their final name, so every
``rem-home-<UTC time>Z.sqlite3`` there is a verified copy.

When the store won't open because the file is damaged, ``open_store`` moves the file
and its ``-wal``/``-shm`` aside (never deleting them), restores the newest copy that
still passes the check, or starts an empty store when there is none, and records a
notice. Rem posts that notice in its chat, which the desktop and the phone show.

A full disk never costs the live files. A restore copies the chosen snapshot to a
private temporary file beside the database first; only when that copy is complete and
on disk does it record a restore in progress (``restoring.json``), move the damaged
files aside and rename the copy into place. A start after a stop part way through
finishes the restore instead of creating an empty store. When a check or a restore
can't run because a copy fails, the live files stay as they are and Rem's chat says so.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from gofer.ui.rem_home_store import RemHomeStore

log = logging.getLogger(__name__)

STORE_FILE = "rem-home.sqlite3"
FOLDER = "rem-db-backups"
# How many verified copies stay. Each is about the size of the live database.
KEEP = 3
# A copy is taken at startup unless the newest is younger than this, so restarting
# Raticode several times doesn't replace yesterday's copies with near-identical ones.
STARTUP_GAP = timedelta(hours=1)
INTERVAL = timedelta(days=1)
NAME = re.compile(r"rem-home-(?P<stamp>\d{8}-\d{6})Z\.sqlite3")
# Unfinished copies a stopped snapshot or restore can leave behind.
PARTIAL = re.compile(r"\.rem-home-[0-9a-f]{32}\.tmp")
PARTIAL_AGE = 60 * 60
# Pending chat notices, written before the store opens and posted once it does.
NOTICES = "notices.json"
# Written when a running backend finds the live database damaged; the next start
# checks the file again and restores a copy if it is still damaged.
SUSPECT = "damaged.json"
# Written once the restored copy is staged and before the live files move, so a start
# after a stop part way through can finish the restore.
RESTORING = "restoring.json"
# Folders the live files were moved into by a restore, named by time in UTC.
ASIDE = re.compile(r"(?:damaged|replaced)-(?P<stamp>\d{8}-\d{6})Z(?:-\d+)?")
# Headroom kept free on top of the bytes a copy needs.
SPACE_MARGIN = 1 << 20
# Extra room a scheduled copy leaves free on top of its own size.
SNAPSHOT_MARGIN = 256 << 20
# SQLite primary result codes that mean the file itself is damaged. Busy, locked,
# I/O and permission errors are not damage and are never "repaired" by a restore.
SQLITE_CORRUPT = 11
SQLITE_NOTADB = 26
DAMAGE_WORDS = ("malformed", "not a database", "corrupt")


class SnapshotFailed(RuntimeError):
    """A copy of the live database didn't pass its check."""


def folder(data_dir: Path) -> Path:
    """The private backup folder, created and kept at mode 0700."""
    place = data_dir / FOLDER
    place.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(place, 0o700)
    return place


def is_damage(exc: BaseException) -> bool:
    """Whether a SQLite error means the database file is damaged."""
    if not isinstance(exc, sqlite3.DatabaseError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF in {SQLITE_CORRUPT, SQLITE_NOTADB}
    if isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return any(word in text for word in DAMAGE_WORDS)


def quick_check(path: Path, *, live: bool = False) -> bool:
    """Whether ``path`` is a SQLite database that passes ``PRAGMA quick_check``.

    Copies are opened read-only. ``live`` opens the in-use database normally, since a
    read-only connection can't create the -shm file a WAL database needs after a
    clean shutdown (and would report a healthy file as unreadable).
    """
    if not path.is_file():
        return False
    target = str(path) if live else f"{path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(target, uri=not live, timeout=15)) as db:
            rows = db.execute("PRAGMA quick_check").fetchall()
    except sqlite3.DatabaseError:
        return False
    return [tuple(row) for row in rows] == [("ok",)]


def probe(path: Path) -> sqlite3.DatabaseError | None:
    """Read the schema from the main file alone; return the damage error, if any.

    ``immutable=1`` takes no locks and never touches the -wal or -shm files. A normal
    connection to a damaged file deletes them when it closes, which loses evidence and
    any changes still in the WAL. Only damage errors are returned.
    """
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?immutable=1", uri=True)) as db:
            db.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        return exc if is_damage(exc) else None
    return None


def no_space(exc: BaseException) -> bool:
    """Whether an error means the disk (or the account's quota) is full."""
    return isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}


def ensure_space(place: Path, needed: int) -> None:
    """Raise ENOSPC when ``place`` has less than ``needed`` bytes (plus headroom) free.

    Only a cheap early answer: the copy itself can still run out of space, and callers
    handle that the same way.
    """
    try:
        free = shutil.disk_usage(place).free
    except OSError:
        return
    if free < needed + SPACE_MARGIN:
        raise OSError(
            errno.ENOSPC,
            f"Not enough free disk space ({needed} bytes needed, {free} free)",
            str(place),
        )


def damage_on_copy(data_dir: Path, *, full: bool) -> str | None:
    """Open a scratch copy of the live files (WAL included) and return its damage, if any.

    The live files stay exactly as they are. ``full`` runs ``quick_check``; otherwise
    only the schema is read, as opening the store does. Raises OSError when the copy
    can't be made, such as on a full disk.
    """
    live = data_dir / STORE_FILE
    if not live.is_file():
        return None
    wal = Path(f"{live}-wal")
    place = folder(data_dir)
    ensure_space(place, live.stat().st_size + (wal.stat().st_size if wal.is_file() else 0))
    scratch = place / f".check-{uuid4().hex}"
    scratch.mkdir(mode=0o700)
    try:
        copy = scratch / STORE_FILE
        shutil.copyfile(live, copy)
        if wal.is_file():
            shutil.copyfile(wal, Path(f"{copy}-wal"))
        try:
            with closing(sqlite3.connect(copy)) as db:
                db.execute("SELECT count(*) FROM sqlite_master").fetchone()
                if full:
                    rows = [tuple(r) for r in db.execute("PRAGMA quick_check").fetchall()]
                    if rows != [("ok",)]:
                        return "failed its integrity check"
        except sqlite3.DatabaseError as exc:
            return str(exc) if is_damage(exc) else None
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%d-%H%M%S")


def taken_at(path: Path) -> datetime | None:
    match = NAME.fullmatch(path.name)
    if not match:
        return None
    return datetime.strptime(match["stamp"], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def snapshots(data_dir: Path) -> list[Path]:
    """Verified copies, newest first."""
    place = data_dir / FOLDER
    if not place.is_dir():
        return []
    found = [p for p in place.iterdir() if p.is_file() and NAME.fullmatch(p.name)]
    return sorted(found, key=lambda p: p.name, reverse=True)


def describe(path: Path) -> dict[str, Any]:
    moment = taken_at(path)
    return {
        "name": path.name,
        "path": str(path),
        "taken_at": moment.isoformat() if moment else None,
        "size": path.stat().st_size,
    }


def _private_file(path: Path) -> None:
    """Create ``path`` empty with mode 0600, failing if it exists."""
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))


def _fsync(path: Path) -> None:
    """Flush a file to disk; a full disk can fail here even after every write worked."""
    handle = os.open(path, os.O_RDWR)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _fsync_dir(place: Path) -> None:
    """Make a rename in ``place`` durable, where the platform supports it."""
    if os.name != "posix":
        return
    try:
        handle = os.open(place, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def _clean_partials(place: Path, clock: float | None = None) -> None:
    clock = time.time() if clock is None else clock
    for item in place.iterdir():
        if PARTIAL.fullmatch(item.name):
            try:
                if clock - item.stat().st_mtime > PARTIAL_AGE:
                    item.unlink()
            except FileNotFoundError:
                pass


def prune(data_dir: Path, keep: int = KEEP) -> list[str]:
    removed = []
    for old in snapshots(data_dir)[keep:]:
        try:
            old.unlink()
            removed.append(old.name)
        except FileNotFoundError:
            pass
    return removed


def snapshot(data_dir: Path, *, moment: datetime | None = None, keep: int = KEEP) -> Path:
    """Copy the live database with the backup API, check it and keep the last few.

    The backup runs as one step, so the copy is a single consistent read of the
    database (WAL readers never block Rem's writers). Raises SnapshotFailed when the
    copy doesn't pass ``quick_check``; the backup API copies pages as they are, so
    that usually means the live database is damaged.
    """
    source_path = data_dir / STORE_FILE
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    place = folder(data_dir)
    _clean_partials(place)
    # A copy is about as big as the database and its log. Check first, so a nearly full
    # disk isn't filled to the last byte, failing Rem's own writes, before giving up.
    wal = Path(f"{source_path}-wal")
    size = source_path.stat().st_size + (wal.stat().st_size if wal.is_file() else 0)
    ensure_space(place, size + max(size // 10, SNAPSHOT_MARGIN))
    moment = moment or datetime.now(UTC)
    target = place / f"rem-home-{stamp(moment)}Z.sqlite3"
    partial = place / f".rem-home-{uuid4().hex}.tmp"
    _private_file(partial)
    try:
        with (
            closing(sqlite3.connect(source_path, timeout=15)) as source,
            closing(sqlite3.connect(partial)) as copy,
        ):
            source.backup(copy)
            # The copy stands alone: no -wal or -shm beside it when it is opened.
            copy.execute("PRAGMA journal_mode=DELETE")
        if not quick_check(partial):
            raise SnapshotFailed("The copy of Rem's database failed its integrity check")
        os.chmod(partial, 0o600)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
        for suffix in ("-journal", "-wal", "-shm"):
            Path(f"{partial}{suffix}").unlink(missing_ok=True)
    prune(data_dir, keep)
    return target


def replace_after_clear(data_dir: Path) -> Path | None:
    """Delete every copy taken before Rem's history was cleared, then take a fresh one.

    The copies would otherwise bring back the conversation, tasks and memories the user
    just cleared. Returns the new copy, or None when it couldn't be taken; the next
    scheduled copy then tries again.
    """
    for old in snapshots(data_dir):
        old.unlink(missing_ok=True)
    try:
        return snapshot(data_dir)
    except (OSError, sqlite3.Error, SnapshotFailed) as exc:
        log.warning("Couldn't back up Rem's database after clearing it: %s", exc)
        return None


# Moving aside and restoring ----------------------------------------------------------


def _store_files(data_dir: Path) -> list[Path]:
    base = data_dir / STORE_FILE
    return [p for p in (base, Path(f"{base}-wal"), Path(f"{base}-shm")) if p.exists()]


def _aside_target(data_dir: Path, reason: str, moment: datetime) -> Path:
    """A new folder name for files moved aside; the folder isn't created."""
    place = folder(data_dir)
    target = place / f"{reason}-{stamp(moment)}Z"
    suffix = 1
    while target.exists():
        suffix += 1
        target = place / f"{reason}-{stamp(moment)}Z-{suffix}"
    return target


def _move_into(files: list[Path], target: Path) -> None:
    target.mkdir(mode=0o700)
    for item in files:
        os.replace(item, target / item.name)


def move_aside(data_dir: Path, reason: str, *, moment: datetime | None = None) -> Path | None:
    """Move the live database and its -wal/-shm into a new private folder; never delete.

    Returns that folder, or None when there was nothing to move.
    """
    files = _store_files(data_dir)
    if not files:
        return None
    target = _aside_target(data_dir, reason, moment or datetime.now(UTC))
    _move_into(files, target)
    return target


def _stage(data_dir: Path, copy: Path) -> Path:
    """Copy a verified snapshot to a private (0600) temporary file beside the database.

    The copy is flushed to disk before it is returned, so running out of space shows up
    here, while the live files are still untouched. Nothing is left behind on failure.
    """
    ensure_space(data_dir, copy.stat().st_size)
    partial = data_dir / f".rem-home-{uuid4().hex}.tmp"
    _private_file(partial)
    try:
        shutil.copyfile(copy, partial)
        os.chmod(partial, 0o600)
        _fsync(partial)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return partial


def _put_back(files: list[Path], target: Path | None) -> bool:
    """Undo a move aside that stopped part way; whether every file is back in place."""
    if target is None or not target.is_dir():
        return True
    back = True
    for item in files:
        moved = target / item.name
        if not moved.exists():
            continue
        if item.exists():
            back = False
            continue
        try:
            os.replace(moved, item)
        except OSError:
            back = False
    if back:
        try:
            target.rmdir()
        except OSError:
            pass
    return back


def _finish(data_dir: Path, notice: dict[str, Any]) -> None:
    """Record a finished restore's notice and clear the in-progress and damage marks.

    The restore itself is done by now, so a notice that can't be written is held in
    memory and posted by this backend, and the in-progress mark stays so the next start
    records it again (the notice ID is fixed, so the chat shows it once).
    """
    place = data_dir / FOLDER
    try:
        add_notice(data_dir, notice)
    except OSError as exc:
        log.warning("Couldn't save Rem's database restore notice: %s", exc)
        _hold(data_dir, notice)
    else:
        (place / RESTORING).unlink(missing_ok=True)
    (place / SUSPECT).unlink(missing_ok=True)


def restore(
    data_dir: Path,
    *,
    reason: str,
    error: str = "",
    choice: Path | None = None,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Move the live database aside and put the newest verified copy in its place.

    ``choice`` restores that copy instead, which must still pass the check. With no
    usable copy the live files are still moved aside, so Rem starts an empty store.
    Records a notice for Rem's chat and returns it.

    The copy is staged in a temporary file beside the database before anything moves.
    An OSError up to that point (a full disk, for example) is raised with the live
    files untouched. One while moving the files is undone where possible, then raised.
    """
    moment = moment or datetime.now(UTC)
    candidates = [choice] if choice is not None else snapshots(data_dir)
    chosen = next((c for c in candidates if quick_check(c)), None)
    if choice is not None and chosen is None:
        raise ValueError(f"{choice.name} is missing or doesn't pass its integrity check")
    files = _store_files(data_dir)
    target = _aside_target(data_dir, reason, moment) if files else None
    restored_at = taken_at(chosen) if chosen is not None else None
    notice = {
        "id": f"rem-db-{reason}-{stamp(moment)}-{uuid4().hex[:8]}",
        "reason": reason,
        "at": moment.isoformat(),
        "error": error,
        "restored": chosen.name if chosen is not None else None,
        "restored_from": restored_at.isoformat() if restored_at else None,
        "kept": str(target) if target else None,
    }
    marker = folder(data_dir) / RESTORING
    staged: Path | None = None
    try:
        if chosen is not None:
            staged = _stage(data_dir, chosen)
        _write_json(marker, notice)
    except BaseException:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise
    # The risky step: from here until the rename, the live database isn't in place.
    try:
        if target is not None:
            _move_into(files, target)
        if staged is not None:
            os.replace(staged, data_dir / STORE_FILE)
            staged = None
            _fsync_dir(data_dir)
    except OSError:
        if staged is not None:
            staged.unlink(missing_ok=True)
        if _put_back(files, target):
            marker.unlink(missing_ok=True)
        raise
    _finish(data_dir, notice)
    log.warning(
        "Rem's database was %s: %s",
        f"restored from {chosen.name}" if chosen else "started empty",
        error or reason,
    )
    return notice


def _complete(data_dir: Path, notice: dict[str, Any]) -> dict[str, Any]:
    """Finish a restore that stopped after the live files were moved aside."""
    wanted = notice.get("restored")
    found = snapshots(data_dir)
    ordered = [p for p in found if p.name == wanted] + [p for p in found if p.name != wanted]
    chosen = next((c for c in ordered if quick_check(c)), None)
    # A -wal or -shm still beside the missing file belongs to the moved database and
    # must never be applied to the restored copy.
    kept = Path(notice["kept"]) if notice.get("kept") else None
    for item in _store_files(data_dir):
        if kept is not None and kept.is_dir() and not (kept / item.name).exists():
            os.replace(item, kept / item.name)
    move_aside(data_dir, "interrupted")
    if chosen is not None:
        staged = _stage(data_dir, chosen)
        os.replace(staged, data_dir / STORE_FILE)
        _fsync_dir(data_dir)
    restored_at = taken_at(chosen) if chosen is not None else None
    notice = {
        **notice,
        "restored": chosen.name if chosen is not None else None,
        "restored_from": restored_at.isoformat() if restored_at else None,
    }
    _finish(data_dir, notice)
    log.warning(
        "Finished an interrupted restore of Rem's database: %s",
        f"restored {chosen.name}" if chosen else "no verified copy, started empty",
    )
    return notice


def resume_interrupted(data_dir: Path) -> dict[str, Any] | None:
    """Finish a restore that a stop (a crash, a power cut) left part way; return its notice.

    With ``restoring.json``: when the live file is back and the old one was moved, the
    restore finished and only its notice is recorded; when nothing was moved, the mark
    is dropped and the usual checks run; when the live file is missing, the copy is put
    in place. Without the mark, a missing live file and a ``damaged-*`` folder newer than
    the newest snapshot mean the same thing, so that snapshot is restored instead of
    letting Rem start empty. Raises OSError when the copy can't be made.
    """
    live = data_dir / STORE_FILE
    place = data_dir / FOLDER
    marker = place / RESTORING
    if marker.is_file():
        notice = _read_json(marker)
        if not (isinstance(notice, dict) and isinstance(notice.get("id"), str)):
            marker.unlink(missing_ok=True)
        else:
            kept = Path(notice["kept"]) if notice.get("kept") else None
            moved = kept is not None and (kept / STORE_FILE).is_file()
            if live.is_file():
                if not moved:
                    marker.unlink(missing_ok=True)
                    return None
                _finish(data_dir, notice)
                return notice
            return _complete(data_dir, notice)
    if live.exists() or not place.is_dir():
        return None
    found = snapshots(data_dir)
    newest = taken_at(found[0]) if found else None
    if newest is None:
        return None
    aside = []
    for item in place.iterdir():
        match = ASIDE.fullmatch(item.name)
        if match and item.is_dir():
            moment = datetime.strptime(match["stamp"], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
            aside.append((moment, item))
    if not aside:
        return None
    moment, kept = max(aside)
    if moment <= newest:
        return None
    now = datetime.now(UTC)
    return _complete(
        data_dir,
        {
            "id": f"rem-db-damaged-{stamp(now)}-{uuid4().hex[:8]}",
            "reason": "damaged",
            "at": now.isoformat(),
            "error": "an earlier restore stopped before it finished",
            "restored": None,
            "restored_from": None,
            "kept": str(kept),
        },
    )


# Notices ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Any) -> None:
    partial = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    _private_file(partial)
    try:
        partial.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


# Notices that couldn't be written to notices.json (a full disk), by data folder. This
# backend posts them; they are lost if it stops first.
_held: dict[str, list[dict[str, Any]]] = {}
_held_lock = threading.Lock()


def _hold(data_dir: Path, notice: dict[str, Any]) -> None:
    with _held_lock:
        _held.setdefault(str(data_dir), []).append(notice)


def add_notice(data_dir: Path, notice: dict[str, Any]) -> None:
    path = folder(data_dir) / NOTICES
    pending = _read_json(path)
    pending = pending if isinstance(pending, list) else []
    _write_json(path, [*pending, notice])


def queue_notice(data_dir: Path, notice: dict[str, Any]) -> None:
    """Record a notice for Rem's chat, holding it in memory when the file can't be written."""
    try:
        add_notice(data_dir, notice)
    except OSError as exc:
        log.warning("Couldn't save a notice about Rem's database: %s", exc)
        _hold(data_dir, notice)


def pending_notices(data_dir: Path) -> list[dict[str, Any]]:
    pending = _read_json(data_dir / FOLDER / NOTICES)
    saved = [n for n in pending if isinstance(n, dict)] if isinstance(pending, list) else []
    with _held_lock:
        held = list(_held.get(str(data_dir), []))
    ids = {n.get("id") for n in saved}
    return saved + [n for n in held if n.get("id") not in ids]


def _local(value: str | None) -> str:
    if not value:
        return ""
    moment = datetime.fromisoformat(value).astimezone()
    return f"{moment:%b} {moment.day}, {moment:%Y} at {moment:%H:%M}"


def unchecked_notice(step: str, exc: OSError, damage: str = "") -> dict[str, Any]:
    """A notice that a check (``step="check"``) or a restore (``"restore"``) couldn't run."""
    moment = datetime.now(UTC)
    return {
        "id": f"rem-db-unchecked-{stamp(moment)}-{uuid4().hex[:8]}",
        "reason": "unchecked",
        "step": step,
        "at": moment.isoformat(),
        "error": exc.strerror or str(exc),
        "no_space": no_space(exc),
        "damage": damage,
        "restored": None,
        "restored_from": None,
        "kept": None,
    }


def notice_text(notice: dict[str, Any]) -> str:
    if notice.get("reason") == "unchecked":
        because = (
            "because there isn't enough free disk space"
            if notice.get("no_space")
            else f"because a copy failed ({notice.get('error') or 'file error'})"
        )
        if notice.get("step") == "restore":
            what = f" ({notice['damage']})" if notice.get("damage") else ""
            return (
                f"Rem's database looks damaged{what}, but Raticode couldn't restore a "
                f"verified copy {because}. Its files are untouched and Rem opened them as "
                f"they are, so some of Rem's chat, tasks or board may not load. Free up "
                f"disk space and restart Raticode to restore the copy."
            )
        return (
            f"Raticode couldn't check Rem's database for damage {because}, so Rem opened "
            f"it as it is. Free up disk space and restart Raticode to run the check again."
        )
    kept = notice.get("kept")
    kept_line = f" The previous files are kept, not deleted, in {kept}." if kept else ""
    restored = notice.get("restored_from")
    if notice.get("reason") == "replaced":
        return (
            f"Rem's database was restored with `Raticode rem restore-db` from the "
            f"verified copy saved {_local(restored)}. Chat, tasks and board changes made "
            f"after that time aren't in it.{kept_line}"
        )
    why = f" ({notice['error']})" if notice.get("error") else ""
    if restored:
        return (
            f"Rem's database was damaged{why}, so Raticode restored the verified copy "
            f"saved {_local(restored)}. Chat, tasks and board changes made after that "
            f"time are missing. Workflows, the studio and phone pairing keep working."
            f"{kept_line}"
        )
    return (
        f"Rem's database was damaged{why} and there was no verified copy to restore, so "
        f"Raticode started Rem with an empty database. Earlier chat, tasks, board and Rem "
        f"settings aren't in it. Workflows, the studio and phone pairing keep working."
        f"{kept_line}"
    )


def damage_text(data_dir: Path) -> str:
    """Rem's chat notice when the running backend finds its database damaged."""
    found = snapshots(data_dir)
    newest = taken_at(found[0]) if found else None
    then = (
        f"restores the newest verified copy, saved {_local(newest.isoformat())}"
        if newest
        else "starts Rem with an empty database, because there is no verified copy yet"
    )
    return (
        "Rem's database failed its integrity check. Restart Raticode: on the next start it "
        f"moves the damaged files aside (they are kept, not deleted) and {then}. Until "
        "then some of Rem's chat, tasks or board may not load."
    )


def post_notices(data_dir: Path, store: RemHomeStore) -> list[dict[str, Any]]:
    """Post pending restore notices in Rem's chat once, then clear them."""
    pending = pending_notices(data_dir)
    if not pending:
        return []
    posted = []
    with store.connect() as db:
        for notice in pending:
            posted.append(
                store.message(
                    db,
                    notice_text(notice),
                    identity=str(notice["id"]),
                    database_restore={
                        k: notice.get(k)
                        for k in ("reason", "step", "at", "restored", "restored_from", "kept")
                        if k in notice
                    },
                )
            )
    # Message IDs are fixed, so posting twice after a crash here shows one notice.
    (data_dir / FOLDER / NOTICES).unlink(missing_ok=True)
    with _held_lock:
        _held.pop(str(data_dir), None)
    return posted


# Opening the store -----------------------------------------------------------------


def mark_suspect(data_dir: Path, error: str) -> None:
    """Record that the live database failed a check, for the next start to confirm."""
    _write_json(folder(data_dir) / SUSPECT, {"at": datetime.now(UTC).isoformat(), "error": error})


def _flag(data_dir: Path, error: str) -> None:
    """Mark the live database for a check at the next start, if the mark can be written."""
    if (data_dir / FOLDER / SUSPECT).is_file():
        return
    try:
        mark_suspect(data_dir, error)
    except OSError as exc:
        log.warning("Couldn't mark Rem's database for a check at the next start: %s", exc)


def _check_failed(data_dir: Path, exc: OSError) -> dict[str, Any]:
    """A damage check couldn't copy the live files: keep them and check at the next start.

    Returns the chat notice, which is recorded once the store opens as it is.
    """
    log.error("Couldn't check Rem's database for damage; opening it as it is: %s", exc)
    _flag(data_dir, "the check couldn't run")
    return unchecked_notice("check", exc)


def _try_restore(data_dir: Path, error: str) -> bool:
    """Restore a verified copy; on an OSError keep the live files, say so and return False."""
    try:
        restore(data_dir, reason="damaged", error=error)
    except OSError as exc:
        log.error("Couldn't restore Rem's database (%s); its files are untouched: %s", error, exc)
        _flag(data_dir, error)
        queue_notice(data_dir, unchecked_notice("restore", exc, damage=error))
        return False
    return True


def open_store(
    data_dir: Path, factory: Callable[[Path], RemHomeStore] | None = None
) -> RemHomeStore:
    """Open Rem's store, restoring a verified copy when the file is damaged.

    A restore that a stop left part way is finished first. A database that a running
    backend found damaged (``damaged.json``) is checked again here and restored only if
    it still fails. When a check or a restore can't copy the files (a full disk), the
    live files are opened as they are, the mark is kept for the next start and Rem's
    chat says why. Errors that aren't damage, such as a locked database, are raised as
    they are.
    """
    if factory is None:
        from gofer.ui.rem_home_store import RemHomeStore

        factory = RemHomeStore
    data_dir.mkdir(parents=True, exist_ok=True)
    live = data_dir / STORE_FILE
    suspect = data_dir / FOLDER / SUSPECT
    try:
        _clean_partials(data_dir)
    except OSError:
        pass
    try:
        resume_interrupted(data_dir)
    except OSError as exc:
        # Starting empty here would hide the moved database; the next start tries again.
        raise OSError(
            exc.errno,
            f"Raticode couldn't finish restoring Rem's database ({exc.strerror or exc}). "
            "Free up disk space and start Raticode again",
        ) from exc
    unchecked: dict[str, Any] | None = None
    if suspect.is_file():
        try:
            error = damage_on_copy(data_dir, full=True)
        except OSError as exc:
            unchecked = _check_failed(data_dir, exc)
        else:
            if not error:
                suspect.unlink(missing_ok=True)
            else:
                _try_restore(data_dir, error)
    elif (found := probe(live)) is not None:
        # The main file looks damaged. Confirm with its WAL on a copy, so a file that
        # only needs its WAL replayed isn't replaced, then move the untouched files.
        try:
            error = damage_on_copy(data_dir, full=False)
        except OSError as exc:
            unchecked = _check_failed(data_dir, exc)
        else:
            if error and not _try_restore(data_dir, error):
                # The schema can't be read, so a normal open would fail too, and would
                # delete the -wal and -shm on the way. Leave them for the next start.
                raise sqlite3.DatabaseError(
                    f"Rem's database is damaged ({error}) and couldn't be restored for now"
                ) from found
    try:
        store = factory(data_dir)
    except sqlite3.DatabaseError as exc:
        if not is_damage(exc):
            raise
        # Damage the probe didn't see, such as in the WAL. SQLite may already have
        # removed the -wal and -shm here; whatever is left is moved aside.
        first = str(exc)
        damage = exc
    else:
        if unchecked is not None:
            queue_notice(data_dir, unchecked)
        return store
    # A restore notice replaces the one about the check that couldn't run.
    if not _try_restore(data_dir, first):
        raise damage
    try:
        return factory(data_dir)
    except sqlite3.DatabaseError as exc:
        if not is_damage(exc):
            raise
    # The restored copy passed its check but still won't open: start empty. Its
    # notice is replaced, since nothing was restored after all.
    moment = datetime.now(UTC)
    move_aside(data_dir, "damaged", moment=moment)
    _write_json(
        folder(data_dir) / NOTICES,
        [
            *pending_notices(data_dir)[:-1],
            {
                "id": f"rem-db-damaged-{stamp(moment)}-{uuid4().hex[:8]}",
                "reason": "damaged",
                "at": moment.isoformat(),
                "error": first,
                "restored": None,
                "restored_from": None,
                "kept": str(data_dir / FOLDER),
            },
        ],
    )
    return factory(data_dir)


# The running backend's schedule ------------------------------------------------------


class Snapshots:
    """Takes a verified copy at startup and once a day, off the caller's thread."""

    def __init__(
        self,
        data_dir: Path,
        on_damage: Callable[[str], None] | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        self.data_dir = data_dir
        # Off for a RemHome built without its monitor (tests that tick by hand).
        self.enabled = enabled
        self.on_damage = on_damage or (lambda error: None)
        self.started = False
        self.worker: threading.Thread | None = None
        self.next_try = 0.0
        self.next_check = 0.0
        self.last_error = ""

    def due(self, clock: datetime | None = None) -> bool:
        clock = clock or datetime.now(UTC)
        found = snapshots(self.data_dir)
        newest = taken_at(found[0]) if found else None
        if newest is None:
            return True
        gap = INTERVAL if self.started else STARTUP_GAP
        return clock - newest >= gap

    def tick(self) -> None:
        """Start a snapshot in the background when one is due and none is running."""
        if not self.enabled:
            return
        if self.worker is not None and self.worker.is_alive():
            return
        if time.monotonic() < self.next_try:
            return
        due = self.due()
        self.started = True
        # The folder is looked at once a minute, not on every tick.
        self.next_try = time.monotonic() + 60
        if not due:
            return
        self.worker = threading.Thread(target=self.run, daemon=True, name="rem-db-snapshot")
        self.worker.start()

    def run(self) -> Path | None:
        try:
            path = snapshot(self.data_dir)
        except SnapshotFailed as exc:
            self.next_try = time.monotonic() + 3600
            self.check_live(str(exc))
            return None
        except (OSError, sqlite3.Error) as exc:
            # Out of space, a locked file, or a damaged page met during the copy.
            self.next_try = time.monotonic() + 3600
            self.last_error = str(exc)
            log.warning("Rem's database snapshot failed: %s", exc)
            if isinstance(exc, sqlite3.DatabaseError) and is_damage(exc):
                self.check_live(str(exc))
            return None
        self.last_error = ""
        log.info("Saved a verified copy of Rem's database: %s", path.name)
        return path

    def suspect(self, error: str) -> None:
        """A query hit damage: check the whole live file in the background, at most hourly."""
        if self.worker is not None and self.worker.is_alive():
            return
        if time.monotonic() < self.next_check:
            return
        self.next_check = time.monotonic() + 3600
        self.worker = threading.Thread(
            target=self.check_live, args=(error,), daemon=True, name="rem-db-check"
        )
        self.worker.start()

    def check_live(self, error: str) -> bool:
        """Check the live database; when it fails, mark it for restore at the next start."""
        self.last_error = error
        if quick_check(self.data_dir / STORE_FILE, live=True):
            return False
        log.error("Rem's database failed its integrity check; it is restored at the next start")
        mark_suspect(self.data_dir, "failed its integrity check")
        self.on_damage(error)
        return True
