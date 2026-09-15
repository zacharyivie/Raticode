"""Executable deployment preflight for compiled Rattish IR."""

from __future__ import annotations

import codecs
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gofer.core.network_policy import NetworkPolicyViolation, validate_http_request_url
from gofer.core.provider_profiles import (
    resolve_provider_settings,
    unresolved_provider_secret_refs,
    validate_provider_settings,
)
from gofer.rattish.diagnostics import RattishDiagnostic, RattishError, SourcePosition, SourceSpan
from gofer.rattish.project_paths import path_kind, project_path
from gofer.rattish.prompt_templates import PromptTemplateError, validate_prompt_template
from gofer.rattish.provider_runtime import (
    default_provider_subscriptions,
    runtime_subscription_id,
)
from gofer.rattish.runtime import (
    DEFAULT_NODE_HANDLERS,
    NodeHandlerRegistry,
    _workflow_interface_fingerprint,
)
from gofer.subscriptions.base import Subscription
from gofer.utils.paths import get_data_dir


@dataclass(frozen=True, slots=True)
class PreflightFailure:
    code: str
    message: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreflightContext:
    project_root: Path
    data_dir: Path
    subscriptions: Mapping[str, Subscription]
    handlers: NodeHandlerRegistry
    registry: PreflightRegistry


