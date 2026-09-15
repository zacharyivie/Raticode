"""Disk-backed Rattish compilation and internal IR artifact caching."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from gofer.rattish.compiler import (
    RATTISH_COMPILER_VERSION,
    CompileContext,
    CompileResult,
    RattishCompiler,
    ReferencedWorkflow,
)
from gofer.rattish.contracts import canonical_json_bytes, json_fingerprint
from gofer.rattish.diagnostics import (
    RattishCompileError,
    RattishDiagnostic,
    SourcePosition,
    SourceSpan,
)
from gofer.rattish.migration import migrate_source
from gofer.rattish.parser import parse_rattish
from gofer.rattish.project_paths import project_path
from gofer.rattish.provider_contracts import ProviderContract, load_provider_contracts
from gofer.rattish.runtime import (
    InvalidRattishIrError,
    _workflow_interface_fingerprint,
    load_ir,
)
from gofer.rattish.storage import registered_source_root
from gofer.utils.brand_compat import LEGACY_WORKSPACE_DIRECTORIES
from gofer.utils.paths import get_data_dir

ARTIFACT_VERSION = 1


class RattishArtifactError(RuntimeError):
    """Raised when source or an internal artifact cannot be read or written."""


@dataclass(frozen=True, slots=True)
class CompiledArtifact:
    ir: dict[str, Any]
    diagnostics: tuple[dict[str, Any], ...]
    cache_hit: bool
    source_path: Path


def compile_rattish_file(
    source_path: Path,
    *,
    data_dir: Path | None = None,
    workflow_id: str | None = None,
    project_root: Path | None = None,
) -> CompiledArtifact:
    """Compile one source file or return its validated internal cached artifact."""
    resolved_data_dir = (data_dir or get_data_dir()).expanduser().resolve()
    return _compile_rattish_file(
        source_path.expanduser().resolve(),
        data_dir=resolved_data_dir,
        workflow_id=workflow_id,
        project_root=project_root,
        stack=(),
    )


def compile_rattish_source(
    source: str,
    source_path: Path,
    *,
    data_dir: Path | None = None,
    workflow_id: str | None = None,
    project_root: Path | None = None,
) -> CompileResult:
    """Compile an unsaved Rattish buffer without publishing an artifact."""
    resolved_source_path = source_path.expanduser().resolve()
    resolved_data_dir = (data_dir or get_data_dir()).expanduser().resolve()
    asset_root = rattish_asset_root()
    compiler = _compiler(asset_root)
    providers = _provider_contracts(asset_root)
    registered = _registered_source(resolved_source_path, resolved_data_dir)
    resolved_workflow_id = (
        workflow_id
        or (registered.workflow_id if registered is not None else None)
        or _validation_workflow_id(resolved_source_path)
    )
    resolved_project_root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else registered.project_root
        if registered is not None
        else resolved_source_path.parent
    )
    try:
        entrypoint = resolved_source_path.relative_to(resolved_project_root).as_posix()
    except ValueError:
        entrypoint = resolved_source_path.as_posix()
    referenced = _referenced_workflows(
        source,
        source_path=resolved_source_path,
        project_root=resolved_project_root,
        data_dir=resolved_data_dir,
        stack=(resolved_source_path,),
    )
    return compiler.compile(
        source,
        CompileContext(
            workflow_id=resolved_workflow_id,
            project_root=resolved_project_root,
            entrypoint=entrypoint,
            compiler_version=RATTISH_COMPILER_VERSION,
            provider_contracts=providers,
            referenced_workflows=referenced,
        ),
    )


def _compile_rattish_file(
    source_path: Path,
    *,
    data_dir: Path,
    workflow_id: str | None,
    project_root: Path | None,
    stack: tuple[Path, ...],
) -> CompiledArtifact:
    try:
        source_path = migrate_source(source_path)
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RattishArtifactError(f"Could not read Rattish source {source_path}: {exc}") from exc

    asset_root = rattish_asset_root()
    compiler = _compiler(asset_root)
    provider_contracts = _provider_contracts(asset_root)
    registered = _registered_source(source_path, data_dir)
    resolved_workflow_id = (
        workflow_id
        or (registered.workflow_id if registered is not None else None)
        or _validation_workflow_id(source_path)
    )
    resolved_project_root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else registered.project_root
        if registered is not None
        else source_path.parent
    )
    referenced = _referenced_workflows(
        source,
        source_path=source_path,
        project_root=resolved_project_root,
        data_dir=data_dir,
        stack=(*stack, source_path),
    )
    try:
        entrypoint = source_path.relative_to(resolved_project_root).as_posix()
    except ValueError:
        entrypoint = source_path.as_posix()
    context = CompileContext(
        workflow_id=resolved_workflow_id,
        project_root=resolved_project_root,
        entrypoint=entrypoint,
        compiler_version=RATTISH_COMPILER_VERSION,
        provider_contracts=provider_contracts,
        referenced_workflows=referenced,
    )
    cache_inputs = _cache_inputs(
        source_path,
        source,
        resolved_workflow_id,
        resolved_project_root,
        compiler,
        provider_contracts,
        referenced,
    )
    cache_path = _cache_path(data_dir, source_path, resolved_workflow_id)
    cached = _read_cached_artifact(
        cache_path,
        cache_inputs,
        asset_root,
        provider_contracts,
        referenced,
    )
    if cached is not None:
        return cached

    result = compiler.compile(source, context)
    diagnostics = tuple(item.to_json() for item in result.diagnostics)
    document = {
        "artifact_version": ARTIFACT_VERSION,
        "cache_inputs": cache_inputs,
        "source_path": str(source_path),
        "diagnostics": list(diagnostics),
        "ir": result.ir,
    }
    _write_json_atomic(cache_path, document)
    return CompiledArtifact(result.ir, diagnostics, False, source_path)


def _referenced_workflows(
    source: str,
    *,
    source_path: Path,
    project_root: Path,
    data_dir: Path,
    stack: tuple[Path, ...],
) -> dict[tuple[str, str], ReferencedWorkflow]:
    ast = parse_rattish(source, source_id=source_path.name)
    resolved: dict[tuple[str, str], ReferencedWorkflow] = {}
    for node in ast["nodes"]:
        fields = {
            entry["name"]["canonical"]: entry
            for entry in node["entries"]
            if entry["kind"] == "field"
        }
        type_field = fields.get("type")
        if type_field is None or type_field["value"].get("canonical") != "workflow":
            continue
        id_field = fields.get("workflow-id")
        path_field = fields.get("workflow-path")
        if (id_field is None) == (path_field is None):
            continue
        field = id_field or path_field
        assert field is not None
        value_ast = field["value"]
        value = value_ast.get("value")
        if not isinstance(value, str) and value_ast.get("kind") == "identifier_value":
            value = value_ast.get("canonical")
        if not isinstance(value, str):
            continue
        if id_field is not None:
            from gofer.rattish.workspaces import find_registered_workflow

            try:
                child_registration = find_registered_workflow(value, registry_dir=data_dir)
            except (ValueError, OSError) as exc:
                raise _workflow_compile_error(
                    "RATTISH_CHILD_WORKFLOW_UNRESOLVED",
                    str(exc),
                    source_path.name,
                    field["value"]["span"],
                    {"node": node["name"]["canonical"], "workflow_id": value},
                ) from exc
            child_path = child_registration.entrypoint.expanduser().resolve()
            child_project_root = child_registration.project_root
            child_id = child_registration.workflow_id
            source_kind: Literal["registry", "project_path"] = "registry"
            key = (source_kind, value.lower())
            locator = child_registration.workflow_id
        else:
            try:
                child_path = migrate_source(project_path(project_root, value).resolve())
            except (ValueError, OSError) as exc:
                raise _workflow_compile_error(
                    "RATTISH_WORKFLOW_PATH_INVALID",
                    str(exc),
                    source_path.name,
                    field["value"]["span"],
                    {"node": node["name"]["canonical"], "workflow_path": value},
                ) from exc
            if child_path.suffix.lower() not in {".rattish", ".rad"}:
                raise _workflow_compile_error(
                    "RATTISH_WORKFLOW_PATH_INVALID",
                    "Referenced workflow paths must name a .rattish source file.",
                    source_path.name,
                    field["value"]["span"],
                    {"node": node["name"]["canonical"], "workflow_path": value},
                )
            child_project_root = project_root
            child_id = _validation_workflow_id(child_path)
            source_kind = "project_path"
            key = (source_kind, value)
            locator = value
        if child_path in stack:
            chain = [str(item) for item in (*stack, child_path)]
            raise _workflow_compile_error(
                "RATTISH_WORKFLOW_RECURSION",
                "Recursive workflow reference detected: " + " -> ".join(chain),
                source_path.name,
                field["value"]["span"],
                {"node": node["name"]["canonical"], "chain": chain},
            )
        try:
            child = _compile_rattish_file(
                child_path,
                data_dir=data_dir,
                workflow_id=child_id,
                project_root=child_project_root,
                stack=stack,
            )
        except RattishArtifactError as exc:
            raise _workflow_compile_error(
                "RATTISH_CHILD_WORKFLOW_UNRESOLVED",
                str(exc),
                source_path.name,
                field["value"]["span"],
                {
                    "node": node["name"]["canonical"],
                    "source_kind": source_kind,
                    "source": locator,
                },
            ) from exc
        resolved[key] = ReferencedWorkflow(
            source_kind=source_kind,
            source=locator,
            source_path=child_path,
            ir=child.ir,
        )
    return resolved


# Installed assets are read-only during normal use. Include file identities so
# development edits and atomic replacements invalidate these bounded caches.
AssetIdentity = tuple[tuple[Path, int, int, int, int], ...]


def _asset_identity(paths: list[Path]) -> AssetIdentity:
    identities = []
    for path in paths:
        stat = path.stat()
        identities.append((path, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino))
    return tuple(identities)


def _compiler(asset_root: Path) -> RattishCompiler:
    paths = sorted((asset_root / "schemas").glob("*.json")) + sorted(
        (asset_root / "contracts").glob("*.json")
    )
    return _cached_compiler(asset_root, _asset_identity(paths))


@lru_cache(maxsize=8)
def _cached_compiler(asset_root: Path, identity: AssetIdentity) -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=asset_root / "schemas",
        contract_paths=sorted((asset_root / "contracts").glob("*.json")),
    )


def _provider_contracts(asset_root: Path) -> dict[str, ProviderContract]:
    paths = [
        asset_root / "schemas" / "provider-contract.schema.json",
        *sorted((asset_root / "providers").glob("*.json")),
    ]
    return _cached_provider_contracts(asset_root, _asset_identity(paths))


@lru_cache(maxsize=8)
def _cached_provider_contracts(
    asset_root: Path,
    identity: AssetIdentity,
) -> dict[str, ProviderContract]:
    return load_provider_contracts(
        asset_root / "schemas" / "provider-contract.schema.json",
        sorted((asset_root / "providers").glob("*.json")),
    )


@lru_cache(maxsize=8)
def _diagnostic_validator(identity: AssetIdentity) -> Draft202012Validator:
    schema = json.loads(identity[0][0].read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def rattish_asset_root() -> Path:
    packaged = Path(__file__).with_name("assets")
    if packaged.is_dir():
        return packaged
    repository = Path(__file__).parents[3] / "rattish"
    if repository.is_dir():
        return repository
    raise RattishArtifactError("Rattish schemas and node contracts are not installed.")


def rattish_docs_root() -> Path:
    """Return the installed Rattish specification directory."""
    asset_root = rattish_asset_root()
    packaged = asset_root / "docs"
    if packaged.is_dir():
        return packaged
    repository = asset_root / "spec"
    if repository.is_dir():
        return repository
    raise RattishArtifactError("Rattish language documentation is not installed.")


def rattish_assistant_skill_path() -> Path:
    """Return the workflow assistant skill bundled with this installation."""
    packaged = rattish_asset_root() / "assistant-skill" / "SKILL.md"
    if packaged.is_file():
        return packaged
    repository = Path(__file__).parents[3] / "skills" / "gofer-flow-workflow-builder" / "SKILL.md"
    if repository.is_file():
        return repository
    raise RattishArtifactError("Raticode workflow-builder skill is not installed.")


def _cache_inputs(
    source_path: Path,
    source: str,
    workflow_id: str,
    project_root: Path,
    compiler: RattishCompiler,
    provider_contracts: Mapping[str, ProviderContract],
    referenced_workflows: Mapping[tuple[str, str], ReferencedWorkflow],
) -> str:
    normalized_source = source.replace("\r\n", "\n").replace("\r", "\n")
    contracts = [
        {
            "node_type": contract.node_type,
            "version": contract.version,
            "fingerprint": contract.fingerprint,
        }
        for contract in sorted(compiler.contracts, key=lambda item: item.node_type)
    ]
    providers = [
        {
            "provider_id": contract.provider_id,
            "version": contract.version,
            "fingerprint": contract.fingerprint,
        }
        for contract in sorted(provider_contracts.values(), key=lambda item: item.provider_id)
    ]
    return json_fingerprint(
        {
            "compiler_version": RATTISH_COMPILER_VERSION,
            "contracts": contracts,
            "provider_contracts": providers,
            "project_root": str(project_root),
            "referenced_workflows": [
                {
                    "source_kind": item.source_kind,
                    "source": item.source,
                    "workflow_id": item.ir["workflow"]["id"],
                    "compilation_fingerprint": item.ir["source"]["compilation_fingerprint"],
                }
                for item in sorted(
                    referenced_workflows.values(),
                    key=lambda value: (value.source_kind, value.source),
                )
            ],
            "source_fingerprint": (
                "sha256:" + hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
            ),
            "workflow_id": workflow_id,
        }
    )


def _cache_path(data_dir: Path, source_path: Path, workflow_id: str) -> Path:
    source_key = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()
    legacy_path = data_dir / "radish" / "artifacts" / f"{source_key}.json"
    workflow_root = registered_source_root(source_path, data_dir)
    if (
        workflow_root is None
        and source_path.name == "workflow.rattish"
        and source_path.parent.name == workflow_id
        and source_path.parent.parent.name in {".raticode", *LEGACY_WORKSPACE_DIRECTORIES}
    ):
        workflow_root = source_path.parent
    if workflow_root is None:
        return legacy_path
    path = workflow_root / "compiled" / f"{source_key}.json"
    _move_legacy_file(legacy_path, path)
    return path


def _move_legacy_file(source: Path, destination: Path) -> None:
    """Move one old app-data artifact into its registered workflow workspace."""
    if destination.exists() or not source.is_file():
        return
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
    except OSError:
        return


def _read_cached_artifact(
    path: Path,
    cache_inputs: str,
    asset_root: Path,
    provider_contracts: Mapping[str, ProviderContract],
    referenced_workflows: Mapping[tuple[str, str], ReferencedWorkflow],
) -> CompiledArtifact | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(document, dict)
            or document.get("artifact_version") != ARTIFACT_VERSION
            or document.get("cache_inputs") != cache_inputs
            or not isinstance(document.get("diagnostics"), list)
            or not isinstance(document.get("ir"), dict)
        ):
            return None
        compiler = _compiler(asset_root)
        schema = compiler.ir_validator.schema
        contract_registry = compiler.contracts
        ir = load_ir(
            cast(dict[str, Any], document["ir"]),
            schema,
            {contract.node_type: contract.document for contract in contract_registry},
            provider_contracts,
            {
                key: {
                    "interface_fingerprint": _workflow_interface_fingerprint(workflow.ir),
                    "compilation_fingerprint": workflow.ir["source"]["compilation_fingerprint"],
                }
                for key, workflow in referenced_workflows.items()
            },
        )
        diagnostic_validator = _diagnostic_validator(
            _asset_identity([asset_root / "schemas" / "diagnostic.schema.json"])
        )
        diagnostics = tuple(
            cast(dict[str, Any], item) for item in document["diagnostics"] if isinstance(item, dict)
        )
        if len(diagnostics) != len(document["diagnostics"]):
            return None
        for diagnostic in diagnostics:
            diagnostic_validator.validate(diagnostic)
        source_path = Path(str(document.get("source_path", "")))
        if not source_path.is_absolute():
            return None
        return CompiledArtifact(ir, diagnostics, True, source_path)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        InvalidRattishIrError,
        ValidationError,
    ):
        return None


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(canonical_json_bytes(document) + b"\n")
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RattishArtifactError(f"Could not publish internal Rattish artifact: {exc}") from exc


def _validation_workflow_id(source_path: Path) -> str:
    candidate = (
        source_path.parent.name if source_path.name == "workflow.rattish" else source_path.stem
    )
    normalized = re.sub(r"[^a-z0-9]+", "-", candidate.lower()).strip("-")
    if not normalized:
        return "workflow"
    if not normalized[0].isalpha():
        return f"workflow-{normalized}"
    return normalized


def _registered_source(source_path: Path, data_dir: Path) -> Any | None:
    from gofer.rattish.workspaces import list_registered_workflows

    return next(
        (
            item
            for item in list_registered_workflows(registry_dir=data_dir)
            if item.entrypoint.expanduser().resolve() == source_path
        ),
        None,
    )


def _workflow_compile_error(
    code: str,
    message: str,
    file: str,
    span: Mapping[str, Any],
    details: dict[str, Any],
) -> RattishCompileError:
    source_span = SourceSpan(
        SourcePosition(**span["start"]),
        SourcePosition(**span["end"]),
    )
    return RattishCompileError(
        [
            RattishDiagnostic(
                code=code,
                severity="error",
                phase="semantic",
                message=message,
                file=file,
                span=source_span,
                details=details,
            )
        ]
    )
