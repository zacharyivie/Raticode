from __future__ import annotations

import json
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


def phone_source() -> str:
    return """Rattish: 1
Workflow:
  name: Phone notice
  inputs:
    summary:
      schema: {"type": "string"}
      required: true
Node notify:
  type: notification
  channel: phone
  title: Backup finished
  urgency: critical
  with:
    body: input.summary
"""


def pair_phone(data_dir: Path, *, role: str = "controller", state: str = "active") -> None:
    """The columns of the device registry the phone check reads, with one peer."""
    import sqlite3

    devices = data_dir / "devices"
    devices.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(devices / "devices.sqlite3")
    db.execute("CREATE TABLE peers (device_id TEXT, role TEXT, state TEXT)")
    db.execute("INSERT INTO peers VALUES ('phone-1', ?, ?)", (role, state))
    db.commit()
    db.close()


@pytest.mark.anyio
async def test_phone_channel_stores_a_notice_for_the_next_sync(tmp_path: Path) -> None:
    from gofer.core.phone_alerts import PhoneAlertStore

    data_dir = tmp_path / "data"
    adapter = RecordingNotificationAdapter()
    result = await execute_workflow(
        compile_source(phone_source(), tmp_path),
        workflow_inputs={"summary": "12 files copied."},
        notification_adapter=adapter,
        data_dir=data_dir,
        run_id="run-42",
    )

    assert result.outcome == "pass"
    assert result.latest_node_outputs["notify"] == {
        "sent": True,
        "title": "Backup finished",
        "body": "12 files copied.",
        "channel": "phone",
        "urgency": "critical",
    }
    # The phone channel never goes through the desktop, webhook or email adapter.
    assert adapter.notifications == []
    stored = PhoneAlertStore(data_dir).after(0)
    assert stored["last"] == 1 and stored["more"] is False
    (notice,) = stored["alerts"]
    assert {key: notice[key] for key in ("reason", "source", "title", "body", "run_id")} == {
        "reason": "workflow",
        "source": "notification",
        "title": "Backup finished",
        "body": "12 files copied.",
        "run_id": "run-42",
    }
    assert notice["workflow_id"] == "notification"
    assert notice["workflow_name"] == "Phone notice"
    assert (data_dir / "phone-alerts.sqlite3").stat().st_mode & 0o077 == 0


