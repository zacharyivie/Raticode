from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gofer.ui import system_speech
from gofer.utils.atomic_output import (
    atomic_binary_output,
    open_binary_input,
    unlink_without_links,
)
from gofer.utils.logging import get_logger

log = get_logger(__name__)

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
VOSK_MODEL_DOWNLOAD_MB = 130
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
SYSTEM_ENGINE = "system"
OFFLINE_ENGINE = "offline"
OFFLINE_ENGINE_NAME = "Raticode's offline speech model"
OFFLINE_SWITCH = "You can download Raticode's offline speech model in Settings > Devices."
OFFLINE_MODEL_MISSING = (
    f"{OFFLINE_ENGINE_NAME} isn't downloaded yet. Download it (about "
    f"{VOSK_MODEL_DOWNLOAD_MB} MB) in Settings > Devices > Speech recognition."
)
OFFLINE_MODEL_DAMAGED = (
    f"{OFFLINE_ENGINE_NAME} is damaged or out of date. Download it again in "
    "Settings > Devices > Speech recognition."
)
# The Mac's own trust store: Apple's roots, and the System keychain, where an administrator
# or a device-management profile installs a company's root certificate. The frozen
# backend's OpenSSL can't see either, so they are read with Apple's `security` tool.
MACOS_SECURITY_TOOL = "/usr/bin/security"
MACOS_KEYCHAINS = (
    "/System/Library/Keychains/SystemRootCertificates.keychain",
    "/Library/Keychains/System.keychain",
)
MACOS_KEYCHAIN_TIMEOUT_SECONDS = 15
_PEM_CERTIFICATE = re.compile(
    r"-----BEGIN CERTIFICATE-----\s.+?-----END CERTIFICATE-----", re.DOTALL
)
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
    # The OS recognizer, when one is used; the offline Vosk recognizer takes over if it fails.
    speech: system_speech.SystemSpeechSession | None = None
    data_dir: Path | None = None
    engine: str = OFFLINE_ENGINE
    notice: str = ""
    # For the log only: how much audio arrived, never the audio or the words.
    started_at: float = field(default_factory=time.monotonic)
    audio_bytes: int = 0
    chunks: int = 0


class ChatMediaError(ValueError):
    pass


@dataclass
class _ModelDownload:
    """The offline model's download, started from Settings > Devices and run in the background."""

    model_path: Path
    # starting, downloading, installing (checksum, unpacking, checking), installed or failed.
    state: str = "starting"
    received: int = 0
    total: int = 0
    error: str = ""

    @property
    def active(self) -> bool:
        return self.state in {"starting", "downloading", "installing"}

    def view(self) -> dict[str, Any]:
        view: dict[str, Any] = {
            "state": self.state,
            "receivedBytes": self.received,
            "totalBytes": self.total,
        }
        if self.error:
            view["error"] = self.error
        return view


_model_download: _ModelDownload | None = None
_model_download_lock = threading.Lock()


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

    target_dir = attachment_thread_dir(data_dir.resolve(), thread_id)
    attachments: list[dict[str, Any]] = []
    created: list[Path] = []
    try:
        for name, media_type, content in decoded:
            attachment_id = uuid.uuid4().hex
            storage_name = f"{attachment_id}-{name}"
            path = target_dir / storage_name
            with atomic_binary_output(path, exclusive=True) as output:
                output.write(content)
            created.append(path)
            attachments.append(
                {
                    "id": attachment_id,
                    "name": name,
                    "size": len(content),
                    "type": media_type,
                    "storageName": storage_name,
                }
            )
    except OSError as exc:
        _remove_created_attachments(created)
        raise ChatMediaError("Attachments could not be saved safely. Retry the upload.") from exc
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
    root = attachment_thread_dir(data_dir.resolve(), _safe_identifier(thread_id, "thread"))
    path = root / storage_name
    try:
        with open_binary_input(path):
            pass
    except OSError as exc:
        name = attachment.get("name", "file")
        raise ChatMediaError(f"Attached file is no longer available: {name}") from exc
    return path


