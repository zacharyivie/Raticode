from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console
from rich.table import Table

from gofer.core.agent import AgentConfig
from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.core.bundles import (
    BundleError,
    export_workflow_bundle,
    import_workflow_bundle,
    preview_workflow_bundle,
)
from gofer.core.executor import ResumeOptions, WorkflowExecutor
from gofer.core.graph import EdgeConditionType, EdgeConfig, GraphNode
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
    HttpRetryPolicy,
    InfiniteFanSource,
    LocalSearchOperation,
    LocalVectorizeOperation,
    LoopOperation,
    MoveFileOperation,
    NotificationOperation,
    OpenResourceOperation,
    Operation,
    OperationType,
    PassOperation,
    PromptFileOperation,
    PythonScriptOperation,
    ReadFileOperation,
    ShellScriptOperation,
    StartOperation,
    TabularFanSource,
    TriggerEventsFanSource,
    WriteFileOperation,
)
from gofer.core.planner import build_execution_plan, plan_to_json
from gofer.core.revisions import (
    WorkflowRevisionError,
    capture_workflow_revision,
    diff_workflow_revision,
    list_workflow_revisions,
    restore_workflow_revision,
)
from gofer.core.run_outputs import write_run_node_outputs_payload
from gofer.core.secrets import workflow_secret_readiness
from gofer.core.structured_output import OutputFieldOperator
from gofer.core.templates import (
    create_workflow_from_template,
    list_workflow_templates,
    preview_workflow_template,
)
from gofer.core.usage import summarize_node_outputs
from gofer.core.validation import validate_workflow_file
from gofer.core.workflow import (
    AgenticWorkflow,
    ScheduleConfig,
    WatchConfig,
    WorkflowConfig,
    masked_workflow_parameters,
    resolve_workflow_parameters,
)
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription
from gofer.ui.api import (
    WorkflowAlreadyExistsError,
    WorkflowCreateError,
    WorkflowLogError,
    WorkflowTriggerError,
    WorkflowUpdateError,
    duplicate_workflow_payload,
    import_workflow_payload,
    latest_workflow_log_payload,
    list_workflow_payloads,
    list_workflow_run_logs_payload,
    prune_workflow_run_logs_payload,
    rename_workflow_payload,
    replay_workflow_trigger_payload,
    trigger_workflow_payload,
    workflow_run_log_payload,
    write_run_summary_payload,
)
from gofer.ui.chat import delete_workflow_chat_prompt
from gofer.utils.paths import get_data_dir
from gofer.utils.registry import find_workflow
from gofer.utils.run_state import request_workflow_stop, workflow_stop_path

app = typer.Typer(help="Manage and run workflows", no_args_is_help=True)
recipe_app = typer.Typer(help="Create common workflow patterns", no_args_is_help=True)
app.add_typer(recipe_app, name="recipe")
logs_app = typer.Typer(help="Inspect workflow run logs", no_args_is_help=True)
app.add_typer(logs_app, name="logs")
console = Console()

_SUBSCRIPTIONS = {
    "claude_code": ClaudeCodeSubscription(),
    "codex": CodexSubscription(),
    "openai_api": OpenAiApiSubscription(),
    "anthropic_api": AnthropicApiSubscription(),
}


def _write_cli_run_artifacts(result: Any, limits: Any, base: Path) -> None:
    if result.log_path is None:
        return
    write_run_node_outputs_payload(result, limits)
    write_run_summary_payload(base, result.workflow_id, result.log_path)


def _resolve_workflow(name: str, data_dir: Path | None) -> AgenticWorkflow:
    """Resolve a workflow name/ID or file path."""
    path = Path(name)
    if path.suffix == ".toml" and path.exists():
        return AgenticWorkflow.from_file(path)
    return find_workflow(name, data_dir)


def _resolve_workflow_with_path(name: str, data_dir: Path | None) -> tuple[AgenticWorkflow, Path]:
    """Resolve a workflow and the TOML path that should be updated."""
    path = Path(name)
    if path.suffix == ".toml":
        if not path.exists():
            raise KeyError(f"Workflow file '{path}' not found")
        return AgenticWorkflow.from_file(path), path

    base = data_dir or get_data_dir()
    candidate = base / f"{name}.toml"
    if candidate.exists():
        return AgenticWorkflow.from_file(candidate), candidate

    for candidate in sorted(base.glob("*.toml")) if base.exists() else []:
        try:
            wf = AgenticWorkflow.from_file(candidate)
        except Exception:
            continue
        if wf.config.id == name:
            return wf, candidate

    raise KeyError(f"Workflow '{name}' not found in {base}")


def _log_base_dir(data_dir: Path | None) -> Path:
    return (data_dir or get_data_dir()) / "logs"


def _agent_access_warnings(
    wf: AgenticWorkflow,
    path_base: Path | None = None,
) -> list[str]:
    return [
        warning
        for warning in wf.resource_warnings(path_base)
        if "grants provider filesystem access outside working_dir" in warning
    ]


def _print_agent_access_summary(
    wf: AgenticWorkflow,
    path_base: Path | None = None,
) -> None:
    warnings = _agent_access_warnings(wf, path_base)
    if not warnings:
        return
    console.print("[yellow]Agent filesystem access outside working_dir:[/yellow]")
    for warning in warnings:
        console.print(f"[yellow]- {warning}[/yellow]")


def _print_local_vector_index_stats(data: dict[str, Any]) -> None:
    if "indexed_file_count" not in data or "chunk_count" not in data:
        return
    strategy = data.get("strategy")
    search_strategy = data.get("search_strategy")
    console.print(
        "[dim]Index stats: "
        f"{data.get('indexed_file_count')} files, "
        f"{data.get('chunk_count')} chunks, "
        f"{data.get('index_size_bytes')} bytes, "
        f"last update {data.get('last_update_time')}, "
        f"strategy {strategy}, search {search_strategy}, "
        f"{data.get('stale_files')} stale, {data.get('deleted_files')} deleted"
        "[/dim]"
    )


def _print_bundle_import_plan(plan: dict[str, Any], *, dry_run: bool) -> None:
    title = "Bundle import preview" if dry_run else "Imported bundle"
    console.print(
        f"[bold]{title}[/bold] for [cyan]{plan['workflowId']}[/cyan] → {plan['workflowPath']}"
    )
    created = plan.get("filesToCreate") or []
    overwritten = plan.get("filesToOverwrite") or []
    conflicts = plan.get("conflicts") or []
    required_secrets = plan.get("requiredSecrets") or []
    external_requirements = plan.get("externalRequirements") or []
    risk_warnings = plan.get("riskWarnings") or []

    if created:
        console.print("[green]Will create:[/green]")
        for path in created:
            console.print(f"  • {path}")
    if overwritten:
        console.print("[yellow]Will overwrite:[/yellow]")
        for path in overwritten:
            console.print(f"  • {path}")
    if conflicts:
        console.print("[yellow]Conflicts:[/yellow]")
        for conflict in conflicts:
            console.print(f"  • {conflict['path']}: {conflict['action']}")
    if risk_warnings:
        console.print("[red]High-risk configuration:[/red]")
        for warning in risk_warnings:
            console.print(f"  • {warning}")
    if required_secrets:
        console.print(
            "[yellow]Required secrets:[/yellow] "
            + ", ".join(str(item.get("name")) for item in required_secrets)
        )
    if external_requirements:
        console.print("[yellow]External requirements:[/yellow]")
        for item in external_requirements:
            console.print(f"  • {item.get('path')}: {item.get('reason')}")


