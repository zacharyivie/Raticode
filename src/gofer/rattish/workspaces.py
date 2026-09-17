"""Registered Rattish projects and canonical on-disk workflow workspaces."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from gofer.rattish.contracts import canonical_json_bytes
from gofer.rattish.migration import migrate_source as _migrate_source
from gofer.utils.atomic_output import (
    atomic_binary_output,
    mkdir_without_links,
    remove_tree_without_links,
)
from gofer.utils.brand_compat import LEGACY_WORKFLOW_IGNORES, LEGACY_WORKSPACE_DIRECTORIES
from gofer.utils.paths import get_data_dir

REGISTRY_VERSION = 1
REGISTRY_FILE = "workspace-registry.json"
WORKSPACE_DIRECTORY = ".raticode"
WORKFLOW_ENTRYPOINT = "workflow.rattish"
WORKFLOW_METADATA = "workflow.metadata.json"
WORKFLOW_IGNORE = ".raticodeignore"
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

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.workflow_id,
            "name": self.name,
            "projectRoot": str(self.project_root),
            "projectName": self.project_root.name or str(self.project_root),
            "workflowRoot": str(self.workflow_root),
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
                        os.fchmod(output.fileno(), source.stat().st_mode & 0o777)
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


def discover_registered_workflows(
    project_root: Path,
    *,
    registry_dir: Path | None = None,
) -> tuple[RegisteredWorkflow, ...]:
    """Register only .raticode/<workflow-name>/workflow.rattish entrypoints."""
    project_root = project_root.expanduser().resolve()
    if not project_root.is_dir():
        raise RattishWorkspaceError(f"Project folder does not exist: {project_root}")

    workspace_root = project_root / WORKSPACE_DIRECTORY
    if workspace_root.is_symlink():
        raise RattishWorkspaceError("The workflow workspace must not be a symbolic link")
    candidates: set[Path] = set()
    if workspace_root.is_dir():
        for workflow_root in workspace_root.iterdir():
            if workflow_root.is_symlink() or not workflow_root.is_dir():
                continue
            entrypoint = workflow_root / WORKFLOW_ENTRYPOINT
            if entrypoint.is_file() and not entrypoint.is_symlink():
                candidates.add(entrypoint)

    registry_root = (registry_dir or get_data_dir()).expanduser().resolve()
    document = _read_registry(registry_root)
    existing = [_registered_workflow(item) for item in document["workflows"]]
    existing_by_source = {workflow.entrypoint.resolve(): workflow for workflow in existing}
    discovered: list[RegisteredWorkflow] = []
    changed = False

    for entrypoint in sorted(candidates):
        workflow_root = entrypoint.parent
        registered = existing_by_source.get(entrypoint)
        if registered is not None:
            discovered.append(registered)
            continue
        workflow_name = workflow_root.name
        workflow_id = _allocate_workflow_id(
            _slugify(workflow_name),
            document,
            project_root,
            existing_workspace=workflow_root,
        )
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

    if changed:
        _write_registry(registry_root, document)
    return tuple(discovered)


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


def _read_registry(registry_dir: Path) -> dict[str, Any]:
    path = registry_dir / "radish" / REGISTRY_FILE
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
    project_root = workflow.project_root.expanduser().resolve()
    workflow_root = workflow.workflow_root.expanduser().resolve()
    entrypoint = workflow.entrypoint.expanduser().resolve()
    return (
        workflow_root.parent == project_root / WORKSPACE_DIRECTORY
        and entrypoint == workflow_root / WORKFLOW_ENTRYPOINT
        and not workflow.workflow_root.is_symlink()
        and not workflow.entrypoint.is_symlink()
        and not (project_root / WORKSPACE_DIRECTORY).is_symlink()
    )


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
