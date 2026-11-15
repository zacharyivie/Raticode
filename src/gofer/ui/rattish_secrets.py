"""Studio API for workflow secrets: names and status only, never a saved value."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.rattish.workflow_secrets import (
    WorkflowSecretError,
    check_name,
    list_folder_workflow_secrets,
    list_workflow_secrets,
    remove_workflow_secret,
    save_workflow_secret,
    secret_status,
)

MAX_BODY_BYTES = 64 * 1024


def _secret_names(value: Any) -> set[str]:
    """Every ``secret["NAME"]`` reference in compiled IR bindings."""
    names: set[str] = set()
    if isinstance(value, dict):
        if value.get("root") == "secret" and isinstance(value.get("symbol"), str):
            names.add(value["symbol"])
        for item in value.values():
            names |= _secret_names(item)
    elif isinstance(value, list):
        for item in value:
            names |= _secret_names(item)
    return names


def workflow_secret_names(base: Path, workflow_id: str) -> list[str] | None:
    """Names one registered workflow reads, or None when it can't be compiled now."""
    from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.workspaces import RattishWorkspaceError, find_registered_workflow

    try:
        registered = find_registered_workflow(workflow_id, registry_dir=base)
        artifact = compile_rattish_file(
            registered.entrypoint,
            data_dir=base,
            workflow_id=registered.workflow_id,
            project_root=registered.project_root,
        )
    except (RattishArtifactError, RattishError, RattishWorkspaceError, OSError):
        return None
    names: set[str] = set()
    for node in artifact.ir.get("nodes", []):
        names |= _secret_names(node.get("bindings", []))
    return sorted(names)


def secrets_payload(base: Path, workflow_id: str | None = None) -> dict[str, Any]:
    """What Workflow settings > Secrets shows. ``used`` is None without a workflow.

    ``folder`` lists the names a headless backend's secrets folder holds files for.
    """
    used = None
    if workflow_id:
        names = workflow_secret_names(base, workflow_id)
        if names is not None:
            used = [{"name": name, "status": secret_status(name, base)} for name in names]
    return {
        "secrets": list_workflow_secrets(base),
        "used": used,
        "folder": list_folder_workflow_secrets(base),
    }


def save_secret_payload(base: Path, body: Any, workflow_id: str | None = None) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"name", "value"}:
        raise WorkflowSecretError("Send a secret name and value.")
    saved = save_workflow_secret(body.get("name"), body.get("value"), base)
    return {**secrets_payload(base, workflow_id), "saved": saved}


def remove_secret_payload(base: Path, name: str, workflow_id: str | None = None) -> dict[str, Any]:
    removed = remove_workflow_secret(check_name(name), base)
    return {**secrets_payload(base, workflow_id), "removed": removed}
