from __future__ import annotations

import base64
import io
import wave
from pathlib import Path

import pytest

from gofer.ui import chat_media
from gofer.ui.chat_media import (
    ChatMediaError,
    locate_chat_attachment,
    resolve_chat_attachment,
    store_chat_attachments,
    transcribe_chat_audio,
)
from tests.helpers.fake_speech import FakeRecognizerEngine


def test_store_chat_attachments_preserves_binary_files_and_confines_paths(tmp_path) -> None:
    payload = store_chat_attachments(
        {
            "threadId": "thread/../one",
            "files": [
                {
                    "name": "../screen.png",
                    "type": "image/png",
                    "data": base64.b64encode(b"\x89PNG\x00binary").decode(),
                }
            ],
        },
        tmp_path,
    )
    attachment = payload["attachments"][0]
    path = resolve_chat_attachment(
        attachment,
        data_dir=tmp_path,
        thread_id="thread/../one",
    )

    assert path.read_bytes() == b"\x89PNG\x00binary"
    assert path.is_relative_to(tmp_path / "chat-attachments")
    assert attachment["name"] == "screen.png"
    assert attachment["type"] == "image/png"


def test_transcribe_chat_audio_runs_local_recognizer(monkeypatch, tmp_path) -> None:
    wav = _wav_bytes(b"\x01\x00" * 1600)
    captured: dict[str, object] = {}

    def fake_local(audio, data_dir):
        captured.update(audio=audio, data_dir=data_dir)
        return "build the workflow"

    monkeypatch.setattr(chat_media, "_transcribe_wav_locally", fake_local)
    result = transcribe_chat_audio(
        {"data": base64.b64encode(wav).decode()},
        data_dir=tmp_path,
    )

    assert result == {"text": "build the workflow"}
    assert captured == {"audio": wav, "data_dir": tmp_path}


def test_transcribe_chat_audio_points_silent_input_to_device_settings(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(chat_media, "_transcribe_wav_locally", lambda _audio, _data_dir: "")

    with pytest.raises(ChatMediaError, match="Settings > Devices"):
        transcribe_chat_audio(
            {"data": base64.b64encode(_wav_bytes(b"\x00\x00" * 1600)).decode()},
            data_dir=tmp_path,
        )


def test_streaming_transcription_returns_partial_and_final_text(monkeypatch, tmp_path) -> None:
    engine = FakeRecognizerEngine(
        script=[("Build the", False), ("Build the workflow", True)], final="Now."
    )
    chat_media._transcription_sessions.clear()
    monkeypatch.setattr(chat_media, "_load_offline_model", lambda _data_dir: engine)
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path, engine="offline")[
        "sessionId"
    ]
    chunk = {
        "data": base64.b64encode(b"\x01\x00\x02\x00").decode(),
        "sessionId": session_id,
    }

    # A partial, then a phrase the model finished after a long pause, then the end.
    assert chat_media.stream_chat_transcription(chunk) == {"text": "Build the"}
    assert chat_media.stream_chat_transcription(chunk) == {"text": "Build the workflow"}
    assert chat_media.finish_chat_transcription({"sessionId": session_id}) == {
        "text": "Build the workflow Now."
    }
    assert session_id not in chat_media._transcription_sessions
    # Half a second of silence before the first audio and after the reset, and 1.5 s
    # at the end, so the model doesn't drop first or last words.
    assert engine.calls == [
        "create_stream",
        "silence:8000",
        "audio:2",
        "audio:2",
        "reset",
        "silence:8000",
        "silence:24000",
        "input_finished",
    ]
    # 16-bit PCM arrives as floats in [-1, 1].
    assert engine.fake.streams[0].samples[1] == [1 / 32768, 2 / 32768]


def test_the_offline_model_only_revises_its_partial_never_finished_phrases(
    monkeypatch, tmp_path
) -> None:
    """The voice tidy only rewrites what follows text it already kept, so finished
    phrases must stay as they were while the partial changes."""
    engine = FakeRecognizerEngine(
        script=[("So", False), ("So we", False), ("So we ship it", True), ("Then", False)],
        final="Then test it",
    )
    chat_media._transcription_sessions.clear()
    monkeypatch.setattr(chat_media, "_load_offline_model", lambda _data_dir: engine)
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path, engine="offline")[
        "sessionId"
    ]
    chunk = {"data": base64.b64encode(b"\x05\x00" * 4000).decode(), "sessionId": session_id}
    shown = [chat_media.stream_chat_transcription(chunk)["text"] for _ in range(4)]
    assert shown == ["So", "So we", "So we ship it", "So we ship it Then"]
    assert all(later.startswith(earlier) for earlier, later in zip(shown, shown[1:], strict=False))
    final = chat_media.finish_chat_transcription({"sessionId": session_id})["text"]
    assert final == "So we ship it Then test it"


