"""Executable deployment preflight for compiled Rattish IR."""

from __future__ import annotations

import codecs
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gofer.core.network_policy import NetworkPolicyViolation, validate_http_request_url
from gofer.core.provider_profiles import (
    load_provider_profiles,
    resolve_provider_settings,
    unresolved_provider_secret_refs,
    validate_provider_settings,
)
from gofer.rattish import agent_sandbox, shells
from gofer.rattish.diagnostics import RattishDiagnostic, RattishError, SourcePosition, SourceSpan
from gofer.rattish.project_paths import path_kind, project_path
from gofer.rattish.prompt_templates import PromptTemplateError, validate_prompt_template
from gofer.rattish.provider_fallback import provider_choices, sign_in
from gofer.rattish.provider_runtime import (
    default_provider_subscriptions,
    runtime_subscription_id,
)
from gofer.rattish.runtime import (
    DEFAULT_NODE_HANDLERS,
    NodeHandlerRegistry,
    _workflow_interface_fingerprint,
)
from gofer.rattish.workflow_secrets import (
    WorkflowSecretError,
    how_to_add,
    not_found_message,
    workflow_secret_value,
)
from gofer.subscriptions.base import Subscription
from gofer.utils.paths import get_data_dir


@dataclass(frozen=True, slots=True)
class PreflightFailure:
    code: str
    message: str
    details: dict[str, Any]
    # How to fix it. Without one, the check's contract description is shown.
    suggestions: tuple[str, ...] = ()
    # Overrides the check's declared severity, such as "info" for a check that was skipped.
    severity: Literal["error", "warning", "info"] | None = None


@dataclass(frozen=True, slots=True)
class PreflightContext:
    project_root: Path
    data_dir: Path
    subscriptions: Mapping[str, Subscription]
    handlers: NodeHandlerRegistry
    registry: PreflightRegistry
    # A triggered run refuses some things a run you start yourself allows.
    triggered: bool = False


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


def _secret_problem(name: str, data_dir: Path) -> tuple[str, str, str | None] | None:
    """Why ``secret[name]`` can't resolve, or None.

    ("missing" | "unreadable", message, the source that failed: "keychain",
    "secrets folder" or None).
    """
    try:
        if workflow_secret_value(name, data_dir) is not None:
            return None
    except WorkflowSecretError as exc:
        return "unreadable", str(exc), exc.source
    return "missing", not_found_message(name), None


def _trigger_file_input_diagnostics(ir: Mapping[str, Any]) -> list[RattishDiagnostic]:
    """A trigger has nobody to choose a file, so a required file input stops its run."""
    from gofer.rattish.file_inputs import file_input_specs

    required = {item["name"] for item in ir["workflow"]["inputs"] if item["required"]}
    names = [name for name in file_input_specs(ir) if name in required]
    if not names:
        return []
    listed = ", ".join(repr(name) for name in names)
    one = len(names) == 1
    return [
        RattishDiagnostic(
            code="RATTISH_PREFLIGHT_FILE_INPUT_UNFILLED",
            severity="error",
            phase="preflight",
            message=(
                f"A trigger can't fill the required file {'input' if one else 'inputs'} "
                f"{listed}: nobody is there to choose a file."
            ),
            file=ir["source_map"]["workflow"]["file"],
            span=_source_span(ir["source_map"]["workflow"]),
            details={"inputs": names},
            suggestions=(
                "Make the input optional (required: false), or run this workflow yourself "
                "and choose the file.",
            ),
        )
    ]


