from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from tests.unit import test_ui_server as harness


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server = harness._fake_server(tmp_path / "data")

    async def source(**kwargs: Any):
        yield {"type": "final", "message": {"body": "No live provider in this test"}}

    service = RemHome(
        server.data_dir,
        source=source,
        jobs=server.chat_jobs,
        steering=server.chat_steering,
        authorize=server._authorize_rem_home,
        start=False,
    )
    server.rem_home = service
    service._lease()
    monkeypatch.setattr(harness, "_fake_server", lambda *_args, **_kwargs: server)
    yield (
        lambda method, path, **options: harness._request(tmp_path, method, path, **options),
        service,
    )
    service.close()
    server.chat_jobs.close()
    server.chat_steering.close()
    server.generation_jobs.close()


def test_rem_api_task_controls_and_conflicts(api):
    request, service = api
    assert request("GET", "/api/rem/tasks", authenticated=False).status == 401
    assert request("GET", "/api/rem/conversation").json()["conversation"]["turn_state"] == "idle"
    recipe = {
        "request_id": "create",
        "title": "Remember",
        "type": "reminder",
        "prompt": "Call Alex",
        "schedule": {"kind": "daily", "local_time": "09:00", "timezone": "UTC"},
    }
    response = request("POST", "/api/rem/tasks", body=recipe)
    assert response.status == 200, response.json()
    task = response.json()["task"]
    assert request("POST", "/api/rem/tasks", body=recipe).json() == response.json()
    assert (
        request(
            "PATCH",
            f"/api/rem/tasks/{task['id']}",
            body={
                "request_id": "conflict",
                "expected_revision": 0,
                "prompt": "Other",
            },
        ).status
        == 409
    )
    edited = request(
        "PATCH",
        f"/api/rem/tasks/{task['id']}",
        body={
            "request_id": "edit",
            "expected_revision": 1,
            "prompt": "Call Jamie",
        },
    ).json()["task"]
    assert edited["prompt"] == "Call Jamie"
    paused = request("POST", f"/api/rem/tasks/{task['id']}/pause", body={"request_id": "pause"})
    assert paused.json()["task"]["state"] == "paused"
    resumed = request(
        "PATCH",
        f"/api/rem/tasks/{task['id']}",
        body={
            "request_id": "resume",
            "expected_revision": 3,
            "enabled": True,
        },
    )
    assert resumed.json()["task"]["state"] == "enabled"
    run = request("POST", f"/api/rem/tasks/{task['id']}/run", body={"request_id": "run"}).json()[
        "run"
    ]
    thread = request("GET", f"/api/rem/threads/{run['thread_id']}").json()
    assert thread["metadata"]["managedRunId"] == run["id"]
    assert (
        request("POST", "/api/chat/stream", body={"conversationId": run["thread_id"]}).status == 400
    )
    assert (
        request("POST", f"/api/rem/runs/{run['id']}/cancel", body={"request_id": "stop"}).json()[
            "run"
        ]["state"]
        == "cancelling"
    )
    service.tick()
    assert request("GET", f"/api/rem/runs/{run['id']}").json()["run"]["state"] == "cancelled"
    service._update(run["id"], state="blocked")
    assert request(
        "POST", f"/api/rem/runs/{run['id']}/acknowledge", body={"request_id": "ack"}
    ).json()["run"]["acknowledged"]
    assert request("DELETE", f"/api/rem/tasks/{task['id']}?request_id=delete").status == 200
    assert request("GET", "/api/rem/tasks").json()["tasks"] == []
    assert request("GET", "/api/rem/threads/missing").status == 404
    assert request("GET", "/api/rem/events?after=0").json()["events"]


def test_rem_reset_requires_authentication_and_confirmation(api):
    request, service = api
    identity = service.store.conversation()["conversation"]["id"]
    payload = {"request_id": "reset", "conversation_id": identity, "confirm": True}
    assert request("POST", "/api/rem/reset", body=payload, authenticated=False).status == 401
    assert request("POST", "/api/rem/reset", body={**payload, "confirm": False}).status == 400
    assert (
        request("POST", "/api/rem/reset", body={**payload, "conversation_id": "old"}).status == 409
    )
    result = request("POST", "/api/rem/reset", body=payload)
    assert result.status == 200, result.json()
    assert result.json()["conversation"]["id"] != identity
    assert request("GET", "/api/rem/tasks").json()["tasks"] == []


