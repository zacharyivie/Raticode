"""Phase 0 and headless CLI behaviour: honest legacy commands, stderr errors,
--format json everywhere, GOFER_DATA_DIR, headless serve and agent tokens."""

from __future__ import annotations

import http.client
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

import click
import pytest
from typer.testing import CliRunner

from gofer.cli.client import AGENT_SESSION_ENVS
from gofer.cli.main import app
from gofer.ui import headless
from gofer.utils.paths import get_data_dir

runner = CliRunner()

RUNNABLE = """Rattish: 1
Workflow:
  name: Demo flow
Node echo:
  type: bash-command
  command: printf headless-ok
"""


def _workflow(tmp_path: Path) -> tuple[Path, str, Path]:
    from gofer.rattish.workspaces import create_registered_workflow

    data_dir = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Demo flow", registry_dir=data_dir)
    workflow.entrypoint.write_text(RUNNABLE, encoding="utf-8")
    return data_dir, workflow.workflow_id, workflow.entrypoint


@pytest.fixture(autouse=True)
def _clean_client_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests run inside Rem sessions too; each starts as the user's own terminal.
    for name in (
        "RATICODE_URL",
        "RATICODE_SOCKET",
        "RATICODE_TOKEN",
        "RATICODE_TOKEN_FILE",
        *AGENT_SESSION_ENVS,
    ):
        monkeypatch.delenv(name, raising=False)


def _invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


def test_get_data_dir_honors_gofer_data_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOFER_DATA_DIR", str(tmp_path / "chosen"))
    assert get_data_dir() == tmp_path / "chosen"
    monkeypatch.setenv("GOFER_DATA_DIR", "  ")
    assert get_data_dir().name == "gofer"


def test_cli_uses_gofer_data_dir_without_data_dir_flag(tmp_path, monkeypatch) -> None:
    data_dir, workflow_id, _ = _workflow(tmp_path)
    monkeypatch.setenv("GOFER_DATA_DIR", str(data_dir))
    result = _invoke("workflow", "list", "--format", "json")
    assert result.exit_code == 0, result.output
    assert [item["id"] for item in json.loads(result.stdout)["workflows"]] == [workflow_id]


@pytest.mark.parametrize(
    ("command", "hint"),
    [
        (["show"], "rattish inspect-ir"),
        (["stop"], "Runs view"),
        (["secrets"], "rattish preflight"),
        (["history"], "History in the app"),
        (["diff", "WORKFLOW", "rev-1"], "History in the app"),
        (["edit"], "rattish check"),
        (["trigger"], "triggers add webhook"),
        (["set-schedule", "--cron", "0 * * * *"], "triggers add cron"),
        (["set-info", "--name", "Other"], "rattish check"),
        (["add-node", "--id", "x", "--type", "pass"], "rattish check"),
        (["add-edge", "--from", "a", "--to", "b"], "rattish check"),
    ],
)
def test_toml_only_commands_point_rattish_workflows_at_rattish(tmp_path, command, hint) -> None:
    data_dir, workflow_id, source = _workflow(tmp_path)
    args = [workflow_id if item == "WORKFLOW" else item for item in command]
    if "WORKFLOW" not in command:
        args.insert(1, workflow_id)
    result = _invoke("workflow", *args, "--data-dir", str(data_dir))
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "is a Rattish workflow" in result.stderr
    assert f"Raticode workflow {command[0]} only works with legacy TOML" in result.stderr
    assert hint in result.stderr
    # The workflow itself is untouched.
    assert source.read_text(encoding="utf-8") == RUNNABLE


