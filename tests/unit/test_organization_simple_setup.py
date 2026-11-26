"""Simpler Organizations setup and the edge cases around idle, restarted and failing workers.

- Workers the user created or added never got projects, so they never checked in, pulled
  Backlog or reviewed on their own. Only the board's People view could give them projects.
- A worker without projects but with a heartbeat setting showed a next check-in time that
  never came.
- A crash during a check-in left it waiting for the user's review and counted it as a
  failed check-in, so three crashes stopped check-ins until the user resumed them.
- A check-in that hit a temporary provider problem counted as a failure too, so a provider
  outage of a few hours stalled every worker's check-ins until the user resumed them.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.ui.organization_store import has_assignment, self_work_assignment
from gofer.ui.organizations import RESTART_REVIEW_REASON, OrganizationManager
from tests.helpers.organizations import act, assign_lead


def start(tmp_path: Path, stream: Any = None) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=stream or quick, start_runtime=False)


async def quick(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


# Not the shared company: no project, and it creates as any actor with following on or off.
def company(
    manager: OrganizationManager,
    root: Path,
    actor: str = "user",
    follow: bool = True,
    **config: Any,
) -> Any:
    employees = config.pop(
        "employees",
        [
            {"id": "lead", "name": "Avery Lane"},
            {"id": "mira", "name": "Mira Santos", "reportsTo": "lead"},
        ],
    )
    params: dict[str, Any] = {
        "config": {"name": "Studio", "slug": "studio", "employees": employees, **config}
    }
    if follow:
        params["followProjects"] = True
    return manager.call(str(root), actor, {"action": "create", "params": params})


def state(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return manager.store.get("", org["id"])


def projects(manager: OrganizationManager, org: dict[str, Any], eid: str) -> dict[str, Any]:
    return self_work_assignment(state(manager, org)["runtime"], eid)


def configure(
    manager: OrganizationManager,
    org: dict[str, Any],
    actor: str = "user",
    follow: bool = True,
    **changes: Any,
) -> Any:
    current = state(manager, org)
    params: dict[str, Any] = {
        "config": {**current["config"], **changes},
        "expectedRevision": current["revision"],
        "reason": "Test change",
    }
    if follow:
        params["followProjects"] = True
    return act(manager, org, "configure", actor, **params)


def settle(manager: OrganizationManager, seconds: float = 10) -> None:
    until = time.monotonic() + seconds
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def due_now(manager: OrganizationManager, org: dict[str, Any], eid: str = "lead") -> None:
    def change(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["heartbeats"][eid] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", change)


def checkins(manager: OrganizationManager, org: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in state(manager, org)["runtime"]["tasks"] if t["heartbeat"]]


# ------------------------------------------------------------------ workers get projects


def test_the_users_create_gives_every_worker_the_organizations_folders(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    current = state(manager, org)
    for eid in ("lead", "mira"):
        saved = projects(manager, org, eid)
        assert saved == {"projects": [str(tmp_path)], "revision": 1, "follows": True}
        assert has_assignment(current, eid)
    manager.close()


def test_creating_without_asking_leaves_workers_without_projects(tmp_path: Path) -> None:
    # The CLI, imports and older callers don't ask, and nothing changes for them.
    manager = start(tmp_path)
    org = company(manager, tmp_path, follow=False)
    assert projects(manager, org, "lead") == {"projects": [], "revision": 0, "follows": False}
    manager.close()


def test_rem_and_agent_tokens_cannot_give_workers_projects(tmp_path: Path) -> None:
    manager = start(tmp_path)
    for actor in ("Rem:thread", "Rem:token:ci"):
        root = tmp_path / actor.replace(":", "-")
        root.mkdir()
        org = company(manager, root, actor=actor)
        assert projects(manager, org, "lead")["revision"] == 0
    manager.close()


def test_a_worker_rem_adds_gets_no_projects_even_when_the_user_approves(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    current = state(manager, org)
    employees = [*current["config"]["employees"], {"id": "nova", "name": "Nova Reed"}]
    asked = configure(manager, org, "Rem:thread", employees=employees)
    if asked.get("requestId"):
        act(manager, org, "request_decide", requestId=asked["requestId"], decision="approve")
    assert "nova" in {e["id"] for e in state(manager, org)["config"]["employees"]}
    # The request the user saw didn't name projects, so approving it gives none.
    assert projects(manager, org, "nova")["revision"] == 0
    manager.close()


def test_a_worker_the_user_adds_gets_the_folders_and_legacy_workers_stay(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, follow=False)
    current = state(manager, org)
    employees = [*current["config"]["employees"], {"id": "nova", "name": "Nova Reed"}]
    configure(manager, org, employees=employees)
    assert projects(manager, org, "nova")["projects"] == [str(tmp_path)]
    # Workers saved before stay as they were until the user gives them projects.
    assert projects(manager, org, "lead")["revision"] == 0
    manager.close()


def test_following_workers_get_new_folders_and_custom_picks_stay(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    # Mira's projects were picked by hand; giving her all folders keeps her following.
    act(
        manager,
        org,
        "self_work_projects",
        employeeId="mira",
        projects=[str(tmp_path)],
        expectedRevision=1,
    )
    assert projects(manager, org, "mira")["follows"] is True
    configure(manager, org, projectRoots=[str(tmp_path), str(other)])
    assert projects(manager, org, "lead")["projects"] == [str(tmp_path), str(other)]
    assert projects(manager, org, "mira")["projects"] == [str(tmp_path), str(other)]
    # A narrower pick is the user's choice and isn't widened by the next folder.
    act(
        manager,
        org,
        "self_work_projects",
        employeeId="mira",
        projects=[str(other)],
        expectedRevision=projects(manager, org, "mira")["revision"],
    )
    third = tmp_path / "third"
    third.mkdir()
    configure(manager, org, projectRoots=[str(tmp_path), str(other), str(third)])
    assert projects(manager, org, "mira") == {
        "projects": [str(other)],
        "revision": 4,
        "follows": False,
    }
    assert str(third) in projects(manager, org, "lead")["projects"]
    manager.close()


def test_workspace_grants_bound_what_a_following_worker_gets(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    manager = start(tmp_path)
    org = company(
        manager,
        tmp_path,
        employees=[{"id": "lead", "name": "Avery Lane", "workspacePaths": [str(other)]}],
        projectRoots=[str(tmp_path), str(other)],
    )
    assert projects(manager, org, "lead")["projects"] == [str(other)]
    manager.close()


def test_a_following_worker_without_folders_is_not_bounded_until_one_comes(
    tmp_path: Path,
) -> None:
    manager = OrganizationManager(tmp_path / "data", stream=quick, start_runtime=False)
    created = manager.call(
        "",
        "user",
        {
            "action": "create",
            "params": {
                "config": {"name": "Empty", "employees": [{"id": "lead", "name": "Avery Lane"}]},
                "followProjects": True,
            },
        },
    )
    org = {**created, "projectRoot": ""}
    current = state(manager, org)
    assert projects(manager, org, "lead")["follows"] is True
    # No folders yet, so the worker keeps the organization-wide reach for tickets.
    assert not has_assignment(current, "lead")
    configure(manager, org, projectRoots=[str(tmp_path)])
    assert projects(manager, org, "lead")["projects"] == [str(tmp_path)]
    assert has_assignment(state(manager, org), "lead")
    manager.close()


def test_removing_a_worker_drops_its_projects(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    current = state(manager, org)
    configure(
        manager,
        org,
        employees=[e for e in current["config"]["employees"] if e["id"] == "lead"],
    )
    assert "mira" not in state(manager, org)["runtime"]["selfWorkProjects"]
    manager.close()


# ------------------------------------------------------------------ stale check-in times


def test_a_worker_without_projects_shows_no_next_check_in(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(
        manager,
        tmp_path,
        follow=False,
        employees=[{"id": "lead", "name": "Avery Lane", "heartbeatSeconds": 900}],
    )
    act(manager, org, "control", state="running")
    manager.dispatch()
    read = act(manager, org, "read")
    beat = read["diagnostics"]["heartbeats"]["lead"]
    assert beat["nextAt"] is None and beat["unassigned"] is True
    assert "lead" not in read["runtime"]["heartbeats"]
    assert not checkins(manager, org)
    manager.close()


# ------------------------------------------------------------------ check-ins and restarts


def test_a_crash_during_a_check_in_closes_it_instead_of_asking_for_review(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, follow=False, employees=[{"id": "lead", "name": "Avery Lane"}])
    act(manager, org, "control", state="running")
    work = act(manager, org, "task_create", title="Real work", assignee="lead")

    def lose(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["tasks"].append(
            {
                **next(t for t in current["runtime"]["tasks"] if t["id"] == work["id"]),
                "id": "checkin-1",
                "title": "Check in",
                "heartbeat": True,
                "status": "in_progress",
                "claim": "lost-checkin",
                "comments": [],
            }
        )
        for task in current["runtime"]["tasks"]:
            if task["id"] == work["id"]:
                task.update(claim="lost-run", status="in_progress")
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", lose)
    manager.close()
    restarted = start(tmp_path)
    tasks = {t["id"]: t for t in state(restarted, org)["runtime"]["tasks"]}
    checkin = tasks["checkin-1"]
    assert checkin["status"] == "cancelled" and checkin["claim"] is None
    assert checkin["comments"][-1]["body"].startswith("Raticode stopped during this check-in")
    # Real work still waits for review, and only it is counted as interrupted.
    assert tasks[work["id"]]["blockReason"] == RESTART_REVIEW_REASON
    runtime = state(restarted, org)["runtime"]
    assert not runtime.get("heartbeatFailures", {}).get("lead")
    events = restarted.store.events("", org["id"])
    recovered = [e["payload"] for e in events if e["kind"] == "runtime_recovered"][-1]
    assert recovered["interrupted"] == 1
    restarted.close()


def test_a_provider_outage_never_stalls_check_ins(tmp_path: Path) -> None:
    async def overloaded(**options: Any) -> Any:
        raise RuntimeError("529 overloaded: the service is at capacity")
        yield {}

    manager = start(tmp_path, overloaded)
    org = company(
        manager,
        tmp_path,
        follow=False,
        employees=[{"id": "lead", "name": "Avery Lane", "heartbeatSeconds": 900}],
    )
    assign_lead(manager, org)
    act(manager, org, "control", state="running")
    for _ in range(5):
        due_now(manager, org)
        manager.dispatch()
        manager.dispatch()
        settle(manager)
    read = act(manager, org, "read")
    beat = read["diagnostics"]["heartbeats"]["lead"]
    # Before the fix, the fourth failure stopped check-ins until the user resumed them.
    assert beat["stalled"] is None and beat["failures"] == 0
    closed = checkins(manager, org)
    assert closed and all(t["status"] == "cancelled" for t in closed)
    assert "looks temporary" in closed[-1]["comments"][-1]["body"]
    # Nothing waits on the user, and check-ins come less often while the outage lasts.
    assert not [t for t in read["runtime"]["tasks"] if t.get("waitingOn") == "you"]
    assert read["runtime"]["quietCheckins"]["lead"] >= 3
    manager.close()


def test_a_check_in_that_fails_for_good_still_stops_and_says_so(tmp_path: Path) -> None:
    async def broken(**options: Any) -> Any:
        raise RuntimeError("Invalid API key")
        yield {}

    manager = start(tmp_path, broken)
    org = company(
        manager,
        tmp_path,
        follow=False,
        employees=[{"id": "lead", "name": "Avery Lane", "heartbeatSeconds": 900}],
    )
    assign_lead(manager, org)
    act(manager, org, "control", state="running")
    for _ in range(5):
        due_now(manager, org)
        manager.dispatch()
        manager.dispatch()
        settle(manager)
    beat = act(manager, org, "read")["diagnostics"]["heartbeats"]["lead"]
    assert beat["stalled"] and beat["stalled"]["reason"].startswith("The last run failed")
    manager.close()
