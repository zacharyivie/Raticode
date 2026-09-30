from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import stat
import time
import tomllib
import urllib.parse
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any, cast

import tomli_w

from gofer.core.operations import SubflowOperation
from gofer.core.references import parse_exact_reference
from gofer.core.resources import ResourceLimits, bundle_resource_limits_from_env
from gofer.core.workflow import AgenticWorkflow, WebhookTriggerConfig, validate_workflow_id
from gofer.utils.atomic_output import (
    atomic_binary_output,
    open_binary_input,
    scandir_without_links,
)

BUNDLE_FORMAT_VERSION = 1
MANIFEST_PATH = "manifest.json"
WORKFLOW_PATH = "workflow.toml"
ASSET_PREFIX = "assets/"
IMPORTED_ASSET_PREFIX = "bundle-assets"
SECRET_TOKEN_PATTERN = re.compile(
    r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}|secret:([A-Za-z_][A-Za-z0-9_.-]*)"
)
SENSITIVE_FIELD_NAMES = {
    "access-key",
    "access_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "x_api_key",
    "api-key",
    "api_key",
    "passwd",
    "private_key",
    "token",
    "password",
    "secret",
}
MASKED_SECRET_VALUE = "***"


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class BundlePath:
    source: Path
    workflow_path: str
    archive_path: str
    kind: str


@dataclass(frozen=True)
class ExternalRequirement:
    path: str
    reason: str
    owner: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason, "owner": self.owner}


@dataclass(frozen=True)
class BundleManifest:
    format_version: int
    workflow_id: str
    workflow_name: str
    gofer_flow_version: str
    included_paths: list[dict[str, str]]
    required_secrets: list[dict[str, str]]
    provider_assumptions: list[dict[str, str]]
    triggers: list[dict[str, str]]
    external_requirements: list[dict[str, str]]
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "formatVersion": self.format_version,
            "workflow": {
                "id": self.workflow_id,
                "name": self.workflow_name,
            },
            "goferFlowVersion": self.gofer_flow_version,
            "includedPaths": self.included_paths,
            "requiredSecrets": self.required_secrets,
            "providerAssumptions": self.provider_assumptions,
            "triggers": self.triggers,
            "externalRequirements": self.external_requirements,
        }
        if self.notes:
            payload["notes"] = self.notes
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleManifest:
        if not isinstance(data, dict) or not isinstance(data.get("workflow", {}), dict):
            raise BundleError("Bundle manifest and workflow metadata must be objects")
        pending: list[tuple[Any, int]] = [(data, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 64:
                raise BundleError("Bundle manifest nesting is too deep")
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        if type(data.get("formatVersion", 0)) is not int:
            raise BundleError("Bundle manifest formatVersion must be an integer")
        for key in (
            "includedPaths",
            "requiredSecrets",
            "providerAssumptions",
            "triggers",
            "externalRequirements",
        ):
            items = data.get(key, [])
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise BundleError(f"Bundle manifest {key} must be a list of objects")
        for item in data.get("includedPaths", []):
            if any(
                not isinstance(item.get(key), str) or not item[key]
                for key in ("path", "archivePath")
            ):
                raise BundleError("Bundle manifest includedPaths entries need path and archivePath")
        workflow = data.get("workflow", {})
        return cls(
            format_version=int(data.get("formatVersion", 0)),
            workflow_id=str(workflow.get("id") or ""),
            workflow_name=str(workflow.get("name") or ""),
            gofer_flow_version=str(data.get("goferFlowVersion") or "unknown"),
            included_paths=[
                {str(k): str(v) for k, v in item.items()}
                for item in data.get("includedPaths", [])
                if isinstance(item, dict)
            ],
            required_secrets=[
                {str(k): str(v) for k, v in item.items()}
                for item in data.get("requiredSecrets", [])
                if isinstance(item, dict)
            ],
            provider_assumptions=[
                {str(k): str(v) for k, v in item.items()}
                for item in data.get("providerAssumptions", [])
                if isinstance(item, dict)
            ],
            triggers=[
                {str(k): str(v) for k, v in item.items()}
                for item in data.get("triggers", [])
                if isinstance(item, dict)
            ],
            external_requirements=[
                {str(k): str(v) for k, v in item.items()}
                for item in data.get("externalRequirements", [])
                if isinstance(item, dict)
            ],
            notes=str(data["notes"]) if data.get("notes") is not None else None,
        )


@dataclass(frozen=True)
class ImportConflict:
    path: str
    action: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "action": self.action}


@dataclass(frozen=True)
class BundleImportPlan:
    manifest: BundleManifest
    workflow_id: str
    workflow_name: str
    workflow_path: Path
    files_to_create: list[str]
    files_to_overwrite: list[str]
    conflicts: list[ImportConflict]
    external_requirements: list[dict[str, str]]
    required_secrets: list[dict[str, str]]
    path_rewrites: dict[str, str] = field(default_factory=dict)
    risk_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflowId": self.workflow_id,
            "workflowName": self.workflow_name,
            "workflowPath": str(self.workflow_path),
            "manifest": self.manifest.to_dict(),
            "filesToCreate": self.files_to_create,
            "filesToOverwrite": self.files_to_overwrite,
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "externalRequirements": self.external_requirements,
            "requiredSecrets": self.required_secrets,
            "pathRewrites": self.path_rewrites,
            "riskWarnings": self.risk_warnings,
        }


@dataclass(frozen=True)
class _ValidatedBundleArchive:
    names: tuple[str, ...]
    info_by_name: dict[str, zipfile.ZipInfo]


def export_workflow_bundle(
    workflow_ref: str | Path,
    output_path: Path,
    *,
    data_dir: Path | None = None,
    notes: str | None = None,
) -> BundleManifest:
    workflow, workflow_path = _resolve_workflow_with_path(workflow_ref, data_dir)
    raw = _read_workflow_toml(workflow_path)
    sanitized = _sanitized_workflow_data(raw)
    path_base = workflow_path.parent
    included, external = _collect_bundle_paths(workflow, path_base)
    manifest = BundleManifest(
        format_version=BUNDLE_FORMAT_VERSION,
        workflow_id=workflow.config.id,
        workflow_name=workflow.config.name,
        gofer_flow_version=_gofer_version(),
        included_paths=[
            {
                "path": item.workflow_path,
                "archivePath": item.archive_path,
                "kind": item.kind,
            }
            for item in included
        ],
        required_secrets=_required_secrets(raw, workflow, included),
        provider_assumptions=_provider_assumptions(workflow),
        triggers=_triggers(workflow),
        external_requirements=[item.to_dict() for item in external],
        notes=notes,
    )
    with atomic_binary_output(output_path) as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_PATH, json.dumps(manifest.to_dict(), indent=2) + "\n")
            archive.writestr(WORKFLOW_PATH, tomli_w.dumps(sanitized))
            for item in included:
                if item.kind == "subflow":
                    child = _sanitized_workflow_data(_read_workflow_toml(item.source))
                    archive.writestr(item.archive_path, tomli_w.dumps(child))
                elif item.source.is_dir():
                    for file_path in _bundle_asset_paths(item.source):
                        rel = file_path.relative_to(item.source).as_posix()
                        _write_bundle_asset(
                            archive,
                            file_path,
                            _safe_archive_join(item.archive_path, rel),
                        )
                else:
                    _write_bundle_asset(archive, item.source, item.archive_path)
    return manifest


