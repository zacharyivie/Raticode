"""Scheduled, verified complete-brain backups.

A backup is the existing complete-brain package written to a folder outside the
brain, checked with the package verifier after it is saved, and pruned to the last
few copies. Settings and results live on the brain's local record, never in the
portable ``.raticode/brain.json``, because the folder is a path on this computer.
"""

from __future__ import annotations

import hashlib
import re
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.ui.brain_documents import checked_archive

FREQUENCIES = ("daily", "weekly")
MAX_KEEP = 60
# Settings warn when the newest verified backup is older than this.
STALE_AFTER = timedelta(days=7)
DEFAULT_BACKUP: dict[str, Any] = {
    "enabled": False,
    "folder": "",
    "frequency": "daily",
    "weekday": 0,
    "time": "03:00",
    "keep": 7,
    "conversations": False,
}
FIELDS = set(DEFAULT_BACKUP)
# The name slug is for people; the tag (from the brain's ID) is what ties a file to its
# brain, so copies made before a rename are still pruned afterwards.
NAME = re.compile(r"(?:.*-)?(?P<tag>[0-9a-f]{8})-backup-(?P<stamp>\d{8}-\d{6})Z\.zip")
# Unfinished files a killed backup can leave: export_package's staging file and
# atomic_binary_output's publication file.
PARTIAL = re.compile(
    r"\.raticode-export-[A-Za-z0-9_]+\.zip\.partial"
    r"|\.(?:.*-)?[0-9a-f]{8}-backup-\d{8}-\d{6}Z\.zip\.[0-9a-f]{32}\.tmp"
)
# A file still being written is touched constantly; anything idle this long is abandoned.
PARTIAL_AGE = 60 * 60


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def backup_settings(brain: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULT_BACKUP, **(brain.get("backup") or {})}


def validate_backup(settings: Any, root: Path) -> dict[str, Any]:
    """Return complete, checked backup settings for a brain stored at ``root``."""
    if not isinstance(settings, dict) or set(settings) - FIELDS:
        raise ValueError("Unknown backup settings")
    merged = {**DEFAULT_BACKUP, **settings}
    for key in ("enabled", "conversations"):
        if not isinstance(merged[key], bool):
            raise ValueError("Backup switches must be on or off")
    if merged["frequency"] not in FREQUENCIES:
        raise ValueError("Back up daily or weekly")
    weekday = merged["weekday"]
    if not isinstance(weekday, int) or isinstance(weekday, bool) or not 0 <= weekday <= 6:
        raise ValueError("Choose a day of the week for weekly backups")
    if not isinstance(merged["time"], str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", merged["time"]
    ):
        raise ValueError("Choose a backup time as HH:MM")
    keep = merged["keep"]
    if not isinstance(keep, int) or isinstance(keep, bool) or not 1 <= keep <= MAX_KEEP:
        raise ValueError(f"Keep between 1 and {MAX_KEEP} backups")
    folder = merged["folder"]
    if not isinstance(folder, str) or len(folder) > 4096:
        raise ValueError("Choose a backup folder")
    if folder:
        path = Path(folder).expanduser()
        if not path.is_absolute():
            raise ValueError("Choose an absolute backup folder")
        resolved = path.resolve(strict=False)
        brain_root = root.resolve(strict=False)
        if resolved == brain_root or resolved.is_relative_to(brain_root):
            # The next backup would pack the previous ones, and indexing would read them.
            raise ValueError("Choose a backup folder outside the brain")
        merged["folder"] = str(resolved)
    elif merged["enabled"]:
        raise ValueError("Choose a backup folder before turning on scheduled backups")
    return merged


def brain_tag(brain: dict[str, Any]) -> str:
    """Short tag from the brain's ID: it ties backup files to one brain across renames."""
    return hashlib.sha256(str(brain["id"]).encode()).hexdigest()[:8]


def file_prefix(brain: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(brain.get("name", "")).lower()).strip("-")[:40]
    return f"{slug or 'brain'}-{brain_tag(brain)}"


def backup_path(folder: Path, brain: dict[str, Any], moment: datetime) -> Path:
    stamp = moment.astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    return folder / f"{file_prefix(brain)}-backup-{stamp}Z.zip"


def existing_backups(folder: Path, brain: dict[str, Any]) -> list[Path]:
    """This brain's backup files in ``folder``, newest first, whatever the brain was called."""
    tag = brain_tag(brain)
    found: list[tuple[str, Path]] = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    for path in entries:
        match = NAME.fullmatch(path.name)
        if match and match["tag"] == tag and path.is_file() and not path.is_symlink():
            found.append((match["stamp"], path))
    return [path for _, path in sorted(found, key=lambda item: item, reverse=True)]


def clean_partials(folder: Path, now: float | None = None) -> list[str]:
    """Delete unfinished backup files left by a backup that was killed mid-write."""
    moment = time.time() if now is None else now
    removed = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    for path in entries:
        if not PARTIAL.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            if moment - path.stat().st_mtime < PARTIAL_AGE:
                continue  # Possibly another brain's backup, still writing.
            path.unlink()
            removed.append(path.name)
        except OSError:
            continue
    return removed


def prune(folder: Path, brain: dict[str, Any], keep: int, newest: Path) -> list[str]:
    """Delete this brain's older backups beyond ``keep``, never the one just verified."""
    removed = []
    kept = 0
    for path in existing_backups(folder, brain):
        if path == newest or kept < keep - 1:
            kept += path != newest
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            continue
    return removed


def verify_package(path: Path) -> dict[str, Any]:
    """Run the package verifier on a saved package: structure, limits and every file hash."""
    manifest, entries = checked_archive(path)
    with zipfile.ZipFile(path) as archive:
        for name, digest in manifest["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"Backup verification failed: {name} does not match")
    return {
        "files": len(manifest["files"]),
        "documents": len(manifest["library"].get("documents", [])),
        "expanded": sum(entry.file_size for entry in entries),
    }


def due_key(settings: dict[str, Any], moment: datetime) -> str | None:
    """The schedule period a backup is due for at local ``moment``, or None when not due.

    A missed time (computer asleep or off) runs at the next check in the same day for
    daily backups, or later the same week for weekly ones.
    """
    if not settings.get("enabled") or not settings.get("folder"):
        return None
    clock = moment.strftime("%H:%M")
    if settings["frequency"] == "weekly":
        day = int(settings["weekday"])
        if moment.weekday() < day or (moment.weekday() == day and clock < settings["time"]):
            return None
        year, week, _ = moment.isocalendar()
        return f"{year}-W{week:02d}"
    if clock < settings["time"]:
        return None
    return moment.date().isoformat()


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def backup_warning(brain: dict[str, Any], moment: datetime) -> str:
    """Why the settings page should warn about backups, or an empty string."""
    status = brain.get("backupStatus") or {}
    settings = backup_settings(brain)
    verified = _parse((status.get("lastVerified") or {}).get("at"))
    if verified is not None:
        age = moment - verified
        if age <= STALE_AFTER:
            return ""
        return f"No verified backup in {age.days} days."
    since = _parse(status.get("enabledAt"))
    if settings["enabled"] and since is not None and moment - since > STALE_AFTER:
        return f"No verified backup since backups were turned on {(moment - since).days} days ago."
    return ""
