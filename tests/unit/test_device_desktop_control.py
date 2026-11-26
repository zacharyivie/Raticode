"""Using Raticode from a phone: opt-in, path-free results and desktop-owned actions."""

import base64
import errno
import hashlib
import json
import logging
import re
import secrets
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices import desktop_control as module
from gofer.devices import wake
from gofer.devices.control import DeviceControl
from gofer.devices.registry import PairingError
from gofer.devices.remote_control import swarm_wire_id
from gofer.ui.organizations import OrganizationManager
from gofer.ui.rem_kanban import add_report, new_ticket, put_ticket, read_tickets
from tests.unit.test_device_application import TestSecrets, event
from tests.unit.test_device_application import application as application
from tests.unit.test_device_parity import parity
from tests.unit.test_device_remote_control import Swarms
from tests.unit.test_rem_home import home as home


class RemStore:
    def __init__(self, root: str) -> None:
        self.messages: list[dict[str, Any]] = [
            {"id": "m1", "role": "user", "body": "Check builds", "created_at": "t1"},
            {
                "id": "m2",
                "role": "rem",
                "body": "Started Build check.",
                "created_at": "t2",
                "work": {
                    "task_id": "task1",
                    "run_id": "run1",
                    "thread_id": "thread1",
                    "title": "Build check",
                    "config": {"grantId": "secret-grant"},
                },
            },
        ]
        self.task = {
            "id": "task1",
            "title": "Build check",
            "type": "maintain",
            "prompt": "Look for failed builds",
            "project_dir": root,
            "state": "enabled",
            "revision": 3,
            "schedule": {"kind": "weekly", "local_time": "09:00", "weekdays": ["monday"]},
            "acceptance_criteria": [{"description": "Report failures"}],
            "active_run": {
                "id": "run1",
                "state": "executing",
                "config": {"grantId": "g"},
                "thread_id": "thread1",
            },
            "last_run": None,
            "recent_runs": [],
        }
        self.mutations: list[tuple[str, str]] = []
        self.config: dict[str, Any] = {"projects": [{"root": root}]}

    @contextmanager
    def read(self) -> Iterator[None]:
        yield None

    def setting(self, _db: None, key: str) -> dict[str, Any]:
        assert key in {"config", "conversation"}
        return self.config if key == "config" else self.conversation_setting

    def conversation(self, limit: int = 200) -> dict[str, Any]:
        shown = self.messages[-limit:] if limit > 0 else []
        return {
            "conversation": {"turn_state": "idle", "provider": "codex"},
            "messages": shown,
            "earlier": len(self.messages) - len(shown),
        }

    def find_message(self, identity: str, *, waiting: bool = False) -> dict[str, Any] | None:
        return next((m for m in self.messages if m.get("id") == identity), None)

    def tail(self, limit: int, *, roles: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        kept = [m for m in self.messages if not roles or m.get("role") in roles]
        return kept[-limit:] if limit > 0 else []

    def tasks(self, *, summary: bool = False) -> list[dict[str, Any]]:
        # The phone never needs run traces or worker config.
        assert summary
        return [self.task]

    def task_detail(self, identity: str, *, summary: bool = False) -> dict[str, Any]:
        # rem.task reads only the task the phone opened.
        found = next((t for t in self.tasks(summary=summary) if t.get("id") == identity), None)
        if found is None:
            raise KeyError(identity)
        return found

    def thread_ids(self) -> list[str]:
        return ["thread1"]

    def thread(self, identifier: str) -> dict[str, Any]:
        if identifier != "thread1":
            raise KeyError(identifier)
        return {
            "metadata": {"title": "Build check"},
            "running": True,
            "messages": [
                {"id": "t1", "role": "assistant", "body": "Working"},
                {"id": "t2", "role": "system", "body": "internal"},
            ],
        }

    def mutate_run(self, identity: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
        self.mutations.append((action, identity))
        return {}

    def dismiss_updates(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.mutations.append(("dismiss_updates", payload))
        return {"dismissed": [], "count": 0, "needs_you": 0}

    conversation_setting: dict[str, Any] = {"turn_state": "idle"}


class Rem:
    def __init__(self, root: str) -> None:
        self.store = RemStore(root)
        self.calls: list[tuple[str, Any]] = []

    def submit_message(
        self, payload: dict[str, Any], *, source: str | None = None
    ) -> dict[str, Any]:
        assert source == "phone"
        self.calls.append(("message", payload))
        return {"accepted": True}

    def instruct(self, thread: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("instruct", (thread, payload)))
        return {}

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create", payload))
        return {}

    def edit_task(self, task: str, payload: dict[str, Any], action: str) -> dict[str, Any]:
        self.calls.append((action, (task, payload)))
        return {}

    def retry_review(self, run: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("retry_review", (run, payload)))
        return {}

    def pause_run(self, run: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("pause_run", (run, payload)))
        return {}

    def resume_run(self, run: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("resume_run", (run, payload)))
        return {}


@pytest.fixture
def phone(application, tmp_path):
    app, peer = application
    context, _, _, _ = parity(app, peer, tmp_path)
    root = context["projects"][0]["root"]
    app.desktop.rem = Rem(root)
    sequence = iter(range(10, 1000))

    def send(kind: str, op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        replies = app.handle(
            peer,
            event(kind, next(sequence), thread_id=None, payload={"op": op, "params": params or {}}),
        )
        results = [r for r in replies if r["type"] == "desktop.result"]
        if not results:
            # Refused before the desktop looked at it, such as by the protocol schema.
            [error] = [r["payload"] for r in replies if r["type"] == "error"]
            return {"ok": False, "code": error["code"], "detail": error["detail"]}
        first = results[0]["payload"]
        if not first["ok"]:
            return {"ok": False, "code": first["code"], "detail": first["detail"]}
        assert [r["payload"]["part"] for r in results] == list(range(first["parts"]))
        return {"ok": True, "value": json.loads("".join(r["payload"]["body"] for r in results))}

    return app, peer, context, send


def test_desktop_control_is_off_until_the_user_allows_it(phone):
    app, peer, _, send = phone
    refused = send("desktop.read", "rem.home")
    assert refused == {"ok": False, "code": "unauthorized", "detail": module.OFF}
    assert send("desktop.action", "rem.message", {"body": "hi"})["code"] == "unauthorized"
    assert app.desktop.rem.calls == []
    snapshot = app.handle(
        peer,
        event(
            "sync.request",
            1,
            thread_id=None,
            payload={"after_sequence": None, "limit": 10, "include_connection": True},
        ),
    )[-1]["payload"]["connection"]
    assert snapshot["desktop_control"] is False and snapshot["remote_control"] is False

    app.desktop.enable(peer, True)
    connection = app.handle(
        peer,
        event(
            "sync.request",
            2,
            thread_id=None,
            payload={"after_sequence": None, "limit": 10, "include_connection": True},
        ),
    )[-1]["payload"]["connection"]
    # Full desktop control includes approvals and swarm controls.
    assert connection["desktop_control"] and connection["remote_control"]
    assert send("desktop.read", "rem.home")["ok"]
    # Turning thread sync off also turns desktop control off.
    app.workspace.enable(peer, False)
    assert send("desktop.read", "rem.home")["code"] == "unauthorized"


def test_rem_results_carry_no_paths_and_actions_use_desktop_services(phone):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)
    overview = send("desktop.read", "overview")["value"]
    assert {p["name"] for p in overview["projects"]} == {"Notes", "Code"}
    home = send("desktop.read", "rem.home")["value"]
    text = json.dumps(home)
    assert context["projects"][0]["root"] not in text and "grant" not in text.lower()
    assert home["tasks"][0]["project_name"] == "Notes"
    assert home["tasks"][0]["active_run"]["state"] == "executing"
    assert home["messages"][1]["work"]["task_id"] == "task1"
    thread = send("desktop.read", "rem.thread", {"thread_id": "thread1"})["value"]
    assert [m["id"] for m in thread["messages"]] == ["t1"] and thread["running"]
    assert send("desktop.read", "rem.thread", {"thread_id": "gone"})["code"] == "not_found"

    rem = app.desktop.rem
    send("desktop.action", "rem.message", {"body": "Any failures?"})
    assert rem.calls[-1][0] == "message" and rem.calls[-1][1]["body"] == "Any failures?"
    project = next(p["id"] for p in context["projects"] if p["name"] == "Code")
    send(
        "desktop.action",
        "rem.task.create",
        {
            "title": "Nightly",
            "type": "maintain",
            "prompt": "Check",
            "project_id": project,
            "project_dir": "/etc",
        },
    )
    created = rem.calls[-1][1]
    # The phone names a catalog project; the desktop supplies its own folder.
    assert created["project_dir"] == next(
        p["root"] for p in context["projects"] if p["name"] == "Code"
    )
    unknown = send(
        "desktop.action",
        "rem.task.create",
        {
            "title": "x",
            "type": "errand",
            "prompt": "x",
            "project_id": "00000000-0000-4000-8000-000000000999",
        },
    )
    assert unknown["code"] == "invalid"
    send("desktop.action", "rem.task.pause", {"task_id": "task1"})
    assert rem.calls[-1] == (
        "pause",
        ("task1", {"request_id": rem.calls[-1][1][1]["request_id"], "cancel_current": False}),
    )
    send("desktop.action", "rem.task.resume", {"task_id": "task1", "expected_revision": 3})
    assert rem.calls[-1][1][1]["enabled"] is True
    send("desktop.action", "rem.run.cancel", {"run_id": "run1"})
    assert rem.store.mutations == [("cancel", "run1")]
    send("desktop.action", "rem.instruct", {"thread_id": "thread1", "body": "Also lint"})
    assert rem.calls[-1][1][0] == "thread1"


def test_dismiss_all_dismisses_the_updates_the_phone_showed_in_one_action(phone):
    app, peer, _context, send = phone
    app.desktop.enable(peer, True)
    # Phones offer Dismiss all only when the desktop says it takes the action.
    assert send("desktop.read", "rem.home")["value"]["dismiss_all"] is True
    store = app.desktop.rem.store
    shown = send("desktop.action", "rem.run.acknowledge_all", {"run_ids": ["run1", "run2", 7, ""]})
    assert shown["ok"] and "tasks" in shown["value"]
    action, payload = store.mutations[-1]
    assert action == "dismiss_updates"
    # Only text IDs are passed on; the request ID makes a repeated event run once.
    assert payload["run_ids"] == ["run1", "run2", "7"] and payload["request_id"]
    send("desktop.action", "rem.run.acknowledge_all", {})
    assert "run_ids" not in store.mutations[-1][1]


def test_large_results_are_split_into_bounded_parts(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem.store.messages = [
        {"id": f"m{i}", "role": "user", "body": 'é"' * 2500, "created_at": "t"} for i in range(80)
    ]
    result = send("desktop.read", "rem.home")["value"]
    # The newest messages that fit are shown; the rest are counted as earlier.
    assert 0 < len(result["messages"]) < 80 and result["messages"][-1]["id"] == "m79"
    assert result["earlier"] == 80 - len(result["messages"])
    rows = app.registry.db.execute("SELECT COUNT(*) FROM device_outbox WHERE peer=?", (peer,))
    assert rows.fetchone()[0] > 3


def test_the_phone_counts_messages_the_desktop_didnt_read_as_earlier(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem.store.messages = [
        {"id": f"m{i}", "role": "user", "body": "Hi", "created_at": "t"} for i in range(1000)
    ]
    result = send("desktop.read", "rem.home")["value"]
    # The desktop reads its newest window; the phone shows the newest 80 of it, and the
    # count of earlier messages it can page back through still covers the whole chat.
    assert [m["id"] for m in result["messages"]][-1] == "m999" and len(result["messages"]) == 80
    assert result["earlier"] == 920


def test_work_threads_reach_the_phone_with_thoughts_grouped_like_the_desktop(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)

    def thought(i: int, **trace: Any) -> dict[str, Any]:
        return {"id": f"w{i}", "role": "assistant", "kind": "thought", "body": "x", "trace": trace}

    report = "## Done\n" + "word " * 1200
    messages = [
        {"id": "ask", "role": "user", "sender": "rem", "body": "Fix the build"},
        thought(1, id="think", kind="summary", phase="start", status="running", title="Thinking"),
        thought(
            2,
            id="think",
            kind="summary",
            phase="result",
            status="complete",
            title="Thought",
            detail="for 1s",
        ),
        thought(3, id="sh", kind="tool", phase="start", status="running", title="Bash", input="{}"),
        thought(
            4,
            id="sh",
            kind="tool",
            phase="update",
            title="Bash",
            detail="Run the tests",
            command="pytest -q",
        ),
        thought(
            5,
            id="sh",
            kind="tool",
            phase="result",
            status="complete",
            title="Tool result",
            output="SECRET",
        ),
        thought(6, id="ls", kind="tool", status="complete", title="Bash", command="ls -la"),
        {"id": "mem", "role": "assistant", "kind": "memory", "body": "hidden"},
        thought(7, kind="summary", title="Summary", body="Tests pass now."),
        thought(8, kind="summary", body="Tokens used: 1,234"),
        {"id": "done", "role": "assistant", "kind": "final", "body": report},
        {"id": "mine", "role": "user", "sender": "user", "body": "Also check the loading state"},
        thought(9, id="late", kind="tool", phase="start", status="running", title="Read"),
    ]
    app.desktop.rem.store.thread = lambda identifier: {
        "metadata": {"title": "Fix the build"},
        "running": True,
        "messages": messages,
    }
    thread = send("desktop.read", "rem.thread", {"thread_id": "thread1"})["value"]
    assert [(m["id"], m["kind"]) for m in thread["messages"]] == [
        ("ask", ""),
        ("w1", "thoughts"),
        ("done", "final"),
        ("mine", ""),
        ("w9", "thoughts"),
    ]
    # Instructions say who wrote them; replies and thought groups don't.
    assert [m.get("sender") for m in thread["messages"]] == ["rem", None, None, "user", None]
    group = thread["messages"][1]
    # Start, update and result records of one step merge; the memory note doesn't split the run.
    assert group["count"] == 4 and group["body"] == "Worked through 4 steps."
    assert [(s["title"], s["detail"], s["command"], s["status"]) for s in group["steps"]] == [
        ("Thought", "for 1s", "", "complete"),
        ("Bash", "Run the tests", "", "complete"),
        ("Bash", "", "ls -la", "complete"),
        ("Summary", "", "", ""),
    ]
    assert group["steps"][3]["body"] == "Tests pass now."
    assert "SECRET" not in json.dumps(thread) and "hidden" not in json.dumps(thread)
    # The final report arrives whole instead of cut at 4,000 characters.
    assert thread["messages"][2]["body"] == report and thread["earlier"] == 0
    assert thread["messages"][4]["steps"][0]["status"] == "running"


def test_unexpected_failures_still_answer_the_request(phone, monkeypatch):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)

    def broken() -> list[dict[str, Any]]:
        raise RuntimeError("internal detail")

    monkeypatch.setattr(app.desktop.rem.store, "tasks", broken)
    failed = send("desktop.read", "rem.home")
    assert failed["code"] == "invalid" and "internal" not in failed["detail"]


def test_a_repeated_action_event_runs_once(phone):
    app, peer, _, _ = phone
    app.desktop.enable(peer, True)
    action = event(
        "desktop.action",
        500,
        thread_id=None,
        payload={"op": "rem.message", "params": {"body": "once"}},
    )
    app.handle(peer, action)
    app.handle(peer, action)
    assert [c for c in app.desktop.rem.calls if c[0] == "message"] == [
        ("message", {"request_id": action["request_id"], "body": "once"})
    ]


def test_organizations_use_the_desktop_manager(phone, tmp_path):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)

    async def stream(**_: Any) -> Any:
        if False:
            yield {}

    manager = OrganizationManager(tmp_path / "orgs", stream=stream, start_runtime=False)
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
                        "name": "Eng",
                        "projectRoots": [root],
                        "employees": [{"id": "lead", "name": "Lead", "title": "CTO"}],
                    }
                },
            },
        )
        listed = send("desktop.read", "organizations.list")["value"]["organizations"]
        assert listed[0]["projects"] == ["Notes"] and root not in json.dumps(listed)
        assert (listed[0]["needs_you"], listed[0]["archived"]) == (0, False)
        oid = org["id"]
        created = send(
            "desktop.action",
            "organizations.task",
            {"organization_id": oid, "action": "create", "title": "Ship", "assignee": "lead"},
        )["value"]
        task = created["tasks"][0]
        assert task["assignee"] == "Lead" and task["status"] == "todo"
        commented = send(
            "desktop.action",
            "organizations.task",
            {
                "organization_id": oid,
                "action": "comment",
                "task_id": task["task_id"],
                "body": "Looks good",
            },
        )["value"]
        assert commented["tasks"][0]["comments"][-1]["body"] == "Looks good"
        stale = send(
            "desktop.action",
            "organizations.task",
            {
                "organization_id": oid,
                "action": "cancel",
                "task_id": task["task_id"],
                "expected_revision": 1,
            },
        )
        assert stale["code"] == "conflict"
        cancelled = send(
            "desktop.action",
            "organizations.task",
            {
                "organization_id": oid,
                "action": "cancel",
                "task_id": task["task_id"],
                "expected_revision": 2,
            },
        )["value"]
        assert cancelled["tasks"][0]["status"] == "cancelled"
        paused = send(
            "desktop.action", "organizations.control", {"organization_id": oid, "state": "paused"}
        )["value"]
        assert paused["state"] == "paused"
        assert paused["pause_reason"] == "Paused by you"
        assert paused["usage"]["runs"] == 0 and paused["pending_requests"] == 0
        # A phone with Use Raticode from this phone on is the user (phone_guard.py).
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
                            {
                                **current["config"]["employees"][0],
                                "permissionMode": "danger-full-access",
                            }
                        ],
                    },
                    "expectedRevision": current["revision"],
                    "reason": "Full access",
                },
            },
        )
        # The phone is the user: it starts an organization with full-access workers too.
        started = send(
            "desktop.action", "organizations.control", {"organization_id": oid, "state": "running"}
        )
        assert started["ok"], started
        assert manager.store.get("", oid)["runtime"]["state"] == "running"
        send("desktop.action", "organizations.control", {"organization_id": oid, "state": "paused"})
        # Configuration, imports and secrets are not phone operations.
        assert (
            send(
                "desktop.action",
                "organizations.task",
                {"organization_id": oid, "action": "configure"},
            )["code"]
            == "invalid"
        )
        manager.enabled = False
        assert send("desktop.read", "organizations.list")["code"] == "unavailable"
    finally:
        manager.close()


