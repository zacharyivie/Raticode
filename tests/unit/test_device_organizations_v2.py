"""The five-section Organizations view and its actions from a paired phone."""

import copy
import json
from typing import Any

import pytest

from gofer.ui.organizations import OrganizationManager
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone


@pytest.fixture
def org(phone, tmp_path):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)

    async def stream(**_: Any) -> Any:
        if False:
            yield {}

    manager = OrganizationManager(tmp_path / "orgs", stream=stream, start_runtime=False)
    root = context["projects"][0]["root"]
    app.desktop.organizations = manager
    created = manager.call(
        root,
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Eng",
                    "mission": "Ship a calm phone app",
                    "projectRoots": [root],
                    "employees": [
                        {"id": "lead", "name": "Lead", "title": "CTO", "persona": "Careful"},
                        {
                            "id": "dev",
                            "name": "Dev",
                            "reportsTo": "lead",
                            "responsibilities": ["Builds"],
                            "paused": True,
                        },
                    ],
                }
            },
        },
    )
    oid = created["id"]

    def plan(kind: str, item: dict[str, Any]) -> None:
        current = manager.store.get("", oid)
        manager.call(
            "",
            "user",
            {
                "action": "plan_save",
                "organizationId": oid,
                "params": {"kind": kind, "item": item, "expectedRevision": current["revision"]},
            },
        )

    plan("project", {"id": "app", "name": "Phone app", "workspacePath": root, "owner": "lead"})
    plan("milestone", {"id": "beta", "title": "Beta", "dueAt": "2026-12-01"})
    plan("milestone", {"id": "m1", "title": "Logs", "projectId": "app", "parentId": "beta"})
    plan("epic", {"id": "e1", "title": "Developer log", "milestoneId": "m1", "projectId": "app"})
    try:
        yield app, peer, root, send, manager, oid
    finally:
        manager.close()


def read(send, oid: str) -> dict[str, Any]:
    value: dict[str, Any] = send("desktop.read", "organizations.read", {"organization_id": oid})[
        "value"
    ]
    return value


def test_the_read_carries_all_five_sections_without_paths(org):
    app, _, root, send, manager, oid = org
    assert send("desktop.read", "overview")["value"]["organizations_v2"] is True
    manager.call(
        "",
        "user",
        {
            "action": "task_create",
            "organizationId": oid,
            "params": {"title": "Follow the log", "epicId": "e1", "project": "app"},
        },
    )
    view = read(send, oid)
    assert view["v2"] and view["mission"] == "Ship a calm phone app"
    assert {m["milestone_id"] for m in view["milestones"]} == {"beta", "m1"}
    assert view["epics"][0]["milestone_id"] == "m1"
    assert view["initiatives"][0]["folder"] == "Notes"
    ticket = view["tasks"][0]
    assert ticket["epic_id"] == "e1" and ticket["project_id"] == "app" and ticket["key"]
    assert ticket["evidence_counts"] == {"ok": 0, "warn": 0, "bad": 0, "none": 0, "pending": 0}
    workers = {w["employee_id"]: w for w in view["workers"]}
    assert workers["lead"]["persona"] == "Careful" and workers["dev"]["responsibilities"] == [
        "Builds"
    ]
    employees = {e["employee_id"]: e for e in view["employees"]}
    assert employees["dev"]["reports_to"] == "lead"
    assert root not in json.dumps(view)


