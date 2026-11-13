"""Approve all: the user approves exactly the consent requests they confirmed, safely ordered."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_consent import approval_order
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


def company(manager: OrganizationManager, root: Path) -> Any:
    employees = [
        {"id": "lead", "name": "Avery", "permissionMode": "read-only"},
        {"id": "builder", "name": "Mira", "paused": True},
    ]
    return manager.call(
        str(root),
        "user",
        {"action": "create", "params": {"config": {"name": "Desk", "employees": employees}}},
    )


def act(
    manager: OrganizationManager, org: dict[str, Any], action: str, actor: str = "user", **p: Any
) -> Any:
    return manager.call(
        org["projectRoot"], actor, {"organizationId": org["id"], "action": action, "params": p}
    )


def rem(manager: OrganizationManager, org: dict[str, Any], action: str, **params: Any) -> Any:
    result = act(manager, org, action, "Rem:thread", **params)
    assert result.get("needsUserConsent") is True, result
    return result


def state(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return manager.store.get("", org["id"])


def ask_config(manager: OrganizationManager, org: dict[str, Any], change: Any) -> Any:
    current = state(manager, org)
    config = copy.deepcopy(current["config"])
    change(config)
    return rem(
        manager, org, "configure", config=config, expectedRevision=current["revision"], reason="x"
    )


def gated_ticket(manager: OrganizationManager, org: dict[str, Any], title: str) -> Any:
    return act(manager, org, "task_create", title=title, assignee="lead", approvalRequired=True)


def ask_ungate(manager: OrganizationManager, org: dict[str, Any], task: dict[str, Any]) -> Any:
    return rem(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"approvalRequired": False},
    )


def approve_all(manager: OrganizationManager, org: dict[str, Any], ids: list[str]) -> Any:
    return act(manager, org, "request_approve_all", requestIds=ids)


def decided_order(manager: OrganizationManager, org: dict[str, Any]) -> list[str]:
    events = act(manager, org, "events", after=0)
    return [
        e["payload"]["requestId"]
        for e in events
        if e["kind"] == "consent_decided" and e["payload"].get("decision") == "approve"
    ]


def test_approves_everything_listed_with_changes_before_the_start(manager, tmp_path):
    org = company(manager, tmp_path)
    one, two = gated_ticket(manager, org, "Charter"), gated_ticket(manager, org, "Limits")
    # Rem asked in an unsafe order: start first, then the tickets, then the permissions.
    start = rem(manager, org, "control", state="running")
    ungate_one, ungate_two = ask_ungate(manager, org, one), ask_ungate(manager, org, two)
    permissions = ask_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="workspace-write")
    )
    ids = [r["requestId"] for r in (start, ungate_one, ungate_two, permissions)]

    result = approve_all(manager, org, ids)

    assert result["approved"] == 4
    assert [r["status"] for r in result["results"]] == ["approved"] * 4
    # The report is in the order they were applied: configuration, tickets, then start.
    applied = [permissions["requestId"], ungate_one["requestId"], ungate_two["requestId"]]
    assert [r["requestId"] for r in result["results"]] == [*applied, start["requestId"]]
    assert decided_order(manager, org) == [*applied, start["requestId"]]
    after = state(manager, org)
    assert after["runtime"]["state"] == "running"
    assert after["config"]["employees"][0]["permissionMode"] == "workspace-write"
    assert not any(t["approvalRequired"] for t in after["runtime"]["tasks"])
    assert result["organization"]["runtime"]["state"] == "running"
    act(manager, org, "control", state="paused")


def test_a_request_made_after_the_list_was_shown_stays_waiting(manager, tmp_path):
    org = company(manager, tmp_path)
    task = gated_ticket(manager, org, "Charter")
    shown = ask_ungate(manager, org, task)
    later = ask_config(manager, org, lambda c: c["employees"][1].update(paused=False))

    result = approve_all(manager, org, [shown["requestId"]])

    assert [r["requestId"] for r in result["results"]] == [shown["requestId"]]
    requests = state(manager, org)["runtime"]["consentRequests"]
    assert requests[shown["requestId"]]["status"] == "approved"
    assert requests[later["requestId"]]["status"] == "pending"
    assert state(manager, org)["config"]["employees"][1]["paused"] is True


def test_a_revision_conflict_is_left_waiting_and_holds_back_the_start(manager, tmp_path):
    org = company(manager, tmp_path)
    # Both replace the configuration Rem saw at the same revision; only one can apply.
    first = ask_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="workspace-write")
    )
    second = ask_config(manager, org, lambda c: c["employees"][1].update(paused=False))
    start = rem(manager, org, "control", state="running")

    result = approve_all(
        manager, org, [start["requestId"], second["requestId"], first["requestId"]]
    )

    by_id = {r["requestId"]: r for r in result["results"]}
    assert by_id[first["requestId"]]["status"] == "approved"
    assert by_id[second["requestId"]]["status"] == "skipped"
    assert "revision" in by_id[second["requestId"]]["message"]
    assert by_id[start["requestId"]]["status"] == "skipped"
    assert "another request" in by_id[start["requestId"]]["message"]
    after = state(manager, org)
    # The conflicting change didn't undo the first one, and nothing started.
    assert after["config"]["employees"][0]["permissionMode"] == "workspace-write"
    assert after["config"]["employees"][1]["paused"] is True
    assert after["runtime"]["state"] == "paused"
    requests = after["runtime"]["consentRequests"]
    assert requests[second["requestId"]]["status"] == "pending"
    assert requests[start["requestId"]]["status"] == "pending"
    # The individual buttons still work on what was left.
    act(manager, org, "request_decide", requestId=second["requestId"], decision="decline")
    act(manager, org, "request_decide", requestId=start["requestId"], decision="approve")
    assert state(manager, org)["runtime"]["state"] == "running"
    act(manager, org, "control", state="paused")


def test_a_ticket_changed_since_rem_asked_is_left_waiting(manager, tmp_path):
    org = company(manager, tmp_path)
    task = gated_ticket(manager, org, "Charter")
    request = ask_ungate(manager, org, task)
    act(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"title": "Charter v2"},
    )
    result = approve_all(manager, org, [request["requestId"]])
    [entry] = result["results"]
    assert entry["status"] == "skipped" and "ticket changed" in entry["message"]
    assert state(manager, org)["runtime"]["consentRequests"][request["requestId"]]["status"] == (
        "pending"
    )


def test_each_failure_is_reported_and_the_rest_still_apply(manager, tmp_path):
    org = company(manager, tmp_path)
    task = gated_ticket(manager, org, "Charter")
    ungate = ask_ungate(manager, org, task)
    start = rem(manager, org, "control", state="running")
    archive = rem(manager, org, "archive", archived=True)

    result = approve_all(
        manager, org, [archive["requestId"], start["requestId"], ungate["requestId"]]
    )

    by_id = {r["requestId"]: r for r in result["results"]}
    assert by_id[ungate["requestId"]]["status"] == "approved"
    assert by_id[start["requestId"]]["status"] == "approved"
    # Archiving needs a paused organization; it was tried after the start and refused.
    assert by_id[archive["requestId"]]["status"] == "failed"
    assert by_id[archive["requestId"]]["message"]
    assert state(manager, org)["runtime"]["consentRequests"][archive["requestId"]]["status"] == (
        "failed"
    )
    assert result["approved"] == 2
    act(manager, org, "control", state="paused")


def test_a_request_decided_after_the_list_was_shown_holds_back_the_start(manager, tmp_path):
    org = company(manager, tmp_path)
    task = gated_ticket(manager, org, "Charter")
    ungate = ask_ungate(manager, org, task)
    start = rem(manager, org, "control", state="running")
    declined = rem(manager, org, "resume_all", expectedRevision=state(manager, org)["revision"])
    # Declined elsewhere, for example on the phone, after the list opened.
    act(manager, org, "request_decide", requestId=declined["requestId"], decision="decline")

    result = approve_all(
        manager, org, [start["requestId"], declined["requestId"], ungate["requestId"]]
    )

    by_id = {r["requestId"]: r for r in result["results"]}
    assert by_id[ungate["requestId"]]["status"] == "approved"
    assert by_id[declined["requestId"]]["status"] == "skipped"
    assert "No longer waiting" in by_id[declined["requestId"]]["message"]
    assert by_id[start["requestId"]]["status"] == "skipped"
    assert state(manager, org)["runtime"]["state"] == "paused"
    assert result["approved"] == 1


@pytest.mark.parametrize("actor", ["Rem:thread", "Rem:token:ci"])
def test_only_the_user_can_approve_all(manager, tmp_path, actor):
    org = company(manager, tmp_path)
    request = rem(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="Only the user"):
        act(manager, org, "request_approve_all", actor, requestIds=[request["requestId"]])
    assert state(manager, org)["runtime"]["consentRequests"][request["requestId"]]["status"] == (
        "pending"
    )


def test_employees_cannot_approve_all(manager, tmp_path):
    org = company(manager, tmp_path)
    request = rem(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="Employees cannot configure"):
        manager.call(
            "",
            "employee:lead",
            {
                "organizationId": org["id"],
                "action": "request_approve_all",
                "params": {"requestIds": [request["requestId"]]},
            },
            employee_id="lead",
            bound_org=org["id"],
            generation=0,
        )


@pytest.mark.parametrize("ids", [[], ["a", "a"], [f"r{n}" for n in range(21)], "abc", [1]])
def test_the_list_must_name_up_to_twenty_different_requests(manager, tmp_path, ids):
    org = company(manager, tmp_path)
    with pytest.raises(ValueError, match="Choose"):
        approve_all(manager, org, ids)


def test_approval_order_puts_edits_first_by_revision_and_starting_last():
    requests = [
        {"id": "start", "action": "control", "at": "1"},
        {"id": "archive", "action": "archive", "at": "0"},
        {"id": "ticket", "action": "task_update", "at": "2", "params": {"expectedRevision": 9}},
        {"id": "late", "action": "configure", "at": "3", "params": {"expectedRevision": 4}},
        {"id": "early", "action": "configure", "at": "5", "params": {"expectedRevision": 3}},
        {"id": "holds", "action": "resume_all", "at": "4", "params": {"expectedRevision": 4}},
    ]
    assert [r["id"] for r in approval_order(requests)] == [
        "early",
        "late",
        "holds",
        "ticket",
        "start",
        "archive",
    ]