def test_organization_read_says_why_the_next_worker_waits_for_a_computer(phone, tmp_path):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)

    async def stream(**_: Any) -> Any:
        if False:
            yield {}

    manager = OrganizationManager(tmp_path / "orgs", stream=stream, start_runtime=False)
    root = context["projects"][0]["root"]
    try:
        app.desktop.organizations = manager
        org = manager.call(
            root,
            "user",
            {"action": "create", "params": {"config": {"name": "Eng", "projectRoots": [root]}}},
        )
        reason = "Waiting for a free worker: Workstation is full (2 of 2 running)"

        def waiting(state: str) -> Any:
            def change(current: dict[str, Any]) -> dict[str, Any]:
                current["runtime"].update(state=state, placementWait=reason)
                return {"probe": True}

            return change

        manager.store.mutate("", org["id"], "test", "probe", waiting("running"))
        read = send("desktop.read", "organizations.read", {"organization_id": org["id"]})
        assert read["value"]["placement_wait"] == reason
        # A paused organization starts nothing, so it isn't waiting for a computer.
        manager.store.mutate("", org["id"], "test", "probe", waiting("paused"))
        read = send("desktop.read", "organizations.read", {"organization_id": org["id"]})
        assert read["value"]["placement_wait"] == ""
    finally:
        manager.close()


def test_swarms_start_in_shared_projects_only(phone):
    app, peer, context, send = phone
    root = context["projects"][0]["root"]
    swarms = Swarms(root)
    swarms.start = lambda r, i, task, **kw: swarms.calls.append(("start", (r, i, task)))
    app.remote.swarms = swarms
    app.desktop.enable(peer, True)
    wire = swarm_wire_id(root, "swarm1")
    assert send("desktop.action", "swarms.start", {"swarm_id": wire, "task": "Add previews"})["ok"]
    assert swarms.calls[-1] == ("start", (root, "swarm1", "Add previews"))
    other = swarm_wire_id("/elsewhere", "swarm1")
    assert send("desktop.action", "swarms.start", {"swarm_id": other, "task": "x"})["code"] == (
        "invalid"
    )


