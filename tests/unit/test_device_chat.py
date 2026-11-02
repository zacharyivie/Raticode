import asyncio
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.device_chat import DeviceChatBridge


class Work:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str, str | None]] = []
        self.recorded: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.done = threading.Event()
        self.granted = True

    def record_attachments(self, peer, request_id, attachments):
        self.recorded.append((peer, request_id, attachments))

    def claim(self):
        return None

    def authorized(self, peer, thread):
        return self.granted

    def runnable(self, peer, request_id):
        return self.granted

    def complete(self, peer, request_id, text, error=None):
        self.results.append((peer, request_id, text, error))
        self.done.set()


def request(tmp_path: Path) -> dict[str, Any]:
    return {
        "peer": str(uuid4()),
        "turn_id": str(uuid4()),
        "event": {
            "thread_id": str(uuid4()),
            "request_id": str(uuid4()),
            "payload": {
                "text": "question",
                "permission_mode": "danger-full-access",
                "project_path": "/untrusted",
            },
        },
        "context": {"provider": "codex", "model": "cli-default", "project_path": str(tmp_path)},
        "messages": [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "question"},
        ],
    }


def test_existing_jobs_run_once_with_local_context_and_history(tmp_path):
    captured = []

    async def source(**kwargs):
        captured.append(kwargs)
        yield {"type": "final", "message": {"body": "answer"}}

    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    bridge.dispatch(task)
    assert work.done.wait(5)
    list(jobs.events(task["event"]["thread_id"], task["turn_id"]))
    assert work.results[0][2:] == ("answer", None)
    assert captured[0]["permission_mode"] == "workspace-write"
    assert captured[0]["working_dir"] == tmp_path
    assert captured[0]["messages"][1] == {"role": "assistant", "body": "old answer"}
    bridge.dispatch(task)
    assert len(captured) == 1
    bridge.close()
    jobs.close()
    steering.close()


def test_invalid_policy_and_revocation_never_launch_provider(tmp_path):
    launched = []

    async def source(**kwargs):
        launched.append(kwargs)
        yield {"type": "final", "message": {"body": "should not happen"}}

    for mode, granted in [("invalid-policy", True), ("read-only", False)]:
        work = Work()
        work.granted = granted
        jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
        bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
        task = request(tmp_path)
        task["context"]["permission_mode"] = mode
        bridge.dispatch(task)
        assert work.done.wait(5)
        list(jobs.events(task["event"]["thread_id"], task["turn_id"]))
        assert work.results[0][3] == "dispatch_failed"
        bridge.close()
        jobs.close()
        steering.close()
    assert launched == []


async def test_revoking_grant_cancels_running_provider(tmp_path):
    started, cancelled = threading.Event(), threading.Event()

    async def source(**kwargs):
        started.set()
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        cancelled.set()
        yield {"type": "stopped"}

    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    bridge.dispatch(task)
    assert await asyncio.to_thread(started.wait, 5)
    runner = asyncio.create_task(bridge.run())
    work.granted = False
    assert await asyncio.to_thread(cancelled.wait, 5)
    assert await asyncio.to_thread(work.done.wait, 5)
    bridge.close()
    await runner
    jobs.close()
    steering.close()


async def test_dispatch_recovers_after_transient_claim_failure(tmp_path):
    class IntermittentWork(Work):
        attempts = 0

        def claim(self):
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("temporary storage failure")
            self.done.set()
            return None

    work = IntermittentWork()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path)
    runner = asyncio.create_task(bridge.run())
    assert await asyncio.to_thread(work.done.wait, 5)
    assert work.attempts >= 2
    bridge.close()
    await runner
    jobs.close()
    steering.close()


def test_phone_attachment_is_saved_with_the_desktop_thread(tmp_path):
    captured = []
    desktop_thread = "desktop-thread"

    async def source(**kwargs):
        captured.append(kwargs)
        assert "raticode_attachment" in kwargs["messages"][-1]["body"]
        yield {"type": "final", "message": {"body": "File read"}}

    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    task["context"]["desktop_thread_id"] = desktop_thread
    task["attachments"] = [
        {"id": "phone-file", "name": "sample.txt", "mime": "text/plain", "data": b"sample"}
    ]
    bridge.dispatch(task)
    assert work.done.wait(5)
    list(jobs.events(desktop_thread, task["turn_id"]))
    assert captured
    assert work.results[0][2:] == ("File read", None)
    # The file outlives the turn, under the desktop thread, so later desktop
    # turns that resend this message can still read it.
    files = list((tmp_path / "chat-attachments" / desktop_thread).iterdir())
    assert [path.read_bytes() for path in files] == [b"sample"]
    ((peer, request_id, saved),) = work.recorded
    assert (peer, request_id) == (task["peer"], task["event"]["request_id"])
    assert saved == [
        {
            "id": "phone-file",
            "name": "sample.txt",
            "size": 6,
            "type": "text/plain",
            "mime": "text/plain",
            "storageName": files[0].name,
        }
    ]
    bridge.close()
    jobs.close()
    steering.close()


def test_earlier_phone_attachments_reach_later_turns(tmp_path):
    from gofer.ui.chat_media import store_chat_attachments

    captured = []

    async def source(**kwargs):
        captured.append(kwargs)
        yield {"type": "final", "message": {"body": "ok"}}

    thread = "desktop-thread"
    stored = store_chat_attachments(
        {"threadId": thread, "files": [{"name": "photo.png", "type": "image/png", "data": ""}]},
        tmp_path,
    )["attachments"]
    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    task["context"]["desktop_thread_id"] = thread
    task["messages"] = [
        {"role": "user", "content": "look at this", "attachments": stored},
        {"role": "user", "content": "lost", "attachments": [{"name": "gone.png"}]},
        {"role": "assistant", "content": "seen"},
        {"role": "user", "content": "what was in the photo?"},
    ]
    bridge.dispatch(task)
    assert work.done.wait(5)
    list(jobs.events(thread, task["turn_id"]))
    first, lost = captured[0]["messages"][:2]
    assert (
        f'path="{tmp_path / "chat-attachments" / thread / stored[0]["storageName"]}"'
        in (first["body"])
    )
    assert "[Attachment no longer available: gone.png]" in lost["body"]
    bridge.close()
    jobs.close()
    steering.close()


def test_fleet_permission_requires_an_explicit_boolean(tmp_path):
    import pytest

    from gofer.ui.device_chat import validate_context

    context = request(tmp_path)["context"]
    for value in ("true", "false", 1, None):
        with pytest.raises(ValueError, match="invalid_fleet_permission"):
            validate_context({**context, "fleet_execute": value})
    assert validate_context({**context, "fleet_execute": True})["fleet_execute"] is True
    assert validate_context({**context, "fleet_execute": False})["fleet_execute"] is False


@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
def test_additional_remote_providers_keep_existing_default_policy(tmp_path, provider):
    from gofer.ui.device_chat import validate_context

    context = request(tmp_path)["context"]
    assert (
        validate_context({**context, "provider": provider, "permission_mode": "default"})[
            "provider"
        ]
        == provider
    )
    with pytest.raises(ValueError, match="unsupported_provider_permission"):
        validate_context({**context, "provider": provider, "permission_mode": "danger-full-access"})
