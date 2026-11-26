"""Odd Organizations behavior found in real use: quitting mid-run and stale consent requests.

- Quitting Raticode while a worker ran left its ticket "Cancelled before it finished",
  charged the run, and kept it Blocked after the organization started again.
- A Rem request bound to an older ticket revision failed with "Task changed" once anything
  touched the ticket (the user approving its plan, a comment), even though nothing the
  request changes had moved. Asking again added a second request for the same change.
- An unexpected error while applying an approved request left it "applying" until the
  next restart.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_consent import approve_all
from gofer.ui.organization_store import OrganizationConflict
from gofer.ui.organizations import RESTART_REVIEW_REASON, OrganizationManager
from gofer.ui.rem_ticket_activity import PAUSED_REASON
from tests.helpers.organizations import act, company


def start(tmp_path: Path, stream: Any) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)


async def quick(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def state(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return manager.store.get("", org["id"])


def ticket(manager: OrganizationManager, org: dict[str, Any], tid: str) -> Any:
    return next(t for t in state(manager, org)["runtime"]["tasks"] if t["id"] == tid)


def wait_for(check: Any, seconds: float = 10) -> None:
    until = time.monotonic() + seconds
    while not check() and time.monotonic() < until:
        time.sleep(0.02)
    assert check()


def recoveries(manager: OrganizationManager, org: dict[str, Any]) -> list[dict[str, Any]]:
    events = manager.store.events("", org["id"])
    return [e["payload"] for e in events if e["kind"] == "runtime_recovered"]


class Worker:
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


# ------------------------------------------------------------------ quitting mid-run


def quit_mid_run(tmp_path: Path, **config: Any) -> tuple[Worker, dict[str, Any], str]:
    worker = Worker()
    manager = start(tmp_path, worker)
    org = company(manager, tmp_path, **config)
    made = act(manager, org, "task_create", title="Long job", assignee="mira")
    act(manager, org, "control", state="running")
    manager.dispatch()
    wait_for(lambda: worker.started == 1 and bool(manager._active))
    manager.close()
    return worker, org, made["id"]


def test_quitting_mid_run_parks_the_ticket_like_a_pause(tmp_path: Path) -> None:
    worker, org, tid = quit_mid_run(tmp_path)
    restarted = start(tmp_path, worker)
    task = ticket(restarted, org, tid)
    # Not "Cancelled before it finished", no run charged, and it goes first next time.
    assert task["status"] == "blocked" and task["blockReason"] == PAUSED_REASON
    assert task["turns"] == 0 and task["claim"] is None and task.get("pulledAt")
    bodies = [c["body"] for c in task["comments"]]
    assert any(b.startswith("Raticode closed before this run finished") for b in bodies)
    assert not any("review before retrying" in b for b in bodies)
    attempt = next(iter(state(restarted, org)["runtime"]["attempts"].values()))
    assert attempt["status"] == "paused"
    runtime = state(restarted, org)["runtime"]
    assert runtime["state"] == "paused"
    assert runtime["pauseReason"] == "Paused because Raticode restarted"
    assert recoveries(restarted, org)[-1] == {
        "reason": "Started paused; nothing was interrupted",
        "kept": False,
        "interrupted": 0,
    }
    # Starting the organization again runs the ticket without anyone reopening it.
    worker.finish = True
    act(restarted, org, "control", state="running")
    restarted.dispatch()
    wait_for(lambda: ticket(restarted, org, tid)["status"] == "in_review")
    assert worker.started == 2
    restarted.close()


def test_an_organization_that_keeps_running_resumes_work_cut_off_by_quitting(
    tmp_path: Path,
) -> None:
    worker, org, tid = quit_mid_run(tmp_path, resumeAfterRestart=True)
    worker.finish = True
    restarted = start(tmp_path, worker)
    assert state(restarted, org)["runtime"]["state"] == "running"
    assert recoveries(restarted, org)[-1]["reason"] == (
        "Kept running after a restart; nothing was interrupted"
    )
    restarted.dispatch()
    wait_for(lambda: ticket(restarted, org, tid)["status"] == "in_review")
    restarted.close()


def test_work_lost_to_a_crash_still_waits_for_review(tmp_path: Path) -> None:
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Mid-run", assignee="mira")
    act(manager, org, "control", state="running")

    def lose(current: dict[str, Any]) -> dict[str, Any]:
        for task in current["runtime"]["tasks"]:
            task.update(claim="lost-run", status="in_progress")
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", lose)
    manager.close()
    restarted = start(tmp_path, quick)
    task = ticket(restarted, org, made["id"])
    assert task["status"] == "blocked" and task["blockReason"] == RESTART_REVIEW_REASON
    assert recoveries(restarted, org)[-1]["reason"] == (
        "Started paused; 1 interrupted ticket waits for review"
    )
    restarted.close()


def test_a_company_the_user_paused_keeps_their_pause_reason_after_a_crash(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    act(manager, org, "task_create", title="Mid-run", assignee="mira")

    def lose(current: dict[str, Any]) -> dict[str, Any]:
        for task in current["runtime"]["tasks"]:
            task.update(claim="lost-run", status="in_progress")
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", lose)
    act(manager, org, "control", state="paused")
    manager.close()
    restarted = start(tmp_path, quick)
    assert state(restarted, org)["runtime"]["pauseReason"] == "Paused by you"
    restarted.close()


# ------------------------------------------------------------------ consent requests


def gated_ticket(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return act(manager, org, "task_create", title="Charter", assignee="mira", approvalRequired=True)


def rem_removes_approval(manager: OrganizationManager, org: dict[str, Any], tid: str) -> Any:
    result = act(
        manager,
        org,
        "task_update",
        "Rem:thread",
        taskId=tid,
        expectedRevision=ticket(manager, org, tid)["revision"],
        changes={"approvalRequired": False},
    )
    assert result["needsUserConsent"]
    return result


def decide(manager: OrganizationManager, org: dict[str, Any], rid: str) -> Any:
    return act(manager, org, "request_decide", requestId=rid, decision="approve")


def request(manager: OrganizationManager, org: dict[str, Any], rid: str) -> Any:
    return state(manager, org)["runtime"]["consentRequests"][rid]


def test_a_request_survives_the_user_approving_the_tickets_plan_first(tmp_path: Path) -> None:
    # Seen in real use: the user approved three plans, then Rem's requests to remove those
    # same tickets' approval steps all failed with "Task changed. Read it before updating."
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    made = gated_ticket(manager, org)
    asked = rem_removes_approval(manager, org, made["id"])
    act(manager, org, "approve", taskId=made["id"], expectedRevision=made["revision"])
    act(manager, org, "comment", taskId=made["id"], body="Looks right.")
    decide(manager, org, asked["requestId"])
    assert ticket(manager, org, made["id"])["approvalRequired"] is False
    assert request(manager, org, asked["requestId"])["status"] == "approved"
    manager.close()


def test_approve_all_applies_a_request_whose_ticket_only_changed_elsewhere(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    made = gated_ticket(manager, org)
    asked = rem_removes_approval(manager, org, made["id"])
    act(manager, org, "comment", taskId=made["id"], body="Context for the worker.")
    result = approve_all(manager, "", org["id"], [asked["requestId"]], ttl_seconds=3600)
    assert result["results"][0]["status"] == "approved"
    assert ticket(manager, org, made["id"])["approvalRequired"] is False
    manager.close()


def test_a_request_still_fails_when_what_it_changes_moved(tmp_path: Path) -> None:
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    made = gated_ticket(manager, org)
    asked = rem_removes_approval(manager, org, made["id"])
    # The user edits what the ticket asks for: Rem's request is about the old plan.
    act(
        manager,
        org,
        "task_update",
        taskId=made["id"],
        expectedRevision=ticket(manager, org, made["id"])["revision"],
        changes={"description": "A different job"},
    )
    with pytest.raises(OrganizationConflict, match="Task changed"):
        decide(manager, org, asked["requestId"])
    assert ticket(manager, org, made["id"])["approvalRequired"] is True
    result = approve_all(manager, "", org["id"], [asked["requestId"]], ttl_seconds=3600)
    assert result["results"][0]["status"] == "skipped"
    manager.close()


def test_rem_plan_approval_applies_after_a_comment_but_not_after_a_plan_edit(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    first, second = gated_ticket(manager, org), gated_ticket(manager, org)
    asks = {
        t["id"]: act(
            manager, org, "approve", "Rem:thread", taskId=t["id"], expectedRevision=t["revision"]
        )["requestId"]
        for t in (first, second)
    }
    act(manager, org, "comment", taskId=first["id"], body="Ready when you are.")
    decide(manager, org, asks[first["id"]])
    assert ticket(manager, org, first["id"])["approvedRevision"] == 1
    act(
        manager,
        org,
        "task_update",
        taskId=second["id"],
        expectedRevision=second["revision"],
        changes={"title": "Charter, rewritten"},
    )
    with pytest.raises(OrganizationConflict):
        decide(manager, org, asks[second["id"]])
    assert ticket(manager, org, second["id"])["approvedRevision"] is None
    manager.close()


def test_asking_again_for_the_same_change_refreshes_the_waiting_request(tmp_path: Path) -> None:
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    made = gated_ticket(manager, org)
    first = rem_removes_approval(manager, org, made["id"])
    act(
        manager,
        org,
        "task_update",
        taskId=made["id"],
        expectedRevision=made["revision"],
        changes={"description": "Sharper scope"},
    )
    again = rem_removes_approval(manager, org, made["id"])
    assert again["requestId"] == first["requestId"]
    waiting = [
        r
        for r in state(manager, org)["runtime"]["consentRequests"].values()
        if r["status"] == "pending"
    ]
    assert len(waiting) == 1
    assert waiting[0]["params"]["expectedRevision"] == ticket(manager, org, made["id"])["revision"]
    decide(manager, org, first["requestId"])
    assert ticket(manager, org, made["id"])["approvalRequired"] is False
    manager.close()


def test_an_unexpected_error_while_applying_closes_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    asked = act(manager, org, "control", "Rem:thread", state="running")
    assert asked["needsUserConsent"]

    def locked(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(manager.store, "control", locked)
    with pytest.raises(OrganizationConflict, match="database is locked"):
        decide(manager, org, asked["requestId"])
    item = request(manager, org, asked["requestId"])
    assert item["status"] == "failed" and "database is locked" in item["error"]
    manager.close()
