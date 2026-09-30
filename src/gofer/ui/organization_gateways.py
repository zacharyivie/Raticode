"""Bounded remote job protocol and paired-desktop transport."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gofer.core.http import read_response_bytes


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        raise ValueError("Remote gateway redirects are not permitted")


class HttpGateway:
    """HTTPS jobs/v1: PUT is idempotent; GET returns status; DELETE requests cancellation."""

    def __init__(self, target: dict[str, Any], token: str) -> None:
        self.target, self.token = target, token

    def request(self, method: str, request_id: str, body: Any = None) -> dict[str, Any]:
        request = Request(
            self.target["url"].rstrip("/") + "/jobs/" + request_id,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=15) as response:
                raw = read_response_bytes(response, 1_048_576)
        except HTTPError as exc:
            raise ValueError(f"Remote gateway returned HTTP {exc.code}") from None
        if len(raw) > 1_048_576:
            raise ValueError("Remote gateway response exceeds 1 MiB")
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("requestId") != request_id:
            raise ValueError("Remote gateway returned a mismatched job receipt")
        if value.get("state") not in {
            "queued",
            "running",
            "completed",
            "failed",
            "rejected",
            "cancelled",
            "cancel_requested",
            "outcome_unknown",
        }:
            raise ValueError("Remote gateway returned an invalid state")
        if "turnsUsed" in value and (
            type(value["turnsUsed"]) is not int
            or not 0 <= value["turnsUsed"] <= self.target["turnLimit"]
        ):
            raise ValueError(
                "Remote gateway exceeded its turn reservation or reported invalid usage"
            )
        if "usage" in value and not isinstance(value["usage"], dict):
            raise ValueError("Remote gateway usage must be an object")
        return value

    def submit(self, request_id: str, text: str, lineage: dict[str, Any]) -> Any:
        return self.request(
            "PUT",
            request_id,
            {
                "protocol": "raticode/jobs/v1",
                "requestId": request_id,
                "text": text,
                "lineage": lineage,
                "turnLimit": self.target["turnLimit"],
                "maxConcurrency": 1,
                "budgetUsd": self.target.get("budgetUsd"),
            },
        )

    def status(self, request_id: str) -> dict[str, Any]:
        return self.request("GET", request_id)

    def cancel(self, request_id: str) -> Any:
        return self.request("DELETE", request_id)


class FleetGateway:
    def __init__(self, control: Any, target: dict[str, Any]) -> None:
        self.control, self.target = control, target

    def submit(self, request_id: str, text: str, lineage: dict[str, Any]) -> Any:
        return self.control.action(
            {
                "action": "send",
                "kind": "job.submit",
                "request_id": request_id,
                "device_id": self.target["deviceId"],
                "thread_id": self.target["threadId"],
                "project_id": self.target["projectId"],
                "text": text,
                "organization": lineage,
            }
        )

    def status(self, request_id: str) -> dict[str, Any]:
        receipt = self.control.action(
            {
                "action": "work_status",
                "device_id": self.target["deviceId"],
                "request_id": request_id,
            }
        )
        events = receipt.get("events", [])
        usage: dict[str, Any] = next(
            (
                e["payload"].get("usage", {})
                for e in reversed(events)
                if e["type"] == "job.status" and "usage" in e["payload"]
            ),
            {},
        )
        return {
            "state": receipt["state"],
            "turnsUsed": 1,
            "usage": usage,
            "text": "".join(e["payload"]["text"] for e in events if e["type"] == "chat.message"),
        }

    def cancel(self, request_id: str) -> Any:
        return self.control.action(
            {
                "action": "cancel_work",
                "device_id": self.target["deviceId"],
                "request_id": request_id,
            }
        )