def test_workflow_commands_that_handle_rattish(tmp_path) -> None:
    data_dir, workflow_id, _ = _workflow(tmp_path)
    flags = ["--data-dir", str(data_dir)]

    checked = _invoke("workflow", "validate", workflow_id, "--format", "json", *flags)
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.stdout)["command"] == "check"
    assert json.loads(_invoke("workflow", "validate", workflow_id, "--json", *flags).stdout)["ok"]

    planned = _invoke("workflow", "plan", workflow_id, "--format", "json", *flags)
    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.stdout)["command"] == "preflight"

    ran = _invoke("workflow", "run", workflow_id, *flags)
    assert ran.exit_code == 0, ran.output

    listed = _invoke("workflow", "logs", "list", workflow_id, "--format", "json", *flags)
    assert listed.exit_code == 0, listed.output
    runs = json.loads(listed.stdout)["runs"]
    assert len(runs) == 1 and runs[0]["status"] == "success"

    latest = _invoke("workflow", "logs", "latest", workflow_id, "--path-only", *flags)
    assert latest.exit_code == 0, latest.output
    assert latest.stdout.strip()

    usage = _invoke("workflow", "usage", workflow_id, "--json", *flags)
    assert usage.exit_code == 0, usage.output
    assert len(json.loads(usage.stdout)["runs"]) == 1

    bundle = tmp_path / "demo.raticode"
    exported = _invoke("workflow", "export", workflow_id, "--output", str(bundle), *flags)
    assert exported.exit_code == 0, exported.output
    assert bundle.is_file()

    removed = _invoke("workflow", "rm", workflow_id, "--yes", *flags)
    assert removed.exit_code == 0, removed.output
    after = json.loads(_invoke("workflow", "list", "--json", *flags).stdout)["workflows"]
    assert after == []


def test_errors_go_to_stderr_and_formats_are_unified(tmp_path) -> None:
    flags = ["--data-dir", str(tmp_path)]
    missing = _invoke("workflow", "show", "nope", *flags)
    assert missing.exit_code == 1
    assert missing.stdout == ""
    assert "not found" in missing.stderr

    as_flag = _invoke("workflow", "list", "--json", *flags)
    as_format = _invoke("workflow", "list", "--format", "json", *flags)
    assert as_flag.exit_code == as_format.exit_code == 0
    assert json.loads(as_flag.stdout) == json.loads(as_format.stdout)

    bad = _invoke("workflow", "list", "--format", "yaml", *flags)
    assert bad.exit_code == 2
    assert "Unsupported format" in bad.stderr

    for command in (
        ["provider", "profile", "list"],
        ["runner", "list"],
        ["runner", "status"],
        ["tokens", "list"],
        ["rem", "skill", "list"],
    ):
        result = _invoke(*command, "--format", "json", *flags)
        assert result.exit_code == 0, (command, result.output)
        assert isinstance(json.loads(result.stdout), list), command

    doctor = _invoke("doctor", "--format", "json", *flags)
    assert json.loads(doctor.stdout)


def test_schema_and_help_point_at_rattish() -> None:
    root = _invoke("--help")
    # Typer forces colored help on GitHub Actions; compare the plain text.
    assert "rattish docs --format json" in " ".join(click.unstyle(root.stdout).split())
    assert "schema --format json" not in root.stdout

    pointer = _invoke("schema")
    assert pointer.exit_code == 0, pointer.output
    payload = json.loads(pointer.stdout)
    assert payload["language"] == "rattish"
    assert Path(payload["grammar"]).name == "grammar.ebnf"
    assert "--legacy-toml" in payload["legacyToml"]

    legacy = _invoke("schema", "--legacy-toml")
    assert legacy.exit_code == 0
    assert json.loads(legacy.stdout)["legacy"] is True
    assert "mutation_command" in json.loads(
        _invoke("schema", "--command", "workflow.add-node").stdout
    )

    workflow_help = _invoke("workflow", "--help").stdout
    assert "[Legacy TOML]" in workflow_help
    for group in ("agent", "schedule", "watch", "runner"):
        assert "[Legacy TOML]" in _invoke(group, "--help").stdout, group


def test_legacy_commands_are_all_still_registered() -> None:
    import typer

    command: Any = typer.main.get_command(app)
    workflow_commands = set(command.commands["workflow"].commands)
    assert {
        "add-agent",
        "add-edge",
        "add-node",
        "build",
        "edit",
        "history",
        "set-schedule",
        "set-watch",
        "show",
        "stop",
        "trigger",
    } <= workflow_commands
    assert {"agent", "schedule", "watch", "runner", "ui", "serve", "tokens"} <= set(
        command.commands
    )


# --- headless ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("[::1]", True),
        ("localhost", True),
        ("127.0.0.2", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.10", False),
        ("example.com", False),
    ],
)
def test_loopback_detection(host, loopback) -> None:
    assert headless.is_loopback_host(host) is loopback


