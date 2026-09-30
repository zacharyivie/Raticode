from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.core.http import HttpRequest, HttpResponse, UrllibHttpClient
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"


class RecordingHttpClient:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "http-request.json"],
    )


def compile_source(source: str, project_root: Path) -> dict[str, Any]:
    return compiler().compile(source, CompileContext("http-request", project_root)).ir


@pytest.mark.anyio
async def test_http_request_compiles_defaults_binds_json_and_returns_structured_output(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1
Workflow:
  name: Bound HTTP request
  inputs:
    payload:
      schema: {"type": "object"}
      required: true
Node call:
  type: http-request
  method: post
  url: https://example.com/api
  params: {"mode": "quick"}
  response-mode: JSON
  output-mapping: {"answer": "json.answer"}
  with:
    json: input.payload
"""
    ir = compile_source(source, tmp_path)
    client = RecordingHttpClient(
        [HttpResponse(200, {"Content-Type": "application/json"}, b'{"answer": 42}')]
    )

    result = await execute_node(
        ir,
        "call",
        workflow_inputs={"payload": {"question": "life"}},
        http_client=client,
    )

    assert result.outcome == "success"
    assert result.output["json"] == {"answer": 42}
    assert result.output["selected"] == {"answer": 42}
    assert result.output["value"] == {"answer": 42}
    assert client.requests[0].url == "https://example.com/api?mode=quick"
    assert json.loads(client.requests[0].body or b"") == {"question": "life"}
    assert client.requests[0].timeout_seconds == 30


@pytest.mark.anyio
async def test_http_request_retries_configured_status_then_succeeds(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Retrying HTTP request
Node call:
  type: http-request
  url: https://example.com/api
  retry: {"attempts": 2, "backoff": "0s", "retry-on-statuses": [503]}
"""
    client = RecordingHttpClient([HttpResponse(503, {}, b"busy"), HttpResponse(200, {}, b"ready")])

    result = await execute_node(compile_source(source, tmp_path), "call", http_client=client)

    assert result.outcome == "success"
    assert result.output["attempts"] == 2
    assert len(client.requests) == 2


@pytest.mark.anyio
async def test_http_request_reports_unexpected_status_as_network_failure(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Failed HTTP request
Node call:
  type: http-request
  url: https://example.com/api
"""
    client = RecordingHttpClient([HttpResponse(404, {}, b"missing")])

    result = await execute_node(compile_source(source, tmp_path), "call", http_client=client)

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_HTTP_UNEXPECTED_STATUS"
    assert result.output["status"] == 404


def test_http_request_rejects_json_and_body_together(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Invalid body
Node call:
  type: http-request
  url: https://example.com/api
  json: {"value": 1}
  body: text
"""

    with pytest.raises(RattishCompileError) as caught:
        compile_source(source, tmp_path)

    assert "RATTISH_MUTUALLY_EXCLUSIVE_FIELDS" in {item.code for item in caught.value.diagnostics}


def test_http_request_plaintext_credentials_warn_without_blocking_ir(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Plaintext request credential
Node call:
  type: http-request
  url: https://example.com/api
  headers: {"Authorization": "Bearer visible-token"}
"""

    result = compiler().compile(source, CompileContext("http-warning", tmp_path))

    assert result.ir["nodes"][0]["configuration"]["headers"] == {
        "Authorization": "Bearer visible-token"
    }
    assert [item.code for item in result.diagnostics] == ["RATTISH_SUSPECTED_PLAINTEXT_SECRET"]
    assert result.diagnostics[0].details["field"] == "headers.Authorization"


@pytest.mark.parametrize("host", ["127.0.0.1", "100.64.0.1", "[::ffff:100.64.0.1]"])
def test_http_request_preflight_blocks_local_network_targets(tmp_path: Path, host: str) -> None:
    source = f"""Rattish: 1
Workflow:
  name: Unsafe HTTP request
Node call:
  type: http-request
  url: http://{host}/admin
"""

    result = run_preflight(compile_source(source, tmp_path), data_dir=tmp_path / "data")

    assert not result.ready
    assert [item.code for item in result.diagnostics] == ["RATTISH_PREFLIGHT_NETWORK_POLICY"]


@pytest.mark.parametrize(
    "url",
    [
        "http://alice:private-password@127.0.0.1/private-path?token=private-query",
        "https://alice:private-password@example.test:private-port/?token=private-query",
        "https://alice:private-password@[private-address]/?token=private-query",
    ],
)
@pytest.mark.anyio
async def test_http_policy_failure_redacts_preflight_and_runtime_details(
    tmp_path: Path, url: str
) -> None:
    source = f"""Rattish: 1
Workflow:
  name: Private request
Node call:
  type: http-request
  url: {json.dumps(url)}
  params: {{"search": "value"}}
"""
    ir = compile_source(source, tmp_path)
    preflight = run_preflight(ir, data_dir=tmp_path / "data")
    assert not preflight.ready
    assert preflight.diagnostics[0].code == "RATTISH_PREFLIGHT_NETWORK_POLICY"
    assert "private-" not in str(preflight.diagnostics[0])
    assert "alice" not in str(preflight.diagnostics[0].details)

    client = RecordingHttpClient([])
    result = await execute_node(ir, "call", http_client=client)
    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_HTTP_NETWORK_POLICY"
    assert "private-" not in str(result.error)
    assert "alice" not in str(result.error)
    assert client.requests == []


@pytest.mark.parametrize(
    "url,headers",
    [
        ("https://1.1.1.1/private-path invalid?token=private-token", {}),
        ("https://1.1.1.1/", {"Authorization": "Bearer private-token\rBAD"}),
    ],
)
@pytest.mark.anyio
async def test_http_transport_failure_never_records_invalid_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str, headers: dict[str, str]
) -> None:
    source = f"""Rattish: 1
Workflow:
  name: Private malformed request
Node call:
  type: http-request
  url: {json.dumps(url)}
  headers: {json.dumps(headers)}
"""

    def unexpected_connection(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Invalid request must be rejected before connecting")

    class InlineHttpClient(UrllibHttpClient):
        async def send(self, request: HttpRequest) -> HttpResponse:
            # Exercise the real request serializer without worker-thread I/O.
            # Malformed requests must fail before any connection is opened.
            return self._send_sync(request)

    monkeypatch.setattr("gofer.core.http.socket.create_connection", unexpected_connection)
    result = await execute_node(
        compile_source(source, tmp_path), "call", http_client=InlineHttpClient()
    )
    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_HTTP_TRANSPORT_ERROR"
    assert "Invalid HTTP request" in str(result.error)
    assert "private-" not in str(result.error)
    assert not result.output
