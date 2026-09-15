from __future__ import annotations

import copy
import importlib.metadata
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Literal, Union, cast, get_args, get_origin

from pydantic import BaseModel, TypeAdapter

from gofer.core.agent import AgentConfig
from gofer.core.graph import EdgeConfig, GraphNode
from gofer.core.operations import FanSource, Operation
from gofer.core.references import (
    REFERENCE_FIELD_CAPABILITIES,
    REFERENCE_NAMESPACE_CAPABILITIES,
)
from gofer.core.resources import ResourceLimits
from gofer.core.workflow import (
    PARAMETER_NAME_PATTERN,
    WORKFLOW_ID_PATTERN,
    FilesystemAccessEntry,
    ScheduleConfig,
    WatchConfig,
    WebhookTriggerConfig,
    WorkflowComponentConfig,
    WorkflowConfig,
    WorkflowParameterConfig,
    WorkflowVariableConfig,
)

AUTHORING_SCHEMA_VERSION = "1.0.0"
WORKFLOW_FORMAT_VERSIONS = ["1"]


def _inline_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    definitions = schema.get("$defs", {})

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                target = copy.deepcopy(definitions[name])
                siblings = {key: item for key, item in value.items() if key != "$ref"}
                return visit({**target, **siblings})
            return {key: visit(item) for key, item in value.items() if key != "$defs"}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    return cast(dict[str, Any], visit(schema))