def run_preflight(
    ir: Mapping[str, Any],
    *,
    registry: PreflightRegistry | None = None,
    data_dir: Path | None = None,
    subscriptions: Mapping[str, Subscription] | None = None,
    handlers: NodeHandlerRegistry | None = None,
    triggered: bool = False,
    worktree: bool = True,
) -> PreflightResult:
    """Run every preflight check frozen into IR without executing a node.

    ``triggered`` checks the workflow as a trigger would start it. ``worktree=False``
    skips the ``workspace: worktree`` checks for a run that stays in the project folder,
    such as a test set's.
    """
    checks = registry or DEFAULT_PREFLIGHT_CHECKS
    runtime_handlers = handlers or DEFAULT_NODE_HANDLERS
    context = PreflightContext(
        Path(ir["source"]["project_root"]),
        data_dir or get_data_dir(),
        subscriptions if subscriptions is not None else default_provider_subscriptions(),
        runtime_handlers,
        checks,
        triggered,
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
    if worktree:
        diagnostics.extend(_worktree_diagnostics(ir, context))
    if triggered:
        diagnostics.extend(_trigger_file_input_diagnostics(ir))
    for node in ir["nodes"]:
        for binding in node["bindings"]:
            source = binding["source"]
            for reference in _binding_references(source):
                if reference["root"] != "secret":
                    continue
                secret_name = str(reference["symbol"])
                problem = _secret_problem(secret_name, context.data_dir)
                # A default covers a secret nobody set, not a saved one the keychain won't give.
                if problem is None or (binding["default"]["present"] and problem[0] == "missing"):
                    continue
                diagnostics.append(
                    RattishDiagnostic(
                        code="RATTISH_PREFLIGHT_SECRET_UNAVAILABLE",
                        severity="error",
                        phase="preflight",
                        message=problem[1],
                        file=node["source_span"]["file"],
                        span=_source_span(node["source_span"]),
                        details={
                            "node": node["id"],
                            "secret": secret_name,
                            **({"source": problem[2]} if problem[2] else {}),
                        },
                        suggestions=(how_to_add(secret_name),),
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
                    severity=failure.severity or declaration["severity"],
                    phase="preflight",
                    message=failure.message,
                    file=node["source_span"]["file"],
                    span=_source_span(node["source_span"]),
                    details={"node": node["id"], "check": declaration["id"], **failure.details},
                    suggestions=failure.suggestions or (declaration["description"],),
                )
            )
    return PreflightResult(tuple(diagnostics))


def _worktree_diagnostics(
    ir: Mapping[str, Any], context: PreflightContext
) -> list[RattishDiagnostic]:
    """``workspace: worktree`` needs a git repository, and works best with a setup file."""
    from gofer.rattish import run_worktrees
    from gofer.ui.worktree_setup import CONFIG_PATH

    if not run_worktrees.uses_worktree(ir):
        return []
    project_root = context.project_root
    where = ir["source_map"]["workflow"]

    def diagnostic(
        code: str, severity: Literal["error", "warning"], message: str, suggestion: str
    ) -> RattishDiagnostic:
        return RattishDiagnostic(
            code=code,
            severity=severity,
            phase="preflight",
            message=message,
            file=where["file"],
            span=_source_span(where),
            details={"workflow_id": ir["workflow"]["id"], "field": "workspace"},
            suggestions=(suggestion,),
        )

    try:
        # Only checks the repository: nothing is created.
        run_worktrees.plan(context.data_dir, project_root, ir["workflow"]["id"], "preflight")
    except run_worktrees.RunWorktreeError as exc:
        return [
            diagnostic(
                "RATTISH_PREFLIGHT_WORKTREE_UNAVAILABLE",
                "error",
                str(exc),
                "Commit the project to git, or set workspace: project.",
            )
        ]
    if (project_root / CONFIG_PATH).is_file():
        return []
    return [
        diagnostic(
            "RATTISH_PREFLIGHT_WORKTREE_SETUP_MISSING",
            "warning",
            f"Each run gets a fresh worktree with only the files git tracks, and this project "
            f"has no {CONFIG_PATH} to install packages or link folders into it. Steps that "
            "need dependencies, such as a test command, may fail there.",
            f"Commit a {CONFIG_PATH} that prepares a worktree, then approve it. See "
            "https://raticode.dev/docs/studio/worktree-setup",
        )
    ]


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


def _may_be_created_upstream(node: Mapping[str, Any]) -> bool:
    """Whether a node waits on `needs` that may create its input earlier in the run."""
    return bool(node["readiness"]["needs"])


def _provider_installed(provider_id: str, context: PreflightContext) -> tuple[bool, str]:
    subscription_id: str
    try:
        subscription_id = runtime_subscription_id(provider_id)
    except ValueError:
        subscription_id = provider_id
    subscription = context.subscriptions.get(subscription_id)
    return subscription is not None and subscription.is_available(), subscription_id


def _agent_provider_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """The step can run when its provider, or any of its fallback providers, is installed.

    A missing primary provider with an installed fallback is a fallback the run will
    take, which ``fallback-provider-available`` reports as a warning.
    """
    choices = provider_choices(node)
    provider_id = choices[0]["provider_id"]
    installed, subscription_id = _provider_installed(provider_id, context)
    if installed or any(
        _provider_installed(choice["provider_id"], context)[0] for choice in choices[1:]
    ):
        return None
    if len(choices) > 1:
        names = ", ".join(repr(choice["provider_id"]) for choice in choices)
        return PreflightFailure(
            "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE",
            f"None of the step's providers ({names}) is installed or available.",
            {
                "provider": provider_id,
                "subscription": subscription_id,
                "providers": [choice["provider_id"] for choice in choices],
            },
        )
    return PreflightFailure(
        "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE",
        f"Provider runtime {provider_id!r} is not installed or available.",
        {"provider": provider_id, "subscription": subscription_id},
    )


def _agent_fallback_provider_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """Warn about uninstalled providers in a step that has a fallback list."""
    choices = provider_choices(node)
    if len(choices) < 2:
        return None
    missing = [
        index
        for index, choice in enumerate(choices)
        if not _provider_installed(choice["provider_id"], context)[0]
    ]
    if not missing or len(missing) == len(choices):
        # All missing is the provider-available error, not a warning on top of it.
        return None
    names = ", ".join(repr(choices[index]["provider_id"]) for index in missing)
    return PreflightFailure(
        "RATTISH_PREFLIGHT_FALLBACK_PROVIDER_UNAVAILABLE",
        (
            f"Provider {names} is not installed or available, so the step will skip it."
            if len(missing) == 1
            else f"Providers {names} are not installed or available, so the step will skip them."
        ),
        {
            "providers": [choices[index]["provider_id"] for index in missing],
            "fallback": [index - 1 for index in missing if index > 0],
            "primary_missing": 0 in missing,
        },
    )


def _agent_profile_compatible(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """Every provider in the step's chain must have a usable profile and credentials.

    The first error stops the check. A warning, such as a deprecated model, an account
    that needs sign-in while the chain has another entry, or a fallback entry that is
    another account of an earlier entry's provider, is reported only when no entry has an
    error. When every entry is an account that needs sign-in, the first one is an error.
    """
    warnings: list[PreflightFailure] = []
    choices = provider_choices(node)
    signed_out = 0
    for index, choice in enumerate(choices):
        failure = _choice_profile_problem(choice, context, only_choice=len(choices) == 1)
        if failure is None:
            continue
        if failure.code == "RATTISH_PREFLIGHT_ACCOUNT_NEEDS_SIGN_IN":
            signed_out += 1
        if index:
            failure = PreflightFailure(
                failure.code,
                f"Fallback entry {index} ({choice['provider_id']}): {failure.message}",
                {**failure.details, "fallback": index - 1},
                failure.suggestions,
                failure.severity,
            )
        if failure.severity == "warning":
            warnings.append(failure)
            continue
        return failure
    if len(choices) > 1 and signed_out == len(choices):
        # No entry can run: every one is an account that needs sign-in.
        return PreflightFailure(
            warnings[0].code,
            " ".join(item.message for item in warnings),
            {**warnings[0].details, "profiles": [item.details["profile"] for item in warnings]},
            tuple(dict.fromkeys(text for item in warnings for text in item.suggestions)),
            "error",
        )
    account_warning = _fallback_account_warning(choices, context)
    if account_warning is not None:
        warnings.append(account_warning)
    if not warnings:
        return None
    if len(warnings) == 1:
        return warnings[0]
    return PreflightFailure(
        warnings[0].code,
        " ".join(item.message for item in warnings),
        {**warnings[0].details, "models": [item.details.get("model") for item in warnings]},
        tuple(dict.fromkeys(text for item in warnings for text in item.suggestions)),
        "warning",
    )


def _fallback_account_warning(
    choices: list[dict[str, Any]], context: PreflightContext
) -> PreflightFailure | None:
    """Warn about fallback entries the step skips when their provider hits a usage limit.

    Such an entry is another plan sign-in (an account or the default sign-in) of a provider
    an earlier entry already uses, so only sign-in and outage errors reach it.
    """
    try:
        profiles = load_provider_profiles(context.data_dir)
    except (OSError, ValueError):
        profiles = {}
    entries: list[int] = []
    for index in range(1, len(choices)):
        identity, api_key = sign_in(choices[index], profiles)
        if not api_key and any(
            earlier["provider_id"] == choices[index]["provider_id"]
            and sign_in(earlier, profiles)[0] != identity
            for earlier in choices[:index]
        ):
            entries.append(index)
    if not entries:
        return None
    names = [
        f"Fallback entry {index} ({choices[index]['provider_id']}"
        + (f" profile {choices[index]['profile']}" if choices[index].get("profile") else "")
        + ")"
        for index in entries
    ]
    return PreflightFailure(
        "RATTISH_PREFLIGHT_FALLBACK_SAME_PROVIDER_ACCOUNT",
        (
            f"{' and '.join(names)} "
            + ("is another account" if len(entries) == 1 else "are other accounts")
            + " of a provider the step already uses. Raticode doesn't move work to another "
            "account of the same provider at a usage limit, so "
            + ("it" if len(entries) == 1 else "they")
            + " will only be used for sign-in or outage errors."
        ),
        {
            "fallback": [index - 1 for index in entries],
            "providers": [choices[index]["provider_id"] for index in entries],
            "profiles": [choices[index].get("profile") for index in entries],
        },
        (
            "To keep the step running at a usage limit, add another provider or an "
            "API-key profile to the fallback list.",
        ),
        "warning",
    )


def account_sign_in_state(name: str, data_dir: Path) -> str | None:
    """An account profile's sign-in state: its last check if recent, else a quick check.

    None when the profile isn't an account or the state isn't known in time. Tests
    replace this so preflight never starts a real provider CLI.
    """
    from gofer.core.provider_accounts import is_account, quick_status

    try:
        profile = load_provider_profiles(data_dir).get(name)
    except (OSError, ValueError):
        return None
    if profile is None or not is_account(profile):
        return None
    status = quick_status(profile, data_dir=data_dir)
    return str(status["state"]) if status and status.get("state") else None


def _account_sign_in_problem(
    name: str, resolution: Mapping[str, Any], context: PreflightContext, only_choice: bool
) -> PreflightFailure | None:
    """An account that needs sign-in: an error for the step's only choice, else a warning."""
    from gofer.core.provider_accounts import NEEDS_SIGN_IN, account_adapter

    if account_sign_in_state(name, context.data_dir) != NEEDS_SIGN_IN:
        # Signed in, or a state preflight can't tell (no CLI, a slow check): it passes.
        return None
    subscription = runtime_subscription_id(resolution["provider_id"])
    adapter = account_adapter(subscription)
    label = adapter.label if adapter else str(resolution["provider_id"])
    return PreflightFailure(
        "RATTISH_PREFLIGHT_ACCOUNT_NEEDS_SIGN_IN",
        f"{label} account {name!r} needs sign-in, so the step can't use it.",
        {"provider": resolution["provider_id"], "profile": name, "state": NEEDS_SIGN_IN},
        (f"Sign in {name!r} under Settings > Providers > {label}, then run preflight again.",),
        "error" if only_choice else "warning",
    )


def _choice_profile_problem(
    resolution: Mapping[str, Any], context: PreflightContext, *, only_choice: bool = True
) -> PreflightFailure | None:
    """What stops one choice's profile from running, or None.

    ``only_choice`` says the step has no other entry to move on to, which makes an
    account that needs sign-in an error rather than a warning.
    """
    profile = resolution["profile"]
    try:
        subscription_id = runtime_subscription_id(resolution["provider_id"])
        settings = resolve_provider_settings(
            agent_subscription=subscription_id,
            profile_name=profile,
            operation_model=resolution["model"],
            operation_effort=resolution["effort"],
            data_dir=context.data_dir,
        )
        validate_provider_settings(settings)
        if subscription_id in {"antigravity", "grok"}:
            from gofer.subscriptions.acp_providers import require_acp_permissions

            require_acp_permissions(subscription_id, settings.approval_mode)
    except ValueError as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_PROFILE_UNAVAILABLE",
            str(exc),
            {"provider": resolution["provider_id"], "profile": profile},
        )
    missing_secrets = unresolved_provider_secret_refs(settings)
    if missing_secrets:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_SECRET_UNAVAILABLE",
            "Provider credentials are unavailable: " + ", ".join(missing_secrets),
            {"secrets": missing_secrets},
        )
    if settings.account_id and profile:
        signed_out = _account_sign_in_problem(str(profile), resolution, context, only_choice)
        if signed_out is not None:
            return signed_out
    if settings.local:
        from gofer.core.local_models import preflight_problem

        problem = preflight_problem(settings)
        if problem is not None:
            return PreflightFailure(
                "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE",
                problem,
                {
                    "provider": resolution["provider_id"],
                    "profile": profile,
                    "model": settings.model,
                    "api_base_url": settings.api_base_url,
                },
            )
    elif settings.subscription in {"anthropic_api", "openai_api"}:
        from gofer.core.model_lifecycle import check_model

        model_problem = check_model(settings, context.data_dir)
        if model_problem is not None:
            return PreflightFailure(
                model_problem.code,
                model_problem.message,
                {
                    "provider": resolution["provider_id"],
                    "profile": profile,
                    **{
                        name: value
                        for name, value in model_problem.to_json().items()
                        if name in {"model", "state", "replacement", "retires"}
                    },
                },
                (model_problem.suggestion,),
                model_problem.severity,
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
    if not path.exists() and _may_be_created_upstream(node):
        # A required upstream node may write the prompt. The agent reports it when it runs.
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
    if not path.exists() and _may_be_created_upstream(node):
        # A required upstream node may create the folder. The agent reports it when it runs.
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        f"Agent working directory {path} is unavailable or inaccessible.",
        {"path": str(path)},
    )


