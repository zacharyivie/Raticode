from __future__ import annotations

import json
import ssl
from unittest.mock import MagicMock

import pytest

from gofer.core.approvals import (
    DesktopNotificationAdapter,
    MultiChannelNotificationAdapter,
    Notification,
    _send_email_sync,
)
from gofer.core.http import HttpRequest, HttpResponse
from gofer.core.operations import HttpRetryPolicy


class FakeHttpClient:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_multi_channel_notification_adapter_retries_webhook_status() -> None:
    http_client = FakeHttpClient(
        [
            HttpResponse(status=503, headers={}, body=b"try again"),
            HttpResponse(status=202, headers={}, body=b"ok"),
        ]
    )
    adapter = MultiChannelNotificationAdapter(http_client=http_client)

    await adapter.send(
        Notification(
            title="Done",
            body="Complete",
            channel="slack",
            webhook_url="https://hooks.example.test/services/token",
            retry=HttpRetryPolicy(attempts=2, retry_on_statuses=[503]),
            timeout_seconds=2,
        )
    )

    assert len(http_client.requests) == 2
    request = http_client.requests[0]
    assert request.method == "POST"
    assert request.url == "https://hooks.example.test/services/token"
    assert request.timeout_seconds == 2
    assert b"*Done*\\nComplete" in (request.body or b"")


@pytest.mark.parametrize("starttls", [True, False])
def test_email_notification_verifies_tls_before_authentication(monkeypatch, starttls) -> None:
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    monkeypatch.setattr("gofer.core.approvals.smtplib.SMTP", lambda *args, **kwargs: smtp)

    _send_email_sync(
        Notification(
            title="Done",
            body="Complete",
            channel="email",
            email_from="sender@example.com",
            email_to=["recipient@example.com"],
            smtp_host="smtp.example.com",
            smtp_username="sender",
            smtp_password="credential",
            smtp_starttls=starttls,
        )
    )

    if starttls:
        context = smtp.starttls.call_args.kwargs["context"]
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert [call[0] for call in smtp.method_calls] == ["starttls", "login", "send_message"]
    else:
        smtp.starttls.assert_not_called()
    smtp.login.assert_called_once_with("sender", "credential")
    message = smtp.send_message.call_args.args[0]
    assert message["To"] == "recipient@example.com"
    assert message.get_content().strip() == "Complete"


