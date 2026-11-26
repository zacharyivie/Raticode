"""Runs from every registered Rattish workflow in one list, newest first.

The Workflow runs view, ``Raticode rattish runs --all``, the phone's Workflows page and
the workflow line in Rem's "While you were away" card all read this list.

A run record holds the compiled workflow, every node's output and its events, so
reading each one would get slower with every run kept. Instead each workflow has a
small summary file under ``<data>/rattish-run-summaries``. An entry is reused while
the run record's size and modification time match what the entry saw, so a listing
reads one summary file per workflow and stats each run record. Only a run whose
record changed since the last listing (a new or running one) is read in full. Run
IDs start with their start time, so a time range skips older runs before any stat.
The summary files are a cache: deleting them only makes the next listing slower.

A workflow's own Runs list and the clean-up of old runs read the same summaries
through :func:`workflow_run_summaries`; the Runs list then reads in full only the
page of runs it returns.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat as stat_kind
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.utils.atomic_output import atomic_binary_output

log = logging.getLogger(__name__)

SUMMARY_DIRECTORY = "rattish-run-summaries"
# 2 added each run's provider fallbacks. 3 keys each file by the run folder's real
# path, so the run lists and the clean-up after each run share one file.
SUMMARY_VERSION = 3
# The most provider switches a run summary keeps.
MAX_FALLBACKS = 10
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
# What each status filter accepts; the list reports the first name.
STATUS_NAMES = {
    "success": "success",
    "passed": "success",
    "error": "error",
    "failed": "error",
    "running": "running",
    "stopped": "stopped",
    "disconnected": "disconnected",
    "interrupted": "disconnected",
}
STATUS_WORDS = {
    "success": "passed",
    "error": "failed",
    "running": "running",
    "stopped": "stopped",
    "disconnected": "interrupted",
}
STARTED_BY_LABELS = {"manual": "Manual", "trigger": "Trigger", "phone": "Phone", "rem": "Rem"}
_RELATIVE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
# A run ID's start time and a record's started_at differ by milliseconds. A
# record is skipped by its ID only when it is clearly older than the range.
_ID_SLACK = timedelta(minutes=5)


class WorkflowRunsError(ValueError):
    """A filter the run list can't use, with a sentence for the user."""


def parse_time(value: str | None, *, now: datetime | None = None) -> datetime | None:
    """An ISO date or time, or a span back from now such as ``30m``, ``24h``, ``7d`` or ``2w``."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    match = _RELATIVE.match(text)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        return (now or datetime.now(UTC)) - timedelta(**{_UNITS[unit]: amount})
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise WorkflowRunsError(
            f"'{text}' isn't a time. Use an ISO time such as 2026-11-14T08:00 or a span "
            "such as 30m, 24h, 7d or 2w."
        ) from None
    return parsed if parsed.tzinfo else parsed.astimezone()


def status_filter(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    name = STATUS_NAMES.get(value.strip().lower())
    if name is None:
        raise WorkflowRunsError(
            f"Unknown status '{value}'. Use success, error, running, stopped or disconnected."
        )
    return name


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _id_time(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id.split("-", 1)[0], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def run_error_message(document: Mapping[str, Any]) -> str:
    """A run record's error message, cut to the length the run lists show."""
    error = document.get("error")
    message = error.get("message") if isinstance(error, Mapping) else None
    return str(message)[:300] if message else ""


