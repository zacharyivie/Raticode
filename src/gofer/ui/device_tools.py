"""Turn-scoped fleet tools. Pairing and target-local grants are never agent tools."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from typing import Any, Protocol
from uuid import UUID

from gofer.ui.swarm_tools import SwarmToolServer

INSTRUCTIONS = """Raticode fleet tools report authenticated paired-device evidence.
Use fleet_status to inspect machines and freshness. Unknown/stale is not up or down.
Use fleet_send only for work the user authorized on a paired desktop, with its
target-local thread and project grants. A mobile device cannot execute work.
Keep the same request_id on a retry. A queued receipt is not completion; inspect
fleet_work_status. Never retry outcome_unknown as new work without a user decision.
Pairing and authorization changes require the local Settings UI.
"""


class FleetControl(Protocol):
    def action(self, body: dict[str, Any]) -> dict[str, Any]: ...


def fleet_tools(
    read_only: bool, file_scope: tuple[str, str, str] | None = None
) -> list[dict[str, Any]]:
    result = [
        {
            "name": "fleet_status",
            "description": "Inspect paired fleet and authenticated freshness.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "fleet_work_status",
            "description": "Inspect a durable remote work receipt/result.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string"},
                    "request_id": {"type": "string"},
                },
                "required": ["device_id", "request_id"],
                "additionalProperties": False,
            },
        },
    ]
    if not read_only:
        result.append(
            {
                "name": "fleet_send",
                "description": "Queue authorized work on a paired desktop using its local grants.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string"},
                        "thread_id": {"type": "string"},
                        "project_id": {"type": "string"},
                        "request_id": {"type": "string"},
                        "text": {"type": "string", "maxLength": 16000},
                    },
                    "required": ["device_id", "thread_id", "project_id", "request_id", "text"],
                    "additionalProperties": False,
                },
            }
        )
    if not read_only or file_scope is not None:
        properties = {"path": {"type": "string"}}
        if file_scope is None:
            properties.update(device_id={"type": "string"}, thread_id={"type": "string"})
        result.append(
            {
                "name": "device_offer_file",
                "description": (
                    "Offer a user-requested file inside the locally granted project. "
                    "The receiving device requests its encrypted transfer."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            }
        )
    return result


class FleetActions:
    def __init__(
        self, control: FleetControl, read_only: bool, file_scope: tuple[str, str, str] | None = None
    ) -> None:
        self.control = control
        self.read_only = read_only
        self.file_scope = file_scope

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "device_offer_file":
            if self.read_only and self.file_scope is None:
                raise ValueError("This turn cannot offer files")
            fields = {"path"} if self.file_scope else {"path", "device_id", "thread_id"}
            if set(arguments) != fields or not all(isinstance(v, str) for v in arguments.values()):
                raise ValueError("Invalid file offer arguments")
            body = {"action": "offer_file", **arguments}
            if self.file_scope:
                body.update(
                    device_id=self.file_scope[0],
                    thread_id=self.file_scope[1],
                    origin_request=self.file_scope[2],
                )
            return self.control.action(body)
        if name == "fleet_status":
            if arguments:
                raise ValueError("fleet_status takes no arguments")
            return self.control.action({"action": "fleet_status"})
        fields = {"device_id", "request_id"}
        if name == "fleet_send":
            if self.read_only:
                raise ValueError("Read-only turns cannot send fleet work")
            fields |= {"thread_id", "project_id", "text"}
        elif name != "fleet_work_status":
            raise ValueError("Unknown fleet tool")
        if set(arguments) != fields or not all(isinstance(v, str) for v in arguments.values()):
            raise ValueError("Invalid fleet tool arguments")
        for key in fields - {"text"}:
            if str(UUID(arguments[key])) != arguments[key]:
                raise ValueError("Fleet identifiers must be canonical UUIDs")
        if name == "fleet_send":
            if not arguments["text"].strip() or len(arguments["text"]) > 16000:
                raise ValueError("Supply a bounded work request")
            return self.control.action({"action": "send", "kind": "job.submit", **arguments})
        result = self.control.action({"action": "work_status", **arguments})
        if self.file_scope is not None:
            # Device-origin turns can inspect fleet work state, but another
            # locally granted project's conversation is not theirs to read.
            return {"state": result.get("state", "unknown"), "request_id": arguments["request_id"]}
        return result


async def stream_with_fleet_tools(
    source: Callable[..., AsyncGenerator[dict[str, Any], None]],
    control: FleetControl | None,
    *,
    read_only_override: bool | None = None,
    file_scope: tuple[str, str, str] | None = None,
    **kwargs: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    if control is None:
        async with aclosing(source(**kwargs)) as stream:
            async for event in stream:
                yield event
        return
    read_only = kwargs.get("permission_mode") in {None, "read-only", "plan"}
    if read_only_override is not None:
        read_only = read_only_override or read_only
    actions = FleetActions(control, read_only, file_scope)
    server = SwarmToolServer()
    url = server.register(actions.call, fleet_tools(read_only, file_scope), INSTRUCTIONS)
    try:
        workflow = dict(kwargs.get("workflow") or {})
        resources = dict(workflow.get("remResources") or {})
        resources["mcpServers"] = [
            item for item in resources.get("mcpServers", []) if item.get("name") != "fleet"
        ] + [{"name": "fleet", "type": "http", "url": url}]
        workflow["remResources"] = resources
        async with aclosing(source(**{**kwargs, "workflow": workflow})) as stream:
            async for event in stream:
                yield event
    finally:
        server.close()
