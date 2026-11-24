"""The offline speech model on a Mac: whose certificates its download trusts, the
download Settings > Devices starts with progress, and dictation never downloading it."""

from __future__ import annotations

import datetime
import hashlib
import io
import random
import socket
import ssl
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

from gofer.ui import chat_media, system_speech
from gofer.ui.chat_media import ChatMediaError


def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


# Certificates: the Mac's keychains first, then the bundled certifi list.


def _authority(name: str):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _server_files(tmp_path: Path, ca_key, ca_cert) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "server.pem", tmp_path / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _pem(cert) -> str:
    from cryptography.hazmat.primitives import serialization

    return str(cert.public_bytes(serialization.Encoding.PEM).decode())


def _handshake(context: ssl.SSLContext, cert_path: Path, key_path: Path) -> None:
    """One TLS handshake with a local server presenting cert_path; raises if it's refused."""
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve() -> None:
        conn, _ = listener.accept()
        try:
            with server.wrap_socket(conn, server_side=True):
                pass
        except (ssl.SSLError, OSError):
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname="localhost"):
                pass
    finally:
        listener.close()
        thread.join(timeout=5)


def no_system_cas(monkeypatch, tmp_path) -> None:
    missing = str(tmp_path / "Python.framework" / "etc" / "openssl")
    monkeypatch.setattr(
        chat_media.ssl,
        "get_default_verify_paths",
        lambda: chat_media.ssl.DefaultVerifyPaths(
            None, None, "SSL_CERT_FILE", f"{missing}/cert.pem", "SSL_CERT_DIR", f"{missing}/certs"
        ),
    )


class FakeSecurity:
    """Stands in for /usr/bin/security: one PEM listing per keychain."""

    def __init__(self, listings: dict[str, str] | None = None, *, fail: Exception | None = None):
        self.listings = listings or {}
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        assert kwargs["timeout"] == chat_media.MACOS_KEYCHAIN_TIMEOUT_SECONDS
        if self.fail is not None:
            raise self.fail
        return subprocess.CompletedProcess(command, 0, self.listings.get(command[-1], ""), "")


def test_a_company_root_in_the_mac_system_keychain_is_trusted(monkeypatch, tmp_path) -> None:
    """A network that inspects secure connections signs with its own root, which IT puts
    in the System keychain. certifi alone refuses it; the Mac's keychain accepts it."""
    ca_key, ca_cert = _authority("Example Corp Inspection Root")
    cert_path, key_path = _server_files(tmp_path, ca_key, ca_cert)
    no_system_cas(monkeypatch, tmp_path)

    bundled_only, _ = chat_media._download_tls_context(platform="linux")
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(bundled_only, cert_path, key_path)

    security = FakeSecurity({"/Library/Keychains/System.keychain": _pem(ca_cert)})
    mac, source = chat_media._download_tls_context(platform="darwin", run=security)
    _handshake(mac, cert_path, key_path)
    assert mac.verify_mode == ssl.CERT_REQUIRED and mac.check_hostname
    assert source == "macOS keychains (1 certificates), then bundled certifi"


def test_the_mac_reads_its_root_and_system_keychains_with_apples_tool(
    monkeypatch, tmp_path
) -> None:
    import certifi

    roots = Path(certifi.where()).read_text()
    blocks = chat_media._PEM_CERTIFICATE.findall(roots)
    security = FakeSecurity(
        {
            chat_media.MACOS_KEYCHAINS[0]: "\n".join(blocks[:3]),
            chat_media.MACOS_KEYCHAINS[1]: "noise\n" + blocks[3],
        }
    )
    no_system_cas(monkeypatch, tmp_path)
    context, source = chat_media._download_tls_context(platform="darwin", run=security)
    assert security.calls == [
        ["/usr/bin/security", "find-certificate", "-a", "-p", keychain]
        for keychain in (
            "/System/Library/Keychains/SystemRootCertificates.keychain",
            "/Library/Keychains/System.keychain",
        )
    ]
    # Keychain first, then the bundled list as a backstop; duplicates are harmless.
    assert source == "macOS keychains (4 certificates), then bundled certifi"
    assert context.cert_store_stats()["x509_ca"] >= len(blocks)


