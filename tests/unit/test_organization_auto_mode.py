"""Auto mode: Rem decides an organization's requests, and halts only when it must.

Every test uses its own data folder and a fake reviewer in place of Rem's model, so no
real organization or provider is touched.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import organization_auto
from gofer.ui.organization_auto import (
    approve_on_instruction,
    needs_review,
    review_pass,
)
from gofer.ui.organizations import OrganizationManager


async def final(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


class Reviewer:
    """Rem's model, faked: answers in order and remembers every prompt."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str, cancel: threading.Event) -> Any:
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


def open_manager(data: Path, reviewer: Any = None) -> OrganizationManager:
    manager = OrganizationManager(data, stream=final, start_runtime=False)
    manager.auto_review = reviewer
    return manager


@pytest.fixture
def manager(tmp_path: Path) -> Any:
    manager = open_manager(tmp_path / "data")
    try:
        yield manager
    finally:
        manager.close()


def company(manager: OrganizationManager, root: Path, *, auto: bool = True) -> Any:
    employees = [
        {"id": "lead", "name": "Avery", "permissionMode": "read-only"},
        {"id": "builder", "name": "Mira", "paused": True},
    ]
    org = manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Desk",
                    "mission": "Paper-only research",
                    "employees": employees,
                    "monthlyTurnLimit": 50,
                }
            },
        },
    )
    if auto:
        org = act(manager, org, "auto_mode", enabled=True, confirm=True)
    return org


def act(
    manager: OrganizationManager, org: dict[str, Any], action: str, actor: str = "user", **p: Any
) -> Any:
    return manager.call(
        org["projectRoot"], actor, {"organizationId": org["id"], "action": action, "params": p}
    )


def state(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return manager.store.get("", org["id"])


def rem_config(manager: OrganizationManager, org: dict[str, Any], change: Any) -> Any:
    current = state(manager, org)
    config = copy.deepcopy(current["config"])
    config.pop("autoMode", None)
    config.pop("packageFiles", None)
    change(config)
    return act(
        manager,
        org,
        "configure",
        "Rem:thread",
        config=config,
        expectedRevision=current["revision"],
        reason="x",
    )


def request_of(manager: OrganizationManager, org: dict[str, Any], rid: str) -> Any:
    return state(manager, org)["runtime"]["consentRequests"][rid]


def events(manager: OrganizationManager, org: dict[str, Any], kind: str) -> list[Any]:
    return [e for e in act(manager, org, "events", after=0) if e["kind"] == kind]


def run(manager: OrganizationManager, org: dict[str, Any]) -> list[dict[str, Any]]:
    return review_pass(manager, org["projectRoot"], org["id"])


APPROVE = {"summary": "ok", "decision": "approve", "reason": "Fits the charter."}


# Turning it on and off ------------------------------------------------------------------


def test_auto_mode_is_off_by_default_and_rem_requests_wait_for_the_user(manager, tmp_path):
    manager.auto_review = Reviewer(APPROVE)
    org = company(manager, tmp_path, auto=False)
    assert state(manager, org)["config"]["autoMode"] is False
    asked = rem_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="workspace-write")
    )

    assert asked["needsUserConsent"] is True
    assert run(manager, org) == []
    assert not needs_review(state(manager, org))
    assert manager.auto_review.prompts == []
    public = act(manager, org, "read")
    assert public["runtime"]["consentRequests"][asked["requestId"]]["waitingOn"] == "you"
    # Rem still can't decide it: the user-only rule is unchanged outside Auto mode.
    with pytest.raises(ValueError, match="Only the user"):
        act(
            manager,
            org,
            "request_decide",
            "Rem:thread",
            requestId=asked["requestId"],
            decision="approve",
        )
    assert request_of(manager, org, asked["requestId"])["status"] == "pending"


@pytest.mark.parametrize("actor", ["Rem:thread", "Rem:token:ci"])
def test_only_the_user_can_turn_auto_mode_on_or_off(manager, tmp_path, actor):
    org = company(manager, tmp_path, auto=False)
    with pytest.raises(ValueError, match="Only the user"):
        act(manager, org, "auto_mode", actor, enabled=True, confirm=True)
    with pytest.raises(ValueError, match="Only the user"):
        rem_config(manager, org, lambda c: c.update(autoMode=True))
    assert state(manager, org)["config"]["autoMode"] is False


