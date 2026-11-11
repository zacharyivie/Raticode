"""The CLI's scripting promises: --format json on record commands, honest hints and
help, legacy refusals, completion paths and the website pages that describe them."""

from __future__ import annotations

import json
import re
import shlex
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from gofer.cli.client import ExitCode
from gofer.cli.commands.api import API_PATHS, complete_api_path
from gofer.cli.main import app
from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.core.health import run_health_checks

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "website" / "docs"
runner = CliRunner()

SIMPLE_TOML = """
[workflow]
id = "simple"
name = "Simple"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
"""

RATTISH = """Rattish: 1
Workflow:
  name: Demo flow
Node echo:
  type: bash-command
  command: printf ok
"""

CLIENT_ENV = (
    "RATICODE_URL",
    "RATICODE_SOCKET",
    "RATICODE_TOKEN",
    "RATICODE_TOKEN_FILE",
    "RATICODE_DATA_DIR",
    "GOFER_DATA_DIR",
    "RATICODE_AGENT_SESSION",
    "RATICODE_EXPERIMENTAL_ORGANIZATIONS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CLIENT_ENV:
        monkeypatch.delenv(name, raising=False)


def _invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


def _commands() -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []

    def walk(command: Any, path: list[str]) -> None:
        if hasattr(command, "commands"):
            for name, child in command.commands.items():
                walk(child, [*path, name])
        else:
            found.append((" ".join(path), command))

    walk(typer.main.get_command(app), [])
    return found


def _rattish_workflow(tmp_path: Path) -> tuple[Path, str, Path]:
    from gofer.rattish.workspaces import create_registered_workflow

    data_dir = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Demo flow", registry_dir=data_dir)
    workflow.entrypoint.write_text(RATTISH, encoding="utf-8")
    return data_dir, workflow.workflow_id, workflow.entrypoint


# --- --format json on record commands ---------------------------------------------------


def test_every_list_and_status_command_accepts_format_json() -> None:
    record_commands = {"list", "status", "approvals"}
    missing = [
        path
        for path, command in _commands()
        if not command.hidden
        and path.split()[-1] in record_commands
        and not any("--format" in getattr(param, "opts", ()) for param in command.params)
    ]
    assert missing == []


def test_every_visible_command_has_help_text() -> None:
    assert [path for path, command in _commands() if not command.hidden and not command.help] == []


def test_agent_list_prints_json(tmp_path: Path) -> None:
    empty = _invoke("agent", "list", "--format", "json", "--data-dir", str(tmp_path))
    assert empty.exit_code == 0, empty.output
    assert json.loads(empty.stdout) == []

    created = _invoke(
        "agent",
        "create",
        "--name",
        "Json Agent",
        "--subscription",
        "codex",
        "--working-dir",
        str(tmp_path),
        "--prompt",
        "hello",
        "--data-dir",
        str(tmp_path),
    )
    assert created.exit_code == 0, created.output
    listed = _invoke("agent", "list", "--format", "json", "--data-dir", str(tmp_path))
    assert listed.exit_code == 0, listed.output
    [agent] = json.loads(listed.stdout)
    assert agent["agentId"] == "json-agent"
    assert agent["subscription"] == "codex"
    assert agent["workingDir"] == str(tmp_path)


def test_watch_list_prints_json(tmp_path: Path) -> None:
    empty = _invoke("watch", "list", "--format", "json", "--data-dir", str(tmp_path))
    assert empty.exit_code == 0, empty.output
    assert json.loads(empty.stdout) == []

    (tmp_path / "watched.toml").write_text(
        SIMPLE_TOML + '\n[workflow.watch]\npath = "inputs"\nglob = "*.txt"\nrecursive = true\n'
    )
    listed = _invoke("watch", "list", "--format", "json", "--data-dir", str(tmp_path))
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout) == [
        {
            "id": "simple",
            "workflowPath": str(tmp_path / "watched.toml"),
            "path": "inputs",
            "glob": "*.txt",
            "mode": "batch",
            "maxConcurrency": 1,
            "recursive": True,
            "debounceSeconds": 1.0,
        }
    ]


