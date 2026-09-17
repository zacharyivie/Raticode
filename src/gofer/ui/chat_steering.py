"""Rem-owned interruption and continuation, independent of provider sessions.

HTTP handlers run on separate threads and event loops. State transitions therefore
use a threading lock; no asyncio object crosses the HTTP request boundary.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gofer.ui.chat_media import CHAT_ATTACHMENT_MAX_COUNT, resolve_chat_attachment


class ChatSteeringConflict(ValueError):
    """The requested turn is stale or a request ID was reused with other text."""


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError(f"{name} must be 1-128 letters, digits, underscores or hyphens")
    return value


@dataclass
class ChatTurn:
    conversation_id: str
    turn_id: str
    provider: str
    model: str
    attachment_data_dir: Path | None = None
    generation: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    stopped: bool = False
    pending: list[str] = field(default_factory=list)
    delivering: list[str] = field(default_factory=list)


class ChatSteering:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.directory = data_dir / "chat-steering"
        self._lock = threading.RLock()
        self._active: dict[str, ChatTurn] = {}
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self._turn_ids: dict[str, set[str]] = {}
        self._ended: dict[tuple[str, str], str] = {}

    def _load(self, conversation_id: str) -> dict[str, dict[str, Any]]:
        if conversation_id not in self._records:
            path = self.directory / f"{conversation_id}.json"
            document = json.loads(path.read_text()) if path.exists() else {}
            records = document.get("receipts", {})
            self._turn_ids[conversation_id] = set(document.get("turnIds", []))
            # An interrupted server never silently restarts provider work.
            for receipt in records.values():
                if receipt["status"] == "interrupting":
                    receipt["status"] = "failed"
                    receipt["error"] = (
                        "Server stopped before delivery was confirmed; review before retry"
                    )
            self._records[conversation_id] = records
        return self._records[conversation_id]

    def _save(self, conversation_id: str, records: dict[str, dict[str, Any]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, filename = tempfile.mkstemp(dir=self.directory, prefix=".receipt-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"receipts": records, "turnIds": sorted(self._turn_ids[conversation_id])},
                    handle,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.directory / f"{conversation_id}.json")
            self._records[conversation_id] = records
        finally:
            Path(filename).unlink(missing_ok=True)

    def receipts(self, conversation_id: str) -> list[dict[str, Any]]:
        conversation_id = _identifier(conversation_id, "conversationId")
        with self._lock:
            self._reconcile(conversation_id)
            return copy.deepcopy(list(self._load(conversation_id).values()))

    def _reconcile(self, conversation_id: str) -> None:
        records = copy.deepcopy(self._load(conversation_id))
        active = self._active.get(conversation_id)
        changed = False
        for receipt in records.values():
            if receipt["status"] == "interrupting" and (
                active is None or receipt["turnId"] != active.turn_id or active.stopped
            ):
                receipt["status"] = self._ended.get(
                    (conversation_id, receipt["turnId"]),
                    "cancelled" if active is not None and active.stopped else "failed",
                )
                receipt["error"] = "Turn ended before delivery was confirmed; review before retry"
                changed = True
        if changed:
            self._save(conversation_id, records)

    def begin(
        self,
        conversation_id: str,
        turn_id: str,
        provider: str,
        model: str,
        *,
        data_dir: Path | None = None,
    ) -> ChatTurn:
        conversation_id = _identifier(conversation_id, "conversationId")
        turn_id = _identifier(turn_id, "turnId")
        with self._lock:
            if conversation_id in self._active:
                raise ChatSteeringConflict("This conversation already has an active turn")
            records = self._load(conversation_id)
            if turn_id in self._turn_ids[conversation_id]:
                raise ChatSteeringConflict("turnId has already been used; start with a fresh ID")
            self._turn_ids[conversation_id].add(turn_id)
            try:
                self._save(conversation_id, records)
            except OSError:
                self._turn_ids[conversation_id].discard(turn_id)
                raise
            turn = ChatTurn(conversation_id, turn_id, provider, model, attachment_data_dir=data_dir)
            self._active[conversation_id] = turn
            return turn

    def steer(self, body: dict[str, Any]) -> dict[str, Any]:
        conversation_id = _identifier(body.get("conversationId"), "conversationId")
        turn_id = _identifier(body.get("turnId"), "turnId")
        request_id = _identifier(body.get("requestId"), "requestId")
        text = body.get("text")
        attachments = copy.deepcopy(body.get("attachments", []))
        if not isinstance(attachments, list) or len(attachments) > CHAT_ATTACHMENT_MAX_COUNT:
            raise ValueError("Steering accepts up to five attachments")
        if not isinstance(text, str) or len(text) > 100_000 or not (text.strip() or attachments):
            raise ValueError("Steering needs text or attachments, with at most 100000 characters")
        with self._lock:
            self._reconcile(conversation_id)
            records = copy.deepcopy(self._load(conversation_id))
            if request_id in records:
                old = records[request_id]
                if (
                    old["text"] != text
                    or old["turnId"] != turn_id
                    or old.get("attachments", []) != attachments
                ):
                    raise ChatSteeringConflict(
                        "requestId was already used for a different instruction"
                    )
                return copy.deepcopy(old)
            turn = self._active.get(conversation_id)
            if turn is None or turn.turn_id != turn_id or turn.stopped:
                raise ChatSteeringConflict("The requested Rem turn is no longer active")
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    raise ValueError("Each steering attachment must be a file reference")
                resolve_chat_attachment(
                    attachment,
                    data_dir=turn.attachment_data_dir or self.data_dir,
                    thread_id=conversation_id,
                )
                if str(attachment.get("type", "")).startswith("image/") and turn.provider not in {
                    "codex",
                    "claude_code",
                }:
                    raise ValueError(
                        f"{turn.provider} image attachments are not supported by this adapter"
                    )
            receipt = {
                "conversationId": conversation_id,
                "turnId": turn_id,
                "requestId": request_id,
                "text": text,
                **({"attachments": attachments} if attachments else {}),
                "provider": turn.provider,
                "model": turn.model,
                "generation": turn.generation + 1,
                "mode": "restart",
                "status": "interrupting",
            }
            records[request_id] = receipt
            self._save(conversation_id, records)
            turn.pending.append(request_id)
            turn.cancel.set()
            return copy.deepcopy(receipt)

    def stop(self, conversation_id: str, turn_id: str) -> None:
        conversation_id = _identifier(conversation_id, "conversationId")
        turn_id = _identifier(turn_id, "turnId")
        with self._lock:
            turn = self._active.get(conversation_id)
            if turn is None or turn.turn_id != turn_id:
                raise ChatSteeringConflict("The requested Rem turn is no longer active")
            try:
                self._transition(turn, turn.pending + turn.delivering, "cancelled")
            finally:
                turn.stopped = True
                turn.cancel.set()

    def _transition(self, turn: ChatTurn, ids: list[str], status: str) -> list[dict[str, Any]]:
        records = copy.deepcopy(self._load(turn.conversation_id))
        changed = []
        for request_id in ids:
            if records[request_id]["status"] == "interrupting":
                records[request_id]["status"] = status
                if status == "delivered":
                    records[request_id]["generation"] = turn.generation
                changed.append(copy.deepcopy(records[request_id]))
        if changed:
            self._save(turn.conversation_id, records)
        return changed

    def close(self) -> None:
        with self._lock:
            for turn in self._active.values():
                turn.stopped = True
                turn.cancel.set()

    async def stream(
        self,
        turn: ChatTurn,
        factory: Callable[..., AsyncIterator[dict[str, Any]]],
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        messages = copy.deepcopy(kwargs.pop("messages", []))
        try:
            while True:
                yield {
                    "type": "turn",
                    "conversationId": turn.conversation_id,
                    "turnId": turn.turn_id,
                    "generation": turn.generation,
                    "provider": turn.provider,
                    "model": turn.model,
                }
                with self._lock:
                    stopped = turn.stopped
                if stopped:
                    yield {"type": "stopped", "turnId": turn.turn_id}
                    return
                terminal: dict[str, Any] | None = None
                partial: list[str] = []
                source = factory(messages=messages, cancel_event=turn.cancel, **kwargs)
                try:
                    async for event in source:
                        with self._lock:
                            restarting = bool(turn.pending) or turn.stopped
                            receipts = []
                            if not restarting and event.get("type") in {"thought", "final"}:
                                receipts = self._transition(turn, turn.delivering, "delivered")
                        for receipt in receipts:
                            yield {"type": "steering", "receipt": receipt}
                        # A receipt yields to the consumer. Stop or another steer may
                        # have arrived while that consumer persisted/rendered it.
                        with self._lock:
                            restarting = bool(turn.pending) or turn.stopped
                        if event.get("type") in {"final", "error"}:
                            terminal = event
                            # Closing the generator drains/terminates its process before restart.
                            break
                        if event.get("type") == "thought":
                            trace = event.get("trace") or {}
                            if trace.get("kind") in {"message", "summary"}:
                                partial.append(str(trace.get("body") or event.get("text") or ""))
                        if not restarting:
                            yield {**event, "generation": turn.generation, "turnId": turn.turn_id}
                finally:
                    close = getattr(source, "aclose", None)
                    if close is not None:
                        await close()
                with self._lock:
                    if turn.stopped:
                        receipts = self._transition(
                            turn, turn.pending + turn.delivering, "cancelled"
                        )
                        restart = False
                    elif turn.pending:
                        # Instructions already sent to the interrupted generation stay in
                        # the transcript. Do not append them again in the successor.
                        receipts = []
                        pending = turn.pending
                        turn.delivering += pending
                        turn.pending = []
                        records = self._load(turn.conversation_id)
                        final_body = (terminal or {}).get("message", {}).get("body")
                        if final_body or partial:
                            messages.append(
                                {"role": "assistant", "body": str(final_body or "\n".join(partial))}
                            )
                        messages.append(
                            {
                                "role": "system",
                                "body": (
                                    "The previous process was interrupted. Continue the same task "
                                    "using the following steering instructions. Completed tool "
                                    "effects remain in the workspace; inspect before repeating."
                                ),
                            }
                        )
                        messages.extend(
                            {
                                "role": "user",
                                "body": records[key]["text"],
                                **(
                                    {"attachments": copy.deepcopy(records[key]["attachments"])}
                                    if records[key].get("attachments")
                                    else {}
                                ),
                            }
                            for key in pending
                        )
                        turn.generation += 1
                        turn.cancel = threading.Event()
                        restart = True
                    else:
                        receipts = self._transition(turn, turn.delivering, "failed")
                        restart = False
                    if not restart:
                        # Atomic with steer(): completion either observes accepted text
                        # or removes the turn before a stale request can be accepted.
                        self._active.pop(turn.conversation_id, None)
                for receipt in receipts:
                    yield {"type": "steering", "receipt": receipt}
                if restart:
                    yield {
                        "type": "interrupted",
                        "turnId": turn.turn_id,
                        "generation": turn.generation - 1,
                        "partial": terminal,
                        "messages": copy.deepcopy(messages),
                    }
                    continue
                if turn.stopped:
                    yield {"type": "stopped", "turnId": turn.turn_id}
                elif terminal is not None:
                    yield {**terminal, "generation": turn.generation, "turnId": turn.turn_id}
                return
        finally:
            with self._lock:
                turn.cancel.set()
                self._ended[(turn.conversation_id, turn.turn_id)] = (
                    "cancelled" if turn.stopped else "failed"
                )
                if self._active.get(turn.conversation_id) is turn:
                    self._active.pop(turn.conversation_id, None)
                self._transition(
                    turn,
                    turn.pending + turn.delivering,
                    "cancelled" if turn.stopped else "failed",
                )
