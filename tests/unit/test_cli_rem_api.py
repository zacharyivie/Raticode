"""The Rem, ticket and self-work commands, end to end against a real local backend.

The backend listens on a loopback port with an isolated data folder. Rem's service runs
with a stub decision generator and never starts tasks, so no provider CLI runs.
"""

from __future__ import annotations

import json
import socket
import sys
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


def test_tasks_create_pins_a_task_to_a_computer_and_refuses_a_develop_task_elsewhere(backend):
    service, project, _ = backend
    desktop = "c70c43e6-38b4-4c64-8c20-61397f412d22"
    pinned = cli(
        "rem",
        "tasks",
        "create",
        "--title",
        "Find the slowdown",
        "--type",
        "research",
        "--prompt",
        "Why did startup slow down?",
        "--project",
        str(project),
        "--computer",
        desktop,
        "--request-id",
        "pin-1",
    )["data"]["task"]
    assert pinned["computer"] == desktop
    refused = cli(
        "rem",
        "tasks",
        "create",
        "--title",
        "Fix it",
        "--type",
        "develop",
        "--prompt",
        "Fix the bug",
        "--project",
        str(project),
        "--computer",
        desktop,
        "--request-id",
        "pin-2",
        code=2,
    )
    assert refused["error"]["http"] == 400
    assert (
        "Only research and conversation tasks can run on another computer"
        in (refused["error"]["message"])
    )


def test_acknowledge_all_dismisses_every_update_but_keeps_questions(backend):
    from tests.unit.test_rem_home import force_run

    service, _, _ = backend
    runs = {}
    for title in ("Failed", "Question"):
        created = cli(
            "rem",
            "tasks",
            "create",
            "--title",
            title,
            "--type",
            "errand",
            "--prompt",
            "Check",
            "--schedule",
            '{"kind": "daily", "local_time": "09:00", "timezone": "UTC"}',
        )["data"]["task"]
        runs[title] = cli("rem", "tasks", "run", created["id"])["data"]["run"]["id"]
    force_run(service, runs["Failed"], state="failed", needs_user=False)
    force_run(service, runs["Question"], state="blocked", needs_user=True)
    text = cli("rem", "runs", "acknowledge-all", "--output", "text").stdout
    assert "Dismissed 1 task update. 1 still needs you." in text
    assert cli("rem", "runs", "show", runs["Failed"])["data"]["acknowledged"] is True
    assert not cli("rem", "runs", "show", runs["Question"])["data"].get("acknowledged")
    again = cli("rem", "runs", "acknowledge-all")
    assert again["command"] == "rem runs acknowledge-all"
    assert again["data"]["count"] == 0 and again["data"]["needs_you"] == 1


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


def test_memories_take_an_end_date_and_keep_removes_it(backend):
    added = cli("rem", "memories", "add", "In Oslo for the summit", "--until", "2099-06-30")
    (memory,) = added["data"]["memories"]
    assert memory["until"].startswith("2099-06-30T23:59:59") and memory["ended"] is False
    edited = cli("rem", "memories", "edit", memory["id"], "--until", "2099-07-01T09:00:00+02:00")
    assert edited["data"]["memories"][0]["until"] == "2099-07-01T09:00:00+02:00"
    kept = cli("rem", "memories", "edit", memory["id"], "--keep")["data"]["memories"][0]
    assert "until" not in kept
    # A date and time without a timezone is refused, as for organization memories.
    refused = cli("rem", "memories", "edit", memory["id"], "--until", "2099-07-01T09:00", code=2)
    assert "needs a timezone" in refused["error"]["message"]
    both = cli("rem", "memories", "edit", memory["id"], "--until", "2099-07-01", "--keep", code=2)
    assert both["error"]["code"] == "invalid_input"
    text = cli("rem", "memories", "add", "Trip", "--until", "2099-01-01", "--output", "text")
    assert "(until 2099-01-01)" in text.stdout


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