def test_local_offline_recognizer_accepts_pcm_wav(monkeypatch, tmp_path) -> None:
    engine = FakeRecognizerEngine(script=[("Local", False)], final="Local words.")
    monkeypatch.setattr(chat_media, "_load_offline_model", lambda _data_dir: engine)

    result = chat_media._transcribe_wav_locally(_wav_bytes(b"\x01\x00" * 1600), tmp_path)

    assert result == "Local words."


def test_local_offline_recognizer_rejects_compressed_audio(tmp_path) -> None:
    with pytest.raises(ChatMediaError, match="valid WAV"):
        chat_media._transcribe_wav_locally(b"webm audio", tmp_path)


def test_the_offline_model_is_nemotron_3_5_streaming_from_a_pinned_archive() -> None:
    assert chat_media.OFFLINE_MODEL_NAME == (
        "nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"
    )
    assert chat_media.OFFLINE_MODEL_URL == (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2"
    )
    assert (
        chat_media.OFFLINE_MODEL_DOWNLOAD_BYTES
        < chat_media.OFFLINE_MODEL_DOWNLOAD_MAX_BYTES
        < 2 * chat_media.OFFLINE_MODEL_DOWNLOAD_BYTES
    )
    assert round(chat_media.OFFLINE_MODEL_DOWNLOAD_BYTES / 1e6) == (
        chat_media.OFFLINE_MODEL_DOWNLOAD_MB
    )
    assert set(chat_media.OFFLINE_MODEL_FILES) == {
        "encoder.int8.onnx",
        "decoder.int8.onnx",
        "joiner.int8.onnx",
        "tokens.txt",
    }
    # OpenMDW-1.1 asks that copies keep the license and the notices of origin.
    notice = chat_media.OFFLINE_MODEL_NOTICE
    assert "OpenMDW License Agreement, version 1.1" in notice
    assert "https://openmdw.ai/license/1-1/" in notice
    assert "NVIDIA" in notice and "sherpa-onnx" in notice
    assert "NVIDIA Open Model License" not in notice
    # The English-only model local dev builds used is cleaned up like Vosk's.
    assert "nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25" in (
        chat_media.LEGACY_MODEL_NAMES
    )


def test_model_download_limit_error_reports_configured_size() -> None:
    source = io.BytesIO(b"0" * (1024 * 1024 + 1))
    target = io.BytesIO()

    with pytest.raises(ChatMediaError, match="exceeded 1 MiB"):
        chat_media._copy_limited(source, target, 1024 * 1024)


