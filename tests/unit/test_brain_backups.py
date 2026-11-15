"""Scheduled, verified complete-brain backups and the optional conversation archive."""

from __future__ import annotations

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
    backup_warning,
    due_key,
    existing_backups,
    file_prefix,
    validate_backup,
    verify_package,
)
from gofer.ui.brain_library import BrainLibrary


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
