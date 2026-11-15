"""The organization list says how many things wait on the user, for the sidebar and the phone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organizations import OrganizationManager


async def final(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


@pytest.fixture
def manager(tmp_path: Path) -> Any:
    manager = OrganizationManager(tmp_path / "data", stream=final, start_runtime=False)
    try:
        yield manager
    finally:
        manager.close()


def act(manager: OrganizationManager, org: dict[str, Any], action: str, **p: Any) -> Any:
    return manager.call(
        org["projectRoot"], "user", {"organizationId": org["id"], "action": action, "params": p}
    )


def listed(manager: OrganizationManager) -> Any:
    return manager.call("", "user", {"action": "list", "params": {}})[0]


def test_needs_you_counts_requests_reviews_blocked_work_and_plans_to_approve(manager, tmp_path):
    org = manager.call(
        str(tmp_path),
        "user",
        {
            "action": "create",
            "params": {"config": {"name": "Desk", "employees": [{"id": "lead", "name": "Avery"}]}},
        },
    )
    assert listed(manager)["needsYou"] == 0

    act(manager, org, "task_create", title="Plan first", assignee="lead", approvalRequired=True)
    act(manager, org, "task_create", title="Just do it", assignee="lead")
    stuck = act(manager, org, "task_create", title="Stuck", assignee="lead")
    act(
        manager,
        org,
        "task_update",
        taskId=stuck["id"],
        expectedRevision=stuck["revision"],
        changes={"status": "blocked", "blockReason": "Needs a key"},
    )
    summary = listed(manager)
    assert summary["openTasks"] == 3
    assert (summary["needsYou"], summary["waitingRequests"]) == (2, 0)

    # A request from Rem counts too, once. Rem asking to start always waits for the user.
    asked = manager.call(
        org["projectRoot"],
        "Rem:thread",
        {"organizationId": org["id"], "action": "control", "params": {"state": "running"}},
    )
    assert asked.get("needsUserConsent") is True, asked
    summary = listed(manager)
    assert (summary["needsYou"], summary["waitingRequests"]) == (3, 1)

    # Finished or cancelled work no longer waits on anyone.
    stuck = next(t for t in act(manager, org, "read")["runtime"]["tasks"] if t["title"] == "Stuck")
    act(
        manager,
        org,
        "task_update",
        taskId=stuck["id"],
        expectedRevision=stuck["revision"],
        changes={"status": "cancelled"},
    )
    assert listed(manager)["needsYou"] == 2