def _bundle_asset_paths(source: Path) -> Iterator[Path]:
    """Enumerate ordinary assets without traversing links or special files."""
    try:
        metadata = source.lstat()
        if stat.S_ISREG(metadata.st_mode):
            yield source
        elif stat.S_ISDIR(metadata.st_mode):
            with scandir_without_links(source) as entries:
                names = sorted(entry.name for entry in entries)
            for name in names:
                yield from _bundle_asset_paths(source / name)
        else:
            raise BundleError(f"Bundle asset must be an ordinary file or directory: {source}")
    except OSError as exc:
        raise BundleError(f"Could not read bundle asset: {source}") from exc


def _write_bundle_asset(archive: zipfile.ZipFile, source: Path, archive_path: str) -> None:
    try:
        with open_binary_input(source) as input_file:
            metadata = os.fstat(input_file.fileno())
            timestamp = time.localtime(metadata.st_mtime)[:6]
            if timestamp[0] < 1980:
                timestamp = (1980, 1, 1, 0, 0, 0)
            elif timestamp[0] > 2107:
                timestamp = (2107, 12, 31, 23, 59, 58)
            info = zipfile.ZipInfo(archive_path, timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = metadata.st_mode << 16
            info.file_size = metadata.st_size
            with archive.open(info, "w") as target:
                shutil.copyfileobj(input_file, target, 1024 * 1024)
    except OSError as exc:
        raise BundleError(f"Could not read bundle asset: {source}") from exc


def preview_workflow_bundle(
    bundle_path: Path,
    *,
    data_dir: Path | None = None,
    limits: ResourceLimits | None = None,
) -> BundleImportPlan:
    return _build_import_plan(
        bundle_path,
        _data_dir(data_dir),
        replace=False,
        limits=limits or bundle_resource_limits_from_env(),
    )


def import_workflow_bundle(
    bundle_path: Path,
    *,
    data_dir: Path | None = None,
    replace: bool = False,
    dry_run: bool = False,
    limits: ResourceLimits | None = None,
) -> BundleImportPlan:
    base = _data_dir(data_dir)
    active_limits = limits or bundle_resource_limits_from_env()
    plan = _build_import_plan(bundle_path, base, replace=replace, limits=active_limits)
    if dry_run:
        return plan
    base.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path) as archive:
        validated = _validate_archive_entries(archive, active_limits)
        workflow_data = _workflow_data_for_import(archive, plan, validated, active_limits)
        rewritten_components = _rewritten_subflow_components(
            archive,
            plan,
            validated,
            active_limits,
        )
        archived_names = validated.names
        for item in plan.manifest.included_paths:
            archive_path = item["archivePath"]
            original_path = item["path"]
            target_rel = plan.path_rewrites.get(original_path, original_path)
            for name in archived_names:
                if name == archive_path or name.startswith(f"{archive_path.rstrip('/')}/"):
                    if name.endswith("/"):
                        continue
                    nested = None
                    if name != archive_path:
                        nested = PurePosixPath(name).relative_to(PurePosixPath(archive_path))
                    destination = _safe_destination(
                        base,
                        target_rel,
                        nested.as_posix() if nested is not None else None,
                    )
                    with archive.open(name) as source:
                        payload = source.read(validated.info_by_name[name].file_size + 1)
                    destination_rel = (
                        _safe_archive_join(
                            target_rel,
                            nested.as_posix(),
                        )
                        if nested is not None
                        else target_rel
                    )
                    if (
                        item.get("kind") == "subflow"
                        and name.endswith(".toml")
                        and rewritten_components
                    ):
                        payload = _rewrite_imported_workflow_bytes(
                            payload,
                            rewritten_components,
                            destination_rel,
                        )
                    _write_imported_file(destination, payload)
        _write_imported_file(plan.workflow_path, tomli_w.dumps(workflow_data).encode())
    AgenticWorkflow.from_file(plan.workflow_path).validate(plan.workflow_path, base)
    return plan


def _write_imported_file(destination: Path, payload: bytes) -> None:
    try:
        mode = destination.lstat().st_mode
    except FileNotFoundError:
        mode = 0
    with atomic_binary_output(destination) as output:
        output.write(payload)
        # Replacement must retain executable bits on existing ordinary assets,
        # without following a planted link or restoring special permission bits.
        if os.name != "nt" and stat.S_ISREG(mode):
            os.fchmod(output.fileno(), stat.S_IMODE(mode) & 0o777)