@pytest.mark.parametrize("actor", ["Rem:thread", "Rem:token:ci"])
def test_rem_cannot_create_a_company_with_auto_mode_already_on(manager, tmp_path, actor):
    """Otherwise Rem would decide the start request for a company it designed itself."""
    config = {"name": "Self-run", "mission": "x", "employees": [{"id": "a", "name": "A"}]}
    with pytest.raises(ValueError, match="Only the user"):
        manager.call(
            str(tmp_path),
            actor,
            {"action": "create", "params": {"config": {**config, "autoMode": True}}},
        )
    assert manager.call("", "user", {"action": "list", "params": {}}) == []
    created = manager.call(str(tmp_path), actor, {"action": "create", "params": {"config": config}})
    assert state(manager, created)["config"]["autoMode"] is False


def test_employees_cannot_turn_auto_mode_on(manager, tmp_path):
    org = company(manager, tmp_path, auto=False)
    with pytest.raises(ValueError, match="Employees cannot configure"):
        manager.call(
            "",
            "employee:lead",
            {"organizationId": org["id"], "action": "auto_mode", "params": {"enabled": True}},
            employee_id="lead",
            bound_org=org["id"],
            generation=0,
        )


def test_turning_it_on_needs_a_confirmation(manager, tmp_path):
    org = company(manager, tmp_path, auto=False)
    with pytest.raises(ValueError, match="confirm"):
        act(manager, org, "auto_mode", enabled=True)
    org = act(manager, org, "auto_mode", enabled=True, confirm=True)
    assert org["config"]["autoMode"] is True
    # Rem's configuration changes and restores keep it as it is.
    rem_config(manager, org, lambda c: c.update(description="New words"))
    assert state(manager, org)["config"]["autoMode"] is True
    current = state(manager, org)
    act(manager, org, "restore", revision=1, expectedRevision=current["revision"], reason="r")
    assert state(manager, org)["config"]["autoMode"] is True
    # Turning it off needs no confirmation.
    assert act(manager, org, "auto_mode", enabled=False)["config"]["autoMode"] is False


# Rem's decisions ------------------------------------------------------------------------


def test_rem_approves_an_ordinary_request(manager, tmp_path):
    manager.auto_review = Reviewer(APPROVE)
    org = company(manager, tmp_path)
    asked = rem_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="workspace-write")
    )
    assert asked["autoReview"] is True and asked["needsUserConsent"] is False
    assert (
        act(manager, org, "read")["runtime"]["consentRequests"][asked["requestId"]]["waitingOn"]
        == "rem"
    )

    [result] = run(manager, org)

    assert result["decision"] == "approve" and result["applied"] is True
    request = request_of(manager, org, asked["requestId"])
    assert request["status"] == "approved"
    assert request["decidedBy"] == "Rem (Auto mode)"
    assert state(manager, org)["config"]["employees"][0]["permissionMode"] == "workspace-write"
    [decided] = events(manager, org, "consent_decided")
    assert decided["actor"] == "Rem:auto"
    assert decided["payload"]["decidedBy"] == "Rem (Auto mode)"
    assert state(manager, org)["runtime"]["autoDecisions"][-1]["reason"] == "Fits the charter."
    # The prompt carries the charter, the request and the revision it expects.
    prompt = manager.auto_review.prompts[0]
    assert "Paper-only research" in prompt and "permissionMode" in prompt


def test_a_dangerous_request_is_made_safe_and_the_organization_keeps_working(manager, tmp_path):
    org = company(manager, tmp_path)
    act(manager, org, "control", state="running")
    safer = {
        "summary": "sandboxed instead",
        "decision": "modify",
        "reason": "Full access isn't needed for research.",
        "changes": [
            {
                "op": "replace",
                "path": "/config/employees/0/permissionMode",
                "value": "workspace-write",
            }
        ],
    }
    # Rem first tries to approve it as asked, which Raticode refuses; then it makes it safe.
    manager.auto_review = Reviewer(APPROVE, safer)
    asked = rem_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="danger-full-access")
    )

    [result] = run(manager, org)

    assert result["decision"] == "modify" and result["applied"] is True
    assert "can't be approved as asked" in manager.auto_review.prompts[1]
    after = state(manager, org)
    assert after["config"]["employees"][0]["permissionMode"] == "workspace-write"
    assert after["runtime"]["state"] == "running"
    assert not after["runtime"].get("autoHalt")
    request = request_of(manager, org, asked["requestId"])
    assert request["status"] == "approved"
    assert request["appliedParams"]["config"]["employees"][0]["permissionMode"] == "workspace-write"
    act(manager, org, "control", state="paused")


