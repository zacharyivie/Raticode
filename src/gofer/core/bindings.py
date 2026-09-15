from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import networkx as nx

from gofer.core.graph import GraphNode
from gofer.core.references import (
    REFERENCE_FIELD_CAPABILITIES,
    ReferenceNamespace,
    parse_exact_reference,
)
from gofer.core.secrets import workflow_secret_readiness
from gofer.core.workflow import AgenticWorkflow

BindingStatus = Literal[
    "resolved",
    "runtime-bound",
    "optional",
    "invalid",
    "type-incompatible",
]

TEMPLATE_REFERENCE_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class BindingInspection:
    id: str
    destination_node: str
    destination_field: str
    expression: str
    namespace: str
    producer: str
    source_type: str
    destination_type: str
    resolution_phase: str
    status: BindingStatus
    mode: Literal["exact", "embedded"]
    coercion: str
    destination_layer: str
    consumer: str
    message: str | None = None
    suggestions: tuple[str, ...] = ()
    secret: bool = False
    readiness: Literal["present", "missing"] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "destinationNode": self.destination_node,
            "destinationField": self.destination_field,
            "expression": self.expression,
            "namespace": self.namespace,
            "producer": self.producer,
            "sourceType": self.source_type,
            "destinationType": self.destination_type,
            "resolutionPhase": self.resolution_phase,
            "status": self.status,
            "mode": self.mode,
            "coercion": self.coercion,
            "destinationLayer": self.destination_layer,
            "consumer": self.consumer,
            "secret": self.secret,
        }
        if self.message:
            payload["message"] = self.message
        if self.suggestions:
            payload["suggestions"] = list(self.suggestions)
        if self.readiness is not None:
            payload["readiness"] = self.readiness
        return payload


def binding_contract() -> dict[str, Any]:
    """Describe the interpolation layers without claiming to parse a shell."""
    return {
        "id": "gofer.bindings.v1",
        "statuses": [
            "resolved",
            "runtime-bound",
            "optional",
            "invalid",
            "type-incompatible",
        ],
        "layers": [
            {
                "id": "gofer-interpolation",
                "description": "Raticode resolves explicit references and {{...}} templates.",
            },
            {
                "id": "input-mapping",
                "description": "Node inputs retain native values for exact references.",
            },
            {
                "id": "generated-environment",
                "description": (
                    "env.* inputs and operation.env values become process environment variables."
                ),
            },
            {
                "id": "shell-expansion",
                "description": (
                    "The shell owns expressions such as ${FILE_NAME}; "
                    "Raticode does not parse them."
                ),
            },
        ],
    }


def inspect_workflow_bindings(
    workflow: AgenticWorkflow,
    *,
    workflow_path: Path | None = None,
    data_dir: Path | None = None,
) -> list[BindingInspection]:
    secret_readiness: dict[str, Literal["present", "missing"]] = {
        item.name: "present" if item.present else "missing"
        for item in workflow_secret_readiness(
            workflow,
            workflow_path=workflow_path,
            data_dir=data_dir,
        )
    }
    inspections: list[BindingInspection] = []
    for node in workflow.graph.nodes_in_order():
        for field, value, capabilities in _reference_fields(node):
            inspections.extend(
                _inspect_value(
                    workflow,
                    node,
                    field,
                    value,
                    capabilities,
                    secret_readiness,
                )
            )
    unique_inspections = {inspection.id: inspection for inspection in inspections}
    return sorted(
        unique_inspections.values(),
        key=lambda item: (item.destination_node, item.destination_field, item.expression),
    )


