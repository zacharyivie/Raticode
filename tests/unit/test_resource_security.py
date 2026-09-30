from __future__ import annotations

import hashlib
import io
import threading
import time
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import anyio
import pytest

from gofer.core import http
from gofer.ui import chat_media as media


@pytest.mark.parametrize("declared", [True, False])
def test_http_response_budget(monkeypatch, declared):
    monkeypatch.setattr(http, "HTTP_RESPONSE_MAX_BYTES", 8)
    reads = []
    body = io.BytesIO(b"123456789")

    def read1(amount):
        reads.append(amount)
        return body.read1(amount)

    class Connection:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def set_policy_target(self, *args):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return SimpleNamespace(
                status=200,
                length=9 if declared else None,
                read1=read1,
                headers={"Content-Length": "9"} if declared else {},
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(http, "_PolicyHttpConnection", Connection)
    with pytest.raises(ValueError, match="response limit"):
        http.UrllibHttpClient()._send_sync(
            http.HttpRequest(method="GET", url="http://127.0.0.1", network_allowlist=["127.0.0.1"])
        )
    assert (not reads) if declared else max(reads) <= 9


@contextmanager
def trickle_server(headers=False):
    disconnected = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            try:
                if headers:
                    self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                else:
                    self.send_response(200)
                    self.send_header("Connection", "close")
                    self.end_headers()
                for _ in range(100):
                    self.connection.sendall(b"x")
                    time.sleep(0.02)
            except (OSError, ValueError):
                pass
            finally:
                disconnected.set()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield (
            http.HttpRequest(
                method="GET",
                url=f"http://127.0.0.1:{server.server_port}",
                network_allowlist=["127.0.0.1"],
                timeout_seconds=0.15,
            ),
            disconnected,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("headers", [True, False])
def test_http_total_deadline_stops_trickles(headers):
    with trickle_server(headers) as (request, disconnected):
        started = time.monotonic()
        with pytest.raises((TimeoutError, OSError, http.http.client.HTTPException)):
            http.UrllibHttpClient()._send_sync(request)
        assert time.monotonic() - started < 1
        assert disconnected.wait(1)


@pytest.mark.asyncio
async def test_http_cancellation_closes_active_socket():
    with trickle_server() as (request, disconnected):
        with anyio.move_on_after(0.06) as scope:
            await http.UrllibHttpClient().send(request)
        assert scope.cancel_called
        assert await anyio.to_thread.run_sync(disconnected.wait, 1)


def archive_bytes(
    entries: list[tuple[str | zipfile.ZipInfo, bytes]], compression: int = zipfile.ZIP_STORED
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, body in entries:
            if isinstance(name, str):
                member = zipfile.ZipInfo(name)
                # Preserve malicious names that ZipInfo normalizes on Windows.
                member.filename = name
                member.compress_type = compression
            else:
                member = name
            archive.writestr(member, body)
    return output.getvalue()


@pytest.mark.parametrize("name", ["../outside", "/outside", "C:/outside", "x\\y", "x/../../bad"])
def test_model_archive_rejects_unsafe_paths(tmp_path, name):
    with zipfile.ZipFile(io.BytesIO(archive_bytes([(name, b"x")]))) as archive:
        assert archive.infolist()[0].orig_filename == name
        with pytest.raises(media.ChatMediaError, match="unsafe path"):
            media._safe_extract_zip(archive, tmp_path)
    assert [p for p in tmp_path.iterdir() if p.name != "gofer-env"] == []


def test_model_archive_rejects_windows_normalized_path(tmp_path):
    with zipfile.ZipFile(io.BytesIO(archive_bytes([("x\\y", b"x")]))) as archive:
        member = archive.infolist()[0]
        assert member.orig_filename == "x\\y"
        # Reproduce Windows ZIP reading on every platform.
        member.filename = "x/y"
        with pytest.raises(media.ChatMediaError, match="unsafe path"):
            media._safe_extract_zip(archive, tmp_path)
    assert [p for p in tmp_path.iterdir() if p.name != "gofer-env"] == []


def test_model_archive_rejects_links_and_amplification(tmp_path):
    link = zipfile.ZipInfo("link")
    link.external_attr = 0o120777 << 16
    for entries, compression in [
        ([(link, b"outside")], zipfile.ZIP_STORED),
        ([("bomb", b"0" * 100000)], zipfile.ZIP_DEFLATED),
    ]:
        with zipfile.ZipFile(io.BytesIO(archive_bytes(entries, compression))) as archive:
            with pytest.raises(media.ChatMediaError):
                media._safe_extract_zip(archive, tmp_path)
    assert [p for p in tmp_path.iterdir() if p.name != "gofer-env"] == []


@pytest.mark.parametrize(
    "budget",
    ["VOSK_MODEL_MAX_ENTRIES", "VOSK_MODEL_MAX_FILE_BYTES", "VOSK_MODEL_MAX_EXPANDED_BYTES"],
)
def test_model_archive_enforces_extraction_budgets(tmp_path, monkeypatch, budget):
    monkeypatch.setattr(media, budget, 1)
    with zipfile.ZipFile(io.BytesIO(archive_bytes([("a", b"aa"), ("b", b"bb")]))) as archive:
        with pytest.raises(media.ChatMediaError):
            media._safe_extract_zip(archive, tmp_path)
    assert [p for p in tmp_path.iterdir() if p.name != "gofer-env"] == []


@pytest.mark.parametrize("failure", ["hash", "layout", None])
def test_model_install_checks_digest_layout_and_promotes_atomically(tmp_path, monkeypatch, failure):
    content = b"model fixture"
    name = media.VOSK_MODEL_NAME
    raw = archive_bytes([(f"{name}/am/final.mdl", content)])
    monkeypatch.setattr(media, "urlopen", lambda *a, **kw: io.BytesIO(raw))
    monkeypatch.setattr(
        media, "VOSK_MODEL_SHA256", "bad" if failure == "hash" else hashlib.sha256(raw).hexdigest()
    )
    monkeypatch.setattr(
        media,
        "VOSK_MODEL_FILES",
        {
            "am/final.mdl": hashlib.sha256(content).hexdigest(),
            **({"missing": "bad"} if failure == "layout" else {}),
        },
    )
    target = tmp_path / name
    if failure:
        with pytest.raises(media.ChatMediaError):
            media._ensure_vosk_model(target)
        assert not target.exists()
        assert [p for p in tmp_path.iterdir() if p.name != "gofer-env"] == []
    else:
        media._ensure_vosk_model(target)
        assert (target / "am/final.mdl").read_bytes() == content
        monkeypatch.setattr(
            media, "urlopen", lambda *a, **kw: pytest.fail("cached model downloaded")
        )
        media._ensure_vosk_model(target)
        (target / "am/final.mdl").write_bytes(b"tampered")
        assert not media._valid_vosk_model(target)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_cancelled_connect_does_not_send_a_request(monkeypatch, scheme):
    class Socket:
        closed = False

        def close(self):
            self.closed = True

        def sendall(self, *args):
            pytest.fail("request sent after cancellation")

    control = http._RequestControl()
    transport = Socket()

    def connect(*args):
        control.close()  # Cancellation while the OS is still completing connect.
        return transport

    monkeypatch.setattr(http.socket, "create_connection", connect)
    with pytest.raises(TimeoutError):
        http.UrllibHttpClient()._send_sync(
            http.HttpRequest(
                method="GET", url=f"{scheme}://127.0.0.1", network_allowlist=["127.0.0.1"]
            ),
            control,
        )
    assert transport.closed


def test_existing_model_rejects_extra_native_resources_and_links(tmp_path, monkeypatch):
    folder = tmp_path / "model"
    folder.mkdir()
    (folder / "model").write_bytes(b"known")
    monkeypatch.setattr(media, "VOSK_MODEL_FILES", {"model": hashlib.sha256(b"known").hexdigest()})
    assert media._valid_vosk_model(folder)
    (folder / "extra.conf").write_text("injected resource")
    assert not media._valid_vosk_model(folder)
    (folder / "extra.conf").unlink()
    (folder / "unknown").mkdir()
    assert not media._valid_vosk_model(folder)
    (folder / "unknown").rmdir()
    (tmp_path / "alias").symlink_to(folder, target_is_directory=True)
    assert not media._valid_vosk_model(tmp_path / "alias")