def test_headless_refuses_non_loopback_bind(tmp_path) -> None:
    result = _invoke(
        "serve", "--headless", "--host", "0.0.0.0", "--port", "0", "--data-dir", str(tmp_path)
    )
    assert result.exit_code == 2
    assert "loopback" in result.stderr
    assert not (tmp_path / "headless" / "endpoint.json").exists()


def test_serve_takes_no_token_on_the_command_line() -> None:
    import typer

    command: Any = typer.main.get_command(app)
    for serve in (command.commands["serve"], command.commands["ui"].commands["serve"]):
        options = {opt for param in serve.params for opt in param.opts}
        assert "--token" not in options
        assert "--token-file" in options


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_private_files_and_token_file_checks(tmp_path) -> None:
    target = tmp_path / "private" / "secret"
    headless.write_private_file(target, "value\n")
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700

    token_file = tmp_path / "token"
    token_file.write_text("x" * 40 + "\n", encoding="utf-8")
    token_file.chmod(0o644)
    with pytest.raises(headless.HeadlessError, match="chmod 600"):
        headless.read_token_file(token_file)
    token_file.chmod(0o600)
    assert headless.read_token_file(token_file) == "x" * 40


class _Captured:
    server: Any = None


@pytest.fixture
def headless_serve(tmp_path, monkeypatch):
    """Run serve(headless=True) in a thread with background pollers stubbed out."""
    from gofer.ui import server as ui

    monkeypatch.setattr(ui, "ensure_local_gofer_cli", lambda data_dir: None)
    monkeypatch.setattr(ui, "start_background_refresh", lambda data_dir: lambda: None)
    monkeypatch.setattr(ui, "start_leftover_reclaimer", lambda: lambda: None)
    monkeypatch.setattr(ui, "_install_shutdown_handlers", lambda server: None)
    monkeypatch.setenv("RATICODE_EXPERIMENTAL_ORGANIZATIONS", "1")
    captured = _Captured()
    real_create = ui.create_server

    def create(**kwargs: Any) -> Any:
        captured.server = real_create(**kwargs)
        return captured.server

    monkeypatch.setattr(ui, "create_server", create)
    threads: list[threading.Thread] = []

    def start(data_dir: Path, **kwargs: Any) -> Any:
        thread = threading.Thread(
            target=ui.serve,
            kwargs={"host": "127.0.0.1", "port": 0, "data_dir": data_dir, "headless": True}
            | kwargs,
            daemon=True,
        )
        thread.start()
        threads.append(thread)
        deadline = time.monotonic() + 15
        while not (data_dir / "headless" / "endpoint.json").exists():
            assert time.monotonic() < deadline, "headless backend didn't start"
            assert thread.is_alive(), "headless backend exited"
            time.sleep(0.05)
        # The endpoint is published before serve() prints GOFER_UI_READY to stdout. Wait
        # until the server answers, which happens only after that print, so the line
        # can't land inside a CliRunner's captured stdout.
        port = json.loads((data_dir / "headless" / "endpoint.json").read_text())["port"]
        while True:
            assert time.monotonic() < deadline, "headless backend didn't answer"
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                connection.request("GET", "/api/health")
                if connection.getresponse().status == 200:
                    break
            except OSError:
                time.sleep(0.05)
            finally:
                connection.close()
        return captured.server

    yield start
    if captured.server is not None:
        captured.server.shutdown()
    for thread in threads:
        thread.join(timeout=10)