@dataclass(frozen=True, slots=True)
class PreflightResult:
    diagnostics: tuple[RattishDiagnostic, ...]

    @property
    def ready(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


PreflightChecker = Callable[[Mapping[str, Any], PreflightContext], PreflightFailure | None]


class PreflightRegistry:
    """Preflight implementations keyed by runtime handler and contract check ID."""

    def __init__(
        self,
        checks: Mapping[tuple[str, str], PreflightChecker] | None = None,
    ) -> None:
        self._checks = dict(checks or {})

    def register(self, handler_id: str, check_id: str, checker: PreflightChecker) -> None:
        key = (handler_id, check_id)
        if key in self._checks:
            raise ValueError(f"Preflight check {handler_id!r}/{check_id!r} is already registered.")
        self._checks[key] = checker

    def get(self, handler_id: str, check_id: str) -> PreflightChecker | None:
        return self._checks.get((handler_id, check_id))


def run_preflight(
    ir: Mapping[str, Any],
    *,
    registry: PreflightRegistry | None = None,
    data_dir: Path | None = None,
    subscriptions: Mapping[str, Subscription] | None = None,
    handlers: NodeHandlerRegistry | None = None,
) -> PreflightResult:
    """Run every preflight check frozen into IR without executing a node."""
    checks = registry or DEFAULT_PREFLIGHT_CHECKS
    runtime_handlers = handlers or DEFAULT_NODE_HANDLERS
    context = PreflightContext(
        Path(ir["source"]["project_root"]),
        data_dir or get_data_dir(),
        subscriptions if subscriptions is not None else default_provider_subscriptions(),
        runtime_handlers,
        checks,
    )
    diagnostics: list[RattishDiagnostic] = []
    if not ir["nodes"]:
        diagnostics.append(
            RattishDiagnostic(
                code="RATTISH_WORKFLOW_EMPTY",
                severity="error",
                phase="preflight",
                message="The workflow has no nodes to execute.",
                file=ir["source_map"]["workflow"]["file"],
                span=_source_span(ir["source_map"]["workflow"]),
                details={"workflow_id": ir["workflow"]["id"]},
                suggestions=("Add at least one Node declaration.",),
            )
        )
    for node in ir["nodes"]:
        for binding in node["bindings"]:
            source = binding["source"]
            for reference in _binding_references(source):
                if reference["root"] != "secret":
                    continue
                secret_name = reference["symbol"]
                if secret_name in os.environ or binding["default"]["present"]:
                    continue
                diagnostics.append(
                    RattishDiagnostic(
                        code="RATTISH_PREFLIGHT_SECRET_UNAVAILABLE",
                        severity="error",
                        phase="preflight",
                        message=f"Environment secret {secret_name!r} is unavailable.",
                        file=node["source_span"]["file"],
                        span=_source_span(node["source_span"]),
                        details={"node": node["id"], "secret": secret_name},
                        suggestions=(f"Set {secret_name} before running this workflow.",),
                    )
                )
        if not runtime_handlers.supports(node["runtime_handler"]):
            diagnostics.append(
                RattishDiagnostic(
                    code="RATTISH_PREFLIGHT_HANDLER_UNAVAILABLE",
                    severity="error",
                    phase="preflight",
                    message=(f"Runtime handler {node['runtime_handler']!r} is not installed."),
                    file=node["source_span"]["file"],
                    span=_source_span(node["source_span"]),
                    details={
                        "node": node["id"],
                        "handler": node["runtime_handler"],
                    },
                    suggestions=(
                        "Install the node plugin or use a node type supported by this runtime.",
                    ),
                )
            )
            continue
        for declaration in node["preflight_checks"]:
            checker = checks.get(node["runtime_handler"], declaration["id"])
            failure: PreflightFailure | None
            if checker is None:
                failure = PreflightFailure(
                    "RATTISH_PREFLIGHT_CHECK_UNAVAILABLE",
                    f"No implementation is installed for preflight check {declaration['id']!r}.",
                    {"handler": node["runtime_handler"], "check": declaration["id"]},
                )
            else:
                failure = checker(node, context)
            if failure is None:
                continue
            diagnostics.append(
                RattishDiagnostic(
                    code=failure.code,
                    severity=declaration["severity"],
                    phase="preflight",
                    message=failure.message,
                    file=node["source_span"]["file"],
                    span=_source_span(node["source_span"]),
                    details={"node": node["id"], "check": declaration["id"], **failure.details},
                    suggestions=(declaration["description"],),
                )
            )
    return PreflightResult(tuple(diagnostics))


def _binding_references(source: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if source["kind"] == "reference":
        return [source["reference"]]
    if source["kind"] != "expression":
        return []
    references: list[Mapping[str, Any]] = []

    def visit(expression: Mapping[str, Any]) -> None:
        kind = expression["kind"]
        if kind == "logical":
            visit(expression["left"])
            visit(expression["right"])
        elif kind == "not":
            visit(expression["operand"])
        elif kind in {"exists", "null_test", "reference"}:
            references.append(expression["reference"])
        elif kind == "comparison":
            references.extend(
                operand["reference"]
                for operand in (expression["left"], expression["right"])
                if operand["kind"] == "reference"
            )

    visit(source["expression"])
    return references


def _configuration_is_dynamic(node: Mapping[str, Any], field_name: str) -> bool:
    binding_name = field_name.replace("_", "-")
    if any(binding["name"] == binding_name for binding in node["bindings"]):
        return True

    def contains_template(value: Any) -> bool:
        if isinstance(value, str):
            return "{{" in value or "}}" in value
        if isinstance(value, list):
            return any(contains_template(item) for item in value)
        if isinstance(value, Mapping):
            return any(contains_template(item) for item in value.values())
        return False

    return contains_template(node["configuration"].get(field_name))


def _agent_provider_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    provider_id = node["resolutions"]["provider"]["provider_id"]
    try:
        subscription_id = runtime_subscription_id(provider_id)
    except ValueError:
        subscription_id = provider_id
    subscription = context.subscriptions.get(subscription_id)
    if subscription is not None and subscription.is_available():
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE",
        f"Provider runtime {provider_id!r} is not installed or available.",
        {"provider": provider_id, "subscription": subscription_id},
    )


def _agent_profile_compatible(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    resolution = node["resolutions"]["provider"]
    try:
        subscription_id = runtime_subscription_id(resolution["provider_id"])
        settings = resolve_provider_settings(
            agent_subscription=subscription_id,
            profile_name=node["configuration"]["profile"],
            operation_model=resolution["model"],
            operation_effort=resolution["effort"],
            data_dir=context.data_dir,
        )
        validate_provider_settings(settings)
    except ValueError as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_PROFILE_UNAVAILABLE",
            str(exc),
            {"provider": resolution["provider_id"], "profile": node["configuration"]["profile"]},
        )
    missing_secrets = unresolved_provider_secret_refs(settings)
    if missing_secrets:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_SECRET_UNAVAILABLE",
            "Provider credentials are unavailable: " + ", ".join(missing_secrets),
            {"secrets": missing_secrets},
        )
    return None


