from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_store import RUN_INTERNALS
from tests.unit import test_ui_server as harness
from tests.unit.test_rem_home import force_run


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
    force_run(service, run["id"], state="blocked")
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
    # An unapproved project is left out without stopping the rest of Rem.
    pruned = request("POST", "/api/rem/context", body=context)
    assert pruned.status == 200, pruned.json()
    assert "approved" in pruned.json()["warnings"][0]
    with service.store.read() as db:
        assert service.store.setting(db, "config")["projects"] == []
    assert service.has_context
    assert request("GET", "/api/rem/conversation").json()["context_ready"] is True
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
    # A message sent while Rem replies waits for its turn instead of being refused.
    other = request("POST", "/api/rem/messages", body={"request_id": "other", "body": "More"})
    assert other.status == 200 and other.json()["waiting"] is True
    conversation = request("GET", "/api/rem/conversation").json()
    assert len(conversation["messages"]) == 1
    assert [m["id"] for m in conversation["waiting"]] == ["other"]
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


def test_rem_memories_can_be_listed_corrected_and_forgotten(api):
    request, service = api
    with service.store.connect() as db:
        service.store.set_setting(
            db,
            "memories",
            {
                "old": {"text": "Uses Google OAuth.", "created_at": "2026-10-01T00:00:00+00:00"},
                "new": {"text": "Digest at 06:00.", "created_at": "2026-10-08T00:00:00+00:00"},
            },
        )
    assert request("GET", "/api/rem/memories", authenticated=False).status == 401
    listed = request("GET", "/api/rem/memories").json()["memories"]
    assert [m["id"] for m in listed] == ["new", "old"]

    edited = request("PATCH", "/api/rem/memories/old", body={"text": "  Never use Google OAuth.  "})
    assert edited.status == 200, edited.json()
    old = next(m for m in edited.json()["memories"] if m["id"] == "old")
    assert old["text"] == "Never use Google OAuth." and old["edited_at"]
    assert request("PATCH", "/api/rem/memories/old", body={"text": " "}).status == 400
    assert request("PATCH", "/api/rem/memories/old", body={"text": "x" * 2001}).status == 400
    assert request("PATCH", "/api/rem/memories/missing", body={"text": "x"}).status == 404

    forgotten = request("DELETE", "/api/rem/memories/new")
    assert [m["id"] for m in forgotten.json()["memories"]] == ["old"]
    assert request("DELETE", "/api/rem/memories/new").status == 404
    with service.store.read() as db:
        assert set(service.store.setting(db, "memories")) == {"old"}


def test_rem_api_retries_only_an_interrupted_review(api):
    request, service = api
    task = request(
        "POST",
        "/api/rem/tasks",
        body={
            "request_id": "create-retry",
            "title": "Check notes",
            "type": "errand",
            "prompt": "Check notes",
            "schedule": {"kind": "daily", "local_time": "09:00"},
        },
    ).json()["task"]
    run = request(
        "POST", f"/api/rem/tasks/{task['id']}/run", body={"request_id": "run-retry"}
    ).json()["run"]
    service._update(run["id"], state="succeeded")
    last = request("GET", "/api/rem/tasks").json()["tasks"][0]["last_run"]
    assert last["review_retryable"] is False
    refused = request(
        "POST", f"/api/rem/runs/{run['id']}/retry_review", body={"request_id": "early"}
    )
    assert refused.status == 409
    # The implementation finished but its review stopped: Retry review is offered and works once.
    force_run(
        service,
        run["id"],
        state="blocked",
        blocked_phase="verifying",
        worker_result="Implemented.",
        verification_thread_id="review-thread",
    )
    last = request("GET", "/api/rem/tasks").json()["tasks"][0]["last_run"]
    assert last["review_retryable"] is True
    retried = request(
        "POST", f"/api/rem/runs/{run['id']}/retry_review", body={"request_id": "retry"}
    )
    assert retried.status == 200
    tasks = request("GET", "/api/rem/tasks").json()["tasks"]
    assert tasks[0]["active_run"]["id"] == run["id"]
    assert tasks[0]["last_run"].get("review_retryable") is not True


