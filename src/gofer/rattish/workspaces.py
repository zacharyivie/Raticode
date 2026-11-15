"""Registered Rattish projects and canonical on-disk workflow workspaces."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from gofer.rattish.contracts import canonical_json_bytes
from gofer.rattish.migration import migrate_source as _migrate_source
from gofer.rattish.project_scan import path_is_in_skipped_directory, scan_project_workflows
from gofer.utils.atomic_output import (
    atomic_binary_output,
    mkdir_without_links,
    remove_tree_without_links,
)
from gofer.utils.brand_compat import LEGACY_WORKFLOW_IGNORES, LEGACY_WORKSPACE_DIRECTORIES
from gofer.utils.paths import get_data_dir

REGISTRY_VERSION = 1
REGISTRY_FILE = "workspace-registry.json"
# Request handlers look workflows up many times a second, and reading and
# validating the registry takes about 11 ms with 80 workflows. A parsed copy is
# reused while the file is unchanged, for at most this long, so a workspace moved
# on disk is still noticed (see _read_registry).
REGISTRY_CACHE_SECONDS = 2.0
_registry_cache: dict[Path, tuple[tuple[int, int, int], float, dict[str, Any]]] = {}
_registry_cache_lock = threading.Lock()
WORKSPACE_DIRECTORY = ".raticode"
WORKFLOW_ENTRYPOINT = "workflow.rattish"
WORKFLOW_METADATA = "workflow.metadata.json"
WORKFLOW_IGNORE = ".raticodeignore"
# Permission bits a copied or imported workflow file may keep: owner bits and group/other
# read and execute. Never setuid/setgid/sticky, and never group/other write.
SAFE_FILE_MODE_MASK = 0o755
DEFAULT_RATICODE_IGNORE = """# Sensitive local configuration
.env
.env.*
*.pem
*.key
*.p12
*.pfx
credentials.json
secrets.json

# Development and runtime state
.git/
.venv/
venv/
node_modules/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.log
logs/
checkpoints/
compiled/
agent-memory/
"""


class RattishWorkspaceError(ValueError):
    """Raised when a project or registered workflow workspace is invalid."""


@dataclass(frozen=True, slots=True)
class RegisteredWorkflow:
    workflow_id: str
    name: str
    project_root: Path
    workflow_root: Path
    entrypoint: Path
    created_at: str

    @property
    def managed(self) -> bool:
        """Whether the workflow lives in the project's .raticode folder.

        Managed workflows keep their compiled builds, run records and agent memory in
        their own git-ignored folder. Workflows found elsewhere in a project keep them in
        the app's data folder, so running one never writes into the project's source.
        """
        return _is_managed_workspace(self)

    @property
    def relative_folder(self) -> str:
        """The workflow's folder relative to its project, with / separators."""
        try:
            relative = self.workflow_root.relative_to(self.project_root)
        except ValueError:
            return str(self.workflow_root)
        return relative.as_posix() if relative.parts else "."

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.workflow_id,
            "name": self.name,
            "projectRoot": str(self.project_root),
            "projectName": self.project_root.name or str(self.project_root),
            "workflowRoot": str(self.workflow_root),
            "relativeFolder": self.relative_folder,
            "managed": self.managed,
            "sourcePath": str(self.entrypoint),
            "createdAt": self.created_at,
        }