def test_tickets_move_like_the_desktop_board(org):
    _, _, _, send, manager, oid = org
    created = send(
        "desktop.action",
        "organizations.task",
        {
            "organization_id": oid,
            "action": "create",
            "title": "Filter levels",
            "status": "backlog",
            "epic_id": "e1",
            "project_id": "app",
        },
    )["value"]
    ticket = created["tasks"][0]
    assert ticket["status"] == "backlog" and ticket["epic_id"] == "e1"

    def move(column: str, revision: int) -> dict[str, Any]:
        answer: dict[str, Any] = send(
            "desktop.action",
            "organizations.task",
            {
                "organization_id": oid,
                "action": "move",
                "task_id": ticket["task_id"],
                "expected_revision": revision,
                "column": column,
            },
        )
        return answer

    todo = move("todo", ticket["revision"])["value"]["tasks"][0]
    assert todo["status"] == "todo"
    # In progress queues it first for its worker; the desktop keeps it in To do until it runs.
    pulled = move("running", todo["revision"])["value"]["tasks"][0]
    assert pulled["status"] == "todo" and pulled["revision"] > todo["revision"]
    assert move("todo", todo["revision"])["code"] == "conflict"
    assert move("review", pulled["revision"])["code"] == "invalid"
    assert move("done", pulled["revision"])["code"] == "invalid"
    # Done work reopens on its way back to Backlog.
    current = manager.store.get("", oid)["runtime"]["tasks"][0]
    manager.call(
        "",
        "user",
        {
            "action": "task_update",
            "organizationId": oid,
            "params": {
                "taskId": current["id"],
                "expectedRevision": current["revision"],
                "changes": {"status": "cancelled"},
            },
        },
    )
    cancelled = manager.store.get("", oid)["runtime"]["tasks"][0]
    back = move("backlog", cancelled["revision"])["value"]["tasks"][0]
    assert back["status"] == "backlog"
    refused = send(
        "desktop.action",
        "organizations.task",
        {"organization_id": oid, "action": "create", "title": "x", "status": "in_progress"},
    )
    assert refused["code"] == "invalid"


def test_notes_are_created_and_read_one_at_a_time(org):
    _, _, _, send, manager, oid = org
    view = send(
        "desktop.action",
        "organizations.knowledge",
        {
            "organization_id": oid,
            "action": "create",
            "kind": "finding",
            "title": "Phone drops on sleep",
            "topic": "Mobile",
            "body": "Seen after the screen turns off for a minute.",
            "project_id": "app",
        },
    )["value"]
    note = view["notes"][0]
    assert note["kind"] == "finding" and note["topic"] == "Mobile" and note["author"] == "You"
    assert "body" not in note
    other = manager.call(
        "",
        "user",
        {
            "action": "knowledge_save",
            "organizationId": oid,
            "params": {
                "entry": {
                    "kind": "decision",
                    "title": "Keep the link alive",
                    "body": "Reconnect when the screen turns on.",
                    "links": [note["note_id"]],
                }
            },
        },
    )
    full = send(
        "desktop.read", "organizations.note", {"organization_id": oid, "note_id": other["id"]}
    )["value"]
    assert full["body"].startswith("Reconnect") and full["links"] == [note["note_id"]]
    assert (
        send("desktop.read", "organizations.note", {"organization_id": oid, "note_id": "missing"})[
            "ok"
        ]
        is False
    )
    bad = send(
        "desktop.action",
        "organizations.knowledge",
        {"organization_id": oid, "action": "create", "kind": "secret", "title": "x"},
    )
    assert bad["code"] == "invalid"
    assert (
        send(
            "desktop.action",
            "organizations.knowledge",
            {"organization_id": oid, "action": "archive", "title": "x"},
        )["code"]
        == "invalid"
    )


def test_workers_are_held_and_released_but_never_widened(org):
    _, _, _, send, manager, oid = org

    def worker(employee: str, paused: bool) -> dict[str, Any]:
        answer: dict[str, Any] = send(
            "desktop.action",
            "organizations.worker",
            {"organization_id": oid, "employee_id": employee, "paused": paused},
        )
        return answer

    held = worker("lead", True)["value"]
    assert {e["employee_id"]: e["paused"] for e in held["employees"]}["lead"] is True
    released = worker("lead", False)["value"]
    assert {e["employee_id"]: e["paused"] for e in released["employees"]}["lead"] is False
    current = manager.store.get("", oid)
    manager.call(
        "",
        "user",
        {
            "action": "configure",
            "organizationId": oid,
            "params": {
                "config": {
                    **current["config"],
                    "employees": [
                        {**e, "permissionMode": "danger-full-access"} if e["id"] == "dev" else e
                        for e in current["config"]["employees"]
                    ],
                },
                "expectedRevision": current["revision"],
                "reason": "Full access",
            },
        },
    )
    # The phone is the user: it releases a full-access worker's hold as the desktop can.
    released = worker("dev", False)["value"]
    assert {e["employee_id"]: e["paused"] for e in released["employees"]}["dev"] is False
    assert manager.store.get("", oid)["config"]["employees"][1]["paused"] is False
    assert worker("nobody", True)["code"] == "not_found"
    assert (
        send(
            "desktop.action",
            "organizations.worker",
            {"organization_id": oid, "employee_id": "lead", "paused": "yes"},
        )["code"]
        == "invalid"
    )


