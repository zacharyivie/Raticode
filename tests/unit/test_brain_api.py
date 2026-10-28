from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.server import GoferUiServer


@pytest.fixture
def api(tmp_path: Path):
    server = GoferUiServer(("127.0.0.1", 0), tmp_path / "app", api_token="brain-api-test")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(
    server: GoferUiServer,
    action: str = "snapshot",
    params: dict[str, Any] | None = None,
    grants: dict[str, str] | None = None,
    *,
    token: bool = True,
) -> tuple[int, Any]:
    connection = http.client.HTTPConnection(
        str(server.server_address[0]), int(server.server_address[1]), timeout=10
    )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer brain-api-test"
    connection.request(
        "POST",
        "/api/second-brains",
        json.dumps({"action": action, "params": params or {}, "workspaceGrants": grants or {}}),
        headers,
    )
    response = connection.getresponse()
    value = json.loads(response.read())
    connection.close()
    return response.status, value


def test_second_brain_api_requires_auth_and_never_seeds_demo_content(api):
    assert request(api, token=False)[0] == 401
    status, value = request(api)
    assert status == 200
    assert value["result"]["brains"] == []
    assert value["result"]["documents"] == []


def test_folder_registration_requires_a_desktop_grant(api, tmp_path):
    root = tmp_path / "external-brain"
    root.mkdir()
    params = {"root": str(root), "metadata": "app"}
    assert request(api, "open", params)[0] == 403
    grant = api.path_grants.register(root)
    status, value = request(api, "open", params, {str(root): grant})
    assert status == 200
    assert value["result"]["root"] == str(root)
    assert len(request(api)[1]["result"]["brains"]) == 1


def test_export_and_import_paths_cannot_use_a_brain_grant_to_escape(api, tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    grant = api.path_grants.register(root)
    brain = request(api, "open", {"root": str(root)}, {str(root): grant})[1]["result"]
    target = tmp_path / "outside.zip"
    status, value = request(
        api,
        "task",
        {"kind": "export", "brainId": brain["id"], "destination": str(target)},
        {str(target): grant},
    )
    assert status == 403
    assert "error" in value
    assert not target.exists()


def test_malformed_actions_are_structured_errors(api):
    status, value = request(api, "mutate", {"kind": "unknown", "id": "x"})
    assert status == 400 and "error" in value
