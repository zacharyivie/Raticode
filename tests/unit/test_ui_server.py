from __future__ import annotations

import base64
import json
import socket
import threading
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import quote

import pytest

from gofer.core.resources import DEFAULT_RESOURCE_LIMITS, ResourceLimits
from gofer.core.scheduler import WorkflowScheduler
from gofer.core.watcher import WorkflowWatcher
from gofer.rattish.workspaces import create_registered_workflow
from gofer.ui import server as server_module
from gofer.ui.chat import workflow_chat_prompt_path
from gofer.ui.server import (
    DesktopPathGrantStore,
    GoferUiRequestHandler,
    GoferUiServer,
    WorkflowBundleError,
    create_server,
    ready_payload,
    render_usage_summary_html,
    render_workflow_usage_html,
    sync_workflow_schedules,
    sync_workflow_watchers,
    ui_session_payload,
    workflow_usage_page_payload,
)
from gofer.utils.run_state import workflow_run_stop_path


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
    server.resource_limits = resource_limits or DEFAULT_RESOURCE_LIMITS
    server.api_token = "test-ui-token"
    server.allowed_origins = {"http://127.0.0.1:5173"}
    server.path_grant_secret = "test-secret"
    server.path_grants = DesktopPathGrantStore()
    server.trusted_project_roots = tuple(
        sorted(
            {
                str(workflow.project_root.resolve())
                for workflow in server_module.list_registered_workflows(registry_dir=tmp_path)
            }
        )
    )
    server.gofer_cli_path = None
    server.server_address = ("127.0.0.1", 8765)
    server.sync_calls = 0
    server.continuous_sync_calls = 0

    def sync_schedules() -> None:
        server.sync_calls += 1

    def ensure_continuous_runs() -> None:
        server.continuous_sync_calls += 1

    setattr(server, "sync_schedules", sync_schedules)
    setattr(server, "ensure_continuous_runs", ensure_continuous_runs)
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


def test_ui_server_syncs_workflow_schedules(tmp_path) -> None:
    workflow_path = tmp_path / "scheduled.toml"
    workflow_path.write_text(
        """
[workflow]
id = "scheduled"
name = "Scheduled"

[workflow.schedule]
cron_expression = "43 * * * *"
timezone = "America/New_York"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip()
    )

    scheduler = WorkflowScheduler(db_path=tmp_path / "schedules.db")

    sync_workflow_schedules(tmp_path, scheduler)
    assert [job["id"] for job in scheduler.list_workflows()] == ["scheduled"]

    workflow_path.write_text(
        """
[workflow]
id = "scheduled"
name = "Scheduled"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip()
    )
    sync_workflow_schedules(tmp_path, scheduler)
    assert scheduler.list_workflows() == []


def test_ui_server_sync_skips_invalid_schedule_timezone(tmp_path) -> None:
    (tmp_path / "invalid-timezone.toml").write_text(
        """
[workflow]
id = "invalid-timezone"
name = "Invalid Timezone"

[workflow.schedule]
cron_expression = "43 * * * *"
timezone = "ETC"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip()
    )
    scheduler = WorkflowScheduler(db_path=tmp_path / "schedules.db")

    sync_workflow_schedules(tmp_path, scheduler)

    assert scheduler.list_workflows() == []


def test_ui_server_sync_skips_schedule_when_run_continuously(tmp_path) -> None:
    (tmp_path / "continuous.toml").write_text(
        """
[workflow]
id = "continuous"
name = "Continuous"
run_continuously = true

[workflow.schedule]
cron_expression = "43 * * * *"
timezone = "America/New_York"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip()
    )
    scheduler = WorkflowScheduler(db_path=tmp_path / "schedules.db")

    sync_workflow_schedules(tmp_path, scheduler)

    assert scheduler.list_workflows() == []


def test_ui_server_syncs_workflow_watchers(tmp_path) -> None:
    workflow_path = tmp_path / "watched.toml"
    workflow_path.write_text(
        """
[workflow]
id = "watched"
name = "Watched"

[workflow.watch]
path = "inputs"
glob = "*.txt"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip()
    )
    watcher = WorkflowWatcher()

    sync_workflow_watchers(tmp_path, watcher)

    assert [item["id"] for item in watcher.list_workflows()] == ["watched"]


def test_ui_server_sync_skips_watcher_when_run_continuously(tmp_path) -> None:
    (tmp_path / "continuous.toml").write_text(
        """
[workflow]
id = "continuous"
name = "Continuous"
run_continuously = true

[workflow.watch]
path = "inputs"
glob = "*.txt"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip()
    )
    watcher = WorkflowWatcher()

    sync_workflow_watchers(tmp_path, watcher)

    assert watcher.list_workflows() == []


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


