"""Contract-backed Rattish compiler vertical slice."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]

from gofer.rattish.contracts import (
    LoadedNodeContract,
    NodeContractRegistry,
    canonical_json_bytes,
    json_fingerprint,
)
from gofer.rattish.diagnostics import (
    RattishCompileError,
    RattishDiagnostic,
    SourcePosition,
    SourceSpan,
)
from gofer.rattish.ir_validation import (
    InvalidRattishIrError,
    ValidatedRattishIR,
    validate_ir_invariants,
)
from gofer.rattish.lexer import DuplicateJsonKeyError, strict_json_loads
from gofer.rattish.parser import parse_rattish
from gofer.rattish.project_paths import normalize_project_path, project_path
from gofer.rattish.prompt_templates import (
    PromptPlaceholder,
    PromptTemplateError,
    parse_prompt_template,
    validate_prompt_template,
)
from gofer.rattish.provider_contracts import ProviderContract as ProviderContract
from gofer.rattish.schema_compat import (
    instance_matches_schema,
    schema_accepts_schema,
    unsupported_profile_paths,
)

IR_SCHEMA_ID = "urn:raticode:rattish:schema:ir-1.json"
JSON_SCHEMA_ID = "https://json-schema.org/draft/2020-12/schema"
RATTISH_COMPILER_VERSION = "0.2.1"

_COMMON_NODE_FIELDS = {
    "allow-fail",
    "finish",
    "max-concurrency",
    "max-runs",
    "retry-count",
    "retry-delay",
    "start",
    "timeout",
    "type",
}
_SENSITIVE_NAME = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|auth(?:orization)?|bearer|credential|key|pass(?:word)?|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_DURATION_MULTIPLIERS = {
    "ms": 1,
    "s": 1000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
}


@dataclass(frozen=True, slots=True)
class CompileContext:
    workflow_id: str
    project_root: Path
    entrypoint: str = "workflow.rattish"
    compiler_version: str = RATTISH_COMPILER_VERSION
    provider_contracts: Mapping[str, ProviderContract] = dataclass_field(default_factory=dict)
    referenced_workflows: Mapping[tuple[str, str], ReferencedWorkflow] = dataclass_field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class ReferencedWorkflow:
    """One recursively compiled child made available to semantic analysis."""

    source_kind: Literal["registry", "project_path"]
    source: str
    source_path: Path
    ir: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CompileResult:
    ast: dict[str, Any]
    ir: ValidatedRattishIR
    diagnostics: tuple[RattishDiagnostic, ...]


@dataclass(slots=True)
class _NodeDraft:
    node_id: str
    declaration: dict[str, Any]
    contract: LoadedNodeContract
    configuration: dict[str, Any]
    common: dict[str, Any]
    needs: list[str]
    bindings_ast: list[dict[str, Any]]
    routes_ast: list[dict[str, Any]]
    output_schema: dict[str, Any]
    dependencies: list[dict[str, Any]]
    provider_resolution: dict[str, Any] | None
    workflow_resolution: dict[str, Any] | None
    workflow_input_ports: Mapping[str, dict[str, Any]]
    workflow_inputs: Mapping[str, dict[str, Any]]
    activation_contexts: set[tuple[str, ...]] = dataclass_field(default_factory=set)
    guaranteed_sources: set[str] = dataclass_field(default_factory=set)


class RattishCompiler:
    """Compile Rattish 1 to schema-valid IR for implemented node contracts."""

    def __init__(
        self,
        *,
        contracts: NodeContractRegistry,
        ast_schema: Mapping[str, Any],
        ir_schema: Mapping[str, Any],
    ) -> None:
        self.contracts = contracts
        self.ast_validator = Draft202012Validator(ast_schema)
        self.ir_validator = Draft202012Validator(ir_schema)

    @classmethod
    def from_paths(
        cls,
        *,
        schema_root: Path,
        contract_paths: list[Path],
        contract_fingerprints: dict[str, str] | None = None,
    ) -> RattishCompiler:
        contracts = NodeContractRegistry.from_files(
            schema_root / "node-contract.schema.json",
            contract_paths,
            fingerprint_overrides=contract_fingerprints,
        )
        return cls(
            contracts=contracts,
            ast_schema=json.loads((schema_root / "ast.schema.json").read_text(encoding="utf-8")),
            ir_schema=json.loads((schema_root / "ir.schema.json").read_text(encoding="utf-8")),
        )

    def compile(self, source: str, context: CompileContext) -> CompileResult:
        ast = parse_rattish(source, source_id=context.entrypoint)
        try:
            self.ast_validator.validate(ast)
        except ValidationError as exc:
            raise RuntimeError(f"Parser produced an invalid AST: {exc.message}") from exc

        diagnostics: list[RattishDiagnostic] = []
        if ast["version_directive"]["value"] != 1:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_UNSUPPORTED_VERSION",
                    "semantic",
                    f"Rattish version {ast['version_directive']['value']} is not supported.",
                    ast["version_directive"]["span"],
                    context,
                )
            )

        workflow = self._analyze_workflow(ast["workflow"], context, diagnostics)
        workflow_inputs = workflow.pop("_input_schemas")
        workflow_outputs_field = workflow.pop("_outputs_field")
        drafts = self._analyze_nodes(ast["nodes"], workflow_inputs, context, diagnostics)
        workflow_input_optional = {
            item["name"]: not item["required"] and not item["default"]["present"]
            for item in workflow["inputs"]
        }
        workflow["outputs"] = self._workflow_outputs(
            workflow_outputs_field,
            drafts,
            workflow_inputs,
            workflow_input_optional,
            context,
            diagnostics,
        )
        self._analyze_graph(drafts, context, diagnostics)
        if any(item.severity == "error" for item in diagnostics):
            raise RattishCompileError(diagnostics)

        ir = self._lower(source, ast, workflow, drafts, context, diagnostics)
        try:
            self.ir_validator.validate(ir)
        except ValidationError as exc:
            diagnostic = self._diagnostic(
                "RATTISH_IR_INVALID",
                "lowering",
                f"Compiler produced invalid IR: {exc.message}",
                ast["span"],
                context,
                details={"path": list(exc.absolute_path)},
            )
            raise RattishCompileError([*diagnostics, diagnostic]) from exc
        try:
            validate_ir_invariants(ir)
        except InvalidRattishIrError as exc:
            diagnostic = self._diagnostic(
                "RATTISH_IR_INVALID",
                "lowering",
                f"Compiler produced invalid IR: {exc}",
                ast["span"],
                context,
            )
            raise RattishCompileError([*diagnostics, diagnostic]) from exc
        return CompileResult(
            ast=ast,
            ir=ValidatedRattishIR._from_validated(ir),
            diagnostics=tuple(diagnostics),
        )

    def _analyze_workflow(
        self,
        declaration: Mapping[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> dict[str, Any]:
        fields = self._field_map(declaration["entries"], context, diagnostics)
        supported = {"name", "interface-version", "inputs", "outputs", "max-runs", "timeout"}
        for name, field in fields.items():
            if name not in supported:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_UNKNOWN_FIELD",
                        "semantic",
                        f"Workflow field {name!r} is not implemented in this compiler slice.",
                        field["name"]["span"],
                        context,
                        details={"field": name},
                    )
                )
        name_field = fields.get("name")
        raw_name = self._value(name_field["value"]) if name_field else None
        if not isinstance(raw_name, str) or not raw_name.strip():
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_MISSING_WORKFLOW_NAME",
                    "semantic",
                    "Workflow requires a nonempty name.",
                    declaration["span"],
                    context,
                )
            )
            raw_name = ""
        inputs, input_schemas = self._workflow_inputs(fields.get("inputs"), context, diagnostics)
        return {
            "id": context.workflow_id.lower(),
            "name": raw_name,
            "interface_version": self._optional_positive_int(
                fields.get("interface-version"), "interface-version", context, diagnostics
            ),
            "max_runs": self._optional_positive_int(
                fields.get("max-runs"), "max-runs", context, diagnostics
            ),
            "timeout_ms": self._optional_duration(
                fields.get("timeout"), "timeout", context, diagnostics
            ),
            "inputs": inputs,
            "outputs": [],
            "_input_schemas": input_schemas,
            "_outputs_field": fields.get("outputs"),
        }

    def _analyze_nodes(
        self,
        declarations: list[dict[str, Any]],
        workflow_inputs: Mapping[str, dict[str, Any]],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> dict[str, _NodeDraft]:
        drafts: dict[str, _NodeDraft] = {}
        for declaration in declarations:
            node_id = declaration["name"]["canonical"]
            if node_id in drafts:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_DUPLICATE_IDENTIFIER",
                        "semantic",
                        f"Node {node_id!r} is declared more than once.",
                        declaration["name"]["span"],
                        context,
                        details={"node": node_id},
                    )
                )
                continue
            fields, needs, bindings, routes = self._node_parts(declaration, context, diagnostics)
            type_field = fields.get("type")
            node_type = (
                type_field["value"].get("canonical")
                if type_field and type_field["value"].get("kind") == "identifier_value"
                else None
            )
            if not isinstance(node_type, str):
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_MISSING_FIELD",
                        "semantic",
                        f"Node {node_id!r} requires type.",
                        declaration["span"],
                        context,
                        details={"node": node_id, "field": "type"},
                    )
                )
                continue
            assert type_field is not None
            contract = self.contracts.get(node_type)
            if contract is None:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_UNKNOWN_NODE_TYPE",
                        "semantic",
                        f"No node contract is available for {node_type!r}.",
                        type_field["value"]["span"],
                        context,
                        details={"node": node_id, "type": node_type},
                    )
                )
                continue
            unsupported_sources = {
                item["source"] for item in contract.document["computed_defaults"].values()
            } - {"provider_contract", "referenced_workflow"}
            if unsupported_sources:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_COMPILER_CAPABILITY_MISSING",
                        "semantic",
                        f"The current compiler slice cannot lower {node_type!r} yet.",
                        type_field["value"]["span"],
                        context,
                        details={"node": node_id, "type": node_type},
                    )
                )
                continue

            configuration = json.loads(json.dumps(contract.document["defaults"]))
            common: dict[str, Any] = {}
            for field_name, field in fields.items():
                if field_name == "type":
                    continue
                try:
                    if field_name in _COMMON_NODE_FIELDS:
                        common[field_name] = self._value(field["value"])
                    else:
                        machine_name = field_name.replace("-", "_")
                        configuration[machine_name] = self._configuration_value(
                            contract, machine_name, field["value"]
                        )
                except TypeError:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_FIELD_VALUE",
                            "semantic",
                            (
                                f"Node {node_id!r} field {field_name!r} requires a literal "
                                "value. Use 'with' to bind a reference."
                            ),
                            field["value"]["span"],
                            context,
                            details={"node": node_id, "field": field_name},
                        )
                    )

            self._materialize_configuration_defaults(
                configuration, contract.document["configuration_schema"]
            )
            self._apply_contract_specific_defaults(
                node_id, declaration, contract, configuration, context, diagnostics
            )

            workflow_resolution, workflow_ports, workflow_output, workflow_dependencies = (
                self._resolve_workflow_node(
                    node_id,
                    declaration,
                    contract,
                    configuration,
                    context,
                    diagnostics,
                )
            )
            provider_resolution = self._apply_computed_defaults(
                node_id, declaration, contract, configuration, context, diagnostics
            )
            self._validate_configuration(
                node_id,
                declaration,
                contract,
                configuration,
                {binding["name"]["canonical"] for binding in bindings},
                context,
                diagnostics,
            )
            self._run_static_diagnostics(
                declaration, node_id, contract, configuration, context, diagnostics
            )
            if workflow_output is None:
                output_schema, dependencies = self._resolved_output_schema(
                    node_id, declaration, contract, configuration, context, diagnostics
                )
            else:
                output_schema, dependencies = workflow_output, workflow_dependencies
            if provider_resolution is not None:
                dependencies.append(
                    {
                        "kind": "provider_contract",
                        "id": provider_resolution["provider_id"],
                        "version": provider_resolution["contract_version"],
                        "path": None,
                        "fingerprint": provider_resolution["contract_fingerprint"],
                    }
                )
            drafts[node_id] = _NodeDraft(
                node_id=node_id,
                declaration=declaration,
                contract=contract,
                configuration=configuration,
                common=common,
                needs=needs,
                bindings_ast=bindings,
                routes_ast=routes,
                output_schema=output_schema,
                dependencies=dependencies,
                provider_resolution=provider_resolution,
                workflow_resolution=workflow_resolution,
                workflow_input_ports=workflow_ports,
                workflow_inputs=workflow_inputs,
            )
        for draft in drafts.values():
            self._validate_configuration_templates(draft, context, diagnostics)
        return drafts

    def _validate_configuration_templates(
        self,
        draft: _NodeDraft,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        names = {binding["name"]["canonical"] for binding in draft.bindings_ast}
        if draft.contract.node_type == "prompt-file":
            variables = draft.configuration.get("variables", {})
            if isinstance(variables, Mapping):
                names.update(str(name).lower() for name in variables)
        for field_name, template in self._configuration_template_strings(draft.configuration):
            try:
                validate_prompt_template(template, names)
            except PromptTemplateError as exc:
                source_name = field_name.split(".", 1)[0].replace("_", "-")
                field = self._find_field(draft.declaration, source_name)
                code = (
                    "RATTISH_PROMPT_TEMPLATE_INVALID"
                    if (draft.contract.node_type == "agent" and field_name == "prompt")
                    or (draft.contract.node_type == "prompt-file" and field_name == "template")
                    else "RATTISH_TEMPLATE_INVALID"
                )
                diagnostics.append(
                    self._diagnostic(
                        code,
                        "semantic",
                        str(exc).replace("Prompt ", "Template "),
                        (
                            field["value"]["span"]
                            if field is not None
                            else draft.declaration["span"]
                        ),
                        context,
                        details={"node": draft.node_id, "field": field_name},
                    )
                )

    @classmethod
    def _configuration_template_strings(cls, value: Any, prefix: str = "") -> list[tuple[str, str]]:
        templates: list[tuple[str, str]] = []
        if isinstance(value, str) and ("{{" in value or "}}" in value):
            templates.append((prefix, value))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                templates.extend(cls._configuration_template_strings(item, f"{prefix}[{index}]"))
        elif isinstance(value, Mapping):
            for name, item in value.items():
                child = f"{prefix}.{name}" if prefix else str(name)
                templates.extend(cls._configuration_template_strings(item, child))
        return templates

    def _materialize_configuration_defaults(
        self, value: dict[str, Any], schema: Mapping[str, Any]
    ) -> None:
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return
        if schema.get("x-rattish-apply-property-defaults") is True:
            for name, child_schema in properties.items():
                if (
                    name not in value
                    and isinstance(child_schema, Mapping)
                    and "default" in child_schema
                ):
                    value[name] = json.loads(json.dumps(child_schema["default"]))
        variants = schema.get("x-rattish-variant-defaults")
        if isinstance(variants, Mapping):
            discriminator = schema.get("x-rattish-variant-discriminator")
            selected = value.get(discriminator) if isinstance(discriminator, str) else None
            selected_defaults = variants.get(selected)
            if isinstance(selected_defaults, Mapping):
                for name, default in selected_defaults.items():
                    value.setdefault(str(name), json.loads(json.dumps(default)))
        for name, child_schema in properties.items():
            child = value.get(name)
            if isinstance(child, dict) and isinstance(child_schema, Mapping):
                self._materialize_configuration_defaults(child, child_schema)

    def _apply_contract_specific_defaults(
        self,
        node_id: str,
        declaration: Mapping[str, Any],
        contract: LoadedNodeContract,
        configuration: dict[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        if contract.node_type != "loop":
            if contract.node_type == "local-vectorize":
                chunk_size = configuration.get("chunk_size")
                overlap = configuration.get("chunk_overlap")
                if (
                    isinstance(chunk_size, int)
                    and isinstance(overlap, int)
                    and overlap >= chunk_size
                ):
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_FIELD_VALUE",
                            "semantic",
                            "local-vectorize chunk-overlap must be smaller than chunk-size.",
                            declaration["span"],
                            context,
                            details={"node": node_id, "field": "chunk-overlap"},
                        )
                    )
            return
        source = configuration.get("source")
        if not isinstance(source, dict):
            return
        source_type = source.get("type")
        if not isinstance(source_type, str):
            return
        variants = {
            "count": {"type", "count", "max_concurrency", "fail_fast"},
            "tabular": {"type", "path", "max_concurrency", "fail_fast"},
            "directory": {
                "type",
                "path",
                "glob",
                "include_content",
                "max_concurrency",
                "fail_fast",
            },
            "trigger-events": {
                "type",
                "include_content",
                "max_concurrency",
                "fail_fast",
            },
            "infinite": {"type", "max_concurrency", "fail_fast"},
        }
        allowed = variants.get(source_type)
        if allowed is None:
            return
        unexpected = sorted(set(source) - allowed)
        if unexpected:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    f"Loop source {source_type!r} does not accept: {', '.join(unexpected)}.",
                    declaration["span"],
                    context,
                    details={"node": node_id, "fields": unexpected},
                )
            )
        if source_type in {"tabular", "directory"} and not isinstance(source.get("path"), str):
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_MISSING_FIELD",
                    "semantic",
                    f"Loop source {source_type!r} requires path.",
                    declaration["span"],
                    context,
                    details={"node": node_id, "field": "source.path"},
                )
            )
        authored_path = source.get("path")
        if isinstance(authored_path, str):
            try:
                project_path(context.project_root, authored_path)
            except ValueError as exc:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_PATH_OUTSIDE_PROJECT",
                        "semantic",
                        str(exc),
                        declaration["span"],
                        context,
                        details={"node": node_id, "field": "source.path"},
                    )
                )

    def _resolve_workflow_node(
        self,
        node_id: str,
        declaration: Mapping[str, Any],
        contract: LoadedNodeContract,
        configuration: dict[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, dict[str, Any]],
        dict[str, Any] | None,
        list[dict[str, Any]],
    ]:
        if contract.node_type != "workflow":
            return None, {}, None, []
        workflow_id = configuration.get("workflow_id")
        workflow_path = configuration.get("workflow_path")
        if isinstance(workflow_id, str) and not isinstance(workflow_path, str):
            key = ("registry", workflow_id.lower())
        elif isinstance(workflow_path, str) and not isinstance(workflow_id, str):
            key = ("project_path", workflow_path)
        else:
            return None, {}, {"$schema": JSON_SCHEMA_ID}, []
        referenced = context.referenced_workflows.get(key)
        if referenced is None:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_CHILD_WORKFLOW_UNRESOLVED",
                    "semantic",
                    f"Referenced workflow for node {node_id!r} could not be resolved.",
                    declaration["span"],
                    context,
                    details={"node": node_id, "source_kind": key[0], "source": key[1]},
                )
            )
            return None, {}, {"$schema": JSON_SCHEMA_ID}, []

        child = referenced.ir
        interface_version = child["workflow"]["interface_version"]
        if interface_version is None:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_WORKFLOW_INTERFACE_VERSION_REQUIRED",
                    "semantic",
                    f"Referenced workflow {child['workflow']['id']!r} has no interface-version.",
                    declaration["span"],
                    context,
                    details={"node": node_id, "workflow_id": child["workflow"]["id"]},
                )
            )
            return None, {}, {"$schema": JSON_SCHEMA_ID}, []
        requested_version = configuration.get("version")
        if requested_version is not None and requested_version != interface_version:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_CHILD_INTERFACE_VERSION_MISMATCH",
                    "semantic",
                    (
                        f"Workflow node {node_id!r} requests interface version "
                        f"{requested_version}, but the resolved workflow provides "
                        f"version {interface_version}."
                    ),
                    declaration["span"],
                    context,
                    details={
                        "node": node_id,
                        "requested": requested_version,
                        "resolved": interface_version,
                    },
                )
            )
        configuration["version"] = interface_version

        input_ports = {
            item["name"]: {
                "schema": item["schema"],
                "required": item["required"] and not item["default"]["present"],
            }
            for item in child["workflow"]["inputs"]
        }
        output_schema: dict[str, Any] = {
            "$schema": JSON_SCHEMA_ID,
            "type": "object",
            "properties": {item["name"]: item["schema"] for item in child["workflow"]["outputs"]},
            "additionalProperties": False,
        }
        output_names = [item["name"] for item in child["workflow"]["outputs"]]
        if output_names:
            output_schema["required"] = output_names
        input_schema: dict[str, Any] = {
            "$schema": JSON_SCHEMA_ID,
            "type": "object",
            "properties": {name: item["schema"] for name, item in input_ports.items()},
            "additionalProperties": False,
        }
        required_inputs = [name for name, item in input_ports.items() if item["required"]]
        if required_inputs:
            input_schema["required"] = required_inputs
        interface_document = {
            "workflow_id": child["workflow"]["id"],
            "interface_version": interface_version,
            "inputs": child["workflow"]["inputs"],
            "outputs": [
                {"name": item["name"], "schema": item["schema"]}
                for item in child["workflow"]["outputs"]
            ],
        }
        interface_fingerprint = json_fingerprint(interface_document)
        resolution = {
            "workflow_id": child["workflow"]["id"],
            "source_kind": referenced.source_kind,
            "source": referenced.source,
            "interface_version": interface_version,
            "interface_fingerprint": interface_fingerprint,
            "compilation_fingerprint": child["source"]["compilation_fingerprint"],
            "input_schema": input_schema,
            "output_schema": output_schema,
        }
        dependency_path = referenced.source if referenced.source_kind == "project_path" else None
        dependencies = [
            {
                "kind": "workflow",
                "id": child["workflow"]["id"],
                "version": child["compiler"]["version"],
                "path": dependency_path,
                "fingerprint": child["source"]["compilation_fingerprint"],
            },
            {
                "kind": "workflow_interface",
                "id": child["workflow"]["id"],
                "version": interface_version,
                "path": dependency_path,
                "fingerprint": interface_fingerprint,
            },
        ]
        return resolution, input_ports, output_schema, dependencies

    def _workflow_inputs(
        self,
        field: Mapping[str, Any] | None,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        if field is None:
            return [], {}
        value = field["value"]
        if value["kind"] != "map":
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    "Workflow inputs must be a map.",
                    value["span"],
                    context,
                )
            )
            return [], {}
        inputs: list[dict[str, Any]] = []
        schemas: dict[str, dict[str, Any]] = {}
        for input_entry in value["entries"]:
            key = input_entry["key"]
            if key["kind"] != "identifier":
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_FIELD_VALUE",
                        "semantic",
                        "Workflow input names must be Rattish identifiers.",
                        key["span"],
                        context,
                    )
                )
                continue
            name = key["canonical"]
            if name in schemas:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_DUPLICATE_IDENTIFIER",
                        "semantic",
                        f"Workflow input {name!r} is declared more than once.",
                        key["span"],
                        context,
                    )
                )
                continue
            definition = input_entry["value"]
            if definition["kind"] != "map":
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_FIELD_VALUE",
                        "semantic",
                        f"Workflow input {name!r} requires a definition map.",
                        definition["span"],
                        context,
                    )
                )
                continue
            parts = {
                self._map_key(item["key"]).replace("-", "_"): item for item in definition["entries"]
            }
            schema_entry = parts.get("schema")
            if schema_entry is None:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_MISSING_FIELD",
                        "semantic",
                        f"Workflow input {name!r} requires schema.",
                        definition["span"],
                        context,
                    )
                )
                continue
            schema_value = self._value(schema_entry["value"])
            if not isinstance(schema_value, dict):
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_SCHEMA_INVALID",
                        "semantic",
                        f"Workflow input {name!r} schema must be an object.",
                        schema_entry["value"]["span"],
                        context,
                    )
                )
                continue
            schema = dict(schema_value)
            schema.setdefault("$schema", JSON_SCHEMA_ID)
            self._validate_selected_schema(
                f"workflow-input-{name}", definition, schema, context, diagnostics
            )
            required_entry = parts.get("required")
            required = self._value(required_entry["value"]) if required_entry else False
            if not isinstance(required, bool):
                assert required_entry is not None
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_FIELD_VALUE",
                        "semantic",
                        f"Workflow input {name!r} required must be Boolean.",
                        required_entry["value"]["span"],
                        context,
                    )
                )
                required = False
            default_entry = parts.get("default")
            default = {"present": False}
            if default_entry is not None:
                default_value = self._value(default_entry["value"])
                if not instance_matches_schema(schema, default_value):
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_BINDING_TYPE_MISMATCH",
                            "semantic",
                            f"Default for workflow input {name!r} does not match its schema.",
                            default_entry["value"]["span"],
                            context,
                        )
                    )
                default = {"present": True, "value": default_value}
            schemas[name] = schema
            inputs.append(
                {"name": name, "required": required, "schema": schema, "default": default}
            )
        inputs.sort(key=lambda item: item["name"])
        return inputs, schemas

    def _workflow_outputs(
        self,
        field: Mapping[str, Any] | None,
        drafts: Mapping[str, _NodeDraft],
        workflow_inputs: Mapping[str, dict[str, Any]],
        workflow_input_optional: Mapping[str, bool],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> list[dict[str, Any]]:
        if field is None:
            return []
        value = field["value"]
        if value["kind"] != "map":
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    "Workflow outputs must be a map.",
                    value["span"],
                    context,
                )
            )
            return []

        outputs: list[dict[str, Any]] = []
        names: set[str] = set()
        for output_entry in value["entries"]:
            key = output_entry["key"]
            if key["kind"] != "identifier":
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_FIELD_VALUE",
                        "semantic",
                        "Workflow output names must be Rattish identifiers.",
                        key["span"],
                        context,
                    )
                )
                continue
            name = key["canonical"]
            if name in names:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_DUPLICATE_IDENTIFIER",
                        "semantic",
                        f"Workflow output {name!r} is declared more than once.",
                        key["span"],
                        context,
                        details={"output": name},
                    )
                )
                continue
            names.add(name)

            definition = output_entry["value"]
            if definition["kind"] != "map":
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_FIELD_VALUE",
                        "semantic",
                        f"Workflow output {name!r} requires a definition map.",
                        definition["span"],
                        context,
                    )
                )
                continue
            parts: dict[str, Mapping[str, Any]] = {}
            for item in definition["entries"]:
                part_name = self._map_key(item["key"]).replace("-", "_")
                if part_name in parts:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_DUPLICATE_FIELD",
                            "semantic",
                            f"Workflow output {name!r} repeats field {part_name!r}.",
                            item["key"]["span"],
                            context,
                            details={"output": name, "field": part_name},
                        )
                    )
                    continue
                parts[part_name] = item
                if part_name not in {"from", "schema"}:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNKNOWN_FIELD",
                            "semantic",
                            f"Workflow output {name!r} has unknown field {part_name!r}.",
                            item["key"]["span"],
                            context,
                            details={"output": name, "field": part_name},
                        )
                    )

            from_entry = parts.get("from")
            schema_entry = parts.get("schema")
            if from_entry is None:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_MISSING_FIELD",
                        "semantic",
                        f"Workflow output {name!r} requires from.",
                        definition["span"],
                        context,
                        details={"output": name, "field": "from"},
                    )
                )
            if schema_entry is None:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_MISSING_FIELD",
                        "semantic",
                        f"Workflow output {name!r} requires schema.",
                        definition["span"],
                        context,
                        details={"output": name, "field": "schema"},
                    )
                )
            if from_entry is None or schema_entry is None:
                continue

            reference_ast = from_entry["value"]
            if reference_ast["kind"] != "reference":
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_WRONG_REFERENCE_KIND",
                        "semantic",
                        f"Workflow output {name!r} from must be a reference.",
                        reference_ast["span"],
                        context,
                        details={"output": name},
                    )
                )
                continue
            schema_ast = schema_entry["value"]
            if schema_ast["kind"] != "json" or not isinstance(schema_ast["value"], dict):
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_SCHEMA_INVALID",
                        "semantic",
                        f"Workflow output {name!r} schema must be a JSON object.",
                        schema_ast["span"],
                        context,
                        details={"output": name},
                    )
                )
                continue
            schema = dict(schema_ast["value"])
            schema.setdefault("$schema", JSON_SCHEMA_ID)
            self._validate_selected_schema(
                f"workflow-output-{name}", definition, schema, context, diagnostics
            )
            reference, source_schema = self._lower_workflow_output_reference(
                name,
                reference_ast,
                drafts,
                workflow_inputs,
                workflow_input_optional,
                context,
                diagnostics,
            )
            if not schema_accepts_schema(schema, source_schema):
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_OUTPUT_TYPE_MISMATCH",
                        "semantic",
                        f"Workflow output {name!r} source is not assignable to its schema.",
                        reference_ast["span"],
                        context,
                        details={"output": name},
                    )
                )
            outputs.append({"name": name, "source": reference, "schema": schema})
        outputs.sort(key=lambda item: item["name"])
        return outputs

    def _lower_workflow_output_reference(
        self,
        output_name: str,
        reference: Mapping[str, Any],
        drafts: Mapping[str, _NodeDraft],
        workflow_inputs: Mapping[str, dict[str, Any]],
        workflow_input_optional: Mapping[str, bool],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        root = reference["root"]["canonical"]
        selectors = reference["selectors"]
        symbol: str | None = None
        channel: str | None = None
        path: list[dict[str, Any]] = []
        schema: dict[str, Any] = {"$schema": JSON_SCHEMA_ID}
        optional = False

        if root == "node":
            if len(selectors) < 2:
                diagnostics.append(
                    self._workflow_output_reference_diagnostic(
                        output_name,
                        "Node references require a node ID and output, status, or error channel.",
                        reference["span"],
                        context,
                    )
                )
            else:
                symbol = selectors[0]["canonical"]
                channel = selectors[1]["canonical"]
                producer = drafts.get(symbol)
                if producer is None:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNRESOLVED_REFERENCE",
                            "semantic",
                            f"Workflow output {output_name!r} names unknown node {symbol!r}.",
                            selectors[0]["span"],
                            context,
                            details={"output": output_name, "node": symbol},
                        )
                    )
                elif channel not in {"output", "status", "error"}:
                    diagnostics.append(
                        self._workflow_output_reference_diagnostic(
                            output_name,
                            f"Node reference channel {channel!r} is not output, status, or error.",
                            selectors[1]["span"],
                            context,
                        )
                    )
                else:
                    optional = True
                    if channel == "output":
                        schema = producer.output_schema
                    elif channel == "status":
                        schema = {
                            "$schema": JSON_SCHEMA_ID,
                            "enum": ["success", "failure", "cancelled"],
                        }
                    else:
                        schema = self._error_schema()
                    schema, path = self._select_workflow_output_schema(
                        output_name,
                        schema,
                        selectors[2:],
                        context,
                        diagnostics,
                    )
        elif root == "input":
            if not selectors:
                diagnostics.append(
                    self._workflow_output_reference_diagnostic(
                        output_name,
                        f"Reference root {root!r} requires an input name.",
                        reference["span"],
                        context,
                    )
                )
            else:
                symbol = selectors[0].get("canonical") or selectors[0].get("source")
                if symbol not in workflow_inputs:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNRESOLVED_REFERENCE",
                            "semantic",
                            f"Workflow output {output_name!r} names unknown input {symbol!r}.",
                            selectors[0]["span"],
                            context,
                            details={"output": output_name, "input": symbol},
                        )
                    )
                else:
                    schema = workflow_inputs[symbol]
                    optional = workflow_input_optional[symbol]
                    schema, path = self._select_workflow_output_schema(
                        output_name,
                        schema,
                        selectors[1:],
                        context,
                        diagnostics,
                    )
        else:
            diagnostics.append(
                self._workflow_output_reference_diagnostic(
                    output_name,
                    f"Reference root {root!r} cannot be exposed as a public workflow output.",
                    reference["span"],
                    context,
                )
            )

        lowered = {
            "root": root,
            "symbol": symbol,
            "channel": channel,
            "path": path,
            "optional": optional,
            "schema": schema,
        }
        return lowered, schema

    def _select_workflow_output_schema(
        self,
        output_name: str,
        schema: Mapping[str, Any],
        selectors: list[Mapping[str, Any]],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        selected = dict(schema)
        path: list[dict[str, Any]] = []
        for selector in selectors:
            if selector["kind"] == "member":
                member = selector["source"]
                properties = selected.get("properties")
                if isinstance(properties, Mapping) and isinstance(properties.get(member), Mapping):
                    selected = {"$schema": JSON_SCHEMA_ID, **properties[member]}
                    path.append({"kind": "member", "value": member, "case_sensitive": True})
                    continue
                if selected.get("additionalProperties") is False:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_JSON_SELECTOR",
                            "semantic",
                            f"Workflow output {output_name!r} selects unknown member {member!r}.",
                            selector["span"],
                            context,
                            details={"output": output_name, "member": member},
                        )
                    )
                else:
                    selected = {"$schema": JSON_SCHEMA_ID}
                path.append({"kind": "member", "value": member, "case_sensitive": True})
                continue

            items = selected.get("items")
            if selected.get("type") != "array" or not isinstance(items, Mapping):
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_JSON_SELECTOR",
                        "semantic",
                        f"Workflow output {output_name!r} indexes a non-array value.",
                        selector["span"],
                        context,
                        details={"output": output_name},
                    )
                )
                selected = {"$schema": JSON_SCHEMA_ID}
            else:
                selected = {"$schema": JSON_SCHEMA_ID, **items}
            path.append({"kind": "index", "value": selector["value"]})
        return selected, path

    def _workflow_output_reference_diagnostic(
        self,
        output_name: str,
        message: str,
        span: Mapping[str, Any],
        context: CompileContext,
    ) -> RattishDiagnostic:
        return self._diagnostic(
            "RATTISH_WRONG_REFERENCE_KIND",
            "semantic",
            f"Workflow output {output_name!r}: {message}",
            span,
            context,
            details={"output": output_name},
        )

    @staticmethod
    def _error_schema() -> dict[str, Any]:
        return {
            "$schema": JSON_SCHEMA_ID,
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "code": {"type": "string"},
                "message": {"type": "string"},
                "details": {},
            },
            "required": ["kind", "code", "message"],
            "additionalProperties": False,
        }

    def _node_parts(
        self,
        declaration: Mapping[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> tuple[dict[str, dict[str, Any]], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
        field_entries = [entry for entry in declaration["entries"] if entry["kind"] == "field"]
        fields = self._field_map(field_entries, context, diagnostics)
        needs_entries = [entry for entry in declaration["entries"] if entry["kind"] == "needs"]
        binding_entries = [entry for entry in declaration["entries"] if entry["kind"] == "bindings"]
        route_entries = [entry for entry in declaration["entries"] if entry["kind"] == "routes"]
        for entries, field_name in (
            (needs_entries, "needs"),
            (binding_entries, "with"),
            (route_entries, "to"),
        ):
            if len(entries) > 1:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_DUPLICATE_FIELD",
                        "semantic",
                        f"Node repeats {field_name!r}.",
                        entries[1]["span"],
                        context,
                        details={"field": field_name},
                    )
                )
        needs = [item["canonical"] for item in needs_entries[0]["nodes"]] if needs_entries else []
        bindings = binding_entries[0]["bindings"] if binding_entries else []
        routes = route_entries[0]["routes"] if route_entries else []
        return fields, needs, bindings, routes

    def _field_map(
        self,
        entries: list[dict[str, Any]],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> dict[str, dict[str, Any]]:
        fields: dict[str, dict[str, Any]] = {}
        for field in entries:
            name = field["name"]["canonical"]
            if name in fields:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_DUPLICATE_FIELD",
                        "semantic",
                        f"Field {name!r} appears more than once.",
                        field["name"]["span"],
                        context,
                        details={"field": name},
                    )
                )
            else:
                fields[name] = field
        return fields

    def _validate_configuration(
        self,
        node_id: str,
        declaration: Mapping[str, Any],
        contract: LoadedNodeContract,
        configuration: Mapping[str, Any],
        binding_names: set[str],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        validator = Draft202012Validator(contract.document["configuration_schema"])
        for error in validator.iter_errors(configuration):
            code = "RATTISH_INVALID_FIELD_VALUE"
            if error.validator == "type" and isinstance(error.instance, str):
                placeholder = self._exact_template_placeholder(error.instance)
                if placeholder is not None and placeholder.name.lower() in binding_names:
                    continue
            if error.validator == "required":
                required = {
                    str(name) for name in error.validator_value if str(name) not in error.instance
                }
                bound_machine_names = {name.replace("-", "_") for name in binding_names}
                if required and required <= bound_machine_names:
                    continue
                code = "RATTISH_MISSING_FIELD"
            elif error.validator == "additionalProperties":
                code = "RATTISH_UNKNOWN_FIELD"
            elif error.validator == "not":
                code = "RATTISH_MUTUALLY_EXCLUSIVE_FIELDS"
            diagnostics.append(
                self._diagnostic(
                    code,
                    "semantic",
                    f"Node {node_id!r} configuration is invalid: {error.message}",
                    declaration["span"],
                    context,
                    details={"node": node_id, "path": list(error.absolute_path)},
                )
            )
        self._normalize_configuration_paths(
            configuration,
            contract.document["configuration_schema"],
            node_id,
            declaration,
            context,
            diagnostics,
        )

    @staticmethod
    def _exact_template_placeholder(template: str) -> PromptPlaceholder | None:
        try:
            placeholders = parse_prompt_template(template)
        except PromptTemplateError:
            return None
        if len(placeholders) != 1:
            return None
        match = re.fullmatch(r"\s*\{\{\s*(.*?)\s*\}\}\s*", template, re.DOTALL)
        if match is None or match.group(1).strip() != placeholders[0].source:
            return None
        return placeholders[0]

    def _normalize_configuration_paths(
        self,
        configuration: Mapping[str, Any],
        schema: Mapping[str, Any],
        node_id: str,
        declaration: Mapping[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
        prefix: str = "",
    ) -> None:
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return
        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, Mapping) or field_name not in configuration:
                continue
            value = configuration[field_name]
            path_name = f"{prefix}.{field_name}" if prefix else field_name
            if field_schema.get("x-rattish-type") == "Path" and isinstance(value, str):
                try:
                    normalized = normalize_project_path(value)
                    project_path(context.project_root, normalized)
                    if isinstance(configuration, dict):
                        configuration[field_name] = normalized
                except ValueError as exc:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_PATH_OUTSIDE_PROJECT",
                            "semantic",
                            str(exc),
                            declaration["span"],
                            context,
                            details={"node": node_id, "field": path_name, "path": value},
                        )
                    )
            elif isinstance(value, Mapping):
                self._normalize_configuration_paths(
                    value,
                    field_schema,
                    node_id,
                    declaration,
                    context,
                    diagnostics,
                    path_name,
                )

    def _analyze_graph(
        self,
        drafts: Mapping[str, _NodeDraft],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        for draft in drafts.values():
            start_declared = draft.common.get("start") is True
            finish = draft.common.get("finish")
            allow_fail = draft.common.get("allow-fail") is True
            if start_declared and draft.needs:
                field = self._find_field(draft.declaration, "start")
                assert field is not None
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_START_ASSERTION",
                        "semantic",
                        f"Node {draft.node_id!r} cannot declare start: true with needs.",
                        field["value"]["span"],
                        context,
                        details={"node": draft.node_id},
                    )
                )
            if finish in {"pass", "fail"} and draft.routes_ast:
                field = self._find_field(draft.declaration, "finish")
                assert field is not None
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_TERMINAL",
                        "semantic",
                        f"Terminal node {draft.node_id!r} cannot declare to.",
                        field["value"]["span"],
                        context,
                        details={"node": draft.node_id, "finish": finish},
                    )
                )
            if finish == "fail" and allow_fail:
                field = self._find_field(draft.declaration, "allow-fail")
                assert field is not None
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INVALID_TERMINAL",
                        "semantic",
                        f"Node {draft.node_id!r} cannot combine finish: fail "
                        "with allow-fail: true.",
                        field["value"]["span"],
                        context,
                        details={"node": draft.node_id, "finish": "fail"},
                    )
                )

            if draft.contract.node_type == "break":
                if draft.routes_ast:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_BREAK_ROUTE",
                            "semantic",
                            f"Break node {draft.node_id!r} cannot declare to.",
                            draft.declaration["span"],
                            context,
                            details={"node": draft.node_id},
                        )
                    )
                loop_id = draft.configuration.get("loop")
                target_draft = drafts.get(loop_id) if isinstance(loop_id, str) else None
                if target_draft is None or target_draft.contract.node_type != "loop":
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_BREAK_TARGET",
                            "semantic",
                            f"Break node {draft.node_id!r} must name a loop node.",
                            draft.declaration["span"],
                            context,
                            details={"node": draft.node_id, "target": loop_id},
                        )
                    )

            seen_needs: set[str] = set()
            for needed in draft.needs:
                if needed in seen_needs:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_DUPLICATE_REQUIREMENT",
                            "semantic",
                            f"Node {draft.node_id!r} repeats requirement {needed!r}.",
                            draft.declaration["span"],
                            context,
                        )
                    )
                seen_needs.add(needed)
                if needed not in drafts:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNRESOLVED_NODE",
                            "semantic",
                            f"Requirement {needed!r} does not name a Node.",
                            draft.declaration["span"],
                            context,
                            details={"node": draft.node_id, "target": needed},
                        )
                    )
            seen_routes: set[str] = set()
            route_modes: dict[str, set[str]] = {}
            for route in draft.routes_ast:
                target = route["target"]["canonical"]
                if target not in drafts:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNRESOLVED_NODE",
                            "semantic",
                            f"Route target {target!r} does not name a Node.",
                            route["target"]["span"],
                            context,
                            details={"source_node": draft.node_id, "target": target},
                        )
                    )
                signature = json.dumps(
                    self._semantic_value(route), sort_keys=True, separators=(",", ":")
                )
                if signature in seen_routes:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_DUPLICATE_ROUTE",
                            "semantic",
                            f"Node {draft.node_id!r} repeats the same route to {target!r}.",
                            route["span"],
                            context,
                            details={"source_node": draft.node_id, "target": target},
                        )
                    )
                seen_routes.add(signature)
                route_modes.setdefault(target, set()).add(route["mode"])
                if (
                    route["mode"] == "when"
                    and self._predicate_can_match_failure(route["predicate"])
                    and not allow_fail
                ):
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNREACHABLE_FAILURE_ROUTE",
                            "semantic",
                            f"Failure route from {draft.node_id!r} requires allow-fail: true.",
                            route["span"],
                            context,
                            details={"source_node": draft.node_id, "target": target},
                        )
                    )
                if route["mode"] == "when":
                    for reference in self._predicate_references(route["predicate"]):
                        if reference["root"]["canonical"] == "secret":
                            diagnostics.append(
                                self._diagnostic(
                                    "RATTISH_WRONG_REFERENCE_KIND",
                                    "semantic",
                                    "Secret references cannot appear in route predicates.",
                                    reference["span"],
                                    context,
                                    details={"source_node": draft.node_id},
                                )
                            )
                        else:
                            self._validate_reference(reference, draft, drafts, context, diagnostics)
            for target, modes in route_modes.items():
                if {"unconditional", "otherwise"} <= modes:
                    route = next(
                        item
                        for item in draft.routes_ast
                        if item["target"]["canonical"] == target and item["mode"] == "otherwise"
                    )
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_CONTRADICTORY_ROUTE",
                            "semantic",
                            f"Node {draft.node_id!r} has unconditional and otherwise "
                            f"routes to {target!r}.",
                            route["span"],
                            context,
                            details={"source_node": draft.node_id, "target": target},
                        )
                    )

        incoming: dict[str, set[str]] = {node_id: set() for node_id in drafts}
        for source in drafts.values():
            for route in source.routes_ast:
                target = route["target"]["canonical"]
                if target in incoming:
                    incoming[target].add(source.node_id)

        route_graph = {
            node_id: {
                route["target"]["canonical"]
                for route in draft.routes_ast
                if route["target"]["canonical"] in drafts
            }
            for node_id, draft in drafts.items()
        }
        activation_contexts: dict[str, set[tuple[str, ...]]] = {
            node_id: ({()} if not draft.needs else set()) for node_id, draft in drafts.items()
        }
        changed = True
        while changed:
            changed = False
            for source_id, targets in route_graph.items():
                source_contexts = activation_contexts[source_id]
                if not source_contexts:
                    continue
                routed_contexts = set()
                for activation_context in source_contexts:
                    if (
                        drafts[source_id].contract.node_type == "loop"
                        and source_id not in activation_context
                    ):
                        routed_contexts.add((*activation_context, source_id))
                    else:
                        routed_contexts.add(activation_context)
                for target in targets:
                    previous = len(activation_contexts[target])
                    activation_contexts[target].update(routed_contexts)
                    changed = changed or len(activation_contexts[target]) != previous
        for node_id, contexts in activation_contexts.items():
            drafts[node_id].activation_contexts = contexts

        for draft in drafts.values():
            for needed in draft.needs:
                producer = drafts.get(needed)
                if producer is None:
                    continue
                compatible = any(
                    consumer_context[: len(producer_context)] == producer_context
                    for producer_context in producer.activation_contexts
                    for consumer_context in draft.activation_contexts
                )
                if compatible:
                    continue
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_INCOMPATIBLE_ACTIVATION_LINEAGE",
                        "semantic",
                        f"Requirement {needed!r} cannot complete in an activation lineage "
                        f"used by {draft.node_id!r}.",
                        draft.declaration["span"],
                        context,
                        details={"node": draft.node_id, "target": needed},
                    )
                )

        predecessors: dict[str, set[str]] = {node_id: set() for node_id in drafts}
        for source_id, targets in route_graph.items():
            for target in targets:
                predecessors[target].add(source_id)
        starts = {node_id for node_id, draft in drafts.items() if not draft.needs}
        all_nodes = set(drafts)
        dominators = {
            node_id: ({node_id} if node_id in starts else set(all_nodes)) for node_id in drafts
        }
        changed = True
        while changed:
            changed = False
            for node_id in sorted(all_nodes - starts):
                incoming_nodes = predecessors[node_id]
                if not incoming_nodes:
                    updated = {node_id}
                else:
                    common = set(all_nodes)
                    for predecessor in incoming_nodes:
                        common &= dominators[predecessor]
                    updated = {node_id, *common}
                if updated != dominators[node_id]:
                    dominators[node_id] = updated
                    changed = True
        need_closure = {node_id: set(draft.needs) for node_id, draft in drafts.items()}
        changed = True
        while changed:
            changed = False
            for node_id in sorted(drafts):
                expanded = set(need_closure[node_id])
                for needed in list(expanded):
                    expanded.update(need_closure.get(needed, set()))
                if expanded != need_closure[node_id]:
                    need_closure[node_id] = expanded
                    changed = True
        for node_id, draft in drafts.items():
            draft.guaranteed_sources = (dominators[node_id] | need_closure[node_id]) - {node_id}
        for draft in drafts.values():
            if draft.contract.node_type != "break":
                continue
            loop_id = draft.configuration.get("loop")
            if not isinstance(loop_id, str) or loop_id not in drafts:
                continue
            reachable = {loop_id}
            frontier = [loop_id]
            while frontier:
                current = frontier.pop()
                for target in route_graph[current] - reachable:
                    reachable.add(target)
                    frontier.append(target)
            if draft.node_id not in reachable:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_UNREACHABLE_BREAK",
                        "semantic",
                        f"Break node {draft.node_id!r} is not reachable from loop {loop_id!r}.",
                        draft.declaration["span"],
                        context,
                        details={"node": draft.node_id, "loop": loop_id},
                    )
                )
        potentially_runnable = {node_id for node_id, draft in drafts.items() if not draft.needs}
        changed = True
        while changed:
            changed = False
            for node_id, draft in drafts.items():
                if node_id in potentially_runnable:
                    continue
                if (
                    set(draft.needs) <= potentially_runnable
                    and incoming[node_id] & potentially_runnable
                ):
                    potentially_runnable.add(node_id)
                    changed = True
        for draft in drafts.values():
            for needed in draft.needs:
                if needed in drafts and needed not in potentially_runnable:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNREACHABLE_REQUIREMENT",
                            "semantic",
                            f"Requirement {needed!r} cannot become runnable in this workflow.",
                            draft.declaration["span"],
                            context,
                            details={"node": draft.node_id, "target": needed},
                        )
                    )

    def _lower(
        self,
        source: str,
        ast: Mapping[str, Any],
        workflow: dict[str, Any],
        drafts: Mapping[str, _NodeDraft],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> dict[str, Any]:
        incoming: dict[str, set[str]] = {node_id: set() for node_id in drafts}
        for draft in drafts.values():
            for route in draft.routes_ast:
                target = route["target"]["canonical"]
                if target in incoming:
                    incoming[target].add(draft.node_id)

        dependencies = [
            {
                "kind": "node_contract",
                "id": contract.node_type,
                "version": contract.version,
                "path": None,
                "fingerprint": contract.fingerprint,
            }
            for contract in sorted(
                {draft.contract.node_type: draft.contract for draft in drafts.values()}.values(),
                key=lambda item: item.node_type,
            )
        ]
        additional_dependencies = {
            (item["kind"], item["id"], str(item["version"]), item["fingerprint"]): item
            for draft in drafts.values()
            for item in draft.dependencies
        }
        dependencies.extend(additional_dependencies[key] for key in sorted(additional_dependencies))
        normalized_source = source.replace("\r\n", "\n").replace("\r", "\n")
        source_fingerprint = f"sha256:{hashlib.sha256(normalized_source.encode()).hexdigest()}"
        compilation_fingerprint = json_fingerprint(
            {
                "compiler_version": context.compiler_version,
                "dependencies": dependencies,
                "rattish_version": 1,
                "source_fingerprint": source_fingerprint,
                "workflow_id": context.workflow_id,
            }
        )
        lowered_nodes = [
            self._lower_node(draft, drafts, incoming[draft.node_id], context, diagnostics)
            for draft in sorted(drafts.values(), key=lambda item: item.node_id)
        ]
        node_source_map = {
            draft.node_id: self._file_span(draft.declaration["span"], context)
            for draft in sorted(drafts.values(), key=lambda item: item.node_id)
        }
        return {
            "$schema": IR_SCHEMA_ID,
            "ir_version": 1,
            "rattish_version": 1,
            "compiler": {"name": "raticode-rattish", "version": context.compiler_version},
            "source": {
                "workflow_id": context.workflow_id,
                "entrypoint": context.entrypoint,
                "project_root": str(context.project_root),
                "source_fingerprint": source_fingerprint,
                "compilation_fingerprint": compilation_fingerprint,
                "dependencies": dependencies,
            },
            "workflow": workflow,
            "execution_policy": {
                "initial_activation": "nodes_without_needs",
                "readiness_latch": "all_once_per_activation_lineage",
                "locked_signal_policy": "coalesce_until_unlocked",
                "same_group_signal_policy": "coalesce",
                "cross_group_signal_policy": "fifo_by_arrival",
                "output_selection": "latest_successful_at_consumer_start",
                "input_snapshot": "consumer_start",
                "unresolved_join": "fail_at_quiescence",
                "missed_schedules": "skip",
                "workflow_timeout_clock": "active_processing_time",
                "completion_event_order": [
                    "record_completion",
                    "satisfy_readiness",
                    "emit_routes",
                    "schedule_runnable_targets",
                ],
            },
            "nodes": lowered_nodes,
            "source_map": {
                "workflow": self._file_span(ast["workflow"]["span"], context),
                "nodes": node_source_map,
            },
        }

    def _lower_node(
        self,
        draft: _NodeDraft,
        drafts: Mapping[str, _NodeDraft],
        incoming: set[str],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> dict[str, Any]:
        allow_fail = self._bool_common(draft, "allow-fail", False, context, diagnostics)
        seen_bindings: set[str] = set()
        for binding in draft.bindings_ast:
            binding_name = binding["name"]["canonical"]
            if binding_name in seen_bindings:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_DUPLICATE_FIELD",
                        "semantic",
                        f"Node {draft.node_id!r} repeats binding {binding_name!r}.",
                        binding["name"]["span"],
                        context,
                        details={"node": draft.node_id, "field": binding_name},
                    )
                )
            seen_bindings.add(binding_name)
        declared_ports = (
            draft.workflow_input_ports
            if draft.contract.document["input_ports"]["mode"] == "referenced_workflow"
            else draft.contract.document["input_ports"]["ports"]
        )
        for port_name, port in declared_ports.items():
            configured_name = port_name.replace("-", "_")
            if (
                port.get("required")
                and port_name not in seen_bindings
                and configured_name not in draft.configuration
            ):
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_MISSING_INPUT",
                        "semantic",
                        f"Node {draft.node_id!r} requires input {port_name!r}.",
                        draft.declaration["span"],
                        context,
                        details={"node": draft.node_id, "input": port_name},
                    )
                )
        bindings = [
            self._lower_binding(binding, draft, drafts, context, diagnostics)
            for binding in draft.bindings_ast
        ]
        self._validate_prompt_selector_schemas(draft, bindings, context, diagnostics)
        if any(item.severity == "error" for item in diagnostics):
            raise RattishCompileError(diagnostics)
        routes = [
            self._lower_route(route, draft, drafts, context, diagnostics)
            for route in draft.routes_ast
        ]
        routes.sort(
            key=lambda item: (
                item["target"],
                item["mode"],
                canonical_json_bytes(item["predicate"]),
            )
        )
        execution = {
            "allow_fail": allow_fail,
            "timeout_ms": self._common_duration(draft, "timeout", context, diagnostics),
            "max_runs": self._common_positive_int(draft, "max-runs", context, diagnostics),
            "max_concurrency": self._common_positive_int(
                draft, "max-concurrency", context, diagnostics, default=1
            ),
            "retry_count": self._common_nonnegative_int(
                draft, "retry-count", context, diagnostics, default=0
            ),
            "retry_delay_ms": self._common_duration(
                draft, "retry-delay", context, diagnostics, default=1000
            ),
            "start_declared": self._bool_common(draft, "start", False, context, diagnostics),
            "initial_activation": not draft.needs,
            "finish": self._finish_common(draft, context, diagnostics),
        }
        if any(item.severity == "error" for item in diagnostics):
            raise RattishCompileError(diagnostics)
        return {
            "id": draft.node_id,
            "type": draft.contract.node_type,
            "runtime_handler": draft.contract.document["runtime_handler"],
            "contract": {
                "node_type": draft.contract.node_type,
                "version": draft.contract.version,
                "fingerprint": draft.contract.fingerprint,
            },
            "configuration": draft.configuration,
            "execution": execution,
            "readiness": {
                "needs": sorted(draft.needs),
                "incoming_route_sources": sorted(incoming),
                "latch": "all_once_per_activation_lineage",
                "pending_signal_policy": "coalesce_until_unlocked",
            },
            "bindings": sorted(bindings, key=lambda item: item["name"]),
            "output": {
                "schema": draft.output_schema,
                "error_kinds": sorted(draft.contract.document["error_kinds"]),
                "retention": "latest_successful_per_activation_lineage",
            },
            "preflight_checks": sorted(
                draft.contract.document["preflight_checks"], key=lambda item: item["id"]
            ),
            "effects": sorted(draft.contract.document["effects"]),
            "routes": routes,
            "resolutions": {
                "provider": draft.provider_resolution,
                "workflow": draft.workflow_resolution,
            },
            "control": (
                {
                    "kind": "break",
                    "loop_node_id": draft.configuration["loop"],
                    "cancel_queued": True,
                    "cancel_running": False,
                    "wait_for_running_branches": True,
                    "idempotent_per_lineage": True,
                }
                if draft.contract.node_type == "break"
                else None
            ),
            "source_span": self._file_span(draft.declaration["span"], context),
        }

    def _validate_prompt_selector_schemas(
        self,
        draft: _NodeDraft,
        bindings: list[dict[str, Any]],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        schemas = {binding["name"]: binding["source_schema"] for binding in bindings}
        if draft.contract.node_type == "prompt-file":
            variables = draft.configuration.get("variables", {})
            if isinstance(variables, Mapping):
                for name in variables:
                    schemas.setdefault(str(name).lower(), {"type": "string"})
        for field_name, template in self._configuration_template_strings(draft.configuration):
            try:
                placeholders = parse_prompt_template(template)
            except PromptTemplateError:
                continue
            for placeholder in placeholders:
                schema: Mapping[str, Any] = schemas.get(placeholder.name.lower(), {})
                invalid_selector: str | int | None = None
                for selector in placeholder.selectors:
                    if isinstance(selector, int):
                        items = schema.get("items")
                        if schema.get("type") != "array" or not isinstance(items, Mapping):
                            invalid_selector = selector
                            break
                        schema = items
                        continue
                    properties = schema.get("properties")
                    if isinstance(properties, Mapping) and isinstance(
                        properties.get(selector), Mapping
                    ):
                        schema = properties[selector]
                    elif schema.get("additionalProperties") is False:
                        invalid_selector = selector
                        break
                    else:
                        schema = {}
                if invalid_selector is None:
                    exact = self._exact_template_placeholder(template)
                    if exact is not None and exact.source == placeholder.source:
                        top_level = field_name.split(".", 1)[0].split("[", 1)[0]
                        properties = draft.contract.document["configuration_schema"].get(
                            "properties", {}
                        )
                        destination = (
                            properties.get(top_level, {}) if isinstance(properties, Mapping) else {}
                        )
                        if (
                            field_name == top_level
                            and destination
                            and not schema_accepts_schema(destination, schema)
                        ):
                            diagnostics.append(
                                self._diagnostic(
                                    "RATTISH_TEMPLATE_TYPE_MISMATCH",
                                    "semantic",
                                    f"Template value {placeholder.source!r} is not assignable "
                                    f"to configuration field {field_name!r}.",
                                    draft.declaration["span"],
                                    context,
                                    details={
                                        "node": draft.node_id,
                                        "field": field_name,
                                        "placeholder": placeholder.source,
                                    },
                                )
                            )
                    continue
                prompt_field = (draft.contract.node_type == "agent" and field_name == "prompt") or (
                    draft.contract.node_type == "prompt-file" and field_name == "template"
                )
                diagnostics.append(
                    self._diagnostic(
                        (
                            "RATTISH_PROMPT_TEMPLATE_INVALID"
                            if prompt_field
                            else "RATTISH_TEMPLATE_INVALID"
                        ),
                        "semantic",
                        f"Template placeholder {placeholder.source!r} selects a value not "
                        "present in its binding schema.",
                        draft.declaration["span"],
                        context,
                        details={
                            "node": draft.node_id,
                            "field": field_name,
                            "selector": invalid_selector,
                        },
                    )
                )

    def _lower_binding(
        self,
        binding: Mapping[str, Any],
        consumer: _NodeDraft,
        drafts: Mapping[str, _NodeDraft],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> dict[str, Any]:
        name = binding["name"]["canonical"]
        source_ast: Mapping[str, Any]
        default_ast: Mapping[str, Any] | None = None
        if binding["form"] == "compact":
            source_ast = binding["value"]
        else:
            source_entries = [item for item in binding["entries"] if item["kind"] == "from"]
            default_entries = [item for item in binding["entries"] if item["kind"] == "default"]
            if len(source_entries) != 1:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_MISSING_FIELD",
                        "semantic",
                        f"Binding {name!r} requires exactly one from field.",
                        binding["span"],
                        context,
                    )
                )
                source_ast = {"kind": "null", "value": None, "span": binding["span"]}
            else:
                source_ast = source_entries[0]["reference"]
            if default_entries:
                default_ast = default_entries[0]["value"]

        if source_ast["kind"] == "reference":
            self._validate_reference(source_ast, consumer, drafts, context, diagnostics)
            reference, source_schema = self._lower_reference(source_ast, consumer, drafts)
            source: dict[str, Any] = {"kind": "reference", "reference": reference}
            if reference["optional"] and default_ast is None:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_BINDING_DEFAULT_REQUIRED",
                        "semantic",
                        f"Binding {name!r} may be absent and requires a default.",
                        binding["span"],
                        context,
                    )
                )
        elif source_ast["kind"] == "expression":
            expression_ast = source_ast["expression"]
            self._validate_expression_references(
                expression_ast, consumer, drafts, context, diagnostics
            )
            expression = self._lower_predicate(expression_ast, consumer, drafts, context)
            self._validate_lowered_predicate(
                expression, {"span": source_ast["span"]}, context, diagnostics
            )
            source = {"kind": "expression", "expression": expression}
            source_schema = {"$schema": JSON_SCHEMA_ID, "type": "boolean"}
        else:
            value = self._value(source_ast)
            source = {"kind": "literal", "value": value}
            source_schema = self._infer_schema(value)

        port_contract = consumer.contract.document["input_ports"]
        declared_port = (
            consumer.workflow_input_ports.get(name)
            if port_contract["mode"] == "referenced_workflow"
            else port_contract["ports"].get(name)
        )
        if declared_port is not None:
            destination_schema = declared_port["schema"]
            if port_contract["mode"] == "referenced_workflow":
                delivery = {
                    "kind": "workflow_input",
                    "name": name,
                    "encoding": "identity",
                }
            elif declared_port["delivery"] == "stdin":
                delivery = {"kind": "stdin", "encoding": "utf8"}
            else:
                delivery = {
                    "kind": "local_binding",
                    "name": name,
                    "encoding": "identity",
                }
        elif port_contract["mode"] == "open":
            destination_schema = port_contract["additional_port_schema"]
            delivery_contract = port_contract["additional_port_delivery"]
            if delivery_contract["delivery"] == "environment":
                delivery = {
                    "kind": "environment",
                    "name": name.replace("-", "_").upper(),
                    "encoding": "string_or_canonical_json",
                    "precedence": "over_node_and_inherited_environment",
                }
            else:
                delivery = {
                    "kind": "local_binding",
                    "name": name,
                    "encoding": "identity",
                }
        else:
            destination_schema = source_schema
            delivery = {"kind": "local_binding", "name": name, "encoding": "identity"}

        default = (
            {"present": False}
            if default_ast is None
            else {"present": True, "value": self._value(default_ast)}
        )
        if not schema_accepts_schema(destination_schema, source_schema):
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_BINDING_TYPE_MISMATCH",
                    "semantic",
                    f"Binding {name!r} is not assignable to node {consumer.node_id!r} input.",
                    source_ast["span"],
                    context,
                    details={"node": consumer.node_id, "input": name},
                )
            )
        if default_ast is not None:
            default_value = self._value(default_ast)
            if not instance_matches_schema(destination_schema, default_value):
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_BINDING_TYPE_MISMATCH",
                        "semantic",
                        f"Default for binding {name!r} does not match its destination schema.",
                        default_ast["span"],
                        context,
                        details={"node": consumer.node_id, "input": name},
                    )
                )
        return {
            "name": name,
            "source": source,
            "source_schema": source_schema,
            "destination_schema": destination_schema,
            "default": default,
            "delivery": delivery,
        }

    def _validate_expression_references(
        self,
        expression: Mapping[str, Any],
        consumer: _NodeDraft,
        drafts: Mapping[str, _NodeDraft],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        kind = expression["kind"]
        if kind in {"logical"}:
            self._validate_expression_references(
                expression["left"], consumer, drafts, context, diagnostics
            )
            self._validate_expression_references(
                expression["right"], consumer, drafts, context, diagnostics
            )
            return
        if kind in {"not", "group"}:
            self._validate_expression_references(
                expression["operand"], consumer, drafts, context, diagnostics
            )
            return
        if kind in {"exists", "null_test", "reference_predicate"}:
            self._validate_reference(
                expression["reference"], consumer, drafts, context, diagnostics
            )
            return
        if kind == "comparison":
            for operand in (expression["left"], expression["right"]):
                if operand["kind"] == "reference":
                    self._validate_reference(operand, consumer, drafts, context, diagnostics)

    def _lower_reference(
        self,
        reference_ast: Mapping[str, Any],
        consumer: _NodeDraft,
        drafts: Mapping[str, _NodeDraft],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        root = reference_ast["root"]["canonical"]
        selectors = reference_ast["selectors"]
        symbol: str | None = None
        channel: str | None = None
        path: list[dict[str, Any]] = []
        schema: dict[str, Any] = {"$schema": JSON_SCHEMA_ID}
        optional = False
        if root == "node" and len(selectors) >= 2:
            symbol = selectors[0]["canonical"]
            channel = selectors[1]["canonical"]
            producer = drafts.get(symbol)
            if producer is not None and channel == "output":
                schema = producer.output_schema
                for selector in selectors[2:]:
                    if selector["kind"] == "member":
                        value = selector["source"]
                        path.append({"kind": "member", "value": value, "case_sensitive": True})
                        schema = self._member_schema(schema, value)
                    else:
                        value = selector["value"]
                        path.append({"kind": "index", "value": value})
                        schema = self._item_schema(schema)
                optional = symbol not in consumer.guaranteed_sources or self._draft_allow_fail(
                    producer
                )
            elif channel == "status":
                schema = {"$schema": JSON_SCHEMA_ID, "enum": ["success", "failure", "cancelled"]}
                optional = symbol not in consumer.guaranteed_sources
            elif channel == "error":
                schema = {
                    "$schema": JSON_SCHEMA_ID,
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "details": {},
                    },
                    "required": ["kind", "code", "message"],
                    "additionalProperties": False,
                }
                # Completion does not guarantee an error document. A successful producer
                # has no error channel, even when its activation is guaranteed.
                optional = True
        elif root == "input" and selectors:
            first = selectors[0]
            symbol = first.get("canonical") or first.get("source")
            schema = consumer.workflow_inputs.get(symbol, {"$schema": JSON_SCHEMA_ID})
            for selector in selectors[1:]:
                if selector["kind"] == "member":
                    value = selector["source"]
                    path.append({"kind": "member", "value": value, "case_sensitive": True})
                    schema = self._member_schema(schema, value)
                else:
                    path.append({"kind": "index", "value": selector["value"]})
                    schema = self._item_schema(schema)
        elif root == "trigger" and selectors:
            first = selectors[0]
            symbol = first.get("canonical") or first.get("source")
            if symbol == "events":
                schema = {
                    "$schema": JSON_SCHEMA_ID,
                    "type": "array",
                    "items": {"type": "object"},
                }
            for selector in selectors[1:]:
                if selector["kind"] == "member":
                    value = selector["source"]
                    path.append({"kind": "member", "value": value, "case_sensitive": True})
                    schema = self._member_schema(schema, value)
                else:
                    path.append({"kind": "index", "value": selector["value"]})
                    schema = self._item_schema(schema)
        elif root == "secret" and selectors:
            symbol = selectors[0]["source"]
            schema = {"$schema": JSON_SCHEMA_ID, "type": "string"}
        return (
            {
                "root": root,
                "symbol": symbol,
                "channel": channel,
                "path": path,
                "optional": optional,
                "schema": schema,
            },
            schema,
        )

    def _validate_reference(
        self,
        reference: Mapping[str, Any],
        consumer: _NodeDraft,
        drafts: Mapping[str, _NodeDraft],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        root = reference["root"]["canonical"]
        selectors = reference["selectors"]
        if root in {"workflow", "loop"}:
            replacement = "input" if root == "workflow" else "node.<loop-id>.output"
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_REFERENCE_ROOT_REMOVED",
                    "semantic",
                    f"Reference root {root!r} is not part of Rattish 1; use {replacement}.",
                    reference["span"],
                    context,
                    details={"consumer": consumer.node_id, "root": root},
                )
            )
            return
        if root != "node":
            if not selectors:
                diagnostics.append(
                    self._diagnostic(
                        "RATTISH_UNRESOLVED_REFERENCE",
                        "semantic",
                        f"Reference root {root!r} requires a symbol or channel.",
                        reference["span"],
                        context,
                        details={"consumer": consumer.node_id, "root": root},
                    )
                )
            elif root == "input":
                symbol = selectors[0].get("canonical") or selectors[0].get("source")
                if symbol not in consumer.workflow_inputs:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNRESOLVED_REFERENCE",
                            "semantic",
                            f"Reference names unknown workflow input {symbol!r}.",
                            selectors[0]["span"],
                            context,
                            details={"consumer": consumer.node_id, "input": symbol},
                        )
                    )
            elif root == "trigger":
                symbol = selectors[0].get("canonical") or selectors[0].get("source")
                if symbol != "events":
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_UNRESOLVED_REFERENCE",
                            "semantic",
                            "Rattish 1 exposes trigger data through trigger.events.",
                            selectors[0]["span"],
                            context,
                            details={"consumer": consumer.node_id, "trigger_field": symbol},
                        )
                    )
            elif root == "secret":
                if selectors[0].get("notation") != "bracket":
                    message = (
                        "Secret references require exact bracket notation, such as "
                        'secret["API_KEY"].'
                    )
                elif not selectors[0].get("source"):
                    message = "Secret references require a nonempty environment name."
                elif len(selectors) > 1:
                    message = "Secret references cannot contain value selectors."
                else:
                    message = ""
                if message:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_WRONG_REFERENCE_KIND",
                            "semantic",
                            message,
                            reference["span"],
                            context,
                            details={"consumer": consumer.node_id},
                        )
                    )
            return
        if len(selectors) < 2:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_WRONG_REFERENCE_KIND",
                    "semantic",
                    "Node references require a node ID and output, status, or error channel.",
                    reference["span"],
                    context,
                    details={"consumer": consumer.node_id},
                )
            )
            return
        producer_id = selectors[0]["canonical"]
        producer = drafts.get(producer_id)
        if producer is None:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_UNRESOLVED_REFERENCE",
                    "semantic",
                    f"Reference names unknown node {producer_id!r}.",
                    selectors[0]["span"],
                    context,
                    details={"consumer": consumer.node_id, "node": producer_id},
                )
            )
            return
        channel = selectors[1]["canonical"]
        if channel not in {"output", "status", "error"}:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_WRONG_REFERENCE_KIND",
                    "semantic",
                    f"Node reference channel {channel!r} is not output, status, or error.",
                    selectors[1]["span"],
                    context,
                    details={"consumer": consumer.node_id, "node": producer_id},
                )
            )
            return
        if channel != "output":
            return
        schema: Mapping[str, Any] = producer.output_schema
        for selector in selectors[2:]:
            if selector["kind"] == "member":
                properties = schema.get("properties")
                member = selector["source"]
                if isinstance(properties, Mapping) and member in properties:
                    child = properties[member]
                    schema = child if isinstance(child, Mapping) else {}
                    continue
                schema_type = schema.get("type")
                allows_object = schema_type == "object" or (
                    isinstance(schema_type, list) and "object" in schema_type
                )
                if schema_type is not None and not allows_object:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_JSON_SELECTOR",
                            "semantic",
                            f"Output selector reads member {member!r} from non-object output "
                            f"of node {producer_id!r}.",
                            selector["span"],
                            context,
                            details={"consumer": consumer.node_id, "node": producer_id},
                        )
                    )
                    return
                additional = schema.get("additionalProperties")
                if additional is False:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_JSON_SELECTOR",
                            "semantic",
                            f"Output of node {producer_id!r} has no member {member!r}.",
                            selector["span"],
                            context,
                            details={"consumer": consumer.node_id, "node": producer_id},
                        )
                    )
                    return
                schema = additional if isinstance(additional, Mapping) else {}
            else:
                items = schema.get("items")
                if schema.get("type") != "array" or not isinstance(items, Mapping):
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_JSON_SELECTOR",
                            "semantic",
                            f"Output selector indexes a non-array value from node {producer_id!r}.",
                            selector["span"],
                            context,
                            details={"consumer": consumer.node_id, "node": producer_id},
                        )
                    )
                    return
                schema = items

    def _lower_route(
        self,
        route: Mapping[str, Any],
        source: _NodeDraft,
        drafts: Mapping[str, _NodeDraft],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> dict[str, Any]:
        mode_map = {
            "unconditional": "unconditional",
            "when": "conditional",
            "otherwise": "otherwise",
        }
        predicate = (
            self._lower_predicate(route["predicate"], source, drafts, context)
            if route["mode"] == "when"
            else None
        )
        if predicate is not None:
            self._validate_lowered_predicate(predicate, route, context, diagnostics)
        return {
            "target": route["target"]["canonical"],
            "mode": mode_map[route["mode"]],
            "predicate": predicate,
            "eligible_outcomes": ["success", "allowed_failure"],
        }

    def _validate_lowered_predicate(
        self,
        predicate: Mapping[str, Any],
        route: Mapping[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        kind = predicate["kind"]
        if kind == "logical":
            self._validate_lowered_predicate(predicate["left"], route, context, diagnostics)
            self._validate_lowered_predicate(predicate["right"], route, context, diagnostics)
            return
        if kind == "not":
            self._validate_lowered_predicate(predicate["operand"], route, context, diagnostics)
            return
        if kind == "reference":
            if "boolean" not in self._schema_types(predicate["reference"]["schema"]):
                self._predicate_error(
                    "A standalone route reference must have Boolean schema.",
                    route,
                    context,
                    diagnostics,
                )
            return
        if kind != "comparison":
            return
        operator = predicate["operator"]
        left_types = self._operand_types(predicate["left"])
        right_types = self._operand_types(predicate["right"])
        numeric = {"integer", "number"}
        compatible = bool(left_types & right_types) or bool(
            left_types & numeric and right_types & numeric
        )
        if operator in {"==", "!="} and not compatible:
            self._predicate_error(
                f"Predicate operator {operator} compares incompatible schemas.",
                route,
                context,
                diagnostics,
            )
        elif operator in {"<", "<=", ">", ">="}:
            ordered = (left_types <= numeric and right_types <= numeric) or (
                left_types == {"string"} and right_types == {"string"}
            )
            if not ordered:
                self._predicate_error(
                    f"Predicate operator {operator} requires two Numbers or two Strings.",
                    route,
                    context,
                    diagnostics,
                )
        elif operator == "contains" and not left_types & {"string", "array", "object"}:
            self._predicate_error(
                "Predicate operator contains requires a String, List, or Object on the left.",
                route,
                context,
                diagnostics,
            )
        elif operator == "matches":
            if left_types != {"string"} or right_types != {"string"}:
                self._predicate_error(
                    "Predicate operator matches requires String operands.",
                    route,
                    context,
                    diagnostics,
                )
            right = predicate["right"]
            if right["kind"] == "literal" and isinstance(right["value"], str):
                try:
                    re.compile(right["value"])
                except re.error as exc:
                    diagnostics.append(
                        self._diagnostic(
                            "RATTISH_INVALID_REGEX",
                            "semantic",
                            f"Route regular expression is invalid: {exc}",
                            route["span"],
                            context,
                        )
                    )

    def _predicate_error(
        self,
        message: str,
        route: Mapping[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        diagnostics.append(
            self._diagnostic(
                "RATTISH_PREDICATE_TYPE_MISMATCH",
                "semantic",
                message,
                route["span"],
                context,
            )
        )

    def _operand_types(self, operand: Mapping[str, Any]) -> set[str]:
        schema = (
            operand["reference"]["schema"] if operand["kind"] == "reference" else operand["schema"]
        )
        return self._schema_types(schema)

    @staticmethod
    def _schema_types(schema: Mapping[str, Any]) -> set[str]:
        raw = schema.get("type")
        if isinstance(raw, str):
            return {raw}
        if isinstance(raw, list):
            return {item for item in raw if isinstance(item, str)}
        if "enum" in schema:
            return {RattishCompiler._json_type(value) for value in schema["enum"]}
        return set()

    @staticmethod
    def _json_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        return "object"

    def _lower_predicate(
        self,
        predicate: Mapping[str, Any],
        source: _NodeDraft,
        drafts: Mapping[str, _NodeDraft],
        context: CompileContext,
    ) -> dict[str, Any]:
        _ = context
        kind = predicate["kind"]
        if kind == "group":
            return self._lower_predicate(predicate["operand"], source, drafts, context)
        if kind == "logical":
            return {
                "kind": "logical",
                "operator": predicate["operator"],
                "left": self._lower_predicate(predicate["left"], source, drafts, context),
                "right": self._lower_predicate(predicate["right"], source, drafts, context),
            }
        if kind == "not":
            return {
                "kind": "not",
                "operand": self._lower_predicate(predicate["operand"], source, drafts, context),
            }
        if kind == "status":
            return {"kind": "status", "value": predicate["value"]}
        if kind == "comparison":
            return {
                "kind": "comparison",
                "operator": predicate["operator"],
                "left": self._lower_operand(predicate["left"], source, drafts),
                "right": self._lower_operand(predicate["right"], source, drafts),
            }
        if kind == "exists":
            reference, _ = self._lower_reference(predicate["reference"], source, drafts)
            return {"kind": "exists", "reference": reference}
        if kind == "null_test":
            reference, _ = self._lower_reference(predicate["reference"], source, drafts)
            return {"kind": "null_test", "operator": predicate["operator"], "reference": reference}
        reference_ast = predicate.get("reference", predicate)
        reference, _ = self._lower_reference(reference_ast, source, drafts)
        return {"kind": "reference", "reference": reference}

    def _lower_operand(
        self, operand: Mapping[str, Any], source: _NodeDraft, drafts: Mapping[str, _NodeDraft]
    ) -> dict[str, Any]:
        if operand["kind"] == "reference":
            reference, _ = self._lower_reference(operand, source, drafts)
            return {"kind": "reference", "reference": reference}
        value = self._value(operand)
        return {"kind": "literal", "value": value, "schema": self._infer_schema(value)}

    @staticmethod
    def _member_schema(schema: Mapping[str, Any], member: str) -> dict[str, Any]:
        properties = schema.get("properties")
        if isinstance(properties, Mapping) and isinstance(properties.get(member), Mapping):
            return {"$schema": JSON_SCHEMA_ID, **properties[member]}
        return {"$schema": JSON_SCHEMA_ID}

    @staticmethod
    def _item_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
        items = schema.get("items")
        if isinstance(items, Mapping):
            return {"$schema": JSON_SCHEMA_ID, **items}
        return {"$schema": JSON_SCHEMA_ID}

    def _resolved_output_schema(
        self,
        node_id: str,
        declaration: Mapping[str, Any],
        contract: LoadedNodeContract,
        configuration: Mapping[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        output = contract.document["success_output"]
        if output["kind"] == "fixed":
            return cast(dict[str, Any], output["schema"]), []
        if output["kind"] != "configuration_selected":
            return {"$schema": JSON_SCHEMA_ID}, []

        schema_fields = output["schema_fields"]
        inline_field = next(
            (name for name in schema_fields if isinstance(configuration.get(name), Mapping)),
            None,
        )
        if inline_field is not None:
            schema = dict(configuration[inline_field])
            schema.setdefault("$schema", JSON_SCHEMA_ID)
            self._validate_selected_schema(node_id, declaration, schema, context, diagnostics)
            return schema, []

        path_field = next(
            (
                name
                for name in schema_fields
                if name.endswith("_path") and isinstance(configuration.get(name), str)
            ),
            None,
        )
        if path_field is None:
            return cast(dict[str, Any], output["default_schema"]), []
        authored_path = cast(str, configuration[path_field])
        project_root = context.project_root.resolve()
        schema_path = (project_root / authored_path).resolve()
        try:
            relative_path = schema_path.relative_to(project_root)
        except ValueError:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_SCHEMA_REFERENCE_FORBIDDEN",
                    "semantic",
                    f"Schema path for node {node_id!r} escapes the workflow project.",
                    declaration["span"],
                    context,
                    details={"node": node_id, "path": authored_path},
                )
            )
            return {"$schema": JSON_SCHEMA_ID}, []
        try:
            raw = schema_path.read_text(encoding="utf-8")
            loaded = strict_json_loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_SCHEMA_REFERENCE_MISSING",
                    "semantic",
                    f"Cannot load schema for node {node_id!r}: {exc}",
                    declaration["span"],
                    context,
                    details={"node": node_id, "path": authored_path},
                )
            )
            return {"$schema": JSON_SCHEMA_ID}, []
        if not isinstance(loaded, dict):
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_SCHEMA_INVALID",
                    "semantic",
                    f"Schema file for node {node_id!r} must contain a JSON object.",
                    declaration["span"],
                    context,
                    details={"node": node_id, "path": authored_path},
                )
            )
            return {"$schema": JSON_SCHEMA_ID}, []
        loaded.setdefault("$schema", JSON_SCHEMA_ID)
        self._validate_selected_schema(node_id, declaration, loaded, context, diagnostics)
        dependency = {
            "kind": "schema",
            "id": str(relative_path).replace("\\", "/"),
            "version": None,
            "path": str(relative_path).replace("\\", "/"),
            "fingerprint": json_fingerprint(loaded),
        }
        return loaded, [dependency]

    def _validate_selected_schema(
        self,
        node_id: str,
        declaration: Mapping[str, Any],
        schema: Mapping[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_SCHEMA_INVALID",
                    "semantic",
                    f"Output schema for node {node_id!r} is invalid: {exc.message}",
                    declaration["span"],
                    context,
                    details={"node": node_id, "path": list(exc.absolute_path)},
                )
            )
            return
        unsupported = unsupported_profile_paths(schema)
        if unsupported:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_SCHEMA_PROFILE_UNSUPPORTED",
                    "semantic",
                    f"Output schema for node {node_id!r} uses unsupported keyword paths.",
                    declaration["span"],
                    context,
                    details={"node": node_id, "paths": [list(path) for path in unsupported]},
                )
            )

    def _configuration_value(
        self,
        contract: LoadedNodeContract,
        field_name: str,
        value_ast: Mapping[str, Any],
    ) -> Any:
        value = self._value(value_ast)
        properties = contract.document["configuration_schema"].get("properties", {})
        field_schema = properties.get(field_name, {})
        return self._configuration_schema_value(value, field_schema)

    @classmethod
    def _configuration_schema_value(cls, value: Any, schema: Mapping[str, Any]) -> Any:
        if isinstance(value, str):
            if schema.get("x-rattish-role") in {"identifier", "provider_id"}:
                return value.lower()
            if schema.get("x-rattish-case-insensitive") is True:
                return value.lower()
            return value
        if isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                return [cls._configuration_schema_value(item, item_schema) for item in value]
            return value
        if isinstance(value, dict):
            properties = schema.get("properties")
            if not isinstance(properties, Mapping):
                return value
            normalized: dict[str, Any] = {}
            for authored_name, item in value.items():
                machine_name = authored_name.replace("-", "_")
                item_schema = properties.get(machine_name, {})
                normalized[machine_name] = cls._configuration_schema_value(item, item_schema)
            return normalized
        return value

    def _apply_computed_defaults(
        self,
        node_id: str,
        declaration: Mapping[str, Any],
        contract: LoadedNodeContract,
        configuration: dict[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> dict[str, Any] | None:
        computed = contract.document["computed_defaults"]
        provider_defaults = {
            name: item for name, item in computed.items() if item["source"] == "provider_contract"
        }
        if not provider_defaults:
            return None
        provider_id = configuration.get("provider")
        if not isinstance(provider_id, str):
            return None
        provider_id = provider_id.lower()
        configuration["provider"] = provider_id
        provider = next(
            (
                value
                for key, value in context.provider_contracts.items()
                if key.lower() == provider_id or value.provider_id.lower() == provider_id
            ),
            None,
        )
        if provider is None:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_PROVIDER_CONTRACT_UNRESOLVED",
                    "semantic",
                    f"No compile-time provider contract is available for {provider_id!r}.",
                    declaration["span"],
                    context,
                    details={"node": node_id, "provider": provider_id},
                )
            )
            return None
        for field_name, rule in provider_defaults.items():
            if field_name in configuration:
                continue
            provider_field = rule.get("field")
            if provider_field == "default_model":
                configuration[field_name] = provider.default_model
            elif provider_field == "default_effort":
                configuration[field_name] = provider.default_effort
        return {
            "provider_id": provider_id,
            "contract_version": provider.version,
            "contract_fingerprint": provider.fingerprint,
            "model": configuration["model"],
            "effort": configuration["effort"],
            "profile": configuration.get("profile"),
        }

    def _run_static_diagnostics(
        self,
        declaration: Mapping[str, Any],
        node_id: str,
        contract: LoadedNodeContract,
        configuration: Mapping[str, Any],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        declared_fields = {
            entry["name"]["canonical"].replace("-", "_"): entry
            for entry in declaration["entries"]
            if entry["kind"] == "field"
        }
        static_diagnostics = contract.document["static_diagnostics"]
        rules = {item["rule"] for item in static_diagnostics}
        for rule in static_diagnostics:
            if rule["rule"] != "mutually_exclusive_fields":
                continue
            present = [name for name in rule["fields"] if name in declared_fields]
            if len(present) < 2:
                continue
            second = declared_fields[present[1]]
            diagnostics.append(
                RattishDiagnostic(
                    code=rule["code"],
                    severity=rule["severity"],
                    phase="semantic",
                    message=f"Fields {present[0]!r} and {present[1]!r} are mutually exclusive.",
                    file=context.entrypoint,
                    span=self._span(second["name"]["span"]),
                    details={"node": node_id, "fields": present[:2]},
                )
            )
        if "suspected_plaintext_secret" in rules:
            secret_fields = {
                field_name
                for rule in static_diagnostics
                if rule["rule"] == "suspected_plaintext_secret"
                for field_name in rule["fields"]
            }
            self._plaintext_secret_warnings(
                declaration, node_id, secret_fields, context, diagnostics
            )
        if (
            "blank_prompt" in rules
            and not str(configuration.get("prompt", "")).strip()
            and not configuration.get("prompt_path")
        ):
            diagnostics.append(
                RattishDiagnostic(
                    code="RATTISH_BLANK_AGENT_PROMPT",
                    severity="warning",
                    phase="semantic",
                    message=f"Agent {node_id!r} has no inline prompt or prompt path.",
                    file=context.entrypoint,
                    span=self._span(declaration["name"]["span"]),
                    details={"node": node_id},
                    suggestions=(
                        "Add prompt or prompt-path if the Agent does not receive its task "
                        "another way.",
                    ),
                )
            )

    def _plaintext_secret_warnings(
        self,
        declaration: Mapping[str, Any],
        node_id: str,
        configured_fields: set[str],
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> None:
        for field in declaration["entries"]:
            if field["kind"] != "field":
                continue
            field_name = field["name"]["canonical"].replace("-", "_")
            if field_name not in configured_fields:
                continue
            if field_name == "env" and field["value"]["kind"] == "map":
                for entry in field["value"]["entries"]:
                    key = self._map_key(entry["key"])
                    if not _SENSITIVE_NAME.search(key):
                        continue
                    diagnostics.append(
                        RattishDiagnostic(
                            code="RATTISH_SUSPECTED_PLAINTEXT_SECRET",
                            severity="warning",
                            phase="semantic",
                            message=(
                                f"Environment entry {key!r} looks like a plaintext credential."
                            ),
                            file=context.entrypoint,
                            span=self._span(entry["value"]["span"]),
                            details={"field": f"env.{key}"},
                            suggestions=(
                                "Use a secret reference when the runtime should supply this value.",
                            ),
                        )
                    )
                continue
            value = self._value(field["value"])
            sensitive_paths = list(self._sensitive_value_paths(value, field_name))
            if field_name == "url" and isinstance(value, str):
                sensitive_paths.extend(
                    f"url.{key}"
                    for key, _ in urllib.parse.parse_qsl(
                        urllib.parse.urlsplit(value).query, keep_blank_values=True
                    )
                    if _SENSITIVE_NAME.search(key)
                )
            for path in sorted(set(sensitive_paths)):
                diagnostics.append(
                    RattishDiagnostic(
                        code="RATTISH_SUSPECTED_PLAINTEXT_SECRET",
                        severity="warning",
                        phase="semantic",
                        message=f"Field {path!r} looks like a plaintext credential.",
                        file=context.entrypoint,
                        span=self._span(field["value"]["span"]),
                        details={"node": node_id, "field": path},
                        suggestions=(
                            "Use a secret reference when the runtime should supply this value.",
                        ),
                    )
                )

    @classmethod
    def _sensitive_value_paths(cls, value: Any, path: str) -> list[str]:
        if isinstance(value, Mapping):
            paths: list[str] = []
            for key, item in value.items():
                child = f"{path}.{key}"
                if _SENSITIVE_NAME.search(str(key)):
                    paths.append(child)
                else:
                    paths.extend(cls._sensitive_value_paths(item, child))
            return paths
        if isinstance(value, list):
            return [
                nested
                for index, item in enumerate(value)
                for nested in cls._sensitive_value_paths(item, f"{path}.{index}")
            ]
        if value not in {None, ""} and _SENSITIVE_NAME.search(path.rsplit(".", 1)[-1]):
            return [path]
        return []

    def _optional_positive_int(
        self,
        field: Mapping[str, Any] | None,
        name: str,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> int | None:
        if field is None or field["value"]["kind"] == "none":
            return None
        value = self._value(field["value"])
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    f"{name} must be a positive integer or none.",
                    field["value"]["span"],
                    context,
                )
            )
            return None
        return value

    def _optional_duration(
        self,
        field: Mapping[str, Any] | None,
        name: str,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> int | None:
        if field is None or field["value"]["kind"] == "none":
            return None
        value = field["value"]
        if value["kind"] != "duration":
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    f"{name} must be a duration or none.",
                    value["span"],
                    context,
                )
            )
            return None
        amount = cast(int, value["amount"])
        unit = cast(str, value["canonical_unit"])
        return amount * _DURATION_MULTIPLIERS[unit]

    def _common_duration(
        self,
        draft: _NodeDraft,
        name: str,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
        *,
        default: int | None = None,
    ) -> int | None:
        if name not in draft.common:
            return default
        value = draft.common[name]
        if value is None:
            return None
        ast_field = self._find_field(draft.declaration, name)
        assert ast_field is not None
        ast_value = ast_field["value"]
        if ast_value["kind"] != "duration":
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    f"{name} must be a duration or none.",
                    ast_value["span"],
                    context,
                )
            )
            return default
        amount = cast(int, ast_value["amount"])
        unit = cast(str, ast_value["canonical_unit"])
        return amount * _DURATION_MULTIPLIERS[unit]

    def _common_positive_int(
        self,
        draft: _NodeDraft,
        name: str,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
        *,
        default: int | None = None,
    ) -> int | None:
        if name not in draft.common:
            return default
        value = draft.common[name]
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            field = self._find_field(draft.declaration, name)
            assert field is not None
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    f"{name} must be a positive integer or none.",
                    field["value"]["span"],
                    context,
                )
            )
            return default
        return value

    def _common_nonnegative_int(
        self,
        draft: _NodeDraft,
        name: str,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
        *,
        default: int,
    ) -> int:
        if name not in draft.common:
            return default
        value = draft.common[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            field = self._find_field(draft.declaration, name)
            assert field is not None
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    f"{name} must be a nonnegative integer.",
                    field["value"]["span"],
                    context,
                )
            )
            return default
        return value

    def _bool_common(
        self,
        draft: _NodeDraft,
        name: str,
        default: bool,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> bool:
        if name not in draft.common:
            return default
        value = draft.common[name]
        if not isinstance(value, bool):
            field = self._find_field(draft.declaration, name)
            assert field is not None
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    f"{name} must be Boolean.",
                    field["value"]["span"],
                    context,
                )
            )
            return default
        return value

    def _finish_common(
        self,
        draft: _NodeDraft,
        context: CompileContext,
        diagnostics: list[RattishDiagnostic],
    ) -> str | None:
        value = draft.common.get("finish")
        if value is None:
            return None
        if not isinstance(value, str) or value.lower() not in {"pass", "fail"}:
            field = self._find_field(draft.declaration, "finish")
            assert field is not None
            diagnostics.append(
                self._diagnostic(
                    "RATTISH_INVALID_FIELD_VALUE",
                    "semantic",
                    "finish must be pass, fail, or none.",
                    field["value"]["span"],
                    context,
                )
            )
            return None
        return value.lower()

    @staticmethod
    def _draft_allow_fail(draft: _NodeDraft) -> bool:
        return draft.common.get("allow-fail") is True

    @staticmethod
    def _find_field(declaration: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
        return next(
            (
                entry
                for entry in declaration["entries"]
                if entry["kind"] == "field" and entry["name"]["canonical"] == name
            ),
            None,
        )

    @staticmethod
    def _semantic_value(value: Any) -> Any:
        if isinstance(value, list):
            return [RattishCompiler._semantic_value(item) for item in value]
        if isinstance(value, Mapping):
            if value.get("kind") == "identifier":
                return value["canonical"]
            return {
                key: RattishCompiler._semantic_value(item)
                for key, item in value.items()
                if key not in {"span", "source", "keyword_source"}
            }
        return value

    @staticmethod
    def _predicate_can_match_failure(predicate: Mapping[str, Any]) -> bool:
        kind = predicate["kind"]
        if kind == "status":
            return bool(predicate["value"] == "failed")
        if kind == "logical":
            return RattishCompiler._predicate_can_match_failure(
                predicate["left"]
            ) or RattishCompiler._predicate_can_match_failure(predicate["right"])
        if kind == "group":
            return RattishCompiler._predicate_can_match_failure(predicate["operand"])
        if kind == "not":
            return RattishCompiler._predicate_can_match_success(predicate["operand"])
        return False

    @staticmethod
    def _predicate_can_match_success(predicate: Mapping[str, Any]) -> bool:
        kind = predicate["kind"]
        if kind == "status":
            return bool(predicate["value"] == "succeeded")
        if kind == "logical":
            return RattishCompiler._predicate_can_match_success(
                predicate["left"]
            ) or RattishCompiler._predicate_can_match_success(predicate["right"])
        if kind == "group":
            return RattishCompiler._predicate_can_match_success(predicate["operand"])
        if kind == "not":
            return RattishCompiler._predicate_can_match_failure(predicate["operand"])
        return False

    @staticmethod
    def _predicate_references(predicate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        kind = predicate["kind"]
        if kind == "reference":
            return [predicate]
        if kind in {"exists", "null_test"}:
            return [predicate["reference"]]
        if kind == "comparison":
            return [
                operand
                for operand in (predicate["left"], predicate["right"])
                if operand["kind"] == "reference"
            ]
        if kind == "logical":
            return [
                *RattishCompiler._predicate_references(predicate["left"]),
                *RattishCompiler._predicate_references(predicate["right"]),
            ]
        if kind in {"group", "not"}:
            return RattishCompiler._predicate_references(predicate["operand"])
        return []

    @staticmethod
    def _value(value: Mapping[str, Any]) -> Any:
        kind = value["kind"]
        if kind in {"string", "integer", "number", "boolean", "json", "null"}:
            return value["value"]
        if kind == "none":
            return None
        if kind == "identifier_value":
            return value["canonical"]
        if kind == "duration":
            return value["source"]
        if kind == "list":
            return [RattishCompiler._value(item) for item in value["items"]]
        if kind == "map":
            return {
                RattishCompiler._map_key(entry["key"]): RattishCompiler._value(entry["value"])
                for entry in value["entries"]
            }
        raise TypeError(f"Cannot convert AST value kind {kind!r} to a literal")

    @staticmethod
    def _map_key(key: Mapping[str, Any]) -> str:
        raw_key = key["value"] if key["kind"] == "string" else key["canonical"]
        return cast(str, raw_key)

    @staticmethod
    def _infer_schema(value: Any) -> dict[str, Any]:
        schema: dict[str, Any] = {"$schema": JSON_SCHEMA_ID}
        if value is None:
            schema["type"] = "null"
        elif isinstance(value, bool):
            schema["type"] = "boolean"
        elif isinstance(value, int):
            schema["type"] = "integer"
        elif isinstance(value, float):
            schema["type"] = "number"
        elif isinstance(value, str):
            schema["type"] = "string"
        elif isinstance(value, list):
            schema["type"] = "array"
            item_schemas = [RattishCompiler._infer_schema(item) for item in value]
            schema["items"] = item_schemas[0] if item_schemas else {}
        elif isinstance(value, dict):
            schema.update(
                {
                    "type": "object",
                    "properties": {
                        key: RattishCompiler._infer_schema(item) for key, item in value.items()
                    },
                    "required": list(value),
                    "additionalProperties": False,
                }
            )
        return schema

    @staticmethod
    def _span(value: Mapping[str, Any]) -> SourceSpan:
        return SourceSpan(
            SourcePosition(**value["start"]),
            SourcePosition(**value["end"]),
        )

    def _diagnostic(
        self,
        code: str,
        phase: Literal["semantic", "lowering"],
        message: str,
        span: Mapping[str, Any],
        context: CompileContext,
        *,
        details: dict[str, Any] | None = None,
    ) -> RattishDiagnostic:
        return RattishDiagnostic(
            code=code,
            severity="error",
            phase=phase,
            message=message,
            file=context.entrypoint,
            span=self._span(span),
            details=details or {},
        )

    @staticmethod
    def _file_span(span: Mapping[str, Any], context: CompileContext) -> dict[str, Any]:
        return {"file": context.entrypoint, **span}