def _agent_prompt_path_readable(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "prompt_path"):
        return None
    prompt_path = node["configuration"]["prompt_path"]
    if prompt_path is None:
        return None
    path = project_path(context.project_root, prompt_path)
    if path.is_file() and os.access(path, os.R_OK):
        try:
            validate_prompt_template(
                path.read_text(encoding="utf-8"),
                {binding["name"] for binding in node["bindings"]},
            )
        except (OSError, UnicodeError, PromptTemplateError) as exc:
            return PreflightFailure(
                "RATTISH_PREFLIGHT_PROMPT_TEMPLATE_INVALID",
                str(exc),
                {"path": str(path)},
            )
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Agent prompt file {path} is unavailable or unreadable.",
        {"path": str(path)},
    )


def _agent_working_dir_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "working_dir"):
        return None
    path = project_path(context.project_root, node["configuration"]["working_dir"])
    if path.is_dir() and os.access(path, os.R_OK | os.X_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Agent working directory {path} is unavailable or inaccessible.",
        {"path": str(path)},
    )


def _local_vector_source_readable(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "source_path"):
        return None
    path = project_path(context.project_root, node["configuration"]["source_path"])
    if path.exists() and os.access(path, os.R_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Local vector source {path} is unavailable or unreadable.",
        {"path": str(path)},
    )


def _local_vector_destination_ready(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "index_path"):
        return None
    path = project_path(context.project_root, node["configuration"]["index_path"])
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if parent.is_dir() and os.access(parent, os.W_OK | os.X_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_DESTINATION_UNAVAILABLE",
        f"Local vector index destination {path} is not writable.",
        {"path": str(path)},
    )


def _local_vector_encoding_supported(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    encoding = node["configuration"]["encoding"]
    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_CONFIGURATION_INVALID",
            str(exc),
            {"encoding": encoding},
        )
    return None


def _local_vector_strategy_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    configuration = node["configuration"]
    selected = (
        configuration["embedding_strategy"],
        configuration["search_strategy"],
    )
    if selected == ("hash_token_v1", "cosine_v1"):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_CONFIGURATION_INVALID",
        "The selected local retrieval strategy pair is unavailable.",
        {"embedding_strategy": selected[0], "search_strategy": selected[1]},
    )


def _local_search_index_readable(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "index_path"):
        return None
    path = project_path(context.project_root, node["configuration"]["index_path"])
    if path.is_file() and os.access(path, os.R_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Local vector index {path} is unavailable or unreadable.",
        {"path": str(path)},
    )


def _approval_store_ready(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = node
    parent = context.data_dir
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if parent.is_dir() and os.access(parent, os.W_OK | os.X_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_APPROVAL_STORE_UNAVAILABLE",
        f"Approval store root {context.data_dir} is not writable.",
        {"path": str(context.data_dir)},
    )


def _loop_source_ready(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "source"):
        return None
    source = node["configuration"]["source"]
    if source["type"] not in {"tabular", "directory"}:
        return None
    authored_path = source.get("path")
    if not isinstance(authored_path, str):
        return PreflightFailure(
            "RATTISH_PREFLIGHT_CONFIGURATION_INVALID",
            f"Loop source {source['type']!r} requires path.",
            {"field": "source.path"},
        )
    # Upstream nodes may create these inputs. Check access when the loop activates.
    return None


def _bash_shell_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = node, context
    executable = "powershell.exe" if sys.platform == "win32" else "bash"
    if shutil.which(executable) is not None:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Required shell executable {executable!r} is unavailable.",
        {"executable": executable},
    )


def _bash_working_dir_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "working_dir"):
        return None
    working_dir = node["configuration"]["working_dir"]
    path = (
        context.project_root
        if working_dir is None
        else _project_path(context.project_root, working_dir)
    )
    if path.is_dir() and os.access(path, os.R_OK | os.X_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Bash working directory {path} is unavailable or inaccessible.",
        {"path": str(path)},
    )


def _read_path_readable(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "path"):
        return None
    path = _project_path(context.project_root, node["configuration"]["path"])
    if path.is_file() and os.access(path, os.R_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Input file {path} is unavailable or unreadable.",
        {"path": str(path)},
    )


def _folder_path_readable(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "path"):
        return None
    path = project_path(context.project_root, node["configuration"]["path"])
    if path.is_dir() and os.access(path, os.R_OK | os.X_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Folder {path} is unavailable or inaccessible.",
        {"path": str(path)},
    )


