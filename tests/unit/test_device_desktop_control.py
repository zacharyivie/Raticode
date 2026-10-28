"""Using Raticode from a phone: opt-in, path-free results and desktop-owned actions."""

import json
from typing import Any

import pytest

from gofer.devices import desktop_control as module
from gofer.devices.remote_control import swarm_wire_id
from gofer.ui.organizations import OrganizationManager
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_parity import parity
from tests.unit.test_device_remote_control import Swarms


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

    def conversation(self) -> dict[str, Any]:
        return {
            "conversation": {"turn_state": "idle", "provider": "codex"},
            "messages": self.messages,
        }

    def tasks(self) -> list[dict[str, Any]]:
        return [self.task]

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
