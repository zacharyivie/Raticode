"""Narrow report output grants and hash-bound acceptance evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.ui.report_outputs import ReportTools
from gofer.ui.swarm_tools import SwarmToolServer
from gofer.utils.atomic_output import mkdir_without_links, open_binary_input

if TYPE_CHECKING:
    from gofer.ui.rem_home import RemHome


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
        with open_binary_input(Path(result["path"])) as handle:
            data = handle.read(2 * 1024 * 1024 + 1)
        receipt = {**result, "sha256": hashlib.sha256(data).hexdigest(), "epoch": run["epoch"]}
        with home.store.connect() as db:
            current = home.store.get(db, "runs", run["id"])
            receipt["superseded"] = current["epoch"] != run["epoch"]
            current.setdefault("artifacts", []).append(receipt)
            home.store.put(db, "runs", current)
            home.store.event(db, "artifact.saved", {"run_id": run["id"], "path": result["path"]})
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
    )
    try:
        yield {"name": "rem_artifacts", "type": "http", "url": url}
    finally:
        server.revoke(url)
        server.close()
