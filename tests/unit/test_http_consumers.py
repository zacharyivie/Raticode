"""Exercise bounded HTTP consumers with the real stdlib framing parser."""

from __future__ import annotations

import base64
import http.client
import io
import json
import time
import zipfile
from typing import Any

import pytest

from gofer.core import provider_usage, provider_usage_reports
from gofer.core.http import read_response_bytes
from gofer.devices.framing import Frame
from gofer.devices.relay import NtfyRelay, RelayError
from gofer.ui import organization_gateways, organization_packages, report_outputs
from gofer.ui.second_brain import serve_stdio


def consumer(name: str, monkeypatch: Any) -> tuple[Any, bytes]:
    if name == "claude":
        return lambda: provider_usage._claude_allowance(
            "test-token"
        ), b'{"five_hour":{"utilization":40}}'
    if name == "cursor":
        body = {"teamMemberSpend": [{"email": "test@example.com", "spendCents": 1250}]}
        return lambda: provider_usage._cursor_spending("test-key", "test@example.com"), json.dumps(
            body
        ).encode()
    if name == "reporting":
        return lambda: provider_usage_reports._get_json(
            "https://example.test/usage", {}, time.monotonic() + 10
        ), b'{"data":[],"has_more":false}'
    if name == "gateway":
        gateway = organization_gateways.HttpGateway(
            {"url": "https://example.test", "turnLimit": 1}, "test-token"
        )
        return lambda: gateway.status(
            "test-request"
        ), b'{"requestId":"test-request","state":"completed"}'
    if name == "pdf":
        monkeypatch.setenv("RATICODE_REPORT_PDF_URL", "http://127.0.0.1:12345/pdf")
        monkeypatch.setenv("RATICODE_REPORT_PDF_TOKEN", "test-token")
        return lambda: report_outputs.render_pdf("<html></html>"), b"%PDF-1.7\nfixture\n%%EOF"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("repo-commit/COMPANY.md", "# Test company")
    return lambda: organization_packages.github_package("owner/repo", "a" * 40), archive.getvalue()


def wire_response(body: bytes, framing: str, truncated: bool) -> http.client.HTTPResponse:
    if framing == "fixed":
        wire = f"Content-Length: {len(body) + (10 if truncated else 0)}\r\n\r\n".encode() + body
    elif framing == "chunked":
        wire = (
            b"Transfer-Encoding: chunked\r\n\r\n" + f"{len(body):x}\r\n".encode() + body + b"\r\n"
        )
        if not truncated:
            wire += b"0\r\n\r\n"
    else:
        wire = b"\r\n" + body

    class Socket:
        def makefile(self, *args: Any) -> io.BufferedReader:
            return io.BufferedReader(io.BytesIO(b"HTTP/1.1 200 OK\r\n" + wire))

    response = http.client.HTTPResponse(Socket())  # type: ignore[arg-type]
    response.begin()
    return response


@pytest.mark.parametrize("name", ["claude", "cursor", "reporting", "gateway", "pdf", "package"])
@pytest.mark.parametrize(
    "framing,truncated",
    [("fixed", False), ("chunked", False), ("eof", False), ("fixed", True), ("chunked", True)],
)
def test_http_consumers_require_complete_response(monkeypatch, name, framing, truncated):
    call, body = consumer(name, monkeypatch)

    class Opener:
        def open(self, *args: Any, **kwargs: Any) -> http.client.HTTPResponse:
            return wire_response(body, framing, truncated)

    for module in (provider_usage.urllib.request, organization_gateways, report_outputs):
        monkeypatch.setattr(module, "build_opener", lambda *args: Opener())
    if truncated:
        with pytest.raises(ValueError, match="Incomplete HTTP response"):
            call()
    else:
        result = call()
        assert result
        if name == "package":
            assert base64.b64decode(result["COMPANY.md"]) == b"# Test company"


@pytest.mark.parametrize("operation", ["poll", "publish"])
@pytest.mark.parametrize(
    "framing,truncated",
    [("fixed", False), ("chunked", False), ("eof", False), ("fixed", True), ("chunked", True)],
)
async def test_relay_requires_complete_response(monkeypatch, operation, framing, truncated):
    relay = NtfyRelay("https://relay.example.test")
    topic = "opaque_topic_01234567890123456789"
    raw = Frame(
        "12345678-1234-1234-1234-123456789abc", "resume", "initiator_to_responder", 0, b"tls"
    ).encode()
    event = {"event": "message", "id": "message1", "topic": topic, "message": raw.decode()}
    body = (json.dumps(event) + "\n").encode() if operation == "poll" else b"{}"

    class Opener:
        def open(self, *args: Any, **kwargs: Any) -> http.client.HTTPResponse:
            return wire_response(body, framing, truncated)

    monkeypatch.setattr(relay, "_opener", Opener())
    call = relay.poll(topic) if operation == "poll" else relay.publish(topic, raw)
    if truncated:
        with pytest.raises(RelayError, match="^relay_unavailable_or_invalid$"):
            await call
    else:
        result = await call
        if operation == "poll":
            assert result is not None
            assert len(result) == 1
            assert result[0].frame == raw
            assert result[0].id == "message1"
        else:
            assert result is None


@pytest.mark.parametrize("framing", ["fixed", "chunked", "eof"])
@pytest.mark.parametrize("size", [7, 8, 9, 100])
def test_bounded_reader_retains_size_detection(framing, size):
    with wire_response(b"x" * size, framing, False) as response:
        assert read_response_bytes(response, 8) == b"x" * min(size, 9)


@pytest.mark.parametrize("framing", ["fixed", "chunked"])
def test_truncated_pdf_keeps_mcp_alive_and_publishes_no_files(monkeypatch, tmp_path, framing):
    consumer("pdf", monkeypatch)

    class Opener:
        def open(self, *args: Any, **kwargs: Any) -> http.client.HTTPResponse:
            return wire_response(b"%PDF-1.7\nprivate report content", framing, True)

    monkeypatch.setattr(report_outputs, "build_opener", lambda *args: Opener())
    tools = report_outputs.ReportTools(tmp_path, "pdf")
    requests = [
        {
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "save_report",
                "arguments": {
                    "path": "reports/review.pdf",
                    "content": "<html><head></head><body>Report</body></html>",
                },
            },
        },
        {"id": 2, "method": "tools/call", "params": {"name": "rules", "arguments": {}}},
    ]
    output = io.StringIO()
    serve_stdio(
        tools.call,
        [],
        tools.call("rules", {}),
        "reports",
        input_stream=io.StringIO("\n".join(json.dumps(request) for request in requests) + "\n"),
        output_stream=output,
    )
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2]
    assert replies[0]["result"]["isError"]
    assert "Incomplete HTTP response" in json.dumps(replies[0])
    assert "private report content" not in output.getvalue()
    assert not replies[1]["result"].get("isError")
    assert not list(tmp_path.rglob("*.pdf*"))
