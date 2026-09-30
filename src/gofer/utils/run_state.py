from __future__ import annotations

from pathlib import Path

from gofer.utils.atomic_output import atomic_binary_output, unlink_without_links
from gofer.utils.paths import get_data_dir


def workflow_stop_path(workflow_id: str, data_dir: Path | None = None) -> Path:
    base = data_dir or get_data_dir()
    safe_id = workflow_id.replace("/", "_").replace("\\", "_")
    return base / "run-state" / f"{safe_id}.stop"


def workflow_run_stop_path(
    workflow_id: str,
    run_id: str,
    data_dir: Path | None = None,
) -> Path:
    base = data_dir or get_data_dir()
    safe_workflow_id = workflow_id.replace("/", "_").replace("\\", "_")
    if safe_workflow_id in {"", ".", ".."}:
        raise ValueError("A workflow identifier is required for a run stop marker")
    safe_run_id = run_id.replace("/", "_").replace("\\", "_")
    return base / "run-state" / safe_workflow_id / f"{safe_run_id}.stop"


def request_workflow_stop(workflow_id: str, data_dir: Path | None = None) -> Path:
    path = workflow_stop_path(workflow_id, data_dir)
    with atomic_binary_output(path) as output:
        output.write(b"stop requested\n")
    return path


def request_workflow_run_stop(
    workflow_id: str,
    run_id: str,
    data_dir: Path | None = None,
) -> Path:
    path = workflow_run_stop_path(workflow_id, run_id, data_dir)
    with atomic_binary_output(path) as output:
        output.write(b"stop requested\n")
    return path


def clear_workflow_stop(workflow_id: str, data_dir: Path | None = None) -> None:
    clear_stop_marker(workflow_stop_path(workflow_id, data_dir))


def clear_stop_marker(path: Path) -> None:
    """Remove a stale marker without following a replaced parent directory."""
    try:
        unlink_without_links(path)
    except OSError:
        return