def _sandboxed_choices(
    node: Mapping[str, Any], context: PreflightContext
) -> list[tuple[Mapping[str, Any], str, Any]]:
    """The step's provider choices that run in Raticode's sandbox: (choice, id, settings)."""
    configuration = node["configuration"]
    found: list[tuple[Mapping[str, Any], str, Any]] = []
    for choice in provider_choices(node):
        subscription_id, settings = agent_sandbox.choice_settings(choice, context.data_dir)
        if subscription_id is None or not agent_sandbox.builtin_adapter(
            context.subscriptions.get(subscription_id), subscription_id
        ):
            continue
        if agent_sandbox.unsandboxed_reason(subscription_id, configuration, settings) is None:
            found.append((choice, subscription_id, settings))
    return found


def _agent_sandbox_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """Each sandboxed provider in the step's chain must be able to start in its sandbox."""
    configuration = node["configuration"]
    for index, choice in enumerate(provider_choices(node)):
        subscription_id, settings = agent_sandbox.choice_settings(choice, context.data_dir)
        if subscription_id is None or not agent_sandbox.builtin_adapter(
            context.subscriptions.get(subscription_id), subscription_id
        ):
            continue
        problem = agent_sandbox.sandbox_problem(
            subscription_id, configuration, settings, triggered=context.triggered
        )
        if problem is None:
            continue
        prefix = f"Fallback entry {index} ({choice['provider_id']}): " if index else ""
        return PreflightFailure(
            problem.code,
            prefix + problem.message,
            {
                "provider": choice["provider_id"],
                **({"fallback": index - 1} if index else {}),
            },
            (problem.suggestion,) if problem.suggestion else (),
        )
    return None


