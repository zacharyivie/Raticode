from __future__ import annotations

import base64
import json
import os
import socket
import threading
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import quote, urlparse

import pytest

from gofer.core.resources import DEFAULT_RESOURCE_LIMITS, ResourceLimits
from gofer.rattish.trigger_service import TriggerRun
from gofer.rattish.triggers import TriggerStore
from gofer.rattish.workspaces import create_registered_workflow, find_registered_workflow
from gofer.ui import api as api_module
from gofer.ui import server as server_module
from gofer.ui.chat import workflow_chat_prompt_path
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.server import (
    DesktopPathGrantStore,
    GoferUiRequestHandler,
    GoferUiServer,
    WorkflowBundleError,
    create_server,
    ready_payload,
)
from tests.helpers.time_limit import finishes_within
from tests.unit.test_rattish_parser_bad_symbols import BAD_SOURCES as BAD_RATTISH_SOURCES


class HandlerResult:
    def __init__(
        self,
        status: int,
        headers: list[tuple[str, str]],
        body: bytes,
        server: GoferUiServer,
    ) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.server = server

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        return self.body.decode("utf-8")

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in reversed(self.headers):
            if key.lower() == lowered:
                return value
        return None


def _fake_server(
    tmp_path: Path,
    *,
    resource_limits: ResourceLimits | None = None,
) -> GoferUiServer:
    server = GoferUiServer.__new__(GoferUiServer)
    server.data_dir = tmp_path
    server.chat_steering = ChatSteering(tmp_path)
    server.chat_jobs = server_module.ChatJobs(tmp_path)
    server.generation_jobs = server_module.GenerationJobs(tmp_path)
    server.provider_auth = server_module.ProviderAuthSessions()
    server.link_previews = server_module.LinkPreviews()
    server.resource_limits = resource_limits or DEFAULT_RESOURCE_LIMITS
    server.api_token = "test-ui-token"
    server.allowed_origins = {"http://127.0.0.1:5173"}
    server.path_grant_secret = "test-secret"
    server.path_grants = DesktopPathGrantStore()
    server.gofer_cli_path = None
    server.server_address = ("127.0.0.1", 8765)
    return server


def _request(
    tmp_path: Path,
    method: str,
    path: str,
    *,
    body: dict[str, object] | bytes | None = None,
    headers: dict[str, str] | None = None,
    resource_limits: ResourceLimits | None = None,
    authenticated: bool = True,
) -> HandlerResult:
    raw_body = b""
    if isinstance(body, bytes):
        raw_body = body
    elif body is not None:
        raw_body = json.dumps(body).encode("utf-8")

    server = _fake_server(tmp_path, resource_limits=resource_limits)
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    setattr(handler, "_check_discovery_cancelled", lambda: None)
    handler.server = server
    handler.path = path
    handler.headers = Message()
    request_headers = dict(headers or {})
    request_headers.setdefault("Host", "127.0.0.1:8765")
    if authenticated:
        request_headers.setdefault("Authorization", "Bearer test-ui-token")
    for key, value in request_headers.items():
        handler.headers[key] = value
    if body is not None:
        handler.headers["Content-Length"] = str(len(raw_body))
    handler.rfile = BytesIO(raw_body)
    handler.wfile = BytesIO()
    status: dict[str, int] = {}
    response_headers: list[tuple[str, str]] = []

    def send_response(code: int, message: str | None = None) -> None:
        status["code"] = code

    def send_header(keyword: str, value: str) -> None:
        response_headers.append((keyword, value))

    def end_headers() -> None:
        return None

    setattr(handler, "send_response", send_response)
    setattr(handler, "send_header", send_header)
    setattr(handler, "end_headers", end_headers)

    getattr(handler, f"do_{method}")()
    return HandlerResult(status["code"], response_headers, handler.wfile.getvalue(), server)


def _fake_handler(server: GoferUiServer) -> GoferUiRequestHandler:
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.server = server
    return handler


def test_swarm_routes_require_authentication(tmp_path: Path) -> None:
    result = _request(tmp_path, "GET", "/api/swarms", authenticated=False)
    assert result.status == 401


def test_swarm_start_rejects_ungranted_project(tmp_path: Path) -> None:
    project = tmp_path.parent / f"{tmp_path.name}-swarm-project"
    project.mkdir()
    result = _request(
        tmp_path,
        "POST",
        "/api/swarms/team/start",
        body={"projectRoot": str(project), "task": "Implement a feature"},
    )
    assert result.status == 403
    assert "approved" in result.text()


@pytest.mark.parametrize("project", ["", "relative/project", 42])
def test_swarm_routes_reject_invalid_project(tmp_path: Path, project: object) -> None:
    result = _request(tmp_path, "POST", "/api/swarms", body={"projectRoot": project})
    assert result.status == 400