def test_a_broken_keychain_certificate_is_skipped_not_fatal(monkeypatch, tmp_path) -> None:
    _key, cert = _authority("Good Root")
    broken = "-----BEGIN CERTIFICATE-----\nnot base64 at all\n-----END CERTIFICATE-----"
    security = FakeSecurity({chat_media.MACOS_KEYCHAINS[1]: broken + "\n" + _pem(cert)})
    no_system_cas(monkeypatch, tmp_path)
    _context, source = chat_media._download_tls_context(platform="darwin", run=security)
    assert source == "macOS keychains (1 certificates, 1 unreadable), then bundled certifi"


@pytest.mark.parametrize(
    "security",
    [
        FakeSecurity(fail=FileNotFoundError("/usr/bin/security")),
        FakeSecurity(fail=subprocess.TimeoutExpired("security", 15)),
        FakeSecurity({}),
    ],
)
def test_without_keychain_certificates_the_mac_still_has_the_bundled_ones(
    monkeypatch, tmp_path, caplog, security
) -> None:
    no_system_cas(monkeypatch, tmp_path)
    context, source = chat_media._download_tls_context(platform="darwin", run=security)
    assert source.startswith("bundled certifi (") and source.endswith("cert.pem is missing)")
    assert context.cert_store_stats()["x509_ca"] > 0
    if security.fail is not None:
        assert "Couldn't read certificates from /System/Library/Keychains" in caplog.text


def test_other_systems_never_run_the_keychain_tool(monkeypatch, tmp_path) -> None:
    security = FakeSecurity()
    no_system_cas(monkeypatch, tmp_path)
    for platform in ("linux", "win32"):
        chat_media._download_tls_context(platform=platform, run=security)
    assert security.calls == []


# The download Settings > Devices starts, with progress; dictation never starts one.


class FakeResponse(io.BytesIO):
    def __init__(self, raw: bytes, *, gate: threading.Event | None = None) -> None:
        super().__init__(raw)
        self.headers = {"Content-Length": str(len(raw))}
        self.gate = gate

    def read(self, size: int | None = -1) -> bytes:
        if self.gate is not None and self.tell():
            assert self.gate.wait(5)
        return super().read(size)


@pytest.fixture
def small_model(monkeypatch):
    """A one-file model whose archive the fake server returns in 1 MiB reads."""
    content = random.Random(20261115).randbytes(3 * 1024 * 1024)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
        for name, body in (("README.md", b"readme"), ("tokens.txt", content)):
            member = tarfile.TarInfo(f"{chat_media.OFFLINE_MODEL_ARCHIVE_DIR}/{name}")
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    raw = buffer.getvalue()
    monkeypatch.setattr(chat_media, "OFFLINE_MODEL_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(
        chat_media, "OFFLINE_MODEL_FILES", {"tokens.txt": hashlib.sha256(content).hexdigest()}
    )
    monkeypatch.setattr(chat_media, "OFFLINE_MODEL_FREE_BYTES", 0)
    monkeypatch.setattr(chat_media, "_model_download", None)
    monkeypatch.setattr(chat_media, "_offline_engine", None)
    monkeypatch.setattr(chat_media, "_offline_engine_path", None)
    monkeypatch.setattr(chat_media, "_legacy_cleaned", set())
    monkeypatch.setattr(
        chat_media, "_download_tls_context", lambda: (ssl.create_default_context(), "test")
    )
    return raw


OFFLINE_STATUS = {
    "name": "Raticode's offline speech model",
    "installed": False,
    "downloadMb": 475,
    "model": "NVIDIA Nemotron 3.5 ASR Streaming 0.6B",
    "license": "OpenMDW License 1.1",
    "licenseUrl": "https://openmdw.ai/license/1-1/",
}


