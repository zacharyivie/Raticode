"""Organizations hardening: loops and stale state found by driving organizations with fake
providers in their own data folders.

- Holding a worker mid-run charged the run and blocked its ticket "for review" as if the
  configuration broke it; after the hold it stayed blocked until someone reopened it.
- A worker reviewer, or Rem in Auto mode, could send the same result back forever: each
  round cost a worker run, and only the ticket's run limit stopped it.
- A plan or result Auto mode had sent to Rem stayed in Needs you as a second copy after
  Auto mode was turned off or the user decided the ticket; once stale, Rem "declined" it
  on the ticket and marked it reviewed, so a changed plan was never asked about again.
"""

from __future__ import annotations

import asyncio
import copy
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_auto import needs_review, review_pass
from gofer.ui.organization_recovery import (
    MAX_SEND_BACKS,
    rem_may_reopen,
    worker_reviews,
)
from gofer.ui.organizations import OrganizationManager


async def quick(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


class Reviewer:
    """Rem's model, faked: gives the same answer every time and counts the prompts."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, prompt: str, cancel: threading.Event) -> Any:
        self.prompts.append(prompt)
        return copy.deepcopy(self.answer)


def start(tmp_path: Path, stream: Any = quick, reviewer: Any = None) -> OrganizationManager:
    manager = OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)
    manager.auto_review = reviewer
    return manager


def company(manager: OrganizationManager, root: Path, *, auto: bool = False) -> Any:
    org = manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "slug": "studio",
                    "employees": [
                        {"id": "lead", "name": "Avery Lane"},
                        {"id": "mira", "name": "Mira Santos"},
                    ],
                    "projects": [{"id": "desktop", "name": "Desktop"}],
                }
            },
        },
    )
    if auto:
        org = act(manager, org, "auto_mode", enabled=True, confirm=True)
    return org


def act(manager: OrganizationManager, org: dict[str, Any], action: str, **params: Any) -> Any:
    return manager.call(
        org["projectRoot"],
        "user",
        {"organizationId": org["id"], "action": action, "params": params},
    )


def as_worker(
    manager: OrganizationManager, org: dict[str, Any], eid: str, action: str, **params: Any
) -> Any:
    state = manager.store.get("", org["id"])
    return manager.call(
        org["projectRoot"],
        f"employee:{eid}",
        {"organizationId": org["id"], "action": action, "params": params},
        employee_id=eid,
        bound_org=org["id"],
        generation=state["runtime"]["generation"],
    )


def state(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return manager.store.get("", org["id"])


def ticket(manager: OrganizationManager, org: dict[str, Any], tid: str) -> Any:
    return next(t for t in state(manager, org)["runtime"]["tasks"] if t["id"] == tid)


def assign(manager: OrganizationManager, org: dict[str, Any], eid: str) -> None:
    act(
        manager,
        org,
        "self_work_projects",
        employeeId=eid,
        projects=[org["projectRoot"]],
        expectedRevision=0,
    )


def settle(manager: OrganizationManager, seconds: float = 10) -> None:
    until = time.monotonic() + seconds
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def run_once(manager: OrganizationManager) -> None:
    manager.dispatch()
    settle(manager)


def run_until_handed_in(manager: OrganizationManager, org: dict[str, Any], tid: str) -> None:
    for _ in range(5):
        if ticket(manager, org, tid)["status"] == "in_review":
            return
        run_once(manager)
    assert ticket(manager, org, tid)["status"] == "in_review"


def hand_in(manager: OrganizationManager, org: dict[str, Any], tid: str) -> None:
    def change(current: dict[str, Any]) -> dict[str, Any]:
        task = next(t for t in current["runtime"]["tasks"] if t["id"] == tid)
        task.update(
            status="in_review",
            reviewRequestedAt=datetime.now(UTC).isoformat(),
            revision=task["revision"] + 1,
        )
        return {}

    manager.store.mutate("", org["id"], "system", "turn_finished", change)


def comments(manager: OrganizationManager, org: dict[str, Any], tid: str) -> list[str]:
    return [str(c.get("body")) for c in ticket(manager, org, tid)["comments"]]


# ---------------------------------------------------------------- review loops


def test_a_worker_reviewer_cannot_send_one_result_back_forever(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    assign(manager, org, "lead")
    assign(manager, org, "mira")
    made = act(manager, org, "task_create", title="Ship", assignee="mira", reviewer="lead")
    act(manager, org, "control", state="running")

    for _ in range(MAX_SEND_BACKS):
        run_until_handed_in(manager, org, made["id"])
        task = ticket(manager, org, made["id"])
        assert worker_reviews(state(manager, org), task)
        as_worker(
            manager,
            org,
            "lead",
            "review",
            taskId=task["id"],
            expectedRevision=task["revision"],
            decision="revise",
            body="Not yet.",
        )

    # The next result waits for the user, who is told why.
    run_until_handed_in(manager, org, made["id"])
    task = ticket(manager, org, made["id"])
    runs = task["turns"]
    assert not worker_reviews(state(manager, org), task)
    assert made["id"] in act(manager, org, "read")["diagnostics"]["attention"]
    assert any(
        f"back {MAX_SEND_BACKS} times in a row" in b for b in comments(manager, org, task["id"])
    )
    with pytest.raises(ValueError, match="the user reviews it now"):
        as_worker(
            manager,
            org,
            "lead",
            "review",
            taskId=task["id"],
            expectedRevision=task["revision"],
            decision="revise",
            body="Still not.",
        )
    # Nothing runs again until the user decides.
    run_once(manager)
    run_once(manager)
    assert ticket(manager, org, made["id"])["status"] == "in_review"
    assert ticket(manager, org, made["id"])["turns"] == runs

    # The user's own send-back lets the reviewer review the next result again.
    act(
        manager,
        org,
        "review",
        taskId=task["id"],
        expectedRevision=task["revision"],
        decision="revise",
        body="Try the other approach.",
    )
    run_until_handed_in(manager, org, made["id"])
    assert worker_reviews(state(manager, org), ticket(manager, org, made["id"]))
    manager.close()


def test_a_worker_reviewer_may_still_accept_after_the_limit(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    assign(manager, org, "lead")
    made = act(manager, org, "task_create", title="Ship", assignee="mira", reviewer="lead")
    act(manager, org, "control", state="running")

    def spent(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["sendBacks"] = {made["id"]: MAX_SEND_BACKS}
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", spent)
    hand_in(manager, org, made["id"])
    task = ticket(manager, org, made["id"])
    as_worker(
        manager,
        org,
        "lead",
        "review",
        taskId=task["id"],
        expectedRevision=task["revision"],
        decision="accept",
        body="Good now.",
    )
    assert ticket(manager, org, made["id"])["status"] == "done"
    assert made["id"] not in (state(manager, org)["runtime"].get("sendBacks") or {})
    manager.close()


def test_auto_mode_stops_sending_a_result_back_and_leaves_it_for_you(tmp_path: Path) -> None:
    reviewer = Reviewer({"decision": "decline", "reason": "Missing tests.", "recommendation": ""})
    manager = start(tmp_path, reviewer=reviewer)
    org = company(manager, tmp_path, auto=True)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    act(manager, org, "control", state="running")

    for _ in range(MAX_SEND_BACKS):
        run_until_handed_in(manager, org, made["id"])
        assert needs_review(state(manager, org))
        review_pass(manager, org["projectRoot"], org["id"])
        assert ticket(manager, org, made["id"])["status"] == "todo"

    run_until_handed_in(manager, org, made["id"])
    asked = len(reviewer.prompts)
    assert not needs_review(state(manager, org))
    assert review_pass(manager, org["projectRoot"], org["id"]) == []
    assert len(reviewer.prompts) == asked
    read = act(manager, org, "read")
    assert ticket(manager, org, made["id"])["status"] == "in_review"
    assert made["id"] in read["diagnostics"]["attention"]
    manager.close()


def test_rem_does_not_reopen_a_result_reviewers_keep_rejecting(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, auto=True)
    assign(manager, org, "lead")
    made = act(manager, org, "task_create", title="Ship", assignee="mira", reviewer="lead")
    act(manager, org, "control", state="running")

    def rejected(current: dict[str, Any], count: int) -> dict[str, Any]:
        task = next(t for t in current["runtime"]["tasks"] if t["id"] == made["id"])
        task.update(status="blocked", blockReason="Rejected in review by Avery Lane; x")
        current["runtime"]["sendBacks"] = {made["id"]: count}
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", lambda c: rejected(c, 1))
    assert rem_may_reopen(state(manager, org), ticket(manager, org, made["id"]))
    manager.store.mutate("", org["id"], "test", "probe", lambda c: rejected(c, MAX_SEND_BACKS))
    assert not rem_may_reopen(state(manager, org), ticket(manager, org, made["id"]))
    manager.close()


# ---------------------------------------------------------------- stale Auto mode requests


def overloaded(prompt: str, cancel: threading.Event) -> Any:
    raise RuntimeError("503 Service Unavailable: overloaded")


def queued_plan(tmp_path: Path) -> tuple[OrganizationManager, dict[str, Any], dict[str, Any], str]:
    """A plan Auto mode sent to Rem, still waiting because Rem's model was busy."""
    manager = start(tmp_path, reviewer=overloaded)
    org = company(manager, tmp_path, auto=True)
    made = act(manager, org, "task_create", title="Charter", assignee="lead", approvalRequired=True)
    review_pass(manager, org["projectRoot"], org["id"])
    [rid] = state(manager, org)["runtime"]["consentRequests"]
    assert state(manager, org)["runtime"]["consentRequests"][rid]["status"] == "pending"
    return manager, org, made, rid


def needs_you(manager: OrganizationManager, org: dict[str, Any]) -> list[str]:
    read = act(manager, org, "read")
    return [
        rid
        for rid, r in read["runtime"]["consentRequests"].items()
        if r["status"] == "pending" and r.get("waitingOn") == "you"
    ]


def test_turning_auto_mode_off_withdraws_its_ticket_requests(tmp_path: Path) -> None:
    manager, org, made, rid = queued_plan(tmp_path)
    act(manager, org, "auto_mode", enabled=False)
    # The ticket itself waits for the user; a second "Approve the plan" item would repeat it.
    assert needs_you(manager, org) == []
    assert state(manager, org)["runtime"]["consentRequests"][rid]["status"] == "expired"
    assert made["id"] in act(manager, org, "read")["diagnostics"]["attention"]
    manager.close()


def test_rem_consent_requests_still_go_back_to_you_when_auto_mode_turns_off(
    tmp_path: Path,
) -> None:
    manager, org, _, _ = queued_plan(tmp_path)
    asked = manager.call(
        org["projectRoot"],
        "Rem:thread",
        {"organizationId": org["id"], "action": "control", "params": {"state": "running"}},
    )
    act(manager, org, "auto_mode", enabled=False)
    assert needs_you(manager, org) == [asked["requestId"]]
    manager.close()


def test_deciding_the_ticket_yourself_withdraws_rems_request(tmp_path: Path) -> None:
    manager, org, made, rid = queued_plan(tmp_path)
    task = ticket(manager, org, made["id"])
    act(manager, org, "approve", taskId=task["id"], expectedRevision=task["revision"])
    assert state(manager, org)["runtime"]["consentRequests"][rid]["status"] == "expired"
    # Rem has nothing left to decide, and says nothing about it on the ticket.
    manager.auto_review = Reviewer({"decision": "approve", "reason": "Fine.", "recommendation": ""})
    assert not needs_review(state(manager, org))
    assert review_pass(manager, org["projectRoot"], org["id"]) == []
    assert not any("Rem (Auto mode)" in b for b in comments(manager, org, made["id"]))
    manager.close()


def test_a_stale_ticket_request_is_asked_again_not_declined(tmp_path: Path) -> None:
    manager, org, made, rid = queued_plan(tmp_path)
    task = ticket(manager, org, made["id"])
    # The plan changed after Rem was asked; the old request can't apply as asked.
    act(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"description": "Paper data only."},
    )
    reviewer = Reviewer({"decision": "approve", "reason": "Fine.", "recommendation": ""})
    manager.auto_review = reviewer

    def due(current: dict[str, Any]) -> dict[str, Any]:
        for request in current["runtime"]["consentRequests"].values():
            request.get("autoReview", {}).pop("retryAt", None)
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", due)
    review_pass(manager, org["projectRoot"], org["id"])
    review_pass(manager, org["projectRoot"], org["id"])
    current = state(manager, org)
    assert current["runtime"]["consentRequests"][rid]["status"] == "expired"
    # Rem approved the changed plan instead of leaving it unasked.
    approved = ticket(manager, org, made["id"])
    assert approved["approvedRevision"] == approved["planRevision"]
    assert len(reviewer.prompts) == 1
    assert not any("declined" in b for b in comments(manager, org, made["id"]))
    assert all(d["decision"] != "decline" for d in current["runtime"].get("autoDecisions", []))
    manager.close()