def _wav_bytes(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(pcm)
    return output.getvalue()


def test_offline_model_canonicalizes_data_root_alias(monkeypatch, tmp_path):
    real = tmp_path / "private" / "var"
    real.mkdir(parents=True)
    alias = tmp_path / "var"
    alias.symlink_to(real, target_is_directory=True)
    paths: list[Path] = []
    monkeypatch.setattr(chat_media, "_offline_engine", None)
    monkeypatch.setattr(chat_media, "_offline_engine_path", None)
    monkeypatch.setattr(chat_media, "_require_offline_model", paths.append)
    monkeypatch.setattr(chat_media.offline_speech, "import_sherpa_onnx", lambda: "sherpa")
    monkeypatch.setattr(
        chat_media.offline_speech, "new_engine", lambda path, sherpa: (path, sherpa)
    )
    expected = real / "speech-models" / chat_media.OFFLINE_MODEL_NAME
    assert chat_media._load_offline_model(alias) == (expected, "sherpa")
    assert paths == [expected]
    # Loaded once and shared by later dictations.
    assert chat_media._load_offline_model(alias) == (expected, "sherpa")
    assert paths == [expected]


def test_fork_copies_attachments_independently(tmp_path) -> None:
    import shutil

    from gofer.ui.chat_media import copy_chat_attachments, resolve_chat_attachment

    attachments = store_chat_attachments(
        {
            "threadId": "original",
            "files": [
                {
                    "name": "evidence.txt",
                    "type": "text/plain",
                    "data": base64.b64encode(b"original evidence").decode(),
                }
            ],
        },
        tmp_path,
    )["attachments"]
    copy_chat_attachments(
        {"sourceThreadId": "original", "threadId": "fork", "attachments": attachments}, tmp_path
    )
    shutil.rmtree(tmp_path / "chat-attachments" / "original")
    copied = resolve_chat_attachment(attachments[0], data_dir=tmp_path, thread_id="fork")
    assert copied.read_bytes() == b"original evidence"


def test_fork_attachment_copy_rejects_traversal_and_missing_sources(tmp_path) -> None:
    from gofer.ui.chat_media import copy_chat_attachments

    for source, target, name in [
        ("../outside", "fork", "a" * 32 + "-test"),
        ("original", "../outside", "a" * 32 + "-test"),
        ("original", "fork", "../../secret"),
        ("original", "fork", "a" * 32 + "-missing"),
    ]:
        with pytest.raises(ChatMediaError):
            copy_chat_attachments(
                {
                    "sourceThreadId": source,
                    "threadId": target,
                    "attachments": [{"storageName": name}],
                },
                tmp_path,
            )


def test_locate_chat_attachment_finds_uploads_and_refuses_anything_else(tmp_path) -> None:
    note = {"name": "notes.txt", "type": "text/plain", "data": base64.b64encode(b"hi").decode()}
    stored = store_chat_attachments({"threadId": "thread-one", "files": [note]}, tmp_path)
    name = stored["attachments"][0]["storageName"]
    expected = (tmp_path / "chat-attachments" / "thread-one" / name).resolve()
    # With or without the thread that uploaded it (a forked thread may not know it).
    assert locate_chat_attachment(name, data_dir=tmp_path, thread_id="thread-one") == expected
    assert locate_chat_attachment(name, data_dir=tmp_path) == expected
    assert locate_chat_attachment(name, data_dir=tmp_path, thread_id="other") == expected
    for bad in ("", "notes.txt", "../" + name, name + "/x", "0" * 32 + "-a\\b"):
        with pytest.raises(ChatMediaError, match="invalid"):
            locate_chat_attachment(bad, data_dir=tmp_path)
    missing = "f" * 32 + "-gone.png"
    with pytest.raises(ChatMediaError, match="no longer available"):
        locate_chat_attachment(missing, data_dir=tmp_path)
    # A link planted under an upload's name is not followed.
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    linked = "e" * 32 + "-secret.txt"
    (tmp_path / "chat-attachments" / "thread-one" / linked).symlink_to(outside)
    with pytest.raises(ChatMediaError, match="no longer available"):
        locate_chat_attachment(linked, data_dir=tmp_path)


# The offline model's download: certificates and errors the user can act on.


def no_system_cas(monkeypatch, tmp_path) -> None:
    missing = str(tmp_path / "Python.framework" / "etc" / "openssl")
    monkeypatch.setattr(
        chat_media.ssl,
        "get_default_verify_paths",
        lambda: chat_media.ssl.DefaultVerifyPaths(
            None, None, "SSL_CERT_FILE", f"{missing}/cert.pem", "SSL_CERT_DIR", f"{missing}/certs"
        ),
    )


def test_a_frozen_backend_without_system_certificates_uses_the_bundled_ones(
    monkeypatch, tmp_path
) -> None:
    import certifi

    loaded: list[str] = []
    real = chat_media.ssl.SSLContext.load_verify_locations

    def load(self, cafile=None, *args, **kwargs):
        loaded.append(cafile)
        return real(self, cafile, *args, **kwargs)

    monkeypatch.setattr(chat_media.ssl.SSLContext, "load_verify_locations", load)
    no_system_cas(monkeypatch, tmp_path)
    made, source = chat_media._download_tls_context()
    assert loaded == [certifi.where()]
    assert made.verify_mode == chat_media.ssl.CERT_REQUIRED and made.check_hostname
    assert made.cert_store_stats()["x509_ca"] > 0
    assert source.startswith("bundled certifi")


def test_system_certificates_are_kept_when_present(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "cert.pem"
    bundle.write_text("")
    monkeypatch.setattr(
        chat_media.ssl,
        "get_default_verify_paths",
        lambda: chat_media.ssl.DefaultVerifyPaths(str(bundle), None, "", "", "", ""),
    )
    _context, source = chat_media._download_tls_context()
    assert source == f"system ({bundle})"


def test_the_download_passes_its_context_and_names_a_certificate_failure(
    monkeypatch, tmp_path, caplog
) -> None:
    seen: dict[str, object] = {}

    def refuse(request, **kwargs):
        seen.update(kwargs)
        raise chat_media.URLError(
            chat_media.ssl.SSLCertVerificationError(1, "certificate verify failed")
        )

    no_system_cas(monkeypatch, tmp_path)
    monkeypatch.setattr(chat_media, "OFFLINE_MODEL_FREE_BYTES", 0)
    monkeypatch.setattr(chat_media, "urlopen", refuse)
    with pytest.raises(ChatMediaError) as raised:
        chat_media._ensure_offline_model(tmp_path / "models" / chat_media.OFFLINE_MODEL_NAME)
    assert isinstance(seen["context"], chat_media.ssl.SSLContext)
    assert "security certificate couldn't be verified" in str(raised.value)
    assert "certificates: bundled certifi" in caplog.text
    assert "SSLCertVerificationError" in caplog.text


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (chat_media.URLError(TimeoutError("timed out")), "the connection timed out"),
        (TimeoutError("timed out"), "the connection timed out"),
        (
            chat_media.HTTPError(chat_media.OFFLINE_MODEL_URL, 503, "busy", {}, None),  # type: ignore[arg-type]
            "the server answered HTTP 503",
        ),
        (chat_media.URLError(OSError(8, "nodename nor servname")), "Check the connection"),
        (OSError(28, "No space left on device"), "couldn't be saved: No space left on device"),
        (RuntimeError("boom"), "could not be installed. Check the connection"),
    ],
)
def test_download_failures_say_what_happened(error, expected) -> None:
    assert expected in chat_media._download_failure_message(error)