def test_settings_round_trip_through_the_desktop_window(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    unavailable = send(
        "desktop.action", "settings.update", {"changes": {"appearance.theme": "dark"}}
    )
    assert unavailable["code"] == "unavailable"
    window: dict[str, dict[str, Any]] = {
        "appearance": {"theme": "light", "reducedMotion": "system"},
        "assistant": {"provider": "codex", "model": "model-a", "swarmAccessEnabled": True},
        "browser": {"remConfirm": "signed-in"},
        "editor": {"fontSize": 13},
    }
    assert app.desktop.exchange_settings(window, []) == {"changes": []}
    view = send("desktop.read", "settings.get")["value"]
    assert view["available"] and view["values"]["appearance.theme"] == "light"
    # Settings that widen agent permissions are never offered or accepted.
    assert "browser.remConfirm" not in view["values"]
    assert "assistant.swarmAccessEnabled" not in view["values"]
    models = next(s for s in view["settings"] if s["key"] == "assistant.model")["options"]
    assert ["model-a", "model-a"] in models
    for changes in (
        {"browser.remConfirm": "never"},
        {"editor.fontSize": 99},
        {"appearance.theme": "neon"},
        {"assistant.provider": "unknown"},
    ):
        assert send("desktop.action", "settings.update", {"changes": changes})["code"] == "invalid"
    updated = send(
        "desktop.action",
        "settings.update",
        {"changes": {"appearance.theme": "dark", "editor.fontSize": 15}},
    )["value"]
    assert updated["values"]["appearance.theme"] == "dark" and updated["pending"] == 1
    queued = app.desktop.exchange_settings(window, [])["changes"]
    assert queued[0]["values"] == {"appearance.theme": "dark", "editor.fontSize": 15}
    window["appearance"]["theme"] = "dark"
    window["editor"]["fontSize"] = 15
    assert app.desktop.exchange_settings(window, [queued[0]["id"]]) == {"changes": []}
    assert send("desktop.read", "settings.get")["value"]["pending"] == 0


def test_phone_picks_rems_account_per_provider(phone, tmp_path):
    """Desktop settings > Rem > <provider> account: default, choose by usage or one account."""
    from gofer.core.provider_accounts import create_account

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    data_dir = tmp_path / "accounts-data"
    app.desktop.rem.data_dir = data_dir
    create_account("work", provider="claude_code", data_dir=data_dir)
    window: dict[str, Any] = {
        "assistant": {"provider": "claude_code", "providerAccounts": {"claude_code": "auto"}}
    }
    app.desktop.exchange_settings(window, [])
    key = "assistant.providerAccounts.claude_code"
    view = send("desktop.read", "settings.get")["value"]
    item = next(s for s in view["settings"] if s["key"] == key)
    assert item["kind"] == "enum" and item["label"] == "Claude Code account"
    assert "provider" not in item
    assert item["options"] == [
        ["", "Default sign-in"],
        ["auto", "Choose by usage"],
        ["work", "Claude Code · work"],
    ]
    assert view["values"][key] == "auto"
    assert "assistant.claudeAccount" not in view["values"]
    refused = send("desktop.action", "settings.update", {"changes": {key: "missing"}})
    assert refused["code"] == "invalid"
    send("desktop.action", "settings.update", {"changes": {key: "work"}})
    queued = app.desktop.exchange_settings(window, [])["changes"]
    assert queued[-1]["values"] == {key: "work"}


def test_phone_turns_co_author_removal_on_without_the_window(phone):
    """Settings > Rem > Remove Co-authored-by lines lives in the backend, so it applies now."""
    from gofer.core.provider_preferences import strip_co_authors_enabled

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    key = "commitMessage.stripCoAuthors"
    view = send("desktop.read", "settings.get")["value"]
    item = next(s for s in view["settings"] if s["key"] == key)
    assert item == {
        "key": key,
        "group": "Rem",
        "label": "Remove Co-authored-by lines from commits",
        "kind": "bool",
    }
    assert view["values"][key] is False
    # No window snapshot yet: a window setting waits, this one doesn't.
    changed = send("desktop.action", "settings.update", {"changes": {key: True}})["value"]
    assert changed["values"][key] is True and changed["pending"] == 0
    assert strip_co_authors_enabled() is True
    assert send("desktop.action", "settings.update", {"changes": {key: "yes"}})["code"] == (
        "invalid"
    )
    window = {"appearance": {"theme": "light"}, "commitMessage": {"stripCoAuthors": False}}
    assert app.desktop.exchange_settings(window, []) == {"changes": []}
    # The window never overrides or receives it.
    assert send("desktop.read", "settings.get")["value"]["values"][key] is True
    mixed = send(
        "desktop.action",
        "settings.update",
        {"changes": {key: False, "appearance.theme": "dark"}},
    )["value"]
    assert mixed["values"][key] is False and mixed["pending"] == 1
    assert strip_co_authors_enabled() is False
    queued = app.desktop.exchange_settings(window, [])["changes"]
    assert queued[0]["values"] == {"appearance.theme": "dark"}


def test_phone_usage_and_self_work_report_carry_verified_work(phone, tmp_path):
    """The phone's Usage page and report card get the numbers the CLI and desktop show."""
    from datetime import UTC, datetime, timedelta

    from gofer.core.usage_history import resolve_range
    from gofer.core.usage_service import usage_overview
    from gofer.devices.desktop_control import DesktopControl
    from gofer.ui.rem_verified_work import verified_work
    from tests.unit.test_rem_verified_work import LINE, seed_history

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    data = tmp_path / "verified-data"
    data.mkdir()
    seed_history(data, datetime.now(UTC) - timedelta(days=1))
    app.desktop.usage = lambda **period: usage_overview(data, **period)
    view = send("desktop.read", "usage.overview", {"range": "30d", "tz": "UTC"})["value"]
    expected = verified_work(data, resolve_range(period="30d", tz="UTC"))
    assert view["verified_work"]["line"] == LINE
    for key in ("accepted", "first_pass", "repairs_per_ticket", "blocked_causes", "step_ins"):
        assert view["verified_work"][key] == expected[key]
    assert view["verified_work"]["project_check"] == expected["project_check"]
    assert view["verified_work"]["project_check"]["flaky"] == 1
    report = {
        "items": [],
        "counts": {"finished": 3},
        "verified_work": {"line": LINE, "accepted": 3, "first_pass": 1, "blocked": True},
    }
    item = DesktopControl._rem_message(
        {
            "id": "r",
            "role": "assistant",
            "body": "Report",
            "kind": "self_work_report",
            "self_work_report": report,
        }
    )
    assert item["self_work_report"]["verified_work"] == {
        "line": LINE,
        "accepted": 3,
        "first_pass": 1,
    }


def test_phone_usage_asks_for_its_period_and_gets_the_token_chart(phone, tmp_path):
    from gofer.core.usage_ledger import record_invocation
    from gofer.core.usage_service import usage_overview

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    data = tmp_path / "usage-data"
    record_invocation(
        invocation_id="phone-usage",
        provider="codex",
        data_dir=data,
        metadata={"input_tokens": 90, "output_tokens": 10},
    )
    record_invocation(
        invocation_id="phone-usage-work",
        provider="codex",
        profile="work",
        data_dir=data,
        metadata={"input_tokens": 20, "output_tokens": 0},
    )
    asked: list[dict[str, Any]] = []

    def usage(**period: Any) -> dict[str, Any]:
        asked.append(period)
        return usage_overview(data, **period)

    app.desktop.usage = usage
    view = send("desktop.read", "usage.overview", {"range": "6mo", "tz": "UTC"})["value"]
    assert asked[-1] == {"period": "6mo", "start": None, "end": None, "tz": "UTC"}
    assert view["range"]["key"] == "6mo" and view["days"] == 180
    assert view["activity"]["total_tokens"] == 120
    assert view["series"]["bucket"] == "week"
    assert view["series"]["providers"] == [
        {
            "provider": "codex",
            "total_tokens": 120,
            "calls": 2,
            # The phone draws each provider in the desktop's brand palette.
            "style": {"dark": "#10A37F", "light": "#0D8A6B", "pattern": "stripes"},
        }
    ]
    assert sum(b["providers"].get("codex", 0) for b in view["series"]["buckets"]) == 120
    # And each account, for the chart's By account switch, in a shade of that palette.
    from gofer.core.provider_colors import account_style

    assert view["series"]["accounts"] == [
        {
            "key": "codex:",
            "provider": "codex",
            "profile": None,
            "name": "Default account",
            "total_tokens": 100,
            "calls": 1,
            "style": {"dark": "#10A37F", "light": "#0D8A6B", "pattern": "stripes"},
        },
        {
            "key": "codex:work",
            "provider": "codex",
            "profile": "work",
            "name": "work",
            "total_tokens": 20,
            "calls": 1,
            "style": account_style("codex", 1),
        },
    ]
    assert sum(b["accounts"].get("codex:work", 0) for b in view["series"]["buckets"]) == 20
    custom = send(
        "desktop.read",
        "usage.overview",
        {"range": "custom", "start": "2026-01-01", "end": "2026-01-31", "tz": "UTC"},
    )["value"]
    assert custom["range"]["start_date"] == "2026-01-01" and custom["range"]["days"] is None
    # A phone with no period gets the desktop's default, as older phones always did.
    assert send("desktop.read", "usage.overview")["value"]["range"]["key"] == "30d"
    refused = send(
        "desktop.read", "usage.overview", {"range": "custom", "start": "2026-02-01", "end": "x"}
    )
    assert refused["ok"] is False and refused["code"] == "invalid"


def test_phone_usage_shows_api_cost_without_plan_prices(phone, tmp_path):
    from gofer.core.usage_ledger import record_invocation
    from gofer.core.usage_service import usage_overview
    from tests.helpers.cli_parity import phone_operations

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    data = tmp_path / "usage-data"
    record_invocation(
        invocation_id="priced",
        provider="claude_code",
        model="claude-opus-5-5",
        data_dir=data,
        metadata={"input_tokens": 2_000_000, "cache_read_tokens": 1_000_000, "output_tokens": 0},
    )
    record_invocation(
        invocation_id="unpriced",
        provider="codex",
        model="cli-default",
        data_dir=data,
        metadata={"input_tokens": 10, "output_tokens": 1},
    )
    app.desktop.usage = lambda **period: usage_overview(data, **period)
    cost = send("desktop.read", "usage.overview", {"range": "7d"})["value"]["api_cost"]
    assert cost["total"]["cost_usd"] > 0 and cost["total"]["lower_bound"] is True
    codex = next(p for p in cost["providers"] if p["provider"] == "codex")
    assert codex["cost_usd"] is None and codex["unpriced_models"] == ["cli-default"]
    # Saved vs API and the phone's plan-price action were removed.
    assert not {"savings", "plans", "plan_providers"} & cost.keys()
    assert "usage.plans.save" not in phone_operations()


def test_phone_usage_cards_carry_sign_in_state_and_rems_next_account(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    windows = [
        {
            "id": "five_hour",
            "label": "Five-hour allowance",
            "used_percent": 20,
            "resets_at": "2026-11-17T14:14:00+00:00",
        },
        {"id": "seven_day", "label": "Weekly allowance", "remaining_percent": 70},
    ]
    overview = {
        "days": 30,
        "accounts": [
            {
                "provider": "claude_code",
                "profile": "work",
                "label": "Claude Code",
                "status": "available",
                "sign_in_state": "signed_in",
                "windows": windows,
                "rem_next": {"why": "Most room left: 30% of its weekly allowance used."},
            },
            {
                "provider": "claude_code",
                "profile": "home",
                "label": "Claude Code",
                "status": "auth_required",
                "needs_sign_in": True,
                "sign_in_state": "needs_sign_in",
                "windows": [],
            },
        ],
    }
    app.desktop.usage = lambda **_period: overview
    work, home = send("desktop.read", "usage.overview")["value"]["accounts"]
    assert work["profile"] == "work" and work["sign_in_state"] == "signed_in"
    assert work["rem_next"] == {"why": "Most room left: 30% of its weekly allowance used."}
    assert [w["id"] for w in work["windows"]] == ["five_hour", "seven_day"]
    assert [w["used_percent"] for w in work["windows"]] == [20, 30]
    assert home["status"] == "needs sign-in" and home["sign_in_state"] == "needs_sign_in"
    assert "rem_next" not in home


def test_unpairing_forgets_desktop_control(phone):
    app, peer, _, _ = phone
    app.desktop.enable(peer, True)
    app.remove_peer(peer)
    assert app.desktop.enabled() == []


def test_unchanged_screens_are_answered_with_a_digest(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    first = send("desktop.read", "rem.home")["value"]
    again = send("desktop.read", "rem.home", {"known": first["digest"]})["value"]
    assert again == {"unchanged": True, "digest": first["digest"]}
    app.desktop.rem.store.messages.append(
        {"id": "m3", "role": "rem", "body": "New", "created_at": "t3"}
    )
    changed = send("desktop.read", "rem.home", {"known": first["digest"]})["value"]
    assert changed["messages"][-1]["id"] == "m3" and changed["digest"] != first["digest"]


def _block_rem_tasks(app: Any) -> tuple[threading.Event, threading.Event]:
    """Make the Rem store's task read wait until released, like a slow desktop service."""
    started, release = threading.Event(), threading.Event()
    store = app.desktop.rem.store
    tasks = store.tasks

    def slow_tasks(*, summary: bool = False) -> list[dict[str, Any]]:
        started.set()
        assert release.wait(5)
        result: list[dict[str, Any]] = tasks(summary=summary)
        return result

    store.tasks = slow_tasks
    return started, release


def test_slow_desktop_services_do_not_hold_the_device_lock(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    started, release = _block_rem_tasks(app)
    answers: list[dict[str, Any]] = []
    reader = threading.Thread(target=lambda: answers.append(send("desktop.read", "rem.home")))
    reader.start()
    try:
        assert started.wait(5)
        # The desktop window's device calls take this lock; a phone read must not stall them.
        assert app.registry.lock.acquire(timeout=1)
        app.registry.lock.release()
    finally:
        release.set()
        reader.join(5)
    assert answers[0]["ok"] and answers[0]["value"]["tasks"][0]["task_id"] == "task1"


def test_turning_off_phone_access_during_a_slow_read_refuses_it(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    started, release = _block_rem_tasks(app)
    answers: list[dict[str, Any]] = []
    reader = threading.Thread(target=lambda: answers.append(send("desktop.read", "rem.home")))
    reader.start()
    try:
        assert started.wait(5)
        app.desktop.enable(peer, False)
    finally:
        release.set()
        reader.join(5)
    assert answers == [{"ok": False, "code": "unauthorized", "detail": module.OFF}]


def _upload(app: Any, peer: str, thread: str, data: bytes, sequence: int) -> str:
    """Upload a file from the phone on a thread, the way the phone app does."""
    identifier = str(uuid4())
    offer = {
        "file_id": identifier,
        "name": "photo.png",
        "mime": "application/octet-stream",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "expires_at": int(app.registry.clock()) + 600,
    }
    answer = app.handle(peer, event("file.offer", sequence, thread_id=thread, payload=offer))
    if answer[-1]["type"] == "error":
        raise PermissionError(answer[-1]["payload"]["code"])
    chunk = {
        "file_id": identifier,
        "offset": 0,
        "data": base64.urlsafe_b64encode(data).rstrip(b"=").decode(),
        "eof": True,
    }
    status = app.handle(peer, event("file.chunk", sequence + 1, thread_id=thread, payload=chunk))
    assert status[-1]["payload"]["state"] == "available"
    return identifier


def test_rem_chat_attachments_use_the_phone_file_channel(phone, tmp_path):
    app, peer, _, send = phone
    rem = app.desktop.rem
    rem.data_dir = tmp_path / "rem-data"
    channel = app.desktop.files_thread(peer)
    # The channel is closed until the user lets this phone use Raticode. The refusal
    # is answered, so the phone drops the upload instead of resending it forever.
    with pytest.raises(PermissionError, match="unauthorized"):
        _upload(app, peer, channel, b"x", 600)
    app.desktop.enable(peer, True)
    home = send("desktop.read", "rem.home")["value"]
    assert home["files_thread"] == channel and home["media"] is True
    assert send("desktop.read", "overview")["value"]["media"] is True
    # Another phone's channel, or any other unshared thread, stays closed.
    with pytest.raises(PermissionError, match="unauthorized"):
        _upload(app, peer, str(uuid4()), b"x", 610)

    first = _upload(app, peer, channel, b"\x89PNG first", 620)
    second = _upload(app, peer, channel, b"second", 630)
    sent = send(
        "desktop.action",
        "rem.message",
        {"body": "What's in these?", "attachment_ids": [first, second]},
    )
    assert sent["ok"]
    payload = rem.calls[-1][1]
    attachments = payload["attachments"]
    assert [a["id"] for a in attachments] == [first, second]
    assert attachments[0]["type"] == "image/png"
    folder = rem.data_dir / "chat-attachments" / "rem-home"
    assert (folder / attachments[0]["storageName"]).read_bytes() == b"\x89PNG first"
    # The desktop keeps its copy, so the phone channel's quota is released.
    assert (
        app.registry.db.execute(
            "SELECT COUNT(*) FROM device_files WHERE peer=?", (peer,)
        ).fetchone()[0]
        == 0
    )

    missing = send("desktop.action", "rem.message", {"body": "x", "attachment_ids": [first]})
    assert missing["code"] == "invalid" and "Attach it again" in missing["detail"]
    too_many = send(
        "desktop.action", "rem.message", {"body": "x", "attachment_ids": [str(uuid4())] * 2}
    )
    assert too_many["code"] == "invalid"

    rem.store.messages.append(
        {
            "id": payload["request_id"],
            "role": "user",
            "body": "What's in these?",
            "created_at": "t3",
            "attachments": attachments,
        }
    )
    shown = send("desktop.read", "rem.home")["value"]["messages"][-1]
    assert shown["attachments"] == [
        {"file_id": first, "name": "photo.png", "size": 10},
        {"file_id": second, "name": "photo.png", "size": 6},
    ]
    assert "storageName" not in json.dumps(shown) and str(rem.data_dir) not in json.dumps(shown)


def test_every_rem_chat_attachment_reaches_the_phone_whichever_device_sent_it(phone, tmp_path):
    """The phone asks for an attachment by message and attachment ID; the desktop offers
    its stored copy on the phone's Rem chat channel, whether the desktop window or a
    phone attached it, and never takes a path from the phone."""
    from gofer.ui.chat_media import attachment_thread_dir
    from gofer.ui.rem_home import REM_ATTACHMENT_THREAD

    app, peer, _, send = phone
    rem = app.desktop.rem
    rem.data_dir = tmp_path / "rem-data"
    folder = attachment_thread_dir(rem.data_dir.resolve(), REM_ATTACHMENT_THREAD)
    folder.mkdir(parents=True)
    channel = app.desktop.files_thread(peer)
    picture = b"\x89PNG desktop picture" * 2000
    (folder / "771f264b-image.png").write_bytes(picture)
    attached = {
        "id": "771f264b",
        "name": "image.png",
        "size": len(picture),
        "type": "image/png",
        "storageName": "771f264b-image.png",
        "path": str(folder / "771f264b-image.png"),
    }
    rem.store.messages.append(
        {
            "id": "desk",
            "role": "user",
            "body": "Look",
            "created_at": "t3",
            "attachments": [attached],
        }
    )
    params = {"message_id": "desk", "file_id": "771f264b"}
    assert send("desktop.action", "rem.attachment", params)["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["attachment_files"] is True
    assert send("desktop.read", "rem.home")["value"]["attachment_files"] is True

    # Sent from the desktop window: offered under the name it was attached with.
    before = app.registry.db.execute("SELECT MAX(sequence) FROM device_outbox").fetchone()[0]
    fetched = send("desktop.action", "rem.attachment", params)
    assert fetched["ok"], fetched
    assert fetched["value"]["name"] == "image.png" and fetched["value"]["size"] == len(picture)
    assert str(tmp_path) not in json.dumps(fetched["value"])
    offers = [e for e in app.pending(peer, before) if e["type"] == "file.offer"]
    assert [(o["thread_id"], o["payload"]["file_id"], o["payload"]["name"]) for o in offers] == [
        (channel, fetched["value"]["file_id"], "image.png")
    ]
    received, offset, sequence = b"", 0, 700
    while True:
        chunk = app.handle(
            peer,
            event(
                "file.request" if not offset else "file.status",
                sequence,
                thread_id=channel,
                payload={"file_id": fetched["value"]["file_id"]}
                if not offset
                else {
                    "file_id": fetched["value"]["file_id"],
                    "state": "receiving",
                    "received_size": offset,
                },
            ),
        )[-1]["payload"]
        received += base64.urlsafe_b64decode(chunk["data"] + "==")
        offset, sequence = len(received), sequence + 1
        if chunk["eof"]:
            break
    assert received == picture
    assert hashlib.sha256(received).hexdigest() == offers[0]["payload"]["sha256"]

    # Sent from a phone: once the channel copy is released, the stored copy still reaches it.
    uploaded = _upload(app, peer, channel, b"phone photo", 800)
    assert send("desktop.action", "rem.message", {"body": "Mine", "attachment_ids": [uploaded]})[
        "ok"
    ]
    payload = rem.calls[-1][1]
    rem.store.messages.append(
        {
            "id": payload["request_id"],
            "role": "user",
            "body": "Mine",
            "created_at": "t4",
            "attachments": payload["attachments"],
        }
    )
    again = send(
        "desktop.action",
        "rem.attachment",
        {"message_id": payload["request_id"], "file_id": uploaded},
    )
    assert (
        again["ok"]
        and again["value"]["name"] == "photo.png"
        and again["value"]["file_id"] != uploaded
    )
    chunk = app.handle(
        peer,
        event(
            "file.request", 900, thread_id=channel, payload={"file_id": again["value"]["file_id"]}
        ),
    )[-1]["payload"]
    assert base64.urlsafe_b64decode(chunk["data"] + "==") == b"phone photo"

    # A name the phone wouldn't accept falls back to the stored name.
    (folder / "aa-bad.txt").write_bytes(b"x")
    rem.store.messages.append(
        {
            "id": "odd",
            "role": "user",
            "body": "",
            "created_at": "t5",
            "attachments": [
                {"id": "aa", "name": " bad\n.txt", "size": 1, "storageName": "aa-bad.txt"}
            ],
        }
    )
    named = send("desktop.action", "rem.attachment", {"message_id": "odd", "file_id": "aa"})
    assert named["ok"] and named["value"]["name"] == "aa-bad.txt"

    # Only stored attachments of the user's own messages; nothing the phone names by path.
    (tmp_path / "secret.txt").write_text("private")
    (folder / "ln-link.txt").symlink_to(tmp_path / "secret.txt")
    (folder / "big-big.bin").write_bytes(b"")
    with open(folder / "big-big.bin", "r+b") as handle:
        handle.truncate(module.MAX_FILE + 1)
    rem.store.messages += [
        {
            "id": "tricks",
            "role": "user",
            "body": "",
            "created_at": "t6",
            "attachments": [
                {"id": "up", "name": "x", "size": 1, "storageName": "../secret.txt"},
                {"id": "ln", "name": "link.txt", "size": 7, "storageName": "ln-link.txt"},
                {"id": "gone", "name": "gone.png", "size": 1, "storageName": "gone-gone.png"},
                {
                    "id": "big",
                    "name": "big.bin",
                    "size": module.MAX_FILE + 1,
                    "storageName": "big-big.bin",
                },
                {"id": "none", "name": "none.png", "size": 1},
            ],
        },
        {
            "id": "rem-said",
            "role": "rem",
            "body": "",
            "created_at": "t7",
            "attachments": [attached],
        },
    ]
    for message_id, identifier, code in [
        ("tricks", "up", "not_found"),
        ("tricks", "ln", "not_found"),
        ("tricks", "gone", "not_found"),
        ("tricks", "none", "not_found"),
        ("tricks", "missing", "not_found"),
        ("tricks", "big", "unavailable"),
        ("rem-said", "771f264b", "not_found"),
        ("nowhere", "771f264b", "not_found"),
    ]:
        refused = send(
            "desktop.action", "rem.attachment", {"message_id": message_id, "file_id": identifier}
        )
        assert refused["ok"] is False and refused["code"] == code, (message_id, identifier, refused)
        assert str(tmp_path) not in refused["detail"]
    for bad in [
        {},
        {"message_id": "desk"},
        {"message_id": "desk", "file_id": 5},
        {"message_id": "", "file_id": "x"},
    ]:
        assert send("desktop.action", "rem.attachment", bad)["code"] == "invalid"
    app.desktop.enable(peer, False)
    assert send("desktop.action", "rem.attachment", params)["code"] == "unauthorized"


def test_files_on_rem_replies_go_only_to_the_phone_that_asked(phone, tmp_path):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    brain = tmp_path / "brain"
    (brain / "reports").mkdir(parents=True)
    report = brain / "reports" / "audit.html"
    report.write_bytes(b"<p>Audit</p>" * 2000)
    rem.store.config["brain"] = {"root": str(brain)}
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("private")

    asked = send("desktop.action", "rem.message", {"body": "Send me the audit"})
    assert asked["ok"]
    request = rem.calls[-1][1]["request_id"]
    files = [
        {"name": "audit.html", "size": 24000, "path": str(report)},
        {"name": "elsewhere.txt", "size": 7, "path": str(outside)},
    ]
    rem.store.messages += [
        {
            "id": f"reply-{request}",
            "role": "rem",
            "body": "Here.",
            "created_at": "t4",
            "files": files,
            "reply_to": request,
        },
        # Asked on the desktop, not by this phone.
        {
            "id": "reply-desk",
            "role": "rem",
            "body": "Here.",
            "created_at": "t5",
            "files": files,
            "reply_to": "desk",
        },
    ]
    before = app.registry.db.execute("SELECT MAX(sequence) FROM device_outbox").fetchone()[0]
    messages = send("desktop.read", "rem.home")["value"]["messages"]
    reply = next(m for m in messages if m["id"] == f"reply-{request}")
    assert "files" not in next(m for m in messages if m["id"] == "reply-desk")
    # Rem checked both files when it wrote the reply, including the one outside its
    # open folders, so both are offered. Their paths never leave the desktop.
    assert [(f["name"], f["size"]) for f in reply["files"]] == [
        ("audit.html", 24000),
        ("elsewhere.txt", 7),
    ]
    assert str(tmp_path) not in json.dumps(messages)
    channel = app.desktop.files_thread(peer)
    offers = [e for e in app.pending(peer, before) if e["type"] == "file.offer"]
    assert [(o["thread_id"], o["payload"]["file_id"]) for o in offers] == [
        (channel, reply["files"][0]["file_id"]),
        (channel, reply["files"][1]["file_id"]),
    ]
    # Polling again shows the same offer instead of sending another.
    again = send("desktop.read", "rem.home")["value"]["messages"]
    assert next(m for m in again if m["id"] == reply["id"])["files"] == reply["files"]
    assert len([e for e in app.pending(peer, before) if e["type"] == "file.offer"]) == 2
    outside_offer = reply["files"][1]["file_id"]
    outside_chunk = app.handle(
        peer,
        event("file.request", 690, thread_id=channel, payload={"file_id": outside_offer}),
    )[-1]["payload"]
    assert base64.urlsafe_b64decode(outside_chunk["data"] + "==") == b"private"

    # A link Rem wrote in its chat reaches the same files as its attachments, path unseen.
    rem.store.messages.append(
        {"id": "linked", "role": "rem", "body": f"See [it](<{outside}>)", "created_at": "t6"}
    )
    tapped = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "linked", "href": str(outside)},
    )
    assert tapped["ok"], tapped
    assert str(tmp_path) not in json.dumps(tapped["value"])

    identifier = reply["files"][0]["file_id"]
    chunk = app.handle(
        peer,
        event("file.request", 700, thread_id=channel, payload={"file_id": identifier}),
    )[-1]["payload"]
    assert base64.urlsafe_b64decode(chunk["data"] + "==") == report.read_bytes()[:16384]
    app.remove_peer(peer)
    assert app.registry.db.execute("SELECT COUNT(*) FROM device_rem_files").fetchone()[0] == 0


def test_phone_actions_go_only_to_the_phone_that_asked(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    assert send("desktop.action", "rem.message", {"body": "20 minute timer"})["ok"]
    request = rem.calls[-1][1]["request_id"]
    actions = [
        {"type": "timer", "seconds": 1200},
        # Stored before a rule changed, or written by hand: checked again on the way out.
        {"type": "dial", "number": "*#06#"},
    ]
    rem.store.messages += [
        {
            "id": "mine",
            "role": "rem",
            "body": "Ready.",
            "created_at": "t4",
            "phone_actions": actions,
            "reply_to": request,
        },
        {
            "id": "desk",
            "role": "rem",
            "body": "Ready.",
            "created_at": "t5",
            "phone_actions": actions,
            "reply_to": "desk-request",
        },
        {
            "id": "untagged",
            "role": "rem",
            "body": "Ready.",
            "created_at": "t6",
            "phone_actions": actions,
        },
    ]
    home = send("desktop.read", "rem.home")["value"]
    assert home["phone_actions"] is True
    shown = {m["id"]: m.get("phone_actions") for m in home["messages"]}
    assert shown["mine"] == [{"type": "timer", "seconds": 1200}]
    assert shown["desk"] is None and shown["untagged"] is None


def test_files_on_finished_task_updates_go_to_the_phone_that_asked(phone, tmp_path):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    clip = tmp_path / "rem-sample.mp4"
    clip.write_bytes(b"\x00" * 17051)
    asked = send("desktop.action", "rem.message", {"body": "Send me a video please."})
    assert asked["ok"]
    request = rem.calls[-1][1]["request_id"]
    files = [{"name": "rem-sample.mp4", "size": 17051, "path": str(clip)}]
    work = {
        "task_id": "task",
        "run_id": "run",
        "thread_id": "thread",
        "title": "Find a video to share",
        "type": "errand",
        "state": "succeeded",
    }
    rem.store.messages += [
        {
            "id": "finished-run-0",
            "role": "rem",
            "kind": "task_update",
            "body": "Here's a 3-second sample video.",
            "created_at": "t4",
            "work": work,
            "files": files,
            "reply_to": request,
        },
        # A task someone asked for on the desktop keeps its files there.
        {
            "id": "finished-desk-0",
            "role": "rem",
            "kind": "task_update",
            "body": "Here.",
            "created_at": "t5",
            "work": {**work, "run_id": "desk"},
            "files": files,
            "reply_to": "desk",
        },
    ]
    before = app.registry.db.execute("SELECT MAX(sequence) FROM device_outbox").fetchone()[0]
    messages = send("desktop.read", "rem.home")["value"]["messages"]
    update = next(m for m in messages if m["id"] == "finished-run-0")
    assert update["kind"] == "task_update" and update["work"]["run_id"] == "run"
    assert [(f["name"], f["size"]) for f in update["files"]] == [("rem-sample.mp4", 17051)]
    assert "files" not in next(m for m in messages if m["id"] == "finished-desk-0")
    assert str(tmp_path) not in json.dumps(messages)
    offers = [e for e in app.pending(peer, before) if e["type"] == "file.offer"]
    assert [o["payload"]["file_id"] for o in offers] == [update["files"][0]["file_id"]]
    chunk = app.handle(
        peer,
        event(
            "file.request",
            710,
            thread_id=app.desktop.files_thread(peer),
            payload={"file_id": update["files"][0]["file_id"]},
        ),
    )[-1]["payload"]
    assert base64.urlsafe_b64decode(chunk["data"] + "==") == b"\x00" * 16384


def test_message_images_are_fetched_by_the_desktop(phone, monkeypatch):
    app, peer, _, send = phone
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    fetched: list[str] = []

    def fetch(url: str, accept: str, limit: int, **_: Any) -> tuple[str, str, bytes, bool]:
        fetched.append(url)
        if "big" in url:
            return url, "image/png", png[:limit], True
        if "page" in url:
            return url, "text/html", b"<html></html>", False
        # The bytes decide the type, not the server's header.
        return url, "application/octet-stream", png, False

    monkeypatch.setattr("gofer.ui.link_previews.fetch", fetch)
    url = "https://example.com/cat.png"
    assert send("desktop.read", "media.image", {"url": url})["code"] == "unauthorized"
    assert fetched == []
    app.desktop.enable(peer, True)
    image = send("desktop.read", "media.image", {"url": url})["value"]
    assert image["url"] == url and image["mime"] == "image/png"
    assert base64.b64decode(image["data"]) == png
    assert send("desktop.read", "media.image", {"url": "https://x.test/big"})["code"] == "invalid"
    page = send("desktop.read", "media.image", {"url": "https://x.test/page"})
    assert page["code"] == "invalid" and "isn't a JPEG" in page["detail"]
    assert send("desktop.read", "media.image", {"url": ""})["code"] == "invalid"


def test_image_fetches_refuse_private_addresses():
    from gofer.ui.link_previews import PreviewError

    with pytest.raises(PreviewError):
        module.fetch_image("http://127.0.0.1/cat.png")
    assert module.image_type(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp"
    assert module.image_type(b"<svg") is None


def test_offered_file_names_match_what_the_phone_accepts():
    # The phone strips these from names and refuses an offer whose name it would change.
    assert module._phone_name("audit report.html")
    for name in ("", ".", "..", " padded", "a\\b", "line\nbreak", "evil‮exe.txt"):
        assert not module._phone_name(name)


def test_finished_one_time_tasks_leave_the_list_but_their_links_still_open(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    done_run = {
        "id": "run2",
        "state": "succeeded",
        "thread_id": "thread2",
        "created_at": "2026-10-30T10:00:00+00:00",
        "finished_at": "2026-10-30T11:00:00+00:00",
    }
    done = {
        **rem.store.task,
        "id": "task2",
        "title": "One-off report",
        "schedule": {"kind": "once"},
        "next_occurrence_at": None,
        "active_run": None,
        "last_run": done_run,
        "recent_runs": [done_run],
    }
    recent = {
        **done,
        "id": "task3",
        "title": "Recently run weekly",
        "schedule": {"kind": "weekly", "weekdays": ["monday"]},
        "last_run": {**done_run, "id": "run3", "finished_at": "2026-10-31T09:00:00+00:00"},
    }
    rem.store.task["active_run"]["created_at"] = "2026-10-29T08:00:00+00:00"
    rem.store.tasks = lambda summary=False: [rem.store.task, done, recent]
    rem.store.thread_ids = lambda: ["thread1", "thread2"]
    rem.store.messages.append(
        {
            "id": "m3",
            "role": "rem",
            "body": "The report is ready.",
            "created_at": "t3",
            "work": {"task_id": "task2", "run_id": "run2", "thread_id": "thread2", "title": "x"},
        }
    )
    rem.store.messages.append(
        {
            "id": "m4",
            "role": "rem",
            "body": "Cleaned up long ago.",
            "created_at": "t4",
            "work": {"task_id": "gone", "run_id": "r", "thread_id": "gone-thread", "title": "Old"},
        }
    )

    assert send("desktop.read", "overview")["value"]["task_retention"] is True
    home = send("desktop.read", "rem.home")["value"]
    assert home["task_retention"] is True
    # The most recently run task comes first; the finished one-time task is gone.
    assert [t["task_id"] for t in home["tasks"]] == ["task3", "task1"]
    assert home["tasks"][0]["ran_at"] == "2026-10-31T09:00:00+00:00"
    assert all(t["listed"] and t["cleanup_at"] == "" for t in home["tasks"])
    links = {m["id"]: m["work"] for m in home["messages"] if "work" in m}
    assert links["m3"]["task_available"] and links["m3"]["thread_available"]
    assert links["m3"]["thread_id"] == "thread2"
    assert not links["m4"]["task_available"] and not links["m4"]["thread_available"]
    assert links["m4"]["title"] == "Old"

    task = send("desktop.read", "rem.task", {"task_id": "task2"})["value"]
    assert task["title"] == "One-off report" and task["listed"] is False
    assert task["finished_at"] == "2026-10-30T11:00:00+00:00"
    assert task["cleanup_at"] == "2026-11-29T11:00:00+00:00"
    assert send("desktop.read", "rem.task", {"task_id": "gone"})["code"] == "not_found"


def test_rem_home_carries_rich_results_and_live_progress(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    rem.store.task["active_run"].update(
        phase_label="Checking the work",
        waiting_reason="",
        verification_thread_id="review1",
        criteria=[{"id": "a", "result": "pass"}, {"id": "b", "result": "unknown"}],
    )
    report = "/home/user/brain/reports/compare.html"
    rem.store.messages.append(
        {
            "id": "finished-run1",
            "role": "rem",
            "created_at": "t9",
            "body": f"The comparison is ready.\n\n[compare](<{report}>)",
            "details": "| Option | Score |\n|---|---|\n| A | 1 |" + "x" * 7000,
            "suggestions": ["Why A?", "", "Compare B", "Set it up", "Fourth"],
            "meta": {"provider": "claude_code", "model": "claude-sonnet-5-5", "seconds": 2.5},
            "result": {
                "title": "Compare options",
                "type": "research",
                "state": "succeeded",
                "checks": {"passed": 2, "failed": 0, "total": 2},
                "artifacts": [
                    {
                        "name": "compare.html",
                        "title": "Compare",
                        "path": report,
                        "kind": "html",
                        "size": 10,
                        "link": f"[compare](<{report}>)",
                    },
                    # A path the message never names isn't sent to the phone.
                    {
                        "name": "x.html",
                        "title": "X",
                        "path": "/private/x.html",
                        "kind": "html",
                        "size": 1,
                    },
                ],
                "sources": [{"url": "https://example.com/a", "title": "A", "kind": "cited"}],
                "thread_id": "thread1",
            },
        }
    )
    home = send("desktop.read", "rem.home")["value"]
    assert home["rich_results"] is True
    assert home["turn_label"] == "" and home["draft_reply"] == ""
    shown = home["messages"][-1]
    assert len(shown["details"]) == 6000 and shown["details"].startswith("| Option |")
    assert shown["suggestions"] == ["Why A?", "Compare B", "Set it up"]
    assert shown["meta"] == {
        "provider": "claude_code",
        "model": "claude-sonnet-5-5",
        "seconds": 2.5,
    }
    card = shown["result"]
    assert card["checks"] == {"passed": 2, "failed": 0, "total": 2}
    assert [a["href"] for a in card["artifacts"]] == [report]
    assert "/private/x.html" not in json.dumps(shown) and "link" not in card["artifacts"][0]
    assert card["sources"] == [{"url": "https://example.com/a", "title": "A", "kind": "cited"}]
    run = home["tasks"][0]["active_run"]
    assert run["phase_label"] == "Checking the work"
    assert run["checks"] == {"passed": 1, "failed": 0, "total": 2}
    assert run["verification_thread_id"] == "review1"
    assert send("desktop.read", "overview")["value"]["rich_results"] is True


def test_rem_task_lists_each_check_of_the_newest_run_with_its_reason(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    checks = [
        {"id": "a", "description": "Tests pass", "result": "fail", "evidence": "x" * 5000},
        {"id": "b", "description": "Docs", "result": "unknown", "evidence": "Not accepted."},
        {"id": "c", "description": "Lint", "result": "pass", "evidence": "ruff passed"},
        {"id": "d", "result": "weird"},
        "not a check",
    ]
    last = {"id": "run1", "state": "blocked", "summary_short": "Needs you", "criteria": checks}
    rem.store.task.update(active_run=None, last_run=last, recent_runs=[last, {**last, "id": "r0"}])

    task = send("desktop.read", "rem.task", {"task_id": rem.store.task["id"]})["value"]
    details = task["last_run"]["criteria"]
    assert [(c["id"], c["description"], c["result"]) for c in details] == [
        ("a", "Tests pass", "fail"),
        ("b", "Docs", "unknown"),
        ("c", "Lint", "pass"),
        # Unknown outcomes and missing descriptions arrive as unknown and empty.
        ("d", "", "unknown"),
    ]
    assert len(details[0]["evidence"]) == 1500 and details[1]["evidence"] == "Not accepted."
    assert task["last_run"]["checks"] == {"passed": 1, "failed": 1, "total": 4}
    assert task["active_run"] is None
    # Older runs and the polled task list keep their counts only.
    assert all("criteria" not in run for run in task["runs"])
    home = send("desktop.read", "rem.home")["value"]
    assert "criteria" not in home["tasks"][0]["last_run"]

    rem.store.task.update(last_run={**last, "criteria": [{"id": "a", "result": "pass"}] * 60})
    task = send("desktop.read", "rem.task", {"task_id": rem.store.task["id"]})["value"]
    assert len(task["last_run"]["criteria"]) == 40


def test_source_previews_are_fetched_by_the_desktop(phone, monkeypatch):
    app, peer, _, send = phone
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    page = (
        b'<html><head><meta property="og:title" content="A guide">'
        b'<meta property="og:image" content="/cover.png"></head></html>'
    )

    def fetch(url: str, accept: str, limit: int, **_: Any) -> tuple[str, str, bytes, bool]:
        if url.endswith(".png"):
            return url, "image/png", png, False
        return url, "text/html", page, False

    monkeypatch.setattr("gofer.ui.link_previews.fetch", fetch)
    monkeypatch.setattr(module, "_previews", None)
    url = "https://example.com/guide"
    assert send("desktop.read", "media.preview", {"url": url})["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    preview = send("desktop.read", "media.preview", {"url": url})["value"]
    assert preview["title"] == "A guide" and preview["url"] == url
    assert preview["image"].startswith("data:image/png;base64,")
    assert send("desktop.read", "media.preview", {"url": ""})["code"] == "invalid"


def test_phone_pauses_and_resumes_a_run_on_a_desktop_that_says_so(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    assert send("desktop.read", "overview")["value"]["run_pause"] is True
    rem.store.task.update(
        active_run=None,
        last_run={"id": "run1", "state": "paused", "paused": {"by": "priority"}, "thread_id": "t"},
    )
    home = send("desktop.read", "rem.home")["value"]
    assert home["run_pause"] is True
    last = home["tasks"][0]["last_run"]
    assert last["state"] == "paused" and last["paused_by"] == "priority"
    assert send("desktop.action", "rem.run.pause", {"run_id": "run1"})["ok"]
    assert rem.calls[-1][0] == "pause_run" and rem.calls[-1][1][0] == "run1"
    assert send("desktop.action", "rem.run.resume", {"run_id": "run1"})["ok"]
    assert rem.calls[-1][0] == "resume_run" and rem.calls[-1][1][0] == "run1"


def test_phone_retries_a_review_and_reads_the_reply_as_it_grows(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    overview = send("desktop.read", "overview")["value"]
    assert overview["review_retry"] is True and overview["draft_deltas"] is True
    assert overview["thread_read"] is True
    assert overview["thread_purge"] is True
    rem.store.task.update(
        active_run=None,
        last_run={"id": "run1", "state": "blocked", "review_retryable": True, "thread_id": "t"},
    )
    home = send("desktop.read", "rem.home")["value"]
    assert home["tasks"][0]["last_run"]["review_retryable"] is True
    assert home["review_retry"] is True and home["draft_deltas"] is True
    assert send("desktop.action", "rem.run.retry_review", {"run_id": "run1"})["ok"]
    assert rem.calls[-1][0] == "retry_review" and rem.calls[-1][1][0] == "run1"

    rem.store.messages.append(
        {"id": "u", "role": "rem", "kind": "task_update", "body": "Done.", "created_at": "t"}
    )
    assert send("desktop.read", "rem.home")["value"]["messages"][-1]["kind"] == "task_update"

    # While Rem replies, the phone reads only what was added since its last read.
    rem.store.conversation_setting = {
        "turn_state": "replying",
        "turn_label": "Writing the reply",
        "draft_reply": "Here is the plan",
    }
    first = send("desktop.read", "rem.draft", {"since": 0})["value"]
    first.pop("digest", None)
    assert first == {
        "replying": True,
        "turn_label": "Writing the reply",
        "start": 0,
        "text": "Here is the plan",
        "length": 16,
        "reset": False,
        "attempt": None,
    }
    rem.store.conversation_setting["draft_reply"] = "Here is the plan for today."
    grown = send("desktop.read", "rem.draft", {"since": 16})["value"]
    assert (grown["start"], grown["text"], grown["reset"]) == (16, " for today.", False)
    # A new attempt starts over shorter than what the phone has: it gets all of it.
    rem.store.conversation_setting["draft_reply"] = "Retry"
    restarted = send("desktop.read", "rem.draft", {"since": 27})["value"]
    assert (restarted["start"], restarted["text"], restarted["reset"]) == (0, "Retry", True)
    rem.store.conversation_setting = {"turn_state": "idle", "draft_reply": "stale"}
    done = send("desktop.read", "rem.draft", {"since": 5})["value"]
    assert done["replying"] is False and done["text"] == "" and done["reset"] is True


def test_a_new_reply_attempt_never_adds_to_the_phone_s_old_draft(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    rem.store.conversation_setting = {
        "turn_state": "replying",
        "draft_reply": "I'll check",
        "draft_attempt": 1,
    }
    assert send("desktop.read", "rem.draft", {"since": 0})["value"]["text"] == "I'll check"
    # Rem asked its model again (a repair, say) and the new text is already longer.
    rem.store.conversation_setting = {
        "turn_state": "replying",
        "draft_reply": "Starting a worker on the audit now.",
        "draft_attempt": 2,
    }
    again = send("desktop.read", "rem.draft", {"since": 10})["value"]
    assert (again["start"], again["reset"]) == (0, True)
    assert again["text"] == "Starting a worker on the audit now."
    # Within one attempt, reads go on adding only the new text.
    rem.store.conversation_setting["draft_reply"] += " It reports back."
    grown = send("desktop.read", "rem.draft", {"since": 35})["value"]
    assert (grown["start"], grown["text"], grown["reset"]) == (35, " It reports back.", False)


def test_a_resent_draft_read_that_names_its_attempt_still_starts_over(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    rem.store.conversation_setting = {
        "turn_state": "replying",
        "draft_reply": "I'll check",
        "draft_attempt": 1,
    }
    first = send("desktop.read", "rem.draft", {"since": 0})["value"]
    assert (first["text"], first["attempt"]) == ("I'll check", 1)
    rem.store.conversation_setting = {
        "turn_state": "replying",
        "draft_reply": "Starting a worker on the audit now.",
        "draft_attempt": 2,
    }
    # The answer to this read is lost, so the phone sends the same read again.
    for _ in range(2):
        again = send("desktop.read", "rem.draft", {"since": 10, "attempt": 1})["value"]
        assert (again["start"], again["reset"], again["attempt"]) == (0, True, 2)
        assert again["text"] == "Starting a worker on the audit now."


def test_a_phone_message_sent_while_rem_replies_shows_as_waiting(phone, home):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    assert send("desktop.action", "rem.message", {"body": "What is running?"})["ok"]
    queued = send("desktop.action", "rem.message", {"body": "And what finished?"})
    assert queued["ok"], queued
    view = queued["value"]
    assert view["turn_state"] == "replying"
    assert [(m["body"], m.get("state")) for m in view["messages"][-2:]] == [
        ("What is running?", None),
        ("And what finished?", "waiting"),
    ]


def test_phone_reads_and_changes_rem_memories_like_the_desktop_panel(phone, home):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    assert send("desktop.read", "overview")["value"]["memories"] is True
    added = send("desktop.action", "rem.memory.add", {"text": "Works in Eastern time"})["value"]
    assert added["mode"] == "on"
    (item,) = added["memories"]
    assert item["text"] == "Works in Eastern time" and item["added_by_user"] is True
    assert "source_message_id" not in item
    secret = send("desktop.action", "rem.memory.add", {"text": "password: hunter22"})
    assert secret["code"] == "invalid" and "password, key or token" in secret["detail"]

    edited = send(
        "desktop.action", "rem.memory.edit", {"memory_id": item["id"], "text": "Works in UTC"}
    )["value"]
    assert edited["memories"][0]["text"] == "Works in UTC"
    paused = send("desktop.action", "rem.memory.mode", {"mode": "paused"})["value"]
    assert paused["mode"] == "paused"
    assert send("desktop.read", "rem.memories")["value"]["mode"] == "paused"
    forget = {"memory_id": item["id"]}
    assert send("desktop.action", "rem.memory.forget", forget)["value"]["memories"] == []
    # A redelivered forget isn't an error.
    assert send("desktop.action", "rem.memory.forget", forget)["ok"] is True

    send("desktop.action", "rem.memory.mode", {"mode": "on"})
    home.generate = lambda p, c, e: {"reply": "Noted.", "memories": ["Prefers tea"]}
    home.submit_message({"request_id": "m1", "body": "I like tea"})
    home._reply()
    shown = next(
        m for m in send("desktop.read", "rem.home")["value"]["messages"] if m["id"] == "reply-m1"
    )
    assert shown["memory"]["saved"][0]["text"] == "Prefers tea"
    assert shown["memory"]["undone"] is False
    undone = send("desktop.action", "rem.memory.undo", {"message_id": "reply-m1"})["value"]
    assert undone["memories"] == []
    shown = next(
        m for m in send("desktop.read", "rem.home")["value"]["messages"] if m["id"] == "reply-m1"
    )
    assert shown["memory"]["undone"] is True


def test_phone_memory_list_tags_project_memories_by_name(phone, home, tmp_path):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    root = tmp_path / "Site"
    root.mkdir()
    home.configure({"provider": "codex", "projects": [{"root": str(root), "name": "Site"}]})
    home.add_memory({"text": "Deploys from main", "project": str(root)})
    home.add_memory({"text": "Prefers tea"})
    listed = send("desktop.read", "rem.memories")["value"]["memories"]
    tags = {item["text"]: item["project"] for item in listed}
    # The phone gets the project's name for the tag, never its folder path.
    assert tags == {"Deploys from main": "Site", "Prefers tea": ""}
    # Editing the words on the phone keeps the memory in its project.
    project_item = next(item for item in listed if item["project"])
    edited = send(
        "desktop.action",
        "rem.memory.edit",
        {"memory_id": project_item["id"], "text": "Deploys from tags"},
    )["value"]["memories"]
    assert {item["text"]: item["project"] for item in edited}["Deploys from tags"] == "Site"


def test_phone_sets_an_end_date_and_keeps_an_ended_memory(phone, home):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    home.add_memory({"text": "Kids are at camp"})
    (item,) = send("desktop.read", "rem.memories")["value"]["memories"]
    assert item["until"] == "" and item["ended"] is False
    dated = send(
        "desktop.action", "rem.memory.edit", {"memory_id": item["id"], "until": "2099-07-04"}
    )["value"]["memories"][0]
    assert dated["until"].startswith("2099-07-04T23:59:59") and dated["ended"] is False
    assert dated["text"] == "Kids are at camp"
    # An end that has passed shows as ended; Keep (until null) clears it.
    with home.store.connect() as db:
        saved = home.store.setting(db, "memories")
        saved[item["id"]]["until"] = "2026-01-01T00:00:00+00:00"
        home.store.set_setting(db, "memories", saved)
    assert send("desktop.read", "rem.memories")["value"]["memories"][0]["ended"] is True
    kept = send("desktop.action", "rem.memory.edit", {"memory_id": item["id"], "until": None})
    assert kept["value"]["memories"][0] == {**dated, "until": "", "ended": False}
    # A past day is refused, as on the desktop.
    refused = send(
        "desktop.action", "rem.memory.edit", {"memory_id": item["id"], "until": "2020-01-01"}
    )
    assert refused["ok"] is False


def _file_decision(home, project: Path, topic: str = "checks.fast") -> dict[str, Any]:
    from gofer.ui import rem_decisions

    (project / "package.json").write_text('{"scripts": {"test": "vitest"}}')
    request = rem_decisions.DecisionRequest.model_validate(
        {
            "topic": topic,
            "question": "Which command runs the fast checks?",
            "recommendation": {
                "answer": "npm test",
                "why": "package.json defines it.",
                "evidence": [
                    {"kind": "quote", "ref": str(project / "package.json"), "excerpt": "vitest"}
                ],
            },
            "alternatives": [{"answer": "make check", "why_not": "No Makefile here."}],
        }
    )
    checked, _ = rem_decisions.check_evidence(request, str(project))
    with home.store.connect() as db:
        _, item = rem_decisions.file_request(
            db, home.store, checked, project=str(project), decides_alone=False
        )
    return item


def test_phone_reads_and_answers_rem_decisions_like_the_desktop_panel(phone, home, tmp_path):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    overview = send("desktop.read", "overview")["value"]
    assert overview["rem_decisions"] is True
    project = tmp_path / "Site"
    project.mkdir()
    item = _file_decision(home, project)

    listed = send("desktop.read", "rem.decisions")["value"]
    assert listed["waiting"] == 1 and listed["more"] == 0
    (shown,) = listed["decisions"]
    assert shown["id"] == item["id"] and shown["status"] == "pending"
    assert shown["recommended"] == "npm test" and shown["why"] == "package.json defines it."
    assert shown["alternatives"] == [{"answer": "make check", "why_not": "No Makefile here."}]
    # The phone sees the project's name, and a file outside it by name only; never a folder.
    assert shown["project"] == "Site"
    assert shown["evidence"][0]["ref"] == "package.json"
    assert str(tmp_path) not in json.dumps(listed)
    assert [h["action"] for h in shown["history"]] == ["requested"]

    # A stale revision is refused, and nothing changes.
    stale = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "use", "id": item["id"], "expected_revision": listed["revision"] - 1},
    )
    assert stale == {"ok": False, "code": "conflict", "detail": module.DECISION_CHANGED}
    used = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "use", "id": item["id"], "expected_revision": listed["revision"]},
    )["value"]
    (confirmed,) = used["decisions"]
    assert confirmed["status"] == "confirmed" and confirmed["answer"] == "npm test"
    assert confirmed["decided_by"] == "user" and used["revision"] == listed["revision"] + 1

    edited = send(
        "desktop.action",
        "rem.decision.change",
        {
            "action": "edit",
            "id": item["id"],
            "expected_revision": used["revision"],
            "answer": "npm run test:fast",
        },
    )["value"]
    assert edited["decisions"][0]["answer"] == "npm run test:fast"
    reopened = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "reopen", "id": item["id"], "expected_revision": edited["revision"]},
    )["value"]
    # Ask again files a new card; the decision stays in force until it is answered.
    asked, kept = reopened["decisions"]
    assert asked["status"] == "pending" and asked["asked_again"] is True
    assert asked["supersedes"] == item["id"] and asked["current_answer"] == "npm run test:fast"
    assert kept["id"] == item["id"] and kept["status"] == "confirmed"
    changed = send(
        "desktop.action",
        "rem.decision.change",
        {
            "action": "change",
            "id": asked["id"],
            "expected_revision": reopened["revision"],
            "answer": "  ",
        },
    )
    assert changed["code"] == "invalid" and "answer" in changed["detail"]
    rejected = send(
        "desktop.action",
        "rem.decision.change",
        {
            "action": "reject",
            "id": asked["id"],
            "expected_revision": reopened["revision"],
            "reason": "Not now",
        },
    )["value"]
    # Turning the card down keeps the current answer.
    assert [(d["id"], d["status"]) for d in rejected["decisions"]] == [
        (item["id"], "confirmed"),
        (asked["id"], "rejected"),
    ]
    assert rejected["decisions"][1]["history"][-1]["note"] == "Not now"
    # Retire is its own action from the phone too: the schema and the desktop accept it, and
    # a decision with no record file is told to use Forget instead.
    retire = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "retire", "id": item["id"], "expected_revision": rejected["revision"]},
    )
    assert retire["code"] == "invalid" and "forget this one instead" in retire["detail"]
    forgotten = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "forget", "id": item["id"], "expected_revision": rejected["revision"]},
    )["value"]
    assert [d["id"] for d in forgotten["decisions"]] == [asked["id"]]
    missing = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "use", "id": item["id"], "expected_revision": forgotten["revision"]},
    )
    assert missing["code"] == "not_found"


def test_phone_looks_again_in_a_decisions_project_and_sees_raticodes_check(phone, home, tmp_path):
    from gofer.ui import rem_decisions

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    project = tmp_path / "Site"
    project.mkdir()
    (project / "package.json").write_text('{\n  "scripts": {\n    "test": "vitest run"\n  }\n}\n')
    item = _file_decision(home, project)
    listed = send("desktop.read", "rem.decisions")["value"]
    used = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "use", "id": item["id"], "expected_revision": listed["revision"]},
    )["value"]
    assert used["decisions"][0]["last_check"] is None
    assert used["decisions"][0]["carried_over"] is False
    assert used["decisions"][0]["read_from"] is None
    with home.store.connect() as db:
        rem_decisions.note_check(
            db,
            home.store,
            item["id"],
            passed=False,
            result={
                "passed": False,
                "exit_code": 1,
                "ran_at": "2026-11-21T08:00:00+00:00",
                "seconds": 2.5,
                "output": "secret log",
                "folder": str(project),
            },
        )
    shown = send("desktop.read", "rem.decisions")["value"]
    assert shown["decisions"][0]["last_check"] == {
        "passed": False,
        "exit_code": 1,
        "ran_at": "2026-11-21T08:00:00+00:00",
        "seconds": 2.5,
    }
    # Its output and folder stay on the desktop.
    assert "secret log" not in json.dumps(shown) and str(tmp_path) not in json.dumps(shown)
    # Look again scans the decision's project by its ID: the phone sends no folder. The
    # decision in force stays; the scan's own finding is the same answer, so nothing new.
    scanned = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "scan", "id": item["id"], "expected_revision": shown["revision"]},
    )["value"]
    (kept,) = [d for d in scanned["decisions"] if d["topic"] == "checks.fast"]
    assert kept["id"] == item["id"] and kept["status"] == "confirmed"
    stale = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "scan", "id": item["id"], "expected_revision": shown["revision"] - 1},
    )
    assert stale["code"] == "conflict"