def _build_import_plan(
    bundle_path: Path,
    base: Path,
    *,
    replace: bool,
    limits: ResourceLimits,
) -> BundleImportPlan:
    if not bundle_path.exists():
        raise BundleError(f"{bundle_path} not found")
    with zipfile.ZipFile(bundle_path) as archive:
        validated = _validate_archive_entries(archive, limits)
        try:
            manifest = BundleManifest.from_dict(
                json.loads(_read_archive_text(archive, MANIFEST_PATH, validated, limits))
            )
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise BundleError("Bundle has no valid manifest") from exc
        if manifest.format_version != BUNDLE_FORMAT_VERSION:
            raise BundleError(f"Unsupported bundle format version {manifest.format_version}")
        raw = tomllib.loads(_read_archive_text(archive, WORKFLOW_PATH, validated, limits))
        archived_asset_files = _archived_asset_files(validated, manifest)
        bundled_components = _bundled_subflow_components(
            archive,
            manifest,
            validated,
            limits,
        )
    workflow = AgenticWorkflow.from_dict(raw)
    requested_id = workflow.config.id
    workflow_id = requested_id if replace else _available_workflow_id(requested_id, base)
    workflow_name = (
        workflow.config.name
        if workflow_id == requested_id
        else f"{workflow.config.name} ({workflow_id})"
    )
    workflow_path = _safe_destination(base, f"{workflow_id}.toml")

    path_rewrites: dict[str, str] = {}
    conflicts: list[ImportConflict] = []
    asset_conflict = any(
        _safe_destination(base, item["path"]).exists() for item in manifest.included_paths
    )
    if asset_conflict and not replace:
        for item in manifest.included_paths:
            path_rewrites[item["path"]] = f"{IMPORTED_ASSET_PREFIX}/{workflow_id}/{item['path']}"
            conflicts.append(
                ImportConflict(
                    path=item["path"],
                    action=f"rename to {path_rewrites[item['path']]}",
                )
            )
    if not replace:
        for component_id, bundled in bundled_components.items():
            item_path = bundled.get("path", "")
            version = bundled.get("version", "")
            if not item_path or not version or item_path in path_rewrites:
                continue
            for existing_path, existing_version in _existing_subflow_component_versions(
                base,
                component_id,
            ):
                if existing_version == version:
                    continue
                path_rewrites[item_path] = f"{IMPORTED_ASSET_PREFIX}/{workflow_id}/{item_path}"
                conflicts.append(
                    ImportConflict(
                        path=existing_path.relative_to(base).as_posix(),
                        action=(
                            f"component version conflict: existing {existing_version}, "
                            f"bundle requires {version}; rename bundled component to "
                            f"{path_rewrites[item_path]}"
                        ),
                    )
                )
                break
    if workflow_path.exists() and not replace:
        conflicts.append(
            ImportConflict(
                path=f"{requested_id}.toml",
                action=f"rename to {workflow_id}.toml",
            )
        )
    conflicts.extend(
        _subflow_version_conflicts(
            archive_payload=raw,
            manifest=manifest,
            base=base,
            bundled_component_versions={
                component_id: data["version"]
                for component_id, data in bundled_components.items()
                if data.get("version")
            },
        )
    )

    files_to_create: list[str] = []
    files_to_overwrite: list[str] = []
    import_paths = [f"{workflow_id}.toml"]
    for item, nested in archived_asset_files:
        target_rel = path_rewrites.get(item["path"], item["path"])
        import_paths.append(_safe_archive_join(target_rel, nested) if nested else target_rel)
    for rel in import_paths:
        destination = _safe_destination(base, rel)
        if destination.exists():
            files_to_overwrite.append(destination.relative_to(base).as_posix())
        else:
            files_to_create.append(destination.relative_to(base).as_posix())

    return BundleImportPlan(
        manifest=manifest,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        workflow_path=workflow_path,
        files_to_create=files_to_create,
        files_to_overwrite=files_to_overwrite,
        conflicts=conflicts,
        external_requirements=manifest.external_requirements,
        required_secrets=manifest.required_secrets,
        path_rewrites=path_rewrites,
        risk_warnings=_import_risk_warnings(workflow),
    )


def _subflow_version_conflicts(
    *,
    archive_payload: dict[str, Any],
    manifest: BundleManifest,
    base: Path,
    bundled_component_versions: dict[str, str],
) -> list[ImportConflict]:
    conflicts: list[ImportConflict] = []
    referenced_versions: dict[str, str] = dict(bundled_component_versions)
    for node in archive_payload.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "subflow":
            continue
        component_id = str(node.get("component_id") or "").strip()
        version = str(node.get("version") or "").strip()
        if component_id and version:
            referenced_versions[component_id] = version
    if not referenced_versions:
        return conflicts
    for item in manifest.included_paths:
        if item.get("kind") != "subflow":
            continue
        target = _safe_destination(base, item["path"])
        if not target.exists():
            continue
        try:
            existing = AgenticWorkflow.from_file(target)
        except Exception:
            continue
        if existing.component is None:
            continue
        requested = referenced_versions.get(existing.component.id)
        if requested and existing.component.version != requested:
            conflicts.append(
                ImportConflict(
                    path=item["path"],
                    action=(
                        f"component version conflict: existing {existing.component.version}, "
                        f"bundle requires {requested}"
                    ),
                )
            )
    return conflicts


def _bundled_subflow_components(
    archive: zipfile.ZipFile,
    manifest: BundleManifest,
    validated: _ValidatedBundleArchive,
    limits: ResourceLimits,
) -> dict[str, dict[str, str]]:
    components: dict[str, dict[str, str]] = {}
    for item in manifest.included_paths:
        if item.get("kind") != "subflow":
            continue
        archive_path = item.get("archivePath")
        if not isinstance(archive_path, str):
            continue
        try:
            data = tomllib.loads(_read_archive_text(archive, archive_path, validated, limits))
        except Exception:
            continue
        component = data.get("component")
        if not isinstance(component, dict):
            continue
        component_id = str(component.get("id") or "").strip()
        version = str(component.get("version") or "").strip()
        if component_id and version:
            components[component_id] = {
                "path": item["path"],
                "version": version,
            }
    return components


def _existing_subflow_component_versions(
    base: Path,
    component_id: str,
) -> list[tuple[Path, str]]:
    matches: list[tuple[Path, str]] = []
    paths: list[Path] = []
    candidate = base / f"{component_id}.toml"
    if candidate.exists():
        paths.append(candidate)
    paths.extend(path for path in sorted(base.rglob("*.toml")) if path != candidate)
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            workflow = AgenticWorkflow.from_file(path)
        except Exception:
            continue
        if workflow.component is None or workflow.component.id != component_id:
            continue
        matches.append((path, workflow.component.version))
    return matches


def _archived_asset_files(
    archive: _ValidatedBundleArchive,
    manifest: BundleManifest,
) -> list[tuple[dict[str, str], str | None]]:
    files: list[tuple[dict[str, str], str | None]] = []
    for item in manifest.included_paths:
        archive_path = _safe_relative_path(item["archivePath"])
        matched = False
        for name in archive.names:
            if name.endswith("/"):
                continue
            if name == archive_path:
                files.append((item, None))
                matched = True
                continue
            if name.startswith(f"{archive_path.rstrip('/')}/"):
                nested = PurePosixPath(name).relative_to(PurePosixPath(archive_path)).as_posix()
                files.append((item, _safe_relative_path(nested)))
                matched = True
        if not matched:
            raise BundleError(f"Bundle is missing archived asset {archive_path}")
    return files


def _workflow_data_for_import(
    archive: zipfile.ZipFile,
    plan: BundleImportPlan,
    validated: _ValidatedBundleArchive,
    limits: ResourceLimits,
) -> dict[str, Any]:
    raw = tomllib.loads(_read_archive_text(archive, WORKFLOW_PATH, validated, limits))
    raw.setdefault("workflow", {})["id"] = plan.workflow_id
    raw["workflow"]["name"] = plan.workflow_name
    if plan.path_rewrites:
        _rewrite_workflow_paths(raw, plan.path_rewrites)
        _rewrite_subflow_component_paths(
            raw,
            _rewritten_subflow_components(archive, plan, validated, limits),
            f"{plan.workflow_id}.toml",
        )
    return raw