def locate_chat_attachment(storage_name: Any, *, data_dir: Path, thread_id: Any = None) -> Path:
    """Where an uploaded chat attachment is stored, so the window can preview it.

    Only names Raticode gave uploads (a random 32-hex id and a file name) are accepted,
    and only inside the chat attachment folders. A sent message may not say which
    thread uploaded it (a forked thread keeps the name), so without ``thread_id`` every
    thread's folder is checked; the random id makes the name unique.
    """
    name = str(storage_name or "")
    if not re.fullmatch(r"[0-9a-f]{32}-[^/\\]+", name):
        raise ChatMediaError("An attached file reference is invalid.")
    root = data_dir.resolve() / "chat-attachments"
    folders = [attachment_thread_dir(data_dir.resolve(), str(thread_id))] if thread_id else []
    if root.is_dir():
        folders += sorted(
            entry for entry in root.iterdir() if entry.is_dir() and not entry.is_symlink()
        )
    for folder in folders:
        path = folder / name
        try:
            with open_binary_input(path):
                return path
        except OSError:
            continue
    raise ChatMediaError("This attached file is no longer available.")


def copy_chat_attachments(payload: dict[str, Any], data_dir: Path) -> dict[str, bool]:
    """Give a fork its own attachment files, so deleting its source is harmless."""
    source_id = _safe_identifier(payload.get("sourceThreadId"), "thread")
    target_id = _safe_identifier(payload.get("threadId"), "thread")
    attachments = payload.get("attachments")
    if source_id == target_id or not isinstance(attachments, list):
        raise ChatMediaError("Choose a separate destination thread and its attachments.")
    if not 0 < len(attachments) <= CHAT_ATTACHMENT_MAX_COUNT:
        raise ChatMediaError(f"Copy up to {CHAT_ATTACHMENT_MAX_COUNT} attachments at a time.")
    paths = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ChatMediaError("Each attachment must be a file object.")
        paths.append(resolve_chat_attachment(attachment, data_dir=data_dir, thread_id=source_id))
    target = attachment_thread_dir(data_dir.resolve(), target_id)
    created: list[Path] = []
    try:
        for source in paths:
            with open_binary_input(source) as input_file:
                content = input_file.read(CHAT_ATTACHMENT_MAX_FILE_BYTES + 1)
            if len(content) > CHAT_ATTACHMENT_MAX_FILE_BYTES:
                raise ChatMediaError("An attachment is larger than 20 MB.")
            destination = target / source.name
            with atomic_binary_output(destination, exclusive=True) as output:
                output.write(content)
            created.append(destination)
    except ChatMediaError:
        _remove_created_attachments(created)
        raise
    except OSError as exc:
        _remove_created_attachments(created)
        raise ChatMediaError("Attachments could not be copied safely. Retry the fork.") from exc
    return {"copied": True}


def _remove_created_attachments(paths: list[Path]) -> None:
    for path in paths:
        try:
            unlink_without_links(path)
        except OSError:
            # A removed or replaced parent must not redirect rollback elsewhere.
            pass


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
        log.warning("Local transcription failed", exc_info=True)
        raise ChatMediaError(f"Local transcription failed: {exc}") from exc
    if not text:
        raise ChatMediaError(
            "No speech was detected. Test the microphone in Settings > Devices, "
            "then speak closer to the selected input."
        )
    return {"text": text}