def _agent_network_declared(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """A sandboxed step whose settings show it needs hosts must list them in network."""
    configuration = node["configuration"]
    choices = _sandboxed_choices(node, context)
    if not choices:
        return None
    prompt_text: str | None = configuration["prompt"]
    if configuration["prompt_path"] is not None and not _configuration_is_dynamic(
        node, "prompt_path"
    ):
        path = project_path(context.project_root, configuration["prompt_path"])
        try:
            with path.open("rb") as handle:
                prompt_text = handle.read(agent_sandbox.MAX_PROMPT_BYTES).decode("utf-8", "replace")
        except OSError:
            prompt_text = None
    need = agent_sandbox.network_need(configuration, prompt_text)
    if need is None:
        return None
    network = agent_sandbox.step_network(configuration)
    message, suggestions = agent_sandbox.network_fix(
        node["id"],
        need,
        network,
        codex=any(subscription_id == "codex" for _, subscription_id, _ in choices),
    )
    return PreflightFailure(
        "RATTISH_PREFLIGHT_NETWORK_UNDECLARED", message, need.to_details(), suggestions
    )


def _agent_runs_unsandboxed(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """Warn when any provider in the step's chain runs its shell unsandboxed."""
    found = agent_sandbox.node_unsandboxed(node, context.data_dir)
    if not found:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RUNS_UNSANDBOXED",
        agent_sandbox.unsandboxed_message(found),
        {"unsandboxed": [{"provider": i["provider"], "reason": i["reason"]} for i in found]},
        (
            "Its shell can change any file you can, read your credentials and reach any "
            "host. Use claude-code or codex with the sandbox on for steps triggers start.",
        ),
    )


def _agent_extra_paths_allowed(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """Refuse extra paths that are, hold or sit inside a credential folder."""
    paths: list[Path] = []
    for authored in node["configuration"].get("extra_paths") or []:
        if not isinstance(authored, str):
            continue
        try:
            paths.append(project_path(context.project_root, authored))
        except ValueError:
            continue
    refused = agent_sandbox.credential_extra_paths(paths, context.data_dir)
    if not refused:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_CREDENTIAL_PATH",
        f"Agent {node['id']!r} can't be given a credential folder: " + "; ".join(refused) + ".",
        {"paths": refused},
        ("Remove it from the step's extra-paths.",),
    )


def _agent_sandbox_widened(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """Warn when the step's own settings let its shell reach beyond its project."""
    found = agent_sandbox.node_widened(node, context.project_root, context.data_dir)
    if not found:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_SANDBOX_WIDENED",
        f"Agent {node['id']!r}: " + agent_sandbox.widened_message(found),
        {"widened": [{key: v for key, v in item.items() if key != "node"} for item in found]},
        (
            "The workflow file itself sets this, so check it before a trigger runs it with "
            "nobody watching. Keep extra-paths inside the project and list the hosts in "
            "`network` instead of `any` where you can.",
        ),
    )


def _local_vector_source_readable(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if _configuration_is_dynamic(node, "source_path"):
        return None
    path = project_path(context.project_root, node["configuration"]["source_path"])
    if path.exists() and os.access(path, os.R_OK):
        return None
    if not path.exists() and _may_be_created_upstream(node):
        # A required upstream node may create the source. The node reports it when it runs.
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
    if not path.exists() and _may_be_created_upstream(node):
        # An upstream local-vectorize may build the index. The search reports it when it runs.
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


def _rem_ready(node: Mapping[str, Any], context: PreflightContext) -> PreflightFailure | None:
    from gofer.ui.rem_workflow import RemWorkflowError, existing_store

    _ = node
    try:
        store = existing_store(context.data_dir)
    except RemWorkflowError as exc:
        # A damaged database: the message says to restart Raticode to restore it.
        return PreflightFailure(
            "RATTISH_PREFLIGHT_REM_UNAVAILABLE", exc.message, {"path": str(context.data_dir)}
        )
    except (OSError, sqlite3.Error) as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_REM_UNAVAILABLE",
            f"Rem's task store can't be opened: {exc}",
            {"path": str(context.data_dir)},
        )
    if store is None:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_REM_UNAVAILABLE",
            "Rem hasn't been set up in this Raticode data folder. Open Rem in Raticode first.",
            {"path": str(context.data_dir)},
        )
    return None


