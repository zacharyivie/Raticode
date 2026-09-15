"""Revisioned Rattish document analysis for Studio editors."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from gofer.rattish.artifacts import (
    RattishArtifactError,
    _compiler,
    compile_rattish_source,
    rattish_asset_root,
)
from gofer.rattish.diagnostics import (
    RattishDiagnostic,
    RattishError,
    SourcePosition,
    SourceSpan,
)
from gofer.rattish.parser import InvalidSourceRegion, parse_rattish_recovering
from gofer.rattish.preflight import run_preflight
from gofer.rattish.workspaces import (
    RattishWorkspaceError,
    RegisteredWorkflow,
    find_registered_workflow,
    read_workflow_metadata,
    update_registered_workflow_name,
    write_workflow_metadata,
    write_workflow_source,
)

MAX_EDITOR_SOURCE_BYTES = 4 * 1024 * 1024
_NODE_HEADER = re.compile(r"(?im)^\s*node\s+([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s*:")
_TYPE_FIELD = re.compile(r"(?im)^\s+type\s*:\s*([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s*$")
_IDENTIFIER_SOURCE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\Z")


class RattishEditorError(ValueError):
    """Raised when an editor document cannot be opened or persisted."""


class RattishRevisionConflict(RattishEditorError):
    """Raised when a save would overwrite a newer source or metadata revision."""

    def __init__(self, resource: str, expected: str, current: str) -> None:
        self.resource = resource
        self.expected = expected
        self.current = current
        super().__init__(
            f"{resource} changed after it was opened. Expected {expected}, found {current}."
        )

    def to_payload(self) -> dict[str, str]:
        return {
            "error": str(self),
            "code": "RATTISH_EDITOR_REVISION_CONFLICT",
            "resource": self.resource,
            "expectedRevision": self.expected,
            "currentRevision": self.current,
        }


class RattishEditorService:
    """Analyze unsaved buffers and persist registered workflow documents safely."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_valid: dict[tuple[str, str], str] = {}

    def open_document(self, workflow_id: str, *, data_dir: Path) -> dict[str, Any]:
        workflow = self._workflow(workflow_id, data_dir)
        source = self._read_source(workflow)
        return self._analyze(workflow, source, data_dir=data_dir, saved_source=source)

    def analyze_document(
        self,
        workflow_id: str,
        source: str,
        *,
        data_dir: Path,
    ) -> dict[str, Any]:
        workflow = self._workflow(workflow_id, data_dir)
        saved_source = self._read_source(workflow)
        return self._analyze(workflow, source, data_dir=data_dir, saved_source=saved_source)

    def save_document(
        self,
        workflow_id: str,
        source: str,
        expected_revision: str,
        *,
        data_dir: Path,
    ) -> dict[str, Any]:
        workflow = self._workflow(workflow_id, data_dir)
        self._check_source_size(source)
        with self._lock:
            current_source = self._read_source(workflow)
            current_revision = source_revision(current_source)
            if expected_revision != current_revision:
                raise RattishRevisionConflict("source", expected_revision, current_revision)
            try:
                write_workflow_source(workflow, source)
            except RattishWorkspaceError as exc:
                raise RattishEditorError(str(exc)) from exc
            result = self._analyze(
                workflow,
                source,
                data_dir=data_dir,
                saved_source=source,
            )
            workflow_name = result["workflow"].get("name")
            if result["compilation"]["state"] == "valid" and workflow_name:
                try:
                    update_registered_workflow_name(
                        workflow.workflow_id,
                        str(workflow_name),
                        registry_dir=data_dir,
                    )
                except RattishWorkspaceError as exc:
                    raise RattishEditorError(str(exc)) from exc
            return result

    def save_metadata(
        self,
        workflow_id: str,
        metadata: dict[str, Any],
        expected_revision: str,
        *,
        data_dir: Path,
    ) -> dict[str, Any]:
        workflow = self._workflow(workflow_id, data_dir)
        with self._lock:
            current = self._read_metadata(workflow)
            current_revision = metadata_revision(current)
            if expected_revision != current_revision:
                raise RattishRevisionConflict("metadata", expected_revision, current_revision)
            try:
                write_workflow_metadata(workflow, metadata)
            except RattishWorkspaceError as exc:
                raise RattishEditorError(str(exc)) from exc
            persisted = self._read_metadata(workflow)
        return {
            "workflowId": workflow.workflow_id,
            "metadata": persisted,
            "metadataRevision": metadata_revision(persisted),
        }

    def mutate_document(
        self,
        workflow_id: str,
        mutations: list[dict[str, Any]],
        expected_revision: str,
        *,
        data_dir: Path,
    ) -> dict[str, Any]:
        """Apply source-aware editor mutations and persist them as one revision."""
        workflow = self._workflow(workflow_id, data_dir)
        with self._lock:
            source = self._read_source(workflow)
            current_revision = source_revision(source)
            if expected_revision != current_revision:
                raise RattishRevisionConflict("source", expected_revision, current_revision)
            try:
                ast = parse_rattish_recovering(source, source_id=workflow.entrypoint.name).ast
            except RattishError as exc:
                raise RattishEditorError(
                    "The graph cannot edit source until the workflow declaration can be parsed."
                ) from exc
            if ast is None:
                raise RattishEditorError(
                    "The graph cannot edit source until the workflow declaration can be parsed."
                )
            updated = source
            applied: list[dict[str, Any]] = []
            for mutation in mutations:
                updated, mutation_edits = _apply_source_mutation(updated, ast, mutation)
                applied.extend(mutation_edits)
                recovered = parse_rattish_recovering(updated, source_id=workflow.entrypoint.name)
                if recovered.ast is None:
                    mutation_name = mutation.get("kind")
                    raise RattishEditorError(
                        f"Mutation {mutation_name!r} made the workflow declaration unreadable."
                    )
                ast = recovered.ast
            try:
                write_workflow_source(workflow, updated)
            except RattishWorkspaceError as exc:
                raise RattishEditorError(str(exc)) from exc
            result = self._analyze(
                workflow,
                updated,
                data_dir=data_dir,
                saved_source=updated,
            )
            workflow_name = result["workflow"].get("name")
            if result["compilation"]["state"] == "valid" and workflow_name:
                update_registered_workflow_name(
                    workflow.workflow_id,
                    str(workflow_name),
                    registry_dir=data_dir,
                )
            result["sourceEdits"] = applied
            return result

    def _analyze(
        self,
        workflow: RegisteredWorkflow,
        source: str,
        *,
        data_dir: Path,
        saved_source: str,
    ) -> dict[str, Any]:
        self._check_source_size(source)
        revision = source_revision(source)
        saved_revision = source_revision(saved_source)
        metadata = self._read_metadata(workflow)
        diagnostics: list[RattishDiagnostic] = []
        invalid_regions: tuple[InvalidSourceRegion, ...] = ()
        ast: dict[str, Any] | None = None
        ir: Mapping[str, Any] | None = None
        preflight_payload: dict[str, Any] | None = None
        compilation_state = "invalid"

        try:
            recovered = parse_rattish_recovering(source, source_id=workflow.entrypoint.name)
            ast = recovered.ast
            diagnostics.extend(recovered.diagnostics)
            invalid_regions = recovered.invalid_regions
        except RattishError as exc:
            diagnostics.extend(exc.diagnostics)

        if ast is not None and not diagnostics:
            try:
                compiled = compile_rattish_source(
                    source,
                    workflow.entrypoint,
                    data_dir=data_dir,
                    workflow_id=workflow.workflow_id,
                    project_root=workflow.project_root,
                )
                ir = compiled.ir
                diagnostics.extend(compiled.diagnostics)
                compilation_state = "valid"
                preflight = run_preflight(compiled.ir, data_dir=data_dir)
                preflight_payload = {
                    "ready": preflight.ready,
                    "diagnostics": [item.to_json() for item in preflight.diagnostics],
                }
            except RattishError as exc:
                diagnostics.extend(exc.diagnostics)
            except RattishArtifactError as exc:
                diagnostics.append(_editor_diagnostic(workflow.entrypoint, str(exc)))

        graph = _graph_projection(source, ast, invalid_regions, diagnostics, ir)
        key = (str(data_dir.expanduser().resolve()), workflow.workflow_id.lower())
        fingerprint: str | None = None
        if ir is not None:
            fingerprint = str(ir["source"]["compilation_fingerprint"])
            with self._lock:
                self._last_valid[key] = fingerprint
        with self._lock:
            last_valid = self._last_valid.get(key)
        node_contracts = [
            {
                "nodeType": contract.node_type,
                "version": contract.version,
                "fingerprint": contract.fingerprint,
                "configurationSchema": contract.document["configuration_schema"],
                "defaults": contract.document.get("defaults", {}),
            }
            for contract in sorted(
                _compiler(rattish_asset_root()).contracts,
                key=lambda item: item.node_type,
            )
        ]

        return {
            "workflowId": workflow.workflow_id,
            "sourcePath": str(workflow.entrypoint),
            "projectRoot": str(workflow.project_root),
            "source": source,
            "sourceRevision": revision,
            "savedRevision": saved_revision,
            "dirty": revision != saved_revision,
            "metadata": metadata,
            "metadataRevision": metadata_revision(metadata),
            "workflow": _workflow_projection(ast),
            "ast": ast,
            "invalidRegions": [
                {"span": region.span.to_json(), "source": region.source}
                for region in invalid_regions
            ],
            "diagnostics": [item.to_json() for item in diagnostics],
            "graph": graph,
            "nodeContracts": node_contracts,
            "compilation": {
                "state": compilation_state,
                "fingerprint": fingerprint,
                "lastValidFingerprint": last_valid,
                "irVersion": ir["ir_version"] if ir is not None else None,
            },
            "preflight": preflight_payload,
            "runnable": bool(
                ir is not None
                and ir["nodes"]
                and preflight_payload is not None
                and preflight_payload["ready"]
            ),
        }

    @staticmethod
    def _workflow(workflow_id: str, data_dir: Path) -> RegisteredWorkflow:
        try:
            return find_registered_workflow(workflow_id, registry_dir=data_dir)
        except RattishWorkspaceError as exc:
            raise RattishEditorError(str(exc)) from exc

    @staticmethod
    def _read_source(workflow: RegisteredWorkflow) -> str:
        try:
            return workflow.entrypoint.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RattishEditorError(
                f"Could not read Rattish source {workflow.entrypoint}: {exc}"
            ) from exc

    @staticmethod
    def _read_metadata(workflow: RegisteredWorkflow) -> dict[str, Any]:
        try:
            return read_workflow_metadata(workflow)
        except RattishWorkspaceError as exc:
            raise RattishEditorError(str(exc)) from exc

    @staticmethod
    def _check_source_size(source: str) -> None:
        size = len(source.encode("utf-8"))
        if size > MAX_EDITOR_SOURCE_BYTES:
            raise RattishEditorError(
                f"Rattish editor source exceeds {MAX_EDITOR_SOURCE_BYTES} bytes."
            )