def test_phone_decision_changes_stay_within_the_contract(phone, home):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    # Adding a decision names a project by folder, so it stays on the desktop and the CLI.
    added = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "add", "id": "D-1", "expected_revision": 0, "answer": "x"},
    )
    assert added["ok"] is False
    unrevised = send("desktop.action", "rem.decision.change", {"action": "use", "id": "D-1"})
    assert unrevised["ok"] is False
    # The desktop checks the same rules again, whatever reaches it.
    rem = app.desktop.rem
    with pytest.raises(module.Refused) as refused:
        app.desktop._rem_decision_change(rem, {"action": "add", "id": "D-1"})
    assert refused.value.code == "invalid"
    with pytest.raises(module.Refused) as refused:
        app.desktop._rem_decision_change(
            rem, {"action": "use", "id": "D-1", "expected_revision": True}
        )
    assert refused.value.code == "invalid"


def _record_book(revision: int = 4) -> dict[str, Any]:
    """A decisions book with one design decision record in force and a conflict's fix."""
    record = {
        "context": "Sessions lived in Redis. " + "x" * 2000,
        "options": "Keep Redis; move to Postgres.",
        "consequences": "Session reads go through the app's database.",
        "check": "No change adds a Redis client. " + "y" * 900,
        "applies_to": ["src/sessions/**", *(f"src/part{n}/**" for n in range(12))],
        "forbids": ["src/**/redis*.py", "z" * 300, 7],
        "signals": ["redis"],
    }
    return {
        "revision": revision,
        "waiting": 1,
        "provisional": 0,
        "confirmed": 1,
        "items": [
            {
                "id": "D-6",
                "project": "/home/me/Site",
                "project_name": "Site",
                "topic": "conflict:d-3-d-4",
                "question": "These decisions conflict: D-3, D-4. Replace them with one?",
                "answer": "Store sessions in SQLite.",
                "recommended": "Store sessions in SQLite.",
                "status": "pending",
                "remember_as": "record",
                "record": record,
                "conflict": "D-3 keeps Redis while D-4 removes it, see /home/me/Site/notes.md",
                "supersedes_all": ["D-3", "D-4", "D-5", "D-7", "D-8", "D-9", "D-10"],
                "history": [],
            },
            {
                "id": "D-4",
                "project": "/home/me/Site",
                "project_name": "Site",
                "topic": "adr:sessions",
                "question": "Where do sessions live?",
                "answer": "Store sessions in SQLite.",
                "status": "confirmed",
                "remember_as": "record",
                "record": record,
                "record_path": "docs/decisions/0004-sessions.md",
                "record_error": "",
                "edited_outside": True,
                "history": [],
            },
            {
                "id": "D-2",
                "project": "/home/me/Site",
                "project_name": "Site",
                "topic": "checks.fast",
                "question": "Which command runs the fast checks?",
                "answer": "npm test",
                "status": "confirmed",
                "remember_as": "fact",
                "record": {"context": "not a record"},
                "record_path": "docs/decisions/9999.md",
                "edited_outside": True,
                "history": [],
            },
            {
                "id": "D-1",
                "project": None,
                "topic": "tone",
                "question": "How should Rem write?",
                "answer": "Plainly.",
                "status": "confirmed",
                "history": [],
            },
        ],
    }


