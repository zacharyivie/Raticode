"""Turn-scoped Rem project selection and explicitly requested child threads."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from typing import Any
from uuid import uuid4

from gofer.ui.swarm_tools import SwarmToolServer

INSTRUCTIONS = """Rem thread tools are available. Open projects are listed below.
Organizations are global: use organization_action directly to manage them, without
selecting a project. In global scope, select_project before doing project file work.
Choose the project from the user's request; ask when ambiguous. After selecting, end this
turn immediately without editing. Raticode continues the request in that project's directory.
Use start_thread only when the user explicitly asks to create/start a separate or new
thread. Never create a thread merely to delegate ordinary work. Copy the requested task
accurately into message. The new thread inherits this thread's provider, model, permissions,
and resources, and starts in the chosen project. Do not perform its task in this thread.
A tool receipt means the action is queued; Raticode applies it when this turn finishes.
"""


def thread_tools(global_scope: bool, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not projects:
        return []
    root = {"type": "string", "enum": [project["root"] for project in projects]}
    tools = [
        {
            "name": "start_thread",
            "description": "Start a new Rem thread only when explicitly requested by the user.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "projectRoot": root,
                    "message": {"type": "string", "description": "The task for the new thread."},
                    "userRequest": {
                        "type": "string",
                        "description": "Quote the latest user request for a new thread.",
                    },
                },
                "required": ["projectRoot", "message", "userRequest"],
                "additionalProperties": False,
            },
        }
    ]
    if global_scope:
        tools.append(
            {
                "name": "select_project",
                "description": "Select this global thread's project before editing.",
                # Claude Code's plan mode, used for global turns, rejects MCP tools
                # unless they declare that they leave the workspace unchanged.
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"projectRoot": root},
                    "required": ["projectRoot"],
                    "additionalProperties": False,
                },
            }
        )
    return tools


class ThreadActions:
    def __init__(self, config: dict[str, Any], messages: list[dict[str, Any]]) -> None:
        self.projects = {item["root"]: item for item in config.get("projects", [])}
        self.global_scope = config.get("global") is True
        self.latest_user = next(
            (
                str(item.get("body", ""))
                for item in reversed(messages)
                if item.get("role") == "user"
            ),
            "",
        )
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            root = arguments.get("projectRoot")
            if not isinstance(root, str) or root not in self.projects:
                raise ValueError("Choose one of the open projects.")
            project = self.projects[root]
            if name == "select_project":
                if not self.global_scope:
                    raise ValueError("Only global threads can select their own project.")
                event = {
                    "type": "project-scope",
                    "projectRoot": root,
                    "projectName": project["name"],
                }
            elif name == "start_thread":
                message = arguments.get("message")
                quote = arguments.get("userRequest")
                if not isinstance(message, str) or not message.strip() or len(message) > 100_000:
                    raise ValueError("Supply the task for the new thread.")
                if not isinstance(quote, str) or not quote.strip() or quote not in self.latest_user:
                    raise ValueError("Quote the user's explicit request for a new thread.")
                event = {
                    "type": "new-thread",
                    "threadId": str(uuid4()),
                    "projectRoot": root,
                    "message": message,
                }
            else:
                raise ValueError("Unknown Rem thread tool.")
            # One handoff per turn prevents repeated calls from duplicating work.
            if self.events:
                previous = self.events[0]
                if previous["type"] == event["type"] and previous["projectRoot"] == root:
                    return {"queued": True, **previous}
                raise ValueError("A thread action is already queued for this turn.")
            self.events.append(event)
            return {
                "queued": True,
                **event,
                "instruction": "End this turn now. Raticode will apply the action.",
            }


async def stream_with_thread_tools(
    source: Callable[..., AsyncGenerator[dict[str, Any], None]], **kwargs: Any
) -> AsyncGenerator[dict[str, Any], None]:
    workflow = dict(kwargs.get("workflow") or {})
    config = workflow.get("remThreads") or {}
    if not config.get("projects") and not config.get("global"):
        async with aclosing(source(**kwargs)) as stream:
            async for event in stream:
                yield event
        return
    actions = ThreadActions(config, kwargs.get("messages") or [])
    server = SwarmToolServer()
    url = server.register(
        actions.call,
        thread_tools(actions.global_scope, list(actions.projects.values())),
        INSTRUCTIONS,
    )
    try:
        resources = dict(workflow.get("remResources") or {})
        resources["mcpServers"] = [
            item for item in resources.get("mcpServers", []) if item.get("name") != "rem_threads"
        ] + [{"name": "rem_threads", "type": "http", "url": url}]
        instructions = INSTRUCTIONS
        if config.get("spawned"):
            instructions += (
                "This is already the requested new thread. "
                "Perform its task here; do not spawn it again.\n"
            )
        workflow.update(remResources=resources, remThreadInstructions=instructions)
        # Project selection gates native edits, not research. Codex reads skill
        # files through its command tool under the read-only sandbox below.
        # Other enforced adapters use dedicated read tools without commands.
        if actions.global_scope and kwargs.get("provider") != "codex":
            workflow["remResources"] = {**resources, "shell": False}
        initial = {**kwargs, "workflow": workflow, "trusted_rem_threads_url": url}
        if actions.global_scope and kwargs.get("provider") == "codex":
            initial["permission_mode"] = "read-only"
        # Claude global turns retain read and web tools (see _build_chat_command).
        # Keep the user's permission mode so global management MCP writes work.
        pending_final = None
        async with aclosing(source(**initial)) as stream:
            async for event in stream:
                if event.get("type") == "final":
                    pending_final = event
                else:
                    yield event
                if event.get("type") in {"error", "stopped"}:
                    return
        if kwargs.get("cancel_event") is not None and kwargs["cancel_event"].is_set():
            return
        if not actions.events:
            if pending_final:
                yield pending_final
            return
        action = actions.events[0]
        if action["type"] == "new-thread":
            yield action
            if pending_final:
                yield pending_final
            return
        project = actions.projects[action["projectRoot"]]
        workflow["remSwarmAccess"] = {
            **(workflow.get("remSwarmAccess") or {}),
            "grantId": project.get("grantId"),
        }
        workflow.update(
            projectRoot=project["root"],
            projectName=project["name"],
            remResources=resources,
            remThreads={**config, "global": False},
            remThreadInstructions="Project selected. Continue the task in this directory.\n"
            + INSTRUCTIONS,
        )
        # Rebuild the provider invocation so cwd, project instructions, and edit tracking agree.
        continuation = dict(kwargs, workflow=workflow)
        continuation.pop("working_dir", None)
        continuation["workflow"]["workflows"] = []
        # Steering restarts must retain the newly selected project too.
        kwargs["workflow"].update(
            {
                key: workflow[key]
                for key in (
                    "projectRoot",
                    "projectName",
                    "remThreads",
                    "remSwarmAccess",
                    "workflows",
                )
            }
        )
        yield action
        async with aclosing(stream_with_thread_tools(source, **continuation)) as stream:
            async for event in stream:
                yield event
    finally:
        server.revoke(url)
        await asyncio.to_thread(server.close)
