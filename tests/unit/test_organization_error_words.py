"""Organization error messages say worker and check-in, as the screens and GLOSSARY.md do.

The config keeps its own names (``employees``, ``employeeId``, ``heartbeatSeconds``), and the
phone maps two start-hint reason codes by their exact text, so those stay. Every other message
raised from the organization modules is read by the user and must not say employee or heartbeat.
"""

from __future__ import annotations

import ast
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "gofer" / "ui"
# Reason codes OrganizationModel.kt (startHint/startTone) matches by exact text.
PHONE_REASON_CODES = {"Assign an employee", "Employee is held"}
OLD_WORDS = re.compile(r"employee|heartbeat", re.IGNORECASE)


def raised_messages(path: Path) -> list[tuple[int, str]]:
    """The literal text of each `raise X("...")`, with an f-string's fixed parts joined."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        for arg in node.exc.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.append((arg.lineno, arg.value))
            elif isinstance(arg, ast.JoinedStr):
                text = "".join(
                    part.value
                    for part in arg.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                found.append((arg.lineno, text))
    return found


def organization_modules() -> list[Path]:
    return sorted(SRC.glob("organization*.py"))


def test_scan_covers_the_organization_modules() -> None:
    names = {path.name for path in organization_modules()}
    assert {"organizations.py", "organization_store.py", "organization_operations.py"} <= names
    # The scan sees plain and f-string messages, so an empty result means clean, not blind.
    sample = SRC / "organization_store.py"
    assert any("check-in interval" in text for _, text in raised_messages(sample))


def test_raised_organization_messages_say_worker_not_employee() -> None:
    offenders = [
        f"{path.name}:{line}: {text!r}"
        for path in organization_modules()
        for line, text in raised_messages(path)
        if text not in PHONE_REASON_CODES and OLD_WORDS.search(text)
    ]
    assert offenders == []


def test_check_in_interval_messages_name_the_form_field() -> None:
    from gofer.ui.organization_store import check_heartbeats

    with pytest.raises(ValueError, match="Mira's check-in interval must be at least 15 minutes"):
        check_heartbeats([], [{"id": "mira", "name": "Mira", "heartbeatSeconds": 300}])


def test_saving_a_worker_without_a_name_says_worker(tmp_path, monkeypatch) -> None:
    from gofer.ui import server as ui

    monkeypatch.setattr(ui, "ensure_local_gofer_cli", lambda data_dir: None)
    monkeypatch.setenv("RATICODE_EXPERIMENTAL_ORGANIZATIONS", "1")
    server = ui.create_server(port=0, data_dir=tmp_path, api_token="test-org-words")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}/api/organizations"

    def post(body: dict[str, Any]) -> Any:
        req = urllib.request.Request(
            origin,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-org-words",
            },
            data=json.dumps(body).encode(),
        )
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())["result"]

    try:
        org = post(
            {
                "projectRoot": str(tmp_path),
                "action": "create",
                "params": {
                    "config": {
                        "name": "Words company",
                        "employees": [{"id": "builder", "name": "Mira", "paused": True}],
                    }
                },
            }
        )
        config = org["config"]
        worker = {**config["employees"][0], "name": "   "}
        with pytest.raises(urllib.error.HTTPError) as refused:
            post(
                {
                    "projectRoot": str(tmp_path),
                    "organizationId": org["id"],
                    "action": "configure",
                    "params": {
                        "config": {**config, "employees": [worker, *config["employees"][1:]]},
                        "expectedRevision": org["revision"],
                        "reason": "Blank name",
                    },
                }
            )
        assert refused.value.code == 400
        error = json.loads(refused.value.read())["error"]
        assert "Worker name is required" in error
        assert "Employee name" not in error
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