def test_the_phone_reads_and_edits_the_work_schedule(org):
    _, _, _, send, manager, oid = org
    overview = send("desktop.read", "overview", {})["value"]
    assert overview["organization_schedule"] is True
    before = read(send, oid)
    schedule = before["schedule"]
    # Nothing is applied by itself; the suggested schedule comes along to start from.
    assert schedule["configured"] is False and schedule["blocks"] == []
    assert schedule["current"] is None and schedule["timezone"] == "UTC"
    assert [b["label"] for b in schedule["template"]][:2] == [
        "New feature work",
        "Application hardening",
    ]
    assert schedule["template"][5]["nth_week"] == 1 and schedule["template"][5]["weekday"] == 0
    blocks = [{**b, "focus": b["focus"] or "Keep going"} for b in schedule["template"]]
    saved = send(
        "desktop.action",
        "organizations.schedule",
        {
            "organization_id": oid,
            "expected_revision": before["revision"],
            "timezone": "America/New_York",
            "blocks": blocks,
        },
    )
    assert saved["ok"] is True
    after = saved["value"]["schedule"]
    assert after["configured"] is True and after["timezone"] == "America/New_York"
    assert [b["when"] for b in after["blocks"]][4:] == [
        "Fridays 13:00–17:00",
        "Monthly, 1st Monday, 09:00–12:00",
        "Quarterly, 1st Tuesday of the 1st month, 09:00–17:00",
    ]
    assert after["current"] is not None and after["next"] is not None
    config = manager.store.get("", oid)["config"]
    assert config["operatingHours"]["mode"] == "always"
    assert config["operatingHours"]["timezone"] == "America/New_York"
    assert config["workSchedule"]["blocks"][5]["nthWeek"] == 1
    assert manager.store.get("", oid)["runtime"]["state"] == "paused"
    # A stale revision and an unordered overlap are refused with readable reasons.
    stale = send(
        "desktop.action",
        "organizations.schedule",
        {"organization_id": oid, "expected_revision": before["revision"], "blocks": []},
    )
    assert stale["ok"] is False and stale["code"] == "conflict"
    clash = send(
        "desktop.action",
        "organizations.schedule",
        {
            "organization_id": oid,
            "expected_revision": saved["value"]["revision"],
            "blocks": [
                {"block_id": "a", "label": "A", "start": "08:00", "end": "12:00"},
                {"block_id": "b", "label": "B", "start": "10:00", "end": "14:00"},
            ],
        },
    )
    assert clash["ok"] is False and "neither takes precedence" in clash["detail"]


def test_the_phone_gets_whole_current_and_next_blocks_and_plain_refusals(org):
    _, _, _, send, manager, oid = org
    before = read(send, oid)
    allday = [
        {"block_id": "all", "label": "All day", "start": "00:00", "end": "00:00"},
        {"block_id": "lunch", "label": "Lunch", "start": "12:00", "end": "13:00"},
    ]
    saved = send(
        "desktop.action",
        "organizations.schedule",
        {"organization_id": oid, "expected_revision": before["revision"], "blocks": allday},
    )["value"]["schedule"]
    for view in (saved["current"], saved["next"]):
        assert view is not None and view["start"] and view["end"] and view["when"]
    clash = send(
        "desktop.action",
        "organizations.schedule",
        {
            "organization_id": oid,
            "expected_revision": before["revision"] + 1,
            "blocks": [{**allday[1], "block_id": "a"}, {**allday[1], "block_id": "b"}],
        },
    )
    assert clash["detail"] == (
        "Lunch and Lunch overlap and neither takes precedence: they repeat on the same "
        "cadence and last equally long. Change one's times, or its cadence."
    )