def start_chat_transcription(
    *,
    data_dir: Path,
    engine: str = SYSTEM_ENGINE,
) -> dict[str, str]:
    """Starts a dictation with the OS recognizer, or Raticode's offline model.

    ``engine`` is the user's choice in Settings > Devices. Linux has no OS recognizer,
    and an OS without a usable one falls back to the offline model, saying why.
    """
    speech: system_speech.SystemSpeechSession | None = None
    notice = ""
    # Why the OS recognizer wasn't used, kept for the error if the offline model fails too.
    skipped = ""
    if engine != OFFLINE_ENGINE:
        found = system_speech.system_engine()
        if found.present:
            try:
                speech = system_speech.SystemSpeechSession(found)
            except (OSError, ValueError) as exc:
                skipped = f"{found.name} couldn't start: {exc}"
        elif found.id:
            skipped = found.reason
            log.warning("Dictation can't use %s: %s (%s)", found.name, found.reason, found.detail)
        if skipped:
            notice = _offline_notice(skipped)
    else:
        log.info("Dictation uses %s, as chosen in Settings", OFFLINE_ENGINE_NAME)
    if speech is not None:
        session = _TranscriptionSession(
            recognizer=None,
            speech=speech,
            data_dir=data_dir,
            engine=speech.engine.id,
        )
        name = speech.engine.name
    else:
        try:
            recognizer = _new_vosk_recognizer(data_dir)
        except ChatMediaError as exc:
            log.warning("Dictation couldn't start %s: %s", OFFLINE_ENGINE_NAME, exc)
            # Both recognizers failed; the first reason is usually the one to fix.
            raise ChatMediaError(f"{skipped.rstrip('.')}. {exc}" if skipped else str(exc)) from exc
        session = _TranscriptionSession(
            recognizer=recognizer,
            data_dir=data_dir,
            notice=notice,
        )
        name = OFFLINE_ENGINE_NAME
    session_id = uuid.uuid4().hex
    with _transcription_sessions_lock:
        _prune_transcription_sessions()
        if len(_transcription_sessions) >= CHAT_TRANSCRIPTION_MAX_SESSIONS:
            oldest_id = min(
                _transcription_sessions,
                key=lambda key: _transcription_sessions[key].updated_at,
            )
            _close_session(_transcription_sessions.pop(oldest_id, None))
        _transcription_sessions[session_id] = session
        open_sessions = len(_transcription_sessions)
    log.info("Dictation started with %s (%d open)", name, open_sessions)
    result = {"sessionId": session_id, "engine": session.engine, "engineName": name}
    if notice:
        result["notice"] = notice
    return result


def stream_chat_transcription(payload: dict[str, Any]) -> dict[str, str]:
    session = _get_transcription_session(payload)
    pcm = _decode_audio_payload(payload, maximum=CHAT_AUDIO_CHUNK_MAX_BYTES)
    if len(pcm) % 2:
        raise ChatMediaError("The transcription audio chunk is not valid 16-bit PCM.")
    with session.lock:
        session.updated_at = time.monotonic()
        session.audio_bytes += len(pcm)
        session.chunks += 1
        speech = session.speech
        if speech is not None and not speech.failure:
            speech.push(pcm)
            return {"text": speech.text()}
        switched = speech is not None
        if switched:
            _fall_back_to_offline(session)
        text = _accept_offline_audio(session, pcm)
        result = {"text": text}
        if switched:
            result.update({"engine": session.engine, "notice": session.notice})
        return result


def finish_chat_transcription(payload: dict[str, Any]) -> dict[str, str]:
    session_id = _transcription_session_id(payload)
    with _transcription_sessions_lock:
        session = _transcription_sessions.pop(session_id, None)
    if session is None:
        log.warning("Dictation finished after its session had expired or was replaced")
        raise ChatMediaError("The transcription session expired. Start recording again.")
    with session.lock:
        try:
            return _finish_session(session)
        except ChatMediaError as exc:
            log.warning("Dictation ended without words: %s (%s)", exc, _session_summary(session))
            raise


def _finish_session(session: _TranscriptionSession) -> dict[str, str]:
    speech = session.speech
    text = ""
    if speech is not None and not speech.failure:
        text = speech.finish()
        if speech.failure and text:
            # It stopped at the very end; the words it heard still count.
            session.notice = f"{speech.failure} The words heard before it stopped are kept."
            speech = None
        elif not speech.failure:
            speech = None
    if speech is not None:
        try:
            _fall_back_to_offline(session)
        except ChatMediaError as exc:
            # No offline model to carry on with: the words already heard still count.
            text = _combined_transcription(session.committed)
            if not text:
                raise
            session.notice = f"{exc} The words heard before it stopped are kept."
    if session.recognizer is not None:
        final = _vosk_result_text(session.recognizer.FinalResult())
        if final:
            session.committed.append(final)
        text = _combined_transcription(session.committed)
    if not text:
        raise ChatMediaError(
            "No speech was detected. Test the microphone in Settings > Devices, "
            "then speak closer to the selected input."
        )
    log.info("Dictation finished (%s)", _session_summary(session))
    result = {"text": text}
    if session.notice:
        result["notice"] = session.notice
    return result