# ---------------------------------------------------------------- holding a worker mid-run


class LongRun:
    """A fake provider that works until it's told to finish or is stopped."""

    def __init__(self) -> None:
        self.finish = False
        self.started = 0

    async def __call__(self, **options: Any) -> Any:
        self.started += 1
        yield {"type": "status", "message": "working"}
        while not self.finish:
            await asyncio.sleep(0.02)
        yield {"type": "final", "message": {"body": "Finished."}}


def hold(manager: OrganizationManager, org: dict[str, Any], eid: str, held: bool) -> None:
    current = state(manager, org)
    config = copy.deepcopy(current["config"])
    config.pop("packageFiles", None)
    for employee in config["employees"]:
        if employee["id"] == eid:
            employee["paused"] = held
    act(manager, org, "configure", config=config, expectedRevision=current["revision"], reason="x")


def wait_for(check: Any, seconds: float = 10) -> None:
    until = time.monotonic() + seconds
    while not check() and time.monotonic() < until:
        time.sleep(0.02)
    assert check()


def test_holding_a_worker_mid_run_parks_the_ticket_until_they_are_back(tmp_path: Path) -> None:
    provider = LongRun()
    manager = start(tmp_path, provider)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Long job", assignee="mira")
    act(manager, org, "control", state="running")
    manager.dispatch()
    wait_for(lambda: provider.started == 1 and bool(manager._active))

    hold(manager, org, "mira", True)
    wait_for(lambda: not manager._active)
    task = ticket(manager, org, made["id"])
    # Not blocked for review as if the configuration broke it, and the run isn't charged.
    assert task["status"] == "todo" and task["turns"] == 0 and task.get("pulledAt")
    assert task["claim"] is None
    assert any("was held before this run finished" in b for b in comments(manager, org, task["id"]))
    assert not any("removed an owner" in b for b in comments(manager, org, task["id"]))
    [attempt] = state(manager, org)["runtime"]["attempts"].values()
    assert attempt["status"] == "paused"
    # Nothing runs while the worker is held.
    run_once(manager)
    assert provider.started == 1

    hold(manager, org, "mira", False)
    provider.finish = True
    run_once(manager)
    assert provider.started == 2
    assert ticket(manager, org, made["id"])["status"] == "in_review"
    manager.close()


def test_changing_a_running_workers_permissions_still_stops_for_review(tmp_path: Path) -> None:
    provider = LongRun()
    manager = start(tmp_path, provider)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Long job", assignee="mira")
    act(manager, org, "control", state="running")
    manager.dispatch()
    wait_for(lambda: provider.started == 1 and bool(manager._active))
    current = state(manager, org)
    config = copy.deepcopy(current["config"])
    config.pop("packageFiles", None)
    for employee in config["employees"]:
        if employee["id"] == "mira":
            employee.update(paused=True, permissionMode="read-only")
    act(manager, org, "configure", config=config, expectedRevision=current["revision"], reason="x")
    wait_for(lambda: not manager._active)
    task = ticket(manager, org, made["id"])
    assert task["status"] == "blocked"
    assert task["blockReason"].startswith("Stopped because the configuration changed")
    manager.close()