def test_the_phone_turns_backlog_pulls_on_and_off(org):
    _, _, _, send, manager, oid = org
    overview = send("desktop.read", "overview", {})["value"]
    assert overview["organization_backlog_pull"] is True
    before = read(send, oid)
    # On unless turned off.
    assert before["pull_backlog"] is True
    saved = send(
        "desktop.action",
        "organizations.backlog_pull",
        {"organization_id": oid, "expected_revision": before["revision"], "enabled": False},
    )
    assert saved["ok"] is True and saved["value"]["pull_backlog"] is False
    assert manager.store.get("", oid)["config"]["pullBacklog"] is False
    history = manager.call("", "user", {"action": "history", "organizationId": oid, "params": {}})
    assert history[0]["reason"] == (
        "Workers leave Backlog tickets for you to move, set from the phone"
    )
    # A stale revision and a value that isn't true or false are refused plainly.
    stale = send(
        "desktop.action",
        "organizations.backlog_pull",
        {"organization_id": oid, "expected_revision": before["revision"], "enabled": True},
    )
    assert stale["ok"] is False and stale["code"] == "conflict"
    vague = send(
        "desktop.action",
        "organizations.backlog_pull",
        {"organization_id": oid, "expected_revision": saved["value"]["revision"], "enabled": "on"},
    )
    assert vague["ok"] is False and vague["code"] == "invalid"
    again = send(
        "desktop.action",
        "organizations.backlog_pull",
        {"organization_id": oid, "expected_revision": saved["value"]["revision"], "enabled": True},
    )
    assert again["ok"] is True and again["value"]["pull_backlog"] is True


def _ask(manager: OrganizationManager, oid: str, action: str, **params: Any) -> str:
    asked = manager.call(
        "", "Rem:thread", {"action": action, "organizationId": oid, "params": params}
    )
    assert asked["needsUserConsent"] is True
    return str(asked["requestId"])


def _requests(manager: OrganizationManager, oid: str) -> Any:
    return manager.store.get("", oid)["runtime"]["consentRequests"]


def test_the_phone_approves_exactly_the_requests_it_showed(org):
    _, _, _, send, manager, oid = org
    assert send("desktop.read", "overview", {})["value"]["organization_requests"] is True
    task = manager.call(
        "",
        "user",
        {
            "action": "task_create",
            "organizationId": oid,
            "params": {"title": "Charter", "assignee": "lead", "approvalRequired": True},
        },
    )
    start = _ask(manager, oid, "control", state="running")
    ungate = _ask(
        manager,
        oid,
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"approvalRequired": False},
    )
    view = read(send, oid)
    # Oldest first, each with the ID the phone sends back.
    assert [r["request_id"] for r in view["requests"]] == [start, ungate]
    shown = [r["request_id"] for r in view["requests"]]
    later = _ask(manager, oid, "archive", archived=True)

    done = send(
        "desktop.action",
        "organizations.requests",
        {"organization_id": oid, "decision": "approve", "request_ids": shown},
    )

    assert done["ok"] is True
    results = done["value"]["request_results"]
    # The ticket change applied before the start, and each says how it went.
    assert [(r["request_id"], r["status"]) for r in results] == [
        (ungate, "approved"),
        (start, "approved"),
    ]
    assert done["value"]["state"] == "running"
    assert [r["request_id"] for r in done["value"]["requests"]] == [later]
    assert _requests(manager, oid)[later]["status"] == "pending"
    manager.call(
        "", "user", {"action": "control", "organizationId": oid, "params": {"state": "paused"}}
    )


