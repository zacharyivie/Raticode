from __future__ import annotations

import base64
import io
import json
import sys
import wave
from types import SimpleNamespace

import pytest

from gofer.ui import chat_media
from gofer.ui.chat_media import (
    ChatMediaError,
    resolve_chat_attachment,
    store_chat_attachments,
    transcribe_chat_audio,
)


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
    class FakeRecognizer:
        def __init__(self) -> None:
            self.chunks = 0

        def AcceptWaveform(self, pcm) -> bool:
            assert pcm == b"\x01\x00\x02\x00"
            self.chunks += 1
            return self.chunks > 1

        def PartialResult(self) -> str:
            return json.dumps({"partial": "build the"})

        def Result(self) -> str:
            return json.dumps({"text": "build the workflow"})

        def FinalResult(self) -> str:
            return json.dumps({"text": "now"})

    chat_media._transcription_sessions.clear()
    monkeypatch.setattr(chat_media, "_new_vosk_recognizer", lambda _data_dir: FakeRecognizer())
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path)["sessionId"]
    chunk = {
        "data": base64.b64encode(b"\x01\x00\x02\x00").decode(),
        "sessionId": session_id,
    }

    assert chat_media.stream_chat_transcription(chunk) == {"text": "build the"}
    assert chat_media.stream_chat_transcription(chunk) == {"text": "build the workflow"}
    assert chat_media.finish_chat_transcription({"sessionId": session_id}) == {
        "text": "build the workflow now"
    }
    assert session_id not in chat_media._transcription_sessions


def test_local_vosk_recognizer_accepts_pcm_wav(monkeypatch, tmp_path) -> None:
    class FakeRecognizer:
        def __init__(self, model, sample_rate) -> None:
            assert model == "model"
            assert sample_rate == 16_000

        def AcceptWaveform(self, _data) -> bool:
            return False

        def Result(self) -> str:
            return json.dumps({"text": ""})

        def FinalResult(self) -> str:
            return json.dumps({"text": "local words"})

    monkeypatch.setitem(
        sys.modules,
        "vosk",
        SimpleNamespace(KaldiRecognizer=FakeRecognizer, SetLogLevel=lambda _level: None),
    )
    monkeypatch.setattr(chat_media, "_load_vosk_model", lambda _data_dir: "model")

    result = chat_media._transcribe_wav_locally(_wav_bytes(b"\x01\x00" * 1600), tmp_path)

    assert result == "local words"


def test_local_vosk_recognizer_rejects_compressed_audio(tmp_path) -> None:
    with pytest.raises(ChatMediaError, match="valid WAV"):
        chat_media._transcribe_wav_locally(b"webm audio", tmp_path)


def test_vosk_uses_desktop_sized_streaming_model() -> None:
    assert chat_media.VOSK_MODEL_NAME == "vosk-model-en-us-0.22-lgraph"
    assert chat_media.VOSK_MODEL_URL.endswith("/vosk-model-en-us-0.22-lgraph.zip")
    assert chat_media.VOSK_MODEL_DOWNLOAD_MAX_BYTES >= 128 * 1024 * 1024


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