def create_registered_workflow(
    project_root: Path,
    name: str,
    *,
    registry_dir: Path | None = None,
) -> RegisteredWorkflow:
    """Create and register one Rattish workflow below a repository project folder."""
    project_root = project_root.expanduser().resolve()
    if not project_root.is_dir():
        raise RattishWorkspaceError(f"Project folder does not exist: {project_root}")
    workflow_name = name.strip()
    if not workflow_name:
        raise RattishWorkspaceError("Workflow name is required")

    registry_root = (registry_dir or get_data_dir()).expanduser().resolve()
    document = _read_registry(registry_root)
    workflow_id = _allocate_workflow_id(_slugify(workflow_name), document, project_root)
    workflow_root = project_root / WORKSPACE_DIRECTORY / workflow_id
    workspace_created = False
    try:
        from gofer.rattish.artifacts import compile_rattish_file

        mkdir_without_links(workflow_root, exclusive=True)
        workspace_created = True
        entrypoint = workflow_root / WORKFLOW_ENTRYPOINT
        _write_text_atomic(entrypoint, _initial_source(workflow_name))
        _write_json_atomic(workflow_root / WORKFLOW_METADATA, _initial_metadata())
        _write_text_atomic(workflow_root / WORKFLOW_IGNORE, DEFAULT_RATICODE_IGNORE)
        compile_rattish_file(
            entrypoint,
            data_dir=registry_root,
            workflow_id=workflow_id,
        )
        created_at = datetime.now(UTC).isoformat()
        registered = RegisteredWorkflow(
            workflow_id=workflow_id,
            name=workflow_name,
            project_root=project_root,
            workflow_root=workflow_root,
            entrypoint=entrypoint,
            created_at=created_at,
        )
        _register(document, registered)
        _write_registry(registry_root, document)
    except Exception:
        if workspace_created:
            try:
                remove_tree_without_links(workflow_root)
            except OSError:
                pass
        raise
    return registered


def install_registered_workflow(
    project_root: Path,
    staged_root: Path,
    name: str,
    requested_id: str,
    *,
    registry_dir: Path | None = None,
) -> RegisteredWorkflow:
    """Move a validated workflow workspace into a project and register it."""
    project_root = project_root.expanduser().resolve()
    staged_root = staged_root.expanduser().resolve()
    if not project_root.is_dir():
        raise RattishWorkspaceError(f"Project folder does not exist: {project_root}")
    for legacy_source in staged_root.rglob("*.rad"):
        migrate_source(legacy_source)
    if not staged_root.is_dir() or not (staged_root / WORKFLOW_ENTRYPOINT).is_file():
        raise RattishWorkspaceError("Imported workflow is missing workflow.rattish")
    workflow_name = name.strip()
    if not workflow_name:
        raise RattishWorkspaceError("Imported workflow name is required")

    registry_root = (registry_dir or get_data_dir()).expanduser().resolve()
    document = _read_registry(registry_root)
    workflow_id = _allocate_workflow_id(
        _slugify(requested_id or workflow_name),
        document,
        project_root,
    )
    workflow_root = project_root / WORKSPACE_DIRECTORY / workflow_id
    mkdir_without_links(workflow_root.parent)
    installed = False
    try:
        mkdir_without_links(workflow_root, exclusive=True)
        installed = True
        # Copy only validated regular files. Each destination is independently
        # opened below no-follow parents, including directories created mid-copy.
        for directory, folders, files in os.walk(staged_root, followlinks=False):
            relative = Path(directory).relative_to(staged_root)
            for folder in folders:
                if (Path(directory) / folder).is_symlink():
                    raise RattishWorkspaceError("Imported workflow contains a directory link")
                mkdir_without_links(workflow_root / relative / folder)
            for filename in files:
                source = Path(directory) / filename
                if source.is_symlink() or not source.is_file():
                    raise RattishWorkspaceError("Imported workflow contains a non-regular file")
                with (
                    source.open("rb") as input_file,
                    atomic_binary_output(workflow_root / relative / filename) as output,
                ):
                    shutil.copyfileobj(input_file, output, 1024 * 1024)
                    if os.name != "nt":
                        os.fchmod(output.fileno(), source.stat().st_mode & SAFE_FILE_MODE_MASK)
        shutil.rmtree(staged_root)
        entrypoint = workflow_root / WORKFLOW_ENTRYPOINT
        if not (workflow_root / WORKFLOW_METADATA).is_file():
            _write_json_atomic(workflow_root / WORKFLOW_METADATA, _initial_metadata())
        for ignore_name in LEGACY_WORKFLOW_IGNORES:
            legacy_ignore = workflow_root / ignore_name
            if legacy_ignore.is_file() and not (workflow_root / WORKFLOW_IGNORE).exists():
                legacy_ignore.rename(workflow_root / WORKFLOW_IGNORE)
        if not (workflow_root / WORKFLOW_IGNORE).is_file():
            _write_text_atomic(workflow_root / WORKFLOW_IGNORE, DEFAULT_RATICODE_IGNORE)

        from gofer.rattish.artifacts import compile_rattish_file

        compile_rattish_file(entrypoint, data_dir=registry_root, workflow_id=workflow_id)
        registered = RegisteredWorkflow(
            workflow_id=workflow_id,
            name=workflow_name,
            project_root=project_root,
            workflow_root=workflow_root,
            entrypoint=entrypoint,
            created_at=datetime.now(UTC).isoformat(),
        )
        _register(document, registered)
        _write_registry(registry_root, document)
        return registered
    except Exception:
        if installed:
            try:
                remove_tree_without_links(workflow_root)
            except OSError:
                pass
        raise


