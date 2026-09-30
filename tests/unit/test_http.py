from __future__ import annotations

import http.client
import io
import socket
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.core import http as http_module
from gofer.core.http import HttpRequest, UrllibHttpClient, append_query_params
from gofer.core.network_policy import (
    NetworkPolicyViolation,
    resolve_http_request_target,
    validate_http_request_url,
)


def _install_recording_connection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int,
    headers: list[tuple[str, str]],
    body: bytes,
) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    class RecordingConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            calls.update({"host": host, "port": port, "timeout": timeout})

        def set_policy_target(self, host: str, port: int) -> None:
            calls.update({"connect_host": host, "connect_port": port})

        def request(
            self,
            method: str,
            path: str,
            *,
            body: bytes | None,
            headers: dict[str, str],
        ) -> None:
            calls.update({"method": method, "path": path, "body": body, "headers": headers})

        def getresponse(self) -> SimpleNamespace:
            return SimpleNamespace(
                status=status,
                headers=SimpleNamespace(items=lambda: headers),
                length=None,
                read1=io.BytesIO(body).read1,
            )

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(http_module, "_PolicyHttpConnection", RecordingConnection)
    return calls


def test_append_query_params_returns_original_url_for_empty_params() -> None:
    url = "https://example.test/search?existing=1#results"

    assert append_query_params(url, {}) == url


@pytest.mark.parametrize("authority", ["alice:private-password@", "alice:private-password@:443"])
def test_network_policy_redacts_credentials_when_url_host_is_missing(authority: str) -> None:
    with pytest.raises(NetworkPolicyViolation, match="missing URL host") as caught:
        validate_http_request_url(
            f"https://{authority}/resource?token=private-query#private-fragment"
        )
    assert "alice" not in str(caught.value)
    assert "private-" not in str(caught.value)
    assert "<missing-host>" in caught.value.url


@pytest.mark.parametrize(
    "url",
    [
        "https://alice:private-password@example.test:private-port/resource?token=private-query",
        "https://alice:private-password@[private-address]/resource?token=private-query",
        "https://alice:private-password@example.test:99999/resource?token=private-query",
    ],
)
@pytest.mark.parametrize("operation", ["validate", "resolve", "send", "query"])
def test_malformed_http_authorities_never_expose_credentials(url: str, operation: str) -> None:
    with pytest.raises(NetworkPolicyViolation, match="invalid URL authority") as caught:
        if operation == "validate":
            validate_http_request_url(url)
        elif operation == "resolve":
            resolve_http_request_target(url)
        elif operation == "send":
            UrllibHttpClient()._send_sync(HttpRequest(method="GET", url=url))
        else:
            append_query_params(url, {"mode": "test"})
    assert "private-" not in str(caught.value)
    assert "alice" not in str(caught.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]"])
def test_network_policy_errors_hide_credentials_in_url_paths(host: str) -> None:
    with pytest.raises(NetworkPolicyViolation) as caught:
        validate_http_request_url(
            f"http://alice:private-password@{host}/services/private-path?token=private-query"
        )
    assert caught.value.url == f"http://{host}/"
    assert "private-" not in str(caught.value)
    assert "alice" not in str(caught.value)


def test_append_query_params_preserves_existing_repeated_and_blank_values() -> None:
    url = "https://example.test/search?tag=one&empty=&tag=two"

    assert (
        append_query_params(url, {"tag": "three", "q": ""})
        == "https://example.test/search?tag=one&empty=&tag=two&tag=three&q="
    )


def test_append_query_params_encodes_spaces_special_characters_and_keeps_fragment() -> None:
    url = "https://example.test/search?existing=value#section"

    assert (
        append_query_params(url, {"phrase": "hello world", "symbols": "a/b?&="})
        == "https://example.test/search?existing=value&phrase=hello+world&symbols=a%2Fb%3F%26%3D#section"
    )