def test_settings_downloads_the_model_in_the_background_with_progress(
    monkeypatch, tmp_path, small_model, caplog
) -> None:
    caplog.set_level("INFO", logger="gofer.ui.chat_media")
    gate = threading.Event()
    requested: list[object] = []

    def serve(request, **kwargs):
        requested.append(request)
        return FakeResponse(small_model, gate=gate)

    monkeypatch.setattr(chat_media, "urlopen", serve)
    before = chat_media.offline_model_status(data_dir=tmp_path)
    assert before == OFFLINE_STATUS
    started = chat_media.start_offline_model_download(data_dir=tmp_path)
    assert started["download"]["state"] in {"starting", "installing", "downloading"}

    def downloading() -> bool:
        status = chat_media.offline_model_status(data_dir=tmp_path)["download"]
        return bool(status["state"] == "downloading" and status["receivedBytes"] > 0)

    wait_for(downloading)
    status = chat_media.offline_model_status(data_dir=tmp_path)["download"]
    assert status["totalBytes"] == len(small_model)
    assert 0 < status["receivedBytes"] < len(small_model)
    # Asking again while it runs doesn't start a second download.
    chat_media.start_offline_model_download(data_dir=tmp_path)
    # Dictation meanwhile says it's on its way instead of downloading it too.
    with pytest.raises(ChatMediaError, match=r"still downloading, \d+% done"):
        chat_media._load_offline_model(tmp_path)
    gate.set()
    wait_for(lambda: chat_media.offline_model_status(data_dir=tmp_path)["installed"])
    wait_for(lambda: "download" not in chat_media.offline_model_status(data_dir=tmp_path))
    assert len(requested) == 1
    assert "as asked in Settings > Devices" in caplog.text
    assert "Installed the local speech model" in caplog.text


def test_a_failed_download_shows_its_reason_until_it_is_tried_again(
    monkeypatch, tmp_path, small_model
) -> None:
    def refuse(request, **kwargs):
        raise chat_media.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))

    monkeypatch.setattr(chat_media, "urlopen", refuse)
    chat_media.start_offline_model_download(data_dir=tmp_path)
    wait_for(
        lambda: (
            chat_media.offline_model_status(data_dir=tmp_path).get("download", {}).get("state")
            == "failed"
        )
    )
    failed = chat_media.offline_model_status(data_dir=tmp_path)["download"]
    assert "security certificate couldn't be verified" in failed["error"]

    monkeypatch.setattr(chat_media, "urlopen", lambda request, **kw: FakeResponse(small_model))
    chat_media.start_offline_model_download(data_dir=tmp_path)
    # The model folder appears just before the job clears its "installing" state, so
    # wait for both rather than racing the background thread on a busy machine.
    wait_for(
        lambda: (
            (status := chat_media.offline_model_status(data_dir=tmp_path))["installed"]
            and "download" not in status
        )
    )


def test_dictation_never_downloads_the_model_itself(monkeypatch, tmp_path, small_model) -> None:
    monkeypatch.setattr(
        chat_media, "urlopen", lambda *a, **kw: pytest.fail("dictation started a download")
    )
    monkeypatch.setattr(
        chat_media.offline_speech,
        "import_sherpa_onnx",
        lambda: pytest.fail("sherpa-onnx loaded without a model"),
    )
    with pytest.raises(ChatMediaError) as raised:
        chat_media.start_chat_transcription(data_dir=tmp_path, engine="offline")
    assert str(raised.value) == (
        "Raticode's offline speech model isn't downloaded yet. Download it (about 475 MB) "
        "in Settings > Devices > Speech recognition."
    )
    # On a Mac without its helper, both reasons are given.
    real = system_speech.system_engine
    monkeypatch.setattr(system_speech, "system_engine", lambda: real("darwin", {}))
    with pytest.raises(ChatMediaError) as raised:
        chat_media.start_chat_transcription(data_dir=tmp_path)
    assert str(raised.value).startswith(
        "This copy of Raticode doesn't include its macOS speech helper. "
        "Raticode's offline speech model isn't downloaded yet."
    )
    # A damaged copy asks for a fresh download instead of fetching one.
    model = tmp_path / "speech-models" / chat_media.OFFLINE_MODEL_NAME
    model.mkdir(parents=True)
    (model / "tokens.txt").write_bytes(b"tampered")
    with pytest.raises(ChatMediaError, match="damaged or out of date. Download it again"):
        chat_media._load_offline_model(tmp_path)


