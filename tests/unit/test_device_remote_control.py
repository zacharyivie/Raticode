"""Background phone use: bounded polling records, resource toggles, approvals and swarms."""

from typing import Any

import pytest

from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.devices.remote_control import swarm_wire_id
from gofer.devices.workspace import resource_options
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_parity import parity


def request(kind: str, sequence: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return event(kind, sequence, thread_id=None, payload=payload or {})


def test_polling_requests_never_exhaust_the_lifetime_inbox(application, monkeypatch):
    from gofer.devices import application as module

    app, peer = application
    monkeypatch.setattr(module, "RECENT_READS", 5)
    first = request("catalog.request", 0)
    app.handle(peer, first)
    for sequence in range(1, 20):
        app.handle(peer, request("catalog.request", sequence))
    db = app.registry.db
    assert db.execute("SELECT COUNT(*) FROM device_inbox WHERE peer=?", (peer,)).fetchone()[0] == 0
    assert (
        db.execute("SELECT COUNT(*) FROM device_inbox_reads WHERE peer=?", (peer,)).fetchone()[0]
        == 5
    )
    # A recent duplicate is still recognized, and a reused sequence still conflicts.
    duplicate = request("catalog.request", 20)
    app.handle(peer, duplicate)
    replies = "SELECT COUNT(*) FROM device_outbox WHERE peer=?"
    before = db.execute(replies, (peer,)).fetchone()[0]
    app.handle(peer, duplicate)
    assert db.execute(replies, (peer,)).fetchone()[0] == before
    with pytest.raises(ValueError, match="sequence_conflict"):
        app.handle(peer, request("catalog.request", 20))


def test_resources_change_only_on_explicit_phone_choice(application, tmp_path):
    app, peer = application
    context, meta, shared, _ = parity(app, peer, tmp_path)
    thread = shared["thread_id"]
    options = {o["name"]: o for o in resource_options(context)}
    assert options["Commands"]["enabled"] and not options["Browser"]["enabled"]
    enabled = sorted(o["id"] for o in options.values() if o["enabled"])
    history = app.handle(
        peer,
        event(
            "thread.history.request",
            1,
            thread_id=thread,
            payload={"before": None, "include_presentation": True},
        ),
    )[-1]["payload"]
    assert sorted(history["resource_ids"]) == enabled
    base = {"project_id": context["project_id"], "provider": "codex", "model": "model-a"}
    # An older phone resends a stale list without the flag; nothing changes.
    stale = app.handle(
        peer,
        event(
            "thread.context.update",
            2,
            thread_id=thread,
            payload={**base, "expected_revision": 1, "resource_ids": []},
        ),
    )[-1]
    assert stale["type"] == "thread.snapshot"
    assert sorted(stale["payload"]["resource_ids"]) == enabled
    assert "resources" not in app.workspace.export(peer, thread)["metadata"] or (
        app.workspace.export(peer, thread)["metadata"]["resources"] == meta["resources"]
    )
    chosen = [options["Commands"]["id"], options["Browser"]["id"]]
    changed = app.handle(
        peer,
        event(
            "thread.context.update",
            3,
            thread_id=thread,
            payload={**base, "expected_revision": 2, "resource_ids": chosen, "set_resources": True},
        ),
    )[-1]
    assert changed["type"] == "thread.snapshot"
    assert sorted(changed["payload"]["resource_ids"]) == sorted(chosen)
    resources = app.workspace.export(peer, thread)["metadata"]["resources"]
    assert resources["shell"] and resources["browser"]
    assert not resources["web"] and not resources["agents"]
    unknown = app.handle(
        peer,
        event(
            "thread.context.update",
            4,
            thread_id=thread,
            payload={
                **base,
                "expected_revision": 3,
                "resource_ids": ["00000000-0000-4000-8000-000000000999"],
                "set_resources": True,
            },
        ),
    )[-1]
    assert unknown["type"] == "error"


def test_provider_catalog_does_not_need_the_desktop_window(application):
    app, peer = application
    calls = []

    def providers() -> list[dict[str, Any]]:
        calls.append(1)
        return [{"id": "codex", "models": ["model-a"]}]

    app.workspace.provider_source = providers
    worker = app.workspace.refresh_providers()
    assert worker is not None
    worker.join(5)
    assert app.workspace.catalog(peer)["providers"] == [{"id": "codex", "models": ["model-a"]}]
    assert app.workspace.refresh_providers() is None
    assert len(calls) == 1


def test_provider_catalog_failure_never_breaks_a_device_session(application):
    app, peer = application

    def failing() -> list[dict[str, Any]]:
        raise RuntimeError("cannot run inside the device event loop")

    app.workspace.provider_source = failing
    app.workspace.providers = []
    worker = app.workspace.refresh_providers()
    assert worker is not None
    worker.join(5)
    reply = app.handle(peer, request("catalog.request", 0))[-1]
    assert reply["type"] == "catalog.snapshot" and reply["payload"]["providers"] == []


class Swarms:
    def __init__(self, root: str) -> None:
        self.root = root
        self.calls: list[tuple[str, Any]] = []
        self.swarm = {
            "id": "swarm1",
            "name": "Previews",
            "agents": [{"id": "lead", "name": "Lead"}],
            "run": {
                "state": "running",
                "task": "Add previews",
                "progress": {"percent": 40.0},
                "agentStates": {"lead": {"state": "working"}},
                "messages": [{"senderId": "lead", "body": "Started", "createdAt": "now"}],
                "humanInbox": [
                    {
                        "id": "ask1",
                        "state": "pending",
                        "description": "Merge the branch?",
                        "recommendedAction": "Merge",
                        "createdAt": "now",
                    }
                ],
            },
        }

    def list(self, root: str) -> list[dict[str, Any]]:
        return [self.swarm] if root == self.root else []

    def get(self, root: str, identifier: str, **_: Any) -> dict[str, Any]:
        return self.swarm

    def control(self, root: str, identifier: str, action: str, **options: Any) -> None:
        self.calls.append(("control", action))

    def message(self, root: str, identifier: str, payload: dict[str, Any]) -> None:
        self.calls.append(("message", payload["body"]))

    def execution(self, root: str, identifier: str, payload: dict[str, Any]) -> None:
        self.calls.append(("execution", payload))
        self.swarm["run"]["humanInbox"][0]["state"] = "addressed"


def test_approvals_and_swarms_need_desktop_opt_in(application, tmp_path):
    app, peer = application
    context, _, _, _ = parity(app, peer, tmp_path)
    root = context["projects"][0]["root"]
    swarms = Swarms(root)
    remote = app.remote
    remote.swarms = swarms
    remote.data_dir = tmp_path / "data"
    decided: list[tuple[Any, ...]] = []
    remote.decide_workflow = lambda *args, **kwargs: decided.append((args, kwargs))
    ApprovalStore(remote.data_dir).create_or_update(
        ApprovalRequest(workflow_id="deploy", run_id="run1", node_id="gate", message="Ship it?")
    )
    sequence = iter(range(10, 100))

    def send(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return app.handle(peer, request(kind, next(sequence), payload))[-1]

    locked = send("approval.request", {})
    assert locked["payload"] == {"remote_allowed": False, "approvals": []}
    assert send("swarm.request", {"swarm_id": None})["payload"]["swarms"] == []
    wire = swarm_wire_id(root, "swarm1")
    refused = send("swarm.control", {"swarm_id": wire, "action": "pause"})
    assert refused["type"] == "error" and swarms.calls == []

    remote.enable(peer, True)
    snapshot = send("approval.request", {})["payload"]
    assert snapshot["remote_allowed"]
    by_source = {a["source"]: a for a in snapshot["approvals"]}
    assert by_source["workflow"]["summary"] == "Ship it?"
    assert by_source["swarm"]["instruction_required"]
    workflow = by_source["workflow"]
    stale = send(
        "approval.resolve",
        {"approval_id": workflow["approval_id"], "action_digest": "0" * 64, "decision": "approve"},
    )
    assert stale["type"] == "error" and decided == []
    send(
        "approval.resolve",
        {
            "approval_id": workflow["approval_id"],
            "action_digest": workflow["action_digest"],
            "decision": "approve",
        },
    )
    assert decided[0][0][:4] == ("deploy", "run1", "gate", "approved")
    assert decided[0][1]["decided_by"] == "phone"
    swarm_approval = by_source["swarm"]
    needs_text = send(
        "approval.resolve",
        {
            "approval_id": swarm_approval["approval_id"],
            "action_digest": swarm_approval["action_digest"],
            "decision": "deny",
        },
    )
    assert needs_text["type"] == "error"
    send(
        "approval.resolve",
        {
            "approval_id": swarm_approval["approval_id"],
            "action_digest": swarm_approval["action_digest"],
            "decision": "deny",
            "instruction": "Run the tests first",
        },
    )
    assert swarms.calls[-1][1]["decision"] == "redirect"

    listed = send("swarm.request", {"swarm_id": wire})["payload"]["swarms"][0]
    assert listed["status"] == "running" and listed["progress"] == 40
    assert listed["agents"] == [{"name": "Lead", "state": "working"}]
    assert listed["messages"][0]["sender"] == "Lead"
    send("swarm.control", {"swarm_id": wire, "action": "pause"})
    send("swarm.message", {"swarm_id": wire, "body": "Focus on tests"})
    assert ("control", "pause") in swarms.calls and ("message", "Focus on tests") in swarms.calls
    remote.enable(peer, False)
    assert send("swarm.control", {"swarm_id": wire, "action": "stop"})["type"] == "error"
