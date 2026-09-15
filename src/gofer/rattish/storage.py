"""On-disk storage policy for registered Rattish workflows."""

from __future__ import annotations

import os
from pathlib import Path


def registered_workflow_root(workflow_id: str, registry_dir: Path) -> Path | None:
    """Return the canonical workspace for a registered workflow ID."""
    from gofer.rattish.workspaces import RattishWorkspaceError, find_registered_workflow

    try:
        workflow = find_registered_workflow(workflow_id, registry_dir=registry_dir)
    except RattishWorkspaceError:
        return None
    return workflow.workflow_root.expanduser().resolve()


def registered_source_root(source_path: Path, registry_dir: Path) -> Path | None:
    """Return the canonical workspace when source_path is a registered entrypoint."""
    from gofer.rattish.workspaces import RattishWorkspaceError, list_registered_workflows

    resolved_source = source_path.expanduser().resolve()
    try:
        workflows = list_registered_workflows(registry_dir=registry_dir)
    except RattishWorkspaceError:
        return None
    for workflow in workflows:
        if workflow.entrypoint.expanduser().resolve() == resolved_source:
            return workflow.workflow_root.expanduser().resolve()
    return None


def workflow_owned_directory(
    workflow_id: str,
    registry_dir: Path,
    directory: str,
) -> Path | None:
    """Return a directory below the registered workflow workspace."""
    root = registered_workflow_root(workflow_id, registry_dir)
    return root / directory if root is not None else None


def migrate_legacy_directory(source: Path, destination: Path) -> None:
    """Move an old app-data directory into a registered workflow workspace."""
    if not source.is_dir():
        return
    try:
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            return
        for path in source.iterdir():
            target = destination / path.name
            if not target.exists():
                os.replace(path, target)
        source.rmdir()
    except OSError:
        return