def _rem_task_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    from gofer.ui.rem_workflow import RemWorkflowError, existing_store, task_record

    task_id = str(node["configuration"]["task_id"])
    try:
        store = existing_store(context.data_dir)
        if store is None:
            raise RemWorkflowError("rem", "", "")
        task_record(store, task_id)
    except RemWorkflowError as exc:
        if exc.code == "RATTISH_REM_UNAVAILABLE":
            return PreflightFailure(
                "RATTISH_PREFLIGHT_REM_UNAVAILABLE", exc.message, {"path": str(context.data_dir)}
            )
        return PreflightFailure(
            "RATTISH_PREFLIGHT_REM_TASK_UNAVAILABLE",
            f"Rem task {task_id!r} doesn't exist or was deleted. Choose another task.",
            {"task_id": task_id},
        )
    except (OSError, sqlite3.Error) as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_REM_UNAVAILABLE",
            f"Rem's task store can't be opened: {exc}",
            {"task_id": task_id},
        )
    return None


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
    _ = context
    shell = shells.configured_shell(node["configuration"])
    if shell != "auto":
        return _shell_failure(shell)
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
    if not path.exists() and _may_be_created_upstream(node):
        # A required upstream node may create the folder. The command reports it when it runs.
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
    if not path.exists() and _may_be_created_upstream(node):
        # A required upstream node may create the file. The read reports it when it runs.
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
    if not path.exists() and _may_be_created_upstream(node):
        # A required upstream node may create the folder. The node reports it when it runs.
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
    if not path.exists() and _may_be_created_upstream(node):
        # A required upstream node may write the template. The node reports it when it runs.
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
            {"url": exc.url},
        )
    return None