def _session_summary(session: _TranscriptionSession) -> str:
    seconds = session.audio_bytes / 32_000
    return (
        f"{_engine_name(session)}, {seconds:.1f} s of audio in {session.chunks} chunks "
        f"over {time.monotonic() - session.started_at:.1f} s"
    )


def _engine_name(session: _TranscriptionSession) -> str:
    if session.speech is not None:
        return session.speech.engine.name
    return OFFLINE_ENGINE_NAME if session.recognizer is not None else "no recognizer"


def cancel_chat_transcription(payload: dict[str, Any]) -> dict[str, bool]:
    session_id = _transcription_session_id(payload)
    with _transcription_sessions_lock:
        session = _transcription_sessions.pop(session_id, None)
    if session is not None:
        log.info("Dictation cancelled (%s)", _session_summary(session))
    _close_session(session)
    return {"cancelled": session is not None}


def transcription_engines(*, data_dir: Path, refresh: bool = False) -> dict[str, Any]:
    """What Settings > Devices shows: the OS recognizer's state and the offline model's."""
    found = system_speech.system_engine()
    status = system_speech.check_system_engine(found, refresh=refresh)
    return {"system": status, "offline": offline_model_status(data_dir=data_dir)}


def offline_model_status(*, data_dir: Path) -> dict[str, Any]:
    """Whether the offline model is installed, and how its download is going if one ran."""
    status: dict[str, Any] = {
        "name": OFFLINE_ENGINE_NAME,
        "installed": _vosk_model_present(data_dir),
        "downloadMb": VOSK_MODEL_DOWNLOAD_MB,
    }
    job = _download_for(_vosk_model_dir(data_dir))
    if job is not None and (job.active or (job.state == "failed" and not status["installed"])):
        status["download"] = job.view()
    return status


def start_offline_model_download(*, data_dir: Path) -> dict[str, Any]:
    """Downloads and installs the offline model in the background; the user asked for it.

    Only one download runs at a time. Its progress is read with offline_model_status.
    """
    global _model_download
    model_path = _vosk_model_dir(data_dir)
    with _model_download_lock:
        current = _model_download
        if current is not None and current.active and current.model_path == model_path:
            busy = True
        else:
            busy = False
            job = _ModelDownload(model_path)
            _model_download = job
    if busy:
        return offline_model_status(data_dir=data_dir)
    log.info("Downloading %s, as asked in Settings > Devices", OFFLINE_ENGINE_NAME)
    threading.Thread(
        target=_run_model_download, args=(job,), name="offline-speech-model", daemon=True
    ).start()
    return offline_model_status(data_dir=data_dir)


def _run_model_download(job: _ModelDownload) -> None:
    def progress(state: str, received: int, total: int) -> None:
        with _model_download_lock:
            job.state, job.received, job.total = state, received, total

    try:
        _ensure_vosk_model(job.model_path, progress=progress)
    except ChatMediaError as exc:
        with _model_download_lock:
            job.state, job.error = "failed", str(exc)
        return
    except Exception:  # noqa: BLE001
        log.exception("The offline speech model download stopped unexpectedly")
        with _model_download_lock:
            job.state = "failed"
            job.error = "The local speech model could not be installed. Try again."
        return
    with _model_download_lock:
        job.state = "installed"


def _download_for(model_path: Path) -> _ModelDownload | None:
    with _model_download_lock:
        job = _model_download
        if job is None or job.model_path != model_path:
            return None
        return _ModelDownload(job.model_path, job.state, job.received, job.total, job.error)


def _vosk_model_dir(data_dir: Path) -> Path:
    # macOS data roots can pass through /var -> /private/var. Canonicalize
    # the trusted root, while preserving link checks below it.
    return data_dir.resolve() / "speech-models" / VOSK_MODEL_NAME


def _offline_notice(reason: str) -> str:
    return f"{reason.rstrip('.')}. Using {OFFLINE_ENGINE_NAME} instead."


