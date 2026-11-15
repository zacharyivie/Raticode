"""Automatic clean-up of old Rattish run records.

Every Rattish run, whatever starts it, ends in ``run_service.run_rattish_file``,
which calls :func:`apply_run_retention` once the final record is saved. The
Studio's Prune button, ``Raticode rattish prune`` and ``Raticode workflow logs
prune`` pick runs with the same :func:`prune_run_records`.

A run is never picked while it is running, waiting on an approval, being
resumed, or recorded as the run a still-running run resumed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.rattish.run_values import delete_run_files
from gofer.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_RETENTION_SETTINGS = {"keepDays": 14, "keepFailedDays": 30, "keepLast": 100}
RETENTION_SETTINGS_FILE = "retention.json"
# Saved statuses the Studio shows as failed runs; keepFailedDays applies to them.
FAILED_RUN_STATUSES = frozenset({"failed", "preflight_failed", "invalid_inputs"})


class RunRecordError(ValueError):
    """Raised when a Rattish run record can't be read."""


@dataclass(frozen=True, slots=True)
class RunRecord:
    document: dict[str, Any]
    path: Path

    @property
    def run_id(self) -> str:
        return str(self.document["run_id"])


@dataclass(slots=True)
class PruneResult:
    candidates: list[RunRecord] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    # Unreadable run records, which are left alone.
    skipped: int = 0


