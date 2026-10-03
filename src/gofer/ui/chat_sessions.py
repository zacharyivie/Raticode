"""Rem-owned references to provider-native conversations, never copied transcripts.

Each completed message resumes an explicit native ID. Provider tools retain their
own history and compaction. Session references survive desktop/backend restarts.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from functools import wraps
from hashlib import sha256
from pathlib import Path
from typing import Any, ParamSpec

import anyio

from gofer.core.prompt_envelope import prompt_envelope
from gofer.utils.atomic_output import atomic_binary_output, mkdir_without_links, open_binary_input
from gofer.utils.paths import get_data_dir

P = ParamSpec("P")
RECOVERY_DELAYS = (1.0, 3.0)
RECOVERY_PROMPT = (
    "Your previous response was interrupted by a provider error. Continue the original task "
    "in this same session. Inspect the current files and completed actions before doing more "
    "work, avoid repeating completed edits or external actions, and finish the user's request."
)


def _recoverable(provider: str, event: dict[str, Any]) -> bool:
    # Start with the observed Cursor failure only. Never infer transient failures
    # from damaged wire records, authentication failures, or arbitrary tool text.
    return (
        provider == "cursor"
        and event.get("type") == "error"
        and event.get("errorKind") == "provider"
        and not event.get("parseError")
        and str(event.get("error", "")).strip().lower() == "provider emitted malformed json content"
    )


async def _recovery_delay(delay: float, cancel: Any) -> bool:
    deadline = anyio.current_time() + delay
    while anyio.current_time() < deadline:
        if cancel is not None and cancel.is_set():
            return False
        await anyio.sleep(min(0.05, max(0, deadline - anyio.current_time())))
    return cancel is None or not cancel.is_set()


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class ChatSession:
    root: Path
    state: dict[str, Any]
    users: list[str]
    resumed: bool
    document: dict[str, Any]
    handoff: bool = False
    pending: dict[str, Any] = field(default_factory=dict)

    def save(self) -> None:
        self.document["sessions"][self.state["provider"]] = self.state
        with atomic_binary_output(self.root / "session.json") as output:
            output.write(json.dumps(self.document).encode())

    @property
    def native_id(self) -> str | None:
        return self.state.get("nativeId")

    def prompt(self, instructions: str, context: str, request: str) -> str:
        self.pending.update(instructions=_digest(instructions), context=_digest(context))
        if self.resumed:
            if self.state.get("instructions") == self.pending["instructions"]:
                instructions = ""
            if self.state.get("context") == self.pending["context"]:
                context = ""
        return prompt_envelope(instructions=instructions, context=context, request=request)

    def observe(self, event: dict[str, Any]) -> None:
        native = event.get("sessionId")
        if not isinstance(native, str) or not native:
            return
        if (
            native.startswith("-")
            or "\0" in native
            or len(native) > 512
            or any(c.isspace() for c in native)
        ):
            raise ValueError("Provider returned an invalid native session identity")
        if self.native_id and native != self.native_id:
            raise ValueError("Provider changed native session identity while resuming Rem")
        updated = {**self.state, **self.pending, "nativeId": native, "users": self.users}
        if updated == self.state and self.document.get("lastProvider") == self.state["provider"]:
            return
        self.state = updated
        self.state.pop("deliveryUncertain", None)
        self.document["lastProvider"] = self.state["provider"]
        self.save()


def _plan(root: Path, options: dict[str, Any]) -> tuple[ChatSession, list[dict[str, Any]]]:
    messages = options["messages"]
    provider = options["provider"]
    workflow = options.get("workflow") or {}
    # ACP requires the same cwd on load. Also keep different projects isolated.
    project = workflow.get("projectRoot") or str(options.get("working_dir") or "")
    users = [_digest(m) for m in messages if m.get("role", "user") == "user"]
    document: dict[str, Any] = {}
    try:
        with open_binary_input(root / "session.json") as source:
            raw = json.load(source)
        if not isinstance(raw, dict):
            raise ValueError("Invalid saved session metadata")
        if "version" not in raw:
            # Upgrade the original single-provider reference without discarding
            # its native ID or private configuration directory.
            raw = {
                "version": 2,
                "project": raw.get("project"),
                "users": raw.get("users", []),
                "sessions": {raw.get("provider"): raw},
            }
        if raw.get("version") != 2 or not isinstance(raw.get("sessions"), dict):
            raise ValueError("Invalid saved session metadata")
        for saved in raw["sessions"].values():
            if not isinstance(saved, dict) or not re.fullmatch(
                r"[0-9a-f]{32}", str(saved.get("generation", ""))
            ):
                raise ValueError("Invalid saved session metadata")
        document = raw
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as exc:
        if not options.get("reset_session"):
            raise ValueError("Rem's saved provider session cannot be read") from exc
        document = {}
    branch_users = document.get("users", [])
    if (
        options.get("reset_session")
        or document.get("project") != project
        or not isinstance(branch_users, list)
        or users[: len(branch_users)] != branch_users
    ):
        # Edits invalidate every provider's view of the old branch, including
        # dormant sessions. A provider switch alone keeps all references.
        document = {}
    sessions = document.setdefault("sessions", {})
    # Older references did not record the active provider. Its consumed user
    # hashes identify the provider that last received the branch history.
    last_provider = document.get("lastProvider") or next(
        (
            name
            for name, saved in sessions.items()
            if saved.get("nativeId") and saved.get("users") == branch_users
        ),
        None,
    )
    handoff = last_provider is not None and last_provider != provider
    document.update(version=2, project=project, users=users)
    state = sessions.get(provider, {})
    previous = state.get("users")
    previous = previous if isinstance(previous, list) else []
    same_provider = state.get("provider") == provider and state.get("project") == project
    if same_provider and state.get("deliveryUncertain") and not options.get("reset_session"):
        raise ValueError(
            "The previous turn ended without a native session ID. "
            "Edit and resend the message to start a new provider session safely."
        )
    resumed = bool(
        not options.get("reset_session")
        and state.get("provider") == provider
        and state.get("project") == project
        and state.get("nativeId")
        and previous
        and users[: len(previous)] == previous
    )
    if resumed:
        if len(users) == len(previous):
            raise ValueError("This message was already sent to the provider session")
        seen = 0
        for index, message in enumerate(messages):
            if message.get("role", "user") == "user":
                seen += 1
                if seen > len(previous):
                    # Include restart/control messages preceding the next user,
                    # while skipping the native assistant reply and its traces.
                    while index > 0 and messages[index - 1].get("role") == "system":
                        index -= 1
                    messages = messages[index:]
                    break
    else:
        state = {
            "provider": provider,
            "project": project,
            "generation": uuid.uuid4().hex,
        }
    session = ChatSession(root, state, users, resumed, document, handoff=handoff)
    session.save()
    return session, messages


def with_chat_session(
    function: Callable[P, AsyncGenerator[dict[str, Any], None]],
) -> Callable[P, AsyncGenerator[dict[str, Any], None]]:
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> AsyncGenerator[dict[str, Any], None]:
        bound = signature.bind(*args, **kwargs)
        options = bound.arguments
        workflow = options.get("workflow") or {}
        identity = options.get("conversation_id") or workflow.get("chatThreadId")
        if not identity or options.get("steering") is not None:
            source = function(*args, **kwargs)
            try:
                async for event in source:
                    yield event
            finally:
                await source.aclose()
            return
        root = (
            (options.get("data_dir") or get_data_dir())
            / "chat-provider-sessions"
            / _digest(identity)
        )
        mkdir_without_links(root)
        # A nonblocking SQLite lease works across request loops, threads and
        # backend processes. Closing the connection releases it after crashes.
        lease = sqlite3.connect(root / "lease.sqlite3", timeout=0)
        try:
            lease.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            lease.close()
            raise ValueError(
                "This Rem conversation already has an active provider session"
            ) from exc
        session: ChatSession | None = None
        started_at = anyio.current_time()
        try:
            session, messages = _plan(root, options)
            options["messages"] = messages
            options["_session"] = session
            attempt = 0
            cancel = options.get("cancel_event")
            while True:
                terminal = None
                source = function(*bound.args, **bound.kwargs)
                try:
                    async for event in source:
                        session.observe(event)
                        if event.get("type") in {"final", "error"}:
                            terminal = event
                        else:
                            yield event
                finally:
                    # Fully close/reap the old provider before a continuation.
                    await source.aclose()
                if terminal is None:
                    return
                if not session.native_id:
                    session.state["deliveryUncertain"] = True
                    session.save()
                    if terminal.get("type") == "final":
                        raise ValueError(
                            "Provider did not report a native session ID. "
                            "Update its CLI before continuing this conversation."
                        )
                if cancel is not None and cancel.is_set():
                    yield {"type": "stopped"}
                    return
                can_recover = session.native_id and _recoverable(options["provider"], terminal)
                if not can_recover or attempt >= len(RECOVERY_DELAYS):
                    session.state.pop("recoveryAttempt", None)
                    session.save()
                    yield {
                        **terminal,
                        **(
                            {"durationMs": round((anyio.current_time() - started_at) * 1000)}
                            if "durationMs" in terminal
                            else {}
                        ),
                        "sessionResumed": session.resumed,
                        "recoveryAttempts": attempt,
                        "resumeAvailable": bool(
                            session.native_id and terminal.get("type") == "error"
                        ),
                    }
                    return
                attempt += 1
                session.state["recoveryAttempt"] = attempt
                session.save()
                yield {
                    "type": "recovery",
                    "attempt": attempt,
                    "maxAttempts": len(RECOVERY_DELAYS),
                    "message": (
                        f"Cursor response interrupted. Resuming, attempt {attempt} "
                        f"of {len(RECOVERY_DELAYS)}."
                    ),
                    "partial": {
                        **(terminal.get("message") or {}),
                        **{
                            key: terminal[key]
                            for key in ("changes", "completedAt", "durationMs")
                            if key in terminal
                        },
                    },
                }
                if not await _recovery_delay(RECOVERY_DELAYS[attempt - 1], cancel):
                    yield {"type": "stopped"}
                    return
                session.resumed = True
                options["messages"] = [{"role": "user", "body": RECOVERY_PROMPT}]
        finally:
            try:
                if session and "recoveryAttempt" in session.state:
                    session.state.pop("recoveryAttempt")
                    session.save()
            finally:
                lease.close()

    return wrapped
