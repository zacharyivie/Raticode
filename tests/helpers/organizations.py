"""Shared helpers for organization tests.

Each helper calls ``OrganizationManager.call`` the way the board or a worker would, so
test files don't each carry their own copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.ui.organizations import OrganizationManager


def act(
    manager: OrganizationManager,
    org: dict[str, Any],
    action: str,
    actor: str = "user",
    **params: Any,
) -> Any:
    """Run one organization action as ``actor`` (the user unless named)."""
    return manager.call(
        org["projectRoot"], actor, {"organizationId": org["id"], "action": action, "params": params}
    )


def as_worker(
    manager: OrganizationManager,
    org: dict[str, Any],
    action: str,
    worker: str = "mira",
    **params: Any,
) -> Any:
    """Run one organization action as a bound worker in the current runtime generation."""
    state = manager.store.get("", org["id"])
    return manager.call(
        org["projectRoot"],
        f"employee:{worker}",
        {"organizationId": org["id"], "action": action, "params": params},
        employee_id=worker,
        bound_org=org["id"],
        generation=state["runtime"]["generation"],
    )


def assign(manager: OrganizationManager, org: dict[str, Any], eid: str) -> None:
    """Give an employee the organization's project, the board's way of assigning projects.

    Self-work, such as check-ins, needs an assigned project.
    """
    act(
        manager,
        org,
        "self_work_projects",
        employeeId=eid,
        projects=[org["projectRoot"]],
        expectedRevision=0,
    )


def assign_lead(manager: OrganizationManager, org: dict[str, Any]) -> None:
    """Give the lead the organization's project, so tests don't depend on following."""
    assign(manager, org, "lead")


def company(manager: OrganizationManager, root: Path, **config: Any) -> Any:
    """Create the Studio organization: a lead, Mira reporting to the lead, and a Desktop project.

    Pass ``employees`` or any other config key to change it.
    """
    employees = config.pop(
        "employees",
        [
            {"id": "lead", "name": "Avery Lane"},
            {"id": "mira", "name": "Mira Santos", "reportsTo": "lead"},
        ],
    )
    return manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "slug": "studio",
                    "employees": employees,
                    "projects": [{"id": "desktop", "name": "Desktop"}],
                    **config,
                }
            },
        },
    )