def coerce_retention_settings(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    settings: dict[str, int] = {}
    for key, default in DEFAULT_RETENTION_SETTINGS.items():
        raw = source.get(key, default)
        settings[key] = (
            max(0, int(raw))
            if isinstance(raw, int | float) and not isinstance(raw, bool)
            else default
        )
    return settings


def saved_retention_settings(data_dir: Path, workflow_id: str) -> dict[str, int]:
    """The workflow's own retention settings, else the global ones, else the defaults."""
    try:
        document = json.loads(
            (data_dir / "settings" / RETENTION_SETTINGS_FILE).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        document = {}
    if not isinstance(document, dict):
        document = {}
    workflows = document.get("workflows")
    if isinstance(workflows, dict) and workflow_id in workflows:
        return coerce_retention_settings(workflows[workflow_id])
    return coerce_retention_settings(document.get("global"))


def read_run_record(path: Path, *, settled: bool = False) -> RunRecord:
    """One run record, with a run whose runner is gone reported as interrupted."""
    from gofer.rattish.run_service import effective_run_status

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunRecordError(f"Could not read Rattish run artifact {path.name}: {exc}") from exc
    if (
        not isinstance(document, dict)
        or document.get("run_artifact_version") != 1
        or not document.get("run_id")
    ):
        raise RunRecordError(f"Invalid Rattish run artifact {path.name}.")
    if document.get("status") == "running":
        status = effective_run_status(document)
        if status == "interrupted" and document.get("runner_pid") == os.getpid() and not settled:
            # This process saves a final record before the run stops counting as
            # active, so the run may have finished after the read.
            return read_run_record(path, settled=True)
        document["status"] = status
    return RunRecord(document, path)


def run_record_paths(directory: Path) -> list[Path]:
    """A workflow's run records, newest first."""
    if not directory.is_dir():
        return []
    stamped = []
    for path in directory.glob("*.json"):
        try:
            if path.is_file():
                stamped.append((path.stat().st_mtime_ns, path.name, path))
        except OSError:
            continue
    return [path for _, _, path in sorted(stamped, reverse=True)]


def load_run_records(directory: Path) -> tuple[list[RunRecord], int]:
    """Readable run records, newest first, and how many were unreadable."""
    records: list[RunRecord] = []
    skipped = 0
    for path in run_record_paths(directory):
        try:
            records.append(read_run_record(path))
        except RunRecordError:
            skipped += 1
    return records, skipped


def protected_run_ids(records: Collection[RunRecord], data_dir: Path, workflow_id: str) -> set[str]:
    """Runs that must survive any prune.

    Running runs, runs waiting on an approval, runs a Raticode process is
    resuming, and the run each running run resumed. Raises OSError when pending
    approvals can't be read, so a caller prunes nothing rather than guess.
    """
    from gofer.core.approvals import ApprovalStore
    from gofer.rattish.run_service import is_rattish_run_being_resumed

    protected: set[str] = set()
    for record in records:
        if record.document.get("status") != "running":
            continue
        protected.add(record.run_id)
        parent = (record.document.get("resumed_from") or {}).get("run_id")
        if parent:
            protected.add(str(parent))
    protected.update(
        request.run_id for request in ApprovalStore(data_dir).list_pending(workflow_id)
    )
    protected.update(
        record.run_id
        for record in records
        if is_rattish_run_being_resumed(data_dir, workflow_id, record.run_id)
    )
    return protected


def _parse_started_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def select_prune_candidates(
    records: list[RunRecord],
    *,
    protected: Collection[str],
    keep_last: int | None,
    keep_days: int | None,
    keep_failed_days: int | None,
    now: datetime | None = None,
) -> list[RunRecord]:
    """Records, newest first, that the settings no longer keep.

    The newest ``keep_last`` runs stay. Older runs stay until they are
    ``keep_days`` old, or ``keep_failed_days`` for a failed run; with no
    failed-run limit, failed runs use ``keep_days``. A missing age limit keeps
    every run it applies to.
    """
    current = now or datetime.now(UTC)
    retained = {record.run_id for record in records[: max(0, keep_last or 0)]}
    candidates = []
    for record in records:
        if record.run_id in retained or record.run_id in protected:
            continue
        status = str(record.document.get("status") or "")
        threshold = (
            keep_failed_days
            if status in FAILED_RUN_STATUSES and keep_failed_days is not None
            else keep_days
        )
        if threshold is None:
            continue
        started = _parse_started_at(record.document.get("started_at"))
        if started is not None and started >= current - timedelta(days=max(0, threshold)):
            continue
        candidates.append(record)
    return candidates


def prune_run_records(
    data_dir: Path,
    workflow_id: str,
    directory: Path,
    *,
    keep_last: int | None = None,
    keep_days: int | None = None,
    keep_failed_days: int | None = None,
    use_saved_settings: bool = False,
    dry_run: bool = True,
    keep_run_ids: Collection[str] = (),
) -> PruneResult:
    """Pick, and unless ``dry_run`` delete, run records the settings no longer keep.

    With ``use_saved_settings`` the workflow's saved retention settings replace
    the three limits. Unreadable records are counted in ``skipped`` and kept.
    """
    if use_saved_settings:
        settings = saved_retention_settings(data_dir, workflow_id)
        keep_last = settings["keepLast"]
        keep_days = settings["keepDays"]
        keep_failed_days = settings["keepFailedDays"]
    records, skipped = load_run_records(directory)
    protected = protected_run_ids(records, data_dir, workflow_id) | set(keep_run_ids)
    result = PruneResult(
        candidates=select_prune_candidates(
            records,
            protected=protected,
            keep_last=keep_last,
            keep_days=keep_days,
            keep_failed_days=keep_failed_days,
        ),
        skipped=skipped,
    )
    if not dry_run:
        for record in result.candidates:
            # The record, its stop marker and its saved inputs go together.
            delete_run_files(record.path)
            result.deleted.append(record.run_id)
    return result


def apply_run_retention(
    data_dir: Path,
    workflow_id: str,
    directory: Path,
    *,
    keep_run_ids: Collection[str] = (),
) -> list[str]:
    """Delete the workflow's runs that its saved retention settings no longer keep.

    Called after every run settles. Never raises: a failed clean-up must not
    change the outcome of the run that triggered it.
    """
    try:
        settings = saved_retention_settings(data_dir, workflow_id)
        # Most workflows keep fewer runs than keepLast, so skip reading them.
        if len(run_record_paths(directory)) <= settings["keepLast"]:
            return []
        result = prune_run_records(
            data_dir,
            workflow_id,
            directory,
            keep_last=settings["keepLast"],
            keep_days=settings["keepDays"],
            keep_failed_days=settings["keepFailedDays"],
            dry_run=False,
            keep_run_ids=keep_run_ids,
        )
    except Exception:
        logger.warning("Rattish run retention failed for %s", workflow_id, exc_info=True)
        return []
    if result.deleted:
        logger.info(
            "Removed %d old run(s) of %s under its retention settings",
            len(result.deleted),
            workflow_id,
        )
    return result.deleted