def _prompt_template_readable(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "template_path"):
        return None
    template_path = node["configuration"]["template_path"]
    if template_path is None:
        return None
    path = project_path(context.project_root, template_path)
    if path.is_file() and os.access(path, os.R_OK):
        variables = node["configuration"].get("variables", {})
        names = {binding["name"] for binding in node["bindings"]}
        if isinstance(variables, Mapping):
            names.update(str(name).lower() for name in variables)
        try:
            validate_prompt_template(path.read_text(encoding="utf-8"), names)
        except (OSError, UnicodeError, PromptTemplateError) as exc:
            return PreflightFailure(
                "RATTISH_PREFLIGHT_PROMPT_TEMPLATE_INVALID",
                str(exc),
                {"path": str(path)},
            )
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Prompt template {path} is unavailable or unreadable.",
        {"path": str(path)},
    )


def _open_target_ready(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "target"):
        return None
    configuration = node["configuration"]
    target = configuration["target"].strip()
    resource_type = configuration["resource_type"]
    if resource_type == "url" or (resource_type == "auto" and "://" in target):
        scheme = target.split(":", 1)[0].lower()
        if scheme in {"http", "https", "file", "mailto"}:
            return None
        return PreflightFailure(
            "RATTISH_PREFLIGHT_CONFIGURATION_INVALID",
            f"Resource URL {target!r} has an unsupported scheme.",
            {"target": target},
        )
    if resource_type == "app":
        return None
    try:
        path = project_path(context.project_root, target)
    except ValueError as exc:
        return PreflightFailure("RATTISH_PREFLIGHT_PATH_POLICY", str(exc), {"target": target})
    if resource_type == "file" and path.is_file() and os.access(path, os.R_OK):
        return None
    if resource_type == "folder" and path.is_dir() and os.access(path, os.R_OK | os.X_OK):
        return None
    if resource_type == "auto" and path.exists() and os.access(path, os.R_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Resource target {path} does not match type {resource_type!r}.",
        {"path": str(path), "resource_type": resource_type},
    )


def _resource_opener_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    configuration = node["configuration"]
    if configuration["resource_type"] == "app":
        if _configuration_is_dynamic(node, "target"):
            return None
        executable = configuration["target"]
    elif sys.platform == "win32":
        executable = "cmd"
    elif sys.platform == "darwin":
        executable = "open"
    else:
        executable = "xdg-open"
    if shutil.which(executable) is not None:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Resource opener {executable!r} is unavailable.",
        {"executable": executable},
    )


def _http_network_target_valid(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    configuration = node["configuration"]
    if _configuration_is_dynamic(node, "url"):
        return None
    try:
        validate_http_request_url(
            configuration["url"],
            allowlist=configuration["network_allowlist"],
            resolver=lambda _host, _port: (),
        )
    except NetworkPolicyViolation as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_NETWORK_POLICY",
            str(exc),
            {"url": configuration["url"]},
        )
    return None


def _notification_channel_configured(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    configuration = node["configuration"]
    bound = {binding["name"] for binding in node["bindings"]}
    if "channel" in bound:
        return None
    channel = configuration["channel"]
    if channel in {"slack", "teams", "webhook"}:
        if configuration["webhook_url"] is None and "webhook-url" not in bound:
            return PreflightFailure(
                "RATTISH_PREFLIGHT_NOTIFICATION_CONFIGURATION",
                f"{channel} notifications require webhook-url.",
                {"channel": channel, "field": "webhook-url"},
            )
        if "webhook-url" in bound:
            return None
        try:
            validate_http_request_url(
                configuration["webhook_url"],
                allowlist=configuration["network_allowlist"],
                resolver=lambda _host, _port: (),
            )
        except NetworkPolicyViolation as exc:
            return PreflightFailure(
                "RATTISH_PREFLIGHT_NETWORK_POLICY",
                str(exc),
                {"channel": channel},
            )
    if channel == "email":
        required = {
            "smtp-host": configuration["smtp_host"],
            "email-from": configuration["email_from"],
            "email-to": configuration["email_to"],
        }
        missing = [name for name, value in required.items() if not value and name not in bound]
        if missing:
            return PreflightFailure(
                "RATTISH_PREFLIGHT_NOTIFICATION_CONFIGURATION",
                "Email notification settings are missing: " + ", ".join(missing),
                {"channel": channel, "fields": missing},
            )
    return None


def _notification_channel_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    if any(binding["name"] == "channel" for binding in node["bindings"]):
        return None
    if node["configuration"]["channel"] != "desktop":
        return None
    if sys.platform == "win32":
        executables = ("powershell.exe", "powershell", "pwsh.exe", "pwsh")
        if any(shutil.which(executable) for executable in executables):
            return None
        message = "Desktop notifications require PowerShell on Windows."
    elif sys.platform == "darwin":
        if shutil.which("osascript") is not None:
            return None
        message = "Desktop notifications require osascript on macOS."
    else:
        if not os.environ.get("DISPLAY"):
            message = "Desktop notifications require DISPLAY on Unix desktop sessions."
        elif shutil.which("notify-send") is None:
            message = "Desktop notifications require notify-send on Unix desktop sessions."
        else:
            return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_NOTIFICATION_UNAVAILABLE",
        message,
        {"channel": "desktop"},
    )