def _accept_offline_audio(session: _TranscriptionSession, pcm: bytes) -> str:
    if session.recognizer.AcceptWaveform(pcm):
        text = _vosk_result_text(session.recognizer.Result())
        if text:
            session.committed.append(text)
        partial = ""
    else:
        partial = _vosk_partial_text(session.recognizer.PartialResult())
    return _combined_transcription(session.committed, partial)


def _fall_back_to_offline(session: _TranscriptionSession) -> None:
    """The OS recognizer failed: keep its words and carry on with the offline model.

    Audio recorded before the OS recognizer was ready is replayed, so nothing said is
    lost. The offline model is only used here when it is already installed; its
    download is too large to start in the middle of a dictation.
    """
    speech = session.speech
    if speech is None:
        return
    reason = speech.failure or f"{speech.engine.name} stopped."
    log.warning(
        "%s stopped during dictation (%s): %s",
        speech.engine.name,
        speech.failure_code or "no code",
        reason,
    )
    heard = speech.text()
    replay = speech.replay()
    speech.close()
    session.speech = None
    if heard:
        session.committed.append(heard)
    data_dir = session.data_dir
    if data_dir is None or not _vosk_model_present(data_dir):
        log.warning("%s isn't installed, so dictation can't carry on", OFFLINE_ENGINE_NAME)
        raise ChatMediaError(f"{reason} {OFFLINE_SWITCH}")
    try:
        session.recognizer = _new_vosk_recognizer(data_dir)
    except ChatMediaError as exc:
        log.warning("%s couldn't take over: %s", OFFLINE_ENGINE_NAME, exc)
        raise ChatMediaError(f"{reason} {exc}") from exc
    log.info(
        "%s took over, replaying %.1f s of audio heard before the first recognizer was ready",
        OFFLINE_ENGINE_NAME,
        sum(map(len, replay)) / 32_000,
    )
    session.engine = OFFLINE_ENGINE
    session.notice = _offline_notice(reason)
    for chunk in replay:
        _accept_offline_audio(session, chunk)


def _close_session(session: _TranscriptionSession | None) -> None:
    if session is not None and session.speech is not None:
        session.speech.close()


def _vosk_model_present(data_dir: Path) -> bool:
    """A cheap check that the offline model was installed; loading it verifies every file."""
    if _vosk_model is not None:
        return True
    try:
        model_path = _vosk_model_dir(data_dir)
        return model_path.is_dir() and all(
            (model_path / name).is_file() for name in VOSK_MODEL_FILES
        )
    except OSError:
        return False


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
        log.warning("The Vosk speech library can't be imported", exc_info=True)
        raise ChatMediaError(
            "Local transcription support is not installed. Reinstall Raticode with Vosk support."
        ) from exc
    SetLogLevel(-1)
    return KaldiRecognizer(_load_vosk_model(data_dir), 16_000)


def _load_vosk_model(data_dir: Path) -> Any:
    global _vosk_model, _vosk_model_path
    model_path = _vosk_model_dir(data_dir)
    with _vosk_model_lock:
        if _vosk_model is not None and _vosk_model_path == model_path:
            return _vosk_model
        _require_vosk_model(model_path)
        try:
            from vosk import Model
        except ImportError as exc:
            log.warning("The Vosk speech library can't be imported", exc_info=True)
            raise ChatMediaError(
                "Local transcription support is not installed. "
                "Reinstall Raticode with Vosk support."
            ) from exc
        started = time.monotonic()
        try:
            _vosk_model = Model(str(model_path))
        except Exception as exc:  # noqa: BLE001
            log.warning("The local speech model couldn't be loaded", exc_info=True)
            raise ChatMediaError(
                f"The local speech model couldn't be loaded ({type(exc).__name__})."
            ) from exc
        _vosk_model_path = model_path
        log.info("Loaded the local speech model in %.1f s", time.monotonic() - started)
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