def _summary_of(document: Mapping[str, Any]) -> dict[str, Any]:
    """What the lists and clean-up need from one run record, and nothing else."""
    from gofer.rattish.run_service import run_started_by

    usage = document.get("usage")
    entry: dict[str, Any] = {
        "run_id": str(document.get("run_id") or ""),
        "status": str(document.get("status") or ""),
        "workflow_name": str((document.get("workflow") or {}).get("name") or ""),
        "started_at": document.get("started_at"),
        "finished_at": document.get("finished_at"),
        "duration_ms": document.get("duration_ms") or 0,
        "started_by": run_started_by(document),
        "resumed_from": (document.get("resumed_from") or {}).get("run_id"),
        "error": run_error_message(document),
        "usage": (
            {key: value for key, value in usage.items() if key not in {"nodes", "limits"}}
            if isinstance(usage, Mapping)
            else None
        ),
    }
    fallbacks = [
        {
            "node": str(record.get("node_id") or ""),
            "from": switch.get("from") or {},
            "to": switch.get("to") or {},
            "reason": str(switch.get("reason") or ""),
            # An account passed over at a usage limit: listed, but not a switch.
            **({"skipped": True} if switch.get("skipped") else {}),
        }
        for record in document.get("runs") or []
        if isinstance(record, Mapping)
        for switch in record.get("fallbacks") or []
        if isinstance(switch, Mapping)
    ]
    if fallbacks:
        entry["fallbacks"] = fallbacks[:MAX_FALLBACKS]
    for key in ("runner_pid", "runner_start"):
        if key in document:
            entry[key] = document[key]
    return entry


def fallback_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A run's provider switches for the list: node, providers, reason and one line."""
    from gofer.rattish.provider_fallback import switch_message

    rows = []
    for item in summary.get("fallbacks") or []:
        try:
            line = switch_message(item)
        except (KeyError, TypeError):
            continue
        rows.append(
            {
                "nodeId": item["node"],
                "from": item["from"],
                "to": item["to"],
                "reason": item["reason"],
                **({"skipped": True} if item.get("skipped") else {}),
                "line": f"{item['node']}: {line}",
            }
        )
    return rows