@dataclass(frozen=True, slots=True)
class ProjectDiscovery:
    """Workflows registered for one project, and whether the folder walk hit a limit."""

    workflows: tuple[RegisteredWorkflow, ...]
    limited: bool = False


def discover_registered_workflows(
    project_root: Path,
    *,
    registry_dir: Path | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
) -> tuple[RegisteredWorkflow, ...]:
    """Register every workflow in a project: see discover_project_workflows."""
    return discover_project_workflows(
        project_root, registry_dir=registry_dir, check_cancelled=check_cancelled
    ).workflows


def discover_project_workflows(
    project_root: Path,
    *,
    registry_dir: Path | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
) -> ProjectDiscovery:
    """Register .raticode/<name>/workflow.rattish and every other workflow.rattish below.

    The rest of the project is walked without following links and without entering
    dependency, build, cache or Raticode worktree folders (see project_scan). Registrations
    of workflows found outside .raticode whose file is gone are removed, so renaming or
    deleting one doesn't leave a broken entry behind.
    """
    check_cancelled()
    project_root = project_root.expanduser().resolve()
    if not project_root.is_dir():
        raise RattishWorkspaceError(f"Project folder does not exist: {project_root}")

    workspace_root = project_root / WORKSPACE_DIRECTORY
    if workspace_root.is_symlink():
        raise RattishWorkspaceError("The workflow workspace must not be a symbolic link")
    managed_candidates: set[Path] = set()
    if workspace_root.is_dir():
        for workflow_root in workspace_root.iterdir():
            check_cancelled()
            if workflow_root.is_symlink() or not workflow_root.is_dir():
                continue
            entrypoint = workflow_root / WORKFLOW_ENTRYPOINT
            if entrypoint.is_file() and not entrypoint.is_symlink():
                managed_candidates.add(entrypoint)

    check_cancelled()
    registry_root = (registry_dir or get_data_dir()).expanduser().resolve()
    scan = scan_project_workflows(
        project_root,
        data_dir=registry_root,
        check_cancelled=check_cancelled,
    )
    project_candidates = [path for path in scan.entrypoints if path not in managed_candidates]

    check_cancelled()
    document = _read_registry(registry_root)
    existing = [_registered_workflow(item) for item in document["workflows"]]
    existing_by_source = {workflow.entrypoint.resolve(): workflow for workflow in existing}
    discovered: list[RegisteredWorkflow] = []
    changed = _forget_missing_project_workflows(document, project_root, set(project_candidates))

    # Managed workflows go first so their folder names keep the plain IDs.
    for entrypoint in [*sorted(managed_candidates), *project_candidates]:
        check_cancelled()
        workflow_root = entrypoint.parent
        registered = existing_by_source.get(entrypoint)
        if registered is not None:
            discovered.append(registered)
            continue
        managed = entrypoint in managed_candidates
        workflow_name = workflow_root.name or project_root.name or "workflow"
        if managed:
            workflow_id = _allocate_workflow_id(
                _slugify(workflow_name),
                document,
                project_root,
                existing_workspace=workflow_root,
            )
        else:
            workflow_id = _allocate_project_workflow_id(document, project_root, workflow_root)
        registered = RegisteredWorkflow(
            workflow_id=workflow_id,
            name=workflow_name,
            project_root=project_root,
            workflow_root=workflow_root,
            entrypoint=entrypoint,
            created_at=datetime.now(UTC).isoformat(),
        )
        _register(document, registered)
        existing_by_source[entrypoint] = registered
        discovered.append(registered)
        changed = True

    check_cancelled()
    if changed:
        _write_registry(registry_root, document)
    return ProjectDiscovery(tuple(discovered), scan.limited)


