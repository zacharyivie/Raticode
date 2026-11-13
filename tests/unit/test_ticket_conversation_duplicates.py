"""An organization ticket's conversation shows each comment once (DESK-2, Nov 2026).

The worker's report and the user's "Accepted" each showed twice: the status entry written with
the move copied the comment just above it, and the views drew status entries as comments.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_ticket_activity as activity
from gofer.ui.organizations import OrganizationManager
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone

REPORT = (
    "I ran the permission trial in plan mode. Its done-when isn't met.\n\n"
    "| Capability | Result |\n|---|---|\n| Read the project | Worked |"
)


async def reporting_worker(**_: Any) -> Any:
    yield {"type": "final", "message": {"body": REPORT}}


@pytest.fixture
def desk(tmp_path: Path) -> Any:
    manager = OrganizationManager(tmp_path / "data", stream=reporting_worker, start_runtime=False)
    org = manager.call(
        str(tmp_path),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Isolated desk",
                    "employees": [{"id": "director", "name": "Director"}],
                }
            },
        },
    )

    def call(action: str, actor: str = "user", **params: Any) -> Any:
        return manager.call(
            str(tmp_path), actor, {"organizationId": org["id"], "action": action, "params": params}
        )

    yield manager, call
    manager.close()


def drain(manager: OrganizationManager) -> None:
    deadline = time.monotonic() + 5
    while manager._active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not manager._active


def ticket(call: Any, tid: str) -> dict[str, Any]:
    return next(t for t in call("read")["runtime"]["tasks"] if t["id"] == tid)


def bodies(comments: list[dict[str, Any]]) -> list[str]:
    return [str(c.get("body") or "") for c in comments if c.get("body")]


def test_a_run_and_its_acceptance_each_write_the_words_once(desk) -> None:
    manager, call = desk
    created = call("task_create", title="Permission trial", assignee="director")
    call("control", state="running")
    manager.dispatch()
    drain(manager)
    task = ticket(call, created["id"])
    if task["status"] != "in_review":
        task = call(
            "task_update",
            taskId=created["id"],
            expectedRevision=task["revision"],
            changes={"status": "in_review", "evidence": "Ran the trial"},
        )
    call(
        "review",
        taskId=created["id"],
        expectedRevision=task["revision"],
        decision="accept",
        body="Accepted",
    )
    stored = ticket(call, created["id"])["comments"]
    # The report and the acceptance are each stored once; the status entries only say what moved.
    assert bodies(stored).count(REPORT) == 1
    assert bodies(stored).count("Accepted") == 1
    statuses = [c for c in stored if c.get("kind") == "status"]
    assert [c["to"] for c in statuses][-1] == "done"
    assert all(not activity._repeats(c["body"], REPORT) for c in statuses if c["body"])
    view = activity.visible_comments(stored)
    assert [c.get("kind", "comment") for c in view] == [c.get("kind", "comment") for c in stored]


def test_a_report_posted_as_a_comment_is_not_added_again_as_the_final_message(desk) -> None:
    manager, call = desk
    created = call("task_create", title="Report twice", assignee="director")

    async def commenting_worker(**_: Any) -> Any:
        # The worker posts its report with the comment tool, then ends with the same words.
        org = call("read")
        claim = next(t["claim"] for t in org["runtime"]["tasks"] if t["id"] == created["id"])
        manager.call(
            "",
            "employee:director",
            {
                "organizationId": org["id"],
                "action": "comment",
                "params": {"taskId": created["id"], "body": REPORT},
            },
            employee_id="director",
            bound_org=org["id"],
            generation=org["runtime"]["generation"],
            run_id=claim,
        )
        yield {"type": "final", "message": {"body": REPORT}}

    manager.stream = commenting_worker
    call("control", state="running")
    manager.dispatch()
    drain(manager)
    stored = ticket(call, created["id"])["comments"]
    assert [c["actor"] for c in stored if c.get("body") == REPORT] == ["employee:director"]


# Stored conversations, read back --------------------------------------------------------------


DESK_2 = [
    {"actor": "employee:director", "at": "2026-11-11T20:05:36.001518+00:00", "body": REPORT},
    {
        "actor": "employee:director",
        "at": "2026-11-11T20:05:36.001833+00:00",
        "kind": "status",
        "from": "in_progress",
        "to": "in_review",
        "title": "Work complete, moved to In review",
        "body": "I ran the permission trial in plan mode. Its done-when isn't met.",
    },
    {
        "actor": "user",
        "at": "2026-11-11T20:09:12.379912+00:00",
        "body": "Accepted",
        "decision": "accept",
    },
    {
        "actor": "user",
        "at": "2026-11-11T20:09:12.380346+00:00",
        "kind": "status",
        "from": "in_review",
        "to": "done",
        "title": "Verified complete, moved to Done",
        "body": "Accepted",
    },
]


def test_stored_status_entries_that_repeat_a_comment_lose_only_the_repeated_text() -> None:
    view = activity.visible_comments(DESK_2)
    assert [(c.get("kind", "comment"), c["body"]) for c in view] == [
        ("comment", REPORT),
        ("status", ""),
        ("comment", "Accepted"),
        ("status", ""),
    ]
    assert view[1]["title"] == "Work complete, moved to In review"
    # Nothing stored changes.
    assert DESK_2[1]["body"] and DESK_2[3]["body"] == "Accepted"


def test_a_clipped_recap_still_counts_as_a_repeat() -> None:
    long_report = "Ported the importer. " * 30
    recap = long_report[:300].rsplit(" ", 1)[0] + "…"
    view = activity.visible_comments(
        [
            {"actor": "employee:mira", "at": "2026-01-01T00:00:00+00:00", "body": long_report},
            {
                "actor": "employee:mira",
                "at": "2026-01-01T00:00:00+00:00",
                "kind": "status",
                "to": "in_review",
                "title": "Work complete",
                "body": recap,
            },
        ]
    )
    assert view[1]["body"] == ""


def test_a_status_entry_with_its_own_reason_keeps_it() -> None:
    comments = [
        {"actor": "employee:mira", "at": "2026-01-01T00:00:00+00:00", "body": "Started."},
        {
            "actor": "system",
            "at": "2026-01-01T00:01:00+00:00",
            "kind": "status",
            "to": "blocked",
            "title": "Blocked: task failed",
            "body": "The last run failed; see the conversation",
        },
        # Only comments since the last status entry count: an older one isn't "just above".
        {
            "actor": "system",
            "at": "2026-01-01T00:02:00+00:00",
            "kind": "status",
            "to": "todo",
            "title": "Unblocked",
            "body": "Started.",
        },
    ]
    view = activity.visible_comments(comments)
    assert [c["body"] for c in view] == [
        "Started.",
        "The last run failed; see the conversation",
        "Started.",
    ]


def test_exact_retries_are_hidden_but_said_again_later_is_kept() -> None:
    comments = [
        {"actor": "user", "at": "2026-01-01T00:00:00+00:00", "body": "Ping"},
        {"actor": "user", "at": "2026-01-01T00:00:01+00:00", "body": "Ping"},
        # Someone else saying the same is their own comment.
        {"actor": "employee:mira", "at": "2026-01-01T00:00:02+00:00", "body": "Ping"},
        # The same person an hour later is a new comment.
        {"actor": "employee:mira", "at": "2026-01-01T01:00:02+00:00", "body": "Ping"},
        # A review decision on the same words is a different act.
        {"actor": "user", "at": "2026-01-01T01:00:03+00:00", "body": "Ping", "decision": "accept"},
    ]
    view = activity.visible_comments(comments)
    assert [(c["actor"], c["at"][11:19]) for c in view] == [
        ("user", "00:00:00"),
        ("employee:mira", "00:00:02"),
        ("employee:mira", "01:00:02"),
        ("user", "01:00:03"),
    ]


def test_new_status_entries_do_not_copy_the_comment_above() -> None:
    final = {"actor": "employee:mira", "at": "x", "body": "Ported it; 12 tests pass."}
    review = {"actor": "user", "at": "y", "body": "Needs the CSV path.", "decision": "revise"}
    reviewed: dict[str, Any] = {
        "id": "o1",
        "title": "Port",
        "status": "in_review",
        "comments": [final],
        "dependsOn": [],
        "blockReason": "",
    }
    org: dict[str, Any] = {"config": {"employees": []}, "runtime": {"tasks": [reviewed]}}
    activity.note_organization_transitions(org, {"o1": "in_progress"}, "employee:mira")
    assert reviewed["comments"][-1]["body"] == ""
    reviewed["comments"].append(review)
    reviewed["status"] = "todo"
    activity.note_organization_transitions(org, {"o1": "in_review"}, "user")
    assert reviewed["comments"][-1]["title"] == "Sent back to To do: changes requested"
    assert reviewed["comments"][-1]["body"] == ""
    # With no report in the conversation, the entry still says what the run left.
    bare: dict[str, Any] = {
        "id": "o2",
        "title": "Bare",
        "status": "in_review",
        "comments": [],
        "dependsOn": [],
        "blockReason": "",
        "evidence": "Proof is in out.txt.",
    }
    org["runtime"]["tasks"] = [bare]
    activity.note_organization_transitions(org, {"o2": "in_progress"}, "employee:mira")
    assert bare["comments"][-1]["body"] == "Proof is in out.txt."


# The phone ---------------------------------------------------------------------------------------


def test_the_phone_gets_each_comment_once_with_status_entries_marked(phone, tmp_path) -> None:
    app, peer, context, send = phone
    app.desktop.enable(peer, True)

    async def idle(**_: Any) -> Any:
        if False:
            yield {}

    manager = OrganizationManager(tmp_path / "orgs", stream=idle, start_runtime=False)
    root = context["projects"][0]["root"]
    try:
        app.desktop.organizations = manager
        org = manager.call(
            root,
            "user",
            {
                "action": "create",
                "params": {
                    "config": {
                        "name": "Isolated desk",
                        "projectRoots": [root],
                        "employees": [{"id": "director", "name": "Director"}],
                    }
                },
            },
        )
        oid = org["id"]
        created = manager.call(
            root,
            "user",
            {
                "organizationId": oid,
                "action": "task_create",
                "params": {"title": "Trial", "assignee": "director"},
            },
        )

        def legacy(current: dict[str, Any]) -> dict[str, Any]:
            # What older Raticode stored for DESK-2: each status entry copied the comment above.
            task = next(t for t in current["runtime"]["tasks"] if t["id"] == created["id"])
            task["comments"].extend(DESK_2)
            return {"seeded": True}

        manager.store.mutate(root, oid, "system", "test_seed", legacy)
        value = send("desktop.read", "organizations.read", {"organization_id": oid})["value"]
        task = next(t for t in value["tasks"] if t["task_id"] == created["id"])
        assert [(c.get("kind", "comment"), c["body"]) for c in task["comments"]] == [
            ("comment", REPORT),
            ("status", ""),
            ("comment", "Accepted"),
            ("status", ""),
        ]
        assert task["comments"][1]["title"] == "Work complete, moved to In review"
        assert task["comments"][3]["to"] == "done" and task["comments"][2]["actor"] == "You"
        assert task["comment_count"] == 4
        # The stored conversation is untouched.
        stored = next(
            t for t in manager.store.get("", oid)["runtime"]["tasks"] if t["id"] == created["id"]
        )
        assert [c["body"] for c in stored["comments"]][-1] == "Accepted"
    finally:
        manager.close()