def test_a_modification_that_stays_dangerous_is_refused(manager, tmp_path):
    org = company(manager, tmp_path)
    still = {
        "decision": "modify",
        "reason": "x",
        "changes": [
            {
                "op": "replace",
                "path": "/config/employees/0/permissionMode",
                "value": "bypassPermissions",
            }
        ],
    }
    manager.auto_review = Reviewer(still)
    asked = rem_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="danger-full-access")
    )
    [result] = run(manager, org)
    # Two unusable answers become a decline: nothing unsafe happens and nothing halts.
    assert result["decision"] == "decline"
    assert request_of(manager, org, asked["requestId"])["status"] == "declined"
    assert state(manager, org)["config"]["employees"][0]["permissionMode"] == "read-only"
    assert not state(manager, org)["runtime"].get("autoHalt")


def test_rem_declines_with_a_reason_on_the_ticket(manager, tmp_path):
    org = company(manager, tmp_path)
    task = act(manager, org, "task_create", title="Charter", assignee="lead", approvalRequired=True)
    manager.auto_review = Reviewer(
        {
            "decision": "decline",
            "reason": "The charter needs the Risk Officer's review first.",
            "recommendation": "Assign the review to the Risk Officer.",
        }
    )
    asked = act(
        manager,
        org,
        "task_update",
        "Rem:thread",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"approvalRequired": False},
    )
    results = run(manager, org)

    declined = next(r for r in results if r["requestId"] == asked["requestId"])
    assert declined["decision"] == "decline" and declined["applied"] is True
    request = request_of(manager, org, asked["requestId"])
    assert request["status"] == "declined"
    assert "Risk Officer" in request["decisionNote"]
    ticket = next(t for t in state(manager, org)["runtime"]["tasks"] if t["id"] == task["id"])
    assert ticket["approvalRequired"] is True
    assert any(
        c["actor"] == "Rem:auto" and "Recommended instead" in c["body"] for c in ticket["comments"]
    )


def test_plans_and_proposals_go_to_rem_instead_of_the_user(manager, tmp_path):
    org = company(manager, tmp_path)
    task = act(manager, org, "task_create", title="Charter", assignee="lead", approvalRequired=True)
    manager.auto_review = Reviewer(
        {
            "decision": "modify",
            "reason": "Narrowed to paper data.",
            "ticketChanges": {"description": "Use paper data only."},
        }
    )
    assert needs_review(state(manager, org))

    [result] = run(manager, org)

    assert result["kind"] == "plan" and result["applied"] is True
    ticket = next(t for t in state(manager, org)["runtime"]["tasks"] if t["id"] == task["id"])
    assert ticket["description"] == "Use paper data only."
    assert ticket["approvedRevision"] == ticket["planRevision"]
    # Nothing more waits: the next pass asks Rem nothing.
    assert run(manager, org) == []
    assert len(manager.auto_review.prompts) == 1


# Halting --------------------------------------------------------------------------------


def test_dangerous_and_unfixable_halts_with_a_request_for_the_user(manager, tmp_path):
    org = company(manager, tmp_path)
    act(manager, org, "control", state="running")
    manager.auto_review = Reviewer(
        {
            "decision": "halt",
            "reason": "The charter itself now asks for a live brokerage connection.",
            "halt": {
                "kind": "dangerous",
                "category": "live_trading",
                "userAction": "Decide whether this desk may ever trade live, then answer the "
                "request.",
                "whyNotFixable": "Declining leaves the charter asking for it on every ticket.",
            },
        }
    )
    asked = rem_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="danger-full-access")
    )

    [result] = run(manager, org)

    assert result["decision"] == "halt"
    after = state(manager, org)
    assert after["runtime"]["state"] == "paused"
    assert "Decide whether this desk may ever trade live" in after["runtime"]["pauseReason"]
    assert after["runtime"]["autoHalt"]["requestId"] == asked["requestId"]
    public = act(manager, org, "read")
    assert public["runtime"]["consentRequests"][asked["requestId"]]["waitingOn"] == "you"
    assert manager.store.summaries()[0]["waitingRequests"] == 1
    # Nothing else is reviewed while it's stopped.
    assert not needs_review(after)
    # The user's answer lifts the stop and the organization picks up where it was.
    act(manager, org, "request_decide", requestId=asked["requestId"], decision="decline")
    after = state(manager, org)
    assert not after["runtime"].get("autoHalt")
    assert after["runtime"]["state"] == "running"
    act(manager, org, "control", state="paused")