def _forget_missing_project_workflows(
    document: dict[str, Any], project_root: Path, found: set[Path]
) -> bool:
    """Drop this project's registrations outside .raticode whose file no longer exists.

    Only a missing file counts: a registration the walk skipped, for example past a
    limit or in an unreadable folder, stays.
    """
    kept = []
    for item in document["workflows"]:
        workflow = _registered_workflow(item)
        if (
            Path(item["project_root"]) == project_root
            and not _is_managed_workspace(workflow)
            and workflow.entrypoint.name == WORKFLOW_ENTRYPOINT
            and workflow.entrypoint not in found
            and not os.path.lexists(workflow.entrypoint)
        ):
            continue
        kept.append(item)
    if len(kept) == len(document["workflows"]):
        return False
    document["workflows"] = kept
    return True


def list_registered_workflows(
    *, registry_dir: Path | None = None
) -> tuple[RegisteredWorkflow, ...]:
    registry_root = (registry_dir or get_data_dir()).expanduser().resolve()
    document = _read_registry(registry_root)
    workflows = [_registered_workflow(item) for item in document["workflows"]]
    workflows = [
        workflow for workflow in workflows if _registered_workflow_is_in_workspace(workflow)
    ]
    return tuple(sorted(workflows, key=lambda item: (str(item.project_root), item.workflow_id)))


def find_registered_workflow(
    workflow_id: str,
    *,
    registry_dir: Path | None = None,
) -> RegisteredWorkflow:
    canonical = workflow_id.lower()
    matches = [
        workflow
        for workflow in list_registered_workflows(registry_dir=registry_dir)
        if workflow.workflow_id.lower() == canonical
    ]
    if not matches:
        raise RattishWorkspaceError(f"Registered workflow not found: {workflow_id}")
    if len(matches) > 1:
        raise RattishWorkspaceError(f"Workflow ID is registered more than once: {workflow_id}")
    return matches[0]


def delete_registered_workflow(
    workflow_id: str,
    *,
    registry_dir: Path | None = None,
) -> RegisteredWorkflow:
    """Delete a managed workspace or standalone source and remove its registration."""
    registry_root = (registry_dir or get_data_dir()).expanduser().resolve()
    document = _read_registry(registry_root)
    matches = [
        (index, item)
        for index, item in enumerate(document["workflows"])
        if str(item["id"]).lower() == workflow_id.lower()
    ]
    if len(matches) != 1:
        raise RattishWorkspaceError(f"Registered workflow not found: {workflow_id}")
    index, item = matches[0]
    workflow = _registered_workflow(item)
    if not _registered_workflow_is_in_workspace(workflow):
        raise RattishWorkspaceError(
            f"Refusing to delete workflow outside its project: {workflow.entrypoint}"
        )
    if _is_managed_workspace(workflow):
        if workflow.workflow_root.exists():
            remove_tree_without_links(workflow.workflow_root, onerror=_remove_readonly_file)
    else:
        workflow.entrypoint.unlink(missing_ok=True)
        _workflow_metadata_path(workflow).unlink(missing_ok=True)
    document["workflows"].pop(index)
    _write_registry(registry_root, document)
    return workflow


def _remove_readonly_file(function: Any, target_path: str, _error: Any) -> None:
    """Retry Windows cleanup after making a read-only workflow file writable."""
    os.chmod(target_path, stat.S_IWRITE)
    function(target_path)


