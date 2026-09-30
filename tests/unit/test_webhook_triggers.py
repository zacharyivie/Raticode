from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.workflow import AgenticWorkflow
from gofer.ui.api import (
    WorkflowTriggerError,
    latest_workflow_log_payload,
    list_workflow_payloads,
    list_workflow_run_logs_payload,
    replay_workflow_trigger_payload,
    trigger_workflow_payload,
    update_workflow_payload,
    workflow_plan_payload,
    workflow_run_log_payload,
)

runner = CliRunner()


def _write_webhook_workflow(path: Path) -> None:
    path.write_text(
        """
[workflow]
id = "hooked"
name = "Hooked"

[workflow.webhooks.github]
enabled = true
token = "secret-token"
fanout_path = "payload.items"
source = "github"

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s|%s|%s" "$ISSUE" "$EVENT" "$SOURCE"'

[nodes.inputs]
"env.ISSUE" = "{{trigger.payload.issue.number}}"
"env.EVENT" = "{{trigger.headers.x_github_event}}"
"env.SOURCE" = "{{trigger.source}}"
""".strip(),
        encoding="utf-8",
    )


def test_webhook_trigger_config_round_trips(tmp_path: Path) -> None:
    workflow_path = tmp_path / "hooked.toml"
    _write_webhook_workflow(workflow_path)

    workflow = AgenticWorkflow.from_file(workflow_path)
    trigger = workflow.config.webhooks["github"]

    assert trigger.enabled is True
    assert trigger.token == "secret-token"
    assert trigger.fanout_path == "payload.items"
    assert trigger.source == "github"

    saved = tmp_path / "saved.toml"
    workflow.to_file(saved)
    reloaded = AgenticWorkflow.from_file(saved)

    assert reloaded.config.webhooks["github"].enabled is True
    assert reloaded.config.webhooks["github"].token == "secret-token"


def test_update_workflow_payload_preserves_masked_webhook_token(tmp_path: Path) -> None:
    workflow_path = tmp_path / "hooked.toml"
    _write_webhook_workflow(workflow_path)
    payload = list_workflow_payloads(tmp_path)["workflows"][0]

    assert payload["webhooks"]["github"]["tokenConfigured"] is True
    assert "token" not in payload["webhooks"]["github"]

    update_workflow_payload("hooked", payload, tmp_path)

    reloaded = AgenticWorkflow.from_file(workflow_path)
    assert reloaded.config.webhooks["github"].token == "secret-token"


@pytest.mark.anyio
@pytest.mark.parametrize("token", ["wrong", "invalid-\u00e9"])
async def test_webhook_trigger_rejects_unauthorized_request(tmp_path: Path, token: str) -> None:
    _write_webhook_workflow(tmp_path / "hooked.toml")

    with pytest.raises(WorkflowTriggerError, match="Unauthorized"):
        await trigger_workflow_payload(
            "hooked",
            "github",
            tmp_path,
            payload={"issue": {"number": 7}},
            token=token,
        )


