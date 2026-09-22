import json

from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.device_transcript import project_device_turn
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_workspace import share


def test_local_replay_preserves_trace_and_final_ids_without_leaking_to_phone(application, tmp_path):
    app, peer = application
    context, metadata, shared = share(app, peer, tmp_path)
    request = event(thread_id=shared["thread_id"])
    app.handle(peer, request)
    claimed = app.claim()
    jobs = app.local_chat_jobs = ChatJobs(tmp_path)
    path = jobs._path(metadata["id"], claimed["turn_id"])
    path.parent.mkdir(parents=True)
    trace = {"kind": "reasoning", "title": "Check the layout"}
    events = [
        {
            "sequence": 1,
            "type": "thought",
            "text": "Checking ",
            "deltaStreamId": "one",
            "trace": trace,
        },
        {"sequence": 2, "type": "thought", "text": "the layout.", "deltaStreamId": "one"},
    ]
    before = app.workspace.sync_token(peer, shared["thread_id"])
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    assert before != app.workspace.sync_token(peer, shared["thread_id"])
    running = app.workspace.export(peer, shared["thread_id"])
    thought = next(m for m in running["messages"] if m.get("kind") == "thought")
    assert thought["body"] == "Checking the layout."
    assert thought["trace"] == trace
    assert running["running"]
    assert running["messages"][-1]["running"]
    # Only authenticated local desktop export contains diagnostics.
    assert all(
        "Checking" not in m["body"] for m in app.workspace.messages(peer, shared["thread_id"])
    )
    events.append(
        {"sequence": 3, "type": "final", "message": {"body": "Finished"}, "durationMs": 4000}
    )
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    app.complete(peer, request["request_id"], "Finished")
    completed = app.workspace.export(peer, shared["thread_id"])
    assert not completed["running"]
    assert completed["messages"][-1]["running"] is False
    assert sum(m["id"] == "reply-" + request["request_id"] for m in completed["messages"]) == 1
    assert (
        next(m for m in completed["messages"] if m["id"] == request["request_id"])["origin"]
        == "phone"
    )
    assert (
        next(m for m in completed["messages"] if m.get("kind") == "thought")["id"] == thought["id"]
    )
    # Closing/reopening the backend still reads the existing journal, without execution.
    assert ChatJobs(tmp_path).snapshot(metadata["id"], claimed["turn_id"]) == events
    app.workspace.exchange(peer, metadata, [], context, completed["revision"])
    assert app.workspace.export(peer, shared["thread_id"])["messages"] == []


def test_stopped_or_failed_turn_clears_running_marker():
    for kind in ("stopped", "error"):
        messages = project_device_turn(
            "request",
            [
                {"sequence": 1, "type": "thought", "text": "Working"},
                {"sequence": 2, "type": kind, "error": "Provider stopped"},
            ],
            "failed",
        )
        assert messages[-1]["id"] == "phone-summary-request"
        assert messages[-1]["running"] is False


def test_snapshot_ignores_partial_tail_without_waiting(tmp_path):
    jobs = ChatJobs(tmp_path)
    path = jobs._path("thread", "turn")
    path.parent.mkdir(parents=True)
    path.write_text('{"sequence":1,"type":"thought","text":"Saved"}\n{"sequence":2')
    assert len(jobs.snapshot("thread", "turn")) == 1
    assert jobs.snapshot("thread", "missing") == []


def test_phone_history_preserves_groups_trace_timing_and_legacy_shape(application, tmp_path):
    app, peer = application
    context, metadata, shared = share(app, peer, tmp_path)
    request = event(thread_id=shared["thread_id"])
    app.handle(peer, request)
    claimed = app.claim()
    jobs = app.local_chat_jobs = ChatJobs(tmp_path)
    path = jobs._path(metadata["id"], claimed["turn_id"])
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(
            json.dumps(e) + "\n"
            for e in [
                {"sequence": 1, "type": "thought", "text": "**Check the layout**"},
                {
                    "sequence": 2,
                    "type": "thought",
                    "text": "bash",
                    "trace": {
                        "id": "tool-1",
                        "kind": "tool",
                        "title": "bash",
                        "command": "pwd",
                        "status": "running",
                    },
                },
                {
                    "sequence": 3,
                    "type": "thought",
                    "text": "bash",
                    "trace": {
                        "id": "tool-1",
                        "kind": "tool",
                        "title": "Tool result",
                        "output": "/fixture",
                        "status": "completed",
                    },
                },
                {"sequence": 4, "type": "final", "message": {"body": "Done"}, "durationMs": 1234},
            ]
        )
    )
    app.complete(peer, request["request_id"], "Done")
    rich = app.handle(
        peer,
        event(
            "thread.history.request",
            sequence=1,
            thread_id=shared["thread_id"],
            payload={"before": None, "include_presentation": True},
        ),
    )[0]["payload"]
    thoughts = [m for m in rich["messages"] if m["presentation"].get("kind") == "thought"]
    assert len(thoughts) == 3
    assert len({m["presentation"]["groupId"] for m in thoughts}) == 1
    assert thoughts[-1]["presentation"]["trace"]["output"] == "/fixture"
    assert rich["messages"][-1]["presentation"]["durationMs"] == 1234
    assert rich["total"] == len(rich["messages"])
    legacy = app.handle(
        peer,
        event(
            "thread.history.request",
            sequence=2,
            thread_id=shared["thread_id"],
            payload={"before": None},
        ),
    )[0]["payload"]
    assert all(set(m) == {"id", "role", "text"} for m in legacy["messages"])
    assert all("presentation" not in m for m in legacy["messages"])


def test_renderer_exchange_keeps_presentation_and_bounds_large_wire_details(application, tmp_path):
    app, peer = application
    context, metadata, shared = share(app, peer, tmp_path)
    messages = [
        {
            "id": "thinking",
            "role": "assistant",
            "body": "**Review**",
            "kind": "thought",
            "groupId": "desktop-turn",
            "trace": {"id": "tool", "kind": "tool", "title": "bash", "output": "🚀" * 20000},
        },
        {
            "id": "summary",
            "role": "assistant",
            "body": "",
            "kind": "turn-summary",
            "running": False,
            "durationMs": 2500,
        },
    ]
    app.workspace.exchange(peer, metadata, messages, context, shared["revision"])
    rich = app.handle(
        peer,
        event(
            "thread.history.request",
            thread_id=shared["thread_id"],
            payload={"before": None, "include_presentation": True},
        ),
    )[0]
    assert len(json.dumps(rich).encode()) < 65536
    thought = rich["payload"]["messages"][0]["presentation"]
    assert thought["groupId"] == "desktop-turn"
    assert thought["truncated"]
    assert rich["payload"]["messages"][-1]["presentation"]["durationMs"] == 2500