def _require_vosk_model(model_path: Path) -> None:
    """The model must already be installed: a dictation never starts a 130 MB download.

    Settings > Devices downloads it, with progress (start_offline_model_download).
    """
    if _valid_vosk_model(model_path):
        return
    job = _download_for(model_path)
    if job is not None and job.active:
        share = f", {job.received * 100 // job.total}% done" if job.total else ""
        log.warning("Dictation can't use %s yet: it is still downloading", OFFLINE_ENGINE_NAME)
        raise ChatMediaError(
            f"{OFFLINE_ENGINE_NAME} is still downloading{share}. Try again when "
            "Settings > Devices says it's installed."
        )
    damaged = model_path.exists()
    log.warning(
        "Dictation can't use %s: it is %s",
        OFFLINE_ENGINE_NAME,
        "damaged or out of date" if damaged else "not downloaded",
    )
    raise ChatMediaError(OFFLINE_MODEL_DAMAGED if damaged else OFFLINE_MODEL_MISSING)


ModelProgress = Callable[[str, int, int], None]


def _ensure_vosk_model(model_path: Path, *, progress: ModelProgress | None = None) -> None:
    def report(state: str, received: int = 0, total: int = 0) -> None:
        if progress is not None:
            progress(state, received, total)

    report("installing")
    if _valid_vosk_model(model_path):
        return
    log.info(
        "The local speech model is %s; downloading it (about %d MB)",
        "damaged or out of date" if model_path.exists() else "not installed",
        VOSK_MODEL_DOWNLOAD_MB,
    )
    started = time.monotonic()
    model_root = model_path.parent
    model_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    context, ca_source = _download_tls_context()
    try:
        # A private staging directory prevents partial downloads or extraction
        # failures from becoming a model that the native loader can consume.
        with tempfile.TemporaryDirectory(prefix=".vosk-", dir=model_root) as staging:
            stage = Path(staging)
            archive_path = stage / "model.zip"
            request = Request(VOSK_MODEL_URL, headers={"User-Agent": "Raticode local speech/1"})
            with (
                urlopen(request, timeout=60, context=context) as response,
                archive_path.open("xb") as target,
            ):
                total = _content_length(response)
                report("downloading", 0, total)
                _copy_limited(
                    response,
                    target,
                    VOSK_MODEL_DOWNLOAD_MAX_BYTES,
                    progress=lambda received: report("downloading", received, total),
                )
            size = archive_path.stat().st_size
            report("installing", size, total or size)
            with archive_path.open("rb") as downloaded:
                digest = hashlib.file_digest(downloaded, "sha256").hexdigest()
            if digest != VOSK_MODEL_SHA256:
                log.warning(
                    "The downloaded speech model (%d bytes) has the wrong checksum, "
                    "so it was discarded",
                    size,
                )
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
    except ChatMediaError as exc:
        log.warning("The local speech model could not be installed: %s", exc)
        raise
    except Exception as exc:  # noqa: BLE001
        # Name the underlying error type explicitly: Python 3.11 tracebacks don't
        # print the type of a URLError's reason, and the Mac certificate diagnosis
        # depends on seeing it (for example SSLCertVerificationError).
        reason = getattr(exc, "reason", None)
        cause = reason if isinstance(reason, BaseException) else exc
        log.warning(
            "The local speech model could not be installed from %s "
            "(certificates: %s; error: %s: %s)",
            VOSK_MODEL_URL,
            ca_source,
            type(cause).__name__,
            cause,
            exc_info=True,
        )
        raise ChatMediaError(_download_failure_message(exc)) from exc
    log.info(
        "Installed the local speech model (%d bytes) in %.0f s",
        size,
        time.monotonic() - started,
    )