def test_impossible_without_the_user_halts(manager, tmp_path):
    org = company(manager, tmp_path)
    act(
        manager,
        org,
        "task_create",
        title="Pull broker statements",
        assignee="lead",
        approvalRequired=True,
    )
    manager.auto_review = Reviewer(
        {
            "decision": "halt",
            "reason": "It needs a sign-in to the statements portal.",
            "halt": {
                "kind": "impossible",
                "category": "human_sign_in",
                "userAction": "Sign in to the statements portal on this computer.",
            },
        }
    )
    [result] = run(manager, org)
    assert result["decision"] == "halt"
    halt = state(manager, org)["runtime"]["autoHalt"]
    assert halt["kind"] == "impossible" and halt["category"] == "human_sign_in"
    # Starting it again is the user's answer too.
    act(manager, org, "control", state="running")
    assert not state(manager, org)["runtime"].get("autoHalt")
    act(manager, org, "control", state="paused")


def test_an_ordinary_request_never_halts(manager, tmp_path):
    org = company(manager, tmp_path)
    nervous = {
        "decision": "halt",
        "reason": "Not sure.",
        "halt": {"kind": "dangerous", "userAction": "Look at it.", "whyNotFixable": "Unsure"},
    }
    manager.auto_review = Reviewer(nervous, APPROVE)
    # Waking a paused worker is an ordinary request.
    rem_config(manager, org, lambda c: c["employees"][1].update(paused=False))
    [result] = run(manager, org)
    assert result["decision"] == "approve"
    assert "found nothing dangerous" in manager.auto_review.prompts[1]
    assert not state(manager, org)["runtime"].get("autoHalt")


def test_rem_unavailable_halts_after_repeated_failures(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(organization_auto, "RETRY_SECONDS", (0,))
    org = company(manager, tmp_path)
    manager.auto_review = Reviewer(RuntimeError("not signed in"))
    asked = rem_config(manager, org, lambda c: c["employees"][1].update(paused=False))
    assert run(manager, org) == []
    assert run(manager, org) == []
    [result] = run(manager, org)
    assert result["decision"] == "halt"
    halt = state(manager, org)["runtime"]["autoHalt"]
    assert halt["kind"] == "impossible" and halt["category"] == "rem_unavailable"
    assert "Settings > Rem" in halt["userAction"]
    assert request_of(manager, org, asked["requestId"])["status"] == "pending"


def test_budget_and_policy_changes_wait_for_the_user_without_halting(manager, tmp_path):
    manager.auto_review = Reviewer(APPROVE)
    org = company(manager, tmp_path)
    asked = rem_config(manager, org, lambda c: c.update(monthlyTurnLimit=500))

    [result] = run(manager, org)

    assert result["decision"] == "left_for_you"
    assert manager.auto_review.prompts == []
    request = request_of(manager, org, asked["requestId"])
    assert request["status"] == "pending" and request["route"] == "user"
    assert not state(manager, org)["runtime"].get("autoHalt")
    assert state(manager, org)["config"]["monthlyTurnLimit"] == 50
    assert (
        act(manager, org, "read")["runtime"]["consentRequests"][asked["requestId"]]["waitingOn"]
        == "you"
    )


# Order, conflicts and restarts ----------------------------------------------------------


def test_a_revision_conflict_is_closed_and_reported(manager, tmp_path):
    manager.auto_review = Reviewer(APPROVE)
    org = company(manager, tmp_path)
    first = rem_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="workspace-write")
    )
    second = rem_config(manager, org, lambda c: c["employees"][1].update(paused=False))

    results = {r["requestId"]: r for r in run(manager, org)}

    assert results[first["requestId"]]["decision"] == "approve"
    stale = results[second["requestId"]]
    assert stale["decision"] == "decline" and "revision" in stale["reason"]
    assert request_of(manager, org, second["requestId"])["status"] == "declined"
    # Only one Rem call: the stale one was closed without asking.
    assert len(manager.auto_review.prompts) == 1