def test_rem_memory_api_adds_switches_modes_and_undoes_a_reply(api):
    request, service = api
    assert (
        request("POST", "/api/rem/memories", body={"text": "x"}, authenticated=False).status == 401
    )
    added = request("POST", "/api/rem/memories", body={"text": "Works in Eastern time"})
    assert added.status == 200, added.json()
    assert [m["text"] for m in added.json()["memories"]] == ["Works in Eastern time"]
    assert request("POST", "/api/rem/memories", body={"text": "api key: abcd1234"}).status == 400

    assert (
        request("PUT", "/api/rem/memory-mode", body={"mode": "paused"}).json()["mode"] == "paused"
    )
    assert request("PUT", "/api/rem/memory-mode", body={"mode": "maybe"}).status == 400
    assert request("GET", "/api/rem/memories").json()["mode"] == "paused"

    with service.store.connect() as db:
        service.store.message(
            db,
            "Saved.",
            identity="reply-1",
            memory={"saved": [{"id": "new", "text": "Likes tea"}]},
        )
        saved = service.store.setting(db, "memories")
        saved["new"] = {"text": "Likes tea", "created_at": "2026-11-01T00:00:00+00:00"}
        service.store.set_setting(db, "memories", saved)
    undone = request("POST", "/api/rem/messages/reply-1/undo-memory")
    assert undone.status == 200, undone.json()
    assert [m["text"] for m in undone.json()["memories"]] == ["Works in Eastern time"]
    assert request("POST", "/api/rem/messages/missing/undo-memory").status == 404


def test_rem_polls_get_run_summaries_and_unchanged_answers(api):
    request, service = api
    task = request(
        "POST",
        "/api/rem/tasks",
        body={
            "request_id": "create-poll",
            "title": "Check notes",
            "type": "errand",
            "prompt": "Check notes",
            "schedule": {"kind": "daily", "local_time": "09:00"},
        },
    ).json()["task"]
    run = request(
        "POST", f"/api/rem/tasks/{task['id']}/run", body={"request_id": "run-poll"}
    ).json()["run"]
    traces = [{"type": "tool", "output": "x" * 50_000}]
    service._update(run["id"], worker_traces=traces, review_traces=traces, worker_result="Done.")

    # The list the desktop polls leaves out traces and worker internals.
    listed = request("GET", "/api/rem/tasks").json()
    view = listed["tasks"][0]
    for shown in (view["active_run"], view["last_run"], *view["recent_runs"]):
        assert shown["id"] == run["id"] and shown["thread_id"] == run["thread_id"]
        assert not set(RUN_INTERNALS) & set(shown)
    assert len(json.dumps(listed)) < 20_000
    # Rem's own turns still read the whole run.
    assert service.store.tasks()[0]["active_run"]["worker_traces"] == traces

    # A client that has the current version gets a short answer instead.
    version = listed["version"]
    assert request("GET", f"/api/rem/tasks?known={version}").json() == {
        "unchanged": True,
        "version": version,
    }
    service._update(run["id"], phase_label="Second pass after review")
    changed = request("GET", f"/api/rem/tasks?known={version}").json()
    assert changed["version"] != version
    assert changed["tasks"][0]["active_run"]["phase_label"] == "Second pass after review"

    conversation = request("GET", "/api/rem/conversation").json()
    assert request("GET", f"/api/rem/conversation?known={conversation['version']}").json() == {
        "unchanged": True,
        "version": conversation["version"],
        "context_ready": conversation["context_ready"],
    }

    thread_path = f"/api/rem/threads/{run['thread_id']}"
    thread = request("GET", thread_path).json()
    assert request("GET", f"{thread_path}?known={thread['version']}").json()["unchanged"] is True
    service.store.append_thread(
        run["thread_id"], {"id": "progress", "role": "assistant", "body": "Halfway"}
    )
    grown = request("GET", f"{thread_path}?known={thread['version']}").json()
    assert grown["messages"][-1]["body"] == "Halfway"
    assert grown["version"] != thread["version"]


def test_rem_polls_can_send_their_version_in_a_header(api):
    request, _service = api
    tasks = request("GET", "/api/rem/tasks").json()
    # A fixed address lets the browser reuse its cross-origin check between polls.
    assert request("GET", "/api/rem/tasks", headers={"X-Gofer-Known": tasks["version"]}).json() == {
        "unchanged": True,
        "version": tasks["version"],
    }
    conversation = request("GET", "/api/rem/conversation").json()
    unchanged = request(
        "GET", "/api/rem/conversation", headers={"X-Gofer-Known": conversation["version"]}
    ).json()
    assert unchanged["unchanged"] is True and "messages" not in unchanged
    # A stale version still gets the full view.
    assert "tasks" in request("GET", "/api/rem/tasks", headers={"X-Gofer-Known": "old"}).json()


def test_a_deleted_open_project_only_stops_tasks_that_work_in_it(api):
    """Removing a worktree that was open as a project used to stop every Rem task."""
    _request, service = api
    root = service.data_dir / "projects"
    task_folder, removed = root / "repo", root / "repo-worktree"
    task_folder.mkdir(parents=True)
    projects = [{"root": str(task_folder)}, {"root": str(removed)}]
    # The removed folder is skipped; the task's own folder is still checked.
    service.authorize({"projects": projects}, {"type": "develop", "project_dir": str(task_folder)})
    service.authorize({"projects": projects}, {})
    with pytest.raises(ValueError, match="project folder no longer exists"):
        service.authorize({"projects": projects}, {"type": "develop", "project_dir": str(removed)})
    # Folders that exist but aren't canonical are still refused, including links.
    removed.symlink_to(task_folder)
    with pytest.raises(ValueError, match="canonical existing directories"):
        service.authorize({"projects": projects}, {})
    removed.unlink()
    removed.symlink_to(root / "missing")
    with pytest.raises(ValueError, match="canonical existing directories"):
        service.authorize({"projects": projects}, {})
    with pytest.raises(ValueError, match="canonical existing directories"):
        service.authorize({"projects": [{"root": "relative/path"}]}, {})