def test_notices_report_a_workflow_phone_notice(backend):
    """A run's notification node with channel: phone shows up as a workflow alert."""
    import anyio

    from gofer.rattish.compiler import CompileContext, RattishCompiler
    from gofer.rattish.workflow_runtime import execute_workflow

    service, project, _ = backend
    first = cli("rem", "notices", "--include", "workflow")["data"]
    assert first["notices"] == [] and first["include"] == ["workflow"]
    rattish = Path(__file__).parents[2] / "rattish"
    compiler = RattishCompiler.from_paths(
        schema_root=rattish / "schemas",
        contract_paths=[rattish / "contracts" / "notification.json"],
    )
    source = """Rattish: 1
Workflow:
  name: Phone notice
Node notify:
  type: notification
  channel: phone
  title: Backup finished
  body: 12 files copied.
"""
    ir = compiler.compile(source, CompileContext("phone-notice", project)).ir
    result = anyio.run(
        lambda: execute_workflow(ir, data_dir=service.data_dir, run_id="run-phone-1")
    )
    assert result.latest_node_outputs["notify"]["sent"] is True
    found = cli("rem", "notices", "--cursor", first["cursor"], "--include", "workflow")["data"]
    assert [
        (n["reason"], n["kind"], n["title"], n["body"], n["workflow_id"], n["run_id"])
        for n in found["notices"]
    ] == [
        (
            "workflow",
            "workflow",
            "Backup finished",
            "12 files copied.",
            "phone-notice",
            "run-phone-1",
        )
    ]
    text = cli(
        "rem", "notices", "--cursor", first["cursor"], "--include", "workflow", "--output", "text"
    ).stdout
    assert "workflow: Backup finished" in text
    # The default includes workflow alerts; naming another switch leaves them out.
    assert cli("rem", "notices", "--cursor", first["cursor"])["data"]["notices"]
    assert not cli("rem", "notices", "--cursor", first["cursor"], "--include", "finished")["data"][
        "notices"
    ]
    # A cursor from before workflow alerts still reads, starting the workflow part from now.
    legacy = ".".join(first["cursor"].split(".")[:4])
    assert cli("rem", "notices", "--cursor", legacy)["data"]["notices"] == []


def test_notices_say_how_many_workflow_notices_were_dropped(backend, monkeypatch):
    """Notices pruned from the computer's store before a read show up as dropped."""
    from gofer.core import phone_alerts

    service, _, _ = backend
    monkeypatch.setattr(phone_alerts, "KEEP", 2)
    store = phone_alerts.PhoneAlertStore(Path(service.data_dir))
    first = cli("rem", "notices", "--include", "workflow")["data"]
    assert first["dropped"] == 0
    for index in range(5):
        store.add(title=f"t{index}", body="", workflow_id="wf", run_id=f"r{index}")
    found = cli("rem", "notices", "--cursor", first["cursor"], "--include", "workflow")["data"]
    assert [n["title"] for n in found["notices"]] == ["t3", "t4"]
    assert found["dropped"] == 3
    text = cli(
        "rem", "notices", "--cursor", first["cursor"], "--include", "workflow", "--output", "text"
    ).stdout
    assert "workflow: 3 workflow messages were removed before this read" in text
    # Only a read that includes workflow alerts hears about the gap.
    assert (
        cli("rem", "notices", "--cursor", first["cursor"], "--include", "finished")["data"][
            "dropped"
        ]
        == 0
    )


def test_settings_export_apply_round_trip_and_agent_guard(backend, tmp_path, monkeypatch):
    service, project, _ = backend
    shown = cli("rem", "settings", "show")["data"]
    assert shown["context_ready"] is True
    assert shown["config"]["projects"][0]["root"] == str(project)
    target = tmp_path / "rem.json"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o644)
    exported = runner.invoke(app, ["rem", "settings", "export", "--file", str(target)])
    assert exported.exit_code == 0, exported.stderr
    if sys.platform != "win32":
        # It can name MCP servers and folders: only this account may read it.
        assert target.stat().st_mode & 0o777 == 0o600
        linked = tmp_path / "linked.json"
        linked.symlink_to(tmp_path / "elsewhere.json")
        refused = runner.invoke(app, ["rem", "settings", "export", "--file", str(linked)])
        assert refused.exit_code != 0
        assert not (tmp_path / "elsewhere.json").exists()
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