def test_a_saved_decision_is_applied_once_after_a_restart(tmp_path, monkeypatch):
    data = tmp_path / "data"
    first = open_manager(data, Reviewer(APPROVE))
    org = company(first, tmp_path)
    asked = rem_config(first, org, lambda c: c["employees"][1].update(paused=False))
    real = organization_auto.apply_decision

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Raticode stopped")

    monkeypatch.setattr(organization_auto, "apply_decision", crash)
    with pytest.raises(RuntimeError):
        run(first, org)
    first.close()
    assert request_of(first, org, asked["requestId"])["autoReview"]["state"] == "decided"

    monkeypatch.setattr(organization_auto, "apply_decision", real)
    again = Reviewer(APPROVE)
    second = open_manager(data, again)
    try:
        [result] = run(second, org)
        assert result["decision"] == "approve" and result["applied"] is True
        assert again.prompts == []
        assert run(second, org) == []
        approvals = [
            e
            for e in events(second, org, "consent_decided")
            if e["payload"]["decision"] == "approve"
        ]
        assert len(approvals) == 1
        assert state(second, org)["config"]["employees"][1]["paused"] is False
    finally:
        second.close()


def test_turning_it_off_hands_waiting_requests_back_to_the_user(manager, tmp_path):
    org = company(manager, tmp_path)
    asked = rem_config(manager, org, lambda c: c["employees"][1].update(paused=False))
    act(manager, org, "auto_mode", enabled=False)
    read = act(manager, org, "read")
    assert read["runtime"]["consentRequests"][asked["requestId"]]["waitingOn"] == "you"
    assert run(manager, org) == []


# Approving on the user's instruction ----------------------------------------------------


def message(body: str, at: str = "9999-01-01T00:00:00+00:00") -> dict[str, Any]:
    return {"id": "user-msg-1", "body": body, "created_at": at}


def test_the_users_chat_instruction_approves_in_any_organization(manager, tmp_path):
    org = company(manager, tmp_path, auto=False)
    one = rem_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="workspace-write")
    )
    start = act(manager, org, "control", "Rem:thread", state="running")

    result = approve_on_instruction(
        manager, org["id"], None, message=message("Approve those for me")
    )

    assert result["approved"] == 2
    # Configuration first, starting last, as with Approve all.
    assert [r["requestId"] for r in result["results"]] == [one["requestId"], start["requestId"]]
    request = request_of(manager, org, one["requestId"])
    assert request["decidedBy"] == "Rem, on your instruction"
    assert request["instruction"]["messageId"] == "user-msg-1"
    decided = events(manager, org, "consent_decided")
    assert {e["actor"] for e in decided} == {"Rem:user-instruction"}
    assert all(e["payload"]["instructedBy"]["messageId"] == "user-msg-1" for e in decided)
    assert state(manager, org)["runtime"]["state"] == "running"
    act(manager, org, "control", state="paused")


def test_an_obvious_okay_needs_no_magic_words(manager, tmp_path):
    # Rem judges whether the user's message is their okay; Raticode no longer matches words.
    org = company(manager, tmp_path, auto=False)
    asked = rem_config(manager, org, lambda c: c["employees"][1].update(paused=False))

    result = approve_on_instruction(
        manager,
        org["id"],
        [asked["requestId"]],
        message=message("Sure, go for it"),
        reason="Answered my question about releasing the worker",
    )

    assert result["approved"] == 1
    request = request_of(manager, org, asked["requestId"])
    assert request["status"] == "approved"
    assert request["instruction"] == {
        "messageId": "user-msg-1",
        "at": "9999-01-01T00:00:00+00:00",
        "reason": "Answered my question about releasing the worker",
    }