def read_workflow_metadata(workflow: RegisteredWorkflow) -> dict[str, Any]:
    """Read and validate editor metadata for a registered workflow."""
    path = _workflow_metadata_path(workflow)
    if not path.exists():
        return _initial_metadata()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator(_metadata_schema()).validate(document)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise RattishWorkspaceError(f"Invalid workflow metadata {path}: {exc}") from exc
    return cast(dict[str, Any], document)


def write_workflow_metadata(workflow: RegisteredWorkflow, document: dict[str, Any]) -> None:
    """Validate and atomically replace editor metadata."""
    try:
        Draft202012Validator(_metadata_schema()).validate(document)
    except ValidationError as exc:
        raise RattishWorkspaceError(
            f"Refusing to write invalid workflow metadata: {exc.message}"
        ) from exc
    _write_json_atomic(_workflow_metadata_path(workflow), document)


def write_workflow_source(workflow: RegisteredWorkflow, source: str) -> None:
    """Atomically replace the registered Rattish entrypoint."""
    _write_text_atomic(workflow.entrypoint, source)


def update_registered_workflow_name(
    workflow_id: str,
    name: str,
    *,
    registry_dir: Path | None = None,
) -> None:
    """Update the display name without changing the installed workflow ID."""
    workflow_name = name.strip()
    if not workflow_name:
        raise RattishWorkspaceError("Workflow name is required")
    registry_root = (registry_dir or get_data_dir()).expanduser().resolve()
    document = _read_registry(registry_root)
    matches = [
        item for item in document["workflows"] if str(item["id"]).lower() == workflow_id.lower()
    ]
    if len(matches) != 1:
        raise RattishWorkspaceError(f"Registered workflow not found: {workflow_id}")
    matches[0]["name"] = workflow_name
    _write_registry(registry_root, document)