def _tag_field_ids(value: Any, prefix: str) -> Any:
    """Add stable, nesting-aware field IDs to an inlined JSON Schema."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "properties" and isinstance(item, dict):
                properties: dict[str, Any] = {}
                for name, field_schema in item.items():
                    field_id = f"{prefix}.{name}"
                    tagged = _tag_field_ids(field_schema, field_id)
                    if isinstance(tagged, dict):
                        tagged["x-gofer-field-id"] = field_id
                    properties[name] = tagged
                result[key] = properties
            else:
                result[key] = _tag_field_ids(item, prefix)
        return result
    if isinstance(value, list):
        return [_tag_field_ids(item, prefix) for item in value]
    return value


def _raw_schema(source: type[BaseModel] | Any) -> dict[str, Any]:
    schema: dict[str, Any] = (
        source.model_json_schema(by_alias=True)
        if isinstance(source, type) and issubclass(source, BaseModel)
        else TypeAdapter(source).json_schema(by_alias=True)
    )
    return _inline_local_refs(schema)


def _finish_schema(schema: dict[str, Any], capability_id: str) -> dict[str, Any]:
    tagged = cast(dict[str, Any], _tag_field_ids(copy.deepcopy(schema), capability_id))
    tagged["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    tagged["$id"] = f"https://gofer-flow.local/schema/{AUTHORING_SCHEMA_VERSION}/{capability_id}"
    tagged["x-gofer-capability-id"] = capability_id
    return tagged


def _rename_fields(
    schema: dict[str, Any],
    names: dict[str, str],
    *,
    omit: set[str] | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(schema)
    omitted = omit or set()
    properties = result.get("properties", {})
    result["properties"] = {
        names.get(name, name): field for name, field in properties.items() if name not in omitted
    }
    result["required"] = [
        names.get(name, name) for name in result.get("required", []) if name not in omitted
    ]
    return result


def _union_members(annotation: Any) -> tuple[type[BaseModel], ...]:
    annotated = get_args(annotation)[0] if get_origin(annotation) is Annotated else annotation
    members = get_args(annotated)
    return tuple(
        member for member in members if isinstance(member, type) and issubclass(member, BaseModel)
    )


OPERATION_MODELS: dict[str, type[BaseModel]] = {}
for _model in _union_members(Operation):
    _literal = get_args(_model.model_fields["type"].annotation)[0]
    OPERATION_MODELS[str(getattr(_literal, "value", _literal))] = _model


def operation_field_enum_values(field_name: str) -> list[object]:
    """Return enum/const values for a field from the runtime operation models."""
    values: set[object] = set()
    for model in OPERATION_MODELS.values():
        field = model.model_fields.get(field_name)
        if field is None:
            continue
        schema = TypeAdapter(field.annotation).json_schema()

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("enum"), list):
                    values.update(value["enum"])
                if "const" in value:
                    values.add(value["const"])
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(schema)
    return sorted(values, key=str)


def _graph_envelope_schema() -> dict[str, Any]:
    schema = _rename_fields(
        _raw_schema(GraphNode),
        {"node_id": "id"},
        omit={"operation"},
    )
    schema["properties"]["id"]["description"] = "Unique node ID within the workflow."
    schema["properties"]["on_failure"].update(
        {
            "deprecated": True,
            "description": (
                "Deprecated parser compatibility field. Use outgoing edges with explicit "
                "conditions instead. 'halt' synthesizes on_success; other values synthesize "
                "always."
            ),
            "x-gofer-replacement": "edges.*.condition",
        }
    )
    return schema


def _node_schema_for_operation(name: str) -> dict[str, Any]:
    envelope = _graph_envelope_schema()
    operation = _raw_schema(OPERATION_MODELS[name])
    properties = {**envelope["properties"], **operation["properties"]}
    required = list(dict.fromkeys([*envelope.get("required", []), *operation.get("required", [])]))
    return {
        "title": f"{name} workflow node",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _node_schema() -> dict[str, Any]:
    return {
        "title": "Workflow node",
        "oneOf": [_node_schema_for_operation(name) for name in sorted(OPERATION_MODELS)],
    }


def _agent_schema() -> dict[str, Any]:
    schema = _rename_fields(_raw_schema(AgentConfig), {}, omit={"agent_id"})
    schema["description"] = "Agent table value; the [agents.<id>] table key supplies agent_id."
    schema["x-gofer-container-key-field"] = "agent_id"
    return schema


def _edge_schema() -> dict[str, Any]:
    return _rename_fields(
        _raw_schema(EdgeConfig),
        {"from_node": "from", "to_node": "to"},
    )


def _webhook_schema() -> dict[str, Any]:
    schema = _rename_fields(_raw_schema(WebhookTriggerConfig), {}, omit={"id"})
    schema["description"] = "Webhook table value; the table key supplies the webhook id."
    schema["x-gofer-container-key-field"] = "id"
    return schema


def _apply_runtime_constraints(capability: str, schema: dict[str, Any]) -> None:
    properties = schema.get("properties", {})
    if capability in {"workflow", "subflow"} and "id" in properties:
        properties["id"]["pattern"] = WORKFLOW_ID_PATTERN.pattern
    if capability == "workflow":
        for field_name in ("inputs", "parameters", "variables"):
            if field_name in properties:
                properties[field_name]["propertyNames"] = {
                    "pattern": PARAMETER_NAME_PATTERN.pattern
                }
    if capability == "workflow" and "parameters" in properties:
        parameter_schema = _raw_schema(WorkflowParameterConfig)
        _apply_runtime_constraints("parameter", parameter_schema)
        properties["parameters"]["additionalProperties"] = parameter_schema
    if capability == "workflow" and "inputs" in properties:
        input_schema = _raw_schema(WorkflowParameterConfig)
        _apply_runtime_constraints("parameter", input_schema)
        properties["inputs"]["additionalProperties"] = input_schema
    if capability == "workflow" and "variables" in properties:
        properties["variables"]["additionalProperties"] = {
            "anyOf": [
                _raw_schema(WorkflowVariableConfig),
                {"type": ["string", "number", "integer", "boolean", "array", "object", "null"]},
            ]
        }
    if capability == "workflow" and "webhooks" in properties:
        properties["webhooks"]["propertyNames"] = {"pattern": WORKFLOW_ID_PATTERN.pattern}
        properties["webhooks"]["additionalProperties"] = _webhook_schema()
    if capability == "subflow" and "inputs" in properties:
        properties["inputs"]["propertyNames"] = {"pattern": PARAMETER_NAME_PATTERN.pattern}
        parameter_schema = _raw_schema(WorkflowParameterConfig)
        _apply_runtime_constraints("parameter", parameter_schema)
        properties["inputs"]["additionalProperties"] = parameter_schema
    if capability == "parameter":
        schema.setdefault("allOf", []).append(
            {
                "if": {"properties": {"type": {"const": "enum"}}, "required": ["type"]},
                "then": {"properties": {"choices": {"minItems": 1}}},
            }
        )


def capability_schema(capability: str) -> dict[str, Any]:
    sources: dict[str, type[BaseModel] | Any] = {
        "workflow": WorkflowConfig,
        "trigger.schedule": ScheduleConfig,
        "trigger.watch": WatchConfig,
        "parameter": WorkflowParameterConfig,
        "variable": WorkflowVariableConfig,
        "subflow": WorkflowComponentConfig,
        "resource": ResourceLimits,
        "filesystem_access": FilesystemAccessEntry,
        "fan_source": FanSource,
    }
    if capability == "agent":
        schema = _agent_schema()
    elif capability == "node":
        schema = _node_schema()
    elif capability == "edge":
        schema = _edge_schema()
    elif capability == "trigger.webhook":
        schema = _webhook_schema()
    elif capability in sources:
        schema = _raw_schema(sources[capability])
    elif capability == "document":
        schema = _document_schema()
    else:
        raise KeyError(f"Unsupported capability: {capability}")
    _apply_runtime_constraints(capability, schema)
    return _finish_schema(schema, capability)


CAPABILITY_IDS = (
    "agent",
    "document",
    "edge",
    "fan_source",
    "filesystem_access",
    "node",
    "parameter",
    "resource",
    "subflow",
    "trigger.schedule",
    "trigger.watch",
    "trigger.webhook",
    "workflow",
)


def _document_schema() -> dict[str, Any]:
    workflow = capability_schema("workflow")
    component = capability_schema("subflow")
    agent = capability_schema("agent")
    node = capability_schema("node")
    edge = capability_schema("edge")
    for nested in (workflow, component, agent, node, edge):
        nested.pop("$schema", None)
        nested.pop("$id", None)
    return {
        "title": "Raticode TOML authoring document",
        "type": "object",
        "properties": {
            "workflow": workflow,
            "component": component,
            "agents": {"type": "object", "additionalProperties": agent},
            "nodes": {"type": "array", "items": node},
            "edges": {"type": "array", "items": edge},
        },
        "required": ["workflow"],
        "additionalProperties": False,
    }


def _example_value(annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        return _example_value(args[0])
    if origin is Literal:
        value = args[0]
        return getattr(value, "value", value)
    if origin in (Union, UnionType):
        return _example_value(next(arg for arg in args if arg is not type(None)))
    if origin is list:
        return []
    if origin is dict:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _minimal_example(annotation)
    if annotation is Path:
        return "example-path"
    if annotation is str:
        return "value"
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is bool:
        return False
    return {}


def _minimal_example(model: type[BaseModel]) -> dict[str, Any]:
    example: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if field.is_required():
            output_name = field.serialization_alias or field.alias or name
            example[output_name] = _example_value(field.annotation)
    return example


def _operation_contract(name: str) -> dict[str, Any]:
    operation_example = _minimal_example(OPERATION_MODELS[name])
    TypeAdapter(Operation).validate_python(operation_example)
    example = {"id": f"example-{name.replace('_', '-')}", **operation_example}
    deprecations = [
        {
            "field_id": "node.on_failure",
            "replacement": "edges.*.condition",
            "message": "Use explicit conditional edges instead.",
        }
    ]
    if name == "agent":
        deprecations.extend(
            [
                {
                    "field_id": "operation.agent.fan_source",
                    "replacement": "operation.loop.source",
                    "message": "Fan-out belongs on a loop operation.",
                },
                {
                    "field_id": "operation.agent.dynamic_count",
                    "replacement": "operation.loop.source",
                    "message": "Dynamic fan-out belongs on a loop operation.",
                },
            ]
        )
    return {
        "id": f"operation.{name}",
        "type": name,
        "schema": _finish_schema(_node_schema_for_operation(name), f"operation.{name}"),
        "minimal_example": example,
        "deprecations": deprecations,
    }


REFERENCE_SUPPORT: dict[str, Any] = {
    "syntax": "{{namespace.path}}",
    "namespaces": REFERENCE_NAMESPACE_CAPABILITIES,
    "default_field_modes": ["literal"],
    "modes": {
        "literal": "The field is used as written.",
        "interpolation": "References may appear within a string.",
        "exact_typed_reference": "A reference-only value preserves its runtime type.",
    },
    "field_rules": [
        {"field_pattern": field, "accepts": list(modes)}
        for field, modes in sorted(REFERENCE_FIELD_CAPABILITIES.items())
    ],
}


REPRESENTATIVE_EXAMPLES = [
    {
        "id": "example.safe-authoring",
        "description": (
            "Parameters, environment interpolation, input mapping, a loop binding, "
            "condition, and subflow."
        ),
        "workflow": {
            "workflow": {
                "id": "authoring-example",
                "name": "Authoring example",
                "parameters": {"topic": {"type": "string", "required": True}},
            },
            "nodes": [
                {
                    "id": "prepare",
                    "type": "bash_command",
                    "command": "printf '%s' '{{params.topic}}'",
                    "env": {"TOPIC": "{{params.topic}}"},
                },
                {"id": "items", "type": "loop", "source": {"type": "count", "count": 2}},
                {
                    "id": "child",
                    "type": "subflow",
                    "component_id": "safe-child",
                    "parameter_bindings": {"topic": "{{params.topic}}"},
                    "inputs": {"item": "{{item.index}}"},
                },
            ],
            "edges": [
                {"from": "prepare", "to": "items", "condition": "on_success"},
                {"from": "items", "to": "child"},
            ],
        },
    }
]


def cli_version() -> str:
    try:
        return importlib.metadata.version("gofer-flow")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+source"


def authoring_contract(
    *, operation: str | None = None, capability: str | None = None
) -> dict[str, Any]:
    metadata = {
        "cli_version": cli_version(),
        "schema_version": AUTHORING_SCHEMA_VERSION,
        "workflow_format_versions": WORKFLOW_FORMAT_VERSIONS,
    }
    if operation is not None:
        if operation not in OPERATION_MODELS:
            raise KeyError(f"Unsupported operation capability: {operation}")
        return {"metadata": metadata, "operation": _operation_contract(operation)}
    if capability is not None:
        return {
            "metadata": metadata,
            "capability": {"id": capability, "schema": capability_schema(capability)},
        }
    return {
        "metadata": metadata,
        "contract_id": "gofer.authoring",
        "schemas": {name: capability_schema(name) for name in CAPABILITY_IDS},
        "operations": [_operation_contract(name) for name in sorted(OPERATION_MODELS)],
        "references": REFERENCE_SUPPORT,
        "deprecations": [
            {
                "field_id": "node.on_failure",
                "replacement": "edges.*.condition",
                "message": "Use explicit conditional edges instead.",
            }
        ],
        "mutation_commands": [
            {
                "id": "mutation.workflow.add-node",
                "command": "gof workflow add-node",
                "operation_contract": "gof schema --operation <type>",
            },
            {
                "id": "mutation.workflow.add-edge",
                "command": "gof workflow add-edge",
                "schema_capability": "edge",
            },
        ],
        "examples": REPRESENTATIVE_EXAMPLES,
    }