def environment_binding_issues(workflow: AgenticWorkflow) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for node in workflow.graph.nodes_in_order():
        operation_env = getattr(node.operation, "env", {})
        if not isinstance(operation_env, dict):
            operation_env = {}
        operation_uses_environment = str(node.operation.type) in {
            "bash_command",
            "python_script",
            "shell_script",
        }
        generated: dict[str, str] = {}
        for input_name in node.inputs:
            if not operation_uses_environment and not input_name.startswith("env."):
                continue
            if input_name == "stdin" or (
                input_name.startswith("stdin") and input_name[5:].isdigit()
            ):
                continue
            env_name = input_name[4:] if input_name.startswith("env.") else input_name
            field = f"inputs.{input_name}"
            if not env_name or not ENV_NAME_PATTERN.fullmatch(env_name):
                issues.append(
                    {
                        "code": "workflow.binding_env_name_invalid",
                        "nodeId": node.node_id,
                        "field": field,
                        "message": (
                            f"Node '{node.node_id}' maps '{input_name}' to invalid environment "
                            f"name '{env_name}'. Use [A-Za-z_][A-Za-z0-9_]*."
                        ),
                    }
                )
                continue
            generated[env_name] = field
        for env_name in operation_env:
            field = f"operation.env.{env_name}"
            if not ENV_NAME_PATTERN.fullmatch(str(env_name)):
                issues.append(
                    {
                        "code": "workflow.binding_env_name_invalid",
                        "nodeId": node.node_id,
                        "field": field,
                        "message": (
                            f"Node '{node.node_id}' declares invalid environment name "
                            f"'{env_name}'. Use [A-Za-z_][A-Za-z0-9_]*."
                        ),
                    }
                )
            if env_name in generated:
                issues.append(
                    {
                        "code": "workflow.binding_env_conflict",
                        "nodeId": node.node_id,
                        "field": field,
                        "message": (
                            f"Node '{node.node_id}' sets environment variable '{env_name}' from "
                            f"both {field} and {generated[env_name]}; the input mapping wins "
                            "at runtime."
                        ),
                    }
                )
    return issues


def _reference_fields(node: GraphNode) -> list[tuple[str, str, tuple[str, ...]]]:
    fields: list[tuple[str, str, tuple[str, ...]]] = []
    input_capabilities = REFERENCE_FIELD_CAPABILITIES["nodes.*.inputs.*"]
    fields.extend(
        (f"inputs.{key}", value, input_capabilities) for key, value in node.inputs.items()
    )
    if node.for_each is not None:
        fields.append(("for_each", node.for_each, REFERENCE_FIELD_CAPABILITIES["nodes.*.for_each"]))
    dumped = node.operation.model_dump(by_alias=True)
    for path, value in _iter_strings(dumped):
        if path == "type":
            continue
        pattern = f"nodes[type={node.operation.type.value}].{path}"
        capabilities = _field_capabilities(pattern)
        if capabilities is None and "{{" in value:
            capabilities = ("literal", "exact_typed_reference", "interpolation")
        if capabilities is not None:
            fields.append((f"operation.{path}", value, capabilities))
    return fields


def _field_capabilities(pattern: str) -> tuple[str, ...] | None:
    direct = REFERENCE_FIELD_CAPABILITIES.get(pattern)
    if direct is not None:
        return direct
    parts = pattern.split(".")
    for registered, capabilities in REFERENCE_FIELD_CAPABILITIES.items():
        registered_parts = registered.split(".")
        if len(parts) < len(registered_parts):
            continue
        if all(
            expected == "*" or expected == actual
            for expected, actual in zip(registered_parts, parts, strict=False)
        ):
            return capabilities
    return None


def _iter_strings(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(prefix, value)]
    if isinstance(value, dict):
        result: list[tuple[str, str]] = []
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.extend(_iter_strings(nested, path))
        return result
    if isinstance(value, list):
        result = []
        for index, nested in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            result.extend(_iter_strings(nested, path))
        return result
    return []


def _inspect_value(
    workflow: AgenticWorkflow,
    node: GraphNode,
    field: str,
    value: str,
    capabilities: tuple[str, ...],
    secret_readiness: dict[str, Literal["present", "missing"]],
) -> list[BindingInspection]:
    matches = list(TEMPLATE_REFERENCE_PATTERN.finditer(value))
    exact_expression = parse_exact_reference(value)
    exact_wrapped = bool(matches) and exact_expression is not None and len(matches) == 1
    unsupported_embedded = False
    if matches:
        mode: Literal["exact", "embedded"] = (
            "exact" if exact_wrapped and "exact_typed_reference" in capabilities else "embedded"
        )
        unsupported_embedded = mode == "embedded" and "interpolation" not in capabilities
        expressions = (
            [exact_expression]
            if mode == "exact" and exact_expression is not None
            else [match.group(1).strip() for match in matches]
        )
    elif "exact_typed_reference" in capabilities:
        candidate = exact_expression
        if candidate is None or not _looks_like_reference(candidate, workflow):
            return []
        mode = "exact"
        expressions = [candidate]
    else:
        return []
    expressions = list(dict.fromkeys(expressions))
    inspections = [
        _inspection(
            workflow,
            node,
            field,
            expression,
            mode,
            secret_readiness,
        )
        for expression in expressions
    ]
    if unsupported_embedded:
        return [
            replace(
                inspection,
                status="invalid",
                message="This field accepts exact references, not embedded templates.",
            )
            for inspection in inspections
        ]
    return inspections