def test_email_notification_does_not_send_credentials_after_tls_failure(monkeypatch) -> None:
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.starttls.side_effect = ssl.SSLCertVerificationError("Untrusted SMTP certificate")
    monkeypatch.setattr("gofer.core.approvals.smtplib.SMTP", lambda *args, **kwargs: smtp)

    with pytest.raises(ssl.SSLCertVerificationError, match="Untrusted SMTP certificate"):
        _send_email_sync(
            Notification(
                title="Done",
                body="Complete",
                channel="email",
                email_from="sender@example.com",
                email_to=["recipient@example.com"],
                smtp_host="smtp.example.com",
                smtp_username="sender",
                smtp_password="credential",
            )
        )

    smtp.login.assert_not_called()
    smtp.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_linux_notification_treats_text_as_positional_arguments(monkeypatch) -> None:
    calls = []

    async def fake_run_subprocess(cmd, **kwargs):
        calls.append(cmd)
        return 0, "", ""

    monkeypatch.setattr("gofer.core.approvals.sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("gofer.core.approvals.shutil.which", lambda _binary: "/bin/notify-send")
    monkeypatch.setattr("gofer.core.approvals.run_subprocess", fake_run_subprocess)

    await DesktopNotificationAdapter().send(Notification(title="--help", body="--version"))

    assert calls == [["/bin/notify-send", "-u", "normal", "--", "--help", "--version"]]


@pytest.mark.asyncio
async def test_desktop_notification_adapter_sends_windows_notification(monkeypatch) -> None:
    calls = []

    async def fake_run_subprocess(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return 0, "", ""

    def fake_which(binary):
        if binary == "powershell.exe":
            return "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
        return None

    monkeypatch.setattr("gofer.core.approvals.sys.platform", "win32")
    monkeypatch.setattr("gofer.core.approvals.shutil.which", fake_which)
    monkeypatch.setattr("gofer.core.approvals.run_subprocess", fake_run_subprocess)

    await DesktopNotificationAdapter().send(
        Notification(title="Done", body="Complete", urgency="critical")
    )

    cmd, kwargs = calls[0]
    assert cmd[0].endswith("powershell.exe")
    assert "-STA" in cmd
    assert "-NoProfile" in cmd
    script = cmd[cmd.index("-Command") + 1]
    assert "$displayMilliseconds = 5000" in script
    assert "ShowBalloonTip($displayMilliseconds)" in script
    assert "Start-Sleep -Milliseconds $displayMilliseconds" in script
    assert kwargs["env"] == {
        "GOFER_NOTIFICATION_TITLE": "Done",
        "GOFER_NOTIFICATION_BODY": "Complete",
        "GOFER_NOTIFICATION_URGENCY": "critical",
    }


@pytest.mark.asyncio
async def test_desktop_notification_adapter_requires_powershell_on_windows(monkeypatch) -> None:
    monkeypatch.setattr("gofer.core.approvals.sys.platform", "win32")
    monkeypatch.setattr("gofer.core.approvals.shutil.which", lambda _binary: None)

    with pytest.raises(RuntimeError, match="PowerShell"):
        await DesktopNotificationAdapter().send(Notification(title="Done", body="Complete"))


@pytest.mark.asyncio
async def test_desktop_notification_adapter_fails_without_display(monkeypatch) -> None:
    monkeypatch.setattr("gofer.core.approvals.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)

    with pytest.raises(RuntimeError, match="DISPLAY"):
        await DesktopNotificationAdapter().send(Notification(title="Done", body="Complete"))


@pytest.mark.asyncio
async def test_desktop_notification_adapter_fails_without_notify_send(monkeypatch) -> None:
    monkeypatch.setattr("gofer.core.approvals.sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("gofer.core.approvals.shutil.which", lambda _binary: None)

    with pytest.raises(RuntimeError, match="notify-send"):
        await DesktopNotificationAdapter().send(Notification(title="Done", body="Complete"))


@pytest.mark.asyncio
async def test_desktop_notification_adapter_fails_when_notify_send_fails(monkeypatch) -> None:
    async def fake_run_subprocess(*_args, **_kwargs):
        return 1, "", "cannot connect to notification daemon"

    monkeypatch.setattr("gofer.core.approvals.sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("gofer.core.approvals.shutil.which", lambda _binary: "/bin/notify-send")
    monkeypatch.setattr("gofer.core.approvals.run_subprocess", fake_run_subprocess)

    with pytest.raises(RuntimeError, match="cannot connect"):
        await DesktopNotificationAdapter().send(Notification(title="Done", body="Complete"))


@pytest.mark.asyncio
async def test_desktop_notification_failure_includes_env_when_stderr_is_empty(
    monkeypatch,
) -> None:
    async def fake_run_subprocess(*_args, **_kwargs):
        return 1, "", ""

    monkeypatch.setattr("gofer.core.approvals.sys.platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr("gofer.core.approvals.shutil.which", lambda _binary: "/bin/notify-send")
    monkeypatch.setattr("gofer.core.approvals.run_subprocess", fake_run_subprocess)

    with pytest.raises(RuntimeError) as exc_info:
        await DesktopNotificationAdapter().send(Notification(title="Done", body="Complete"))

    message = str(exc_info.value)
    assert "exit_code=1" in message
    assert "DISPLAY=:0" in message
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in message
    assert "XDG_RUNTIME_DIR=/run/user/1000" in message


def test_a_decision_survives_the_waiting_run_rewriting_its_request(tmp_path, monkeypatch) -> None:
    """The run's waiter rewrites the request often; it must never put back an undecided copy."""
    import threading
    import time

    from gofer.core import approvals
    from gofer.core.approvals import ApprovalRequest, ApprovalStore

    store = ApprovalStore(tmp_path)
    store.create_or_update(ApprovalRequest("flow", "run-1", "gate", "Ship it?"))
    real_write = approvals._write_request
    decided: list[str] = []
    errors: list[Exception] = []

    def decide() -> None:
        try:
            decided.append(
                store.decide("flow", "run-1", "gate", "approved", decided_by="ui").status
            )
        except Exception as error:  # noqa: BLE001 - reported below
            errors.append(error)

    deciding = threading.Thread(target=decide)

    def slow_waiter_write(path, request) -> None:
        # The user decides while the waiter holds its undecided copy.
        if request.waiter_pid is not None and not deciding.is_alive() and not decided:
            deciding.start()
            time.sleep(0.2)
        real_write(path, request)

    monkeypatch.setattr(approvals, "_write_request", slow_waiter_write)
    store.mark_waiting("flow", "run-1", "gate")
    deciding.join(5)

    assert errors == []
    assert decided == ["decided"]
    request = store.get("flow", "run-1", "gate")
    assert request is not None and request.decision is not None
    assert request.decision.decision == "approved"
    assert request.waiter_pid is not None


def test_a_timeout_never_replaces_a_decision_made_after_the_read(tmp_path) -> None:
    from gofer.core.approvals import ApprovalRequest, ApprovalStore

    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            "flow",
            "run-1",
            "gate",
            "Ship it?",
            requested_at="2020-01-01T00:00:00+00:00",
            timeout_seconds=1,
        )
    )
    stale = ApprovalRequest.from_dict(
        json.loads(store.request_path("flow", "run-1", "gate").read_text())
    )
    # Someone decided it on disk; the stale in-memory copy still looks undecided.
    path = store.request_path("flow", "run-1", "gate")
    data = json.loads(path.read_text())
    data["decision"] = {"decision": "approved", "decidedBy": "ui", "notes": ""}
    path.write_text(json.dumps(data))

    result = store._expire_if_needed(stale)

    assert result.decision is not None and result.decision.decision == "approved"
    saved = store.get("flow", "run-1", "gate")
    assert saved is not None and saved.decision is not None
    assert saved.decision.decision == "approved"


def test_a_pending_request_is_listed_when_the_folder_listing_misses_its_file(
    tmp_path, monkeypatch
) -> None:
    """A waiting gate replaces its file several times a second. On btrfs a folder
    listing taken during a replace can leave the file out; its lock file still names it."""
    from pathlib import Path

    from gofer.core.approvals import ApprovalRequest, ApprovalStore

    store = ApprovalStore(tmp_path)
    store.create_or_update(ApprovalRequest("flow", "run-1", "gate", "Ship it?"))
    store.mark_waiting("flow", "run-1", "gate")
    real_glob = Path.glob

    def listing_mid_replace(self: Path, pattern: str, *args, **kwargs):
        # What a listing taken while the file is being replaced returns.
        return [p for p in real_glob(self, pattern, *args, **kwargs) if p.name != "gate.json"]

    monkeypatch.setattr(Path, "glob", listing_mid_replace)
    assert [(r.run_id, r.node_id, r.status) for r in store.list_pending("flow")] == [
        ("run-1", "gate", "pending")
    ]


def test_a_request_is_listed_every_time_while_its_gate_keeps_rewriting_it(tmp_path) -> None:
    """The real race: the waiter's rewrites must never hide the request from a listing."""
    import threading
    import time

    from gofer.core.approvals import ApprovalRequest, ApprovalStore

    store = ApprovalStore(tmp_path)
    store.create_or_update(ApprovalRequest("flow", "run-1", "gate", "Ship it?"))
    stop = threading.Event()

    def waiter() -> None:
        while not stop.is_set():
            store.mark_waiting("flow", "run-1", "gate")

    thread = threading.Thread(target=waiter)
    thread.start()
    misses = listings = 0
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            listings += 1
            if len(store.list_pending("flow")) != 1:
                misses += 1
    finally:
        stop.set()
        thread.join()
    assert listings and misses == 0, f"{misses} of {listings} listings missed the request"