def test_without_a_user_message_nothing_is_approved(manager, tmp_path):
    org = company(manager, tmp_path, auto=False)
    asked = rem_config(manager, org, lambda c: c["employees"][1].update(paused=False))
    for missing in ({"id": "", "body": "approve it"}, {"id": "m", "body": "  "}):
        with pytest.raises(ValueError, match="user's own chat message"):
            approve_on_instruction(manager, org["id"], None, message=missing)
    with pytest.raises(ValueError, match="user's own message"):
        manager._decide(
            "",
            state(manager, org),
            {"requestId": asked["requestId"], "decision": "approve"},
            decider="Rem:user-instruction",
        )
    with pytest.raises(ValueError, match="Only the user"):
        manager._decide(
            "",
            state(manager, org),
            {"requestId": asked["requestId"], "decision": "approve"},
            decider="Rem:thread",
        )
    assert request_of(manager, org, asked["requestId"])["status"] == "pending"


def test_requests_after_the_message_and_conflicts_are_reported(manager, tmp_path):
    org = company(manager, tmp_path, auto=False)
    first = rem_config(
        manager, org, lambda c: c["employees"][0].update(permissionMode="workspace-write")
    )
    second = rem_config(manager, org, lambda c: c["employees"][1].update(paused=False))
    sent = request_of(manager, org, second["requestId"])["at"]
    late = act(manager, org, "control", "Rem:thread", state="running")

    result = approve_on_instruction(
        manager, org["id"], None, message=message("ok, approve all of them", sent)
    )

    statuses = {r["requestId"]: r["status"] for r in result["results"]}
    assert statuses[first["requestId"]] == "approved"
    # Both asked at the same revision: the second can no longer apply as asked.
    assert statuses[second["requestId"]] == "skipped"
    # Asked after the message: reported, and left waiting.
    assert statuses[late["requestId"]] == "skipped"
    assert request_of(manager, org, late["requestId"])["status"] == "pending"
    # Named by ID, a request asked after the message still isn't approved.
    named = approve_on_instruction(
        manager, org["id"], [late["requestId"]], message=message("approve that", sent)
    )
    assert named["results"][0]["status"] == "skipped"
    assert request_of(manager, org, late["requestId"])["status"] == "pending"


@pytest.mark.parametrize("actor", ["Rem:thread", "Rem:token:ci"])
def test_tools_and_tokens_still_cannot_decide(manager, tmp_path, actor):
    org = company(manager, tmp_path, auto=False)
    asked = rem_config(manager, org, lambda c: c["employees"][1].update(paused=False))
    for action, params in (
        ("request_decide", {"requestId": asked["requestId"], "decision": "approve"}),
        ("request_approve_all", {"requestIds": [asked["requestId"]]}),
    ):
        with pytest.raises(ValueError, match="Only the user"):
            act(manager, org, action, actor, **params)
    assert request_of(manager, org, asked["requestId"])["status"] == "pending"


def test_risky_words_in_a_plan_need_rem_to_say_why_nothing_risky_happens(manager, tmp_path):
    org = company(manager, tmp_path)
    task = act(
        manager,
        org,
        "task_create",
        title="Wire the Alpaca brokerage adapter",
        description="Read-only quotes for the paper book. No live trading.",
        assignee="lead",
        approvalRequired=True,
    )
    checked = {
        **APPROVE,
        "hintsCleared": "It only reads quotes into the paper book; the ticket forbids live orders.",
    }
    # A plain approve is refused because the text names a brokerage; the checked one applies.
    manager.auto_review = Reviewer(APPROVE, checked)

    [result] = run(manager, org)

    assert result["decision"] == "approve" and result["applied"] is True
    assert "hintsCleared" in manager.auto_review.prompts[1]
    assert result["hintsCleared"].startswith("It only reads quotes")
    ticket = next(t for t in state(manager, org)["runtime"]["tasks"] if t["id"] == task["id"])
    assert ticket["approvedRevision"] == ticket["planRevision"]


def test_risky_words_without_an_explanation_are_declined_not_approved(manager, tmp_path):
    org = company(manager, tmp_path)
    task = act(
        manager,
        org,
        "task_create",
        title="Deploy the dashboard and delete all old reports",
        assignee="lead",
        approvalRequired=True,
    )
    manager.auto_review = Reviewer(APPROVE)
    [result] = run(manager, org)
    # Two plain approvals become a decline: nothing risky runs, and nothing halts.
    assert result["decision"] == "decline"
    ticket = next(t for t in state(manager, org)["runtime"]["tasks"] if t["id"] == task["id"])
    assert ticket["approvedRevision"] is None
    assert not state(manager, org)["runtime"].get("autoHalt")