def test_workflow_approvals_prints_json(tmp_path: Path) -> None:
    empty = _invoke("workflow", "approvals", "--format", "json", "--data-dir", str(tmp_path))
    assert empty.exit_code == 0, empty.output
    assert json.loads(empty.stdout) == []

    ApprovalStore(tmp_path).create_or_update(
        ApprovalRequest(workflow_id="simple", run_id="run-1", node_id="gate", message="Ship?")
    )
    listed = _invoke("workflow", "approvals", "--format", "json", "--data-dir", str(tmp_path))
    assert listed.exit_code == 0, listed.output
    [request] = json.loads(listed.stdout)
    assert (request["workflowId"], request["runId"], request["nodeId"]) == (
        "simple",
        "run-1",
        "gate",
    )
    assert request["status"] == "pending"

    bad = _invoke("workflow", "approvals", "--format", "yaml", "--data-dir", str(tmp_path))
    assert bad.exit_code == 2
    assert bad.stdout == ""


# --- Hints and help -----------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_unreachable_backend_hint_points_at_headless_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATICODE_TOKEN", "t" * 32)
    result = _invoke(
        "api",
        "GET",
        "/health",
        "--endpoint",
        f"http://127.0.0.1:{_free_port()}",
        "--data-dir",
        str(tmp_path),
    )
    assert result.exit_code == ExitCode.UNREACHABLE
    message = json.loads(result.stdout)["error"]["message"]
    assert "Raticode serve --headless" in message
    assert "ui serve" not in message


def test_root_and_rattish_help_describe_what_the_commands_do() -> None:
    root = _invoke("--help")
    assert "JSON envelope" in root.stdout
    assert "Data commands accept" not in root.stdout
    rattish = _invoke("rattish", "--help")
    assert "(Backend)" in rattish.stdout
    assert "deployment readiness" not in rattish.stdout


def test_doctor_reports_the_organizations_preview_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def preview() -> dict[str, Any]:
        payload = run_health_checks(data_dir=tmp_path).to_dict()
        [item] = [
            entry
            for entry in payload["diagnostics"]
            if entry["id"] == "feature_preview.organizations"
        ]
        return dict(item)

    off = preview()
    assert off["severity"] == "ok"
    assert off["detail"] == {"env": "RATICODE_EXPERIMENTAL_ORGANIZATIONS", "enabled": False}
    assert "RATICODE_EXPERIMENTAL_ORGANIZATIONS=1" in off["message"]

    monkeypatch.setenv("RATICODE_EXPERIMENTAL_ORGANIZATIONS", "1")
    assert preview()["detail"]["enabled"] is True


# --- Legacy groups refuse Rattish workflows -----------------------------------------------


@pytest.mark.parametrize(
    ("command", "hint"),
    [
        (["agent", "list", "--workflow", "ID"], "rattish inspect-ir"),
        (["runner", "queue", "ID"], "rattish run"),
        (["schedule", "add", "SOURCE"], "rattish triggers add cron"),
    ],
)
def test_legacy_groups_point_rattish_workflows_at_rattish(
    tmp_path: Path, command: list[str], hint: str
) -> None:
    data_dir, workflow_id, source = _rattish_workflow(tmp_path)
    args = [{"ID": workflow_id, "SOURCE": str(source)}.get(item, item) for item in command]
    if command[0] == "schedule":
        args += ["--db", str(tmp_path / "schedules.db")]
    else:
        args += ["--data-dir", str(data_dir)]
    result = _invoke(*args)
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "is a Rattish workflow" in result.stderr
    assert f"Raticode {command[0]} {command[1]} only works with legacy TOML" in result.stderr
    assert hint in result.stderr
    assert source.read_text(encoding="utf-8") == RATTISH
    assert not (tmp_path / "schedules.db").exists()


def test_legacy_groups_still_report_missing_toml_workflows(tmp_path: Path) -> None:
    result = _invoke("agent", "list", "--workflow", "missing", "--data-dir", str(tmp_path))
    assert result.exit_code == 1
    assert "not found" in result.output


# --- Completion ------------------------------------------------------------------------


def _manifest_api_paths() -> list[str]:
    manifest = json.loads((ROOT / "docs" / "cli-parity.json").read_text(encoding="utf-8"))
    paths: set[str] = set()
    for key, entry in manifest["routes"].items():
        if "excluded" in entry:
            continue
        route = key.split(" ", 1)[1].split("#", 1)[0].removeprefix("/api")
        kept: list[str] = []
        for segment in route.strip("/").split("/"):
            if segment == "*":
                break
            kept.append(segment)
        paths.add("/" + "/".join(kept))
    return sorted(paths)


