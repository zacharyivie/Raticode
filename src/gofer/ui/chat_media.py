from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
import wave
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from gofer.utils.atomic_output import open_binary_input

CHAT_ATTACHMENT_MAX_COUNT = 5
CHAT_ATTACHMENT_MAX_FILE_BYTES = 20 * 1024 * 1024
CHAT_ATTACHMENT_MAX_TOTAL_BYTES = 40 * 1024 * 1024
CHAT_AUDIO_MAX_BYTES = 25 * 1024 * 1024
CHAT_AUDIO_CHUNK_MAX_BYTES = 1024 * 1024
CHAT_TRANSCRIPTION_MAX_SESSIONS = 8
CHAT_TRANSCRIPTION_SESSION_TTL_SECONDS = 15 * 60
VOSK_MODEL_NAME = "vosk-model-en-us-0.22-lgraph"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"
VOSK_MODEL_DOWNLOAD_MAX_BYTES = 160 * 1024 * 1024
# Pinned from the fixed upstream HTTPS archive on 2026-09-10. Review both the
# archive and member digests when intentionally changing the model version.
VOSK_MODEL_SHA256 = "d9838b4aaa82a75c4a17f5aca300eaca129aaab2a7cbf951bafbb500eb9c4334"
VOSK_MODEL_MAX_ENTRIES = 128
VOSK_MODEL_MAX_FILE_BYTES = 96 * 1024 * 1024
VOSK_MODEL_MAX_EXPANDED_BYTES = 320 * 1024 * 1024
VOSK_MODEL_MAX_COMPRESSION_RATIO = 100
VOSK_MODEL_FILES = {
    "ivector/splice.conf": "9f0c5f7c82d18eaf25d8bce470efa9f7741f88411fe428774bc0a9bb69a24756",
    "ivector/global_cmvn.stats": "3d7d721fa592c21136597955fd550b1f84568d32e4de7adc35d3b9a033e65afe",
    "ivector/final.mat": "29f411865e71494ff1ff03965b3a5812b5c38d3bcdff3e62aa80462978234f4f",
    "ivector/online_cmvn.conf": "a2f3571754b64297cb7efb2e7ca3df61995c5a45fcbb97188f90613552bb2dfe",
    "ivector/final.dubm": "76309d6d4f4612de0e6e2c1e836811becaa9a0adc0e0bfff53a90371488a413d",
    "ivector/final.ie": "ed35fa2f46d8853370f89480a53d3e0184ceaab9afef7f112b43cdda492e5326",
    "am/final.mdl": "c8586dfa7f571b8b01bac8217ef96493690c65aa512b72e6ce5acde39c262afe",
    "am/tree": "cc3f3ecb42b3e4513575e7c43c922eb1cf48657f46414350cdb48c165b51be2b",
    "graph/phones/word_boundary.int": (
        "9f63870f605d47e29a050117d255c4fc2cb1551d903883c0040aa16219856165"
    ),
    "graph/disambig_tid.int": "efbeb98e263f08d2de5477158854bd9e307a35600db4c58a935ccbaf4432e345",
    "graph/words.txt": "2714049587071344be482fa0e6b7b33792020ba689f422f9895dde5bedbd667b",
    "graph/Gr.fst": "0edf01f3582de257e89415cd508c21790bb6f0e820e743d87adcafd2bcbd9545",
    "graph/HCLr.fst": "96981cf4a3d5fe9e8f4a5205b681fadec0e85221972c929192b121a9f7e7c175",
    "graph/phones.txt": "6d01b4f94c7a161fc41a5573ace33f72d0c01b7b6e99cc7d14ffe9526d4fe4ca",
    "conf/model.conf": "f867cc746ce8633b747f723a8b78ea36a6e5db291e8c37bfefc5fb155420b10c",
    "conf/mfcc.conf": "df62f0c23a628b6a2c2e030d43b72ca992fcbe9f2d6d768946579ae25898570f",
    "README": "722534312e1a64c449a02aa487b231f018c234ea238f19cfe921fcfe19278df4",
}
_SAFE_PART = re.compile(r"[^A-Za-z0-9_.-]+")
_vosk_model: Any | None = None
_vosk_model_path: Path | None = None
_vosk_model_lock = threading.Lock()
_transcription_sessions: dict[str, _TranscriptionSession] = {}
_transcription_sessions_lock = threading.Lock()


@dataclass
class _TranscriptionSession:
    recognizer: Any
    committed: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    updated_at: float = field(default_factory=time.monotonic)


class ChatMediaError(ValueError):
    pass