def _parse_trigger_context(trigger_json: str | None) -> dict[str, Any] | None:
    if not trigger_json:
        return None
    try:
        data = json.loads(trigger_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--trigger-json must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise typer.BadParameter("--trigger-json must decode to an object")
    return data


def _parse_param_values(
    param_values: list[str] | None,
    params_json: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if params_json:
        try:
            parsed = json.loads(params_json)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"--params-json must be valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise typer.BadParameter("--params-json must decode to an object")
        params.update({str(key): value for key, value in parsed.items()})
    for item in param_values or []:
        if "=" not in item:
            raise typer.BadParameter("--param values must be KEY=VALUE")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter("--param keys cannot be empty")
        try:
            params[key] = json.loads(raw)
        except json.JSONDecodeError:
            params[key] = raw
    return params


def _prompt_missing_params(wf: AgenticWorkflow, params: dict[str, Any]) -> dict[str, Any]:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return params
    prompted = dict(params)
    for name, spec in wf.config.declared_inputs.items():
        if name in prompted or spec.default is not None or not spec.required:
            continue
        if spec.type == "boolean":
            prompted[name] = typer.confirm(spec.label or name)
        else:
            prompted[name] = typer.prompt(spec.label or name, hide_input=spec.type == "secret")
    return prompted


def _parse_json_payload(payload_json: str | None, payload_file: Path | None) -> Any:
    if payload_json and payload_file is not None:
        raise typer.BadParameter("Use either --payload-json or --payload-file, not both")
    if payload_file is not None:
        try:
            payload_json = payload_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise typer.BadParameter(str(exc)) from exc
    if not payload_json:
        return {}
    try:
        return json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"payload must be valid JSON: {exc}") from exc


def _parse_headers(header_values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for header in header_values:
        if ":" not in header:
            raise typer.BadParameter("--header values must be formatted as Name: value")
        name, value = header.split(":", 1)
        name = name.strip()
        if not name:
            raise typer.BadParameter("--header name cannot be empty")
        headers[name] = value.strip()
    return headers


def _print_execution_plan(plan: dict[str, Any]) -> None:
    console.print(
        f"[bold]Execution plan[/bold] for [cyan]{plan['workflowId']}[/cyan] "
        f"({plan['workflowName']})"
    )
    start_nodes = plan.get("startNodes") or []
    if start_nodes:
        console.print("[bold]Start nodes:[/bold] " + ", ".join(str(node) for node in start_nodes))

    validation = plan.get("validation") or {}
    diagnostics = validation.get("diagnostics") if isinstance(validation, dict) else None
    if isinstance(diagnostics, list) and diagnostics:
        console.print("[bold]Validation diagnostics:[/bold]")
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or "warning")
            subject = str(item.get("subject") or item.get("targetId") or "")
            suffix = f" [{subject}]" if subject else ""
            console.print(f"  • {severity}: {item.get('message')}{suffix}")

    trigger = plan.get("triggerContext") or {}
    if trigger:
        trigger_parts = []
        if trigger.get("schedule"):
            schedule = trigger["schedule"]
            trigger_parts.append(
                f"schedule={schedule.get('cron_expression')} {schedule.get('timezone')}"
            )
        if trigger.get("watch"):
            watch = trigger["watch"]
            trigger_parts.append(
                f"watch={watch.get('path')} glob={watch.get('glob')} mode={watch.get('mode')}"
            )
        if trigger.get("runContinuously"):
            trigger_parts.append("run_continuously=true")
        if trigger.get("provided"):
            trigger_parts.append("trigger_context=provided")
        console.print("[bold]Trigger:[/bold] " + "; ".join(trigger_parts))

    destructive = plan.get("destructiveActions") or []
    if destructive:
        console.print("[red]Destructive or high-impact actions:[/red]")
        for action in destructive:
            console.print(f"  • {action}")

    warnings = plan.get("warnings") or []
    if warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for warning in warnings:
            console.print(f"  • {warning}")

    required_secrets = plan.get("requiredSecrets") or []
    if required_secrets:
        console.print(
            "[yellow]Required secrets:[/yellow] "
            + ", ".join(_secret_name(item) for item in required_secrets)
        )
    readiness = plan.get("secretReadiness") or []
    if readiness:
        console.print("[bold]Secret readiness:[/bold]")
        for item in readiness:
            status = str(item.get("status") or "missing")
            name = str(item.get("name") or "")
            marker = "[green]present[/green]" if status == "present" else "[red]missing[/red]"
            sources = item.get("sources") or []
            suffix = f" ({', '.join(str(source) for source in sources)})" if sources else ""
            console.print(f"  • {name}: {marker}{suffix}")

    resource_limits = plan.get("resourceLimits") or {}
    execution_limits = plan.get("executionLimits") or {}
    if isinstance(resource_limits, dict):
        console.print(
            "[bold]Resource limits:[/bold] "
            f"fanout_items={resource_limits.get('max_fanout_items')} "
            f"fanout_concurrency={resource_limits.get('max_fanout_concurrency')} "
            f"files_scanned={resource_limits.get('max_files_scanned')} "
            f"max_node_runs={execution_limits.get('maxTotalNodeRuns')}"
        )

    projected_usage = plan.get("projectedLlmUsage") or {}
    if isinstance(projected_usage, dict) and projected_usage.get("agent_calls"):
        console.print(
            "[bold]Projected LLM usage:[/bold] "
            f"calls={projected_usage.get('agent_calls')} "
            f"tokens~{projected_usage.get('total_tokens')} "
            f"cost~${float(projected_usage.get('estimated_cost') or 0.0):.6f}"
        )

    parameters = plan.get("inputs") or plan.get("parameters") or {}
    if isinstance(parameters, dict) and parameters:
        console.print("[bold]Workflow inputs:[/bold]")
        for name, value in parameters.items():
            console.print(f"  • {name}={value}")

    providers = plan.get("providerRequirements") or []
    if providers:
        console.print("[bold]Provider CLI requirements:[/bold]")
        for provider in providers:
            availability = "available" if provider.get("available") else "missing"
            line = (
                f"  • {provider['agentId']}: {provider['subscription']} "
                f"binary={provider.get('binary') or 'unknown'} ({availability}) "
                f"cwd={provider['workingDir']}"
            )
            if provider.get("profile"):
                line += f" profile={provider['profile']}"
            if provider.get("model"):
                line += f" model={provider['model']}"
            if provider.get("timeout"):
                line += f" timeout={provider['timeout']}s"
            extra_paths = provider.get("extraPaths") or []
            if extra_paths:
                line += f" extra_paths={', '.join(str(path) for path in extra_paths)}"
            console.print(line)

    branches = plan.get("conditionalBranches") or []
    if branches:
        console.print("[bold]Conditional branches:[/bold]")
        for branch in branches:
            console.print(
                f"  • {branch.get('from')} -> {branch.get('to')} when {branch.get('label')}"
            )

    _print_binding_report(plan.get("bindings") or [])

    for generation in plan.get("generations") or []:
        table = Table(title=f"Generation {generation['index']}", show_lines=False)
        table.add_column("Node")
        table.add_column("Type")
        table.add_column("Working dir")
        table.add_column("Impact")
        table.add_column("Fan-out")
        table.add_column("Policy")
        for node in generation.get("nodes") or []:
            fan_out = node.get("fanOut")
            fan_text = ""
            if fan_out:
                fan_text = f"{fan_out.get('sourceType')} count={_format_fan_out_count(fan_out)}"
                if fan_out.get("sampleItems"):
                    samples = [_format_plan_sample(item) for item in fan_out["sampleItems"]]
                    fan_text += f" samples={'; '.join(samples)}"
            impact = "; ".join(node.get("sideEffects") or []) or node.get("detail", "")
            policy_parts = []
            if node.get("timeoutSeconds") is not None:
                policy_parts.append(f"timeout={node.get('timeoutSeconds')}s")
            if node.get("retryCount"):
                policy_parts.append(
                    f"retries={node.get('retryCount')} delay={node.get('retryDelaySeconds')}s"
                )
            if node.get("allowFailure"):
                policy_parts.append("allow_failure=true")
            if not node.get("awaitAllInputs", True):
                policy_parts.append("await_all_inputs=false")
            table.add_row(
                node["id"],
                node["type"],
                str(node.get("workingDir") or ""),
                impact,
                fan_text,
                "; ".join(policy_parts),
            )
        console.print(table)
        for node in generation.get("nodes") or []:
            impact = "; ".join(node.get("sideEffects") or []) or node.get("detail", "")
            if impact:
                console.print(f"  Impact for {node['id']}: {impact}")
            working_dir = node.get("workingDir")
            if working_dir:
                console.print(f"  Working dir for {node['id']}: {working_dir}")
            policy_parts = []
            if node.get("timeoutSeconds") is not None:
                policy_parts.append(f"timeout={node.get('timeoutSeconds')}s")
            if node.get("retryCount"):
                policy_parts.append(
                    f"retries={node.get('retryCount')} delay={node.get('retryDelaySeconds')}s"
                )
            if node.get("allowFailure"):
                policy_parts.append("allow_failure=true")
            if not node.get("awaitAllInputs", True):
                policy_parts.append("await_all_inputs=false")
            if policy_parts:
                console.print(f"  Policy for {node['id']}: {'; '.join(policy_parts)}")
            fan_out = node.get("fanOut") or {}
            if fan_out:
                console.print(
                    "  Fan-out for "
                    f"{node['id']}: {fan_out.get('sourceType')} "
                    f"count={_format_fan_out_count(fan_out)}"
                )
            samples = fan_out.get("sampleItems") or []
            if samples:
                sample_text = "; ".join(_format_plan_sample(item) for item in samples)
                console.print(f"  Samples for {node['id']}: {sample_text}")


def _print_validate_http_diagnostics(plan: dict[str, Any]) -> None:
    http_requests: list[str] = []
    for generation in plan.get("generations") or []:
        for node in generation.get("nodes") or []:
            for detail in node.get("sideEffectDetails") or []:
                if detail.get("kind") == "network" and detail.get("action") == "http_request":
                    http_requests.append(
                        f"{node.get('id')}: {detail.get('method')} {detail.get('host')}"
                    )
    if http_requests:
        console.print("[bold]HTTP requests:[/bold]")
        for request in http_requests:
            console.print(f"  • {request}")
    required_secrets = plan.get("requiredSecrets") or []
    if required_secrets:
        console.print(
            "[yellow]Required secrets:[/yellow] "
            + ", ".join(_secret_name(item) for item in required_secrets)
        )


def _print_binding_report(bindings: list[dict[str, Any]]) -> None:
    if not bindings:
        return
    console.print("[bold]Runtime bindings:[/bold]")
    for binding in bindings:
        status = str(binding.get("status") or "runtime-bound")
        color = "red" if status in {"invalid", "type-incompatible"} else "cyan"
        destination = f"{binding.get('destinationNode')}.{binding.get('destinationField')}"
        source = str(binding.get("expression") or "")
        source_type = str(binding.get("sourceType") or "unknown")
        destination_type = str(binding.get("destinationType") or "unknown")
        phase = str(binding.get("resolutionPhase") or "runtime")
        coercion = str(binding.get("coercion") or "none")
        line = (
            f"  • [{color}]{status}[/{color}] {destination} <- {source} "
            f"({source_type} -> {destination_type}, {phase}"
        )
        if coercion != "none":
            line += f", {coercion} coercion"
        readiness = binding.get("readiness")
        if readiness:
            line += f", secret {readiness}"
        console.print(line + f") [dim]{binding.get('id') or ''}[/dim]")


def _format_plan_sample(item: Any) -> str:
    if isinstance(item, dict):
        if "path" in item:
            return str(item["path"])
        if "name" in item:
            return str(item["name"])
        return json.dumps(item, sort_keys=True, default=str)
    return str(item)


def _secret_name(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "")
    return str(item)


def _format_fan_out_count(fan_out: dict[str, Any]) -> str:
    count = fan_out.get("count")
    if count is None:
        return "unknown"
    if fan_out.get("countExact", True):
        return str(count)
    lower_bound = fan_out.get("countLowerBound", count)
    return f"at least {lower_bound}"


def _workflow_log_context(workflow: str, data_dir: Path | None) -> tuple[str, Path]:
    wf, path = _resolve_workflow_with_path(workflow, data_dir)
    return wf.config.id, data_dir or path.parent


def _slugify(value: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9-]", "-", value.lower())) or "workflow"


def _parse_key_values(values: list[str] | None, option_name: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise typer.BadParameter(f"{option_name} values must be KEY=VALUE")
        key, item = value.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"{option_name} keys cannot be empty")
        parsed[key] = item
    return parsed


def _parse_json_object(value: str | None, option_name: str) -> dict[str, str]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter(f"{option_name} must be a JSON object")
    return {str(k): str(v) for k, v in parsed.items()}


def _parse_json_value(value: str | None, option_name: str) -> object | None:
    if value is None or value == "":
        return None
    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} must be valid JSON") from exc
    return parsed


def _parse_json_dict(value: str | None, option_name: str) -> dict[str, object]:
    if not value:
        return {}
    parsed = _parse_json_value(value, option_name)
    if not isinstance(parsed, dict):
        raise typer.BadParameter(f"{option_name} must be a JSON object")
    return parsed


def _parse_output_schema(value: str | None) -> str | dict[str, object] | None:
    if value is None or not value.strip():
        return None
    stripped = value.strip()
    if not stripped.startswith("{"):
        return stripped
    parsed = _parse_json_value(stripped, "--output-schema")
    if not isinstance(parsed, dict):
        raise typer.BadParameter("--output-schema JSON must be an object")
    return parsed


def _save_workflow(wf: AgenticWorkflow, path: Path) -> None:
    try:
        wf.validate(path)
    except Exception as exc:
        console.print(f"[red]Invalid workflow: {exc}[/red]")
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    wf.to_file(path)
    console.print(f"[green]Saved[/green] {path}")


def _fan_source_from_options(
    fan_source: str | None,
    fan_count: str,
    fan_path: Path | None,
    fan_glob: str,
    fan_include_content: bool,
    fan_max_concurrency: int,
    fan_fail_fast: bool,
) -> Any | None:
    if fan_source is None:
        return None
    normalized = fan_source.replace("_", "-")
    if normalized == "count":
        count: int | str
        count = int(fan_count) if fan_count.isdigit() else fan_count
        return CountFanSource(
            type="count",
            count=cast(Any, count),
            max_concurrency=fan_max_concurrency,
            fail_fast=fan_fail_fast,
        )
    if normalized == "tabular":
        if fan_path is None:
            raise typer.BadParameter("--fan-path is required for tabular fan-out")
        return TabularFanSource(
            type="tabular",
            path=fan_path,
            max_concurrency=fan_max_concurrency,
            fail_fast=fan_fail_fast,
        )
    if normalized == "directory":
        if fan_path is None:
            raise typer.BadParameter("--fan-path is required for directory fan-out")
        return DirectoryFanSource(
            type="directory",
            path=fan_path,
            glob=fan_glob,
            include_content=fan_include_content,
            max_concurrency=fan_max_concurrency,
            fail_fast=fan_fail_fast,
        )
    if normalized in {"trigger-events", "trigger_events"}:
        return TriggerEventsFanSource(
            type="trigger_events",
            include_content=fan_include_content,
            max_concurrency=fan_max_concurrency,
            fail_fast=fan_fail_fast,
        )
    if normalized == "infinite":
        return InfiniteFanSource(type="infinite")
    raise typer.BadParameter(
        "--fan-source must be one of count, tabular, directory, trigger-events, infinite"
    )


