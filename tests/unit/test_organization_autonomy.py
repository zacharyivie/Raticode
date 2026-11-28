"""Organizations keep working without the user: retries, Rem's look at stuck tickets in
Auto mode, worker reviewers that get asked, and alerts only when the user is needed.

Every test uses its own data folder, a fake provider and a fake reviewer in place of
Rem's model, so no real organization or provider is touched.
"""

from __future__ import annotations

import copy
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.ui.organization_auto import needs_review, review_pass
from gofer.ui.organization_recovery import MAX_REM_REOPENS, RETRY_SECONDS, rem_may_reopen
from gofer.ui.organizations import OrganizationManager
from gofer.ui.rem_ticket_activity import RETRY_REASON
from tests.helpers.organizations import act, assign

FAILED = "The last run failed; see the conversation"


async def quick(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def failing(*errors: str) -> Any:
    """A provider that fails with each error in turn, then answers."""
    left = list(errors)

    async def stream(**options: Any) -> Any:
        if left:
            yield {"type": "error", "error": left.pop(0)}
            return
        yield {"type": "final", "message": {"body": "Done."}}

    return stream


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


def start(tmp_path: Path, stream: Any = quick, reviewer: Any = None) -> OrganizationManager:
    manager = OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)
    manager.auto_review = reviewer
    return manager