def source_revision(source: str) -> str:
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def metadata_revision(metadata: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _apply_source_mutation(
    source: str,
    ast: Mapping[str, Any],
    mutation: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    kind = str(mutation.get("kind", ""))
    if kind in {"set_field", "remove_field"}:
        declaration = _mutation_declaration(ast, mutation.get("target"))
        field = _mutation_identifier(mutation.get("field"), "field")
        entries = declaration["entries"]
        existing = next(
            (
                entry
                for entry in entries
                if entry["kind"] == "field" and entry["name"]["canonical"] == field.lower()
            ),
            None,
        )
        if kind == "remove_field":
            if existing is None:
                return source, []
            edit = _line_removal_edit(source, existing["span"])
            return _apply_byte_edits(source, [edit]), [_edit_payload(edit)]
        rendered = _render_mutation_value(mutation)
        if existing is not None:
            edit = _span_edit(existing["value"]["span"], rendered)
        else:
            insert_at = declaration["span"]["end"]["offset"]
            prefix = "" if source[:insert_at].endswith(("\n", "\r")) else "\n"
            edit = (insert_at, insert_at, f"{prefix}  {field}: {rendered}")
        return _apply_byte_edits(source, [edit]), [_edit_payload(edit)]

    if kind == "set_needs":
        declaration = _mutation_node(ast, mutation.get("node"))
        node_ids = [
            _mutation_identifier(value, "needs node")
            for value in _mutation_string_list(mutation.get("nodes"), "nodes")
        ]
        existing = next(
            (entry for entry in declaration["entries"] if entry["kind"] == "needs"), None
        )
        if not node_ids:
            if existing is None:
                return source, []
            edit = _line_removal_edit(source, existing["span"])
        else:
            if existing is None:
                offset = declaration["span"]["end"]["offset"]
                replacement = _render_identifier_list("needs", node_ids)
                edit = (offset, offset, f"\n{replacement}")
            else:
                replacement = _render_identifier_list("needs", node_ids, indent="") + "\n"
                edit = _span_edit(existing["span"], replacement)
        return _apply_byte_edits(source, [edit]), [_edit_payload(edit)]

    if kind == "set_routes":
        declaration = _mutation_node(ast, mutation.get("node"))
        routes = mutation.get("routes")
        if not isinstance(routes, list):
            raise RattishEditorError("set_routes requires a routes list.")
        existing = next(
            (entry for entry in declaration["entries"] if entry["kind"] == "routes"), None
        )
        if not routes:
            if existing is None:
                return source, []
            edit = _line_removal_edit(source, existing["span"])
        else:
            if existing is None:
                offset = declaration["span"]["end"]["offset"]
                replacement = _render_routes(routes)
                edit = (offset, offset, f"\n{replacement}")
            else:
                replacement = _render_routes(routes, indent="") + "\n"
                edit = _span_edit(existing["span"], replacement)
        return _apply_byte_edits(source, [edit]), [_edit_payload(edit)]

    if kind == "add_node":
        node_id = _mutation_identifier(mutation.get("node"), "node")
        node_type = _mutation_identifier(mutation.get("node_type"), "node type")
        if any(node["name"]["canonical"] == node_id.lower() for node in ast["nodes"]):
            raise RattishEditorError(f"Node {node_id!r} already exists.")
        fields = mutation.get("fields", {})
        if not isinstance(fields, Mapping):
            raise RattishEditorError("add_node fields must be an object.")
        lines = [f"Node {node_id}:", f"  type: {node_type}"]
        for field, value in fields.items():
            field_name = _mutation_identifier(field, "field")
            lines.append(_render_field("  ", field_name, _render_json_value(value)))
        prefix = "\n" if source.endswith(("\n", "\r")) else "\n\n"
        replacement = prefix + "\n".join(lines) + "\n"
        offset = len(source.encode("utf-8"))
        edit = (offset, offset, replacement)
        return _apply_byte_edits(source, [edit]), [_edit_payload(edit)]

    if kind == "duplicate_node":
        declaration = _mutation_node(ast, mutation.get("node"))
        next_name = _mutation_identifier(mutation.get("name"), "node name")
        if any(node["name"]["canonical"] == next_name.lower() for node in ast["nodes"]):
            raise RattishEditorError(f"Node {next_name!r} already exists.")
        encoded = source.encode("utf-8")
        start = declaration["span"]["start"]["offset"]
        end = declaration["span"]["end"]["offset"]
        block = encoded[start:end]
        name_start = declaration["name"]["span"]["start"]["offset"] - start
        name_end = declaration["name"]["span"]["end"]["offset"] - start
        duplicated = (block[:name_start] + next_name.encode("utf-8") + block[name_end:]).decode(
            "utf-8"
        )
        prefix = "\n" if source.endswith(("\n", "\r")) else "\n\n"
        offset = len(encoded)
        edit = (offset, offset, prefix + duplicated)
        return _apply_byte_edits(source, [edit]), [_edit_payload(edit)]

    if kind == "delete_node":
        declaration = _mutation_node(ast, mutation.get("node"))
        deleted_id = declaration["name"]["canonical"]
        edits = [_declaration_removal_edit(source, declaration["span"])]
        for node in ast["nodes"]:
            if node is declaration:
                continue
            needs = next((entry for entry in node["entries"] if entry["kind"] == "needs"), None)
            if needs and any(item["canonical"] == deleted_id for item in needs["nodes"]):
                remaining = [
                    item["source"] for item in needs["nodes"] if item["canonical"] != deleted_id
                ]
                edits.append(
                    _span_edit(
                        needs["span"],
                        _render_identifier_list("needs", remaining, indent="") + "\n",
                    )
                    if remaining
                    else _line_removal_edit(source, needs["span"])
                )
            routes = next((entry for entry in node["entries"] if entry["kind"] == "routes"), None)
            if routes and any(
                route["target"]["canonical"] == deleted_id for route in routes["routes"]
            ):
                matching = [
                    route
                    for route in routes["routes"]
                    if route["target"]["canonical"] == deleted_id
                ]
                if len(matching) == len(routes["routes"]):
                    edits.append(_line_removal_edit(source, routes["span"]))
                else:
                    edits.extend(_line_removal_edit(source, route["span"]) for route in matching)
        return _apply_byte_edits(source, edits), [_edit_payload(edit) for edit in edits]

    if kind == "rename_node":
        declaration = _mutation_node(ast, mutation.get("node"))
        next_name = _mutation_identifier(mutation.get("name"), "node name")
        canonical = next_name.lower()
        if any(
            node is not declaration and node["name"]["canonical"] == canonical
            for node in ast["nodes"]
        ):
            raise RattishEditorError(f"Node {next_name!r} already exists.")
        previous = declaration["name"]["canonical"]
        edits = [_span_edit(declaration["name"]["span"], next_name)]
        for node in ast["nodes"]:
            for entry in node["entries"]:
                if entry["kind"] == "needs":
                    edits.extend(
                        _span_edit(item["span"], next_name)
                        for item in entry["nodes"]
                        if item["canonical"] == previous
                    )
                elif entry["kind"] == "routes":
                    edits.extend(
                        _span_edit(route["target"]["span"], next_name)
                        for route in entry["routes"]
                        if route["target"]["canonical"] == previous
                    )
                edits.extend(_reference_rename_edits(entry, previous, next_name))
        return _apply_byte_edits(source, edits), [_edit_payload(edit) for edit in edits]

    if kind == "change_node_type":
        declaration = _mutation_node(ast, mutation.get("node"))
        next_type = _mutation_identifier(mutation.get("node_type"), "node type")
        type_field = next(
            (
                entry
                for entry in declaration["entries"]
                if entry["kind"] == "field" and entry["name"]["canonical"] == "type"
            ),
            None,
        )
        if type_field is None:
            raise RattishEditorError("The selected node has no type field.")
        previous_type = _field_identifier(type_field)
        contract = _compiler(rattish_asset_root()).contracts.get(previous_type or "")
        removable = {
            name.replace("_", "-")
            for name in (
                contract.document["configuration_schema"].get("properties", {})
                if contract is not None
                else {}
            )
        }
        edits = [_span_edit(type_field["value"]["span"], next_type)]
        edits.extend(
            _line_removal_edit(source, entry["span"])
            for entry in declaration["entries"]
            if entry["kind"] == "field" and entry["name"]["canonical"] in removable
        )
        return _apply_byte_edits(source, edits), [_edit_payload(edit) for edit in edits]

    raise RattishEditorError(f"Unsupported Rattish source mutation {kind!r}.")


def _mutation_declaration(ast: Mapping[str, Any], target: Any) -> Mapping[str, Any]:
    if target == "workflow" or target == {"workflow": True}:
        return cast(Mapping[str, Any], ast["workflow"])
    if isinstance(target, Mapping) and isinstance(target.get("node"), str):
        return _mutation_node(ast, target["node"])
    raise RattishEditorError("Mutation target must be 'workflow' or a node target.")


def _mutation_node(ast: Mapping[str, Any], value: Any) -> Mapping[str, Any]:
    node_id = _mutation_identifier(value, "node").lower()
    for node in ast["nodes"]:
        if node["name"]["canonical"] == node_id:
            return cast(Mapping[str, Any], node)
    raise RattishEditorError(f"Node {value!r} does not exist.")


def _mutation_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_SOURCE.fullmatch(value) is None:
        raise RattishEditorError(f"{label.capitalize()} must be a Rattish identifier.")
    return value


def _mutation_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RattishEditorError(f"{label.capitalize()} must be a string list.")
    return value


def _render_mutation_value(mutation: Mapping[str, Any]) -> str:
    if "valueSource" in mutation:
        value_source = mutation["valueSource"]
        if not isinstance(value_source, str) or not value_source.strip():
            raise RattishEditorError("valueSource must be a non-empty string.")
        return value_source.strip()
    if "value" not in mutation:
        raise RattishEditorError("set_field requires value or valueSource.")
    return _render_json_value(mutation["value"])


def _render_json_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _render_field(indent: str, key: str, rendered: str) -> str:
    if "\n" not in rendered:
        return f"{indent}{key}: {rendered}"
    lines = rendered.splitlines()
    return f"{indent}{key}: |\n" + "\n".join(f"{indent}  {line}" for line in lines)


def _render_identifier_list(key: str, values: list[str], *, indent: str = "  ") -> str:
    if len(values) == 1:
        return f"{indent}{key}: {values[0]}"
    return f"{indent}{key}:\n" + "\n".join(f"{indent}  - {value}" for value in values)


def _render_routes(routes: list[Any], *, indent: str = "  ") -> str:
    if len(routes) == 1 and isinstance(routes[0], str):
        target = _mutation_identifier(routes[0], "route target")
        return f"{indent}to: {target}"
    lines = [f"{indent}to:"]
    for route in routes:
        if isinstance(route, str):
            lines.append(f"{indent}  - {_mutation_identifier(route, 'route target')}")
            continue
        if not isinstance(route, Mapping):
            raise RattishEditorError("Each route must be a target string or object.")
        target = _mutation_identifier(route.get("target"), "route target")
        mode = route.get("mode", "unconditional")
        if mode == "unconditional":
            lines.append(f"{indent}  - {target}")
        elif mode == "otherwise":
            lines.append(f"{indent}  - {target} otherwise")
        elif mode == "when" and isinstance(route.get("predicateSource"), str):
            lines.append(f"{indent}  - {target} when {route['predicateSource'].strip()}")
        else:
            raise RattishEditorError("Conditional routes require mode and predicateSource.")
    return "\n".join(lines)


def _reference_rename_edits(
    value: Any, previous: str, next_name: str
) -> list[tuple[int, int, str]]:
    edits: list[tuple[int, int, str]] = []
    if isinstance(value, Mapping):
        if value.get("kind") == "reference" and value.get("root", {}).get("canonical") == "node":
            selectors = value.get("selectors", [])
            if selectors and selectors[0].get("canonical") == previous:
                edits.append(_span_edit(selectors[0]["span"], next_name))
        for child in value.values():
            edits.extend(_reference_rename_edits(child, previous, next_name))
    elif isinstance(value, list):
        for child in value:
            edits.extend(_reference_rename_edits(child, previous, next_name))
    return edits


def _span_edit(span: Mapping[str, Any], replacement: str) -> tuple[int, int, str]:
    return span["start"]["offset"], span["end"]["offset"], replacement


def _line_removal_edit(source: str, span: Mapping[str, Any]) -> tuple[int, int, str]:
    encoded = source.encode("utf-8")
    start = span["start"]["offset"]
    end = span["end"]["offset"]
    while start > 0 and encoded[start - 1 : start] not in {b"\n", b"\r"}:
        start -= 1
    span_includes_line_end = end > start and encoded[end - 1 : end] in {b"\n", b"\r"}
    if not span_includes_line_end:
        while end < len(encoded) and encoded[end : end + 1] not in {b"\n", b"\r"}:
            end += 1
        if end < len(encoded) and encoded[end : end + 2] == b"\r\n":
            end += 2
        elif end < len(encoded):
            end += 1
    return start, end, ""


def _declaration_removal_edit(source: str, span: Mapping[str, Any]) -> tuple[int, int, str]:
    start = span["start"]["offset"]
    end = span["end"]["offset"]
    return start, end, ""


def _apply_byte_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    encoded = source.encode("utf-8")
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        encoded = encoded[:start] + replacement.encode("utf-8") + encoded[end:]
    return encoded.decode("utf-8")


def _edit_payload(edit: tuple[int, int, str]) -> dict[str, Any]:
    return {"startOffset": edit[0], "endOffset": edit[1], "text": edit[2]}


def _graph_projection(
    source: str,
    ast: Mapping[str, Any] | None,
    invalid_regions: tuple[InvalidSourceRegion, ...],
    diagnostics: list[RattishDiagnostic],
    ir: Mapping[str, Any] | None,
) -> dict[str, Any]:
    ir_nodes = {node["id"]: node for node in ir["nodes"]} if ir is not None else {}
    known_contracts = {
        contract.node_type: contract for contract in _compiler(rattish_asset_root()).contracts
    }
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_projection_ids: set[str] = set()
    valid_ids: set[str] = set()

    if ast is not None:
        for declaration_index, declaration in enumerate(ast["nodes"]):
            node_id = declaration["name"]["canonical"]
            projection_id = _unique_projection_id(node_id, seen_projection_ids)
            valid_ids.add(node_id)
            fields = {
                entry["name"]["canonical"]: entry
                for entry in declaration["entries"]
                if entry["kind"] == "field"
            }
            node_type = _field_identifier(fields.get("type"))
            contract = known_contracts.get(node_type or "")
            lowered = ir_nodes.get(node_id)
            node_diagnostics = _diagnostics_in_span(diagnostics, declaration["span"])
            needs = [
                needed["canonical"]
                for entry in declaration["entries"]
                if entry["kind"] == "needs"
                for needed in entry["nodes"]
            ]
            nodes.append(
                {
                    "projectionId": projection_id,
                    "id": node_id,
                    "label": declaration["name"]["source"],
                    "type": node_type,
                    "status": "error" if node_diagnostics else "valid",
                    "contractAvailable": contract is not None,
                    "contract": (
                        {
                            "version": contract.version,
                            "fingerprint": contract.fingerprint,
                            "configurationSchema": contract.document["configuration_schema"],
                            "defaults": contract.document.get("defaults", {}),
                        }
                        if contract is not None
                        else None
                    ),
                    "needs": needs,
                    "execution": lowered["execution"] if lowered is not None else None,
                    "configuration": (lowered["configuration"] if lowered is not None else None),
                    "bindings": (lowered["bindings"] if lowered is not None else []),
                    "authoredFields": {
                        name: _value_projection(field["value"]) for name, field in fields.items()
                    },
                    "sourceSpan": declaration["span"],
                    "diagnostics": [item.to_json() for item in node_diagnostics],
                    "declarationIndex": declaration_index,
                }
            )
            route_index = 0
            for entry in declaration["entries"]:
                if entry["kind"] != "routes":
                    continue
                for route in entry["routes"]:
                    target = route["target"]["canonical"]
                    edges.append(
                        {
                            "id": f"{projection_id}:route:{route_index}",
                            "from": node_id,
                            "to": target,
                            "mode": route["mode"],
                            "predicate": route.get("predicate"),
                            "predicateSource": (
                                _source_span_text(source, route["predicate"]["span"])
                                if route.get("predicate") is not None
                                else None
                            ),
                            "status": "valid",
                            "sourceSpan": route["span"],
                        }
                    )
                    route_index += 1

    invalid_sources = [*invalid_regions]
    if ast is None:
        invalid_sources = [
            InvalidSourceRegion(
                _whole_source_span(source),
                source,
            )
        ]
    for index, region in enumerate(invalid_sources):
        match = _NODE_HEADER.search(region.source)
        if match is None:
            continue
        node_id = match.group(1).lower()
        projection_id = _unique_projection_id(node_id, seen_projection_ids)
        type_match = _TYPE_FIELD.search(region.source)
        nodes.append(
            {
                "projectionId": projection_id,
                "id": node_id,
                "label": match.group(1),
                "type": type_match.group(1).lower() if type_match else None,
                "status": "invalid",
                "contractAvailable": False,
                "contract": None,
                "needs": [],
                "execution": None,
                "configuration": None,
                "sourceSpan": region.span.to_json(),
                "diagnostics": [
                    item.to_json()
                    for item in _diagnostics_in_span(diagnostics, region.span.to_json())
                ],
                "declarationIndex": len(nodes) + index,
            }
        )

    for edge in edges:
        if edge["to"] not in valid_ids:
            edge["status"] = "unresolved"
    return {"nodes": nodes, "edges": edges}


def _whole_source_span(source: str) -> SourceSpan:
    lines = source.splitlines()
    line = len(lines) if lines else 1
    column = len(lines[-1]) + 1 if lines else 1
    return SourceSpan(
        SourcePosition(0, 1, 1),
        SourcePosition(len(source.encode("utf-8")), line, column),
    )


def _workflow_projection(ast: Mapping[str, Any] | None) -> dict[str, Any]:
    if ast is None:
        return {"name": None, "sourceSpan": None}
    workflow = ast["workflow"]
    name = None
    for entry in workflow["entries"]:
        if entry["kind"] == "field" and entry["name"]["canonical"] == "name":
            value = entry["value"]
            name = value.get("value") or value.get("canonical") or value.get("source")
            break
    return {
        "name": name,
        "sourceSpan": workflow["span"],
        "fields": {
            entry["name"]["canonical"]: _value_projection(entry["value"])
            for entry in workflow["entries"]
            if entry["kind"] == "field"
        },
    }


def _value_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    projected: Any
    if value.get("kind") == "map":
        projected = {
            str(entry["key"].get("canonical") or entry["key"].get("value")): _value_projection(
                entry["value"]
            )["value"]
            for entry in value["entries"]
        }
    elif value.get("kind") == "list":
        projected = [_value_projection(item)["value"] for item in value["items"]]
    elif value.get("kind") == "duration":
        projected = value.get("source")
    elif value.get("kind") == "reference":
        root = value["root"]["source"]
        selectors = "".join(
            (
                f".{selector['source']}"
                if selector["kind"] == "member" and selector["notation"] == "dot"
                else f"[{selector['source']}]"
            )
            for selector in value["selectors"]
        )
        projected = root + selectors
    elif "value" in value:
        projected = value["value"]
    elif "canonical" in value:
        projected = value["canonical"]
    elif value.get("kind") in {"none", "null"}:
        projected = None
    else:
        projected = value.get("source")
    return {
        "kind": value.get("kind"),
        "style": value.get("style"),
        "source": value.get("source"),
        "value": projected,
        "span": value["span"],
    }


def _source_span_text(source: str, span: Mapping[str, Any]) -> str:
    encoded = source.encode("utf-8")
    return encoded[span["start"]["offset"] : span["end"]["offset"]].decode("utf-8")


def _field_identifier(field: Mapping[str, Any] | None) -> str | None:
    if field is None:
        return None
    value = field["value"]
    candidate = value.get("canonical") or value.get("value") or value.get("source")
    return str(candidate).lower() if candidate is not None else None


def _diagnostics_in_span(
    diagnostics: list[RattishDiagnostic], span: Mapping[str, Any]
) -> list[RattishDiagnostic]:
    start = int(span["start"]["offset"])
    end = int(span["end"]["offset"])
    return [
        item
        for item in diagnostics
        if item.span.start.offset < end and item.span.end.offset >= start
    ]


def _unique_projection_id(node_id: str, seen: set[str]) -> str:
    candidate = node_id
    suffix = 2
    while candidate in seen:
        candidate = f"{node_id}#{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _editor_diagnostic(path: Path, message: str) -> RattishDiagnostic:
    position = SourcePosition(0, 1, 1)
    return RattishDiagnostic(
        code="RATTISH_EDITOR_ANALYSIS_FAILED",
        severity="error",
        phase="semantic",
        message=message,
        file=str(path),
        span=SourceSpan(position, position),
    )


DEFAULT_RATTISH_EDITOR_SERVICE = RattishEditorService()