@pytest.mark.anyio
async def test_phone_channel_reports_a_store_it_cannot_write(tmp_path: Path) -> None:
    blocked = tmp_path / "data"
    blocked.write_text("not a folder")

    result = await execute_node(
        compile_source(phone_source(), tmp_path),
        "notify",
        workflow_inputs={"summary": "x"},
        data_dir=blocked,
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_NOTIFICATION_SEND_FAILED"
    assert result.error.details == {"channel": "phone"}


@pytest.mark.anyio
async def test_phone_channel_can_be_chosen_by_a_binding(tmp_path: Path) -> None:
    from gofer.core.phone_alerts import PhoneAlertStore

    source = """Rattish: 1
Workflow:
  name: Bound channel
  inputs:
    where:
      schema: {"type": "string", "enum": ["desktop", "phone"]}
      required: true
Node notify:
  type: notification
  title: Done
  with:
    channel: input.where
"""
    result = await execute_node(
        compile_source(source, tmp_path),
        "notify",
        workflow_inputs={"where": "phone"},
        data_dir=tmp_path / "data",
    )

    assert result.outcome == "success" and result.output["channel"] == "phone"
    assert [a["title"] for a in PhoneAlertStore(tmp_path / "data").after(0)["alerts"]] == ["Done"]


def test_phone_preflight_warns_when_no_phone_is_paired(tmp_path: Path) -> None:
    ir = compile_source(phone_source(), tmp_path)
    data_dir = tmp_path / "data"

    result = run_preflight(ir, data_dir=data_dir)

    # A warning: the run still stores the notice, so it may go ahead.
    assert result.ready
    assert [(d.code, d.severity) for d in result.diagnostics] == [
        ("RATTISH_PREFLIGHT_NOTIFICATION_NO_PHONE", "warning")
    ]
    # Another desktop, or a phone whose pairing never finished, isn't a paired phone.
    pair_phone(data_dir, role="desktop")
    assert run_preflight(ir, data_dir=data_dir).diagnostics
    other = tmp_path / "other"
    pair_phone(other, state="pending")
    assert run_preflight(ir, data_dir=other).diagnostics


def test_phone_preflight_is_quiet_with_a_paired_phone(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    pair_phone(data_dir)

    result = run_preflight(compile_source(phone_source(), tmp_path), data_dir=data_dir)

    assert result.ready and result.diagnostics == ()


def test_phone_alert_store_positions_and_pruning(tmp_path: Path, monkeypatch) -> None:
    from gofer.core import phone_alerts

    store = phone_alerts.PhoneAlertStore(tmp_path)
    nothing = {"last": 0, "more": False, "dropped": 0, "alerts": []}
    # No file yet: nothing to read, and reading doesn't create one.
    assert store.after(None) == nothing
    assert not store.path.exists()
    monkeypatch.setattr(phone_alerts, "KEEP", 3)
    monkeypatch.setattr(phone_alerts, "READ_ROWS", 2)
    for index in range(5):
        store.add(title=f"t{index}", body="b" * 5000, workflow_id="wf", run_id=f"r{index}")
    # Without a position, only the current one comes back.
    assert store.after(None) == {**nothing, "last": 5}
    page = store.after(0)
    # The oldest two were pruned, and a reader from before them hears it missed two.
    assert [a["title"] for a in page["alerts"]] == ["t2", "t3"] and page["more"] is True
    assert page["dropped"] == 2
    assert len(page["alerts"][0]["body"]) == phone_alerts.BODY_LIMIT
    assert store.after(1)["dropped"] == 1
    rest = store.after(page["last"])
    assert [a["id"] for a in rest["alerts"]] == ["workflow-5"] and rest["more"] is False
    # The next page of the same backlog reports nothing missed again.
    assert rest["dropped"] == 0
    # A position past the end, say after the file was removed, starts over from now.
    store.path.unlink()
    store.add(title="new", body="", workflow_id="wf", run_id="r")
    assert store.after(rest["last"]) == {**nothing, "last": 1}


def test_phone_alert_store_caps_notices_per_run(tmp_path: Path, monkeypatch) -> None:
    from gofer.core import phone_alerts

    monkeypatch.setattr(phone_alerts, "RUN_CAP", 3)
    store = phone_alerts.PhoneAlertStore(tmp_path)
    for index in range(3):
        stored = store.add(
            title=f"t{index}", body="b", workflow_id="wf", workflow_name="Nightly", run_id="run-1"
        )
        assert stored["more_from_run"] == 0
    other = store.add(title="other run", body="", workflow_id="wf", run_id="run-2")
    first_more = store.add(title="t3", body="secret", workflow_id="wf", run_id="run-1")
    # Past the cap the run's notice says how many more, without the folded words.
    assert (first_more["more_from_run"], first_more["title"], first_more["body"]) == (
        1,
        "",
        "And 1 more from this run.",
    )
    assert first_more["workflow_name"] == "" and first_more["run_id"] == "run-1"
    seen = store.after(0)["last"]
    for index in range(4, 8):
        last_more = store.add(title=f"t{index}", body="", workflow_id="wf", run_id="run-1")
    assert last_more["more_from_run"] == 5
    # One "And N more" notice per run: it moved to the newest position with the new count,
    # so a phone that already showed it hears the new count.
    alerts = store.after(0)["alerts"]
    assert [a["more_from_run"] for a in alerts] == [0, 0, 0, 0, 5]
    assert [a["title"] for a in alerts[:4]] == ["t0", "t1", "t2", "other run"]
    assert alerts[-1]["id"] == last_more["id"] != first_more["id"]
    assert [a["body"] for a in store.after(seen)["alerts"]] == ["And 5 more from this run."]
    # The moved notice isn't counted as missed: nothing was pruned.
    assert store.after(0)["dropped"] == 0
    # Other runs keep their own allowance, and a notice with no run is never folded.
    assert other["more_from_run"] == 0
    for _ in range(5):
        assert store.add(title="x", body="", workflow_id="wf", run_id="")["more_from_run"] == 0


def test_phone_alert_store_reads_a_file_from_before_the_cap(tmp_path: Path) -> None:
    import sqlite3

    from gofer.core import phone_alerts

    db = sqlite3.connect(tmp_path / phone_alerts.ALERTS_FILE)
    db.execute(
        "CREATE TABLE alerts (seq INTEGER PRIMARY KEY AUTOINCREMENT, reason TEXT NOT NULL, "
        "source TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, workflow_id TEXT NOT "
        "NULL, workflow_name TEXT NOT NULL, run_id TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO alerts (reason, source, title, body, workflow_id, workflow_name, run_id, "
        "created_at) VALUES ('workflow','notification','old','','wf','','r','2026-11-14T00:00:00')"
    )
    db.commit()
    db.close()

    store = phone_alerts.PhoneAlertStore(tmp_path)
    store.add(title="new", body="", workflow_id="wf", run_id="r")

    found = store.after(0)
    assert [(a["title"], a["more_from_run"]) for a in found["alerts"]] == [("old", 0), ("new", 0)]


@pytest.mark.anyio
async def test_phone_notices_never_carry_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core.phone_alerts import PhoneAlertStore

    monkeypatch.setenv("PHONE_NOTICE_TOKEN", "tok-abc-123")
    source = phone_source().replace("body: input.summary", 'body: secret["PHONE_NOTICE_TOKEN"]')
    data_dir = tmp_path / "data"
    result = await execute_workflow(
        compile_source(source, tmp_path),
        workflow_inputs={"summary": "unused"},
        notification_adapter=RecordingNotificationAdapter(),
        data_dir=data_dir,
        run_id="run-43",
    )

    assert result.outcome == "pass"
    (notice,) = PhoneAlertStore(data_dir).after(0)["alerts"]
    assert "tok-abc-123" not in json.dumps(notice)
    assert notice["body"] == "[REDACTED]"