def test_api_path_completion_matches_the_parity_manifest() -> None:
    expected = _manifest_api_paths()
    listing = "".join(f'    "{path}",\n' for path in expected)
    assert list(API_PATHS) == expected, (
        "Update API_PATHS in src/gofer/cli/commands/api.py to:\nAPI_PATHS = (\n" + listing + ")"
    )
    # Excluded routes, such as the window's session token, are never offered.
    assert "/session" not in API_PATHS
    assert complete_api_path("/sess") == []


def test_completion_explains_when_typer_cannot_print_scripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "typer._completion_shared", None)
    result = _invoke("completion", "bash")
    assert result.exit_code == ExitCode.FAILED
    assert result.stdout == ""
    assert "--install-completion" in result.stderr


def test_completion_uses_a_typer_function_that_still_exists() -> None:
    from typer._completion_shared import get_completion_script

    script = get_completion_script(
        prog_name="Raticode", complete_var="_RATICODE_COMPLETE", shell="bash"
    )
    assert "_RATICODE_COMPLETE" in script


# --- Website pages -----------------------------------------------------------------------


def _docs(*names: str) -> str:
    return "\n".join((DOCS / name).read_text(encoding="utf-8") for name in names)


def test_docs_do_not_advertise_a_unix_socket_listener() -> None:
    text = _docs("reference/cli.md", "reference/headless.md", "reference/cli-headless.md")
    assert "unix:" not in text
    assert "RATICODE_SOCKET" not in text
    assert "can't listen on a Unix socket" in text


def test_docs_name_the_commands_the_audit_found_missing() -> None:
    text = _docs("reference/cli.md", "rem/resources.md", "workflows/triggers.md")
    for command in (
        "provider profile edit",
        "provider profile rm",
        "rem mcp list",
        "rem mcp remove",
        "rem skill path",
        "rem skill remove",
        "triggers enable ID` / `disable ID",
        "`approvals`, `approve` and `reject`",
    ):
        assert command in text, command


def test_docs_say_windows_skips_token_file_permission_checks() -> None:
    text = _docs("reference/headless.md", "reference/cli.md")
    assert "On Windows, Raticode doesn't set or check permissions" in text
    assert "On Windows the CLI doesn't check these permissions" in text


def _container_section() -> str:
    text = _docs("reference/headless.md")
    return text[text.index("## In a container") :]


def _resolve(args: list[str]) -> tuple[Any, list[str]]:
    command: Any = typer.main.get_command(app)
    rest = list(args)
    while hasattr(command, "commands") and rest and rest[0] in command.commands:
        command = command.commands[rest.pop(0)]
    return command, rest


def test_container_how_to_uses_real_commands_and_options() -> None:
    section = _container_section()
    assert "HEALTHCHECK" in section
    assert "RATICODE_EXPERIMENTAL_ORGANIZATIONS=1" in section
    lines = [
        line.strip().rstrip("\\").strip()
        for block in re.findall(r"```(?:bash|dockerfile)\n(.*?)```", section, re.S)
        for line in block.splitlines()
    ]
    # Commands named inline in the bullets, such as `docker exec raticode Raticode ...`.
    lines += [span for span in re.findall(r"`([^`\n]+)`", section) if "Raticode " in span]
    calls = []
    for line in lines:
        if line.startswith("CMD ["):
            calls.append(json.loads(line.removeprefix("CMD ")))
        elif "Raticode " in line and not line.startswith(("ADD", "RUN", "#")):
            words = shlex.split(line.split("Raticode ", 1)[1].split(">", 1)[0])
            calls.append(["Raticode", *words])
    assert len(calls) >= 5, calls
    for call in calls:
        command, rest = _resolve(call[1:])
        assert not hasattr(command, "commands"), call
        # Parsing checks every option name and value against the real command.
        with command.make_context("Raticode", list(rest), resilient_parsing=False):
            pass
    assert ["Raticode", "serve", "--headless"] in calls
    assert any(call[1:3] == ["tokens", "create"] for call in calls)


def test_cli_parsing_rejects_unknown_options_so_the_docs_check_means_something() -> None:
    command, rest = _resolve(["api", "GET", "/health", "--no-such-option"])
    with pytest.raises(Exception, match="No such option"):
        command.make_context("Raticode", rest)
