"""The backend-client commands, run against a real backend on isolated data.

Each test starts the HTTP backend on a temporary data folder with a fixed token,
points the CLI at it through RATICODE_URL and RATICODE_TOKEN_FILE, and checks the
JSON envelope and exit codes scripts depend on. No provider CLI runs: provider
discovery, sign-in status and usage refresh are replaced with fakes.
"""

from __future__ import annotations

import json
import threading
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
    log = ok("rattish", "logs", workflow_id, run_id)
    assert log["runId"] == run_id and log["status"] == "success"
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
    # Editing the exported file and applying it updates the same trigger.
    trigger["enabled"] = True
    edited = backend.tmp / "edited.json"
    ok("rattish", "triggers", "export", workflow_id, "-f", str(edited))
    edited.write_text(json.dumps(exported))
    changed = ok("rattish", "triggers", "apply", workflow_id, "-f", str(edited))
    assert changed["updated"] == [first["created"][0]["id"]]
    state = ok("api", "GET", "/rattish/triggers", "-f", f"workflowId={workflow_id}")
    assert state["triggers"][0]["enabled"] is True
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
    # Configuration never starts an organization.
    assert ok("org", "list")[0]["state"] == "paused"


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
    from gofer.ui import server as ui

    calls: list[int] = []

    async def fake_refresh(data_dir: Path, *, days: int, explicit: bool) -> dict[str, Any]:
        calls.append(days)
        return {
            "scope": "device",
            "days": days,
            "accounts": [{"provider": "codex", "status": "available", "windows": []}],
        }

    monkeypatch.setattr(ui, "refresh_usage", fake_refresh)
    shown = ok("usage", "show", "--days", "7")
    assert shown["days"] == 7 and isinstance(shown["accounts"], list)
    assert ok("usage", "refresh", "--days", "7")["accounts"][0]["provider"] == "codex"
    assert calls == [7]
    assert run("usage", "show", "--days", "5")[0] == 2


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