def test_an_old_vosk_model_is_deleted_and_the_user_is_asked_to_download_the_new_one(
    monkeypatch, tmp_path, small_model, caplog
) -> None:
    caplog.set_level("INFO", logger="gofer.ui.chat_media")
    models = tmp_path / "speech-models"
    old = models / chat_media.LEGACY_VOSK_MODEL_NAME
    (old / "am").mkdir(parents=True)
    (old / "am" / "final.mdl").write_bytes(b"vosk")
    leftover = models / ".vosk-abc123"
    leftover.mkdir()
    (leftover / "model.zip").write_bytes(b"partial")
    # Something of the user's that a link inside the old model points to survives.
    keep = tmp_path / "keep.txt"
    keep.write_text("mine")
    (old / "link").symlink_to(keep)
    monkeypatch.setattr(
        chat_media, "urlopen", lambda *a, **kw: pytest.fail("nothing downloads by itself")
    )

    chat_media.clean_legacy_speech_models(tmp_path)

    assert not old.exists()
    assert not leftover.exists()
    assert keep.read_text() == "mine"
    assert "Removed the old speech model vosk-model-en-us-0.22-lgraph" in caplog.text
    # Settings says the model was replaced, and dictation says the same.
    assert chat_media.offline_model_status(data_dir=tmp_path) == {
        **OFFLINE_STATUS,
        "replaced": True,
    }
    with pytest.raises(ChatMediaError) as raised:
        chat_media.start_chat_transcription(data_dir=tmp_path, engine="offline")
    assert str(raised.value) == (
        "Raticode's offline speech model was replaced by a newer one, so the old "
        "model was removed. Download the new one (about 475 MB) in Settings > Devices > "
        "Speech recognition."
    )
    # Downloading the new model clears the message.
    monkeypatch.setattr(chat_media, "urlopen", lambda request, **kw: FakeResponse(small_model))
    chat_media.start_offline_model_download(data_dir=tmp_path)
    wait_for(
        lambda: (
            (status := chat_media.offline_model_status(data_dir=tmp_path))["installed"]
            and "download" not in status
        )
    )
    assert "replaced" not in chat_media.offline_model_status(data_dir=tmp_path)


def test_the_english_only_nemotron_model_is_removed_and_replaced(tmp_path, small_model) -> None:
    models = tmp_path / "speech-models"
    old = models / "nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25"
    old.mkdir(parents=True)
    (old / "encoder.int8.onnx").write_bytes(b"old")
    chat_media.clean_legacy_speech_models(tmp_path)
    assert not old.exists()
    assert chat_media.offline_model_status(data_dir=tmp_path)["replaced"] is True
    with pytest.raises(ChatMediaError, match="was replaced by a newer one"):
        chat_media.start_chat_transcription(data_dir=tmp_path, engine="offline")


def test_cleaning_up_the_old_model_never_follows_a_link(tmp_path, small_model) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious").write_text("keep")
    models = tmp_path / "speech-models"
    models.mkdir()
    (models / chat_media.LEGACY_VOSK_MODEL_NAME).symlink_to(elsewhere, target_is_directory=True)
    chat_media.clean_legacy_speech_models(tmp_path)
    assert (elsewhere / "precious").read_text() == "keep"
    assert not (models / chat_media.LEGACY_VOSK_MODEL_NAME).exists()
    # A speech-models folder that is itself a link is left alone.
    other = tmp_path / "other"
    (other / "speech-models" / chat_media.LEGACY_VOSK_MODEL_NAME).mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "speech-models").symlink_to(other / "speech-models", target_is_directory=True)
    chat_media.clean_legacy_speech_models(linked)
    assert (other / "speech-models" / chat_media.LEGACY_VOSK_MODEL_NAME).is_dir()


