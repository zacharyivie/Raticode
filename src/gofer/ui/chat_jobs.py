"""Backend-owned Rem jobs with disk-backed, reconnectable event streams."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from gofer.ui.chat_steering import _identifier


class ChatJobs:
    def __init__(self, data_dir: Path, max_active: int = 8) -> None:
        self.directory = data_dir / "chat-jobs"
        self._condition = threading.Condition()
        self._active: set[tuple[str, str]] = set()
        self._closed = False
        self.max_active = max_active

    def _path(self, conversation_id: str, turn_id: str) -> Path:
        conversation_id = _identifier(conversation_id, "conversationId")
        turn_id = _identifier(turn_id, "turnId")
        return self.directory / conversation_id / f"{turn_id}.jsonl"

    def start(
        self,
        conversation_id: str,
        turn_id: str,
        run: Callable[[Callable[[dict[str, Any]], None]], None],
    ) -> None:
        path = self._path(conversation_id, turn_id)
        key = (conversation_id, turn_id)
        with self._condition:
            if self._closed or len(self._active) >= self.max_active:
                raise ValueError("Too many active Rem turns, or backend is stopping")
            if any(item[0] == conversation_id for item in self._active):
                raise ValueError("This conversation already has an active turn")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Exclusive creation prevents retries from launching provider work twice.
            handle = path.open("x", encoding="utf-8")
            self._active.add(key)

        def worker() -> None:
            sequence = 0

            def emit(event: dict[str, Any]) -> None:
                nonlocal sequence
                sequence += 1
                with self._condition:
                    handle.write(json.dumps({**event, "sequence": sequence}) + "\n")
                    handle.flush()
                    self._condition.notify_all()

            try:
                run(emit)
            except Exception as exc:  # noqa: BLE001
                emit({"type": "error", "error": str(exc)})
            finally:
                handle.close()
                with self._condition:
                    self._active.discard(key)
                    self._condition.notify_all()

        threading.Thread(target=worker, name=f"rem-{turn_id}", daemon=True).start()

    def events(
        self, conversation_id: str, turn_id: str, after: int = 0
    ) -> Generator[dict[str, Any], None, None]:
        path = self._path(conversation_id, turn_id)
        if after < 0:
            raise ValueError("after must be nonnegative")
        # Open before returning the iterator so missing turns fail before HTTP headers.
        handle = path.open(encoding="utf-8")

        def follow() -> Generator[dict[str, Any], None, None]:
            terminal = False
            with handle:
                while True:
                    with self._condition:
                        line = handle.readline()
                        if not line:
                            if self._closed or (conversation_id, turn_id) not in self._active:
                                break
                            self._condition.wait(timeout=1)
                            continue
                    event = json.loads(line)
                    terminal = event.get("type") in {"final", "error", "stopped"}
                    if event["sequence"] > after:
                        yield event
                if not terminal:
                    yield {"type": "error", "error": "Backend stopped before this turn completed"}

        return follow()

    def revision(self, conversation_id: str, turn_id: str) -> str:
        """Cheap local journal revision, including completion without another event."""
        try:
            stat = self._path(conversation_id, turn_id).stat()
        except FileNotFoundError:
            return "missing"
        with self._condition:
            active = (conversation_id, turn_id) in self._active
        return f"{stat.st_size}:{stat.st_mtime_ns}:{active}"

    def snapshot(self, conversation_id: str, turn_id: str) -> list[dict[str, Any]]:
        """Read already committed events without waiting for a running provider.

        Used only by the authenticated local desktop conversation mirror. These
        diagnostics are never part of the phone protocol or provider context.
        """
        try:
            with self._condition:
                with self._path(conversation_id, turn_id).open(encoding="utf-8") as handle:
                    return [json.loads(line) for line in handle if line.endswith("\n")]
        except FileNotFoundError:
            return []

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def active_snapshot(self) -> list[dict[str, str]]:
        """Current backend work identities, without prompt text or resource paths."""
        with self._condition:
            return [
                {"thread_id": conversation, "turn_id": turn, "state": "running"}
                for conversation, turn in sorted(self._active)
            ]