def test_urllib_http_client_sends_request_and_maps_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_recording_connection(
        monkeypatch,
        status=201,
        headers=[("Content-Type", "application/json"), ("X-Gofer-Test", "success")],
        body=b'{"ok": true}',
    )

    response = UrllibHttpClient()._send_sync(
        HttpRequest(
            method="post",
            url="http://127.0.0.1:8080/ok?x=1",
            headers={"X-Request": "gofer"},
            body=b"payload",
            timeout_seconds=3.5,
            network_allowlist=["127.0.0.1"],
        )
    )

    assert response.status == 201
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["X-Gofer-Test"] == "success"
    assert response.body == b'{"ok": true}'
    assert calls == {
        "host": "127.0.0.1",
        "port": 8080,
        "timeout": 3.5,
        "connect_host": "127.0.0.1",
        "connect_port": 8080,
        "method": "POST",
        "path": "/ok?x=1",
        "body": b"payload",
        "headers": {"X-Request": "gofer"},
        "closed": True,
    }


def test_urllib_http_client_maps_non_2xx_response_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_recording_connection(
        monkeypatch,
        status=418,
        headers=[("X-Error", "teapot")],
        body=b"request failed",
    )

    response = UrllibHttpClient()._send_sync(
        HttpRequest(
            method="POST",
            url="http://127.0.0.1:8080/error",
            body=b"bad request",
            network_allowlist=["127.0.0.1"],
        )
    )

    assert response.status == 418
    assert response.headers["X-Error"] == "teapot"
    assert response.body == b"request failed"


def test_urllib_http_client_passes_timeout_to_http_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_recording_connection(
        monkeypatch,
        status=204,
        headers=[("X-Test", "ok")],
        body=b"",
    )

    response = UrllibHttpClient()._send_sync(
        HttpRequest(
            method="patch",
            url="http://127.0.0.1:8080/path?x=1",
            headers={"X-Request": "gofer"},
            body=b"body",
            timeout_seconds=1.25,
            network_allowlist=["127.0.0.1"],
        )
    )

    assert response.status == 204
    assert calls == {
        "host": "127.0.0.1",
        "port": 8080,
        "timeout": 1.25,
        "connect_host": "127.0.0.1",
        "connect_port": 8080,
        "method": "PATCH",
        "path": "/path?x=1",
        "body": b"body",
        "headers": {"X-Request": "gofer"},
        "closed": True,
    }