def test_the_phone_decides_requests_like_the_desktop_including_unsandboxed_ones(org):
    _, _, _, send, manager, oid = org
    current = manager.store.get("", oid)
    config = json.loads(json.dumps(current["config"]))
    config["employees"][0]["permissionMode"] = "danger-full-access"
    risky = _ask(
        manager, oid, "configure", config=config, expectedRevision=current["revision"], reason="x"
    )
    start = _ask(manager, oid, "control", state="running")
    other = _ask(manager, oid, "resume_all", expectedRevision=current["revision"])

    declined = send(
        "desktop.action",
        "organizations.requests",
        {"organization_id": oid, "decision": "decline", "request_ids": [other]},
    )
    assert declined["value"]["request_results"] == [
        {"request_id": other, "summary": "", "status": "declined", "message": ""}
    ]
    assert _requests(manager, oid)[other]["status"] == "declined"

    done = send(
        "desktop.action",
        "organizations.requests",
        {"organization_id": oid, "decision": "approve", "request_ids": [start, risky]},
    )
    by_id = {r["request_id"]: r for r in done["value"]["request_results"]}
    # A phone with Use Raticode from this phone on is the user: no phone-only holds.
    assert by_id[risky]["status"] == "approved", by_id[risky]
    assert by_id[start]["status"] == "approved", by_id[start]
    after = manager.store.get("", oid)
    assert after["runtime"]["state"] == "running"
    assert after["config"]["employees"][0].get("permissionMode") == "danger-full-access"
    assert _requests(manager, oid)[risky]["status"] == "approved"


@pytest.mark.parametrize(
    "params",
    [
        {"decision": "approve", "request_ids": []},
        {"decision": "approve", "request_ids": ["a", "a"]},
        {"decision": "approve", "request_ids": "a"},
        {"decision": "maybe", "request_ids": ["a"]},
    ],
)
def test_the_phone_request_list_is_checked(org, params):
    _, _, _, send, _, oid = org
    refused = send("desktop.action", "organizations.requests", {"organization_id": oid, **params})
    assert refused["ok"] is False and refused["code"] == "invalid"


def test_the_phone_turns_auto_mode_on_only_after_confirming(org):
    _, _, _, send, manager, oid = org
    overview = send("desktop.read", "overview", {})["value"]
    assert overview["organization_auto_mode"] is True
    before = read(send, oid)
    assert before["auto_mode"] is False and before["auto_halt"] is None
    assert before["auto_decisions"] == []
    unconfirmed = send(
        "desktop.action",
        "organizations.auto_mode",
        {"organization_id": oid, "expected_revision": before["revision"], "enabled": True},
    )
    assert unconfirmed["ok"] is False and unconfirmed["code"] == "invalid"
    assert manager.store.get("", oid)["config"]["autoMode"] is False
    on = send(
        "desktop.action",
        "organizations.auto_mode",
        {
            "organization_id": oid,
            "expected_revision": before["revision"],
            "enabled": True,
            "confirm": True,
        },
    )
    assert on["ok"] is True and on["value"]["auto_mode"] is True
    # Rem's requests now wait on Rem's review, and the phone says so.
    config = copy.deepcopy(manager.store.get("", oid)["config"])
    config.pop("packageFiles", None)
    config["employees"][0]["paused"] = not config["employees"][0].get("paused")
    asked = manager.call(
        "",
        "Rem:thread",
        {
            "action": "configure",
            "organizationId": oid,
            "params": {
                "config": config,
                "expectedRevision": on["value"]["revision"],
                "reason": "x",
            },
        },
    )
    shown = read(send, oid)
    if asked.get("requestId"):
        request = next(r for r in shown["requests"] if r["request_id"] == asked["requestId"])
        assert request["waiting_on"] == "rem"
    off = send(
        "desktop.action",
        "organizations.auto_mode",
        {"organization_id": oid, "expected_revision": shown["revision"], "enabled": False},
    )
    assert off["ok"] is True and off["value"]["auto_mode"] is False
