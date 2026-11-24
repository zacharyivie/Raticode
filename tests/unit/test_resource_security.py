from __future__ import annotations

import hashlib
import io
import tarfile
import threading
import time
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


def tar_bytes(entries: list[tuple[str, bytes, bytes]]) -> bytes:
    """A .tar.bz2 archive; each entry is (name, body, tar type such as REGTYPE or SYMTYPE)."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:bz2") as archive:
        for name, body, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
            else:
                member.linkname = body.decode()
                archive.addfile(member)
    return output.getvalue()


def model_entry(name: str) -> str:
    return f"{media.OFFLINE_MODEL_ARCHIVE_DIR}/{name}"


def extract(raw: bytes, target) -> None:
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r|bz2") as archive:
        media._extract_model_files(archive, target)


@pytest.fixture
def one_file_model(monkeypatch):
    monkeypatch.setattr(media, "OFFLINE_MODEL_FILES", {"tokens.txt": "unused"})


@pytest.mark.parametrize(
    "name", ["../outside", "/outside", "C:/outside", "x\\y", "x/../../bad", "../tokens.txt"]
)
def test_model_archive_never_writes_unexpected_paths(tmp_path, one_file_model, name):
    target = tmp_path / "model"
    target.mkdir()
    raw = tar_bytes([(name, b"x", tarfile.REGTYPE)])
    with pytest.raises(media.ChatMediaError, match="incomplete"):
        extract(raw, target)
    assert list(target.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "gofer-env") == ["model"]


@pytest.mark.parametrize(
    "entries",
    [
        [(model_entry("tokens.txt"), b"/etc/passwd", tarfile.SYMTYPE)],
        [(model_entry("tokens.txt"), b"/etc/passwd", tarfile.LNKTYPE)],
        [(model_entry("tokens.txt"), b"", tarfile.DIRTYPE)],
        [
            (model_entry("tokens.txt"), b"a", tarfile.REGTYPE),
            (model_entry("tokens.txt"), b"b", tarfile.REGTYPE),
        ],
    ],
    ids=["symlink", "hardlink", "directory", "repeated"],
)
def test_model_archive_rejects_links_and_repeated_files(tmp_path, one_file_model, entries):
    target = tmp_path / "model"
    target.mkdir()
    with pytest.raises(media.ChatMediaError, match="unsafe path"):
        extract(tar_bytes(entries), target)


@pytest.mark.parametrize(
    "budget",
    [
        "OFFLINE_MODEL_MAX_ENTRIES",
        "OFFLINE_MODEL_MAX_FILE_BYTES",
        "OFFLINE_MODEL_MAX_EXPANDED_BYTES",
    ],
)
def test_model_archive_enforces_extraction_budgets(tmp_path, monkeypatch, one_file_model, budget):
    monkeypatch.setattr(media, budget, 1)
    target = tmp_path / "model"
    target.mkdir()
    raw = tar_bytes(
        [("README.md", b"aa", tarfile.REGTYPE), (model_entry("tokens.txt"), b"bb", tarfile.REGTYPE)]
    )
    with pytest.raises(media.ChatMediaError):
        extract(raw, target)


def test_model_archive_skips_everything_but_the_model_files(tmp_path, one_file_model):
    target = tmp_path / "model"
    target.mkdir()
    raw = tar_bytes(
        [
            (model_entry("README.md"), b"readme", tarfile.REGTYPE),
            (model_entry("test_wavs/0.wav"), b"RIFF", tarfile.REGTYPE),
            (model_entry("tokens.txt"), b"<unk> 0", tarfile.REGTYPE),
        ]
    )
    extract(raw, target)
    assert [p.name for p in target.iterdir()] == ["tokens.txt"]
    assert (target / "tokens.txt").read_bytes() == b"<unk> 0"


@pytest.mark.parametrize("failure", ["hash", "layout", None])
def test_model_install_checks_digest_layout_and_promotes_atomically(tmp_path, monkeypatch, failure):
    content = b"model fixture"
    raw = tar_bytes([(model_entry("tokens.txt"), content, tarfile.REGTYPE)])
    monkeypatch.setattr(media, "urlopen", lambda *a, **kw: io.BytesIO(raw))
    monkeypatch.setattr(media, "OFFLINE_MODEL_FREE_BYTES", 0)
    monkeypatch.setattr(
        media,
        "OFFLINE_MODEL_SHA256",
        "bad" if failure == "hash" else hashlib.sha256(raw).hexdigest(),
    )
    monkeypatch.setattr(
        media,
        "OFFLINE_MODEL_FILES",
        {
            "tokens.txt": hashlib.sha256(content).hexdigest(),
            **({"missing": "bad"} if failure == "layout" else {}),
        },
    )
    target = tmp_path / media.OFFLINE_MODEL_NAME
    if failure:
        with pytest.raises(media.ChatMediaError):
            media._ensure_offline_model(target)
        assert not target.exists()
        assert [p for p in tmp_path.iterdir() if p.name != "gofer-env"] == []
    else:
        media._ensure_offline_model(target)
        assert (target / "tokens.txt").read_bytes() == content
        # The model's license asks that copies carry its notice.
        notice = (target / media.OFFLINE_MODEL_NOTICE_FILE).read_text()
        assert "OpenMDW License Agreement, version 1.1" in notice
        assert "nvidia/nemotron-3.5-asr-streaming-0.6b" in notice
        assert sorted(p.name for p in target.iterdir()) == ["NOTICE.txt", "tokens.txt"]
        monkeypatch.setattr(
            media, "urlopen", lambda *a, **kw: pytest.fail("cached model downloaded")
        )
        media._ensure_offline_model(target)
        (target / "tokens.txt").write_bytes(b"tampered")
        assert not media._valid_offline_model(target)


def test_model_install_refuses_to_start_without_disk_space(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "urlopen", lambda *a, **kw: pytest.fail("download started"))
    monkeypatch.setattr(
        media.shutil, "disk_usage", lambda _path: SimpleNamespace(free=300 * 1000 * 1000)
    )
    with pytest.raises(media.ChatMediaError, match=r"about 1\.2 GB of free disk space.*0\.3 GB"):
        media._ensure_offline_model(tmp_path / media.OFFLINE_MODEL_NAME)


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
    monkeypatch.setattr(
        media, "OFFLINE_MODEL_FILES", {"model": hashlib.sha256(b"known").hexdigest()}
    )
    assert media._valid_offline_model(folder)
    (folder / media.OFFLINE_MODEL_NOTICE_FILE).write_text("notice")
    assert media._valid_offline_model(folder)
    (folder / "extra.conf").write_text("injected resource")
    assert not media._valid_offline_model(folder)
    (folder / "extra.conf").unlink()
    (folder / "unknown").mkdir()
    assert not media._valid_offline_model(folder)
    (folder / "unknown").rmdir()
    (tmp_path / "alias").symlink_to(folder, target_is_directory=True)
    assert not media._valid_offline_model(tmp_path / "alias")
