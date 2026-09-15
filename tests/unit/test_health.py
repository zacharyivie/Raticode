from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.health import (
    HealthDiagnostic,
    HealthReport,
    run_health_checks,
    workflow_health_diagnostics,
    workflow_health_payload,
)
from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles
from gofer.ui.api import list_workflow_payloads

runner = CliRunner()


def _doctor_report(
    diagnostics: list[HealthDiagnostic],
    tmp_path: Path,
    *,
    workflow_path: Path | None = None,
) -> HealthReport:
    return HealthReport(
        ok=not any(item.severity == "error" for item in diagnostics),
        diagnostics=diagnostics,
        data_dir=tmp_path,
        workflow_path=workflow_path,
    )


def _assert_before(output: str, first: str, second: str) -> None:
    assert output.index(first) < output.index(second)


def test_doctor_human_reports_ready_checks_when_all_diagnostics_ok(
    monkeypatch,
    tmp_path: Path,
) -> None:
    diagnostics = [
        HealthDiagnostic(
            id="python.version",
            severity="ok",
            message="Python version is supported.",
        ),
        HealthDiagnostic(
            id="data_dir.available",
            severity="ok",
            message="Data directory is writable.",
        ),
    ]
    monkeypatch.setattr(
        "gofer.cli.commands.doctor.run_health_checks",
        lambda **_kwargs: _doctor_report(diagnostics, tmp_path),
    )

    result = runner.invoke(app, ["doctor", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Raticode doctor" in result.output
    assert "Ready checks" in result.output
    assert "- Python version is supported." in result.output
    assert "- Data directory is writable." in result.output
    assert "Errors" not in result.output
    assert "Warnings" not in result.output
    assert '"diagnostics"' not in result.output
    assert "[green]" not in result.output


def test_doctor_human_groups_warnings_separately_and_exits_zero(
    monkeypatch,
    tmp_path: Path,
) -> None:
    diagnostics = [
        HealthDiagnostic(
            id="provider.cli",
            severity="warning",
            message="Configured provider CLI 'codex' is not on PATH.",
        ),
        HealthDiagnostic(
            id="shell.available",
            severity="ok",
            message="Shell binary 'bash' is available.",
        ),
    ]
    monkeypatch.setattr(
        "gofer.cli.commands.doctor.run_health_checks",
        lambda **_kwargs: _doctor_report(diagnostics, tmp_path),
    )

    result = runner.invoke(app, ["doctor", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Warnings" in result.output
    assert "- Configured provider CLI 'codex' is not on PATH." in result.output
    assert "Ready checks" in result.output
    assert "- Shell binary 'bash' is available." in result.output
    assert "Errors" not in result.output
    _assert_before(result.output, "Warnings", "Ready checks")
    assert '"severity": "warning"' not in result.output
    assert "[yellow]" not in result.output


def test_doctor_human_groups_errors_and_exits_nonzero(
    monkeypatch,
    tmp_path: Path,
) -> None:
    diagnostics = [
        HealthDiagnostic(
            id="workflow.provider_cli",
            severity="error",
            message="Workflow provider CLI 'codex' is not on PATH.",
        ),
        HealthDiagnostic(
            id="provider.cli",
            severity="warning",
            message="Configured provider CLI 'codex' is not on PATH.",
        ),
        HealthDiagnostic(
            id="python.version",
            severity="ok",
            message="Python version is supported.",
        ),
    ]
    monkeypatch.setattr(
        "gofer.cli.commands.doctor.run_health_checks",
        lambda **_kwargs: _doctor_report(diagnostics, tmp_path),
    )

    result = runner.invoke(app, ["doctor", "--data-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "Errors" in result.output
    assert "- Workflow provider CLI 'codex' is not on PATH." in result.output
    assert "Warnings" in result.output
    assert "- Configured provider CLI 'codex' is not on PATH." in result.output
    assert "Ready checks" in result.output
    assert "- Python version is supported." in result.output
    _assert_before(result.output, "Errors", "Warnings")
    _assert_before(result.output, "Warnings", "Ready checks")
    assert '"errors"' not in result.output
    assert "[red]" not in result.output


def test_doctor_human_with_no_diagnostics_prints_only_header(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "gofer.cli.commands.doctor.run_health_checks",
        lambda **_kwargs: _doctor_report([], tmp_path),
    )

    result = runner.invoke(app, ["doctor", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert result.output == "Raticode doctor\n"


def test_doctor_human_workflow_option_prints_workflow_diagnostics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "workflow.toml"
    seen: dict[str, object] = {}

    def fake_run_health_checks(*, data_dir: Path | None = None, workflow: str | None = None):
        seen["data_dir"] = data_dir
        seen["workflow"] = workflow
        return _doctor_report(
            [
                HealthDiagnostic(
                    id="workflow.shell_available",
                    severity="error",
                    subject="node:build",
                    message="Workflow node 'build' requires missing shell binary 'bash'.",
                )
            ],
            tmp_path,
            workflow_path=workflow_path,
        )

    monkeypatch.setattr("gofer.cli.commands.doctor.run_health_checks", fake_run_health_checks)

    result = runner.invoke(
        app,
        ["doctor", "--workflow", "workflow-id", "--data-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert seen == {"data_dir": tmp_path, "workflow": "workflow-id"}
    assert "Errors" in result.output
    assert "- Workflow node 'build' requires missing shell binary 'bash'." in result.output
    assert '"workflowPath"' not in result.output


def test_doctor_json_reports_warnings_without_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    workflow = tmp_path / "agent.toml"
    workflow.write_text(
        """
[workflow]
id = "agent-flow"
name = "Agent Flow"

[agents.reviewer]
subscription = "codex"
working_dir = "."

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda _binary: None)

    result = runner.invoke(app, ["doctor", "--json", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["errors"] == []
    assert any(item["id"] == "provider.cli" for item in payload["warnings"])
    assert not (tmp_path / "schedules.db").exists()


def test_global_doctor_only_checks_configured_provider_clis(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "agent.toml"
    workflow.write_text(
        """
[workflow]
id = "agent-flow"
name = "Agent Flow"

[agents.reviewer]
subscription = "codex"
working_dir = "."

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
""",
        encoding="utf-8",
    )

    def fake_which(binary: str) -> str | None:
        if binary == "codex":
            return "/bin/codex"
        return None

    monkeypatch.setattr("gofer.core.health.shutil.which", fake_which)

    payload = run_health_checks(data_dir=tmp_path).to_dict()

    provider_diagnostics = [item for item in payload["diagnostics"] if item["id"] == "provider.cli"]
    assert provider_diagnostics == [
        {
            "id": "provider.cli",
            "severity": "ok",
            "subject": "codex",
            "message": "Configured provider CLI 'codex' is available.",
            "detail": {"binary": "codex", "path": "/bin/codex"},
        }
    ]


def test_doctor_reports_legacy_plaintext_provider_profile_secrets(
    tmp_path: Path,
) -> None:
    save_provider_profiles(
        {
            "legacy": ProviderProfile(
                name="legacy",
                subscription="codex",
                env={
                    "AWS_ACCESS_KEY_ID": "plaintext",
                    "CODEX_API_KEY": "plaintext",
                    "GOFER_TRACE": "1",
                },
            )
        },
        tmp_path,
    )

    payload = run_health_checks(data_dir=tmp_path).to_dict()

    diagnostics = [
        item for item in payload["warnings"] if item["id"] == "provider_profile.plaintext_secret"
    ]
    assert diagnostics == [
        {
            "id": "provider_profile.plaintext_secret",
            "severity": "warning",
            "subject": "legacy",
            "message": (
                "Provider profile 'legacy' stores sensitive env value(s) in "
                "plaintext: AWS_ACCESS_KEY_ID, CODEX_API_KEY. Move them to secret_refs."
            ),
            "detail": {"profile": "legacy", "env": ["AWS_ACCESS_KEY_ID", "CODEX_API_KEY"]},
        }
    ]


def test_shell_diagnostic_matches_executor_shell(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("gofer.core.health.sys.platform", "linux")
    monkeypatch.setattr(
        "gofer.core.health.shutil.which",
        lambda binary: "/bin/sh" if binary == "sh" else None,
    )

    payload = run_health_checks(data_dir=tmp_path).to_dict()

    assert any(
        item["id"] == "shell.available"
        and item["severity"] == "warning"
        and item["detail"]["binary"] == "bash"
        for item in payload["warnings"]
    )


def test_doctor_workflow_reports_missing_shell_as_blocking(
    monkeypatch,
    tmp_path: Path,
) -> None:
    script = tmp_path / "run.sh"
    script.write_text("echo ok\n", encoding="utf-8")
    workflow = tmp_path / "shell.toml"
    workflow.write_text(
        """
[workflow]
id = "shell-flow"
name = "Shell Flow"

[[nodes]]
id = "command"
type = "bash_command"
command = "echo ok"

[[nodes]]
id = "script"
type = "shell_script"
script_path = "run.sh"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.sys.platform", "linux")
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda _binary: None)

    result = runner.invoke(
        app,
        [
            "doctor",
            "--workflow",
            "shell-flow",
            "--json",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    shell_errors = [item for item in payload["errors"] if item["id"] == "workflow.shell_available"]
    assert {item["subject"] for item in shell_errors} == {"node:command", "node:script"}
    assert all(item["detail"]["binary"] == "bash" for item in shell_errors)


def test_doctor_workflow_json_exits_nonzero_for_blocking_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "agent.toml"
    workflow.write_text(
        """
[workflow]
id = "agent-flow"
name = "Agent Flow"

[agents.reviewer]
subscription = "codex"
working_dir = "missing-work"
prompt_path = "prompts/reviewer.md"

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
dynamic_count = 1
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda _binary: None)

    result = runner.invoke(
        app,
        [
            "doctor",
            "--workflow",
            "agent-flow",
            "--json",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    error_ids = {item["id"] for item in payload["errors"]}
    assert "workflow.provider_cli" in error_ids
    assert "workflow.working_dir" in error_ids
    assert "workflow.prompt_path" in error_ids


def test_workflow_health_ignores_stale_agent_prompt_when_node_overrides_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "node-prompt.md").write_text("Use this prompt.", encoding="utf-8")
    workflow = tmp_path / "node-prompt-override.toml"
    workflow.write_text(
        """
[workflow]
id = "node-prompt-override"
name = "Node Prompt Override"

[agents.reviewer]
subscription = "codex"
working_dir = "."
prompt_path = "prompts"

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
prompt_path = "node-prompt.md"
dynamic_count = 1
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda _binary: "/usr/bin/codex")

    diagnostics = workflow_health_diagnostics(workflow)

    assert not any(
        item.id == "workflow.prompt_path" and item.severity == "error" for item in diagnostics
    )


def test_workflow_health_catches_script_and_fanout_paths(monkeypatch, tmp_path: Path) -> None:
    workflow = tmp_path / "paths.toml"
    workflow.write_text(
        """
[workflow]
id = "path-flow"
name = "Path Flow"

[[nodes]]
id = "script"
type = "python_script"
script_path = "scripts/run.py"

[[nodes]]
id = "fan"
type = "loop"
[nodes.source]
type = "directory"
path = "inputs"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda binary: f"/bin/{binary}")

    payload = workflow_health_payload("path-flow", tmp_path)

    assert payload["ok"] is False
    messages = "\n".join(item["message"] for item in payload["errors"])
    assert "Script path does not exist" in messages
    assert "Directory fan-out path does not exist" in messages


def test_workflow_health_catches_file_operation_dependencies(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "file-ops.toml"
    workflow.write_text(
        """
[workflow]
id = "file-ops"
name = "File Ops"

[[nodes]]
id = "write"
type = "write_file"
path = "missing-parent/out.txt"
create_dirs = false

[[nodes]]
id = "copy"
type = "copy_file"
source_path = "missing-source.txt"
destination_path = "out/copy.txt"

[[nodes]]
id = "move"
type = "move_file"
source_path = "missing-move.txt"
destination_path = "out/move.txt"

[[nodes]]
id = "delete"
type = "delete_file"
path = "missing-delete.txt"

[[nodes]]
id = "prompt"
type = "prompt_file"
output_path = "missing-prompt-parent/prompt.txt"
create_dirs = false

[[nodes]]
id = "vectorize"
type = "local_vectorize"
source_path = "missing-docs"
index_path = "missing-index-parent/index.json"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda binary: f"/bin/{binary}")

    payload = workflow_health_payload(workflow, tmp_path)

    error_ids = {item["id"] for item in payload["errors"]}
    assert "workflow.write_file_path" in error_ids
    assert "workflow.file_source_path" in error_ids
    assert "workflow.file_destination_path" not in error_ids
    assert "workflow.delete_file_path" in error_ids
    assert "workflow.prompt_output_path" in error_ids
    assert "workflow.local_vector_source" in error_ids
    assert "workflow.local_vector_index" not in error_ids


def test_workflow_health_catches_deprecated_agent_fan_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "agent-fan.toml"
    workflow.write_text(
        """
[workflow]
id = "agent-fan"
name = "Agent Fan"

[agents.reviewer]
subscription = "codex"
working_dir = "."

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
[nodes.fan_source]
type = "directory"
path = "missing-inputs"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda binary: f"/bin/{binary}")

    payload = workflow_health_payload(workflow, tmp_path)

    assert any(item["id"] == "workflow.fanout_path" for item in payload["errors"])


def test_global_health_does_not_create_missing_data_dir(tmp_path: Path) -> None:
    missing_data_dir = tmp_path / "missing-data"

    report = run_health_checks(data_dir=missing_data_dir)

    assert report.ok is False
    assert not missing_data_dir.exists()
    assert any(item.id == "data_dir.access" for item in report.errors)


def test_global_health_reports_workflow_assistant_cli_packaging(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GOFER_CLI_SOURCE_PATH", raising=False)
    monkeypatch.setattr("gofer.ui.chat.sys.frozen", False, raising=False)
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda _binary: None)

    payload = run_health_checks(data_dir=tmp_path).to_dict()

    assert any(
        item["id"] == "packaging.gofer_cli"
        and item["severity"] == "warning"
        and "no authoritative 'gof' executable" in item["message"]
        for item in payload["warnings"]
    )


def test_global_health_rejects_workflow_assistant_cli_source_inside_data_dir(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "gof"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("GOFER_CLI_SOURCE_PATH", str(source))
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda binary: f"/bin/{binary}")

    payload = run_health_checks(data_dir=data_dir).to_dict()

    assert any(
        item["id"] == "packaging.gofer_cli"
        and item["severity"] == "warning"
        and "mutable Raticode data directory" in item["message"]
        for item in payload["warnings"]
    )


def test_workflow_health_catches_notification_readiness(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "notify.toml"
    workflow.write_text(
        """
[workflow]
id = "notify-flow"
name = "Notify Flow"

[[nodes]]
id = "notify"
type = "notification"
title = "Done"
body = "Workflow complete"

[[nodes]]
id = "approve"
type = "approval_gate"
message = "Continue?"
notify = true
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda binary: f"/bin/{binary}")

    payload = workflow_health_payload(workflow, tmp_path)

    warnings = [
        item for item in payload["warnings"] if item["id"] == "workflow.desktop_notifications"
    ]
    assert {item["subject"] for item in warnings} == {"node:notify", "node:approve"}
    assert all("DISPLAY" in item["message"] for item in warnings)


def test_workflow_health_catches_missing_notification_dbus_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "notify.toml"
    workflow.write_text(
        """
[workflow]
id = "notify-flow"
name = "Notify Flow"

[[nodes]]
id = "notify"
type = "notification"
title = "Done"
body = "Workflow complete"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda binary: f"/bin/{binary}")

    payload = workflow_health_payload(workflow, tmp_path)

    warnings = [
        item for item in payload["warnings"] if item["id"] == "workflow.desktop_notifications"
    ]
    assert len(warnings) == 1
    assert "D-Bus" in warnings[0]["message"]


def test_desktop_notification_health_passes_on_windows_with_powershell(monkeypatch) -> None:
    def fake_which(binary: str) -> str | None:
        if binary == "powershell.exe":
            return "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
        return f"/bin/{binary}"

    monkeypatch.setattr("gofer.core.health.sys.platform", "win32")
    monkeypatch.setattr("gofer.core.health.shutil.which", fake_which)

    payload = run_health_checks().to_dict()

    assert any(
        item["id"] == "desktop.notifications"
        and item["severity"] == "ok"
        and item["subject"] == "Windows"
        and "PowerShell" in item["message"]
        for item in payload["diagnostics"]
    )
    assert not any(item["id"] == "desktop.notifications" for item in payload["warnings"])


def test_desktop_notification_health_warns_on_windows_without_powershell(monkeypatch) -> None:
    monkeypatch.setattr("gofer.core.health.sys.platform", "win32")
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda _binary: None)

    payload = run_health_checks().to_dict()

    assert any(
        item["id"] == "desktop.notifications"
        and item["severity"] == "warning"
        and "PowerShell" in item["message"]
        for item in payload["warnings"]
    )


def test_workflow_health_catches_open_resource_auto_local_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "open.toml"
    workflow.write_text(
        """
[workflow]
id = "open-flow"
name = "Open Flow"

[[nodes]]
id = "missing"
type = "open_resource"
target = "missing.txt"

[[nodes]]
id = "url"
type = "open_resource"
target = "https://example.com/docs"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda binary: f"/bin/{binary}")

    payload = workflow_health_payload(workflow, tmp_path)

    open_resource_errors = [
        item for item in payload["errors"] if item["id"] == "workflow.open_resource"
    ]
    assert len(open_resource_errors) == 1
    assert open_resource_errors[0]["subject"] == "node:missing"
    assert "Open resource target does not exist" in open_resource_errors[0]["message"]


def test_workflow_health_passes_with_fake_provider_and_existing_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    prompts = tmp_path / "prompts"
    work.mkdir()
    prompts.mkdir()
    (prompts / "reviewer.md").write_text("review", encoding="utf-8")
    workflow = tmp_path / "ok.toml"
    workflow.write_text(
        """
[workflow]
id = "ok-flow"
name = "OK Flow"

[agents.reviewer]
subscription = "codex"
working_dir = "work"
prompt_path = "prompts/reviewer.md"

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "work"
dynamic_count = 1
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda binary: f"/bin/{binary}")

    report = run_health_checks(data_dir=tmp_path, workflow=workflow)

    assert report.ok is True
    assert report.errors == []


def test_ui_workflow_payload_includes_inline_health_errors(monkeypatch, tmp_path: Path) -> None:
    workflow = tmp_path / "agent.toml"
    workflow.write_text(
        """
[workflow]
id = "agent-flow"
name = "Agent Flow"

[agents.reviewer]
subscription = "codex"
working_dir = "."

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
dynamic_count = 1
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.health.shutil.which", lambda _binary: None)

    payload = list_workflow_payloads(tmp_path)

    workflow_payload = payload["workflows"][0]
    assert workflow_payload["healthErrors"][0]["id"] == "workflow.provider_cli"


def test_workflow_health_reports_missing_direct_api_secret(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workflow_path = tmp_path / "direct.toml"
    workflow_path.write_text(
        """
[workflow]
id = "direct"
name = "Direct"

[agents.writer]
subscription = "openai_api"
working_dir = "."
model = "gpt-5-mini"

[[nodes]]
id = "ask"
type = "agent"
agent_id = "writer"
working_dir = "."
""",
        encoding="utf-8",
    )

    payload = workflow_health_payload(str(workflow_path), tmp_path)
    error_ids = {item["id"] for item in payload["errors"]}

    assert "workflow.provider_secret" in error_ids
    assert "workflow.provider_cli" not in error_ids
