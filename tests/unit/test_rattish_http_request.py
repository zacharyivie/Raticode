from __future__ import annotations

import http.client
import io
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from gofer.core import http as http_module
from gofer.core.http import HttpRequest, HttpResponse, UrllibHttpClient
from gofer.rattish import runtime
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node
from gofer.rattish.workflow_runtime import execute_workflow

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


def retry_source(attempts: int, statuses: str = "[429, 503]", node_fields: str = "") -> str:
    return f"""Rattish: 1
Workflow:
  name: Rate limited HTTP request
Node call:
  type: http-request
  url: https://example.com/api
  retry: {{"attempts": {attempts}, "backoff": "0s", "retry-on-statuses": {statuses}}}
{node_fields}"""


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every non-zero wait instead of sleeping. Zero-second yields still run."""
    recorded: list[float] = []
    real_sleep = anyio.sleep

    async def fake_sleep(seconds: float) -> None:
        if seconds:
            recorded.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(anyio, "sleep", fake_sleep)
    return recorded


def in_seconds(seconds: int) -> str:
    return format_datetime(datetime.now(UTC) + timedelta(seconds=seconds), usegmt=True)


@pytest.mark.parametrize("form", ["seconds", "http-date"])
@pytest.mark.anyio
async def test_http_request_waits_out_retry_after_before_each_retry(
    tmp_path: Path, sleeps: list[float], form: str
) -> None:
    # An HTTP date has whole seconds, so 4 seconds from now is always more than 2 away.
    retry_after = "2" if form == "seconds" else in_seconds(4)
    limited = HttpResponse(429, {"Retry-After": retry_after}, b"slow down")
    client = RecordingHttpClient([limited, limited, HttpResponse(200, {}, b"ready")])

    result = await execute_node(
        compile_source(retry_source(3), tmp_path), "call", http_client=client
    )

    assert result.outcome == "success"
    assert result.output["attempts"] == 3
    assert len(sleeps) == 2
    assert all(wait >= 2 for wait in sleeps)


@pytest.mark.anyio
async def test_http_request_waits_the_longer_of_backoff_and_retry_after(
    tmp_path: Path, sleeps: list[float]
) -> None:
    source = retry_source(3).replace('"backoff": "0s"', '"backoff": "5s"')
    client = RecordingHttpClient(
        [
            HttpResponse(503, {"Retry-After": "2"}, b"busy"),
            HttpResponse(503, {}, b"busy"),
            HttpResponse(200, {}, b"ready"),
        ]
    )

    result = await execute_node(compile_source(source, tmp_path), "call", http_client=client)

    assert result.outcome == "success"
    assert sleeps == [5, 5]


@pytest.mark.anyio
async def test_http_request_unexpected_429_carries_retry_after_for_retry_count(
    tmp_path: Path, sleeps: list[float]
) -> None:
    client = RecordingHttpClient([HttpResponse(429, {"Retry-After": "120"}, b"slow down")])

    result = await execute_node(
        compile_source(retry_source(1), tmp_path), "call", http_client=client
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_HTTP_UNEXPECTED_STATUS"
    assert result.error.details == {"status": 429, "retry_after_ms": 120_000}
    assert sleeps == []


@pytest.mark.anyio
async def test_http_request_fails_without_retrying_when_retry_after_passes_the_ceiling(
    tmp_path: Path, sleeps: list[float]
) -> None:
    client = RecordingHttpClient(
        [HttpResponse(503, {"Retry-After": "301"}, b"later"), HttpResponse(200, {}, b"ready")]
    )

    result = await execute_node(
        compile_source(retry_source(3), tmp_path), "call", http_client=client
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_HTTP_UNEXPECTED_STATUS"
    assert result.error.details["retryable"] is False
    assert "wait 301 seconds" in result.error.message
    assert "300-second limit" in result.error.message
    assert result.output["attempts"] == 1
    assert len(client.requests) == 1
    assert sleeps == []


@pytest.mark.anyio
async def test_retry_count_waits_out_http_retry_after_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    client = RecordingHttpClient(
        [HttpResponse(429, {"Retry-After": "2"}, b"slow down"), HttpResponse(200, {}, b"ok")]
    )
    monkeypatch.setattr(runtime, "UrllibHttpClient", lambda: client)
    events: list[dict[str, Any]] = []

    result = await execute_workflow(
        compile_source(retry_source(1, node_fields="  retry-count: 1\n"), tmp_path),
        data_dir=tmp_path,
        on_node_event=lambda event, _record: events.append(event),
    )

    assert result.outcome == "pass", result.error
    assert sleeps == [2]
    retried = [event for event in events if event["type"] == "node_retried"]
    assert [event["message"] for event in retried] == [
        "HTTP response status 429 was not expected. Retrying in 2 seconds."
    ]


@pytest.mark.parametrize("retry_after", ["9" * 400, "9" * 20])
@pytest.mark.anyio
async def test_http_request_fails_cleanly_on_an_enormous_retry_after(
    tmp_path: Path, sleeps: list[float], retry_after: str
) -> None:
    # 400 digits parse to infinity; neither length may become an internal error.
    client = RecordingHttpClient([HttpResponse(429, {"Retry-After": retry_after}, b"never")])

    result = await execute_node(
        compile_source(retry_source(3), tmp_path), "call", http_client=client
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_HTTP_UNEXPECTED_STATUS"
    assert result.error.details == {"status": 429, "retryable": False}
    assert "wait more than a day" in result.error.message
    assert "300-second limit" in result.error.message
    assert len(client.requests) == 1
    assert sleeps == []


@pytest.mark.anyio
async def test_http_request_sends_one_content_type_when_headers_set_it(tmp_path: Path) -> None:
    # Rattish lowercases block-map keys, so this Content-Type arrives as content-type.
    source = """Rattish: 1