class _RecordRem:
    """A Rem whose decisions are one record book; it keeps what each change asked for."""

    def __init__(self) -> None:
        self.book = _record_book()
        self.changes: list[dict[str, Any]] = []

    def decisions(self) -> dict[str, Any]:
        return self.book

    def change_decisions(self, payload: dict[str, Any]) -> dict[str, Any]:
        from gofer.ui.rem_decisions import DecisionRefused

        if payload.get("expected_revision") not in (None, self.book["revision"]):
            raise DecisionRefused("conflict", "The decisions changed.")
        self.changes.append(payload)
        self.book = {**self.book, "revision": self.book["revision"] + 1}
        return self.book


def test_queued_questions_reach_the_phone_as_queued_with_their_count(phone):
    app, _, _, _ = phone
    book = _record_book()
    queued = {**book["items"][0], "id": "D-9", "topic": "t9", "status": "queued"}
    shown = app.desktop._rem_decisions({"decisions": {**book, "queued": 1, "items": [queued]}})
    # Never shown as a waiting card: the phone lists it under "N more queued".
    assert shown["queued"] == 1
    assert shown["decisions"][0]["status"] == "queued"


def test_decision_records_reach_the_phone_bounded_and_without_folders(phone):
    app, _, _, _ = phone
    shown = app.desktop._rem_decisions({"decisions": _record_book()})
    fix, record, fact, memory = shown["decisions"]
    # A conflict's fix names what it replaces (at most 5) and why, with no folder in it.
    assert fix["supersedes_all"] == ["D-3", "D-4", "D-5", "D-7", "D-8"]
    assert fix["conflict"] == "D-3 keeps Redis while D-4 removes it, see notes.md"
    assert fix["remember_as"] == "record" and fix["record_path"] == ""
    # A record's parts, each section and list bounded.
    assert record["remember_as"] == "record"
    assert record["record_path"] == "docs/decisions/0004-sessions.md"
    assert record["edited_outside"] is True and record["record_error"] == ""
    parts = record["record"]
    assert len(parts["context"]) == 1500 and parts["context"].startswith("Sessions lived")
    assert len(parts["check"]) == 600
    assert parts["options"] == "Keep Redis; move to Postgres."
    assert len(parts["applies_to"]) == 10 and parts["applies_to"][0] == "src/sessions/**"
    assert parts["forbids"] == ["src/**/redis*.py", "z" * 200]
    assert parts["signals"] == ["redis"]
    # A fact or a memory carries no record, whatever the stored entry holds.
    assert fact["remember_as"] == "fact" and fact["record"] is None
    assert fact["record_path"] == "" and fact["edited_outside"] is False
    assert memory["remember_as"] == "memory" and memory["record"] is None
    assert memory["supersedes_all"] == [] and memory["conflict"] == ""
    assert "/home/me" not in json.dumps(shown)


def test_record_not_in_the_repository_says_why(phone):
    app, _, _, _ = phone
    book = _record_book()
    book["items"][1] = {
        **book["items"][1],
        "record_path": "",
        "record_error": "The project at /home/me/Site isn't a git repository.",
    }
    (_, record, _, _) = app.desktop._rem_decisions({"decisions": book})["decisions"]
    assert record["record_path"] == ""
    assert record["record_error"] == "The project at Site isn't a git repository."
    assert record["record_restore"] is False


def test_record_a_rem_run_changed_tells_the_phone_without_the_hash(phone):
    app, _, _, _ = phone
    book = _record_book()
    book["items"][1] = {
        **book["items"][1],
        "record_restore": "9f2c" * 16,
        "record_error": "The landing branch checkout has uncommitted changes.",
    }
    book["items"][2] = {**book["items"][2], "record_restore": "abcd"}
    shown = app.desktop._rem_decisions({"decisions": book})
    (_, record, fact, _) = shown["decisions"]
    # Only whether a restore waits: the phone says the file differs, not "not in the repo yet".
    assert record["record_restore"] is True
    assert record["record_path"] == "docs/decisions/0004-sessions.md"
    assert fact["record_restore"] is False, "only a design decision has a file"
    assert "9f2c" not in json.dumps(shown)


def test_phone_replace_and_record_edit_reach_the_store(phone):
    app, _, _, _ = phone
    rem = _RecordRem()
    replaced = app.desktop._rem_decision_change(
        rem,
        {
            "action": "replace",
            "id": "D-4",
            "expected_revision": 4,
            "answer": "Store sessions in Postgres.",
            "question": "Where do sessions live?",
            "why": "We run Postgres already.",
            "context": "c" * 1600,
            "options": "Keep SQLite.",
            "consequences": "One database.",
            "check": "k" * 700,
            "applies_to": ["src/sessions/**"],
            "forbids": ["src/**/sqlite*.py"],
            "signals": ["sqlite"],
            "note": "From the phone",
        },
    )
    assert rem.changes[-1] == {
        "action": "replace",
        "id": "D-4",
        "expected_revision": 4,
        "answer": "Store sessions in Postgres.",
        "question": "Where do sessions live?",
        "why": "We run Postgres already.",
        "note": "From the phone",
        "context": "c" * 1500,
        "options": "Keep SQLite.",
        "consequences": "One database.",
        "check": "k" * 600,
        "applies_to": ["src/sessions/**"],
        "forbids": ["src/**/sqlite*.py"],
        "signals": ["sqlite"],
    }
    assert replaced["revision"] == 5
    # An edit sends only the parts it changes.
    app.desktop._rem_decision_change(
        rem,
        {
            "action": "edit",
            "id": "D-4",
            "expected_revision": 5,
            "consequences": "One database, backed up nightly.",
            "applies_to": [],
        },
    )
    assert rem.changes[-1] == {
        "action": "edit",
        "id": "D-4",
        "expected_revision": 5,
        "consequences": "One database, backed up nightly.",
        "applies_to": [],
    }
    # Record parts mean nothing to the other actions, so they don't pass through.
    app.desktop._rem_decision_change(
        rem, {"action": "use", "id": "D-6", "expected_revision": 6, "context": "x"}
    )
    assert rem.changes[-1] == {"action": "use", "id": "D-6", "expected_revision": 6}
    # A stale revision is still a conflict.
    with pytest.raises(module.Refused) as refused:
        app.desktop._rem_decision_change(
            rem, {"action": "replace", "id": "D-4", "expected_revision": 1, "answer": "x"}
        )
    assert refused.value.code == "conflict"
    assert str(refused.value) == module.DECISION_CHANGED


@pytest.mark.parametrize(
    "bad",
    [
        {"applies_to": "src/**"},
        {"forbids": ["ok", 3]},
        {"signals": ["x"] * 11},
        {"applies_to": ["x" * 201]},
        {"context": ["not text"]},
        {"check": 5},
        {"note": None},
    ],
    ids=["glob-text", "glob-number", "too-many", "too-long", "context-list", "check", "note"],
)
def test_phone_record_changes_refuse_bad_types(phone, bad):
    app, _, _, _ = phone
    rem = _RecordRem()
    with pytest.raises(module.Refused) as refused:
        app.desktop._rem_decision_change(
            rem, {"action": "edit", "id": "D-4", "expected_revision": 4, **bad}
        )
    assert refused.value.code == "invalid"
    assert rem.changes == []


def test_phone_replaces_a_decision_through_the_real_store(phone, home, tmp_path):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    project = tmp_path / "Site"
    project.mkdir()
    item = _file_decision(home, project)
    listed = send("desktop.read", "rem.decisions")["value"]
    used = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "use", "id": item["id"], "expected_revision": listed["revision"]},
    )["value"]
    replaced = send(
        "desktop.action",
        "rem.decision.change",
        {
            "action": "replace",
            "id": item["id"],
            "expected_revision": used["revision"],
            "answer": "npm run test:fast",
            "why": "It skips the slow browser tests.",
            "note": "From the phone",
        },
    )["value"]
    by_id = {d["id"]: d for d in replaced["decisions"]}
    old = by_id[item["id"]]
    (new,) = [d for d in replaced["decisions"] if d["id"] != item["id"]]
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"]
    assert new["status"] == "confirmed" and new["answer"] == "npm run test:fast"
    assert new["supersedes"] == item["id"] and new["remember_as"] == "fact"
    assert new["history"][-1]["note"] == "From the phone"
    stale = send(
        "desktop.action",
        "rem.decision.change",
        {
            "action": "replace",
            "id": new["id"],
            "expected_revision": used["revision"],
            "answer": "x",
        },
    )
    assert stale == {"ok": False, "code": "conflict", "detail": module.DECISION_CHANGED}


def test_phone_presses_ok_on_a_change_the_user_asked_rem_for(phone, home, tmp_path):
    from gofer.ui import rem_decisions

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    project = tmp_path / "Site"
    project.mkdir()
    item = _file_decision(home, project)
    home.change_decisions({"action": "use", "id": item["id"]})
    asked = f"Change {item['id']} to npm run test:fast please"
    with home.store.connect() as db:
        entry, _ = rem_decisions.propose_edit(
            db,
            home.store,
            rem_decisions.DecisionEdit(id=item["id"], answer="npm run test:fast", said=asked),
            message=asked,
        )
        assert entry is not None
        home.store.message(
            db,
            "Check it below.",
            identity="reply-asked",
            decision_edits=[rem_decisions.edit_card(entry)],
        )
    shown = next(
        m for m in send("desktop.read", "rem.home")["value"]["messages"] if m["id"] == "reply-asked"
    )
    (card,) = shown["decision_edits"]
    assert card["id"] == entry["id"] and card["status"] == "waiting" and card["project"] == "Site"
    assert card["changes"] == [
        {"field": "answer", "label": "Decision", "before": "npm test", "after": "npm run test:fast"}
    ]
    assert str(tmp_path) not in json.dumps(shown)
    listed = send("desktop.read", "rem.decisions")["value"]
    assert [e["id"] for e in listed["edits"]] == [entry["id"]]
    # OK: the decision changes through the desktop's store, whatever revision the phone had.
    done = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "confirm_edit", "id": entry["id"], "expected_revision": 0},
    )["value"]
    assert (
        next(d for d in done["decisions"] if d["id"] == item["id"])["answer"] == "npm run test:fast"
    )
    assert done["edits"] == []
    shown = next(
        m for m in send("desktop.read", "rem.home")["value"]["messages"] if m["id"] == "reply-asked"
    )
    assert shown["decision_edits"][0]["status"] == "applied"
    # Pressing it twice says why, and changes nothing.
    again = send(
        "desktop.action",
        "rem.decision.change",
        {"action": "confirm_edit", "id": entry["id"], "expected_revision": 0},
    )
    assert again["ok"] is False and "already made" in again["detail"]


def test_task_update_cards_carry_the_decision_and_its_status_now(phone, home, tmp_path):
    from gofer.ui import rem_decisions

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    project = tmp_path / "Site"
    project.mkdir()
    item = _file_decision(home, project)
    fallback = rem_decisions.fallback_request(
        "Deploy now?", f"Found {project}/deploy.sh but no notes.", None
    )
    work = {"task_id": "t1", "run_id": "r1", "thread_id": "th1", "title": "Checks"}
    with home.store.connect() as db:
        home.store.message(
            db,
            rem_decisions.waiting_text(item),
            identity="update-1",
            kind="task_update",
            work={**work, "needs_user": True, "decision": rem_decisions.card(item)},
        )
        home.store.message(
            db,
            rem_decisions.waiting_text(fallback),
            identity="update-2",
            kind="task_update",
            work={**work, "needs_user": True, "decision": fallback},
        )
    view = send("desktop.read", "rem.home")["value"]
    assert view["decisions"] is True and view["decisions_waiting"] == 1
    messages = {m["id"]: m for m in view["messages"]}
    card = messages["update-1"]["work"]["decision"]
    assert card["id"] == item["id"] and card["recommended"] == "npm test"
    assert card["status"] == "pending" and card["live_status"] == "pending"
    assert card["evidence"][0]["ref"] == "package.json"
    plain = messages["update-2"]["work"]["decision"]
    assert plain["fallback"] is True and "id" not in plain and "live_status" not in plain
    assert plain["question"] == "Deploy now?" and str(tmp_path) not in plain["found"]

    # Answered on the desktop: the card still says what it asked, and its status now.
    home.change_decisions({"action": "use", "id": item["id"]})
    view = send("desktop.read", "rem.home")["value"]
    card = next(m for m in view["messages"] if m["id"] == "update-1")["work"]["decision"]
    assert card["status"] == "pending" and card["live_status"] == "confirmed"
    assert view["decisions_waiting"] == 0
    assert view["decisions_revision"] == send("desktop.read", "rem.decisions")["value"]["revision"]


def test_a_question_forgotten_while_it_waited_reaches_the_phone_as_withdrawn(phone, home, tmp_path):
    from gofer.ui import rem_decisions

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    project = tmp_path / "Site"
    project.mkdir()
    item = _file_decision(home, project)
    work = {"task_id": "t1", "run_id": "r1", "thread_id": "th1", "title": "Checks"}
    with home.store.connect() as db:
        home.store.message(
            db,
            rem_decisions.waiting_text(item),
            identity="update-1",
            kind="task_update",
            work={**work, "needs_user": True, "decision": rem_decisions.card(item)},
        )
    home.change_decisions({"action": "forget", "id": item["id"]})
    view = send("desktop.read", "rem.home")["value"]
    card = next(m for m in view["messages"] if m["id"] == "update-1")["work"]["decision"]
    assert card["status"] == "withdrawn" and card["live_status"] == ""


