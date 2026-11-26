from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.health import (
    HealthDiagnostic,
    HealthReport,
    run_health_checks,
)
from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles

runner = CliRunner()


def _doctor_report(diagnostics: list[HealthDiagnostic], tmp_path: Path) -> HealthReport:
    return HealthReport(
        ok=not any(item.severity == "error" for item in diagnostics),
        diagnostics=diagnostics,
        data_dir=tmp_path,
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
        and "no authoritative 'Raticode' executable" in item["message"]
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