def _request(
    server: Any, method: str, path: str, token: str | None, body: Any = None
) -> tuple[int, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", int(server.server_address[1]), timeout=10)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    connection.request(method, path, json.dumps(body) if body is not None else None, headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw else None


def test_headless_serve_writes_private_files_and_loads_rem_settings(
    tmp_path, headless_serve
) -> None:
    data_dir = tmp_path / "data"
    project = tmp_path / "work"
    project.mkdir()
    settings = data_dir / "headless" / "rem-settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"provider": "codex", "projects": [str(project)]}), encoding="utf-8"
    )

    server = headless_serve(data_dir)
    token_path = data_dir / "headless" / "owner-token"
    endpoint_path = data_dir / "headless" / "endpoint.json"
    assert _mode(token_path) == 0o600
    assert _mode(endpoint_path) == 0o600
    assert _mode(data_dir / "headless") == 0o700
    token = token_path.read_text(encoding="utf-8").strip()
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    assert endpoint["url"] == f"http://127.0.0.1:{server.server_address[1]}/api"
    assert endpoint["pid"] == os.getpid()
    assert token not in endpoint_path.read_text(encoding="utf-8")
    assert server.headless and server.api_token == token

    # Rem is configured from the file, so its queue doesn't wait for a window.
    assert server.rem_home.has_context
    status, conversation = _request(server, "GET", "/api/rem/conversation", token)
    assert status == 200 and conversation["context_ready"] is True
    with server.rem_home.store.read() as db:
        config = server.rem_home.store.setting(db, "config")
    assert config["projects"][0]["root"] == str(project.resolve())

    assert _request(server, "GET", "/api/rem/conversation", None)[0] == 401
    assert headless.read_owner_token(data_dir, environ={}) == token
    with pytest.raises(headless.HeadlessError, match="agent session"):
        headless.read_owner_token(data_dir, environ={"RATICODE_AGENT_SESSION": "1"})

    server.shutdown()
    deadline = time.monotonic() + 10
    while endpoint_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not endpoint_path.exists() and not token_path.exists()


def test_headless_without_rem_settings_names_the_file(tmp_path, headless_serve) -> None:
    data_dir = tmp_path / "data"
    server = headless_serve(data_dir)
    assert not server.rem_home.has_context
    assert str(data_dir / "headless" / "rem-settings.json") in server.rem_home.context_hint


def test_headless_token_file_input(tmp_path, headless_serve) -> None:
    data_dir = tmp_path / "data"
    secret = tmp_path / "mounted-token"
    secret.write_text("m" * 48, encoding="utf-8")
    secret.chmod(0o600)
    server = headless_serve(data_dir, token_file=secret)
    assert server.api_token == "m" * 48
    assert not (data_dir / "headless" / "owner-token").exists()
    assert json.loads((data_dir / "headless" / "endpoint.json").read_text())["tokenFile"] is None


