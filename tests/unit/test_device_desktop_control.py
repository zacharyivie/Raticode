"""Using Raticode from a phone: opt-in, path-free results and desktop-owned actions."""

import base64
import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices import desktop_control as module
from gofer.devices.registry import PairingError
from gofer.devices.remote_control import swarm_wire_id
from gofer.ui.organizations import OrganizationManager
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
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

    def tasks(self) -> list[dict[str, Any]]:
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

    def submit_message(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        ("w9", "thoughts"),
    ]
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
    assert thread["messages"][3]["steps"][0]["status"] == "running"


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

    def slow_tasks() -> list[dict[str, Any]]:
        started.set()
        assert release.wait(5)
        result: list[dict[str, Any]] = tasks()
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
    app.handle(peer, event("file.offer", sequence, thread_id=thread, payload=offer))
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
    # The channel is closed until the user lets this phone use Raticode.
    with pytest.raises(PairingError, match="file_thread_grant_required"):
        _upload(app, peer, channel, b"x", 600)
    app.desktop.enable(peer, True)
    home = send("desktop.read", "rem.home")["value"]
    assert home["files_thread"] == channel and home["media"] is True
    assert send("desktop.read", "overview")["value"]["media"] is True
    # Another phone's channel, or any other unshared thread, stays closed.
    with pytest.raises(PairingError):
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

    # A tapped link is not a file Rem attached: outside the open folders it stays refused.
    rem.store.messages.append(
        {"id": "linked", "role": "rem", "body": f"See [it](<{outside}>)", "created_at": "t6"}
    )
    tapped = send(
        "desktop.action",
        "files.link",
        {"source": "rem", "message_id": "linked", "href": str(outside)},
    )
    assert tapped["ok"] is False

    identifier = reply["files"][0]["file_id"]
    chunk = app.handle(
        peer,
        event("file.request", 700, thread_id=channel, payload={"file_id": identifier}),
    )[-1]["payload"]
    assert base64.urlsafe_b64decode(chunk["data"] + "==") == report.read_bytes()[:16384]
    app.remove_peer(peer)
    assert app.registry.db.execute("SELECT COUNT(*) FROM device_rem_files").fetchone()[0] == 0


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
    rem.store.tasks = lambda: [rem.store.task, done, recent]
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
