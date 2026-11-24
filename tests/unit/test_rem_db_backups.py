"""Verified snapshots of Rem's database and a safe start when the file is damaged."""

from __future__ import annotations

import errno
import json
import os
import random
import shutil
import sqlite3
import stat
import sys
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.ui import rem_db_backups as backups
from gofer.ui.rem_home_store import RemHomeStore

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def store_with(data: Path, *bodies: str) -> RemHomeStore:
    store = RemHomeStore(data)
    with store.connect() as db:
        for body in bodies:
            store.message(db, body, identity=f"m-{body}")
    return store


def bodies(store: RemHomeStore) -> list[str]:
    with store.read() as db:
        return [json.loads(row[0])["body"] for row in db.execute("SELECT body FROM messages")]


def settle(data: Path) -> None:
    """Fold the WAL into the main file, as a clean shutdown does."""
    with closing(sqlite3.connect(data / backups.STORE_FILE)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def page_size(path: Path) -> int:
    size = int.from_bytes(path.read_bytes()[16:18], "big")
    return 65536 if size == 1 else size


def scramble_page(path: Path, number: int, *, start: int = 0) -> None:
    """Overwrite one database page (1-based) with random bytes from ``start``."""
    size = page_size(path)
    noise = random.Random(number).randbytes(size - start)
    with path.open("r+b") as handle:
        handle.seek((number - 1) * size + start)
        handle.write(noise)


def root_page(data: Path, table: str) -> int:
    with closing(sqlite3.connect(data / backups.STORE_FILE)) as db:
        row = db.execute("SELECT rootpage FROM sqlite_master WHERE name=?", (table,)).fetchone()
    return int(row[0])


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# Snapshots ---------------------------------------------------------------------------


@POSIX
def test_a_snapshot_is_a_private_checked_standalone_copy(tmp_path: Path) -> None:
    store_with(tmp_path, "hello")
    copy = backups.snapshot(tmp_path)

    assert copy.parent == tmp_path / backups.FOLDER
    assert mode(copy.parent) == 0o700
    assert mode(copy) == 0o600
    assert backups.NAME.fullmatch(copy.name)
    assert backups.quick_check(copy)
    # It opens on its own: no WAL mode, so no -wal or -shm files appear beside it.
    with closing(sqlite3.connect(copy)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    assert sorted(p.name for p in copy.parent.iterdir()) == [copy.name]


@POSIX
def test_an_existing_backup_folder_is_made_private(tmp_path: Path) -> None:
    store_with(tmp_path, "hello")
    (tmp_path / backups.FOLDER).mkdir(mode=0o755)
    os.chmod(tmp_path / backups.FOLDER, 0o755)
    backups.snapshot(tmp_path)
    assert mode(tmp_path / backups.FOLDER) == 0o700


def test_snapshots_keep_the_last_few_and_clear_stale_partials(tmp_path: Path) -> None:
    store_with(tmp_path, "hello")
    start = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    for day in range(5):
        backups.snapshot(tmp_path, moment=start + timedelta(days=day))
    stale = tmp_path / backups.FOLDER / f".rem-home-{'a' * 32}.tmp"
    stale.write_bytes(b"left by a killed copy")
    os.utime(stale, (time.time() - 2 * backups.PARTIAL_AGE,) * 2)
    fresh = tmp_path / backups.FOLDER / f".rem-home-{'b' * 32}.tmp"
    fresh.write_bytes(b"still being written")

    backups.snapshot(tmp_path, moment=start + timedelta(days=5))

    names = [p.name for p in backups.snapshots(tmp_path)]
    assert names == [
        "rem-home-20261106-030000Z.sqlite3",
        "rem-home-20261105-030000Z.sqlite3",
        "rem-home-20261104-030000Z.sqlite3",
    ]
    assert not stale.exists()
    assert fresh.exists()


def test_a_copy_that_fails_its_check_is_not_kept(tmp_path: Path) -> None:
    store_with(tmp_path, *(f"message {n} " + "x" * 2000 for n in range(40)))
    settle(tmp_path)
    scramble_page(tmp_path / backups.STORE_FILE, root_page(tmp_path, "messages"))

    with pytest.raises(backups.SnapshotFailed):
        backups.snapshot(tmp_path)
    assert backups.snapshots(tmp_path) == []
    assert [p.name for p in (tmp_path / backups.FOLDER).iterdir()] == []


def test_snapshots_run_at_startup_and_then_daily(tmp_path: Path) -> None:
    store_with(tmp_path, "hello")
    schedule = backups.Snapshots(tmp_path)
    assert schedule.due()
    copy = schedule.run()
    assert copy is not None
    made = backups.taken_at(copy)
    assert made is not None
    # A restart within the hour doesn't take another; after that it does.
    assert not backups.Snapshots(tmp_path).due(made + timedelta(minutes=30))
    assert backups.Snapshots(tmp_path).due(made + timedelta(hours=2))
    # Once running, the next copy is a day after the last.
    schedule.started = True
    assert not schedule.due(made + timedelta(hours=23))
    assert schedule.due(made + timedelta(hours=24))


def test_tick_takes_one_copy_in_the_background(tmp_path: Path) -> None:
    store_with(tmp_path, "hello")
    schedule = backups.Snapshots(tmp_path)
    schedule.tick()
    assert schedule.worker is not None
    schedule.worker.join(timeout=10)
    schedule.tick()
    assert len(backups.snapshots(tmp_path)) == 1


def test_a_disabled_schedule_takes_no_copies(tmp_path: Path) -> None:
    store_with(tmp_path, "hello")
    schedule = backups.Snapshots(tmp_path, enabled=False)
    schedule.tick()
    assert schedule.worker is None
    assert backups.snapshots(tmp_path) == []


# Damage --------------------------------------------------------------------------------


def test_only_corrupt_and_not_a_database_errors_count_as_damage() -> None:
    corrupt = sqlite3.DatabaseError("database disk image is malformed")
    corrupt.sqlite_errorcode = 11  # type: ignore[attr-defined]
    not_db = sqlite3.DatabaseError("file is not a database")
    not_db.sqlite_errorcode = 26  # type: ignore[attr-defined]
    locked = sqlite3.OperationalError("database is locked")
    locked.sqlite_errorcode = 5  # type: ignore[attr-defined]
    io_error = sqlite3.OperationalError("disk I/O error")
    io_error.sqlite_errorcode = 10  # type: ignore[attr-defined]
    assert backups.is_damage(corrupt)
    assert backups.is_damage(not_db)
    assert not backups.is_damage(locked)
    assert not backups.is_damage(io_error)
    assert not backups.is_damage(ValueError("malformed"))


def _real_error(path: Path) -> sqlite3.DatabaseError:
    with pytest.raises(sqlite3.DatabaseError) as caught:
        RemHomeStore(path.parent)
    return caught.value


def test_a_normal_open_reports_damage_and_loses_the_wal(tmp_path: Path) -> None:
    """Why open_store probes first: SQLite removes the WAL when a damaged file closes."""
    live = tmp_path / backups.STORE_FILE
    live.write_bytes(random.Random(5).randbytes(8192))
    Path(f"{live}-wal").write_bytes(b"old wal")
    assert backups.is_damage(_real_error(live))
    assert not Path(f"{live}-wal").exists()


def test_a_wal_only_database_is_not_mistaken_for_damage(tmp_path: Path) -> None:
    # While another connection stays open, closing the store's connections doesn't
    # checkpoint, so the new messages are only in the WAL.
    store = store_with(tmp_path)
    with closing(sqlite3.connect(tmp_path / backups.STORE_FILE)) as holder:
        holder.execute("SELECT count(*) FROM settings").fetchone()
        with store.connect() as db:
            store.message(db, "only in the wal", identity="m-wal")
        assert Path(f"{tmp_path / backups.STORE_FILE}-wal").stat().st_size > 0
        assert backups.probe(tmp_path / backups.STORE_FILE) is None
        assert bodies(backups.open_store(tmp_path)) == ["only in the wal"]
    assert backups.pending_notices(tmp_path) == []


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_bytes_restore_the_newest_verified_copy(tmp_path: Path, seed: int) -> None:
    store_with(tmp_path, "kept")
    backups.snapshot(tmp_path, moment=datetime(2026, 11, 13, 3, 0, tzinfo=UTC))
    store_with(tmp_path, "newer")
    backups.snapshot(tmp_path, moment=datetime(2026, 11, 14, 3, 0, tzinfo=UTC))
    live = tmp_path / backups.STORE_FILE
    noise = random.Random(seed).randbytes(64 * 1024)
    live.write_bytes(noise)
    Path(f"{live}-wal").write_bytes(b"old wal")
    Path(f"{live}-shm").write_bytes(b"old shm")
    # Found without a normal open, which would delete the -wal and -shm.
    damage = backups.probe(live)
    assert damage is not None and backups.is_damage(damage)

    store = backups.open_store(tmp_path)

    assert bodies(store) == ["kept", "newer"]
    [kept] = [p for p in (tmp_path / backups.FOLDER).iterdir() if p.name.startswith("damaged-")]
    # Moved, never deleted: the same bytes, with the old -wal and -shm beside them.
    assert (kept / backups.STORE_FILE).read_bytes() == noise
    assert (kept / f"{backups.STORE_FILE}-wal").read_bytes() == b"old wal"
    assert (kept / f"{backups.STORE_FILE}-shm").read_bytes() == b"old shm"
    [notice] = backups.pending_notices(tmp_path)
    assert notice["restored"] == "rem-home-20261114-030000Z.sqlite3"
    assert notice["restored_from"] == "2026-11-14T03:00:00+00:00"
    assert notice["kept"] == str(kept)
    assert "not a database" in notice["error"]


def test_a_damaged_schema_page_restores_a_copy(tmp_path: Path) -> None:
    store_with(tmp_path, *(f"message {n}" for n in range(20)))
    backups.snapshot(tmp_path)
    store_with(tmp_path, "lost after the copy")
    settle(tmp_path)
    # Page 1 holds the schema after the 100-byte file header.
    scramble_page(tmp_path / backups.STORE_FILE, 1, start=100)
    assert backups.probe(tmp_path / backups.STORE_FILE) is not None

    store = backups.open_store(tmp_path)

    assert bodies(store) == [f"message {n}" for n in range(20)]
    [notice] = backups.pending_notices(tmp_path)
    assert "malformed" in notice["error"]


def test_a_damaged_data_page_is_found_by_the_snapshot_and_restored_on_restart(
    tmp_path: Path,
) -> None:
    store_with(tmp_path, *(f"message {n} " + "x" * 2000 for n in range(40)))
    good = backups.snapshot(tmp_path)
    settle(tmp_path)
    scramble_page(tmp_path / backups.STORE_FILE, root_page(tmp_path, "messages"))
    # The store still opens: only the messages table is damaged.
    RemHomeStore(tmp_path)
    told: list[str] = []
    schedule = backups.Snapshots(tmp_path, on_damage=told.append)

    assert schedule.run() is None

    assert told and (tmp_path / backups.FOLDER / backups.SUSPECT).is_file()
    assert backups.snapshots(tmp_path) == [good]
    store = backups.open_store(tmp_path)
    assert len(bodies(store)) == 40
    assert not (tmp_path / backups.FOLDER / backups.SUSPECT).exists()
    [notice] = backups.pending_notices(tmp_path)
    assert notice["restored"] == good.name


def test_a_suspect_mark_on_a_healthy_file_is_dropped(tmp_path: Path) -> None:
    store_with(tmp_path, "fine")
    backups.mark_suspect(tmp_path, "query failed once")
    store = backups.open_store(tmp_path)
    assert bodies(store) == ["fine"]
    assert not (tmp_path / backups.FOLDER / backups.SUSPECT).exists()
    assert backups.pending_notices(tmp_path) == []


def test_damage_without_a_copy_starts_an_empty_store(tmp_path: Path) -> None:
    live = tmp_path / backups.STORE_FILE
    live.write_bytes(random.Random(7).randbytes(8192))

    store = backups.open_store(tmp_path)

    assert bodies(store) == []
    [notice] = backups.pending_notices(tmp_path)
    assert notice["restored"] is None
    assert "empty database" in backups.notice_text(notice)
    kept = Path(notice["kept"])
    assert (kept / backups.STORE_FILE).read_bytes() == random.Random(7).randbytes(8192)


def test_a_damaged_copy_is_skipped_for_an_older_good_one(tmp_path: Path) -> None:
    store_with(tmp_path, "older")
    older = backups.snapshot(tmp_path, moment=datetime(2026, 11, 12, tzinfo=UTC))
    newer = backups.snapshot(tmp_path, moment=datetime(2026, 11, 13, tzinfo=UTC))
    newer.write_bytes(b"rotted on disk")
    (tmp_path / backups.STORE_FILE).write_bytes(b"\0" * 4096 + b"garbage" * 100)
    for suffix in ("-wal", "-shm"):
        Path(f"{tmp_path / backups.STORE_FILE}{suffix}").unlink(missing_ok=True)

    store = backups.open_store(tmp_path)

    assert bodies(store) == ["older"]
    assert backups.pending_notices(tmp_path)[0]["restored"] == older.name


def test_errors_that_are_not_damage_leave_the_files_alone(tmp_path: Path) -> None:
    store_with(tmp_path, "fine")
    before = (tmp_path / backups.STORE_FILE).read_bytes()

    def locked(_: Path) -> RemHomeStore:
        error = sqlite3.OperationalError("database is locked")
        error.sqlite_errorcode = 5  # type: ignore[attr-defined]
        raise error

    with pytest.raises(sqlite3.OperationalError):
        backups.open_store(tmp_path, factory=locked)
    assert (tmp_path / backups.STORE_FILE).read_bytes() == before
    assert not (tmp_path / backups.FOLDER).exists()


# Notices -------------------------------------------------------------------------------


def test_notices_are_posted_once_in_rems_chat(tmp_path: Path) -> None:
    store_with(tmp_path, "kept")
    backups.snapshot(tmp_path, moment=datetime(2026, 11, 14, 3, 0, tzinfo=UTC))
    (tmp_path / backups.STORE_FILE).write_bytes(b"not sqlite" * 500)
    store = backups.open_store(tmp_path)

    [posted] = backups.post_notices(tmp_path, store)
    assert backups.post_notices(tmp_path, store) == []

    with store.read() as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM messages")]
    notice = rows[-1]
    assert notice["id"] == posted["id"] and notice["role"] == "rem"
    local = datetime(2026, 11, 14, 3, 0, tzinfo=UTC).astimezone()
    assert f"{local:%b} {local.day}, 2026 at {local:%H:%M}" in notice["body"]
    assert "restored the verified copy" in notice["body"]
    assert "phone pairing keep working" in notice["body"]
    assert notice["database_restore"]["restored"] == "rem-home-20261114-030000Z.sqlite3"
    assert notice["database_restore"]["restored_from"] == "2026-11-14T03:00:00+00:00"


def _rem_home(data: Path, *, start: bool = False) -> Any:
    from gofer.ui.chat_jobs import ChatJobs
    from gofer.ui.chat_steering import ChatSteering
    from gofer.ui.rem_home import RemHome

    async def source(**kwargs: Any):
        yield {"type": "final", "message": {"body": "done"}}

    return RemHome(
        data,
        source=source,
        jobs=ChatJobs(data),
        steering=ChatSteering(data),
        authorize=lambda c, r: None,
        generate=lambda p, c, e: {"reply": "Hello.", "tasks": [], "memories": []},
        start=start,
    )


def _close(home: Any) -> None:
    home.close()
    home.jobs.close()
    home.steering.close()


def test_rem_home_starts_on_a_damaged_file_and_says_so(tmp_path: Path) -> None:
    store_with(tmp_path, "before")
    backups.snapshot(tmp_path)
    scramble_page(tmp_path / backups.STORE_FILE, 1, start=100)

    home = _rem_home(tmp_path)
    try:
        messages = home.store.conversation()["messages"]
        assert [m["body"] for m in messages][:1] == ["before"]
        assert "Rem's database was damaged" in messages[-1]["body"]
        assert backups.pending_notices(tmp_path) == []
    finally:
        _close(home)


def test_rem_home_takes_a_copy_when_it_owns_rem(tmp_path: Path) -> None:
    home = _rem_home(tmp_path)
    try:
        home.db_snapshots.enabled = True
        home.tick()
        assert home.db_snapshots.worker is not None
        home.db_snapshots.worker.join(timeout=10)
        assert len(backups.snapshots(tmp_path)) == 1
    finally:
        _close(home)


def test_rem_home_flags_damage_found_while_running(tmp_path: Path) -> None:
    home = _rem_home(tmp_path)
    try:
        backups.snapshot(tmp_path)
        home._database_damaged("database disk image is malformed")
        last = home.store.conversation()["messages"][-1]
        assert "failed its integrity check. Restart Raticode" in last["body"]
        assert "restores the newest verified copy" in last["body"]
    finally:
        _close(home)


# Workflows -----------------------------------------------------------------------------


def test_workflows_report_a_damaged_store_clearly(tmp_path: Path) -> None:
    from gofer.ui.rem_workflow import RemWorkflowError, existing_store

    (tmp_path / backups.STORE_FILE).write_bytes(b"\x13" * 4096)
    with pytest.raises(RemWorkflowError) as caught:
        existing_store(tmp_path)
    assert caught.value.code == "RATTISH_REM_UNAVAILABLE"
    assert "Restart Raticode" in caught.value.message
    # A workflow process never moves Rem's files; only the backend restores them.
    assert (tmp_path / backups.STORE_FILE).read_bytes() == b"\x13" * 4096


# Command line --------------------------------------------------------------------------


def _cli(*args: str) -> Any:
    from gofer.cli.main import app

    return CliRunner().invoke(app, ["rem", "restore-db", *args])


def test_cli_lists_the_verified_copies(tmp_path: Path) -> None:
    store_with(tmp_path, "hello")
    copy = backups.snapshot(tmp_path, moment=datetime(2026, 11, 14, 3, 0, tzinfo=UTC))
    result = _cli("--list", "--format", "json", "--data-dir", str(tmp_path))
    assert result.exit_code == 0, result.output
    listed = json.loads(result.output)
    assert listed["snapshots"][0]["name"] == copy.name
    assert listed["snapshots"][0]["taken_at"] == "2026-11-14T03:00:00+00:00"


def test_cli_restores_the_newest_copy_and_keeps_the_old_file(tmp_path: Path) -> None:
    store_with(tmp_path, "saved")
    copy = backups.snapshot(tmp_path)
    store_with(tmp_path, "after the copy")
    settle(tmp_path)
    old = (tmp_path / backups.STORE_FILE).read_bytes()

    result = _cli("--format", "json", "--data-dir", str(tmp_path))

    assert result.exit_code == 0, result.output
    notice = json.loads(result.output)
    assert notice["reason"] == "replaced" and notice["restored"] == copy.name
    assert (Path(notice["kept"]) / backups.STORE_FILE).read_bytes() == old
    assert bodies(RemHomeStore(tmp_path)) == ["saved"]
    assert "rem restore-db" in backups.notice_text(backups.pending_notices(tmp_path)[0])


def test_cli_restore_is_refused_inside_an_agent_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_with(tmp_path, "saved")
    backups.snapshot(tmp_path)
    store_with(tmp_path, "after the copy")
    settle(tmp_path)
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")

    assert _cli("--list", "--data-dir", str(tmp_path)).exit_code == 0
    result = _cli("--force", "--data-dir", str(tmp_path))

    assert result.exit_code == 5, result.output
    assert "agent turn" in result.output
    assert bodies(RemHomeStore(tmp_path)) == ["saved", "after the copy"]


def test_cli_restores_a_named_copy(tmp_path: Path) -> None:
    store_with(tmp_path, "first")
    first = backups.snapshot(tmp_path, moment=datetime(2026, 11, 12, tzinfo=UTC))
    store_with(tmp_path, "second")
    backups.snapshot(tmp_path, moment=datetime(2026, 11, 13, tzinfo=UTC))
    result = _cli("--snapshot", first.name, "--data-dir", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert bodies(RemHomeStore(tmp_path)) == ["first"]
    assert _cli("--snapshot", "nope.sqlite3", "--data-dir", str(tmp_path)).exit_code == 1


def test_cli_refuses_while_raticode_holds_rem(tmp_path: Path) -> None:
    store = store_with(tmp_path, "live")
    backups.snapshot(tmp_path)
    with store.connect() as db:
        db.execute("INSERT OR REPLACE INTO lease VALUES(1,?,?)", ("backend", time.time() + 60))

    result = _cli("--data-dir", str(tmp_path))

    assert result.exit_code == 1
    assert "Quit Raticode" in result.output
    assert bodies(RemHomeStore(tmp_path)) == ["live"]
    assert _cli("--force", "--data-dir", str(tmp_path)).exit_code == 0


def test_cli_without_a_copy_changes_nothing(tmp_path: Path) -> None:
    store_with(tmp_path, "only")
    result = _cli("--data-dir", str(tmp_path))
    assert result.exit_code == 1
    assert "no verified copy" in result.output
    assert bodies(RemHomeStore(tmp_path)) == ["only"]


# A full disk ---------------------------------------------------------------------------

_real_copyfile = shutil.copyfile
_real_replace = os.replace


def _copy_step(dst: Path) -> str:
    """Which copy a ``shutil.copyfile`` call is: the check's two, or the restore's."""
    if dst.parent.name.startswith(".check-"):
        return "check-wal" if dst.name.endswith("-wal") else "check"
    if backups.PARTIAL.fullmatch(dst.name):
        return "restore"
    raise AssertionError(f"unexpected copy to {dst}")


def disk_full_at(monkeypatch: pytest.MonkeyPatch, *steps: str) -> list[str]:
    """Make ``shutil.copyfile`` raise ENOSPC at the given copy steps; return every step seen."""
    seen: list[str] = []

    def copy(src: Any, dst: Any, **kwargs: Any) -> Any:
        step = _copy_step(Path(dst))
        seen.append(step)
        if step in steps:
            # A real full disk leaves a partly written file behind.
            Path(dst).write_bytes(b"partial")
            raise OSError(errno.ENOSPC, "No space left on device", str(dst))
        return _real_copyfile(src, dst, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", copy)
    return seen


def live_files(data: Path) -> dict[str, bytes]:
    base = data / backups.STORE_FILE
    return {
        p.name: p.read_bytes()
        for p in (base, Path(f"{base}-wal"), Path(f"{base}-shm"))
        if p.exists()
    }


def aside_folders(data: Path) -> list[Path]:
    place = data / backups.FOLDER
    if not place.is_dir():
        return []
    return sorted(p for p in place.iterdir() if p.is_dir() and not p.name.startswith(".check-"))


def leftovers(data: Path) -> list[str]:
    """Temporary files and scratch folders a failed copy must not leave behind."""
    found = [p.name for p in data.iterdir() if backups.PARTIAL.fullmatch(p.name)]
    place = data / backups.FOLDER
    if place.is_dir():
        found += [p.name for p in place.iterdir() if p.name.startswith((".check-", ".rem-"))]
    return found


def damaged_with_wal(data: Path) -> dict[str, bytes]:
    """A verified copy, then a live file of random bytes with an old -wal and -shm."""
    store_with(data, "kept")
    backups.snapshot(data, moment=datetime(2026, 11, 13, 3, 0, tzinfo=UTC))
    live = data / backups.STORE_FILE
    live.write_bytes(random.Random(11).randbytes(64 * 1024))
    Path(f"{live}-wal").write_bytes(b"old wal")
    Path(f"{live}-shm").write_bytes(b"old shm")
    return live_files(data)


def test_restore_stages_a_private_copy_before_moving_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    damaged_with_wal(tmp_path)
    order: list[str] = []
    real_replace = os.replace

    def replace(src: Any, dst: Any) -> None:
        source = Path(src)
        if backups.PARTIAL.fullmatch(source.name):
            assert mode(source) == 0o600 or sys.platform == "win32"
            assert (tmp_path / backups.FOLDER / backups.RESTORING).is_file()
            order.append("swap")
        elif Path(dst).parent.parent == tmp_path / backups.FOLDER:
            assert (tmp_path / backups.FOLDER / backups.RESTORING).is_file()
            order.append(f"aside {source.name}")
        real_replace(src, dst)

    seen = disk_full_at(monkeypatch)
    monkeypatch.setattr(os, "replace", replace)

    notice = backups.restore(tmp_path, reason="damaged", error="test")

    assert seen == ["restore"]
    assert order[-1] == "swap" and len(order) == 4
    assert notice["restored"] == "rem-home-20261113-030000Z.sqlite3"
    assert not (tmp_path / backups.FOLDER / backups.RESTORING).exists()
    assert leftovers(tmp_path) == []
    assert bodies(RemHomeStore(tmp_path)) == ["kept"]


def test_a_full_disk_while_staging_leaves_the_live_files_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = damaged_with_wal(tmp_path)
    disk_full_at(monkeypatch, "restore")

    with pytest.raises(OSError) as caught:
        backups.restore(tmp_path, reason="damaged", error="test")

    assert caught.value.errno == errno.ENOSPC
    assert live_files(tmp_path) == before
    assert aside_folders(tmp_path) == []
    assert leftovers(tmp_path) == []
    assert not (tmp_path / backups.FOLDER / backups.RESTORING).exists()
    assert backups.pending_notices(tmp_path) == []


def test_a_failed_flush_counts_as_a_full_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = damaged_with_wal(tmp_path)

    def fsync(handle: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError):
        backups.restore(tmp_path, reason="damaged", error="test")
    assert live_files(tmp_path) == before
    assert leftovers(tmp_path) == []


def test_too_little_free_space_stops_before_any_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = damaged_with_wal(tmp_path)
    seen = disk_full_at(monkeypatch)
    monkeypatch.setattr(shutil, "disk_usage", lambda place: SimpleNamespace(free=4096))

    with pytest.raises(OSError) as caught:
        backups.damage_on_copy(tmp_path, full=True)
    assert caught.value.errno == errno.ENOSPC
    with pytest.raises(OSError):
        backups.restore(tmp_path, reason="damaged", error="test")

    assert seen == []
    assert live_files(tmp_path) == before
    assert leftovers(tmp_path) == []


def test_a_failed_swap_puts_the_moved_files_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = damaged_with_wal(tmp_path)
    real_replace = os.replace

    def replace(src: Any, dst: Any) -> None:
        if backups.PARTIAL.fullmatch(Path(src).name):
            raise OSError(errno.EIO, "Input/output error")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(OSError):
        backups.restore(tmp_path, reason="damaged", error="test")

    assert live_files(tmp_path) == before
    assert aside_folders(tmp_path) == []
    assert leftovers(tmp_path) == []
    assert not (tmp_path / backups.FOLDER / backups.RESTORING).exists()


def _held_wal(data: Path) -> tuple[RemHomeStore, sqlite3.Connection]:
    """A healthy store whose newest message is only in its WAL, held by a reader."""
    store = store_with(data, "settled")
    settle(data)
    holder = sqlite3.connect(data / backups.STORE_FILE)
    holder.execute("SELECT count(*) FROM settings").fetchone()
    with store.connect() as db:
        store.message(db, "only in the wal", identity="m-wal")
    assert Path(f"{data / backups.STORE_FILE}-wal").stat().st_size > 0
    return store, holder


@pytest.mark.parametrize("step", ["check", "check-wal"])
def test_a_suspect_check_that_cant_copy_opens_the_store_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    _, holder = _held_wal(tmp_path)
    with closing(holder):
        backups.mark_suspect(tmp_path, "query failed once")
        flag = (tmp_path / backups.FOLDER / backups.SUSPECT).read_bytes()
        seen = disk_full_at(monkeypatch, step)

        store = backups.open_store(tmp_path)

        assert seen[-1] == step and "restore" not in seen
        assert bodies(store) == ["settled", "only in the wal"]
    assert (tmp_path / backups.FOLDER / backups.SUSPECT).read_bytes() == flag
    assert aside_folders(tmp_path) == []
    assert leftovers(tmp_path) == []
    [notice] = backups.pending_notices(tmp_path)
    assert notice["reason"] == "unchecked" and notice["step"] == "check"
    assert notice["no_space"] is True
    text = backups.notice_text(notice)
    assert "couldn't check Rem's database" in text
    assert "isn't enough free disk space" in text
    # Posted in Rem's chat like any other notice.
    [posted] = backups.post_notices(tmp_path, store)
    assert posted["body"] == text
    assert posted["database_restore"]["reason"] == "unchecked"


def test_a_probe_check_that_cant_copy_flags_the_file_and_opens_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_with(tmp_path, "fine")
    monkeypatch.setattr(backups, "probe", lambda path: sqlite3.DatabaseError("malformed"))
    disk_full_at(monkeypatch, "check")

    store = backups.open_store(tmp_path)

    assert bodies(store) == ["fine"]
    assert (tmp_path / backups.FOLDER / backups.SUSPECT).is_file()
    assert aside_folders(tmp_path) == []
    [notice] = backups.pending_notices(tmp_path)
    assert notice["step"] == "check"


def test_a_restore_that_cant_copy_keeps_the_damaged_store_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Damage only in the messages table: the store still opens.
    store_with(tmp_path, *(f"message {n} " + "x" * 2000 for n in range(40)))
    backups.snapshot(tmp_path)
    settle(tmp_path)
    scramble_page(tmp_path / backups.STORE_FILE, root_page(tmp_path, "messages"))
    backups.mark_suspect(tmp_path, "failed its integrity check")
    seen = disk_full_at(monkeypatch, "restore")

    store = backups.open_store(tmp_path)

    assert seen == ["check", "restore"]
    # Still the damaged file, not a restored copy.
    assert not backups.quick_check(tmp_path / backups.STORE_FILE, live=True)
    assert (tmp_path / backups.FOLDER / backups.SUSPECT).is_file()
    assert aside_folders(tmp_path) == []
    assert leftovers(tmp_path) == []
    [notice] = backups.pending_notices(tmp_path)
    assert notice["step"] == "restore" and notice["damage"]
    assert "couldn't restore a verified copy" in backups.notice_text(notice)
    # The damaged messages table can't take the notice yet (Rem logs that); it stays
    # pending until a start that can restore.
    with pytest.raises(sqlite3.DatabaseError):
        backups.post_notices(tmp_path, store)
    monkeypatch.setattr(shutil, "copyfile", _real_copyfile)
    restored = backups.open_store(tmp_path)
    assert len(bodies(restored)) == 40
    assert not (tmp_path / backups.FOLDER / backups.SUSPECT).exists()
    first, second = backups.post_notices(tmp_path, restored)
    assert "couldn't restore" in first["body"] and "restored the verified copy" in second["body"]


@pytest.mark.parametrize("steps", [("check", "restore"), ("restore",)])
def test_an_unreadable_file_on_a_full_disk_keeps_its_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps: tuple[str, ...]
) -> None:
    before = damaged_with_wal(tmp_path)
    disk_full_at(monkeypatch, *steps)

    with pytest.raises(sqlite3.DatabaseError):
        backups.open_store(tmp_path)

    if steps == ("restore",):
        # Confirmed damage that couldn't be restored: the files aren't opened at all.
        assert live_files(tmp_path) == before
    assert live_files(tmp_path)[backups.STORE_FILE] == before[backups.STORE_FILE]
    assert aside_folders(tmp_path) == []
    assert leftovers(tmp_path) == []
    assert (tmp_path / backups.FOLDER / backups.SUSPECT).is_file()
    # Space freed: the next start restores the copy.
    monkeypatch.setattr(shutil, "copyfile", _real_copyfile)
    assert bodies(backups.open_store(tmp_path)) == ["kept"]


def test_a_check_that_cant_run_then_a_restore_posts_only_the_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    damaged_with_wal(tmp_path)
    disk_full_at(monkeypatch, "check")

    store = backups.open_store(tmp_path)

    assert bodies(store) == ["kept"]
    [notice] = backups.pending_notices(tmp_path)
    assert notice["reason"] == "damaged" and notice["restored"]
    assert not (tmp_path / backups.FOLDER / backups.SUSPECT).exists()


def test_a_notice_that_cant_be_saved_is_still_posted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_with(tmp_path, "fine")
    backups.mark_suspect(tmp_path, "query failed once")
    disk_full_at(monkeypatch, "check")

    def full(path: Path, value: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(backups, "_write_json", full)

    store = backups.open_store(tmp_path)

    assert not (tmp_path / backups.FOLDER / backups.NOTICES).exists()
    [posted] = backups.post_notices(tmp_path, store)
    assert "couldn't check" in posted["body"]
    assert backups.pending_notices(tmp_path) == []


def test_cli_on_a_full_disk_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_with(tmp_path, "live")
    backups.snapshot(tmp_path)
    settle(tmp_path)
    before = live_files(tmp_path)
    for steps in (("check",), ("restore",)):
        disk_full_at(monkeypatch, *steps)
        result = _cli("--data-dir", str(tmp_path))
        assert result.exit_code == 1, result.output
        assert "No space left on device" in result.output
        assert live_files(tmp_path)[backups.STORE_FILE] == before[backups.STORE_FILE]
        assert aside_folders(tmp_path) == []
        assert leftovers(tmp_path) == []


# A restore that stopped part way --------------------------------------------------------


class Stopped(BaseException):
    """Stands in for the process being killed at that point."""


def _stop_at(monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    real_replace = os.replace

    def replace(src: Any, dst: Any) -> None:
        source = Path(src)
        if point == "swap" and backups.PARTIAL.fullmatch(source.name):
            raise Stopped
        if point == "aside" and Path(dst).parent.name.startswith("damaged-"):
            if source.name.endswith("-wal"):
                raise Stopped
        if point == "notice" and Path(dst).name == backups.NOTICES:
            raise Stopped
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)


@pytest.mark.parametrize("point", ["aside", "swap", "notice"])
def test_a_start_after_a_stopped_restore_finishes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    before = damaged_with_wal(tmp_path)
    _stop_at(monkeypatch, point)
    with pytest.raises(Stopped):
        backups.restore(tmp_path, reason="damaged", error="test")
    assert (tmp_path / backups.FOLDER / backups.RESTORING).is_file()
    monkeypatch.setattr(os, "replace", _real_replace)

    store = backups.open_store(tmp_path)

    assert bodies(store) == ["kept"]
    assert not (tmp_path / backups.FOLDER / backups.RESTORING).exists()
    [notice] = backups.pending_notices(tmp_path)
    assert notice["restored"] == "rem-home-20261113-030000Z.sqlite3"
    # The damaged files are kept together, with the old -wal never applied to the copy.
    [kept] = [p for p in aside_folders(tmp_path) if p.name.startswith("damaged-")]
    assert live_files(kept) == before
    assert notice["kept"] == str(kept)


def test_a_restore_stopped_before_anything_moved_starts_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = damaged_with_wal(tmp_path)

    def stop(files: list[Path], target: Path) -> None:
        raise Stopped

    monkeypatch.setattr(backups, "_move_into", stop)
    with pytest.raises(Stopped):
        backups.restore(tmp_path, reason="damaged", error="test")
    assert live_files(tmp_path) == before
    monkeypatch.undo()

    store = backups.open_store(tmp_path)

    assert bodies(store) == ["kept"]
    [notice] = backups.pending_notices(tmp_path)
    assert notice["restored"] == "rem-home-20261113-030000Z.sqlite3"
    assert not (tmp_path / backups.FOLDER / backups.RESTORING).exists()


def test_a_missing_file_after_a_newer_damaged_folder_restores_the_snapshot(
    tmp_path: Path,
) -> None:
    store_with(tmp_path, "kept")
    backups.snapshot(tmp_path, moment=datetime(2026, 11, 13, 3, 0, tzinfo=UTC))
    # A restore from before restoring.json existed, stopped after moving the file.
    backups.move_aside(tmp_path, "damaged", moment=datetime(2026, 11, 14, 9, 0, tzinfo=UTC))
    assert not (tmp_path / backups.STORE_FILE).exists()

    store = backups.open_store(tmp_path)

    assert bodies(store) == ["kept"]
    [notice] = backups.pending_notices(tmp_path)
    assert notice["restored"] == "rem-home-20261113-030000Z.sqlite3"
    assert notice["kept"].endswith("damaged-20261114-090000Z")
    assert "restored the verified copy" in backups.notice_text(notice)


def test_a_missing_file_after_an_older_damaged_folder_starts_empty(tmp_path: Path) -> None:
    store_with(tmp_path, "kept")
    backups.move_aside(tmp_path, "damaged", moment=datetime(2026, 11, 12, 9, 0, tzinfo=UTC))
    store_with(tmp_path, "later")
    backups.snapshot(tmp_path, moment=datetime(2026, 11, 13, 3, 0, tzinfo=UTC))
    (tmp_path / backups.STORE_FILE).unlink()
    for suffix in ("-wal", "-shm"):
        Path(f"{tmp_path / backups.STORE_FILE}{suffix}").unlink(missing_ok=True)

    store = backups.open_store(tmp_path)

    assert bodies(store) == []
    assert backups.pending_notices(tmp_path) == []


def test_finishing_a_stopped_restore_on_a_full_disk_never_starts_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    damaged_with_wal(tmp_path)
    _stop_at(monkeypatch, "swap")
    with pytest.raises(Stopped):
        backups.restore(tmp_path, reason="damaged", error="test")
    monkeypatch.setattr(os, "replace", _real_replace)
    disk_full_at(monkeypatch, "restore")

    with pytest.raises(OSError) as caught:
        backups.open_store(tmp_path)

    assert "Free up disk space" in str(caught.value)
    assert not (tmp_path / backups.STORE_FILE).exists()
    assert (tmp_path / backups.FOLDER / backups.RESTORING).is_file()


def test_a_snapshot_on_a_nearly_full_disk_stops_before_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_with(tmp_path, "hello")
    first = backups.snapshot(tmp_path)
    # Less room than the database plus headroom: nothing is copied, the old copy stays.
    monkeypatch.setattr(
        shutil, "disk_usage", lambda place: SimpleNamespace(free=backups.SNAPSHOT_MARGIN)
    )
    with pytest.raises(OSError) as caught:
        backups.snapshot(tmp_path, moment=datetime.now(UTC) + timedelta(days=1))
    assert caught.value.errno == errno.ENOSPC
    assert backups.snapshots(tmp_path) == [first]
    assert sorted(p.name for p in first.parent.iterdir()) == [first.name]


def test_copies_taken_before_a_clear_are_replaced_by_one_without_the_history(
    tmp_path: Path,
) -> None:
    store = store_with(tmp_path, "cleared secret")
    for day in range(3):
        backups.snapshot(tmp_path, moment=datetime(2026, 11, 10 + day, tzinfo=UTC))
    # Clear history deletes with secure_delete and vacuums, as RemHome.clear_history does.
    with store.connect() as db:
        db.execute("PRAGMA secure_delete=ON")
        db.execute("DELETE FROM messages")
    with closing(sqlite3.connect(tmp_path / backups.STORE_FILE)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("VACUUM")

    fresh = backups.replace_after_clear(tmp_path)

    assert fresh is not None and backups.snapshots(tmp_path) == [fresh]
    with closing(sqlite3.connect(fresh)) as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
