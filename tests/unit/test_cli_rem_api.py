"""The Rem, ticket and self-work commands, end to end against a real local backend.

The backend listens on a loopback port with an isolated data folder. Rem's service runs
with a stub decision generator and never starts tasks, so no provider CLI runs.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli import client as client_module
from gofer.cli.client import write_api_files
from gofer.cli.commands import _backend
from gofer.cli.main import app
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import new_ticket, put_ticket

runner = CliRunner()

CLIENT_ENV = (
    "RATICODE_URL",
    "RATICODE_SOCKET",
    "RATICODE_TOKEN",
    "RATICODE_TOKEN_FILE",
    "RATICODE_DATA_DIR",
    "GOFER_DATA_DIR",
    "GOFER_UI_API_TOKEN",
    "RATICODE_NON_INTERACTIVE",
    *_backend.AGENT_ENV,
)


def _sockets_available() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    if not _sockets_available():
        pytest.skip("local sockets are unavailable in this environment")
    from gofer.ui.server import create_server

    for name in CLIENT_ENV:
        monkeypatch.delenv(name, raising=False)
    # Organizations stays off, as on a new install, whatever this shell has set.
    monkeypatch.delenv("RATICODE_EXPERIMENTAL_ORGANIZATIONS", raising=False)
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    server = create_server(host="127.0.0.1", port=0, data_dir=data_dir, api_token="cli-token")

    async def no_provider(**_: Any) -> Any:
        yield {"type": "final", "message": {"body": "No live provider in this test"}}

    server.rem_home.close()
    service = RemHome(
        server.data_dir,
        source=no_provider,
        jobs=server.chat_jobs,
        steering=server.chat_steering,
        authorize=lambda config, recipe: None,
        generate=lambda prompt, config, cancel: {"reply": "Noted.", "tasks": [], "memories": []},
        start=False,
    )
    server.rem_home = service
    # The closed service keeps its lease until it expires; this one takes over now.
    with service.store.connect() as db:
        db.execute("DELETE FROM lease")
    assert service._lease()
    service.configure(
        {"provider": "codex", "projects": [{"root": str(project), "name": "project"}]}
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Stands in for Rem's worker: answers a pending chat message with the stub reply.
    # Like Rem's own loop, it also starts a message that arrived while Rem was still
    # replying; without that, a slow machine leaves it waiting forever.
    stop = threading.Event()

    def answer() -> None:
        while not stop.wait(0.05):
            service._promote_waiting()
            service._reply()

    replier = threading.Thread(target=answer, daemon=True)
    replier.start()
    port = int(server.server_address[1])
    write_api_files(data_dir, url=f"http://127.0.0.1:{port}", token="cli-token")
    monkeypatch.setenv("RATICODE_DATA_DIR", str(data_dir))
    sent: list[tuple[str, str]] = []
    original = client_module.ApiClient.request

    def counted(self: Any, method: str, path: str, **kwargs: Any) -> Any:
        sent.append((method, path))
        return original(self, method, path, **kwargs)

    monkeypatch.setattr(client_module.ApiClient, "request", counted)
    try:
        yield service, project, sent
    finally:
        stop.set()
        replier.join(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
        server.chat_jobs.close()
        server.chat_steering.close()
        server.generation_jobs.close()


def cli(*args: str, code: int = 0, stdin: str | None = None) -> Any:
    result = runner.invoke(app, list(args), input=stdin)
    assert result.exit_code == code, (result.stdout, result.stderr, result.exception)
    if result.stdout.strip().startswith("{"):
        envelope = json.loads(result.stdout.splitlines()[-1])
        assert envelope["ok"] is (code == 0)
        assert set(envelope) >= {"ok", "command", "data", "error", "warnings"}
        return envelope
    return result


def test_chat_send_wait_read_and_search(backend):
    service, _, _ = backend
    sent = cli("rem", "say", "Hello Rem", "--request-id", "m1")
    assert sent["command"] == "rem say" and sent["request_id"] == "m1"
    assert sent["data"]["request_id"] == "m1"
    # The same request ID is recognised, not stored twice.
    cli("rem", "say", "Hello Rem", "--request-id", "m1")
    waited = cli("rem", "say", "How are you?", "--wait", "--timeout", "30s")
    assert [m["body"] for m in waited["data"]["replies"]] == ["Noted."]
    page = cli("rem", "messages", "--tail", "3")["data"]
    assert [m["body"] for m in page["messages"]][-2:] == ["How are you?", "Noted."]
    later = cli("rem", "messages", "--after", str(page["last"]))["data"]
    assert later["messages"] == [] and later["last"] == page["last"]
    found = cli("rem", "search", "Hello")["data"]
    assert found["total"] == 1 and found["terms"] == ["Hello"]
    text = cli("rem", "messages", "--tail", "1", "--output", "text")
    assert "Noted." in text.stdout


def test_follow_prints_new_messages_as_json_lines(backend):
    service, _, _ = backend
    start = cli("rem", "messages", "--tail", "0")["data"]["last"]
    with service.store.connect() as db:
        service.store.message(db, "First update")
        service.store.message(db, "Second update")
    result = runner.invoke(
        app, ["rem", "follow", "--after", str(start), "--timeout", "1s", "--interval", "0.5"]
    )
    # Following ends at the timeout, which is exit 8 like `Raticode api --follow`.
    assert result.exit_code == 8, result.stderr
    *events, last = [json.loads(line) for line in result.stdout.splitlines()]
    assert [e["kind"] for e in events] == ["event", "event"]
    assert [e["data"]["body"] for e in events] == ["First update", "Second update"]
    assert last["kind"] == "result" and last["error"]["code"] == "timeout"
    assert last["data"] == {"events": 2}


def test_task_lifecycle_controls_revisions_and_wait(backend):
    service, project, _ = backend
    created = cli(
        "rem",
        "tasks",
        "create",
        "--title",
        "Remind",
        "--type",
        "reminder",
        "--prompt",
        "Call Alex",
        "--schedule",
        '{"kind": "daily", "local_time": "09:00", "timezone": "UTC"}',
        "--request-id",
        "t1",
    )["data"]["task"]
    identity = created["id"]
    listed = cli("rem", "tasks", "list")["data"]
    assert [t["id"] for t in listed] == [identity]
    shown = cli("rem", "tasks", "show", identity)["data"]
    stale = cli(
        "rem", "tasks", "edit", identity, "--expected-revision", "0", "--prompt", "x", code=4
    )
    assert stale["error"]["code"] == "conflict" and stale["error"]["http"] == 409
    edited = cli(
        "rem",
        "tasks",
        "edit",
        identity,
        "--expected-revision",
        str(shown["revision"]),
        "--prompt",
        "Call Jamie",
    )["data"]["task"]
    assert edited["prompt"] == "Call Jamie"
    paused = cli("rem", "tasks", "pause", identity)["data"]["task"]
    assert paused["state"] == "paused"
    resumed = cli(
        "rem", "tasks", "resume", identity, "--expected-revision", str(paused["revision"])
    )["data"]["task"]
    assert resumed["state"] == "enabled"
    run = cli("rem", "tasks", "run", identity)["data"]["run"]
    # No worker runs in this test, so the run stays queued and waiting times out.
    timed_out = cli(
        "rem", "tasks", "wait", identity, "--timeout", "1s", "--interval", "0.5", code=8
    )
    assert timed_out["error"]["code"] == "timeout"
    assert cli("rem", "runs", "show", run["id"])["data"]["state"] == "queued"
    cli("rem", "runs", "cancel", run["id"])
    deadline = time.monotonic() + 10
    while service.store.tasks()[0]["active_run"] and time.monotonic() < deadline:
        service.tick()
        time.sleep(0.05)
    stopped = cli("rem", "tasks", "wait", identity, "--timeout", "5s", code=1)
    assert stopped["error"]["code"] == "run_cancelled"
    assert stopped["data"]["state"] == "cancelled"
    assert cli("rem", "tasks", "delete", identity, code=2)["error"]["code"] == "invalid_input"
    cli("rem", "tasks", "delete", identity, "--yes")
    assert cli("rem", "tasks", "list")["data"] == []
    missing = cli("rem", "runs", "show", "nope", code=3)
    assert missing["error"]["http"] == 404


def test_tasks_can_wait_for_other_tasks(backend):
    service, _, _ = backend
    later = '{"kind": "once", "timezone": "UTC", "at": "2099-01-01T09:00:00+00:00"}'
    first = cli(
        "rem",
        "tasks",
        "create",
        "--title",
        "Audit",
        "--type",
        "reminder",
        "--prompt",
        "Audit",
        "--schedule",
        later,
    )["data"]["task"]
    second = cli(
        "rem",
        "tasks",
        "create",
        "--title",
        "Fix",
        "--type",
        "reminder",
        "--prompt",
        "Fix",
        "--after",
        first["id"],
    )["data"]["task"]
    assert second["prerequisites"][0]["task_id"] == first["id"]
    assert second["dependency"]["state"] == "waiting"
    assert second["active_run"] is None
    text = cli("rem", "tasks", "list", "--output", "text").stdout
    assert "waiting" in text and "Waiting for “Audit”" in text
    assert cli("rem", "tasks", "list", "--needs-you")["data"] == []
    looped = cli(
        "rem",
        "tasks",
        "edit",
        first["id"],
        "--expected-revision",
        str(first["revision"]),
        "--after",
        second["id"],
        code=2,
    )
    assert "wait for each other" in looped["error"]["message"]
    both = cli(
        "rem",
        "tasks",
        "edit",
        second["id"],
        "--expected-revision",
        "1",
        "--after",
        first["id"],
        "--clear-after",
        code=2,
    )
    assert both["error"]["code"] == "invalid_input"
    service.edit_task(first["id"], {"request_id": "drop-audit"}, "delete")
    held = cli("rem", "tasks", "list", "--needs-you")["data"]
    assert [t["id"] for t in held] == [second["id"]]
    assert held[0]["dependency"]["state"] == "held"
    assert "on_hold" in cli("rem", "tasks", "list", "--output", "text").stdout
    cleared = cli(
        "rem", "tasks", "edit", second["id"], "--expected-revision", "1", "--clear-after"
    )["data"]["task"]
    assert cleared["prerequisites"] == [] and "dependency" not in cleared
    assert cleared["next_occurrence_at"]


def test_wait_exit_codes_follow_the_run_outcome():
    assert _api_exit({"state": "succeeded"}) == 0
    assert _api_exit({"state": "blocked", "needs_user": True}) == 7
    assert _api_exit({"state": "failed"}) == 1
    assert _api_exit({"state": "outcome_unknown"}) == 8


def _api_exit(run: dict[str, Any]) -> int:
    from gofer.cli.commands.rem_api import _run_exit

    return _run_exit(run)


def test_memories(backend):
    added = cli("rem", "memories", "add", "Prefers short answers")["data"]
    assert added["mode"] == "on"
    (memory,) = added["memories"]
    cli("rem", "memories", "edit", memory["id"], "Prefers very short answers")
    assert cli("rem", "memories", "list")["data"]["memories"][0]["text"] == (
        "Prefers very short answers"
    )
    cli("rem", "memories", "mode", "paused")
    assert cli("rem", "memories", "list")["data"]["mode"] == "paused"
    cli("rem", "memories", "forget", memory["id"])
    assert cli("rem", "memories", "list")["data"]["memories"] == []
    assert cli("rem", "memories", "forget", memory["id"], code=3)["error"]["code"] == "not_found"


def test_notices_start_from_now_then_report_what_needs_you(backend):
    service, _, _ = backend
    first = cli("rem", "notices")["data"]
    assert first["notices"] == [] and first["cursor"].startswith("c1.")
    with service.store.connect() as db:
        service.store.message(
            db,
            "I need your sign-in to continue.",
            kind="task_update",
            work={"task_id": "t", "run_id": "r", "title": "Deploy", "needs_user": True},
        )
        service.store.message(db, "Just chatting.")
    found = cli("rem", "notices", "--cursor", first["cursor"])["data"]
    assert [(n["reason"], n["title"]) for n in found["notices"]] == [("needs_you", "Deploy")]
    again = cli("rem", "notices", "--cursor", found["cursor"])["data"]
    assert again["notices"] == []
    only_finished = cli("rem", "notices", "--cursor", first["cursor"], "--include", "finished")[
        "data"
    ]
    assert only_finished["notices"] == []
    bogus = cli("rem", "notices", "--cursor", "bogus", code=2)
    assert bogus["error"]["http"] == 400


def test_settings_export_apply_round_trip_and_agent_guard(backend, tmp_path, monkeypatch):
    service, project, _ = backend
    shown = cli("rem", "settings", "show")["data"]
    assert shown["context_ready"] is True
    assert shown["config"]["projects"][0]["root"] == str(project)
    target = tmp_path / "rem.json"
    exported = runner.invoke(app, ["rem", "settings", "export", "--file", str(target)])
    assert exported.exit_code == 0, exported.stderr
    config = json.loads(target.read_text())
    config["model"] = "gpt-test"
    target.write_text(json.dumps(config))
    cli("rem", "settings", "apply", "--file", str(target))
    assert cli("rem", "settings", "show")["data"]["config"]["model"] == "gpt-test"
    monkeypatch.setenv("RATICODE_COMMAND_ACTIVITY", str(tmp_path / "activity"))
    refused = cli("rem", "settings", "apply", "--file", str(target), code=5)
    assert refused["error"]["code"] == "user_only"


def _proposal(service: RemHome, project: Path) -> dict[str, Any]:
    with service.store.connect() as db:
        ticket = new_ticket(
            db,
            title="Idea from self-work",
            status="backlog",
            created_by="rem",
            project_dir=str(project),
            proposal={"state": "pending", "reason": "Looks useful", "round": "r1"},
        )
        put_ticket(db, ticket)
    return ticket


def test_tickets_create_edit_move_comment_with_revisions(backend):
    service, project, _ = backend
    board = cli(
        "tickets",
        "create",
        "--title",
        "Fix flaky test",
        "--project",
        str(project),
        "--status",
        "backlog",
        "--request-id",
        "k1",
    )["data"]["board"]
    (ticket,) = board["tickets"]
    assert ticket["key"] == "REM-1" and ticket["status"] == "backlog"
    # A retried request with the same ID adds nothing.
    cli(
        "tickets",
        "create",
        "--title",
        "Fix flaky test",
        "--project",
        str(project),
        "--status",
        "backlog",
        "--request-id",
        "k1",
    )
    assert len(cli("tickets", "list")["data"]["tickets"]) == 1
    conflict = cli(
        "tickets", "edit", "REM-1", "--expected-revision", "9", "--priority", "high", code=4
    )
    assert conflict["error"]["code"] == "conflict"
    cli("tickets", "edit", "REM-1", "--expected-revision", "1", "--priority", "high")
    shown = cli("tickets", "show", "REM-1")["data"]
    assert shown["ticket"]["priority"] == "high" and shown["ticket"]["revision"] == 2
    cli("tickets", "comment", "REM-1", "Seen on CI twice", "--request-id", "c1")
    cli("tickets", "comment", "REM-1", "Seen on CI twice", "--request-id", "c1")
    comments = cli("tickets", "comments", "REM-1")["data"]["comments"]
    assert [c["body"] for c in comments].count("Seen on CI twice") == 1
    cli("tickets", "move", "REM-1", "todo", "--expected-revision", "2")
    listed = cli("tickets", "list", "--status", "todo")["data"]["tickets"]
    assert [t["key"] for t in listed] == ["REM-1"]
    cli("tickets", "move", "REM-1", "done", "--expected-revision", "3")
    assert cli("tickets", "show", "REM-1")["data"]["ticket"]["status"] == "done"
    assert cli("tickets", "move", "REM-1", "sideways", "--expected-revision", "4", code=2)


def test_proposals_need_the_user_and_agents_cannot_decide_or_finish(backend, tmp_path, monkeypatch):
    service, project, sent = backend
    proposal = _proposal(service, project)
    key = "REM-1"
    assert [t["key"] for t in cli("tickets", "list", "--proposals")["data"]["tickets"]] == [key]
    # The app's rule holds through the CLI: an unapproved proposal can't be moved.
    refused = cli("tickets", "move", key, "todo", "--expected-revision", "1", code=2)
    assert "Approve" in refused["error"]["message"]
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    before = len(sent)
    for args in (
        ("tickets", "approve", key, "--expected-revision", "1"),
        ("tickets", "decline", key, "--expected-revision", "1"),
        ("tickets", "move", key, "done", "--expected-revision", "1"),
        ("tickets", "projects", str(project), "--expected-revision", "1"),
        ("selfwork", "set", "--expected-revision", "0", "--enabled"),
    ):
        assert cli(*args, code=5)["error"]["code"] == "user_only"
    assert len(sent) == before, "refused before any request was sent"
    monkeypatch.delenv("RATICODE_AGENT_SESSION")
    cli("tickets", "approve", key, "--expected-revision", "1")
    approved = cli("tickets", "show", proposal["id"])["data"]["ticket"]
    assert approved["status"] == "todo" and approved["proposed"] is False


def test_organization_ticket_needs_organizations(backend):
    refused = cli("tickets", "create", "--title", "x", "--org", "acme", code=2)
    assert refused["error"]["code"] == "unavailable"


def test_selfwork_status_set_export_apply_and_report(backend, tmp_path):
    status = cli("selfwork", "status")["data"]
    revision = status["settings"]["revision"]
    assert status["state"]["mode"] in {"off", "waiting", "paused"}
    assert (
        cli(
            "selfwork",
            "set",
            "--expected-revision",
            str(revision + 5),
            "--max-threads",
            "2",
            code=4,
        )["error"]["code"]
        == "conflict"
    )
    changed = cli(
        "selfwork",
        "set",
        "--expected-revision",
        str(revision),
        "--max-threads",
        "2",
        "--no-auto-pull-backlog",
    )["data"]
    assert changed["settings"]["max_threads"] == 2
    exported = runner.invoke(app, ["selfwork", "export"])
    settings = json.loads(exported.stdout)
    assert settings == {
        "enabled": changed["settings"]["enabled"],
        "max_threads": 2,
        "auto_pull_backlog": False,
    }
    settings["max_threads"] = 3
    target = tmp_path / "self-work.json"
    target.write_text(json.dumps(settings))
    applied = cli("selfwork", "apply", "--file", str(target))["data"]
    assert applied["settings"]["max_threads"] == 3
    target.write_text(json.dumps({"budget": 5}))
    assert cli("selfwork", "apply", "--file", str(target), code=2)
    report = cli("selfwork", "report", "--mark-seen")
    assert report["command"] == "selfwork report"


def test_errors_go_to_stderr_with_an_envelope_and_exit_code(backend, monkeypatch):
    monkeypatch.setenv("RATICODE_TOKEN", "wrong")
    result = runner.invoke(app, ["rem", "tasks", "list"])
    assert result.exit_code == 5
    assert json.loads(result.stdout)["error"]["http"] == 401
    assert "rejected" in result.stderr
    text = runner.invoke(app, ["rem", "tasks", "list", "--output", "text"])
    assert text.exit_code == 5 and text.stdout == ""
    assert text.stderr.startswith("Raticode rem tasks list:")


def test_unreachable_backend_exits_6(tmp_path, monkeypatch):
    for name in CLIENT_ENV:
        monkeypatch.delenv(name, raising=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("RATICODE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("RATICODE_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["selfwork", "status"])
    assert result.exit_code == 6
    assert json.loads(result.stdout)["error"]["code"] == "unreachable"