def _read_encoding_supported(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    encoding = node["configuration"]["encoding"]
    errors = node["configuration"]["errors"]
    try:
        codecs.lookup(encoding)
        codecs.lookup_error(errors)
    except LookupError as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_CONFIGURATION_INVALID",
            str(exc),
            {"encoding": encoding, "errors": errors},
        )
    return None


def _python_interpreter_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    return _interpreter_available("python", node, context)


def _shell_interpreter_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    return _interpreter_available("bash", node, context)


def _interpreter_available(
    executable: str,
    node: Mapping[str, Any],
    context: PreflightContext,
) -> PreflightFailure | None:
    _ = node, context
    if shutil.which(executable) is not None:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Required script interpreter {executable!r} is unavailable.",
        {"executable": executable},
    )


def _script_readable(node: Mapping[str, Any], context: PreflightContext) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "script_path"):
        return None
    path = _project_path(context.project_root, node["configuration"]["script_path"])
    if path.is_file() and os.access(path, os.R_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Script file {path} is unavailable or unreadable.",
        {"path": str(path)},
    )


def _project_path(project_root: Path, authored: str) -> Path:
    return project_path(project_root, authored)


def _mutation_path_policy(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    configuration = node["configuration"]
    fields = [name for name in ("path", "source_path", "destination_path") if name in configuration]
    try:
        for field in fields:
            if _configuration_is_dynamic(node, field):
                continue
            project_path(context.project_root, configuration[field])
    except ValueError as exc:
        return PreflightFailure("RATTISH_PREFLIGHT_PATH_POLICY", str(exc), {"fields": fields})
    return None


def _source_readable(node: Mapping[str, Any], context: PreflightContext) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "source_path"):
        return None
    source = project_path(context.project_root, node["configuration"]["source_path"])
    if source.is_symlink():
        return None
    if path_kind(source) != "missing" and os.access(source, os.R_OK):
        if node["runtime_handler"] != "raticode.move_file" or os.access(
            source.parent, os.W_OK | os.X_OK
        ):
            return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Source path {source} is unavailable or unreadable.",
        {"path": str(source)},
    )


