"""Phone controls retain desktop identity, policy and searchable saved history."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices.application import DeviceApplication
from gofer.ui.device_chat import validate_context
from gofer.ui.device_context import shared_context
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_workspace import share


def parity(
    app: DeviceApplication, peer: str, tmp_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    first = tmp_path / "notes"
    second = tmp_path / "code"
    first.mkdir()
    second.mkdir()
    meta = {
        "id": "desktop-thread",
        "title": "Notes",
        "projectRoot": str(first),
        "provider": "codex",
        "model": "model-a",
        "effort": "high",
        "permissionsByProvider": {"codex": "danger-full-access", "antigravity": "cli-managed"},
        "resources": {"shell": True},
        "mobileGroup": "pinned",
    }
    context = shared_context(
        meta,
        {
            "remThreads": {
                "projects": [
                    {"root": str(first), "name": "Notes"},
                    {"root": str(second), "name": "Code"},
                ]
            }
        },
        [
            {"id": "codex", "defaultPermissionMode": "workspace-write"},
            {"id": "antigravity", "defaultPermissionMode": "default"},
            {"id": "grok", "defaultPermissionMode": "cli-managed"},
        ],
        first,
    )
    app.workspace.enable(peer, True)
    shared = app.workspace.exchange(
        peer, meta, [{"id": "old", "role": "assistant", "body": "README is here"}], context, None
    )
    app.workspace.providers = [
        {"id": "codex", "models": ["model-a"], "efforts": {"model-a": ["high", "low"]}},
        {"id": "antigravity", "models": ["model-a"]},
        {"id": "grok", "models": ["model-a"]},
    ]
    return context, meta, shared, second


def test_same_permissions_and_open_project_catalog(application, tmp_path):
    app, peer = application
    context, _, _, _ = parity(app, peer, tmp_path)
    assert context["permission_mode"] == "danger-full-access"
    assert context["provider_permissions"]["antigravity"] == "cli-managed"
    assert context["provider_permissions"]["grok"] == "cli-managed"
    assert {p["name"] for p in app.workspace.catalog(peer)["projects"]} == {"Notes", "Code"}
    for provider, mode in [
        ("grok", "cli-managed"),
        ("antigravity", "cli-managed"),
        ("claude_code", "bypassPermissions"),
    ]:
        assert (
            validate_context({**context, "provider": provider, "permission_mode": mode})[
                "permission_mode"
            ]
            == mode
        )


def test_scope_effort_global_preserve_identity_history_resources(application, tmp_path):
    app, peer = application
    context, meta, shared, second = parity(app, peer, tmp_path)
    target = context["projects"][1]["id"]
    payload = {
        "expected_revision": 1,
        "project_id": target,
        "resource_ids": [],
        "provider": "codex",
        "model": "model-a",
        "effort": "low",
    }
    changed = app.handle(
        peer, event("thread.context.update", thread_id=shared["thread_id"], payload=payload)
    )[0]
    assert changed["type"] == "thread.snapshot"
    assert changed["payload"]["effort"] == "low"
    exported = app.workspace.export(peer, shared["thread_id"])
    assert exported["metadata"]["id"] == meta["id"]
    assert exported["metadata"]["projectRoot"] == str(second)
    assert exported["metadata"]["resources"] == meta["resources"]
    assert exported["messages"][0]["body"] == "README is here"
    payload.update(expected_revision=2, project_id=None)
    assert (
        app.handle(
            peer,
            event(
                "thread.context.update", sequence=1, thread_id=shared["thread_id"], payload=payload
            ),
        )[0]["type"]
        == "thread.snapshot"
    )
    assert app.workspace.export(peer, shared["thread_id"])["metadata"]["scopeMode"] == "global"
    assert (
        app.handle(peer, event(thread_id=shared["thread_id"], sequence=2))[0]["type"]
        == "chat.accepted"
    )
    claimed = app.claim()
    assert claimed["context"]["workflow"]["remThreads"]["global"] is True
    app.select_project(peer, shared["thread_id"], str(second))
    assert app.workspace.export(peer, shared["thread_id"])["metadata"]["projectRoot"] == str(second)


def test_invalid_scope_effort_and_stale_revision_rejected(application, tmp_path):
    app, peer = application
    context, _, shared, _ = parity(app, peer, tmp_path)
    payload = {
        "expected_revision": 1,
        "project_id": context["project_id"],
        "resource_ids": [],
        "provider": "codex",
        "model": "model-a",
        "effort": "high",
    }
    changes: list[dict[str, Any]] = [
        {"project_id": str(uuid4())},
        {"effort": "invented"},
        {"expected_revision": 0},
    ]
    for seq, change in enumerate(changes):
        result = app.handle(
            peer,
            event(
                "thread.context.update",
                sequence=seq,
                thread_id=shared["thread_id"],
                payload={**payload, **change},
            ),
        )[0]
        assert result["type"] == "error"
    assert app.workspace.export(peer, shared["thread_id"])["revision"] == 1


def test_refresh_does_not_cancel_shared_work_or_revoke_children(application, tmp_path):
    app, peer = application
    context, meta, shared, _ = parity(app, peer, tmp_path)
    request = event(thread_id=shared["thread_id"])
    app.handle(peer, request)
    app.claim()
    context["workflow"]["remResources"] = {"shell": True, "web": True}
    app.workspace.exchange(peer, meta, [], context, shared["revision"])
    assert app.runnable(peer, request["request_id"])
    app.revoke_grant(peer, shared["thread_id"])
    assert not app.runnable(peer, request["request_id"])


def test_cross_project_file_for_same_user_and_wire_thread(application, tmp_path):
    app, peer = application
    _, _, shared, second = parity(app, peer, tmp_path)
    readme = second / "README.md"
    readme.write_text("Project documentation")
    request = event(thread_id=shared["thread_id"])
    app.handle(peer, request)
    app.claim()
    offer = app.offer_file(peer, shared["thread_id"], str(readme), request["request_id"])
    assert offer["type"] == "file.offer"
    assert offer["thread_id"] == shared["thread_id"]
    assert offer["payload"]["name"] == "README.md"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    with pytest.raises(ValueError):
        app.offer_file(peer, shared["thread_id"], str(outside), request["request_id"])


def test_search_full_history_pagination_and_anchor(application, tmp_path):
    app, peer = application
    context, meta, shared = share(app, peer, tmp_path)
    body = "x" * 4100 + "the lost README match" + "x" * 6000
    messages = [{"id": "buried", "role": "assistant", "body": body}] + [
        {"id": f"recent-{i}", "role": "user", "body": "recent"} for i in range(80)
    ]
    app.workspace.exchange(peer, meta, messages, context, 1)
    result = app.handle(
        peer, event("thread.search.request", thread_id=None, payload={"query": "readme"})
    )[0]
    match = result["payload"]["results"][0]
    assert "README" in match["snippet"]
    history = app.handle(
        peer,
        event(
            "thread.history.request",
            sequence=1,
            thread_id=shared["thread_id"],
            payload={"before": None, "message_id": match["message_id"]},
        ),
    )[0]
    assert any("README" in m["text"] for m in history["payload"]["messages"])
    app.revoke_grant(peer, shared["thread_id"])
    assert not app.workspace.search(peer, {"query": "readme"})["results"]


def test_thread_groups_and_running_use_desktop_jobs(application, tmp_path):
    import threading

    from gofer.ui.chat_jobs import ChatJobs

    app, peer = application
    context, meta, shared, _ = parity(app, peer, tmp_path)
    jobs = ChatJobs(tmp_path / "jobs")
    done = threading.Event()
    app.local_chat_jobs = jobs
    try:
        jobs.start(meta["id"], str(uuid4()), lambda emit: done.wait(5))
        details = app.workspace.details(
            peer, shared["thread_id"], {**context, "desktop_thread_id": meta["id"]}
        )
        assert details["group"] == "pinned" and details["running"]
        response = app.handle(
            peer,
            event(
                "sync.request",
                thread_id=None,
                payload={"after_sequence": None, "limit": 100, "include_thread_details": True},
            ),
        )[-1]
        assert response["payload"]["threads"][0]["running"]
    finally:
        done.set()
        jobs.close()


def test_phone_permissions_are_editable_and_reflect_back_on_desktop(application, tmp_path):
    app, peer = application
    context, _, shared, _ = parity(app, peer, tmp_path)
    payload = {
        "expected_revision": 1,
        "project_id": context["project_id"],
        "resource_ids": [],
        "provider": "antigravity",
        "model": "model-a",
        "effort": "",
        "permission_mode": "cli-managed",
    }
    result = app.handle(
        peer, event("thread.context.update", thread_id=shared["thread_id"], payload=payload)
    )[0]
    assert result["payload"]["permission_mode"] == "cli-managed"
    exported = app.workspace.export(peer, shared["thread_id"])
    assert exported["metadata"]["permissionsByProvider"]["antigravity"] == "cli-managed"
    payload.update(expected_revision=2, permission_mode="invented")
    assert (
        app.handle(
            peer,
            event(
                "thread.context.update", sequence=1, thread_id=shared["thread_id"], payload=payload
            ),
        )[0]["type"]
        == "error"
    )


def test_global_phone_turn_selects_project_and_continues_with_desktop_policy(
    application, tmp_path, monkeypatch
):
    from gofer.ui import rem_threads
    from gofer.ui.chat_jobs import ChatJobs
    from gofer.ui.chat_steering import ChatSteering
    from gofer.ui.device_chat import DeviceChatBridge

    app, peer = application
    context, meta, shared, second = parity(app, peer, tmp_path)
    context = app.workspace.scope_context(context, None)
    app.workspace.exchange(peer, {**meta, "projectRoot": "", "scopeMode": "global"}, [], context, 1)
    instances = []

    class FakeTools:
        callback: Any = None

        def __init__(self) -> None:
            instances.append(self)

        def register(self, callback: Any, *args: Any) -> str:
            self.callback = callback
            return "http://127.0.0.1/disposable-tools"

        def revoke(self, url: str) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(rem_threads, "SwarmToolServer", FakeTools)
    calls = []

    async def source(**kwargs: Any) -> Any:
        calls.append((kwargs["permission_mode"], kwargs["workflow"]["projectRoot"]))
        if len(calls) == 1:
            instances[-1].callback("select_project", {"projectRoot": str(second)})
        yield {"type": "final", "message": {"body": "selected project response"}}

    jobs, steering = ChatJobs(tmp_path / "jobs"), ChatSteering(tmp_path / "jobs")
    bridge = DeviceChatBridge(app, jobs, steering, tmp_path, source=source)
    try:
        request = event(thread_id=shared["thread_id"])
        app.handle(peer, request)
        claimed = app.claim()
        bridge.dispatch(claimed)
        list(jobs.events(meta["id"], claimed["turn_id"]))
        assert calls == [("read-only", ""), ("danger-full-access", str(second))]
        exported = app.workspace.export(peer, shared["thread_id"])
        assert exported["metadata"]["id"] == meta["id"]
        assert exported["metadata"]["projectRoot"] == str(second)
        assert exported["messages"][-1]["body"] == "selected project response"
    finally:
        bridge.close()
        jobs.close()
        steering.close()


def test_bridge_file_tool_uses_paired_wire_id_not_desktop_native_id(tmp_path, monkeypatch):
    from gofer.ui import device_chat
    from gofer.ui.chat_jobs import ChatJobs
    from gofer.ui.chat_steering import ChatSteering
    from tests.unit.test_device_chat import Work, request

    captured = []

    async def wrap(
        source: Any, control: Any, *, file_scope: Any, read_only_override: Any, **options: Any
    ) -> Any:
        captured.append(file_scope)
        async for output in source(**options):
            yield output

    async def source(**kwargs: Any) -> Any:
        yield {"type": "final", "message": {"body": "response"}}

    monkeypatch.setattr(device_chat, "stream_with_fleet_tools", wrap)
    work = Work()
    jobs = ChatJobs(tmp_path)
    steering = ChatSteering(tmp_path)
    bridge = device_chat.DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    task["context"]["desktop_thread_id"] = "renderer-native-thread"
    try:
        bridge.dispatch(task)
        assert work.done.wait(5)
        list(jobs.events("renderer-native-thread", task["turn_id"]))
        assert captured == [(task["peer"], task["event"]["thread_id"], task["event"]["request_id"])]
    finally:
        bridge.close()
        jobs.close()
        steering.close()


def test_explicit_child_thread_inherits_settings_and_is_queued_once(application, tmp_path):
    app, peer = application
    context, meta, shared, second = parity(app, peer, tmp_path)
    request = event(thread_id=shared["thread_id"])
    app.handle(peer, request)
    app.claim()
    action = {
        "threadId": str(uuid4()),
        "projectRoot": str(second),
        "message": "Inspect this project",
    }
    app.spawn_thread(peer, request["request_id"], action)
    app.spawn_thread(peer, request["request_id"], {**action, "threadId": str(uuid4())})
    child = app.claim()
    assert child["context"]["project_path"] == str(second)
    assert child["context"]["permission_mode"] == context["permission_mode"]
    assert child["context"]["workflow"]["remThreads"]["spawned"]
    assert child["event"]["payload"]["text"] == action["message"]
    assert app.claim() is None
    exported = app.workspace.export(peer, child["event"]["thread_id"])
    assert exported["metadata"]["parentThreadId"] == meta["id"]
    assert exported["mobile_created"]
    app.revoke_grant(peer, shared["thread_id"])
    with pytest.raises(ValueError):
        app.spawn_thread(peer, request["request_id"], action)


def test_diagnostics_are_searchable_but_not_replayed_to_provider(application, tmp_path):
    app, peer = application
    context, meta, shared = share(app, peer, tmp_path)
    app.workspace.exchange(
        peer,
        meta,
        [
            {
                "id": "thought",
                "role": "assistant",
                "kind": "thought",
                "body": "A saved diagnostic needle",
            },
            {"id": "answer", "role": "assistant", "kind": "final", "body": "The answer"},
        ],
        context,
        1,
    )
    assert app.workspace.search(peer, {"query": "diagnostic needle"})["results"]
    assert [
        m["body"] for m in app.workspace.messages(peer, shared["thread_id"], for_dispatch=True)
    ] == ["The answer"]


def test_creation_uses_selected_global_scope_model_effort_and_permissions(application, tmp_path):
    app, peer = application
    context, _, shared, _ = parity(app, peer, tmp_path)
    payload = {
        "title": "New thread",
        "template_thread_id": shared["thread_id"],
        "project_id": None,
        "resource_ids": [],
        "provider": "codex",
        "model": "model-a",
        "effort": "low",
        "permission_mode": "read-only",
    }
    response = app.handle(peer, event("thread.create", thread_id=None, payload=payload))[0]
    assert response["type"] == "thread.snapshot"
    assert response["payload"]["project_id"] is None
    assert response["payload"]["effort"] == "low"
    assert response["payload"]["permission_mode"] == "read-only"
    remote = app.workspace.export(peer, response["thread_id"])
    assert remote["metadata"]["scopeMode"] == "global"
    assert remote["metadata"]["resources"] == {"shell": True}
    assert remote["metadata"]["projectRoot"] == ""
    invalid = {**payload, "permission_mode": "invented-mode"}
    rejected = app.handle(
        peer, event("thread.create", sequence=1, thread_id=None, payload=invalid)
    )[0]
    assert rejected["type"] == "error"
    unknown = {**payload, "project_id": str(uuid4())}
    assert (
        app.handle(peer, event("thread.create", sequence=2, thread_id=None, payload=unknown))[0][
            "type"
        ]
        == "error"
    )
