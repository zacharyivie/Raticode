"""Validated Rattish IR loading and contract-neutral node dispatch."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import anyio
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from gofer.core.agent import Agent, AgentConfig, AgentResult
from gofer.core.approvals import (
    ApprovalRequest,
    ApprovalStore,
    MultiChannelNotificationAdapter,
    Notification,
    NotificationAdapter,
    wait_for_decision,
)
from gofer.core.http import HttpClient, HttpRequest, UrllibHttpClient, append_query_params
from gofer.core.llm_prompts import common_llm_task_prompt
from gofer.core.network_policy import NetworkPolicyViolation, validate_http_request_url
from gofer.core.operations import HttpRetryPolicy
from gofer.core.provider_profiles import (
    ResolvedProviderSettings,
    resolve_provider_settings,
    validate_provider_settings,
)
from gofer.core.resources import DEFAULT_RESOURCE_LIMITS, read_text_limited
from gofer.core.structured_output import (
    StructuredOutputError,
    parse_and_validate_output,
    structured_output_instruction,
)
from gofer.rattish.contracts import canonical_json_bytes, json_fingerprint
from gofer.rattish.ir_validation import (
    InvalidRattishIrError as InvalidRattishIrError,
)
from gofer.rattish.ir_validation import (
    ValidatedRattishIR,
    validate_ir_invariants,
    validate_ir_versions,
)
from gofer.rattish.project_paths import path_kind, project_path
from gofer.rattish.prompt_templates import render_prompt_template, render_template_value
from gofer.rattish.provider_contracts import ProviderContract
from gofer.rattish.provider_runtime import (
    default_provider_subscriptions,
    runtime_subscription_id,
)
from gofer.rattish.storage import migrate_legacy_directory, workflow_owned_directory
from gofer.subscriptions.base import Subscription
from gofer.utils.paths import get_data_dir
from gofer.utils.process import run_subprocess


class UnsupportedRattishRuntimeError(RuntimeError):
    """Raised when no runtime handler implements a compiled handler ID."""


class InvalidRattishWorkflowInputError(ValueError):
    """Raised when supplied workflow inputs do not satisfy the compiled interface."""


@dataclass(frozen=True, slots=True)
class RuntimeErrorInfo:
    kind: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NodeExecutionResult:
    node_id: str
    outcome: Literal["success", "allowed_failure", "failure"]
    output: Any
    error: RuntimeErrorInfo | None = None


@dataclass(frozen=True, slots=True)
class ResolvedBindings:
    local: dict[str, Any]
    environment: dict[str, str]
    stdin: bytes | None
    workflow_inputs: dict[str, Any] = field(default_factory=dict)
    sensitive_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    project_root: Path
    workflow_id: str
    run_id: str
    workflow_inputs: Mapping[str, Any]
    trigger_events: tuple[Mapping[str, Any], ...]
    node_outputs: Mapping[str, Any]
    node_statuses: Mapping[str, str] = field(default_factory=dict)
    node_errors: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    subscriptions: Mapping[str, Subscription] = field(default_factory=dict)
    data_dir: Path = field(default_factory=get_data_dir)
    agent_run_memory: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    max_file_read_bytes: int = DEFAULT_RESOURCE_LIMITS.max_file_read_bytes
    max_subprocess_output_bytes: int = DEFAULT_RESOURCE_LIMITS.max_subprocess_output_bytes
    trash_root: Path = field(default_factory=lambda: get_data_dir() / "trash")
    http_client: HttpClient = field(default_factory=UrllibHttpClient)
    notification_adapter: NotificationAdapter = field(
        default_factory=MultiChannelNotificationAdapter
    )
    approval_store: ApprovalStore | None = None
    handlers: NodeHandlerRegistry | None = None


@dataclass(frozen=True, slots=True)
class HandlerResult:
    success: bool
    output: Any
    error: RuntimeErrorInfo | None = None


RuntimeHandler = Callable[
    [Mapping[str, Any], RuntimeContext, ResolvedBindings], Awaitable[HandlerResult]
]


class NodeHandlerRegistry:
    """Runtime handlers keyed by the immutable handler ID stored in IR."""

    def __init__(self, handlers: Mapping[str, RuntimeHandler] | None = None) -> None:
        self._handlers = dict(handlers or {})

    def register(self, handler_id: str, handler: RuntimeHandler) -> None:
        if handler_id in self._handlers:
            raise ValueError(f"Runtime handler {handler_id!r} is already registered.")
        self._handlers[handler_id] = handler

    def require(self, handler_id: str) -> RuntimeHandler:
        try:
            return self._handlers[handler_id]
        except KeyError as exc:
            raise UnsupportedRattishRuntimeError(
                f"Runtime handler {handler_id!r} is not installed."
            ) from exc

    def supports(self, handler_id: str) -> bool:
        return handler_id in self._handlers


def load_ir(
    document: Mapping[str, Any],
    schema: Mapping[str, Any],
    contracts: Mapping[str, Mapping[str, Any]] | None = None,
    provider_contracts: Mapping[str, ProviderContract] | None = None,
    workflow_dependencies: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> ValidatedRattishIR:
    """Validate an untrusted IR document before returning an owned copy."""
    owned = json.loads(json.dumps(document))
    validate_ir_versions(owned)
    try:
        Draft202012Validator(schema).validate(owned)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "$"
        raise InvalidRattishIrError(f"Invalid Rattish IR at {location}: {exc.message}") from exc
    validate_ir_invariants(owned)
    if contracts is None:
        raise InvalidRattishIrError("Loading Rattish IR requires the installed node contracts.")
    for node in owned["nodes"]:
        contract = contracts.get(node["type"])
        if contract is None:
            raise InvalidRattishIrError(f"Node contract {node['type']!r} is not installed.")
        if contract["runtime_handler"] != node["runtime_handler"]:
            raise InvalidRattishIrError(
                f"Node {node['id']!r} runtime handler does not match its contract."
            )
        if (
            contract["contract_version"] != node["contract"]["version"]
            or json_fingerprint(contract) != node["contract"]["fingerprint"]
        ):
            raise InvalidRattishIrError(
                f"Node {node['id']!r} contract identity is stale or invalid."
            )
        try:
            Draft202012Validator(contract["configuration_schema"]).validate(node["configuration"])
        except ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "$"
            raise InvalidRattishIrError(
                f"Node {node['id']!r} configuration is invalid at {location}: {exc.message}"
            ) from exc
        _validate_materialized_configuration(node, contract)

    for node in owned["nodes"]:
        provider = node["resolutions"]["provider"]
        if provider is not None:
            if provider_contracts is None:
                raise InvalidRattishIrError(
                    "Loading provider-backed IR requires the installed provider contracts."
                )
            installed = provider_contracts.get(provider["provider_id"])
            if installed is None or (
                installed.version != provider["contract_version"]
                or installed.fingerprint != provider["contract_fingerprint"]
            ):
                raise InvalidRattishIrError(
                    f"Node {node['id']!r} provider contract is unavailable or stale."
                )

        workflow = node["resolutions"]["workflow"]
        if workflow is not None:
            if workflow_dependencies is None:
                raise InvalidRattishIrError(
                    "Loading workflow-backed IR requires the resolved child dependencies."
                )
            installed_workflow = workflow_dependencies.get(
                (workflow["source_kind"], workflow["source"])
            )
            if installed_workflow is None or (
                installed_workflow.get("interface_fingerprint") != workflow["interface_fingerprint"]
                or installed_workflow.get("compilation_fingerprint")
                != workflow["compilation_fingerprint"]
            ):
                raise InvalidRattishIrError(
                    f"Node {node['id']!r} child workflow dependency is unavailable or stale."
                )
    return ValidatedRattishIR._from_validated(cast(dict[str, Any], owned))


def _validate_materialized_configuration(
    node: Mapping[str, Any], contract: Mapping[str, Any]
) -> None:
    configuration = node["configuration"]
    for collection_name in ("defaults", "computed_defaults"):
        for field_name in contract.get(collection_name, {}):
            if field_name not in configuration:
                raise InvalidRattishIrError(
                    f"Node {node['id']!r} omits materialized configuration field {field_name!r}."
                )

    def visit(value: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
        properties = schema.get("properties", {})
        if schema.get("x-rattish-apply-property-defaults") is True:
            for name, child_schema in properties.items():
                if (
                    isinstance(child_schema, Mapping)
                    and child_schema.get("x-rattish-allow-absence") is not True
                    and name not in value
                ):
                    raise InvalidRattishIrError(
                        f"Node {node['id']!r} omits nested materialized field {name!r}."
                    )
        variants = schema.get("x-rattish-variant-defaults")
        discriminator = schema.get("x-rattish-variant-discriminator")
        if isinstance(variants, Mapping) and isinstance(discriminator, str):
            selected_defaults = variants.get(value.get(discriminator), {})
            if isinstance(selected_defaults, Mapping):
                for name in selected_defaults:
                    if name not in value:
                        raise InvalidRattishIrError(
                            f"Node {node['id']!r} omits variant field {name!r}."
                        )
        if not isinstance(properties, Mapping):
            return
        for name, child_schema in properties.items():
            child_value = value.get(name)
            if isinstance(child_value, Mapping) and isinstance(child_schema, Mapping):
                visit(child_value, child_schema)

    schema = contract.get("configuration_schema")
    if isinstance(configuration, Mapping) and isinstance(schema, Mapping):
        visit(configuration, schema)


def _require_validated_ir(ir: Mapping[str, Any]) -> None:
    if not isinstance(ir, ValidatedRattishIR):
        raise InvalidRattishIrError(
            "Runtime entry points require IR returned by the compiler or load_ir()."
        )


async def execute_node(
    ir: Mapping[str, Any],
    node_id: str,
    *,
    workflow_inputs: Mapping[str, Any] | None = None,
    trigger_events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
    node_outputs: Mapping[str, Any] | None = None,
    node_statuses: Mapping[str, str] | None = None,
    node_errors: Mapping[str, Mapping[str, Any]] | None = None,
    handlers: NodeHandlerRegistry | None = None,
    trash_root: Path | None = None,
    subscriptions: Mapping[str, Subscription] | None = None,
    data_dir: Path | None = None,
    agent_run_memory: dict[str, list[dict[str, str]]] | None = None,
    http_client: HttpClient | None = None,
    notification_adapter: NotificationAdapter | None = None,
    run_id: str | None = None,
    approval_store: ApprovalStore | None = None,
) -> NodeExecutionResult:
    """Resolve inputs, dispatch one IR node, and validate successful output."""
    _require_validated_ir(ir)
    node = _find_node(ir, node_id)
    resolved_data_dir = data_dir or get_data_dir()
    registry = handlers or DEFAULT_NODE_HANDLERS
    context = RuntimeContext(
        project_root=Path(ir["source"]["project_root"]),
        workflow_id=ir["workflow"]["id"],
        run_id=run_id or uuid.uuid4().hex,
        workflow_inputs=workflow_inputs or {},
        trigger_events=tuple(trigger_events or ()),
        node_outputs=node_outputs or {},
        node_statuses=node_statuses or {},
        node_errors=node_errors or {},
        subscriptions=(
            subscriptions if subscriptions is not None else default_provider_subscriptions()
        ),
        data_dir=resolved_data_dir,
        agent_run_memory=agent_run_memory if agent_run_memory is not None else {},
        trash_root=trash_root or resolved_data_dir / "trash",
        http_client=http_client or UrllibHttpClient(),
        notification_adapter=notification_adapter or MultiChannelNotificationAdapter(),
        approval_store=approval_store,
        handlers=registry,
    )
    bindings = ResolvedBindings(local={}, environment={}, stdin=None)
    try:
        bindings = _resolve_bindings(node, context)
        effective_node = dict(node)
        effective_node["configuration"] = _render_configuration(
            node["configuration"],
            bindings.local,
            skip={
                "prompt" if node["type"] == "agent" else "",
                "template" if node["type"] == "prompt-file" else "",
            },
        )
        handler = registry.require(node["runtime_handler"])
        timeout_ms = node["execution"]["timeout_ms"]
        if timeout_ms is None:
            handled = await handler(effective_node, context, bindings)
        else:
            with anyio.fail_after(timeout_ms / 1000):
                handled = await handler(effective_node, context, bindings)
    except TimeoutError as exc:
        timeout_ms = node["execution"]["timeout_ms"]
        handled = HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "timeout",
                "RATTISH_TIMEOUT",
                str(exc) or "Node timed out.",
                {"scope": "node", "timeout_ms": timeout_ms},
            ),
        )
    except (OSError, UnicodeError) as exc:
        handled = HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "filesystem",
                "RATTISH_RUNTIME_FILESYSTEM_ERROR",
                str(exc),
                {"exception": type(exc).__name__},
            ),
        )
    except (LookupError, ValueError) as exc:
        handled = HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "configuration",
                "RATTISH_RUNTIME_CONFIGURATION_ERROR",
                str(exc),
                {"exception": type(exc).__name__},
            ),
        )
    except Exception as exc:
        handled = HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "internal",
                "RATTISH_RUNTIME_INTERNAL_ERROR",
                str(exc) or f"{type(exc).__name__} raised without a message.",
                {"exception": type(exc).__name__},
            ),
        )

    if bindings.sensitive_values:
        handled = HandlerResult(
            handled.success,
            _redact_sensitive(handled.output, bindings.sensitive_values),
            _redact_runtime_error(handled.error, bindings.sensitive_values),
        )

    if handled.success:
        validation_error = next(
            Draft202012Validator(node["output"]["schema"]).iter_errors(handled.output), None
        )
        if validation_error is not None:
            handled = HandlerResult(
                False,
                {},
                RuntimeErrorInfo(
                    "output_validation",
                    "RATTISH_RUNTIME_OUTPUT_INVALID",
                    validation_error.message,
                    {"path": list(validation_error.absolute_path)},
                ),
            )

    if handled.success:
        outcome: Literal["success", "allowed_failure", "failure"] = "success"
    elif node["execution"]["allow_fail"]:
        outcome = "allowed_failure"
    else:
        outcome = "failure"
    return NodeExecutionResult(node_id, outcome, handled.output, handled.error)


async def execute_bash_node(
    ir: Mapping[str, Any],
    node_id: str,
    *,
    workflow_inputs: Mapping[str, Any] | None = None,
    node_outputs: Mapping[str, Any] | None = None,
) -> NodeExecutionResult:
    """Compatibility wrapper for the original Bash-only vertical slice API."""
    node = _find_node(ir, node_id)
    if node["runtime_handler"] != "raticode.bash_command":
        raise UnsupportedRattishRuntimeError(
            f"Node {node_id!r} does not use the Bash runtime handler."
        )
    return await execute_node(
        ir,
        node_id,
        workflow_inputs=workflow_inputs,
        node_outputs=node_outputs,
    )


async def _agent_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]
    resolution = node["resolutions"]["provider"]
    if resolution is None:
        raise ValueError("Agent IR has no provider resolution.")
    subscription_id = runtime_subscription_id(resolution["provider_id"])
    subscription = context.subscriptions.get(subscription_id)
    if subscription is None:
        return HandlerResult(
            False,
            "",
            RuntimeErrorInfo(
                "provider",
                "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE",
                f"Provider runtime {resolution['provider_id']!r} is unavailable.",
                {"provider": resolution["provider_id"]},
            ),
        )
    settings = resolve_provider_settings(
        agent_subscription=subscription_id,
        profile_name=configuration["profile"],
        operation_model=resolution["model"],
        operation_effort=resolution["effort"],
        data_dir=context.data_dir,
    )
    validate_provider_settings(settings)

    working_dir = project_path(context.project_root, configuration["working_dir"])
    prompt_path = (
        project_path(context.project_root, configuration["prompt_path"])
        if configuration["prompt_path"] is not None
        else None
    )
    agent_config = AgentConfig(
        agent_id=node["id"],
        subscription=subscription_id,
        working_dir=working_dir,
        profile=configuration["profile"],
        model=resolution["model"],
        effort=resolution["effort"],
        prompt_path=None,
        tools=list(configuration["tools"]),
        mcp_servers=list(configuration["mcp_servers"]),
        env=dict(configuration["env"]),
        extra_paths=[
            project_path(context.project_root, path) for path in configuration["extra_paths"]
        ],
    )
    if configuration["skill"]:
        prompt_override = f"/{configuration['skill'].strip().lstrip('/')}"
    else:
        prompt_template = (
            configuration["prompt"]
            if prompt_path is None
            else read_text_limited(
                prompt_path,
                encoding="utf-8",
                errors="strict",
                max_bytes=context.max_file_read_bytes,
            )
        )
        prompt_override = render_prompt_template(prompt_template, bindings.local)
    output_schema = node["output"]["schema"]
    structured = (
        configuration["output_schema"] is not None
        or configuration["output_schema_path"] is not None
    )
    prompt_suffix = structured_output_instruction(output_schema) if structured else None
    memory = _load_agent_memory(node["id"], configuration["memory"], context)
    result = await Agent(agent_config, subscription).run(
        {},
        prompt_override=prompt_override,
        prompt_suffix=prompt_suffix,
        memory=memory,
        max_output_bytes=context.max_subprocess_output_bytes,
        timeout=settings.timeout,
        provider_settings=settings,
    )
    if not result.success:
        message = result.message or result.output or f"Provider exited with {result.exit_code}."
        return HandlerResult(
            False,
            result.output,
            RuntimeErrorInfo(
                "provider",
                "RATTISH_RUNTIME_PROVIDER_ERROR",
                message,
                {
                    "provider": resolution["provider_id"],
                    "exit_code": result.exit_code,
                },
            ),
        )

    final_result = result
    output: Any = result.output
    if structured:
        output, final_result, error = await _structured_agent_output(
            result,
            output_schema,
            configuration["repair_attempts"],
            agent_config,
            subscription,
            settings,
            context,
        )
        if error is not None:
            return HandlerResult(False, result.output, error)
    if not bindings.sensitive_values:
        _remember_agent_result(node["id"], configuration["memory"], final_result, context)
    return HandlerResult(True, output)


async def _workflow_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.workflow_runtime import execute_workflow

    resolution = node["resolutions"]["workflow"]
    if resolution is None:
        raise ValueError("Workflow IR has no referenced workflow resolution.")
    try:
        if resolution["source_kind"] == "registry":
            from gofer.rattish.workspaces import find_registered_workflow

            registered = find_registered_workflow(
                resolution["source"], registry_dir=context.data_dir
            )
            child_path = registered.entrypoint
            child_project_root = registered.project_root
            child_id = registered.workflow_id
        else:
            child_path = project_path(context.project_root, resolution["source"])
            child_project_root = context.project_root
            child_id = resolution["workflow_id"]
        child = compile_rattish_file(
            child_path,
            data_dir=context.data_dir,
            workflow_id=child_id,
            project_root=child_project_root,
        )
    except (OSError, ValueError, RattishArtifactError, RattishError) as exc:
        diagnostics = (
            [item.to_json() for item in exc.diagnostics] if isinstance(exc, RattishError) else []
        )
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "child_workflow",
                "RATTISH_CHILD_WORKFLOW_COMPILE_FAILED",
                f"Referenced workflow {resolution['workflow_id']!r} no longer compiles.",
                {
                    "workflow_id": resolution["workflow_id"],
                    "diagnostics": diagnostics,
                    "reason": str(exc),
                },
            ),
        )
    actual_fingerprint = _workflow_interface_fingerprint(child.ir)
    if actual_fingerprint != resolution["interface_fingerprint"]:
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "child_workflow",
                "RATTISH_WORKFLOW_INTERFACE_CHANGED",
                "The referenced workflow interface changed after its caller was compiled.",
                {
                    "workflow_id": resolution["workflow_id"],
                    "expected": resolution["interface_fingerprint"],
                    "actual": actual_fingerprint,
                },
            ),
        )
    actual_compilation = child.ir["source"]["compilation_fingerprint"]
    if actual_compilation != resolution["compilation_fingerprint"]:
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "child_workflow",
                "RATTISH_WORKFLOW_DEPENDENCY_CHANGED",
                "The referenced workflow implementation changed after its caller was compiled.",
                {
                    "workflow_id": resolution["workflow_id"],
                    "expected": resolution["compilation_fingerprint"],
                    "actual": actual_compilation,
                },
            ),
        )
    try:
        result = await execute_workflow(
            child.ir,
            workflow_inputs=bindings.workflow_inputs,
            handlers=context.handlers,
            trash_root=context.trash_root,
            subscriptions=context.subscriptions,
            data_dir=context.data_dir,
            notification_adapter=context.notification_adapter,
        )
    except InvalidRattishWorkflowInputError as exc:
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "child_workflow",
                "RATTISH_WORKFLOW_INPUT_INVALID",
                str(exc),
                {"workflow_id": resolution["workflow_id"]},
            ),
        )
    if result.outcome == "pass":
        return HandlerResult(True, result.outputs)
    cause = result.error
    return HandlerResult(
        False,
        {},
        RuntimeErrorInfo(
            "child_workflow",
            cause.code if cause is not None else "RATTISH_CHILD_WORKFLOW_FAILED",
            (
                cause.message
                if cause is not None
                else f"Child workflow {resolution['workflow_id']!r} failed."
            ),
            {
                "workflow_id": resolution["workflow_id"],
                "cause": _error_document(cause) if cause is not None else None,
                "completed_runs": len(result.runs),
            },
        ),
    )


def _workflow_interface_fingerprint(ir: Mapping[str, Any]) -> str:
    return json_fingerprint(
        {
            "workflow_id": ir["workflow"]["id"],
            "interface_version": ir["workflow"]["interface_version"],
            "inputs": ir["workflow"]["inputs"],
            "outputs": [
                {"name": item["name"], "schema": item["schema"]}
                for item in ir["workflow"]["outputs"]
            ],
        }
    )


async def _structured_agent_output(
    result: AgentResult,
    schema: dict[str, Any],
    repair_attempts: int,
    agent_config: AgentConfig,
    subscription: Subscription,
    settings: ResolvedProviderSettings,
    context: RuntimeContext,
) -> tuple[Any, AgentResult, RuntimeErrorInfo | None]:
    validation_error = ""
    for attempt in range(repair_attempts + 1):
        try:
            return parse_and_validate_output(result.output, schema), result, None
        except StructuredOutputError as exc:
            validation_error = str(exc)
        if attempt == repair_attempts:
            break
        repair_prompt = (
            "Your previous response did not satisfy the required structured output. "
            "Correct it without repeating unrelated work. Return only the corrected JSON "
            "value.\n\nValidation error:\n"
            f"{validation_error}\n\nPrevious response:\n{result.output}\n\n"
            f"{structured_output_instruction(schema)}"
        )
        result = await Agent(agent_config, subscription).run(
            {},
            prompt_override=repair_prompt,
            max_output_bytes=context.max_subprocess_output_bytes,
            timeout=settings.timeout,
            provider_settings=settings,
        )
        if not result.success:
            validation_error = result.message or result.output or "Provider repair call failed."
    return (
        None,
        result,
        RuntimeErrorInfo(
            "output_validation",
            "RATTISH_RUNTIME_OUTPUT_INVALID",
            f"Structured output repair exhausted: {validation_error}",
            {"repair_attempts": repair_attempts},
        ),
    )


def _agent_memory_path(node_id: str, context: RuntimeContext) -> Path:
    safe_workflow = re.sub(r"[^a-zA-Z0-9_.-]+", "-", context.workflow_id)
    safe_node = re.sub(r"[^a-zA-Z0-9_.-]+", "-", node_id)
    legacy_directory = context.data_dir / "radish" / "agent-memory" / safe_workflow
    memory_directory = workflow_owned_directory(
        context.workflow_id,
        context.data_dir,
        "agent-memory",
    )
    if memory_directory is None:
        memory_directory = legacy_directory
    else:
        migrate_legacy_directory(legacy_directory, memory_directory)
    return memory_directory / f"{safe_node}.json"


def _load_agent_memory(node_id: str, mode: str, context: RuntimeContext) -> list[dict[str, str]]:
    if mode == "run":
        return list(context.agent_run_memory.get(node_id, []))
    if mode != "all":
        return []
    path = _agent_memory_path(node_id, context)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(document, list):
        return []
    return [
        {"role": str(item["role"]), "body": str(item["body"])}
        for item in document
        if isinstance(item, dict) and item.get("role") and item.get("body")
    ][-40:]


def _remember_agent_result(
    node_id: str, mode: str, result: AgentResult, context: RuntimeContext
) -> None:
    if mode not in {"run", "all"}:
        return
    turns = _load_agent_memory(node_id, mode, context)
    if result.current_prompt or result.prompt:
        turns.append({"role": "user", "body": result.current_prompt or result.prompt or ""})
    if result.message or result.output:
        turns.append({"role": "assistant", "body": result.message or result.output})
    turns = turns[-40:]
    if mode == "run":
        context.agent_run_memory[node_id] = turns
        return
    path = _agent_memory_path(node_id, context)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(turns, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


async def _http_request_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]
    url = str(_binding_or_configuration(bindings, configuration, "url"))
    method = str(configuration["method"]).upper()
    headers_value = _binding_or_configuration(bindings, configuration, "headers")
    params_value = _binding_or_configuration(bindings, configuration, "params")
    if not isinstance(headers_value, Mapping) or not isinstance(params_value, Mapping):
        raise ValueError("HTTP headers and params must be objects.")
    headers = {str(key): str(value) for key, value in headers_value.items()}
    params = {str(key): str(value) for key, value in params_value.items()}
    url = append_query_params(url, params)
    allowlist = [str(value) for value in configuration["network_allowlist"]]
    try:
        validate_http_request_url(url, allowlist=allowlist)
    except NetworkPolicyViolation as exc:
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo("network", "RATTISH_HTTP_NETWORK_POLICY", str(exc), {"url": url}),
        )

    json_payload = _binding_or_configuration(bindings, configuration, "json")
    body_value = _binding_or_configuration(bindings, configuration, "body")
    if "json" in bindings.local and "body" in bindings.local:
        raise ValueError("HTTP bindings json and body are mutually exclusive.")
    if "json" in bindings.local:
        body_value = None
    elif "body" in bindings.local:
        json_payload = None
    body: bytes | None = None
    if json_payload is not None:
        body = canonical_json_bytes(json_payload)
        headers.setdefault("Content-Type", "application/json")
    elif body_value is not None:
        body = str(body_value).encode("utf-8")

    retry = configuration["retry"]
    attempts = int(retry["attempts"])
    timeout_seconds = _duration_seconds(configuration["request_timeout"])
    backoff_seconds = _duration_seconds(retry["backoff"], allow_zero=True)
    retry_statuses = {int(value) for value in retry["retry_on_statuses"]}
    response = None
    last_error: Exception | None = None
    completed_attempts = 0
    for attempt in range(1, attempts + 1):
        completed_attempts = attempt
        try:
            response = await context.http_client.send(
                HttpRequest(method, url, headers, body, timeout_seconds, allowlist)
            )
            last_error = None
        except Exception as exc:  # noqa: BLE001
            response = None
            last_error = exc
        if response is not None and not (attempt < attempts and response.status in retry_statuses):
            break
        if attempt < attempts and backoff_seconds:
            await anyio.sleep(backoff_seconds)

    if response is None:
        message = "HTTP request did not produce a response."
        if last_error is not None:
            message = f"HTTP request failed after {completed_attempts} attempt(s): {last_error}"
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "network",
                "RATTISH_HTTP_TRANSPORT_ERROR",
                message,
                {"attempts": completed_attempts},
            ),
        )

    body_text = response.body.decode("utf-8", errors="replace")
    parsed_json: Any = None
    json_error: json.JSONDecodeError | None = None
    if configuration["response_mode"] in {"auto", "json"} or configuration["output_mapping"]:
        try:
            parsed_json = json.loads(body_text) if body_text else None
        except json.JSONDecodeError as exc:
            if configuration["response_mode"] == "json":
                json_error = exc
    response_data = {
        "status": response.status,
        "headers": dict(response.headers),
        "body": body_text,
        "json": parsed_json,
    }
    try:
        selected = {
            name: _dotted_value(response_data, path)
            for name, path in configuration["output_mapping"].items()
        }
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "configuration",
                "RATTISH_HTTP_OUTPUT_MAPPING_INVALID",
                str(exc),
                {},
            ),
        )
    response_mode = configuration["response_mode"]
    if response_mode == "json":
        value: Any = parsed_json
    elif response_mode == "none":
        value = None
    else:
        value = body_text
    output = {
        **response_data,
        "value": value,
        "selected": selected,
        "url": url,
        "method": method,
        "attempts": completed_attempts,
    }
    if json_error is not None:
        return HandlerResult(
            False,
            output,
            RuntimeErrorInfo(
                "network",
                "RATTISH_HTTP_INVALID_JSON",
                f"Response body is not valid JSON: {json_error}",
                {"status": response.status},
            ),
        )
    if response.status not in {int(value) for value in configuration["expected_statuses"]}:
        return HandlerResult(
            False,
            output,
            RuntimeErrorInfo(
                "network",
                "RATTISH_HTTP_UNEXPECTED_STATUS",
                f"HTTP response status {response.status} was not expected.",
                {"status": response.status},
            ),
        )
    return HandlerResult(True, output)


async def _notification_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]

    def resolved(machine_name: str) -> Any:
        return _binding_or_configuration(
            bindings, configuration, machine_name.replace("_", "-"), machine_name
        )

    channel = str(resolved("channel")).lower()
    urgency = str(resolved("urgency")).lower()
    if channel not in {"desktop", "slack", "teams", "webhook", "email"}:
        raise ValueError(f"Unsupported notification channel: {channel}")
    if urgency not in {"low", "normal", "critical"}:
        raise ValueError(f"Unsupported notification urgency: {urgency}")
    headers_value = resolved("headers")
    email_to_value = resolved("email_to")
    if not isinstance(headers_value, Mapping):
        raise ValueError("Notification headers must be an object.")
    if not isinstance(email_to_value, list):
        raise ValueError("Notification email-to must be an array.")
    retry = configuration["retry"]
    notification = Notification(
        title=str(resolved("title")),
        body=str(resolved("body")),
        channel=channel,
        urgency=urgency,
        webhook_url=_optional_string(resolved("webhook_url")),
        headers={str(key): str(value) for key, value in headers_value.items()},
        payload=resolved("payload"),
        email_from=_optional_string(resolved("email_from")),
        email_to=[str(value) for value in email_to_value],
        smtp_host=_optional_string(resolved("smtp_host")),
        smtp_port=int(resolved("smtp_port")),
        smtp_username=_optional_string(resolved("smtp_username"), keep_empty=True),
        smtp_password=_optional_string(resolved("smtp_password"), keep_empty=True),
        smtp_starttls=bool(resolved("smtp_starttls")),
        timeout_seconds=_duration_seconds(configuration["request_timeout"]),
        retry=HttpRetryPolicy(
            attempts=int(retry["attempts"]),
            backoff_seconds=_duration_seconds(retry["backoff"], allow_zero=True),
            retry_on_statuses=[int(value) for value in retry["retry_on_statuses"]],
        ),
        expected_statuses=[int(value) for value in configuration["expected_statuses"]],
        network_allowlist=[str(value) for value in configuration["network_allowlist"]],
    )
    try:
        await context.notification_adapter.send(notification)
    except ValueError as exc:
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                "configuration",
                "RATTISH_NOTIFICATION_CONFIGURATION_INVALID",
                str(exc),
                {"channel": channel},
            ),
        )
    except Exception as exc:  # noqa: BLE001
        kind = "configuration" if channel == "desktop" else "network"
        return HandlerResult(
            False,
            {},
            RuntimeErrorInfo(
                kind,
                "RATTISH_NOTIFICATION_SEND_FAILED",
                str(exc) or f"Failed to send {channel} notification.",
                {"channel": channel},
            ),
        )
    return HandlerResult(
        True,
        {
            "sent": True,
            "title": notification.title,
            "body": notification.body,
            "channel": channel,
            "urgency": urgency,
        },
    )


async def _approval_gate_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]
    message = str(_binding_or_configuration(bindings, configuration, "message"))
    subject_value = _binding_or_configuration(bindings, configuration, "subject")
    subject = None if subject_value is None else str(subject_value)
    timeout_value = configuration["decision_timeout"]
    timeout_seconds = _duration_seconds(timeout_value) if timeout_value is not None else None
    store = context.approval_store or ApprovalStore(context.data_dir)
    request = ApprovalRequest(
        workflow_id=context.workflow_id,
        run_id=context.run_id,
        node_id=node["id"],
        message=message,
        approvers=[str(value) for value in configuration["approvers"]],
        timeout_seconds=timeout_seconds,
        timeout_decision=configuration["timeout_decision"],
        subject_manifest_id=subject,
    )
    store.create_or_update(request)
    if configuration["notify"]:
        try:
            await context.notification_adapter.send(
                Notification(title=configuration["notification_title"], body=message)
            )
        except Exception:  # noqa: BLE001
            # Notification is best-effort. The persisted approval remains actionable.
            pass
    decision = await wait_for_decision(
        store,
        context.workflow_id,
        context.run_id,
        node["id"],
        timeout_seconds=timeout_seconds,
    )
    if decision is None:
        decision_value = "timeout" if configuration["timeout_decision"] == "timeout" else "rejected"
        decided = store.decide(
            context.workflow_id,
            context.run_id,
            node["id"],
            cast(Literal["approved", "rejected", "timeout"], decision_value),
            decided_by="raticode",
            notes=f"Timed out after {timeout_seconds} seconds",
        ).decision
        assert decided is not None
        decision = decided
    output = {
        "decision": decision.decision,
        "approved": decision.decision == "approved",
        "decided_by": decision.decided_by,
        "notes": decision.notes,
        "message": message,
        "subject": subject,
    }
    if output["approved"]:
        return HandlerResult(True, output)
    return HandlerResult(
        False,
        output,
        RuntimeErrorInfo(
            "timeout" if decision.decision == "timeout" else "configuration",
            (
                "RATTISH_APPROVAL_TIMED_OUT"
                if decision.decision == "timeout"
                else "RATTISH_APPROVAL_NOT_GRANTED"
            ),
            f"Approval was {decision.decision}.",
            {"decision": decision.decision, "decided_by": decision.decided_by},
        ),
    )


async def _common_llm_task_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]
    resolution = node["resolutions"]["provider"]
    if resolution is None:
        raise ValueError("Common LLM task IR has no provider resolution.")
    subscription_id = runtime_subscription_id(resolution["provider_id"])
    subscription = context.subscriptions.get(subscription_id)
    if subscription is None:
        return HandlerResult(
            False,
            "",
            RuntimeErrorInfo(
                "provider",
                "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE",
                f"Provider runtime {resolution['provider_id']!r} is unavailable.",
            ),
        )
    settings = resolve_provider_settings(
        agent_subscription=subscription_id,
        profile_name=configuration["profile"],
        operation_model=resolution["model"],
        operation_effort=resolution["effort"],
        data_dir=context.data_dir,
    )
    validate_provider_settings(settings)
    agent_config = AgentConfig(
        agent_id=node["id"],
        subscription=subscription_id,
        working_dir=project_path(context.project_root, configuration["working_dir"]),
        profile=configuration["profile"],
        model=resolution["model"],
        effort=resolution["effort"],
    )
    target = str(_binding_or_configuration(bindings, configuration, "target"))
    instructions = str(_binding_or_configuration(bindings, configuration, "instructions"))
    prompt = common_llm_task_prompt(configuration["task"], target, instructions)
    structured = (
        configuration["output_schema"] is not None
        or configuration["output_schema_path"] is not None
    )
    output_schema = node["output"]["schema"]
    result = await Agent(agent_config, subscription).run(
        dict(bindings.local),
        prompt_override=prompt,
        prompt_suffix=structured_output_instruction(output_schema) if structured else None,
        memory=_load_agent_memory(node["id"], configuration["memory"], context),
        max_output_bytes=context.max_subprocess_output_bytes,
        timeout=settings.timeout,
        provider_settings=settings,
    )
    if not result.success:
        return HandlerResult(
            False,
            result.output,
            RuntimeErrorInfo(
                "provider",
                "RATTISH_RUNTIME_PROVIDER_ERROR",
                result.message or result.output or f"Provider exited with {result.exit_code}.",
            ),
        )
    output: Any = result.output
    final_result = result
    if structured:
        output, final_result, error = await _structured_agent_output(
            result,
            output_schema,
            configuration["repair_attempts"],
            agent_config,
            subscription,
            settings,
            context,
        )
        if error is not None:
            return HandlerResult(False, result.output, error)
    _remember_agent_result(node["id"], configuration["memory"], final_result, context)
    return HandlerResult(True, output)


def _token_vector(text: str) -> dict[str, float]:
    counts: dict[str, float] = {}
    for token in re.findall(r"[A-Za-z0-9_]{2,}", text.lower()):
        key = hashlib.blake2b(token.encode("utf-8"), digest_size=4).hexdigest()
        counts[key] = counts.get(key, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in counts.values()))
    return {key: value / norm for key, value in counts.items()} if norm else counts


def _require_local_vector_strategy(configuration: Mapping[str, Any]) -> None:
    if (
        configuration["embedding_strategy"] != "hash_token_v1"
        or configuration["search_strategy"] != "cosine_v1"
    ):
        raise ValueError(
            "Unsupported local vector strategy: "
            f"embedding={configuration['embedding_strategy']!r}, "
            f"search={configuration['search_strategy']!r}"
        )


async def _local_vectorize_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    del bindings
    configuration = node["configuration"]
    _require_local_vector_strategy(configuration)
    source_path = project_path(context.project_root, configuration["source_path"])
    index_path = project_path(context.project_root, configuration["index_path"])
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    iterator = (
        source_path.rglob(configuration["glob"])
        if source_path.is_dir() and configuration["recursive"]
        else source_path.glob(configuration["glob"])
        if source_path.is_dir()
        else iter([source_path])
    )
    files = sorted(path for path in iterator if path.is_file())
    if len(files) > DEFAULT_RESOURCE_LIMITS.max_files_scanned:
        raise ValueError(
            "local-vectorize scanned file limit exceeded: "
            f"{len(files)} > {DEFAULT_RESOURCE_LIMITS.max_files_scanned}"
        )
    mode = configuration["mode"]
    existing_document: Mapping[str, Any] | None = None
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, Mapping):
                existing_document = loaded
        except json.JSONDecodeError:
            existing_document = None
    expected_metadata = {
        "source_root": str(source_path),
        "glob": configuration["glob"],
        "recursive": configuration["recursive"],
        "chunk_size": configuration["chunk_size"],
        "chunk_overlap": configuration["chunk_overlap"],
        "encoding": configuration["encoding"],
        "embedding_strategy": configuration["embedding_strategy"],
        "search_strategy": configuration["search_strategy"],
    }
    reusable_by_path: dict[str, list[dict[str, Any]]] = {}
    if mode == "incremental" and existing_document is not None:
        existing_entries = existing_document.get("entries")
        if existing_document.get("metadata") == expected_metadata and isinstance(
            existing_entries, list
        ):
            for entry in existing_entries:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    reusable_by_path.setdefault(entry["path"], []).append(entry)
    entries: list[dict[str, Any]] = []
    aggregate_bytes = 0
    overlap = configuration["chunk_overlap"]
    chunk_size = configuration["chunk_size"]
    if overlap >= chunk_size:
        raise ValueError("chunk-overlap must be smaller than chunk-size.")
    for path in files:
        stat = path.stat()
        reusable = reusable_by_path.get(str(path), [])
        if reusable and all(
            entry.get("metadata") == {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            for entry in reusable
        ):
            entries.extend(json.loads(json.dumps(reusable)))
            continue
        aggregate_bytes += stat.st_size
        if aggregate_bytes > DEFAULT_RESOURCE_LIMITS.max_aggregate_read_bytes:
            raise ValueError("local-vectorize aggregate input limit exceeded.")
        text = read_text_limited(
            path,
            max_bytes=context.max_file_read_bytes,
            encoding=configuration["encoding"],
        )
        start = 0
        chunk_number = 0
        while start < len(text) or (not text and chunk_number == 0):
            chunk = text[start : start + chunk_size]
            entries.append(
                {
                    "path": str(path),
                    "chunk": chunk_number,
                    "text": chunk,
                    "vector": _token_vector(chunk),
                    "metadata": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
                }
            )
            chunk_number += 1
            if not text:
                break
            start += chunk_size - overlap
    document = {
        "version": 2,
        "metadata": expected_metadata,
        "entries": entries,
    }
    current = existing_document == document
    serialized = json.dumps(document, sort_keys=True)
    if len(serialized.encode("utf-8")) > DEFAULT_RESOURCE_LIMITS.max_vector_index_bytes:
        raise ValueError("local-vectorize index size limit exceeded.")
    if mode != "validate" and (not current or mode == "full"):
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(serialized, encoding="utf-8")
    status = "current" if current else "stale" if mode == "validate" else "updated"
    return HandlerResult(
        True,
        {
            "source_path": str(source_path),
            "index_path": str(index_path),
            "mode": mode,
            "status": status,
            "current": current,
            "file_count": len(files),
            "chunk_count": len(entries),
        },
    )


async def _local_search_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]
    _require_local_vector_strategy(configuration)
    index_path = project_path(context.project_root, configuration["index_path"])
    index = json.loads(
        read_text_limited(
            index_path,
            max_bytes=DEFAULT_RESOURCE_LIMITS.max_vector_index_bytes,
        )
    )
    if not isinstance(index, Mapping) or not isinstance(index.get("entries"), list):
        raise ValueError(f"Invalid local vector index: {index_path}")
    metadata = index.get("metadata", {})
    if isinstance(metadata, Mapping):
        strategy_configuration = {
            "embedding_strategy": metadata.get(
                "embedding_strategy", configuration["embedding_strategy"]
            ),
            "search_strategy": metadata.get("search_strategy", configuration["search_strategy"]),
        }
        _require_local_vector_strategy(strategy_configuration)
    query = str(_binding_or_configuration(bindings, configuration, "query"))
    query_vector = _token_vector(query)
    ranked: list[tuple[float, Mapping[str, Any]]] = []
    for entry in index.get("entries", []):
        vector = entry.get("vector", {})
        score = sum(value * float(vector.get(key, 0.0)) for key, value in query_vector.items())
        if score >= configuration["score_threshold"]:
            ranked.append((score, entry))
    ranked.sort(
        key=lambda item: (
            -item[0],
            str(item[1].get("path", "")),
            int(item[1].get("chunk", 0)),
        )
    )
    results: list[dict[str, Any]] = []
    for score, entry in ranked[: configuration["top_k"]]:
        item: dict[str, Any] = {
            "score": round(score, 4),
            "path": entry.get("path"),
            "chunk": entry.get("chunk"),
            "text": entry.get("text", ""),
        }
        if configuration["include_snippets"]:
            item["snippet"] = entry.get("text", "")
        if configuration["include_file_metadata"]:
            item["metadata"] = entry.get("metadata", {})
        results.append(item)
    return HandlerResult(
        True,
        {
            "query": query,
            "index_path": str(index_path),
            "count": len(results),
            "results": results,
        },
    )


def _tabular_loop_items(path: Path, context: RuntimeContext) -> list[dict[str, Any]]:
    if path.stat().st_size > context.max_file_read_bytes:
        raise ValueError("Loop tabular source exceeds the per-file read limit.")
    if path.suffix.lower() == ".jsonl":
        items = [
            dict(json.loads(line))
            for line in read_text_limited(path, max_bytes=context.max_file_read_bytes).splitlines()
            if line.strip()
        ]
    elif path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as stream:
            items = [dict(row) for row in csv.DictReader(stream)]
    elif path.suffix.lower() == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise ValueError("Loop .xlsx sources require the xlsx optional dependency.") from exc
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            rows = sheet.iter_rows(values_only=True)
            headers = [str(value) for value in next(rows)]
            items = [dict(zip(headers, row, strict=False)) for row in rows]
        finally:
            workbook.close()
    else:
        raise ValueError("Loop tabular sources support .jsonl, .csv, and .xlsx files.")
    if len(items) > DEFAULT_RESOURCE_LIMITS.max_fanout_items:
        raise ValueError("Loop tabular source exceeds the fan-out item limit.")
    return items


async def _loop_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    del bindings
    source = node["configuration"]["source"]
    source_type = source["type"]
    items: list[dict[str, Any]]
    if source_type == "count":
        if source["count"] > DEFAULT_RESOURCE_LIMITS.max_fanout_items:
            raise ValueError("Loop count exceeds the fan-out item limit.")
        items = [{"index": index} for index in range(source["count"])]
    elif source_type == "tabular":
        items = _tabular_loop_items(project_path(context.project_root, source["path"]), context)
    elif source_type == "directory":
        root = project_path(context.project_root, source["path"])
        # Path.glob can silently return no matches for a missing/unreadable root.
        # Open it first so OS failures remain errors, while an empty directory succeeds.
        with os.scandir(root):
            pass
        items = []
        paths = sorted(item for item in root.glob(source["glob"]) if item.is_file())
        if len(paths) > DEFAULT_RESOURCE_LIMITS.max_fanout_items:
            raise ValueError("Loop directory source exceeds the fan-out item limit.")
        aggregate_bytes = 0
        for path in paths:
            item: dict[str, Any] = {
                "path": str(path),
                "name": path.name,
                "directory": str(path.parent),
            }
            if source["include_content"]:
                aggregate_bytes += path.stat().st_size
                if aggregate_bytes > DEFAULT_RESOURCE_LIMITS.max_aggregate_read_bytes:
                    raise ValueError("Loop directory content exceeds the aggregate read limit.")
                item["file_content"] = read_text_limited(
                    path, max_bytes=context.max_file_read_bytes
                )
            items.append(item)
    elif source_type == "trigger-events":
        items = []
        for index, value in enumerate(context.trigger_events):
            item = dict(value)
            item.setdefault("index", index)
            item.setdefault("event_json", json.dumps(value, sort_keys=True))
            event_path = item.get("path")
            if source["include_content"] and isinstance(event_path, str):
                path = project_path(context.project_root, event_path)
                if path.is_file():
                    item["file_content"] = read_text_limited(
                        path, max_bytes=context.max_file_read_bytes
                    )
            items.append(item)
    elif source_type == "infinite":
        items = []
    else:
        raise ValueError(f"Unsupported loop source type: {source_type}")
    return HandlerResult(
        True,
        {
            "items": items,
            "infinite": source_type == "infinite",
            "max_concurrency": source["max_concurrency"],
            "fail_fast": source["fail_fast"],
        },
    )


async def _break_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    del context, bindings
    return HandlerResult(
        True,
        {
            "loop": node["configuration"]["loop"],
            "message": node["configuration"]["message"],
        },
    )


def _optional_string(value: Any, *, keep_empty: bool = False) -> str | None:
    if value is None:
        return None
    converted = str(value)
    return converted if converted or keep_empty else None


def _duration_seconds(value: str, *, allow_zero: bool = False) -> float:
    match = re.fullmatch(r"(0|[1-9][0-9]*)(ms|s|m|h|d)", value)
    if match is None or (match.group(1) == "0" and not allow_zero):
        raise ValueError(f"Invalid duration: {value!r}")
    factors = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    return int(match.group(1)) * factors[match.group(2)]


def _dotted_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


async def _bash_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]
    environment = dict(configuration["env"])
    environment.update(bindings.environment)
    working_dir = configuration["working_dir"]
    cwd = (
        context.project_root
        if working_dir is None
        else _resolve_project_path(context.project_root, working_dir)
    )
    timeout_ms = node["execution"]["timeout_ms"]
    returncode, stdout, stderr = await run_subprocess(
        _shell_command_args(configuration["command"]),
        cwd=cwd,
        env=environment,
        stdin=bindings.stdin,
        timeout=None if timeout_ms is None else timeout_ms / 1000,
        max_output_bytes=context.max_subprocess_output_bytes,
    )
    output = {"stdout": stdout, "stderr": stderr, "exit_code": returncode}
    if returncode == 0:
        return HandlerResult(True, output)
    return HandlerResult(
        False,
        output,
        RuntimeErrorInfo(
            "command",
            "RATTISH_RUNTIME_COMMAND_FAILED",
            stderr or f"Command exited with status {returncode}.",
            {"exit_code": returncode},
        ),
    )


async def _read_file_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    _ = bindings
    configuration = node["configuration"]
    path = _resolve_project_path(context.project_root, configuration["path"])
    content = read_text_limited(
        path,
        encoding=configuration["encoding"],
        errors=configuration["errors"],
        max_bytes=context.max_file_read_bytes,
    )
    return HandlerResult(
        True,
        {
            "content": content,
            "path": str(path),
            "file_name": path.name,
            "file_stem": path.stem,
            "file_extension": path.suffix,
            "directory": str(path.parent),
        },
    )


async def _file_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    _ = bindings
    path = project_path(context.project_root, node["configuration"]["path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    return HandlerResult(
        True,
        {
            "path": str(path),
            "file_path": str(path),
            "file_name": path.name,
            "file_stem": path.stem,
            "file_extension": path.suffix,
            "parent_path": str(path.parent),
            "directory": str(path.parent),
        },
    )


async def _folder_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    _ = bindings
    path = project_path(context.project_root, node["configuration"]["path"])
    if not path.is_dir():
        raise NotADirectoryError(path)
    return HandlerResult(
        True,
        {
            "path": str(path),
            "folder_path": str(path),
            "folder_name": path.name,
            "parent_path": str(path.parent),
            "directory": str(path),
        },
    )


async def _open_resource_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    _ = bindings
    configuration = node["configuration"]
    target = configuration["target"].strip()
    resource_type = configuration["resource_type"]
    if resource_type == "auto":
        if "://" in target:
            resource_type = "url"
        else:
            candidate = project_path(context.project_root, target)
            resource_type = "folder" if candidate.is_dir() else "file"
    if resource_type in {"file", "folder"}:
        resolved = project_path(context.project_root, target)
        if resource_type == "file" and not resolved.is_file():
            raise FileNotFoundError(resolved)
        if resource_type == "folder" and not resolved.is_dir():
            raise NotADirectoryError(resolved)
        target = str(resolved)
    command = _open_resource_command(target, resource_type, configuration["args"])
    returncode, stdout, stderr = await run_subprocess(
        command,
        max_output_bytes=context.max_subprocess_output_bytes,
    )
    if returncode != 0:
        return HandlerResult(
            False,
            {"target": target, "resource_type": resource_type},
            RuntimeErrorInfo(
                "command",
                "RATTISH_RUNTIME_COMMAND_FAILED",
                stderr or stdout or f"Resource opener exited with status {returncode}.",
                {"exit_code": returncode, "target": target},
            ),
        )
    return HandlerResult(True, {"target": target, "resource_type": resource_type})


async def _prompt_file_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]
    if configuration["template_path"] is None:
        template = configuration["template"]
    else:
        template_path = project_path(context.project_root, configuration["template_path"])
        template = read_text_limited(
            template_path,
            encoding=configuration["encoding"],
            errors="strict",
            max_bytes=context.max_file_read_bytes,
        )
    variables: dict[str, Any] = dict(configuration["variables"])
    variables.update(bindings.local)
    rendered = render_prompt_template(template, variables)
    output_path = project_path(context.project_root, configuration["output_path"])
    if output_path.is_symlink():
        raise ValueError(f"Refusing to write through symlink: {output_path}")
    if output_path.exists() and not configuration["overwrite"]:
        raise FileExistsError(output_path)
    if not output_path.parent.exists():
        if not configuration["create_dirs"]:
            raise FileNotFoundError(output_path.parent)
        output_path.parent.mkdir(parents=True)
    encoded = rendered.encode(configuration["encoding"])
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output_path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return HandlerResult(
        True,
        {
            "path": str(output_path),
            "content": rendered,
            "prompt": rendered,
            "inputs": variables,
        },
    )


async def _write_file_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    configuration = node["configuration"]
    path = project_path(context.project_root, configuration["path"])
    if path.is_symlink():
        raise ValueError(f"Refusing to write through symlink: {path}")
    existed = path.exists()
    if existed and path.is_dir():
        raise IsADirectoryError(path)
    if existed and not configuration["append"] and not configuration["overwrite"]:
        raise FileExistsError(path)
    if not path.parent.exists():
        if not configuration["create_dirs"]:
            raise FileNotFoundError(path.parent)
        path.parent.mkdir(parents=True)
    content = (
        bindings.stdin.decode("utf-8") if bindings.stdin is not None else configuration["content"]
    )
    encoded = content.encode(configuration["encoding"])
    if configuration["append"]:
        with path.open("ab") as stream:
            stream.write(encoded)
        action = "appended" if existed else "created"
    else:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        action = "replaced" if existed else "created"
    return HandlerResult(
        True,
        {
            "path": str(path),
            "action": action,
            "bytes_written": len(encoded),
            "characters_written": len(content),
        },
    )


async def _copy_file_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    _ = bindings
    return _copy_or_move(node, context, move=False)


async def _move_file_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    _ = bindings
    return _copy_or_move(node, context, move=True)


def _copy_or_move(node: Mapping[str, Any], context: RuntimeContext, *, move: bool) -> HandlerResult:
    configuration = node["configuration"]
    source = project_path(context.project_root, configuration["source_path"])
    destination = project_path(context.project_root, configuration["destination_path"])
    kind = path_kind(source)
    if kind == "missing":
        raise FileNotFoundError(source)
    if source == destination:
        raise ValueError("Source and destination must be different.")
    if kind == "directory" and destination.is_relative_to(source):
        raise ValueError("A directory cannot be copied or moved inside itself.")
    if destination.exists() or destination.is_symlink():
        if not configuration["overwrite"]:
            raise FileExistsError(destination)
        _remove_path(destination, recursive=True)
    if not destination.parent.exists():
        if not configuration["create_dirs"]:
            raise FileNotFoundError(destination.parent)
        destination.parent.mkdir(parents=True)
    if move:
        shutil.move(str(source), str(destination))
    elif kind == "symlink":
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    elif kind == "directory":
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)
    return HandlerResult(True, _transfer_output(source, destination, kind))


async def _delete_file_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    _ = bindings
    configuration = node["configuration"]
    path = project_path(context.project_root, configuration["path"])
    kind = path_kind(path)
    if kind == "missing":
        if not configuration["missing_ok"]:
            raise FileNotFoundError(path)
        return HandlerResult(
            True,
            {
                "path": str(path),
                "path_kind": "missing",
                "disposition": "missing",
                "trash_path": None,
            },
        )
    if kind == "directory" and not configuration["recursive"] and not configuration["use_trash"]:
        raise IsADirectoryError(f"recursive: true is required to permanently delete {path}")
    trash_path = None
    if configuration["use_trash"]:
        context.trash_root.mkdir(parents=True, exist_ok=True)
        trash_path = context.trash_root / f"{path.name}-{uuid.uuid4().hex}"
        shutil.move(str(path), str(trash_path))
        disposition = "trashed"
    else:
        _remove_path(path, recursive=configuration["recursive"])
        disposition = "deleted"
    return HandlerResult(
        True,
        {
            "path": str(path),
            "path_kind": kind,
            "disposition": disposition,
            "trash_path": str(trash_path) if trash_path is not None else None,
        },
    )


def _remove_path(path: Path, *, recursive: bool) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif recursive:
        shutil.rmtree(path)
    else:
        path.rmdir()


def _transfer_output(source: Path, destination: Path, kind: str) -> dict[str, Any]:
    return {
        "source_path": str(source),
        "destination_path": str(destination),
        "source_name": source.name,
        "destination_name": destination.name,
        "destination_directory": str(destination.parent),
        "path_kind": kind,
    }


async def _python_script_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    return await _script_handler("python", node, context, bindings)


async def _shell_script_handler(
    node: Mapping[str, Any], context: RuntimeContext, bindings: ResolvedBindings
) -> HandlerResult:
    return await _script_handler("bash", node, context, bindings)


async def _script_handler(
    interpreter: str,
    node: Mapping[str, Any],
    context: RuntimeContext,
    bindings: ResolvedBindings,
) -> HandlerResult:
    configuration = node["configuration"]
    script_path = _resolve_project_path(context.project_root, configuration["script_path"])
    environment = dict(configuration["env"])
    environment.update(bindings.environment)
    timeout_ms = node["execution"]["timeout_ms"]
    returncode, stdout, stderr = await run_subprocess(
        [interpreter, str(script_path), *configuration["args"]],
        cwd=context.project_root,
        env=environment,
        stdin=bindings.stdin,
        timeout=None if timeout_ms is None else timeout_ms / 1000,
        max_output_bytes=context.max_subprocess_output_bytes,
    )
    output = {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": returncode,
        "script_path": str(script_path),
    }
    if returncode == 0:
        return HandlerResult(True, output)
    return HandlerResult(
        False,
        output,
        RuntimeErrorInfo(
            "command",
            "RATTISH_RUNTIME_COMMAND_FAILED",
            stderr or f"Script exited with status {returncode}.",
            {"exit_code": returncode, "script_path": str(script_path)},
        ),
    )


def _find_node(ir: Mapping[str, Any], node_id: str) -> Mapping[str, Any]:
    for node in ir["nodes"]:
        if node["id"] == node_id:
            return cast(Mapping[str, Any], node)
    raise KeyError(f"IR has no node {node_id!r}")


def _resolve_bindings(node: Mapping[str, Any], context: RuntimeContext) -> ResolvedBindings:
    local: dict[str, Any] = {}
    environment: dict[str, str] = {}
    workflow_inputs: dict[str, Any] = {}
    stdin: bytes | None = None
    sensitive_values: list[str] = []
    for binding in node["bindings"]:
        try:
            value = _binding_value(
                binding,
                context.workflow_inputs,
                context.trigger_events,
                context.node_outputs,
                context.node_statuses,
                context.node_errors,
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                f"Binding {binding['name']!r} could not resolve its source from the "
                "available runtime data."
            ) from exc
        delivery = binding["delivery"]
        source = binding["source"]
        local[binding["name"]] = value
        if _source_uses_secret(source) and isinstance(value, str) and value:
            sensitive_values.append(value)
        if delivery["kind"] == "environment":
            environment[delivery["name"]] = _environment_value(value)
        elif delivery["kind"] == "stdin":
            if not isinstance(value, str):
                raise UnsupportedRattishRuntimeError(
                    f"Binding {binding['name']!r} delivered to stdin is not text."
                )
            stdin = value.encode("utf-8")
        elif delivery["kind"] in {"local_binding", "workflow_input"}:
            local[delivery["name"]] = value
            if delivery["kind"] == "workflow_input":
                workflow_inputs[delivery["name"]] = value
        else:
            raise UnsupportedRattishRuntimeError(
                f"Binding delivery {delivery['kind']!r} is not implemented."
            )
    return ResolvedBindings(
        local=local,
        environment=environment,
        stdin=stdin,
        workflow_inputs=workflow_inputs,
        sensitive_values=tuple(dict.fromkeys(sensitive_values)),
    )


def _redact_sensitive(value: Any, sensitive_values: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in sensitive_values:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item, sensitive_values) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _redact_sensitive(item, sensitive_values) for key, item in value.items()}
    return value


def _redact_runtime_error(
    error: RuntimeErrorInfo | None, sensitive_values: tuple[str, ...]
) -> RuntimeErrorInfo | None:
    if error is None:
        return None
    details = _redact_sensitive(error.details, sensitive_values)
    assert isinstance(details, dict)
    message = _redact_sensitive(error.message, sensitive_values)
    assert isinstance(message, str)
    return RuntimeErrorInfo(error.kind, error.code, message, details)


def _binding_value(
    binding: Mapping[str, Any],
    workflow_inputs: Mapping[str, Any],
    trigger_events: tuple[Mapping[str, Any], ...],
    node_outputs: Mapping[str, Any],
    node_statuses: Mapping[str, str],
    node_errors: Mapping[str, Mapping[str, Any]],
) -> Any:
    source = binding["source"]
    if source["kind"] == "literal":
        return source["value"]
    if source["kind"] == "expression":
        return _evaluate_predicate(
            source["expression"],
            NodeExecutionResult("", "success", None),
            workflow_inputs,
            trigger_events,
            node_outputs,
            node_statuses,
            node_errors,
        )

    reference = source["reference"]
    try:
        if reference["root"] == "input":
            value = workflow_inputs[reference["symbol"]]
        elif reference["root"] == "trigger" and reference["symbol"] == "events":
            value = list(trigger_events)
        elif reference["root"] == "secret":
            value = os.environ[reference["symbol"]]
        elif reference["root"] == "node":
            if reference["channel"] == "output":
                value = node_outputs[reference["symbol"]]
            elif reference["channel"] == "status":
                value = node_statuses[reference["symbol"]]
            elif reference["channel"] == "error":
                value = node_errors[reference["symbol"]]
            else:
                raise UnsupportedRattishRuntimeError(
                    f"Node reference channel {reference['channel']!r} is not implemented."
                )
        else:
            raise UnsupportedRattishRuntimeError(
                f"Reference root/channel {reference['root']!r}/{reference['channel']!r} "
                "is not implemented."
            )
        for selector in reference["path"]:
            value = value[selector["value"]]
        return value
    except (KeyError, IndexError, TypeError):
        default = binding["default"]
        if default["present"]:
            return default["value"]
        if reference["optional"]:
            return None
        raise


def _source_uses_secret(source: Mapping[str, Any]) -> bool:
    if source["kind"] == "reference":
        return bool(source["reference"]["root"] == "secret")
    if source["kind"] != "expression":
        return False

    def visit(expression: Mapping[str, Any]) -> bool:
        kind = expression["kind"]
        if kind == "logical":
            return visit(expression["left"]) or visit(expression["right"])
        if kind == "not":
            return visit(expression["operand"])
        if kind in {"exists", "null_test", "reference"}:
            return bool(expression["reference"]["root"] == "secret")
        if kind == "comparison":
            return any(
                operand["kind"] == "reference" and operand["reference"]["root"] == "secret"
                for operand in (expression["left"], expression["right"])
            )
        return False

    return visit(source["expression"])


def _render_configuration(
    value: Any,
    bindings: Mapping[str, Any],
    *,
    skip: set[str] | None = None,
    field_name: str = "",
) -> Any:
    if field_name and field_name in (skip or set()):
        return value
    if isinstance(value, str):
        if "{{" not in value and "}}" not in value:
            return value
        return render_template_value(value, bindings)
    if isinstance(value, list):
        return [_render_configuration(item, bindings, skip=skip) for item in value]
    if isinstance(value, Mapping):
        return {
            str(name): _render_configuration(item, bindings, skip=skip, field_name=str(name))
            for name, item in value.items()
        }
    return value


def _binding_or_configuration(
    bindings: ResolvedBindings,
    configuration: Mapping[str, Any],
    binding_name: str,
    configuration_name: str | None = None,
) -> Any:
    if binding_name in bindings.local:
        return bindings.local[binding_name]
    return configuration[configuration_name or binding_name.replace("-", "_")]


_MISSING = object()


def _prepare_workflow_inputs(ir: Mapping[str, Any], supplied: Mapping[str, Any]) -> dict[str, Any]:
    declarations = {item["name"]: item for item in ir["workflow"]["inputs"]}
    unknown = sorted(set(supplied) - set(declarations))
    if unknown:
        raise InvalidRattishWorkflowInputError(f"Unknown workflow input(s): {', '.join(unknown)}.")
    prepared = dict(supplied)
    for name, declaration in declarations.items():
        if name not in prepared:
            default = declaration["default"]
            if default["present"]:
                prepared[name] = default["value"]
            elif declaration["required"]:
                raise InvalidRattishWorkflowInputError(
                    f"Required workflow input {name!r} was not supplied."
                )
            else:
                continue
        error = next(Draft202012Validator(declaration["schema"]).iter_errors(prepared[name]), None)
        if error is not None:
            raise InvalidRattishWorkflowInputError(
                f"Workflow input {name!r} is invalid: {error.message}"
            )
    return prepared


def _evaluate_predicate(
    predicate: Mapping[str, Any],
    current_result: NodeExecutionResult,
    workflow_inputs: Mapping[str, Any],
    trigger_events: tuple[Mapping[str, Any], ...],
    node_outputs: Mapping[str, Any],
    node_statuses: Mapping[str, str],
    node_errors: Mapping[str, Mapping[str, Any]],
) -> bool:
    kind = predicate["kind"]
    if kind == "logical":
        left = _evaluate_predicate(
            predicate["left"],
            current_result,
            workflow_inputs,
            trigger_events,
            node_outputs,
            node_statuses,
            node_errors,
        )
        if predicate["operator"] == "and" and not left:
            return False
        if predicate["operator"] == "or" and left:
            return True
        return _evaluate_predicate(
            predicate["right"],
            current_result,
            workflow_inputs,
            trigger_events,
            node_outputs,
            node_statuses,
            node_errors,
        )
    if kind == "not":
        return not _evaluate_predicate(
            predicate["operand"],
            current_result,
            workflow_inputs,
            trigger_events,
            node_outputs,
            node_statuses,
            node_errors,
        )
    if kind == "status":
        status_value = str(predicate["value"])
        return (status_value == "succeeded") == (current_result.outcome == "success")
    if kind in {"exists", "null_test", "reference"}:
        value = _reference_value(
            predicate["reference"],
            workflow_inputs,
            trigger_events,
            node_outputs,
            node_statuses,
            node_errors,
        )
        if kind == "exists":
            return value is not _MISSING
        if kind == "null_test":
            is_null = value is not _MISSING and value is None
            return is_null if predicate["operator"] == "is_null" else not is_null
        return value is not _MISSING and bool(value)
    if kind == "comparison":
        left = _operand_value(
            predicate["left"],
            workflow_inputs,
            trigger_events,
            node_outputs,
            node_statuses,
            node_errors,
        )
        right = _operand_value(
            predicate["right"],
            workflow_inputs,
            trigger_events,
            node_outputs,
            node_statuses,
            node_errors,
        )
        if left is _MISSING or right is _MISSING:
            return False
        operator = predicate["operator"]
        try:
            if operator == "==":
                return bool(left == right)
            if operator == "!=":
                return bool(left != right)
            if operator == "<":
                return bool(left < right)
            if operator == "<=":
                return bool(left <= right)
            if operator == ">":
                return bool(left > right)
            if operator == ">=":
                return bool(left >= right)
            if operator == "contains":
                return bool(right in left)
            if operator == "matches":
                return re.search(str(right), str(left)) is not None
        except (TypeError, re.error):
            return False
    raise UnsupportedRattishRuntimeError(f"Predicate kind {kind!r} is not implemented.")


def _predicate_uses_output(predicate: Mapping[str, Any]) -> bool:
    kind = predicate["kind"]
    if kind == "logical":
        return _predicate_uses_output(predicate["left"]) or _predicate_uses_output(
            predicate["right"]
        )
    if kind == "not":
        return _predicate_uses_output(predicate["operand"])
    if kind in {"exists", "null_test", "reference"}:
        reference = predicate["reference"]
        return bool(reference["root"] == "node" and reference["channel"] == "output")
    if kind == "comparison":
        return any(
            operand["kind"] == "reference"
            and operand["reference"]["root"] == "node"
            and operand["reference"]["channel"] == "output"
            for operand in (predicate["left"], predicate["right"])
        )
    return False


def _operand_value(
    operand: Mapping[str, Any],
    workflow_inputs: Mapping[str, Any],
    trigger_events: tuple[Mapping[str, Any], ...],
    node_outputs: Mapping[str, Any],
    node_statuses: Mapping[str, str],
    node_errors: Mapping[str, Mapping[str, Any]],
) -> Any:
    if operand["kind"] == "literal":
        return operand["value"]
    return _reference_value(
        operand["reference"],
        workflow_inputs,
        trigger_events,
        node_outputs,
        node_statuses,
        node_errors,
    )


def _reference_value(
    reference: Mapping[str, Any],
    workflow_inputs: Mapping[str, Any],
    trigger_events: tuple[Mapping[str, Any], ...],
    node_outputs: Mapping[str, Any],
    node_statuses: Mapping[str, str],
    node_errors: Mapping[str, Mapping[str, Any]],
) -> Any:
    root = reference["root"]
    symbol = reference["symbol"]
    if root == "input":
        value: Any = workflow_inputs.get(symbol, _MISSING)
    elif root == "trigger" and symbol == "events":
        value = list(trigger_events)
    elif root == "secret":
        value = os.environ.get(str(symbol), _MISSING)
    elif root == "node" and reference["channel"] == "output":
        value = node_outputs.get(symbol, _MISSING)
    elif root == "node" and reference["channel"] == "status":
        value = node_statuses.get(symbol, _MISSING)
    elif root == "node" and reference["channel"] == "error":
        value = node_errors.get(symbol, _MISSING)
    else:
        return _MISSING
    for selector in reference["path"]:
        if value is _MISSING:
            break
        try:
            value = value[selector["value"]]
        except (KeyError, IndexError, TypeError):
            return _MISSING
    return value


def _error_document(error: RuntimeErrorInfo) -> dict[str, Any]:
    return {
        "kind": error.kind,
        "code": error.code,
        "message": error.message,
        "details": error.details,
    }


def _resolve_project_path(project_root: Path, authored_path: str) -> Path:
    return project_path(project_root, authored_path)


def _environment_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return canonical_json_bytes(value).decode("utf-8")


def _shell_command_args(command: str) -> list[str]:
    if sys.platform == "win32":
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
    return ["bash", "-c", command]


def _open_resource_command(target: str, resource_type: str, args: list[str]) -> list[str]:
    if resource_type == "app":
        return [target, *args]
    if sys.platform == "win32":
        return ["cmd", "/c", "start", "", target]
    if sys.platform == "darwin":
        return ["open", target]
    return ["xdg-open", target]


DEFAULT_NODE_HANDLERS = NodeHandlerRegistry(
    {
        "raticode.agent": _agent_handler,
        "raticode.approval_gate": _approval_gate_handler,
        "raticode.bash_command": _bash_handler,
        "raticode.break": _break_handler,
        "raticode.common_llm_task": _common_llm_task_handler,
        "raticode.python_script": _python_script_handler,
        "raticode.read_file": _read_file_handler,
        "raticode.file": _file_handler,
        "raticode.folder": _folder_handler,
        "raticode.http_request": _http_request_handler,
        "raticode.local_search": _local_search_handler,
        "raticode.local_vectorize": _local_vectorize_handler,
        "raticode.loop": _loop_handler,
        "raticode.notification": _notification_handler,
        "raticode.open_resource": _open_resource_handler,
        "raticode.prompt_file": _prompt_file_handler,
        "raticode.shell_script": _shell_script_handler,
        "raticode.write_file": _write_file_handler,
        "raticode.copy_file": _copy_file_handler,
        "raticode.move_file": _move_file_handler,
        "raticode.delete_file": _delete_file_handler,
        "raticode.workflow": _workflow_handler,
    }
)
