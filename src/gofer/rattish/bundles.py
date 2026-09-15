"""Portable bundles for registered Rattish workflow workspaces."""

from __future__ import annotations

import fnmatch
import json
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from gofer.core.resources import DEFAULT_RESOURCE_LIMITS, ResourceLimits
from gofer.rattish.workspaces import (
    WORKFLOW_ENTRYPOINT,
    WORKFLOW_IGNORE,
    RegisteredWorkflow,
    find_registered_workflow,
    install_registered_workflow,
)
from gofer.utils.atomic_output import atomic_binary_output
from gofer.utils.brand_compat import (
    LEGACY_BUNDLE_FORMATS,
    LEGACY_WORKFLOW_IGNORES,
)

BUNDLE_EXTENSION = ".raticode"
BUNDLE_MANIFEST = "raticode.bundle.json"
BUNDLE_VERSION = 1


class RattishBundleError(ValueError):
    """Raised when a Rattish workflow bundle cannot be exported or imported."""


@dataclass(frozen=True, slots=True)
class RattishBundlePreview:
    workflow_id: str
    workflow_name: str
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflowId": self.workflow_id,
            "workflowName": self.workflow_name,
            "files": list(self.files),
        }


def export_rattish_bundle(
    workflow_id: str,
    output_path: Path,
    *,
    registry_dir: Path,
) -> RattishBundlePreview:
    workflow = find_registered_workflow(workflow_id, registry_dir=registry_dir)
    output_path = output_path.expanduser().absolute()
    if output_path.suffix.lower() != BUNDLE_EXTENSION:
        output_path = output_path.with_name(f"{output_path.name}{BUNDLE_EXTENSION}")
    if output_path == workflow.workflow_root or workflow.workflow_root in output_path.parents:
        raise RattishBundleError("Export the bundle outside the workflow folder")

    ignore_path = workflow.workflow_root / WORKFLOW_IGNORE
    for ignore_name in LEGACY_WORKFLOW_IGNORES:
        if not ignore_path.exists():
            ignore_path = workflow.workflow_root / ignore_name
    patterns = _read_ignore_patterns(ignore_path)
    files: list[tuple[Path, str]] = []
    for path in sorted(workflow.workflow_root.rglob("*")):
        relative = path.relative_to(workflow.workflow_root).as_posix()
        if relative in LEGACY_WORKFLOW_IGNORES and relative != ignore_path.name:
            continue
        if _is_ignored(relative, path.is_dir(), patterns):
            continue
        if path.is_symlink():
            raise RattishBundleError(f"Workflow bundle cannot include symbolic link: {relative}")
        if path.is_file():
            files.append(
                (path, WORKFLOW_IGNORE if relative in LEGACY_WORKFLOW_IGNORES else relative)
            )
    included = {relative for _, relative in files}
    if WORKFLOW_ENTRYPOINT not in included:
        raise RattishBundleError(f"{WORKFLOW_IGNORE} excludes required {WORKFLOW_ENTRYPOINT}")
    if included.intersection({BUNDLE_MANIFEST, *LEGACY_BUNDLE_FORMATS}):
        raise RattishBundleError(f"Workflow contains reserved bundle file {BUNDLE_MANIFEST}")

    preview = RattishBundlePreview(workflow.workflow_id, workflow.name, tuple(sorted(included)))
    manifest = {
        "format": "raticode-workflow",
        "version": BUNDLE_VERSION,
        "workflowId": workflow.workflow_id,
        "workflowName": workflow.name,
        "files": list(preview.files),
    }
    try:
        with atomic_binary_output(output_path) as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(BUNDLE_MANIFEST, json.dumps(manifest, indent=2) + "\n")
                for source, relative in files:
                    archive.write(source, relative)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RattishBundleError(f"Could not export workflow bundle: {exc}") from exc
    return preview


def preview_rattish_bundle(
    bundle_path: Path,
    *,
    limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
) -> RattishBundlePreview:
    with _open_validated_bundle(bundle_path, limits) as archive:
        manifest = _read_manifest(archive, limits)
        manifest_name = _manifest_name(archive)
        names = tuple(sorted(name for name in archive.namelist() if name != manifest_name))
        declared = tuple(sorted(manifest["files"]))
        if names != declared:
            raise RattishBundleError("Bundle file list does not match its manifest")
        if WORKFLOW_ENTRYPOINT not in names and "workflow.rad" not in names:
            raise RattishBundleError(f"Bundle is missing required {WORKFLOW_ENTRYPOINT}")
        return RattishBundlePreview(manifest["workflowId"], manifest["workflowName"], names)