def _destination_ready(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    configuration = node["configuration"]
    destination_field = next(
        name for name in ("destination_path", "output_path", "path") if name in configuration
    )
    if _configuration_is_dynamic(node, destination_field):
        return None
    authored = configuration[destination_field]
    destination = project_path(context.project_root, authored)
    if destination.is_symlink() and node["runtime_handler"] == "raticode.write_file":
        return PreflightFailure(
            "RATTISH_PREFLIGHT_DESTINATION_INVALID",
            f"Write destination {destination} is a symlink.",
            {"path": str(destination)},
        )
    destination_exists = destination.exists() or destination.is_symlink()
    append_write = node["runtime_handler"] == "raticode.write_file" and configuration.get("append")
    if destination_exists and not append_write and not configuration.get("overwrite", True):
        return PreflightFailure(
            "RATTISH_PREFLIGHT_DESTINATION_INVALID",
            f"Destination {destination} exists and overwrite is false.",
            {"path": str(destination)},
        )
    parent = destination.parent
    existing_parent = next(
        (candidate for candidate in (parent, *parent.parents) if candidate.exists()),
        None,
    )
    if existing_parent is None or not os.access(existing_parent, os.W_OK | os.X_OK):
        return PreflightFailure(
            "RATTISH_PREFLIGHT_DESTINATION_INVALID",
            f"Destination parent for {destination} is not writable.",
            {"path": str(destination)},
        )
    if not parent.exists() and not configuration.get("create_dirs", False):
        return PreflightFailure(
            "RATTISH_PREFLIGHT_DESTINATION_INVALID",
            f"Destination parent {parent} is missing and create-dirs is false.",
            {"path": str(parent)},
        )
    return None


def _write_encoding_supported(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    encoding = node["configuration"]["encoding"]
    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_CONFIGURATION_INVALID", str(exc), {"encoding": encoding}
        )
    return None


def _delete_target_ready(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    configuration = node["configuration"]
    if _configuration_is_dynamic(node, "path"):
        return None
    target = project_path(context.project_root, configuration["path"])
    kind = path_kind(target)
    if kind == "missing" and not configuration["missing_ok"]:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_RESOURCE_MISSING",
            f"Delete target {target} is missing.",
            {"path": str(target)},
        )
    if kind == "directory" and not configuration["recursive"] and not configuration["use_trash"]:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_CONFIGURATION_INVALID",
            "recursive: true is required for permanent directory deletion.",
            {"path": str(target)},
        )
    return None


def _trash_ready(node: Mapping[str, Any], context: PreflightContext) -> PreflightFailure | None:
    _ = context
    if not node["configuration"]["use_trash"]:
        return None
    trash_root = get_data_dir() / "trash"
    existing_parent = next(
        (candidate for candidate in (trash_root, *trash_root.parents) if candidate.exists()),
        None,
    )
    if existing_parent is not None and os.access(existing_parent, os.W_OK | os.X_OK):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_DESTINATION_INVALID",
        f"Trash storage parent for {trash_root} is not writable.",
        {"path": str(trash_root)},
    )


def _child_workflow_ready(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file

    resolution = node["resolutions"]["workflow"]
    if resolution is None:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_CONFIGURATION_INVALID",
            "Workflow node has no frozen child resolution.",
            {},
        )
    try:
        if resolution["source_kind"] == "registry":
            from gofer.rattish.workspaces import find_registered_workflow

            registered = find_registered_workflow(
                resolution["source"], registry_dir=context.data_dir
            )
            child_path = registered.entrypoint
            child_root = registered.project_root
            child_id = registered.workflow_id
        else:
            child_path = project_path(context.project_root, resolution["source"])
            child_root = context.project_root
            child_id = resolution["workflow_id"]
        child = compile_rattish_file(
            child_path,
            data_dir=context.data_dir,
            workflow_id=child_id,
            project_root=child_root,
        )
    except (OSError, ValueError, RattishArtifactError, RattishError) as exc:
        diagnostics = (
            [item.to_json() for item in exc.diagnostics] if isinstance(exc, RattishError) else []
        )
        return PreflightFailure(
            "RATTISH_PREFLIGHT_CHILD_WORKFLOW_UNAVAILABLE",
            str(exc),
            {
                "workflow_id": resolution["workflow_id"],
                "diagnostics": diagnostics,
            },
        )
    actual = _workflow_interface_fingerprint(child.ir)
    if actual != resolution["interface_fingerprint"]:
        return PreflightFailure(
            "RATTISH_WORKFLOW_INTERFACE_CHANGED",
            "The referenced workflow interface changed after its caller was compiled.",
            {
                "workflow_id": resolution["workflow_id"],
                "expected": resolution["interface_fingerprint"],
                "actual": actual,
            },
        )
    actual_compilation = child.ir["source"]["compilation_fingerprint"]
    if actual_compilation != resolution["compilation_fingerprint"]:
        return PreflightFailure(
            "RATTISH_WORKFLOW_DEPENDENCY_CHANGED",
            "The referenced workflow implementation changed after its caller was compiled.",
            {
                "workflow_id": resolution["workflow_id"],
                "expected": resolution["compilation_fingerprint"],
                "actual": actual_compilation,
            },
        )
    child_result = run_preflight(
        child.ir,
        registry=context.registry,
        data_dir=context.data_dir,
        subscriptions=context.subscriptions,
        handlers=context.handlers,
    )
    if child_result.ready:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_CHILD_WORKFLOW_NOT_READY",
        f"Referenced workflow {resolution['workflow_id']!r} did not pass preflight.",
        {
            "workflow_id": resolution["workflow_id"],
            "diagnostics": [item.to_json() for item in child_result.diagnostics],
        },
    )


