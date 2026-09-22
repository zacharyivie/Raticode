from typing import Any
from uuid import uuid4

import pytest

from gofer.ui.device_tools import FleetActions, fleet_tools, stream_with_fleet_tools


class Control:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def action(self, body):
        self.calls.append(body)
        return {"state": "queued"}


def test_read_only_fleet_cannot_mutate_even_with_direct_call():
    control = Control()
    tools = FleetActions(control, True)
    assert {tool["name"] for tool in fleet_tools(True)} == {"fleet_status", "fleet_work_status"}
    with pytest.raises(ValueError, match="Read-only"):
        tools.call("fleet_send", {})
    assert control.calls == []
    tools.call("fleet_status", {})
    assert control.calls == [{"action": "fleet_status"}]


def test_send_has_stable_id_and_cannot_add_authority():
    control = Control()
    tools = FleetActions(control, False)
    args = {key: str(uuid4()) for key in ("device_id", "thread_id", "project_id", "request_id")}
    args["text"] = "Run the approved project tests"
    tools.call("fleet_send", args)
    tools.call("fleet_send", args)
    assert control.calls[0] == control.calls[1]
    assert control.calls[0]["kind"] == "job.submit"
    with pytest.raises(ValueError):
        tools.call("fleet_send", {**args, "permission_mode": "danger-full-access"})
    with pytest.raises(ValueError):
        tools.call("authorize_thread", args)


def test_remote_file_offer_cannot_change_recipient_or_thread():
    control = Control()
    tools = FleetActions(control, True, ("paired-controller", "granted-thread", "active-request"))
    tools.call("device_offer_file", {"path": "/project/result.txt"})
    assert control.calls == [
        {
            "action": "offer_file",
            "path": "/project/result.txt",
            "device_id": "paired-controller",
            "thread_id": "granted-thread",
            "origin_request": "active-request",
        }
    ]
    with pytest.raises(ValueError):
        tools.call("device_offer_file", {"path": "/project/result.txt", "device_id": "other-peer"})


def test_remote_fleet_status_does_not_expose_other_project_conversations():
    class ResultControl(Control):
        def action(self, body):
            return {"state": "completed", "events": [{"payload": {"text": "private project B"}}]}

    tools = FleetActions(ResultControl(), True, ("controller", "thread-A", "request-A"))
    request_id = str(uuid4())
    result = tools.call("fleet_work_status", {"device_id": str(uuid4()), "request_id": request_id})
    assert result == {"state": "completed", "request_id": request_id}


async def test_turn_scoped_resource_injection_preserves_thread_context():
    seen = []

    async def source(**options):
        seen.append(options)
        yield {"type": "final", "message": {"body": "done"}}

    workflow: dict[str, Any] = {
        "projectRoot": "/test",
        "thread_id": str(uuid4()),
        "remResources": {
            "shell": False,
            "mcpServers": [
                {"name": "user_resource", "type": "http", "url": "https://example.invalid"}
            ],
        },
    }
    result = [
        event
        async for event in stream_with_fleet_tools(
            source,
            Control(),
            workflow=workflow,
            permission_mode="read-only",
        )
    ]
    assert result[0]["type"] == "final"
    assert seen[0]["workflow"]["thread_id"] == workflow["thread_id"]
    assert seen[0]["workflow"]["remResources"]["shell"] is False
    servers = seen[0]["workflow"]["remResources"]["mcpServers"]
    assert [server["name"] for server in servers] == ["user_resource", "fleet"]
    assert len(workflow["remResources"]["mcpServers"]) == 1
