"""Turn-scoped Rem project selection and explicitly requested child threads."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.ui.swarm_tools import TOOL_FAILURE_HINT, SwarmToolServer

INSTRUCTIONS = """Rem thread tools are available. Open projects are listed below.
Global scope uses the configured provider permissions and selected Rem resources.
Use global tools such as Second Brain save_note without selecting a project.
Organizations are global: use organization_action directly to manage them, without
selecting a project. Do not modify repository files unless explicitly asked by the user.
For requested project file work in global scope, call select_project first.
Choose the project from the user's request; ask when ambiguous. After selecting, end this
turn immediately without editing. Raticode continues the request in that project's directory.
Use start_thread only when the user explicitly asks to create/start a separate or new
thread. Never create a thread merely to delegate ordinary work. Copy the requested task
accurately into message. The new thread inherits this thread's provider, model, permissions,
and resources, and starts in the chosen project. Do not perform its task in this thread.
A tool receipt means the action is queued; Raticode applies it when this turn finishes.
"""

WORKTREE_INSTRUCTIONS = """Git worktrees inside an open project folder count as open projects,
including worktrees created during this turn. Pass the worktree's absolute path.
"""

HANDOFF_INSTRUCTIONS = """Earlier in this turn you called select_project, and Raticode moved this
thread to {name} ({root}). You are now working in that directory. That part of the user's
request is complete, and so are the steps you took before selecting it, such as creating
this folder or worktree: do not repeat them, and do not try to switch projects again.
Continue with any remaining work here. If nothing remains, tell the user the thread now
works in {root}.
"""

GIT_TIMEOUT_SECONDS = 10


def thread_tools(
    global_scope: bool, projects: list[dict[str, Any]], *, worktrees: bool = False
) -> list[dict[str, Any]]:
    if not projects:
        return []
    roots = [project["root"] for project in projects]
    root: dict[str, Any] = {"type": "string", "enum": roots}
    if worktrees:
        # Clients read the tool list once per session, so a fixed enum would
        # reject worktrees created later in the turn. The call checks the path.
        root = {
            "type": "string",
            "description": "An open project root, or a git worktree inside one. "
            "Open projects: " + ", ".join(roots),
        }
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
                # Project selection leaves files unchanged and remains available
                # when the user explicitly chooses Claude Code's plan mode.
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


def _git_worktrees(project: Path) -> set[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode:
        return set()
    return {
        Path(line.removeprefix("worktree ")).resolve()
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    }


class ThreadActions:
    def __init__(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        worktrees: bool = False,
    ) -> None:
        self.projects = {item["root"]: item for item in config.get("projects", [])}
        self.worktrees = worktrees
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

    def _worktree_project(self, value: str) -> dict[str, Any] | None:
        """Admit a git worktree that appeared inside an open project during the turn.

        The open project's grant already covers every folder inside it, so the
        worktree reuses that grant. Worktrees elsewhere still need the user to open them.
        """
        target = Path(value)
        if not self.worktrees or not target.is_absolute():
            return None
        try:
            canonical = target.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not canonical.is_dir():
            return None
        for project in list(self.projects.values()):
            try:
                parent = Path(project["root"]).resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if canonical == parent or not canonical.is_relative_to(parent):
                continue
            if canonical in _git_worktrees(parent):
                found = {
                    "root": str(canonical),
                    "name": canonical.name,
                    "grantId": project.get("grantId"),
                }
                self.projects[found["root"]] = found
                return found
        return None

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            root = arguments.get("projectRoot")
            project = (
                (self.projects.get(root) or self._worktree_project(root))
                if isinstance(root, str)
                else None
            )
            if project is None:
                raise ValueError("Choose one of the open projects.")
            root = project["root"]
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
    source: Callable[..., AsyncGenerator[dict[str, Any], None]],
    *,
    worktrees: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run a Rem turn with thread tools, then apply the queued project handoff.

    With ``worktrees``, the tools also accept git worktrees created inside an
    open project after the turn started.
    """
    workflow = dict(kwargs.get("workflow") or {})
    config = workflow.get("remThreads") or {}
    if not config.get("projects") and not config.get("global"):
        async with aclosing(source(**kwargs)) as stream:
            async for event in stream:
                yield event
        return
    actions = ThreadActions(config, kwargs.get("messages") or [], worktrees=worktrees)
    base_instructions = INSTRUCTIONS + (WORKTREE_INSTRUCTIONS if worktrees else "")
    server = SwarmToolServer()
    url = server.register(
        actions.call,
        thread_tools(actions.global_scope, list(actions.projects.values()), worktrees=worktrees),
        base_instructions,
        label="Rem thread",
        failure_hint=TOOL_FAILURE_HINT,
    )
    try:
        resources = dict(workflow.get("remResources") or {})
        resources["mcpServers"] = [
            item for item in resources.get("mcpServers", []) if item.get("name") != "rem_threads"
        ] + [{"name": "rem_threads", "type": "http", "url": url}]
        instructions = base_instructions
        handoff = config.get("handoff")
        if isinstance(handoff, dict):
            # The continuation is a fresh provider session that replays the same
            # request, so it has to be told the selection already happened.
            instructions = (
                HANDOFF_INSTRUCTIONS.format(name=handoff["name"], root=handoff["root"])
                + instructions
            )
        if config.get("spawned"):
            instructions += (
                "This is already the requested new thread. "
                "Perform its task here; do not spawn it again.\n"
            )
        workflow.update(remResources=resources, remThreadInstructions=instructions)
        initial = {**kwargs, "workflow": workflow, "trusted_rem_threads_url": url}
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
            # A worktree found during the turn stays open for the continuation.
            remThreads={
                **config,
                "projects": list(actions.projects.values()),
                "global": False,
                "handoff": {"root": project["root"], "name": project["name"]},
            },
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
        async with aclosing(
            stream_with_thread_tools(source, worktrees=worktrees, **continuation)
        ) as stream:
            async for event in stream:
                yield event
    finally:
        server.revoke(url)
        await asyncio.to_thread(server.close)