def _registry_schema() -> dict[str, Any]:
    schema_path = Path(__file__).with_name("assets") / "schemas" / "workspace-registry.schema.json"
    if not schema_path.is_file():
        schema_path = (
            Path(__file__).parents[3] / "rattish" / "schemas" / "workspace-registry.schema.json"
        )
    try:
        return cast(dict[str, Any], json.loads(schema_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RattishWorkspaceError(f"Could not load workspace registry schema: {exc}") from exc


def _metadata_schema() -> dict[str, Any]:
    schema_path = Path(__file__).with_name("assets") / "schemas" / "workflow-metadata.schema.json"
    if not schema_path.is_file():
        schema_path = Path(__file__).parents[3] / "rattish" / "schemas" / schema_path.name
    try:
        return cast(dict[str, Any], json.loads(schema_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RattishWorkspaceError(f"Could not load workflow metadata schema: {exc}") from exc


def _empty_registry() -> dict[str, Any]:
    return {"registry_version": REGISTRY_VERSION, "workflows": []}


def _registry_stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    # Saving replaces the file, so a new inode marks a change even within one tick.
    return (info.st_mtime_ns, info.st_size, info.st_ino)


def _read_registry(registry_dir: Path) -> dict[str, Any]:
    path = registry_dir / "radish" / REGISTRY_FILE
    stamp = _registry_stamp(path)
    if stamp is None:
        return _empty_registry()
    with _registry_cache_lock:
        cached = _registry_cache.get(path)
    if cached and cached[0] == stamp and time.monotonic() - cached[1] < REGISTRY_CACHE_SECONDS:
        # Callers may change the document before saving it, so each gets its own copy.
        return copy.deepcopy(cached[2])
    document = _load_registry(registry_dir, path)
    stamp = _registry_stamp(path)
    if stamp is not None:
        with _registry_cache_lock:
            _registry_cache[path] = (stamp, time.monotonic(), copy.deepcopy(document))
    return document


def _load_registry(registry_dir: Path, path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_registry()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator(_registry_schema()).validate(document)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise RattishWorkspaceError(f"Invalid Rattish workspace registry {path}: {exc}") from exc
    previous = canonical_json_bytes(document)
    # A project may already have renamed its workspace on disk. Keep its IDs and
    # creation times when reading the previous registry paths.
    for item in document["workflows"]:
        project = Path(item["project_root"])
        for directory in LEGACY_WORKSPACE_DIRECTORIES:
            old_root = project / directory
            source_root = Path(item["workflow_root"])
            if source_root.is_relative_to(old_root):
                new_root = project / WORKSPACE_DIRECTORY / source_root.relative_to(old_root)
                if not source_root.exists() and new_root.is_dir():
                    entrypoint = Path(item["entrypoint"])
                    if entrypoint.is_relative_to(source_root):
                        item["workflow_root"] = str(new_root)
                        item["entrypoint"] = str(new_root / entrypoint.relative_to(source_root))
        entrypoint = Path(item["entrypoint"])
        if (
            Path(item["workflow_root"]).parent == project / WORKSPACE_DIRECTORY
            and entrypoint == Path(item["workflow_root"]) / "workflow.rad"
        ):
            item["entrypoint"] = str(migrate_source(entrypoint))
    if canonical_json_bytes(document) != previous:
        _write_registry(registry_dir, document)
    return cast(dict[str, Any], document)


def _write_registry(registry_dir: Path, document: dict[str, Any]) -> None:
    try:
        Draft202012Validator(_registry_schema()).validate(document)
    except ValidationError as exc:
        raise RattishWorkspaceError(
            f"Refusing to write invalid workspace registry: {exc.message}"
        ) from exc
    _write_json_atomic(registry_dir / "radish" / REGISTRY_FILE, document)


def _register(document: dict[str, Any], workflow: RegisteredWorkflow) -> None:
    if any(
        str(item["id"]).lower() == workflow.workflow_id.lower() for item in document["workflows"]
    ):
        raise RattishWorkspaceError(f"Workflow ID is already registered: {workflow.workflow_id}")
    document["workflows"].append(
        {
            "id": workflow.workflow_id,
            "name": workflow.name,
            "project_root": str(workflow.project_root),
            "workflow_root": str(workflow.workflow_root),
            "entrypoint": str(workflow.entrypoint),
            "created_at": workflow.created_at,
        }
    )
    document["workflows"].sort(key=lambda item: (item["project_root"], item["id"]))


def _allocate_workflow_id(
    requested: str,
    document: dict[str, Any],
    project_root: Path,
    *,
    existing_workspace: Path | None = None,
) -> str:
    registered_ids = {str(item["id"]).lower() for item in document["workflows"]}
    workspace = project_root / WORKSPACE_DIRECTORY
    suffix = 1
    while True:
        candidate = requested if suffix == 1 else f"{requested}-{suffix}"
        candidate_workspace = workspace / candidate
        workspace_available = not candidate_workspace.exists() or (
            existing_workspace is not None
            and candidate_workspace.resolve() == existing_workspace.resolve()
        )
        if candidate.lower() not in registered_ids and workspace_available:
            return candidate
        suffix += 1


# Workflow IDs may be 128 characters; leave room for a numeric suffix.
_MAX_ALLOCATED_ID_LENGTH = 100


def _allocate_project_workflow_id(
    document: dict[str, Any], project_root: Path, workflow_root: Path
) -> str:
    """Pick a stable ID for a workflow found outside .raticode.

    The folder name comes first (jev-demo), then the folder's path in the project
    (rattish-starters-jev-demo), then that path with a number. IDs are kept in the
    registry, so a workflow keeps its ID once it has one.
    """
    relative = workflow_root.relative_to(project_root)
    folder = workflow_root.name if relative.parts else project_root.name
    candidates = [_trim_id(_slugify(folder or "workflow"))]
    if len(relative.parts) > 1:
        candidates.append(_trim_id(_slugify("-".join(relative.parts))))
    registered_ids = {str(item["id"]).lower() for item in document["workflows"]}
    for candidate in candidates:
        managed_folder = project_root / WORKSPACE_DIRECTORY / candidate
        if candidate not in registered_ids and not managed_folder.exists():
            return candidate
    return _allocate_workflow_id(candidates[-1], document, project_root)


def _trim_id(slug: str) -> str:
    return slug[:_MAX_ALLOCATED_ID_LENGTH].rstrip("-") or "workflow"


def _registered_workflow(item: dict[str, Any]) -> RegisteredWorkflow:
    return RegisteredWorkflow(
        workflow_id=item["id"],
        name=item["name"],
        project_root=Path(item["project_root"]),
        workflow_root=Path(item["workflow_root"]),
        entrypoint=Path(item["entrypoint"]),
        created_at=item["created_at"],
    )


def _registered_workflow_is_in_workspace(workflow: RegisteredWorkflow) -> bool:
    """Whether a registration still points at a workflow this project may list and run.

    Either .raticode/<name>/workflow.rattish, or a workflow.rattish found elsewhere in the
    project, reached without any link and outside the folders discovery skips.
    """
    project_root = workflow.project_root.expanduser().resolve()
    workflow_root = workflow.workflow_root.expanduser().resolve()
    entrypoint = workflow.entrypoint.expanduser().resolve()
    if (
        entrypoint != workflow_root / WORKFLOW_ENTRYPOINT
        or workflow.workflow_root.is_symlink()
        or workflow.entrypoint.is_symlink()
    ):
        return False
    if workflow_root.parent == project_root / WORKSPACE_DIRECTORY:
        return not (project_root / WORKSPACE_DIRECTORY).is_symlink()
    return _is_project_workflow(workflow, project_root, entrypoint)


def _is_project_workflow(
    workflow: RegisteredWorkflow, project_root: Path, entrypoint: Path
) -> bool:
    # The stored path must already be canonical: a link anywhere along it would make
    # resolve() differ, which is how a link out of the project is refused.
    stored = workflow.entrypoint.expanduser()
    if not stored.is_absolute() or stored != entrypoint:
        return False
    try:
        relative = entrypoint.relative_to(project_root)
    except ValueError:
        return False
    return not path_is_in_skipped_directory(relative) and entrypoint.is_file()


def _workflow_metadata_path(workflow: RegisteredWorkflow) -> Path:
    if _is_managed_workspace(workflow):
        return workflow.workflow_root / WORKFLOW_METADATA
    return workflow.entrypoint.with_suffix(".metadata.json")


def _is_managed_workspace(workflow: RegisteredWorkflow) -> bool:
    project_root = workflow.project_root.expanduser().resolve()
    workflow_root = workflow.workflow_root.expanduser().resolve()
    return workflow.entrypoint.name.lower() == WORKFLOW_ENTRYPOINT and any(
        workflow_root.is_relative_to(project_root / directory)
        and workflow_root != project_root / directory
        for directory in (WORKSPACE_DIRECTORY, *LEGACY_WORKSPACE_DIRECTORIES)
    )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        return "workflow"
    return slug if slug[0].isalpha() else f"workflow-{slug}"


def _initial_source(name: str) -> str:
    encoded_name = json.dumps(name, ensure_ascii=False)
    return f"Rattish: 1\n\nWorkflow:\n  name: {encoded_name}\n"


def _initial_metadata() -> dict[str, Any]:
    return {
        "metadataVersion": 1,
        "canvas": {"nodes": {}, "zoom": 1.0, "pan": {"x": 0, "y": 0}},
        "editor": {"foldedDeclarations": []},
    }


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    _write_bytes_atomic(path, canonical_json_bytes(document) + b"\n")


def _write_text_atomic(path: Path, content: str) -> None:
    _write_bytes_atomic(path, content.encode("utf-8"))


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    try:
        with atomic_binary_output(path) as output:
            output.write(content)
    except OSError as exc:
        raise RattishWorkspaceError(f"Could not write {path}: {exc}") from exc


def migrate_source(path: Path) -> Path:
    """Expose migration failures through the workspace API's normal error handling."""
    try:
        return _migrate_source(path)
    except OSError as exc:
        raise RattishWorkspaceError(str(exc)) from exc