def _looks_like_reference(
    expression: str,
    workflow: AgenticWorkflow,
) -> bool:
    # Unwrapped references are a compatibility shorthand for paths such as
    # ``step.output``. A bare namespace cannot select a runtime value and may
    # also be a valid literal, as with the agent memory setting ``run``.
    if "." not in expression:
        return False
    root = expression.split(".", 1)[0]
    known = {item.value for item in ReferenceNamespace}
    known.update(item.node_id for item in workflow.graph.nodes_in_order())
    if root in known:
        return True
    # Raw exact references have no delimiter that distinguishes them from dotted
    # filenames and domains. Only treat an unknown root as a reference when it is
    # plausibly a misspelling of a reserved namespace; {{...}} is always explicit.
    return bool(
        difflib.get_close_matches(
            root,
            [
                *[
                    item.value
                    for item in ReferenceNamespace
                    if item is not ReferenceNamespace.NODES
                ],
                *[item.node_id for item in workflow.graph.nodes_in_order()],
            ],
            n=1,
            cutoff=0.75,
        )
    )


def _inspection(
    workflow: AgenticWorkflow,
    node: GraphNode,
    field: str,
    expression: str,
    mode: Literal["exact", "embedded"],
    secret_readiness: dict[str, Literal["present", "missing"]],
) -> BindingInspection:
    parts = expression.split(".")
    namespace = parts[0] if parts else ""
    source_type = "unknown"
    producer = namespace or "unknown"
    phase = "run-start"
    status: BindingStatus = "runtime-bound"
    message: str | None = None
    suggestions: tuple[str, ...] = ()
    secret = namespace == ReferenceNamespace.SECRET
    readiness: Literal["present", "missing"] | None = None
    node_ids = {item.node_id for item in workflow.graph.nodes_in_order()}
    input_names = {key.split(".", 1)[0] for key in node.inputs}
    known_namespaces = {item.value for item in ReferenceNamespace}

    if namespace in {ReferenceNamespace.PARAMS, ReferenceNamespace.INPUTS}:
        producer = "workflow.inputs"
        parameter = workflow.config.declared_inputs.get(parts[1]) if len(parts) > 1 else None
        if parameter is None:
            status = "invalid"
            candidates = sorted(workflow.config.declared_inputs)
            suggestions = _suggest(parts[1] if len(parts) > 1 else "", candidates)
            message = f"Unknown workflow input '{'.'.join(parts[1:]) or '<missing>'}'."
        elif len(parts) != 2:
            status = "invalid"
            message = f"Parameter '{parts[1]}' is scalar and has no nested fields."
        else:
            source_type = str(parameter.type)
            if parameter.default is not None:
                status = "resolved"
                phase = "plan-time"
    elif namespace == ReferenceNamespace.VARS:
        producer = "workflow.variables"
        variable = workflow.config.variables.get(parts[1]) if len(parts) > 1 else None
        if variable is None:
            status = "invalid"
            candidates = sorted(workflow.config.variables)
            suggestions = _suggest(parts[1] if len(parts) > 1 else "", candidates)
            message = f"Unknown workflow variable '{'.'.join(parts[1:]) or '<missing>'}'."
        else:
            source_type = str(variable.type)
            if variable.initial is not None:
                status = "resolved"
                phase = "plan-time"
    elif namespace == ReferenceNamespace.TRIGGER:
        producer = "workflow.trigger"
        status = "optional"
        source_type = "unknown"
    elif namespace in {ReferenceNamespace.LOOP, ReferenceNamespace.ITEM, ReferenceNamespace.ITEMS}:
        producer = "loop-item"
        phase = "loop-item"
        if not _has_loop_context(workflow, node.node_id):
            status = "invalid"
            message = f"Namespace '{namespace}' is unavailable outside a loop or for_each context."
        else:
            source_type = _loop_source_type(parts)
    elif namespace == ReferenceNamespace.PREVIOUS:
        producer = "previous-predecessor"
        phase = "upstream-node-completion"
        if workflow.graph._graph.in_degree(node.node_id) == 0:
            status = "invalid"
            message = "Namespace 'previous' is unavailable because this node has no predecessor."
        else:
            source_type = _node_output_type(parts[1:])
            message, suggestions = _node_output_path_issue(parts[1:])
            if message:
                status = "invalid"
    elif namespace in node_ids and namespace not in known_namespaces:
        producer = namespace
        phase = "upstream-node-completion"
        if namespace == node.node_id or not nx.has_path(
            workflow.graph._graph, namespace, node.node_id
        ):
            status = "invalid"
            message = f"Producer node '{namespace}' cannot reach node '{node.node_id}'."
        else:
            source_type = _node_output_type(parts[1:])
            message, suggestions = _node_output_path_issue(parts[1:])
            if message:
                status = "invalid"
    elif namespace == ReferenceNamespace.NODES:
        producer = parts[1] if len(parts) > 1 else "unknown"
        phase = "upstream-node-completion"
        if len(parts) < 2 or parts[1] not in node_ids:
            status = "invalid"
            suggestions = _suggest(parts[1] if len(parts) > 1 else "", sorted(node_ids))
            message = f"Unknown producer node '{parts[1] if len(parts) > 1 else '<missing>'}'."
        elif parts[1] == node.node_id or not nx.has_path(
            workflow.graph._graph, parts[1], node.node_id
        ):
            status = "invalid"
            message = f"Producer node '{parts[1]}' cannot reach node '{node.node_id}'."
        else:
            source_type = _node_output_type(parts[2:])
            message, suggestions = _node_output_path_issue(parts[2:])
            if message:
                status = "invalid"
    elif namespace == ReferenceNamespace.WORKFLOW:
        producer = "workflow.metadata"
        phase = "plan-time"
        status = "resolved"
        fields = {"id", "name", "path"}
        if len(parts) != 2 or parts[1] not in fields:
            status = "invalid"
            suggestions = _suggest(parts[1] if len(parts) > 1 else "", sorted(fields))
            message = f"Unknown workflow field '{'.'.join(parts[1:]) or '<missing>'}'."
        else:
            source_type = "string"
    elif namespace == ReferenceNamespace.RUN:
        producer = "workflow.run"
        fields = {"id", "logPath", "approveCommand", "rejectCommand"}
        if len(parts) != 2 or parts[1] not in fields:
            status = "invalid"
            suggestions = _suggest(parts[1] if len(parts) > 1 else "", sorted(fields))
            message = f"Unknown run field '{'.'.join(parts[1:]) or '<missing>'}'."
        else:
            source_type = "string"
    elif namespace == ReferenceNamespace.SECRET:
        producer = "secret-store"
        source_type = "secret"
        if len(parts) != 2:
            status = "invalid"
            message = "Secret references must use secret.<name>."
        else:
            readiness = secret_readiness.get(parts[1], "missing")
    elif namespace in input_names:
        producer = f"node:{node.node_id}.inputs.{namespace}"
        source_type = "unknown"
        if mode == "exact":
            status = "invalid"
            message = "Node input aliases are available only inside interpolation fields."
    else:
        status = "invalid"
        candidates = sorted(
            (known_namespaces - {ReferenceNamespace.NODES}) | node_ids | input_names
        )
        suggestions = _suggest(namespace, candidates)
        message = f"Unknown reference namespace '{namespace}'."

    destination_type = _destination_type(node, field)
    if status not in {"invalid", "optional"} and not _types_compatible(
        source_type, destination_type, mode
    ):
        status = "type-incompatible"
        message = (
            f"Reference type '{source_type}' is incompatible with destination type "
            f"'{destination_type}'."
        )
    coercion = "none" if mode == "exact" else "string"
    destination_layer, consumer = _destination_boundary(node, field)
    if mode == "exact" and destination_type == "string" and _is_process_input(node, field):
        coercion = "string"
    # Secret names are identifiers, not contents. Values are never resolved or included here.
    stable_expression = expression
    return BindingInspection(
        id=f"binding:{node.node_id}:{field}:{stable_expression}",
        destination_node=node.node_id,
        destination_field=field,
        expression=stable_expression,
        namespace=namespace,
        producer=producer,
        source_type=source_type,
        destination_type=destination_type,
        resolution_phase=phase,
        status=status,
        mode=mode,
        coercion=coercion,
        destination_layer=destination_layer,
        consumer=consumer,
        message=message,
        suggestions=suggestions,
        secret=secret,
        readiness=readiness,
    )


