from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast, overload

import anyio
from pydantic import BaseModel

from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.core.health import run_health_checks
from gofer.core.provider_profiles import (
    load_provider_profiles,
    provider_profile_from_ui_payload,
    provider_profile_ui_payload,
    save_provider_profiles,
    validate_provider_profile_name,
)
from gofer.core.resources import (
    DEFAULT_RESOURCE_LIMITS,
    ResourceLimits,
)
from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
from gofer.rattish.bundles import (
    RattishBundleError,
    duplicate_rattish_workspace,
    export_rattish_bundle,
    import_rattish_bundle,
    preview_rattish_bundle,
)
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.editor import (
    DEFAULT_RATTISH_EDITOR_SERVICE,
    MAX_EDITOR_SOURCE_BYTES,
    RattishEditorError,
)
from gofer.rattish.parser import parse_rattish_recovering
from gofer.rattish.preflight import run_preflight
from gofer.rattish.run_retention import (
    RETENTION_SETTINGS_FILE,
    UNREADABLE_RETENTION_MESSAGE,
    coerce_retention_settings,
    prune_run_records,
    read_retention_document,
    retention_workflows,
    save_retention_settings,
)
from gofer.rattish.run_service import (
    NEVER_STARTED_STATUSES,
    RattishResumeRefused,
    RattishRunArtifactError,
    RattishRunResult,
    StartedBy,
    effective_run_status,
    fold_run_journal,
    new_run_id,
    rattish_run_from_preview,
    rerun_rattish_run,
    rerun_refusal,
    resume_rattish_run,
    resume_refusal,
    resume_rerun_nodes,
    resumed_child,
    resumed_run_id,
    run_rattish_file,
    run_started_by,
    run_stop_requested,
    stop_rattish_runs,
)
from gofer.rattish.run_values import withheld_input_names
from gofer.rattish.storage import migrate_legacy_directory, workflow_owned_directory
from gofer.rattish.trigger_service import TriggerRun, run_in_process
from gofer.rattish.triggers import TriggerStore
from gofer.rattish.workflow_id import validate_workflow_id
from gofer.rattish.workspaces import (
    RattishWorkspaceError,
    RegisteredWorkflow,
    create_registered_workflow,
    delete_registered_workflow,
    discover_project_workflows,
    find_registered_workflow,
    list_registered_workflows,
    update_registered_workflow_name,
)
from gofer.subscriptions.acp_providers import AcpSubscription
from gofer.subscriptions.antigravity import AntigravitySubscription
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.cli_providers import CliSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription
from gofer.ui.chat import delete_workflow_chat_prompt, workflow_chat_prompt_path
from gofer.ui.workflow_runs import run_error_message, workflow_run_summaries
from gofer.utils.atomic_output import atomic_binary_output
from gofer.utils.logging import get_logger
from gofer.utils.paths import get_data_dir

log = get_logger(__name__)


class WorkflowAlreadyExistsError(ValueError):
    pass


class WorkflowCreateError(ValueError):
    pass


class WorkflowUpdateError(ValueError):
    pass


class WorkflowRunError(ValueError):
    pass


RATTISH_RESUME_OPTIONS_UNSUPPORTED = (
    "Rattish runs resume where they stopped. To rerun from a chosen node, use Run from here "
    "in the Studio, `Raticode rattish run PATH --from NODE` or `Raticode rattish start "
    "WORKFLOW_ID --from NODE`. Rerunning only failed nodes or ignoring saved results isn't "
    "supported."
)


class WorkflowApprovalError(ValueError):
    pass


class WorkflowPlanError(ValueError):
    pass


class ProviderProfileError(ValueError):
    pass


class WorkflowLogError(ValueError):
    pass


class WorkflowBundleError(ValueError):
    pass


_subscriptions = {
    "claude_code": ClaudeCodeSubscription(),
    "codex": CodexSubscription(),
    **{provider: CliSubscription(provider) for provider in ("cursor", "copilot", "opencode")},
    "antigravity": AntigravitySubscription(),
    "grok": AcpSubscription("grok"),
    "openai_api": OpenAiApiSubscription(),
    "anthropic_api": AnthropicApiSubscription(),
}
_active_run_lock = threading.Lock()
_active_rattish_runs: dict[tuple[str, str, str], threading.Event] = {}
CHAT_THREAD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
INDEX_VERSION = 1


class DiscoveryCancelled(Exception):
    """The caller no longer needs this discovery result."""


def list_workflow_payloads(
    data_dir: Path | None = None, *, check_cancelled: Callable[[], None] = lambda: None
) -> dict[str, Any]:
    """Return registered Rattish workflow summaries for Studio.

    ``legacyTomlFiles`` names TOML workflows left in the data folder. Raticode no
    longer loads them; the desktop offers to convert each one to Rattish.
    """
    check_cancelled()
    base = _data_dir(data_dir)
    workflows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    payload: dict[str, Any] = {
        "authoringLanguage": "rattish",
        "dataDir": str(base),
        "workflows": workflows,
        "errors": errors,
        "legacyTomlFiles": [],
    }
    try:
        base.stat()
    except FileNotFoundError:
        return payload
    except OSError:
        pass

    try:
        registered = list_registered_workflows(registry_dir=base)
    except RattishWorkspaceError as exc:
        errors.append({"path": "workspace-registry.json", "message": str(exc)})
        registered = ()
    for registered_workflow in registered:
        check_cancelled()
        workflows.append(_registered_rattish_payload(registered_workflow, base))

    errors.sort(key=lambda error: (error["path"], error["message"]))
    payload["legacyTomlFiles"] = _legacy_toml_files_payload(base)
    return payload


def _legacy_toml_files_payload(base: Path) -> list[dict[str, str]]:
    from gofer.rattish.toml_convert import legacy_toml_workflow_files

    try:
        paths = legacy_toml_workflow_files(base)
    except OSError:
        return []
    return [{"name": path.name, "path": str(path)} for path in paths]


def create_registered_workflow_payload(
    name: str,
    project_root: Path,
    *,
    registry_dir: Path | None = None,
) -> dict[str, Any]:
    """Create a registered Rattish workflow and return its read-only studio summary."""
    base = _data_dir(registry_dir)
    try:
        registered = create_registered_workflow(project_root, name, registry_dir=base)
    except (RattishWorkspaceError, RattishArtifactError, RattishError) as exc:
        raise WorkflowCreateError(str(exc)) from exc
    return _registered_rattish_payload(registered, base)