def _source_span(value: Mapping[str, Any]) -> SourceSpan:
    return SourceSpan(
        SourcePosition(**value["start"]),
        SourcePosition(**value["end"]),
    )


DEFAULT_PREFLIGHT_CHECKS = PreflightRegistry(
    {
        ("raticode.agent", "provider-available"): _agent_provider_available,
        ("raticode.agent", "profile-compatible"): _agent_profile_compatible,
        ("raticode.agent", "prompt-path-readable"): _agent_prompt_path_readable,
        ("raticode.agent", "working-dir-available"): _agent_working_dir_available,
        ("raticode.approval_gate", "approval-store-ready"): _approval_store_ready,
        ("raticode.bash_command", "shell-available"): _bash_shell_available,
        ("raticode.bash_command", "working-dir-available"): _bash_working_dir_available,
        ("raticode.python_script", "interpreter-available"): _python_interpreter_available,
        ("raticode.python_script", "script-readable"): _script_readable,
        ("raticode.read_file", "path-readable"): _read_path_readable,
        ("raticode.read_file", "encoding-supported"): _read_encoding_supported,
        ("raticode.file", "path-readable"): _read_path_readable,
        ("raticode.folder", "path-readable"): _folder_path_readable,
        ("raticode.http_request", "network-target-valid"): _http_network_target_valid,
        ("raticode.common_llm_task", "provider-available"): _agent_provider_available,
        ("raticode.common_llm_task", "profile-compatible"): _agent_profile_compatible,
        ("raticode.common_llm_task", "working-dir-available"): _agent_working_dir_available,
        ("raticode.local_vectorize", "source-readable"): _local_vector_source_readable,
        ("raticode.local_vectorize", "index-destination-ready"): _local_vector_destination_ready,
        ("raticode.local_vectorize", "encoding-supported"): _local_vector_encoding_supported,
        ("raticode.local_vectorize", "strategy-available"): _local_vector_strategy_available,
        ("raticode.local_search", "index-readable"): _local_search_index_readable,
        ("raticode.local_search", "strategy-available"): _local_vector_strategy_available,
        ("raticode.loop", "source-ready"): _loop_source_ready,
        ("raticode.notification", "channel-configured"): _notification_channel_configured,
        ("raticode.notification", "channel-available"): _notification_channel_available,
        ("raticode.prompt_file", "template-readable"): _prompt_template_readable,
        ("raticode.prompt_file", "destination-ready"): _destination_ready,
        ("raticode.prompt_file", "encoding-supported"): _write_encoding_supported,
        ("raticode.open_resource", "target-ready"): _open_target_ready,
        ("raticode.open_resource", "opener-available"): _resource_opener_available,
        ("raticode.shell_script", "interpreter-available"): _shell_interpreter_available,
        ("raticode.shell_script", "script-readable"): _script_readable,
        ("raticode.write_file", "path-policy"): _mutation_path_policy,
        ("raticode.write_file", "destination-ready"): _destination_ready,
        ("raticode.write_file", "encoding-supported"): _write_encoding_supported,
        ("raticode.copy_file", "path-policy"): _mutation_path_policy,
        ("raticode.copy_file", "source-readable"): _source_readable,
        ("raticode.copy_file", "destination-ready"): _destination_ready,
        ("raticode.move_file", "path-policy"): _mutation_path_policy,
        ("raticode.move_file", "source-readable"): _source_readable,
        ("raticode.move_file", "destination-ready"): _destination_ready,
        ("raticode.delete_file", "path-policy"): _mutation_path_policy,
        ("raticode.delete_file", "target-ready"): _delete_target_ready,
        ("raticode.delete_file", "trash-ready"): _trash_ready,
        ("raticode.workflow", "child-workflow-ready"): _child_workflow_ready,
    }
)