def test_ui_server_creates_missing_data_dir_before_scheduler_start(tmp_path) -> None:
    if not _sockets_available():
        pytest.skip("local sockets are unavailable in this environment")
    data_dir = tmp_path / "missing" / "gofer"
    server = create_server(host="127.0.0.1", port=0, data_dir=data_dir)

    try:
        server.scheduler.start(paused=True)

        assert data_dir.is_dir()
        assert (data_dir / "schedules.db").exists()
    finally:
        if server.scheduler.is_running():
            server.scheduler.shutdown(wait=False)
        server.server_close()


def test_ui_server_health_check_works_on_dynamic_port(tmp_path) -> None:
    response = _request(tmp_path, "GET", "/api/health")

    assert response.status == 200
    assert response.json() == {"ok": True}
    assert response.header("Content-Type") == "application/json"
    assert response.header("Content-Length") == str(len(response.body))
    assert response.header("Access-Control-Allow-Origin") is None


def test_ui_server_session_payload_requires_existing_bootstrap_token(tmp_path) -> None:
    response = _request(
        tmp_path,
        "GET",
        "/api/session",
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert response.status == 200
    assert response.json() == {
        "apiBaseUrl": "http://127.0.0.1:8765/api",
        "apiToken": "test-ui-token",
    }
    assert response.header("Access-Control-Allow-Origin") == "http://127.0.0.1:5173"


def test_ui_server_session_payload_is_origin_gated(tmp_path) -> None:
    response = _request(
        tmp_path,
        "GET",
        "/api/session",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status == 403
    assert response.json() == {"error": "Origin is not allowed"}


def test_ui_session_payload_uses_bound_server_address(tmp_path) -> None:
    fake_server = _fake_server(tmp_path)
    fake_server.server_address = ("127.0.0.1", 0)

    assert ui_session_payload(fake_server) == {
        "apiBaseUrl": "http://127.0.0.1:0/api",
        "apiToken": "test-ui-token",
    }


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


def test_ui_server_renders_llm_usage_summary() -> None:
    html = render_usage_summary_html(
        {
            "totals": {
                "agent_calls": 2,
                "total_tokens": 42,
                "estimated_cost": 0.125,
                "agent_time_seconds": 3.5,
            },
            "most_expensive_nodes": [
                {
                    "node_id": "expensive",
                    "model": "gpt-test",
                    "total_tokens": 30,
                    "estimated_cost": 0.1,
                    "duration_seconds": 1.0,
                }
            ],
            "slowest_nodes": [
                {
                    "node_id": "slow",
                    "provider": "codex",
                    "total_tokens": 12,
                    "estimated_cost": 0.025,
                    "duration_seconds": 2.5,
                }
            ],
        }
    )

    assert "LLM run usage" in html
    assert "Most expensive nodes" in html
    assert "Slowest nodes" in html
    assert "expensive" in html


def test_ui_server_workflow_usage_page_reads_recent_sidecars(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "logs" / "usage-ui"
    workflow_dir.mkdir(parents=True)
    log_path = workflow_dir / "run.log"
    log_path.write_text("done\n", encoding="utf-8")
    log_path.with_suffix(".outputs.json").write_text(
        json.dumps(
            {
                "workflowId": "usage-ui",
                "runId": "run.log",
                "nodeOutputs": {},
                "usageSummary": {
                    "totals": {
                        "agent_calls": 1,
                        "input_tokens": 4,
                        "output_tokens": 6,
                        "total_tokens": 10,
                        "estimated_cost": 0.01,
                        "agent_time_seconds": 2.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    payload = workflow_usage_page_payload("usage-ui", tmp_path)
    rendered = render_workflow_usage_html(payload)

    assert payload["totals"]["agent_calls"] == 1
    assert payload["totals"]["total_tokens"] == 10
    assert "LLM usage for usage-ui" in rendered
    assert "run.log" in rendered


def test_ui_server_workflow_plan_route_uses_trigger_context_and_base_path(tmp_path) -> None:
    workflow_path = tmp_path / "watched-plan.toml"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    event_file = inputs / "one.txt"
    event_file.write_text("one")
    workflow_path.write_text(
        """
[workflow]
id = "watched-plan"
name = "Watched Plan"

[workflow.watch]
path = "inputs"
glob = "*.txt"
mode = "fanout"

[[nodes]]
id = "trigger"
type = "loop"

[nodes.source]
type = "trigger_events"
include_content = true
""".strip()
    )
    body = json.dumps(
        {"triggerContext": {"events": [{"path": str(event_file), "kind": "modified"}]}}
    ).encode()
    server = GoferUiServer.__new__(GoferUiServer)
    server.data_dir = tmp_path
    server.resource_limits = DEFAULT_RESOURCE_LIMITS
    server.api_token = "test-ui-token"
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.server = server
    server.server_address = ("127.0.0.1", 8765)
    handler.path = "/api/workflows/watched-plan/plan"
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(body))
    handler.headers["Authorization"] = "Bearer test-ui-token"
    handler.headers["Host"] = "127.0.0.1:8765"
    handler.rfile = BytesIO(body)
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

    handler.do_POST()

    handler.wfile.seek(0)
    payload = json.loads(handler.wfile.read().decode("utf-8"))
    assert status["code"] == 200
    plan = payload["plan"]
    assert plan["pathResolutionBase"] == str(tmp_path)
    assert plan["triggerContext"]["watch"]["path"] == str(inputs)
    fan_out = plan["generations"][0]["nodes"][0]["fanOut"]
    assert fan_out["count"] == 1
    assert fan_out["sampleItems"][0]["path"] == str(event_file)
    assert "content" not in fan_out["sampleItems"][0]


def test_ui_server_webhook_trigger_endpoint_runs_workflow(tmp_path: Path) -> None:
    (tmp_path / "hooked.toml").write_text(
        """
[workflow]
id = "hooked"
name = "Hooked"

[workflow.webhooks.default]
enabled = true
allow_unauthenticated = true

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s" "$SOURCE"'

[nodes.inputs]
"env.SOURCE" = "{{trigger.source}}"
""".strip(),
        encoding="utf-8",
    )

    response = _request(
        tmp_path,
        "POST",
        "/api/workflows/hooked/webhooks/default/trigger",
        body={"ok": True},
    )

    payload = cast(dict[str, object], response.json())
    trigger = cast(dict[str, object], payload["trigger"])
    run = cast(dict[str, object], trigger["run"])
    node_outputs = cast(dict[str, object], run["nodeOutputs"])
    echo = cast(dict[str, object], node_outputs["echo"])
    assert response.status == 202
    assert trigger["triggerId"] == "default"
    assert echo["output"] == "http"


def test_ui_server_webhook_replay_requires_token(tmp_path: Path) -> None:
    (tmp_path / "hooked.toml").write_text(
        """
[workflow]
id = "hooked"
name = "Hooked"

[workflow.webhooks.default]
enabled = true
token = "secret-token"

[[nodes]]
id = "echo"
type = "bash_command"
command = 'printf "%s" "$VALUE"'

[nodes.inputs]
"env.VALUE" = "{{trigger.payload.value}}"
""".strip(),
        encoding="utf-8",
    )

    trigger_response = _request(
        tmp_path,
        "POST",
        "/api/workflows/hooked/webhooks/default/trigger",
        body={"value": "original"},
        headers={"X-Gofer-Webhook-Token": "secret-token"},
    )
    trigger_payload = cast(dict[str, object], trigger_response.json())["trigger"]
    run_id = cast(dict[str, object], trigger_payload)["runId"]

    unauthorized = _request(
        tmp_path,
        "POST",
        "/api/workflows/hooked/webhooks/default/replay",
        body={"runId": str(run_id)},
    )

    assert unauthorized.status == 400
    assert "Unauthorized" in unauthorized.text()

    authorized = _request(
        tmp_path,
        "POST",
        "/api/workflows/hooked/webhooks/default/replay",
        body={"runId": str(run_id)},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert authorized.status == 202


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


def test_ui_desktop_path_grant_registration_requires_secret(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / f"{tmp_path.name}-registered"
    outside_dir.mkdir()

    denied = _request(
        tmp_path,
        "POST",
        "/api/desktop/path-grants",
        body={"grantId": "grant-1", "path": str(outside_dir)},
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


def test_ui_server_get_routes_forward_to_api_payloads(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_list(data_dir):
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


def test_ui_server_post_workflow_routes_and_syncs(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_create(name, project_root, *, registry_dir):
        calls["create"] = (name, project_root, registry_dir)
        return {"id": "created"}

    def fake_open_project(project_root, *, registry_dir):
        calls["open_project"] = (project_root, registry_dir)
        return {"projectRoot": str(project_root), "workflows": [{"id": "existing"}]}

    def fake_import(content, data_dir):
        calls["import"] = (content, data_dir)
        return {"id": "imported"}

    def fake_rename(workflow_id, name, data_dir):
        calls["rename"] = (workflow_id, name, data_dir)
        return {"id": "renamed"}

    def fake_duplicate(workflow_id, name, data_dir):
        calls["duplicate"] = (workflow_id, name, data_dir)
        return {"id": "copy"}

    monkeypatch.setattr(server_module, "create_registered_workflow_payload", fake_create)
    monkeypatch.setattr(server_module, "open_project_payload", fake_open_project)
    monkeypatch.setattr(server_module, "import_workflow_payload", fake_import)
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
    imported = _request(tmp_path, "POST", "/api/workflows/import", body={"content": "toml"})
    renamed = _request(tmp_path, "POST", "/api/workflows/wf/rename", body={"name": "Renamed"})
    duplicate = _request(tmp_path, "POST", "/api/workflows/wf/duplicate", body={"name": "Copy"})

    assert create.status == 201
    assert create.json() == {"workflow": {"id": "created"}}
    assert create.server.sync_calls == 1
    assert open_project.status == 200
    assert open_project.json() == {
        "projectRoot": str(project_root),
        "workflows": [{"id": "existing"}],
    }
    assert open_project.server.sync_calls == 1
    assert imported.status == 201
    assert renamed.status == 200
    assert renamed.server.sync_calls == 1
    assert renamed.server.continuous_sync_calls == 1
    assert duplicate.status == 201
    assert calls == {
        "create": ("Created", project_root, tmp_path),
        "open_project": (project_root, tmp_path),
        "import": ("toml", tmp_path),
        "rename": ("wf", "Renamed", tmp_path),
        "duplicate": ("wf", "Copy", tmp_path),
    }


def test_ui_server_update_delete_run_and_stop_routes(monkeypatch, tmp_path) -> None:
    async def fake_run(workflow_id, data_dir, *, dry_run, trigger_context=None):
        return {
            "workflowId": workflow_id,
            "dataDir": str(data_dir),
            "dryRun": dry_run,
            "triggerContext": trigger_context,
        }

    monkeypatch.setattr(
        server_module,
        "update_workflow_payload",
        lambda workflow_id, payload, data_dir: {
            "id": workflow_id,
            "name": payload["name"],
            "dataDir": str(data_dir),
        },
    )
    monkeypatch.setattr(
        server_module,
        "delete_workflow_payload",
        lambda workflow_id, data_dir, **_kwargs: {
            "workflowId": workflow_id,
            "deleted": True,
        },
    )
    monkeypatch.setattr(
        server_module,
        "stop_workflow_run_payload",
        lambda workflow_id, data_dir, run_id=None: {
            "workflowId": workflow_id,
            "runId": run_id,
            "stopped": True,
        },
    )
    monkeypatch.setattr(server_module, "run_workflow_payload", fake_run)

    updated = _request(tmp_path, "PUT", "/api/workflows/wf", body={"name": "Updated"})
    deleted = _request(tmp_path, "DELETE", "/api/workflows/wf")
    stopped = _request(tmp_path, "POST", "/api/workflows/wf/stop")
    stopped_run = _request(tmp_path, "POST", "/api/workflows/wf/runs/run.log/stop")
    dry_run = _request(
        tmp_path,
        "POST",
        "/api/workflows/wf/run",
        body={"dryRun": True, "triggerContext": {"source": "test"}},
    )

    assert updated.status == 200
    assert updated.json() == {"workflow": {"id": "wf", "name": "Updated", "dataDir": str(tmp_path)}}
    assert updated.server.sync_calls == 1
    assert deleted.json() == {"workflowId": "wf", "deleted": True}
    assert deleted.server.sync_calls == 1
    assert stopped.json() == {"workflowId": "wf", "runId": None, "stopped": True}
    assert stopped_run.json() == {"workflowId": "wf", "runId": "run.log", "stopped": True}
    assert dry_run.json() == {
        "plan": {
            "workflowId": "wf",
            "dataDir": str(tmp_path),
            "dryRun": True,
            "triggerContext": {"source": "test"},
        }
    }


def test_ui_server_retention_queue_and_approval_routes(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        server_module,
        "retention_settings_payload",
        lambda data_dir, workflow_id=None: {"workflowId": workflow_id, "settings": {}},
    )
    monkeypatch.setattr(
        server_module,
        "update_retention_settings_payload",
        lambda data_dir, settings, workflow_id=None: {
            "workflowId": workflow_id,
            "settings": settings,
        },
    )
    monkeypatch.setattr(
        server_module,
        "queue_workflow_run_payload",
        lambda workflow_id, data_dir, **kwargs: {"workflowId": workflow_id, **kwargs},
    )
    monkeypatch.setattr(
        server_module,
        "cancel_queued_run_payload",
        lambda run_id, data_dir: {"runId": run_id, "cancelled": True},
    )
    monkeypatch.setattr(
        server_module,
        "list_workflow_approvals_payload",
        lambda workflow_id, data_dir, include_decided: {
            "workflowId": workflow_id,
            "includeDecided": include_decided,
        },
    )
    monkeypatch.setattr(
        server_module,
        "decide_workflow_approval_payload",
        lambda workflow_id, run_id, node_id, decision, data_dir, decided_by, notes: {
            "workflowId": workflow_id,
            "runId": run_id,
            "nodeId": node_id,
            "decision": decision,
            "decidedBy": decided_by,
            "notes": notes,
        },
    )

    assert _request(tmp_path, "GET", "/api/retention").json() == {
        "workflowId": None,
        "settings": {},
    }
    assert _request(tmp_path, "GET", "/api/workflows/wf/retention").json() == {
        "workflowId": "wf",
        "settings": {},
    }
    assert _request(
        tmp_path,
        "POST",
        "/api/workflows/wf/retention",
        body={"keepLast": 5},
    ).json() == {"workflowId": "wf", "settings": {"keepLast": 5}}
    assert _request(
        tmp_path,
        "POST",
        "/api/workflows/wf/queue",
        body={"priority": 7, "trigger": "manual", "parameters": {}, "targetLabels": ["a"]},
    ).json() == {
        "workflowId": "wf",
        "priority": 7,
        "trigger": "manual",
        "parameters": {},
        "target_labels": ["a"],
    }
    assert _request(tmp_path, "POST", "/api/queue/run-1/cancel").json() == {
        "runId": "run-1",
        "cancelled": True,
    }
    assert _request(tmp_path, "GET", "/api/workflows/wf/approvals?all=0").json() == {
        "workflowId": "wf",
        "includeDecided": False,
    }
    assert _request(
        tmp_path,
        "POST",
        "/api/workflows/wf/approvals/run-1/node-1/reject",
        body={"by": "tester", "notes": "no"},
    ).json() == {
        "workflowId": "wf",
        "runId": "run-1",
        "nodeId": "node-1",
        "decision": "rejected",
        "decidedBy": "tester",
        "notes": "no",
    }


def test_ui_server_provider_profile_runner_prune_and_resume_routes(
    monkeypatch,
    tmp_path,
) -> None:
    async def fake_resume(workflow_id, data_dir, **kwargs):
        return {"workflowId": workflow_id, "dataDir": str(data_dir), **kwargs}

    monkeypatch.setattr(
        server_module,
        "provider_profiles_payload",
        lambda data_dir: {"profiles": [{"name": "default"}], "dataDir": str(data_dir)},
    )
    monkeypatch.setattr(
        server_module,
        "upsert_provider_profile_payload",
        lambda payload, data_dir: {"profile": payload, "dataDir": str(data_dir)},
    )
    monkeypatch.setattr(
        server_module,
        "delete_provider_profile_payload",
        lambda profile_name, data_dir: {"name": profile_name, "deleted": True},
    )
    monkeypatch.setattr(
        server_module,
        "runner_queue_payload",
        lambda data_dir: {"runs": [], "dataDir": str(data_dir)},
    )
    monkeypatch.setattr(
        server_module,
        "prune_workflow_run_logs_payload",
        lambda workflow_id, data_dir, **kwargs: {
            "workflowId": workflow_id,
            "dataDir": str(data_dir),
            **kwargs,
        },
    )
    monkeypatch.setattr(server_module, "resume_workflow_payload", fake_resume)

    assert _request(tmp_path, "GET", "/api/provider/profiles").json() == {
        "profiles": [{"name": "default"}],
        "dataDir": str(tmp_path),
    }
    assert _request(
        tmp_path,
        "POST",
        "/api/provider/profiles",
        body={"name": "default", "provider": "codex"},
    ).json() == {
        "profile": {"name": "default", "provider": "codex"},
        "dataDir": str(tmp_path),
    }
    assert _request(tmp_path, "DELETE", "/api/provider/profiles/default").json() == {
        "name": "default",
        "deleted": True,
    }
    assert _request(tmp_path, "GET", "/api/runners").json() == {
        "runs": [],
        "dataDir": str(tmp_path),
    }
    assert _request(
        tmp_path,
        "POST",
        "/api/workflows/wf/logs/prune",
        body={
            "keepLast": "5",
            "keepDays": "10",
            "keepFailedDays": "",
            "dryRun": False,
        },
    ).json() == {
        "workflowId": "wf",
        "dataDir": str(tmp_path),
        "keep_last": 5,
        "keep_days": 10,
        "keep_failed_days": None,
        "dry_run": False,
    }
    assert _request(
        tmp_path,
        "POST",
        "/api/workflows/wf/runs/run.log/resume",
        body={
            "fromNode": "a",
            "skipCache": True,
            "force": True,
            "triggerContext": {"reason": "test"},
        },
    ).json() == {
        "run": {
            "workflowId": "wf",
            "dataDir": str(tmp_path),
            "run_id": "run.log",
            "from_node": "a",
            "only_node": None,
            "skip_cache": True,
            "force": True,
            "trigger_context": {"reason": "test"},
        }
    }


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

    def fake_start(*, data_dir):
        assert data_dir == tmp_path
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


def test_ui_server_chat_stream_provider_error_is_ndjson(monkeypatch, tmp_path) -> None:
    async def bad_stream(**_kwargs):
        raise server_module.ChatProviderError("stream unavailable")
        yield {"type": "unreachable"}

    monkeypatch.setattr(server_module, "stream_workflow_chat", bad_stream)

    response = _request(tmp_path, "POST", "/api/chat/stream", body={"messages": []})

    assert response.status == 200
    assert response.header("Content-Type") == "application/x-ndjson; charset=utf-8"
    assert response.text().splitlines() == ['{"type": "error", "error": "stream unavailable"}']


@pytest.mark.parametrize(
    ("method", "path", "body", "patch_name", "error", "expected_status"),
    [
        (
            "POST",
            "/api/workflows",
            {"name": "Taken", "projectRoot": "."},
            "create_registered_workflow_payload",
            server_module.WorkflowAlreadyExistsError("taken"),
            409,
        ),
        (
            "POST",
            "/api/workflows/import",
            {"content": "bad"},
            "import_workflow_payload",
            server_module.WorkflowCreateError("bad import"),
            400,
        ),
        (
            "PUT",
            "/api/workflows/wf",
            {"id": "wf"},
            "update_workflow_payload",
            server_module.WorkflowUpdateError("bad update"),
            400,
        ),
        (
            "POST",
            "/api/workflows/wf/run",
            {},
            "run_workflow_payload",
            server_module.WorkflowRunError("bad run"),
            400,
        ),
        (
            "GET",
            "/api/workflows/wf/logs/latest",
            None,
            "latest_workflow_log_payload",
            server_module.WorkflowLogError("bad log"),
            400,
        ),
        (
            "POST",
            "/api/chat",
            {"messages": []},
            "run_workflow_chat",
            server_module.ChatProviderError("bad provider"),
            400,
        ),
    ],
)
def test_ui_server_error_status_mappings(
    monkeypatch,
    tmp_path,
    method,
    path,
    body,
    patch_name,
    error,
    expected_status,
) -> None:
    async def async_raise(*_args, **_kwargs):
        raise error

    def sync_raise(*_args, **_kwargs):
        raise error

    replacement = (
        async_raise if patch_name in {"run_workflow_payload", "run_workflow_chat"} else sync_raise
    )
    monkeypatch.setattr(server_module, patch_name, replacement)
    if patch_name == "create_registered_workflow_payload":
        body = {**body, "projectRoot": str(tmp_path)}

    response = _request(tmp_path, method, path, body=body)

    assert response.status == expected_status
    assert response.json() == {"error": str(error)}


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
    assert options.header("Access-Control-Allow-Methods") == "GET, POST, PUT, DELETE, OPTIONS"
    assert "Authorization" in (options.header("Access-Control-Allow-Headers") or "")


def test_ui_server_continuous_monitor_starts_one_thread_and_cleans_inactive(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeThread:
        created: list[FakeThread] = []

        def __init__(self, *args, **kwargs) -> None:
            self.target = kwargs.get("target")
            self.args = kwargs.get("args", ())
            self.name = kwargs.get("name")
            self.daemon = kwargs.get("daemon", False)
            self.alive = False
            self.started = 0
            FakeThread.created.append(self)

        def start(self) -> None:
            self.started += 1
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            self.alive = False

    (tmp_path / "continuous.toml").write_text(
        """
[workflow]
id = "continuous"
name = "Continuous"
run_continuously = true

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "invalid.toml").write_text("not = [valid", encoding="utf-8")
    monkeypatch.setattr(server_module.threading, "Thread", FakeThread)
    server = GoferUiServer.__new__(GoferUiServer)
    server.data_dir = tmp_path
    server.resource_limits = ResourceLimits(max_watcher_concurrency=5)
    server._continuous_runs = {}
    server._continuous_lock = threading.Lock()

    server.ensure_continuous_runs()
    server.ensure_continuous_runs()

    assert list(server._continuous_runs) == ["continuous"]
    assert len(FakeThread.created) == 1
    assert FakeThread.created[0].started == 1

    (tmp_path / "continuous.toml").write_text(
        """
[workflow]
id = "continuous"
name = "Continuous"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip(),
        encoding="utf-8",
    )
    server.ensure_continuous_runs()

    assert server._continuous_runs == {}


def test_ui_server_start_stop_continuous_monitor_uses_single_monitor_thread(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeThread:
        def __init__(self, *args, **kwargs) -> None:
            self.target = kwargs.get("target")
            self.name = kwargs.get("name")
            self.daemon = kwargs.get("daemon", False)
            self.alive = False
            self.started = 0
            self.joined = False

        def start(self) -> None:
            self.started += 1
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            self.joined = True
            self.alive = False

    monkeypatch.setattr(server_module.threading, "Thread", FakeThread)
    server = GoferUiServer.__new__(GoferUiServer)
    server.data_dir = tmp_path
    server.resource_limits = DEFAULT_RESOURCE_LIMITS
    server._continuous_runs = {}
    server._continuous_lock = threading.Lock()
    server._continuous_stop = threading.Event()
    server._continuous_thread = None

    server.start_continuous_monitor()
    first_thread = server._continuous_thread
    server.start_continuous_monitor()
    server.stop_continuous_monitor()

    assert first_thread is server._continuous_thread
    assert first_thread.started == 1
    assert first_thread.joined is True
    assert server._continuous_stop.is_set()


def test_ui_server_serve_shutdown_cleans_up(monkeypatch, tmp_path, capsys) -> None:
    class FakeLifecycle:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object | None]] = []

        def start(self, paused: bool = False) -> None:
            self.calls.append(("start", paused))

        def resume(self) -> None:
            self.calls.append(("resume", None))

        def shutdown(self, wait: bool = True) -> None:
            self.calls.append(("shutdown", wait))

    class FakeServer:
        def __init__(self) -> None:
            self.server_address = ("127.0.0.1", 4321)
            self.data_dir = tmp_path
            self.gofer_cli_path = None
            self.api_token = "ready-token"
            self.emit_ready_token = False
            self.scheduler = FakeLifecycle()
            self.watcher = FakeLifecycle()
            self.calls: list[str] = []

        def sync_schedules(self) -> None:
            self.calls.append("sync_schedules")

        def start_continuous_monitor(self) -> None:
            self.calls.append("start_continuous_monitor")

        def stop_continuous_monitor(self) -> None:
            self.calls.append("stop_continuous_monitor")

        def serve_forever(self) -> None:
            self.calls.append("serve_forever")
            raise KeyboardInterrupt

        def server_close(self) -> None:
            self.calls.append("server_close")

    fake_server = FakeServer()
    monkeypatch.setattr(server_module, "create_server", lambda **_kwargs: fake_server)
    monkeypatch.setattr(server_module, "_install_shutdown_handlers", lambda _server: None)

    server_module.serve(host="127.0.0.1", port=9999, data_dir=tmp_path)

    output = capsys.readouterr()
    assert "GOFER_UI_READY" in output.out
    assert "ready-token" not in output.out
    assert "GOFER_UI_STOPPED" in output.err
    assert fake_server.scheduler.calls == [
        ("start", True),
        ("resume", None),
        ("shutdown", False),
    ]
    assert fake_server.watcher.calls == [("start", False), ("shutdown", False)]
    assert fake_server.calls == [
        "sync_schedules",
        "start_continuous_monitor",
        "serve_forever",
        "stop_continuous_monitor",
        "server_close",
    ]


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


def test_ui_server_stops_specific_running_workflow_log(tmp_path) -> None:
    log_dir = tmp_path / "logs" / "stop-me"
    log_dir.mkdir(parents=True)
    run_id = "2026-06-17T12-00-00-0400.log"
    (log_dir / run_id).write_text(
        "2026-06-17T12:00:00-04:00 - stop-me started successfully\n",
        encoding="utf-8",
    )
    response = _request(tmp_path, "POST", f"/api/workflows/stop-me/runs/{run_id}/stop")

    payload = response.json()
    assert response.status == 200
    assert isinstance(payload, dict)
    assert payload["stopped"] is True
    assert workflow_run_stop_path("stop-me", run_id, tmp_path).exists()


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


def test_commit_message_endpoint_uses_restricted_generator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import AsyncMock

    from gofer.ui import commit_message

    generate = AsyncMock(return_value={"message": "fix: expose resolved edits"})
    monkeypatch.setattr(commit_message, "generate_commit_message", generate)
    response = _request(
        tmp_path,
        "POST",
        "/api/chat/commit-message",
        body={"provider": "codex", "model": "cli-default", "diff": "+staged"},
    )
    assert response.status == 200
    assert response.json() == {"message": "fix: expose resolved edits"}
    generate.assert_awaited_once_with(
        provider="codex",
        model="cli-default",
        effort=None,
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
    "path", ["/api/session", "/api/workflows", "/api/doctor", "/workflows/demo/usage"]
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
    response = _request(tmp_path, "GET", "/api/session", headers={"Host": host})
    assert response.status == 403
    assert "test-ui-token" not in response.text()


def test_opaque_origin_desktop_requires_token_and_narrow_preflight(tmp_path):
    authenticated = _request(tmp_path, "GET", "/api/session", headers={"Origin": "null"})
    assert authenticated.status == 200
    assert authenticated.header("Access-Control-Allow-Origin") == "null"
    preflight = _request(
        tmp_path,
        "OPTIONS",
        "/api/session",
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
        tmp_path, "OPTIONS", "/api/session", authenticated=False, headers={"Origin": "null"}
    )
    assert rejected.status == 403
    assert "null" not in server_module._default_allowed_origins()


@pytest.mark.parametrize(
    "method,path,required",
    [
        ("GET", "/api/health", False),
        ("GET", "/api/session", True),
        ("GET", "/workflows/w/usage", True),
        ("PUT", "/api/workflows/w", True),
        ("DELETE", "/api/workflows/w", True),
        ("OPTIONS", "/api/workflows", False),
        ("POST", "/api/workflows/w/webhooks/default/trigger", False),
        ("POST", "/api/workflows/w/webhooks/default/replay", False),
        ("GET", "/api/workflows/w/webhooks/default/trigger", True),
        ("POST", "/api/other/webhooks/default/trigger", True),
    ],
)
def test_request_policy_covers_methods_and_webhook_exceptions(method, path, required):
    assert server_module._requires_ui_api_auth(method, path) is required


def test_workflow_poll_does_not_reconcile_schedules(tmp_path):
    response = _request(tmp_path, "GET", "/api/workflows")
    assert response.status == 200
    assert response.server.sync_calls == 0


def test_schedule_reconciliation_only_runs_when_configuration_changes(tmp_path, monkeypatch):
    server = _fake_server(tmp_path)
    server._schedule_lock = threading.Lock()
    server._schedule_signature = None
    server.scheduler = cast(Any, object())
    server.watcher = cast(Any, object())
    calls = []
    monkeypatch.setattr(
        server_module, "sync_workflow_schedules", lambda *_: calls.append("schedule")
    )
    monkeypatch.setattr(server_module, "sync_workflow_watchers", lambda *_: calls.append("watch"))
    GoferUiServer.sync_schedules(server)
    GoferUiServer.sync_schedules(server)
    assert calls == ["schedule", "watch"]
    (tmp_path / "external.toml").write_text("# external change")
    GoferUiServer.sync_schedules(server)
    assert calls == ["schedule", "watch", "schedule", "watch"]


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


def test_desktop_trusted_roots_requires_desktop_secret(tmp_path):
    denied = _request(tmp_path, "GET", "/api/desktop/trusted-roots")
    assert denied.status == 403
    project = tmp_path / "project"
    project.mkdir()
    create_registered_workflow(project, "Trusted", registry_dir=tmp_path)
    allowed = _request(
        tmp_path,
        "GET",
        "/api/desktop/trusted-roots",
        headers={"X-Gofer-Desktop-Grant-Secret": "test-secret"},
    )
    assert allowed.status == 200
    assert allowed.json() == {"roots": [str(project)]}


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
    monkeypatch.setattr(
        GoferUiRequestHandler, "_dispatch_POST", lambda self: self._send_json({"ok": True})
    )
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
                    "remSwarmAccess": {"enabled": True},
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
    monkeypatch, tmp_path: Path, endpoint: str
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
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
