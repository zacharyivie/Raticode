from __future__ import annotations

import copy
import csv
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Literal, cast

from gofer.core.agent import configured_extra_paths
from gofer.core.bindings import binding_contract, inspect_workflow_bindings
from gofer.core.graph import EdgeConditionType, GraphNode
from gofer.core.llm_prompts import common_llm_task_prompt
from gofer.core.network_policy import network_policy_warnings
from gofer.core.operations import (
    AgentOperation,
    ApprovalGateOperation,
    BashCommandOperation,
    BreakOperation,
    CommonLlmTaskOperation,
    CopyFileOperation,
    CountFanSource,
    DeleteFileOperation,
    DirectoryFanSource,
    FailOperation,
    FileOperation,
    FolderOperation,
    HttpRequestOperation,
    InfiniteFanSource,
    LocalSearchOperation,
    LocalVectorizeOperation,
    LoopOperation,
    MoveFileOperation,
    NotificationOperation,
    OpenResourceOperation,
    PassOperation,
    PromptFileOperation,
    PythonScriptOperation,
    ReadFileOperation,
    ShellScriptOperation,
    StartOperation,
    SubflowOperation,
    TabularFanSource,
    TriggerEventsFanSource,
    WorkflowCallOperation,
    WriteFileOperation,
)
from gofer.core.provider_capabilities import ProviderId, resolve_provider_executable
from gofer.core.provider_profiles import (
    DIRECT_API_SUBSCRIPTIONS,
    resolve_provider_settings,
    validate_provider_settings,
)
from gofer.core.references import parse_exact_reference
from gofer.core.resources import DEFAULT_RESOURCE_LIMITS, ResourceLimits
from gofer.core.secrets import (
    secret_reference_names as workflow_secret_reference_names,
)
from gofer.core.secrets import (
    workflow_secret_readiness,
)
from gofer.core.structured_output import resolve_output_schema
from gofer.core.usage import LlmUsageTotals, estimate_tokens
from gofer.core.validation import validate_workflow
from gofer.core.workflow import AgenticWorkflow, FilesystemAccessEntry
from gofer.prompts.manager import PromptManager