def test_tickets_wait_for_other_tickets(backend):
    service, project, _ = backend
    cli("tickets", "create", "--title", "Polling triggers", "--request-id", "w1")
    cli("tickets", "create", "--title", "Webhooks", "--request-id", "w2")
    created = cli(
        "tickets",
        "create",
        "--title",
        "Starter workflows",
        "--waits-for",
        "REM-1",
        "--waits-for",
        "REM-2",
        "--request-id",
        "w3",
    )["data"]["board"]
    starters = next(t for t in created["tickets"] if t["key"] == "REM-3")
    assert [w["key"] for w in starters["waits_for"]] == ["REM-1", "REM-2"]
    assert starters["waiting"] is True
    shown = cli("tickets", "show", "REM-3", "--output", "text")
    assert "Waits for: REM-1 Polling triggers (todo); REM-2 Webhooks (todo)" in shown.stdout
    listed = cli("tickets", "list", "--output", "text")
    assert "Starter workflows (waits for REM-1, REM-2)" in listed.stdout
    # A loop is refused with the path that would close it.
    looped = cli(
        "tickets", "edit", "REM-1", "--expected-revision", "1", "--waits-for", "REM-3", code=2
    )
    assert "REM-1 → REM-3 → REM-1" in looped["error"]["message"]
    # Replacing the list, then clearing it.
    cli("tickets", "edit", "REM-3", "--expected-revision", "1", "--waits-for", "REM-2")
    assert [w["key"] for w in cli("tickets", "show", "REM-3")["data"]["ticket"]["waits_for"]] == [
        "REM-2"
    ]
    assert cli(
        "tickets",
        "edit",
        "REM-3",
        "--expected-revision",
        "2",
        "--waits-for",
        "REM-1",
        "--clear-waits",
        code=2,
    )
    cli("tickets", "edit", "REM-3", "--expected-revision", "2", "--clear-waits")
    assert cli("tickets", "show", "REM-3")["data"]["ticket"]["waits_for"] == []
    # A cancelled ticket it waits for shows a notice.
    cli("tickets", "edit", "REM-3", "--expected-revision", "3", "--waits-for", "REM-2")
    cli("tickets", "move", "REM-2", "cancelled", "--expected-revision", "1")
    notice = cli("tickets", "show", "REM-3", "--output", "text").stdout
    assert "Notice: REM-2 “Webhooks” was cancelled" in notice