def test_phone_alerts_cover_only_its_own_requests_and_their_tasks(phone, home):
    app, peer, _, send = phone
    assert send("desktop.read", "rem.notices")["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    store = home.store
    assert send("desktop.read", "overview")["value"]["rem_notices"] is True

    def asked_here() -> str:
        request = str(uuid4())
        app.desktop.db.execute("INSERT INTO device_rem_requests VALUES (?,?)", (peer, request))
        return request

    def task(identity: str, source: str) -> None:
        body = {"id": identity, "delegation_context": {"source_message_id": source}}
        with store.connect() as db:
            db.execute("INSERT INTO tasks VALUES (?,?)", (identity, json.dumps(body)))

    with store.connect() as db:
        store.message(db, "Typed on the desktop", identity="desk-1", role="user")
        store.message(db, "Desktop answer", identity="reply-desk-1")
    # With no position the phone only learns where the conversation is now.
    start = send("desktop.read", "rem.notices")["value"]
    assert start["notices"] == [] and start["cursor"] == 2 and start["more"] is False

    mine = asked_here()
    task("task-phone", mine)
    task("task-desk", "desk-1")
    update = {"task_id": "task-phone", "title": "Build check", "needs_user": True}
    with store.connect() as db:
        store.message(db, "What failed?", identity=mine, role="user")
        store.message(db, "Here is what failed.", identity=f"reply-{mine}")
        store.message(db, "Needs you", identity="finished-p", kind="task_update", work=update)
        desk = {"task_id": "task-desk"}
        store.message(db, "Desk task done", identity="finished-d", kind="task_update", work=desk)
        store.message(db, "Another desktop question", identity="desk-2", role="user")
        store.message(db, "Desktop answer two", identity="reply-desk-2")
    read = send("desktop.read", "rem.notices", {"after": start["cursor"]})["value"]
    assert [(n["id"], n["kind"]) for n in read["notices"]] == [
        (f"reply-{mine}", "reply"),
        ("finished-p", "task_update"),
    ]
    assert read["notices"][1]["title"] == "Build check" and read["notices"][1]["needs_user"] is True
    assert read["notices"][0]["body"] == "Here is what failed." and read["cursor"] == 8
    assert send("desktop.read", "rem.notices", {"after": 8})["value"]["notices"] == []

    # A reply that lands after the phone already read its own question still counts.
    later = asked_here()
    with store.connect() as db:
        store.message(db, "And now?", identity=later, role="user")
    assert send("desktop.read", "rem.notices", {"after": 8})["value"]["cursor"] == 9
    with store.connect() as db:
        for index in range(10):
            store.message(db, f"Part {index}", identity=f"reply-{later}-{index}")
    burst = send("desktop.read", "rem.notices", {"after": 9})["value"]
    assert [n["body"] for n in burst["notices"]] == [f"Part {i}" for i in range(2, 10)]
    assert burst["skipped"] == 2 and burst["cursor"] == 19

    # A reset conversation, or a bad position, never replays old messages.
    assert send("desktop.read", "rem.notices", {"after": 500})["value"] == {
        "cursor": 19,
        "notices": [],
        "skipped": 0,
        "more": False,
        "digest": send("desktop.read", "rem.notices", {"after": 500})["value"]["digest"],
    }
    for bad in ("9", True, -1, 2**53, 1.5):
        assert send("desktop.read", "rem.notices", {"after": bad})["code"] == "invalid"
    # A legacy "Started" notice repeating a reply written with it alerts once, as the chat shows it.
    started = {"task_id": "task-phone", "run_id": "run-9", "title": "Build check"}
    with store.connect() as db:
        store.message(db, "Build check", identity=asked_here(), role="user")
        store.message(db, "Started Build check.", identity="queued-run-9", work=started)
        store.message(db, "On it.", identity="reply-run-9", work=started)
    legacy = send("desktop.read", "rem.notices", {"after": 19})["value"]
    assert [n["id"] for n in legacy["notices"]] == ["reply-run-9"] and legacy["cursor"] == 22

    # Another phone's requests are not this phone's alerts.
    app.desktop.db.execute("DELETE FROM device_rem_requests WHERE peer=?", (peer,))
    assert send("desktop.read", "rem.notices", {"after": 2})["value"]["notices"] == []


ALL_ALERTS = ["needs_you", "finished", "proposals", "workflow"]


def alerting_phone(phone: Any, home: Any) -> Any:
    """A phone allowed to use this desktop, with the real Rem home behind it."""
    app, peer, context, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    return app, peer, context, send


def research_round(home, session: str, *titles: str) -> None:
    """Self-work ingesting one research pass of the round ``session``."""
    run = {
        "id": str(uuid4()),
        "worker_result": "```json\n"
        + json.dumps({"proposals": [{"title": t} for t in titles], "duplicates": []})
        + "\n```",
    }
    record = {"title": "Research", "target": "/project", "session_id": session}
    with home.store.connect() as db:
        home.self_work._ingest(db, run, record, [], {"/project"})


def check_phone_can_read(answer: dict[str, Any]) -> None:
    """What the phone's parser relies on, and only ever keeps."""
    assert 0 < len(answer["mark"]) <= 256
    for notice in answer["notices"]:
        assert notice["kind"] in {
            "reply",
            "task_update",
            "proposals",
            "organization",
            "workflow",
            "decision",
        }
        assert notice.get("reason", "needs_you") in ALL_ALERTS
        assert 0 < len(notice["id"]) <= 128 and len(notice["body"]) <= 1500


# Sequences for refused_alert_read, clear of the ones the phone fixture's send uses.
_REFUSED_SEQUENCE = iter(range(5000, 6000))


def refused_alert_read(app: Any, peer: str, params: dict[str, Any]) -> bool:
    """Whether the protocol schema or the desktop refuses this ``rem.notices`` read."""
    replies = app.handle(
        peer,
        event(
            "desktop.read",
            next(_REFUSED_SEQUENCE),
            thread_id=None,
            payload={"op": "rem.notices", "params": params},
        ),
    )
    results = [r["payload"] for r in replies if r["type"] == "desktop.result"]
    if results:
        return results[0]["ok"] is False and results[0]["code"] == "invalid"
    errors = [r["payload"] for r in replies if r["type"] == "error"]
    return len(errors) == 1 and errors[0]["retryable"] is False


def test_alert_filter_is_advertised_and_checked(phone, home):
    app, peer, _, send = alerting_phone(phone, home)
    overview = send("desktop.read", "overview")["value"]
    assert overview["rem_notices_filter"] is True
    assert overview["rem_notices_workflow"] is True
    bad_lists: list[Any] = [
        [],
        None,
        "needs_you",
        ["sometimes"],
        [1],
        [["needs_you"]],
        ["needs_you", "needs_you"],
    ]
    for bad in bad_lists:
        assert refused_alert_read(app, peer, {"include": bad}), bad
    bad_marks: list[Any] = [5, None, "", "abc", "n1.1", "n1.1.2.3.4", "n1.a.2", "n2.1.2"]
    for bad in [*bad_marks, "n1.1.2.x", "x" * 257]:
        assert refused_alert_read(app, peer, {"include": ["finished"], "mark": bad}), bad
    # Marks from before workflow alerts, and ones that carry the workflow position.
    for good in ("n1.1.2", "n1.1.-", "n1.1.2.3", "n1.1.-.0"):
        read = send("desktop.read", "rem.notices", {"include": ["workflow"], "mark": good})
        assert read["ok"] is True, good
    filtered = {"include": ["finished"], "after": 0, "extra": True}
    assert send("desktop.read", "rem.notices", filtered)["code"] == "invalid"
    assert send("desktop.read", "rem.notices", {"include": ["finished"], "after": "1"})["code"] == (
        "invalid"
    )

    # A phone without the filter sends what it always did and gets what it always got.
    plain = send("desktop.read", "rem.notices")["value"]
    assert set(plain) == {"cursor", "notices", "skipped", "more", "digest"}
    plain = send("desktop.read", "rem.notices", {"after": 0})["value"]
    assert set(plain) == {"cursor", "notices", "skipped", "more", "digest"}
    # A field outside ``params`` is an unknown event field, refused as it always was.
    replies = app.handle(
        peer,
        event(
            "desktop.read",
            900,
            thread_id=None,
            payload={"op": "rem.notices", "params": {}, "include": ALL_ALERTS},
        ),
    )
    refused = [r["payload"] for r in replies if r["type"] == "error"]
    assert len(refused) == 1 and refused[0]["retryable"] is False
    assert not [r for r in replies if r["type"] == "desktop.result"]
    app.desktop.rem = None
    assert send("desktop.read", "overview")["value"]["rem_notices_filter"] is False
    assert send("desktop.read", "overview")["value"]["rem_notices_workflow"] is False


def test_alert_filter_covers_tasks_from_any_source(phone, home):
    app, peer, _, send = alerting_phone(phone, home)
    store = home.store

    def update(identity: str, body: str, *, state: str, needs_user: bool = False) -> None:
        work = {"task_id": f"task-{identity}", "title": "Desk job", "state": state}
        with store.connect() as db:
            store.message(
                db,
                body,
                identity=identity,
                kind="task_update",
                work={**work, "needs_user": needs_user},
            )

    start = send("desktop.read", "rem.notices", {"include": ALL_ALERTS})["value"]
    assert start["notices"] == [] and start["more"] is False and start["mark"]
    # What this phone asked for keeps the old rule: it counts, with no reason.
    request = str(uuid4())
    app.desktop.db.execute(
        "INSERT INTO device_rem_requests(peer, request) VALUES(?,?)", (peer, request)
    )
    with store.connect() as db:
        store.message(db, "Please check", identity=request, role="user")
        store.message(db, "Own answer", identity=f"reply-{request}")
    update(
        "n-1",
        "Waiting on [the notes](</home/me/a b.md>) in /home/me/x/y.txt, see https://example.com/a/b",
        state="blocked",
        needs_user=True,
    )
    update("f-1", "All done.", state="succeeded")
    update("f-2", "It failed.", state="failed")
    update("skip-1", "Cancelled by you.", state="cancelled")
    update("skip-2", "Checking the saved thread.", state="executing")
    after = start["cursor"]
    read = send(
        "desktop.read",
        "rem.notices",
        {"after": after, "include": ALL_ALERTS, "mark": start["mark"]},
    )["value"]
    check_phone_can_read(read)
    assert [(n["id"], n.get("reason")) for n in read["notices"]] == [
        (f"reply-{request}", None),
        ("n-1", "needs_you"),
        ("f-1", "finished"),
        ("f-2", "finished"),
    ]
    assert read["notices"][1]["needs_user"] is True
    assert read["notices"][1]["body"] == (
        "Waiting on the notes in y.txt, see https://example.com/a/b"
    )
    assert "/home" not in json.dumps(read["notices"])

    # Only the switches that are on count, and a task waiting on the user isn't finished.
    only = send("desktop.read", "rem.notices", {"after": after, "include": ["needs_you"]})["value"]
    assert [n["id"] for n in only["notices"]] == [f"reply-{request}", "n-1"]
    only = send("desktop.read", "rem.notices", {"after": after, "include": ["finished"]})["value"]
    assert [n["id"] for n in only["notices"]] == [f"reply-{request}", "f-1", "f-2"]
    # Without a filter, work started elsewhere stays on the desktop as before.
    plain = send("desktop.read", "rem.notices", {"after": after})["value"]
    assert [n["id"] for n in plain["notices"]] == [f"reply-{request}"]
    assert "mark" not in plain and "reason" not in plain["notices"][0]


def test_self_work_updates_in_chat_do_not_alert_a_second_time(phone, home):
    """Self-work's own alerts cover its tickets; its chat updates must not repeat them."""
    app, peer, _, send = alerting_phone(phone, home)
    store = home.store
    start = send("desktop.read", "rem.notices", {"include": ALL_ALERTS})["value"]
    with store.connect() as db:
        for identity, extra in (("self", {"self_work": "ticket"}), ("asked", {})):
            store.message(
                db,
                "Finished REM-1.",
                identity=identity,
                kind="task_update",
                work={"task_id": f"task-{identity}", "title": "Job", "state": "succeeded", **extra},
            )
    read = send(
        "desktop.read",
        "rem.notices",
        {"after": start["cursor"], "include": ALL_ALERTS, "mark": start["mark"]},
    )["value"]
    assert [n["id"] for n in read["notices"]] == ["asked"]


def test_rem_home_carries_the_board_tickets_named_in_chat(phone, home):
    app, peer, _, send = alerting_phone(phone, home)
    with home.store.connect() as db:
        created = new_ticket(db, title="Fix login", status="todo")
        put_ticket(db, created)
        home.store.message(db, f"Started REM-{created['number']}; REM-404 isn't real.")
    tickets = send("desktop.read", "rem.home")["value"]["tickets"]
    key = f"REM-{created['number']}"
    assert list(tickets) == [key]
    assert tickets[key]["title"] == "Fix login" and tickets[key]["ticket_id"] == created["id"]
    assert tickets[key]["status"] == "todo" and tickets[key]["scope"] == "rem"


def test_alert_filter_reports_each_self_work_round_once(phone, home):
    app, peer, _, send = alerting_phone(phone, home)
    include = {"include": ALL_ALERTS}
    research_round(home, "before", "From before the switch")
    first = send("desktop.read", "rem.notices", include)["value"]
    # The first filtered read only sets the position: nothing from before alerts.
    assert first["notices"] == [] and first["mark"].startswith("n1.")
    cursor = first["cursor"]

    def read(mark: str, names: list[str] | None = None) -> dict[str, Any]:
        params = {"after": cursor, "include": names or ALL_ALERTS, "mark": mark}
        answer: dict[str, Any] = send("desktop.read", "rem.notices", params)["value"]
        return answer

    research_round(home, "round-1", "First idea", "Second idea")
    research_round(home, "round-1", "Third idea")
    one = read(first["mark"])
    check_phone_can_read(one)
    assert [(n["id"], n["kind"], n["reason"]) for n in one["notices"]] == [
        ("self-work-round-round-1", "proposals", "proposals")
    ]
    assert one["notices"][0]["body"] == "3 proposals waiting for your approval."
    assert one["notices"][0]["title"] == "Self-work proposals"
    assert "First idea" not in json.dumps(one["notices"])

    # The same round keeps its ID when it adds more; a new round is its own item.
    research_round(home, "round-1", "Fourth idea")
    again = read(one["mark"])
    assert [n["id"] for n in again["notices"]] == ["self-work-round-round-1"]
    assert again["notices"][0]["body"] == "4 proposals waiting for your approval."
    research_round(home, "round-2", "Fifth idea")
    next_round = read(again["mark"])
    assert [n["id"] for n in next_round["notices"]] == ["self-work-round-round-2"]

    # A round nothing is left waiting from doesn't alert.
    research_round(home, "round-3", "Decided idea")
    with home.store.connect() as db:
        tickets = read_tickets(db)
        for ticket in tickets:
            if ticket["title"] == "Decided idea":
                ticket["proposal"]["state"] = "approved"
                put_ticket(db, ticket)
    assert read(next_round["mark"])["notices"] == []

    # The switch being off leaves the round out, but the position still moves past it.
    research_round(home, "round-4", "Unwanted idea")
    filtered = read(next_round["mark"], ["needs_you"])
    assert filtered["notices"] == []
    assert read(filtered["mark"], ALL_ALERTS)["notices"] == []
    # A position past a cleared report starts from now.
    gone = send("desktop.read", "rem.notices", {**include, "after": cursor, "mark": "n1.9999.-"})[
        "value"
    ]
    assert gone["notices"] == [] and gone["mark"] == read(filtered["mark"])["mark"]


def test_alert_filter_reports_self_work_tickets_outside_the_chat(phone, home):
    app, peer, _, send = alerting_phone(phone, home)
    store = home.store
    with store.connect() as db:
        for title, status in (("Stuck", "blocked"), ("Shipped", "done"), ("Moved on", "todo")):
            put_ticket(db, new_ticket(db, title=title, status=status))
        tickets = {t["title"]: t for t in read_tickets(db)}
    start = send("desktop.read", "rem.notices", {"include": ALL_ALERTS})["value"]
    with store.connect() as db:
        add_report(
            db, "work_done", ticket_id=tickets["Stuck"]["id"], title="Stuck", state="blocked"
        )
        add_report(
            db, "work_done", ticket_id=tickets["Shipped"]["id"], title="Shipped", state="done"
        )
        add_report(
            db, "work_done", ticket_id=tickets["Moved on"]["id"], title="Moved on", state="blocked"
        )
        add_report(db, "work_done", ticket_id="", title="Research pass", state="blocked")
        add_report(
            db,
            "work_done",
            ticket_id=tickets["Stuck"]["id"],
            title="Stopped",
            state="blocked",
            stopped=True,
        )
    read = send(
        "desktop.read",
        "rem.notices",
        {"after": start["cursor"], "include": ALL_ALERTS, "mark": start["mark"]},
    )["value"]
    check_phone_can_read(read)
    assert [(n["title"], n["kind"], n["reason"], n["needs_user"]) for n in read["notices"]] == [
        ("Stuck", "organization", "needs_you", True),
        ("Shipped", "organization", "finished", False),
    ]
    assert read["notices"][0]["body"] == "“Stuck” needs you."
    # Reading again from the new position reports nothing twice.
    assert (
        send(
            "desktop.read",
            "rem.notices",
            {"after": start["cursor"], "include": ALL_ALERTS, "mark": read["mark"]},
        )["value"]["notices"]
        == []
    )


def test_alert_filter_reports_organization_tasks(phone, home, tmp_path):
    app, peer, context, send = alerting_phone(phone, home)

    async def stream(**_: Any) -> Any:
        if False:
            yield {}

    manager = OrganizationManager(tmp_path / "orgs", stream=stream, start_runtime=False)
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
                        "name": "Eng",
                        "projectRoots": [root],
                        "employees": [{"id": "lead", "name": "Lead", "title": "CTO"}],
                    }
                },
            },
        )
        oid = org["id"]
        tasks: dict[str, str] = {}
        for title in ("Stuck", "Review me", "Shipped", "Mine", "Waiting", "Heartbeat", "Undone"):
            created = manager.call(
                root,
                "user",
                {
                    "action": "task_create",
                    "organizationId": oid,
                    "params": {"title": title, "assignee": "lead"},
                },
            )
            tasks[title] = created["id"]
        start = send("desktop.read", "rem.notices", {"include": ALL_ALERTS})["value"]

        def change(actor: str, title: str, status: str, **extra: Any) -> None:
            def apply(current: dict[str, Any]) -> dict[str, Any]:
                task = next(t for t in current["runtime"]["tasks"] if t["id"] == tasks[title])
                task.update(status=status, **extra)
                return {}

            manager.store.mutate(root, oid, actor, "state_updated", apply)

        employee = "employee:lead"
        change(employee, "Stuck", "blocked", blockReason="Needs a decision")
        change(employee, "Review me", "in_review")
        change(employee, "Shipped", "done")
        # None of these is something to tell the user about.
        change("user", "Mine", "done")
        change(employee, "Waiting", "blocked", blockReason="dependencies")
        change(employee, "Heartbeat", "done", heartbeat=True)
        change(employee, "Undone", "in_review")
        change(employee, "Undone", "todo")

        def ask(current: dict[str, Any]) -> dict[str, Any]:
            current["runtime"].setdefault("consentRequests", {})["req"] = {
                "id": "req",
                "status": "pending",
            }
            return {"requestId": "req", "action": "control", "summary": "Start it"}

        manager.store.mutate(root, oid, "Rem", "consent_requested", ask)

        params = {"after": start["cursor"], "include": ALL_ALERTS, "mark": start["mark"]}
        read = send("desktop.read", "rem.notices", params)["value"]
        check_phone_can_read(read)
        assert [(n["title"], n["reason"], n["kind"]) for n in read["notices"]] == [
            ("Stuck", "needs_you", "organization"),
            ("Review me", "needs_you", "organization"),
            ("Shipped", "finished", "organization"),
            ("Eng", "needs_you", "organization"),
        ]
        assert read["notices"][0]["body"] == "Eng: “Stuck” needs you."
        assert read["notices"][3]["body"] == "Eng: A change needs you."
        assert read["notices"][0]["needs_user"] is True
        assert read["more"] is False
        # IDs are the same if the phone names the same place twice.
        again = send("desktop.read", "rem.notices", params)["value"]
        assert [n["id"] for n in again["notices"]] == [n["id"] for n in read["notices"]]

        # A newly shared item starts from the saved mark; nothing from before repeats.
        following = send(
            "desktop.read",
            "rem.notices",
            {"after": start["cursor"], "include": ["needs_you"], "mark": read["mark"]},
        )["value"]
        assert following["notices"] == [] and following["mark"] == read["mark"]
        change(employee, "Stuck", "done")
        turned_on = send(
            "desktop.read",
            "rem.notices",
            {"after": start["cursor"], "include": ["finished"], "mark": following["mark"]},
        )["value"]
        assert [n["title"] for n in turned_on["notices"]] == ["Stuck"]

        # Organizations turned off report nothing, and the mark still works.
        manager.enabled = False
        off = send(
            "desktop.read",
            "rem.notices",
            {"after": start["cursor"], "include": ALL_ALERTS, "mark": turned_on["mark"]},
        )["value"]
        assert off["notices"] == [] and off["mark"].startswith("n1.")
    finally:
        manager.close()
        app.desktop.organizations = None