SAMPLE_LIMIT = 5
SECRET_REF_PATTERN = re.compile(
    r"^\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$"
    r"|^secret:([A-Za-z_][A-Za-z0-9_]*)$"
)
SECRET_INTERPOLATION_PATTERN = re.compile(r"\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
SENSITIVE_FIELD_NAMES = {
    "authorization",
    "cookie",
    "x-api-key",
    "api-key",
    "token",
    "password",
    "secret",
}


def build_execution_plan(
    workflow: AgenticWorkflow,
    *,
    workflow_path: Path | None = None,
    data_dir: Path | None = None,
    trigger_context: dict[str, Any] | None = None,
    invocation_inputs: dict[str, Any] | None = None,
    sample_limit: int = SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Build a read-only preview of workflow execution impact."""
    if invocation_inputs is not None:
        workflow = _workflow_with_invocation_scope(workflow, invocation_inputs)
    limits = workflow.config.resource_limits or DEFAULT_RESOURCE_LIMITS
    path_base = workflow_path.parent if workflow_path is not None else None
    profile_data_dir = data_dir if data_dir is not None else path_base
    warnings = workflow.resource_warnings(path_base)
    warnings.extend(_webhook_risk_warnings(workflow))
    bindings = inspect_workflow_bindings(
        workflow,
        workflow_path=workflow_path,
        data_dir=data_dir,
    )
    bindings_by_node: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        bindings_by_node.setdefault(binding.destination_node, []).append(binding.to_dict())
    plan: dict[str, Any] = {
        "workflowId": workflow.config.id,
        "workflowName": workflow.config.name,
        "inputs": {
            name: declaration.model_dump(mode="json", exclude_none=True)
            for name, declaration in workflow.config.declared_inputs.items()
        },
        "initialVariables": {
            name: "***" if declaration.secret else declaration.initial
            for name, declaration in workflow.config.variables.items()
        },
        "startNodes": _start_nodes(workflow),
        "generations": [],
        "edges": _edge_plan(workflow),
        "conditionalBranches": _conditional_branches(workflow),
        "warnings": warnings,
        "validation": validate_workflow(
            workflow,
            workflow_path=workflow_path,
            data_dir=data_dir,
        ).to_dict(),
        "destructiveActions": [],
        "destructiveActionDetails": [],
        "requiredSecrets": [],
        "secretReadiness": [],
        "providerRequirements": [],
        "filesystemRequirements": [],
        "projectedLlmUsage": LlmUsageTotals().to_dict(),
        "resourceLimits": limits.model_dump(),
        "executionLimits": {
            "maxTotalNodeRuns": workflow.config.max_total_node_runs,
            "runContinuously": workflow.config.run_continuously,
        },
        "triggerContext": _trigger_plan(workflow, trigger_context, path_base),
        "bindingContract": binding_contract(),
        "bindings": [binding.to_dict() for binding in bindings],
    }
    if path_base is not None:
        plan["pathResolutionBase"] = str(path_base)

    secret_statuses = workflow_secret_readiness(
        workflow,
        workflow_path=workflow_path,
        data_dir=profile_data_dir,
    )
    secret_names: set[str] = {status.name for status in secret_statuses}
    provider_keys: dict[
        tuple[
            str,
            str,
            str | None,
            str | None,
            float | None,
            str,
            str | None,
            bool,
            str | None,
            bool,
        ],
        set[str],
    ] = {}
    filesystem_requirements: dict[str, dict[str, Any]] = {}
    fan_out_multipliers: dict[str, int] = {}

    for generation_index, generation in enumerate(workflow.graph.topological_generations()):
        planned_nodes = []
        for node in generation:
            inherited_fan_out = _inherited_fan_out_multiplier(
                workflow,
                node.node_id,
                fan_out_multipliers,
            )
            node_plan = _node_plan(
                workflow,
                node,
                limits=limits,
                path_base=path_base,
                data_dir=profile_data_dir,
                trigger_context=trigger_context or {},
                sample_limit=sample_limit,
                inherited_fan_out=inherited_fan_out,
                bindings=bindings_by_node.get(node.node_id, []),
            )
            planned_nodes.append(node_plan)
            plan["destructiveActions"].extend(node_plan["destructiveActions"])
            plan["destructiveActionDetails"].extend(node_plan["destructiveActionDetails"])
            plan["warnings"].extend(node_plan["warnings"])
            projected_usage = node_plan.get("projectedLlmUsage")
            if isinstance(projected_usage, dict):
                plan["projectedLlmUsage"]["agent_calls"] += int(
                    projected_usage.get("agent_calls") or 0
                )
                plan["projectedLlmUsage"]["input_tokens"] += int(
                    projected_usage.get("input_tokens") or 0
                )
                plan["projectedLlmUsage"]["output_tokens"] += int(
                    projected_usage.get("output_tokens") or 0
                )
                plan["projectedLlmUsage"]["total_tokens"] += int(
                    projected_usage.get("total_tokens") or 0
                )
                plan["projectedLlmUsage"]["estimated_cost"] += float(
                    projected_usage.get("estimated_cost") or 0.0
                )
                plan["projectedLlmUsage"]["agent_time_seconds"] += float(
                    projected_usage.get("agent_time_seconds") or 0.0
                )
            for secret in node_plan["requiredSecrets"]:
                secret_names.add(secret)
            for requirement in node_plan["providerRequirements"]:
                provider_key = (
                    str(requirement["agentId"]),
                    str(requirement["subscription"]),
                    (
                        str(requirement["profile"])
                        if requirement.get("profile") is not None
                        else None
                    ),
                    (str(requirement["model"]) if requirement.get("model") is not None else None),
                    (
                        float(requirement["timeout"])
                        if requirement.get("timeout") is not None
                        else None
                    ),
                    str(requirement["workingDir"]),
                    (str(requirement["binary"]) if requirement.get("binary") is not None else None),
                    bool(requirement.get("directApi", False)),
                    (
                        str(requirement["apiBaseUrl"])
                        if requirement.get("apiBaseUrl") is not None
                        else None
                    ),
                    bool(requirement["available"]),
                )
                provider_keys.setdefault(provider_key, set()).update(
                    str(path) for path in requirement.get("extraPaths", [])
                )
            for requirement in node_plan["filesystemRequirements"]:
                filesystem_key = json.dumps(requirement, sort_keys=True, default=str)
                filesystem_requirements[filesystem_key] = requirement
            fan_out_multipliers[node.node_id] = _successor_fan_out_multiplier(
                inherited_fan_out,
                node_plan.get("fanOut"),
            )
        plan["generations"].append(
            {
                "index": generation_index,
                "nodes": planned_nodes,
            }
        )

    plan["warnings"] = sorted(set(plan["warnings"]))
    plan["destructiveActions"] = sorted(set(plan["destructiveActions"]))
    plan["destructiveActionDetails"] = _dedupe_details(plan["destructiveActionDetails"])
    plan["requiredSecrets"] = sorted(secret_names)
    plan["secretReadiness"] = [status.to_dict() for status in secret_statuses]
    plan["providerRequirements"] = []
    for (
        agent_id,
        subscription,
        profile,
        model,
        timeout,
        working_dir,
        binary,
        direct_api,
        api_base_url,
        available,
    ), extra_paths in sorted(provider_keys.items()):
        provider_requirement: dict[str, Any] = {
            "agentId": agent_id,
            "subscription": subscription,
            "workingDir": working_dir,
            "binary": binary,
            "directApi": direct_api,
            "apiBaseUrl": api_base_url,
            "available": available,
            "extraPaths": sorted(extra_paths),
        }
        if profile is not None:
            provider_requirement["profile"] = profile
        if model is not None:
            provider_requirement["model"] = model
        if timeout is not None:
            provider_requirement["timeout"] = timeout
        plan["providerRequirements"].append(provider_requirement)
    plan["filesystemRequirements"] = [
        filesystem_requirements[key] for key in sorted(filesystem_requirements)
    ]
    plan["warnings"] = sorted(set(plan["warnings"]))
    return plan


def _workflow_with_invocation_scope(
    workflow: AgenticWorkflow,
    inputs: dict[str, Any],
) -> AgenticWorkflow:
    """Render a disposable planning copy without exposing invocation secrets."""

    planned = copy.deepcopy(workflow)
    safe_inputs = {
        name: (
            "***"
            if planned.config.declared_inputs.get(name)
            and planned.config.declared_inputs[name].is_secret
            else value
        )
        for name, value in inputs.items()
    }
    variables: dict[str, Any] = {}
    for name, declaration in planned.config.variables.items():
        context = {"inputs": safe_inputs, "params": safe_inputs, "vars": variables}
        variables[name] = (
            "***" if declaration.secret else _render_plan_scope_value(declaration.initial, context)
        )
        declaration.initial = variables[name]
    context = {"inputs": safe_inputs, "params": safe_inputs, "vars": variables}
    for node in planned.graph.nodes_in_order():
        payload = _render_plan_scope_value(node.operation.model_dump(by_alias=True), context)
        if isinstance(payload, dict):
            planned.graph._nodes[node.node_id] = node.model_copy(
                update={"operation": node.operation.__class__.model_validate(payload)}
            )
    return planned


def _render_plan_scope_value(value: object, context: dict[str, Any]) -> object:
    if isinstance(value, Path):
        return _render_plan_scope_value(str(value), context)
    if isinstance(value, str):
        exact = parse_exact_reference(value)
        if exact is not None and value.strip().startswith("{{"):
            selected: object = context
            for part in exact.split("."):
                if not isinstance(selected, dict) or part not in selected:
                    return value
                selected = selected[part]
            return selected
        return PromptManager._interpolate(value, context)
    if isinstance(value, dict):
        return {str(key): _render_plan_scope_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_plan_scope_value(item, context) for item in value]
    return value


def _start_nodes(workflow: AgenticWorkflow) -> list[str]:
    return sorted(
        str(node_id) for node_id, degree in workflow.graph._graph.in_degree() if int(degree) == 0
    )


def _conditional_branches(workflow: AgenticWorkflow) -> list[dict[str, Any]]:
    return [
        edge
        for edge in _edge_plan(workflow)
        if edge["condition"]
        not in {
            EdgeConditionType.ALWAYS.value,
            EdgeConditionType.AFTER_LOOP.value,
        }
    ]


def _edge_plan(workflow: AgenticWorkflow) -> list[dict[str, Any]]:
    edges = []
    for from_id, to_id in workflow.graph._graph.edges():
        edge = workflow.graph.get_edge_config(from_id, to_id)
        label = edge.condition.value
        if edge.condition == EdgeConditionType.OUTPUT_MATCHES and edge.output_pattern:
            label = f"output_matches:{edge.output_pattern}"
        elif edge.condition == EdgeConditionType.OUTPUT_FIELD:
            label = edge.explanation()
        item: dict[str, Any] = {
            "from": from_id,
            "to": to_id,
            "condition": edge.condition.value,
            "label": label,
            "outputPattern": edge.output_pattern,
        }
        if edge.condition == EdgeConditionType.OUTPUT_FIELD:
            item.update(
                {
                    "field": edge.field,
                    "operator": edge.operator.value if edge.operator is not None else None,
                    "value": edge.value,
                    "explanation": edge.explanation(),
                }
            )
        edges.append(item)
    return edges


def _inherited_fan_out_multiplier(
    workflow: AgenticWorkflow,
    node_id: str,
    fan_out_multipliers: dict[str, int],
) -> int:
    multiplier = 1
    for predecessor_id in workflow.graph._graph.predecessors(node_id):
        edge = workflow.graph.get_edge_config(predecessor_id, node_id)
        if edge.condition == EdgeConditionType.AFTER_LOOP:
            continue
        multiplier = max(multiplier, fan_out_multipliers.get(predecessor_id, 1))
    return multiplier


def _successor_fan_out_multiplier(
    inherited_fan_out: int,
    fan_out: object,
) -> int:
    if not isinstance(fan_out, dict):
        return inherited_fan_out
    count = fan_out.get("count")
    if not isinstance(count, int):
        return inherited_fan_out
    return inherited_fan_out * max(0, count)


def _trigger_plan(
    workflow: AgenticWorkflow,
    trigger_context: dict[str, Any] | None,
    path_base: Path | None,
) -> dict[str, Any]:
    plan: dict[str, Any] = {}
    if workflow.config.schedule is not None:
        plan["schedule"] = workflow.config.schedule.model_dump()
    if workflow.config.watch is not None:
        watch_path = _resolve_path(workflow.config.watch.path, path_base)
        plan["watch"] = {
            **workflow.config.watch.model_dump(),
            "path": str(watch_path),
        }
    if workflow.config.run_continuously:
        plan["runContinuously"] = True
    if workflow.config.webhooks:
        plan["webhooks"] = {
            trigger_id: {
                "enabled": config.enabled,
                "source": config.source,
                "fanoutPath": config.fanout_path,
                "tokenConfigured": config.has_authentication,
                "allowUnauthenticated": config.allow_unauthenticated,
                "storeRawPayload": config.store_raw_payload,
                "replayPayloadRetention": "raw" if config.store_raw_payload else "sanitized",
                "sensitivePayloadFields": sorted(config.sensitive_payload_fields),
                "risk": "high"
                if config.store_raw_payload
                or config.missing_authentication
                or config.requires_unauthenticated_warning
                else "normal",
                "riskReasons": _webhook_risk_reasons(config),
            }
            for trigger_id, config in sorted(workflow.config.webhooks.items())
        }
    if trigger_context:
        plan["provided"] = trigger_context
    return plan


def _webhook_risk_warnings(workflow: AgenticWorkflow) -> list[str]:
    warnings: list[str] = []
    for trigger_id, config in sorted(workflow.config.webhooks.items()):
        if config.missing_authentication:
            warnings.append(
                f"Enabled webhook trigger '{trigger_id}' has no authentication configured; "
                "runtime requests will be rejected."
            )
        if config.requires_unauthenticated_warning:
            warnings.append(
                f"Webhook trigger '{trigger_id}' allows unauthenticated requests; "
                "this is high risk and intended only for local testing."
            )
        if config.store_raw_payload:
            warnings.append(
                f"Webhook trigger '{trigger_id}' stores raw replay payloads; "
                "incoming secrets may be persisted."
            )
    return warnings


def _webhook_risk_reasons(config: Any) -> list[str]:
    reasons: list[str] = []
    if config.missing_authentication:
        reasons.append("missing_authentication")
    if config.requires_unauthenticated_warning:
        reasons.append("unauthenticated_allowed")
    if config.store_raw_payload:
        reasons.append("raw_payload_retention")
    return reasons


def _node_plan(
    workflow: AgenticWorkflow,
    node: GraphNode,
    *,
    limits: ResourceLimits,
    path_base: Path | None,
    data_dir: Path | None,
    trigger_context: dict[str, Any],
    sample_limit: int,
    inherited_fan_out: int = 1,
    bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    op = node.operation
    detail = _operation_detail(workflow, node, path_base)
    (
        side_effects,
        side_effect_details,
        destructive_actions,
        destructive_action_details,
        warnings,
    ) = _operation_impact(op, path_base)
    warnings.extend(_agent_registration_warnings(op, workflow))
    warnings.extend(
        _operation_access_warnings(
            op,
            workflow,
            node.node_id,
            path_base,
            trigger_context,
        )
    )
    fan_out = _fan_out_plan(
        op,
        workflow,
        trigger_context,
        limits,
        sample_limit,
        path_base,
    )
    if fan_out is not None:
        warnings.extend(str(warning) for warning in fan_out.get("warnings", []))
    required_secrets = _required_secrets(op, workflow, path_base, data_dir)
    provider_requirements = _provider_requirements(op, workflow, path_base, data_dir)
    filesystem_requirements = _filesystem_requirements(op, path_base)
    projected_llm_usage = _projected_llm_usage(
        op,
        workflow,
        fan_out,
        path_base,
        node.node_id,
        inherited_fan_out,
    )
    for requirement in provider_requirements:
        for error in requirement.get("validationErrors", []):
            warnings.append(str(error))
        if not requirement.get("available"):
            binary = requirement.get("binary") or requirement["subscription"]
            warnings.append(
                f"Provider CLI '{binary}' is not available for agent {requirement['agentId']}"
            )
    return {
        "id": node.node_id,
        "label": node.label or node.node_id,
        "type": str(op.type),
        "detail": detail,
        "sideEffects": side_effects,
        "sideEffectDetails": side_effect_details,
        "destructiveActions": destructive_actions,
        "destructiveActionDetails": destructive_action_details,
        "warnings": warnings,
        "fanOut": fan_out,
        "requiredSecrets": required_secrets,
        "providerRequirements": provider_requirements,
        "filesystemRequirements": filesystem_requirements,
        "projectedLlmUsage": projected_llm_usage,
        "workingDir": _working_dir(op, workflow, path_base),
        "retryCount": node.retry_count,
        "retryDelaySeconds": node.retry_delay_seconds,
        "timeoutSeconds": node.timeout_seconds,
        "allowFailure": node.allow_failure,
        "awaitAllInputs": node.await_all_inputs,
        "onFailure": node.on_failure,
        "inputs": dict(node.inputs),
        "bindings": bindings or [],
        "outputSchema": (
            resolve_output_schema(op.output_schema, workflow.config.output_schemas)[1]
            if isinstance(op, (AgentOperation, CommonLlmTaskOperation))
            and op.output_schema is not None
            else None
        ),
        "outputSchemaName": (
            op.output_schema
            if isinstance(op, (AgentOperation, CommonLlmTaskOperation))
            and isinstance(op.output_schema, str)
            else None
        ),
        "repairAttempts": (
            op.repair_attempts if isinstance(op, (AgentOperation, CommonLlmTaskOperation)) else 0
        ),
    }


def _projected_llm_usage(
    op: object,
    workflow: AgenticWorkflow,
    fan_out: dict[str, Any] | None,
    path_base: Path | None,
    node_id: str,
    inherited_fan_out: int = 1,
) -> dict[str, object] | None:
    if not isinstance(op, (AgentOperation, CommonLlmTaskOperation)):
        return None
    agent = workflow.agents.get(op.agent_id)
    if agent is None:
        return None
    call_count = max(0, inherited_fan_out)
    if fan_out is not None and isinstance(fan_out.get("count"), int):
        call_count *= max(0, int(fan_out["count"]))
    prompt_text = _prompt_preview_text(op, workflow, path_base)
    input_tokens = estimate_tokens(prompt_text, agent.pricing) * call_count
    historical = _historical_llm_usage_average(workflow.config.id, node_id, path_base)
    output_tokens = int(round(historical["output_tokens"] * call_count))
    total_tokens = input_tokens + output_tokens
    estimated_cost = (
        input_tokens * agent.pricing.input_cost_per_1k_tokens
        + output_tokens * agent.pricing.output_cost_per_1k_tokens
    ) / 1000
    agent_time_seconds = historical["duration_seconds"] * call_count
    source = (
        "dry_run_prompt_template_with_historical_averages"
        if historical["samples"]
        else "dry_run_prompt_template_chars_per_token"
    )
    return {
        "agent_calls": call_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": estimated_cost,
        "agent_time_seconds": agent_time_seconds,
        "estimated": True,
        "source": source,
        "historical_samples": historical["samples"],
        "provider": agent.subscription,
        "profile": agent.profile,
        "model": agent.model,
    }


def _historical_llm_usage_average(
    workflow_id: str,
    node_id: str,
    path_base: Path | None,
) -> dict[str, float]:
    if path_base is None:
        return {"output_tokens": 0.0, "duration_seconds": 0.0, "samples": 0.0}
    log_dir = path_base / "logs" / workflow_id
    samples: list[tuple[int, float]] = []
    try:
        output_paths = sorted(
            log_dir.glob("*.outputs.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return {"output_tokens": 0.0, "duration_seconds": 0.0, "samples": 0.0}
    for output_path in output_paths[:20]:
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        summary = payload.get("usageSummary")
        nodes = summary.get("nodes") if isinstance(summary, dict) else None
        if not isinstance(nodes, list):
            continue
        for node_usage in nodes:
            if not isinstance(node_usage, dict) or node_usage.get("node_id") != node_id:
                continue
            samples.append(
                (
                    int(node_usage.get("output_tokens") or 0),
                    float(node_usage.get("duration_seconds") or 0.0),
                )
            )
    if not samples:
        return {"output_tokens": 0.0, "duration_seconds": 0.0, "samples": 0.0}
    return {
        "output_tokens": sum(output for output, _ in samples) / len(samples),
        "duration_seconds": sum(duration for _, duration in samples) / len(samples),
        "samples": float(len(samples)),
    }


def _prompt_preview_text(
    op: AgentOperation | CommonLlmTaskOperation,
    workflow: AgenticWorkflow,
    path_base: Path | None,
) -> str:
    if isinstance(op, AgentOperation):
        if op.skill_name:
            return f"/{op.skill_name.strip().lstrip('/')}"
        prompt_path = op.prompt_path
        if prompt_path is None:
            agent = workflow.agents.get(op.agent_id)
            prompt_path = agent.prompt_path if agent is not None else None
        if prompt_path is None:
            return ""
        path = _resolve_path(prompt_path, path_base)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""
    return common_llm_task_prompt(op.task, op.target, op.instructions)


def _operation_detail(
    workflow: AgenticWorkflow,
    node: GraphNode,
    path_base: Path | None,
) -> str:
    op = node.operation
    if isinstance(op, BashCommandOperation):
        return op.command
    if isinstance(op, (PythonScriptOperation, ShellScriptOperation)):
        return " ".join([str(_resolve_path(op.script_path, path_base)), *op.args])
    if isinstance(op, ReadFileOperation):
        return str(_resolve_path(op.path, path_base))
    if isinstance(op, WriteFileOperation):
        mode = "append" if op.append else "write"
        return f"{mode} {_resolve_path(op.path, path_base)}"
    if isinstance(op, (CopyFileOperation, MoveFileOperation)):
        return (
            f"{_resolve_path(op.source_path, path_base)} -> "
            f"{_resolve_path(op.destination_path, path_base)}"
        )
    if isinstance(op, DeleteFileOperation):
        return str(_resolve_path(op.path, path_base))
    if isinstance(op, (FileOperation, FolderOperation)):
        return str(_resolve_path(op.path, path_base))
    if isinstance(op, OpenResourceOperation):
        return op.target
    if isinstance(op, PromptFileOperation):
        source = (
            str(_resolve_path(op.template_path, path_base))
            if op.template_path is not None
            else "inline template"
        )
        return f"{source} -> {_resolve_path(op.output_path, path_base)}"
    if isinstance(op, CommonLlmTaskOperation):
        return f"{op.agent_id}:{op.task} {op.target}".strip()
    if isinstance(op, LocalVectorizeOperation):
        return (
            f"{_resolve_path(op.source_path, path_base)} -> "
            f"{_resolve_path(op.index_path, path_base)}"
        )
    if isinstance(op, LocalSearchOperation):
        return f"{_resolve_path(op.index_path, path_base)} top_k={op.top_k}"
    if isinstance(op, HttpRequestOperation):
        parsed = urllib.parse.urlsplit(op.url)
        return f"{op.method.upper()} {parsed.netloc or '<dynamic-host>'}"
    if isinstance(op, ApprovalGateOperation):
        timeout = f" timeout={op.timeout_seconds}s" if op.timeout_seconds else ""
        return f"approval gate{timeout}"
    if isinstance(op, NotificationOperation):
        return f"notify {op.channel}: {op.title}"
    if isinstance(op, WorkflowCallOperation):
        return f"run workflow {op.workflow_id}"
    if isinstance(op, SubflowOperation):
        version = f"@{op.version}" if op.version else ""
        return f"subflow {op.component_id}{version}"
    if isinstance(op, AgentOperation):
        parts = [op.agent_id]
        if op.skill_name:
            parts.append(f"skill={op.skill_name}")
        if op.prompt_path:
            parts.append(f"prompt={_resolve_path(op.prompt_path, path_base)}")
        agent = workflow.agents.get(op.agent_id)
        if agent is not None:
            parts.append(f"provider={agent.subscription}")
        return " ".join(parts)
    if isinstance(op, LoopOperation):
        return _fan_source_label(op.source, path_base)
    if isinstance(op, PassOperation):
        return op.message
    if isinstance(op, FailOperation):
        return op.message
    if isinstance(op, BreakOperation):
        return op.message
    if isinstance(op, StartOperation):
        return "start"
    return str(op.type)


def _operation_impact(
    op: object,
    path_base: Path | None,
) -> tuple[list[str], list[dict[str, object]], list[str], list[dict[str, object]], list[str]]:
    side_effects: list[str] = []
    side_effect_details: list[dict[str, object]] = []
    destructive: list[str] = []
    destructive_details: list[dict[str, object]] = []
    warnings: list[str] = []
    if isinstance(op, BashCommandOperation):
        side_effects.append(f"shell command: {op.command}")
        side_effect_details.append(
            {
                "kind": "command",
                "action": "execute",
                "command": op.command,
                "destructive": True,
                "effectsInferred": False,
            }
        )
        destructive.append(f"unknown shell command effects: {op.command}")
        destructive_details.append(
            {
                "kind": "command",
                "action": "unknown_effects",
                "command": op.command,
                "destructive": True,
                "effectsInferred": False,
            }
        )
        warnings.append("Shell command effects cannot be inferred")
    elif isinstance(op, PythonScriptOperation):
        script_path = _resolve_path(op.script_path, path_base)
        side_effects.append(f"python script: {script_path}")
        side_effect_details.append(
            _path_detail(
                kind="script",
                action="execute_python",
                path=script_path,
                destructive=True,
                effects_inferred=False,
            )
        )
        destructive.append(f"unknown python script effects: {script_path}")
        destructive_details.append(
            _path_detail(
                kind="script",
                action="unknown_effects",
                path=script_path,
                destructive=True,
                effects_inferred=False,
            )
        )
        warnings.append("Script effects cannot be inferred")
        if not script_path.exists():
            warnings.append(f"Missing python script: {script_path}")
    elif isinstance(op, ShellScriptOperation):
        script_path = _resolve_path(op.script_path, path_base)
        side_effects.append(f"shell script: {script_path}")
        side_effect_details.append(
            _path_detail(
                kind="script",
                action="execute_shell",
                path=script_path,
                destructive=True,
                effects_inferred=False,
            )
        )
        destructive.append(f"unknown shell script effects: {script_path}")
        destructive_details.append(
            _path_detail(
                kind="script",
                action="unknown_effects",
                path=script_path,
                destructive=True,
                effects_inferred=False,
            )
        )
        warnings.append("Script effects cannot be inferred")
        if not script_path.exists():
            warnings.append(f"Missing shell script: {script_path}")
    elif isinstance(op, ReadFileOperation):
        path = _resolve_path(op.path, path_base)
        side_effects.append(f"read file: {path}")
        side_effect_details.append(
            _path_detail(
                kind="file",
                action="read",
                path=path,
                destructive=False,
            )
        )
        if not path.exists():
            warnings.append(f"Missing read target: {path}")
    elif isinstance(op, WorkflowCallOperation):
        side_effects.append(f"run workflow: {op.workflow_id}")
        side_effect_details.append(
            {
                "kind": "workflow",
                "action": "run",
                "workflow_id": op.workflow_id,
                "destructive": True,
                "effectsInferred": False,
            }
        )
        warnings.append("Nested workflow effects depend on the target workflow")
    elif isinstance(op, WriteFileOperation):
        path = _resolve_path(op.path, path_base)
        side_effects.append(f"write file: {path}")
        side_effect_details.append(
            _path_detail(
                kind="file",
                action="append" if op.append else "write",
                path=path,
                destructive=op.append or op.overwrite,
                append=op.append,
                overwrite=op.overwrite,
            )
        )
        if op.append:
            destructive.append(f"append file: {path}")
            destructive_details.append(
                _path_detail(
                    kind="file",
                    action="append",
                    path=path,
                    destructive=True,
                    append=True,
                )
            )
        elif op.overwrite:
            destructive.append(f"overwrite file: {path}")
            destructive_details.append(
                _path_detail(
                    kind="file",
                    action="overwrite",
                    path=path,
                    destructive=True,
                    overwrite=True,
                )
            )
        else:
            warnings.append(f"Write fails if target exists: {path}")
    elif isinstance(op, CopyFileOperation):
        source_path = _resolve_path(op.source_path, path_base)
        destination_path = _resolve_path(op.destination_path, path_base)
        side_effects.append(f"copy file: {source_path} -> {destination_path}")
        side_effect_details.append(
            _two_path_detail(
                kind="file",
                action="copy",
                source_path=source_path,
                destination_path=destination_path,
                destructive=op.overwrite,
                overwrite=op.overwrite,
            )
        )
        if not source_path.exists():
            warnings.append(f"Missing copy source: {source_path}")
        if op.overwrite:
            destructive.append(f"overwrite copy destination: {destination_path}")
            destructive_details.append(
                _path_detail(
                    kind="file",
                    action="overwrite_copy_destination",
                    path=destination_path,
                    destructive=True,
                    overwrite=True,
                )
            )
    elif isinstance(op, MoveFileOperation):
        source_path = _resolve_path(op.source_path, path_base)
        destination_path = _resolve_path(op.destination_path, path_base)
        side_effects.append(f"move file: {source_path} -> {destination_path}")
        side_effect_details.append(
            _two_path_detail(
                kind="file",
                action="move",
                source_path=source_path,
                destination_path=destination_path,
                destructive=True,
                overwrite=op.overwrite,
            )
        )
        destructive.append(f"move source: {source_path}")
        destructive_details.append(
            _two_path_detail(
                kind="file",
                action="move",
                source_path=source_path,
                destination_path=destination_path,
                destructive=True,
                overwrite=op.overwrite,
            )
        )
        if not source_path.exists():
            warnings.append(f"Missing move source: {source_path}")
        if op.overwrite:
            destructive.append(f"overwrite move destination: {destination_path}")
            destructive_details.append(
                _path_detail(
                    kind="file",
                    action="overwrite_move_destination",
                    path=destination_path,
                    destructive=True,
                    overwrite=True,
                )
            )
    elif isinstance(op, DeleteFileOperation):
        path = _resolve_path(op.path, path_base)
        side_effects.append(f"delete file: {path}")
        action = "recursive delete" if op.recursive else "delete"
        side_effect_details.append(
            _path_detail(
                kind="file",
                action="delete",
                path=path,
                destructive=True,
                recursive=op.recursive,
                missing_ok=op.missing_ok,
            )
        )
        destructive.append(f"{action}: {path}")
        destructive_details.append(
            _path_detail(
                kind="file",
                action="recursive_delete" if op.recursive else "delete",
                path=path,
                destructive=True,
                recursive=op.recursive,
                missing_ok=op.missing_ok,
            )
        )
        if not path.exists() and not op.missing_ok:
            warnings.append(f"Missing delete target: {path}")
    elif isinstance(op, FileOperation):
        path = _resolve_path(op.path, path_base)
        side_effects.append(f"reference file: {path}")
        side_effect_details.append(
            _path_detail(
                kind="file",
                action="reference",
                path=path,
                destructive=False,
            )
        )
        if not path.exists():
            warnings.append(f"Missing file resource: {path}")
    elif isinstance(op, FolderOperation):
        path = _resolve_path(op.path, path_base)
        side_effects.append(f"reference folder: {path}")
        side_effect_details.append(
            _path_detail(
                kind="folder",
                action="reference",
                path=path,
                destructive=False,
            )
        )
        if not path.exists():
            warnings.append(f"Missing folder resource: {path}")
    elif isinstance(op, OpenResourceOperation):
        side_effects.append(f"open resource: {op.target}")
        side_effect_details.append(
            {
                "kind": "resource",
                "action": "open",
                "target": op.target,
                "destructive": False,
            }
        )
    elif isinstance(op, PromptFileOperation):
        output_path = _resolve_path(op.output_path, path_base)
        side_effects.append(f"write prompt file: {output_path}")
        detail = _path_detail(
            kind="file",
            action="write_prompt",
            path=output_path,
            destructive=op.overwrite,
            overwrite=op.overwrite,
        )
        if op.template_path is not None:
            detail["sourcePath"] = str(_resolve_path(op.template_path, path_base))
        side_effect_details.append(detail)
        if op.overwrite:
            destructive.append(f"overwrite prompt file: {output_path}")
            destructive_details.append(
                _path_detail(
                    kind="file",
                    action="overwrite_prompt",
                    path=output_path,
                    destructive=True,
                    overwrite=True,
                )
            )
        if op.template_path is not None:
            template_path = _resolve_path(op.template_path, path_base)
            if not template_path.exists():
                warnings.append(f"Missing prompt template: {template_path}")
    elif isinstance(op, CommonLlmTaskOperation):
        side_effects.append(f"provider call: {op.agent_id} {op.task}")
        side_effect_details.append(
            {
                "kind": "provider",
                "action": "call",
                "agentId": op.agent_id,
                "task": op.task,
                "destructive": False,
            }
        )
    elif isinstance(op, LocalVectorizeOperation):
        source_path = _resolve_path(op.source_path, path_base)
        index_path = _resolve_path(op.index_path, path_base)
        side_effects.append(f"scan files: {source_path}")
        side_effect_details.append(
            _two_path_detail(
                kind="file",
                action="vectorize",
                source_path=source_path,
                destination_path=index_path,
                destructive=True,
            )
        )
        destructive.append(f"write vector index: {index_path}")
        destructive_details.append(
            _path_detail(
                kind="file",
                action="write_vector_index",
                path=index_path,
                destructive=True,
            )
        )
        if not source_path.exists():
            warnings.append(f"Missing vectorize source: {source_path}")
    elif isinstance(op, LocalSearchOperation):
        index_path = _resolve_path(op.index_path, path_base)
        side_effects.append(f"read vector index: {index_path}")
        side_effect_details.append(
            _path_detail(
                kind="file",
                action="read_vector_index",
                path=index_path,
                destructive=False,
            )
        )
        if not index_path.exists():
            warnings.append(f"Missing search index: {index_path}")
    elif isinstance(op, HttpRequestOperation):
        parsed = urllib.parse.urlsplit(op.url)
        host = parsed.netloc or "<dynamic-host>"
        configured_secret_fields = {field.lower() for field in op.secret_fields}
        secret_values = _http_plan_secret_values(op, configured_secret_fields)
        side_effects.append(f"http request: {op.method.upper()} {host}")
        side_effect_details.append(
            {
                "kind": "network",
                "action": "http_request",
                "method": op.method.upper(),
                "url": _masked_http_plan_url(
                    op,
                    configured_secret_fields,
                    secret_values,
                ),
                "host": host,
                "params": _masked_http_plan_value(
                    op.params,
                    configured_secret_fields,
                    secret_values=secret_values,
                ),
                "expectedStatuses": list(op.expected_statuses),
                "networkAllowlist": list(op.network_allowlist),
                "destructive": op.method.upper() not in {"GET", "HEAD", "OPTIONS"},
                "effectsInferred": True,
            }
        )
        if "{{" in op.url and "}}" in op.url:
            warnings.append(f"HTTP request URL contains unresolved dynamic values: {op.url}")
        warnings.extend(network_policy_warnings(op.url, op.network_allowlist))
    elif isinstance(op, ApprovalGateOperation):
        side_effects.append("pause for approval")
        side_effect_details.append(
            {
                "kind": "approval",
                "action": "wait",
                "message": op.message,
                "approvers": list(op.approvers),
                "timeoutSeconds": op.timeout_seconds,
                "timeoutDecision": op.timeout_decision,
                "notify": op.notify,
                "destructive": False,
                "effectsInferred": True,
            }
        )
        warnings.append(f"Workflow pauses for approval at node message: {op.message}")
        if "{{" in op.message and "}}" in op.message:
            warnings.append(f"Approval message contains unresolved dynamic values: {op.message}")
    elif isinstance(op, NotificationOperation):
        side_effects.append(f"{op.channel} notification: {op.title}")
        secret_values = _notification_plan_secret_values(op)
        side_effect_details.append(
            {
                "kind": "notification",
                "action": "send",
                "channel": op.channel,
                "title": op.title,
                "body": op.body,
                "urgency": op.urgency,
                "webhookUrl": _masked_notification_plan_url(op.webhook_url, secret_values),
                "headers": _masked_http_plan_value(
                    op.headers,
                    set(),
                    secret_values=secret_values,
                ),
                "payload": _masked_http_plan_value(
                    op.payload,
                    set(),
                    secret_values=secret_values,
                ),
                "emailFrom": _masked_http_plan_value(
                    op.email_from,
                    set(),
                    secret_values=secret_values,
                ),
                "emailTo": _masked_http_plan_value(
                    op.email_to,
                    set(),
                    secret_values=secret_values,
                ),
                "smtpHost": _masked_http_plan_value(
                    op.smtp_host,
                    set(),
                    secret_values=secret_values,
                ),
                "smtpPort": op.smtp_port,
                "smtpUsername": _masked_notification_plan_credential(op.smtp_username),
                "timeoutSeconds": op.timeout_seconds,
                "retry": op.retry.model_dump(),
                "expectedStatuses": list(op.expected_statuses),
                "networkAllowlist": list(op.network_allowlist),
                "destructive": False,
                "effectsInferred": True,
            }
        )
        if "{{" in op.body and "}}" in op.body:
            warnings.append(f"Notification body contains unresolved dynamic values: {op.body}")
    elif isinstance(op, AgentOperation):
        side_effects.append(f"provider call: {op.agent_id}")
        side_effect_details.append(
            {
                "kind": "provider",
                "action": "call",
                "agentId": op.agent_id,
                "destructive": False,
            }
        )
        if op.prompt_path is not None:
            prompt_path = _resolve_path(op.prompt_path, path_base)
            if not prompt_path.exists():
                warnings.append(f"Missing agent prompt file: {prompt_path}")
    return (
        side_effects,
        side_effect_details,
        destructive,
        destructive_details,
        warnings,
    )


def _operation_access_warnings(
    op: object,
    workflow: AgenticWorkflow,
    node_id: str,
    path_base: Path | None,
    trigger_context: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []

    def check(
        path: Path,
        permission: Literal["read", "write", "execute"],
        label: str,
    ) -> None:
        resolved = _resolve_path(path, path_base)
        if _path_has_workflow_access(workflow, resolved, permission, path_base):
            return
        warnings.append(
            f"Node '{node_id}' {label} requires {permission} access to outside path "
            f"{resolved}; add it to workflow filesystem_access."
        )

    if isinstance(op, PythonScriptOperation | ShellScriptOperation):
        check(op.script_path, "execute", "script path")
    elif isinstance(op, ReadFileOperation):
        check(op.path, "read", "read path")
    elif isinstance(op, WriteFileOperation):
        check(op.path, "write", "write path")
    elif isinstance(op, CopyFileOperation):
        check(op.source_path, "read", "copy source path")
        check(op.destination_path, "write", "copy destination path")
    elif isinstance(op, MoveFileOperation):
        check(op.source_path, "write", "move source path")
        check(op.destination_path, "write", "move destination path")
    elif isinstance(op, DeleteFileOperation):
        check(op.path, "write", "delete path")
    elif isinstance(op, FileOperation):
        check(op.path, "read", "file resource path")
    elif isinstance(op, FolderOperation):
        check(op.path, "read", "folder resource path")
    elif isinstance(op, OpenResourceOperation):
        if _open_resource_target_is_local_path(op):
            check(Path(op.target), "read", "open_resource target path")
    elif isinstance(op, PromptFileOperation):
        if op.template_path is not None:
            check(op.template_path, "read", "prompt template path")
        check(op.output_path, "write", "prompt output path")
    elif isinstance(op, LocalVectorizeOperation):
        index_path = _resolve_path(op.index_path, path_base)
        check(op.source_path, "read", "local_vectorize source path")
        check(op.index_path, "write", "local_vectorize index path")
        check(index_path.parent, "write", "local_vectorize index directory")
        check(
            _default_vector_entries_path(index_path),
            "write",
            "local_vectorize entries path",
        )
    elif isinstance(op, LocalSearchOperation):
        check(op.index_path, "read", "local_search index path")

    source = op.source if isinstance(op, LoopOperation) else None
    if isinstance(source, TabularFanSource):
        check(source.path, "read", "tabular fan-out path")
    elif isinstance(source, DirectoryFanSource):
        check(source.path, "read", "directory fan-out path")
    elif isinstance(source, TriggerEventsFanSource) and source.include_content:
        events = trigger_context.get("events")
        if isinstance(events, list):
            for index, event in enumerate(events):
                if not isinstance(event, dict) or not event.get("path"):
                    continue
                check(Path(str(event["path"])), "read", f"trigger event {index} path")

    return warnings


def _default_vector_entries_path(index_path: Path) -> Path:
    return index_path.with_name(f"{index_path.name}.entries.jsonl")


def _open_resource_target_is_local_path(op: OpenResourceOperation) -> bool:
    if op.resource_type == "app":
        return False
    if op.resource_type == "url":
        return False
    return "://" not in op.target


def _path_has_workflow_access(
    workflow: AgenticWorkflow,
    path: Path,
    permission: Literal["read", "write", "execute"],
    path_base: Path | None,
) -> bool:
    if path_base is None:
        return True
    resolved_path = _resolved_for_access(path)
    trusted_root = _resolved_for_access(path_base)
    if resolved_path == trusted_root or trusted_root in resolved_path.parents:
        root_entry = _project_root_access_entry(workflow, trusted_root, path_base)
        return getattr(root_entry, permission) if root_entry is not None else True
    for entry in workflow.config.filesystem_access:
        if not getattr(entry, permission):
            continue
        if _access_entry_covers_path(entry, resolved_path, path_base):
            return True
    return False


def _access_entry_covers_path(
    entry: FilesystemAccessEntry,
    resolved_path: Path,
    path_base: Path | None,
) -> bool:
    entry_path = _resolve_path(entry.path, path_base)
    resolved_entry = _resolved_for_access(entry_path)
    return resolved_path == resolved_entry or resolved_entry in resolved_path.parents


def _project_root_access_entry(
    workflow: AgenticWorkflow,
    trusted_root: Path,
    path_base: Path | None,
) -> FilesystemAccessEntry | None:
    for entry in workflow.config.filesystem_access:
        entry_path = _resolve_path(entry.path, path_base)
        if _resolved_for_access(entry_path) == trusted_root:
            return entry
    return None


def _resolved_for_access(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _agent_registration_warnings(op: object, workflow: AgenticWorkflow) -> list[str]:
    if isinstance(op, (AgentOperation, CommonLlmTaskOperation)):
        if op.agent_id not in workflow.agents:
            return [f"Agent '{op.agent_id}' is not registered in workflow"]
    return []


def _fan_out_plan(
    op: object,
    workflow: AgenticWorkflow,
    trigger_context: dict[str, Any],
    limits: ResourceLimits,
    sample_limit: int,
    path_base: Path | None,
) -> dict[str, Any] | None:
    source = None
    if isinstance(op, LoopOperation):
        source = op.source
    elif isinstance(op, AgentOperation) and op.fan_source is not None:
        source = op.fan_source
    if source is None:
        if isinstance(op, AgentOperation) and op.dynamic_count != 1:
            return _agent_dynamic_count_plan(op, limits, sample_limit)
        return None

    plan: dict[str, Any] = {
        "sourceType": source.type,
        "maxConcurrency": source.max_concurrency,
        "failFast": source.fail_fast,
        "count": None,
        "countExact": False,
        "countLowerBound": None,
        "sampleItems": [],
        "warnings": [],
    }
    try:
        if isinstance(source, CountFanSource):
            if isinstance(source.count, int):
                count = source.count
                sample: list[dict[str, object]] = [
                    {"index": str(i)} for i in range(min(count, sample_limit))
                ]
            elif isinstance(source.count, str) and source.count.strip().isdigit():
                count = int(source.count.strip())
                sample = [{"index": str(i)} for i in range(min(count, sample_limit))]
            elif source.count in (None, ""):
                count = 1
                sample = [{"index": "0"}]
            else:
                count = None
                sample = []
                plan["warnings"].append(f"Unresolved dynamic count expression: {source.count}")
            plan["count"] = count
            plan["countExact"] = count is not None
            plan["countLowerBound"] = count
            plan["sampleItems"] = sample
        elif isinstance(source, TabularFanSource):
            path = _resolve_path(source.path, path_base)
            if not _path_has_workflow_access(workflow, path, "read", path_base):
                plan["warnings"].append(
                    f"Tabular fan-out preview skipped because read access is not granted: {path}"
                )
            elif not path.exists():
                plan["warnings"].append(f"Missing tabular fan-out source: {path}")
            else:
                tabular_count, tabular_sample, tabular_warnings, partial = _preview_tabular(
                    path,
                    limits,
                    sample_limit,
                )
                plan["count"] = tabular_count
                plan["countExact"] = not partial
                plan["countLowerBound"] = tabular_count
                plan["sampleItems"] = tabular_sample
                plan["warnings"].extend(tabular_warnings)
            plan["path"] = str(path)
        elif isinstance(source, DirectoryFanSource):
            path = _resolve_path(source.path, path_base)
            plan["path"] = str(path)
            plan["glob"] = source.glob
            plan["includeContent"] = source.include_content
            if not _path_has_workflow_access(workflow, path, "read", path_base):
                plan["warnings"].append(
                    f"Directory fan-out preview skipped because read access is not granted: {path}"
                )
            elif not path.exists():
                plan["warnings"].append(f"Missing directory fan-out source: {path}")
            elif not path.is_dir():
                plan["warnings"].append(f"Directory fan-out source is not a directory: {path}")
            else:
                directory_count, directory_sample, directory_warnings, scanned, partial = (
                    _preview_directory(source, path, limits, sample_limit)
                )
                plan["count"] = directory_count
                plan["countExact"] = not partial
                plan["countLowerBound"] = directory_count
                plan["sampleItems"] = directory_sample
                plan["scannedPaths"] = scanned
                plan["warnings"].extend(directory_warnings)
        elif isinstance(source, TriggerEventsFanSource):
            if "events" not in trigger_context:
                plan["count"] = None
                plan["warnings"].append(
                    "No trigger context events provided; trigger-event fan-out "
                    "count cannot be estimated"
                )
            elif isinstance(trigger_context["events"], list):
                count, sample, trigger_warnings = _preview_trigger_events(
                    trigger_context["events"],
                    source,
                    sample_limit,
                )
                plan["count"] = count
                plan["countExact"] = True
                plan["countLowerBound"] = count
                plan["sampleItems"] = sample
                plan["warnings"].extend(trigger_warnings)
                if count > limits.max_fanout_items:
                    plan["warnings"].append(
                        f"Trigger-event fan-out count {count} exceeds limit "
                        f"{limits.max_fanout_items}"
                    )
            else:
                plan["warnings"].append("Trigger context events is not a list")
        elif isinstance(source, InfiniteFanSource):
            plan["count"] = None
            plan["warnings"].append("Infinite fan-out count cannot be estimated")
    except Exception as exc:  # noqa: BLE001
        plan["warnings"].append(f"Fan-out estimate failed: {exc}")
    return plan


def _agent_dynamic_count_plan(
    op: AgentOperation,
    limits: ResourceLimits,
    sample_limit: int,
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "sourceType": "agent_dynamic_count",
        "maxConcurrency": None,
        "failFast": True,
        "count": None,
        "countExact": False,
        "countLowerBound": None,
        "sampleItems": [],
        "warnings": ["agent dynamic_count is deprecated; use a loop node feeding this agent"],
    }
    if isinstance(op.dynamic_count, int):
        count = op.dynamic_count
        plan["count"] = count
        plan["countExact"] = True
        plan["countLowerBound"] = count
        plan["sampleItems"] = [{"index": str(i)} for i in range(min(count, sample_limit))]
        if count > limits.max_fanout_items:
            plan["warnings"].append(
                f"Agent dynamic_count {count} exceeds limit {limits.max_fanout_items}"
            )
    elif isinstance(op.dynamic_count, str) and op.dynamic_count.strip().isdigit():
        count = int(op.dynamic_count.strip())
        plan["count"] = count
        plan["countExact"] = True
        plan["countLowerBound"] = count
        plan["sampleItems"] = [{"index": str(i)} for i in range(min(count, sample_limit))]
        if count > limits.max_fanout_items:
            plan["warnings"].append(
                f"Agent dynamic_count {count} exceeds limit {limits.max_fanout_items}"
            )
    else:
        plan["warnings"].append(f"Unresolved dynamic_count expression: {op.dynamic_count}")
    return plan


def _preview_directory(
    source: DirectoryFanSource,
    source_path: Path,
    limits: ResourceLimits,
    sample_limit: int,
) -> tuple[int, list[dict[str, object]], list[str], int, bool]:
    count = 0
    scanned = 0
    partial = False
    sample: list[dict[str, object]] = []
    warnings: list[str] = []

    for path in source_path.glob(source.glob):
        scanned += 1
        if scanned > limits.max_files_scanned:
            partial = True
            warnings.append(
                "Directory fan-out scan exceeded limit "
                f"{limits.max_files_scanned} paths; preview count is partial"
            )
            break
        if not path.is_file():
            continue
        count += 1
        if len(sample) < sample_limit:
            sample.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "sizeBytes": path.stat().st_size,
                }
            )
        if count > limits.max_fanout_items:
            partial = True
            warnings.append(
                f"Directory fan-out count exceeds limit {limits.max_fanout_items} "
                "items; preview count is partial"
            )
            break

    return count, sorted(sample, key=lambda item: str(item["path"])), warnings, scanned, partial


def _preview_tabular(
    path: Path,
    limits: ResourceLimits,
    sample_limit: int,
) -> tuple[int, list[dict[str, object]], list[str], bool]:
    suffix = path.suffix.lower()
    row_limit = limits.max_fanout_items
    scan_limit = max(0, row_limit) + 1
    warnings: list[str] = []
    partial = False

    def _with_row(row: dict[str, object]) -> dict[str, object]:
        return {**row, "_row": json.dumps(row, default=str)}

    def _should_stop(count: int) -> bool:
        nonlocal partial
        if count <= row_limit:
            return False
        partial = True
        warnings.append(
            f"Tabular fan-out count {count} exceeds limit {row_limit}; preview count is partial"
        )
        return True

    count = 0
    sample: list[dict[str, object]] = []
    if suffix == ".jsonl":
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                count += 1
                if len(sample) < sample_limit:
                    sample.append(_with_row(row))
                if count >= scan_limit and _should_stop(count):
                    break
        return count, sample, warnings, partial
    if suffix == ".csv":
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                count += 1
                if len(sample) < sample_limit:
                    sample.append(_with_row(dict(row)))
                if count >= scan_limit and _should_stop(count):
                    break
        return count, sample, warnings, partial
    if suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise ImportError(
                "openpyxl is required for .xlsx support: pip install 'gofer-flow[xlsx]'"
            ) from exc
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = [str(h) for h in next(rows_iter)]
            for row in rows_iter:
                item = dict(zip(headers, row))
                count += 1
                if len(sample) < sample_limit:
                    sample.append(_with_row(item))
                if count >= scan_limit and _should_stop(count):
                    break
            return count, sample, warnings, partial
        finally:
            wb.close()
    raise ValueError(f"Unsupported tabular format: {suffix!r}. Use .jsonl, .csv, or .xlsx")


def _file_path_data(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "file_path": str(path),
        "file_name": path.name,
        "file_stem": path.stem,
        "file_extension": path.suffix,
        "parent_path": str(path.parent),
        "directory": str(path.parent),
    }


def _path_detail(
    *,
    kind: str,
    action: str,
    path: Path,
    destructive: bool,
    effects_inferred: bool = True,
    **extra: object,
) -> dict[str, object]:
    return {
        "kind": kind,
        "action": action,
        "path": str(path),
        "exists": path.exists(),
        "destructive": destructive,
        "effectsInferred": effects_inferred,
        **extra,
    }


def _two_path_detail(
    *,
    kind: str,
    action: str,
    source_path: Path,
    destination_path: Path,
    destructive: bool,
    effects_inferred: bool = True,
    **extra: object,
) -> dict[str, object]:
    return {
        "kind": kind,
        "action": action,
        "sourcePath": str(source_path),
        "sourceExists": source_path.exists(),
        "destinationPath": str(destination_path),
        "destinationExists": destination_path.exists(),
        "destructive": destructive,
        "effectsInferred": effects_inferred,
        **extra,
    }


def _dedupe_details(details: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: dict[str, dict[str, object]] = {}
    for detail in details:
        deduped[json.dumps(detail, sort_keys=True, default=str)] = detail
    return [deduped[key] for key in sorted(deduped)]


def _preview_trigger_events(
    events: list[object],
    source: TriggerEventsFanSource,
    sample_limit: int,
) -> tuple[int, list[dict[str, object]], list[str]]:
    count = 0
    sample: list[dict[str, object]] = []
    warnings: list[str] = []
    skipped_non_dict = 0
    content_omitted = False

    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            skipped_non_dict += 1
            continue

        count += 1
        if len(sample) >= sample_limit:
            continue

        item: dict[str, object] = {
            **event,
            "index": str(idx),
            "event_json": json.dumps(event, default=str),
        }
        path = event.get("path")
        if path:
            event_path = Path(str(path))
            item.update(_file_path_data(event_path))
            item.setdefault("name", event_path.name)
            item.setdefault("directory", str(event_path.parent))
            if event_path.exists() and event_path.is_file():
                size = event_path.stat().st_size
                item["sizeBytes"] = size
                if source.include_content:
                    item["contentIncluded"] = False
                    content_omitted = True
            elif source.include_content:
                warnings.append(f"Missing trigger event file: {event_path}")
        sample.append(item)

    if skipped_non_dict:
        warnings.append(
            f"Skipped {skipped_non_dict} non-object trigger event"
            f"{'' if skipped_non_dict == 1 else 's'}"
        )
    if content_omitted:
        warnings.append("Trigger event file content omitted from plan preview")
    return count, sample, warnings


def _required_secrets(
    op: object,
    workflow: AgenticWorkflow,
    path_base: Path | None,
    data_dir: Path | None,
    seen_components: set[Path] | None = None,
) -> list[str]:
    values: dict[str, str] = {}
    profile_secrets: set[str] = set()
    if isinstance(op, (BashCommandOperation, PythonScriptOperation, ShellScriptOperation)):
        values.update(op.env)
    elif isinstance(op, AgentOperation):
        agent = workflow.agents.get(op.agent_id)
        if agent is not None:
            values.update(agent.env)
            settings = resolve_provider_settings(
                agent_subscription=agent.subscription,
                profile_name=agent.profile,
                agent_model=agent.model,
                agent_effort=agent.effort,
                operation_profile=op.profile,
                operation_model=op.model,
                operation_effort=op.effort,
                operation_timeout=op.timeout,
                data_dir=data_dir,
            )
            profile_secrets.update(settings.secret_refs.values())
            if settings.api_key_secret:
                profile_secrets.add(settings.api_key_secret)
            elif settings.api_key_env:
                profile_secrets.add(settings.api_key_env)
    elif isinstance(op, CommonLlmTaskOperation):
        agent = workflow.agents.get(op.agent_id)
        if agent is not None:
            values.update(agent.env)
            settings = resolve_provider_settings(
                agent_subscription=agent.subscription,
                profile_name=agent.profile,
                agent_model=agent.model,
                agent_effort=agent.effort,
                operation_profile=op.profile,
                operation_model=op.model,
                operation_effort=op.effort,
                operation_timeout=op.timeout,
                data_dir=data_dir,
            )
            profile_secrets.update(settings.secret_refs.values())
            if settings.api_key_secret:
                profile_secrets.add(settings.api_key_secret)
            elif settings.api_key_env:
                profile_secrets.add(settings.api_key_env)
    elif isinstance(op, HttpRequestOperation):
        for field, value in _iter_strings(op.model_dump(by_alias=True)):
            values[field] = value
    elif isinstance(op, NotificationOperation):
        for field, value in _iter_strings(op.model_dump()):
            values[field] = value
    elif isinstance(op, SubflowOperation):
        component, _component_path = _subflow_component_for_plan(op, path_base)
        if component is not None and component.component is not None:
            profile_secrets.update(component.component.secret_requirements)
            profile_secrets.update(
                _nested_subflow_secrets(
                    component,
                    _component_path,
                    data_dir,
                    seen_components,
                )
            )
        for secret in op.secret_requirements:
            profile_secrets.add(secret)
        for field, value in _iter_strings(op.model_dump()):
            values[field] = value
    return sorted(
        profile_secrets
        | {
            secret
            for value in values.values()
            for secret in workflow_secret_reference_names(str(value))
            if secret is not None
        }
        | {value[4:] for value in values.values() if str(value).startswith("env:")}
    )


def _provider_requirements(
    op: object,
    workflow: AgenticWorkflow,
    path_base: Path | None,
    data_dir: Path | None,
    seen_components: set[Path] | None = None,
) -> list[dict[str, Any]]:
    if isinstance(op, SubflowOperation):
        component, component_path = _subflow_component_for_plan(op, path_base)
        component_requirements: list[dict[str, Any]] = []
        if component is not None and component.component is not None:
            component_requirements = component.component.provider_requirements
        source_path = str(component_path) if component_path is not None else None
        requirements = [
            _subflow_provider_requirement(requirement, op, path_base, source_path)
            for requirement in [*component_requirements, *op.provider_requirements]
        ]
        requirements.extend(
            _nested_subflow_provider_requirements(
                component,
                component_path,
                data_dir,
                seen_components,
            )
        )
        return requirements
    if isinstance(op, (AgentOperation, CommonLlmTaskOperation)):
        agent = workflow.agents.get(op.agent_id)
        if agent is None:
            return []
        settings = resolve_provider_settings(
            agent_subscription=agent.subscription,
            profile_name=agent.profile,
            agent_model=agent.model,
            agent_effort=agent.effort,
            operation_profile=op.profile,
            operation_model=op.model,
            operation_effort=op.effort,
            operation_timeout=op.timeout,
            data_dir=data_dir,
        )
        validation_errors: list[str] = []
        try:
            validate_provider_settings(settings)
        except ValueError as exc:
            validation_errors.append(str(exc))
        extra_paths = _configured_extra_paths(agent, path_base)
        binary = _provider_binary(settings.subscription)
        is_direct = settings.subscription in DIRECT_API_SUBSCRIPTIONS
        available = is_direct or (
            binary is not None
            and resolve_provider_executable(cast(ProviderId, settings.subscription)) is not None
        )
        requirement = {
            "agentId": agent.agent_id,
            "subscription": settings.subscription,
            "profile": settings.profile_name,
            "model": settings.model,
            "timeout": settings.timeout,
            "workingDir": str(_resolve_path(op.working_dir, path_base)),
            "binary": binary,
            "available": available,
            "directApi": is_direct,
            "apiBaseUrl": settings.api_base_url if is_direct else None,
            "extraPaths": extra_paths,
        }
        if validation_errors:
            requirement["validationErrors"] = validation_errors
        return [requirement]
    return []


def _subflow_provider_requirement(
    requirement: dict[str, Any],
    op: SubflowOperation,
    path_base: Path | None,
    source_path: str | None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "agentId": requirement.get("agentId") or requirement.get("agent_id") or "subflow",
        "subscription": (
            requirement.get("subscription") or requirement.get("provider") or "component"
        ),
        "profile": requirement.get("profile"),
        "model": requirement.get("model"),
        "timeout": requirement.get("timeout"),
        "workingDir": str(path_base or Path(".")),
        "binary": requirement.get("binary"),
        "available": True,
        "directApi": False,
        "apiBaseUrl": None,
        "extraPaths": [],
        "componentId": op.component_id,
    }
    if source_path is not None:
        item["sourcePath"] = source_path
    return item


def _filesystem_requirements(
    op: object,
    path_base: Path | None,
    seen_components: set[Path] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(op, SubflowOperation):
        return []
    component, component_path = _subflow_component_for_plan(op, path_base)
    requirements: list[dict[str, Any]] = []
    if component is not None and component.component is not None:
        for component_entry in component.component.filesystem_access:
            requirements.append(
                _subflow_filesystem_requirement(
                    component_entry.model_dump(mode="json", exclude_none=True),
                    op,
                    source="component",
                    component_path=component_path,
                )
            )
    for node_requirement in op.filesystem_access:
        requirements.append(
            _subflow_filesystem_requirement(
                node_requirement,
                op,
                source="node",
                component_path=component_path,
            )
        )
    requirements.extend(
        _subflow_internal_filesystem_requirements(
            component,
            component_path,
            op,
        )
    )
    requirements.extend(
        _nested_subflow_filesystem_requirements(
            component,
            component_path,
            seen_components,
        )
    )
    return requirements


def _subflow_internal_filesystem_requirements(
    component: AgenticWorkflow | None,
    component_path: Path | None,
    op: SubflowOperation,
) -> list[dict[str, Any]]:
    if component is None or component_path is None:
        return []
    requirements: list[dict[str, Any]] = []
    nested_base = component_path.parent
    for node in component.graph.nodes_in_order():
        if isinstance(node.operation, SubflowOperation):
            continue
        for requirement in _operation_filesystem_requirement_items(
            node.operation,
            nested_base,
        ):
            item = _subflow_filesystem_requirement(
                requirement,
                op,
                source="internal_node",
                component_path=component_path,
            )
            item["nodeId"] = node.node_id
            item["field"] = requirement.get("field")
            item["resolvedPath"] = requirement.get("resolvedPath")
            requirements.append(item)
    return requirements


def _nested_subflow_secrets(
    component: AgenticWorkflow | None,
    component_path: Path | None,
    data_dir: Path | None,
    seen_components: set[Path] | None,
) -> set[str]:
    if component is None or component_path is None:
        return set()
    resolved_component_path = _resolved_for_access(component_path)
    seen = set(seen_components or set())
    if resolved_component_path in seen:
        return set()
    seen.add(resolved_component_path)
    nested_base = component_path.parent
    secrets: set[str] = set()
    for node in component.graph.nodes_in_order():
        secrets.update(
            _required_secrets(
                node.operation,
                component,
                nested_base,
                data_dir,
                seen,
            )
        )
    return secrets


def _nested_subflow_provider_requirements(
    component: AgenticWorkflow | None,
    component_path: Path | None,
    data_dir: Path | None,
    seen_components: set[Path] | None,
) -> list[dict[str, Any]]:
    if component is None or component_path is None:
        return []
    resolved_component_path = _resolved_for_access(component_path)
    seen = set(seen_components or set())
    if resolved_component_path in seen:
        return []
    seen.add(resolved_component_path)
    nested_base = component_path.parent
    requirements: list[dict[str, Any]] = []
    for node in component.graph.nodes_in_order():
        requirements.extend(
            _provider_requirements(
                node.operation,
                component,
                nested_base,
                data_dir,
                seen,
            )
        )
    return requirements


def _nested_subflow_filesystem_requirements(
    component: AgenticWorkflow | None,
    component_path: Path | None,
    seen_components: set[Path] | None,
) -> list[dict[str, Any]]:
    if component is None or component_path is None:
        return []
    resolved_component_path = _resolved_for_access(component_path)
    seen = set(seen_components or set())
    if resolved_component_path in seen:
        return []
    seen.add(resolved_component_path)
    nested_base = component_path.parent
    requirements: list[dict[str, Any]] = []
    for node in component.graph.nodes_in_order():
        requirements.extend(_filesystem_requirements(node.operation, nested_base, seen))
    return requirements


def _operation_filesystem_requirement_items(
    op: object,
    path_base: Path | None,
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []

    def add(
        path: Path,
        permission: Literal["read", "write", "execute"],
        field: str,
    ) -> None:
        requirements.append(
            {
                "path": str(path),
                "read": permission == "read",
                "write": permission == "write",
                "execute": permission == "execute",
                "field": field,
                "resolvedPath": str(_resolve_path(path, path_base)),
            }
        )

    if isinstance(op, PythonScriptOperation | ShellScriptOperation):
        add(op.script_path, "execute", "operation.script_path")
    elif isinstance(op, ReadFileOperation):
        add(op.path, "read", "operation.path")
    elif isinstance(op, WriteFileOperation):
        add(op.path, "write", "operation.path")
    elif isinstance(op, CopyFileOperation):
        add(op.source_path, "read", "operation.source_path")
        add(op.destination_path, "write", "operation.destination_path")
    elif isinstance(op, MoveFileOperation):
        add(op.source_path, "write", "operation.source_path")
        add(op.destination_path, "write", "operation.destination_path")
    elif isinstance(op, DeleteFileOperation):
        add(op.path, "write", "operation.path")
    elif isinstance(op, FileOperation):
        add(op.path, "read", "operation.path")
    elif isinstance(op, FolderOperation):
        add(op.path, "read", "operation.path")
    elif isinstance(op, OpenResourceOperation):
        if _open_resource_target_is_local_path(op):
            add(Path(op.target), "read", "operation.target")
    elif isinstance(op, PromptFileOperation):
        if op.template_path is not None:
            add(op.template_path, "read", "operation.template_path")
        add(op.output_path, "write", "operation.output_path")
    elif isinstance(op, LocalVectorizeOperation):
        index_path = _resolve_path(op.index_path, path_base)
        add(op.source_path, "read", "operation.source_path")
        add(op.index_path, "write", "operation.index_path")
        add(index_path.parent, "write", "operation.index_path")
        add(_default_vector_entries_path(index_path), "write", "operation.index_path")
    elif isinstance(op, LocalSearchOperation):
        add(op.index_path, "read", "operation.index_path")

    source = op.source if isinstance(op, LoopOperation) else None
    if isinstance(source, TabularFanSource):
        add(source.path, "read", "operation.source.path")
    elif isinstance(source, DirectoryFanSource):
        add(source.path, "read", "operation.source.path")

    return requirements


def _subflow_filesystem_requirement(
    requirement: dict[str, Any],
    op: SubflowOperation,
    *,
    source: str,
    component_path: Path | None,
) -> dict[str, Any]:
    item = {
        "path": str(requirement.get("path") or ""),
        "read": bool(requirement.get("read", True)),
        "write": bool(requirement.get("write", True)),
        "execute": bool(requirement.get("execute", False)),
        "componentId": op.component_id,
        "source": source,
    }
    if component_path is not None:
        item["sourcePath"] = str(component_path)
    return item


def _subflow_component_for_plan(
    op: SubflowOperation,
    path_base: Path | None,
) -> tuple[AgenticWorkflow | None, Path | None]:
    if path_base is None:
        return None, None
    if op.source_path is not None:
        source = op.source_path.expanduser()
        candidate = source if source.is_absolute() else path_base / source
        if not candidate.exists():
            return None, candidate
        try:
            return AgenticWorkflow.from_file(candidate), candidate
        except Exception:
            return None, candidate
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
            return workflow, candidate
    for path in sorted(path_base.rglob("*.toml")):
        try:
            workflow = AgenticWorkflow.from_file(path)
        except Exception:
            continue
        if workflow.component is not None and workflow.component.id == component_id:
            return workflow, path
    return None, None


def _provider_binary(subscription: str) -> str | None:
    if subscription == "antigravity":
        return "agy"
    if subscription in {"cursor", "copilot", "opencode", "antigravity", "grok"}:
        return "cursor-agent" if subscription == "cursor" else subscription
    if subscription == "codex":
        return "codex"
    if subscription == "claude_code":
        return "claude"
    return None


def _configured_extra_paths(
    agent: Any,
    path_base: Path | None,
) -> list[str]:
    if path_base is None:
        try:
            return [str(path) for path in configured_extra_paths(agent)]
        except Exception:
            return [str(path) for path in agent.extra_paths]
    paths: list[str] = []
    for extra_path in agent.extra_paths:
        path = _resolve_path(extra_path, path_base)
        try:
            paths.append(str(path.resolve()))
        except OSError:
            paths.append(str(path))
    return paths


def _working_dir(
    op: object,
    workflow: AgenticWorkflow,
    path_base: Path | None,
) -> str | None:
    if isinstance(op, BashCommandOperation):
        return str(_resolve_path(op.working_dir, path_base)) if op.working_dir is not None else None
    if isinstance(op, (AgentOperation, CommonLlmTaskOperation)):
        return str(_resolve_path(op.working_dir, path_base))
    agent_id = getattr(op, "agent_id", None)
    if isinstance(agent_id, str):
        agent = workflow.agents.get(agent_id)
        if agent is not None:
            return str(_resolve_path(agent.working_dir, path_base))
    return None


def _unresolved_values(node: GraphNode, workflow: AgenticWorkflow) -> list[str]:
    values: list[str] = []
    node_ids = {workflow_node.node_id for workflow_node in workflow.graph.nodes_in_order()}
    for key, value in node.inputs.items():
        if isinstance(value, str) and _is_dynamic_reference(value, node_ids):
            values.append(f"{node.node_id}.inputs.{key}={value}")
    op = node.operation
    if isinstance(op, LoopOperation) and isinstance(op.source, CountFanSource):
        if (
            isinstance(op.source.count, str)
            and op.source.count.strip()
            and not op.source.count.strip().isdigit()
        ):
            values.append(f"{node.node_id}.fan_source.count={op.source.count}")
    if (
        isinstance(op, AgentOperation)
        and isinstance(op.dynamic_count, str)
        and op.dynamic_count.strip()
        and not op.dynamic_count.strip().isdigit()
    ):
        values.append(f"{node.node_id}.dynamic_count={op.dynamic_count}")
    for field, value in _iter_strings(op.model_dump(by_alias=True)):
        if field == "type" or field.endswith(".type"):
            continue
        if "{{" in value and "}}" in value:
            values.append(f"{node.node_id}.{field}={value}")
        elif _is_dynamic_reference(value, node_ids):
            values.append(f"{node.node_id}.{field}={value}")
    return sorted(set(values))


def _is_dynamic_reference(value: str, node_ids: set[str]) -> bool:
    expression = value.strip().strip("{}").strip()
    if not expression:
        return False
    if expression in node_ids:
        return True
    if "." not in expression:
        return False
    root = expression.split(".", 1)[0]
    return root in {"trigger", "params", "loop", "previous", *node_ids}


def _iter_strings(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(prefix, value)]
    if isinstance(value, Path):
        return [(prefix, str(value))]
    if isinstance(value, dict):
        items: list[tuple[str, str]] = []
        for key, nested in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_iter_strings(nested, next_prefix))
        return items
    if isinstance(value, list):
        items = []
        for index, nested in enumerate(value):
            items.extend(_iter_strings(nested, f"{prefix}[{index}]"))
        return items
    return []


def _secret_reference_names(value: str) -> list[str]:
    match = SECRET_REF_PATTERN.match(value.strip())
    names = [match.group(1) or match.group(2)] if match is not None else []
    names.extend(match.group(1) for match in SECRET_INTERPOLATION_PATTERN.finditer(value))
    return names


def _is_sensitive_field(path: str, configured: set[str]) -> bool:
    normalized = path.lower()
    if normalized in configured:
        return True
    name = normalized.rsplit(".", maxsplit=1)[-1]
    return name in SENSITIVE_FIELD_NAMES or any(
        token in name for token in ("token", "secret", "password")
    )


def _collect_plan_leaf_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, dict):
        values: set[str] = set()
        for item in value.values():
            values.update(_collect_plan_leaf_strings(item))
        return values
    if isinstance(value, list):
        values = set()
        for item in value:
            values.update(_collect_plan_leaf_strings(item))
        return values
    if value is None:
        return set()
    text = str(value)
    return {text} if text else set()


def _collect_http_plan_secret_values(
    value: object,
    configured: set[str],
    path: str = "",
) -> set[str]:
    if isinstance(value, dict):
        values: set[str] = set()
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _is_sensitive_field(child_path, configured):
                values.update(_collect_plan_leaf_strings(item))
            else:
                values.update(_collect_http_plan_secret_values(item, configured, child_path))
        return values
    if isinstance(value, list):
        values = set()
        for item in value:
            values.update(_collect_http_plan_secret_values(item, configured, path))
        return values
    if path and _is_sensitive_field(path, configured):
        return _collect_plan_leaf_strings(value)
    return set()


def _http_plan_secret_values(
    op: HttpRequestOperation,
    configured: set[str],
) -> set[str]:
    values: set[str] = set()
    if _is_sensitive_field("url", configured):
        values.update(_collect_plan_leaf_strings(op.url))
    parsed = urllib.parse.urlsplit(op.url)
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if _is_sensitive_field(key, configured):
            values.update(_collect_plan_leaf_strings(value))
    values.update(_collect_http_plan_secret_values(op.headers, configured))
    values.update(_collect_http_plan_secret_values(op.params, configured))
    values.update(_collect_http_plan_secret_values(op.json_payload, configured))
    if op.body is not None:
        values.update(_collect_http_plan_secret_values(op.body, configured, "body"))
    return {value for value in values if value}


def _replace_http_plan_secret_values(value: str, secret_values: set[str]) -> str:
    masked = value
    for secret_value in sorted(secret_values, key=len, reverse=True):
        masked = masked.replace(secret_value, "***")
    return masked


def _masked_http_plan_value(
    value: object,
    configured: set[str],
    path: str = "",
    *,
    secret_values: set[str] | None = None,
) -> object:
    if isinstance(value, dict):
        masked: dict[str, object] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _is_sensitive_field(child_path, configured) or (
                isinstance(item, str) and _secret_reference_names(item)
            ):
                masked[str(key)] = "***"
            else:
                masked[str(key)] = _masked_http_plan_value(
                    item,
                    configured,
                    child_path,
                    secret_values=secret_values,
                )
        return masked
    if isinstance(value, list):
        return [
            _masked_http_plan_value(
                item,
                configured,
                path,
                secret_values=secret_values,
            )
            for item in value
        ]
    if isinstance(value, str) and _secret_reference_names(value):
        return "***"
    if isinstance(value, str) and secret_values:
        return _replace_http_plan_secret_values(value, secret_values)
    return value


def _masked_http_plan_url(
    op: HttpRequestOperation,
    configured: set[str],
    secret_values: set[str],
) -> str:
    if _is_sensitive_field("url", configured) or _secret_reference_names(op.url):
        return "***"
    parsed = urllib.parse.urlsplit(op.url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    masked_pairs = [
        (
            key,
            "***"
            if _is_sensitive_field(key, configured) or _secret_reference_names(value)
            else _replace_http_plan_secret_values(value, secret_values),
        )
        for key, value in query_pairs
    ]
    masked_url = urllib.parse.urlunsplit(
        parsed._replace(query=urllib.parse.urlencode(masked_pairs))
    )
    return _replace_http_plan_secret_values(masked_url, secret_values)


def _notification_plan_secret_values(op: NotificationOperation) -> set[str]:
    values: set[str] = set()
    values.update(_collect_http_plan_secret_values(op.headers, set()))
    values.update(_collect_http_plan_secret_values(op.payload, set()))
    values.update(_collect_http_plan_secret_values(op.smtp_username, set(), "smtp_username"))
    values.update(_collect_http_plan_secret_values(op.smtp_password, set(), "smtp_password"))
    if op.webhook_url:
        parsed = urllib.parse.urlsplit(op.webhook_url)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            if _is_sensitive_field(key, set()):
                values.add(value)
    return {value for value in values if value}


def _masked_notification_plan_credential(value: str | None) -> str | None:
    return "***" if value is not None else None


def _masked_notification_plan_url(url: str | None, secret_values: set[str]) -> str | None:
    if url is None:
        return None
    return "***"


def _fan_source_label(source: object, path_base: Path | None) -> str:
    if isinstance(source, CountFanSource):
        return f"count={source.count}"
    if isinstance(source, TabularFanSource):
        return f"tabular {_resolve_path(source.path, path_base)}"
    if isinstance(source, DirectoryFanSource):
        return f"directory {_resolve_path(source.path, path_base)} glob={source.glob}"
    if isinstance(source, TriggerEventsFanSource):
        return "trigger events"
    if isinstance(source, InfiniteFanSource):
        return "infinite"
    return str(source)


def _resolve_path(path: Path, path_base: Path | None) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute() or path_base is None:
        return expanded
    return path_base / expanded


def plan_to_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, indent=2, sort_keys=True, default=str)