def store_chat_attachments(
    payload: dict[str, Any],
    data_dir: Path,
) -> dict[str, list[dict[str, Any]]]:
    thread_id = _safe_identifier(payload.get("threadId"), "thread")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ChatMediaError("Choose at least one file to attach.")
    if len(raw_files) > CHAT_ATTACHMENT_MAX_COUNT:
        raise ChatMediaError(f"You can attach up to {CHAT_ATTACHMENT_MAX_COUNT} files.")

    decoded: list[tuple[str, str, bytes]] = []
    total = 0
    for item in raw_files:
        if not isinstance(item, dict):
            raise ChatMediaError("Each attachment must be a file object.")
        name = _safe_filename(item.get("name"))
        media_type = _safe_media_type(item.get("type"))
        try:
            content = base64.b64decode(str(item.get("data") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise ChatMediaError(f"{name} could not be decoded.") from exc
        if len(content) > CHAT_ATTACHMENT_MAX_FILE_BYTES:
            raise ChatMediaError(f"{name} is larger than 20 MB.")
        total += len(content)
        if total > CHAT_ATTACHMENT_MAX_TOTAL_BYTES:
            raise ChatMediaError("Attachments cannot exceed 40 MB in one message.")
        decoded.append((name, media_type, content))

    target_dir = attachment_thread_dir(data_dir, thread_id)
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    attachments: list[dict[str, Any]] = []
    for name, media_type, content in decoded:
        attachment_id = uuid.uuid4().hex
        storage_name = f"{attachment_id}-{name}"
        path = target_dir / storage_name
        path.write_bytes(content)
        if os.name != "nt":
            path.chmod(0o600)
        attachments.append(
            {
                "id": attachment_id,
                "name": name,
                "size": len(content),
                "type": media_type,
                "storageName": storage_name,
            }
        )
    return {"attachments": attachments}


def resolve_chat_attachment(
    attachment: dict[str, Any],
    *,
    data_dir: Path,
    thread_id: str,
) -> Path:
    storage_name = str(attachment.get("storageName") or "")
    if not re.fullmatch(r"[0-9a-f]{32}-[^/\\]+", storage_name):
        raise ChatMediaError("An attached file reference is invalid.")
    root = attachment_thread_dir(data_dir, _safe_identifier(thread_id, "thread")).resolve()
    path = (root / storage_name).resolve()
    if path.parent != root or not path.is_file():
        name = attachment.get("name", "file")
        raise ChatMediaError(f"Attached file is no longer available: {name}")
    return path


def attachment_thread_dir(data_dir: Path, thread_id: str) -> Path:
    return data_dir / "chat-attachments" / _safe_identifier(thread_id, "thread")


def transcribe_chat_audio(
    payload: dict[str, Any],
    *,
    data_dir: Path,
) -> dict[str, str]:
    try:
        audio = base64.b64decode(str(payload.get("data") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise ChatMediaError("The recording could not be decoded.") from exc
    if not audio:
        raise ChatMediaError("The recording is empty.")
    if len(audio) > CHAT_AUDIO_MAX_BYTES:
        raise ChatMediaError("The recording is larger than 25 MB.")
    try:
        text = _transcribe_wav_locally(audio, data_dir)
    except ChatMediaError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ChatMediaError(f"Local transcription failed: {exc}") from exc
    if not text:
        raise ChatMediaError(
            "No speech was detected. Test the microphone in Settings > Devices, "
            "then speak closer to the selected input."
        )
    return {"text": text}


def start_chat_transcription(*, data_dir: Path) -> dict[str, str]:
    session = _TranscriptionSession(recognizer=_new_vosk_recognizer(data_dir))
    session_id = uuid.uuid4().hex
    with _transcription_sessions_lock:
        _prune_transcription_sessions()
        if len(_transcription_sessions) >= CHAT_TRANSCRIPTION_MAX_SESSIONS:
            oldest_id = min(
                _transcription_sessions,
                key=lambda key: _transcription_sessions[key].updated_at,
            )
            _transcription_sessions.pop(oldest_id, None)
        _transcription_sessions[session_id] = session
    return {"sessionId": session_id}


def stream_chat_transcription(payload: dict[str, Any]) -> dict[str, str]:
    session = _get_transcription_session(payload)
    pcm = _decode_audio_payload(payload, maximum=CHAT_AUDIO_CHUNK_MAX_BYTES)
    if len(pcm) % 2:
        raise ChatMediaError("The transcription audio chunk is not valid 16-bit PCM.")
    with session.lock:
        if session.recognizer.AcceptWaveform(pcm):
            text = _vosk_result_text(session.recognizer.Result())
            if text:
                session.committed.append(text)
            partial = ""
        else:
            partial = _vosk_partial_text(session.recognizer.PartialResult())
        session.updated_at = time.monotonic()
        return {"text": _combined_transcription(session.committed, partial)}


def finish_chat_transcription(payload: dict[str, Any]) -> dict[str, str]:
    session_id = _transcription_session_id(payload)
    with _transcription_sessions_lock:
        session = _transcription_sessions.pop(session_id, None)
    if session is None:
        raise ChatMediaError("The transcription session expired. Start recording again.")
    with session.lock:
        final = _vosk_result_text(session.recognizer.FinalResult())
        if final:
            session.committed.append(final)
        text = _combined_transcription(session.committed)
    if not text:
        raise ChatMediaError(
            "No speech was detected. Test the microphone in Settings > Devices, "
            "then speak closer to the selected input."
        )
    return {"text": text}


def cancel_chat_transcription(payload: dict[str, Any]) -> dict[str, bool]:
    session_id = _transcription_session_id(payload)
    with _transcription_sessions_lock:
        removed = _transcription_sessions.pop(session_id, None) is not None
    return {"cancelled": removed}


def _transcribe_wav_locally(audio: bytes, data_dir: Path) -> str:
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ChatMediaError("The recording must be mono 16-bit PCM audio.")
            if source.getframerate() != 16_000 or source.getcomptype() != "NONE":
                raise ChatMediaError("The recording must be uncompressed 16 kHz audio.")
            pcm = source.readframes(source.getnframes())
    except (EOFError, wave.Error) as exc:
        raise ChatMediaError("The recording is not a valid WAV file.") from exc

    recognizer = _new_vosk_recognizer(data_dir)
    parts: list[str] = []
    for offset in range(0, len(pcm), 8_000):
        if recognizer.AcceptWaveform(pcm[offset : offset + 8_000]):
            parts.append(_vosk_result_text(recognizer.Result()))
    parts.append(_vosk_result_text(recognizer.FinalResult()))
    return " ".join(part for part in parts if part).strip()


def _new_vosk_recognizer(data_dir: Path) -> Any:
    try:
        from vosk import KaldiRecognizer, SetLogLevel
    except ImportError as exc:
        raise ChatMediaError(
            "Local transcription support is not installed. Reinstall Raticode with Vosk support."
        ) from exc
    SetLogLevel(-1)
    return KaldiRecognizer(_load_vosk_model(data_dir), 16_000)


def _load_vosk_model(data_dir: Path) -> Any:
    global _vosk_model, _vosk_model_path
    model_path = data_dir / "speech-models" / VOSK_MODEL_NAME
    with _vosk_model_lock:
        if _vosk_model is not None and _vosk_model_path == model_path:
            return _vosk_model
        _ensure_vosk_model(model_path)
        try:
            from vosk import Model
        except ImportError as exc:
            raise ChatMediaError(
                "Local transcription support is not installed. "
                "Reinstall Raticode with Vosk support."
            ) from exc
        _vosk_model = Model(str(model_path))
        _vosk_model_path = model_path
        return _vosk_model


def _valid_vosk_model(model_path: Path) -> bool:
    if model_path.is_symlink() or not model_path.is_dir():
        return False
    expected = set(VOSK_MODEL_FILES)
    allowed_directories = {
        parent.as_posix()
        for name in expected
        for parent in Path(name).parents
        if parent != Path(".")
    }
    found: set[str] = set()
    try:
        for directory, folders, files in os.walk(model_path, followlinks=False):
            for name in folders:
                folder = Path(directory) / name
                if (
                    folder.is_symlink()
                    or folder.relative_to(model_path).as_posix() not in allowed_directories
                ):
                    return False
            for name in files:
                file = Path(directory) / name
                relative = file.relative_to(model_path).as_posix()
                if relative not in expected or file.is_symlink():
                    return False
                found.add(relative)
        if found != expected:
            return False
        for relative, expected_digest in VOSK_MODEL_FILES.items():
            digest = hashlib.sha256()
            total = 0
            with open_binary_input(model_path / relative) as source:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > VOSK_MODEL_MAX_FILE_BYTES:
                        return False
                    digest.update(chunk)
            if digest.hexdigest() != expected_digest:
                return False
    except OSError:
        return False
    return True


def _ensure_vosk_model(model_path: Path) -> None:
    if _valid_vosk_model(model_path):
        return
    model_root = model_path.parent
    model_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        # A private staging directory prevents partial downloads or extraction
        # failures from becoming a model that the native loader can consume.
        with tempfile.TemporaryDirectory(prefix=".vosk-", dir=model_root) as staging:
            stage = Path(staging)
            archive_path = stage / "model.zip"
            request = Request(VOSK_MODEL_URL, headers={"User-Agent": "Raticode local speech/1"})
            with urlopen(request, timeout=60) as response, archive_path.open("xb") as target:
                _copy_limited(response, target, VOSK_MODEL_DOWNLOAD_MAX_BYTES)
            with archive_path.open("rb") as downloaded:
                digest = hashlib.file_digest(downloaded, "sha256").hexdigest()
            if digest != VOSK_MODEL_SHA256:
                raise ChatMediaError("The local speech model checksum does not match.")
            with zipfile.ZipFile(archive_path) as archive:
                _safe_extract_zip(archive, stage)
            extracted = stage / VOSK_MODEL_NAME
            if not _valid_vosk_model(extracted):
                raise ChatMediaError("The downloaded local speech model is incomplete.")
            if model_path.is_symlink():
                raise ChatMediaError("The local speech model path must not be a link.")
            if model_path.exists():
                shutil.rmtree(model_path)
            extracted.replace(model_path)
    except ChatMediaError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ChatMediaError(
            "The local speech model could not be installed. Check the connection and try again."
        ) from exc


def _copy_limited(source: Any, target: Any, limit: int) -> None:
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            limit_mib = limit // (1024 * 1024)
            raise ChatMediaError(f"The local speech model download exceeded {limit_mib} MiB.")
        target.write(chunk)


def _safe_extract_zip(archive: zipfile.ZipFile, target: Path) -> None:
    target_root = target.resolve()
    members = archive.infolist()
    if len(members) > VOSK_MODEL_MAX_ENTRIES:
        raise ChatMediaError("The local speech model archive has too many entries.")
    total = 0
    seen: set[str] = set()
    for member in members:
        name = member.filename
        destination = (target / name).resolve()
        kind = stat.S_IFMT(member.external_attr >> 16)
        if (
            member.orig_filename != name
            or "\\" in name
            or ":" in name
            or name.startswith("/")
            or ".." in Path(name).parts
            or destination == target_root
            or not destination.is_relative_to(target_root)
            or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
            or name in seen
        ):
            raise ChatMediaError("The local speech model archive contains an unsafe path.")
        seen.add(name)
        total += member.file_size
        if (
            member.file_size > VOSK_MODEL_MAX_FILE_BYTES
            or total > VOSK_MODEL_MAX_EXPANDED_BYTES
            or member.file_size > max(1, member.compress_size) * VOSK_MODEL_MAX_COMPRESSION_RATIO
        ):
            raise ChatMediaError("The local speech model archive exceeds extraction limits.")
    for member in members:
        destination = target / member.filename
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with archive.open(member) as source, destination.open("xb") as output:
            os.chmod(destination, 0o600)
            _copy_limited(source, output, min(member.file_size, VOSK_MODEL_MAX_FILE_BYTES))


def _vosk_result_text(value: str) -> str:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""


def _vosk_partial_text(value: str) -> str:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("partial") or "").strip() if isinstance(payload, dict) else ""


def _decode_audio_payload(payload: dict[str, Any], *, maximum: int) -> bytes:
    try:
        audio = base64.b64decode(str(payload.get("data") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise ChatMediaError("The recording could not be decoded.") from exc
    if not audio:
        raise ChatMediaError("The recording is empty.")
    if len(audio) > maximum:
        raise ChatMediaError("The transcription audio chunk is too large.")
    return audio


def _transcription_session_id(payload: dict[str, Any]) -> str:
    session_id = str(payload.get("sessionId") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise ChatMediaError("A valid transcription session is required.")
    return session_id


def _get_transcription_session(payload: dict[str, Any]) -> _TranscriptionSession:
    session_id = _transcription_session_id(payload)
    with _transcription_sessions_lock:
        _prune_transcription_sessions()
        session = _transcription_sessions.get(session_id)
    if session is None:
        raise ChatMediaError("The transcription session expired. Start recording again.")
    return session


def _prune_transcription_sessions() -> None:
    cutoff = time.monotonic() - CHAT_TRANSCRIPTION_SESSION_TTL_SECONDS
    expired = [
        session_id
        for session_id, session in _transcription_sessions.items()
        if session.updated_at < cutoff
    ]
    for session_id in expired:
        _transcription_sessions.pop(session_id, None)


def _combined_transcription(committed: list[str], partial: str = "") -> str:
    return " ".join(part for part in [*committed, partial] if part).strip()


def _safe_identifier(value: Any, label: str) -> str:
    result = _SAFE_PART.sub("_", str(value or "")).strip("._")[:160]
    if not result:
        raise ChatMediaError(f"A {label} id is required.")
    return result


def _safe_filename(value: Any) -> str:
    name = Path(str(value or "attachment").replace("\\", "/")).name
    name = _SAFE_PART.sub("_", name).strip(".")[:200]
    return name or "attachment"


def _safe_media_type(value: Any) -> str:
    media_type = str(value or "application/octet-stream").lower()
    if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type):
        return "application/octet-stream"
    return media_type[:255]