def test_alert_filter_without_organizations_keeps_the_mark_readable(phone, home):
    _, _, _, send = alerting_phone(phone, home)
    first = send("desktop.read", "rem.notices", {"include": ["finished"]})["value"]
    # No organizations here: "-", then the workflow alert position.
    assert re.fullmatch(r"n1\.\d+\.-\.\d+", first["mark"])
    read = send(
        "desktop.read",
        "rem.notices",
        {"after": first["cursor"], "include": ["finished"], "mark": first["mark"]},
    )["value"]
    assert read["notices"] == [] and read["mark"] == first["mark"]


def test_alert_filter_caps_a_burst_at_eight_newest_first(phone, home):
    _, _, _, send = alerting_phone(phone, home)
    store = home.store
    start = send("desktop.read", "rem.notices", {"include": ALL_ALERTS})["value"]
    with store.connect() as db:
        for index in range(11):
            store.message(
                db,
                f"Done {index}",
                identity=f"burst-{index}",
                kind="task_update",
                work={"task_id": "t", "title": "T", "state": "succeeded"},
            )
    read = send(
        "desktop.read",
        "rem.notices",
        {"after": start["cursor"], "include": ALL_ALERTS, "mark": start["mark"]},
    )["value"]
    assert [n["id"] for n in read["notices"]] == [f"burst-{i}" for i in range(3, 11)]
    assert read["skipped"] == 3


def test_phone_voice_tidy_answers_pending_then_the_tidied_words(phone, monkeypatch, tmp_path):
    from gofer.core import provider_preferences
    from gofer.ui import voice_tidy

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    monkeypatch.setattr(provider_preferences, "get_data_dir", lambda: tmp_path)
    assert send("desktop.read", "overview")["value"]["voice_tidy"] is False
    # With tidying off the heard words come straight back.
    off = send("desktop.action", "rem.tidy", {"text": "hello there"})["value"]
    assert (off["text"], off["tidied"]) == ("hello there", False)
    assert send("desktop.action", "rem.tidy", {"text": " "})["code"] == "invalid"
    assert send("desktop.action", "rem.tidy", {"text": "x" * 4001})["code"] == "invalid"

    provider_preferences.save_voice_tidy_preference({"enabled": True})
    assert send("desktop.read", "overview")["value"]["voice_tidy"] is True
    assert send("desktop.read", "rem.home")["value"]["voice_tidy"] is True
    chosen = {"enabled": True, "available": True, "context": True, "label": "Claude Haiku 5.5"}
    monkeypatch.setattr(voice_tidy, "current", lambda default, rem: chosen)
    seen: dict[str, Any] = {}
    release = threading.Event()
    finished = threading.Event()

    def tidy(heard, selected, data_dir, *, context=""):
        release.wait(5)
        seen.update(heard=heard, context=context)
        return {"text": "Hello there.", "tidied": True, "reason": "", "model": "Haiku", "ms": 1}

    monkeypatch.setattr(voice_tidy, "tidy", tidy)
    original = app.desktop.tidy_finished

    def tidy_finished(*args: Any) -> None:
        original(*args)
        finished.set()

    monkeypatch.setattr(app.desktop, "tidy_finished", tidy_finished)
    request = event(
        "desktop.action",
        900,
        thread_id=None,
        payload={"op": "rem.tidy", "params": {"text": "hello there"}},
    )
    first = [r for r in app.handle(peer, request) if r["type"] == "desktop.result"]
    # The phone's queue entry clears at once; the slow model call isn't on the lock.
    assert json.loads(first[0]["payload"]["body"])["pending"] is True
    release.set()
    assert finished.wait(5)
    later = [
        e
        for e in app.pending(peer, first[-1]["sequence"])
        if e["type"] == "desktop.result" and e["request_id"] == request["request_id"]
    ]
    body = json.loads("".join(e["payload"]["body"] for e in later))
    assert (body["text"], body["tidied"]) == ("Hello there.", True)
    assert later[0]["payload"]["op"] == "rem.tidy" and later[0]["thread_id"] is None
    assert seen["heard"] == "hello there" and "Check builds" in seen["context"]
    # A resent request is answered from the record and isn't tidied twice.
    seen.clear()
    app.handle(peer, request)
    assert seen == {}


# Wake the computer ---------------------------------------------------------------

WAKE_HOST = "192.168.1.20"
WAKE_MAC = "aa:bb:cc:dd:ee:01"


@pytest.fixture
def waking(phone):
    """A phone using Raticode, on a desktop whose card is a fake one."""
    app, peer, _, send = phone
    app.lan_endpoint = {"host": WAKE_HOST, "port": 44233}
    app.desktop.hardware_address = lambda host: WAKE_MAC if host == WAKE_HOST else None
    app.desktop.enable(peer, True)
    return app, peer, send


def overview(send: Any) -> dict[str, Any]:
    answer = send("desktop.read", "overview")
    assert answer["ok"], answer
    value: dict[str, Any] = answer["value"]
    return value


def pair_phone(app: Any, peer: str | None = None) -> str:
    identity = peer or str(uuid4())
    pin = secrets.token_bytes(32)
    app.registry.propose(identity, pin, "controller", app.registry.clock() + 300)
    app.registry.confirm(identity, pin.hex())
    app.registry.acknowledge(
        identity,
        pin,
        "controller",
        {"type": "session.ready_ack", "device_id": identity, "trust_revision": 1},
    )
    return identity


def read_overview_as(app: Any, peer: str, sequence: int) -> dict[str, Any]:
    replies = app.handle(
        peer,
        event("desktop.read", sequence, thread_id=None, payload={"op": "overview", "params": {}}),
    )
    result = next(r for r in replies if r["type"] == "desktop.result")["payload"]
    assert result["ok"], result
    value: dict[str, Any] = json.loads(result["body"])
    return value


def test_wake_address_is_off_by_default(waking):
    _, _, send = waking
    assert "wake_mac" not in overview(send)


def test_wake_address_follows_the_setting_and_is_gone_when_off(waking):
    app, peer, send = waking
    app.desktop.wake.enable(peer, True)
    answer = overview(send)
    assert answer["wake_mac"] == WAKE_MAC
    assert re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", answer["wake_mac"])
    assert len(answer["wake_mac"]) == 17
    app.desktop.wake.enable(peer, False)
    assert "wake_mac" not in overview(send)


def test_wake_address_is_read_fresh_for_each_answer(waking):
    app, peer, send = waking
    app.desktop.wake.enable(peer, True)
    assert overview(send)["wake_mac"] == WAKE_MAC
    app.desktop.hardware_address = lambda _host: "aa:bb:cc:dd:ee:02"
    assert overview(send)["wake_mac"] == "aa:bb:cc:dd:ee:02"


def test_wake_setting_is_per_phone(waking):
    app, peer, send = waking
    other = pair_phone(app)
    app.workspace.enable(other, True)
    app.desktop.enable(other, True)
    app.desktop.wake.enable(peer, True)
    assert overview(send)["wake_mac"] == WAKE_MAC
    assert "wake_mac" not in read_overview_as(app, other, 10)
    assert app.desktop.wake.enabled() == [peer]


def test_wake_needs_desktop_control(waking):
    app, peer, send = waking
    app.desktop.enable(peer, False)
    app.desktop.wake.enable(peer, True)
    # Enabling has no effect: the phone can't read overview at all.
    assert app.desktop.wake.allowed(peer) is False
    refused = send("desktop.read", "overview")
    assert refused["code"] == "unauthorized" and WAKE_MAC not in json.dumps(refused)
    # Turning desktop control off also stops the field, as does turning thread sync off.
    app.desktop.enable(peer, True)
    assert overview(send)["wake_mac"] == WAKE_MAC
    app.desktop.enable(peer, False)
    assert app.desktop.wake.allowed(peer) is False
    assert send("desktop.read", "overview")["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    app.workspace.enable(peer, False)
    assert app.desktop.wake.allowed(peer) is False


def test_unpairing_forgets_the_wake_setting(waking):
    app, peer, _ = waking
    app.desktop.wake.enable(peer, True)
    app.remove_peer(peer)
    assert app.desktop.wake.enabled() == []
    pair_phone(app, peer)
    app.workspace.enable(peer, True)
    app.desktop.enable(peer, True)
    assert app.desktop.wake.allowed(peer) is False
    assert "wake_mac" not in read_overview_as(app, peer, 500)


def test_wake_setting_refuses_a_desktop(waking):
    app, _, _ = waking
    computer, pin = str(uuid4()), secrets.token_bytes(32)
    app.registry.propose(computer, pin, "desktop", app.registry.clock() + 300)
    app.registry.confirm(computer, pin.hex())
    app.registry.acknowledge(
        computer,
        pin,
        "desktop",
        {"type": "session.ready_ack", "device_id": computer, "trust_revision": 1},
    )
    with pytest.raises(PairingError, match="phone_required"):
        app.desktop.wake.enable(computer, True)
    assert app.desktop.wake.enabled() == []


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        {"host": "8.8.8.8", "port": 44233},
        {"host": "100.101.1.2", "port": 44233},
        {"host": "fd7a:115c:a1e0::1", "port": 44233},
        {"host": "127.0.0.1", "port": 44233},
        {"host": "169.254.10.2", "port": 44233},
        {"host": "fd00::20", "port": 44233},
    ],
    ids=["no-endpoint", "public", "tailscale", "tailscale-v6", "loopback", "link-local", "ipv6"],
)
def test_wake_address_is_left_out_without_a_private_lan_endpoint(waking, endpoint):
    app, peer, send = waking
    app.desktop.wake.enable(peer, True)
    app.lan_endpoint = endpoint
    # Would answer for any host, so only the endpoint rule can keep the key out.
    app.desktop.hardware_address = lambda _host: WAKE_MAC
    answer = overview(send)
    assert "wake_mac" not in answer and answer["media"] is True


def _reader(result: object) -> Any:
    def read(_host: str) -> object:
        if isinstance(result, Exception):
            raise result
        return result

    return read


@pytest.mark.parametrize(
    "result",
    [
        None,
        OSError(f"can't read {WAKE_MAC}"),
        ValueError(f"bad {WAKE_MAC}"),
        "",
        "00:00:00:00:00:00",
        "ff:ff:ff:ff:ff:ff",
        "01:00:5e:00:00:01",
        "aa-bb-cc-dd-ee-01",
        "aa:bb:cc:dd:ee",
    ],
    ids=[
        "none",
        "oserror",
        "other-error",
        "empty",
        "zero",
        "broadcast",
        "multicast",
        "dash",
        "short",
    ],
)
def test_wake_address_is_left_out_when_unreadable_or_unusable(waking, result, caplog):
    app, peer, send = waking
    app.desktop.wake.enable(peer, True)
    app.desktop.hardware_address = _reader(result)
    caplog.set_level(logging.DEBUG)
    answer = overview(send)
    assert "wake_mac" not in answer and answer["media"] is True
    assert WAKE_MAC not in caplog.text


