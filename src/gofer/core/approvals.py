from __future__ import annotations

import json
import os
import shutil
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Literal, Protocol

import anyio

from gofer.core.http import HttpClient, HttpRequest, UrllibHttpClient
from gofer.core.operations import HttpRetryPolicy
from gofer.utils.paths import get_data_dir
from gofer.utils.process import run_subprocess

ApprovalDecisionValue = Literal["approved", "rejected", "timeout"]


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _safe_part(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in value)
    return safe.strip(".-") or "item"


@dataclass
class ApprovalDecision:
    decision: ApprovalDecisionValue
    decided_by: str = "unknown"
    notes: str = ""
    decided_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "decidedBy": self.decided_by,
            "notes": self.notes,
            "decidedAt": self.decided_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ApprovalDecision:
        decision = str(data.get("decision", "rejected"))
        if decision not in {"approved", "rejected", "timeout"}:
            decision = "rejected"
        return cls(
            decision=decision,  # type: ignore[arg-type]
            decided_by=str(data.get("decidedBy") or data.get("decided_by") or "unknown"),
            notes=str(data.get("notes") or ""),
            decided_at=str(data.get("decidedAt") or data.get("decided_at") or _now()),
        )


@dataclass
class ApprovalRequest:
    workflow_id: str
    run_id: str
    node_id: str
    message: str
    status: Literal["pending", "decided"] = "pending"
    approvers: list[str] = field(default_factory=list)
    requested_at: str = field(default_factory=_now)
    timeout_seconds: float | None = None
    timeout_decision: Literal["reject", "timeout"] = "timeout"
    decision: ApprovalDecision | None = None
    workflow_path: str | None = None
    log_path: str | None = None
    checkpoint_path: str | None = None
    subject_manifest_id: str | None = None
    waiter_seen_at: str | None = None
    waiter_pid: int | None = None
    resume_claimed_at: str | None = None
    resume_claimed_by_pid: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "workflowId": self.workflow_id,
            "runId": self.run_id,
            "nodeId": self.node_id,
            "message": self.message,
            "status": self.status,
            "approvers": self.approvers,
            "requestedAt": self.requested_at,
            "timeoutSeconds": self.timeout_seconds,
            "timeoutDecision": self.timeout_decision,
            "decision": self.decision.to_dict() if self.decision is not None else None,
            "workflowPath": self.workflow_path,
            "logPath": self.log_path,
            "checkpointPath": self.checkpoint_path,
            "subjectManifestId": self.subject_manifest_id,
            "waiterSeenAt": self.waiter_seen_at,
            "waiterPid": self.waiter_pid,
            "resumeClaimedAt": self.resume_claimed_at,
            "resumeClaimedByPid": self.resume_claimed_by_pid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ApprovalRequest:
        decision_data = data.get("decision")
        decision = (
            ApprovalDecision.from_dict(decision_data) if isinstance(decision_data, dict) else None
        )
        approvers = data.get("approvers")
        timeout_value = data.get("timeoutSeconds")
        timeout_seconds = (
            float(timeout_value) if isinstance(timeout_value, (str, int, float)) else None
        )
        timeout_decision = str(
            data.get("timeoutDecision") or data.get("timeout_decision") or "timeout"
        )
        if timeout_decision not in {"reject", "timeout"}:
            timeout_decision = "timeout"
        return cls(
            workflow_id=str(data.get("workflowId") or data.get("workflow_id") or ""),
            run_id=str(data.get("runId") or data.get("run_id") or ""),
            node_id=str(data.get("nodeId") or data.get("node_id") or ""),
            message=str(data.get("message") or ""),
            status="decided" if decision is not None else "pending",
            approvers=[str(item) for item in approvers] if isinstance(approvers, list) else [],
            requested_at=str(data.get("requestedAt") or data.get("requested_at") or _now()),
            timeout_seconds=timeout_seconds,
            timeout_decision=timeout_decision,  # type: ignore[arg-type]
            decision=decision,
            workflow_path=(
                str(data.get("workflowPath") or data.get("workflow_path") or "") or None
            ),
            log_path=str(data.get("logPath") or data.get("log_path") or "") or None,
            checkpoint_path=(
                str(data.get("checkpointPath") or data.get("checkpoint_path") or "") or None
            ),
            subject_manifest_id=(
                str(data.get("subjectManifestId") or data.get("subject_manifest_id") or "") or None
            ),
            waiter_seen_at=(
                str(data.get("waiterSeenAt") or data.get("waiter_seen_at") or "") or None
            ),
            waiter_pid=(
                int(waiter_pid)
                if isinstance(
                    waiter_pid := (data.get("waiterPid") or data.get("waiter_pid")),
                    (str, int),
                )
                and str(waiter_pid).isdigit()
                else None
            ),
            resume_claimed_at=(
                str(data.get("resumeClaimedAt") or data.get("resume_claimed_at") or "") or None
            ),
            resume_claimed_by_pid=(
                int(resume_pid)
                if isinstance(
                    resume_pid := (
                        data.get("resumeClaimedByPid") or data.get("resume_claimed_by_pid")
                    ),
                    (str, int),
                )
                and str(resume_pid).isdigit()
                else None
            ),
        )


class ApprovalStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.base_dir = data_dir or get_data_dir()

    def request_path(self, workflow_id: str, run_id: str, node_id: str) -> Path:
        return (
            self.base_dir
            / "approvals"
            / _safe_part(workflow_id)
            / _safe_part(run_id)
            / f"{_safe_part(node_id)}.json"
        )

    def create_or_update(self, request: ApprovalRequest) -> Path:
        path = self.request_path(request.workflow_id, request.run_id, request.node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.get(request.workflow_id, request.run_id, request.node_id)
        if existing is not None and existing.decision is not None:
            request.decision = existing.decision
            request.status = "decided"
        path.write_text(json.dumps(request.to_dict(), indent=2), encoding="utf-8")
        return path

    def get(
        self,
        workflow_id: str,
        run_id: str,
        node_id: str,
    ) -> ApprovalRequest | None:
        path = self.request_path(workflow_id, run_id, node_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        request = ApprovalRequest.from_dict(data)
        return self._expire_if_needed(request)

    def decide(
        self,
        workflow_id: str,
        run_id: str,
        node_id: str,
        decision: ApprovalDecisionValue,
        *,
        decided_by: str = "cli",
        notes: str = "",
    ) -> ApprovalRequest:
        request = self.get(workflow_id, run_id, node_id)
        if request is None:
            raise ValueError("Pending approval not found")
        if request.decision is not None:
            raise ValueError(f"Approval already decided as {request.decision.decision}")
        if request.approvers and decided_by != "gofer" and decided_by not in request.approvers:
            allowed = ", ".join(request.approvers)
            raise ValueError(f"Approver {decided_by!r} is not allowed; expected one of: {allowed}")
        request.status = "decided"
        request.decision = ApprovalDecision(
            decision=decision,
            decided_by=decided_by,
            notes=notes,
        )
        self.create_or_update(request)
        return request

    def mark_waiting(self, workflow_id: str, run_id: str, node_id: str) -> None:
        request = self.get(workflow_id, run_id, node_id)
        if request is None:
            return
        request.waiter_seen_at = _now()
        request.waiter_pid = os.getpid()
        self.create_or_update(request)

    def claim_resume(self, workflow_id: str, run_id: str, node_id: str) -> ApprovalRequest | None:
        request = self.get(workflow_id, run_id, node_id)
        if request is None or request.decision is None:
            return None
        if request.resume_claimed_at and _pid_is_live(request.resume_claimed_by_pid):
            return None
        request.resume_claimed_at = _now()
        request.resume_claimed_by_pid = os.getpid()
        self.create_or_update(request)
        return request

    def release_resume(self, workflow_id: str, run_id: str, node_id: str) -> None:
        request = self.get(workflow_id, run_id, node_id)
        if request is None:
            return
        request.resume_claimed_at = None
        request.resume_claimed_by_pid = None
        self.create_or_update(request)

    def list_pending(self, workflow_id: str | None = None) -> list[ApprovalRequest]:
        return [request for request in self.list_requests(workflow_id) if request.decision is None]

    def list_requests(self, workflow_id: str | None = None) -> list[ApprovalRequest]:
        root = self.base_dir / "approvals"
        if workflow_id is not None:
            roots = [root / _safe_part(workflow_id)]
        else:
            roots = sorted(root.glob("*")) if root.exists() else []
        requests: list[ApprovalRequest] = []
        for request_root in roots:
            for path in sorted(request_root.glob("*/*.json")):
                if path.name.endswith(".checkpoint.json"):
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                request = self._expire_if_needed(ApprovalRequest.from_dict(data))
                requests.append(request)
        return requests

    def _expire_if_needed(self, request: ApprovalRequest) -> ApprovalRequest:
        if request.decision is not None or request.timeout_seconds is None:
            return request
        try:
            requested_at = datetime.fromisoformat(request.requested_at)
        except ValueError:
            return request
        now = datetime.now(requested_at.tzinfo or UTC)
        if now < requested_at + timedelta(seconds=request.timeout_seconds):
            return request
        decision: ApprovalDecisionValue = (
            "timeout" if request.timeout_decision == "timeout" else "rejected"
        )
        request.status = "decided"
        request.decision = ApprovalDecision(
            decision=decision,
            decided_by="gofer",
            notes=f"Timed out after {request.timeout_seconds} seconds",
        )
        path = self.request_path(request.workflow_id, request.run_id, request.node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(request.to_dict(), indent=2), encoding="utf-8")
        return request


def _pid_is_live(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class Notification:
    title: str
    body: str
    channel: str = "desktop"
    urgency: str = "normal"
    webhook_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    payload: object | None = None
    email_from: str | None = None
    email_to: list[str] = field(default_factory=list)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    timeout_seconds: float = 30.0
    retry: HttpRetryPolicy = field(default_factory=HttpRetryPolicy)
    expected_statuses: list[int] = field(default_factory=lambda: [200, 201, 202, 204])
    network_allowlist: list[str] = field(default_factory=list)


class NotificationAdapter(Protocol):
    async def send(self, notification: Notification) -> None: ...


class DesktopNotificationAdapter:
    async def send(self, notification: Notification) -> None:
        if notification.channel != "desktop":
            raise ValueError(f"Unsupported notification channel: {notification.channel}")
        if sys.platform == "win32":
            await _send_windows_desktop_notification(notification)
            return
        if sys.platform == "darwin" and shutil.which("osascript"):
            script = (
                "display notification "
                f"{json.dumps(notification.body)} with title {json.dumps(notification.title)}"
            )
            returncode, stdout, stderr = await run_subprocess(
                ["osascript", "-e", script],
                timeout=5,
            )
            if returncode != 0:
                raise RuntimeError(
                    _notification_failure_message("osascript", returncode, stdout, stderr)
                )
            return
        if sys.platform == "darwin":
            raise RuntimeError("Desktop notifications require 'osascript' on macOS.")
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("Desktop notifications require DISPLAY on Unix desktop sessions.")
        notify_send = shutil.which("notify-send")
        if notify_send is None:
            raise RuntimeError(
                "Desktop notifications require 'notify-send' on Unix desktop sessions."
            )
        returncode, stdout, stderr = await run_subprocess(
            [
                notify_send,
                "-u",
                notification.urgency,
                "--",
                notification.title,
                notification.body,
            ],
            timeout=5,
        )
        if returncode != 0:
            raise RuntimeError(
                _notification_failure_message("notify-send", returncode, stdout, stderr)
            )


class MultiChannelNotificationAdapter:
    def __init__(
        self,
        *,
        desktop_adapter: NotificationAdapter | None = None,
        http_client: HttpClient | None = None,
    ) -> None:
        self._desktop_adapter = desktop_adapter or DesktopNotificationAdapter()
        self._http_client = http_client or UrllibHttpClient()

    async def send(self, notification: Notification) -> None:
        if notification.channel == "desktop":
            await self._desktop_adapter.send(notification)
            return
        if notification.channel in {"slack", "teams", "webhook"}:
            await self._send_webhook(notification)
            return
        if notification.channel == "email":
            await self._send_email(notification)
            return
        raise ValueError(f"Unsupported notification channel: {notification.channel}")

    async def _send_webhook(self, notification: Notification) -> None:
        if not notification.webhook_url:
            raise ValueError(f"{notification.channel} notifications require webhook_url")
        payload = _notification_webhook_payload(notification)
        body = json.dumps(payload, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json", **notification.headers}
        attempts = max(1, notification.retry.attempts)
        retry_statuses = set(notification.retry.retry_on_statuses)
        expected_statuses = set(notification.expected_statuses or [200])
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http_client.send(
                    HttpRequest(
                        method="POST",
                        url=notification.webhook_url,
                        headers=headers,
                        body=body,
                        timeout_seconds=notification.timeout_seconds,
                        network_allowlist=notification.network_allowlist,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                last_error = (
                    f"{notification.channel} webhook failed after {attempt} "
                    f"attempt{'s' if attempt != 1 else ''}: {exc}"
                )
                if attempt >= attempts:
                    break
                await anyio.sleep(notification.retry.backoff_seconds)
                continue
            if response.status in expected_statuses:
                return
            last_error = (
                f"{notification.channel} webhook returned HTTP {response.status}: "
                f"{response.body[:500].decode('utf-8', errors='replace')}"
            )
            if response.status not in retry_statuses or attempt >= attempts:
                break
            await anyio.sleep(notification.retry.backoff_seconds)
        raise RuntimeError(last_error or f"{notification.channel} webhook failed")

    async def _send_email(self, notification: Notification) -> None:
        if not notification.smtp_host:
            raise ValueError("email notifications require smtp_host")
        if not notification.email_from:
            raise ValueError("email notifications require email_from")
        if not notification.email_to:
            raise ValueError("email notifications require email_to")
        attempts = max(1, notification.retry.attempts)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await anyio.to_thread.run_sync(_send_email_sync, notification)
                return
            except (OSError, smtplib.SMTPException) as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                await anyio.sleep(notification.retry.backoff_seconds)
        raise RuntimeError(f"email notification failed: {last_error}") from last_error


def _notification_webhook_payload(notification: Notification) -> object:
    if notification.payload is not None:
        return notification.payload
    if notification.channel == "slack":
        return {"text": f"*{notification.title}*\n{notification.body}".strip()}
    if notification.channel == "teams":
        return {"text": f"**{notification.title}**\n\n{notification.body}".strip()}
    return {
        "title": notification.title,
        "body": notification.body,
        "urgency": notification.urgency,
        "channel": notification.channel,
    }


def _send_email_sync(notification: Notification) -> None:
    message = EmailMessage()
    message["Subject"] = notification.title
    message["From"] = notification.email_from or ""
    message["To"] = ", ".join(notification.email_to)
    message.set_content(notification.body)
    with smtplib.SMTP(
        notification.smtp_host or "",
        notification.smtp_port,
        timeout=notification.timeout_seconds,
    ) as smtp:
        if notification.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if notification.smtp_username or notification.smtp_password:
            smtp.login(notification.smtp_username or "", notification.smtp_password or "")
        smtp.send_message(message)


async def _send_windows_desktop_notification(notification: Notification) -> None:
    powershell = _windows_powershell_path()
    if powershell is None:
        raise RuntimeError("Desktop notifications require PowerShell on Windows.")
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$displayMilliseconds = 5000
$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
try {
    $notifyIcon.Icon = [System.Drawing.SystemIcons]::Information
    $notifyIcon.Visible = $true
    $notifyIcon.BalloonTipTitle = $env:GOFER_NOTIFICATION_TITLE
    $notifyIcon.BalloonTipText = $env:GOFER_NOTIFICATION_BODY
    if ($env:GOFER_NOTIFICATION_URGENCY -eq 'critical') {
        $notifyIcon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Error
    } elseif ($env:GOFER_NOTIFICATION_URGENCY -eq 'low') {
        $notifyIcon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    } else {
        $notifyIcon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    }
    $notifyIcon.ShowBalloonTip($displayMilliseconds)
    Start-Sleep -Milliseconds $displayMilliseconds
} finally {
    $notifyIcon.Dispose()
}
""".strip()
    returncode, stdout, stderr = await run_subprocess(
        [
            powershell,
            "-STA",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        env={
            "GOFER_NOTIFICATION_TITLE": notification.title,
            "GOFER_NOTIFICATION_BODY": notification.body,
            "GOFER_NOTIFICATION_URGENCY": notification.urgency,
        },
        timeout=8,
    )
    if returncode != 0:
        raise RuntimeError(_notification_failure_message("PowerShell", returncode, stdout, stderr))


def _windows_powershell_path() -> str | None:
    return (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh.exe")
        or shutil.which("pwsh")
    )


def _notification_failure_message(
    backend: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> str:
    details = [
        f"{backend} failed to send notification",
        f"exit_code={returncode}",
    ]
    if stderr.strip():
        details.append(f"stderr={stderr.strip()}")
    if stdout.strip():
        details.append(f"stdout={stdout.strip()}")
    details.append(
        "env="
        f"DISPLAY={os.environ.get('DISPLAY') or '<unset>'}, "
        f"DBUS_SESSION_BUS_ADDRESS={os.environ.get('DBUS_SESSION_BUS_ADDRESS') or '<unset>'}, "
        f"XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR') or '<unset>'}"
    )
    return "; ".join(details)


class RecordingNotificationAdapter:
    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    async def send(self, notification: Notification) -> None:
        self.notifications.append(notification)


async def wait_for_decision(
    store: ApprovalStore,
    workflow_id: str,
    run_id: str,
    node_id: str,
    *,
    timeout_seconds: float | None,
) -> ApprovalDecision | None:
    deadline = anyio.current_time() + timeout_seconds if timeout_seconds is not None else None
    while True:
        store.mark_waiting(workflow_id, run_id, node_id)
        request = store.get(workflow_id, run_id, node_id)
        if request is not None and request.decision is not None:
            return request.decision
        if deadline is not None and anyio.current_time() >= deadline:
            return None
        await anyio.sleep(0.1)