# Not the shared company: Mira outside the lead's reports, and auto=True turns on Auto mode.
def company(manager: OrganizationManager, root: Path, *, auto: bool = False, **config: Any) -> Any:
    employees = config.pop(
        "employees",
        [{"id": "lead", "name": "Avery Lane"}, {"id": "mira", "name": "Mira Santos"}],
    )
    org = manager.call(
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
    if auto:
        org = act(manager, org, "auto_mode", enabled=True, confirm=True)
    return org


def read(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return act(manager, org, "read")


def ticket(manager: OrganizationManager, org: dict[str, Any], tid: str) -> Any:
    return next(t for t in read(manager, org)["runtime"]["tasks"] if t["id"] == tid)


def settle(manager: OrganizationManager, seconds: float = 10) -> None:
    until = time.monotonic() + seconds
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def run_once(manager: OrganizationManager) -> None:
    manager.dispatch()
    settle(manager)


def retry_now(manager: OrganizationManager, org: dict[str, Any], tid: str) -> None:
    def change(state: dict[str, Any]) -> dict[str, Any]:
        entry = state["runtime"]["retries"][tid]
        entry["at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", change)


def block(manager: OrganizationManager, org: dict[str, Any], tid: str, reason: str) -> None:
    """Leave a ticket the way Raticode leaves it after a run that went wrong."""

    def change(state: dict[str, Any]) -> dict[str, Any]:
        task = next(t for t in state["runtime"]["tasks"] if t["id"] == tid)
        task.update(status="blocked", blockReason=reason, revision=task["revision"] + 1)
        task["comments"].append({"actor": "system", "at": "", "body": "API key was revoked"})
        return {}

    manager.store.mutate("", org["id"], "system", "turn_finished", change)


def comments(manager: OrganizationManager, org: dict[str, Any], tid: str) -> list[str]:
    return [
        str(c.get("body"))
        for c in ticket(manager, org, tid)["comments"]
        if c.get("kind") != "status"
    ]


# ---------------------------------------------------------------- temporary failures


def test_a_temporary_provider_problem_tries_again_by_itself(tmp_path: Path) -> None:
    manager = start(tmp_path, failing("API Error: 529 Overloaded"))
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    act(manager, org, "control", state="running")
    run_once(manager)

    waiting = ticket(manager, org, made["id"])
    assert (waiting["status"], waiting["blockReason"]) == ("blocked", RETRY_REASON)
    assert "try 1 of 3" in comments(manager, org, made["id"])[-1]
    state = read(manager, org)
    assert state["runtime"]["retries"][made["id"]]["count"] == 1
    # It waits by itself, so it doesn't ask for the user's attention.
    assert made["id"] not in state["diagnostics"]["attention"]
    assert waiting["waitingOn"] == "raticode"

    # Not before its wait is over.
    run_once(manager)
    assert ticket(manager, org, made["id"])["status"] == "blocked"

    retry_now(manager, org, made["id"])
    run_once(manager)  # back to To do
    run_once(manager)  # runs, and this time the provider answers
    done = ticket(manager, org, made["id"])
    assert done["status"] == "in_review"
    assert done["turns"] == 2  # the failed run counted against the ticket's run limit
    assert made["id"] not in read(manager, org)["runtime"].get("retries", {})
    manager.close()


def test_tries_run_out_and_then_the_ticket_waits_for_a_look(tmp_path: Path) -> None:
    manager = start(tmp_path, failing(*["rate limit exceeded"] * 10))
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    act(manager, org, "control", state="running")
    run_once(manager)
    for _ in RETRY_SECONDS:
        retry_now(manager, org, made["id"])
        run_once(manager)
        run_once(manager)

    stuck = ticket(manager, org, made["id"])
    assert (stuck["status"], stuck["blockReason"]) == ("blocked", FAILED)
    assert "already tried again 3 times" in comments(manager, org, made["id"])[-1]
    assert stuck["turns"] == 1 + len(RETRY_SECONDS)
    # A later reopen gets a fresh set of tries.
    assert made["id"] not in read(manager, org)["runtime"].get("retries", {})
    assert made["id"] in read(manager, org)["diagnostics"]["attention"]
    manager.close()


def test_lasting_failures_and_delegated_work_never_retry(tmp_path: Path) -> None:
    manager = start(tmp_path, failing("Invalid API key. Please run /login"))
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    act(manager, org, "control", state="running")
    run_once(manager)
    stuck = ticket(manager, org, made["id"])
    assert (stuck["status"], stuck["blockReason"]) == ("blocked", FAILED)
    assert not read(manager, org)["runtime"].get("retries")
    manager.close()


def test_waiting_to_try_again_does_not_count_as_needing_you(tmp_path: Path) -> None:
    manager = start(tmp_path, failing("503 Service Unavailable"))
    org = company(manager, tmp_path)
    act(manager, org, "task_create", title="Ship", assignee="mira")
    act(manager, org, "control", state="running")
    run_once(manager)
    summary = next(o for o in manager.store.summaries() if o["id"] == org["id"])
    assert summary["needsYou"] == 0
    manager.close()


# ---------------------------------------------------------------- Rem and stuck tickets


def sidebar_count(manager: OrganizationManager, org: dict[str, Any]) -> int:
    """The sidebar's and phone list's "needs you" count for ``org``."""
    return int(next(o for o in manager.store.summaries() if o["id"] == org["id"])["needsYou"])


def approve(reason: str = "A passing error; try again.") -> dict[str, Any]:
    return {"decision": "approve", "reason": reason, "summary": "Reopen"}


def decline(reason: str) -> dict[str, Any]:
    return {"decision": "decline", "reason": reason, "summary": "Leave it"}


def test_auto_mode_rem_reopens_a_failed_ticket(tmp_path: Path) -> None:
    reviewer = Reviewer(approve())
    manager = start(tmp_path, reviewer=reviewer)
    org = company(manager, tmp_path, auto=True)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    block(manager, org, made["id"], FAILED)
    state = manager.store.get("", org["id"])
    assert needs_review(state)
    # Rem looks first, so the user isn't alerted or asked yet.
    assert made["id"] not in read(manager, org)["diagnostics"]["attention"]
    assert not [a for a in manager.alerts_after(0)["alerts"] if a["title"] == "Ship"], (
        "no alert while Rem still has to look"
    )

    review_pass(manager, org["projectRoot"], org["id"])

    reopened = ticket(manager, org, made["id"])
    assert reopened["status"] == "todo"
    assert (
        "Rem (Auto mode) reopened this: A passing error" in comments(manager, org, made["id"])[-1]
    )
    assert read(manager, org)["runtime"]["autoReopens"][made["id"]] == 1
    assert '"kind": "unblock"' in reviewer.prompts[0]
    assert "blockReason" in reviewer.prompts[0]
    manager.close()


def test_auto_mode_rem_leaves_a_ticket_for_you_and_only_then_alerts(tmp_path: Path) -> None:
    reviewer = Reviewer(decline("The API key was revoked. Add a new key in Settings, then reopen."))
    manager = start(tmp_path, reviewer=reviewer)
    org = company(manager, tmp_path, auto=True)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    block(manager, org, made["id"], FAILED)
    before = manager.alerts_after(0)
    assert not [a for a in before["alerts"] if a["title"] == "Ship"]
    # While Rem still has to look, the sidebar doesn't count it as needing the user.
    assert sidebar_count(manager, org) == 0

    review_pass(manager, org["projectRoot"], org["id"])

    stuck = ticket(manager, org, made["id"])
    assert stuck["status"] == "blocked"
    assert comments(manager, org, made["id"])[-1].startswith(
        "Rem (Auto mode) left this blocked for you: The API key was revoked."
    )
    alerts = manager.alerts_after(before["last"])["alerts"]
    # Now it needs the user, in the sidebar count as in Needs you.
    assert sidebar_count(manager, org) == 1
    assert [(a["title"], a["reason"]) for a in alerts] == [("Ship", "needs_you")]
    assert made["id"] in read(manager, org)["diagnostics"]["attention"]
    # Rem doesn't look again at the same blocked spell, even after a comment on it.
    assert not needs_review(manager.store.get("", org["id"]))
    act(manager, org, "comment", taskId=made["id"], body="Looking into the key.")
    assert not needs_review(manager.store.get("", org["id"]))
    review_pass(manager, org["projectRoot"], org["id"])
    assert len(reviewer.prompts) == 1
    manager.close()


def test_rem_only_looks_at_tickets_raticode_stopped(tmp_path: Path) -> None:
    manager = start(tmp_path, reviewer=Reviewer(approve()))
    org = company(manager, tmp_path, auto=True, maxTaskTurns=3)
    tasks = {
        reason: act(manager, org, "task_create", title=f"T {i}", assignee="mira")["id"]
        for i, reason in enumerate(
            [
                FAILED,
                "The last run ended without a final response",
                "Worker stopped unexpectedly; review before retrying",
                "Interrupted when Raticode restarted; review it, then reopen",
                "Rejected in review by Avery Lane; the user or Rem can reopen it",
                "Rejected in review by you; the user or Rem can reopen it",
                "Blocked by you: wait for the design",
                "Run limit for this ticket reached. Reopen it to allow more runs.",
                "Waiting for working hours",
            ]
        )
    }
    for reason, tid in tasks.items():
        block(manager, org, tid, reason)
    state = manager.store.get("", org["id"])
    may = {
        reason
        for reason, tid in tasks.items()
        if rem_may_reopen(state, next(t for t in state["runtime"]["tasks"] if t["id"] == tid))
    }
    assert may == {
        FAILED,
        "The last run ended without a final response",
        "Worker stopped unexpectedly; review before retrying",
        "Interrupted when Raticode restarted; review it, then reopen",
        "Rejected in review by Avery Lane; the user or Rem can reopen it",
    }

    def at_limit(current: dict[str, Any]) -> dict[str, Any]:
        for task in current["runtime"]["tasks"]:
            if task["id"] == tasks[FAILED]:
                task["turns"] = 3
            if task["id"] == tasks["The last run ended without a final response"]:
                task["execution"] = {"kind": "swarm"}
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", at_limit)
    state = manager.store.get("", org["id"])
    # Only the user grants more runs, and delegated work may have had outside effects.
    for reason in (FAILED, "The last run ended without a final response"):
        task = next(t for t in state["runtime"]["tasks"] if t["id"] == tasks[reason])
        assert not rem_may_reopen(state, task)
    manager.close()


def test_rem_reopens_one_ticket_at_most_twice_between_good_runs(tmp_path: Path) -> None:
    manager = start(tmp_path, reviewer=Reviewer(approve()))
    org = company(manager, tmp_path, auto=True)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    for _ in range(MAX_REM_REOPENS):
        block(manager, org, made["id"], FAILED)
        review_pass(manager, org["projectRoot"], org["id"])
        assert ticket(manager, org, made["id"])["status"] == "todo"
    block(manager, org, made["id"], FAILED)
    assert not needs_review(manager.store.get("", org["id"]))
    # From here it waits for the user, who is alerted and sees it in Needs you.
    assert made["id"] in read(manager, org)["diagnostics"]["attention"]
    assert sidebar_count(manager, org) == 1
    assert ticket(manager, org, made["id"])["waitingOn"] == "you"
    manager.close()


def test_auto_mode_keeps_going_through_a_short_outage_of_rems_model(tmp_path: Path) -> None:
    reviewer = Reviewer(RuntimeError("429 Too Many Requests"))
    manager = start(tmp_path, reviewer=reviewer)
    org = company(manager, tmp_path, auto=True)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    block(manager, org, made["id"], FAILED)

    def due(current: dict[str, Any]) -> dict[str, Any]:
        for request in current["runtime"]["consentRequests"].values():
            request.get("autoReview", {}).pop("retryAt", None)
        return {"probe": True}

    for _ in range(3):
        review_pass(manager, org["projectRoot"], org["id"])
        manager.store.mutate("", org["id"], "test", "probe", due)
    # Three failures used to stop the organization; a temporary one gets more tries.
    state = read(manager, org)
    assert not state["runtime"].get("autoHalt")
    assert state["runtime"]["state"] != "paused" or "Auto mode" not in str(
        state["runtime"].get("pauseReason")
    )
    for _ in range(4):
        review_pass(manager, org["projectRoot"], org["id"])
        manager.store.mutate("", org["id"], "test", "probe", due)
    halted = read(manager, org)["runtime"]["autoHalt"]
    assert halted["category"] == "rem_unavailable"
    manager.close()


# ---------------------------------------------------------------- worker reviewers


def hand_in(manager: OrganizationManager, org: dict[str, Any], tid: str) -> None:
    def change(state: dict[str, Any]) -> dict[str, Any]:
        task = next(t for t in state["runtime"]["tasks"] if t["id"] == tid)
        task.update(
            status="in_review",
            reviewRequestedAt=datetime.now(UTC).isoformat(),
            revision=task["revision"] + 1,
        )
        return {}

    manager.store.mutate("", org["id"], "system", "turn_finished", change)


def test_a_worker_reviewer_is_asked_to_review_and_rem_takes_over_after(tmp_path: Path) -> None:
    briefs: list[str] = []

    async def worker(**options: Any) -> Any:
        briefs.append(str(options))
        yield {"type": "final", "message": {"body": "Looked."}}

    reviewer = Reviewer(approve("The result meets its done-when."))
    manager = start(tmp_path, worker, reviewer=reviewer)
    org = company(manager, tmp_path, auto=True)
    assign(manager, org, "lead")
    made = act(manager, org, "task_create", title="Ship", assignee="mira", reviewer="lead")
    hand_in(manager, org, made["id"])
    state = manager.store.get("", org["id"])
    # The worker reviewer comes first; Rem doesn't take it while the reviewer can.
    assert not needs_review(state)

    act(manager, org, "control", state="running")
    run_once(manager)
    run_once(manager)
    checkin = next(t for t in read(manager, org)["runtime"]["tasks"] if t.get("heartbeat"))
    assert checkin["assignee"] == "lead"
    assert "You are the reviewer of these results" in checkin["description"]
    assert "(Ship)" in checkin["description"]
    assert any("You are the reviewer of these results" in brief for brief in briefs)

    # The check-in ended without a review, so in Auto mode Rem reviews it instead.
    assert ticket(manager, org, made["id"])["status"] == "in_review"
    assert needs_review(manager.store.get("", org["id"]))
    review_pass(manager, org["projectRoot"], org["id"])
    assert ticket(manager, org, made["id"])["status"] == "done"
    # The reviewer isn't asked again about the same hand-in.
    run_once(manager)
    assert sum(1 for b in briefs if "You are the reviewer" in b) == 1
    manager.close()


def test_a_held_reviewer_hands_the_review_to_rem_in_auto_mode(tmp_path: Path) -> None:
    manager = start(tmp_path, reviewer=Reviewer(approve("Fine.")))
    org = company(
        manager,
        tmp_path,
        auto=True,
        employees=[
            {"id": "lead", "name": "Avery Lane", "paused": True},
            {"id": "mira", "name": "Mira Santos"},
        ],
    )
    assign(manager, org, "lead")
    made = act(manager, org, "task_create", title="Ship", assignee="mira", reviewer="lead")
    hand_in(manager, org, made["id"])
    assert needs_review(manager.store.get("", org["id"]))
    review_pass(manager, org["projectRoot"], org["id"])
    assert ticket(manager, org, made["id"])["status"] == "done"
    manager.close()


def test_without_auto_mode_a_stuck_ticket_counts_for_you_right_away(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    block(manager, org, made["id"], FAILED)
    assert sidebar_count(manager, org) == 1
    assert made["id"] in read(manager, org)["diagnostics"]["attention"]
    manager.close()