Workflow:
  name: JSON:API request
Node call:
  type: http-request
  method: POST
  url: https://example.com/api
  headers:
    Content-Type: application/vnd.api+json
  json: {"data": {"type": "notes"}}
"""
    client = RecordingHttpClient([HttpResponse(200, {}, b"")])

    result = await execute_node(compile_source(source, tmp_path), "call", http_client=client)

    assert result.outcome == "success"
    sent = client.requests[0].headers
    content_types = [value for name, value in sent.items() if name.lower() == "content-type"]
    assert content_types == ["application/vnd.api+json"]


def test_http_request_header_names_compare_case_insensitively() -> None:
    headers = runtime._request_headers({"Accept": "text/plain", "accept": "application/json"})

    assert headers == {"accept": "application/json"}


class WireHttpClient(UrllibHttpClient):
    """Answer the first request with raw response bytes through the real header parser."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, wire: bytes) -> None:
        self.wire = wire
        self.requests: list[HttpRequest] = []
        self.monkeypatch = monkeypatch

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if len(self.requests) > 1:
            return HttpResponse(200, {}, b"")
        parsed = http.client.HTTPResponse(
            SimpleNamespace(makefile=lambda _mode: io.BytesIO(self.wire)),
            method=request.method,
        )
        parsed.begin()

        class WireConnection:
            def __init__(self, host: str, port: int, timeout: float) -> None:
                pass

            def set_policy_target(self, host: str, port: int) -> None:
                pass

            def request(self, *args: Any, **kwargs: Any) -> None:
                pass

            def getresponse(self) -> http.client.HTTPResponse:
                return parsed

            def close(self) -> None:
                pass

        with self.monkeypatch.context() as patch:
            patch.setattr(http_module, "_PolicyHttpConnection", WireConnection)
            return self._send_sync(request)


@pytest.mark.anyio
async def test_http_request_reads_response_headers_lowercase_with_repeats_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = """Rattish: 1
Workflow:
  name: Response headers
  outputs:
    lower:
      from: node.send.output.headers.content-type
      schema: {}
    upper:
      from: node.send.output.headers.Content-Type
      schema: {}
Node send:
  type: http-request
  url: http://127.0.0.1/session
  network-allowlist: ["127.0.0.1"]
  output-mapping: {"kind": "headers.Content-Type", "cookies": "headers.set-cookie"}
  to: lower
Node lower:
  type: http-request
  needs: send
  method: POST
  url: http://127.0.0.1/echo
  network-allowlist: ["127.0.0.1"]
  with:
    body: node.send.output.headers.content-type
  to: upper
Node upper:
  type: http-request
  needs: lower
  method: POST
  url: http://127.0.0.1/echo
  network-allowlist: ["127.0.0.1"]
  with:
    body: node.send.output.headers.Content-Type
"""
    wire = (
        b"HTTP/1.1 200 OK\r\n"
        b"Set-Cookie: a=1; Expires=Wed, 21 Oct 2026 07:28:00 GMT\r\n"
        b"Set-Cookie: b=2\r\n"
        b"Content-Type: application/json\r\n"
        b"X-Tag: one\r\n"
        b"x-tag: two\r\n"
        b"Content-Length: 2\r\n"
        b"\r\n"
        b"{}"
    )
    client = WireHttpClient(monkeypatch, wire)
    monkeypatch.setattr(runtime, "UrllibHttpClient", lambda: client)

    result = await execute_workflow(compile_source(source, tmp_path), data_dir=tmp_path)

    assert result.outcome == "pass", result.error
    sent = result.latest_node_outputs["send"]
    headers = sent["headers"]
    assert headers["set-cookie"] == "a=1; Expires=Wed, 21 Oct 2026 07:28:00 GMT\nb=2"
    assert headers["x-tag"] == "one, two"
    assert headers["content-type"] == "application/json"
    assert "Content-Type" not in headers
    assert sent["selected"] == {
        "kind": "application/json",
        "cookies": "a=1; Expires=Wed, 21 Oct 2026 07:28:00 GMT\nb=2",
    }
    assert [request.body for request in client.requests[1:]] == [
        b"application/json",
        b"application/json",
    ]
    assert result.outputs == {"lower": "application/json", "upper": "application/json"}