def test_an_interrupted_downloads_staging_folder_is_removed_but_not_a_fresh_one(
    tmp_path, small_model
) -> None:
    import os

    models = tmp_path / "speech-models"
    old_style = models / ".offline-abc123"
    stale = models / f"{chat_media.OFFLINE_STAGING_PREFIX}def456"
    fresh = models / f"{chat_media.OFFLINE_STAGING_PREFIX}running"
    for folder in (old_style, stale, fresh):
        folder.mkdir(parents=True)
        (folder / "model.tar.bz2").write_bytes(b"part")
    (models / chat_media.OFFLINE_MODEL_REPLACED_MARKER).write_text("vosk")
    long_ago = 1_000_000_000
    for folder in (old_style, stale):
        os.utime(folder, (long_ago, long_ago))
    chat_media.clean_legacy_speech_models(tmp_path)
    assert not old_style.exists() and not stale.exists()
    # A download may be unpacking into a young folder right now.
    assert fresh.is_dir()
    assert (models / chat_media.OFFLINE_MODEL_REPLACED_MARKER).is_file()


def test_without_an_old_model_nothing_says_replaced(tmp_path, small_model) -> None:
    chat_media.clean_legacy_speech_models(tmp_path)
    assert "replaced" not in chat_media.offline_model_status(data_dir=tmp_path)
    assert not (tmp_path / "speech-models").exists()


def test_the_download_reports_unknown_size_without_inventing_one() -> None:
    def sized(length: str) -> object:
        return type("Response", (), {"headers": {"Content-Length": length}})()

    assert chat_media._content_length(io.BytesIO(b"")) == 0
    assert chat_media._content_length(sized("x")) == 0
    too_large = chat_media.OFFLINE_MODEL_DOWNLOAD_MAX_BYTES + 1
    assert chat_media._content_length(sized(str(too_large))) == 0
    assert chat_media._content_length(sized("42")) == 42


# Test speech recognition: macOS asks for its permission before the first dictation.

FAKE_HELPER = r"""
import json, os, sys
mode = sys.argv[1]
def say(**event):
    print(json.dumps(event), flush=True)
if mode == "denied":
    say(type="error", code="denied", message="Speech recognition is off for Raticode.")
    sys.exit(3)
if mode == "abort":
    os.abort()
say(type="ready", language="en-US")
assert sys.stdin.buffer.read() == b""
say(type="done")
"""


def helper(mode: str, engine_id: str = "macos") -> system_speech.SystemEngine:
    command = (sys.executable, "-c", FAKE_HELPER, mode)
    return system_speech.SystemEngine(
        engine_id, "macOS speech recognition", command=command, check_command=command
    )


def test_trying_the_helper_gives_it_no_audio_and_reports_it_works(caplog) -> None:
    caplog.set_level("INFO", logger="gofer.ui.system_speech")
    engine = helper("works")
    system_speech._check_cache[engine.check_command] = (time.monotonic(), {"available": False})
    result = system_speech.try_system_engine(engine)
    assert result == {
        "id": "macos",
        "name": "macOS speech recognition",
        "ok": True,
        "message": "macOS speech recognition works (en-US). Speech is recognized on this Mac.",
    }
    # The next check asks the helper again instead of repeating the cached refusal.
    assert engine.check_command not in system_speech._check_cache
    assert "macOS speech recognition test passed (test exited with code 0)" in caplog.text


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("denied", "Speech recognition is off for Raticode."),
        ("abort", "macOS stopped Raticode's speech helper."),
    ],
)
def test_trying_the_helper_reports_a_refusal(caplog, mode, message) -> None:
    result = system_speech.try_system_engine(helper(mode))
    assert result["ok"] is False and result["message"].startswith(message)
    assert "macOS speech recognition test failed" in caplog.text


def test_only_the_macos_helper_is_tried(tmp_path) -> None:
    ran: list[object] = []
    windows = helper("works", engine_id="windows")
    assert system_speech.try_system_engine(windows, run=lambda *a, **k: ran.append(a))[
        "message"
    ] == ("Only macOS speech recognition needs a test.")
    missing = system_speech.system_engine("darwin", {})
    assert system_speech.try_system_engine(missing, run=lambda *a, **k: ran.append(a)) == {
        "id": "macos",
        "name": "macOS speech recognition",
        "ok": False,
        "message": "This copy of Raticode doesn't include its macOS speech helper.",
    }
    assert ran == []