def _suggest(value: str, candidates: list[str]) -> tuple[str, ...]:
    return tuple(difflib.get_close_matches(value, candidates, n=3, cutoff=0.45))


def _has_loop_context(workflow: AgenticWorkflow, node_id: str) -> bool:
    node = workflow.graph._nodes[node_id]
    if node.for_each:
        return True
    for candidate in workflow.graph.nodes_in_order():
        if str(candidate.operation.type) != "loop":
            continue
        if candidate.node_id != node_id and nx.has_path(
            workflow.graph._graph, candidate.node_id, node_id
        ):
            return True
    return False


def _loop_source_type(parts: list[str]) -> str:
    leaf = parts[-1] if parts else ""
    if leaf in {"index", "count"}:
        return "number"
    if leaf in {"path", "name", "content", "value"} or leaf.endswith("_name"):
        return "string"
    return "object"


def _node_output_type(parts: list[str]) -> str:
    if not parts:
        return "object"
    return {
        "output": "string",
        "success": "boolean",
        "value": "unknown",
        "data": "object",
        "error": "string",
        "text": "string",
        "items": "array",
        "exit_code": "number",
        "duration_seconds": "number",
        "skipped": "boolean",
    }.get(parts[0], "unknown")


def _node_output_path_issue(parts: list[str]) -> tuple[str | None, tuple[str, ...]]:
    fields = {
        "node_id",
        "type",
        "success",
        "text",
        "output",
        "value",
        "data",
        "items",
        "error",
        "exit_code",
        "duration_seconds",
        "skipped",
        "terminal_status",
        "loop_items",
        "loop_infinite",
        "loop_max_concurrency",
        "loop_fail_fast",
    }
    if not parts:
        return None, ()
    if parts[0] not in fields:
        return (
            f"Unknown producer output field '{parts[0]}'",
            _suggest(parts[0], sorted(fields)),
        )
    structured_fields = {"data", "items", "value", "loop_items"}
    if len(parts) > 1 and parts[0] not in structured_fields:
        return f"Producer output field '{parts[0]}' is scalar and has no nested fields", ()
    return None, ()