def test_rem_task_selection_survives_prompt_edits_and_later_runs(api):
    request, _service = api
    assert (
        request(
            "POST",
            "/api/rem/context",
            body={"provider": "codex", "model": "rem-model", "effort": "high"},
        ).status
        == 200
    )
    created = request(
        "POST",
        "/api/rem/tasks",
        body={
            "request_id": "cursor-auto",
            "title": "Discuss project status",
            "type": "errand",
            "execution_mode": "conversation",
            "prompt": "Give me a project status update",
            "provider": "cursor",
            "model": "auto",
            "schedule": {"kind": "once", "at": "2028-01-01T09:00:00+00:00"},
        },
    )
    assert created.status == 200, created.json()
    task = created.json()["task"]
    assert not task["active_run"]
    updated = request(
        "PATCH",
        f"/api/rem/tasks/{task['id']}",
        body={
            "request_id": "edit-prompt",
            "expected_revision": 1,
            "prompt": "Summarize the recent commits",
        },
    )
    assert updated.status == 200, updated.json()
    assert updated.json()["task"]["provider"] == "cursor"
    assert updated.json()["task"]["model"] == "auto"
    assert (
        request(
            "POST", "/api/rem/context", body={"provider": "grok", "model": "other-model"}
        ).status
        == 200
    )
    started = request("POST", f"/api/rem/tasks/{task['id']}/run", body={"request_id": "run-later"})
    assert started.status == 200, started.json()
    thread = request("GET", f"/api/rem/threads/{started.json()['run']['thread_id']}").json()
    assert thread["metadata"]["provider"] == "cursor"
    assert thread["metadata"]["model"] == "auto"
    assert thread["metadata"]["effort"] == ""
    assert request("GET", "/api/rem/conversation").json()["conversation"]["provider"] == "grok"


def test_rem_project_writes_require_current_desktop_grants(api, tmp_path: Path):
    request, service = api
    context = {"provider": "codex", "projects": [{"root": str(tmp_path)}]}
    denied = request("POST", "/api/rem/context", body=context)
    assert denied.status == 400
    assert "approved" in denied.json()["error"]
    with pytest.raises(ValueError):
        service.authorize(context, {"type": "develop", "project_dir": str(tmp_path)})
    assert (
        request("POST", "/api/rem/context", body={"provider": "codex", "brain": []}).status == 400
    )
    assert (
        request(
            "POST",
            "/api/rem/tasks",
            body={
                "request_id": "outside",
                "title": "Feature",
                "type": "develop",
                "prompt": "Do work",
                "project_dir": str(tmp_path),
            },
        ).status
        == 400
    )
    assert request("GET", "/api/rem/tasks").json()["tasks"] == []


def test_rem_messages_are_idempotent_and_followups_stay_managed(api):
    request, service = api
    message = {"request_id": "hello", "body": "Hello"}
    assert request("POST", "/api/rem/messages", body=message).status == 200
    assert request("POST", "/api/rem/messages", body=message).status == 200
    assert (
        request("POST", "/api/rem/messages", body={"request_id": "other", "body": "More"}).status
        == 409
    )
    assert len(request("GET", "/api/rem/conversation").json()["messages"]) == 1
    task = request(
        "POST",
        "/api/rem/tasks",
        body={
            "request_id": "reminder",
            "title": "Notice",
            "type": "reminder",
            "prompt": "Remember",
        },
    ).json()["task"]
    run = task["active_run"]
    instructed = request(
        "POST",
        f"/api/rem/threads/{run['thread_id']}/messages",
        body={
            "request_id": "followup",
            "body": "Remember tomorrow instead",
        },
    )
    assert instructed.status == 200
    assert instructed.json()["run"]["epoch"] == 1
    assert (
        request("POST", "/api/chat/steer", body={"conversationId": run["thread_id"]}).status == 400
    )


def test_rem_standalone_development_uses_managed_workspace_without_project_grants(api):
    request, service = api
    assert request("POST", "/api/rem/context", body={"provider": "codex"}).status == 200
    response = request(
        "POST",
        "/api/rem/tasks",
        body={
            "request_id": "standalone",
            "title": "YouTube script",
            "type": "develop",
            "prompt": "Build a standalone daily YouTube report script",
            "project_dir": None,
        },
    )
    assert response.status == 200, response.json()
    task = response.json()["task"]
    run = task["active_run"]
    assert service._work_root(run) == service.data_dir / "rem-work" / task["id"]
    metadata = request("GET", f"/api/rem/threads/{run['thread_id']}").json()["metadata"]
    assert metadata["scopeMode"] == "global" and metadata["projectRoot"] == ""
    assert (
        request(
            "PATCH",
            f"/api/rem/tasks/{task['id']}",
            body={
                "request_id": "edit-script",
                "expected_revision": 1,
                "prompt": "Use the report's transcript API",
            },
        ).status
        == 200
    )
    denied = request(
        "POST",
        "/api/rem/tasks",
        body={
            "request_id": "maintain",
            "title": "Maintenance",
            "type": "maintain",
            "prompt": "Repair the project",
        },
    )
    assert denied.status == 400
