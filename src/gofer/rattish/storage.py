"""On-disk storage policy for registered Rattish workflows."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from gofer.utils.atomic_output import (
    atomic_binary_output,
    mkdir_without_links,
    open_binary_input,
    scandir_without_links,
    unlink_without_links,
)


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
    if not source.exists():
        return
    try:
        # Both roots can live in user-editable projects. Copy through pinned,
        # link-free paths and publish exclusively so a concurrent migration
        # cannot overwrite a newer destination record.
        with scandir_without_links(source) as entries:
            mkdir_without_links(destination)
            for entry in entries:
                path = source / entry.name
                target = destination / entry.name
                if entry.is_dir(follow_symlinks=False):
                    migrate_legacy_directory(path, target)
                elif entry.is_file(follow_symlinks=False):
                    try:
                        with open_binary_input(path) as input_file:
                            metadata = os.fstat(input_file.fileno())
                            # Preserve the age used by log ordering and retention.
                            with atomic_binary_output(
                                target,
                                exclusive=True,
                                timestamps_ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                            ) as output:
                                shutil.copyfileobj(input_file, output, 1024 * 1024)
                        unlink_without_links(path)
                    except FileExistsError:
                        continue
        unlink_without_links(source, directory=True)
    except OSError:
        return