def _render_browser_missing(node: Mapping[str, Any], mode: str) -> PreflightFailure | None:
    if str(node["configuration"].get("render", "never")).lower() != mode:
        return None
    from gofer.rattish.web_render import find_browser

    if find_browser() is not None:
        return None
    if mode == "always":
        message = (
            "render: always needs Chrome, Chromium or Edge, and none is installed on this "
            "computer. Install one, or use render: auto to read the page without it."
        )
    else:
        message = (
            "No Chrome, Chromium or Edge is installed, so render: auto reads the page "
            "without running JavaScript."
        )
    return PreflightFailure("RATTISH_PREFLIGHT_NO_BROWSER", message, {"render": mode})


def _render_browser_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    return _render_browser_missing(node, "always")


def _render_browser_optional(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    _ = context
    return _render_browser_missing(node, "auto")


def _mcp_server(node: Mapping[str, Any], context: PreflightContext) -> Any:
    """The step's server from Rem's list, or the ``McpToolError`` saying why it isn't there."""
    from gofer.rattish import mcp_tool

    try:
        return mcp_tool.library_server(context.data_dir, str(node["configuration"]["server"]))
    except mcp_tool.McpToolError as exc:
        return exc


def _mcp_server_registered(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    from gofer.rattish import mcp_tool

    server = _mcp_server(node, context)
    if not isinstance(server, mcp_tool.McpToolError):
        return None
    name = str(node["configuration"]["server"])
    return PreflightFailure(
        "RATTISH_PREFLIGHT_MCP_SERVER_UNKNOWN",
        server.message,
        server.details,
        (
            f"Add it with `Raticode rem mcp add {name} --url URL` or "
            f"`Raticode rem mcp add {name} -- COMMAND ARGS`, or pick a server from the list.",
        ),
    )


def _mcp_sandbox_available(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    from gofer.rattish import mcp_tool

    server = _mcp_server(node, context)
    if isinstance(server, mcp_tool.McpToolError):
        return None
    settings = mcp_tool.step_settings(node["configuration"], context.project_root)
    problem = mcp_tool.sandbox_problem(server, settings)
    if problem is None:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE",
        problem,
        {"server": server["name"]},
        ("Fix that, or add `sandbox: off` to the step to run its server unsandboxed.",),
    )


def _mcp_tool_arguments_valid(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """List the server's tools, then check the tool name and the arguments' shape.

    This starts the server in its sandbox, so answers are kept for a few minutes per
    server definition and sandbox. A local server that would run unsandboxed (sandbox: off,
    or native Windows) is never started here: opening a workflow in the Studio runs
    preflight, and that must not run a server outside the sandbox. Its run still checks
    the tool and the arguments.
    """
    from gofer.rattish import mcp_tool

    server = _mcp_server(node, context)
    if isinstance(server, mcp_tool.McpToolError):
        return None
    configuration = node["configuration"]
    settings = mcp_tool.step_settings(configuration, context.project_root)
    reason = mcp_tool.unsandboxed_reason(server, settings)
    if reason is not None:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_MCP_TOOL_CHECK_SKIPPED",
            "Tool check skipped: this server runs unsandboxed. Check it with "
            f"Raticode rem mcp tools {server['name']}.",
            {"server": server["name"], "reason": reason},
            ("The run still checks the tool and its arguments when it starts the server.",),
            severity="info",
        )
    if mcp_tool.sandbox_problem(server, settings) is not None:
        return None
    try:
        tools = mcp_tool.list_tools(server, settings, data_dir=context.data_dir, use_cache=True)
    except mcp_tool.McpToolError as exc:
        return PreflightFailure(
            "RATTISH_PREFLIGHT_MCP_SERVER_UNAVAILABLE",
            f"Couldn't list the tools of MCP server {server['name']!r}: {exc.message}",
            {"server": server["name"], "code": exc.code},
            (
                "Check that the server starts with `Raticode rem mcp tools "
                f"{server['name']}`. A server started with npx or uvx needs its package "
                "host in network (such as registry.npmjs.org or pypi.org) unless it is "
                "installed already.",
            ),
        )
    tool_name = str(configuration["tool"])
    if _configuration_is_dynamic(node, "tool"):
        return None
    try:
        tool = mcp_tool.find_tool(tools, tool_name, server["name"])
    except mcp_tool.McpToolError as exc:
        return PreflightFailure("RATTISH_PREFLIGHT_MCP_TOOL_UNKNOWN", exc.message, exc.details)
    if any(binding["name"] == "arguments" for binding in node["bindings"]):
        return None
    arguments = configuration["arguments"]
    if isinstance(arguments, str):
        if "{{" in arguments or "}}" in arguments:
            return None
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            return PreflightFailure(
                "RATTISH_PREFLIGHT_MCP_ARGUMENTS_INVALID",
                f"The arguments aren't valid JSON: {exc.msg}.",
                {"server": server["name"], "tool": tool_name},
            )
    problems = mcp_tool.argument_problems(tool["inputSchema"], arguments, templates=True)
    if not problems:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_MCP_ARGUMENTS_INVALID",
        f"The arguments don't fit tool {tool_name!r}: " + "; ".join(problems[:5]),
        {
            "server": server["name"],
            "tool": tool_name,
            "problems": problems[:20],
            "input_schema": tool["inputSchema"],
        },
        (f"Run `Raticode rem mcp tools {server['name']}` to see each tool's arguments.",),
    )


def _mcp_runs_unsandboxed(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    from gofer.rattish import mcp_tool

    found = mcp_tool.node_unsandboxed(node, context.data_dir)
    if not found:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_RUNS_UNSANDBOXED",
        agent_sandbox.unsandboxed_message(found),
        {"unsandboxed": [{"provider": i["provider"], "reason": i["reason"]} for i in found]},
        (
            "The server can change any file you can, read your credentials and reach any "
            "host. Keep the sandbox on for steps triggers start.",
        ),
    )


def _mcp_package_pinned(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    from gofer.rattish import mcp_packages, mcp_tool

    server = _mcp_server(node, context)
    if isinstance(server, mcp_tool.McpToolError):
        return None
    packages = mcp_packages.unpinned_packages(server)
    if not packages:
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_MCP_PACKAGE_UNPINNED",
        mcp_packages.unpinned_message(str(server["name"]), packages),
        {"server": server["name"], "packages": packages},
        (
            "Pin an exact version in Rem's MCP server list, such as "
            "`Raticode rem mcp add NAME -- npx -y pkg@1.2.3` or `uvx pkg@1.2.3`, or install "
            f"the server and run it directly. {mcp_packages.TOP_LEVEL_ONLY}",
        ),
    )


def _mcp_cache_readable(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    """Note when tool calls here get an empty package cache and can't reach the package host.

    Such a call has to download the server's package, and the step's network blocks that.
    """
    from gofer.rattish import mcp_packages, mcp_tool

    server = _mcp_server(node, context)
    if isinstance(server, mcp_tool.McpToolError) or not mcp_tool.is_local(server):
        return None
    settings = mcp_tool.step_settings(node["configuration"], context.project_root)
    if mcp_tool.unsandboxed_reason(server, settings) is not None:
        return None
    hosts = [
        host
        for host in mcp_packages.package_hosts(server)
        if not agent_sandbox.host_allowed(host, settings.network)
    ]
    if not hosts:
        return None
    problem = mcp_tool.call_cache_problem(server, context.data_dir)
    if problem is None or mcp_tool.sandbox_problem(server, settings) is not None:
        return None
    listed = " and ".join(hosts)
    return PreflightFailure(
        "RATTISH_PREFLIGHT_MCP_CACHE_UNREADABLE",
        f"Tool calls on this computer can't read MCP server {server['name']!r}'s package "
        f"cache: {problem}. Each call downloads the package, and the step's network "
        f"doesn't list {listed}.",
        {"server": server["name"], "hosts": hosts, "reason": problem},
        (
            f"Add {listed} to the step's network, install bubblewrap 0.11 or later, or "
            "install the server and run the installed program.",
        ),
        severity="info",
    )


def _mcp_env_set(node: Mapping[str, Any], context: PreflightContext) -> PreflightFailure | None:
    """Warn when a variable the server's library entry names isn't set here."""
    from gofer.rattish import mcp_tool

    server = _mcp_server(node, context)
    if isinstance(server, mcp_tool.McpToolError):
        return None
    missing = mcp_tool.missing_env(server)
    if not missing:
        return None
    listed = ", ".join(missing)
    return PreflightFailure(
        "RATTISH_PREFLIGHT_MCP_ENV_MISSING",
        f"MCP server {server['name']!r} names environment "
        f"variable{'s' if len(missing) > 1 else ''} {listed}, which "
        f"{'are' if len(missing) > 1 else 'is'} not set where Raticode runs.",
        {"server": server["name"], "missing": missing},
        (
            "Set it in the environment Raticode starts from, or remove the name from the "
            "server in Thread tools. Until then the server starts without it.",
        ),
    )


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


def _notification_phone_paired(
    node: Mapping[str, Any], context: PreflightContext
) -> PreflightFailure | None:
    if any(binding["name"] == "channel" for binding in node["bindings"]):
        return None
    if node["configuration"]["channel"] != "phone":
        return None
    from gofer.core.phone_alerts import phone_paired

    if phone_paired(context.data_dir):
        return None
    return PreflightFailure(
        "RATTISH_PREFLIGHT_NOTIFICATION_NO_PHONE",
        "No phone is paired with this computer. The run still stores the notice, "
        "but no phone will show it.",
        {"channel": "phone"},
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
    shell = shells.configured_shell(node["configuration"])
    if shell != "auto":
        return _shell_failure(shell)
    return _interpreter_available("bash", node, context)


def _shell_failure(shell: str) -> PreflightFailure | None:
    """A step that asks for bash or powershell: is it installed here?"""
    missing = shells.shell_missing(shell)
    if missing is None:
        return None
    return PreflightFailure("RATTISH_PREFLIGHT_RESOURCE_MISSING", str(missing), missing.details)


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
    if not path.exists() and _may_be_created_upstream(node):
        # A required upstream node may write the script. The node reports it when it runs.
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
    if path_kind(source) == "missing" and _may_be_created_upstream(node):
        # A required upstream node may create the source. The node reports it when it runs.
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
    if (
        not parent.exists()
        and not configuration.get("create_dirs", False)
        and not _may_be_created_upstream(node)
    ):
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
    if kind == "missing" and not configuration["missing_ok"] and not _may_be_created_upstream(node):
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
        triggered=context.triggered,
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


def _jev_configured(node: Mapping[str, Any], context: PreflightContext) -> PreflightFailure | None:
    """A saved, turned-on Jev key; checked without reading the keychain or calling Jev."""
    _ = node
    from gofer.classifiers.jev_settings import jev_enabled, jev_key_configured

    if not jev_key_configured(context.data_dir):
        return PreflightFailure(
            "RATTISH_PREFLIGHT_JEV_NOT_CONFIGURED",
            "No Jev key is saved. Add one in Settings > Providers > Jev, or run "
            "`Raticode provider jev set-key`.",
            {},
        )
    if not jev_enabled(context.data_dir):
        return PreflightFailure(
            "RATTISH_PREFLIGHT_JEV_DISABLED",
            "Jev is turned off in Settings > Providers > Jev.",
            {},
        )
    return None


def _source_span(value: Mapping[str, Any]) -> SourceSpan:
    return SourceSpan(
        SourcePosition(**value["start"]),
        SourcePosition(**value["end"]),
    )


DEFAULT_PREFLIGHT_CHECKS = PreflightRegistry(
    {
        ("raticode.agent", "provider-available"): _agent_provider_available,
        ("raticode.agent", "profile-compatible"): _agent_profile_compatible,
        ("raticode.agent", "fallback-provider-available"): _agent_fallback_provider_available,
        ("raticode.agent", "prompt-path-readable"): _agent_prompt_path_readable,
        ("raticode.agent", "working-dir-available"): _agent_working_dir_available,
        ("raticode.agent", "sandbox-available"): _agent_sandbox_available,
        ("raticode.agent", "network-declared"): _agent_network_declared,
        ("raticode.agent", "runs-unsandboxed"): _agent_runs_unsandboxed,
        ("raticode.agent", "extra-paths-allowed"): _agent_extra_paths_allowed,
        ("raticode.agent", "sandbox-widened"): _agent_sandbox_widened,
        ("raticode.approval_gate", "approval-store-ready"): _approval_store_ready,
        ("raticode.bash_command", "shell-available"): _bash_shell_available,
        ("raticode.bash_command", "working-dir-available"): _bash_working_dir_available,
        ("raticode.python_script", "interpreter-available"): _python_interpreter_available,
        ("raticode.python_script", "script-readable"): _script_readable,
        ("raticode.read_file", "path-readable"): _read_path_readable,
        ("raticode.rem_prompt", "rem-ready"): _rem_ready,
        ("raticode.rem_task", "task-available"): _rem_task_available,
        ("raticode.read_file", "encoding-supported"): _read_encoding_supported,
        ("raticode.file", "path-readable"): _read_path_readable,
        ("raticode.folder", "path-readable"): _folder_path_readable,
        ("raticode.http_request", "network-target-valid"): _http_network_target_valid,
        ("raticode.read_web_page", "network-target-valid"): _http_network_target_valid,
        ("raticode.read_web_page", "render-browser-available"): _render_browser_available,
        ("raticode.read_web_page", "render-browser-optional"): _render_browser_optional,
        ("raticode.mcp_tool", "server-registered"): _mcp_server_registered,
        ("raticode.mcp_tool", "sandbox-available"): _mcp_sandbox_available,
        ("raticode.mcp_tool", "tool-arguments-valid"): _mcp_tool_arguments_valid,
        ("raticode.mcp_tool", "runs-unsandboxed"): _mcp_runs_unsandboxed,
        ("raticode.mcp_tool", "package-pinned"): _mcp_package_pinned,
        ("raticode.mcp_tool", "env-set"): _mcp_env_set,
        ("raticode.mcp_tool", "cache-readable"): _mcp_cache_readable,
        ("raticode.jev", "jev-configured"): _jev_configured,
        ("raticode.common_llm_task", "provider-available"): _agent_provider_available,
        ("raticode.common_llm_task", "profile-compatible"): _agent_profile_compatible,
        (
            "raticode.common_llm_task",
            "fallback-provider-available",
        ): _agent_fallback_provider_available,
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
        ("raticode.notification", "phone-paired"): _notification_phone_paired,
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
