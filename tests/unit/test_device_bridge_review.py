"""Project grant and prelaunch cancellation regressions; no real provider calls."""

import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.device_chat import DeviceChatBridge, validate_context


def test_grant_normalizes_symlink_and_retains_original_project(tmp_path: Path) -> None:
    original, other, alias = tmp_path / "original", tmp_path / "other", tmp_path / "alias"
    original.mkdir()
    other.mkdir()
    alias.symlink_to(original, target_is_directory=True)
    context = validate_context(
        {"provider": "codex", "model": "cli-default", "project_path": str(alias)}
    )
    assert context["project_path"] == str(original)
    alias.unlink()
    alias.symlink_to(other, target_is_directory=True)
    assert validate_context(context)["project_path"] == str(original)
    with pytest.raises(ValueError, match="changed_since_authorization"):
        validate_context(context | {"project_path": str(alias)})


def test_replaced_project_directory_requires_new_grant(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    context = validate_context(
        {"provider": "codex", "model": "cli-default", "project_path": str(project)}
    )
    project.rename(tmp_path / "previous-project")
    project.mkdir()
    with pytest.raises(ValueError, match="changed_since_authorization"):
        validate_context(context)


class CancelledWork:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.error: str | None = None

    def claim(self) -> dict[str, Any] | None:
        return None

    def authorized(self, peer: str, thread: str) -> bool:
        return True  # Replacement grant still exists for the same thread.

    def runnable(self, peer: str, request_id: str) -> bool:
        return False  # This particular claimed request was cancelled.

    def complete(self, peer: str, request_id: str, text: str, error: str | None = None) -> None:
        self.error = error
        self.done.set()


def test_cancelled_claim_cannot_launch_under_replacement_grant(tmp_path: Path) -> None:
    launched = []

    async def source(**kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        launched.append(kwargs)
        yield {"type": "final", "message": {"body": "must never launch"}}

    work = CancelledWork()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    request: dict[str, Any] = {
        "peer": str(uuid4()),
        "turn_id": str(uuid4()),
        "event": {
            "thread_id": str(uuid4()),
            "request_id": str(uuid4()),
            "payload": {"text": "cancelled"},
        },
        "context": validate_context(
            {"provider": "codex", "model": "cli-default", "project_path": str(tmp_path)}
        ),
    }
    try:
        bridge.dispatch(request)
        assert work.done.wait(5)
        list(jobs.events(request["event"]["thread_id"], request["turn_id"]))
        assert work.error == "dispatch_failed"
        assert launched == []
    finally:
        bridge.close()
        jobs.close()
        steering.close()