def test_tickets_show_which_decisions_a_ticket_violates(backend):
    service, project, _ = backend
    reason = "Violates decisions: D-4 (How are workflows written?): it mentions “toml workflows”."
    with service.store.connect() as db:
        ticket = new_ticket(
            db,
            title="Support TOML workflows",
            status="blocked",
            project_dir=str(project),
        )
        ticket["block_reason"] = reason
        ticket["violates_decisions"] = {"ids": ["D-4"], "reason": reason, "from": "todo"}
        put_ticket(db, ticket)
        put_ticket(db, new_ticket(db, title="Docs", status="todo", project_dir=str(project)))
    listed = cli("tickets", "list", "--output", "text").stdout
    assert "Support TOML workflows (violates D-4)" in listed
    assert "Docs (violates" not in listed
    shown = cli("tickets", "show", "REM-1", "--output", "text").stdout
    assert reason in shown
    data = cli("tickets", "show", "REM-1")["data"]["ticket"]
    assert data["violates_decisions"] == ["D-4"] and data["violation_reason"] == reason
    assert cli("tickets", "show", "REM-2")["data"]["ticket"]["violates_decisions"] == []


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
        "--session-token-limit",
        "500000",
        "--daily-token-limit",
        "2000000",
        "--rate-limit-percent",
        "80",
        "--daily-allowance-percent",
        "11",
    )["data"]
    assert changed["settings"]["max_threads"] == 2
    assert changed["settings"]["daily_allowance_percent"] == 11
    assert changed["state"]["daily_allowance"]["cap_percent"] == 11
    exported = runner.invoke(app, ["selfwork", "export"])
    settings = json.loads(exported.stdout)
    assert settings == {
        "enabled": changed["settings"]["enabled"],
        "max_threads": 2,
        "auto_pull_backlog": False,
        "session_token_limit": 500_000,
        "daily_token_limit": 2_000_000,
        "rate_limit_percent": 80,
        "daily_allowance_percent": 11,
        "allowance_mode": "daily",
        "pace_aim_percent": 97,
        "pace_lead_hours": 3,
        "auto_approve_proposals": False,
        "provider_accounts": {},
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
    # Pace to reset replaces the daily cap: one choice, with its aim and finish.
    revision = applied["settings"]["revision"]
    paced = cli(
        "selfwork",
        "set",
        "--expected-revision",
        str(revision),
        "--allowance-mode",
        "pace",
        "--pace-aim",
        "50",
        "--pace-lead",
        "4",
    )["data"]
    assert paced["settings"]["allowance_mode"] == "pace"
    assert paced["settings"]["daily_allowance_percent"] == 0
    # Any aim from 1 to 100: half the week is fine.
    assert paced["settings"]["pace_aim_percent"] == 50
    assert paced["settings"]["pace_lead_hours"] == 4
    assert paced["state"]["allowance_pace"]["mode"] == "pace"
    status = runner.invoke(app, ["selfwork", "status", "--output", "text"])
    assert "allowance_mode=pace" in status.stdout
    both = cli(
        "selfwork",
        "set",
        "--expected-revision",
        str(paced["settings"]["revision"]),
        "--daily-allowance-percent",
        "10",
        code=2,
    )
    assert "can't both be on" in both["error"]["message"]
    assert cli(
        "selfwork",
        "set",
        "--expected-revision",
        str(paced["settings"]["revision"]),
        "--allowance-mode",
        "weekly",
        code=2,
    )


def test_selfwork_auto_approval_needs_the_users_confirmation(backend, tmp_path, monkeypatch):
    revision = cli("selfwork", "status")["data"]["settings"]["revision"]
    # Turning it on says what it allows and needs the explicit flag; nothing is sent first.
    refused = cli("selfwork", "set", "--expected-revision", str(revision), "--auto-approve", code=2)
    assert "--confirm-auto-approve" in refused["error"]["message"]
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    agent = cli(
        "selfwork",
        "set",
        "--expected-revision",
        str(revision),
        "--auto-approve",
        "--confirm-auto-approve",
        code=5,
    )
    assert agent["error"]["code"] == "user_only"
    monkeypatch.delenv("RATICODE_AGENT_SESSION")
    on = cli(
        "selfwork",
        "set",
        "--expected-revision",
        str(revision),
        "--auto-approve",
        "--confirm-auto-approve",
    )
    assert on["data"]["settings"]["auto_approve_proposals"] is True
    assert (
        "Auto-approval is on: 0 tickets approved"
        in runner.invoke(app, ["selfwork", "status", "--output", "text"]).stdout
    )
    exported = json.loads(runner.invoke(app, ["selfwork", "export"]).stdout)
    assert exported["auto_approve_proposals"] is True
    off = cli("selfwork", "set", "--expected-revision", str(revision + 1), "--no-auto-approve")
    assert off["data"]["settings"]["auto_approve_proposals"] is False
    # A file that turns it back on needs the same confirmation.
    target = tmp_path / "self-work.json"
    target.write_text(json.dumps(exported))
    assert (
        "--confirm-auto-approve"
        in (cli("selfwork", "apply", "--file", str(target), code=2)["error"]["message"])
    )
    applied = cli("selfwork", "apply", "--file", str(target), "--confirm-auto-approve")
    assert applied["data"]["settings"]["auto_approve_proposals"] is True


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


def test_task_create_names_its_threads_provider_model_and_account(backend):
    """`--account` is the thread picker's Account menu for a task: it wins over Rem's setting."""
    created = cli(
        "rem",
        "tasks",
        "create",
        "--title",
        "On work",
        "--type",
        "reminder",
        "--prompt",
        "Check in",
        "--schedule",
        '{"kind": "daily", "local_time": "09:00", "timezone": "UTC"}',
        "--provider",
        "claude_code",
        "--model",
        "fable",
        "--account",
        "work",
    )["data"]["task"]
    assert created["provider"] == "claude_code"
    assert created["model"] == "fable"
    assert created["provider_profile"] == "work"


def test_decisions_show_and_paths_print_each_record_paths_file_count(backend):
    import subprocess as sp

    service, project, _ = backend
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PATH": __import__("os").environ["PATH"],
    }
    (project / "docs").mkdir()
    (project / "docs" / "releasing.md").write_text("# Releasing\n")
    (project / "secrets").mkdir()
    for name in ("a", "b", "c"):
        (project / "secrets" / name).write_text(name)
    for args in (["init", "-q", "-b", "dev"], ["add", "-A"], ["commit", "-q", "-m", "start"]):
        sp.run(["git", "-C", str(project), *args], check=True, capture_output=True, env=env)
    cli("rem", "decisions", "add", "branch.integration", "dev", "--project", str(project))
    cli(
        "rem",
        "decisions",
        "add",
        "adr:release",
        "dev to stage",
        "--project",
        str(project),
        "--record",
        "--question",
        "How does work reach a release?",
        "--applies-to",
        "github/workflows/**",
        "--applies-to",
        "docs/releasing.md",
        "--forbids",
        "secrets/**",
    )
    listed = cli("rem", "decisions", "list")["data"]["items"]
    record_id = next(i["id"] for i in listed if i.get("remember_as") == "record")
    shown = cli("rem", "decisions", "show", record_id, "--output", "text").stdout
    assert "Applies to: github/workflows/** (matches no files), docs/releasing.md (1 file)" in shown
    assert "Never: secrets/** (matches 3 files that exist now)" in shown
    assert "Paths counted on dev at " in shown
    counted = cli(
        "rem",
        "decisions",
        "paths",
        "--id",
        record_id,
        "--applies-to",
        ".github/workflows/**",
        "--forbids",
        "docs/**",
        "--output",
        "text",
    ).stdout
    assert "Applies to: .github/workflows/** (matches no files)" in counted
    assert "Never: docs/** (matches 1 file that exists now)" in counted
    data = cli("rem", "decisions", "paths", "--project", str(project), "--applies-to", "docs/**")
    assert data["data"]["path_counts"]["applies_to"] == {"docs/**": 1}
    refused = runner.invoke(app, ["rem", "decisions", "paths", "--applies-to", "docs/**"])
    assert "Give --project or --id." in str(refused.exception)


