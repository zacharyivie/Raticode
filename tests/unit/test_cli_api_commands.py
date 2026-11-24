"""The backend-client commands, run against a real backend on isolated data.

Each test starts the HTTP backend on a temporary data folder with a fixed token,
points the CLI at it through RATICODE_URL and RATICODE_TOKEN_FILE, and checks the
JSON envelope and exit codes scripts depend on. No provider CLI runs: provider
discovery, sign-in status and usage refresh are replaced with fakes.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.commands import _backend
from gofer.cli.main import app
from gofer.rattish.workspaces import create_registered_workflow

TOKEN = "cli-api-test-token"
# Tests run inside Rem sessions too, which set these; each test starts without them.
CLEARED_ENV = (
    "RATICODE_URL",
    "RATICODE_SOCKET",
    "RATICODE_TOKEN",
    "RATICODE_TOKEN_FILE",
    "RATICODE_DATA_DIR",
    "GOFER_DATA_DIR",
    "RATICODE_NON_INTERACTIVE",
    *_backend.AGENT_ENV,
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CLEARED_ENV:
        monkeypatch.delenv(name, raising=False)


class Backend:
    def __init__(self, server: Any, data: Path, tmp: Path) -> None:
        self.server = server
        self.data = data
        self.tmp = tmp
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def post(self, path: str, body: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            self.url + "/api" + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def workflow(self, source: str, name: str = "Nightly") -> str:
        project = self.data / "projects" / name.lower()
        project.mkdir(parents=True, exist_ok=True)
        workflow = create_registered_workflow(project, name, registry_dir=self.data)
        workflow.entrypoint.write_text(source, encoding="utf-8")
        return workflow.workflow_id


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Backend]:
    from gofer.ui import server as ui

    monkeypatch.setattr(ui, "ensure_local_gofer_cli", lambda data_dir: None)
    monkeypatch.setenv("RATICODE_EXPERIMENTAL_ORGANIZATIONS", "1")
    data = tmp_path / "data"
    data.mkdir()
    server = ui.create_server(port=0, data_dir=data, api_token=TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    token_file.chmod(0o600)
    current = Backend(server, data, tmp_path)
    monkeypatch.setenv("RATICODE_URL", current.url)
    monkeypatch.setenv("RATICODE_TOKEN_FILE", str(token_file))
    try:
        yield current
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run(*args: str, json_output: bool = True) -> tuple[int, Any, str]:
    argv = [*args, "--format", "json"] if json_output else list(args)
    result = CliRunner().invoke(app, argv)
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    stdout = result.stdout.strip()
    payload = json.loads(stdout) if json_output and stdout else stdout
    return result.exit_code, payload, result.stderr


def ok(*args: str) -> Any:
    code, payload, stderr = run(*args)
    assert code == 0, (args, payload, stderr)
    assert payload["ok"] is True and payload["error"] is None
    assert args[0] in payload["command"]
    return payload["data"]


# Connecting ----------------------------------------------------------------------


def test_unreachable_backend_exits_6_with_an_error_envelope(monkeypatch, tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("x")
    token.chmod(0o600)
    monkeypatch.setenv("RATICODE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("RATICODE_TOKEN_FILE", str(token))
    code, payload, stderr = run("projects", "list")
    assert code == 6
    assert payload["ok"] is False
    assert payload["command"] == "projects list"
    assert stderr.startswith("Raticode projects list:")


def test_wrong_token_exits_5(backend, monkeypatch, tmp_path) -> None:
    wrong = tmp_path / "wrong"
    wrong.write_text("not-the-token")
    wrong.chmod(0o600)
    monkeypatch.setenv("RATICODE_TOKEN_FILE", str(wrong))
    code, payload, _ = run("org", "list")
    assert code == 5
    assert payload["error"]["http"] == 401


def test_backend_found_through_the_headless_files(backend, monkeypatch) -> None:
    from gofer.cli.client import write_api_files

    monkeypatch.delenv("RATICODE_URL")
    monkeypatch.delenv("RATICODE_TOKEN_FILE")
    write_api_files(backend.data, url=backend.url, token=TOKEN)
    assert ok("projects", "list", "--data-dir", str(backend.data)) == []


def test_text_output_goes_to_stdout_and_errors_to_stderr(backend) -> None:
    code, stdout, stderr = run("projects", "list", "--output", "text", json_output=False)
    assert code == 0 and stdout == "No projects with workflows." and stderr == ""
    code, stdout, stderr = run("rattish", "show", "missing", "--output", "text", json_output=False)
    assert code == 3
    assert stdout == ""
    assert "is not registered" in stderr


def test_api_token_is_never_a_command_line_option() -> None:
    import typer

    from tests.helpers.cli_parity import load_manifest

    root = typer.main.get_command(app)
    backend_commands = [
        name for name, kind in load_manifest()["commands"].items() if kind == "backend"
    ]
    for name in backend_commands:
        if name == "api":
            continue  # Thread 2's command; it has its own test.
        command: Any = root
        for part in name.split(" "):
            command = command.commands[part]  # type: ignore[attr-defined]
        options = {opt for param in command.params for opt in getattr(param, "opts", [])}
        assert "--token" not in options, name


# Workflows -----------------------------------------------------------------------

ECHO = """Rattish: 1
Workflow:
  name: Nightly
Node greet:
  type: bash-command
  command: echo hello-from-cli
"""

GATE = """Rattish: 1
Workflow:
  name: Gated
Node approve:
  type: approval-gate
  message: Ship it?