@pytest.mark.parametrize("action", ["messages", "deliveries"])
def test_swarm_routes_forward_project_scoped_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def message(project: str, swarm_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((project, swarm_id, payload))
        return {"id": swarm_id, "run": {"messages": [payload]}}

    original = _fake_server

    def fake_with_swarms(*args: Any, **kwargs: Any) -> GoferUiServer:
        server = original(*args, **kwargs)
        server.swarms = cast(Any, SimpleNamespace(message=message, resolve_delivery=message))
        return server

    monkeypatch.setattr(
        __import__(__name__, fromlist=["_fake_server"]), "_fake_server", fake_with_swarms
    )
    payload = {"projectRoot": str(tmp_path), "text": "Prioritize tests", "recipient": "worker"}
    result = _request(tmp_path, "POST", f"/api/swarms/team/{action}", body=payload)
    assert result.status == 200
    assert calls == [(str(tmp_path.resolve()), "team", payload)]
    assert cast(dict[str, Any], result.json())["swarm"]["id"] == "team"


def test_swarm_resume_requires_project_access(tmp_path: Path) -> None:
    project = tmp_path.parent / f"{tmp_path.name}-resume-project"
    project.mkdir()
    result = _request(
        tmp_path,
        "POST",
        "/api/swarms/team/control",
        body={"projectRoot": str(project), "action": "resume"},
    )
    assert result.status == 403


def test_swarm_api_round_trip_and_weighted_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gofer.ui.swarms import SwarmManager

    manager = SwarmManager(tmp_path, start_runtime=False)
    original = _fake_server

    def fake_with_swarms(*args: Any, **kwargs: Any) -> GoferUiServer:
        server = original(*args, **kwargs)
        server.swarms = manager
        return server

    monkeypatch.setattr(
        __import__(__name__, fromlist=["_fake_server"]), "_fake_server", fake_with_swarms
    )
    try:
        created = _request(
            tmp_path,
            "POST",
            "/api/swarms",
            body={
                "projectRoot": str(tmp_path),
                "name": "Release team",
                "agents": [
                    {
                        "id": "lead",
                        "name": "Lead",
                        "role": "Coordinate and review",
                        "isOrchestrator": True,
                    }
                ],
            },
        )
        assert created.status == 200
        swarm = cast(dict[str, Any], created.json())["swarm"]
        assert swarm["agents"][0]["allowSteering"] is False
        endpoint = f"/api/swarms/{swarm['id']}"
        started = _request(
            tmp_path,
            "POST",
            f"{endpoint}/start",
            body={
                "projectRoot": str(tmp_path),
                "task": "Ship the feature",
            },
        )
        assert started.status == 200
        saved = _request(
            tmp_path,
            "POST",
            f"{endpoint}/objectives",
            body={
                "projectRoot": str(tmp_path),
                "revision": 0,
                "reason": "Estimate implementation effort",
                "objectives": [
                    {
                        "id": "feature",
                        "title": "Ship feature",
                        "milestones": [
                            {
                                "id": "design",
                                "title": "Design",
                                "weight": 1,
                                "status": "accepted",
                                "evidence": "Design approved",
                                "waiverReason": "User reviewed the design",
                            },
                            {
                                "id": "build",
                                "title": "Build",
                                "weight": 3,
                                "status": "accepted",
                                "evidence": "Implementation reviewed",
                                "waiverReason": "User reviewed implementation",
                            },
                            {"id": "verify", "title": "Verify", "weight": 1, "status": "planned"},
                        ],
                    }
                ],
            },
        )
        assert saved.status == 200
        progress = cast(dict[str, Any], saved.json())["swarm"]["run"]["progress"]
        assert progress["percent"] == 80
        assert progress["acceptedWeight"] == 4
        assert progress["totalWeight"] == 5
        loaded = _request(tmp_path, "GET", f"{endpoint}?projectRoot={tmp_path}")
        assert loaded.status == 200
        assert cast(dict[str, Any], loaded.json())["swarm"]["run"]["revision"] == 1
        listed = _request(tmp_path, "GET", f"/api/swarms?projectRoot={tmp_path}")
        assert len(cast(dict[str, Any], listed.json())["swarms"]) == 1
        live = manager.get(tmp_path, swarm["id"], include_history=False)
        live["history"] = [{"id": str(i), "task": f"Task {i}"} for i in range(25)]
        manager._save(live)
        for history_flag in ("0", "false"):
            result = _request(
                tmp_path, "GET", f"{endpoint}?projectRoot={tmp_path}&history={history_flag}"
            )
            current = cast(dict[str, Any], result.json())["swarm"]
            assert "history" not in current
            unchanged = _request(
                tmp_path,
                "GET",
                f"{endpoint}?projectRoot={tmp_path}&since={quote(current['updatedAt'])}",
            )
            assert cast(dict[str, Any], unchanged.json())["swarm"] is None
        page = _request(tmp_path, "GET", f"{endpoint}/history?projectRoot={tmp_path}&offset=20")
        assert [run["id"] for run in cast(dict[str, Any], page.json())["history"]] == [
            "4",
            "3",
            "2",
            "1",
            "0",
        ]
        archived = _request(tmp_path, "GET", f"{endpoint}/history?projectRoot={tmp_path}&runId=24")
        assert cast(dict[str, Any], archived.json())["run"] == live["history"][24]
    finally:
        manager.close()


def _sockets_available() -> bool:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
    except OSError:
        return False
    else:
        probe.close()
        return True


def test_ui_server_dynamic_port_reports_bound_port(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GOFER_UI_EMIT_READY_TOKEN", raising=False)
    if not _sockets_available():
        pytest.skip("local sockets are unavailable in this environment")
    server = create_server(host="127.0.0.1", port=0, data_dir=tmp_path)

    try:
        payload = ready_payload(server)

        assert payload["host"] == "127.0.0.1"
        assert isinstance(payload["port"], int)
        assert payload["port"] > 0
        assert payload["dataDir"] == str(tmp_path)
        assert "goferCliPath" not in payload
        assert isinstance(payload["goferCliAvailable"], bool)
        assert "apiToken" not in payload
        assert isinstance(server.api_token, str)
        assert len(server.api_token) >= 32
    finally:
        server.server_close()


def test_ready_payload_exposes_cli_availability_without_helper_path(tmp_path) -> None:
    fake_server = cast(
        GoferUiServer,
        SimpleNamespace(
            server_address=("127.0.0.1", 37655),
            data_dir=tmp_path,
            gofer_cli_path=tmp_path / ".trusted" / "gof",
            api_token="ready-token",
            emit_ready_token=True,
        ),
    )

    payload = ready_payload(fake_server)

    assert payload == {
        "host": "127.0.0.1",
        "port": 37655,
        "dataDir": str(tmp_path),
        "goferCliAvailable": True,
        "apiToken": "ready-token",
    }
    assert "goferCliPath" not in payload


def test_link_previews_need_the_ui_token_and_refuse_local_pages(tmp_path: Path) -> None:
    path = "/api/link-preview?url=http%3A%2F%2F127.0.0.1%2Fadmin"
    assert _request(tmp_path, "GET", path, authenticated=False).status == 401

    result = _request(tmp_path, "GET", path)

    assert result.status == 422
    assert "public web addresses" in cast(dict[str, Any], result.json())["error"]


def test_chat_images_load_through_the_backend(tmp_path: Path, monkeypatch) -> None:
    path = "/api/media-image?url=http%3A%2F%2F127.0.0.1%2Fcat.png"
    assert _request(tmp_path, "GET", path, authenticated=False).status == 401
    refused = _request(tmp_path, "GET", path)
    assert refused.status == 422
    assert "public web addresses" in cast(dict[str, Any], refused.json())["error"]

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 40
    monkeypatch.setattr(
        "gofer.ui.link_previews.fetch",
        lambda url, accept, limit, **_: (url, "application/octet-stream", png, False),
    )
    loaded = _request(tmp_path, "GET", "/api/media-image?url=https%3A%2F%2Fexample.com%2Fcat")
    assert loaded.status == 200
    assert cast(dict[str, Any], loaded.json())["image"]["mime"] == "image/png"


def test_sent_chat_attachments_can_be_located_for_preview(tmp_path: Path) -> None:
    uploaded = _request(
        tmp_path,
        "POST",
        "/api/chat/attachments",
        body={
            "threadId": "thread-a",
            "files": [{"name": "cat.png", "type": "image/png", "data": "iVBORw0KGgo="}],
        },
    )
    assert uploaded.status == 201
    storage = cast(dict[str, Any], uploaded.json())["attachments"][0]["storageName"]
    path = f"/api/chat/attachments/locate?storageName={storage}"
    assert _request(tmp_path, "GET", path, authenticated=False).status == 401
    located = _request(tmp_path, "GET", path)
    assert located.status == 200
    assert Path(cast(dict[str, Any], located.json())["path"]).name == storage
    assert Path(cast(dict[str, Any], located.json())["path"]).read_bytes() == b"\x89PNG\r\n\x1a\n"
    locate = "/api/chat/attachments/locate?storageName="
    gone = _request(tmp_path, "GET", locate + "a" * 32 + "-gone.png")
    assert gone.status == 404
    assert "no longer available" in cast(dict[str, Any], gone.json())["error"]
    assert _request(tmp_path, "GET", locate + "../etc/passwd").status == 404


def test_ui_server_health_check_works_on_dynamic_port(tmp_path) -> None:
    response = _request(tmp_path, "GET", "/api/health")

    assert response.status == 200
    assert response.json() == {"ok": True}
    assert response.header("Content-Type") == "application/json"
    assert response.header("Content-Length") == str(len(response.body))
    assert response.header("Access-Control-Allow-Origin") is None


@pytest.mark.parametrize("path", ["/api/session", "/api/desktop/trusted-roots"])
def test_ui_server_has_no_session_or_trusted_roots_route(tmp_path, path) -> None:
    # The window gets its token over Electron IPC and nothing read the trusted roots.
    response = _request(
        tmp_path,
        "GET",
        path,
        headers={"Origin": "http://127.0.0.1:5173", "X-Gofer-Desktop-Grant-Secret": "test-secret"},
    )

    assert response.status == 404
    assert "test-ui-token" not in response.text()


def test_ui_server_authenticated_read_is_origin_gated(tmp_path) -> None:
    response = _request(
        tmp_path,
        "GET",
        "/api/doctor",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status == 403
    assert response.json() == {"error": "Origin is not allowed"}


def test_ui_server_reflects_trusted_origin_without_wildcard_cors(tmp_path) -> None:
    response = _request(
        tmp_path,
        "GET",
        "/api/health",
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert response.status == 200
    assert response.header("Access-Control-Allow-Origin") == "http://127.0.0.1:5173"
    assert response.header("Access-Control-Allow-Origin") != "*"


def test_ui_server_rejects_disallowed_origin(tmp_path) -> None:
    response = _request(
        tmp_path,
        "GET",
        "/api/health",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status == 403
    assert response.json() == {"error": "Origin is not allowed"}


@pytest.mark.parametrize(
    "disconnect",
    [BrokenPipeError, ConnectionAbortedError, ConnectionResetError],
)
def test_ui_server_treats_client_disconnect_as_normal(
    monkeypatch: pytest.MonkeyPatch,
    disconnect: type[OSError],
) -> None:
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)

    def disconnect_during_response(_handler: object) -> None:
        raise disconnect("client closed the connection")

    monkeypatch.setattr(
        server_module.BaseHTTPRequestHandler,
        "handle",
        disconnect_during_response,
    )

    handler.handle()


def test_ui_server_does_not_hide_unexpected_handler_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)

    def fail_request(_handler: object) -> None:
        raise RuntimeError("response bug")

    monkeypatch.setattr(server_module.BaseHTTPRequestHandler, "handle", fail_request)

    with pytest.raises(RuntimeError, match="response bug"):
        handler.handle()


def test_ui_server_state_change_requires_ui_api_token(tmp_path) -> None:
    response = _request(
        tmp_path,
        "POST",
        "/api/workflows",
        body={"name": "Demo"},
        authenticated=False,
    )

    assert response.status == 401
    assert response.json() == {"error": "UI API authentication required"}


@pytest.mark.parametrize("header", ["Authorization", "X-Gofer-UI-Token"])
def test_ui_server_rejects_non_ascii_api_credentials(tmp_path: Path, header: str) -> None:
    response = _request(
        tmp_path,
        "GET",
        "/api/doctor",
        headers={
            header: "Bearer invalid-\u00e9" if header == "Authorization" else "invalid-\u00e9"
        },
        authenticated=False,
    )

    assert response.status == 401


@pytest.mark.parametrize("case", sorted(BAD_RATTISH_SOURCES))
def test_document_analyze_reports_lone_symbols_and_empty_list_items(
    tmp_path: Path, case: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    create_registered_workflow(project, "Route Editor", registry_dir=tmp_path)
    source, line, column, message, _suggestions = BAD_RATTISH_SOURCES[case]

    with finishes_within(2):
        response = _request(
            tmp_path,
            "POST",
            "/api/workflows/route-editor/document/analyze",
            body={"source": source},
        )

    assert response.status == 200
    diagnostics = cast(dict[str, Any], response.json())["document"]["diagnostics"]
    assert [
        (item["span"]["start"]["line"], item["span"]["start"]["column"])
        for item in diagnostics
        if message in item["message"]
    ] == [(line, column)]


def test_ui_server_exposes_revisioned_rattish_document_routes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    create_registered_workflow(project, "Route Editor", registry_dir=tmp_path)

    opened_response = _request(tmp_path, "GET", "/api/workflows/route-editor/document")
    assert opened_response.status == 200
    opened = cast(dict[str, Any], opened_response.json())["document"]
    source = (
        opened["source"]
        + """Node prepare:
  type: bash-command
  command: echo ready
"""
    )

    analyzed_response = _request(
        tmp_path,
        "POST",
        "/api/workflows/route-editor/document/analyze",
        body={"source": source},
    )
    assert analyzed_response.status == 200
    assert cast(dict[str, Any], analyzed_response.json())["document"]["dirty"] is True

    saved_response = _request(
        tmp_path,
        "POST",
        "/api/workflows/route-editor/document/save",
        body={"source": source, "expectedRevision": opened["sourceRevision"]},
    )
    assert saved_response.status == 200
    saved = cast(dict[str, Any], saved_response.json())["document"]
    assert saved["dirty"] is False

    conflict = _request(
        tmp_path,
        "POST",
        "/api/workflows/route-editor/document/save",
        body={"source": source, "expectedRevision": opened["sourceRevision"]},
    )
    assert conflict.status == 409
    assert cast(dict[str, Any], conflict.json())["code"] == ("RATTISH_EDITOR_REVISION_CONFLICT")

    metadata = saved["metadata"]
    metadata["canvas"]["nodes"]["prepare"] = {"x": 10, "y": 20}
    metadata_response = _request(
        tmp_path,
        "POST",
        "/api/workflows/route-editor/metadata",
        body={
            "metadata": metadata,
            "expectedRevision": saved["metadataRevision"],
        },
    )
    assert metadata_response.status == 200
    assert cast(dict[str, Any], metadata_response.json())["metadata"]["canvas"]["nodes"][
        "prepare"
    ] == {"x": 10, "y": 20}

    mutation_response = _request(
        tmp_path,
        "POST",
        "/api/workflows/route-editor/document/mutate",
        body={
            "mutations": [
                {
                    "kind": "set_field",
                    "target": {"node": "prepare"},
                    "field": "command",
                    "value": "echo changed",
                }
            ],
            "expectedRevision": saved["sourceRevision"],
        },
    )
    assert mutation_response.status == 200
    mutated = cast(dict[str, Any], mutation_response.json())["document"]
    assert 'command: "echo changed"' in mutated["source"]


def test_ui_server_plans_and_runs_registered_rattish_workflows(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registered = create_registered_workflow(project, "Runnable Rattish", registry_dir=tmp_path)
    registered.entrypoint.write_text(
        """Rattish: 1

Workflow:
  name: Runnable Rattish

Node prepare:
  type: bash-command
  command: "printf ready"
""",
        encoding="utf-8",
    )

    planned = _request(
        tmp_path,
        "POST",
        "/api/workflows/runnable-rattish/plan",
        body={"triggerContext": {}},
    )
    assert planned.status == 200
    plan = cast(dict[str, Any], planned.json())["plan"]
    assert plan["kind"] == "rattish"
    assert plan["runnable"] is True
    assert plan["generations"][0]["nodes"][0]["id"] == "prepare"

    executed = _request(
        tmp_path,
        "POST",
        "/api/workflows/runnable-rattish/run",
        body={"inputs": {}},
    )
    assert executed.status == 200
    run = cast(dict[str, Any], executed.json())["run"]
    assert run["success"] is True
    assert run["workflowId"] == "runnable-rattish"
    assert run["nodeOutputs"]["prepare"]["data"]["stdout"] == "ready"
    assert Path(run["logPath"]).is_file()


def test_ui_server_ignores_data_dir_query_override(tmp_path) -> None:
    requested_data_dir = tmp_path / "requested"
    requested_data_dir.mkdir()
    outside_data_dir = tmp_path / "outside"
    outside_data_dir.mkdir()
    server = GoferUiServer.__new__(GoferUiServer)
    server.data_dir = requested_data_dir
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.server = server

    assert handler._request_data_dir({"data_dir": [str(outside_data_dir)]}) == requested_data_dir


@pytest.mark.parametrize("content", ["a", "!!!!", "eA==!"])
def test_uploaded_bundle_rejects_invalid_base64_without_leaking_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    monkeypatch.setattr(server_module.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(ValueError):
        with server_module._bundle_path_from_body({"bundleContent": content}):
            pytest.fail("Invalid base64 reached the bundle importer")
    assert list(tmp_path.glob("*.zip")) == []


def test_uploaded_bundle_cleans_up_after_consumer_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(RuntimeError, match="consumer failed"):
        with server_module._bundle_path_from_body({"bundleContent": "eA=="}) as path:
            assert path.read_bytes() == b"x"
            raise RuntimeError("consumer failed")
    assert list(tmp_path.glob("*.zip")) == []


def test_ui_bundle_path_allows_data_dir_path_without_grant(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.gof.zip"
    bundle_path.write_bytes(b"zip")
    server = _fake_server(tmp_path)
    handler = _fake_handler(server)

    handler._assert_bundle_path_allowed(bundle_path, "", must_exist=True)


def test_ui_bundle_path_resolves_relative_paths_under_data_dir(tmp_path: Path) -> None:
    assert server_module._resolve_ui_bundle_path(Path("bundle.gof.zip"), tmp_path) == (
        tmp_path / "bundle.gof.zip"
    )


def test_ui_bundle_path_rejects_ungranted_outside_path(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}-outside"
    outside_dir.mkdir()
    bundle_path = outside_dir / "bundle.gof.zip"
    bundle_path.write_bytes(b"zip")
    server = _fake_server(tmp_path)
    handler = _fake_handler(server)

    with pytest.raises(WorkflowBundleError, match="outside the approved"):
        handler._assert_bundle_path_allowed(bundle_path, "", must_exist=True)


def test_ui_bundle_path_allows_granted_outside_path(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}-granted"
    outside_dir.mkdir()
    bundle_path = outside_dir / "bundle.gof.zip"
    bundle_path.write_bytes(b"zip")
    server = _fake_server(tmp_path)
    grant_id = server.path_grants.register(outside_dir, "grant-1")
    handler = _fake_handler(server)

    handler._assert_bundle_path_allowed(bundle_path, grant_id, must_exist=True)


def test_ui_bundle_path_rejects_symlink_parent_escape(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}-symlink-target"
    outside_dir.mkdir()
    bundle_path = outside_dir / "bundle.gof.zip"
    bundle_path.write_bytes(b"zip")
    symlink_dir = tmp_path / "linked"
    try:
        symlink_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlinks are not available: {exc}")
    server = _fake_server(tmp_path)
    handler = _fake_handler(server)

    with pytest.raises(WorkflowBundleError, match="outside the approved"):
        handler._assert_bundle_path_allowed(symlink_dir / "bundle.gof.zip", "", must_exist=True)


def test_ui_bundle_path_uses_nearest_existing_parent_for_missing_destination(
    tmp_path: Path,
) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}-export"
    outside_dir.mkdir()
    destination = outside_dir / "missing" / "bundle.gof.zip"
    server = _fake_server(tmp_path)
    handler = _fake_handler(server)

    with pytest.raises(WorkflowBundleError, match="outside the approved"):
        handler._assert_bundle_path_allowed(destination, "", must_exist=False)

    grant_id = server.path_grants.register(outside_dir, "grant-1")
    handler._assert_bundle_path_allowed(destination, grant_id, must_exist=False)


@pytest.mark.parametrize("secret", ["", "invalid-\u00e9"])
def test_ui_desktop_path_grant_registration_requires_secret(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, secret: str
) -> None:
    # Its own folder: a passing case's tmp_path is removed and its name reused.
    outside_dir = tmp_path_factory.mktemp("registered")

    denied = _request(
        tmp_path,
        "POST",
        "/api/desktop/path-grants",
        body={"grantId": "grant-1", "path": str(outside_dir)},
        headers={"X-Gofer-Desktop-Grant-Secret": secret},
    )
    assert denied.status == 400

    allowed = _request(
        tmp_path,
        "POST",
        "/api/desktop/path-grants",
        body={"grantId": "grant-1", "path": str(outside_dir)},
        headers={"X-Gofer-Desktop-Grant-Secret": "test-secret"},
    )
    assert allowed.status == 201
    payload = cast(dict[str, str], allowed.json())
    assert payload["grantId"] == "grant-1"


def test_ui_server_doctor_endpoint_returns_health_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        server_module,
        "health_payload",
        lambda data_dir, workflow=None: {
            "ok": True,
            "dataDir": str(data_dir),
            "workflow": workflow,
        },
    )
    server = GoferUiServer.__new__(GoferUiServer)
    server.data_dir = tmp_path
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.server = server
    server.server_address = ("127.0.0.1", 8765)
    server.api_token = "test-ui-token"
    handler.headers = Message()
    handler.headers["Host"] = "127.0.0.1:8765"
    handler.headers["Authorization"] = "Bearer test-ui-token"
    handler.path = "/api/doctor"
    handler.wfile = BytesIO()
    status: dict[str, int] = {}

    def send_response(code: int, message: str | None = None) -> None:
        status["code"] = code

    def send_header(_keyword: str, _value: str) -> None:
        return None

    def end_headers() -> None:
        return None

    setattr(handler, "send_response", send_response)
    setattr(handler, "send_header", send_header)
    setattr(handler, "end_headers", end_headers)

    handler.do_GET()

    handler.wfile.seek(0)
    payload = json.loads(handler.wfile.read().decode("utf-8"))
    assert status["code"] == 200
    assert payload == {"ok": True, "dataDir": str(tmp_path), "workflow": None}


def test_ui_server_log_endpoint_forwards_range_query(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_workflow_run_log_payload(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"workflowId": "wf", "runId": "run.log", "logText": "3456"}

    monkeypatch.setattr(
        server_module,
        "workflow_run_log_payload",
        fake_workflow_run_log_payload,
    )
    server = GoferUiServer.__new__(GoferUiServer)
    server.data_dir = tmp_path
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.server = server
    server.server_address = ("127.0.0.1", 8765)
    server.api_token = "test-ui-token"
    handler.headers = Message()
    handler.headers["Host"] = "127.0.0.1:8765"
    handler.headers["Authorization"] = "Bearer test-ui-token"
    handler.path = "/api/workflows/wf/logs/run.log?offset=3&limit=4&tailBytes=8&details=0"
    handler.wfile = BytesIO()
    status: dict[str, int] = {}

    def send_response(code: int, message: str | None = None) -> None:
        status["code"] = code

    def send_header(_keyword: str, _value: str) -> None:
        return None

    def end_headers() -> None:
        return None

    setattr(handler, "send_response", send_response)
    setattr(handler, "send_header", send_header)
    setattr(handler, "end_headers", end_headers)

    handler.do_GET()

    handler.wfile.seek(0)
    payload = json.loads(handler.wfile.read().decode("utf-8"))
    assert status["code"] == 200
    assert payload == {"log": {"workflowId": "wf", "runId": "run.log", "logText": "3456"}}
    assert captured["args"] == ("wf", "run.log", tmp_path)
    assert captured["kwargs"] == {
        "offset": 3,
        "limit": 4,
        "tail_bytes": 8,
        "include_details": False,
    }


def test_provider_settings_discovery_routes(monkeypatch, tmp_path) -> None:
    calls = []

    def provider(provider_id, *, refresh):
        calls.append((provider_id, refresh))
        return SimpleNamespace(to_ui_payload=lambda: {"id": provider_id, "models": []})

    monkeypatch.setattr(
        server_module,
        "provider_capability_service",
        lambda: SimpleNamespace(
            snapshot_payload=lambda: {"providers": [{"id": "codex", "discoveryStatus": "pending"}]},
            provider=provider,
        ),
    )
    snapshot = _request(tmp_path, "GET", "/api/provider/capabilities?snapshot=1")
    assert snapshot.status == 200
    assert snapshot.json() == {"providers": [{"id": "codex", "discoveryStatus": "pending"}]}
    assert calls == []
    refreshed = _request(tmp_path, "GET", "/api/provider/capabilities?provider=codex&refresh=1")
    assert refreshed.status == 200
    assert refreshed.json() == {"providers": [{"id": "codex", "models": []}]}
    assert calls == [("codex", True)]
    invalid = _request(tmp_path, "GET", "/api/provider/capabilities?provider=unknown")
    assert invalid.status == 400
    assert calls == [("codex", True)]


def test_ui_server_get_routes_forward_to_api_payloads(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_list(data_dir, *, check_cancelled):
        calls["list"] = data_dir
        return {"workflows": []}

    def fake_latest(workflow_id, data_dir):
        calls["latest"] = (workflow_id, data_dir)
        return {"id": "latest.log"}

    def fake_logs(workflow_id, data_dir, **kwargs):
        calls["logs"] = (workflow_id, data_dir, kwargs)
        return {"runs": [], "total": 0}

    def fake_events(workflow_id, run_id, data_dir):
        calls["events"] = (workflow_id, run_id, data_dir)
        return [{"type": "start"}]

    monkeypatch.setattr(server_module, "list_workflow_payloads", fake_list)
    monkeypatch.setattr(server_module, "latest_workflow_log_payload", fake_latest)
    monkeypatch.setattr(server_module, "list_workflow_run_logs_payload", fake_logs)
    monkeypatch.setattr(server_module, "workflow_run_events_payload", fake_events)
    monkeypatch.setattr(
        server_module,
        "provider_payload",
        lambda: {"providers": [{"id": "codex"}]},
    )
    monkeypatch.setattr(
        server_module,
        "provider_capabilities_payload",
        lambda *, refresh=False: {"providers": [{"id": "codex", "refresh": refresh}]},
    )

    workflows = _request(tmp_path, "GET", "/api/workflows")
    providers = _request(tmp_path, "GET", "/api/chat/providers")
    capabilities = _request(tmp_path, "GET", "/api/provider/capabilities?refresh=true")
    latest = _request(tmp_path, "GET", "/api/workflows/wf/logs/latest")
    logs = _request(
        tmp_path,
        "GET",
        "/api/workflows/wf/logs?offset=2&limit=3&status=success&trigger=ui&q=needle",
    )
    events = _request(tmp_path, "GET", "/api/workflows/wf/logs/run.log/events")

    assert workflows.status == 200
    assert workflows.json() == {"workflows": []}
    assert providers.json() == {"providers": [{"id": "codex"}]}
    assert capabilities.json() == {"providers": [{"id": "codex", "refresh": True}]}
    assert latest.json() == {"log": {"id": "latest.log"}}
    assert logs.json() == {"runs": [], "total": 0}
    assert events.json() == {"events": [{"type": "start"}]}
    assert calls["list"] == tmp_path
    assert calls["latest"] == ("wf", tmp_path)
    assert calls["events"] == ("wf", "run.log", tmp_path)
    assert calls["logs"] == (
        "wf",
        tmp_path,
        {
            "offset": 2,
            "limit": 3,
            "status": "success",
            "trigger_type": "ui",
            "search": "needle",
            "started_after": None,
            "started_before": None,
        },
    )


def test_ui_server_post_workflow_routes(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_create(name, project_root, *, registry_dir):
        calls["create"] = (name, project_root, registry_dir)
        return {"id": "created"}

    def fake_open_project(project_root, *, registry_dir, check_cancelled):
        calls["open_project"] = (project_root, registry_dir)
        return {"projectRoot": str(project_root), "workflows": [{"id": "existing"}]}

    def fake_rename(workflow_id, name, data_dir):
        calls["rename"] = (workflow_id, name, data_dir)
        return {"id": "renamed"}

    def fake_duplicate(workflow_id, name, data_dir):
        calls["duplicate"] = (workflow_id, name, data_dir)
        return {"id": "copy"}

    monkeypatch.setattr(server_module, "create_registered_workflow_payload", fake_create)
    monkeypatch.setattr(server_module, "open_project_payload", fake_open_project)
    monkeypatch.setattr(server_module, "rename_workflow_payload", fake_rename)
    monkeypatch.setattr(server_module, "duplicate_workflow_payload", fake_duplicate)

    project_root = tmp_path / "project"
    project_root.mkdir()
    create = _request(
        tmp_path,
        "POST",
        "/api/workflows",
        body={"name": "Created", "projectRoot": str(project_root)},
    )
    open_project = _request(
        tmp_path,
        "POST",
        "/api/projects/open",
        body={"projectRoot": str(project_root)},
    )
    renamed = _request(tmp_path, "POST", "/api/workflows/wf/rename", body={"name": "Renamed"})
    duplicate = _request(tmp_path, "POST", "/api/workflows/wf/duplicate", body={"name": "Copy"})

    assert create.status == 201
    assert create.json() == {"workflow": {"id": "created"}}
    assert open_project.status == 200
    assert open_project.json() == {
        "projectRoot": str(project_root),
        "workflows": [{"id": "existing"}],
    }
    assert renamed.status == 200
    assert duplicate.status == 201
    assert calls == {
        "create": ("Created", project_root, tmp_path),
        "open_project": (project_root, tmp_path),
        "rename": ("wf", "Renamed", tmp_path),
        "duplicate": ("wf", "Copy", tmp_path),
    }


def test_ui_server_rerun_route_passes_the_run_and_given_inputs(monkeypatch, tmp_path) -> None:
    import gofer.ui.api as api_module

    async def fake_rerun(run_id, data_dir, **kwargs):
        return {"runId": run_id, "dataDir": str(data_dir), **kwargs}

    monkeypatch.setattr(api_module, "rerun_workflow_payload", fake_rerun)
    assert _request(
        tmp_path,
        "POST",
        "/api/rattish/runs/20261114T000000Z-abc/rerun",
        body={
            "workflowId": "wf",
            "background": True,
            "inputs": {"token": "pw"},
            "startedBy": "rem",
        },
    ).json() == {
        "run": {
            "runId": "20261114T000000Z-abc",
            "dataDir": str(tmp_path),
            "workflow_id": "wf",
            "inputs": {"token": "pw"},
            "background": True,
            "started_by": "rem",
        }
    }
    found = cast(
        dict[str, Any], _request(tmp_path, "POST", "/api/rattish/runs/r2/rerun", body={}).json()
    )
    assert found["run"]["workflow_id"] is None and found["run"]["started_by"] == "manual"
    for body in ({"inputs": "token=pw"}, {"workflowId": 3}, {"startedBy": "phone"}):
        refused = _request(tmp_path, "POST", "/api/rattish/runs/r2/rerun", body=body)
        assert refused.status == 400


def test_ui_server_run_failure_route_passes_the_run_and_node(monkeypatch, tmp_path) -> None:
    import gofer.ui.run_failure as run_failure

    def fake_failure(run_id, data_dir, **kwargs):
        if run_id == "gone":
            raise run_failure.RunFailureError("Run 'gone' was not found.")
        return {"runId": run_id, "dataDir": str(data_dir), **kwargs}

    monkeypatch.setattr(run_failure, "run_failure_payload", fake_failure)
    assert _request(
        tmp_path, "GET", "/api/rattish/runs/r1/failure?workflowId=wf&nodeId=ship"
    ).json() == {
        "runId": "r1",
        "dataDir": str(tmp_path),
        "workflow_id": "wf",
        "node_id": "ship",
    }
    found = cast(dict[str, Any], _request(tmp_path, "GET", "/api/rattish/runs/r1/failure").json())
    assert found["node_id"] is None
    missing = _request(tmp_path, "GET", "/api/rattish/runs/gone/failure")
    assert missing.status == 404
    assert missing.json() == {"error": "Run 'gone' was not found."}


def test_ui_server_chat_routes_and_stream_headers(monkeypatch, tmp_path) -> None:
    async def fake_chat(**kwargs):
        assert kwargs["permission_mode"] == "danger-full-access"
        return {"reply": "ok", "provider": kwargs["provider"]}

    async def fake_stream(**kwargs):
        assert kwargs["permission_mode"] == "read-only"
        yield {"type": "message", "body": "one"}
        yield {"type": "done"}

    monkeypatch.setattr(server_module, "run_workflow_chat", fake_chat)
    monkeypatch.setattr(server_module, "stream_workflow_chat", fake_stream)
    monkeypatch.setattr(
        server_module,
        "undo_chat_changes",
        lambda change_set_id, data_dir: {
            "id": change_set_id,
            "undone": True,
            "dataDir": str(data_dir),
        },
    )
    monkeypatch.setattr(
        server_module,
        "redo_chat_changes",
        lambda change_set_id, data_dir: {
            "id": change_set_id,
            "undone": False,
            "dataDir": str(data_dir),
        },
    )

    chat = _request(
        tmp_path,
        "POST",
        "/api/chat",
        body={"provider": "codex", "messages": [], "permissionMode": "danger-full-access"},
    )
    stream = _request(
        tmp_path, "POST", "/api/chat/stream", body={"messages": [], "permissionMode": "read-only"}
    )
    undo = _request(
        tmp_path,
        "POST",
        "/api/chat/changes/undo",
        body={"changeSetId": "change-1"},
    )
    redo = _request(
        tmp_path,
        "POST",
        "/api/chat/changes/redo",
        body={"changeSetId": "change-1"},
    )

    assert chat.status == 200
    assert chat.json() == {"reply": "ok", "provider": "codex"}
    assert stream.status == 200
    assert stream.header("Content-Type") == "application/x-ndjson; charset=utf-8"
    assert stream.header("Cache-Control") == "no-store"
    assert stream.text().splitlines() == [
        '{"type": "message", "body": "one"}',
        '{"type": "done"}',
    ]
    assert undo.status == 200
    assert undo.json() == {"id": "change-1", "undone": True, "dataDir": str(tmp_path)}
    assert redo.status == 200
    assert redo.json() == {"id": "change-1", "undone": False, "dataDir": str(tmp_path)}


def test_ui_server_uploads_binary_chat_attachments_and_transcribes_audio(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_transcribe(body, *, data_dir):
        assert base64.b64decode(body["data"]) == b"recording"
        assert data_dir == tmp_path
        return {"text": "transcribed message"}

    monkeypatch.setattr(server_module, "transcribe_chat_audio", fake_transcribe)
    attachment = _request(
        tmp_path,
        "POST",
        "/api/chat/attachments",
        body={
            "threadId": "thread-1",
            "files": [
                {
                    "name": "screen.png",
                    "type": "image/png",
                    "data": base64.b64encode(b"\x89PNG\x00binary").decode(),
                }
            ],
        },
    )
    transcription = _request(
        tmp_path,
        "POST",
        "/api/chat/transcribe",
        body={"data": base64.b64encode(b"recording").decode()},
    )

    assert attachment.status == 201
    attachment_payload = cast(dict[str, Any], attachment.json())
    stored = cast(dict[str, Any], attachment_payload["attachments"][0])
    assert stored["name"] == "screen.png"
    assert stored["type"] == "image/png"
    path = tmp_path / "chat-attachments" / "thread-1" / stored["storageName"]
    assert path.read_bytes() == b"\x89PNG\x00binary"
    assert transcription.status == 200
    assert transcription.json() == {"text": "transcribed message"}


def test_ui_server_streams_local_transcription_sessions(monkeypatch, tmp_path) -> None:
    session_id = "a" * 32
    from gofer.core import provider_preferences

    monkeypatch.setattr(provider_preferences, "get_data_dir", lambda: tmp_path)

    def fake_start(*, data_dir, engine, engine_chosen):
        assert data_dir == tmp_path
        # The recognizer chosen in Settings > Devices; the OS's own until changed.
        assert (engine, engine_chosen) == ("system", False)
        return {"sessionId": session_id}

    def fake_chunk(body):
        assert body["sessionId"] == session_id
        assert base64.b64decode(body["data"]) == b"\x01\x00"
        return {"text": "words as"}

    def fake_finish(body):
        assert body == {"sessionId": session_id}
        return {"text": "words as spoken"}

    def fake_cancel(body):
        assert body == {"sessionId": session_id}
        return {"cancelled": True}

    monkeypatch.setattr(server_module, "start_chat_transcription", fake_start)
    monkeypatch.setattr(server_module, "stream_chat_transcription", fake_chunk)
    monkeypatch.setattr(server_module, "finish_chat_transcription", fake_finish)
    monkeypatch.setattr(server_module, "cancel_chat_transcription", fake_cancel)

    started = _request(tmp_path, "POST", "/api/chat/transcribe/start", body={})
    chunked = _request(
        tmp_path,
        "POST",
        "/api/chat/transcribe/chunk",
        body={"data": base64.b64encode(b"\x01\x00").decode(), "sessionId": session_id},
    )
    finished = _request(
        tmp_path,
        "POST",
        "/api/chat/transcribe/finish",
        body={"sessionId": session_id},
    )
    cancelled = _request(
        tmp_path,
        "POST",
        "/api/chat/transcribe/cancel",
        body={"sessionId": session_id},
    )

    assert started.status == 201
    assert started.json() == {"sessionId": session_id}
    assert chunked.json() == {"text": "words as"}
    assert finished.json() == {"text": "words as spoken"}
    assert cancelled.json() == {"cancelled": True}


def test_ui_server_tidies_a_dictation_that_continues_earlier_words(monkeypatch, tmp_path) -> None:
    from gofer.ui import voice_tidy

    calls: list[tuple[str, str]] = []

    def fake_tidy(heard, chosen, data_dir, *, context="", before=""):
        calls.append((heard, before))
        return {"text": heard.capitalize(), "tidied": True, "reason": ""}

    monkeypatch.setattr(GoferUiServer, "rem_home", None, raising=False)
    monkeypatch.setattr(voice_tidy, "current", lambda *_args: {"available": False})
    monkeypatch.setattr(voice_tidy, "tidy", fake_tidy)

    continued = _request(
        tmp_path,
        "POST",
        "/api/chat/voice-tidy",
        body={"text": "and invite the team", "before": "Book a room."},
    )
    alone = _request(tmp_path, "POST", "/api/chat/voice-tidy", body={"text": "book a room"})
    refused = _request(
        tmp_path, "POST", "/api/chat/voice-tidy", body={"text": "words", "before": 3}
    )

    assert continued.json() == {"text": "And invite the team", "tidied": True, "reason": ""}
    assert calls == [("and invite the team", "Book a room."), ("book a room", "")]
    assert alone.status == 200
    assert refused.status == 400 and refused.json() == {"error": "before must be a string"}


def test_ui_server_reports_and_saves_the_speech_recognizer(monkeypatch, tmp_path) -> None:
    from gofer.core import provider_preferences

    monkeypatch.setattr(provider_preferences, "get_data_dir", lambda: tmp_path)
    calls = []

    def fake_engines(*, data_dir, refresh, preference: dict[str, object]):
        calls.append((refresh, preference))
        # A Windows PC with the offline model downloaded dictates with it until the
        # user picks the Windows recognizer.
        engine = "offline" if not preference["chosen"] else preference["engine"]
        return {
            "system": {"id": "windows", "available": True},
            "offline": {"installed": True},
            "engine": engine,
        }

    monkeypatch.setattr(server_module, "transcription_engines", fake_engines)
    shown = _request(tmp_path, "GET", "/api/chat/transcribe/engines")
    assert shown.json() == {
        "engine": "offline",
        "chosen": False,
        "system": {"id": "windows", "available": True},
        "offline": {"installed": True},
    }
    _request(tmp_path, "GET", "/api/chat/transcribe/engines?refresh=1")
    unchosen = {"engine": "system", "chosen": False}
    assert calls == [(False, unchosen), (True, unchosen)]

    saved = _request(tmp_path, "POST", "/api/chat/transcribe/engines", body={"engine": "system"})
    assert saved.json() == {"saved": True, "engine": "system", "chosen": True}
    assert _request(tmp_path, "GET", "/api/chat/transcribe/engines").json() == {
        "engine": "system",
        "chosen": True,
        "system": {"id": "windows", "available": True},
        "offline": {"installed": True},
    }
    saved = _request(tmp_path, "POST", "/api/chat/transcribe/engines", body={"engine": "offline"})
    assert saved.json() == {"saved": True, "engine": "offline", "chosen": True}
    started: dict[str, object] = {}

    def fake_start(*, data_dir, engine, engine_chosen):
        started.update(engine=engine, chosen=engine_chosen)
        return {"sessionId": "b" * 32}

    monkeypatch.setattr(server_module, "start_chat_transcription", fake_start)
    _request(tmp_path, "POST", "/api/chat/transcribe/start", body={})
    assert started == {"engine": "offline", "chosen": True}
    refused = _request(tmp_path, "POST", "/api/chat/transcribe/engines", body={"engine": "cloud"})
    assert refused.status == 400


def test_ui_server_downloads_the_offline_model_and_tries_the_mac_recognizer(
    monkeypatch, tmp_path
) -> None:
    from gofer.core import provider_preferences
    from gofer.ui import system_speech

    monkeypatch.setattr(provider_preferences, "get_data_dir", lambda: tmp_path)
    started = []
    status: dict[str, object] = {
        "name": "Raticode's offline speech model",
        "installed": False,
        "downloadMb": 475,
    }

    def fake_start(*, data_dir):
        started.append(data_dir)
        return {**status, "download": {"state": "starting", "receivedBytes": 0, "totalBytes": 0}}

    monkeypatch.setattr(server_module, "start_offline_model_download", fake_start)
    monkeypatch.setattr(server_module, "offline_model_status", lambda *, data_dir: status)
    shown = _request(tmp_path, "GET", "/api/chat/transcribe/model")
    assert shown.json() == status
    begun = _request(tmp_path, "POST", "/api/chat/transcribe/model", body={})
    assert begun.status == 202
    assert begun.json() == {
        **status,
        "download": {"state": "starting", "receivedBytes": 0, "totalBytes": 0},
    }
    assert len(started) == 1

    refreshed = []
    monkeypatch.setattr(
        system_speech,
        "try_system_engine",
        lambda: {"id": "macos", "ok": True, "message": "macOS speech recognition works."},
    )

    def fake_engines(*, data_dir, refresh, preference):
        refreshed.append(refresh)
        return {
            "system": {"id": "macos", "available": True},
            "offline": status,
            "engine": preference["engine"],
        }

    monkeypatch.setattr(server_module, "transcription_engines", fake_engines)
    tried = _request(tmp_path, "POST", "/api/chat/transcribe/engines/test", body={})
    assert tried.json() == {
        "test": {"id": "macos", "ok": True, "message": "macOS speech recognition works."},
        "engine": "system",
        "chosen": False,
        "system": {"id": "macos", "available": True},
        "offline": status,
    }
    assert refreshed == [True]


def test_ui_server_chat_stream_provider_error_is_ndjson(monkeypatch, tmp_path) -> None:
    async def bad_stream(**_kwargs):
        raise server_module.ChatProviderError("stream unavailable")
        yield {"type": "unreachable"}

    monkeypatch.setattr(server_module, "stream_workflow_chat", bad_stream)

    response = _request(tmp_path, "POST", "/api/chat/stream", body={"messages": []})

    assert response.status == 200
    assert response.header("Content-Type") == "application/x-ndjson; charset=utf-8"
    assert response.text().splitlines() == ['{"type": "error", "error": "stream unavailable"}']


def test_ui_server_unknown_invalid_json_and_options(tmp_path) -> None:
    missing = _request(tmp_path, "GET", "/api/not-found")
    invalid = _request(tmp_path, "POST", "/api/workflows", body=b"{")
    options = _request(
        tmp_path,
        "OPTIONS",
        "/api/workflows",
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert missing.status == 404
    assert missing.json() == {"error": "Not found"}
    assert invalid.status == 400
    assert "Expecting property name" in cast(dict[str, str], invalid.json())["error"]
    assert options.status == 204
    assert options.body == b""
    assert options.header("Access-Control-Allow-Origin") == "http://127.0.0.1:5173"
    assert options.header("Access-Control-Allow-Methods") == (
        "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    )
    assert "Authorization" in (options.header("Access-Control-Allow-Headers") or "")
    assert "X-Gofer-Known" in (options.header("Access-Control-Allow-Headers") or "")
    # The window may reuse this answer, instead of asking again before every poll.
    assert options.header("Access-Control-Max-Age") == "600"


def test_ui_server_rejects_deeply_nested_json_with_a_response(tmp_path) -> None:
    body = b'{"name":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
    response = _request(tmp_path, "POST", "/api/workflows", body=body)
    assert response.status == 400
    assert "nesting" in response.text()


@pytest.mark.parametrize("extra_depth", [0, 1])
def test_ui_json_nesting_limit_boundary(tmp_path, extra_depth: int) -> None:
    depth = server_module.UI_JSON_MAX_DEPTH + extra_depth
    body = b'{"value":' + b"[" * depth + b"]" * depth + b"}"
    handler = _fake_handler(_fake_server(tmp_path))
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = BytesIO(body)
    if extra_depth:
        with pytest.raises(json.JSONDecodeError, match="nesting"):
            handler._read_json()
    else:
        assert "value" in handler._read_json()


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e999"])
def test_ui_server_rejects_nonfinite_json_numbers(tmp_path, number: bytes) -> None:
    response = _request(tmp_path, "POST", "/api/workflows", body=b'{"name":' + number + b"}")
    assert response.status == 400
    assert "finite" in response.text()


@pytest.mark.parametrize("value", [[], {}, True, 1.5])
def test_ui_server_rejects_invalid_optional_integer(tmp_path, value) -> None:
    response = _request(tmp_path, "POST", "/api/workflows/wf/logs/prune", body={"keepLast": value})
    assert response.status == 400
    assert "keepLast must be an integer" in response.text()


@pytest.mark.parametrize("value", [[], {}, True, 1])
def test_ui_server_rejects_invalid_optional_string(tmp_path, value) -> None:
    response = _request(tmp_path, "POST", "/api/chat/commit-message", body={"effort": value})
    assert response.status == 400
    assert "effort must be a string" in response.text()


def test_ui_server_serve_shutdown_cleans_up(monkeypatch, tmp_path, capsys) -> None:
    class FakeServer:
        def __init__(self) -> None:
            self.server_address = ("127.0.0.1", 4321)
            self.data_dir = tmp_path
            self.gofer_cli_path = None
            self.api_token = "ready-token"
            self.emit_ready_token = False
            self.calls: list[str] = []

        def serve_forever(self) -> None:
            self.calls.append("serve_forever")
            raise KeyboardInterrupt

        def server_close(self) -> None:
            self.calls.append("server_close")

    fake_server = FakeServer()
    # The desktop's backend reads no secrets folder, but systemd's variable naming one is
    # sealed before the server (and anything it spawns) starts.
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path / "creds"))
    inherited: list[str | None] = []

    def create_server(**_kwargs: Any) -> FakeServer:
        inherited.append(os.environ.get("CREDENTIALS_DIRECTORY"))
        return fake_server

    monkeypatch.setattr(server_module, "create_server", create_server)
    monkeypatch.setattr(server_module, "_install_shutdown_handlers", lambda _server: None)
    usage_refresh: list[Any] = []

    def start_background_refresh(data_dir: Path) -> Any:
        usage_refresh.append(data_dir)
        return lambda: usage_refresh.append("stop")

    monkeypatch.setattr(server_module, "start_background_refresh", start_background_refresh)
    monkeypatch.setattr(server_module, "start_temp_cleanup", lambda _data_dir: lambda: None)

    server_module.serve(host="127.0.0.1", port=9999, data_dir=tmp_path)
    assert usage_refresh == [tmp_path, "stop"]
    assert inherited == [None]
    assert "CREDENTIALS_DIRECTORY" not in os.environ

    output = capsys.readouterr()
    assert "GOFER_UI_READY" in output.out
    assert "ready-token" not in output.out
    assert "GOFER_UI_STOPPED" in output.err
    assert fake_server.calls == ["serve_forever", "server_close"]


def test_ui_server_chat_unhandled_error_returns_json(monkeypatch, tmp_path) -> None:
    async def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(server_module, "run_workflow_chat", explode)
    response = _request(
        tmp_path,
        "POST",
        "/api/chat",
        body={"messages": [{"role": "user", "body": "hello"}]},
    )

    assert response.status == 500
    assert response.json() == {"error": "Rem failed: boom"}


def test_ui_server_rejects_large_body_before_json_parse() -> None:
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    headers = Message()
    headers["Content-Length"] = str(DEFAULT_RESOURCE_LIMITS.max_api_request_body_bytes + 1)
    handler.headers = headers
    handler.rfile = BytesIO(b"")

    try:
        handler._read_json()
    except json.JSONDecodeError as exc:
        assert "exceeds limit" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected JSONDecodeError")


def test_ui_server_uses_configured_body_limit_before_json_parse() -> None:
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    headers = Message()
    headers["Content-Length"] = "5"
    handler.headers = headers
    handler.rfile = BytesIO(b"")
    handler.server = _fake_server(
        Path("/tmp"),
        resource_limits=ResourceLimits(max_api_request_body_bytes=4),
    )

    try:
        handler._read_json()
    except json.JSONDecodeError as exc:
        assert "limit 4 bytes" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected JSONDecodeError")


def test_ui_server_rejects_missing_content_length_before_json_parse() -> None:
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.headers = Message()
    handler.rfile = BytesIO(b'{"name": "large"}')

    try:
        handler._read_json()
    except json.JSONDecodeError as exc:
        assert "Content-Length is required" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected JSONDecodeError")


def test_ui_server_rejects_invalid_content_length_before_json_parse() -> None:
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    headers = Message()
    headers["Content-Length"] = "not-a-number"
    handler.headers = headers
    handler.rfile = BytesIO(b"{}")

    try:
        handler._read_json()
    except json.JSONDecodeError as exc:
        assert "Invalid Content-Length" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected JSONDecodeError")


def test_ui_server_rejects_incomplete_body_before_json_parse() -> None:
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    headers = Message()
    headers["Content-Length"] = "10"
    handler.headers = headers
    handler.rfile = BytesIO(b"{}")

    try:
        handler._read_json()
    except json.JSONDecodeError as exc:
        assert "Incomplete request body" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected JSONDecodeError")


def test_ui_server_delete_global_chat_prompt(tmp_path) -> None:
    chat_path = workflow_chat_prompt_path(tmp_path, "workflow-assistant")
    chat_path.parent.mkdir(parents=True)
    chat_path.write_text("old chat prompt\n", encoding="utf-8")
    response = _request(tmp_path, "DELETE", "/api/chat")

    assert response.status == 200
    assert response.json() == {"workflowId": "workflow-assistant", "deleted": True}
    assert not chat_path.exists()


def test_ui_server_delete_thread_chat_prompt(tmp_path) -> None:
    chat_path = workflow_chat_prompt_path(tmp_path, "workflow-assistant:thread-1")
    chat_path.parent.mkdir(parents=True)
    chat_path.write_text("old thread prompt\n", encoding="utf-8")
    response = _request(tmp_path, "DELETE", "/api/chat/threads/thread-1")

    assert response.status == 200
    assert response.json() == {"workflowId": "workflow-assistant:thread-1", "deleted": True}
    assert not chat_path.exists()


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_second_brain_requires_a_desktop_folder_grant(tmp_path: Path, endpoint: str) -> None:
    data = tmp_path / "data"
    data.mkdir()
    brain = tmp_path / "knowledge"
    brain.mkdir()
    result = _request(
        data,
        "POST",
        endpoint,
        body={
            "workflow": {"remSecondBrain": {"enabled": True, "root": str(brain), "format": "md"}},
            "messages": [{"role": "user", "body": "Search notes"}],
        },
    )
    assert result.status == 400
    assert "Second Brain folder" in result.text()
    assert "Retry your message" in result.text()


@pytest.mark.parametrize(
    "provider", ["codex", "claude_code", "cursor", "copilot", "opencode", "grok", "antigravity"]
)
def test_commit_message_endpoint_uses_restricted_generator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: str
) -> None:
    from unittest.mock import AsyncMock

    from gofer.ui import commit_message

    generate = AsyncMock(return_value={"message": "fix: expose resolved edits"})
    monkeypatch.setattr(commit_message, "generate_commit_message", generate)
    response = _request(
        tmp_path,
        "POST",
        "/api/chat/commit-message",
        body={
            "provider": provider,
            "model": "cli-default",
            "diff": "+staged",
            "permissionMode": "cli-managed",
        },
    )
    assert response.status == 200
    assert response.json() == {"message": "fix: expose resolved edits"}
    generate.assert_awaited_once_with(
        provider=provider,
        model="cli-default",
        effort=None,
        permission_mode="cli-managed",
        diff="+staged",
        project_root=None,
        inspect_staged=False,
    )
    generate.side_effect = ValueError("Stage changes first.")
    failed = _request(tmp_path, "POST", "/api/chat/commit-message", body={"diff": ""})
    assert failed.status == 400
    assert failed.json() == {"error": "Stage changes first."}


def test_second_brain_expired_grant_recovers_after_registration(
    monkeypatch, tmp_path: Path
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    brain = tmp_path / "brain"
    brain.mkdir()
    now = 1000.0
    monkeypatch.setattr(server_module.time, "monotonic", lambda: now)
    server = _fake_server(data)
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.server = server
    handler.headers = Message()
    handler.headers["X-Gofer-Desktop-Grant-Secret"] = "test-secret"
    registration = {"path": str(brain), "grantId": "private-grant"}
    body = {
        "workflow": {
            "remSecondBrain": {"enabled": True, "root": str(brain), "grantId": "private-grant"}
        }
    }
    handler._register_desktop_path_grant(registration)
    handler._validate_second_brain(body)
    now += 900
    with pytest.raises(WorkflowBundleError, match="Retry your message"):
        handler._validate_second_brain(body)
    handler._register_desktop_path_grant(registration)
    handler._validate_second_brain(body)


@pytest.mark.parametrize(
    "path", ["/api/provider/accounts", "/api/workflows", "/api/doctor", "/workflows/demo/usage"]
)
@pytest.mark.parametrize(
    "origin", [None, "null", "https://attacker.example", "http://127.0.0.1:5173"]
)
def test_sensitive_reads_never_disclose_data_without_credentials(tmp_path, path, origin):
    response = _request(
        tmp_path, "GET", path, headers={"Origin": origin} if origin else {}, authenticated=False
    )
    assert response.status in {401, 403}
    assert "test-ui-token" not in response.text()
    assert str(tmp_path) not in response.text()


@pytest.mark.parametrize(
    "host",
    [
        "attacker.invalid:8765",
        "127.0.0.1:8766",
        "127.0.0.1:8765@attacker.invalid",
        "127.0.0.1:bad",
        "127.0.0.1:8765/path",
    ],
)
def test_api_rejects_arbitrary_host_even_with_credentials(tmp_path, host):
    response = _request(tmp_path, "GET", "/api/doctor", headers={"Host": host})
    assert response.status == 403
    assert "test-ui-token" not in response.text()


def test_opaque_origin_desktop_requires_token_and_narrow_preflight(tmp_path):
    authenticated = _request(tmp_path, "GET", "/api/workflows", headers={"Origin": "null"})
    assert authenticated.status == 200
    assert authenticated.header("Access-Control-Allow-Origin") == "null"
    preflight = _request(
        tmp_path,
        "OPTIONS",
        "/api/workflows",
        authenticated=False,
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert preflight.status == 204
    assert preflight.body == b""
    assert preflight.header("Access-Control-Allow-Origin") == "null"
    rejected = _request(
        tmp_path, "OPTIONS", "/api/workflows", authenticated=False, headers={"Origin": "null"}
    )
    assert rejected.status == 403
    assert "null" not in server_module._default_allowed_origins()


_PATH_CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/path-containment.json").read_text()
)["cases"]


@pytest.mark.parametrize("case", _PATH_CASES, ids=lambda case: case["name"])
def test_shared_path_containment_contract(tmp_path, case):
    root = tmp_path / case["root"]
    root.mkdir()
    for directory in case.get("directories", []):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    for filename in case.get("files", []):
        file = tmp_path / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture")
    for link in case.get("links", []):
        path = tmp_path / link["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(tmp_path / link["target"])
    store = DesktopPathGrantStore()
    grant = store.register(root)
    try:
        allowed = store.covers(tmp_path / case["candidate"], grant)
    except (OSError, RuntimeError):
        allowed = False
    assert allowed is case["allowed"]


def test_commit_message_body_is_rejected_before_reading_large_payload(tmp_path):
    response = _request(
        tmp_path,
        "POST",
        "/api/chat/commit-message",
        headers={"Content-Length": str(server_module.COMMIT_MESSAGE_MAX_BODY_BYTES + 1)},
    )
    assert response.status == 400
    assert "exceeds limit" in response.text()


@pytest.mark.skipif(not _sockets_available(), reason="Loopback sockets unavailable")
def test_http_handler_budget_and_incomplete_request_recovery(tmp_path, monkeypatch):
    import http.client
    import time

    monkeypatch.setattr(server_module, "UI_MAX_HANDLERS", 1)
    monkeypatch.setattr(server_module, "UI_REQUEST_READ_DEADLINE_SECONDS", 0.15)
    server = create_server(host="127.0.0.1", port=0, data_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = server.server_address
    first = socket.create_connection(address)
    first.settimeout(2)
    try:
        first.sendall(b"GET /api/health HTTP/1.1\r\n")
        time.sleep(0.03)
        second = socket.create_connection(address)
        second.settimeout(2)
        try:
            assert second.recv(1) == b""
        finally:
            second.close()
        assert first.recv(1) == b""
        time.sleep(0.03)
        connection = http.client.HTTPConnection(str(address[0]), int(address[1]), timeout=2)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {"ok": True}
        finally:
            connection.close()
        partial = socket.create_connection(address)
        partial.settimeout(2)
        try:
            partial.sendall(
                (
                    "POST /api/workflows HTTP/1.1\r\n"
                    f"Host: {address[0]}:{address[1]}\r\n"
                    f"Authorization: Bearer {server.api_token}\r\n"
                    "Content-Length: 100\r\n\r\n{"
                ).encode()
            )
            assert partial.recv(4096) == b""
        finally:
            partial.close()
    finally:
        first.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parents[1] / "fixtures/path-containment.json").read_text())[
        "lexicalCases"
    ],
)
def test_shared_cross_platform_lexical_containment(case):
    import ntpath
    import posixpath
    from pathlib import PurePosixPath, PureWindowsPath

    path_type = PureWindowsPath if case["platform"] == "win32" else PurePosixPath
    normalizer = ntpath if case["platform"] == "win32" else posixpath
    root = path_type(normalizer.normpath(case["root"]))
    candidate = path_type(normalizer.normpath(case["candidate"]))
    assert server_module._is_path_inside(cast(Any, candidate), cast(Any, root)) is case["allowed"]


def test_expensive_request_budget_does_not_block_health_and_recovers(tmp_path, monkeypatch):
    server = _fake_server(tmp_path)
    server._expensive_slots = threading.BoundedSemaphore(1)
    monkeypatch.setitem(globals(), "_fake_server", lambda *_args, **_kwargs: server)
    assert server._expensive_slots.acquire(blocking=False)
    denied = _request(tmp_path, "POST", "/api/chat", body={})
    assert denied.status == 503
    assert _request(tmp_path, "GET", "/api/health").status == 200
    server._expensive_slots.release()
    monkeypatch.setattr(GoferUiRequestHandler, "_chat_turn", lambda self, request: {"ok": True})
    assert _request(tmp_path, "POST", "/api/chat", body={}).status == 200
    assert server._expensive_slots.acquire(blocking=False)
    server._expensive_slots.release()


def test_ambiguous_body_framing_is_rejected_centrally(tmp_path):
    response = _request(
        tmp_path, "POST", "/api/workflows", body={}, headers={"Transfer-Encoding": "chunked"}
    )
    assert response.status == 400
    assert "framing" in response.text()


def test_log_revision_changes_only_with_response_content(tmp_path, monkeypatch):
    payload = {"logText": "first"}
    monkeypatch.setattr(
        server_module, "latest_workflow_log_payload", lambda *_args, **_kwargs: payload
    )
    first = _request(tmp_path, "GET", "/api/workflows/w/logs/latest")
    same = _request(tmp_path, "GET", "/api/workflows/w/logs/latest")
    assert first.header("ETag")
    assert same.header("ETag") == first.header("ETag")
    payload["logText"] = "second"
    changed = _request(tmp_path, "GET", "/api/workflows/w/logs/latest")
    assert changed.header("ETag") != first.header("ETag")


def test_async_request_deadline_cancels_work_and_preserves_stream_framing(tmp_path, monkeypatch):
    import asyncio

    cancelled = threading.Event()
    server = _fake_server(tmp_path)
    server.request_job_timeout = 0.01
    monkeypatch.setitem(globals(), "_fake_server", lambda *_args, **_kwargs: server)

    async def stalled_stream(**_kwargs):
        try:
            await asyncio.sleep(10)
            yield {"type": "done"}
        finally:
            cancelled.set()

    monkeypatch.setattr(server_module, "stream_workflow_chat", stalled_stream)
    response = _request(tmp_path, "POST", "/api/chat/stream", body={})
    assert cancelled.is_set()
    assert response.status == 200
    assert response.header("Content-Type") == "application/x-ndjson; charset=utf-8"
    assert response.json() == {"type": "error", "error": "Request exceeded its execution deadline"}


def test_commit_message_inspection_endpoint_checks_project_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import AsyncMock

    from gofer.ui import commit_message

    generate = AsyncMock(return_value={"message": "fix: summarize large changes"})
    monkeypatch.setattr(commit_message, "generate_commit_message", generate)
    project = tmp_path / "repo"
    project.mkdir()
    response = _request(
        tmp_path,
        "POST",
        "/api/chat/commit-message",
        body={"projectRoot": str(project), "inspectStaged": True},
    )
    assert response.status == 200
    generate.assert_awaited_once_with(
        provider="codex",
        model="cli-default",
        effort=None,
        permission_mode=None,
        diff="",
        project_root=project,
        inspect_staged=True,
    )
    generate.reset_mock()
    response = _request(
        tmp_path,
        "POST",
        "/api/chat/commit-message",
        body={"projectRoot": str(tmp_path.parent), "inspectStaged": True},
    )
    assert response.status == 400
    assert "approved" in response.text()
    generate.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
@pytest.mark.parametrize("fail", [False, True])
def test_rem_swarm_grant_is_private_and_revoked_after_chat(
    monkeypatch, tmp_path: Path, endpoint: str, fail: bool
) -> None:
    import urllib.error
    import urllib.request

    from gofer.ui.rem_swarms import SWARM_HELP
    from gofer.ui.swarms import SwarmManager

    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    original = _fake_server
    urls: list[str] = []

    def fake_with_swarms(*args: Any, **kwargs: Any) -> GoferUiServer:
        server = original(*args, **kwargs)
        server.swarms = manager
        return server

    def rpc(url: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params or {},
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return dict(json.load(response))

    def inspect(kwargs: dict[str, Any]) -> None:
        url = kwargs["trusted_swarm_url"]
        urls.append(url)
        resources = kwargs["workflow"]["remResources"]
        assert resources["shell"] is False
        assert resources["mcpServers"] == [{"name": "swarm", "type": "http", "url": url}]
        assert rpc(url, "initialize")["result"]["instructions"] == ""
        assert SWARM_HELP not in json.dumps(rpc(url, "tools/list"))
        help_result = rpc(
            url, "tools/call", {"name": "swarm_action", "arguments": {"action": "help"}}
        )
        help_data = json.loads(help_result["result"]["content"][0]["text"])
        assert help_data["projectRoot"] == str(tmp_path)
        assert help_data["readOnly"] is True
        assert help_data["instructions"] == SWARM_HELP
        assert str(tmp_path / "mobile") in help_data["workspacePaths"]
        assert "private-mobile-grant" not in json.dumps(help_data)
        from gofer.ui.chat import build_chat_prompt

        prompt = build_chat_prompt("codex", "cli-default", [], kwargs["workflow"])
        assert "private-mobile-grant" not in prompt
        denied = rpc(url, "tools/call", {"name": "swarm_action", "arguments": {"action": "create"}})
        assert denied["result"]["isError"] is True
        if fail:
            raise server_module.ChatProviderError("Provider failed")

    async def chat(**kwargs: Any) -> dict[str, Any]:
        inspect(kwargs)
        return {"message": "Ready"}

    async def stream(**kwargs: Any):
        inspect(kwargs)
        yield {"type": "done"}

    monkeypatch.setattr(
        __import__(__name__, fromlist=["_fake_server"]), "_fake_server", fake_with_swarms
    )
    monkeypatch.setattr(server_module, "run_workflow_chat", chat)
    monkeypatch.setattr(server_module, "stream_workflow_chat", stream)
    try:
        result = _request(
            tmp_path,
            "POST",
            endpoint,
            body={
                "permissionMode": "read-only",
                "workflow": {
                    "projectRoot": str(tmp_path),
                    "remSwarmAccess": {
                        "enabled": True,
                        "workspaceGrants": {str(tmp_path / "mobile"): "private-mobile-grant"},
                    },
                    "remResources": {
                        "shell": False,
                        "mcpServers": [
                            {"name": "swarm", "type": "http", "url": "http://untrusted.invalid"}
                        ],
                    },
                },
            },
        )
        assert result.status == (400 if fail and endpoint == "/api/chat" else 200)
        assert len(urls) == 1
        with pytest.raises(urllib.error.HTTPError) as error:
            rpc(urls[0], "tools/list")
        assert error.value.code == 401
    finally:
        manager.close()


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_rem_swarm_access_requires_project_grant(
    monkeypatch, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, endpoint: str
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    monkeypatch.setattr(
        server_module, "run_workflow_chat", lambda **kwargs: pytest.fail("Must not start")
    )
    monkeypatch.setattr(
        server_module, "stream_workflow_chat", lambda **kwargs: pytest.fail("Must not start")
    )
    result = _request(
        tmp_path,
        "POST",
        endpoint,
        body={
            "workflow": {
                "projectRoot": str(outside),
                "remSwarmAccess": {"enabled": True},
            }
        },
    )
    assert result.status == 400
    assert "outside the approved" in result.text()


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_both_chat_routes_check_rem_thread_projects(monkeypatch, tmp_path, endpoint) -> None:
    monkeypatch.setattr(
        server_module, "run_workflow_chat", lambda **kwargs: pytest.fail("Must not start")
    )
    monkeypatch.setattr(
        server_module, "stream_workflow_chat", lambda **kwargs: pytest.fail("Must not start")
    )
    result = _request(
        tmp_path,
        "POST",
        endpoint,
        body={"workflow": {"remThreads": {"projects": [{"root": "relative/project"}]}}},
    )
    assert result.status == 400
    assert "Rem projects must be existing absolute directories" in result.text()


@pytest.mark.parametrize(
    "workflow",
    [
        {"projectRoot": "/untrusted", "remSwarmAccess": {"enabled": False}},
        {"projectRoot": "", "remSwarmAccess": {"enabled": True}},
        {},
    ],
)
@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_disabled_or_projectless_rem_chat_has_no_swarm_grant(
    monkeypatch, tmp_path, workflow, endpoint
):
    async def chat(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["trusted_swarm_url"] is None
        assert kwargs["workflow"] == workflow
        return {}

    async def stream(**kwargs):
        await chat(**kwargs)
        yield {"type": "done"}

    monkeypatch.setattr(server_module, "run_workflow_chat", chat)
    monkeypatch.setattr(server_module, "stream_workflow_chat", stream)
    assert _request(tmp_path, "POST", endpoint, body={"workflow": workflow}).status == 200


def test_steering_http_receipts_conflict_stop_and_capacity(monkeypatch, tmp_path):
    server = _fake_server(tmp_path)
    server._expensive_slots = threading.BoundedSemaphore(1)
    assert server._expensive_slots.acquire(False)
    monkeypatch.setattr("tests.unit.test_ui_server._fake_server", lambda *a, **kw: server)
    server.chat_steering.begin("conversation", "turn", "claude_code", "custom")
    body = {
        "conversationId": "conversation",
        "turnId": "turn",
        "requestId": "request",
        "text": "redirect",
    }
    accepted = _request(tmp_path, "POST", "/api/chat/steer", body=body)
    assert accepted.status == 200
    assert cast(dict[str, Any], accepted.json())["receipt"]["status"] == "interrupting"
    assert _request(tmp_path, "POST", "/api/chat/steer", body=body).json() == accepted.json()
    conflict = _request(tmp_path, "POST", "/api/chat/steer", body={**body, "text": "different"})
    assert conflict.status == 409
    unauthenticated = _request(tmp_path, "POST", "/api/chat/steer", body=body, authenticated=False)
    assert unauthenticated.status == 401
    stopped = _request(tmp_path, "POST", "/api/chat/stop", body=body)
    assert stopped.status == 200
    assert (
        _request(tmp_path, "POST", "/api/chat/steer", body={**body, "requestId": "later"}).status
        == 409
    )
    receipts = _request(tmp_path, "GET", "/api/chat/steering?conversationId=conversation")
    assert cast(dict[str, Any], receipts.json())["receipts"][0]["status"] == "cancelled"


def test_identified_chat_stream_reports_turn_and_generation(monkeypatch, tmp_path):
    async def stream(**kwargs):
        assert kwargs["provider"] == "claude_code"
        yield {"type": "final", "message": {"body": "reply"}}

    monkeypatch.setattr(server_module, "stream_workflow_chat", stream)
    result = _request(
        tmp_path,
        "POST",
        "/api/chat/stream",
        body={
            "conversationId": "conversation",
            "turnId": "turn",
            "provider": "claude_code",
            "model": "custom",
        },
    )
    events = [json.loads(line) for line in result.text().splitlines()]
    assert events[0] == {
        "sequence": 1,
        "type": "turn",
        "conversationId": "conversation",
        "turnId": "turn",
        "generation": 0,
        "provider": "claude_code",
        "model": "custom",
    }
    assert events[-1]["type"] == "final"
    assert events[-1]["generation"] == 0


def test_steering_http_persistence_failure_and_recovery(monkeypatch, tmp_path):
    server = _fake_server(tmp_path)
    monkeypatch.setattr("tests.unit.test_ui_server._fake_server", lambda *a, **kw: server)
    turn = server.chat_steering.begin("conversation", "turn", "codex", "cli-default")
    body = {
        "conversationId": "conversation",
        "turnId": "turn",
        "requestId": "request",
        "text": "keep this instruction",
    }
    save = server.chat_steering._save

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(server.chat_steering, "_save", fail)
    assert _request(tmp_path, "POST", "/api/chat/steer", body=body).status == 500
    assert not turn.cancel.is_set()
    monkeypatch.setattr(server.chat_steering, "_save", save)
    assert _request(tmp_path, "POST", "/api/chat/steer", body=body).status == 200
    monkeypatch.setattr(server.chat_steering, "_save", fail)
    assert _request(tmp_path, "POST", "/api/chat/stop", body=body).status == 500
    assert turn.cancel.is_set()
    assert turn.stopped
    assert _request(tmp_path, "GET", "/api/chat/steering?conversationId=conversation").status == 500
    monkeypatch.setattr(server.chat_steering, "_save", save)
    recovered = _request(tmp_path, "GET", "/api/chat/steering?conversationId=conversation")
    receipt = cast(dict[str, Any], recovered.json())["receipts"][0]
    assert receipt["text"] == body["text"]
    assert receipt["status"] == "cancelled"
    assert ChatSteering(tmp_path).receipts("conversation") == [receipt]
    assert _request(tmp_path, "POST", "/api/chat/steer", body=body).json() == {"receipt": receipt}


def test_identified_stream_disconnect_preserves_provider_and_replays(monkeypatch, tmp_path):
    import threading

    server = _fake_server(tmp_path)
    monkeypatch.setattr("tests.unit.test_ui_server._fake_server", lambda *a, **kw: server)
    disconnected = threading.Event()
    original_write = GoferUiRequestHandler._write_stream_event

    def disconnect(self, event):
        if event["type"] == "thought":
            disconnected.set()
            raise BrokenPipeError("disconnected")
        original_write(self, event)

    async def stream(**kwargs):
        yield {"type": "thought", "text": "partial"}
        assert disconnected.wait(3)
        assert not kwargs["cancel_event"].is_set()
        yield {"type": "final", "message": {"body": "finished after disconnect"}}

    monkeypatch.setattr(GoferUiRequestHandler, "_write_stream_event", disconnect)
    monkeypatch.setattr(server_module, "stream_workflow_chat", stream)
    _request(
        tmp_path,
        "POST",
        "/api/chat/stream",
        body={"conversationId": "conversation", "turnId": "turn"},
    )
    # ChatSteering closes a generator once its final event has arrived.
    monkeypatch.setattr(GoferUiRequestHandler, "_write_stream_event", original_write)
    replay = _request(
        tmp_path, "GET", "/api/chat/events?conversationId=conversation&turnId=turn&after=2"
    )
    events = [json.loads(line) for line in replay.text().splitlines()]
    assert len(events) == 1
    assert events[0]["message"]["body"] == "finished after disconnect"
    assert events[0]["sequence"] == 3


def test_provider_settings_persist_and_reject_invalid_updates(monkeypatch, tmp_path) -> None:
    from gofer.core import provider_preferences

    monkeypatch.setattr(provider_preferences, "get_data_dir", lambda: tmp_path)
    result = _request(
        tmp_path, "POST", "/api/provider/settings", body={"provider": "codex", "enabled": False}
    )
    assert result.status == 200
    assert provider_preferences.provider_preference("codex") == {"enabled": False}
    invalid = _request(
        tmp_path,
        "POST",
        "/api/provider/settings",
        body={"provider": "codex", "executable": "/missing"},
    )
    assert invalid.status == 400
    assert provider_preferences.provider_preference("codex") == {"enabled": False}
    denied = _request(
        tmp_path,
        "POST",
        "/api/provider/settings",
        body={"provider": "codex", "enabled": True},
        authenticated=False,
    )
    assert denied.status == 401


def test_provider_model_defaults_round_trip(monkeypatch, tmp_path) -> None:
    from gofer.core import provider_preferences

    monkeypatch.setattr(provider_preferences, "get_data_dir", lambda: tmp_path)
    for model, effort in [("astra", "high"), ("", "")]:
        changes = {"defaultModel": model, "defaultEffort": effort}
        result = _request(
            tmp_path,
            "POST",
            "/api/provider/settings",
            body={"provider": "codex", **changes},
        )
        assert result.status == 200
        assert provider_preferences.provider_preference("codex") == changes
    invalid = _request(
        tmp_path,
        "POST",
        "/api/provider/settings",
        body={"provider": "codex", "defaultModel": ["astra"]},
    )
    assert invalid.status == 400
    assert provider_preferences.provider_preference("codex")["defaultModel"] == ""


def test_provider_auth_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def start(self: object, provider: str) -> dict[str, str]:
        calls.append(provider)
        return {"status": "pending"}

    monkeypatch.setattr(server_module.ProviderAuthSessions, "start", start)
    result = _request(tmp_path, "POST", "/api/provider/auth", body={"provider": "cursor"})
    assert result.status == 200
    assert result.json() == {"status": "pending"}
    assert calls == ["cursor"]
    status = _request(tmp_path, "GET", "/api/provider/auth?provider=cursor")
    assert status.json() == {"status": "idle"}
    cancel = _request(
        tmp_path,
        "POST",
        "/api/provider/auth",
        body={"provider": "cursor", "action": "cancel"},
    )
    assert cancel.json() == {"status": "idle"}
    invalid = _request(
        tmp_path,
        "POST",
        "/api/provider/auth",
        body={"provider": "cursor", "action": "execute"},
    )
    assert invalid.status == 400


def test_provider_auth_start_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: object, provider: str) -> dict[str, str]:
        raise OSError("Could not launch login")

    monkeypatch.setattr(server_module.ProviderAuthSessions, "start", fail)
    result = _request(tmp_path, "POST", "/api/provider/auth", body={"provider": "cursor"})
    assert result.status == 400
    assert result.json() == {"error": "Could not launch login"}


def test_discovery_checkpoint_detects_disconnect_without_consuming_request() -> None:
    import socket

    from gofer.ui.api import DiscoveryCancelled

    server_socket, client_socket = socket.socketpair()
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.connection = server_socket
    try:
        handler._check_discovery_cancelled()
        client_socket.sendall(b"x")
        handler._check_discovery_cancelled()
        assert server_socket.recv(1) == b"x"
        client_socket.close()
        with pytest.raises(DiscoveryCancelled):
            handler._check_discovery_cancelled()
    finally:
        server_socket.close()
        client_socket.close()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_discovery_cancellation_sends_no_error_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    from gofer.ui.api import DiscoveryCancelled

    handler = _fake_handler(_fake_server(tmp_path))
    handler.path = "/api/workflows" if method == "GET" else "/api/projects/open"
    monkeypatch.setattr(handler, "_request_data_dir", lambda query: tmp_path)
    monkeypatch.setattr(handler, "_read_json", lambda **_: {"projectRoot": str(tmp_path)})
    monkeypatch.setattr(handler, "_assert_bundle_path_allowed", lambda *args, **kwargs: tmp_path)

    def cancelled(*args: Any, **kwargs: Any) -> Any:
        raise DiscoveryCancelled()

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Cancelled discovery must not send an error")

    monkeypatch.setattr(server_module, "list_workflow_payloads", cancelled)
    monkeypatch.setattr(server_module, "open_project_payload", cancelled)
    monkeypatch.setattr(handler, "_send_json", unexpected)
    # Both are answered from the route table.
    handler.headers = Message()
    assert handler._answer_route(method, urlparse(handler.path))


def test_swarm_external_workspace_requires_its_own_current_grant(tmp_path: Path) -> None:
    from gofer.ui.api import WorkflowBundleError

    server = _fake_server(tmp_path)
    external = tmp_path.parent / (tmp_path.name + "-mobile")
    external.mkdir()
    with pytest.raises(WorkflowBundleError, match="approved"):
        server._authorize_swarm_workspace(external, None)
    default_grant = server.path_grants.register(tmp_path)
    with pytest.raises(WorkflowBundleError, match="approved"):
        server._authorize_swarm_workspace(external, default_grant)
    grant = server.path_grants.register(external)
    server._authorize_swarm_workspace(external, grant)
    server.path_grants._grants.clear()
    with pytest.raises(WorkflowBundleError, match="approved"):
        server._authorize_swarm_workspace(external, grant)


def test_rem_threads_reject_ungranted_project_before_starting(tmp_path: Path, monkeypatch) -> None:
    async def unexpected(**kwargs):
        raise AssertionError("Provider should not start")
        yield {}

    monkeypatch.setattr(server_module, "stream_workflow_chat", unexpected)
    response = _request(
        tmp_path,
        "POST",
        "/api/chat/stream",
        body={
            "workflow": {
                "remThreads": {
                    "global": True,
                    "projects": [
                        {"root": str(tmp_path.parent), "name": "outside"},
                    ],
                }
            },
        },
    )
    assert response.status == 400
    assert "approved" in response.text()


def test_global_rem_scope_handoff_through_local_mcp(tmp_path: Path, monkeypatch) -> None:
    import urllib.request

    project = tmp_path / "mobile"
    project.mkdir()
    calls = []
    tool_urls = []

    async def fake_provider(**kwargs):
        workflow = kwargs["workflow"]
        calls.append((workflow.get("projectRoot"), kwargs.get("permission_mode")))
        tool = next(
            item for item in workflow["remResources"]["mcpServers"] if item["name"] == "rem_threads"
        )
        tool_urls.append(tool["url"])
        if len(calls) == 1:
            request = urllib.request.Request(
                tool["url"],
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "select_project",
                            "arguments": {"projectRoot": str(project)},
                        },
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                result = json.load(response)
            assert not result["result"].get("isError")
        yield {"type": "final", "message": {"body": "Done"}}

    monkeypatch.setattr(server_module, "stream_workflow_chat", fake_provider)
    result = _request(
        tmp_path,
        "POST",
        "/api/chat/stream",
        body={
            "provider": "codex",
            "permissionMode": "workspace-write",
            "messages": [{"role": "user", "body": "Fix mobile"}],
            "workflow": {
                "remThreads": {
                    "global": True,
                    "projects": [
                        {"root": str(project), "name": "Mobile"},
                    ],
                }
            },
        },
    )
    assert result.status == 200
    events = [json.loads(line) for line in result.text().splitlines() if line]
    assert [event["type"] for event in events] == ["project-scope", "final"]
    assert calls == [(None, "workspace-write"), (str(project), "workspace-write")]
    assert len(set(tool_urls)) == 2


def test_fork_attachment_endpoint_copies_files(tmp_path: Path) -> None:
    from gofer.ui.chat_media import resolve_chat_attachment, store_chat_attachments

    attachments = store_chat_attachments(
        {
            "threadId": "source",
            "files": [
                {
                    "name": "context.txt",
                    "type": "text/plain",
                    "data": base64.b64encode(b"context").decode(),
                }
            ],
        },
        tmp_path,
    )["attachments"]
    result = _request(
        tmp_path,
        "POST",
        "/api/chat/attachments/copy",
        body={
            "sourceThreadId": "source",
            "threadId": "fork",
            "attachments": attachments,
        },
    )
    assert result.status == 201
    assert (
        resolve_chat_attachment(attachments[0], data_dir=tmp_path, thread_id="fork").read_bytes()
        == b"context"
    )
    missing = _request(
        tmp_path,
        "POST",
        "/api/chat/attachments/copy",
        body={
            "sourceThreadId": "missing",
            "threadId": "other",
            "attachments": attachments,
        },
    )
    assert missing.status == 400


def test_commit_settings_routes_persist_validate_and_require_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core.commit_message_format import DEFAULT_COMMIT_MESSAGE_TEMPLATE

    endpoint = "/api/provider/commit-settings"
    selection = {"provider": "codex", "model": "sol"}
    defaults = {
        "autoCommit": False,
        "stripCoAuthors": False,
        "template": DEFAULT_COMMIT_MESSAGE_TEMPLATE,
        "defaultTemplate": DEFAULT_COMMIT_MESSAGE_TEMPLATE,
        "stripCoAuthorsStatus": {"agentCommitsChecked": True, "helperReady": False},
    }
    monkeypatch.setattr(
        "gofer.core.commit_attribution.config_hooks_supported", lambda git="git": True
    )
    assert _request(tmp_path, "GET", endpoint).json() == defaults
    assert _request(tmp_path, "POST", endpoint, body=selection).status == 200
    assert _request(tmp_path, "GET", endpoint).json() == {**defaults, **selection}
    assert _request(tmp_path, "POST", endpoint, body={"provider": "codex"}).status == 400
    assert _request(tmp_path, "POST", endpoint, body=selection, authenticated=False).status == 401
    assert _request(tmp_path, "GET", endpoint, authenticated=False).status == 401
    assert _request(tmp_path, "GET", endpoint).json() == {**defaults, **selection}
    template = "Summary: changes\n\n* One change"
    assert _request(tmp_path, "POST", endpoint, body={"template": template}).json() == {
        "saved": True,
        "autoCommit": False,
        "stripCoAuthors": False,
        **selection,
        "template": template,
    }
    expected = {**defaults, **selection, "template": template}
    assert _request(tmp_path, "GET", endpoint).json() == expected
    assert _request(tmp_path, "POST", endpoint, body={"template": 5}).status == 400
    assert _request(tmp_path, "GET", endpoint).json() == expected
    assert _request(tmp_path, "POST", endpoint, body={"template": ""}).status == 200
    assert _request(tmp_path, "GET", endpoint).json() == {**defaults, **selection}


def test_commit_message_clean_route_follows_the_setting(tmp_path: Path) -> None:
    clean = "/api/provider/commit-message/clean"
    message = "Fix it\n\nBody\n\nCo-authored-by: A <a@example.com>\n"
    assert _request(tmp_path, "POST", clean, body={"message": message}).json() == {
        "message": message
    }
    saved = {"stripCoAuthors": True}
    assert _request(tmp_path, "POST", "/api/provider/commit-settings", body=saved).status == 200
    settings = cast(
        dict[str, Any], _request(tmp_path, "GET", "/api/provider/commit-settings").json()
    )
    assert settings["stripCoAuthors"] is True
    assert _request(tmp_path, "POST", clean, body={"message": message}).json() == {
        "message": "Fix it\n\nBody\n"
    }
    assert _request(tmp_path, "POST", clean, body={"message": 5}).status == 400
    assert _request(tmp_path, "POST", clean, body={"message": "x" * 72001}).status == 400
    assert (
        _request(tmp_path, "POST", clean, body={"message": message}, authenticated=False).status
        == 401
    )


def test_usage_overview_and_period_validation(tmp_path: Path) -> None:
    result = _request(tmp_path, "GET", "/api/usage/overview?days=7")
    assert result.status == 200
    payload = cast(dict[str, Any], result.json())
    assert payload["scope"] == "device"
    assert payload["activity"]["total_tokens"] is None
    assert len(payload["accounts"]) == 9
    assert _request(tmp_path, "GET", "/api/usage/overview?days=0").status == 400
    assert _request(tmp_path, "GET", "/api/usage/overview", authenticated=False).status == 401
    # The older 90-day period is 3mo; longer and custom periods use range.
    legacy = cast(dict[str, Any], _request(tmp_path, "GET", "/api/usage/overview?days=90").json())
    assert legacy["range"]["key"] == "3mo" and legacy["days"] == 90
    for key in ("6mo", "1y", "5y", "all"):
        answer = _request(tmp_path, "GET", f"/api/usage/overview?range={key}&tz=UTC")
        assert answer.status == 200
        assert cast(dict[str, Any], answer.json())["range"]["key"] == key
    custom = _request(
        tmp_path, "GET", "/api/usage/overview?range=custom&start=2026-01-01&end=2026-01-31&tz=UTC"
    )
    assert cast(dict[str, Any], custom.json())["range"]["start_date"] == "2026-01-01"
    backwards = _request(
        tmp_path, "GET", "/api/usage/overview?range=custom&start=2026-02-01&end=2026-01-01"
    )
    assert backwards.status == 400
    assert "on or before" in cast(dict[str, Any], backwards.json())["error"]
    assert _request(tmp_path, "GET", "/api/usage/overview?range=2y").status == 400


def test_plan_price_route_is_gone(tmp_path: Path) -> None:
    # The Saved vs API form and its route were removed; an old plans file stays untouched.
    old_plans = tmp_path / "usage-plans.json"
    old_plans.write_text('{"version": 1, "plans": {"claude_code": {"monthly_usd": 100}}}')
    before = old_plans.read_bytes()
    result = _request(tmp_path, "POST", "/api/usage/plans", body={"plans": {"claude_code": 5}})
    assert result.status == 404
    assert old_plans.read_bytes() == before


def test_usage_refresh_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def refresh(
        data_dir: Path | None, *, query: dict[str, Any], explicit: bool
    ) -> dict[str, object]:
        assert data_dir == tmp_path
        # A user's refresh may retry ordinary failures, but never a provider 429.
        assert explicit is True
        return {"query": query, "accounts": []}

    monkeypatch.setattr(server_module, "refresh_usage", refresh)
    result = _request(tmp_path, "POST", "/api/usage/refresh?days=90", body={})
    assert result.status == 200
    assert cast(dict[str, Any], result.json())["query"]["days"] == "90"
    result = _request(
        tmp_path, "POST", "/api/usage/refresh?range=custom&start=2026-01-01&end=2026-01-02", body={}
    )
    assert cast(dict[str, Any], result.json())["query"] == {
        "period": "custom",
        "days": None,
        "start": "2026-01-01",
        "end": "2026-01-02",
        "tz": None,
    }


def test_report_theme_generation_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def generate(body: dict[str, Any]) -> dict[str, str]:
        assert body == {"description": "Ocean", "provider": "codex", "effort": "high"}
        return {"label": "Ocean", "instructions": "Navy ink", "html": "<html></html>"}

    monkeypatch.setattr("gofer.ui.report_theme_generation.generate_report_theme", generate)
    monkeypatch.setattr(server_module, "UI_JOB_DEADLINE_SECONDS", 0)
    result = _request(
        tmp_path,
        "POST",
        "/api/report-themes/generate",
        body={
            "description": "Ocean",
            "provider": "codex",
            "effort": "high",
        },
    )
    assert result.status == 200
    assert cast(dict[str, Any], result.json())["label"] == "Ocean"
    assert (
        _request(tmp_path, "POST", "/api/report-themes/generate", authenticated=False).status == 401
    )


@pytest.mark.parametrize("fail", [False, True])
def test_report_theme_stream_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    async def generate(body: Any, emit: Any, cancel_event: Any) -> dict[str, str]:
        assert not cancel_event.is_set()
        emit({"type": "progress", "text": "Choosing colors"})
        if fail:
            raise ValueError("Provider unavailable")
        return {"label": "Ocean", "instructions": "Navy", "html": "<html></html>"}

    monkeypatch.setattr("gofer.ui.report_theme_generation.generate_report_theme", generate)
    monkeypatch.setattr(server_module, "UI_JOB_DEADLINE_SECONDS", 0)
    result = _request(
        tmp_path,
        "POST",
        "/api/report-themes/generate",
        headers={"Accept": "application/x-ndjson"},
        body={"description": "Ocean"},
    )
    assert result.status == 200
    events = [json.loads(line) for line in result.body.splitlines()]
    assert events[0] == {"type": "progress", "text": "Choosing colors"}
    assert events[-1]["type"] == ("error" if fail else "final")


@pytest.mark.asyncio
async def test_theme_disconnect_cancels_quiet_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    cancelled = []

    async def generate(body: Any, emit: Any, cancel_event: Any) -> dict[str, str]:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(cancel_event.is_set())
        return {}

    def disconnected() -> None:
        raise server_module.DiscoveryCancelled()

    monkeypatch.setattr("gofer.ui.report_theme_generation.generate_report_theme", generate)
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    monkeypatch.setattr(handler, "_check_discovery_cancelled", disconnected)
    monkeypatch.setattr(
        handler, "_write_stream_event", lambda event: pytest.fail("Unexpected event")
    )
    await handler._stream_report_theme({"description": "Ocean"})
    assert cancelled == [True]


def test_generation_job_routes_require_authentication_and_project_grants(tmp_path: Path) -> None:
    assert _request(tmp_path, "GET", "/api/generation-jobs", authenticated=False).status == 401
    assert _request(tmp_path, "POST", "/api/generation-jobs", authenticated=False).status == 401
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    result = _request(
        tmp_path,
        "POST",
        "/api/generation-jobs",
        body={
            "kind": "commit",
            "projectRoot": str(outside),
        },
    )
    assert result.status == 400
    assert "outside the approved" in result.text()
    result = _request(tmp_path, "GET", f"/api/generation-jobs?kind=commit&projectRoot={outside}")
    assert result.status == 400
    outside.rmdir()
    result = _request(tmp_path, "GET", f"/api/generation-jobs?kind=commit&projectRoot={outside}")
    assert result.status == 400
    assert "outside the approved" in result.text()


@pytest.mark.parametrize("replaced_by_file", [False, True])
def test_commit_job_polling_ignores_worktree_removed_after_grant_renewal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replaced_by_file: bool,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-worktree"
    outside.mkdir()
    server = _fake_server(tmp_path)
    grant_id = server.path_grants.register(outside)
    server.generation_jobs.jobs["old-draft"] = {
        "id": "old-draft",
        "kind": "commit",
        "projectRoot": str(outside),
        "branch": "main",
        "createdAt": 1,
        "status": "failed",
        "error": "Old failure",
    }
    monkeypatch.setattr(f"{__name__}._fake_server", lambda *args, **kwargs: server)
    query = f"kind=commit&projectRoot={quote(str(outside))}&branch=main&grantId={grant_id}"
    before = _request(tmp_path, "GET", f"/api/generation-jobs?{query}")
    assert before.status == 200
    assert cast(dict[str, Any], before.json())["jobs"][0]["id"] == "old-draft"
    outside.rmdir()
    if replaced_by_file:
        outside.write_text("No longer a worktree")
    after = _request(tmp_path, "GET", f"/api/generation-jobs?{query}")
    assert after.status == 200
    assert after.json() == {"jobs": []}
    if not replaced_by_file:
        start = _request(
            tmp_path,
            "POST",
            "/api/generation-jobs",
            body={
                "kind": "commit",
                "projectRoot": str(outside),
                "grantId": grant_id,
            },
        )
        assert start.status == 400
        assert "does not exist" in start.text()


def test_generation_job_post_returns_receipt_without_waiting_for_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gofer.ui import generation_jobs

    release = threading.Event()

    async def theme(body: dict[str, Any], emit: Any, **kwargs: Any) -> dict[str, str]:
        assert release.wait(5)
        return {"label": "Ocean", "instructions": "Navy", "html": "<html>Preview</html>"}

    monkeypatch.setattr(generation_jobs, "generate_report_theme", theme)
    try:
        result = _request(
            tmp_path,
            "POST",
            "/api/generation-jobs",
            body={
                "kind": "theme",
                "description": "An ocean journal",
            },
        )
        assert result.status == 202
        payload = cast(dict[str, Any], result.json())
        assert payload["status"] in {"queued", "running"}
        assert payload["description"] == "An ocean journal"
        assert "result" not in payload
    finally:
        release.set()
    # Do not leave a fake provider writing into pytest's temporary directory.
    import time

    until = time.monotonic() + 5
    while result.server.generation_jobs.list("theme")[0]["status"] in {"queued", "running"}:
        assert time.monotonic() < until
        time.sleep(0.01)
    restored = _request(tmp_path, "GET", "/api/generation-jobs?kind=theme")
    assert restored.status == 200
    assert "<html>Preview</html>" in restored.text()
    dismissed = _request(
        tmp_path, "POST", "/api/generation-jobs/dismiss", body={"id": payload["id"]}
    )
    assert dismissed.status == 200
    empty = _request(tmp_path, "GET", "/api/generation-jobs?kind=theme")
    assert cast(dict[str, Any], empty.json())["jobs"] == []


def _trigger_workflow(tmp_path: Path) -> str:
    project = tmp_path / "trigger-project"
    project.mkdir()
    registered = create_registered_workflow(project, name="Trigger API", registry_dir=tmp_path)
    return registered.workflow_id


def test_rattish_trigger_routes_require_authentication(tmp_path: Path) -> None:
    result = _request(tmp_path, "GET", "/api/rattish/triggers?workflowId=x", authenticated=False)
    assert result.status == 401


def test_rattish_trigger_api_creates_lists_and_removes(tmp_path: Path) -> None:
    workflow_id = _trigger_workflow(tmp_path)

    created = _request(
        tmp_path,
        "POST",
        "/api/rattish/triggers",
        body={
            "workflowId": workflow_id,
            "kind": "cron",
            "config": {"expression": "0 9 * * 1-5", "timezone": "UTC"},
            "overlap": "queue",
        },
    )
    assert created.status == 201, created.text()
    trigger = json.loads(created.body)["trigger"]
    assert trigger["overlap"] == "queue"
    assert trigger["nextRunAt"]

    listed = _request(tmp_path, "GET", f"/api/rattish/triggers?workflowId={workflow_id}")
    assert listed.status == 200
    payload = json.loads(listed.body)
    assert [item["id"] for item in payload["triggers"]] == [trigger["id"]]
    assert payload["scheduler"]["running"] is False
    assert "only while Raticode" in payload["scheduler"]["notice"]
    assert payload["login"]["enabled"] is False

    switched = _request(
        tmp_path, "POST", f"/api/rattish/triggers/{trigger['id']}", body={"enabled": False}
    )
    assert json.loads(switched.body)["trigger"]["enabled"] is False

    removed = _request(tmp_path, "DELETE", f"/api/rattish/triggers/{trigger['id']}")
    assert removed.status == 200
    missing = _request(tmp_path, "DELETE", f"/api/rattish/triggers/{trigger['id']}")
    assert missing.status == 404


def test_rattish_trigger_api_returns_webhook_token_once(tmp_path: Path) -> None:
    workflow_id = _trigger_workflow(tmp_path)
    created = _request(
        tmp_path,
        "POST",
        "/api/rattish/triggers",
        body={"workflowId": workflow_id, "kind": "webhook"},
    )
    assert created.status == 201, created.text()
    body = json.loads(created.body)
    assert body["token"].startswith("rtw_")
    assert body["trigger"]["webhookUrl"].startswith("http://127.0.0.1:")

    listed = _request(tmp_path, "GET", f"/api/rattish/triggers?workflowId={workflow_id}")
    assert body["token"] not in listed.text()

    rotated = _request(
        tmp_path, "POST", f"/api/rattish/triggers/{body['trigger']['id']}/rotate-token", body={}
    )
    assert json.loads(rotated.body)["token"] != body["token"]


def test_rattish_trigger_api_reports_bad_settings(tmp_path: Path) -> None:
    workflow_id = _trigger_workflow(tmp_path)
    bad_cron = _request(
        tmp_path,
        "POST",
        "/api/rattish/triggers",
        body={"workflowId": workflow_id, "kind": "cron", "config": {"expression": "nope"}},
    )
    assert bad_cron.status == 400
    assert "five fields" in bad_cron.text()

    unknown = _request(tmp_path, "GET", "/api/rattish/triggers?workflowId=missing")
    assert unknown.status == 400
    preview = _request(
        tmp_path,
        "POST",
        "/api/rattish/triggers/preview",
        body={"expression": "0 9 * * 1-5", "timezone": "UTC"},
    )
    assert len(json.loads(preview.body)["nextRuns"]) == 3
    assert json.loads(preview.body)["summary"] == "Weekdays at 9:00"


def test_start_at_login_api_writes_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    enabled = _request(tmp_path, "POST", "/api/rattish/triggers/login", body={"enabled": True})
    assert enabled.status == 200, enabled.text()
    entry = Path(json.loads(enabled.body)["login"]["path"])
    assert entry.is_file()
    disabled = _request(tmp_path, "POST", "/api/rattish/triggers/login", body={"enabled": False})
    assert json.loads(disabled.body)["login"]["enabled"] is False
    assert not entry.exists()


def test_deleting_a_workflow_removes_its_triggers(tmp_path: Path) -> None:
    workflow_id = _trigger_workflow(tmp_path)
    created = _request(
        tmp_path,
        "POST",
        "/api/rattish/triggers",
        body={"workflowId": workflow_id, "kind": "webhook"},
    )
    assert created.status == 201
    api_module.delete_workflow_payload(workflow_id, tmp_path)
    assert TriggerStore(tmp_path).list_triggers() == []


def test_a_triggered_run_can_be_stopped_from_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = _trigger_workflow(tmp_path)
    source = find_registered_workflow(workflow_id, registry_dir=tmp_path).entrypoint
    cancel = threading.Event()
    seen: list[threading.Event] = []

    def fake_run(run: TriggerRun) -> Any:
        key = (str(tmp_path.resolve()), workflow_id, run.run_id)
        seen.append(api_module._active_rattish_runs[key])
        return object()

    monkeypatch.setattr(api_module, "run_in_process", fake_run)
    api_module.run_triggered_rattish(
        TriggerRun(
            source=source,
            workflow_id=workflow_id,
            inputs={},
            events=[],
            data_dir=tmp_path.resolve(),
            run_id="run-1",
            cancel_event=cancel,
        )
    )
    assert seen == [cancel]
    assert not any(key[2] == "run-1" for key in api_module._active_rattish_runs)