def _download_tls_context(
    platform: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[ssl.SSLContext, str]:
    """A verifying TLS context for the model download, and where its CAs came from.

    The frozen macOS backend's OpenSSL looks for certificates in the build machine's
    Python.framework folder, which a user's Mac doesn't have, so every certificate
    would fail to verify. On a Mac the context trusts the Mac's own keychains first,
    which also hold a company's root certificate when its network inspects secure
    connections, and then the certifi bundle shipped inside Raticode. Elsewhere certifi
    is used only when OpenSSL's own locations are empty. Verification is never turned
    off, and the downloaded archive must still match its pinned checksum.
    """
    platform = platform or sys.platform
    context = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    sources: list[str] = []
    if _has_system_cas(paths):
        sources.append(f"system ({paths.cafile or paths.capath})")
    if platform == "darwin":
        loaded = skipped = 0
        for certificate in _macos_keychain_certificates(run):
            try:
                context.load_verify_locations(cadata=certificate)
                loaded += 1
            except (ssl.SSLError, ValueError):
                skipped += 1
        if loaded:
            sources.append(
                f"macOS keychains ({loaded} certificates"
                + (f", {skipped} unreadable" if skipped else "")
                + ")"
            )
    if platform == "darwin" or not sources:
        try:
            import certifi

            bundle = certifi.where()
            context.load_verify_locations(cafile=bundle)
        except (ImportError, OSError, ssl.SSLError):
            log.warning(
                "The bundled CA certificates couldn't be loaded for downloads "
                "(OpenSSL looks in %s and %s)",
                paths.openssl_cafile,
                paths.openssl_capath,
                exc_info=True,
            )
        else:
            sources.append(
                "bundled certifi"
                if platform == "darwin" and sources
                else f"bundled certifi ({paths.openssl_cafile} is missing)"
            )
    return context, ", then ".join(sources) or "none found"


def _macos_keychain_certificates(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    """The certificates in the Mac's root and System keychains, as PEM blocks."""
    found: list[str] = []
    for keychain in MACOS_KEYCHAINS:
        try:
            completed = run(
                [MACOS_SECURITY_TOOL, "find-certificate", "-a", "-p", keychain],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MACOS_KEYCHAIN_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("Couldn't read certificates from %s: %s", keychain, exc)
            continue
        if completed.returncode != 0:
            log.warning(
                "Couldn't read certificates from %s (security exited with code %s)",
                keychain,
                completed.returncode,
            )
            continue
        found.extend(_PEM_CERTIFICATE.findall(completed.stdout or ""))
    return found


def _has_system_cas(paths: ssl.DefaultVerifyPaths) -> bool:
    if paths.cafile:
        return True
    try:
        return bool(paths.capath) and any(Path(paths.capath).iterdir())
    except OSError:
        return False


def _download_failure_message(exc: BaseException) -> str:
    """What went wrong with the model download, in words the user can act on."""
    cause: BaseException = exc
    if isinstance(exc, URLError) and not isinstance(exc, HTTPError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            cause = reason
    prefix = "The local speech model couldn't be downloaded"
    if isinstance(cause, ssl.SSLCertVerificationError):
        return (
            f"{prefix}: the download server's security certificate couldn't be verified. "
            "A proxy or filter that inspects secure connections can cause this; "
            "try another network."
        )
    if isinstance(exc, HTTPError):
        return f"{prefix}: the server answered HTTP {exc.code}. Try again later."
    if isinstance(cause, TimeoutError | socket.timeout):
        return f"{prefix}: the connection timed out. Check the connection and try again."
    if isinstance(exc, URLError) or isinstance(
        cause, ConnectionError | socket.gaierror | ssl.SSLError
    ):
        return f"{prefix}. Check the connection and try again."
    if isinstance(exc, OSError):
        detail = exc.strerror or type(exc).__name__
        return f"The local speech model couldn't be saved: {detail}."
    return "The local speech model could not be installed. Check the connection and try again."


def _copy_limited(
    source: Any, target: Any, limit: int, *, progress: Callable[[int], None] | None = None
) -> None:
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            limit_mib = limit // (1024 * 1024)
            raise ChatMediaError(f"The local speech model download exceeded {limit_mib} MiB.")
        target.write(chunk)
        if progress is not None:
            progress(total)


def _content_length(response: Any) -> int:
    """The download's announced size, for its progress bar; 0 when the server doesn't say."""
    headers = getattr(response, "headers", None)
    try:
        value = int(headers.get("Content-Length") or 0) if headers is not None else 0
    except (TypeError, ValueError):
        return 0
    return value if 0 < value <= VOSK_MODEL_DOWNLOAD_MAX_BYTES else 0


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
        _close_session(_transcription_sessions.pop(session_id, None))


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
