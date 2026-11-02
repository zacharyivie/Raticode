"""Run a desktop Rem thread on another paired desktop.

The main desktop keeps the conversation. The chosen desktop runs each turn in a
thread it creates from its own local hosting grant, with its own projects,
provider sign-ins and approval policies. One pinned session per turn carries the
message, live progress from that desktop's turn journal, Stop, and the answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from gofer.devices.application import DeviceApplication, host_template
from gofer.devices.client import exchange

# The device protocol's chat.submit text limit.
TEXT_LIMIT = 4000
# Seconds between progress requests while a turn runs.
POLL = 1.0
# A session stays open while the other desktop keeps answering.
IDLE = 120.0
TERMINAL = frozenset({"completed", "failed", "cancelled", "outcome_unknown"})


class RemoteThreadError(Exception):
    """A user-facing reason the turn could not run on the other desktop."""


def _setup_hint(name: str) -> str:
    return (
        f"{name} has not allowed this computer to run threads there. On {name}, open "
        "Settings, Devices, find this computer and turn on Run threads from this "
        "desktop. Keep at least one project open there."
    )


class RemoteThreads:
    def __init__(self, control: Any) -> None:
        self.control = control

    def _app(self) -> DeviceApplication:
        app = self.control.application
        if not isinstance(app, DeviceApplication):
            raise RemoteThreadError("Device pairing is off on this computer.")
        return app

    def machine(self, peer: str) -> dict[str, Any]:
        """A directly paired, active desktop this computer can reach."""
        app = self._app()
        entry = next(
            (
                p
                for p in app.registry.list()
                if p["device_id"] == peer and p["role"] == "desktop" and p["state"] == "active"
            ),
            None,
        )
        if entry is None:
            raise RemoteThreadError("That computer is no longer paired with this one.")
        name = entry["name"] or "the other computer"
        if not self.control._reachable(peer):
            raise RemoteThreadError(
                f"This computer doesn't know {name}'s address yet. Open Raticode on {name} "
                "so the two computers can connect."
            )
        return {**entry, "name": name}

    async def _session(
        self,
        peer: str,
        request: dict[str, Any],
        *,
        done: Callable[[dict[str, Any]], bool],
        sink: Callable[[dict[str, Any]], None],
        ticker: Callable[[], Sequence[dict[str, Any]]] | None = None,
        trailer: Sequence[dict[str, Any]] = (),
    ) -> None:
        app = self._app()

        def record(event: dict[str, Any]) -> None:
            try:
                app.remote_result(peer, event)
            except ValueError:
                return
            sink(event)

        future = asyncio.run_coroutine_threadsafe(
            exchange(
                app.registry,
                peer,
                request,
                record,
                preface=[app.acknowledgment(peer)],
                trailer=[*trailer, app.advertisement(peer)],
                ticker=ticker,
                done=done,
                idle=IDLE,
            ),
            self.control.loop,
        )
        await asyncio.wrap_future(future)

    async def _ask(
        self, peer: str, kind: str, payload: dict[str, Any], answer: str, **ids: Any
    ) -> dict[str, Any]:
        event = self._app()._session_event(peer, kind, payload, **ids)
        answers: list[dict[str, Any]] = []

        def keep(record: dict[str, Any]) -> None:
            if record.get("request_id") == event["request_id"]:
                answers.append(record)

        try:
            await self._session(
                peer, event, done=lambda r: r["type"] in (answer, "error"), sink=keep
            )
        except (OSError, TimeoutError, ValueError) as exc:
            raise RemoteThreadError("Couldn't reach the other computer.") from exc
        result = next((r for r in answers if r["type"] in (answer, "error")), None)
        if result is None:
            raise RemoteThreadError("The other computer didn't answer.")
        return result

    async def ensure_thread(
        self,
        peer: str,
        local: str,
        *,
        name: str,
        title: str,
        project_name: str | None,
        provider: str,
        model: str,
        effort: str | None,
        permission_mode: str | None,
    ) -> tuple[str, str | None]:
        """The other desktop's thread for this conversation, created on first use."""
        app = self._app()
        existing = app.remote_thread(peer, local)
        if existing:
            return existing, None
        catalog = await self._ask(peer, "catalog.request", {}, "catalog.snapshot")
        if catalog["type"] == "error":
            raise RemoteThreadError(_setup_hint(name))
        projects = catalog["payload"]["projects"]
        if not projects:
            raise RemoteThreadError(_setup_hint(name))
        # The same repository usually has the same folder name on every computer.
        wanted = (project_name or "").casefold()
        chosen = next((p for p in projects if wanted and p["name"].casefold() == wanted), None)
        payload: dict[str, Any] = {
            "title": title[:160] or "New thread",
            "project_id": chosen["id"] if chosen else None,
            "resource_ids": [],
            "provider": provider,
            "model": model,
            "template_thread_id": host_template(app.registry.identity.device_id),
        }
        if effort:
            payload["effort"] = effort
        if permission_mode:
            payload["permission_mode"] = permission_mode
        # Retrying identical content reuses the thread the other desktop already made.
        request_id = str(
            uuid5(
                NAMESPACE_URL,
                "raticode-remote-thread:"
                + local
                + ":"
                + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            )
        )
        created = await self._ask(
            peer, "thread.create", payload, "thread.snapshot", request_id=request_id
        )
        if created["type"] == "error":
            detail = created["payload"]["detail"]
            if created["payload"]["code"] == "unauthorized":
                raise RemoteThreadError(_setup_hint(name))
            raise RemoteThreadError(f"{name}: {detail}")
        remote = created["thread_id"]
        app.save_remote_thread(peer, local, remote)
        where = f"the {chosen['name']} project" if chosen else "its first open project"
        return remote, f"Running on {name} in {where}."

    async def stream(
        self,
        *,
        machine: str,
        messages: list[dict[str, Any]],
        cancel_event: Any = None,
        conversation_id: str | None = None,
        provider: str = "codex",
        model: str = "cli-default",
        effort: str | None = None,
        permission_mode: str | None = None,
        workflow: dict[str, Any] | None = None,
        **_: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Chat stream events for one turn, in the same shapes a local provider yields."""
        started = time.monotonic()
        try:
            peer = machine
            target = self.machine(peer)
            name = target["name"]
            latest = next((m for m in reversed(messages) if m.get("role") == "user"), None)
            if latest is None:
                raise RemoteThreadError("There is no message to send.")
            if latest.get("attachments"):
                raise RemoteThreadError(
                    "Attachments aren't sent to other computers yet. Put the file in a "
                    f"project on {name} and mention its path instead."
                )
            text = str(latest.get("body") or latest.get("content") or "")
            if len(text) > TEXT_LIMIT:
                raise RemoteThreadError(
                    f"Messages to another computer can be up to {TEXT_LIMIT} characters. "
                    "Shorten this one or split it."
                )
            workflow = workflow or {}
            root = workflow.get("projectRoot") or ""
            scoped = not (workflow.get("remThreads") or {}).get("global") and root
            first = next((m for m in messages if m.get("role") == "user"), latest)
            title = " ".join(str(first.get("body") or "").split()[:8])
            remote, note = await self.ensure_thread(
                peer,
                str(conversation_id or workflow.get("chatThreadId") or uuid4()),
                name=name,
                title=title,
                project_name=Path(root).name if scoped else None,
                provider=provider,
                model=model,
                effort=effort,
                permission_mode=permission_mode,
            )
        except RemoteThreadError as exc:
            yield {"type": "error", "error": str(exc)}
            return
        if note:
            yield {"type": "thought", "text": note}
        async for event in self._turn(peer, name, remote, text, cancel_event, started):
            yield event

    async def _turn(
        self,
        peer: str,
        name: str,
        remote: str,
        text: str,
        cancel_event: Any,
        started: float,
    ) -> AsyncGenerator[dict[str, Any], None]:
        app = self._app()
        request_id = str(uuid4())
        submit = app._session_event(
            peer,
            "chat.submit",
            {"text": text, "attachment_ids": []},
            thread_id=remote,
            request_id=request_id,
        )
        inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        history_ids: set[str] = set()
        state: dict[str, Any] = {"polled": 0.0, "outstanding": None, "cancel_sent": False}

        def history() -> dict[str, Any]:
            event = app._session_event(
                peer,
                "thread.history.request",
                {"before": None, "limit": 40, "include_presentation": True},
                thread_id=remote,
            )
            history_ids.add(event["request_id"])
            return event

        def cancellation() -> dict[str, Any]:
            return app._session_event(
                peer,
                "request.cancel",
                {"reason": "Stopped on the main desktop"},
                thread_id=remote,
                request_id=request_id,
            )

        def ticker() -> list[dict[str, Any]]:
            # Runs on the device loop between reads of the open session.
            out = []
            if cancel_event is not None and cancel_event.is_set() and not state["cancel_sent"]:
                state["cancel_sent"] = True
                out.append(cancellation())
            now = time.monotonic()
            if state["outstanding"] is None and now - state["polled"] >= POLL:
                state["polled"] = now
                event = history()
                state["outstanding"] = event["request_id"]
                out.append(event)
            return out

        def sink(record: dict[str, Any]) -> None:
            if record.get("request_id") == request_id or record.get("request_id") in history_ids:
                if record.get("request_id") == state["outstanding"]:
                    state["outstanding"] = None
                inbox.put(record)

        def done(record: dict[str, Any]) -> bool:
            if record["type"] == "error":
                return True
            return bool(
                record["type"] == "request.status" and record["payload"]["status"] in TERMINAL
            )

        emitted: set[str] = set()
        reply: list[str] = []
        outcome: dict[str, Any] | None = None
        failures = 0
        finished = False
        session: asyncio.Future[None] | None = None
        try:
            while outcome is None:
                session = asyncio.ensure_future(
                    self._session(
                        peer, submit, done=done, sink=sink, ticker=ticker, trailer=[history()]
                    )
                )
                while True:
                    ended = session.done()
                    while not inbox.empty():
                        record = inbox.get()
                        kind = record["type"]
                        if kind == "thread.history.snapshot":
                            for event in self._progress(record, request_id, emitted, outcome):
                                yield event
                        elif kind == "chat.message" and record["request_id"] == request_id:
                            reply.append(record["payload"]["text"])
                        elif kind == "error" and record["request_id"] == request_id:
                            outcome = {"status": "failed", "detail": record["payload"]["detail"]}
                        elif kind == "request.status" and record["request_id"] == request_id:
                            if record["payload"]["status"] in TERMINAL:
                                outcome = dict(record["payload"])
                    if ended:
                        break
                    await asyncio.sleep(0.1)
                if outcome is not None:
                    break
                if session.exception() is not None:
                    failures += 1
                    if failures >= 3:
                        outcome = {
                            "status": "failed",
                            "detail": f"Lost the connection to {name}. The turn may still "
                            f"be running there.",
                        }
                        break
                    await asyncio.sleep(2)
            finished = True
        finally:
            if session is not None and not session.done():
                session.cancel()
            if not finished and not state["cancel_sent"]:
                # Closed early, e.g. by Stop: ask the other desktop to stop too.
                state["cancel_sent"] = True
                self._send_and_forget(peer, cancellation())
        status = outcome["status"]
        if status == "completed":
            yield {
                "type": "final",
                "message": {"role": "assistant", "body": "".join(reply)},
                "durationMs": int((time.monotonic() - started) * 1000),
                "completedAt": datetime.now(UTC).isoformat(),
            }
        elif status == "cancelled" and cancel_event is not None and cancel_event.is_set():
            return
        else:
            yield {"type": "error", "error": f"{name}: {outcome['detail']}"}

    def _progress(
        self,
        snapshot: dict[str, Any],
        request_id: str,
        emitted: set[str],
        outcome: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Thoughts and tool steps the other desktop recorded for this turn so far."""
        order: list[str] = []
        merged: dict[str, dict[str, Any]] = {}
        for part in snapshot["payload"]["messages"]:
            source = part.get("source_id", part["id"])
            if source not in merged:
                order.append(source)
                merged[source] = {
                    "role": part["role"],
                    "text": "",
                    **(part.get("presentation") or {}),
                }
            merged[source]["text"] += part["text"]
        if request_id in order:
            order = order[order.index(request_id) + 1 :]
        thoughts = [
            s for s in order if merged[s]["role"] != "user" and merged[s].get("kind") == "thought"
        ]
        events = []
        for index, source in enumerate(thoughts):
            message = merged[source]
            # A thought is final once a later step exists or the turn has ended.
            if source in emitted or (index == len(thoughts) - 1 and outcome is None):
                continue
            emitted.add(source)
            if not message["text"].strip() and not message.get("trace"):
                continue
            events.append(
                {
                    "type": "thought",
                    "text": message["text"],
                    **({"trace": message["trace"]} if message.get("trace") else {}),
                }
            )
        return events

    def _send_and_forget(self, peer: str, event: dict[str, Any]) -> None:
        app = self._app()

        def record(answer: dict[str, Any]) -> None:
            try:
                app.remote_result(peer, answer)
            except ValueError:
                pass

        asyncio.run_coroutine_threadsafe(
            exchange(
                app.registry,
                peer,
                event,
                record,
                preface=[app.acknowledgment(peer)],
                done=lambda r: (
                    r["type"] == "error"
                    or r["type"] == "request.status"
                    and r["payload"]["status"] in TERMINAL | {"cancel_requested"}
                ),
            ),
            self.control.loop,
        )