"""


def test_workflow_list_show_run_wait_runs_logs_and_events(backend) -> None:
    workflow_id = backend.workflow(ECHO)
    listed = ok("api", "GET", "/workflows")["workflows"]
    assert [w["id"] for w in listed] == [workflow_id]
    shown = ok("rattish", "show", workflow_id, "--source")
    assert shown["name"] == "Nightly" and "hello-from-cli" in shown["source"]

    run_result = ok("rattish", "start", workflow_id, "--wait", "--timeout", "60")
    assert run_result["status"] == "success"
    run_id = run_result["id"]
    runs = ok("rattish", "runs", workflow_id)
    assert [r["id"] for r in runs["runs"]] == [run_id]
    assert runs["runs"][0]["usage"]["calls"] == 0
    log = ok("rattish", "logs", workflow_id, run_id)
    assert log["runId"] == run_id and log["status"] == "success"
    assert log["usage"]["summary"] == "No model calls"
    assert log["usage"]["limits"] == {"maxTokens": None, "maxCostUsd": None}
    _, runs_text, _ = run("rattish", "runs", workflow_id, "--output", "text", json_output=False)
    assert "TOKENS" in runs_text and "EST. COST" in runs_text
    _, log_text, _ = run(
        "rattish", "logs", workflow_id, run_id, "--output", "text", json_output=False
    )
    assert log_text.startswith("Usage: No model calls")
    assert ok("rattish", "logs", workflow_id)["runId"] == run_id
    events = ok("rattish", "events", workflow_id, run_id)
    assert any(e["type"] == "node_completed" for e in events["runEvents"])
    assert ok("rattish", "wait", workflow_id, run_id)["status"] == "success"
    projects = ok("projects", "list")
    assert projects[0]["workflows"] == [workflow_id]
    assert "plan" in ok("rattish", "start", workflow_id, "--dry-run")
    stopped = ok("rattish", "stop", workflow_id)
    assert stopped["stopped"] is False
    for args in (
        ("rattish", "show", workflow_id),
        ("rattish", "runs", workflow_id),
        ("rattish", "logs", workflow_id, run_id),
        ("rattish", "events", workflow_id, run_id),
        ("rattish", "approvals", workflow_id),
        ("projects", "list"),
        ("devices", "list"),
        ("usage", "show"),
    ):
        code, stdout, stderr = run(*args, "--output", "text", json_output=False)
        assert code == 0 and stdout and not stderr, (args, stdout, stderr)


def test_run_waiting_on_an_approval_exits_7_until_approved(backend) -> None:
    workflow_id = backend.workflow(GATE, "Gated")
    code, payload, _ = run("rattish", "start", workflow_id, "--wait", "--timeout", "60")
    assert code == 7, payload
    run_id = payload["data"]["id"]
    assert payload["data"]["awaitingApproval"] is True
    pending = ok("rattish", "approvals", workflow_id, "--pending")["approvals"]
    assert [(a["runId"], a["nodeId"]) for a in pending] == [(run_id, "approve")]
    decided = ok("rattish", "approve", workflow_id, run_id, "approve", "--notes", "LGTM")
    assert decided
    finished = ok("rattish", "wait", workflow_id, run_id, "--timeout", "60", "--interval", "0.2")
    assert finished["status"] == "success"
    assert ok("rattish", "approvals", workflow_id, "--pending")["approvals"] == []


def test_run_from_dry_run_lists_the_approval_steps_that_ask_again(backend) -> None:
    workflow_id = backend.workflow(
        GATE.replace("Ship it?\n", "Ship it?\n  to: ship\n")
        + "Node ship:\n  type: bash-command\n  command: printf shipped\n  needs: approve\n",
        "Gated",
    )
    code, payload, _ = run("rattish", "start", workflow_id, "--wait", "--timeout", "60")
    assert code == 7, payload
    run_id = payload["data"]["id"]
    assert ok("rattish", "approve", workflow_id, run_id, "approve")
    finished = ok("rattish", "wait", workflow_id, run_id, "--timeout", "60", "--interval", "0.2")
    assert finished["status"] == "success"

    plan = ok("rattish", "start", workflow_id, "--from", "ship", "--dry-run")["plan"]
    assert plan["reuseRunId"] == run_id
    # The gate is never reused, so nothing is, and the gate asks again.
    assert plan["reusedNodes"] == []
    assert plan["askAgainNodes"] == ["approve"]
    code, text, _ = run(
        "rattish",
        "start",
        workflow_id,
        "--from",
        "ship",
        "--dry-run",
        "--output",
        "text",
        json_output=False,
    )
    assert code == 0, text
    assert f"Run from ship would reuse run {run_id}: no steps" in text
    assert "Approval steps that ask again: approve" in text


def test_runs_all_lists_every_workflow_with_how_each_run_started(backend, monkeypatch) -> None:
    nightly = backend.workflow(ECHO)
    other = backend.workflow(ECHO.replace("Nightly", "Weekly"), "Weekly")
    manual = ok("rattish", "start", nightly, "--wait", "--timeout", "60")["id"]
    # Rem's sessions can't start backend runs; they run a workflow in their own process.
    from gofer.rattish.workspaces import find_registered_workflow

    source = find_registered_workflow(other, registry_dir=backend.data).entrypoint
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    result = CliRunner().invoke(
        app, ["rattish", "run", str(source), "--data-dir", str(backend.data), "--format", "json"]
    )
    monkeypatch.delenv("RATICODE_AGENT_SESSION")
    assert result.exit_code == 0, result.output
    by_rem = json.loads(result.stdout)["run"]["run_id"]

    listed = ok("rattish", "runs", "--all", "--since", "1h")
    assert [(r["id"], r["workflowName"], r["startedBy"]) for r in listed["runs"]] == [
        (by_rem, "Weekly", "rem"),
        (manual, "Nightly", "manual"),
    ]
    assert listed["totals"]["summary"] == "2 workflow runs: 2 passed"
    assert {w["id"] for w in listed["workflows"]} == {nightly, other}
    only = ok("rattish", "runs", "--all", "--workflow", other, "--status", "success")
    assert [r["id"] for r in only["runs"]] == [by_rem]
    assert ok("rattish", "runs", "--all", "--status", "error")["runs"] == []

    code, text, stderr = run("rattish", "runs", "--all", "--output", "text", json_output=False)
    assert code == 0 and not stderr
    header, *_ = text.splitlines()
    assert header.split() == [
        "STARTED",
        "WORKFLOW",
        "HOW",
        "STATUS",
        "DURATION",
        "TOKENS",
        "EST.",
        "COST",
        "RUN",
    ]
    assert "Weekly" in text and " Rem " in text
    assert text.splitlines()[-1] == "2 workflow runs: 2 passed"

    # The run list behind the app's view says the same, and bad filters are refused.
    request = urllib.request.Request(
        backend.url + "/api/rattish/runs?since=yesterday",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=10)
    assert refused.value.code == 400
    assert run("rattish", "runs")[0] == 2
    assert run("rattish", "runs", nightly, "--all")[0] == 2
    assert run("rattish", "runs", nightly, "--since", "1h")[0] == 2
    assert run("rattish", "runs", "--all", "--since", "soon")[0] != 0


def test_unknown_workflow_and_bad_input_exit_codes(backend) -> None:
    assert run("rattish", "show", "nope")[0] == 3
    code, payload, _ = run("rattish", "start", "nope", "--input", "broken")
    assert code == 2 and payload["error"]["code"] == "invalid_input"
    assert run("projects", "list", "--format", "yaml", json_output=False)[0] == 2


def test_workflow_export_and_import_round_trip(backend) -> None:
    workflow_id = backend.workflow(ECHO)
    bundle = backend.tmp / "nightly.raticode"
    exported = ok("rattish", "export", workflow_id, "-f", str(bundle))
    assert exported["bundlePath"] == str(bundle.resolve()) and bundle.is_file()
    assert not any((backend.data / "cli-exports").iterdir())
    preview = ok("rattish", "import", str(bundle), "--project", "/unused", "--preview")
    assert preview
    target = backend.data / "projects" / "copy"
    target.mkdir(parents=True)
    imported = ok("rattish", "import", str(bundle), "--project", str(target))
    assert imported["id"] != workflow_id
    assert len(ok("api", "GET", "/workflows")["workflows"]) == 2
    # Outside the data folder and without a desktop approval, the backend refuses.
    outside = backend.tmp / "outside"
    outside.mkdir()
    code, payload, _ = run("rattish", "import", str(bundle), "--project", str(outside))
    assert code in {2, 5} and payload["ok"] is False


def test_export_moves_only_the_bundle_it_asked_for(tmp_path: Path) -> None:
    # Whatever answers on the backend's port names bundlePath; before the fix the CLI
    # moved that file, whichever of the user's files it was.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from gofer.cli.client import write_api_files

    victim = tmp_path / "notes" / "important.txt"
    victim.parent.mkdir()
    victim.write_text("precious", encoding="utf-8")

    class Hostile(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps({"bundlePath": str(victim)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Hostile)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        data = tmp_path / "data"
        write_api_files(data, url=f"http://127.0.0.1:{server.server_address[1]}", token="t")
        out = tmp_path / "out.raticode"
        run("rattish", "export", "wf", "-f", str(out), "--data-dir", str(data))
    finally:
        server.shutdown()
        server.server_close()
    assert victim.read_text(encoding="utf-8") == "precious"
    assert not out.exists()


def test_social_list_state_cant_add_query_parameters(tmp_path: Path) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from gofer.cli.client import write_api_files

    paths: list[str] = []

    class Recorder(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            paths.append(self.path)
            body = b'{"posts": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        data = tmp_path / "data"
        write_api_files(data, url=f"http://127.0.0.1:{server.server_address[1]}", token="t")
        code, _, _ = run("social", "list", "--state", "draft&limit=1", "--data-dir", str(data))
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    assert paths == ["/api/social/posts?state=draft%26limit%3D1"]


def test_triggers_export_and_apply_are_idempotent(backend) -> None:
    workflow_id = backend.workflow(ECHO)
    spec = backend.tmp / "triggers.json"
    spec.write_text(
        json.dumps(
            {
                "triggers": [
                    {
                        "kind": "cron",
                        "label": "nightly",
                        "config": {"expression": "0 3 * * *", "timezone": "UTC"},
                        "enabled": False,
                    }
                ]
            }
        )
    )
    first = ok("rattish", "triggers", "apply", workflow_id, "-f", str(spec))
    assert len(first["created"]) == 1 and first["updated"] == []
    again = ok("rattish", "triggers", "apply", workflow_id, "-f", str(spec))
    assert again["created"] == [] and again["unchanged"] == [first["created"][0]["id"]]
    exported = ok("rattish", "triggers", "export", workflow_id)
    trigger = exported["triggers"][0]
    assert trigger["config"] == {"expression": "0 3 * * *", "timezone": "UTC"}
    assert "id" not in trigger and "token" not in trigger
    # A file without a Time limit gets the 6-hour default, and export carries it.
    assert trigger["maxRunSeconds"] == 6 * 3600
    # Editing the exported file and applying it updates the same trigger.
    trigger["enabled"] = True
    trigger["maxRunSeconds"] = 1800
    edited = backend.tmp / "edited.json"
    ok("rattish", "triggers", "export", workflow_id, "-f", str(edited))
    edited.write_text(json.dumps(exported))
    changed = ok("rattish", "triggers", "apply", workflow_id, "-f", str(edited))
    assert changed["updated"] == [first["created"][0]["id"]]
    state = ok("api", "GET", "/rattish/triggers", "-f", f"workflowId={workflow_id}")
    assert state["triggers"][0]["enabled"] is True
    assert state["triggers"][0]["maxRunSeconds"] == 1800
    empty = backend.tmp / "empty.json"
    empty.write_text(json.dumps({"triggers": []}))
    kept = ok("rattish", "triggers", "apply", workflow_id, "-f", str(empty))
    assert kept["removed"] == []
    pruned = ok("rattish", "triggers", "apply", workflow_id, "-f", str(empty), "--prune")
    assert pruned["removed"] == [first["created"][0]["id"]]
    assert ok("rattish", "triggers", "export", workflow_id)["triggers"] == []


# Organizations -------------------------------------------------------------------


def _org_file(backend: Backend, document: dict[str, Any], name: str = "org.json") -> Path:
    path = backend.tmp / name
    path.write_text(json.dumps(document))
    return path


def test_org_config_apply_employees_schedule_and_conflicts(backend) -> None:
    created = ok(
        "org",
        "apply",
        "-f",
        str(
            _org_file(
                backend,
                {
                    "config": {
                        "name": "Studio",
                        "employees": [
                            {"id": "lead", "name": "Avery", "role": "Coordinate"},
                            {
                                "id": "risky",
                                "name": "Rory",
                                "role": "Build",
                                "permissionMode": "danger-full-access",
                                "paused": True,
                            },
                        ],
                    }
                },
            )
        ),
    )
    org_id = created["id"]
    listed = ok("org", "list")
    assert [o["id"] for o in listed] == [org_id] and listed[0]["state"] == "paused"
    assert ok("org", "show", org_id)["config"]["name"] == "Studio"

    saved = backend.tmp / "saved.json"
    document = ok("org", "config", org_id, "-f", str(saved))
    edited = {**document, "config": {**document["config"], "mission": "Ship it"}}
    stale = _org_file(backend, edited, "edited.json")
    ok("org", "apply", "-f", str(stale), "--reason", "Mission")
    assert ok("org", "show", org_id)["config"]["mission"] == "Ship it"
    code, payload, _ = run("org", "apply", "-f", str(stale))
    assert code == 4 and payload["error"]["code"] == "conflict"
    assert ok("org", "history", org_id)[0]["actor"] == "user"

    assert ok("org", "employees", "hold", org_id, "lead")["changed"] is True
    held = {e["id"]: e.get("paused") for e in ok("org", "employees", "list", org_id)}
    assert held == {"lead": True, "risky": True}
    assert ok("org", "employees", "release", org_id, "lead")["paused"] is False
    code, payload, _ = run("org", "employees", "release", org_id, "risky")
    assert code == 5 and "desktop" in payload["error"]["message"]
    assert run("org", "employees", "hold", org_id, "ghost")[0] == 3

    schedule = ok("org", "schedule", "show", org_id)
    schedule["blocks"] = [
        {"id": "morning", "label": "Morning", "start": "09:00", "end": "12:00", "focus": "Bugs"}
    ]
    schedule["timezone"] = "Europe/Paris"
    ok("org", "schedule", "apply", org_id, "-f", str(_org_file(backend, schedule, "s.json")))
    updated = ok("org", "schedule", "show", org_id)
    assert [b["id"] for b in updated["blocks"]] == ["morning"]
    assert updated["timezone"] == "Europe/Paris"
    code, _, _ = run("org", "schedule", "apply", org_id, "-f", str(backend.tmp / "s.json"))
    assert code == 4
    # Backlog pulls: on unless turned off; setting the current value changes nothing.
    assert ok("org", "backlog-pulls", org_id) == {"organizationId": org_id, "pullBacklog": True}
    before = ok("org", "show", org_id)["revision"]
    assert ok("org", "backlog-pulls", org_id, "--on")["pullBacklog"] is True
    assert ok("org", "show", org_id)["revision"] == before
    assert ok("org", "backlog-pulls", org_id, "--off")["pullBacklog"] is False
    assert ok("org", "show", org_id)["config"]["pullBacklog"] is False
    assert ok("org", "history", org_id)[0]["reason"] == (
        "Workers leave Backlog tickets for you to move"
    )
    # Configuration never starts an organization.
    assert ok("org", "list")[0]["state"] == "paused"


def test_org_employees_account_chooses_one_of_the_providers_accounts(backend, monkeypatch) -> None:
    import gofer.core.provider_capabilities as capabilities
    from gofer.core import provider_accounts as accounts

    monkeypatch.setattr(
        capabilities, "provider_capabilities_payload", lambda refresh=False: {"providers": []}
    )
    work = accounts.create_account("work", provider="codex", data_dir=backend.data)
    accounts.create_account("personal", provider="claude_code", data_dir=backend.data)
    document = {"config": {"name": "Studio", "employees": [{"id": "lead", "name": "Avery"}]}}
    org_id = ok("org", "apply", "-f", str(_org_file(backend, document)))["id"]

    chosen = ok("org", "employees", "account", org_id, "lead", "work")
    assert chosen == {
        "id": "lead",
        "account": work.account_id,
        "accountName": "work",
        "changed": True,
    }
    assert ok("org", "show", org_id)["config"]["employees"][0]["account"] == work.account_id
    # The ID works too, and choosing the same account changes nothing.
    assert ok("org", "employees", "account", org_id, "lead", work.account_id)["changed"] is False
    # Another provider's account isn't offered.
    code, payload, _ = run("org", "employees", "account", org_id, "lead", "personal")
    assert code == 3 and "work" in payload["error"]["message"]
    back = ok("org", "employees", "account", org_id, "lead", "default")
    assert back["account"] is None and back["changed"] is True
    assert ok("org", "show", org_id)["config"]["employees"][0].get("account") is None
    assert ok("org", "history", org_id)[0]["reason"] == (
        "Avery uses the default sign-in, set from the CLI"
    )


def test_org_tickets_projects_knowledge_and_packages(backend) -> None:
    org_id = ok("org", "apply", "-f", str(_org_file(backend, {"config": {"name": "Tickets"}})))[
        "id"
    ]
    ticket = ok(
        "org", "tickets", "create", org_id, "--title", "Fix flaky test", "--request-id", "t-1"
    )
    again = ok(
        "org", "tickets", "create", org_id, "--title", "Fix flaky test", "--request-id", "t-1"
    )
    assert again["id"] == ticket["id"]
    tasks = ok("org", "tickets", "list", org_id)["items"]
    assert [t["id"] for t in tasks] == [ticket["id"]] and tasks[0]["status"] == "backlog"
    ok(
        "org",
        "tickets",
        "update",
        org_id,
        ticket["id"],
        "--expected-revision",
        str(ticket["revision"]),
        "--set",
        "priority=high",
    )
    code, _, _ = run(
        "org",
        "tickets",
        "update",
        org_id,
        ticket["id"],
        "--expected-revision",
        str(ticket["revision"]),
        "--set",
        "priority=low",
    )
    assert code == 4
    ok("org", "tickets", "comment", org_id, ticket["id"], "--body", "Seen in CI")
    for args in (
        ("org", "list"),
        ("org", "show", org_id),
        ("org", "tickets", "list", org_id),
        ("org", "employees", "list", org_id),
        ("org", "schedule", "show", org_id),
        ("org", "projects", org_id),
        ("org", "history", org_id),
        ("org", "events", org_id),
    ):
        code, stdout, stderr = run(*args, "--output", "text", json_output=False)
        assert code == 0 and stdout and not stderr, (args, stdout, stderr)
    assert (
        "Fix flaky test"
        in run("org", "tickets", "list", org_id, "--output", "text", json_output=False)[1]
    )
    assert ok("org", "projects", org_id) == {"projectRoots": [], "projects": []}

    entry = _org_file(
        backend, {"kind": "decision", "title": "Use SQLite", "summary": "Local first"}, "k.json"
    )
    saved = ok("org", "knowledge", "save", org_id, "-f", str(entry))
    entries = ok("org", "knowledge", "search", org_id, "SQLite")["items"]
    assert saved["id"] in [e["id"] for e in entries]
    assert ok("org", "knowledge", "read", org_id, saved["id"])["title"] == "Use SQLite"

    package = backend.tmp / "org.zip"
    assert ok("org", "export", org_id, "-f", str(package))["files"]
    assert ok("org", "import", str(package), "--preview")
    imported = ok("org", "import", str(package))
    assert imported["id"] != org_id
    assert len(ok("org", "list")) == 2


def test_org_commands_offer_no_run_control_or_consent_decisions() -> None:
    from tests.helpers.cli_parity import cli_commands

    org_commands = [name for name in cli_commands() if name.startswith("org ")]
    for word in ("start", "pause", "resume", "control", "decide", "approve", "consent", "wake"):
        assert not [name for name in org_commands if word in name], word


def test_org_commands_report_a_disabled_preview(backend, monkeypatch) -> None:
    monkeypatch.setattr(backend.server.organizations, "enabled", False)
    code, payload, _ = run("org", "list")
    assert code == 5
    assert "disabled" in payload["error"]["message"]


# Swarms --------------------------------------------------------------------------

SWARM = {
    "config": {
        "name": "Reviewers",
        "agents": [
            {
                "id": "lead",
                "name": "Lead",
                "role": "Coordinate",
                "provider": "codex",
                "isOrchestrator": True,
            },
            {"id": "worker", "name": "Worker", "role": "Review", "provider": "codex"},
        ],
    }
}


def test_swarm_apply_show_export_history_and_delete(backend) -> None:
    project = backend.data / "projects" / "swarm"
    project.mkdir(parents=True)
    spec = backend.tmp / "swarm.json"
    spec.write_text(json.dumps(SWARM))
    created = ok("swarm", "apply", "--project", str(project), "-f", str(spec))
    swarm_id = created["id"]
    assert [s["id"] for s in ok("swarm", "list", "--project", str(project))] == [swarm_id]
    assert ok("swarm", "show", swarm_id, "--project", str(project))["name"] == "Reviewers"
    exported = ok("swarm", "export", swarm_id, "--project", str(project))
    assert exported["swarmId"] == swarm_id and exported["config"]["name"] == "Reviewers"
    exported["config"]["charter"] = "Review every change"
    spec.write_text(json.dumps(exported))
    ok("swarm", "apply", "--project", str(project), "-f", str(spec))
    shown = ok("swarm", "show", swarm_id, "--project", str(project))
    assert shown["charter"] == "Review every change"
    assert shown.get("run") in (None, {})
    assert ok("swarm", "history", swarm_id, "--project", str(project))["history"] == []
    assert run("swarm", "delete", swarm_id, "--project", str(project))[0] == 2
    ok("swarm", "delete", swarm_id, "--project", str(project), "--yes")
    assert ok("swarm", "list", "--project", str(project)) == []


def test_swarm_commands_never_start_work() -> None:
    from tests.helpers.cli_parity import cli_commands

    swarm_commands = {name for name in cli_commands() if name.startswith("swarm ")}
    assert swarm_commands == {
        "swarm list",
        "swarm show",
        "swarm history",
        "swarm export",
        "swarm apply",
        "swarm delete",
    }


# Second Brain --------------------------------------------------------------------


def test_brain_save_search_read_documents_and_projects(backend) -> None:
    root = backend.tmp / "brain"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\nThe kestrel migrates in autumn.\n")
    grant = backend.server.path_grants.register(root)
    brain = backend.post(
        "/second-brains",
        {"action": "open", "params": {"root": str(root)}, "workspaceGrants": {str(root): grant}},
    )["result"]
    listed = ok("brain", "list")
    assert [b["id"] for b in listed] == [brain["id"]]

    saved = ok(
        "brain",
        "save",
        "--brain",
        brain["id"],
        "--title",
        "Heron",
        "--text",
        "Herons hunt at dawn.",
        "--timeout",
        "30",
    )
    assert saved["state"] == "done"
    doc_id = saved["result"]["docId"]
    assert (root / saved["result"]["path"]).is_file()
    hits = ok("brain", "search", "herons", "--brain", brain["id"])["hits"]
    assert doc_id in {h["docId"] for h in hits}
    assert "Herons hunt at dawn." in ok("brain", "read", doc_id)["content"]
    documents = ok("brain", "documents", "--brain", brain["id"])
    assert doc_id in {d["id"] for d in documents}
    assert ok("brain", "projects", "--brain", brain["id"]) is not None
    code, payload, _ = run("brain", "save", "--brain", brain["id"], "--title", "Both")
    assert code == 2


# Providers, usage, devices and threads ---------------------------------------------


def test_providers_models_status_and_settings(backend, monkeypatch) -> None:
    from gofer.ui import server as ui

    fake = {
        "id": "codex",
        "displayName": "Codex",
        "available": True,
        "enabled": True,
        "version": "1.0",
        "defaultModel": "m-1",
        "deniedModels": ["m-2"],
        "models": [{"id": "m-1", "label": "One"}, {"id": "m-2", "label": "Two"}],
    }

    class Capability:
        def to_ui_payload(self) -> dict[str, Any]:
            return fake

    class Service:
        def provider(self, provider: str, refresh: bool = False) -> Capability:
            assert provider == "codex"
            return Capability()

    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        ui, "provider_capabilities_payload", lambda refresh=False: {"providers": [fake]}
    )
    monkeypatch.setattr(ui, "provider_capability_service", lambda: Service())
    monkeypatch.setattr(ui, "save_provider_preference", lambda p, c: saved.update({p: c}))
    monkeypatch.setattr(
        backend.server.provider_auth,
        "status",
        lambda provider: {"provider": provider, "signedIn": True},
    )

    assert [p["id"] for p in ok("provider", "list")] == ["codex"]
    assert [m["id"] for m in ok("provider", "models", "codex")["models"]] == ["m-1", "m-2"]
    code, stdout, _ = run("provider", "models", "codex", "--output", "text", json_output=False)
    assert code == 0 and "default" in stdout and "blocked" in stdout
    assert ok("provider", "status", "codex")["signedIn"] is True
    ok("provider", "set", "codex", "defaultModel=m-1", 'deniedModels=["m-2"]')
    assert saved == {"codex": {"defaultModel": "m-1", "deniedModels": ["m-2"]}}
    assert run("provider", "set", "codex", "colour=blue")[0] == 2


def test_usage_show_and_refresh_use_no_real_provider(backend, monkeypatch) -> None:
    from gofer.core.usage_ledger import record_invocation
    from gofer.ui import server as ui

    calls: list[dict[str, Any]] = []

    async def fake_refresh(
        data_dir: Path, *, query: dict[str, Any], explicit: bool
    ) -> dict[str, Any]:
        calls.append(query)
        return {
            "scope": "device",
            "accounts": [{"provider": "codex", "status": "available", "windows": []}],
        }

    monkeypatch.setattr(ui, "refresh_usage", fake_refresh)
    shown = ok("usage", "show", "--days", "7")
    assert shown["days"] == 7 and isinstance(shown["accounts"], list)
    assert ok("usage", "refresh", "--days", "7")["accounts"][0]["provider"] == "codex"
    assert calls[-1]["days"] == "7"
    assert run("usage", "show", "--days", "5")[0] == 2
    # The older 90 days is 3mo; the new periods and custom ranges go through --range/--from/--to.
    assert ok("usage", "show", "--days", "90")["range"]["key"] == "3mo"
    for period in ("3mo", "6mo", "1y", "5y", "all"):
        assert ok("usage", "show", "--range", period)["range"]["key"] == period
    custom = ok("usage", "show", "--from", "2026-01-01", "--to", "2026-01-31")
    assert custom["range"]["key"] == "custom" and custom["range"]["end_date"] == "2026-01-31"
    ok("usage", "refresh", "--range", "6mo")
    assert calls[-1]["period"] == "6mo"
    assert run("usage", "show", "--range", "2y")[0] == 2
    assert run("usage", "show", "--from", "2026-02-01", "--to", "2026-01-01")[0] == 2
    assert run("usage", "show", "--from", "2026-02-01")[0] == 2
    assert run("usage", "show", "--range", "7d", "--days", "7")[0] == 2
    # Text output lists tokens per bucket and provider.
    record_invocation(
        invocation_id="cli-usage",
        provider="codex",
        data_dir=backend.data,
        metadata={"input_tokens": 1200, "output_tokens": 34},
    )
    code, stdout, _ = run("usage", "show", "--range", "7d", "--output", "text", json_output=False)
    assert code == 0
    assert "Tokens in Raticode, Last 7 days, per day: 1,234 reported" in stdout
    assert "codex 1,234 (100%)" in stdout
    # The codex call names no model, so its API cost is unknown, never $0.
    assert "Estimated API cost (Last 7 days): unknown" in stdout
    assert "Saved vs API" not in stdout
    # --by account splits each provider by account, named as the chart's legend names them.
    record_invocation(
        invocation_id="cli-usage-work",
        provider="codex",
        profile="work",
        data_dir=backend.data,
        metadata={"input_tokens": 3000, "output_tokens": 0},
    )
    code, stdout, _ = run(
        "usage", "show", "--range", "7d", "--by", "account", "--output", "text", json_output=False
    )
    assert code == 0, stdout
    assert "BY ACCOUNT" in stdout
    assert "codex (work) 3,000 (71%)" in stdout
    assert "codex (Default account) 1,234 (29%)" in stdout
    code, stdout, _ = run("usage", "show", "--range", "7d", "--output", "text", json_output=False)
    assert "BY PROVIDER" in stdout and "codex 4,234 (100%)" in stdout
    # JSON carries both groupings whichever way text output splits them.
    shown = ok("usage", "show", "--range", "7d", "--by", "account")
    assert [a["key"] for a in shown["series"]["accounts"]] == ["codex:work", "codex:"]
    assert [p["provider"] for p in shown["series"]["providers"]] == ["codex"]
    assert run("usage", "show", "--by", "model")[0] == 2


def test_usage_alerts_show_and_change_the_limit_alerts(backend) -> None:
    shown = ok("usage", "alerts")
    assert shown["enabled"] is True and shown["thresholds"] == [80, 95]
    assert ok("usage", "alerts", "--off")["enabled"] is False
    # One level changes; the other keeps its saved value.
    changed = ok("usage", "alerts", "--on", "--first", "70")
    assert changed["enabled"] is True and changed["thresholds"] == [70, 95]
    assert ok("usage", "alerts", "--second", "90")["thresholds"] == [70, 90]
    code, _, _ = run("usage", "alerts", "--first", "95")
    assert code != 0
    assert ok("usage", "alerts")["thresholds"] == [70, 90]
    code, stdout, _ = run("usage", "alerts", "--output", "text", json_output=False)
    assert code == 0
    assert "Limit alerts are on, at 70% and 90% of each 5-hour and weekly window." in stdout


def test_api_cost_from_the_cli(backend) -> None:
    from gofer.core.api_prices import price_for
    from gofer.core.usage_ledger import record_invocation

    # The plan-price commands were removed along with Saved vs API.
    assert run("usage", "plan", "list")[0] == 2
    record_invocation(
        invocation_id="cli-cost",
        provider="claude_code",
        model="claude-opus-5-5",
        data_dir=backend.data,
        metadata={"input_tokens": 3_000_000, "cache_read_tokens": 2_000_000, "output_tokens": 0},
    )
    cost = ok("usage", "show", "--range", "24h")["api_cost"]
    price = price_for("claude_code", "claude-opus-5-5")
    assert price is not None
    expected = (1_000_000 * price.input + 2_000_000 * price.cache_read_price) / 1_000_000
    assert cost["total"]["cost_usd"] == pytest.approx(expected)
    assert not {"savings", "plans", "plan_providers"} & cost.keys()
    code, stdout, _ = run("usage", "show", "--range", "24h", "--output", "text", json_output=False)
    assert code == 0
    assert f"Estimated API cost (Last 24 hours): ${expected:,.2f}" in stdout
    assert "Saved vs API" not in stdout


def test_computers_list_and_set_this_computers_worker_limit(backend) -> None:
    [this] = ok("computers", "list")["computers"]
    assert this["this_computer"] is True and this["state"] == "online"
    assert this["max_threads_setting"] == 0 and this["max_threads"] >= 1
    assert set(this["running_by_kind"]) == {"rem", "phone", "organizations", "swarms"}
    assert this["running_work"] == []
    changed = ok("computers", "set", "3")["computers"][0]
    assert changed["max_threads"] == 3 and changed["max_threads_setting"] == 3
    code, payload, _ = run("computers", "set", "40")
    assert code != 0 and payload["ok"] is False
    code, payload, _ = run("computers", "set", "2", "--computer", "missing")
    assert code != 0 and "No computer" in payload["error"]["message"]
    code, stdout, stderr = run("computers", "list", "--output", "text", json_output=False)
    assert code == 0 and not stderr
    assert "this computer" in stdout and "LIMIT" in stdout


def test_computers_list_text_shows_each_computers_running_workers_by_kind() -> None:
    from gofer.cli.commands.system import _computers_text

    fixture = Path(__file__).parents[2] / (
        "src/gofer/devices/protocol/v2/fixtures/desktop.result.computers-list.json"
    )
    body = json.loads(json.loads(fixture.read_text())["payload"]["body"])
    text = _computers_text(body)
    lines = text[text.index("Running workers") :].splitlines()
    assert lines == [
        "Running workers",
        "  Studio desktop: 1 Rem task, 1 organization worker",
        "    - Rem task: Fix the login page",
        "    - organization worker: Acme · Ada",
        # A paired desktop shares counts only; an offline one shows nothing.
        "  Workstation: 2 organization workers, 1 Rem task",
    ]
    # An older desktop without counts shows only its total, in the table.
    body["computers"][1]["running_by_kind"] = None
    assert "Workstation:" not in _computers_text(body)


def test_computers_placement_saves_validates_and_reads_back(backend) -> None:
    view = ok("computers", "placement")
    rem = view["consumers"][0]
    assert rem["consumer"] == "rem" and rem["strategy"] == "overflow" and rem["saved"] is False
    [this] = view["computers"]
    saved = ok("computers", "place", "rem", "manual", "--computer", this["id"])
    assert saved["consumers"][0]["computers"] == [this["id"]]
    assert ok("computers", "placement")["consumers"][0]["strategy"] == "manual"
    code, payload, _ = run("computers", "place", "rem", "manual")
    assert code != 0 and "at least one computer" in payload["error"]["message"]
    code, payload, _ = run("computers", "place", "rem", "manual", "--computer", "missing")
    assert code != 0 and "No computer" in payload["error"]["message"]
    code, payload, _ = run("computers", "place", "org:missing", "balanced")
    assert code != 0 and "No consumer" in payload["error"]["message"]
    code, stdout, stderr = run("computers", "placement", "--output", "text", json_output=False)
    assert code == 0 and not stderr
    assert "Rem" in stdout and "manual" in stdout and "STRATEGY" in stdout


def test_computers_placement_text_shows_the_notice_for_a_paired_only_placement(backend) -> None:
    paired = "00000000-0000-4000-8000-0000000000aa"
    backend.server.computers.fleet = lambda: [
        {
            "device_id": paired,
            "name": "Desktop B",
            "role": "desktop",
            "state": "active",
            "reachability": "reachable",
            "last_seen": time.time(),
            "running_jobs": [],
        }
    ]
    ok("computers", "place", "rem", "manual", "--computer", paired)
    code, stdout, stderr = run("computers", "placement", "--output", "text", json_output=False)
    assert code == 0 and not stderr
    assert (
        "Rem: Rem's work starts only on this computer for now, so with only Desktop B picked, "
        "every task waits. A waiting research or conversation task can still go to Desktop B "
        "when you pick Run there."
    ) in stdout.splitlines()
    # With this computer picked too, nothing is printed under the table.
    this = next(c["id"] for c in ok("computers", "placement")["computers"] if c["this_computer"])
    ok("computers", "place", "rem", "manual", "--computer", paired, "--computer", this)
    _, stdout, _ = run("computers", "placement", "--output", "text", json_output=False)
    assert "starts only on this computer" not in stdout


def test_computers_order_saves_the_order_and_refuses_an_unknown_computer(backend) -> None:
    [this] = ok("computers", "placement")["computers"]
    assert ok("computers", "order", this["id"])["order"] == [this["id"]]
    code, payload, _ = run("computers", "order", this["id"], "missing")
    assert code != 0 and "No computer with the ID missing" in payload["error"]["message"]
    code, stdout, stderr = run(
        "computers", "order", this["id"], "--output", "text", json_output=False
    )
    assert code == 0 and not stderr
    assert stdout.startswith(f"Order: {this['name']}")


def test_devices_and_threads(backend) -> None:
    devices = ok("devices", "list")
    assert "peers" in devices
    receipts = ok("threads", "steering", "conversation-1")
    assert receipts == {"receipts": []}
    # A turn that isn't running can't be stopped: a conflict, exit 4.
    assert run("threads", "stop", "conversation-1", "turn-1")[0] == 4
    code, payload, _ = run("threads", "follow", "conversation-1", "turn-1")
    assert code == 3 and payload["error"]["code"] == "not_found"

    def turn(emit: Any) -> None:
        emit({"type": "delta", "text": "Working"})
        emit({"type": "final", "message": {"body": "Done"}})

    backend.server.chat_jobs.start("conversation-2", "turn-2", turn)
    code, stdout, _ = run("threads", "follow", "conversation-2", "turn-2", json_output=False)
    assert code == 0
    lines = [json.loads(line) for line in stdout.splitlines()]
    events = [line["data"] for line in lines if line["kind"] == "event"]
    assert [e.get("type") for e in events][:2] == ["delta", "final"]
    assert lines[-1]["kind"] == "result" and lines[-1]["data"]["events"] == len(events)
    code, payload, _ = run("threads", "steer", "bad id!", "turn-1", "--text", "hi")
    assert code == 2


@pytest.mark.parametrize(
    "args",
    [
        ("rattish", "approve", "wf", "run", "node"),
        ("rattish", "reject", "wf", "run", "node"),
        ("org", "employees", "release", "org-1", "lead"),
        ("provider", "set", "codex", "defaultModel=m-1"),
        ("swarm", "delete", "s-1", "--project", "/tmp", "--yes"),
    ],
)
def test_user_only_decisions_are_refused_inside_agent_turns(backend, monkeypatch, args) -> None:
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    # A worker can't use the owner token (that is refused on its own); give it an agent
    # token so the command reaches its own user-only check.
    monkeypatch.delenv("RATICODE_TOKEN_FILE")
    monkeypatch.setenv("RATICODE_TOKEN", "rtk_agent_not-a-real-token")
    code, payload, _ = run(*args)
    assert code == 5
    assert payload["error"]["code"] == "user_only"


def test_org_and_swarm_config_files_are_refused_inside_agent_turns(backend, monkeypatch) -> None:
    monkeypatch.setenv("RATICODE_COMMAND_ACTIVITY", "1")
    org = _org_file(backend, {"config": {"name": "Agent made"}})
    assert run("org", "apply", "-f", str(org))[0] == 5
    project = backend.data / "projects" / "agent"
    project.mkdir(parents=True)
    swarm = backend.tmp / "swarm.json"
    swarm.write_text(json.dumps(SWARM))
    assert run("swarm", "apply", "--project", str(project), "-f", str(swarm))[0] == 5
    monkeypatch.delenv("RATICODE_COMMAND_ACTIVITY")
    assert run("org", "list")[1]["data"] == []
