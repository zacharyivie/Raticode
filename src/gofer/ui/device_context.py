"""Translate authenticated desktop thread settings without mobile permission caps."""

from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from gofer.core.provider_permissions import provider_permission_args
from gofer.ui.device_chat import validate_context


def project_id(root: str) -> str:
    return str(uuid5(NAMESPACE_URL, "raticode-project:" + root))


def shared_context(
    metadata: dict[str, Any],
    workflow: dict[str, Any],
    capabilities: list[dict[str, Any]],
    project: Path | None,
) -> dict[str, Any]:
    permissions = {}
    saved = metadata.get("permissionsByProvider") or {}
    for capability in capabilities:
        name = capability["id"]
        mode = saved.get(name) or capability.get("defaultPermissionMode") or "default"
        provider_permission_args(name, mode)
        permissions[name] = mode
    provider = metadata.get("provider") or "codex"
    projects = []
    for item in (workflow.get("remThreads") or {}).get("projects", []):
        root = Path(item["root"]).resolve(strict=True)
        stat = root.stat()
        projects.append(
            {
                **item,
                "root": str(root),
                "id": project_id(str(root)),
                "identity": [stat.st_dev, stat.st_ino],
            }
        )
    global_scope = metadata.get("scopeMode") == "global" or not metadata.get("projectRoot")
    working = project or (Path(projects[0]["root"]) if projects and global_scope else None)
    context = {
        "title": str(metadata.get("title") or "New thread")[:160],
        "provider": provider,
        "model": metadata.get("model") or "cli-default",
        "permission_mode": permissions.get(provider),
        "provider_permissions": permissions,
        "project_id": project_id(str(project)) if project and not global_scope else None,
        "project_name": metadata.get("projectName")
        or next(
            (p["name"] for p in projects if project and p["root"] == str(project)),
            project.name if project else "Global",
        ),
        "resource_ids": [],
        "effort": metadata.get("effort"),
        "scope_mode": "global" if global_scope else "project",
        "desktop_parity": True,
        "projects": projects,
        "workflow": {
            **workflow,
            "remResources": metadata.get("resources") or workflow.get("remResources") or {},
            "remThreads": {
                "global": global_scope,
                "projects": projects,
                "spawned": bool(metadata.get("parentThreadId")),
            },
        },
    }
    if working:
        context["project_path"] = str(working)
        context = validate_context(context)
    return context