def test_agent_tokens_are_hashed_and_limited(tmp_path, headless_serve, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    token_out = tmp_path / "ci.token"
    created = _invoke(
        "tokens", "create", "ci", "--output", str(token_out), "--data-dir", str(data_dir)
    )
    assert created.exit_code == 0, created.output
    agent = token_out.read_text(encoding="utf-8").strip()
    assert agent.startswith("rtk_agent_")
    assert agent not in created.stdout + created.stderr
    assert _mode(token_out) == 0o600
    store_file = data_dir / "headless" / "agent-tokens.json"
    assert _mode(store_file) == 0o600
    assert agent not in store_file.read_text(encoding="utf-8")
    listed = json.loads(
        _invoke("tokens", "list", "--format", "json", "--data-dir", str(data_dir)).stdout
    )
    assert [item["name"] for item in listed] == ["ci"]

    server = headless_serve(data_dir)
    owner = (data_dir / "headless" / "owner-token").read_text(encoding="utf-8").strip()

    assert _request(server, "GET", "/api/workflows", agent)[0] == 200
    for method, path, body in (
        ("GET", "/api/session", None),
        ("POST", "/api/rem/context", {}),
        ("POST", "/api/rem/messages", {"body": "hello"}),
        ("PUT", "/api/rem/kanban/settings", {}),
        ("POST", "/api/rem/kanban/ticket", {"action": "create", "title": "Do it"}),
        ("GET", "/api/devices", None),
        ("POST", "/api/desktop/path-grants", {}),
        ("POST", "/api/workflows/x/approvals/run/node/approve", {}),
        # Rewrite-then-run, triggers, swarms and Second Brain changes (audit H3).
        ("PUT", "/api/workflows/x", {"source": "Rattish: 1\n"}),
        ("POST", "/api/workflows/x/document/save", {"source": "Rattish: 1\n"}),
        ("POST", "/api/workflows/x/run", {}),
        ("DELETE", "/api/workflows/x", None),
        ("POST", "/api/rattish/triggers", {}),
        ("POST", "/api/swarms", {"projectRoot": str(data_dir)}),
        ("POST", "/api/swarms/s1/start", {"projectRoot": str(data_dir)}),
        ("POST", "/api/swarms/s1/control", {"projectRoot": str(data_dir), "action": "resume"}),
        ("POST", "/api/queue/q/cancel", {}),
        ("POST", "/api/second-brains", {"action": "mutate", "params": {}}),
        ("POST", "/api/second-brains", {"action": "remove", "params": {"id": "b"}}),
    ):
        status, payload = _request(server, method, path, agent, body)
        assert status == 403, (method, path, status, payload)
        assert payload["code"] == "agent_token_forbidden"
    # Reads, including Second Brain's, still work.
    status, payload = _request(
        server, "POST", "/api/second-brains", agent, {"action": "snapshot", "params": {}}
    )
    assert status == 200, payload

    # The owner creates a company; the agent can't start it or approve its own request.
    status, created_org = _request(
        server,
        "POST",
        "/api/organizations",
        owner,
        {"projectRoot": str(data_dir), "action": "create", "params": {"config": {"name": "Acme"}}},
    )
    assert status == 200, created_org
    org_id = created_org["result"]["id"]
    status, asked = _request(
        server,
        "POST",
        "/api/organizations",
        agent,
        {
            "projectRoot": str(data_dir),
            "organizationId": org_id,
            "action": "control",
            "params": {"state": "running"},
        },
    )
    assert status == 200, asked
    request_id = asked["result"]["requestId"]
    status, refused = _request(
        server,
        "POST",
        "/api/organizations",
        agent,
        {
            "projectRoot": str(data_dir),
            "organizationId": org_id,
            "action": "request_decide",
            "params": {"requestId": request_id, "decision": "approve"},
        },
    )
    assert status == 400 and "Only the user" in refused["error"]
    org = server.organizations.store.get(str(data_dir), org_id)
    assert org["runtime"]["state"] == "paused"

    revoked = _invoke("tokens", "revoke", "ci", "--data-dir", str(data_dir))
    assert revoked.exit_code == 0, revoked.output
    assert _request(server, "GET", "/api/workflows", agent)[0] == 401

    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    refused_cli = _invoke(
        "tokens",
        "create",
        "other",
        "--output",
        str(tmp_path / "other"),
        "--data-dir",
        str(data_dir),
    )
    assert refused_cli.exit_code == 5
    assert not (tmp_path / "other").exists()


def test_child_processes_are_marked_as_agent_sessions() -> None:
    from gofer.utils.process import build_subprocess_env

    env = build_subprocess_env({"GOFER_UI_API_TOKEN": "secret"})
    assert env["RATICODE_AGENT_SESSION"] == "1"
    assert "GOFER_UI_API_TOKEN" not in env


def test_rem_settings_file_must_not_be_writable_by_others(tmp_path) -> None:
    settings = tmp_path / "rem-settings.json"
    settings.write_text(json.dumps({"projects": [str(tmp_path)]}), encoding="utf-8")
    settings.chmod(0o666)
    with pytest.raises(headless.HeadlessError, match="other accounts"):
        headless.load_rem_settings(settings)
    settings.chmod(0o644)
    loaded = headless.load_rem_settings(settings)
    assert loaded["projects"] == [{"root": str(tmp_path.resolve()), "name": tmp_path.name}]
    settings.write_text(json.dumps({"projects": ["relative/path"]}), encoding="utf-8")
    with pytest.raises(headless.HeadlessError, match="absolute"):
        headless.load_rem_settings(settings)


def test_api_client_discovers_the_headless_backend(tmp_path, headless_serve, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    token_out = tmp_path / "agent.token"
    assert (
        _invoke(
            "tokens", "create", "bot", "--output", str(token_out), "--data-dir", str(data_dir)
        ).exit_code
        == 0
    )
    headless_serve(data_dir)
    flags = ["--data-dir", str(data_dir)]

    owner = _invoke("api", "GET", "/workflows", *flags)
    assert owner.exit_code == 0, owner.output
    assert json.loads(owner.stdout)["ok"] is True

    # Inside an agent session the owner token isn't picked up from the data folder.
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    unauthenticated = _invoke("api", "GET", "/workflows", *flags)
    assert unauthenticated.exit_code == 5, unauthenticated.output

    with_agent = _invoke("api", "GET", "/workflows", "--token-file", str(token_out), *flags)
    assert with_agent.exit_code == 0, with_agent.output
    # `api` refuses what an agent token can't do before sending it; the backend
    # refuses it too (test_agent_tokens_are_hashed_and_limited).
    forbidden = _invoke("api", "GET", "/session", "--token-file", str(token_out), *flags)
    assert forbidden.exit_code == 5, forbidden.output
    assert json.loads(forbidden.stdout)["error"]["code"] == "user_only"

    # An owner token handed over explicitly is refused too, not only one found on disk.
    owner_file = data_dir / "headless" / "owner-token"
    explicit_owner = _invoke("api", "GET", "/workflows", "--token-file", str(owner_file), *flags)
    assert explicit_owner.exit_code == 5, explicit_owner.output
    assert json.loads(explicit_owner.stdout)["error"]["code"] == "owner_token_in_agent_session"


def test_legacy_workflow_writers_go_through_a_running_backend(
    tmp_path, headless_serve, monkeypatch
) -> None:
    """workflow rename, duplicate and rm let a published backend make the change, so the
    backend is the only writer of the workflow files and re-syncs its schedules at once."""
    data_dir, workflow_id, _ = _workflow(tmp_path)
    flags = ["--data-dir", str(data_dir)]
    from gofer.ui.server import GoferUiRequestHandler

    headless_serve(data_dir)
    # Count the re-syncs the request handler does after a change. The backend's own
    # monitor also calls server.sync_schedules() every second, so that isn't counted.
    synced: list[str] = []
    real_sync = GoferUiRequestHandler._sync_schedules

    def sync(handler: GoferUiRequestHandler) -> None:
        synced.append("sync")
        real_sync(handler)

    monkeypatch.setattr(GoferUiRequestHandler, "_sync_schedules", sync)

    renamed = _invoke("workflow", "rename", workflow_id, "--name", "Renamed flow", *flags)
    assert renamed.exit_code == 0, renamed.output
    assert "Renamed flow" in renamed.stdout
    assert len(synced) == 1

    copied = _invoke("workflow", "duplicate", workflow_id, "--name", "Copy flow", *flags)
    assert copied.exit_code == 0, copied.output
    copy_id = copied.stdout.rsplit("(", 1)[1].split(")", 1)[0]
    assert len(synced) == 2

    removed = _invoke("workflow", "rm", copy_id, "--yes", *flags)
    assert removed.exit_code == 0, removed.output
    assert len(synced) == 3
    listed = json.loads(_invoke("workflow", "list", "--json", *flags).stdout)["workflows"]
    assert [item["id"] for item in listed] == [workflow_id]

    # Backend refusals come back as errors, and nothing is written behind its back.
    missing = _invoke("workflow", "rename", "no-such-flow", "--name", "X", *flags)
    assert missing.exit_code != 0
    assert "not found" in missing.output.lower()

    # Inside an agent turn the owner token isn't read, so the backend refuses the change
    # rather than the CLI writing the files itself.
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    refused = _invoke("workflow", "rm", workflow_id, "--yes", *flags)
    assert refused.exit_code == 5, refused.output
    assert len(synced) == 3


def test_legacy_workflow_writers_work_offline_after_the_backend_stops(tmp_path) -> None:
    data_dir, workflow_id, _ = _workflow(tmp_path)
    flags = ["--data-dir", str(data_dir)]
    endpoint = data_dir / "headless" / "endpoint.json"
    endpoint.parent.mkdir(parents=True)
    # A backend that crashed without removing its endpoint file.
    endpoint.write_text(
        json.dumps({"url": "http://127.0.0.1:9", "pid": 2**22 + 12345}), encoding="utf-8"
    )
    renamed = _invoke("workflow", "rename", workflow_id, "--name", "Offline name", *flags)
    assert renamed.exit_code == 0, renamed.output
    assert "Offline name" in renamed.stdout