class _WorkflowSummaries:
    """One workflow's summary file: run ID -> size, mtime and summary."""

    def __init__(self, base: Path, workflow_id: str, directory: Path) -> None:
        self.path = base / SUMMARY_DIRECTORY / f"{workflow_id}.json"
        # Callers name the same run folder with and without symlinks resolved.
        self.directory = os.path.realpath(directory)
        self.entries: dict[str, dict[str, Any]] = {}
        self.changed = False
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if (
            isinstance(document, dict)
            and document.get("version") == SUMMARY_VERSION
            and document.get("directory") == self.directory
            and isinstance(document.get("runs"), dict)
        ):
            self.entries = document["runs"]

    def get(
        self, record: Path, *, stat: os.stat_result, run_id: str | None = None
    ) -> dict[str, Any] | None:
        run_id = record.stem if run_id is None else run_id
        key = [stat.st_size, stat.st_mtime_ns]
        cached = self.entries.get(run_id)
        if isinstance(cached, dict) and cached.get("key") == key:
            return dict(cached["summary"])
        try:
            document = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            document = None
        if not isinstance(document, dict) or document.get("run_artifact_version") != 1:
            # Records are replaced atomically, so this one is damaged or foreign. The
            # workflow's own Runs list reports it; this list leaves it out.
            return None
        summary = _summary_of(document)
        self.entries[run_id] = {"key": key, "summary": summary}
        self.changed = True
        return dict(summary)

    def save(self, keep: Iterable[str]) -> None:
        kept = set(keep)
        if set(self.entries) - kept:
            self.entries = {k: v for k, v in self.entries.items() if k in kept}
            self.changed = True
        if not self.changed:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = {"version": SUMMARY_VERSION, "directory": self.directory, "runs": self.entries}
            with atomic_binary_output(self.path) as output:
                output.write(json.dumps(body, separators=(",", ":"), default=str).encode())
        except OSError as exc:
            log.debug("Could not save run summaries for %s: %s", self.path.stem, exc)


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One readable run record of a workflow: its path and cached summary.

    ``summary["status"]`` is the effective status: a running run whose runner is
    gone reads as ``interrupted``, as when the full record is read.
    """

    path: Path
    summary: dict[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.summary["run_id"])

    @property
    def status(self) -> str:
        return str(self.summary["status"])


def missed_records(
    base: Path, workflow_id: str, directory: Path, listed: Iterable[str]
) -> list[tuple[str, os.stat_result]]:
    """Run records a folder listing left out that the summary file knows: ``(path, stat)``.

    A running run replaces its record on every step, and a listing taken during a replace
    can miss the name (btrfs does; the approvals list had the same race). Looking a known
    run up by name always finds it, so a running run doesn't vanish from the Runs list for
    a moment, and retention still protects what it resumed from. ``listed`` is the
    listing's file names.
    """
    names = set(listed)
    found = []
    for run_id in _WorkflowSummaries(base, workflow_id, directory).entries:
        name = f"{run_id}.json"
        if name in names or run_id.startswith(".") or os.sep in run_id or "/" in run_id:
            continue
        location = os.path.join(directory, name)
        try:
            stat = os.stat(location, follow_symlinks=False)
        except (OSError, ValueError):
            # ValueError: a name with a NUL byte, which a damaged summary file can hold.
            continue
        if stat_kind.S_ISREG(stat.st_mode):
            found.append((location, stat))
    return found


def workflow_run_summaries(
    base: Path, workflow_id: str, directory: Path
) -> tuple[list[RunSummary], int]:
    """A workflow's run records as summaries, newest first, and how many were unreadable.

    Newest first means by modification time, then file name, as the workflow's Runs
    list has always ordered them. Only records that changed since the summary file
    last saw them are read; the rest cost one stat each. Returns ``([], 0)`` when
    the folder holds no run records at all.
    """
    from gofer.rattish.run_service import effective_run_status

    try:
        listing = list(os.scandir(directory))
    except OSError:
        return [], 0
    stamped: list[tuple[int, str, str, os.stat_result]] = []
    for entry in listing:
        # glob("*.json") skips hidden names, such as a summary being written.
        if not entry.name.endswith(".json") or entry.name.startswith("."):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        stamped.append((stat.st_mtime_ns, entry.name, entry.path, stat))
    for location, stat in missed_records(
        base, workflow_id, directory, [entry.name for entry in listing]
    ):
        stamped.append((stat.st_mtime_ns, os.path.basename(location), location, stat))
    stamped.sort(key=lambda item: (item[0], item[1]), reverse=True)
    summaries = _WorkflowSummaries(base, workflow_id, directory)
    readable: list[RunSummary] = []
    unreadable = 0
    seen: list[str] = []
    for _, name, location, stat in stamped:
        stem = name[: -len(".json")]
        path = Path(location)
        summary = summaries.get(path, stat=stat, run_id=stem)
        if summary is not None and summary.get("status") == "running":
            status = effective_run_status(summary)
            if status == "interrupted" and summary.get("runner_pid") == os.getpid():
                # This process saves a run's final record before the run stops counting
                # as active, so the record may have changed since the stat above.
                try:
                    summary = summaries.get(
                        path, stat=path.stat(follow_symlinks=False), run_id=stem
                    )
                except OSError:
                    summary = None
                if summary is not None and summary.get("status") == "running":
                    summary["status"] = effective_run_status(summary)
            else:
                summary["status"] = status
        if summary is not None:
            seen.append(stem)
        if summary is None or not summary.get("run_id"):
            unreadable += 1
            continue
        readable.append(RunSummary(path, summary))
    summaries.save(seen)
    return readable, unreadable


def _effective(summary: dict[str, Any]) -> str:
    from gofer.rattish.run_service import effective_run_status

    return effective_run_status(summary)


def _ui_status(status: str) -> str:
    if status == "passed":
        return "success"
    if status == "interrupted":
        return "disconnected"
    return status if status in {"running", "stopped"} else "error"


def _workflow_runs(
    base: Path,
    registered: Any,
    since: datetime | None,
    until: datetime | None,
    usage_waits: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from gofer.core.approvals import ApprovalStore
    from gofer.rattish.run_usage import usage_payload
    from gofer.rattish.triggers import usage_wait_payload
    from gofer.ui.api import _rattish_run_directory

    workflow_id = registered.workflow_id
    directory = _rattish_run_directory(base, workflow_id)
    if not directory.is_dir():
        return []
    summaries = _WorkflowSummaries(base, workflow_id, directory)
    seen: list[str] = []
    runs: list[dict[str, Any]] = []
    try:
        records = [entry for entry in os.scandir(directory) if entry.name.endswith(".json")]
    except OSError:
        return []
    for entry in records:
        run_id = entry.name[: -len(".json")]
        started = _id_time(run_id)
        if since is not None and started is not None and started < since - _ID_SLACK:
            seen.append(run_id)  # Keep its cached summary; it is only out of range.
            continue
        if until is not None and started is not None and started > until + _ID_SLACK:
            seen.append(run_id)
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not entry.is_file(follow_symlinks=False):
            continue
        summary = summaries.get(Path(entry.path), stat=stat)
        if summary is None:
            continue
        seen.append(run_id)
        runs.append(summary)
    summaries.save(seen)
    pending: set[str] = set()
    for summary in runs:
        status = summary["status"]
        if status == "running":
            status = _effective(summary)
        summary["status"] = status
    if any(summary["status"] == "running" for summary in runs):
        try:
            pending = {request.run_id for request in ApprovalStore(base).list_pending(workflow_id)}
        except Exception:  # noqa: BLE001 - an unreadable approval store must not hide runs.
            pending = set()
    listed = []
    for summary in runs:
        started_at = _aware(summary.get("started_at"))
        if since is not None and (started_at is None or started_at < since):
            continue
        if until is not None and (started_at is None or started_at > until):
            continue
        running = summary["status"] == "running"
        usage = usage_payload(summary.get("usage"))
        wait = (usage_waits or {}).get(summary["run_id"])
        listed.append(
            {
                "workflowId": workflow_id,
                "workflowName": summary.get("workflow_name") or registered.name,
                "projectRoot": str(registered.project_root),
                "projectName": registered.project_root.name or str(registered.project_root),
                "sourcePath": str(registered.entrypoint),
                "id": summary["run_id"],
                "status": _ui_status(summary["status"]),
                "awaitingApproval": running and summary["run_id"] in pending,
                "startedAt": summary.get("started_at"),
                "finishedAt": None if running else summary.get("finished_at"),
                "durationSeconds": float(summary.get("duration_ms") or 0) / 1000,
                "startedBy": summary.get("started_by") or "manual",
                "resumedFrom": summary.get("resumed_from"),
                "error": summary.get("error") or "",
                "usage": usage,
                "fallbacks": fallback_rows(summary),
                "usageLimitWait": usage_wait_payload(wait) if wait is not None else None,
            }
        )
    return listed


def runs_line(totals: Mapping[str, Any]) -> str:
    """One line such as ``6 workflow runs: 5 passed, 1 failed · est. $0.42``."""
    from gofer.rattish.run_usage import format_usd

    count = int(totals.get("runs") or 0)
    if not count:
        return "No workflow runs"
    by_status = totals.get("byStatus") or {}
    parts = [
        f"{by_status[status]} {word}"
        for status, word in STATUS_WORDS.items()
        if by_status.get(status)
    ]
    line = f"{count} workflow {'run' if count == 1 else 'runs'}: {', '.join(parts)}"
    if totals.get("calls"):
        cost = totals.get("costUsd")
        if cost is None:
            line += " · est. cost unknown"
        else:
            prefix = "at least " if totals.get("unreportedCostRuns") else ""
            line += f" · est. {prefix}{format_usd(float(cost))}"
    return line


def _totals(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    calls = 0
    tokens: int | None = None
    cost: float | None = None
    unreported_tokens = 0
    unreported_cost = 0
    for run in runs:
        by_status[run["status"]] = by_status.get(run["status"], 0) + 1
        usage = run.get("usage") or {}
        run_calls = int(usage.get("calls") or 0)
        if not run_calls:
            continue
        calls += run_calls
        if usage.get("totalTokens") is not None:
            tokens = (tokens or 0) + int(usage["totalTokens"])
        if usage.get("totalTokens") is None or usage.get("unreportedTokenCalls"):
            unreported_tokens += 1
        if usage.get("costUsd") is not None:
            cost = (cost or 0.0) + float(usage["costUsd"])
        if usage.get("costUsd") is None or usage.get("unreportedCostCalls"):
            unreported_cost += 1
    totals = {
        "runs": len(runs),
        "byStatus": by_status,
        "calls": calls,
        "totalTokens": tokens,
        "costUsd": round(cost, 6) if cost is not None else None,
        "unreportedTokenRuns": unreported_tokens,
        "unreportedCostRuns": unreported_cost,
    }
    totals["summary"] = runs_line(totals)
    return totals


def all_workflow_runs_payload(
    data_dir: Path | None = None,
    *,
    status: str | None = None,
    workflow_ids: Iterable[str] | None = None,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    started_by: str | None = None,
    project_roots: Iterable[str] | None = None,
    limit: int | None = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Runs from every registered workflow, newest first, with totals for the filter.

    ``totals`` covers every run that matches, not only the returned page.
    ``workflows`` lists every workflow the filters could pick, for a workflow menu.
    ``project_roots`` keeps only workflows in those projects (the phone's scope).
    """
    from gofer.rattish.workspaces import list_registered_workflows
    from gofer.ui.api import _data_dir

    base = _data_dir(data_dir)
    now = datetime.now(UTC)
    start = since if isinstance(since, datetime) else parse_time(since, now=now)
    end = until if isinstance(until, datetime) else parse_time(until, now=now)
    wanted_status = status_filter(status)
    if started_by is not None and started_by not in STARTED_BY_LABELS:
        raise WorkflowRunsError(f"Unknown start '{started_by}'. Use manual, trigger, phone or rem.")
    page_limit = DEFAULT_LIMIT if limit is None else max(0, min(int(limit), MAX_LIMIT))
    page_offset = max(0, int(offset))
    scoped = project_roots is not None
    roots = {os.path.realpath(os.fspath(root)) for root in project_roots or ()}
    chosen = {item.lower() for item in workflow_ids or () if item}

    workflows = []
    names: set[str] = set()
    for registered in list_registered_workflows(registry_dir=base):
        key = registered.workflow_id.lower()
        if key in names:
            continue  # The same ID registered twice shares one run folder.
        if scoped and os.path.realpath(registered.project_root) not in roots:
            continue
        names.add(key)
        workflows.append(registered)

    from gofer.rattish.triggers import usage_waits_by_run

    usage_waits = usage_waits_by_run(base)
    runs: list[dict[str, Any]] = []
    for registered in workflows:
        if chosen and registered.workflow_id.lower() not in chosen:
            continue
        try:
            runs.extend(_workflow_runs(base, registered, start, end, usage_waits))
        except Exception:  # noqa: BLE001 - one broken workflow folder must not hide the rest.
            log.warning("Could not list runs of %s", registered.workflow_id, exc_info=True)
    if wanted_status:
        runs = [run for run in runs if run["status"] == wanted_status]
    if started_by:
        runs = [run for run in runs if run["startedBy"] == started_by]
    runs.sort(key=lambda run: (str(run.get("startedAt") or ""), str(run["id"])), reverse=True)
    return {
        "runs": runs[page_offset : page_offset + page_limit],
        "pagination": {"offset": page_offset, "limit": page_limit, "total": len(runs)},
        "totals": _totals(runs),
        "since": start.isoformat() if start else None,
        "until": end.isoformat() if end else None,
        "workflows": [
            {
                "id": registered.workflow_id,
                "name": registered.name,
                "projectName": registered.project_root.name or str(registered.project_root),
            }
            for registered in sorted(workflows, key=lambda item: item.name.lower())
        ],
    }