def _destination_type(node: GraphNode, field: str) -> str:
    if _is_process_input(node, field):
        return "string"
    if field == "for_each":
        return "array"
    if field in {"operation.source.count", "operation.dynamic_count"}:
        return "number"
    if field.startswith("operation.env.") or ".args." in field:
        return "string"
    if field in {
        "operation.command",
        "operation.working_dir",
        "operation.template",
        "operation.message",
        "operation.notification_title",
        "operation.method",
        "operation.url",
        "operation.body",
        "operation.title",
        "operation.webhook_url",
    }:
        return "string"
    return "any"


def _types_compatible(source: str, destination: str, mode: str) -> bool:
    if mode == "embedded" or destination in {"any", "string"} or source == "unknown":
        return True
    if destination == "number":
        return source == "number"
    if destination == "array":
        return source in {"array", "object"}
    return source == destination


def _is_process_input(node: GraphNode, field: str) -> bool:
    return field.startswith("inputs.") and str(node.operation.type) in {
        "bash_command",
        "python_script",
        "shell_script",
    }


def _destination_boundary(node: GraphNode, field: str) -> tuple[str, str]:
    if _is_process_input(node, field):
        input_name = field.removeprefix("inputs.")
        if input_name == "stdin":
            return "process-stdin", "process"
        if input_name.startswith("stdin") and input_name[5:].isdigit():
            return "process-arguments", "process-or-shell"
        return "generated-environment", "process-or-shell"
    if field.startswith("operation.env.") or field.startswith("inputs.env."):
        return "generated-environment", "process-or-shell"
    if field.startswith("inputs."):
        return "input-mapping", "operation"
    if field == "operation.command":
        return "gofer-interpolation", "shell"
    return "gofer-interpolation", "operation"