def import_rattish_bundle(
    bundle_path: Path,
    project_root: Path,
    *,
    registry_dir: Path,
    limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
) -> RegisteredWorkflow:
    preview = preview_rattish_bundle(bundle_path, limits=limits)
    staging_parent = Path(tempfile.mkdtemp(prefix="raticode-import-"))
    staged_root = staging_parent / "workflow"
    staged_root.mkdir()
    try:
        with _open_validated_bundle(bundle_path, limits) as archive:
            for name in preview.files:
                destination = staged_root / PurePosixPath(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
        return install_registered_workflow(
            project_root,
            staged_root,
            preview.workflow_name,
            preview.workflow_id,
            registry_dir=registry_dir,
        )
    except (OSError, zipfile.BadZipFile) as exc:
        raise RattishBundleError(f"Could not import workflow bundle: {exc}") from exc
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


def _read_ignore_patterns(path: Path) -> tuple[tuple[str, bool], ...]:
    if not path.is_file():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RattishBundleError(f"Could not read {path}: {exc}") from exc
    patterns: list[tuple[str, bool]] = []
    for raw in lines:
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        if pattern:
            patterns.append((pattern.replace("\\", "/"), negated))
    return tuple(patterns)


def _is_ignored(relative: str, is_directory: bool, patterns: tuple[tuple[str, bool], ...]) -> bool:
    ignored = False
    for pattern, negated in patterns:
        directory_pattern = pattern.endswith("/")
        normalized = pattern.lstrip("/").rstrip("/")
        if not normalized:
            continue
        path_parts = PurePosixPath(relative).parts
        pattern_parts = PurePosixPath(normalized).parts
        if "/" in normalized:
            path_candidates = [path_parts]
            if directory_pattern:
                path_candidates = [path_parts[:index] for index in range(1, len(path_parts) + 1)]
                if not is_directory:
                    path_candidates = path_candidates[:-1]
            matched = any(
                _match_path_parts(candidate, pattern_parts) for candidate in path_candidates
            )
        else:
            segment_candidates = (
                path_parts if is_directory or not directory_pattern else path_parts[:-1]
            )
            matched = any(fnmatch.fnmatchcase(part, normalized) for part in segment_candidates)
        if matched:
            ignored = not negated
    return ignored


def _match_path_parts(path: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        return _match_path_parts(path, pattern[1:]) or bool(
            path and _match_path_parts(path[1:], pattern)
        )
    return bool(
        path
        and fnmatch.fnmatchcase(path[0], pattern[0])
        and _match_path_parts(path[1:], pattern[1:])
    )


def _open_validated_bundle(
    bundle_path: Path,
    limits: ResourceLimits,
) -> zipfile.ZipFile:
    path = bundle_path.expanduser().resolve()
    try:
        if path.stat().st_size > limits.max_bundle_compressed_bytes:
            raise RattishBundleError("Workflow bundle exceeds the compressed size limit")
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RattishBundleError(f"Invalid .raticode bundle: {exc}") from exc
    infos = archive.infolist()
    try:
        if len(infos) > limits.max_bundle_entries:
            raise RattishBundleError("Workflow bundle contains too many files")
        names: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename
            safe = PurePosixPath(name)
            if (
                not name
                or name.endswith("/")
                or safe.is_absolute()
                or ".." in safe.parts
                or "\\" in name
                or name in names
            ):
                raise RattishBundleError(f"Unsafe workflow bundle path: {name}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise RattishBundleError(f"Workflow bundle contains symbolic link: {name}")
            if info.file_size > limits.max_bundle_entry_bytes:
                raise RattishBundleError(f"Workflow bundle file exceeds size limit: {name}")
            total += info.file_size
            if total > limits.max_bundle_total_uncompressed_bytes:
                raise RattishBundleError("Workflow bundle exceeds the expanded size limit")
            if (
                info.compress_size
                and info.file_size / info.compress_size > limits.max_bundle_compression_ratio
            ):
                raise RattishBundleError(f"Workflow bundle compression ratio is unsafe: {name}")
            names.add(name)
        return archive
    except Exception:
        archive.close()
        raise


def _manifest_name(archive: zipfile.ZipFile) -> str:
    names = set(archive.namelist()).intersection({BUNDLE_MANIFEST, *LEGACY_BUNDLE_FORMATS})
    if len(names) != 1:
        raise RattishBundleError("Workflow bundle must contain exactly one supported manifest")
    return names.pop()


def _read_manifest(archive: zipfile.ZipFile, limits: ResourceLimits) -> dict[str, Any]:
    try:
        manifest_name = _manifest_name(archive)
        info = archive.getinfo(manifest_name)
        if info.file_size > limits.max_bundle_metadata_bytes:
            raise RattishBundleError("Workflow bundle manifest exceeds the size limit")
        manifest = json.loads(archive.read(info).decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise RattishBundleError("Workflow bundle has no valid manifest") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != (LEGACY_BUNDLE_FORMATS.get(manifest_name, "raticode-workflow"))
        or manifest.get("version") != BUNDLE_VERSION
        or not isinstance(manifest.get("workflowId"), str)
        or not isinstance(manifest.get("workflowName"), str)
        or not isinstance(manifest.get("files"), list)
        or not all(isinstance(item, str) for item in manifest["files"])
    ):
        raise RattishBundleError("Workflow bundle manifest is invalid or unsupported")
    return manifest
