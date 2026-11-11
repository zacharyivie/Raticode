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

    def conversation(self) -> dict[str, Any]:
        return {
            "conversation": {"turn_state": "idle", "provider": "codex"},
            "messages": self.messages,
        }

    def tasks(self, *, summary: bool = False) -> list[dict[str, Any]]:
        # The phone never needs run traces or worker config.
        assert summary
        return [self.task]

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
        # The phone can't review an unsandboxed roster, so it can't start one.
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
        refused = send(
            "desktop.action", "organizations.control", {"organization_id": oid, "state": "running"}
        )
        assert refused["code"] == "unauthorized" and "desktop" in refused["detail"]
        assert manager.store.get("", oid)["runtime"]["state"] == "paused"
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


def test_phone_retries_a_review_and_reads_the_reply_as_it_grows(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    overview = send("desktop.read", "overview")["value"]
    assert overview["review_retry"] is True and overview["draft_deltas"] is True
    assert overview["thread_read"] is True
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


ALL_ALERTS = ["needs_you", "finished", "proposals"]


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
        assert notice["kind"] in {"reply", "task_update", "proposals", "organization"}
        assert notice.get("reason", "needs_you") in ALL_ALERTS
        assert 0 < len(notice["id"]) <= 128 and len(notice["body"]) <= 1500


def test_alert_filter_is_advertised_and_checked(phone, home):
    app, peer, _, send = alerting_phone(phone, home)
    assert send("desktop.read", "overview")["value"]["rem_notices_filter"] is True
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
        assert send("desktop.read", "rem.notices", {"include": bad})["code"] == "invalid"
    for bad in (5, None, "", "abc", "n1.1", "n1.1.2.3", "n1.a.2", "n2.1.2", "x" * 257):
        read = send("desktop.read", "rem.notices", {"include": ["finished"], "mark": bad})
        assert read["code"] == "invalid"
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
    assert first["mark"].endswith(".-")
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