def _operation_from_options(
    node_type: str,
    *,
    command: str | None,
    script_path: Path | None,
    path: Path | None,
    source_path: Path | None,
    destination_path: Path | None,
    target: str | None,
    resource_type: str,
    content: str,
    message: str,
    template: str,
    template_path: Path | None,
    output_path: Path | None,
    variable_mapping: dict[str, str],
    working_dir: Path | None,
    agent_id: str | None,
    prompt_path: Path | None,
    skill_name: str | None,
    profile: str | None,
    model: str | None,
    provider_timeout: float | None,
    task: str,
    target_text: str,
    instructions: str,
    index_path: Path | None,
    vector_mode: str,
    search_top_k: int,
    search_score_threshold: float,
    search_include_snippets: bool,
    search_include_file_metadata: bool,
    dynamic_count: str,
    memory: str,
    input_mapping: dict[str, str],
    output_schema: str | dict[str, object] | None,
    repair_attempts: int,
    env: dict[str, str],
    args: list[str],
    create_dirs: bool,
    overwrite: bool | None,
    append: bool,
    use_trash: bool,
    recursive: bool,
    missing_ok: bool,
    encoding: str,
    errors: str,
    fan_glob: str,
    fan_source: Any,
    http_method: str,
    http_url: str | None,
    http_headers: dict[str, str],
    http_params: dict[str, str],
    http_json: object | None,
    http_body: str | None,
    http_timeout_seconds: float,
    http_retry_attempts: int,
    http_retry_backoff_seconds: float,
    http_retry_statuses: list[int],
    http_expected_statuses: list[int],
    http_response_mode: str,
    http_output_mapping: dict[str, str],
    http_secret_fields: list[str],
    approval_timeout_seconds: float | None,
    approval_timeout_decision: str,
    approval_approvers: list[str],
    approval_notify: bool,
    notification_title: str,
    notification_body: str,
    notification_channel: str,
    notification_urgency: str,
    notification_webhook_url: str | None,
    notification_headers: dict[str, str],
    notification_payload: object | None,
    notification_email_from: str | None,
    notification_email_to: list[str],
    notification_smtp_host: str | None,
    notification_smtp_port: int,
    notification_smtp_username: str | None,
    notification_smtp_password: str | None,
    notification_smtp_starttls: bool,
    notification_timeout_seconds: float,
    notification_retry_attempts: int,
    notification_retry_backoff_seconds: float,
    notification_retry_statuses: list[int],
    notification_expected_statuses: list[int],
    notification_network_allowlist: list[str],
) -> Operation:
    normalized = node_type.replace("-", "_")
    match normalized:
        case OperationType.START:
            return StartOperation(type=OperationType.START)
        case OperationType.PASS:
            return PassOperation(type=OperationType.PASS, message=message)
        case OperationType.FAIL:
            return FailOperation(type=OperationType.FAIL, message=message)
        case OperationType.BREAK:
            return BreakOperation(type=OperationType.BREAK, message=message)
        case OperationType.LOOP:
            if fan_source is None:
                raise typer.BadParameter("--fan-source is required for loop nodes")
            return LoopOperation(type=OperationType.LOOP, source=fan_source)
        case OperationType.BASH_COMMAND:
            if command is None:
                raise typer.BadParameter("--command is required for bash_command nodes")
            return BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command=command,
                working_dir=working_dir,
                env=env,
            )
        case OperationType.PYTHON_SCRIPT:
            if script_path is None:
                raise typer.BadParameter("--script-path is required for python_script nodes")
            return PythonScriptOperation(
                type=OperationType.PYTHON_SCRIPT,
                script_path=script_path,
                args=args,
                env=env,
            )
        case OperationType.SHELL_SCRIPT:
            if script_path is None:
                raise typer.BadParameter("--script-path is required for shell_script nodes")
            return ShellScriptOperation(
                type=OperationType.SHELL_SCRIPT,
                script_path=script_path,
                args=args,
                env=env,
            )
        case OperationType.READ_FILE:
            if path is None:
                raise typer.BadParameter("--path is required for read_file nodes")
            return ReadFileOperation(
                type=OperationType.READ_FILE,
                path=path,
                encoding=encoding,
                errors=errors,
            )
        case OperationType.WRITE_FILE:
            if path is None:
                raise typer.BadParameter("--path is required for write_file nodes")
            return WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=path,
                content=content,
                encoding=encoding,
                create_dirs=create_dirs,
                overwrite=True if overwrite is None else overwrite,
                append=append,
            )
        case OperationType.COPY_FILE:
            if source_path is None or destination_path is None:
                raise typer.BadParameter(
                    "--source-path and --destination-path are required for copy_file nodes"
                )
            return CopyFileOperation(
                type=OperationType.COPY_FILE,
                source_path=source_path,
                destination_path=destination_path,
                create_dirs=create_dirs,
                overwrite=False if overwrite is None else overwrite,
            )
        case OperationType.MOVE_FILE:
            if source_path is None or destination_path is None:
                raise typer.BadParameter(
                    "--source-path and --destination-path are required for move_file nodes"
                )
            return MoveFileOperation(
                type=OperationType.MOVE_FILE,
                source_path=source_path,
                destination_path=destination_path,
                create_dirs=create_dirs,
                overwrite=False if overwrite is None else overwrite,
            )
        case OperationType.DELETE_FILE:
            if path is None:
                raise typer.BadParameter("--path is required for delete_file nodes")
            return DeleteFileOperation(
                type=OperationType.DELETE_FILE,
                path=path,
                use_trash=use_trash,
                recursive=recursive,
                missing_ok=missing_ok,
            )
        case OperationType.FILE:
            if path is None:
                raise typer.BadParameter("--path is required for file nodes")
            return FileOperation(type=OperationType.FILE, path=path)
        case OperationType.FOLDER:
            if path is None:
                raise typer.BadParameter("--path is required for folder nodes")
            return FolderOperation(type=OperationType.FOLDER, path=path)
        case OperationType.OPEN_RESOURCE:
            if target is None:
                raise typer.BadParameter("--target is required for open_resource nodes")
            return OpenResourceOperation(
                type=OperationType.OPEN_RESOURCE,
                target=target,
                resource_type=resource_type,
                args=args,
            )
        case OperationType.PROMPT_FILE:
            if output_path is None:
                raise typer.BadParameter("--output-path is required for prompt_file nodes")
            return PromptFileOperation(
                type=OperationType.PROMPT_FILE,
                output_path=output_path,
                template=template,
                template_path=template_path,
                variables=variable_mapping,
                encoding=encoding,
                create_dirs=create_dirs,
                overwrite=True if overwrite is None else overwrite,
            )
        case OperationType.COMMON_LLM_TASK:
            if agent_id is None or working_dir is None:
                raise typer.BadParameter(
                    "--agent-id and --working-dir are required for common_llm_task nodes"
                )
            if memory not in {"none", "run", "all"}:
                raise typer.BadParameter("--memory must be one of none, run, all")
            return CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id=agent_id,
                task=task,
                target=target_text,
                instructions=instructions,
                working_dir=working_dir,
                profile=profile,
                model=model,
                timeout=provider_timeout,
                memory=memory,
                input_mapping=input_mapping,
                output_schema=output_schema,
                repair_attempts=repair_attempts,
            )
        case OperationType.LOCAL_VECTORIZE:
            if source_path is None or index_path is None:
                raise typer.BadParameter(
                    "--source-path and --index-path are required for local_vectorize nodes"
                )
            if vector_mode not in {"incremental", "full", "validate", "compact"}:
                raise typer.BadParameter(
                    "--vector-mode must be one of incremental, full, validate, compact"
                )
            return LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=source_path,
                index_path=index_path,
                glob=fan_glob,
                recursive=recursive,
                mode=vector_mode,
            )
        case OperationType.LOCAL_SEARCH:
            if index_path is None:
                raise typer.BadParameter("--index-path is required for local_search nodes")
            return LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index_path,
                query=target_text or command or "",
                top_k=search_top_k,
                score_threshold=search_score_threshold,
                include_snippets=search_include_snippets,
                include_file_metadata=search_include_file_metadata,
            )
        case OperationType.HTTP_REQUEST:
            if http_url is None:
                raise typer.BadParameter("--url is required for http_request nodes")
            if http_response_mode not in {"auto", "json", "text", "none"}:
                raise typer.BadParameter("--response-mode must be one of auto, json, text, none")
            return HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method=http_method.upper(),
                url=http_url,
                headers=http_headers,
                params=http_params,
                json=http_json,
                body=http_body,
                timeout_seconds=http_timeout_seconds,
                retry=HttpRetryPolicy(
                    attempts=http_retry_attempts,
                    backoff_seconds=http_retry_backoff_seconds,
                    retry_on_statuses=http_retry_statuses,
                ),
                expected_statuses=http_expected_statuses,
                response_mode=http_response_mode,
                output_mapping=http_output_mapping,
                secret_fields=http_secret_fields,
            )
        case OperationType.APPROVAL_GATE:
            if not message:
                raise typer.BadParameter("--message is required for approval_gate nodes")
            if approval_timeout_decision not in {"reject", "timeout"}:
                raise typer.BadParameter("--timeout-decision must be reject or timeout")
            return ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message=message,
                timeout_seconds=approval_timeout_seconds,
                timeout_decision=approval_timeout_decision,
                approvers=approval_approvers,
                notify=approval_notify,
                notification_title=notification_title,
            )
        case OperationType.NOTIFICATION:
            if notification_channel not in {"desktop", "slack", "teams", "webhook", "email"}:
                raise typer.BadParameter(
                    "--channel must be desktop, slack, teams, webhook, or email"
                )
            if notification_urgency not in {"low", "normal", "critical"}:
                raise typer.BadParameter("--urgency must be low, normal, or critical")
            return NotificationOperation(
                type=OperationType.NOTIFICATION,
                title=notification_title,
                body=notification_body or message,
                channel=notification_channel,
                urgency=notification_urgency,
                webhook_url=notification_webhook_url,
                headers=notification_headers,
                payload=notification_payload,
                email_from=notification_email_from,
                email_to=notification_email_to,
                smtp_host=notification_smtp_host,
                smtp_port=notification_smtp_port,
                smtp_username=notification_smtp_username,
                smtp_password=notification_smtp_password,
                smtp_starttls=notification_smtp_starttls,
                timeout_seconds=notification_timeout_seconds,
                retry=HttpRetryPolicy(
                    attempts=notification_retry_attempts,
                    backoff_seconds=notification_retry_backoff_seconds,
                    retry_on_statuses=notification_retry_statuses,
                ),
                expected_statuses=notification_expected_statuses,
                network_allowlist=notification_network_allowlist,
            )
        case OperationType.AGENT:
            if agent_id is None or working_dir is None:
                raise typer.BadParameter(
                    "--agent-id and --working-dir are required for agent nodes"
                )
            if prompt_path is None and not skill_name:
                raise typer.BadParameter(
                    "agent nodes require --prompt-path unless --skill-name is provided"
                )
            if memory not in {"none", "run", "all"}:
                raise typer.BadParameter("--memory must be one of none, run, all")
            return AgentOperation(
                type=OperationType.AGENT,
                agent_id=agent_id,
                prompt_path=prompt_path,
                working_dir=working_dir,
                profile=profile,
                model=model,
                timeout=provider_timeout,
                skill_name=skill_name,
                memory=memory,
                input_mapping=input_mapping,
                output_schema=output_schema,
                repair_attempts=repair_attempts,
            )
    raise typer.BadParameter(
        "node type must be one of "
        "start, pass, fail, break, loop, bash_command, python_script, shell_script, "
        "agent, read_file, write_file, "
        "copy_file, move_file, delete_file, file, folder, open_resource, prompt_file, "
        "common_llm_task, local_vectorize, local_search, http_request, "
        "approval_gate, notification"
    )