def test_projects_rem_opens_stay_usable_until_the_window_lists_them(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A folder Rem opens gets the backend's own grant and is reported to the window."""
    import gofer.ui.server as server_module
    from gofer.ui.rem_projects import ProjectOpen

    request, service = api
    server = service.authorize.__self__  # type: ignore[attr-defined]
    service.grant = lambda root, grant_id: server.path_grants.register(
        root, grant_id, persistent=True
    )
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    demo = tmp_path / "user" / "Projects" / "demo"
    service.open_projects([ProjectOpen(path=str(demo), create=True)])
    task = {"type": "develop", "project_dir": str(demo)}
    service.authorize(service._current_config(), task)

    # A window context without the folder keeps it, with a grant that doesn't lapse.
    assert request("POST", "/api/rem/context", body={"provider": "codex"}).status == 200
    later = server_module.time.monotonic() + 24 * 3600
    monkeypatch.setattr(server_module.time, "monotonic", lambda: later)
    service.authorize(service._current_config(), task)

    first = request("GET", "/api/rem/conversation").json()
    assert first["opened_projects"] == [{"root": str(demo), "name": "demo"}]
    unchanged = request(
        "GET", "/api/rem/conversation", headers={"X-Gofer-Known": first["version"]}
    ).json()
    assert unchanged["unchanged"] is True
    assert unchanged["opened_projects"] == first["opened_projects"]

    # The window lists it with its own grant; Rem stops reporting it.
    window_grant = server.path_grants.register(demo)
    context = {"provider": "codex", "projects": [{"root": str(demo), "grantId": window_grant}]}
    assert request("POST", "/api/rem/context", body=context).status == 200
    assert "opened_projects" not in request("GET", "/api/rem/conversation").json()
    assert [(p["root"], p["grantId"]) for p in service._current_config()["projects"]] == [
        (str(demo), window_grant)
    ]


def test_polled_runs_name_each_check_they_answer(api):
    """Task lists leave out run recipes, so each check result carries its own description."""
    request, service = api
    task = request(
        "POST",
        "/api/rem/tasks",
        body={
            "request_id": "create-checks",
            "title": "Export",
            "type": "errand",
            "prompt": "Export the runs",
            "schedule": {"kind": "daily", "local_time": "09:00"},
            "acceptance_criteria": [
                {"description": "Rows match the filter", "authored_by": "user"},
                {"description": "Empty lists export a header", "authored_by": "user"},
            ],
        },
    ).json()["task"]
    first, second = (c["id"] for c in task["acceptance_criteria"])
    run = request(
        "POST", f"/api/rem/tasks/{task['id']}/run", body={"request_id": "run-checks"}
    ).json()["run"]
    service._update(
        run["id"],
        criteria=[
            {"id": first, "result": "fail", "evidence": "All 48 rows were exported."},
            {"id": second, "result": "unknown", "evidence": "Not accepted: no receipts."},
            {"id": "gone", "result": "pass", "evidence": "A check from nowhere."},
        ],
    )
    shown = request("GET", "/api/rem/tasks").json()["tasks"][0]["active_run"]
    assert "recipe" not in shown
    assert shown["criteria"] == [
        {
            "id": first,
            "result": "fail",
            "evidence": "All 48 rows were exported.",
            "description": "Rows match the filter",
        },
        {
            "id": second,
            "result": "unknown",
            "evidence": "Not accepted: no receipts.",
            "description": "Empty lists export a header",
        },
        # A check found nowhere keeps its result without a description.
        {"id": "gone", "result": "pass", "evidence": "A check from nowhere."},
    ]

    # Editing the checks mid-run resets them to the new ones, named from the waiting edit.
    edited = request(
        "PATCH",
        f"/api/rem/tasks/{task['id']}",
        body={
            "request_id": "edit-checks",
            "expected_revision": task["revision"],
            "acceptance_criteria": [{"description": "Opens in a spreadsheet"}],
        },
    ).json()["task"]
    [(new, words)] = [(c["id"], c["description"]) for c in edited["acceptance_criteria"]]
    assert words == "Opens in a spreadsheet"
    shown = request("GET", "/api/rem/tasks").json()["tasks"][0]["active_run"]
    assert shown["criteria"] == [{"id": new, "result": "unknown", "description": words}]