def test_urllib_http_client_propagates_lower_level_network_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_network_error(
        _address: tuple[str, int],
        _timeout: float | object = socket._GLOBAL_DEFAULT_TIMEOUT,
        _source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        raise OSError("connection refused")

    monkeypatch.setattr(http_module.socket, "create_connection", raise_network_error)

    with pytest.raises(OSError, match="connection refused"):
        UrllibHttpClient()._send_sync(
            HttpRequest(
                method="GET",
                url="http://127.0.0.1:9/status",
                network_allowlist=["127.0.0.1"],
            )
        )


@pytest.mark.parametrize(
    "http_request,error_type,message",
    [
        (
            HttpRequest("GET", "https://1.1.1.1/", {"Authorization": "Bearer private-token\rBAD"}),
            ValueError,
            "Invalid HTTP request method, headers or encoding",
        ),
        (
            HttpRequest("GET", "https://1.1.1.1/", {"private-header\n": "private-token"}),
            ValueError,
            "Invalid HTTP request method, headers or encoding",
        ),
        (
            HttpRequest("GET", "https://1.1.1.1/private-path invalid?token=private-token"),
            http.client.InvalidURL,
            "Invalid HTTP request URL path",
        ),
        (
            HttpRequest("GET\rprivate-method", "https://1.1.1.1/"),
            ValueError,
            "Invalid HTTP request method, headers or encoding",
        ),
        (
            HttpRequest("GET", "https://1.1.1.1/", {"Authorization": "Bearer private-token\u2603"}),
            ValueError,
            "Invalid HTTP request method, headers or encoding",
        ),
    ],
)
def test_invalid_http_requests_never_expose_credentials(
    monkeypatch: pytest.MonkeyPatch,
    http_request: HttpRequest,
    error_type: type[Exception],
    message: str,
) -> None:
    def unexpected_connection(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Invalid request must be rejected before connecting")

    monkeypatch.setattr(http_module.socket, "create_connection", unexpected_connection)
    with pytest.raises(error_type, match=message) as caught:
        UrllibHttpClient()._send_sync(http_request)
    assert "private-" not in str(caught.value)
    assert caught.value.__suppress_context__


def _send_wire_response(
    monkeypatch: pytest.MonkeyPatch, wire: bytes, *, method: str = "GET"
) -> http_module.HttpResponse:
    # Exercise the real parser and read1 EOF semantics without a network service.
    response = http.client.HTTPResponse(
        SimpleNamespace(makefile=lambda _mode: io.BytesIO(wire)), method=method
    )
    response.begin()
    _install_recording_connection(monkeypatch, status=200, headers=[], body=b"")
    monkeypatch.setattr(http_module._PolicyHttpConnection, "getresponse", lambda self: response)
    return UrllibHttpClient()._send_sync(
        HttpRequest(method, "http://127.0.0.1/", network_allowlist=["127.0.0.1"])
    )


def test_http_client_rejects_truncated_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(http.client.IncompleteRead):
        _send_wire_response(monkeypatch, b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nshort")


@pytest.mark.parametrize("method,status", [("HEAD", 200), ("GET", 304)])
def test_http_client_accepts_large_metadata_length_without_body(
    monkeypatch: pytest.MonkeyPatch, method: str, status: int
) -> None:
    response = _send_wire_response(
        monkeypatch,
        f"HTTP/1.1 {status} OK\r\nContent-Length: 99999999\r\n\r\n".encode(),
        method=method,
    )
    assert response.status == status
    assert response.body == b""


@pytest.mark.parametrize(
    "headers,body,expected",
    [
        (b"Content-Length: 5\r\n", b"whole", b"whole"),
        (b"", b"until EOF", b"until EOF"),
        (b"Transfer-Encoding: chunked\r\n", b"5\r\nwhole\r\n0\r\n\r\n", b"whole"),
    ],
)
def test_http_client_preserves_complete_response_framing(
    monkeypatch: pytest.MonkeyPatch, headers: bytes, body: bytes, expected: bytes
) -> None:
    response = _send_wire_response(monkeypatch, b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body)
    assert response.body == expected


@pytest.mark.parametrize(
    "headers,body",
    [
        (b"Content-Length: 9\r\n", b"123456789"),
        (b"", b"123456789"),
        (b"Transfer-Encoding: chunked\r\n", b"9\r\n123456789\r\n0\r\n\r\n"),
    ],
)
def test_http_client_enforces_body_limit_for_each_framing_mode(
    monkeypatch: pytest.MonkeyPatch, headers: bytes, body: bytes
) -> None:
    monkeypatch.setattr(http_module, "HTTP_RESPONSE_MAX_BYTES", 8)
    with pytest.raises(ValueError, match="response limit"):
        _send_wire_response(monkeypatch, b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body)


def test_http_client_rejects_truncated_chunked_response(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(http.client.IncompleteRead):
        _send_wire_response(
            monkeypatch, b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nshort\r\n"
        )


@pytest.mark.parametrize("host", ["100.64.0.1", "100.127.255.254", "[::ffff:100.64.0.1]"])
def test_network_policy_blocks_shared_address_space(host: str) -> None:
    with pytest.raises(NetworkPolicyViolation):
        resolve_http_request_target(f"http://{host}/")


def test_network_policy_blocks_dns_resolving_to_shared_address_space() -> None:
    with pytest.raises(NetworkPolicyViolation):
        resolve_http_request_target(
            "http://internal.example.test/", resolver=lambda _host, _port: ["100.64.0.1"]
        )


def test_network_policy_allows_explicitly_approved_shared_address() -> None:
    target = resolve_http_request_target("http://100.64.0.1/", allowlist=["100.64.0.0/10"])
    assert target.allowed_by == "100.64.0.0/10"


@pytest.mark.parametrize("host", ["100.63.255.254", "100.128.0.1"])
def test_network_policy_preserves_public_neighbors_of_shared_range(host: str) -> None:
    assert resolve_http_request_target(f"http://{host}/").connect_host == host