@app.command("run")
def run(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without executing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show each node's output"),
    trigger_json: str | None = typer.Option(
        None,
        "--trigger-json",
        help="Trigger context JSON object used for dry-run planning",
    ),
    param: list[str] | None = typer.Option(
        None,
        "--param",
        help="Workflow parameter as KEY=VALUE. May be repeated.",
    ),
    params_json: str | None = typer.Option(
        None,
        "--params-json",
        help="Workflow parameters as a JSON object",
    ),
    input_: list[str] | None = typer.Option(
        None,
        "--input",
        help="Workflow input as KEY=VALUE. May be repeated.",
    ),
    inputs_json: str | None = typer.Option(
        None,
        "--inputs-json",
        help="Workflow inputs as a JSON object",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Execute a workflow by name or file path."""
    try:
        wf, workflow_path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    profile_data_dir = data_dir or get_data_dir()
    if not dry_run:
        wf.validate(workflow_path, profile_data_dir)
    _print_agent_access_summary(wf, workflow_path.parent)
    trigger_context = _parse_trigger_context(trigger_json)
    try:
        legacy_values = _parse_param_values(param, params_json)
        input_values = _parse_param_values(input_, inputs_json)
        overlap = sorted(set(legacy_values) & set(input_values))
        if overlap:
            raise ValueError(
                "Inputs cannot be supplied with both --input and --param: " + ", ".join(overlap)
            )
        run_parameters = resolve_workflow_parameters(
            wf.config,
            _prompt_missing_params(
                wf,
                {
                    **legacy_values,
                    **input_values,
                },
            ),
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    base = data_dir or workflow_path.parent
    if dry_run:
        plan = build_execution_plan(
            wf,
            workflow_path=workflow_path,
            data_dir=profile_data_dir,
            trigger_context=trigger_context,
            invocation_inputs=run_parameters,
        )
        plan["inputs"] = masked_workflow_parameters(wf.config, run_parameters)
        plan["parameters"] = plan["inputs"]
        _print_execution_plan(plan)
        return
    if wf.config.run_continuously:
        runs = list_workflow_run_logs_payload(wf.config.id, base).get("runs") or []
        if any(run_log.get("status") == "running" for run_log in runs):
            console.print(
                f"[yellow]Workflow '{wf.config.id}' is configured to run continuously "
                "and already has an active run.[/yellow]"
            )
            raise typer.Exit(1)

    result = asyncio.run(
        WorkflowExecutor(
            wf,
            _SUBSCRIPTIONS,
            dry_run=dry_run,
            log_base_dir=base / "logs",
            workflow_path=workflow_path,
            data_dir=profile_data_dir,
            stop_file=workflow_stop_path(wf.config.id, base),
        )
        .with_trigger_context(trigger_context or {})
        .with_parameters(run_parameters)
        .run()
    )
    _write_cli_run_artifacts(result, wf.config.resource_limits, base)

    if verbose:
        for node_id, node_out in result.node_outputs.items():
            status = "[green]✓[/green]" if node_out.success else "[red]✗[/red]"
            if node_out.fan_outputs:
                for label, output in node_out.fan_outputs:
                    console.print(f"\n{status} [bold]{label}[/bold]")
                    if output:
                        console.print(output)
            else:
                console.print(f"\n{status} [bold]{node_id}[/bold]")
                output_text = node_out.output
                if node_out.type == str(OperationType.HTTP_REQUEST):
                    preview = node_out.data.get("responsePreview")
                    if isinstance(preview, dict) and isinstance(preview.get("body"), str):
                        output_text = str(preview["body"])
                    elif isinstance(node_out.data.get("error"), str):
                        output_text = str(node_out.data["error"])
                if output_text:
                    console.print(output_text)
                if node_out.type == str(OperationType.LOCAL_VECTORIZE):
                    _print_local_vector_index_stats(node_out.data)

    _print_usage_summary(result.usage_summary)
    if result.success:
        console.print(
            f"[green]✓[/green] Workflow '{result.workflow_id}' completed successfully "
            f"in {result.duration_seconds:.2f}s"
        )
    else:
        console.print(f"[red]✗[/red] Workflow '{result.workflow_id}' failed")
        raise typer.Exit(1)


@app.command("trigger")
def trigger(
    workflow: str = typer.Argument(..., help="Workflow ID to trigger"),
    trigger_id: str = typer.Option("default", "--trigger-id", help="Webhook trigger ID"),
    payload_json: str | None = typer.Option(
        None,
        "--payload-json",
        help="JSON payload to pass as trigger.payload",
    ),
    payload_file: Path | None = typer.Option(
        None,
        "--payload-file",
        help="File containing JSON payload to pass as trigger.payload",
    ),
    header: list[str] = typer.Option(
        [],
        "--header",
        "-H",
        help="Trigger header as 'Name: value'; may be repeated",
    ),
    source: str = typer.Option("cli", "--source", help="Trigger source metadata"),
    token: str | None = typer.Option(
        None,
        "--token",
        help="Webhook token for workflows that require one",
    ),
    replay_run_id: str | None = typer.Option(
        None,
        "--replay-run-id",
        help="Replay the saved trigger payload from a prior run log id",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Trigger a workflow through its configured webhook/API trigger."""
    try:
        if replay_run_id:
            payload = asyncio.run(
                replay_workflow_trigger_payload(
                    workflow,
                    replay_run_id,
                    data_dir,
                    trigger_id=trigger_id,
                )
            )
        else:
            payload = asyncio.run(
                trigger_workflow_payload(
                    workflow,
                    trigger_id,
                    data_dir,
                    payload=_parse_json_payload(payload_json, payload_file),
                    headers=_parse_headers(header),
                    source=source,
                    token=token,
                )
            )
    except (WorkflowTriggerError, typer.BadParameter) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    run_payload = payload.get("run") or {}
    status = "succeeded" if run_payload.get("success") else "failed"
    console.print(
        f"[green]Triggered[/green] {payload['workflowId']}:{payload['triggerId']} "
        f"request={payload['requestId']} run={payload.get('runId') or 'unknown'} {status}"
    )
    if not run_payload.get("success"):
        raise typer.Exit(1)


@app.command("resume")
def resume(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    run_id: str = typer.Option(..., "--run-id", help="Run log file name to resume from"),
    from_node: str | None = typer.Option(
        None,
        "--from-node",
        help="Rerun this node and downstream nodes, reusing successful upstream outputs",
    ),
    only_node: str | None = typer.Option(
        None,
        "--only-node",
        help="Rerun only this node after restoring successful dependency outputs",
    ),
    skip_cache: bool = typer.Option(
        False,
        "--skip-cache",
        help="Ignore reusable node cache entries while resuming",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Ignore saved outputs and run from the beginning",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show each node's output"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Resume a failed or stopped workflow run."""
    if from_node and only_node:
        raise typer.BadParameter("--from-node and --only-node cannot be used together")
    try:
        wf, workflow_path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    for node_id in (from_node, only_node):
        if node_id and node_id not in wf.graph._nodes:
            console.print(f"[red]Node '{node_id}' not found in workflow '{wf.config.id}'[/red]")
            raise typer.Exit(1)

    profile_data_dir = data_dir or get_data_dir()
    wf.validate(workflow_path, profile_data_dir)
    _print_agent_access_summary(wf, workflow_path.parent)
    base = data_dir or workflow_path.parent
    result = asyncio.run(
        WorkflowExecutor(
            wf,
            _SUBSCRIPTIONS,
            log_base_dir=base / "logs",
            workflow_path=workflow_path,
            data_dir=profile_data_dir,
            stop_file=workflow_stop_path(wf.config.id, base),
        )
        .with_resume_options(
            ResumeOptions(
                run_id=run_id,
                from_node=from_node,
                only_node=only_node,
                skip_cache=skip_cache,
                force=force,
            )
        )
        .run()
    )
    _write_cli_run_artifacts(result, wf.config.resource_limits, base)

    if verbose:
        for node_id, node_out in result.node_outputs.items():
            status = "[green]✓[/green]" if node_out.success else "[red]✗[/red]"
            reused = " [dim](reused)[/dim]" if node_out.data.get("reused") else ""
            console.print(f"\n{status} [bold]{node_id}[/bold]{reused}")
            if node_out.output:
                console.print(node_out.output)

    _print_usage_summary(result.usage_summary)
    if result.success:
        console.print(
            f"[green]✓[/green] Workflow '{result.workflow_id}' resumed successfully "
            f"in {result.duration_seconds:.2f}s"
        )
    else:
        console.print(f"[red]✗[/red] Workflow '{result.workflow_id}' resume failed")
        raise typer.Exit(1)


def _print_usage_summary(summary: dict[str, object]) -> None:
    totals = summary.get("totals") if isinstance(summary, dict) else None
    if not isinstance(totals, dict) or not totals.get("agent_calls"):
        return
    console.print(
        "[bold]LLM usage:[/bold] "
        f"calls={totals.get('agent_calls')} "
        f"tokens={totals.get('total_tokens')} "
        f"cost~${float(totals.get('estimated_cost') or 0.0):.6f} "
        f"agent_time={float(totals.get('agent_time_seconds') or 0.0):.2f}s"
    )


@app.command("plan")
def plan(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    json_output: bool = typer.Option(False, "--json", help="Print the plan as JSON"),
    trigger_json: str | None = typer.Option(
        None,
        "--trigger-json",
        help="Trigger context JSON object for trigger-event fan-out estimates",
    ),
    param: list[str] | None = typer.Option(
        None,
        "--param",
        help="Workflow parameter as KEY=VALUE. May be repeated.",
    ),
    params_json: str | None = typer.Option(
        None,
        "--params-json",
        help="Workflow parameters as a JSON object",
    ),
    input_: list[str] | None = typer.Option(
        None, "--input", help="Workflow input as KEY=VALUE. May be repeated."
    ),
    inputs_json: str | None = typer.Option(
        None, "--inputs-json", help="Workflow inputs as a JSON object"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Preview execution order, fan-out, provider calls, and side effects."""
    try:
        wf, workflow_path = _resolve_workflow_with_path(workflow, data_dir)
        profile_data_dir = data_dir or get_data_dir()
        trigger_context = _parse_trigger_context(trigger_json)
        legacy_values = _parse_param_values(param, params_json)
        input_values = _parse_param_values(input_, inputs_json)
        overlap = sorted(set(legacy_values) & set(input_values))
        if overlap:
            raise ValueError(
                "Inputs cannot be supplied with both --input and --param: " + ", ".join(overlap)
            )
        run_parameters = resolve_workflow_parameters(
            wf.config,
            {**legacy_values, **input_values},
            allow_missing_required=True,
        )
        plan_payload = build_execution_plan(
            wf,
            workflow_path=workflow_path,
            data_dir=profile_data_dir,
            trigger_context=trigger_context,
            invocation_inputs=run_parameters,
        )
        plan_payload["inputs"] = masked_workflow_parameters(wf.config, run_parameters)
        plan_payload["parameters"] = plan_payload["inputs"]
    except Exception as exc:
        console.print(f"[red]Plan failed: {exc}[/red]")
        raise typer.Exit(1)

    if json_output:
        sys.stdout.write(plan_to_json(plan_payload))
        sys.stdout.write("\n")
        return
    _print_execution_plan(plan_payload)


@app.command("usage")
def usage(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    json_output: bool = typer.Option(False, "--json", help="Print usage as JSON"),
    limit: int = typer.Option(10, "--limit", min=1, help="Number of recent runs to inspect"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Show LLM usage summaries for recent workflow runs."""
    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        runs_payload = list_workflow_run_logs_payload(workflow_id, base)
    except (KeyError, WorkflowLogError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    run_summaries = []
    for run_log in (runs_payload.get("runs") or [])[:limit]:
        run_id = str(run_log["id"])
        try:
            payload = workflow_run_log_payload(workflow_id, run_id, base)
        except WorkflowLogError:
            continue
        summary = payload.get("usageSummary")
        if not isinstance(summary, dict):
            summary = summarize_node_outputs(payload.get("nodeOutputs") or {})
        run_summaries.append(
            {
                "runId": run_id,
                "startedAt": payload.get("startedAt"),
                "status": payload.get("status"),
                "summary": summary,
            }
        )

    totals = {
        "agent_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
        "agent_time_seconds": 0.0,
    }
    for run_summary in run_summaries:
        summary = run_summary["summary"]
        run_totals = summary.get("totals") if isinstance(summary, dict) else {}
        if not isinstance(run_totals, dict):
            continue
        for key in ("agent_calls", "input_tokens", "output_tokens", "total_tokens"):
            totals[key] += int(run_totals.get(key) or 0)
        totals["estimated_cost"] += float(run_totals.get("estimated_cost") or 0.0)
        totals["agent_time_seconds"] += float(run_totals.get("agent_time_seconds") or 0.0)

    payload = {
        "workflowId": workflow_id,
        "runs": run_summaries,
        "totals": totals,
    }
    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    if not run_summaries:
        console.print(f"No usage records found for [bold]{workflow_id}[/bold].")
        return
    _print_usage_summary({"totals": totals})
    table = Table(title=f"Recent LLM usage for {workflow_id}")
    table.add_column("Run")
    table.add_column("Status")
    table.add_column("Calls", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Agent time", justify="right")
    for run_summary in run_summaries:
        summary = run_summary["summary"]
        run_totals = summary.get("totals") if isinstance(summary, dict) else {}
        if not isinstance(run_totals, dict):
            run_totals = {}
        table.add_row(
            str(run_summary["runId"]),
            str(run_summary.get("status") or "unknown"),
            str(run_totals.get("agent_calls") or 0),
            str(run_totals.get("total_tokens") or 0),
            f"${float(run_totals.get('estimated_cost') or 0.0):.6f}",
            f"{float(run_totals.get('agent_time_seconds') or 0.0):.2f}s",
        )
    console.print(table)


@app.command("secrets")
def secrets(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    json_output: bool = typer.Option(False, "--json", help="Print readiness as JSON"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Show required workflow secrets and whether they are available locally."""
    try:
        wf, workflow_path = _resolve_workflow_with_path(workflow, data_dir)
        profile_data_dir = data_dir or get_data_dir()
        readiness = [
            item.to_dict()
            for item in workflow_secret_readiness(
                wf,
                workflow_path=workflow_path,
                data_dir=profile_data_dir,
            )
        ]
    except Exception as exc:
        console.print(f"[red]Secret readiness failed: {exc}[/red]")
        raise typer.Exit(1)

    payload = {
        "workflowId": wf.config.id,
        "ok": not any(not item["present"] for item in readiness),
        "requiredSecrets": [item["name"] for item in readiness],
        "secretReadiness": readiness,
    }
    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    if not readiness:
        console.print(f"No required secrets found for [bold]{wf.config.id}[/bold].")
        return
    console.print(f"[bold]Secret readiness for {wf.config.id}[/bold]")
    for item in readiness:
        status = "[green]present[/green]" if item["present"] else "[red]missing[/red]"
        sources = item.get("sources") or []
        suffix = f" ({', '.join(str(source) for source in sources)})" if sources else ""
        console.print(f"  • {item['name']}: {status}{suffix}")
    if not payload["ok"]:
        raise typer.Exit(1)


@app.command("stop")
def stop(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Request that a running workflow stop."""
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    base = data_dir or path.parent
    stop_path = request_workflow_stop(wf.config.id, base)
    console.print(f"[green]Stop requested[/green] for '{wf.config.id}' via {stop_path}")


def _find_approval_request(
    store: ApprovalStore,
    run_id: str,
    node_id: str,
    workflow_id: str | None,
) -> ApprovalRequest | None:
    if workflow_id:
        request = store.get(workflow_id, run_id, node_id)
        return request if request is not None and request.decision is None else None
    matches = [
        request
        for request in store.list_pending()
        if request.run_id == run_id and request.node_id == node_id
    ]
    return matches[0] if len(matches) == 1 else None


def _approval_waiter_is_live(request: ApprovalRequest) -> bool:
    if request.waiter_pid is None:
        return False
    try:
        os.kill(request.waiter_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _resume_decided_approval(
    store: ApprovalStore,
    request: ApprovalRequest,
    data_dir: Path | None,
) -> None:
    if request.decision is None or _approval_waiter_is_live(request):
        return
    workflow_path = Path(request.workflow_path) if request.workflow_path else None
    if workflow_path is None or not workflow_path.exists():
        base = data_dir or get_data_dir()
        workflow_path = base / f"{request.workflow_id}.toml"
    if not workflow_path.exists():
        return
    try:
        workflow = AgenticWorkflow.from_file(workflow_path)
        result = asyncio.run(
            WorkflowExecutor(
                workflow,
                _SUBSCRIPTIONS,
                log_base_dir=(data_dir or workflow_path.parent) / "logs",
                workflow_path=workflow_path,
                data_dir=data_dir or get_data_dir(),
                approval_store=store,
            ).resume_from_approval(request)
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Approval recorded, but resume failed: {exc}[/yellow]")
        return
    if result is not None:
        base = data_dir or workflow_path.parent
        _write_cli_run_artifacts(result, workflow.config.resource_limits, base)
        console.print(f"[green]Resumed[/green] {request.workflow_id} {request.run_id}")


def _is_timeout_decision(request: ApprovalRequest) -> bool:
    return (
        request.decision is not None
        and request.decision.decided_by == "gofer"
        and request.decision.notes.startswith("Timed out after ")
    )


def _resume_expired_approvals(
    store: ApprovalStore,
    requests: list[ApprovalRequest],
    data_dir: Path | None,
) -> None:
    for request in requests:
        if _is_timeout_decision(request):
            _resume_decided_approval(store, request, data_dir)


@app.command("approvals")
def approvals(
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Only show pending approvals for this workflow ID",
    ),
    all_requests: bool = typer.Option(
        False,
        "--all",
        help="Show pending and completed approval decisions",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List approval gates."""
    store = ApprovalStore(data_dir)
    all_loaded_requests = store.list_requests(workflow)
    _resume_expired_approvals(store, all_loaded_requests, data_dir)
    requests = (
        all_loaded_requests
        if all_requests
        else [request for request in all_loaded_requests if request.decision is None]
    )
    if not requests:
        console.print("No approvals" if all_requests else "No pending approvals")
        return
    if all_requests:
        table = Table()
        table.add_column("Workflow")
        table.add_column("Run ID")
        table.add_column("Node")
        table.add_column("Status")
        table.add_column("Decision")
        table.add_column("Message")
    else:
        table = Table("Workflow", "Run ID", "Node", "Requested", "Message")
    for request in requests:
        decision = request.decision
        if all_requests:
            table.add_row(
                request.workflow_id,
                request.run_id,
                request.node_id,
                "decided" if decision is not None else "pending",
                (f"{decision.decision} by {decision.decided_by}" if decision is not None else ""),
                request.message,
            )
        else:
            table.add_row(
                request.workflow_id,
                request.run_id,
                request.node_id,
                request.requested_at,
                request.message,
            )
    console.print(table)


@app.command("approve")
def approve(
    run_id: str = typer.Argument(..., help="Run ID from the pending approval"),
    node_id: str = typer.Argument(..., help="Approval node ID"),
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Workflow ID when run/node IDs are not unique",
    ),
    by: str = typer.Option("cli", "--by", help="Approver identity/source"),
    notes: str = typer.Option("", "--notes", help="Decision notes"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Approve a pending approval gate."""
    store = ApprovalStore(data_dir)
    request = _find_approval_request(store, run_id, node_id, workflow)
    if request is None:
        console.print("[red]Pending approval not found or not unique; pass --workflow.[/red]")
        raise typer.Exit(1)
    try:
        decided = store.decide(
            request.workflow_id,
            run_id,
            node_id,
            "approved",
            decided_by=by,
            notes=notes,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Approved[/green] {request.workflow_id} {run_id} {node_id}")
    _resume_decided_approval(store, decided, data_dir)


@app.command("reject")
def reject(
    run_id: str = typer.Argument(..., help="Run ID from the pending approval"),
    node_id: str = typer.Argument(..., help="Approval node ID"),
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Workflow ID when run/node IDs are not unique",
    ),
    by: str = typer.Option("cli", "--by", help="Approver identity/source"),
    notes: str = typer.Option("", "--notes", help="Decision notes"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Reject a pending approval gate."""
    store = ApprovalStore(data_dir)
    request = _find_approval_request(store, run_id, node_id, workflow)
    if request is None:
        console.print("[red]Pending approval not found or not unique; pass --workflow.[/red]")
        raise typer.Exit(1)
    try:
        decided = store.decide(
            request.workflow_id,
            run_id,
            node_id,
            "rejected",
            decided_by=by,
            notes=notes,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Rejected[/green] {request.workflow_id} {run_id} {node_id}")
    _resume_decided_approval(store, decided, data_dir)


@app.command("validate")
def validate(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON diagnostics",
    ),
    explain_bindings: bool = typer.Option(
        False,
        "--explain-bindings",
        help="Explain reference producers, types, coercion, and resolution phases",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Validate a workflow by name or file path."""
    try:
        wf, workflow_path = _resolve_workflow_with_path(workflow, data_dir)
        profile_data_dir = data_dir or get_data_dir()
        report = validate_workflow_file(workflow_path, data_dir=profile_data_dir)
        if json_output:
            sys.stdout.write(json.dumps(report.to_dict(), indent=2))
            sys.stdout.write("\n")
            if not report.ok:
                raise typer.Exit(1)
            return
        if not report.ok:
            for diagnostic in report.errors:
                console.print(f"[red]✗[/red] {diagnostic.message}")
            for diagnostic in report.warnings:
                console.print(f"[yellow]![/yellow] {diagnostic.message}")
            if explain_bindings:
                _print_binding_report([binding.to_dict() for binding in report.bindings])
            raise typer.Exit(1)
        for diagnostic in report.warnings:
            console.print(f"[yellow]![/yellow] {diagnostic.message}")
        if explain_bindings:
            _print_binding_report([binding.to_dict() for binding in report.bindings])
        wf.validate(workflow_path, profile_data_dir)
        _print_validate_http_diagnostics(
            build_execution_plan(
                wf,
                workflow_path=workflow_path,
                data_dir=profile_data_dir,
            )
        )
        console.print(f"[green]✓[/green] '{wf.config.id}' is valid")
    except Exception as exc:
        if isinstance(exc, typer.Exit):
            raise
        if json_output:
            sys.stdout.write(
                json.dumps(
                    {
                        "ok": False,
                        "errors": [
                            {
                                "id": "workflow.validation_failed",
                                "code": "workflow.validation_failed",
                                "severity": "error",
                                "targetType": "workflow",
                                "subject": "workflow",
                                "message": str(exc),
                            }
                        ],
                        "warnings": [],
                        "diagnostics": [],
                    },
                    indent=2,
                )
            )
            sys.stdout.write("\n")
            raise typer.Exit(1)
        console.print(f"[red]✗[/red] Validation failed: {exc}")
        raise typer.Exit(1)


@app.command("import")
def import_workflow(
    source: Path = typer.Argument(..., help="Workflow TOML file or workflow bundle to import"),
    replace: bool = typer.Option(False, "--replace", help="Replace an existing workflow"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview a bundle import without writing files",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Import a workflow TOML file or portable workflow bundle."""
    base = data_dir or get_data_dir()
    if not source.exists():
        console.print(f"[red]{source} not found[/red]")
        raise typer.Exit(1)

    if source.suffix != ".toml":
        try:
            plan = import_workflow_bundle(
                source,
                data_dir=base,
                replace=replace,
                dry_run=dry_run,
            )
        except BundleError as exc:
            console.print(f"[red]Import failed: {exc}[/red]")
            raise typer.Exit(1)
        if not dry_run and plan.workflow_path.exists():
            capture_workflow_revision(plan.workflow_path, base, source="import", author="cli")
        _print_bundle_import_plan(plan.to_dict(), dry_run=dry_run)
        return

    try:
        if dry_run:
            console.print("[red]--dry-run is only supported for workflow bundles[/red]")
            raise typer.Exit(1)
        if replace:
            workflow = AgenticWorkflow.from_file(source)
            workflow.validate(source)
            base.mkdir(parents=True, exist_ok=True)
            destination = base / f"{workflow.config.id}.toml"
            workflow.to_file(destination)
            capture_workflow_revision(destination, base, source="import", author="cli")
            console.print(f"[green]Imported[/green] {workflow.config.id} → {destination}")
            return

        payload = import_workflow_payload(source.read_text(encoding="utf-8"), base)
    except WorkflowAlreadyExistsError as exc:
        console.print(f"[red]{exc}. Use --replace to overwrite.[/red]")
        raise typer.Exit(1)
    except (WorkflowCreateError, OSError, Exception) as exc:
        console.print(f"[red]Import failed: {exc}[/red]")
        raise typer.Exit(1)

    workflow_id = str(payload["id"])
    console.print(f"[green]Imported[/green] {workflow_id} → {base / f'{workflow_id}.toml'}")


@app.command("export")
def export_workflow(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    output: Path = typer.Option(..., "--output", "-o", help="Bundle archive to create"),
    notes: str | None = typer.Option(None, "--notes", help="Optional bundle notes"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Export a workflow and related local files into a portable bundle."""
    try:
        manifest = export_workflow_bundle(workflow, output, data_dir=data_dir, notes=notes)
    except BundleError as exc:
        console.print(f"[red]Export failed: {exc}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]Exported[/green] {manifest.workflow_id} → {output} "
        f"({len(manifest.included_paths)} files, "
        f"{len(manifest.external_requirements)} external requirements)"
    )


@app.command("preview-import")
def preview_import_workflow(
    source: Path = typer.Argument(..., help="Workflow bundle to preview"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Preview a workflow bundle import without writing files."""
    try:
        plan = preview_workflow_bundle(source, data_dir=data_dir)
    except BundleError as exc:
        console.print(f"[red]Preview failed: {exc}[/red]")
        raise typer.Exit(1)
    _print_bundle_import_plan(plan.to_dict(), dry_run=True)


@app.command("history")
def history(
    workflow: str = typer.Argument(..., help="Workflow ID"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum revisions to show"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List workflow revisions."""
    base = data_dir or get_data_dir()
    try:
        revisions = list_workflow_revisions(workflow, base, limit=limit)
    except WorkflowRevisionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if json_output:
        console.print(json.dumps([item.to_dict() for item in revisions], indent=2))
        return
    if not revisions:
        console.print(f"No revisions found for [bold]{workflow}[/bold].")
        return
    table = Table("Revision", "Created", "Source", "Author", "Summary")
    for revision in revisions:
        table.add_row(
            revision.revision_id,
            revision.created_at,
            revision.source,
            revision.author,
            "; ".join(revision.summary),
        )
    console.print(table)


@app.command("diff")
def diff(
    workflow: str = typer.Argument(..., help="Workflow ID"),
    revision_id: str = typer.Argument(..., help="Revision ID to compare with current"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Show a revision diff against the current workflow."""
    base = data_dir or get_data_dir()
    try:
        payload = diff_workflow_revision(workflow, revision_id, base)
    except WorkflowRevisionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if json_output:
        console.print(json.dumps(payload, indent=2))
        return
    for item in payload["summary"]:
        console.print(f"• {item}")
    toml_diff = str(payload.get("tomlDiff") or "")
    if toml_diff:
        console.print(toml_diff)


@app.command("restore")
def restore(
    workflow: str = typer.Argument(..., help="Workflow ID"),
    revision_id: str = typer.Argument(..., help="Revision ID to restore"),
    as_copy: bool = typer.Option(False, "--as-copy", help="Restore as a new workflow"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Restore a workflow revision, validating before replacement."""
    base = data_dir or get_data_dir()
    try:
        payload = restore_workflow_revision(
            workflow,
            revision_id,
            base,
            as_copy=as_copy,
            source="restore",
            author="cli",
        )
    except (WorkflowRevisionError, ValueError) as exc:
        console.print(f"[red]Restore failed: {exc}[/red]")
        raise typer.Exit(1)
    action = "Restored copy" if as_copy else "Restored"
    console.print(f"[green]{action}[/green] {payload['workflowId']} → {payload['path']}")


@logs_app.command("latest")
def logs_latest(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    path_only: bool = typer.Option(False, "--path-only", help="Print only the log path"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Print the latest run log for a workflow."""
    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        payload = latest_workflow_log_payload(workflow_id, base)
    except (KeyError, WorkflowLogError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    log_path = payload.get("logPath")
    if not log_path:
        console.print(f"No run logs found for [bold]{workflow_id}[/bold].")
        return
    if path_only:
        console.print(str(log_path))
        return
    console.print(payload.get("logText") or "")


@logs_app.command("list")
def logs_list(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    offset: int = typer.Option(0, "--offset", min=0, help="Number of runs to skip"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum runs to list"),
    status: str | None = typer.Option(None, "--status", help="Filter by status"),
    trigger: str | None = typer.Option(None, "--trigger", help="Filter by trigger type"),
    search: str | None = typer.Option(None, "--search", help="Search run metadata and log text"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List run logs for a workflow."""
    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        payload = list_workflow_run_logs_payload(
            workflow_id,
            base,
            offset=offset,
            limit=limit,
            status=status,
            trigger_type=trigger,
            search=search,
        )
    except (KeyError, WorkflowLogError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    runs = payload.get("runs") or []
    if not runs:
        console.print(f"No run logs found for [bold]{workflow_id}[/bold].")
        pagination = payload.get("pagination") if isinstance(payload, dict) else {}
        if isinstance(pagination, dict) and pagination.get("searchTruncated"):
            console.print(
                "[yellow]Search stopped at the configured log scan byte limit; "
                "results may be incomplete.[/yellow]"
            )
        return

    for run_log in runs:
        console.print(
            "\t".join(
                (
                    str(run_log["id"]),
                    str(run_log.get("startedAt") or "unknown"),
                    str(run_log.get("status") or "unknown"),
                    str(run_log.get("triggerType") or "unknown"),
                    str(run_log.get("logSizeBytes") or 0),
                    str(base / "logs" / workflow_id / str(run_log["id"])),
                )
            )
        )
    pagination = payload.get("pagination") if isinstance(payload, dict) else {}
    if isinstance(pagination, dict) and pagination.get("searchTruncated"):
        console.print(
            "[yellow]Search stopped at the configured log scan byte limit; "
            "results may be incomplete.[/yellow]"
        )


@logs_app.command("show")
def logs_show(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    run_id: str = typer.Argument(..., help="Run log file name from workflow logs list"),
    offset: int | None = typer.Option(None, "--offset", min=0, help="Read from byte offset"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum bytes to read"),
    tail_bytes: int | None = typer.Option(None, "--tail-bytes", min=1, help="Read last N bytes"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Print a specific workflow run log."""
    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        payload = workflow_run_log_payload(
            workflow_id,
            run_id,
            base,
            offset=offset,
            limit=limit,
            tail_bytes=tail_bytes,
            include_details=False,
        )
    except (KeyError, WorkflowLogError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(payload.get("logText") or "")


@logs_app.command("prune")
def logs_prune(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    keep_last: int | None = typer.Option(
        None,
        "--keep-last",
        min=0,
        help="Always keep newest N runs",
    ),
    keep_days: int | None = typer.Option(
        None,
        "--keep-days",
        min=0,
        help="Keep runs newer than N days",
    ),
    keep_failed_days: int | None = typer.Option(
        None,
        "--keep-failed-days",
        min=0,
        help="Keep failed runs newer than N days",
    ),
    apply: bool = typer.Option(False, "--apply", help="Delete matching runs instead of previewing"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Preview or prune old workflow run logs and sidecar files."""
    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        payload = prune_workflow_run_logs_payload(
            workflow_id,
            base,
            keep_last=keep_last,
            keep_days=keep_days,
            keep_failed_days=keep_failed_days,
            dry_run=not apply,
        )
    except (KeyError, WorkflowLogError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    runs = payload.get("runs") or []
    action = "Would remove" if payload.get("dryRun") else "Removed"
    console.print(f"{action} {len(runs)} run(s) for [bold]{workflow_id}[/bold].")
    for run_log in runs:
        console.print(
            "\t".join(
                (
                    str(run_log["id"]),
                    str(run_log.get("startedAt") or "unknown"),
                    str(run_log.get("status") or "unknown"),
                    str(run_log.get("logSizeBytes") or 0),
                )
            )
        )


@app.command("show")
def show(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Display the graph structure of a workflow."""
    from gofer.cli.dag_renderer import render_workflow

    try:
        wf = _resolve_workflow(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    render_workflow(wf, console)


@app.command("set-info")
def set_info(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    name: str | None = typer.Option(None, "--name", help="Workflow display name"),
    max_total_node_runs: int | None = typer.Option(
        None,
        "--max-total-node-runs",
        min=1,
        help="Maximum node executions allowed in one workflow run",
    ),
    run_continuously: bool | None = typer.Option(
        None,
        "--run-continuously/--no-run-continuously",
        help="Keep one workflow run active and ignore schedule/watch starts",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Update top-level workflow settings without opening an editor."""
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    wf.config = WorkflowConfig(
        id=wf.config.id,
        name=name if name is not None else wf.config.name,
        schedule=wf.config.schedule,
        watch=wf.config.watch,
        run_continuously=(
            run_continuously if run_continuously is not None else wf.config.run_continuously
        ),
        max_total_node_runs=(
            max_total_node_runs
            if max_total_node_runs is not None
            else wf.config.max_total_node_runs
        ),
    )
    _save_workflow(wf, path)


@app.command("set-schedule")
def set_schedule(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    cron: str = typer.Option(..., "--cron", help="Cron expression"),
    timezone: str = typer.Option("UTC", "--timezone", help="Schedule timezone"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Add or replace a workflow cron schedule."""
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    wf.config.schedule = ScheduleConfig(cron_expression=cron, timezone=timezone)
    _save_workflow(wf, path)


@app.command("clear-schedule")
def clear_schedule(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Remove a workflow cron schedule."""
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    wf.config.schedule = None
    _save_workflow(wf, path)


@app.command("set-watch")
def set_watch(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    path_value: Path = typer.Option(..., "--path", help="File or folder to watch"),
    glob: str = typer.Option("*", "--glob", help="Glob pattern for watched paths"),
    recursive: bool = typer.Option(False, "--recursive", help="Watch subdirectories"),
    debounce_seconds: float = typer.Option(
        1.0, "--debounce-seconds", min=0.0, help="Seconds to debounce filesystem events"
    ),
    mode: str = typer.Option("batch", "--mode", help="batch, queue, or fanout"),
    max_concurrency: int = typer.Option(
        1, "--max-concurrency", min=1, help="Maximum concurrent queued watcher runs"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Add or replace workflow file/folder watcher settings."""
    try:
        wf, workflow_path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    try:
        wf.config.watch = WatchConfig(
            path=path_value,
            glob=glob,
            recursive=recursive,
            debounce_seconds=debounce_seconds,
            mode=mode,  # type: ignore[arg-type]
            max_concurrency=max_concurrency,
        )
    except Exception as exc:
        console.print(f"[red]Invalid watch config: {exc}[/red]")
        raise typer.Exit(1)
    _save_workflow(wf, workflow_path)


@app.command("clear-watch")
def clear_watch(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Remove workflow file/folder watcher settings."""
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    wf.config.watch = None
    _save_workflow(wf, path)


@app.command("add-agent")
def add_agent(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    agent_id: str = typer.Option(..., "--id", help="Agent ID"),
    subscription: str = typer.Option(
        ...,
        "--subscription",
        help="codex, claude_code, openai_api, or anthropic_api",
    ),
    working_dir: Path = typer.Option(..., "--working-dir", help="Agent working directory"),
    prompt_path: Path = typer.Option(..., "--prompt-path", help="Prompt markdown path"),
    tool: list[str] | None = typer.Option(None, "--tool", help="Allowed tool name"),
    mcp_server: list[str] | None = typer.Option(None, "--mcp-server", help="MCP server name"),
    extra_path: list[Path] | None = typer.Option(
        None,
        "--extra-path",
        help="Additional path to grant the provider sandbox (repeatable)",
    ),
    env: list[str] | None = typer.Option(None, "--env", help="Environment KEY=VALUE"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Add or replace an agent config in a workflow."""
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    try:
        wf.register_agent(
            AgentConfig(
                agent_id=agent_id,
                subscription=subscription,  # type: ignore[arg-type]
                working_dir=working_dir,
                prompt_path=prompt_path,
                tools=tool or [],
                mcp_servers=mcp_server or [],
                env=_parse_key_values(env, "--env"),
                extra_paths=[path.expanduser().resolve() for path in extra_path or []],
            )
        )
    except Exception as exc:
        console.print(f"[red]Invalid agent config: {exc}[/red]")
        raise typer.Exit(1)
    _save_workflow(wf, path)


@app.command("add-node")
def add_node(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    node_id: str = typer.Option(..., "--id", help="Node ID"),
    node_type: str = typer.Option(..., "--type", help="Node operation type"),
    command: str | None = typer.Option(None, "--command", help="Command for command nodes"),
    script_path: Path | None = typer.Option(
        None, "--script-path", help="Script path for script nodes"
    ),
    path_value: Path | None = typer.Option(
        None, "--path", help="Path for read/write/delete/file/folder nodes"
    ),
    source_path: Path | None = typer.Option(
        None, "--source-path", help="Source path for copy/move nodes"
    ),
    destination_path: Path | None = typer.Option(
        None, "--destination-path", help="Destination path for copy/move nodes"
    ),
    target: str | None = typer.Option(None, "--target", help="Target for open_resource"),
    resource_type: str = typer.Option(
        "auto", "--resource-type", help="auto, file, folder, url, or app"
    ),
    content: str = typer.Option("", "--content", help="Content for write_file nodes"),
    message: str = typer.Option("", "--message", help="Message for pass/fail nodes"),
    template: str = typer.Option("", "--template", help="Inline prompt template"),
    template_path: Path | None = typer.Option(
        None, "--template-path", help="Prompt template file path"
    ),
    output_path: Path | None = typer.Option(
        None, "--output-path", help="Output path for prompt/index nodes"
    ),
    working_dir: Path | None = typer.Option(
        None, "--working-dir", help="Working directory for command/agent nodes"
    ),
    agent_id: str | None = typer.Option(None, "--agent-id", help="Agent ID for agent nodes"),
    prompt_path: Path | None = typer.Option(
        None, "--prompt-path", help="Prompt path for agent nodes"
    ),
    skill_name: str | None = typer.Option(
        None, "--skill-name", help="Skill name for agent skill invocation"
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Provider profile for agent/common LLM nodes"
    ),
    model: str | None = typer.Option(
        None, "--model", help="Provider model override for agent/common LLM nodes"
    ),
    provider_timeout: float | None = typer.Option(
        None,
        "--provider-timeout",
        min=0.0,
        help="Provider CLI timeout override for agent/common LLM nodes",
    ),
    task: str = typer.Option("summarize", "--task", help="Common LLM task preset"),
    target_text: str = typer.Option(
        "", "--task-target", "--query", help="Task target text or search query"
    ),
    instructions: str = typer.Option("", "--instructions", help="Task instructions"),
    index_path: Path | None = typer.Option(None, "--index-path", help="Local vector index path"),
    vector_mode: str = typer.Option(
        "incremental",
        "--vector-mode",
        help="local_vectorize mode: incremental, full, validate, or compact",
    ),
    search_top_k: int = typer.Option(5, "--top-k", min=1, help="local_search result count"),
    search_score_threshold: float = typer.Option(
        0.0,
        "--score-threshold",
        help="Minimum local_search score to include",
    ),
    search_include_snippets: bool = typer.Option(
        True,
        "--include-snippets/--no-include-snippets",
        help="Include snippets in local_search results",
    ),
    search_include_file_metadata: bool = typer.Option(
        True,
        "--include-file-metadata/--no-include-file-metadata",
        help="Include file metadata in local_search results",
    ),
    dynamic_count: str = typer.Option("1", "--dynamic-count", help="Agent dynamic count"),
    memory: str = typer.Option(
        "none",
        "--memory",
        help="Agent memory mode: none, run, or all",
    ),
    input_map: list[str] | None = typer.Option(
        None, "--input-map", help="Agent input mapping KEY=CTX_PATH"
    ),
    node_input: list[str] | None = typer.Option(
        None,
        "--input",
        help=(
            "Common node input KEY=CTX_PATH. Use stdin=SOURCE or env.NAME=SOURCE for command nodes."
        ),
    ),
    input_mapping_json: str | None = typer.Option(
        None, "--input-mapping-json", help="Agent input mapping JSON object"
    ),
    output_schema: str | None = typer.Option(
        None,
        "--output-schema",
        help="Named output schema or inline JSON Schema object for agent/common LLM nodes",
    ),
    repair_attempts: int = typer.Option(
        0,
        "--repair-attempts",
        min=0,
        max=3,
        help="Structured-output repair attempts for agent/common LLM nodes",
    ),
    variable: list[str] | None = typer.Option(
        None, "--variable", help="Prompt template variable KEY=CTX_PATH_OR_VALUE"
    ),
    env: list[str] | None = typer.Option(None, "--env", help="Environment KEY=VALUE"),
    arg: list[str] | None = typer.Option(None, "--arg", help="Script/open-resource arg"),
    create_dirs: bool = typer.Option(True, "--create-dirs/--no-create-dirs"),
    overwrite: bool | None = typer.Option(
        None,
        "--overwrite/--no-overwrite",
        help="Override file overwrite behavior for write/copy/move nodes",
    ),
    append: bool = typer.Option(False, "--append", help="Append in write_file nodes"),
    use_trash: bool = typer.Option(True, "--trash/--no-trash"),
    recursive: bool = typer.Option(False, "--recursive"),
    missing_ok: bool = typer.Option(False, "--missing-ok"),
    encoding: str = typer.Option("utf-8", "--encoding"),
    errors: str = typer.Option("strict", "--errors"),
    pipe_output: bool = typer.Option(False, "--pipe-output"),
    allow_failure: bool = typer.Option(
        False,
        "--allow-failure",
        help="Allow this node to fail without failing the overall workflow",
    ),
    await_all_inputs: bool = typer.Option(
        True,
        "--await-all-inputs/--no-await-all-inputs",
        help="Wait for all upstream nodes before this node runs",
    ),
    retry_count: int = typer.Option(0, "--retry-count", min=0),
    retry_delay_seconds: float = typer.Option(1.0, "--retry-delay-seconds", min=0.0),
    timeout_seconds: float | None = typer.Option(None, "--timeout-seconds", min=0.0),
    fan_source: str | None = typer.Option(
        None,
        "--fan-source",
        help="loop source: count, tabular, directory, trigger-events, or infinite",
    ),
    fan_count: str = typer.Option("1", "--fan-count"),
    fan_path: Path | None = typer.Option(None, "--fan-path"),
    fan_glob: str = typer.Option("*", "--fan-glob"),
    fan_include_content: bool = typer.Option(False, "--fan-include-content"),
    fan_max_concurrency: int = typer.Option(1, "--fan-max-concurrency", min=1),
    fan_fail_fast: bool = typer.Option(False, "--fan-fail-fast"),
    http_method: str = typer.Option("GET", "--method", help="HTTP method for http_request"),
    http_url: str | None = typer.Option(None, "--url", help="URL for http_request"),
    http_header: list[str] | None = typer.Option(None, "--header", help="HTTP header KEY=VALUE"),
    http_param: list[str] | None = typer.Option(
        None, "--param", help="HTTP query parameter KEY=VALUE"
    ),
    http_json: str | None = typer.Option(None, "--json-body", help="HTTP JSON request body"),
    http_body: str | None = typer.Option(None, "--body", help="HTTP raw request body"),
    http_timeout: float = typer.Option(30.0, "--http-timeout", min=0.1),
    http_retry_attempts: int = typer.Option(1, "--http-retry-attempts", min=1),
    http_retry_backoff: float = typer.Option(0.0, "--http-retry-backoff", min=0.0),
    http_retry_status: list[int] | None = typer.Option(
        None, "--http-retry-status", help="HTTP status that should be retried"
    ),
    http_expected_status: list[int] | None = typer.Option(
        None, "--expected-status", help="Expected successful HTTP status"
    ),
    http_response_mode: str = typer.Option(
        "auto", "--response-mode", help="auto, json, text, or none"
    ),
    http_output_map: list[str] | None = typer.Option(
        None, "--output-map", help="HTTP output mapping KEY=response.path"
    ),
    http_secret_field: list[str] | None = typer.Option(
        None, "--secret-field", help="HTTP field/path to mask in logs and UI"
    ),
    approval_timeout: float | None = typer.Option(
        None, "--approval-timeout", help="Seconds before an approval gate times out"
    ),
    approval_timeout_decision: str = typer.Option(
        "timeout", "--timeout-decision", help="Approval timeout output: timeout or reject"
    ),
    approver: list[str] | None = typer.Option(
        None, "--approver", help="Allowed approver identity for approval_gate"
    ),
    approval_notify: bool = typer.Option(
        False, "--notify", help="Send a desktop notification for approval_gate"
    ),
    notification_title: str = typer.Option(
        "Raticode notification", "--title", help="Notification or approval title"
    ),
    notification_body: str = typer.Option("", "--notification-body", help="Notification body"),
    notification_channel: str = typer.Option("desktop", "--channel", help="Notification channel"),
    notification_urgency: str = typer.Option("normal", "--urgency", help="Notification urgency"),
    notification_webhook_url: str | None = typer.Option(
        None, "--notification-webhook-url", help="Slack, Teams, or generic webhook URL"
    ),
    notification_header: list[str] | None = typer.Option(
        None, "--notification-header", help="Notification webhook header KEY=VALUE"
    ),
    notification_payload_json: str | None = typer.Option(
        None, "--notification-payload-json", help="Generic webhook JSON payload"
    ),
    notification_email_from: str | None = typer.Option(
        None, "--notification-email-from", help="Email sender address"
    ),
    notification_email_to: list[str] | None = typer.Option(
        None, "--notification-email-to", help="Email recipient address"
    ),
    notification_smtp_host: str | None = typer.Option(
        None, "--notification-smtp-host", help="SMTP host for email notifications"
    ),
    notification_smtp_port: int = typer.Option(
        587, "--notification-smtp-port", help="SMTP port for email notifications"
    ),
    notification_smtp_username: str | None = typer.Option(
        None, "--notification-smtp-username", help="SMTP username or secret ref"
    ),
    notification_smtp_password: str | None = typer.Option(
        None, "--notification-smtp-password", help="SMTP password secret ref"
    ),
    notification_smtp_starttls: bool = typer.Option(
        True, "--notification-smtp-starttls/--no-notification-smtp-starttls"
    ),
    notification_timeout: float = typer.Option(
        30.0, "--notification-timeout", help="Notification delivery timeout in seconds"
    ),
    notification_retry_attempts: int = typer.Option(
        1, "--notification-retry-attempts", help="Notification delivery attempts"
    ),
    notification_retry_backoff: float = typer.Option(
        0.0, "--notification-retry-backoff", help="Seconds between notification retries"
    ),
    notification_retry_status: list[int] | None = typer.Option(
        None, "--notification-retry-status", help="Webhook HTTP status that should be retried"
    ),
    notification_expected_status: list[int] | None = typer.Option(
        None, "--notification-expected-status", help="Expected webhook success status"
    ),
    notification_network_allowlist: list[str] | None = typer.Option(
        None, "--notification-network-allowlist", help="Allowed webhook host or CIDR"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Add or replace a workflow node.

    Machine-readable options and operation fields: gof schema --command
    workflow.add-node and gof schema --operation TYPE.
    """
    try:
        wf, workflow_path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    mapping = _parse_key_values(input_map, "--input-map")
    mapping.update(_parse_json_object(input_mapping_json, "--input-mapping-json"))
    try:
        operation = _operation_from_options(
            node_type,
            command=command,
            script_path=script_path,
            path=path_value,
            source_path=source_path,
            destination_path=destination_path,
            target=target,
            resource_type=resource_type,
            content=content,
            message=message,
            template=template,
            template_path=template_path,
            output_path=output_path,
            variable_mapping=_parse_key_values(variable, "--variable"),
            working_dir=working_dir,
            agent_id=agent_id,
            prompt_path=prompt_path,
            skill_name=skill_name,
            profile=profile,
            model=model,
            provider_timeout=provider_timeout,
            task=task,
            target_text=target_text,
            instructions=instructions,
            index_path=index_path,
            vector_mode=vector_mode,
            search_top_k=search_top_k,
            search_score_threshold=search_score_threshold,
            search_include_snippets=search_include_snippets,
            search_include_file_metadata=search_include_file_metadata,
            dynamic_count=dynamic_count,
            memory=memory,
            input_mapping=mapping,
            output_schema=_parse_output_schema(output_schema),
            repair_attempts=repair_attempts,
            env=_parse_key_values(env, "--env"),
            args=arg or [],
            create_dirs=create_dirs,
            overwrite=overwrite,
            append=append,
            use_trash=use_trash,
            recursive=recursive,
            missing_ok=missing_ok,
            encoding=encoding,
            errors=errors,
            fan_glob=fan_glob,
            fan_source=_fan_source_from_options(
                fan_source,
                fan_count,
                fan_path,
                fan_glob,
                fan_include_content,
                fan_max_concurrency,
                fan_fail_fast,
            ),
            http_method=http_method,
            http_url=http_url,
            http_headers=_parse_key_values(http_header, "--header"),
            http_params=_parse_key_values(http_param, "--param"),
            http_json=_parse_json_value(http_json, "--json-body"),
            http_body=http_body,
            http_timeout_seconds=http_timeout,
            http_retry_attempts=http_retry_attempts,
            http_retry_backoff_seconds=http_retry_backoff,
            http_retry_statuses=http_retry_status or [],
            http_expected_statuses=http_expected_status or [200],
            http_response_mode=http_response_mode,
            http_output_mapping=_parse_key_values(http_output_map, "--output-map"),
            http_secret_fields=http_secret_field or [],
            approval_timeout_seconds=approval_timeout,
            approval_timeout_decision=approval_timeout_decision,
            approval_approvers=approver or [],
            approval_notify=approval_notify,
            notification_title=notification_title,
            notification_body=notification_body,
            notification_channel=notification_channel,
            notification_urgency=notification_urgency,
            notification_webhook_url=notification_webhook_url,
            notification_headers=_parse_key_values(
                notification_header,
                "--notification-header",
            ),
            notification_payload=_parse_json_value(
                notification_payload_json,
                "--notification-payload-json",
            ),
            notification_email_from=notification_email_from,
            notification_email_to=notification_email_to or [],
            notification_smtp_host=notification_smtp_host,
            notification_smtp_port=notification_smtp_port,
            notification_smtp_username=notification_smtp_username,
            notification_smtp_password=notification_smtp_password,
            notification_smtp_starttls=notification_smtp_starttls,
            notification_timeout_seconds=notification_timeout,
            notification_retry_attempts=notification_retry_attempts,
            notification_retry_backoff_seconds=notification_retry_backoff,
            notification_retry_statuses=notification_retry_status or [],
            notification_expected_statuses=notification_expected_status or [200, 201, 202, 204],
            notification_network_allowlist=notification_network_allowlist or [],
        )
        wf.add_operation(
            GraphNode(
                node_id=node_id,
                operation=operation,
                inputs=_parse_key_values(node_input, "--input"),
                pipe_output=pipe_output,
                allow_failure=allow_failure,
                await_all_inputs=await_all_inputs,
                retry_count=retry_count,
                retry_delay_seconds=retry_delay_seconds,
                timeout_seconds=timeout_seconds,
            )
        )
    except Exception as exc:
        console.print(f"[red]Invalid node config: {exc}[/red]")
        raise typer.Exit(1)
    _save_workflow(wf, workflow_path)


@app.command("rm-node")
def rm_node(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    node_id: str = typer.Option(..., "--id", help="Node ID"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Remove a workflow node and its edges."""
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if node_id not in wf.graph._nodes:
        console.print(f"[red]Node '{node_id}' not found[/red]")
        raise typer.Exit(1)
    wf.graph._graph.remove_node(node_id)
    wf.graph._nodes.pop(node_id, None)
    wf.graph._edges = {edge: cfg for edge, cfg in wf.graph._edges.items() if node_id not in edge}
    _save_workflow(wf, path)


@app.command("add-edge")
def add_edge(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    from_node: str = typer.Option(..., "--from", help="Source node ID"),
    to_node: str = typer.Option(..., "--to", help="Target node ID"),
    condition: str = typer.Option("always", "--condition", help="Edge condition"),
    output_pattern: str | None = typer.Option(
        None, "--output-pattern", help="Regex for output_matches condition"
    ),
    field: str | None = typer.Option(
        None, "--field", help="Structured result field for output_field condition"
    ),
    operator: str | None = typer.Option(
        None, "--operator", help="Typed predicate operator for output_field condition"
    ),
    value: str | None = typer.Option(
        None, "--value", help="JSON comparison value for output_field condition"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Add or replace a workflow edge.

    Machine-readable options and fields: gof schema --command workflow.add-edge.
    """
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
        edge_condition = EdgeConditionType(condition)
        wf.then(
            from_node,
            to_node,
            EdgeConfig(
                from_node=from_node,
                to_node=to_node,
                condition=edge_condition,
                output_pattern=output_pattern,
                field=field,
                operator=OutputFieldOperator(operator) if operator is not None else None,
                value=json.loads(value) if value is not None else None,
            ),
        )
    except Exception as exc:
        console.print(f"[red]Invalid edge config: {exc}[/red]")
        raise typer.Exit(1)
    _save_workflow(wf, path)


@app.command("rm-edge")
def rm_edge(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    from_node: str = typer.Option(..., "--from", help="Source node ID"),
    to_node: str = typer.Option(..., "--to", help="Target node ID"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Remove a workflow edge."""
    try:
        wf, path = _resolve_workflow_with_path(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if not wf.graph._graph.has_edge(from_node, to_node):
        console.print(f"[red]Edge '{from_node}' -> '{to_node}' not found[/red]")
        raise typer.Exit(1)
    wf.graph._graph.remove_edge(from_node, to_node)
    wf.graph._edges.pop((from_node, to_node), None)
    _save_workflow(wf, path)


@app.command("list")
def list_workflows(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List all workflows in the data directory."""
    base = data_dir or get_data_dir()
    payload = list_workflow_payloads(base)
    workflows = payload.get("workflows") or []
    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    if not workflows:
        console.print(f"No workflows found in [bold]{base}[/bold].")
        return

    rows = []
    for workflow in workflows:
        schedule_payload = workflow.get("schedule") or {}
        schedule = (
            schedule_payload.get("cronExpression") if isinstance(schedule_payload, dict) else None
        ) or "—"
        rows.append(
            (
                str(workflow.get("id") or ""),
                str(workflow.get("name") or ""),
                schedule,
                str(len(workflow.get("agents") or [])),
                str(len(workflow.get("nodes") or [])),
            )
        )

    if not rows:
        console.print(f"No workflows found in [bold]{base}[/bold].")
        return

    table = Table("ID", "Name", "Schedule", "Agents", "Nodes")
    for row in rows:
        table.add_row(*row)
    console.print(table)


@app.command("edit")
def edit(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Interactively edit a workflow's fields in the terminal."""
    from gofer.cli.tui_editor import (
        FieldEditorApp,
        sections_to_workflow,
        workflow_to_sections,
    )

    try:
        wf = _resolve_workflow(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    base = data_dir or get_data_dir()
    path = base / f"{wf.config.id}.toml"
    sections = workflow_to_sections(wf)

    if FieldEditorApp(sections, title=f"Edit Workflow: {wf.config.id}").run():
        sections_to_workflow(sections, wf)
        try:
            wf.validate(path, base)
        except Exception as exc:
            console.print(f"[red]Validation failed: {exc}[/red]")
            raise typer.Exit(1)
        wf.to_file(path)
        console.print(f"[green]Saved[/green] {path}")
    else:
        console.print("[yellow]Edit cancelled.[/yellow]")


@app.command("rm")
def rm(
    workflow: str = typer.Argument(..., help="Workflow ID or path to TOML file"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Delete a workflow TOML file."""
    try:
        wf = _resolve_workflow(workflow, data_dir)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    base = data_dir or get_data_dir()
    resolved_path = Path(workflow)
    path = (
        resolved_path
        if resolved_path.suffix == ".toml" and resolved_path.exists()
        else base / f"{wf.config.id}.toml"
    )
    cleanup_base = data_dir or path.parent

    if not yes:
        typer.confirm(f"Delete workflow '{wf.config.id}' ({path})?", abort=True)

    path.unlink()
    shutil.rmtree(cleanup_base / "logs" / wf.config.id, ignore_errors=True)
    shutil.rmtree(cleanup_base / "agent-memory" / wf.config.id, ignore_errors=True)
    workflow_stop_path(wf.config.id, cleanup_base).unlink(missing_ok=True)
    delete_workflow_chat_prompt(cleanup_base, wf.config.id)
    console.print(f"[green]Deleted[/green] {path}")


@app.command("rename")
def rename(
    workflow: str = typer.Argument(..., help="Workflow ID"),
    name: str = typer.Option(..., "--name", help="New workflow name"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Rename a workflow and update its ID to match the new name."""
    try:
        saved = rename_workflow_payload(workflow, name, data_dir)
    except (WorkflowAlreadyExistsError, WorkflowUpdateError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Renamed[/green] workflow to {saved['name']} ({saved['id']})")


@app.command("duplicate")
def duplicate(
    workflow: str = typer.Argument(..., help="Workflow ID"),
    name: str | None = typer.Option(None, "--name", help="Name for duplicate"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Duplicate a workflow TOML file with a new ID/name."""
    try:
        saved = duplicate_workflow_payload(workflow, name, data_dir)
    except (WorkflowAlreadyExistsError, WorkflowCreateError, WorkflowUpdateError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Duplicated[/green] workflow to {saved['name']} ({saved['id']})")


@app.command("build")
def build(
    output: Path | None = typer.Option(None, "--output", help="Output path for workflow TOML"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Interactively build a workflow via a guided wizard."""
    from gofer.cli.commands.builder import WorkflowBuilder

    wf = WorkflowBuilder().run()
    if wf is None:
        raise typer.Abort()
    dest_dir = data_dir or get_data_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = output or dest_dir / f"{wf.config.id}.toml"
    wf.to_file(dest)
    capture_workflow_revision(dest, dest_dir, source="create", author="cli")
    console.print(f"[green]Saved[/green] {dest}")


@app.command("create")
def create(
    name: str | None = typer.Option(None, "--name", help="Workflow name"),
    template: str | None = typer.Option(
        None,
        "--template",
        help="Create from a built-in workflow template",
    ),
    list_templates: bool = typer.Option(
        False,
        "--list-templates",
        help="List built-in workflow templates",
    ),
    preview_template: bool = typer.Option(
        False,
        "--preview-template",
        help="Preview the selected template without creating files",
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Output directory (default: data dir)"
    ),
) -> None:
    """Create a new workflow scaffold in the data directory."""
    if list_templates:
        table = Table(title="Workflow templates")
        table.add_column("Name")
        table.add_column("Purpose")
        table.add_column("Inputs")
        for item in list_workflow_templates():
            table.add_row(
                item.name,
                item.purpose,
                ", ".join(str(param["name"]) for param in item.required_inputs) or "none",
            )
        console.print(table)
        return

    if template:
        if preview_template:
            item = preview_workflow_template(template)
            console.print(f"[bold]{item.title}[/bold] ({item.name})")
            console.print(item.purpose)
            if item.required_inputs:
                console.print("[yellow]Required inputs:[/yellow]")
                for param in item.required_inputs:
                    console.print(f"  • {param['name']} ({param.get('type', 'string')})")
            if item.provider_assumptions:
                console.print("[yellow]Provider assumptions:[/yellow]")
                for provider in item.provider_assumptions:
                    console.print(f"  • {provider['agentId']}: {provider['subscription']}")
            if item.generated_nodes:
                console.print("[yellow]Generated nodes:[/yellow]")
                for node in item.generated_nodes:
                    console.print(f"  • {node['id']} ({node['type']})")
            return
        dest = output or get_data_dir()
        try:
            result = create_workflow_from_template(
                template,
                dest,
                workflow_name=name,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        capture_workflow_revision(result.path, dest, source="template", author="cli")
        console.print(f"Created [bold]{result.path}[/bold] from template {result.template.name}")
        for path in result.created_paths[1:]:
            console.print(f"[dim]Created asset {path}[/dim]")
        return

    if not name:
        console.print("[red]--name is required unless --template or --list-templates is used[/red]")
        raise typer.Exit(1)

    wf_id = re.sub(r"[^a-z0-9-]", "-", name.lower())
    dest = output or get_data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{wf_id}.toml"
    content = f"""[workflow]
id = "{wf_id}"
name = "{name}"

# [workflow.schedule]
# cron_expression = "0 9 * * 1-5"
# timezone = "UTC"

# [workflow.watch]
# path = "inputs"
# glob = "*.txt"
# recursive = false
# debounce_seconds = 1.0
# mode = "batch" # batch, queue, or fanout
# max_concurrency = 1

# [[nodes]]
# id = "my-step"
# type = "bash_command"
# command = "echo hello"

# Control node examples:
# type = "start"
# type = "pass" message = "workflow completed"
# type = "fail" message = "required condition was not met"

# First-class file I/O node examples:
# type = "read_file"   path = "data/input.txt"
# type = "write_file"  path = "data/output.txt"  content = ""
# type = "copy_file"   source_path = "data/input.txt"  destination_path = "archive/input.txt"
# type = "move_file"   source_path = "data/input.txt"  destination_path = "archive/input.txt"
# type = "delete_file" path = "tmp/file.txt" use_trash = true
# type = "open_resource" target = "data/output.txt" resource_type = "auto"
"""
    path.write_text(content)
    capture_workflow_revision(path, dest, source="create", author="cli")
    console.print(f"Created [bold]{path}[/bold]")


@recipe_app.command("watch-folder-summarize")
def recipe_watch_folder_summarize(
    name: str = typer.Option(..., "--name", help="Workflow name"),
    watch_path: Path = typer.Option(..., "--watch-path", help="Folder to watch"),
    glob: str = typer.Option("*", "--glob", help="Changed file glob"),
    recursive: bool = typer.Option(False, "--recursive", help="Watch subdirectories"),
    provider: str = typer.Option(
        "codex",
        "--provider",
        help="codex, claude_code, openai_api, or anthropic_api",
    ),
    working_dir: Path = typer.Option(Path("."), "--working-dir", help="Agent working dir"),
    max_concurrency: int = typer.Option(
        4, "--max-concurrency", min=1, help="Agent fan-out concurrency"
    ),
    debounce_seconds: float = typer.Option(
        1.0, "--debounce-seconds", min=0.0, help="Watcher debounce seconds"
    ),
    output: Path | None = typer.Option(None, "--output", help="Workflow TOML output path"),
    prompt_path: Path | None = typer.Option(
        None, "--prompt-path", help="Prompt markdown path to create or reuse"
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Create a folder watcher workflow that fans changed files into an agent summarizer."""
    workflow_id = _slugify(name)
    base = data_dir or get_data_dir()
    workflow_path = output or base / f"{workflow_id}.toml"
    prompt_file = prompt_path or base / "prompts" / f"{workflow_id}-summarizer.md"

    if workflow_path.exists() and not overwrite:
        console.print(f"[red]{workflow_path} already exists. Use --overwrite to replace it.[/red]")
        raise typer.Exit(1)
    if prompt_file.exists() and not overwrite:
        console.print(f"[red]{prompt_file} already exists. Use --overwrite to replace it.[/red]")
        raise typer.Exit(1)

    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        """Summarize this changed file for a Raticode user.

Event kind: {{kind}}
File path: {{path}}
File name: {{name}}

Content:

{{file_content}}

Return a concise summary with:
- what the file contains or demonstrates
- notable commands, configuration, or workflow details
- any assumptions needed to use it
""",
        encoding="utf-8",
    )

    wf = AgenticWorkflow(
        WorkflowConfig(
            id=workflow_id,
            name=name,
            watch=WatchConfig(
                path=watch_path,
                glob=glob,
                recursive=recursive,
                debounce_seconds=debounce_seconds,
                mode="fanout",
                max_concurrency=1,
            ),
        )
    )
    wf.register_agent(
        AgentConfig(
            agent_id="summarizer",
            subscription=provider,  # type: ignore[arg-type]
            working_dir=working_dir,
            prompt_path=prompt_file,
            tools=[],
            mcp_servers=[],
            env={},
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="changed-files",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TriggerEventsFanSource(
                    type="trigger_events",
                    include_content=True,
                    max_concurrency=max_concurrency,
                    fail_fast=False,
                ),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="summarize-added-files",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="summarizer",
                prompt_path=prompt_file,
                working_dir=working_dir,
            ),
        )
    )
    wf.then("changed-files", "summarize-added-files")
    _save_workflow(wf, workflow_path)
    console.print("[green]Created folder watcher summarizer workflow[/green]")