class Interfaces:
    """Faked interface table and /sys/class/net, for the real address reader."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, sys_net: Path) -> None:
        self.table: dict[str, str | None | OSError] = {}
        self.sys_net = sys_net
        sys_net.mkdir()
        monkeypatch.setattr(wake, "SYS_NET", sys_net)
        monkeypatch.setattr(wake, "supported", lambda: True)
        monkeypatch.setattr(
            wake.socket, "if_nameindex", lambda: list(enumerate(self.table, start=1))
        )
        monkeypatch.setattr(wake, "_interface_address", self.read)

    def read(self, name: str) -> str | None:
        value = self.table[name]
        if isinstance(value, OSError):
            raise value
        return value

    def add(
        self,
        name: str,
        host: str | None | OSError,
        address: str = WAKE_MAC,
        *,
        physical: bool = True,
    ) -> None:
        self.table[name] = host
        folder = self.sys_net / name
        folder.mkdir()
        (folder / "address").write_text(address + "\n")
        if physical:
            (folder / "device").mkdir()


@pytest.fixture
def interfaces(monkeypatch, tmp_path):
    return Interfaces(monkeypatch, tmp_path / "net")


def test_card_holding_the_address_wins_over_earlier_cards(interfaces):
    interfaces.add("eno1", "10.0.0.5", "aa:bb:cc:dd:ee:02")
    interfaces.add("lo", "127.0.0.1", "00:00:00:00:00:00", physical=False)
    interfaces.add("wlan0", None, "aa:bb:cc:dd:ee:03")
    interfaces.add("enp3s0", WAKE_HOST)
    assert wake.hardware_address(WAKE_HOST) == WAKE_MAC


def test_interface_without_a_device_link_is_refused(interfaces):
    interfaces.add("br0", WAKE_HOST, physical=False)
    assert wake.hardware_address(WAKE_HOST) is None


def test_virtual_interface_sharing_the_address_does_not_hide_the_card(interfaces):
    interfaces.add("veth0", WAKE_HOST, "aa:bb:cc:dd:ee:02", physical=False)
    interfaces.add("eno1", WAKE_HOST)
    assert wake.hardware_address(WAKE_HOST) == WAKE_MAC


def test_two_cards_holding_the_address_are_refused(interfaces):
    interfaces.add("eno1", WAKE_HOST)
    interfaces.add("eno2", WAKE_HOST, "aa:bb:cc:dd:ee:02")
    assert wake.hardware_address(WAKE_HOST) is None


def test_no_interface_holding_the_address_is_refused(interfaces):
    interfaces.add("eno1", "192.168.1.21")
    assert wake.hardware_address(WAKE_HOST) is None


def test_uppercase_address_with_whitespace_is_lowercased(interfaces):
    interfaces.add("eno1", WAKE_HOST, "  AA:BB:CC:DD:EE:01")
    assert wake.hardware_address(WAKE_HOST) == WAKE_MAC


@pytest.mark.parametrize(
    "address",
    ["00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01", "ab:bb:cc:dd:ee:01", "nope"],
)
def test_unusable_card_address_is_refused(interfaces, address):
    interfaces.add("eno1", WAKE_HOST, address)
    assert wake.hardware_address(WAKE_HOST) is None


def test_failed_interface_read_leaves_the_address_out(interfaces):
    interfaces.add("eno1", WAKE_HOST)
    interfaces.add("eno2", OSError(errno.EPERM, "denied"))
    assert wake.hardware_address(WAKE_HOST) is None


def test_missing_address_file_leaves_the_address_out(interfaces):
    interfaces.add("eno1", WAKE_HOST)
    (interfaces.sys_net / "eno1" / "address").unlink()
    assert wake.hardware_address(WAKE_HOST) is None


def test_unlisted_interfaces_leave_the_address_out(interfaces, monkeypatch):
    def fail() -> list[tuple[int, str]]:
        raise OSError(errno.EPERM, "denied")

    monkeypatch.setattr(wake.socket, "if_nameindex", fail)
    assert wake.hardware_address(WAKE_HOST) is None


def test_other_systems_report_no_address(interfaces, monkeypatch):
    interfaces.add("eno1", WAKE_HOST)
    monkeypatch.setattr(wake, "supported", lambda: False)
    assert wake.hardware_address(WAKE_HOST) is None


def test_interface_address_comes_from_the_kernel_answer(monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    seen: list[tuple[int, bytes]] = []

    def ioctl(_fd: int, request: int, argument: bytes) -> bytes:
        seen.append((request, argument))
        return argument[:20] + socket.inet_aton(WAKE_HOST) + bytes(232)

    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    assert wake._interface_address("enp3s0") == WAKE_HOST
    assert seen[0][0] == wake.SIOCGIFADDR and seen[0][1].rstrip(b"\0") == b"enp3s0"


@pytest.mark.parametrize("code", [errno.EADDRNOTAVAIL, errno.ENODEV])
def test_interface_without_an_address_holds_nothing(monkeypatch, code):
    fcntl = pytest.importorskip("fcntl")

    def ioctl(*_args: Any) -> bytes:
        raise OSError(code, "no address")

    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    assert wake._interface_address("wlan0") is None


def test_interface_read_failures_are_not_mistaken_for_no_address(monkeypatch):
    fcntl = pytest.importorskip("fcntl")

    def ioctl(*_args: Any) -> bytes:
        raise OSError(errno.EPERM, "denied")

    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    with pytest.raises(OSError, match="denied"):
        wake._interface_address("eno1")


def test_wake_address_is_never_logged(waking, caplog):
    app, peer, send = waking
    caplog.set_level(logging.DEBUG)
    app.desktop.wake.enable(peer, True)
    assert overview(send)["wake_mac"] == WAKE_MAC
    app.desktop.wake.enable(peer, False)
    overview(send)
    assert WAKE_MAC not in caplog.text and WAKE_MAC.upper() not in caplog.text
    # Only that the setting changed, with the device ID.
    changes = [r.getMessage() for r in caplog.records if "Wake this computer" in r.getMessage()]
    assert changes == [f"Wake this computer turned on for {peer}"] + [
        f"Wake this computer turned off for {peer}"
    ]


def _files_holding(root: Path, needle: bytes) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and needle in p.read_bytes()]


def test_wake_address_is_never_on_disk_in_readable_form(waking, tmp_path):
    app, peer, send = waking
    app.desktop.wake.enable(peer, True)
    assert overview(send)["wake_mac"] == WAKE_MAC
    app.registry.db.commit()
    assert _files_holding(tmp_path, WAKE_MAC.encode()) == []


def test_answer_holding_the_wake_address_is_deleted_once_acknowledged(waking):
    app, peer, send = waking
    app.desktop.wake.enable(peer, True)
    db = app.registry.db
    overview(send)
    sequence = db.execute(
        "SELECT MAX(sequence) FROM device_outbox WHERE peer=?", (peer,)
    ).fetchone()[0]
    assert WAKE_MAC in json.dumps(app.pending(peer, sequence - 1, 1)[0]["payload"])

    # Not acknowledged yet: it has to stay until the phone has it.
    app.handle(
        peer,
        event(
            "sync.request",
            300,
            thread_id=None,
            payload={"after_sequence": sequence - 1, "limit": 1, "include_connection": False},
        ),
    )
    assert db.execute(
        "SELECT 1 FROM device_outbox WHERE peer=? AND sequence=?", (peer, sequence)
    ).fetchone()

    app.handle(
        peer,
        event(
            "sync.request",
            301,
            thread_id=None,
            payload={"after_sequence": sequence, "limit": 1, "include_connection": False},
        ),
    )
    assert not db.execute(
        "SELECT 1 FROM device_outbox WHERE peer=? AND sequence=?", (peer, sequence)
    ).fetchone()
    assert not db.execute("SELECT 1 FROM device_wake_answers WHERE peer=?", (peer,)).fetchone()


@pytest.fixture
def settings_api(tmp_path, monkeypatch):
    for name in (
        "RATICODE_DEVICE_EXPERIMENTAL_NETWORK",
        "RATICODE_DEVICE_HOST",
        "RATICODE_DEVICE_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    control = DeviceControl(tmp_path, store=TestSecrets())
    control.action({"action": "enable"})
    yield control
    control.close()


def test_wake_computer_action_reports_device_ids_only(settings_api):
    control = settings_api
    peer = pair_phone(control.application)
    control.application.desktop.hardware_address = lambda _host: WAKE_MAC
    assert control.status()["wake_computer_peers"] == []
    changed = control.action({"action": "wake_computer", "device_id": peer, "enabled": True})
    assert changed["wake_computer_peers"] == [peer]
    assert WAKE_MAC not in json.dumps(changed, default=str)
    off = control.action({"action": "wake_computer", "device_id": peer, "enabled": False})
    assert off["wake_computer_peers"] == []


@pytest.mark.parametrize("choice", [None, 1, "true", [], {}])
def test_wake_computer_action_rejects_a_non_boolean(settings_api, choice):
    control = settings_api
    peer = pair_phone(control.application)
    with pytest.raises(PairingError, match="invalid_wake_computer_choice"):
        control.action({"action": "wake_computer", "device_id": peer, "enabled": choice})
    assert control.status()["wake_computer_peers"] == []


def test_status_says_whether_this_system_can_wake(settings_api, monkeypatch):
    from gofer.devices import control as control_module

    monkeypatch.setattr(control_module, "wake_supported", lambda: False)
    assert settings_api.status()["wake_computer_available"] is False
    monkeypatch.setattr(control_module, "wake_supported", lambda: True)
    assert settings_api.status()["wake_computer_available"] is True


def test_a_task_waiting_for_others_shows_why_on_the_phone(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    rem.store.task.update(
        active_run=None,
        last_run=None,
        recent_runs=[],
        dependency={
            "state": "held",
            "reason": "On hold: “Audit” was blocked. Retry it, or start this task now.",
            "prerequisites": [
                {
                    "task_id": "audit",
                    "title": "Audit",
                    "state": "held",
                    "reason": "“Audit” was blocked",
                    "run_id": "audit-run",
                    "run_state": "blocked",
                    "retryable": True,
                    "thread_id": "private-thread",
                    "since": "2026-11-10T00:00:00+00:00",
                },
                "not a prerequisite",
            ],
        },
    )
    home = send("desktop.read", "rem.home")["value"]
    shown = home["tasks"][0]["dependency"]
    assert shown["state"] == "held" and shown["reason"].startswith("On hold:")
    assert shown["prerequisites"] == [
        {
            "task_id": "audit",
            "title": "Audit",
            "state": "held",
            "reason": "“Audit” was blocked",
            "run_id": "audit-run",
            "run_state": "blocked",
            # An older desktop sends no kind: it is a named task.
            "kind": "task",
            "retryable": True,
        }
    ]
    task = send("desktop.read", "rem.task", {"task_id": rem.store.task["id"]})["value"]
    assert task["dependency"] == shown
    # A task that waits for nothing carries no dependency at all, as from older desktops.
    rem.store.task.pop("dependency")
    assert "dependency" not in send("desktop.read", "rem.home")["value"]["tasks"][0]


def test_a_queued_request_names_what_it_waited_for_on_the_phone(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    rem.store.messages.append(
        {
            "id": "after-run",
            "role": "user",
            "source": "after",
            "body": "Start up to 4 threads to fix the findings.",
            "created_at": "t9",
            "queued_after": [
                {"task_id": "audit", "title": "CLI audit", "summary": "6 gaps", "reports": ["/x"]},
                "junk",
            ],
        }
    )
    shown = next(
        m for m in send("desktop.read", "rem.home")["value"]["messages"] if m["id"] == "after-run"
    )
    assert shown["queued_after"] == ["CLI audit"]
    assert "/x" not in json.dumps(shown)


def test_workflow_notices_reach_the_phone_under_their_own_switch(phone, home):
    from gofer.core.phone_alerts import PhoneAlertStore

    app, peer, _, send = alerting_phone(phone, home)
    alerts = PhoneAlertStore(Path(home.data_dir))
    alerts.add(title="Before", body="From before the switch", workflow_id="wf", run_id="r0")
    first = send("desktop.read", "rem.notices", {"include": ["workflow"]})["value"]
    # The first read only sets the position: notices from before never alert.
    assert first["notices"] == [] and first["mark"].endswith(".1")

    alerts.add(
        title="Nightly report",
        body="Ready: see /home/me/reports/out.md",
        workflow_id="wf-nightly",
        workflow_name="Nightly",
        run_id="run-7",
    )
    params = {"after": first["cursor"], "include": ["workflow"], "mark": first["mark"]}
    read = send("desktop.read", "rem.notices", params)["value"]
    check_phone_can_read(read)
    assert [
        (n["kind"], n["reason"], n["title"], n["workflow_id"], n["run_id"], n["needs_user"])
        for n in read["notices"]
    ] == [("workflow", "workflow", "Nightly report", "wf-nightly", "run-7", False)]
    # Local paths stay on the computer, as in every alert about work the phone didn't ask for.
    assert read["notices"][0]["body"] == "Ready: see out.md"
    assert read["notices"][0]["workflow_name"] == "Nightly"
    assert read["mark"].endswith(".2")

    # Without the switch, the position still moves and the notice stays on the computer.
    alerts.add(title="Quiet", body="", workflow_id="wf", run_id="r2")
    other = send(
        "desktop.read",
        "rem.notices",
        {"after": first["cursor"], "include": ["finished"], "mark": read["mark"]},
    )["value"]
    assert other["notices"] == [] and other["mark"].endswith(".3")

    # A mark from before workflow alerts reads from now instead of sending history.
    legacy = send(
        "desktop.read",
        "rem.notices",
        {"after": first["cursor"], "include": ["workflow"], "mark": "n1.0.-"},
    )["value"]
    assert legacy["notices"] == [] and legacy["mark"].endswith(".3")


def test_a_waiting_question_reaches_the_phone_under_needs_you(phone, home):
    from gofer.core.phone_alerts import PhoneAlertStore

    app, peer, _, send = alerting_phone(phone, home)
    first = send("desktop.read", "rem.notices", {"include": ["needs_you"]})["value"]
    alerts = PhoneAlertStore(Path(home.data_dir))
    assert alerts.add_decision(
        decision_id="D-7",
        question="Publish to /home/me/site/out or keep it local?",
        project_name="site",
        recommended="Keep it local",
    )
    params = {"after": first["cursor"], "include": ["needs_you"], "mark": first["mark"]}
    read = send("desktop.read", "rem.notices", params)["value"]
    check_phone_can_read(read)
    [notice] = read["notices"]
    assert (notice["kind"], notice["reason"], notice["needs_user"]) == (
        "decision",
        "needs_you",
        True,
    )
    assert notice["id"] == "decision-D-7" and notice["project_name"] == "site"
    # The same fields as the protocol's fixture, which the phone's own tests parse.
    fixture = Path(__file__).parents[2] / (
        "src/gofer/devices/protocol/v2/fixtures/desktop.result.rem-notices-decision.json"
    )
    [shown] = json.loads(json.loads(fixture.read_text())["payload"]["body"])["notices"]
    assert notice.keys() == shown.keys() and notice["decision"].keys() == shown["decision"].keys()
    # Local paths stay on the computer, as in every alert about work the phone didn't ask for.
    assert notice["body"] == "Rem has a question: Publish to out or keep it local?"
    assert notice["decision"] == {
        "id": "D-7",
        "question": "Publish to out or keep it local?",
        "recommended": "Keep it local",
        "fallback": False,
    }
    # One notice per decision, however often it is filed again.
    assert (
        alerts.add_decision(decision_id="D-7", question="Again?", project_name="", recommended="")
        is None
    )
    # Without the switch the position moves on and the question stays on the computer.
    assert alerts.add_decision(
        decision_id="D-8", question="Again?", project_name="", recommended=""
    )
    other = send(
        "desktop.read",
        "rem.notices",
        {"after": first["cursor"], "include": ["finished"], "mark": read["mark"]},
    )["value"]
    assert other["notices"] == []


def test_workflow_notices_fold_per_run_and_report_what_was_dropped(phone, home, monkeypatch):
    from gofer.core import phone_alerts

    monkeypatch.setattr(phone_alerts, "RUN_CAP", 2)
    monkeypatch.setattr(phone_alerts, "KEEP", 4)
    app, peer, _, send = alerting_phone(phone, home)
    alerts = phone_alerts.PhoneAlertStore(Path(home.data_dir))
    alerts.add(title="Before", body="", workflow_id="wf", run_id="r0")
    first = send("desktop.read", "rem.notices", {"include": ["workflow"]})["value"]
    assert first["dropped"] == 0

    # Six notices from one run: two kept, the rest folded into one moving notice.
    for index in range(6):
        alerts.add(
            title=f"Step {index}",
            body="",
            workflow_id="wf-nightly",
            workflow_name="Nightly backup",
            run_id="run-9",
        )
    params = {"after": first["cursor"], "include": ["workflow"], "mark": first["mark"]}
    read = send("desktop.read", "rem.notices", params)["value"]
    check_phone_can_read(read)
    assert [(n["title"], n["more_from_run"]) for n in read["notices"]] == [
        ("Step 0", 0),
        ("Step 1", 0),
        ("Nightly backup", 4),
    ]
    assert read["notices"][-1]["body"] == "And 4 more from this run."
    assert read["dropped"] == 0

    # The phone is away while more notices than the store keeps arrive.
    for index in range(6):
        alerts.add(title=f"Other {index}", body="", workflow_id="wf-other", run_id=f"o{index}")
    away = {"after": read["cursor"], "include": ["workflow"], "mark": read["mark"]}
    late = send("desktop.read", "rem.notices", away)["value"]
    assert [n["title"] for n in late["notices"]] == ["Other 2", "Other 3", "Other 4", "Other 5"]
    assert late["dropped"] == 2
    # Without the workflow switch the position moves but no gap is reported.
    other = send("desktop.read", "rem.notices", {**away, "include": ["finished"]})["value"]
    assert other["dropped"] == 0

    # The answer has the fields the protocol fixture shows, which the phone's parser reads.
    fixture = json.loads(
        (
            Path(module.__file__).parent
            / "protocol/v2/fixtures/desktop.result.rem-notices-workflow.json"
        ).read_text()
    )
    shown = json.loads(fixture["payload"]["body"])
    # digest is on every read answer, added by the desktop for change checks.
    assert set(shown) == set(late) - {"digest"}
    assert {key for n in shown["notices"] for key in n} == set(late["notices"][0])


def test_phone_rem_reads_skip_building_tasks_while_nothing_changed(phone, home, monkeypatch):
    from tests.unit.test_rem_home import recipe

    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    daily = {"kind": "daily", "local_time": "09:00", "timezone": "UTC"}
    ids = [
        home.create_task(
            {
                "request_id": f"phone-list-{n}",
                **recipe(
                    type="reminder", title=f"Remind {n}", prompt=f"Remind me {n}", schedule=daily
                ),
            }
        )["task"]["id"]
        for n in range(3)
    ]
    built: list[str] = []
    summarized: list[str] = []
    view, summary = home.store.task_view, app.desktop._task_summary

    def counted_view(db: Any, task: dict[str, Any], *, summary: bool = False) -> dict[str, Any]:
        built.append(task["id"])
        shown: dict[str, Any] = view(db, task, summary=summary)
        return shown

    def counted_summary(peer: str, task: dict[str, Any], catalog: Any = None) -> dict[str, Any]:
        summarized.append(task["id"])
        shown: dict[str, Any] = summary(peer, task, catalog)
        return shown

    monkeypatch.setattr(home.store, "task_view", counted_view)
    monkeypatch.setattr(app.desktop, "_task_summary", counted_summary)

    first = send("desktop.read", "rem.home")["value"]
    assert sorted(t["task_id"] for t in first["tasks"]) == sorted(ids)
    assert not any("prompt" in t for t in first["tasks"])
    assert (len(built), len(summarized)) == (3, 3)
    # The same build answers the next polls: no task view and no phone summary again.
    assert send("desktop.read", "rem.home", {"known": first["digest"]})["value"] == {
        "unchanged": True,
        "digest": first["digest"],
    }
    assert send("desktop.read", "rem.home")["value"]["tasks"] == first["tasks"]
    assert (len(built), len(summarized)) == (3, 3)

    # rem.task reads only the task the phone opened, with its prompt, in the same reply.
    opened = send("desktop.read", "rem.task", {"task_id": ids[1]})["value"]
    assert opened["task_id"] == ids[1] and opened["prompt"] == "Remind me 1"
    assert built[3:] == [ids[1]] and summarized[3:] == [ids[1]]
    assert send("desktop.read", "rem.task", {"task_id": "gone"})["code"] == "not_found"

    # A write to a task is counted, so the next poll builds the list again.
    with home.store.connect() as db:
        task = home.store.get(db, "tasks", ids[0])
        home.store.put(db, "tasks", {**task, "title": "Remind sooner"})
    again = send("desktop.read", "rem.home")["value"]
    assert "Remind sooner" in {t["title"] for t in again["tasks"]}
    assert len(built) == 4 + 3 and len(summarized) == 4 + 3


def test_plan_questions_reach_the_phone_grouped_by_plan_without_folders(phone):
    app, _, _, _ = phone
    item = {
        "id": "D-12",
        "topic": "plan:store",
        "question": "Where do offline notes live?",
        "recommended": "SQLite",
        "status": "pending",
        "project": "/home/me/Notes",
        "project_name": "Notes",
        "plan": "P-1",
    }
    shown = app.desktop._rem_decisions(
        {
            "decisions": {
                "revision": 4,
                "items": [item, {**item, "id": "D-13", "plan": "P-1" * 20}],
                "plans": [
                    {
                        "id": "P-1",
                        "title": "Offline sync",
                        "goal": "g" * 400,
                        "project": "/home/me/Notes",
                        "status": "asking",
                        "answered": 2,
                        "total": 5,
                        "waiting": 1,
                        "queued": 2,
                    },
                    {"id": "P-2", "title": "Old", "status": "weird", "total": -3},
                    {"title": "No ID"},
                ],
            }
        }
    )
    assert shown["decisions"][0]["plan"] == "P-1"
    assert len(shown["decisions"][1]["plan"]) == 16
    first, second = shown["plans"]
    assert first == {
        "id": "P-1",
        "title": "Offline sync",
        "goal": "g" * 300,
        "project": "Notes",
        "status": "asking",
        "answered": 2,
        "total": 5,
        "waiting": 1,
        "queued": 2,
    }
    assert second["status"] == "asking" and second["total"] == 0
    assert "/home/me" not in json.dumps(shown)


def test_phone_decisions_carry_who_asked_without_local_paths():
    asked = {
        "by": "run",
        "label": "REM-1",
        "ticket": "REM-1",
        "task_id": "task-1",
        "task_title": "Fix /home/user/app/src/main.py",
        "at": "2026-11-21T12:00:00+00:00",
        "waiting": [{"run_id": "run-1", "task_id": "task-1", "ticket": "REM-1", "label": "REM-1"}],
        "went_on": [],
        "opens": "task-1",
    }
    item = {"id": "D-1", "topic": "t", "question": "Which?", "status": "pending", "asked": asked}
    sent = module._decision_item(item)["asked"]
    assert sent["label"] == "REM-1" and sent["opens"] == "task-1"
    assert sent["waiting"] == asked["waiting"] and sent["went_on"] == []
    assert "/home/user" not in sent["task_title"]
    assert module._decision_item({**item, "status": "confirmed", "asked": None})["asked"] is None


def test_phone_decisions_say_which_commit_added_a_record_read_from_the_repository():
    item = {
        "id": "D-2",
        "topic": "adr:tabs",
        "question": "Indentation?",
        "status": "confirmed",
        "decided_by": "user",
        "source": {"via": "repository", "commit": "1a2b3c4d5e6f", "author": "Other Person"},
        "history": [{"at": "2026-11-24T10:00:00+00:00", "actor": "raticode", "action": "read"}],
    }
    sent = module._decision_item(item)
    assert sent["read_from"] == {"commit": "1a2b3c4d5e6f", "author": "Other Person"}
    assert sent["decided_by"] == "user"
    # Once the user confirms or changes it in Raticode, it's their decision.
    later = {**item, "history": [*item["history"], {"actor": "user", "action": "changed"}]}
    assert module._decision_item(later)["read_from"] is None
    assert module._decision_item({**item, "source": {"via": "chat"}})["read_from"] is None