def convert_toml_workflow_payload(
    toml_path: Path,
    project_root: Path,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Convert a TOML workflow left in the data folder into a registered Rattish workflow.

    Only a file the workflow list names in ``legacyTomlFiles`` can be converted, so the
    route can't be used to read other files. The TOML file is left as it was.
    """
    from gofer.rattish.toml_convert import (
        TomlConversionError,
        convert_toml_into_project,
        legacy_toml_workflow_files,
    )

    base = _data_dir(data_dir)
    try:
        wanted = toml_path.expanduser().resolve()
        known = {path.resolve() for path in legacy_toml_workflow_files(base)}
    except OSError as exc:
        raise WorkflowCreateError(str(exc)) from exc
    if wanted not in known:
        raise WorkflowCreateError(
            "Only a TOML workflow listed in the data folder can be converted."
        )
    try:
        registered, conversion = convert_toml_into_project(wanted, project_root, registry_dir=base)
    except TomlConversionError as exc:
        raise WorkflowCreateError(str(exc)) from exc
    except (RattishWorkspaceError, RattishArtifactError, RattishError, OSError) as exc:
        raise WorkflowCreateError(str(exc)) from exc
    return {
        "workflow": _registered_rattish_payload(registered, base),
        "conversion": {
            "notes": list(conversion.notes),
            "unsupported": list(conversion.unsupported),
        },
    }


def create_workflow_from_starter_payload(
    starter_id: str,
    name: str,
    project_root: Path,
    *,
    provider: str | None = None,
    registry_dir: Path | None = None,
) -> dict[str, Any]:
    """Copy a starter workflow into the project and return its studio summary.

    PROVIDER, the app the dialog picked, replaces the provider the starter lists.
    """
    from gofer.rattish.starters import create_workflow_from_starter

    base = _data_dir(registry_dir)
    try:
        registered = create_workflow_from_starter(
            project_root, starter_id, name or None, provider=provider, registry_dir=base
        )
    except (RattishWorkspaceError, RattishArtifactError, RattishError) as exc:
        raise WorkflowCreateError(str(exc)) from exc
    return _registered_rattish_payload(registered, base)


def starter_workflows_payload() -> dict[str, Any]:
    """The starter workflows the new-workflow dialog offers, and the apps it may pick.

    providerApps lists the app IDs that can run a starter's model nodes.
    """
    from gofer.rattish.starters import list_starters, provider_apps

    try:
        return {
            "starters": [starter.to_payload() for starter in list_starters()],
            "providerApps": provider_apps(),
        }
    except RattishWorkspaceError as exc:
        raise WorkflowCreateError(str(exc)) from exc


def open_project_payload(
    project_root: Path,
    *,
    registry_dir: Path | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Register and compile workflows while the requesting client still needs them."""
    check_cancelled()
    base = _data_dir(registry_dir)
    try:
        discovery = discover_project_workflows(
            project_root, registry_dir=base, check_cancelled=check_cancelled
        )
    except RattishWorkspaceError as exc:
        raise WorkflowCreateError(str(exc)) from exc
    except OSError as exc:
        reason = (
            "permission denied"
            if isinstance(exc, PermissionError)
            else "filesystem changed or unavailable"
        )
        raise WorkflowCreateError(
            f"Unable to discover project workflows: {reason}. Retry on refresh."
        ) from exc
    workflows = []
    for workflow in discovery.workflows:
        check_cancelled()
        workflows.append(_registered_rattish_payload(workflow, base))
        # Compilation is synchronous; discard its result and stop before the next file.
        check_cancelled()
    return {
        "projectRoot": str(project_root.expanduser().resolve()),
        "workflows": workflows,
        # True when the folder walk stopped at its depth or size limit.
        "discoveryLimited": discovery.limited,
    }


def open_rattish_document_payload(workflow_id: str, data_dir: Path | None = None) -> dict[str, Any]:
    """Open one registered Rattish workflow for Studio editing."""
    return DEFAULT_RATTISH_EDITOR_SERVICE.open_document(
        workflow_id,
        data_dir=_data_dir(data_dir),
    )


def analyze_rattish_document_payload(
    workflow_id: str,
    source: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Analyze an unsaved Rattish buffer without changing the workflow file."""
    return DEFAULT_RATTISH_EDITOR_SERVICE.analyze_document(
        workflow_id,
        source,
        data_dir=_data_dir(data_dir),
    )


def save_rattish_document_payload(
    workflow_id: str,
    source: str,
    expected_revision: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Save a Rattish buffer if its on-disk revision still matches."""
    return DEFAULT_RATTISH_EDITOR_SERVICE.save_document(
        workflow_id,
        source,
        expected_revision,
        data_dir=_data_dir(data_dir),
    )


def mutate_rattish_document_payload(
    workflow_id: str,
    mutations: list[dict[str, Any]],
    expected_revision: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Apply targeted graph edits to a registered Rattish source document."""
    return DEFAULT_RATTISH_EDITOR_SERVICE.mutate_document(
        workflow_id,
        mutations,
        expected_revision,
        data_dir=_data_dir(data_dir),
    )


def save_rattish_metadata_payload(
    workflow_id: str,
    metadata: dict[str, Any],
    expected_revision: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Save workflow editor metadata with optimistic concurrency."""
    return DEFAULT_RATTISH_EDITOR_SERVICE.save_metadata(
        workflow_id,
        metadata,
        expected_revision,
        data_dir=_data_dir(data_dir),
    )


def _registered_rattish_payload(
    workflow: RegisteredWorkflow,
    registry_dir: Path,
) -> dict[str, Any]:
    status = "Ready"
    validation_error = None
    name = workflow.name
    try:
        artifact = compile_rattish_file(
            workflow.entrypoint,
            data_dir=registry_dir,
            workflow_id=workflow.workflow_id,
        )
        node_count = len(artifact.ir["nodes"])
        route_count = sum(len(node["routes"]) for node in artifact.ir["nodes"])
        inputs = _rattish_workflow_inputs(artifact.ir["workflow"]["inputs"])
        # The registry name is only refreshed when Studio saves the source, so
        # files changed elsewhere must report the name they declare.
        declared_name = artifact.ir["workflow"].get("name")
        if isinstance(declared_name, str) and declared_name.strip():
            name = declared_name.strip()
    except (RattishArtifactError, RattishError, OSError) as exc:
        status = "Error"
        filesystem_error = exc if isinstance(exc, OSError) else exc.__cause__
        if isinstance(filesystem_error, OSError):
            reason = (
                "permission denied"
                if isinstance(filesystem_error, PermissionError)
                else "file changed or unavailable"
            )
            validation_error = f"Unable to compile workflow: {reason}. Retry on refresh."
        else:
            validation_error = _rattish_validation_summary(exc)
            name = _declared_rattish_name(workflow.entrypoint) or name
        node_count = 0
        route_count = 0
        inputs = {}
    try:
        updated_at = _updated_at(workflow.entrypoint)
    except OSError:
        updated_at = "Unknown"
    return {
        "id": workflow.workflow_id,
        "name": name,
        "description": f"{node_count} Rattish nodes, {route_count} routes.",
        "status": status,
        "updatedAt": updated_at,
        "sourcePath": str(workflow.entrypoint),
        "sourceFormat": "rattish",
        "readOnly": False,
        "projectRoot": str(workflow.project_root),
        "projectName": workflow.project_root.name or str(workflow.project_root),
        "workflowRoot": str(workflow.workflow_root),
        "relativeFolder": workflow.relative_folder,
        "managed": workflow.managed,
        "inputs": inputs,
        "nodeCount": node_count,
        "routeCount": route_count,
        "invalid": validation_error is not None,
        "validationError": validation_error,
    }


def _declared_rattish_name(entrypoint: Path) -> str | None:
    """Read the declared name from source that parses but does not compile."""
    try:
        if entrypoint.stat().st_size > MAX_EDITOR_SOURCE_BYTES:
            return None
        source = entrypoint.read_text(encoding="utf-8")
        ast = parse_rattish_recovering(source, source_id=entrypoint.name).ast
    except (OSError, UnicodeError, RattishError):
        return None
    if ast is None:
        return None
    for entry in ast["workflow"]["entries"]:
        if entry["kind"] == "field" and entry["name"]["canonical"] == "name":
            value = entry["value"].get("value")
            return value.strip() if isinstance(value, str) and value.strip() else None
    return None


def _rattish_validation_summary(exc: BaseException) -> str:
    """Describe the first compile error instead of only counting errors."""
    diagnostics = getattr(exc, "diagnostics", None) or getattr(exc.__cause__, "diagnostics", None)
    if not diagnostics:
        return str(exc)
    errors = [item for item in diagnostics if item.severity == "error"] or list(diagnostics)
    first = errors[0]
    more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
    hint = next(
        (item for item in first.suggestions if item.startswith(("Did you mean", "Start the file"))),
        "",
    )
    hint = f" {hint}" if hint else ""
    return f"Line {first.span.start.line}: {first.message}{hint}{more}"


def health_payload(data_dir: Path | None = None) -> dict[str, Any]:
    """Return environment health diagnostics for the React UI."""
    return run_health_checks(data_dir=data_dir).to_dict()


def provider_profiles_payload(data_dir: Path | None = None) -> dict[str, Any]:
    profiles = load_provider_profiles(_data_dir(data_dir))
    return {"profiles": [provider_profile_ui_payload(profile) for profile in profiles.values()]}


def upsert_provider_profile_payload(
    payload: dict[str, Any],
    data_dir: Path | None = None,
) -> dict[str, Any]:
    profiles = load_provider_profiles(_data_dir(data_dir))
    existing_name = payload.get("name")
    existing = profiles.get(existing_name) if isinstance(existing_name, str) else None
    try:
        profile = provider_profile_from_ui_payload(payload, existing)
    except ValueError as exc:
        raise ProviderProfileError(str(exc)) from exc
    if profile.account_id != (existing.account_id if existing else None):
        # Account folders are made only by Add account (provider_accounts.py).
        raise ProviderProfileError(
            "Add accounts in Settings > Providers, with Add account in the provider's section"
        )
    profiles[profile.name] = profile
    save_provider_profiles(profiles, _data_dir(data_dir))
    return {"profile": provider_profile_ui_payload(profile)}


def check_provider_profile_payload(
    name: str,
    model: Any = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Ask a local model server profile's server for its models. Never returns the key."""
    from gofer.core.local_models import check_local_server

    try:
        validate_provider_profile_name(name)
        if model is not None and not isinstance(model, str):
            raise ValueError("model must be a string")
        result = check_local_server(name, model=model or None, data_dir=_data_dir(data_dir))
    except ValueError as exc:
        raise ProviderProfileError(str(exc)) from exc
    return {"check": result.to_json()}


def delete_provider_profile_payload(
    name: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        validate_provider_profile_name(name)
    except ValueError as exc:
        raise ProviderProfileError(str(exc)) from exc
    profiles = load_provider_profiles(_data_dir(data_dir))
    if name not in profiles:
        raise ProviderProfileError(f"Provider profile '{name}' not found")
    if profiles[name].account_id:
        from gofer.core.provider_accounts import remove_account

        # Sign the account out with its CLI before its folder goes.
        remove_account(name, data_dir=_data_dir(data_dir))
        return {"profile": name, "deleted": True}
    del profiles[name]
    save_provider_profiles(profiles, _data_dir(data_dir))
    return {"profile": name, "deleted": True}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    with atomic_binary_output(path) as output:
        output.write(json.dumps(payload, default=str, indent=2, sort_keys=True).encode("utf-8"))


def export_rattish_bundle_payload(
    workflow_id: str,
    output_path: Path,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Export a registered Rattish workspace as a portable .raticode archive."""
    base = _data_dir(data_dir)
    try:
        preview = export_rattish_bundle(workflow_id, output_path, registry_dir=base)
    except (RattishBundleError, RattishWorkspaceError, RattishArtifactError, RattishError) as exc:
        raise WorkflowBundleError(str(exc)) from exc
    bundle_path = output_path.expanduser().resolve()
    if bundle_path.suffix.lower() != ".raticode":
        bundle_path = bundle_path.with_name(f"{bundle_path.name}.raticode")
    return {"bundlePath": str(bundle_path), "bundle": preview.to_dict()}


def preview_rattish_bundle_payload(
    bundle_path: Path,
    *,
    resource_limits: ResourceLimits | None = None,
) -> dict[str, Any]:
    """Inspect a .raticode archive without writing project files."""
    try:
        return preview_rattish_bundle(
            bundle_path,
            limits=resource_limits or DEFAULT_RESOURCE_LIMITS,
        ).to_dict()
    except RattishBundleError as exc:
        raise WorkflowBundleError(str(exc)) from exc


def import_rattish_bundle_payload(
    bundle_path: Path,
    project_root: Path,
    data_dir: Path | None = None,
    *,
    resource_limits: ResourceLimits | None = None,
) -> dict[str, Any]:
    """Install a .raticode archive into a project and register it."""
    base = _data_dir(data_dir)
    try:
        registered = import_rattish_bundle(
            bundle_path,
            project_root,
            registry_dir=base,
            limits=resource_limits or DEFAULT_RESOURCE_LIMITS,
        )
    except (RattishBundleError, RattishWorkspaceError, RattishArtifactError, RattishError) as exc:
        raise WorkflowBundleError(str(exc)) from exc
    return _registered_rattish_payload(registered, base)


def delete_workflow_payload(
    workflow_id: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    from gofer.rattish import run_worktrees

    registered = _registered_workflow_or_none(workflow_id, base)
    storage_id = registered.workflow_id if registered is not None else workflow_id
    # Read before the workflow's folder, which holds its run records, goes.
    records = _rattish_worktree_records(base, storage_id) if registered is not None else []
    for document, _record in records:
        if document.get("status") == "running":
            raise WorkflowUpdateError(
                f"Run {document.get('run_id')} of this workflow is still running in its "
                "own worktree. Stop it, then delete the workflow."
            )
    try:
        delete_registered_workflow(workflow_id, registry_dir=base)
    except (OSError, RattishWorkspaceError) as exc:
        raise WorkflowUpdateError(str(exc)) from exc
    if registered is not None:
        # Its triggers would otherwise fail on every occurrence from now on.
        TriggerStore(base).delete_for_source(registered.entrypoint)
    # Runs name their worktree folder after the compiled workflow ID, which is lowercase.
    removed, errors = run_worktrees.remove_workflow_worktrees(
        base, storage_id.lower(), [record for _document, record in records]
    )
    for error in errors:
        log.warning("Couldn't remove a run worktree of deleted workflow %s: %s", storage_id, error)
    delete_workflow_chat_prompt(base, workflow_id)
    return {
        "workflowId": workflow_id,
        "deleted": True,
        "runWorktreesRemoved": len(removed),
        "runWorktreeErrors": errors,
    }


def rename_workflow_payload(
    workflow_id: str,
    name: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    workflow_name = name.strip()
    if not workflow_name:
        raise WorkflowUpdateError("Workflow name is required")

    base = _data_dir(data_dir)
    registered = _registered_workflow_or_none(workflow_id, base)
    if registered is None:
        raise WorkflowUpdateError(f"Workflow '{workflow_id}' not found")
    _set_rattish_workflow_name(registered.workflow_id, workflow_name, base)
    return _registered_rattish_payload(_find_registered(registered.workflow_id, base), base)


def duplicate_workflow_payload(
    workflow_id: str,
    name: str | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    registered = _registered_workflow_or_none(workflow_id, base)
    if registered is None:
        raise WorkflowUpdateError(f"Workflow '{workflow_id}' not found")
    return _duplicate_registered_rattish_workflow(registered, name, base)


def _registered_workflow_or_none(workflow_id: str, base: Path) -> RegisteredWorkflow | None:
    try:
        return find_registered_workflow(workflow_id, registry_dir=base)
    except RattishWorkspaceError:
        return None


def _find_registered(workflow_id: str, base: Path) -> RegisteredWorkflow:
    try:
        return find_registered_workflow(workflow_id, registry_dir=base)
    except RattishWorkspaceError as exc:
        raise WorkflowUpdateError(str(exc)) from exc


def _set_rattish_workflow_name(workflow_id: str, name: str, base: Path) -> None:
    """Rename by editing the declared name, which is what the workflow reports."""
    service = DEFAULT_RATTISH_EDITOR_SERVICE
    try:
        document = service.open_document(workflow_id, data_dir=base)
        service.mutate_document(
            workflow_id,
            [{"kind": "set_field", "target": "workflow", "field": "name", "value": name}],
            str(document["savedRevision"]),
            data_dir=base,
        )
        # Invalid workflows report the registry name, so keep it in step too.
        update_registered_workflow_name(workflow_id, name, registry_dir=base)
    except (RattishEditorError, RattishWorkspaceError, OSError) as exc:
        raise WorkflowUpdateError(f"Unable to rename workflow: {exc}") from exc


def _duplicate_registered_rattish_workflow(
    registered: RegisteredWorkflow,
    name: str | None,
    base: Path,
) -> dict[str, Any]:
    source_name = str(_registered_rattish_payload(registered, base)["name"])
    if name and name.strip():
        candidate_name = name.strip()
    else:
        taken = {
            str(workflow.name).lower()
            for workflow in list_registered_workflows(registry_dir=base)
            if workflow.project_root == registered.project_root
        }
        candidate_name = f"{source_name} copy"
        suffix = 2
        while candidate_name.lower() in taken:
            candidate_name = f"{source_name} copy {suffix}"
            suffix += 1
    try:
        duplicated = duplicate_rattish_workspace(
            registered.workflow_id,
            candidate_name,
            registry_dir=base,
        )
    except RattishError as exc:
        raise WorkflowCreateError(
            f"Fix this workflow before duplicating it. {_rattish_validation_summary(exc)}"
        ) from exc
    except (RattishBundleError, RattishWorkspaceError) as exc:
        raise WorkflowCreateError(str(exc)) from exc
    try:
        _set_rattish_workflow_name(duplicated.workflow_id, candidate_name, base)
    except WorkflowUpdateError:
        # Source that cannot be parsed keeps its text; the registry name still
        # distinguishes the copy until the source is fixed.
        pass
    return _registered_rattish_payload(_find_registered(duplicated.workflow_id, base), base)


def delete_workflow_chat_payload(
    workflow_id: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    _validate_chat_prompt_id(workflow_id)
    path = workflow_chat_prompt_path(base, workflow_id).resolve()
    _assert_within_base(path, base, error_cls=WorkflowUpdateError)
    delete_workflow_chat_prompt(base, workflow_id)
    return {"workflowId": workflow_id, "deleted": True}


async def run_workflow_payload(
    workflow_id: str,
    data_dir: Path | None = None,
    dry_run: bool = False,
    trigger_context: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    background: bool = False,
    expected_revision: str | None = None,
    started_by: StartedBy | None = None,
    from_node: str | None = None,
    reuse_run_id: str | None = None,
) -> dict[str, Any]:
    """Start a run. ``started_by`` is recorded on Rattish runs; see ``run_rattish_file``.

    ``from_node`` starts a Rattish run from that node, reusing an earlier run's
    results for the unchanged nodes upstream of it: the newest run that fits, or
    ``reuse_run_id``. Its ``inputs`` are only the password inputs that run didn't
    save. With ``dry_run`` it returns what would be reused instead of a plan.
    """
    base = _data_dir(data_dir)
    registered = _registered_rattish_workflow(workflow_id, base)
    if registered is None:
        raise WorkflowRunError(f"Workflow '{workflow_id}' not found")
    if reuse_run_id and not from_node:
        raise WorkflowRunError("reuseRunId needs fromNode.")
    if from_node:
        return await _run_rattish_from_node(
            workflow_id,
            registered.entrypoint,
            base,
            from_node,
            reuse_run_id=reuse_run_id,
            password_inputs=_merged_run_inputs(parameters, inputs, WorkflowRunError),
            trigger_context=trigger_context,
            dry_run=dry_run,
            background=background,
            expected_revision=expected_revision,
            started_by=started_by,
        )
    supplied_inputs = _merged_run_inputs(parameters, inputs, WorkflowRunError)
    if dry_run:
        return _rattish_plan_payload(
            registered,
            base,
            trigger_context=trigger_context,
            error_cls=WorkflowRunError,
        )
    if background:
        return await _start_rattish_background_run(
            workflow_id,
            registered.entrypoint,
            base,
            supplied_inputs,
            trigger_context,
            expected_revision,
            started_by=started_by,
        )
    run_id = new_run_id()
    cancel_event = threading.Event()
    active_key = (*_run_key(base, workflow_id), run_id)
    with _active_run_lock:
        _active_rattish_runs[active_key] = cancel_event
    try:
        rattish_result = await run_rattish_file(
            registered.entrypoint,
            workflow_inputs=supplied_inputs,
            trigger_events=([trigger_context] if trigger_context else None),
            data_dir=base,
            run_id=run_id,
            cancel_event=cancel_event,
            expected_revision=expected_revision,
            started_by=started_by,
        )
    except (RattishArtifactError, RattishError, RattishRunArtifactError, OSError) as exc:
        raise WorkflowRunError(str(exc)) from exc
    finally:
        with _active_run_lock:
            _active_rattish_runs.pop(active_key, None)
    return _rattish_run_payload(rattish_result, include_log_text=False)


async def _run_rattish_from_node(
    workflow_id: str,
    source: Path,
    base: Path,
    from_node: str,
    *,
    reuse_run_id: str | None,
    password_inputs: dict[str, Any],
    trigger_context: dict[str, Any] | None,
    dry_run: bool,
    background: bool,
    expected_revision: str | None,
    started_by: StartedBy | None,
) -> dict[str, Any]:
    """Run from a node, or with ``dry_run`` say what it would reuse. See ``run_from``."""
    if trigger_context:
        raise WorkflowRunError("A run from a node uses the reused run's trigger events.")
    if dry_run:
        try:
            return rattish_run_from_preview(
                source, from_node, data_dir=base, reuse_run_id=reuse_run_id
            )
        except (RattishResumeRefused, RattishArtifactError, RattishError, OSError) as exc:
            raise WorkflowRunError(str(exc)) from exc
    if background:
        return await _start_rattish_background_run(
            workflow_id,
            source,
            base,
            {},
            None,
            expected_revision,
            started_by=started_by,
            given_inputs=password_inputs,
            from_node=from_node,
            reuse_run_id=reuse_run_id,
        )
    run_id = new_run_id()
    cancel_event = threading.Event()
    active_key = (*_run_key(base, workflow_id), run_id)
    with _active_run_lock:
        _active_rattish_runs[active_key] = cancel_event
    try:
        result = await run_rattish_file(
            source,
            data_dir=base,
            run_id=run_id,
            cancel_event=cancel_event,
            expected_revision=expected_revision,
            started_by=started_by,
            from_node=from_node,
            reuse_run_id=reuse_run_id,
            resume_inputs=password_inputs or None,
        )
    except (
        RattishResumeRefused,
        RattishArtifactError,
        RattishError,
        RattishRunArtifactError,
        OSError,
    ) as exc:
        raise WorkflowRunError(str(exc)) from exc
    finally:
        with _active_run_lock:
            _active_rattish_runs.pop(active_key, None)
    return _rattish_run_payload(result, include_log_text=False)


async def resume_workflow_payload(
    workflow_id: str,
    data_dir: Path | None = None,
    *,
    run_id: str,
    from_node: str | None = None,
    only_node: str | None = None,
    skip_cache: bool = False,
    force: bool = False,
    trigger_context: dict[str, Any] | None = None,
    background: bool = False,
    expected_revision: str | None = None,
    started_by: StartedBy | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume a run as a new run; see ``resume_rattish_run``.

    The node options (``from_node``, ``only_node``, ``skip_cache``, ``force``) and
    ``trigger_context`` are refused: use run from a node instead. ``inputs`` holds the
    password inputs the run didn't save (its resume state lists them as ``needsInputs``).
    """
    if from_node and only_node:
        raise WorkflowRunError("fromNode and onlyNode cannot be used together")
    if not run_id:
        raise WorkflowRunError("runId is required")
    base = _data_dir(data_dir)
    registered = _registered_rattish_workflow(workflow_id, base)
    if registered is None:
        raise WorkflowRunError(f"Workflow '{workflow_id}' not found")
    if from_node or only_node or skip_cache or force or trigger_context:
        raise WorkflowRunError(RATTISH_RESUME_OPTIONS_UNSUPPORTED)
    try:
        document = _read_rattish_run_artifact(_rattish_run_artifact_path(base, workflow_id, run_id))
    except WorkflowLogError as exc:
        raise WorkflowRunError(str(exc)) from exc
    if background:
        return await _start_rattish_background_run(
            workflow_id,
            registered.entrypoint,
            base,
            {},
            None,
            expected_revision,
            resume_from=document,
            started_by=started_by,
            given_inputs=inputs,
        )
    cancel_event = threading.Event()
    resumed_id = new_run_id()
    active_key = (*_run_key(base, workflow_id), resumed_id)
    with _active_run_lock:
        _active_rattish_runs[active_key] = cancel_event
    try:
        result = await resume_rattish_run(
            registered.entrypoint,
            document,
            data_dir=base,
            run_id=resumed_id,
            cancel_event=cancel_event,
            expected_revision=expected_revision,
            started_by=started_by,
            resume_inputs=inputs or None,
        )
    except (
        RattishResumeRefused,
        RattishArtifactError,
        RattishError,
        RattishRunArtifactError,
        OSError,
    ) as exc:
        raise WorkflowRunError(str(exc)) from exc
    finally:
        with _active_run_lock:
            _active_rattish_runs.pop(active_key, None)
    return _rattish_run_payload(result, include_log_text=False)


def rattish_run_workflow_id(run_id: str, data_dir: Path | None = None) -> str:
    """The registered workflow a Rattish run belongs to. Run IDs are unique."""
    base = _data_dir(data_dir)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None:
        raise WorkflowRunError(f"Invalid run ID {run_id!r}.")
    for workflow in list_registered_workflows(registry_dir=base):
        try:
            directory = _rattish_run_directory(base, workflow.workflow_id)
        except WorkflowLogError:
            continue
        if (directory / f"{run_id}.json").is_file():
            return workflow.workflow_id
    raise WorkflowRunError(f"Run {run_id!r} was not found in any registered workflow.")


def _worktree_run(
    run_id: str, data_dir: Path | None, workflow_id: str | None
) -> tuple[Path, dict[str, Any]]:
    """A Rattish run's record path and document, found from its ID when needed."""
    base = _data_dir(data_dir)
    workflow_id = workflow_id or rattish_run_workflow_id(run_id, base)
    try:
        path = _rattish_run_artifact_path(base, workflow_id, run_id)
        return path, _read_rattish_run_artifact(path)
    except WorkflowLogError as exc:
        raise WorkflowRunError(str(exc)) from exc


def _rattish_worktree_records(
    base: Path, workflow_id: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Each run of the workflow that had a worktree: its document and worktree record."""
    try:
        paths = _rattish_run_artifact_paths(base, workflow_id)
    except WorkflowLogError:
        return []
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for path in paths:
        try:
            document = _read_rattish_run_artifact(path)
        except WorkflowLogError:
            continue
        record = document.get("worktree")
        if isinstance(record, dict):
            found.append((document, record))
    return found


def workflow_run_worktrees_payload(
    workflow_id: str, data_dir: Path | None = None
) -> dict[str, Any]:
    """The workflow's run worktrees still waiting to be landed or discarded.

    Deleting the workflow removes them with their branches, so Studio and the CLI name
    the count before asking.
    """
    from gofer.rattish import run_worktrees

    base = _data_dir(data_dir)
    registered = _find_registered(workflow_id, base)
    pending = run_worktrees.unlanded_run_ids(base, registered.workflow_id.lower())
    return {"workflowId": registered.workflow_id, "pending": len(pending), "runIds": pending}


def run_changes_payload(
    run_id: str, data_dir: Path | None = None, *, workflow_id: str | None = None
) -> dict[str, Any]:
    """A worktree run's patch, as the commit diff view reads it, and its merge state."""
    from gofer.rattish import run_worktrees

    _, document = _worktree_run(run_id, data_dir, workflow_id)
    try:
        payload = run_worktrees.changes(document)
    except run_worktrees.RunWorktreeError as exc:
        raise WorkflowRunError(str(exc)) from exc
    return {**payload, "runId": run_id, "workflowId": document["workflow"]["id"]}


def land_run_payload(
    run_id: str,
    data_dir: Path | None = None,
    *,
    workflow_id: str | None = None,
    expected_tip: str | None = None,
) -> dict[str, Any]:
    """Land a worktree run's branch in the branch its project was on. Never pushes.

    A conflict changes nothing and comes back as ``{"landed": False, "conflict": True}``
    with the files.
    """
    from gofer.rattish import run_worktrees
    from gofer.ui import ticket_patch

    path, document = _worktree_run(run_id, data_dir, workflow_id)
    try:
        return run_worktrees.land(
            document,
            lambda record: run_worktrees.update_record(path, record),
            expected_tip=expected_tip,
        )
    except ticket_patch.LandConflict as exc:
        record = document.get("worktree") or {}
        return {
            "landed": False,
            "conflict": True,
            "conflicts": exc.files,
            "message": str(exc),
            "branch": record.get("branch"),
            "baseBranch": record.get("base_branch"),
        }
    except (run_worktrees.RunWorktreeError, ticket_patch.PatchError, OSError) as exc:
        raise WorkflowRunError(str(exc)) from exc


def discard_run_payload(
    run_id: str, data_dir: Path | None = None, *, workflow_id: str | None = None
) -> dict[str, Any]:
    """Remove a worktree run's worktree and branch, dropping its changes."""
    from gofer.rattish import run_worktrees

    path, document = _worktree_run(run_id, data_dir, workflow_id)
    try:
        return run_worktrees.discard(
            document, lambda record: run_worktrees.update_record(path, record)
        )
    except (run_worktrees.RunWorktreeError, OSError) as exc:
        raise WorkflowRunError(str(exc)) from exc


async def rerun_workflow_payload(
    run_id: str,
    data_dir: Path | None = None,
    *,
    workflow_id: str | None = None,
    inputs: dict[str, Any] | None = None,
    background: bool = False,
    started_by: StartedBy | None = None,
) -> dict[str, Any]:
    """Run a Rattish workflow again with one run's saved inputs and trigger events.

    ``inputs`` holds only the password inputs that run didn't save (its ``rerun``
    state lists them as ``needsInputs``). The new run uses the current source.
    """
    base = _data_dir(data_dir)
    workflow_id = workflow_id or rattish_run_workflow_id(run_id, base)
    registered = _registered_rattish_workflow(workflow_id, base)
    if registered is None:
        raise WorkflowRunError("Only Rattish workflow runs can be run again with their inputs.")
    try:
        document = _read_rattish_run_artifact(_rattish_run_artifact_path(base, workflow_id, run_id))
    except WorkflowLogError as exc:
        raise WorkflowRunError(str(exc)) from exc
    if background:
        return await _start_rattish_background_run(
            workflow_id,
            registered.entrypoint,
            base,
            {},
            None,
            None,
            started_by=started_by,
            rerun_from=document,
            given_inputs=inputs,
        )
    new_id = new_run_id()
    cancel_event = threading.Event()
    active_key = (*_run_key(base, workflow_id), new_id)
    with _active_run_lock:
        _active_rattish_runs[active_key] = cancel_event
    try:
        result = await rerun_rattish_run(
            registered.entrypoint,
            document,
            data_dir=base,
            inputs=inputs,
            run_id=new_id,
            cancel_event=cancel_event,
            started_by=started_by or "manual",
        )
    except (
        RattishResumeRefused,
        RattishArtifactError,
        RattishError,
        RattishRunArtifactError,
        OSError,
    ) as exc:
        raise WorkflowRunError(str(exc)) from exc
    finally:
        with _active_run_lock:
            _active_rattish_runs.pop(active_key, None)
    return _rattish_run_payload(result, include_log_text=False)


def workflow_plan_payload(
    workflow_id: str,
    data_dir: Path | None = None,
    trigger_context: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    registered = _registered_rattish_workflow(workflow_id, base)
    if registered is None:
        raise WorkflowPlanError(f"Workflow '{workflow_id}' not found")
    _merged_run_inputs(parameters, inputs, WorkflowPlanError)
    return _rattish_plan_payload(
        registered,
        base,
        trigger_context=trigger_context,
        error_cls=WorkflowPlanError,
    )


async def _start_rattish_background_run(
    workflow_id: str,
    source: Path,
    base: Path,
    supplied_inputs: dict[str, Any],
    trigger_context: dict[str, Any] | None,
    expected_revision: str | None,
    resume_from: Mapping[str, Any] | None = None,
    started_by: StartedBy | None = None,
    rerun_from: Mapping[str, Any] | None = None,
    given_inputs: Mapping[str, Any] | None = None,
    from_node: str | None = None,
    reuse_run_id: str | None = None,
) -> dict[str, Any]:
    """Start a run, a resume (``resume_from``), a rerun (``rerun_from``) or a run from
    a node (``from_node``) on a thread.

    ``given_inputs`` are the password inputs a resumed, rerun or reused run didn't save.
    """
    run_id = new_run_id()
    cancel_event = threading.Event()
    acknowledged = threading.Event()
    result: dict[str, Any] = {}
    active_key = (*_run_key(base, workflow_id), run_id)
    with _active_run_lock:
        if len(_active_rattish_runs) >= 32:
            raise WorkflowRunError("Too many active workflow runs. Wait for a run to finish.")
        _active_rattish_runs[active_key] = cancel_event

    def started(run: RattishRunResult) -> None:
        result["run"] = _rattish_run_payload(run)
        acknowledged.set()

    async def execute() -> None:
        try:
            if resume_from is not None:
                run = await resume_rattish_run(
                    source,
                    resume_from,
                    data_dir=base,
                    run_id=run_id,
                    cancel_event=cancel_event,
                    on_started=started,
                    expected_revision=expected_revision,
                    started_by=started_by,
                    resume_inputs=given_inputs or None,
                )
            elif from_node is not None:
                run = await run_rattish_file(
                    source,
                    data_dir=base,
                    run_id=run_id,
                    cancel_event=cancel_event,
                    on_started=started,
                    expected_revision=expected_revision,
                    started_by=started_by or "manual",
                    from_node=from_node,
                    reuse_run_id=reuse_run_id,
                    resume_inputs=given_inputs or None,
                )
            elif rerun_from is not None:
                run = await rerun_rattish_run(
                    source,
                    rerun_from,
                    data_dir=base,
                    inputs=given_inputs,
                    run_id=run_id,
                    cancel_event=cancel_event,
                    on_started=started,
                    started_by=started_by or "manual",
                )
            else:
                run = await run_rattish_file(
                    source,
                    workflow_inputs=supplied_inputs,
                    trigger_events=([trigger_context] if trigger_context else None),
                    data_dir=base,
                    run_id=run_id,
                    cancel_event=cancel_event,
                    on_started=started,
                    expected_revision=expected_revision,
                    started_by=started_by,
                )
            result["run"] = _rattish_run_payload(run)
        except Exception as exc:
            # Before the run is acknowledged the caller reports this; afterwards nobody reads
            # it, so it must reach the log.
            if acknowledged.is_set():
                log.exception("Workflow run %s failed after it started", run_id)
            result["error"] = str(exc)
        finally:
            with _active_run_lock:
                _active_rattish_runs.pop(active_key, None)
            acknowledged.set()

    thread = threading.Thread(
        target=lambda: anyio.run(execute), name=f"rattish-{run_id}", daemon=True
    )
    try:
        thread.start()
        with anyio.move_on_after(30) as timeout:
            while not acknowledged.is_set():
                await anyio.sleep(0.01)
        if timeout.cancel_called:
            cancel_event.set()
            raise WorkflowRunError(
                "The runner did not acknowledge the workflow in time. "
                "Its pending start was cancelled."
            )
        if "error" in result:
            raise WorkflowRunError(result["error"])
        return cast(dict[str, Any], result["run"])
    except BaseException:
        cancel_event.set()
        if not thread.is_alive():
            with _active_run_lock:
                _active_rattish_runs.pop(active_key, None)
        raise


def run_triggered_rattish(run: TriggerRun) -> RattishRunResult:
    """Run a trigger's workflow so the app's Stop button can reach it."""
    active_key = (*_run_key(run.data_dir, run.workflow_id), run.run_id)
    with _active_run_lock:
        _active_rattish_runs[active_key] = run.cancel_event
    try:
        return run_in_process(run)
    finally:
        with _active_run_lock:
            _active_rattish_runs.pop(active_key, None)


def stop_rattish_runs_for_shutdown(data_dir: Path | None = None) -> None:
    """Allow cooperative task and process cleanup before the UI server exits."""
    base = str(_data_dir(data_dir).resolve())
    with _active_run_lock:
        active = {key: event for key, event in _active_rattish_runs.items() if key[0] == base}
    for event in active.values():
        event.set()
    deadline = time.monotonic() + 5
    while active and time.monotonic() < deadline:
        with _active_run_lock:
            active = {key: event for key, event in active.items() if key in _active_rattish_runs}
        if active:
            time.sleep(0.01)


def stop_workflow_run_payload(
    workflow_id: str,
    data_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    _validate_storage_workflow_id(workflow_id, WorkflowUpdateError)
    if run_id:
        with _active_run_lock:
            rattish_cancel = _active_rattish_runs.get((*_run_key(base, workflow_id), run_id))
        if rattish_cancel is not None:
            rattish_cancel.set()
            # The marker also lets Runs show "Stop requested" until the run ends.
            _request_rattish_stop(base, workflow_id, run_id, strict=False)
            return {
                "workflowId": workflow_id,
                "runId": run_id,
                "stopped": True,
                "message": "Stop requested",
            }
        if _registered_rattish_workflow(workflow_id, base) is not None:
            # Another process, such as `rattish triggers start`, may own the run.
            stopped = bool(_request_rattish_stop(base, workflow_id, run_id))
            return {
                "workflowId": workflow_id,
                "runId": run_id,
                "stopped": stopped,
                "message": "Stop requested" if stopped else "This run is no longer running.",
            }
    elif _registered_rattish_workflow(workflow_id, base) is not None:
        key = _run_key(base, workflow_id)
        with _active_run_lock:
            local = {
                active[2]: event
                for active, event in _active_rattish_runs.items()
                if active[:2] == key
            }
        for event in local.values():
            event.set()
        asked = {*local, *_request_rattish_stop(base, workflow_id)}
        return {
            "workflowId": workflow_id,
            "stopped": bool(asked),
            "runIds": sorted(asked),
            "message": "Stop requested" if asked else "No active run",
        }
    if run_id:
        return {
            "workflowId": workflow_id,
            "runId": run_id,
            "stopped": False,
            "message": "No active run",
        }
    return {"workflowId": workflow_id, "stopped": False, "message": "No active run"}


def _request_rattish_stop(
    base: Path, workflow_id: str, run_id: str | None = None, *, strict: bool = True
) -> list[str]:
    """Write stop markers that whichever process runs these runs will see."""
    try:
        return stop_rattish_runs(base, workflow_id, run_id)
    except (OSError, ValueError, RattishRunArtifactError) as exc:
        if strict and run_id is not None:
            raise WorkflowUpdateError(f"Could not ask the run to stop: {exc}") from exc
        log.warning("Could not ask runs of %s to stop: %s", workflow_id, exc)
        return []


def _run_key(data_dir: Path, workflow_id: str) -> tuple[str, str]:
    return (str(data_dir.resolve()), workflow_id)


def _approval_request_payload(request: ApprovalRequest) -> dict[str, Any]:
    decision = request.decision
    return {
        "workflowId": request.workflow_id,
        "runId": request.run_id,
        "nodeId": request.node_id,
        "message": request.message,
        "subject": request.subject_manifest_id,
        "status": request.status,
        "cancelledReason": request.cancelled_reason,
        "approvers": request.approvers,
        "requestedAt": request.requested_at,
        "timeoutSeconds": request.timeout_seconds,
        "timeoutDecision": request.timeout_decision,
        "options": request.options,
        "rejectOptions": request.reject_options,
        "reply": request.reply_mode,
        "decision": decision.to_dict() if decision is not None else None,
        "logPath": request.log_path,
    }


def list_workflow_approvals_payload(
    workflow_id: str,
    data_dir: Path | None = None,
    *,
    include_decided: bool = True,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    _validate_storage_workflow_id(workflow_id, WorkflowApprovalError)
    store = ApprovalStore(base)
    for pending in store.list_pending(workflow_id):
        _cancel_approval_for_finished_rattish_run(base, store, pending)
    all_requests = store.list_requests(workflow_id)
    requests = (
        all_requests
        if include_decided
        else [request for request in all_requests if request.status == "pending"]
    )
    return {
        "workflowId": workflow_id,
        "approvals": [_approval_request_payload(request) for request in requests],
    }


def decide_workflow_approval_payload(
    workflow_id: str,
    run_id: str,
    node_id: str,
    decision: str,
    data_dir: Path | None = None,
    *,
    decided_by: str = "ui",
    notes: str = "",
    choice: str | None = None,
    reply: str = "",
) -> dict[str, Any]:
    """Record a decision. A gate with options takes ``choice``, which sets the decision."""
    if decision not in {"approved", "rejected"}:
        raise WorkflowApprovalError("Decision must be approved or rejected")
    base = _data_dir(data_dir)
    _validate_storage_workflow_id(workflow_id, WorkflowApprovalError)
    store = ApprovalStore(base)
    request = store.get(workflow_id, run_id, node_id)
    if request is None or request.decision is not None:
        raise WorkflowApprovalError("Pending approval not found")
    _cancel_approval_for_finished_rattish_run(base, store, request)
    if request.options and choice is None and decision == "rejected":
        raise WorkflowApprovalError(
            "This approval has options instead of Reject; choose one of: "
            + ", ".join(request.options)
            + "."
        )
    try:
        decided = store.decide(
            workflow_id,
            run_id,
            node_id,
            None if request.options else decision,  # type: ignore[arg-type]
            decided_by=decided_by,
            notes=notes,
            choice=choice,
            reply=reply,
        )
    except ValueError as exc:
        raise WorkflowApprovalError(str(exc)) from exc
    persisted_decision = store.get(workflow_id, run_id, node_id) or decided
    # A Rattish run waits on its gate and goes on by itself; "resumed" stays for
    # clients that read it, such as released phones.
    return {
        "workflowId": workflow_id,
        "approval": _approval_request_payload(persisted_decision),
        "resumed": False,
    }


def _cancel_approval_for_finished_rattish_run(
    base: Path, store: ApprovalStore, request: ApprovalRequest
) -> bool:
    """Close a pending gate whose Rattish run already ended, e.g. after a crash."""
    if request.status != "pending":
        return False
    try:
        path = _rattish_run_artifact_path(base, request.workflow_id, request.run_id)
        status = _read_rattish_run_artifact(path).get("status")
    except WorkflowLogError:
        return False
    if status == "running":
        return False
    return store.cancel(
        request.workflow_id,
        request.run_id,
        request.node_id,
        f"The workflow run ended ({status}).",
    )


def workflow_run_events_payload(
    workflow_id: str,
    run_id: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    path = _rattish_run_artifact_path(base, workflow_id, run_id)
    payload = _rattish_artifact_ui_payload(_read_rattish_run_artifact(path), path)
    return {
        "workflowId": workflow_id,
        "runId": run_id,
        "logPath": str(path),
        "status": payload["status"],
        "runEvents": payload["runEvents"],
        "runNodes": payload["runNodes"],
    }


def _rattish_run_directory(base: Path, workflow_id: str) -> Path:
    safe_id = _validate_storage_workflow_id(workflow_id, WorkflowLogError)
    legacy_directory = base / "radish" / "runs" / safe_id
    directory = workflow_owned_directory(safe_id, base, "logs")
    if directory is None:
        return legacy_directory
    migrate_legacy_directory(legacy_directory, directory)
    return directory


def _rattish_run_artifact_paths(base: Path, workflow_id: str) -> list[Path]:
    """The workflow's run records, newest first, including a known one the folder listing
    missed while its run replaced it (workflow_runs.missed_records)."""
    from gofer.ui.workflow_runs import missed_records

    directory = _rattish_run_directory(base, workflow_id)
    if not directory.exists():
        return []
    stamped: list[tuple[int, str, Path]] = []
    listed = list(directory.glob("*.json"))
    for path in listed:
        try:
            if path.is_file():
                stamped.append((path.stat().st_mtime_ns, path.name, path))
        except OSError:
            continue
    for location, stat in missed_records(base, workflow_id, directory, [p.name for p in listed]):
        path = Path(location)
        stamped.append((stat.st_mtime_ns, path.name, path))
    return [path for _, _, path in sorted(stamped, reverse=True)]


def _read_rattish_run_artifact(path: Path, *, settled: bool = False) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowLogError(f"Could not read Rattish run artifact {path.name}: {exc}") from exc
    if not isinstance(document, dict) or document.get("run_artifact_version") != 1:
        raise WorkflowLogError(f"Invalid Rattish run artifact {path.name}.")
    if document.get("status") == "running":
        status = effective_run_status(document)
        if status == "interrupted" and document.get("runner_pid") == os.getpid() and not settled:
            # A run here saves its final record before it stops counting as active,
            # so it may have finished after the read. Its record says so now.
            return _read_rattish_run_artifact(path, settled=True)
        folded = fold_run_journal(path) if status == "interrupted" else None
        if folded is not None:
            folded["status"] = effective_run_status(folded)
            return folded
        document["status"] = status
    return document


def _rattish_resume_states(
    base: Path,
    workflow_id: str,
    documents: list[dict[str, Any]],
    *,
    children: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Whether each run can be resumed now, and which nodes a resume runs again.

    ``children`` maps a run ID to the run that resumed it. Without it, only
    ``documents`` are searched for resumed runs.
    """
    if not documents:
        return {}
    registered = _registered_rattish_workflow(workflow_id, base)
    ir = None
    if registered is not None:
        try:
            ir = compile_rattish_file(
                registered.entrypoint,
                data_dir=base,
                workflow_id=registered.workflow_id,
                project_root=registered.project_root,
            ).ir
        except (RattishArtifactError, RattishError, OSError):
            ir = None
    if children is None:
        # A newer run always resumes an older one, so its record is among these.
        children = {
            str(resumed_run_id(document)): str(document["run_id"])
            for document in documents
            if resumed_run_id(document)
        }
    states = {}
    for document in documents:
        run_id = str(document["run_id"])
        reason = resume_refusal(document, ir)
        if reason is None and run_id in children:
            reason = (
                f"This run was already resumed as run {children[run_id]}. Resume that run instead."
            )
        states[run_id] = {
            "allowed": reason is None,
            "reason": reason or "",
            "rerunNodes": resume_rerun_nodes(document) if reason is None else [],
            # Password inputs the run didn't save; Resume asks for them again.
            "needsInputs": withheld_input_names(document) if reason is None else [],
        }
    return states


def _rattish_run_artifact_path(base: Path, workflow_id: str, run_id: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id) is None:
        raise WorkflowLogError(f"Invalid run ID {run_id!r}.")
    candidate = _rattish_run_directory(base, workflow_id) / f"{run_id.removesuffix('.json')}.json"
    if not candidate.exists() or not candidate.is_file():
        raise WorkflowLogError(f"Run log {run_id!r} not found")
    return candidate


def _rattish_ui_run_status(document: Mapping[str, Any]) -> str:
    status = document["status"]
    if status == "passed":
        return "success"
    if status == "interrupted":
        return "disconnected"
    return str(status) if status in {"running", "stopped"} else "error"


def _rattish_stop_requested(document: Mapping[str, Any], path: Path) -> bool:
    """A running run some process has asked to stop; it shows until the run ends."""
    return document["status"] == "running" and run_stop_requested(path)


def _usage_waits_for(base: Path, documents: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Runs waiting for a usage limit to reset, read only when a listed run failed."""
    if not any(document.get("status") == "failed" for document in documents):
        return {}
    from gofer.rattish.triggers import usage_waits_by_run

    return usage_waits_by_run(base)


def _rattish_run_summary(
    document: Mapping[str, Any],
    path: Path,
    *,
    awaiting_approval: bool = False,
    resume: Mapping[str, Any] | None = None,
    usage_waits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One run in a workflow's Runs list. ``usage_waits`` holds the trigger store's
    runs waiting for a usage limit to reset, by run ID (``usage_waits_by_run``)."""
    from gofer.rattish.triggers import usage_wait_payload

    wait = (usage_waits or {}).get(str(document["run_id"]))
    return {
        "id": str(document["run_id"]),
        "logPath": str(path),
        "status": _rattish_ui_run_status(document),
        "awaitingApproval": awaiting_approval and document["status"] == "running",
        "stopRequested": _rattish_stop_requested(document, path),
        "startedAt": document.get("started_at"),
        "finishedAt": None if document["status"] == "running" else document.get("finished_at"),
        "durationSeconds": float(document.get("duration_ms", 0)) / 1000,
        "error": run_error_message(document),
        "triggerType": "manual",
        "startedBy": rattish_run_started_by(document),
        "hasTriggerReplay": False,
        "resumedFrom": (document.get("resumed_from") or {}).get("run_id"),
        "rerunOf": document.get("rerun_of"),
        "replayedFrom": _replayed_from_summary(document),
        "usage": _rattish_run_usage(document, nodes=False),
        "rerun": rattish_rerun_state(document),
        "usageLimitWait": usage_wait_payload(wait) if wait is not None else None,
        **({"resume": dict(resume)} if resume is not None else {}),
    }


def _replayed_from_summary(document: Mapping[str, Any]) -> dict[str, Any] | None:
    """For a run started from a node: the run it reused, the node and the reused nodes."""
    replayed = document.get("replayed_from")
    if not isinstance(replayed, Mapping) or not replayed.get("run_id"):
        return None
    return {
        "runId": str(replayed["run_id"]),
        "nodeId": str(replayed.get("node_id") or ""),
        "reusedNodes": [str(item) for item in replayed.get("reused_nodes") or []],
        "reusedRuns": int(replayed.get("reused_runs") or 0),
    }


def rattish_rerun_state(document: Mapping[str, Any]) -> dict[str, Any]:
    """Whether a run can be run again with its saved inputs, and what must be given again."""
    reason = rerun_refusal(document)
    return {
        "allowed": reason is None,
        "reason": reason or "",
        "needsInputs": withheld_input_names(document) if reason is None else [],
    }


def rattish_run_started_by(document: Mapping[str, Any]) -> str:
    """How a run started. Runs saved before this was recorded count trigger events."""
    return run_started_by(document)


def _rattish_run_usage(document: Mapping[str, Any], *, nodes: bool = True) -> Any:
    """A run's tokens and estimated cost, or None for a run saved before usage tracking."""
    from gofer.rattish.run_usage import usage_payload

    payload = usage_payload(document.get("usage"))
    if payload is not None and not nodes:
        payload.pop("nodes", None)
    return payload


def latest_workflow_log_payload(workflow_id: str, data_dir: Path | None = None) -> dict[str, Any]:
    base = _data_dir(data_dir)
    _validate_storage_workflow_id(workflow_id, WorkflowLogError)
    rattish_paths = _rattish_run_artifact_paths(base, workflow_id)
    if rattish_paths:
        # One unreadable record must not hide the newest readable run.
        for skipped, path in enumerate(rattish_paths):
            try:
                document = _read_rattish_run_artifact(path)
            except WorkflowLogError:
                continue
            return {**_rattish_artifact_ui_payload(document, path), "skippedRuns": skipped}
        return {
            "workflowId": workflow_id,
            "logPath": None,
            "logText": "",
            "skippedRuns": len(rattish_paths),
        }
    return {"workflowId": workflow_id, "logPath": None, "logText": ""}


def list_workflow_run_logs_payload(
    workflow_id: str,
    data_dir: Path | None = None,
    *,
    offset: int = 0,
    limit: int | None = None,
    status: str | None = None,
    trigger_type: str | None = None,
    search: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    # Filter and page cached summaries, then read in full only the runs returned.
    # Unreadable records are skipped, as one bad run must not hide the whole list.
    entries, skipped_runs = workflow_run_summaries(
        base, workflow_id, _rattish_run_directory(base, workflow_id)
    )
    # A run blocked on an approval gate is still running, but the UI says why it waits.
    awaiting_ids = (
        {request.run_id for request in ApprovalStore(base).list_pending(workflow_id)}
        if any(entry.status == "running" for entry in entries)
        else set()
    )
    # A newer run always resumes an older one, so the oldest resume wins.
    children = {
        str(entry.summary["resumed_from"]): entry.run_id
        for entry in entries
        if entry.summary.get("resumed_from") and entry.status not in NEVER_STARTED_STATUSES
    }
    listed = entries
    if status:
        listed = [entry for entry in listed if _rattish_ui_run_status(entry.summary) == status]
    if trigger_type:
        # Rattish runs report how they started in startedBy; triggerType is always manual.
        listed = listed if trigger_type == "manual" else []
    if search:
        query = search.lower()
        listed = [
            entry
            for entry in listed
            if query in entry.run_id.lower()
            or query in _rattish_ui_run_status(entry.summary).lower()
        ]
    if started_after or started_before:
        listed = [
            entry
            for entry in listed
            if _run_started_at_in_range(
                {"startedAt": entry.summary.get("started_at")}, started_after, started_before
            )
        ]
    total = len(listed)
    page_offset = max(0, offset)
    page_limit = max(0, limit) if limit is not None else None
    page = (
        listed[page_offset : page_offset + page_limit]
        if page_limit is not None
        else listed[page_offset:]
    )
    documents = []
    for entry in page:
        try:
            documents.append((_read_rattish_run_artifact(entry.path), entry.path))
        except WorkflowLogError:
            # Removed or damaged since its summary was checked.
            skipped_runs += 1
    resume = _rattish_resume_states(
        base, workflow_id, [document for document, _ in documents], children=children
    )
    usage_waits = _usage_waits_for(base, [document for document, _ in documents])
    selected = [
        _rattish_run_summary(
            document,
            path,
            awaiting_approval=str(document["run_id"]) in awaiting_ids,
            resume=resume[str(document["run_id"])],
            usage_waits=usage_waits,
        )
        for document, path in documents
    ]
    return {
        "workflowId": workflow_id,
        "runs": selected,
        "pagination": {"offset": page_offset, "limit": page_limit, "total": total},
        "skippedRuns": skipped_runs,
    }


def recent_rattish_runs_payload(
    workflow_id: str, data_dir: Path | None = None, *, limit: int = 20, with_resume: bool = False
) -> dict[str, Any]:
    """The newest Rattish run summaries, reading only those runs' artifacts.

    Also names the nodes each running run waits on for an approval. Unreadable
    artifacts are skipped, as a phone poll should not fail on one bad run.
    """
    base = _data_dir(data_dir)
    _validate_storage_workflow_id(workflow_id, WorkflowLogError)
    paths = _rattish_run_artifact_paths(base, workflow_id)
    documents = []
    for path in paths[: max(0, limit)]:
        try:
            documents.append((_read_rattish_run_artifact(path), path))
        except WorkflowLogError:
            continue
    waiting: dict[str, list[str]] = {}
    if any(document["status"] == "running" for document, _ in documents):
        for request in ApprovalStore(base).list_pending(workflow_id):
            waiting.setdefault(request.run_id, []).append(request.node_id)
    resume = (
        _rattish_resume_states(base, workflow_id, [document for document, _ in documents])
        if with_resume
        else {}
    )
    usage_waits = _usage_waits_for(base, [document for document, _ in documents])
    return {
        "workflowId": workflow_id,
        "runs": [
            _rattish_run_summary(
                document,
                path,
                awaiting_approval=document["run_id"] in waiting,
                resume=resume.get(str(document["run_id"])),
                usage_waits=usage_waits,
            )
            for document, path in documents
        ],
        "total": len(paths),
        "awaitingNodes": waiting,
    }


def rattish_resume_state_payload(
    workflow_id: str, run_id: str, data_dir: Path | None = None
) -> dict[str, Any]:
    """Whether one Rattish run can be resumed now, as run lists report it."""
    base = _data_dir(data_dir)
    path = _rattish_run_artifact_path(base, workflow_id, run_id)
    state = _rattish_resume_states(base, workflow_id, [_read_rattish_run_artifact(path)])[run_id]
    child = resumed_child(path.parent, run_id) if state["allowed"] else None
    if child is not None:
        state = {
            "allowed": False,
            "reason": f"This run was already resumed as run {child}. Resume that run instead.",
            "rerunNodes": [],
            "needsInputs": [],
        }
    return state


def _run_started_at_in_range(
    run: dict[str, Any],
    started_after: datetime | None,
    started_before: datetime | None,
) -> bool:
    started = _parse_run_datetime(run.get("startedAt"))
    if started is None:
        return False
    normalized_after = _normalize_datetime(started_after)
    normalized_before = _normalize_datetime(started_before)
    if normalized_after and started < normalized_after:
        return False
    return not (normalized_before and started > normalized_before)


def workflow_run_log_payload(
    workflow_id: str,
    run_id: str,
    data_dir: Path | None = None,
    *,
    offset: int | None = None,
    limit: int | None = None,
    tail_bytes: int | None = None,
    include_details: bool = True,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    path = _rattish_run_artifact_path(base, workflow_id, run_id)
    document = _read_rattish_run_artifact(path)
    payload = _rattish_artifact_ui_payload(document, path)
    if not include_details:
        return {
            "workflowId": workflow_id,
            "runId": run_id,
            "logPath": str(path),
            "startedAt": document.get("started_at"),
            "status": payload["status"],
            "logText": payload["logText"],
            "runEvents": payload["runEvents"],
            "runNodes": payload["runNodes"],
            "usage": payload["usage"],
        }
    return payload


def prune_workflow_run_logs_payload(
    workflow_id: str,
    data_dir: Path | None = None,
    *,
    keep_last: int | None = None,
    keep_days: int | None = None,
    keep_failed_days: int | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    if keep_last is None and keep_days is None and keep_failed_days is None:
        saved = retention_settings_payload(base, workflow_id)
        if not saved["readable"]:
            raise WorkflowLogError(UNREADABLE_RETENTION_MESSAGE)
        saved_settings = saved["settings"]
        keep_last = saved_settings.get("keepLast")
        keep_days = saved_settings.get("keepDays")
        keep_failed_days = saved_settings.get("keepFailedDays")
    rattish_directory = _rattish_run_directory(base, workflow_id)
    if not _rattish_run_artifact_paths(base, workflow_id):
        return {
            "workflowId": workflow_id,
            "dryRun": dry_run,
            "runs": [],
            "deleted": [],
            "skippedRuns": 0,
        }
    # Shared with automatic retention, so both protect the same runs.
    try:
        result = prune_run_records(
            base,
            workflow_id,
            rattish_directory,
            keep_last=keep_last,
            keep_days=keep_days,
            keep_failed_days=keep_failed_days,
            dry_run=dry_run,
        )
    except OSError as exc:
        raise WorkflowLogError(f"Could not prune runs of {workflow_id}: {exc}") from exc
    return {
        "workflowId": workflow_id,
        "dryRun": dry_run,
        "runs": [
            _rattish_run_summary(record.document, record.path) for record in result.candidates
        ],
        "deleted": result.deleted,
        "skippedRuns": result.skipped,
    }


def _check_retention_settings_path(base: Path) -> None:
    # The settings folder must not lead outside the data folder.
    _safe_path(base, "settings", RETENTION_SETTINGS_FILE, error_cls=WorkflowLogError)


def retention_settings_payload(
    data_dir: Path | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    if workflow_id:
        _validate_storage_workflow_id(workflow_id, WorkflowLogError)
    _check_retention_settings_path(base)
    document = read_retention_document(base)
    readable = document is not None
    global_settings = coerce_retention_settings((document or {}).get("global"))
    workflows = retention_workflows(document or {})
    workflow_settings = (
        coerce_retention_settings(workflows.get(workflow_id))
        if workflow_id and workflow_id in workflows
        else None
    )
    return {
        "workflowId": workflow_id,
        "global": global_settings,
        "workflow": workflow_settings,
        "settings": workflow_settings or global_settings,
        "readable": readable,
        "warning": None if readable else UNREADABLE_RETENTION_MESSAGE,
    }


def update_retention_settings_payload(
    data_dir: Path | None = None,
    *,
    workflow_id: str | None = None,
    settings: dict[str, Any],
) -> dict[str, Any]:
    base = _data_dir(data_dir)
    if workflow_id:
        _validate_storage_workflow_id(workflow_id, WorkflowLogError)
    _check_retention_settings_path(base)
    save_retention_settings(base, workflow_id, settings)
    return retention_settings_payload(base, workflow_id)


def _parse_run_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _normalize_datetime(parsed)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@overload
def _model_dump(model: None) -> None: ...


@overload
def _model_dump(model: BaseModel) -> dict[str, Any]: ...


def _model_dump(model: BaseModel | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return model.model_dump(mode="json", exclude_none=True, by_alias=True)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", value.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "workflow"


def _registered_rattish_workflow(
    workflow_id: str,
    data_dir: Path,
) -> RegisteredWorkflow | None:
    try:
        validate_workflow_id(workflow_id)
    except ValueError:
        return None
    canonical = workflow_id.lower()
    try:
        matches = [
            workflow
            for workflow in list_registered_workflows(registry_dir=data_dir)
            if workflow.workflow_id.lower() == canonical
        ]
    except RattishWorkspaceError:
        return None
    return matches[0] if len(matches) == 1 else None


def _merged_run_inputs(
    parameters: dict[str, Any] | None,
    inputs: dict[str, Any] | None,
    error_cls: type[ValueError],
) -> dict[str, Any]:
    overlap = set(parameters or {}) & set(inputs or {})
    if overlap:
        names = ", ".join(sorted(overlap))
        raise error_cls(f"Inputs cannot be supplied twice: {names}")
    return {**(parameters or {}), **(inputs or {})}


def _rattish_workflow_inputs(items: list[dict[str, Any]]) -> dict[str, Any]:
    from gofer.rattish.file_inputs import file_input_specs

    files = file_input_specs({"workflow": {"inputs": items}})
    result: dict[str, Any] = {}
    for item in items:
        schema = item["schema"]
        schema_type = schema.get("type", "string")
        if isinstance(schema_type, list):
            schema_type = next((value for value in schema_type if value != "null"), "string")
        choices = schema.get("enum")
        spec = files.get(item["name"])
        result[item["name"]] = {
            "type": "file"
            if spec is not None
            else "enum"
            if isinstance(choices, list)
            else schema_type,
            "required": bool(item["required"]),
            **({"choices": choices} if isinstance(choices, list) else {}),
            **({"default": item["default"]["value"]} if item["default"]["present"] else {}),
            **(
                {"file": {"accept": list(spec.accept), "maxSize": spec.max_size}}
                if spec is not None
                else {}
            ),
            "schema": schema,
        }
    return result


def _rattish_plan_payload(
    workflow: RegisteredWorkflow,
    data_dir: Path,
    *,
    trigger_context: dict[str, Any] | None,
    error_cls: type[ValueError],
) -> dict[str, Any]:
    try:
        compiled = compile_rattish_file(
            workflow.entrypoint,
            data_dir=data_dir,
            workflow_id=workflow.workflow_id,
            project_root=workflow.project_root,
        )
        deployment = run_preflight(compiled.ir, data_dir=data_dir)
    except (RattishArtifactError, RattishError, OSError) as exc:
        raise error_cls(str(exc)) from exc

    diagnostics = [
        *compiled.diagnostics,
        *(diagnostic.to_json() for diagnostic in deployment.diagnostics),
    ]
    error_diagnostics = [
        diagnostic for diagnostic in diagnostics if diagnostic.get("severity") == "error"
    ]
    warning_diagnostics = [
        diagnostic for diagnostic in diagnostics if diagnostic.get("severity") == "warning"
    ]
    nodes = compiled.ir["nodes"]
    providers = []
    for node in nodes:
        resolution = node["resolutions"]["provider"]
        if resolution is None:
            continue
        unavailable = any(
            diagnostic.get("details", {}).get("node") == node["id"]
            and diagnostic.get("severity") == "error"
            for diagnostic in diagnostics
        )
        providers.append(
            {
                "agentId": node["id"],
                "subscription": resolution["provider_id"],
                "binary": resolution["provider_id"],
                "available": not unavailable,
                "workingDir": node["configuration"].get("working_dir", "."),
                "profile": resolution["profile"],
                "model": resolution["model"],
                "timeout": (
                    node["execution"]["timeout_ms"] / 1000
                    if node["execution"]["timeout_ms"] is not None
                    else None
                ),
                "extraPaths": node["configuration"].get("extra_paths", []),
            }
        )

    destructive_effects = {"command", "filesystem_write", "network", "state_write"}
    destructive_actions = [
        f"{node['id']}: "
        + ", ".join(
            effect.replace("_", " ") for effect in node["effects"] if effect in destructive_effects
        )
        for node in nodes
        if destructive_effects & set(node["effects"])
    ]
    required_secrets = sorted(
        {
            str(diagnostic["details"]["secret"])
            for diagnostic in diagnostics
            if diagnostic.get("details", {}).get("secret")
        }
    )
    bindings_by_node = {node["id"]: _rattish_plan_bindings(node) for node in nodes}
    nodes_by_id = {node["id"]: node for node in nodes}
    return {
        "kind": "rattish",
        "runnable": bool(nodes) and deployment.ready,
        "blockingDiagnostics": [item["message"] for item in error_diagnostics],
        "warnings": [item["message"] for item in warning_diagnostics],
        "destructiveActions": destructive_actions,
        "providerRequirements": providers,
        "requiredSecrets": required_secrets,
        "bindings": [binding for node in nodes for binding in bindings_by_node[node["id"]]],
        "triggerContext": trigger_context or {},
        "generations": [
            {
                "index": index,
                "nodes": [
                    {
                        "id": node["id"],
                        "label": node["id"],
                        "type": node["type"],
                        "detail": _rattish_node_detail(node, data_dir),
                        "sideEffects": [effect.replace("_", " ") for effect in node["effects"]],
                        "workingDir": node["configuration"].get("working_dir"),
                        "bindings": bindings_by_node[node["id"]],
                    }
                    for node in (nodes_by_id[node_id] for node_id in generation)
                ],
            }
            for index, generation in enumerate(_rattish_plan_generations(nodes))
        ],
        "diagnostics": diagnostics,
        "inputs": _rattish_workflow_inputs(compiled.ir["workflow"]["inputs"]),
        "compilationFingerprint": compiled.ir["source"]["compilation_fingerprint"],
    }


def _rattish_plan_generations(nodes: list[dict[str, Any]]) -> list[list[str]]:
    """Order nodes by when they can first run: start nodes, then routes and needs."""
    predecessors = {
        node["id"]: {
            *node["readiness"]["needs"],
            *node["readiness"]["incoming_route_sources"],
        }
        - {node["id"]}
        for node in nodes
    }
    level: dict[str, int] = {node["id"]: 0 for node in nodes if not node["readiness"]["needs"]}
    while len(level) < len(nodes):
        pending = [node_id for node_id in predecessors if node_id not in level]
        ready = [node_id for node_id in pending if predecessors[node_id] <= level.keys()]
        if not ready:
            # Cycles have no complete order, so place them after a predecessor that runs.
            ready = [node_id for node_id in pending if predecessors[node_id] & level.keys()]
        if not ready:
            # Nothing routes to these nodes, so they never run. List them last.
            last = max(level.values(), default=-1) + 1
            level.update({node_id: last for node_id in pending})
            break
        level.update(
            {
                node_id: 1 + max(level[item] for item in predecessors[node_id] if item in level)
                for node_id in ready
            }
        )
    generations: list[list[str]] = [[] for _ in range(max(level.values(), default=-1) + 1)]
    for node in nodes:
        generations[level[node["id"]]].append(node["id"])
    return [generation for generation in generations if generation]


def _rattish_plan_bindings(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    bindings = []
    for binding in node["bindings"]:
        source = binding["source"]
        reference = source.get("reference") if source["kind"] == "reference" else None
        if reference is not None:
            parts = [reference["root"], reference["symbol"]]
            if reference.get("channel"):
                parts.append(reference["channel"])
            expression = ".".join(str(part) for part in parts if part is not None) + "".join(
                f".{item['value']}" if item["kind"] == "member" else f"[{item['value']}]"
                for item in reference["path"]
            )
            producer = reference["symbol"] if reference["root"] == "node" else reference["root"]
        elif source["kind"] == "literal":
            expression = json.dumps(source["value"], ensure_ascii=False, default=str)
            producer = "literal"
        else:
            expression = "expression"
            producer = "expression"
        delivery = binding.get("delivery") or {}
        bindings.append(
            {
                "id": f"{node['id']}:{binding['name']}",
                "destinationNode": node["id"],
                "destinationField": binding["name"],
                "expression": expression,
                "producer": producer,
                "sourceType": _rattish_schema_type(binding.get("source_schema")),
                "destinationType": _rattish_schema_type(binding.get("destination_schema")),
                "status": "valid",
                "resolutionPhase": (
                    "runtime" if reference is not None and reference["root"] == "node" else "start"
                ),
                "coercion": "string" if delivery.get("kind") == "environment" else None,
                "readiness": None,
            }
        )
    return bindings


def _rattish_schema_type(schema: Any) -> str:
    if isinstance(schema, Mapping):
        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            return " | ".join(str(item) for item in schema_type)
        if schema_type:
            return str(schema_type)
    return "any"


def _rattish_node_detail(node: dict[str, Any], data_dir: Path) -> str:
    configuration = node["configuration"]
    if node["type"] == "rem-task":
        from gofer.ui.rem_workflow import live_tasks

        task_id = str(configuration["task_id"])
        try:
            title = next((t["title"] for t in live_tasks(data_dir) if t["id"] == task_id), "")
        except (OSError, sqlite3.Error):
            title = ""
        mode = "runs and waits for" if configuration.get("wait", True) else "starts"
        return f"{mode} Rem task {title or task_id}"
    if node["type"] == "rem-prompt":
        mode = "may start work" if configuration.get("allow_actions") else "answer only"
        return f"asks Rem ({mode}): {str(configuration.get('prompt') or '')[:120]}"
    for field in (
        "command",
        "script_path",
        "path",
        "source_path",
        "target",
        "url",
        "workflow_id",
        "workflow_path",
        "prompt_path",
    ):
        value = configuration.get(field)
        if value not in (None, ""):
            return str(value)
    return str(node["runtime_handler"])


def _rattish_run_payload(
    result: RattishRunResult, *, include_log_text: bool = True
) -> dict[str, Any]:
    payload = _rattish_artifact_ui_payload(result.document, result.path)
    if not include_log_text:
        # logText repeats rattishRun as indented text; clients can read logPath instead.
        payload.pop("logText")
    return payload


def _provider_app(provider: Any) -> str:
    """``Claude Code 2.3.1`` for a node's provider with a recorded app version, else ""."""
    from gofer.core.provider_versions import app_line

    if not isinstance(provider, Mapping):
        return ""
    version = provider.get("provider_version")
    name = provider.get("provider")
    return app_line(str(name), version) if isinstance(version, str) and version and name else ""


def _rattish_run_graph_snapshot(document: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project the executed IR without consulting today's source or contracts."""
    ir = document.get("compiled_snapshot")
    if not isinstance(ir, Mapping) or ir.get("ir_version") != 1:
        return None
    projected_nodes: list[dict[str, Any]] = []
    projected_edges: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for index, node in enumerate(ir["nodes"]):
        node_id = node["id"]
        projection = {
            "projectionId": node_id,
            "id": node_id,
            "label": node_id,
            "type": node["type"],
            "status": "valid",
            "contractAvailable": False,
            "contract": node["contract"],
            "configuration": node["configuration"],
            "execution": node["execution"],
            "needs": node["readiness"]["needs"],
            "bindings": node["bindings"],
            "sourceSpan": node["source_span"],
            "diagnostics": [],
            "declarationIndex": index,
        }
        projected_nodes.append(projection)
        node_type = node["type"].replace("-", "_")
        nodes.append(
            {
                "id": node_id,
                "label": node_id,
                "type": node_type,
                "operation": {"type": node_type, **node["configuration"]},
                "x": (index % 4) * 280,
                "y": (index // 4) * 160,
                "rattishStatus": "valid",
                "rattish": projection,
            }
        )
        for route_index, route in enumerate(node["routes"]):
            mode = "when" if route["mode"] == "conditional" else route["mode"]
            projected_edge = {
                "id": f"{node_id}:route:{route_index}",
                "from": node_id,
                "to": route["target"],
                "mode": mode,
                "predicate": route["predicate"],
                "predicateSource": None,
                "status": "valid",
            }
            projected_edges.append(projected_edge)
            edges.append(dict(projected_edge))
    return {
        "id": ir["workflow"]["id"],
        "workflowId": ir["workflow"]["id"],
        "name": ir["workflow"]["name"],
        "sourceFormat": "rattish",
        "readOnly": True,
        "sourcePath": document["source"]["path"],
        "projectRoot": ir["source"]["project_root"],
        "runId": document["run_id"],
        "sourceFingerprint": document["source"]["source_fingerprint"],
        "compilationFingerprint": document["source"]["compilation_fingerprint"],
        "nodes": nodes,
        "edges": edges,
        "graph": {"nodes": projected_nodes, "edges": projected_edges},
    }


_RATTISH_STATUS_SEVERITY = {"success": 0, "allowed_failure": 1, "error": 2}


def _rattish_activation_summary(counts: Mapping[str, int]) -> str | None:
    total = counts["total"]
    if total < 2:
        return None
    if counts["error"]:
        return f"{counts['error']} of {total} failed"
    if counts["allowed_failure"]:
        return f"{counts['allowed_failure']} of {total} failed (allowed)"
    return f"{total} runs succeeded"


def _rattish_artifact_ui_payload(document: Mapping[str, Any], path: Path) -> dict[str, Any]:
    run_events = _rattish_ui_events(document)
    completed_events = {
        (event["nodeId"], event["runNumber"]): event
        for event in run_events
        if event["type"] == "node_completed"
    }
    ui_status = _rattish_ui_run_status(document)
    node_outputs = {
        node_id: {
            "success": True,
            "output": json.dumps(output, ensure_ascii=False, default=str),
            "data": output,
        }
        for node_id, output in document["latest_node_outputs"].items()
    }
    run_nodes: dict[str, dict[str, Any]] = {}
    for record in document["runs"]:
        node_id = record["node_id"]
        completed_event = completed_events.get((node_id, record["run_number"]), {})
        output = record["output"]
        error = record["error"]
        success = record["outcome"] == "success"
        record_status = {"success": "success", "allowed_failure": "allowed_failure"}.get(
            record["outcome"], "error"
        )
        output_text = json.dumps(output, ensure_ascii=False, default=str)
        exit_code = output.get("exit_code") if isinstance(output, Mapping) else None
        stdout = output.get("stdout") if isinstance(output, Mapping) else None
        stderr = output.get("stderr") if isinstance(output, Mapping) else None
        error_message = error.get("message") if isinstance(error, Mapping) else None
        if (
            not success
            and exit_code
            and (
                not error_message
                or re.fullmatch(r"(?:Command|Script) exited with status \d+\.", error_message)
            )
        ):
            from gofer.rattish.runtime import command_failure_message

            error_message = command_failure_message(
                "Command", exit_code, stdout or "", stderr or ""
            )
            error = {**(error or {}), "message": error_message}
        if not success and error_message:
            # Timeline rows otherwise read only "Activation N failure."
            for event in run_events:
                if (
                    event.get("nodeId") == node_id
                    and event.get("type") == "node_completed"
                    and event.get("runNumber") == record["run_number"]
                ):
                    event["message"] = error_message

        node_outputs[node_id] = {
            "success": success,
            "outcome": record["outcome"],
            "output": output_text,
            "data": output,
            "exitCode": exit_code,
            "error": error_message,
        }
        duration_ms = record["duration_ms"]
        previous = run_nodes.get(node_id, {})
        previous_attempts = previous.get("attempts", [])
        counts = dict(
            previous.get("activationCounts")
            or {"total": 0, "success": 0, "allowed_failure": 0, "error": 0}
        )
        counts["total"] += 1
        counts[record_status] += 1
        run_nodes[node_id] = {
            "nodeId": node_id,
            "reused": bool(record.get("reused_from_run_id")),
            # The output came from the result cache: no provider call, 0 tokens and $0.
            "cached": bool(record.get("cached_at")),
            "cachedAt": record.get("cached_at"),
            "status": record_status,
            "outcome": record["outcome"],
            "startedAt": record["started_at"],
            "finishedAt": record["finished_at"],
            "durationMs": duration_ms,
            "durationSeconds": duration_ms / 1000,
            "exitCode": exit_code,
            "error": error,
            "message": error_message,
            "runNumber": record["run_number"],
            "activationLineageId": record["activation_lineage_id"],
            "activationGroupId": record["activation_group_id"],
            "attempts": [
                *previous_attempts,
                {
                    "attempt": completed_event.get("attempt", 1),
                    "runNumber": record["run_number"],
                    "fanOutItem": completed_event.get("fanOutItem"),
                    "durationSeconds": duration_ms / 1000,
                    "exitCode": exit_code,
                    "output": output_text,
                    "stdout": stdout,
                    "stderr": stderr,
                    "error": error_message,
                },
            ],
            "data": {"message": error_message},
            "activationCounts": counts,
            "activationSummary": _rattish_activation_summary(counts),
            # Which provider answered (or last failed) and each switch to a fallback.
            "provider": record.get("provider") or previous.get("provider"),
            # "Claude Code 2.3.1": the provider app version the step started, when recorded.
            "providerApp": _provider_app(record.get("provider") or previous.get("provider")),
            "fallbacks": [*previous.get("fallbacks", []), *(record.get("fallbacks") or [])],
        }
        if (
            _RATTISH_STATUS_SEVERITY.get(str(previous.get("status")), -1)
            > _RATTISH_STATUS_SEVERITY[record_status]
        ):
            # A node card summarizes every activation, so a later success must not hide
            # an earlier failure.
            for field_name in ("status", "outcome", "exitCode", "error", "message", "data"):
                run_nodes[node_id][field_name] = previous[field_name]
    active: dict[tuple[str, int], dict[str, Any]] = {}
    for event in run_events:
        node_id = event["nodeId"]
        if node_id == "workflow":
            continue
        key = (node_id, event["runNumber"])
        if event["status"] in {"started", "retried"}:
            active[key] = event
        else:
            active.pop(key, None)
        if event["status"] == "stopped" and run_nodes.get(node_id, {}).get("status") != "error":
            run_nodes.setdefault(node_id, {"nodeId": node_id, "attempts": []}).update(
                status="stopped", finishedAt=event["occurredAt"]
            )
    for (node_id, _), event in active.items():
        run_nodes.setdefault(node_id, {"nodeId": node_id, "attempts": []}).update(
            status=event["status"]
            if ui_status == "running"
            else ("disconnected" if ui_status == "disconnected" else "stopped"),
            startedAt=event["occurredAt"],
            finishedAt=None,
            durationMs=None,
            durationSeconds=None,
            runNumber=event["runNumber"],
            activationLineageId=event["activationLineageId"],
            activationGroupId=event["activationGroupId"],
        )
    usage = _rattish_run_usage(document)
    for node_id, node_usage in ((usage or {}).get("nodes") or {}).items():
        if node_id in run_nodes:
            run_nodes[node_id]["usage"] = node_usage
    return {
        "workflowId": document["workflow"]["id"],
        "runId": document["run_id"],
        "success": None
        if document["status"] in {"running", "interrupted"}
        else document["status"] == "passed",
        "status": ui_status,
        "stopRequested": _rattish_stop_requested(document, path),
        "logPath": str(path),
        "logText": json.dumps(document, ensure_ascii=False, indent=2, default=str),
        "nodeOutputs": node_outputs,
        "runNodes": run_nodes,
        "runEvents": run_events,
        "outputs": document["outputs"],
        "diagnostics": document["diagnostics"],
        "error": document["error"],
        "rattishRun": document,
        "graphSnapshot": _rattish_run_graph_snapshot(document),
        "resumedFrom": (document.get("resumed_from") or {}).get("run_id"),
        "rerunOf": document.get("rerun_of"),
        "replayedFrom": _replayed_from_summary(document),
        "usage": usage,
        "worktree": _worktree_summary(document),
    }


def _worktree_summary(document: Mapping[str, Any]) -> dict[str, Any] | None:
    from gofer.rattish.run_worktrees import worktree_summary

    return worktree_summary(document)


def _rattish_event_message(
    event: Mapping[str, Any], error_message: str | None = None, *, reused_from: str | None = None
) -> str:
    event_type = str(event.get("type") or "event")
    if event.get("message"):
        return str(event["message"])
    if event_type == "node_completed" and event.get("reused"):
        source = f" from run {reused_from}" if reused_from else " from an earlier run"
        return (
            f"Activation {event.get('run_number')} reused its saved result{source} "
            "(not run again, no model call, $0)."
        )
    if event_type == "node_started":
        return f"Activation {event.get('run_number')} started."
    if event_type == "node_retried":
        return f"Activation {event.get('run_number')} retrying."
    if event_type == "node_stopped":
        return f"Activation {event.get('run_number')} stopped."
    if event_type == "node_completed" and event.get("cached"):
        return (
            f"Activation {event.get('run_number')} used a cached result "
            "(no model call, 0 tokens, $0)."
        )
    if event_type == "node_completed":
        return f"Activation {event.get('run_number')} {event.get('outcome')}."
    if event_type == "workflow_completed":
        status = str(event.get("status"))
        label = _RATTISH_RUN_STATUS_LABELS.get(status, status)
        if status != "passed" and error_message:
            return f"Workflow {label}: {error_message}"
        return f"Workflow {label}."
    return "Workflow started."


_RATTISH_RUN_STATUS_LABELS = {
    "passed": "passed",
    "failed": "failed",
    "stopped": "stopped",
    "preflight_failed": "failed preflight",
    "invalid_inputs": "rejected its inputs",
    "interrupted": "was interrupted",
}


def _rattish_ui_events(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    runs = {
        (record["node_id"], record["run_number"]): record for record in document.get("runs", [])
    }
    result: list[dict[str, Any]] = []
    attempts: dict[tuple[str | None, int | None], int] = {}
    run_error = document.get("error")
    run_error_message = (
        str(run_error.get("message") or "") if isinstance(run_error, Mapping) else ""
    ) or None
    for event in document["events"]:
        key = (event.get("node_id"), event.get("run_number"))
        record = runs.get(key)
        if event.get("attempt") is not None:
            attempts[key] = event["attempt"]
        group_id = event.get("activation_group_id") or (
            record.get("activation_group_id") if record else None
        )
        iteration = re.findall(r":iteration:(\d+)", group_id or "")
        result.append(
            {
                "sequence": event["sequence"],
                "type": event["type"],
                "occurredAt": event["at"],
                "nodeId": event.get("node_id") or "workflow",
                "attempt": attempts.get(key, 1) if event.get("node_id") else None,
                "runNumber": event.get("run_number"),
                "fanOutItem": {"index": int(iteration[-1])} if iteration else None,
                "outcome": event.get("outcome"),
                "cached": bool(event.get("cached")),
                # Copied from an earlier run by Resume or Run from here: no call, $0.
                "reused": bool(event.get("reused")),
                "status": (
                    "reused"
                    if event.get("reused")
                    and event.get("outcome") in {"success", "allowed_failure"}
                    else "cached"
                    if event.get("cached") and event.get("outcome") == "success"
                    else "retried"
                    if event["type"] in {"node_retried", "node_fell_back"}
                    else "truncated"
                    if event["type"] == "node_output_truncated"
                    else "stopped"
                    if event["type"] == "node_stopped" or event.get("status") == "stopped"
                    else "completed"
                    if event.get("outcome") == "success"
                    else "allowed_failure"
                    if event.get("outcome") == "allowed_failure"
                    else "failed"
                    if event.get("outcome") == "failure"
                    else "completed"
                    if event.get("status") == "passed"
                    else "failed"
                    if event.get("status")
                    else "started"
                ),
                "message": _rattish_event_message(
                    event,
                    run_error_message,
                    reused_from=record.get("reused_from_run_id") if record else None,
                ),
                "activationLineageId": event.get("activation_lineage_id")
                or (record.get("activation_lineage_id") if record else None),
                "activationGroupId": group_id,
                "durationMs": record.get("duration_ms")
                if record and event["type"] == "node_completed"
                else None,
                **(
                    {"errorCode": run_error.get("code")}
                    if event["type"] == "workflow_completed"
                    and event.get("status") != "passed"
                    and isinstance(run_error, Mapping)
                    else {}
                ),
            }
        )
    return result


def _data_dir(data_dir: Path | None) -> Path:
    return (data_dir or get_data_dir()).resolve()


def _assert_within_base(
    path: Path,
    base: Path,
    *,
    error_cls: type[ValueError],
) -> Path:
    resolved_base = base.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_base and not resolved_path.is_relative_to(resolved_base):
        raise error_cls("Invalid path")
    return resolved_path


def _safe_path(base: Path, *parts: str, error_cls: type[ValueError]) -> Path:
    return _assert_within_base(base.joinpath(*parts), base, error_cls=error_cls)


def _validate_storage_workflow_id(
    workflow_id: str,
    error_cls: type[ValueError],
) -> str:
    try:
        return validate_workflow_id(workflow_id)
    except ValueError as exc:
        raise error_cls("Invalid workflow id") from exc


def _validate_chat_prompt_id(prompt_id: str) -> None:
    if prompt_id == "workflow-assistant":
        return
    if prompt_id.startswith("workflow-assistant:"):
        thread_id = prompt_id.split(":", 1)[1]
        if CHAT_THREAD_ID_PATTERN.fullmatch(thread_id):
            return
        raise WorkflowUpdateError("Invalid chat thread id")
    _validate_storage_workflow_id(prompt_id, WorkflowUpdateError)


def _updated_at(path: Path | None) -> str:
    if path is None or not path.exists():
        return "Unknown"
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%b %d, %Y %H:%M")
