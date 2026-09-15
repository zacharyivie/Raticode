from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.core.approvals import RecordingNotificationAdapter
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node
from gofer.rattish.workflow_runtime import execute_workflow

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "notification.json"],
    )


def compile_source(source: str, project_root: Path) -> dict[str, Any]:
    return compiler().compile(source, CompileContext("notification", project_root)).ir


@pytest.mark.anyio
async def test_notification_binds_content_and_sends_webhook_configuration(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1
Workflow:
  name: Bound notification
  inputs:
    message:
      schema: {"type": "string"}
      required: true
    payload:
      schema: {"type": "object"}
      required: true
Node notify:
  type: notification
  title: Build finished
  channel: WEBHOOK
  webhook-url: https://example.com/hooks/build
  with:
    body: input.message
    payload: input.payload
"""
    adapter = RecordingNotificationAdapter()

    result = await execute_workflow(
        compile_source(source, tmp_path),
        workflow_inputs={"message": "Artifacts ready", "payload": {"state": "ready"}},
        notification_adapter=adapter,
    )

    assert result.outcome == "pass"
    assert result.latest_node_outputs["notify"] == {
        "sent": True,
        "title": "Build finished",
        "body": "Artifacts ready",
        "channel": "webhook",
        "urgency": "normal",
    }
    assert len(adapter.notifications) == 1
    sent = adapter.notifications[0]
    assert sent.webhook_url == "https://example.com/hooks/build"
    assert sent.payload == {"state": "ready"}
    assert sent.timeout_seconds == 30


@pytest.mark.anyio
async def test_notification_binds_email_credentials_without_publishing_them(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1
Workflow:
  name: Bound email notification
  inputs:
    password:
      schema: {"type": "string"}
      required: true
Node notify:
  type: notification
  channel: email
  email-from: raticode@example.com
  email-to:
    - owner@example.com
  smtp-host: smtp.example.com
  smtp-username: raticode
  with:
    smtp-password: input.password
"""
    adapter = RecordingNotificationAdapter()

    result = await execute_node(
        compile_source(source, tmp_path),
        "notify",
        workflow_inputs={"password": "runtime-secret"},
        notification_adapter=adapter,
    )

    assert result.outcome == "success"
    assert "runtime-secret" not in str(result.output)
    assert adapter.notifications[0].smtp_password == "runtime-secret"


@pytest.mark.anyio
async def test_notification_adapter_failure_has_structured_error(tmp_path: Path) -> None:
    class FailingAdapter:
        async def send(self, notification) -> None:
            _ = notification
            raise RuntimeError("webhook unavailable")

    source = """Rattish: 1
Workflow:
  name: Failed notification
Node notify:
  type: notification
  channel: webhook
  webhook-url: https://example.com/hooks/build
"""

    result = await execute_node(
        compile_source(source, tmp_path),
        "notify",
        notification_adapter=FailingAdapter(),
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.kind == "network"
    assert result.error.code == "RATTISH_NOTIFICATION_SEND_FAILED"


def test_notification_rejects_unknown_channel(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Invalid notification
Node notify:
  type: notification
  channel: pager
"""

    with pytest.raises(RattishCompileError) as caught:
        compile_source(source, tmp_path)

    assert "RATTISH_INVALID_FIELD_VALUE" in {item.code for item in caught.value.diagnostics}


def test_notification_preflight_requires_webhook_destination(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Missing webhook
Node notify:
  type: notification
  channel: slack
"""

    result = run_preflight(compile_source(source, tmp_path), data_dir=tmp_path / "data")

    assert not result.ready
    assert [item.code for item in result.diagnostics] == [
        "RATTISH_PREFLIGHT_NOTIFICATION_CONFIGURATION"
    ]


def test_notification_preflight_accepts_complete_email_configuration(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Email notification
Node notify:
  type: notification
  channel: email
  email-from: raticode@example.com
  email-to:
    - owner@example.com
  smtp-host: smtp.example.com
"""

    result = run_preflight(compile_source(source, tmp_path), data_dir=tmp_path / "data")

    assert result.ready


def test_notification_plaintext_smtp_password_warns_without_blocking_ir(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1
Workflow:
  name: Plaintext notification credential
Node notify:
  type: notification
  channel: email
  email-from: raticode@example.com
  email-to: ["owner@example.com"]
  smtp-host: smtp.example.com
  smtp-password: visible-password
"""

    result = compiler().compile(source, CompileContext("notification-warning", tmp_path))

    assert result.ir["nodes"][0]["configuration"]["smtp_password"] == "visible-password"
    assert [item.code for item in result.diagnostics] == ["RATTISH_SUSPECTED_PLAINTEXT_SECRET"]