def test_decisions_retire_keeps_a_written_record_and_says_what_happened_to_its_file(backend):
    import subprocess as sp

    service, project, _ = backend
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PATH": __import__("os").environ["PATH"],
    }
    (project / "README.md").write_text("# App\n")
    for args in (["init", "-q", "-b", "dev"], ["add", "-A"], ["commit", "-q", "-m", "start"]):
        sp.run(["git", "-C", str(project), *args], check=True, capture_output=True, env=env)
    cli("rem", "decisions", "add", "branch.integration", "dev", "--project", str(project))
    cli(
        "rem",
        "decisions",
        "add",
        "adr:db",
        "SQLite",
        "--project",
        str(project),
        "--record",
        "--question",
        "Which database?",
    )
    listed = cli("rem", "decisions", "list")["data"]["items"]
    record = next(i for i in listed if i.get("remember_as") == "record")
    fact = next(i for i in listed if i["topic"] == "branch.integration")
    assert record["record_path"]
    refused = runner.invoke(app, ["rem", "decisions", "retire", fact["id"]])
    assert refused.exit_code != 0
    assert "forget this one instead" in (refused.stdout + str(refused.exception))
    shown = cli("rem", "decisions", "retire", record["id"], "--output", "text").stdout
    assert shown.startswith(f"Retired {record['id']}: workers stop getting it")
    assert "status: rejected" in shown
    assert f"{record['id']}  retired: Which database?" in shown
    items = cli("rem", "decisions", "list")["data"]["items"]
    assert len(items) == len(listed)
    retired = next(i for i in items if i["id"] == record["id"])
    assert retired["status"] == "rejected" and retired["retired_at"]
    text = sp.run(
        ["git", "-C", str(project), "show", f"dev:{record['record_path']}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "status: rejected" in text


def test_a_decision_notice_line_names_the_question_its_id_project_and_recommendation() -> None:
    from gofer.cli.commands.rem_api import _notice_line

    notice = {
        "kind": "decision",
        "reason": "needs_you",
        "body": "Rem has a question: Which branch?",
        "project_name": "Raticode",
        "decision": {"id": "D-12", "question": "Which branch?", "recommended": "dev"},
    }
    assert _notice_line(notice) == (
        "needs_you: Rem has a question: Which branch? (D-12, Raticode; Rem recommends dev)"
    )
    bare = {**notice, "project_name": "", "decision": {"id": "D-3", "recommended": ""}}
    assert _notice_line(bare) == "needs_you: Rem has a question: Which branch? (D-3)"