def _rewritten_subflow_components(
    archive: zipfile.ZipFile,
    plan: BundleImportPlan,
    validated: _ValidatedBundleArchive,
    limits: ResourceLimits,
) -> dict[str, str]:
    rewritten_components: dict[str, str] = {}
    for item in plan.manifest.included_paths:
        original_path = item.get("path")
        if item.get("kind") != "subflow" or original_path not in plan.path_rewrites:
            continue
        archive_path = item.get("archivePath")
        if not isinstance(archive_path, str):
            continue
        try:
            component_data = tomllib.loads(
                _read_archive_text(archive, archive_path, validated, limits)
            )
        except Exception:
            continue
        component = component_data.get("component")
        workflow = component_data.get("workflow")
        component_id = ""
        if isinstance(component, dict):
            component_id = str(component.get("id") or "").strip()
        if not component_id and isinstance(workflow, dict):
            component_id = str(workflow.get("id") or "").strip()
        if not component_id:
            component_id = Path(str(original_path)).stem
        rewritten_components[component_id] = plan.path_rewrites[original_path]
    return rewritten_components


def _rewrite_imported_workflow_bytes(
    payload: bytes,
    rewritten_components: dict[str, str],
    destination_rel: str,
) -> bytes:
    try:
        data = tomllib.loads(payload.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return payload
    if not isinstance(data, dict):
        return payload
    _rewrite_subflow_component_paths(data, rewritten_components, destination_rel)
    return tomli_w.dumps(data).encode()


def _rewrite_subflow_component_paths(
    data: dict[str, Any],
    rewritten_components: dict[str, str],
    owner_path: str,
) -> None:
    if not rewritten_components:
        return
    for node in data.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "subflow":
            continue
        component_id = str(node.get("component_id") or "").strip()
        source_path = rewritten_components.get(component_id)
        if source_path:
            node["source_path"] = _relative_import_source_path(owner_path, source_path)


def _relative_import_source_path(owner_path: str, source_path: str) -> str:
    owner_dir = posixpath.dirname(owner_path) or "."
    return posixpath.relpath(source_path, owner_dir)


def _collect_bundle_paths(
    workflow: AgenticWorkflow,
    path_base: Path,
) -> tuple[list[BundlePath], list[ExternalRequirement]]:
    included: dict[str, BundlePath] = {}
    external: dict[str, ExternalRequirement] = {}
    visited_workflows: set[Path] = set()
    root_base = path_base.resolve()

    def add(
        path: Path | None,
        owner: str,
        kind: str,
        *,
        current_base: Path,
        copy_dir: bool = False,
    ) -> None:
        if path is None:
            return
        if path.is_absolute() or str(path).startswith("~"):
            external[str(path)] = ExternalRequirement(
                path=str(path),
                reason="absolute or user-relative path is machine-specific",
                owner=owner,
            )
            return
        rel = _safe_relative_path(str(path))
        source = (current_base / rel).resolve()
        if not source.exists():
            external[rel] = ExternalRequirement(
                path=rel,
                reason="referenced path was missing during export",
                owner=owner,
            )
            return
        if source.is_dir() and not copy_dir:
            external[rel] = ExternalRequirement(
                path=rel,
                reason="directory path is treated as an external requirement",
                owner=owner,
            )
            return
        try:
            workflow_rel = source.relative_to(root_base).as_posix()
        except ValueError:
            external[str(source)] = ExternalRequirement(
                path=str(source),
                reason="referenced path is outside the workflow directory",
                owner=owner,
            )
            return
        if workflow_rel in included and included[workflow_rel].kind == "subflow":
            # Another node can also use this file as a script or prompt. Keep
            # its workflow classification so export still strips credentials.
            return
        included[workflow_rel] = BundlePath(
            source=source,
            workflow_path=workflow_rel,
            archive_path=f"{ASSET_PREFIX}{workflow_rel}",
            kind=kind,
        )

    def add_vector_index_sidecar(index_path: Path, owner: str, *, current_base: Path) -> None:
        if index_path.is_absolute() or str(index_path).startswith("~"):
            return
        rel = _safe_relative_path(str(index_path))
        source = (current_base / rel).resolve()
        if not source.exists() or source.is_dir():
            return

        entries_path, entries_rel, explicit = _vector_index_entries_path(source, rel, current_base)
        if entries_path is None or entries_rel is None:
            return
        if entries_path.is_absolute() and not entries_path.is_relative_to(root_base):
            external[str(entries_path)] = ExternalRequirement(
                path=str(entries_path),
                reason="absolute or user-relative path is machine-specific",
                owner=f"{owner}.entries_file",
            )
            return
        if not entries_path.exists():
            if explicit:
                external[entries_rel] = ExternalRequirement(
                    path=entries_rel,
                    reason="referenced path was missing during export",
                    owner=f"{owner}.entries_file",
                )
            return
        try:
            workflow_rel = entries_path.resolve().relative_to(root_base).as_posix()
        except ValueError:
            external[str(entries_path)] = ExternalRequirement(
                path=str(entries_path),
                reason="referenced path is outside the workflow directory",
                owner=f"{owner}.entries_file",
            )
            return
        included[workflow_rel] = BundlePath(
            source=entries_path,
            workflow_path=workflow_rel,
            archive_path=f"{ASSET_PREFIX}{workflow_rel}",
            kind="index_entries",
        )

    def collect_workflow_paths(
        current_workflow: AgenticWorkflow,
        current_base: Path,
        owner_prefix: str,
    ) -> None:
        if current_workflow.config.watch is not None:
            add(
                current_workflow.config.watch.path,
                f"{owner_prefix}workflow.watch",
                "watch",
                current_base=current_base,
                copy_dir=False,
            )
        for agent_id, agent in current_workflow.agents.items():
            add(
                agent.prompt_path,
                f"{owner_prefix}agent:{agent_id}.prompt_path",
                "prompt",
                current_base=current_base,
            )
            add(
                agent.working_dir,
                f"{owner_prefix}agent:{agent_id}.working_dir",
                "working_dir",
                current_base=current_base,
                copy_dir=False,
            )
            for index, extra_path in enumerate(agent.extra_paths):
                add(
                    extra_path,
                    f"{owner_prefix}agent:{agent_id}.extra_paths[{index}]",
                    "extra_path",
                    current_base=current_base,
                    copy_dir=False,
                )
        for node in current_workflow.graph.nodes_in_order():
            op = node.operation
            if isinstance(op, SubflowOperation):
                component_path = _resolve_bundle_subflow_path(op, current_base)
                if component_path is not None:
                    owner = f"{owner_prefix}node:{node.node_id}.component"
                    resolved_component = component_path.resolve()
                    try:
                        rel_component = resolved_component.relative_to(root_base)
                    except ValueError:
                        external[str(component_path)] = ExternalRequirement(
                            path=str(component_path),
                            reason="subflow component is outside the workflow directory",
                            owner=owner,
                        )
                    else:
                        workflow_path = rel_component.as_posix()
                        included[workflow_path] = BundlePath(
                            source=component_path,
                            workflow_path=workflow_path,
                            archive_path=f"{ASSET_PREFIX}{workflow_path}",
                            kind="subflow",
                        )
                        if resolved_component not in visited_workflows:
                            visited_workflows.add(resolved_component)
                            try:
                                component_workflow = AgenticWorkflow.from_file(component_path)
                            except Exception:
                                component_workflow = None
                            if component_workflow is not None:
                                collect_workflow_paths(
                                    component_workflow,
                                    component_path.parent,
                                    f"{owner}.",
                                )
                else:
                    external[op.component_id] = ExternalRequirement(
                        path=op.component_id,
                        reason="referenced subflow component was missing during export",
                        owner=f"{owner_prefix}node:{node.node_id}.component",
                    )
            for field_name, kind, copy_dir in (
                ("prompt_path", "prompt", False),
                ("script_path", "script", False),
                ("template_path", "prompt_template", False),
                ("index_path", "index", False),
                ("source_path", "sample_asset", True),
                ("path", "sample_asset", False),
            ):
                if isinstance(op, SubflowOperation) and field_name == "source_path":
                    continue
                value = getattr(op, field_name, None)
                if isinstance(value, Path):
                    owner = f"{owner_prefix}node:{node.node_id}.{field_name}"
                    add(value, owner, kind, current_base=current_base, copy_dir=copy_dir)
                    if field_name == "index_path":
                        add_vector_index_sidecar(value, owner, current_base=current_base)
            source = getattr(op, "source", None)
            source_path = getattr(source, "path", None)
            if isinstance(source_path, Path):
                add(
                    source_path,
                    f"{owner_prefix}node:{node.node_id}.source.path",
                    "sample_asset",
                    current_base=current_base,
                    copy_dir=False,
                )

    collect_workflow_paths(workflow, path_base, "")
    return sorted(included.values(), key=lambda item: item.workflow_path), sorted(
        external.values(),
        key=lambda item: item.path,
    )


def _resolve_bundle_subflow_path(op: SubflowOperation, path_base: Path) -> Path | None:
    if op.source_path is not None:
        source = op.source_path.expanduser()
        candidate = source if source.is_absolute() else path_base / source
        return candidate if candidate.exists() else None
    component_id = op.component_id.strip()
    candidate = path_base / f"{component_id}.toml"
    if candidate.exists():
        try:
            workflow = AgenticWorkflow.from_file(candidate)
        except Exception:
            workflow = None
        if workflow is not None and (
            workflow.component is None or workflow.component.id == component_id
        ):
            return candidate
    for path in sorted(path_base.rglob("*.toml")):
        try:
            workflow = AgenticWorkflow.from_file(path)
        except Exception:
            continue
        if workflow.component is not None and workflow.component.id == component_id:
            return path
    return None


def _required_secrets(
    data: dict[str, Any],
    workflow: AgenticWorkflow,
    included: list[BundlePath],
) -> list[dict[str, str]]:
    sources: dict[str, set[str]] = {}

    def add(name: str, source: str) -> None:
        sources.setdefault(name, set()).add(source)

    for match in SECRET_TOKEN_PATTERN.finditer(json.dumps(data, sort_keys=True)):
        add(match.group(1) or match.group(2), "workflow.toml")
    for item in included:
        if item.kind == "subflow":
            for name in _required_env_secret_names(_read_workflow_toml(item.source)):
                add(name, item.workflow_path)
        for path in _bundle_asset_paths(item.source):
            try:
                with open_binary_input(path) as input_file:
                    text = input_file.read().decode("utf-8")
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                raise BundleError(f"Could not read bundle asset: {path}") from exc
            for match in SECRET_TOKEN_PATTERN.finditer(text):
                add(match.group(1) or match.group(2), item.workflow_path)
    for trigger in workflow.config.webhooks.values():
        if trigger.token_env:
            add(trigger.token_env, f"trigger:{trigger.id}.token_env")
    for name in _required_env_secret_names(data):
        add(name, "workflow.toml")
    return [
        {
            "name": name,
            "description": "Required by " + ", ".join(sorted(secret_sources)),
        }
        for name, secret_sources in sorted(sources.items())
    ]


def _provider_assumptions(workflow: AgenticWorkflow) -> list[dict[str, str]]:
    assumptions = []
    for agent in workflow.agents.values():
        item = {
            "agentId": agent.agent_id,
            "subscription": agent.subscription,
        }
        if agent.profile:
            item["profile"] = agent.profile
        if agent.model:
            item["model"] = agent.model
        assumptions.append(item)
    return assumptions


def _triggers(workflow: AgenticWorkflow) -> list[dict[str, str]]:
    triggers: list[dict[str, str]] = []
    if workflow.config.schedule is not None:
        triggers.append(
            {
                "type": "schedule",
                "cron": workflow.config.schedule.cron_expression,
                "timezone": workflow.config.schedule.timezone,
            }
        )
    if workflow.config.watch is not None:
        triggers.append(
            {
                "type": "watch",
                "path": str(workflow.config.watch.path),
                "glob": workflow.config.watch.glob,
                "mode": workflow.config.watch.mode,
            }
        )
    for trigger_id, trigger in sorted(workflow.config.webhooks.items()):
        item = {
            "type": "webhook",
            "id": trigger_id,
            "source": trigger.source,
            "enabled": str(trigger.enabled).lower(),
            "concurrencyPolicy": trigger.concurrency_policy,
            "tokenConfigured": str(trigger.has_authentication).lower(),
            "allowUnauthenticated": str(trigger.allow_unauthenticated).lower(),
            "risk": "high"
            if trigger.store_raw_payload
            or trigger.missing_authentication
            or trigger.requires_unauthenticated_warning
            else "normal",
        }
        risk_reasons = _webhook_risk_reasons(trigger)
        if risk_reasons:
            item["riskReasons"] = ",".join(risk_reasons)
        if trigger.token_env:
            item["tokenEnv"] = trigger.token_env
        if trigger.fanout_path:
            item["fanoutPath"] = trigger.fanout_path
        triggers.append(item)
    return triggers


def _import_risk_warnings(workflow: AgenticWorkflow) -> list[str]:
    warnings: list[str] = []
    for trigger_id, trigger in sorted(workflow.config.webhooks.items()):
        if trigger.missing_authentication:
            warnings.append(
                f"Enabled webhook trigger '{trigger_id}' has no authentication configured; "
                "importing it enabled will fail validation and runtime requests will be rejected."
            )
        if trigger.requires_unauthenticated_warning:
            warnings.append(
                f"Webhook trigger '{trigger_id}' explicitly allows unauthenticated requests; "
                "this is high risk and intended only for local testing."
            )
        if trigger.store_raw_payload:
            warnings.append(
                f"Webhook trigger '{trigger_id}' stores raw replay payloads; "
                "incoming secrets may be persisted."
            )
    return warnings


def _webhook_risk_reasons(trigger: WebhookTriggerConfig) -> list[str]:
    reasons: list[str] = []
    if trigger.missing_authentication:
        reasons.append("missing_authentication")
    if trigger.requires_unauthenticated_warning:
        reasons.append("unauthenticated_allowed")
    if trigger.store_raw_payload:
        reasons.append("raw_payload_retention")
    return reasons


def _vector_index_entries_path(
    index_path: Path,
    workflow_rel: str,
    path_base: Path,
) -> tuple[Path | None, str | None, bool]:
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (
            _default_vector_entries_path(index_path),
            _default_vector_entries_rel(workflow_rel),
            False,
        )
    if not isinstance(index, dict):
        return (
            _default_vector_entries_path(index_path),
            _default_vector_entries_rel(workflow_rel),
            False,
        )
    entries_file = index.get("entries_file")
    if isinstance(entries_file, str) and entries_file:
        entries_path = Path(entries_file)
        if entries_path.is_absolute() or str(entries_path).startswith("~"):
            return entries_path, str(entries_path), True
        resolved = (index_path.parent / entries_path).resolve()
        try:
            entries_rel = resolved.relative_to(path_base.resolve()).as_posix()
        except ValueError:
            return resolved, str(resolved), True
        return resolved, _safe_relative_path(entries_rel), True
    return (
        _default_vector_entries_path(index_path),
        _default_vector_entries_rel(workflow_rel),
        False,
    )


def _default_vector_entries_path(index_path: Path) -> Path:
    return index_path.with_name(f"{index_path.name}.entries.jsonl")


def _default_vector_entries_rel(workflow_rel: str) -> str:
    path = PurePosixPath(workflow_rel)
    filename = f"{path.name}.entries.jsonl"
    if path.parent == PurePosixPath("."):
        return _safe_relative_path(filename)
    return _safe_archive_join(path.parent.as_posix(), filename)


def _sanitized_workflow_data(data: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(data, default=str))
    webhooks = copied.get("workflow", {}).get("webhooks", {})
    if isinstance(webhooks, dict):
        for trigger in webhooks.values():
            if isinstance(trigger, dict) and trigger.get("token"):
                trigger.pop("token", None)
    _sanitize_http_request_nodes(copied)
    _sanitize_notification_nodes(copied)
    _sanitize_env_maps(copied)
    _sanitize_workflow_scope_secrets(copied)
    return cast(dict[str, Any], copied)


def _sanitize_workflow_scope_secrets(data: dict[str, Any]) -> None:
    def is_reference(value: object) -> bool:
        return isinstance(value, str) and (
            _is_secret_reference_only(value)
            or (value.strip().startswith("{{") and parse_exact_reference(value) is not None)
        )

    def sanitize_declarations(declarations: object, field: str) -> set[str]:
        names: set[str] = set()
        if not isinstance(declarations, dict):
            return names
        for name, declaration in declarations.items():
            if not isinstance(declaration, dict):
                continue
            if declaration.get("secret") is True or declaration.get("type") == "secret":
                names.add(name)
                if field in declaration and not is_reference(declaration[field]):
                    # Omit literals rather than introduce a mask that fails numeric,
                    # boolean, or constrained input validation after import.
                    declaration.pop(field)
        return names

    workflow = data.get("workflow", {})
    if isinstance(workflow, dict):
        names = sanitize_declarations(workflow.get("inputs"), "default")
        names |= sanitize_declarations(workflow.get("parameters"), "default")
        sanitize_declarations(workflow.get("variables"), "initial")
        triggers = [workflow.get("schedule"), workflow.get("watch")]
        webhooks = workflow.get("webhooks")
        if isinstance(webhooks, dict):
            triggers.extend(webhooks.values())
        for trigger in triggers:
            if not isinstance(trigger, dict):
                continue
            for field in ("inputs", "params", "input_bindings"):
                values = trigger.get(field)
                if isinstance(values, dict):
                    for name in names & values.keys():
                        if not is_reference(values[name]):
                            values.pop(name)
    component = data.get("component")
    if isinstance(component, dict):
        sanitize_declarations(component.get("inputs"), "default")


def _required_env_secret_names(data: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for env in _workflow_env_maps(data):
        for key, value in env.items():
            env_name = str(key)
            env_value = str(value)
            if _is_sensitive_env_name(env_name) and not _is_secret_reference_only(env_value):
                names.add(env_name)
    return names


def _sanitize_env_maps(data: dict[str, Any]) -> None:
    for env in _workflow_env_maps(data):
        for key, value in list(env.items()):
            env_name = str(key)
            env_value = str(value)
            if _is_sensitive_env_name(env_name) and not _is_secret_reference_only(env_value):
                env[env_name] = MASKED_SECRET_VALUE


def _workflow_env_maps(data: dict[str, Any]) -> list[dict[str, Any]]:
    env_maps: list[dict[str, Any]] = []
    agents = data.get("agents")
    if isinstance(agents, dict):
        for agent in agents.values():
            if isinstance(agent, dict) and isinstance(agent.get("env"), dict):
                env_maps.append(agent["env"])
    nodes = data.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("env"), dict):
                env_maps.append(node["env"])
    return env_maps


def _sanitize_http_request_nodes(data: dict[str, Any]) -> None:
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "http_request":
            continue
        configured = {str(field).lower() for field in node.get("secret_fields") or []}
        secret_values = _collect_http_secret_values(node, configured)
        node["url"] = _sanitize_http_url(
            str(node.get("url", "")),
            configured,
            secret_values,
        )
        for key in ("headers", "params", "json"):
            if key in node:
                node[key] = _sanitize_http_value(
                    node[key],
                    configured,
                    key,
                    secret_values,
                )
        body = node.get("body")
        if isinstance(body, str):
            node["body"] = _sanitize_http_body(body, configured, secret_values)


def _sanitize_notification_nodes(data: dict[str, Any]) -> None:
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return
    configured: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "notification":
            continue
        secret_values = _collect_notification_secret_values(node)
        webhook_url = node.get("webhook_url")
        if isinstance(webhook_url, str):
            node["webhook_url"] = (
                webhook_url if _is_secret_reference_only(webhook_url) else MASKED_SECRET_VALUE
            )
        for key in ("headers", "payload", "email_to"):
            if key in node:
                node[key] = _sanitize_http_value(
                    node[key],
                    configured,
                    key,
                    secret_values,
                )
        for key in ("email_from", "smtp_host", "smtp_username", "smtp_password"):
            value = node.get(key)
            if isinstance(value, str):
                node[key] = _sanitize_http_string(
                    value,
                    secret_values,
                    force=key in {"smtp_username", "smtp_password"}
                    or _is_sensitive_http_field(key, configured),
                )


def _collect_notification_secret_values(node: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    webhook_url = node.get("webhook_url")
    if isinstance(webhook_url, str):
        parsed = urllib.parse.urlsplit(webhook_url)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            if _is_sensitive_http_field(key, set()):
                values.update(_collect_plain_leaf_strings(value))
    for key in ("headers", "payload", "email_to"):
        values.update(_collect_configured_http_values(node.get(key), set(), key))
    for key in ("email_from", "smtp_host", "smtp_username", "smtp_password"):
        values.update(_collect_configured_http_values(node.get(key), set(), key))
    return {value for value in values if value and not _contains_secret_reference(value)}


def _collect_http_secret_values(node: dict[str, Any], configured: set[str]) -> set[str]:
    values: set[str] = set()
    url = node.get("url")
    if isinstance(url, str):
        if _is_sensitive_http_field("url", configured):
            values.update(_collect_plain_leaf_strings(url))
        parsed = urllib.parse.urlsplit(url)
        for credential in (parsed.username, parsed.password):
            if credential:
                values.update({credential, urllib.parse.unquote(credential)})
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            if _is_sensitive_http_field(key, configured):
                values.update(_collect_plain_leaf_strings(value))
    for key in ("headers", "params", "json"):
        values.update(_collect_configured_http_values(node.get(key), configured, key))
    body = node.get("body")
    if isinstance(body, str):
        values.update(_collect_http_body_secret_values(body, configured))
    return {value for value in values if value and not _contains_secret_reference(value)}


def _collect_configured_http_values(
    value: object,
    configured: set[str],
    path: str = "",
) -> set[str]:
    if isinstance(value, dict):
        values: set[str] = set()
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _is_sensitive_http_field(child_path, configured):
                values.update(_collect_plain_leaf_strings(item))
            else:
                values.update(_collect_configured_http_values(item, configured, child_path))
        return values
    if isinstance(value, list):
        values = set()
        for item in value:
            values.update(_collect_configured_http_values(item, configured, path))
        return values
    if path and _is_sensitive_http_field(path, configured):
        return _collect_plain_leaf_strings(value)
    return set()


def _collect_plain_leaf_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value and not _contains_secret_reference(value) else set()
    if isinstance(value, dict):
        values: set[str] = set()
        for item in value.values():
            values.update(_collect_plain_leaf_strings(item))
        return values
    if isinstance(value, list):
        values = set()
        for item in value:
            values.update(_collect_plain_leaf_strings(item))
        return values
    if value is None:
        return set()
    text = str(value)
    return {text} if text else set()


def _collect_http_body_secret_values(value: str, configured: set[str]) -> set[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        values: set[str] = set()
        for pattern in (
            r"(?:^|[&\s;,])(?P<key>[A-Za-z0-9_.-]+)\s*=\s*(?P<value>[^&\s;,]+)",
            r"(?:^|[\s{,])['\"]?(?P<key>[A-Za-z0-9_.-]+)['\"]?\s*:\s*"
            r"(?P<quote>['\"]?)(?P<value>[^,'\"}\s]+)(?P=quote)",
        ):
            for match in re.finditer(pattern, value):
                if _is_sensitive_http_field(match.group("key"), configured):
                    secret = match.group("value").strip("\"'")
                    if secret and not _contains_secret_reference(secret):
                        values.add(secret)
        return values
    return _collect_configured_http_values(parsed, configured)


def _sanitize_http_url(url: str, configured: set[str], secret_values: set[str]) -> str:
    if _is_sensitive_http_field("url", configured):
        return url if _is_secret_reference_only(url) else MASKED_SECRET_VALUE
    parsed = urllib.parse.urlsplit(url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    sanitized_pairs = [
        (
            key,
            _sanitize_http_string(
                value,
                secret_values,
                force=_is_sensitive_http_field(key, configured),
            ),
        )
        for key, value in query_pairs
    ]
    # URL userinfo is a credential even when no secret_fields were configured.
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    sanitized = urllib.parse.urlunsplit(
        parsed._replace(netloc=netloc, query=urllib.parse.urlencode(sanitized_pairs))
    )
    return _sanitize_http_string(sanitized, secret_values)


def _sanitize_http_value(
    value: object,
    configured: set[str],
    path: str = "",
    secret_values: set[str] | None = None,
) -> object:
    secret_values = secret_values or set()
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            sanitized[str(key)] = _sanitize_http_value(
                item,
                configured,
                child_path,
                secret_values,
            )
        return sanitized
    if isinstance(value, list):
        return [_sanitize_http_value(item, configured, path, secret_values) for item in value]
    if isinstance(value, str):
        return _sanitize_http_string(
            value,
            secret_values,
            force=bool(path) and _is_sensitive_http_field(path, configured),
        )
    if path and _is_sensitive_http_field(path, configured):
        return MASKED_SECRET_VALUE
    return value


def _sanitize_http_body(value: str, configured: set[str], secret_values: set[str]) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        sanitized = _sanitize_http_string(value, secret_values)

        def mask_key_value(match: re.Match[str]) -> str:
            if not _is_sensitive_http_field(match.group("key"), configured):
                return match.group(0)
            quote = match.groupdict().get("quote") or ""
            return f"{match.group('prefix')}{quote}{MASKED_SECRET_VALUE}{quote}"

        sanitized = re.sub(
            r"(?P<prefix>(?:^|[&\s;,])(?P<key>[A-Za-z0-9_.-]+)\s*=\s*)"
            r"(?P<value>[^&\s;,]+)",
            mask_key_value,
            sanitized,
        )
        return re.sub(
            r"(?P<prefix>(?:^|[\s{,])['\"]?(?P<key>[A-Za-z0-9_.-]+)['\"]?\s*:\s*)"
            r"(?P<quote>['\"]?)(?P<value>[^,'\"}\s]+)(?P=quote)",
            mask_key_value,
            sanitized,
        )
    return json.dumps(
        _sanitize_http_value(parsed, configured, secret_values=secret_values),
        default=str,
    )


def _sanitize_http_string(
    value: str,
    secret_values: set[str],
    *,
    force: bool = False,
) -> str:
    if force:
        return value if _is_secret_reference_only(value) else MASKED_SECRET_VALUE
    sanitized = value
    for secret_value in sorted(secret_values, key=len, reverse=True):
        sanitized = sanitized.replace(secret_value, MASKED_SECRET_VALUE)
    return sanitized


def _contains_secret_reference(value: str) -> bool:
    return bool(SECRET_TOKEN_PATTERN.search(value))


def _is_secret_reference_only(value: str) -> bool:
    return SECRET_TOKEN_PATTERN.fullmatch(value.strip()) is not None


def _is_sensitive_http_field(path: str, configured: set[str]) -> bool:
    normalized = path.lower()
    name = normalized.rsplit(".", maxsplit=1)[-1]
    return (
        normalized in configured
        or name in configured
        or name in SENSITIVE_FIELD_NAMES
        or any(token in name for token in ("token", "secret", "password"))
    )


def _is_sensitive_env_name(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in SENSITIVE_FIELD_NAMES
        or normalized.endswith("_key")
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or any(token in normalized for token in ("token", "secret", "password"))
    )


def _rewrite_workflow_paths(data: dict[str, Any], rewrites: dict[str, str]) -> None:
    path_fields = {
        "prompt_path",
        "script_path",
        "template_path",
        "index_path",
        "source_path",
        "path",
        "working_dir",
        "extra_paths",
    }

    def visit(value: Any, key: str | None = None) -> Any:
        if key in path_fields and isinstance(value, str):
            return rewrites.get(value, value)
        if key == "extra_paths" and isinstance(value, list):
            return [rewrites.get(str(item), item) for item in value]
        if isinstance(value, dict):
            return {
                item_key: visit(item_value, str(item_key)) for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    updated = visit(data)
    data.clear()
    data.update(updated)


def _resolve_workflow_with_path(
    workflow_ref: str | Path,
    data_dir: Path | None,
) -> tuple[AgenticWorkflow, Path]:
    path = Path(workflow_ref)
    if path.suffix == ".toml" or path.exists():
        if not path.exists():
            raise BundleError(f"Workflow file '{path}' not found")
        return AgenticWorkflow.from_file(path), path.resolve()
    base = _data_dir(data_dir)
    candidate = _safe_destination(base, f"{workflow_ref}.toml")
    if candidate.exists():
        return AgenticWorkflow.from_file(candidate), candidate
    for candidate in sorted(base.glob("*.toml")) if base.exists() else []:
        workflow = AgenticWorkflow.from_file(candidate)
        if workflow.config.id == str(workflow_ref):
            return workflow, candidate
    raise BundleError(f"Workflow '{workflow_ref}' not found in {base}")


def _read_workflow_toml(path: Path) -> dict[str, Any]:
    try:
        with open_binary_input(path) as fh:
            return tomllib.load(fh)
    except OSError as exc:
        raise BundleError(f"Could not read workflow source: {path}") from exc


def _available_workflow_id(workflow_id: str, base: Path) -> str:
    validate_workflow_id(workflow_id)
    if not _safe_destination(base, f"{workflow_id}.toml").exists():
        return workflow_id
    index = 2
    while True:
        candidate = f"{workflow_id}-{index}"
        validate_workflow_id(candidate)
        if not _safe_destination(base, f"{candidate}.toml").exists():
            return candidate
        index += 1


def _validate_archive_entries(
    archive: zipfile.ZipFile,
    limits: ResourceLimits,
) -> _ValidatedBundleArchive:
    infos = archive.infolist()
    entry_count = len(infos)
    if entry_count > limits.max_bundle_entries:
        raise BundleError(
            "Bundle archive entry count exceeded limit "
            f"{limits.max_bundle_entries} entries (got {entry_count} entries)"
        )

    names = [info.filename for info in infos]
    unique_names = set(names)
    if len(unique_names) != len(names):
        raise BundleError("Bundle contains duplicate archive paths")

    if MANIFEST_PATH not in names:
        raise BundleError("Bundle is missing manifest.json")
    if WORKFLOW_PATH not in names:
        raise BundleError("Bundle is missing workflow.toml")

    total_uncompressed = 0
    total_compressed = 0
    for info in infos:
        _safe_relative_path(info.filename)
        if info.flag_bits & (0x01 | 0x20 | 0x40):
            raise BundleError("Bundle contains an encrypted or patched archive entry")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            # BZIP2/LZMA decompress without bounded output inside ZipExtFile.
            raise BundleError("Bundle uses unsupported compression")
        if info.is_dir():
            if info.file_size or info.compress_size:
                raise BundleError("Bundle directory entry contains file data")
            continue

        total_uncompressed += info.file_size
        total_compressed += info.compress_size
        if info.file_size > limits.max_bundle_entry_bytes:
            raise BundleError(
                f"Bundle archive entry size exceeded limit {limits.max_bundle_entry_bytes} bytes"
            )
        if total_uncompressed > limits.max_bundle_total_uncompressed_bytes:
            raise BundleError(
                "Bundle total uncompressed size exceeded limit "
                f"{limits.max_bundle_total_uncompressed_bytes} bytes "
                f"(got {total_uncompressed} bytes)"
            )
        if total_compressed > limits.max_bundle_compressed_bytes:
            raise BundleError(
                "Bundle compressed size exceeded limit "
                f"{limits.max_bundle_compressed_bytes} bytes "
                f"(got {total_compressed} bytes)"
            )
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > limits.max_bundle_compression_ratio:
            raise BundleError(
                f"Bundle compression ratio exceeded limit {limits.max_bundle_compression_ratio:g}:1"
            )

    for metadata_path in (MANIFEST_PATH, WORKFLOW_PATH):
        metadata_size = archive.getinfo(metadata_path).file_size
        if metadata_size > limits.max_bundle_metadata_bytes:
            raise BundleError(
                f"Bundle {metadata_path} size exceeded metadata limit "
                f"{limits.max_bundle_metadata_bytes} bytes "
                f"(got {metadata_size} bytes)"
            )

    return _ValidatedBundleArchive(
        names=tuple(names),
        info_by_name={info.filename: info for info in infos},
    )


def _read_archive_text(
    archive: zipfile.ZipFile,
    path: str,
    validated: _ValidatedBundleArchive,
    limits: ResourceLimits,
) -> str:
    info = validated.info_by_name[path]
    if info.file_size > limits.max_bundle_metadata_bytes:
        raise BundleError(
            f"Bundle {path} size exceeded metadata limit "
            f"{limits.max_bundle_metadata_bytes} bytes "
            f"(got {info.file_size} bytes)"
        )
    # Bound decompression even when a member lies about its expanded size.
    with archive.open(path) as source:
        data = source.read(info.file_size + 1)
    if len(data) > limits.max_bundle_metadata_bytes:
        raise BundleError(
            f"Bundle {path} size exceeded metadata limit "
            f"{limits.max_bundle_metadata_bytes} bytes "
            f"(got {len(data)} bytes)"
        )
    return data.decode("utf-8")


def _safe_relative_path(path: str) -> str:
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or not str(pure) or any(part in {"", ".", ".."} for part in pure.parts):
        raise BundleError(f"Unsafe bundle path: {path}")
    return pure.as_posix()


def _safe_archive_join(base: str, relative: str) -> str:
    return _safe_relative_path(f"{base.rstrip('/')}/{relative}")


def _safe_destination(base: Path, relative: str, nested: str | None = None) -> Path:
    rel = _safe_relative_path(relative)
    if nested:
        rel = _safe_archive_join(rel, nested)
    destination = (base / rel).resolve()
    resolved_base = base.resolve()
    if destination != resolved_base and not destination.is_relative_to(resolved_base):
        raise BundleError(f"Unsafe destination path: {relative}")
    return destination


def _data_dir(data_dir: Path | None) -> Path:
    from gofer.utils.paths import get_data_dir

    return (data_dir or get_data_dir()).resolve()


def _gofer_version() -> str:
    try:
        return version("gofer-flow")
    except PackageNotFoundError:
        return "unknown"
