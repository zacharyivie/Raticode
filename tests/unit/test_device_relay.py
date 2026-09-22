"""Real local HTTP exercises ntfy carrier bounds without public relay traffic."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices.framing import Frame
from gofer.devices.relay import NtfyRelay, RelayError, _retry_after

TOPIC = "opaque_topic_01234567890123456789"


@pytest.fixture
def relay_server():
    state = {"status": 200, "body": b"{}", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self) -> None:
            state["requests"].append(
                (
                    self.path,
                    dict(self.headers),
                    self.rfile.read(int(self.headers.get("Content-Length", "0"))),
                )
            )
            self.send_response(state["status"])
            self.send_header("Location", "/must-not-follow")
            self.send_header("Retry-After", "120")
            self.end_headers()
            self.wfile.write(state["body"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def frame() -> bytes:
    return Frame(str(uuid4()), "resume", "initiator_to_responder", 0, b"tls-bytes").encode()


@pytest.mark.parametrize(
    "url",
    [
        "http://ntfy.sh",
        "https://user:password@ntfy.sh",
        "https://ntfy.sh/topic",
        "https://ntfy.sh?token=secret",
        "https://ntfy.sh#fragment",
        "file:///tmp/relay",
        "http://192.168.1.5",
        "https://ntfy.sh:bad",
    ],
)
def test_reject_unprotected_or_credential_urls(url):
    with pytest.raises(RelayError, match="relay_url"):
        NtfyRelay(url, allow_loopback_http=True)


async def test_publish_only_frame_and_generic_headers(relay_server):
    state, url = relay_server
    relay = NtfyRelay(url, token="test-token", allow_loopback_http=True)
    raw = frame()
    await relay.publish(TOPIC, raw)
    path, headers, body = state["requests"][0]
    assert path == "/" + TOPIC
    assert body == raw
    assert headers["Title"] == "Raticode"
    assert headers["Authorization"] == "Bearer test-token"
    assert "Filename" not in headers


async def test_poll_roundtrip_cursor_and_no_attachment(relay_server):
    state, url = relay_server
    relay = NtfyRelay(url, allow_loopback_http=True)
    raw = frame()
    event: dict[str, Any] = {
        "event": "message",
        "id": "abc123",
        "topic": TOPIC,
        "message": raw.decode(),
    }
    state["body"] = (json.dumps(event) + "\n").encode()
    result = await relay.poll(TOPIC, "last123")
    assert result[0].frame == raw
    assert result[0].id == "abc123"
    assert state["requests"][0][0].endswith("?poll=1&since=last123")
    event["attachment"] = {"url": "https://example.invalid/private"}
    state["body"] = json.dumps(event).encode()
    with pytest.raises(RelayError, match="relay_event"):
        await relay.poll(TOPIC)


@pytest.mark.parametrize("status,code", [(301, "relay_http"), (429, "relay_rate_limited")])
async def test_no_redirects_and_retry_after(relay_server, status, code):
    state, url = relay_server
    state["status"] = status
    relay = NtfyRelay(url, allow_loopback_http=True)
    with pytest.raises(RelayError, match=code) as error:
        await relay.publish(TOPIC, frame())
    assert error.value.retry_after == 120
    assert len(state["requests"]) == 1
    assert url not in str(error.value)


async def test_publish_rate_limit_blocks_poll_and_publish_across_instances(relay_server):
    state, url = relay_server
    state["status"] = 429
    first = NtfyRelay(url, allow_loopback_http=True)
    second = NtfyRelay(url, allow_loopback_http=True)
    with pytest.raises(RelayError, match="relay_rate_limited"):
        await first.publish(TOPIC, frame())
    state["status"] = 200
    for operation in (second.poll(TOPIC), second.publish(TOPIC, frame()), first.poll(TOPIC)):
        with pytest.raises(RelayError, match="relay_rate_limited") as error:
            await operation
        assert 110 < error.value.retry_after <= 120
    assert len(state["requests"]) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"x" * 16385,
        b'{"event":"message","event":"open"}',
        b'{"event":"message","message":"plaintext"}',
    ],
)
async def test_malformed_and_oversized_input_fail_closed(relay_server, body):
    state, url = relay_server
    state["body"] = body
    with pytest.raises(RelayError):
        await NtfyRelay(url, allow_loopback_http=True).poll(TOPIC)


async def test_plaintext_never_published(relay_server):
    state, url = relay_server
    with pytest.raises(ValueError):
        await NtfyRelay(url, allow_loopback_http=True).publish(TOPIC, b"hello Rem")
    assert state["requests"] == []


def test_retry_after_boundaries():
    assert _retry_after("-1") == 1
    assert _retry_after("99999999999") == 86400
    assert _retry_after("garbage") == 5
