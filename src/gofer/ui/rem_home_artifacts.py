"""Narrow report output grants and hash-bound acceptance evidence."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.ui.report_outputs import ReportTools
from gofer.ui.swarm_tools import TOOL_FAILURE_HINT, SwarmToolServer
from gofer.utils.atomic_output import mkdir_without_links, open_binary_input

if TYPE_CHECKING:
    from gofer.ui.rem_home import RemHome

log = logging.getLogger(__name__)


def artifact_root(run: dict[str, Any]) -> Path:
    brain = run["config"].get("brain") or {}
    base = brain.get("root") if brain.get("enabled") else run["recipe"].get("project_dir")
    if not base:
        raise ValueError("A research report needs an authorized destination")
    return Path(base) / "reports" / "rem"


def validate_artifacts(run: dict[str, Any]) -> None:
    artifacts = [
        a
        for a in run.get("artifacts", [])
        if a.get("epoch") == run["epoch"] and not a.get("superseded")
    ]
    if not artifacts:
        raise ValueError(
            "No saved research artifact receipt. Save through rem_artifacts.save_report."
        )
    root = artifact_root(run).resolve(strict=True)
    for artifact in artifacts:
        path = Path(artifact["path"])
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("The report moved outside its approved destination")
        with open_binary_input(path) as handle:
            data = handle.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            raise ValueError("The saved report is empty or oversized")
        if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise ValueError("The research artifact changed after saving")


def record_artifact(home: RemHome, run: dict[str, Any], path: Path, sha256: str) -> None:
    """Show a report saved into a Second Brain in its Reports and project pages."""
    from gofer.ui.brain_library import BrainLibrary

    shared: BrainLibrary | None = home.brain_library
    library = shared or BrainLibrary(home.data_dir, start=False, recover=False)
    try:
        library.record_artifact(
            path,
            {
                "savedVia": "rem_task",
                "source": "save_report",
                "runId": run["id"],
                "taskId": run.get("task_id", ""),
                "threadId": run.get("thread_id", ""),
                "taskTitle": run["recipe"].get("title", ""),
                "projectRoot": run["recipe"].get("project_dir", ""),
                "provider": run["config"].get("provider", ""),
                "model": run["config"].get("model", ""),
                "sha256": sha256,
            },
        )
    except Exception:  # noqa: BLE001 - the report itself is saved; its record can be backfilled
        log.exception("Could not register a saved report in the Second Brain")
    finally:
        if shared is None:
            library.close()


@contextmanager
def artifact_tools(
    home: RemHome, run: dict[str, Any], role: str
) -> Iterator[dict[str, Any] | None]:
    if role != "execute" or run["recipe"]["type"] != "research":
        yield None
        return
    home.authorize(run["config"], run["recipe"])
    root = artifact_root(run)
    mkdir_without_links(root)
    theme = run["config"].get("report_theme") or {"format": "html"}
    reports = ReportTools(root, theme.get("format", "html"), theme=theme)

    def call(name: str, arguments: dict[str, Any]) -> Any:
        with home.store.connect() as db:
            current = home.store.get(db, "runs", run["id"])
            if current["epoch"] != run["epoch"] or current["state"] == "cancelling":
                raise ValueError("This report grant was revoked")
            selected = home.store.setting(db, "config")
        refreshed = {**run["config"]}
        for field in ("projects", "brain"):
            refreshed[field] = selected.get(field, refreshed.get(field))
        home.authorize(refreshed, run["recipe"])
        if artifact_root({**run, "config": refreshed}) != root:
            raise ValueError("The report destination changed; update the task before saving")
        result = reports.call(name, arguments)
        try:
            with open_binary_input(Path(result["path"])) as handle:
                data = handle.read(2 * 1024 * 1024 + 1)
            receipt = {
                **result,
                "sha256": hashlib.sha256(data).hexdigest(),
                "epoch": run["epoch"],
            }
            with home.store.connect() as db:
                current = home.store.get(db, "runs", run["id"])
                receipt["superseded"] = current["epoch"] != run["epoch"]
                current.setdefault("artifacts", []).append(receipt)
                home.store.put(db, "runs", current)
                home.store.event(
                    db, "artifact.saved", {"run_id": run["id"], "path": result["path"]}
                )
            record_artifact(home, run, Path(result["path"]), receipt["sha256"])
        except (OSError, sqlite3.Error) as exc:
            # The file exists now, so an identical retry would only report a
            # name clash. Say what happened and how to recover.
            log.exception("Rem report receipt was not recorded")
            shown = Path(result["path"]).relative_to(reports.root).as_posix()
            raise ValueError(
                f"The report was written to {shown}, but its receipt couldn't be recorded "
                f"({type(exc).__name__}). Save it again under a new name."
            ) from exc
        return result

    server = SwarmToolServer()
    url = server.register(
        call,
        [
            {
                "name": "save_report",
                "description": reports.call("rules", {}),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "format": {"type": "string", "enum": ["html", "md", "pdf", "slides"]},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": False},
            }
        ],
        "Save reports only through this granted output service.",
        label="Report",
        failure_hint=TOOL_FAILURE_HINT,
    )
    try:
        yield {"name": "rem_artifacts", "type": "http", "url": url}
    finally:
        server.revoke(url)
        server.close()
