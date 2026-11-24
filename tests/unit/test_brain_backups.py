"""Scheduled, verified complete-brain backups and the optional conversation archive."""

from __future__ import annotations

import errno
import json
import os
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_library as library_module
from gofer.ui.brain_backups import (
    PARTIAL,
    backup_health,
    backup_path,
    backup_warning,
    due_key,
    existing_backups,
    file_prefix,
    retained,
    smaller_counts,
    validate_backup,
    verify_package,
)
from gofer.ui.brain_library import BrainLibrary
from gofer.utils import drive_format as drive_format_module


def wait_task(library: BrainLibrary, task: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        receipt = library.task(task["id"])
        if receipt["state"] != "running":
            return receipt
        time.sleep(0.02)
    pytest.fail("Second Brain task did not finish")


def settle(library: BrainLibrary) -> None:
    for task in library.snapshot()["tasks"]:
        wait_task(library, task)


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def archived_brain(library: BrainLibrary, tmp_path: Path) -> dict[str, Any]:
    """A brain with notes plus a Rem conversation archive, like the real vault."""
    root = tmp_path / "brain"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\nThe index keeps exact evidence.\n")
    archive = root / "Rem Conversations"
    (archive / "threads").mkdir(parents=True)
    (archive / "README.raticode.md").write_text("# Rem conversation archive\n")
    (archive / "index.json").write_text(json.dumps({"threads": []}))
    (archive / "threads" / "chat.md").write_text("# Chat\n\nArchived conversation words.\n")
    (archive / "threads" / "cited.md").write_text("# Cited chat\n\nA quoted decision.\n")
    (archive / "threads" / "big.jsonl").write_bytes(b"x" * 4096)
    brain = library.open({"root": str(root)})
    settle(library)
    return brain


def doc_at(library: BrainLibrary, brain: dict[str, Any], path: str) -> dict[str, Any]:
    return next(doc for doc in library.entities("documents", brain["id"]) if doc["path"] == path)


def configure(library: BrainLibrary, brain: dict[str, Any], **backup: Any) -> dict[str, Any]:
    return library.update_brain({"id": brain["id"], "patch": {"backup": backup}})


# ---------------------------------------------------------------- settings


def test_backup_settings_are_checked_and_kept_outside_the_brain(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    folder = tmp_path / "backups"
    checked = validate_backup({"enabled": True, "folder": str(folder), "keep": 3}, root)
    assert checked == {
        "enabled": True,
        "folder": str(folder.resolve()),
        "frequency": "daily",
        "weekday": 0,
        "time": "03:00",
        "keep": 3,
        "conversations": False,
    }
    for bad, message in [
        ({"folder": str(root / "Backups")}, "outside the brain"),
        ({"folder": str(root)}, "outside the brain"),
        ({"folder": "relative/backups"}, "absolute"),
        ({"enabled": True}, "Choose a backup folder"),
        ({"folder": str(folder), "keep": 0}, "Keep between"),
        ({"folder": str(folder), "keep": True}, "Keep between"),
        ({"folder": str(folder), "time": "25:00"}, "HH:MM"),
        ({"folder": str(folder), "frequency": "hourly"}, "daily or weekly"),
        ({"folder": str(folder), "weekday": 7}, "day of the week"),
        ({"folder": str(folder), "conversations": "yes"}, "on or off"),
        ({"folder": str(folder), "command": "rm"}, "Unknown backup settings"),
    ]:
        with pytest.raises(ValueError, match=message):
            validate_backup(bad, root)


def test_backup_settings_stay_on_this_computer_and_start_the_warning_clock(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    updated = configure(library, brain, enabled=True, folder=str(folder))
    assert updated["backup"]["folder"] == str(folder.resolve())
    assert updated["backupStatus"]["enabledAt"]
    portable = json.loads((Path(brain["root"]) / ".raticode/brain.json").read_text())
    assert "backup" not in portable["brain"] and "backupStatus" not in portable["brain"]
    snapshot_brain = library.snapshot()["brains"][0]
    assert snapshot_brain["backup"]["enabled"] is True
    assert snapshot_brain["backupWarning"] == ""
    with pytest.raises(ValueError, match="outside the brain"):
        configure(library, brain, folder=str(Path(brain["root"]) / "copies"))


# ---------------------------------------------------------------- schedule


def test_daily_and_weekly_backups_fall_due_once_per_period():
    daily = {"enabled": True, "folder": "/b", "frequency": "daily", "weekday": 0, "time": "03:00"}
    wednesday = datetime(2026, 11, 11, 2, 59)
    assert due_key(daily, wednesday) is None
    assert due_key(daily, wednesday.replace(hour=3, minute=0)) == "2026-11-11"
    # A computer that was asleep at 03:00 backs up when it wakes the same day.
    assert due_key(daily, wednesday.replace(hour=22)) == "2026-11-11"
    assert due_key({**daily, "enabled": False}, wednesday.replace(hour=4)) is None
    assert due_key({**daily, "folder": ""}, wednesday.replace(hour=4)) is None

    weekly = {**daily, "frequency": "weekly", "weekday": 2, "time": "21:00"}
    assert due_key(weekly, datetime(2026, 11, 10, 23, 0)) is None  # Tuesday
    assert due_key(weekly, datetime(2026, 11, 11, 20, 59)) is None
    assert due_key(weekly, datetime(2026, 11, 11, 21, 0)) == "2026-W46"
    # Missed on Wednesday: still due later that week, with the same period key.
    assert due_key(weekly, datetime(2026, 11, 15, 9, 0)) == "2026-W46"
    assert due_key(weekly, datetime(2026, 11, 16, 9, 0)) is None  # next Monday


def test_warning_after_seven_days_without_a_verified_backup():
    now = datetime(2026, 11, 13, 12, tzinfo=UTC)
    fresh = {"backupStatus": {"lastVerified": {"at": (now - timedelta(days=6)).isoformat()}}}
    assert backup_warning(fresh, now) == ""
    stale = {"backupStatus": {"lastVerified": {"at": (now - timedelta(days=9)).isoformat()}}}
    assert backup_warning(stale, now) == "No verified backup in 9 days."
    # Never backed up and never turned on: nothing was promised, so no warning.
    assert backup_warning({}, now) == ""
    enabled = {
        "backup": {"enabled": True, "folder": "/b"},
        "backupStatus": {"enabledAt": (now - timedelta(days=8)).isoformat()},
    }
    assert "turned on 8 days ago" in backup_warning(enabled, now)
    recent = {**enabled, "backupStatus": {"enabledAt": (now - timedelta(days=2)).isoformat()}}
    assert backup_warning(recent, now) == ""


def test_scheduler_starts_one_backup_per_period(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    configure(library, brain, enabled=True, folder=str(folder), time="03:00")
    library._scheduled_backups(datetime(2026, 11, 13, 2, 0).astimezone())
    assert not [t for t in library.snapshot()["tasks"] if t["kind"] == "backup"]
    library._scheduled_backups(datetime(2026, 11, 13, 9, 0).astimezone())
    library._scheduled_backups(datetime(2026, 11, 13, 9, 1).astimezone())
    tasks = [t for t in library.snapshot()["tasks"] if t["kind"] == "backup"]
    assert len(tasks) == 1
    assert wait_task(library, tasks[0])["state"] == "done"
    assert library.brain(brain["id"])["backupStatus"]["lastScheduled"] == "2026-11-13"
    assert len(existing_backups(folder, library.brain(brain["id"]))) == 1


# ---------------------------------------------------------------- export


def test_complete_export_can_leave_out_the_conversation_archive(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    cited = doc_at(library, brain, "Rem Conversations/threads/cited.md")
    library.mutate(
        {
            "kind": "citations",
            "id": "c1",
            "item": {
                "id": "c1",
                "brainId": brain["id"],
                "docId": cited["id"],
                "version": cited["version"],
                "blockId": cited["blocks"][-1]["id"],
                "quote": "A quoted decision",
            },
        }
    )
    target = tmp_path / "without.zip"
    result = library.export_package(
        {"brainId": brain["id"], "destination": str(target), "conversations": False},
        threading.Event(),
        lambda _: None,
    )
    assert result["verified"] and result["conversations"] is False
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
    assert "files/notes.md" in names
    # Cited evidence stays, so the citation still resolves after a restore.
    assert "files/Rem Conversations/threads/cited.md" in names
    assert "files/Rem Conversations/threads/chat.md" not in names
    assert "files/Rem Conversations/threads/big.jsonl" not in names
    assert "files/Rem Conversations/index.json" not in names
    paths = {doc["path"] for doc in manifest["library"]["documents"]}
    assert paths == {"notes.md", "Rem Conversations/threads/cited.md"}
    assert verify_package(target)["files"] == len(manifest["files"])

    restored = library.import_package(
        {"path": str(target), "destination": str(tmp_path / "copy"), "identity": "copy"},
        threading.Event(),
        lambda _: None,
    )
    settle(library)
    citation = library.entities("citations", restored["brainId"])[0]
    restored_doc = library.entity("documents", citation["docId"])
    assert restored_doc["path"] == "Rem Conversations/threads/cited.md"
    assert not restored_doc.get("missing")

    full = tmp_path / "with.zip"
    library.export_package(
        {"brainId": brain["id"], "destination": str(full)}, threading.Event(), lambda _: None
    )
    with zipfile.ZipFile(full) as archive:
        assert "files/Rem Conversations/threads/big.jsonl" in archive.namelist()


def test_oversized_package_is_refused_before_reading_and_names_the_archive(
    library, tmp_path, monkeypatch
):
    brain = archived_brain(library, tmp_path)
    estimate = library.backup_estimate(brain["id"])
    assert estimate["archives"] == ["Rem Conversations"]
    assert estimate["conversations"] >= 4096
    assert estimate["total"] > estimate["conversations"]
    monkeypatch.setattr(library_module, "MAX_PACKAGE", estimate["total"] - 1)
    reads: list[str] = []
    real_read = library_module.read_source

    def counted(root: Path, relative: str, *args: Any) -> bytes:
        reads.append(relative)
        return real_read(root, relative, *args)

    monkeypatch.setattr(library_module, "read_source", counted)
    with pytest.raises(ValueError, match="Leave out Rem's conversation archive"):
        library.export_package(
            {"brainId": brain["id"], "destination": str(tmp_path / "too-big.zip")},
            threading.Event(),
            lambda _: None,
        )
    assert reads == []
    assert not (tmp_path / "too-big.zip").exists()
    # Without the archive the same brain fits under the limit.
    monkeypatch.setattr(library_module, "read_source", real_read)
    library.export_package(
        {
            "brainId": brain["id"],
            "destination": str(tmp_path / "fits.zip"),
            "conversations": False,
        },
        threading.Event(),
        lambda _: None,
    )


# ---------------------------------------------------------------- backups


def test_backup_now_writes_a_verified_package_and_keeps_the_last_copies(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    configure(library, brain, folder=str(folder), keep=2)
    current = library.brain(brain["id"])
    folder.mkdir()
    unrelated = folder / "holiday-photos.zip"
    unrelated.write_bytes(b"not a backup")
    other_brain = {"id": "brain-other", "name": current["name"]}
    foreign = folder / f"{file_prefix(other_brain)}-backup-20200101-000000Z.zip"
    foreign.write_bytes(b"another brain's backup")

    paths = []
    for _ in range(3):
        task = library.start_task("backup", {"brainId": brain["id"]})
        receipt = wait_task(library, task)
        assert receipt["state"] == "done", receipt.get("error")
        assert receipt["result"]["verified"] is True
        paths.append(Path(receipt["result"]["path"]))

    kept = existing_backups(folder, library.brain(brain["id"]))
    assert kept == sorted(paths[1:], key=lambda p: p.name, reverse=True)
    assert not paths[0].exists()
    assert unrelated.exists() and foreign.exists()
    assert oct(paths[-1].stat().st_mode & 0o777) == "0o600"
    status = library.brain(brain["id"])["backupStatus"]
    assert status["lastVerified"]["path"] == str(paths[-1])
    assert status["lastVerified"]["conversations"] is False
    assert status["lastAttempt"]["state"] == "verified"
    with zipfile.ZipFile(paths[-1]) as archive:
        assert "files/Rem Conversations/threads/chat.md" not in archive.namelist()
    assert library.snapshot()["brains"][0]["backupWarning"] == ""


def test_a_backup_that_fails_verification_is_deleted_and_recorded(library, tmp_path, monkeypatch):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    configure(library, brain, folder=str(folder))

    def corrupt(path: Path) -> dict[str, Any]:
        raise ValueError("Backup verification failed: files/notes.md does not match")

    monkeypatch.setattr(library_module, "verify_package", corrupt)
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    assert receipt["state"] == "failed"
    assert existing_backups(folder, library.brain(brain["id"])) == []
    assert not list(folder.glob("*.zip"))
    status = library.brain(brain["id"])["backupStatus"]
    assert "lastVerified" not in status
    assert status["lastAttempt"]["state"] == "failed"
    assert "does not match" in status["lastAttempt"]["error"]


def test_backup_needs_a_folder_and_rechecks_it_after_the_brain_moves(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    assert receipt["state"] == "failed"
    assert "Choose a backup folder" in receipt["error"]
    # A folder that ends up inside the brain (edited record, moved brain) is refused.
    with library.lock:
        record = library.brain(brain["id"])
        record["backup"] = {"folder": str(Path(brain["root"]) / "inside")}
        library.put_brain(record)
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    assert receipt["state"] == "failed"
    assert "outside the brain" in receipt["error"]
    assert not (Path(brain["root"]) / "inside").exists()


def test_backup_restores_with_the_same_identity(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    configure(library, brain, folder=str(folder), conversations=True)
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    package = Path(receipt["result"]["path"])
    assert receipt["result"]["conversations"] is True
    other = BrainLibrary(tmp_path / "other-computer", start=False)
    try:
        restored = other.import_package(
            {
                "path": str(package),
                "destination": str(tmp_path / "restored"),
                "identity": "restore",
            },
            threading.Event(),
            lambda _: None,
        )
        assert restored["brainId"] == brain["id"]
        assert (tmp_path / "restored/Rem Conversations/threads/big.jsonl").stat().st_size == 4096
    finally:
        other.close()


def test_copies_made_before_a_rename_are_still_pruned(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    configure(library, brain, folder=str(folder), keep=2)
    first = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    old_name = Path(first["result"]["path"])
    library.update_brain({"id": brain["id"], "patch": {"name": "Renamed Field Notes"}})
    paths = [old_name]
    for _ in range(2):
        receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
        assert receipt["state"] == "done", receipt.get("error")
        paths.append(Path(receipt["result"]["path"]))
    assert paths[-1].name.startswith("renamed-field-notes-")
    assert not old_name.exists()
    assert existing_backups(folder, library.brain(brain["id"])) == [paths[2], paths[1]]


def test_backup_names_sort_by_time_not_by_brain_name(tmp_path):
    brain = {"id": "brain-1", "name": "Zebra"}
    tag = file_prefix(brain).rsplit("-", 1)[1]
    older = tmp_path / f"zebra-{tag}-backup-20260101-000000Z.zip"
    newer = tmp_path / f"apple-{tag}-backup-20260201-000000Z.zip"
    other = tmp_path / f"zebra-{'0' * 8}-backup-20260301-000000Z.zip"
    for path in (older, newer, other):
        path.write_bytes(b"x")
    assert existing_backups(tmp_path, brain) == [newer, older]


def test_unfinished_files_from_a_killed_backup_are_cleaned_up(library, tmp_path, monkeypatch):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    folder.mkdir()
    configure(library, brain, folder=str(folder))
    tag = file_prefix(library.brain(brain["id"]))
    staging = folder / ".raticode-export-k1ll3d_x.zip.partial"
    publishing = folder / f".{tag}-backup-20260101-000000Z.zip.{'a' * 32}.tmp"
    fresh = folder / ".raticode-export-stillgoing.zip.partial"
    unrelated = folder / ".raticode-export-notes.txt"
    for path in (staging, publishing, fresh, unrelated):
        path.write_bytes(b"partial")
    hours_ago = time.time() - 3 * 60 * 60
    for path in (staging, publishing, unrelated):
        os.utime(path, (hours_ago, hours_ago))

    staged: list[str] = []
    real_check = library_module.checked_archive

    def recording(path: Path) -> Any:
        staged.append(path.name)
        return real_check(path)

    monkeypatch.setattr(library_module, "checked_archive", recording)
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    assert receipt["state"] == "done", receipt.get("error")
    assert not staging.exists() and not publishing.exists()
    # A recent file may be another brain's backup still writing; other names are never touched.
    assert fresh.exists() and unrelated.exists()
    # The exporter's own staging file has the name the cleanup recognizes.
    assert staged and PARTIAL.fullmatch(staged[0])
    assert not [name for name in os.listdir(folder) if name.startswith(".raticode-export-k")]


# ---------------------------------------------------------------- the last good copy


def backup_now(library: BrainLibrary, brain: dict[str, Any], **params: Any) -> dict[str, Any]:
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"], **params}))
    assert receipt["state"] == "done", receipt.get("error")
    result: dict[str, Any] = receipt["result"]
    return result


def empty_brain_folder(library: BrainLibrary, brain: dict[str, Any]) -> None:
    """Delete every note and file, as a bad sync or a mistaken delete would, then rescan."""
    root = Path(brain["root"])
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if ".raticode" in path.relative_to(root).parts:
            continue
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    wait_task(library, library.start_task("index", {"brainId": brain["id"], "force": True}))
    settle(library)


def package_paths(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return {name for name in archive.namelist() if name.startswith("files/")}


def test_emptied_brain_folder_never_rotates_out_the_last_full_copy(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    configure(library, brain, folder=str(folder), keep=1, conversations=True)
    full = backup_now(library, brain)
    full_path = Path(full["path"])
    assert "files/notes.md" in package_paths(full_path)

    empty_brain_folder(library, brain)
    assert not [p for p in Path(brain["root"]).iterdir() if p.name != ".raticode"]
    empties = [backup_now(library, brain) for _ in range(3)]

    # With keep=1 each of these would have deleted the copy before it.
    assert full_path.exists()
    assert "files/notes.md" in package_paths(full_path)
    assert verify_package(full_path)["files"] == full["files"]
    for result in empties:
        assert result["kept"] is True and result["removed"] == []
        assert Path(result["path"]).exists()
        assert {"files", "documents", "bytes"} >= set(result["smaller"]) and result["smaller"]
    kept = existing_backups(folder, library.brain(brain["id"]))
    assert len(kept) == 4 and kept[-1] == full_path

    status = library.brain(brain["id"])["backupStatus"]
    # Later backups keep comparing with the full copy, not with the empty ones.
    assert status["lastVerified"]["path"] == str(full_path)
    assert status["lastAttempt"]["state"] == "kept"
    assert status["lastAttempt"]["detail"] == "kept, much smaller than the last backup"
    assert status["lastAttempt"]["path"] == empties[-1]["path"]
    assert status["lastAttempt"]["files"] < full["files"]

    snapshot = library.snapshot()
    shown = snapshot["brains"][0]
    assert "much smaller than the last full backup" in shown["backupWarning"]
    issues = [issue for issue in snapshot["health"] if issue["kind"] == "backup"]
    assert len(issues) == 1
    assert issues[0]["brainId"] == brain["id"]
    assert issues[0]["lastVerified"]["files"] == full["files"]
    assert issues[0]["newest"]["files"] == empties[-1]["files"]
    assert f"{full['files']:,} files" in issues[0]["detail"]


def test_accepting_the_smaller_size_prunes_again(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    configure(library, brain, folder=str(folder), keep=1)
    full = Path(backup_now(library, brain)["path"])
    empty_brain_folder(library, brain)
    assert backup_now(library, brain)["kept"] is True

    accepted = backup_now(library, brain, acceptSmaller=True)
    assert "kept" not in accepted
    status = library.brain(brain["id"])["backupStatus"]
    assert status["lastVerified"]["path"] == accepted["path"]
    assert status["lastAttempt"]["state"] == "verified"
    assert not [issue for issue in library.snapshot()["health"] if issue["kind"] == "backup"]
    # keep=1, and the accepted copy is the newest of this week and month, so the
    # older copies from the same week go.
    assert existing_backups(folder, library.brain(brain["id"])) == [Path(accepted["path"])]
    assert not full.exists()
    # The next backup of the same small brain compares with the accepted one.
    assert "kept" not in backup_now(library, brain)


def test_a_copy_is_much_smaller_when_any_count_falls_below_half():
    last = {"files": 100, "documents": 40, "size": 10_000}
    assert smaller_counts({"files": 50, "documents": 20, "size": 5_000}, last) == []
    assert smaller_counts({"files": 49, "documents": 40, "size": 10_000}, last) == ["files"]
    assert smaller_counts({"files": 100, "documents": 19, "size": 4_999}, last) == [
        "documents",
        "bytes",
    ]
    assert smaller_counts({"files": 0, "documents": 0, "size": 0}, None) == []
    # A record from before documents were counted never blocks pruning on that count.
    assert smaller_counts({"files": 100, "documents": 0, "size": 10_000}, {"files": 100}) == []


def test_tiers_keep_the_newest_copy_of_recent_weeks_and_months(tmp_path):
    brain = {"id": "brain-1", "name": "Notes"}
    # Midday UTC, so no local time zone moves a copy into another week or month.
    days = [datetime(2026, 11, 23, 12, tzinfo=UTC) - timedelta(days=n) for n in range(0, 120, 3)]
    paths = [backup_path(tmp_path, brain, day) for day in days]
    for path in paths:
        path.write_bytes(b"x")
    found = existing_backups(tmp_path, brain)
    assert found == paths
    claimed = retained(found, 2, found[0])

    def newest_per(key) -> list[Path]:
        seen: dict[Any, Path] = {}
        for path, day in zip(paths, days, strict=True):
            seen.setdefault(key(day.astimezone()), path)
        return list(seen.values())

    weeks = newest_per(lambda day: day.isocalendar()[:2])[:4]
    months = newest_per(lambda day: (day.year, day.month))[:3]
    assert claimed == {paths[0], paths[1], *weeks, *months}
    assert len(claimed) < len(paths)
    # A copy no tier claims goes; one the monthly tier claims stays, however old.
    assert paths[-1] not in claimed
    assert months[-1] in claimed and months[-1] not in paths[:2] + weeks


def test_pruning_follows_the_tiers(library, tmp_path):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "backups"
    folder.mkdir()
    configure(library, brain, folder=str(folder), keep=1)
    record = library.brain(brain["id"])
    start = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    old = [backup_path(folder, record, start - timedelta(days=n)) for n in (40, 75, 400)]
    for path in old:
        path.write_bytes(b"old copy")
    newest = Path(backup_now(library, brain)["path"])
    claimed = retained([newest, *old], 1, newest)
    survivors = set(existing_backups(folder, library.brain(brain["id"])))
    assert survivors == claimed
    # 40 days back is always in one of the last 3 calendar months; a year back never is.
    assert old[0] in survivors and newest in survivors
    assert old[2] not in survivors


def test_kept_backup_health_names_both_counts():
    brain = {
        "id": "brain-1",
        "backupStatus": {
            "lastVerified": {"files": 1240, "documents": 900, "size": 3 * 1024 * 1024},
            "lastAttempt": {
                "at": "2026-11-23T03:00:00+00:00",
                "state": "kept",
                "path": "/backups/notes-backup.zip",
                "files": 2,
                "documents": 0,
                "size": 900,
            },
        },
    }
    issue = backup_health(brain)
    assert issue is not None and issue["kind"] == "backup"
    assert "2 files, 0 documents, 900 bytes" in issue["detail"]
    assert "1,240 files, 900 documents, 3.0 MB" in issue["detail"]
    assert (
        backup_health({"id": "b", "backupStatus": {"lastAttempt": {"state": "verified"}}}) is None
    )
    assert "much smaller" in backup_warning(brain, datetime(2026, 12, 30, tzinfo=UTC))


# ---------------------------------------------------------------- FAT and exFAT drives


def no_hard_links(monkeypatch) -> None:
    """os.link as a FAT or exFAT drive answers it on Linux."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)


def test_backups_and_exports_work_on_drives_without_hard_links(library, tmp_path, monkeypatch):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "usb"
    folder.mkdir()
    no_hard_links(monkeypatch)
    # Choosing the folder runs the probe through the same publication path.
    configure(library, brain, folder=str(folder))
    assert list(folder.iterdir()) == []

    result = backup_now(library, brain)
    assert result["verified"] is True
    saved = Path(result["path"])
    assert verify_package(saved)["files"] > 0
    assert [path.name for path in folder.iterdir()] == [saved.name]

    export = tmp_path / "usb" / "export.zip"
    library.export_package(
        {"brainId": brain["id"], "destination": str(export)}, threading.Event(), lambda _: None
    )
    assert verify_package(export)["files"] > 0


def test_choosing_an_unwritable_backup_folder_names_the_drive_format(
    library, tmp_path, monkeypatch
):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "usb"
    folder.mkdir()
    real_open = os.open

    def mounted_for_someone_else(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & os.O_CREAT and "raticode-backup-probe" in str(path):
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", mounted_for_someone_else)
    monkeypatch.setattr(drive_format_module, "drive_format", lambda _: "exFAT")
    with pytest.raises(ValueError) as raised:
        configure(library, brain, folder=str(folder))
    message = str(raised.value)
    assert message.startswith(f"Couldn't write to {folder} on this exFAT drive: permission denied.")
    assert "Errno" not in message
    assert "mounted for another user" in message
    assert library.brain(brain["id"]).get("backup", {}).get("folder", "") == ""
    assert list(folder.iterdir()) == []

    # Saving other settings doesn't probe again, and a folder not plugged in yet is kept.
    missing = tmp_path / "unplugged" / "Backups"
    configure(library, brain, folder=str(missing))
    assert not missing.exists()
    assert library.brain(brain["id"])["backup"]["folder"] == str(missing)


def test_a_failed_backup_write_explains_the_drive_limit(library, tmp_path, monkeypatch):
    brain = archived_brain(library, tmp_path)
    folder = tmp_path / "usb"
    configure(library, brain, folder=str(folder))

    def too_large(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EFBIG, "File too large")

    monkeypatch.setattr(library_module.shutil, "copyfileobj", too_large)
    monkeypatch.setattr(drive_format_module, "drive_format", lambda _: "FAT32")
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    assert receipt["state"] == "failed"
    error = library.brain(brain["id"])["backupStatus"]["lastAttempt"]["error"]
    assert error == receipt["error"]
    assert f"{folder} on this FAT32 drive: file too large." in error
    assert "can't hold a file over 4 GB" in error
    assert "Errno" not in error
    assert not [path for path in folder.iterdir() if not PARTIAL.fullmatch(path.name)]


# ---------------------------------------------------------------- files that move


def test_backup_skips_a_save_in_progress_and_a_file_removed_after_the_walk(
    library, tmp_path, monkeypatch
):
    """Rem saving a note renames its temporary file while the backup reads the folder."""
    brain = archived_brain(library, tmp_path)
    root = Path(brain["root"])
    temporary = root / f".rem-note.md.{'0123456789abcdef' * 2}.tmp"
    temporary.write_text("# Half written\n")
    (root / "attachment.bin").write_bytes(b"stray file, not a document")
    real_read = library_module.read_source

    def racing(base: Path, relative: str, *args: Any) -> bytes:
        if relative == temporary.name and temporary.exists():
            temporary.rename(root / "rem-note.md")
        if relative == "attachment.bin":
            (root / relative).unlink(missing_ok=True)
        return real_read(base, relative, *args)

    monkeypatch.setattr(library_module, "read_source", racing)
    folder = tmp_path / "backups"
    configure(library, brain, folder=str(folder))
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    assert receipt["state"] == "done", receipt.get("error")
    [copy] = existing_backups(folder, library.brain(brain["id"]))
    with zipfile.ZipFile(copy) as archive:
        names = archive.namelist()
    assert "files/notes.md" in names
    assert not [name for name in names if name.endswith(".tmp") or "attachment.bin" in name]


def test_backup_still_fails_when_a_library_document_disappears(library, tmp_path, monkeypatch):
    brain = archived_brain(library, tmp_path)
    real_read = library_module.read_source

    def vanished(base: Path, relative: str, *args: Any) -> bytes:
        if relative == "notes.md":
            raise FileNotFoundError(2, "No such file or directory", relative)
        return real_read(base, relative, *args)

    monkeypatch.setattr(library_module, "read_source", vanished)
    folder = tmp_path / "backups"
    configure(library, brain, folder=str(folder))
    receipt = wait_task(library, library.start_task("backup", {"brainId": brain["id"]}))
    assert receipt["state"] == "failed"
    assert existing_backups(folder, library.brain(brain["id"])) == []