@pytest.mark.anyio
async def test_webhook_trigger_rejects_enabled_trigger_without_auth_before_run(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "should-not-exist"
    (tmp_path / "open.toml").write_text(
        f"""
[workflow]
id = "open"
name = "Open"

[workflow.webhooks.default]
enabled = true

[[nodes]]
id = "write"
type = "bash_command"
command = "touch {marker}"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowTriggerError, match="no authentication configured"):
        await trigger_workflow_payload("open", "default", tmp_path, payload={})

    assert not marker.exists()


@pytest.mark.anyio
@pytest.mark.parametrize("token", ["env-secret", "secret-\u00e9"])
async def test_webhook_trigger_token_env_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str,
) -> None:
    monkeypatch.setenv("GOFER_TEST_WEBHOOK_TOKEN", token)
    (tmp_path / "env-hook.toml").write_text(
        """
[workflow]
id = "env-hook"
name = "Env Hook"

[workflow.webhooks.default]
enabled = true
token_env = "GOFER_TEST_WEBHOOK_TOKEN"

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s" "$MESSAGE"'

[nodes.inputs]
"env.MESSAGE" = "{{trigger.payload.message}}"
""".strip(),
        encoding="utf-8",
    )

    result = await trigger_workflow_payload(
        "env-hook",
        "default",
        tmp_path,
        payload={"message": "ok"},
        token=token,
    )

    assert result["run"]["success"] is True
    assert result["run"]["nodeOutputs"]["echo"]["output"] == "ok"


@pytest.mark.anyio
async def test_webhook_trigger_binds_required_workflow_inputs(tmp_path: Path) -> None:
    (tmp_path / "input-hook.toml").write_text(
        """
[workflow]
id = "input-hook"
name = "Input Hook"

[workflow.inputs.issue]
type = "number"
required = true

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true

[workflow.webhooks.default.input_bindings]
issue = "{{trigger.payload.issue.number}}"

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s" "{{inputs.issue}}"'
""".strip(),
        encoding="utf-8",
    )

    result = await trigger_workflow_payload(
        "input-hook",
        "default",
        tmp_path,
        payload={"issue": {"number": 42}},
    )

    assert result["run"]["success"] is True
    assert result["run"]["inputs"] == {"issue": 42}
    assert result["run"]["nodeOutputs"]["echo"]["output"] == "42"


@pytest.mark.anyio
async def test_webhook_replay_preserves_explicit_inputs(tmp_path: Path) -> None:
    (tmp_path / "explicit-input-hook.toml").write_text(
        """
[workflow]
id = "explicit-input-hook"
name = "Explicit Input Hook"

[workflow.inputs.environment]
required = true

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s" "{{inputs.environment}}"'
""".strip(),
        encoding="utf-8",
    )
    original = await trigger_workflow_payload(
        "explicit-input-hook",
        "default",
        tmp_path,
        payload={},
        inputs={"environment": "staging"},
    )

    replayed = await replay_workflow_trigger_payload(
        "explicit-input-hook",
        str(original["runId"]),
        tmp_path,
    )

    assert replayed["run"]["success"] is True
    assert replayed["run"]["inputs"] == {"environment": "staging"}
    assert replayed["run"]["nodeOutputs"]["echo"]["output"] == "staging"


@pytest.mark.anyio
async def test_webhook_replay_restores_secret_explicit_inputs_without_plaintext(
    tmp_path: Path,
) -> None:
    (tmp_path / "secret-input-hook.toml").write_text(
        """
[workflow]
id = "secret-input-hook"
name = "Secret Input Hook"

[workflow.inputs.token]
type = "secret"
required = true

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s" "{{inputs.token}}"'
""".strip(),
        encoding="utf-8",
    )
    original = await trigger_workflow_payload(
        "secret-input-hook",
        "default",
        tmp_path,
        payload={},
        inputs={"token": "replay-token-314"},
    )
    sidecar = (tmp_path / "logs" / "secret-input-hook" / str(original["runId"])).with_suffix(
        ".trigger.json"
    )
    assert "replay-token-314" not in sidecar.read_text(encoding="utf-8")

    replayed = await replay_workflow_trigger_payload(
        "secret-input-hook",
        str(original["runId"]),
        tmp_path,
    )

    assert replayed["run"]["success"] is True
    assert replayed["run"]["inputs"] == {"token": "***"}
    assert replayed["run"]["nodeOutputs"]["echo"]["output"] == "***"


@pytest.mark.anyio
async def test_webhook_trigger_allows_explicit_unauthenticated_opt_in(
    tmp_path: Path,
) -> None:
    (tmp_path / "local-hook.toml").write_text(
        """
[workflow]
id = "local-hook"
name = "Local Hook"

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s" "$MESSAGE"'

[nodes.inputs]
"env.MESSAGE" = "{{trigger.payload.message}}"
""".strip(),
        encoding="utf-8",
    )

    plan = workflow_plan_payload("local-hook", tmp_path)
    webhook_plan = plan["triggerContext"]["webhooks"]["default"]
    assert webhook_plan["risk"] == "high"
    assert "unauthenticated_allowed" in webhook_plan["riskReasons"]

    result = await trigger_workflow_payload(
        "local-hook",
        "default",
        tmp_path,
        payload={"message": "ok"},
    )

    assert result["run"]["success"] is True
    assert result["run"]["nodeOutputs"]["echo"]["output"] == "ok"


@pytest.mark.anyio
async def test_webhook_trigger_interpolates_payload_headers_and_saves_replay(
    tmp_path: Path,
) -> None:
    _write_webhook_workflow(tmp_path / "hooked.toml")

    result = await trigger_workflow_payload(
        "hooked",
        "github",
        tmp_path,
        payload={
            "issue": {"number": 42},
            "items": [{"title": "first", "token": "item-token"}],
            "password": "payload-password",
            "credentials": "provider-credentials",
            "nested": {"api_key": "nested-key"},
        },
        headers={
            "Authorization": "Bearer secret-token",
            "X-Gofer-Webhook-Token": "secret-token",
            "X-Gofer-UI-Token": "desktop-api-secret",
            "X-Gofer-Desktop-Grant-Secret": "desktop-grant-secret",
            "Cookie": "session=cookie-secret",
            "Proxy-Authorization": "Basic proxy-secret",
            "X-GitHub-Event": "issues",
        },
        source="github",
        token="secret-token",
    )

    run = result["run"]
    assert run["success"] is True
    assert run["nodeOutputs"]["echo"]["output"] == "42|issues|github"

    log_payload = workflow_run_log_payload("hooked", str(result["runId"]), tmp_path)
    assert "trigger=" in log_payload["logText"]
    assert '"requestId":' in log_payload["logText"]
    assert "payload-password" not in log_payload["logText"]
    assert "item-token" not in log_payload["logText"]
    assert "provider-credentials" not in log_payload["logText"]
    assert "nested-key" not in log_payload["logText"]

    trigger_sidecar = tmp_path / "logs" / "hooked" / str(result["runId"])
    replay_payload = json.loads(trigger_sidecar.with_suffix(".trigger.json").read_text())
    assert replay_payload["payload"]["issue"]["number"] == 42
    assert replay_payload["payload"]["password"] == "***"
    assert replay_payload["payload"]["credentials"] == "***"
    assert replay_payload["payload"]["items"][0]["token"] == "***"
    assert replay_payload["payload"]["nested"]["api_key"] == "***"
    assert replay_payload["payloadSanitized"] is True
    assert "sanitized before replay storage" in replay_payload["replayNotice"]
    assert replay_payload["headers"]["x_github_event"] == "issues"
    assert "authorization" not in replay_payload["headers"]
    assert "x_gofer_webhook_token" not in replay_payload["headers"]
    assert replay_payload["headers"] == {"x_github_event": "issues"}
    for secret in ("desktop-api-secret", "desktop-grant-secret", "cookie-secret", "proxy-secret"):
        assert secret not in log_payload["logText"]

    runs = list_workflow_run_logs_payload("hooked", tmp_path)["runs"]
    assert runs[0]["triggerType"] == "webhook"
    assert runs[0]["triggerId"] == "github"
    assert runs[0]["hasTriggerReplay"] is True


@pytest.mark.anyio
async def test_webhook_trigger_payload_array_feeds_trigger_event_fanout(
    tmp_path: Path,
) -> None:
    (tmp_path / "fanout.toml").write_text(
        """
[workflow]
id = "fanout"
name = "Fanout"

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true
fanout_path = "payload.items"

[[nodes]]
id = "events"
type = "loop"

[nodes.source]
type = "trigger_events"

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s" "$TITLE"'

[nodes.inputs]
"env.TITLE" = "loop.title"

[[edges]]
from = "events"
to = "echo"
""".strip(),
        encoding="utf-8",
    )

    result = await trigger_workflow_payload(
        "fanout",
        "default",
        tmp_path,
        payload={
            "items": [
                {"title": "one", "password": "first-secret"},
                {"title": "two", "token": "second-secret"},
            ]
        },
    )

    assert result["run"]["nodeOutputs"]["events"]["data"]["count"] == 2
    assert "node output:\none" in result["run"]["logText"]
    assert "node output:\ntwo" in result["run"]["logText"]
    assert "first-secret" not in result["run"]["logText"]
    assert "second-secret" not in result["run"]["logText"]


@pytest.mark.anyio
async def test_webhook_trigger_replay_uses_sanitized_saved_payload(tmp_path: Path) -> None:
    _write_webhook_workflow(tmp_path / "hooked.toml")
    original = await trigger_workflow_payload(
        "hooked",
        "github",
        tmp_path,
        payload={"issue": {"number": 9}, "items": [], "token": "payload-token"},
        headers={"X-GitHub-Event": "issues"},
        token="secret-token",
    )

    replay = await replay_workflow_trigger_payload(
        "hooked",
        str(original["runId"]),
        tmp_path,
        trigger_id="github",
    )

    assert replay["run"]["success"] is True
    assert replay["run"]["nodeOutputs"]["echo"]["output"] == "9|issues|replay:" + str(
        original["runId"]
    )
    assert replay["replay"]["payload"]["token"] == "***"


@pytest.mark.anyio
async def test_webhook_raw_payload_retention_requires_opt_in_and_logs_are_masked(
    tmp_path: Path,
) -> None:
    (tmp_path / "raw-hook.toml").write_text(
        """
[workflow]
id = "raw-hook"
name = "Raw Hook"

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true
store_raw_payload = true
sensitive_payload_fields = ["payload.private_code"]

[[nodes]]
id = "echo"
type = "bash_command"
command = '''
test "$PASSWORD" = "payload-password" &&
test "$PRIVATE_CODE" = "private-value" &&
printf "%s|%s" "$PASSWORD" "$PRIVATE_CODE"
'''

[nodes.inputs]
"env.PASSWORD" = "{{trigger.payload.password}}"
"env.PRIVATE_CODE" = "{{trigger.payload.private_code}}"
""".strip(),
        encoding="utf-8",
    )

    plan = workflow_plan_payload("raw-hook", tmp_path)
    assert plan["triggerContext"]["webhooks"]["default"]["storeRawPayload"] is True
    assert plan["triggerContext"]["webhooks"]["default"]["risk"] == "high"
    assert "unauthenticated_allowed" in plan["triggerContext"]["webhooks"]["default"]["riskReasons"]
    assert any("stores raw replay payloads" in warning for warning in plan["warnings"])

    result = await trigger_workflow_payload(
        "raw-hook",
        "default",
        tmp_path,
        payload={"password": "payload-password", "private_code": "private-value"},
    )

    assert result["run"]["nodeOutputs"]["echo"]["output"] == "***|***"
    assert "payload-password" not in result["run"]["logText"]
    assert "private-value" not in result["run"]["logText"]
    assert result["replay"]["payload"]["password"] == "payload-password"
    assert result["replay"]["payload"]["private_code"] == "private-value"
    assert result["replay"]["payloadSanitized"] is False
    latest = latest_workflow_log_payload("raw-hook", tmp_path)
    assert "payload-password" not in latest["logText"]
    assert "private-value" not in latest["logText"]
    assert latest["nodeOutputs"]["echo"]["output"] == "***|***"
    trigger_line = next(
        line for line in result["run"]["logText"].splitlines() if "trigger=" in line
    )
    assert "payload-password" not in trigger_line
    assert "private-value" not in trigger_line


@pytest.mark.anyio
async def test_webhook_configured_sensitive_payload_fields_mask_default_storage(
    tmp_path: Path,
) -> None:
    (tmp_path / "custom-secret.toml").write_text(
        """
[workflow]
id = "custom-secret"
name = "Custom Secret"

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true
fanout_path = "payload.items"
sensitive_payload_fields = ["payload.private_code"]

[[nodes]]
id = "events"
type = "loop"

[nodes.source]
type = "trigger_events"
""".strip(),
        encoding="utf-8",
    )

    result = await trigger_workflow_payload(
        "custom-secret",
        "default",
        tmp_path,
        payload={"items": [{"private_code": "private-value", "name": "visible"}]},
    )

    assert result["run"]["nodeOutputs"]["events"]["data"]["count"] == 1
    assert "private-value" not in result["run"]["logText"]
    assert "visible" in result["run"]["logText"]
    trigger_sidecar = tmp_path / "logs" / "custom-secret" / str(result["runId"])
    replay_payload = json.loads(trigger_sidecar.with_suffix(".trigger.json").read_text())
    assert replay_payload["payload"]["items"][0]["private_code"] == "***"
    assert replay_payload["payload"]["items"][0]["name"] == "visible"


def test_workflow_trigger_cli_runs_webhook(tmp_path: Path) -> None:
    _write_webhook_workflow(tmp_path / "hooked.toml")

    result = runner.invoke(
        app,
        [
            "workflow",
            "trigger",
            "hooked",
            "--trigger-id",
            "github",
            "--payload-json",
            '{"issue":{"number":5},"items":[]}',
            "--header",
            "X-GitHub-Event: issues",
            "--token",
            "secret-token",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Triggered hooked:github" in result.output
