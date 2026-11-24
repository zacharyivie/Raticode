from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gofer.ui import offline_speech, system_speech
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
# NVIDIA's Nemotron 3.5 ASR Streaming 0.6B (int8, 560 ms chunks), as exported for
# sherpa-onnx. It is multilingual and detects the spoken language itself. Pinned from the
# fixed upstream HTTPS archive on 2026-11-16. Review the archive and file digests, and
# the model's license, when changing the model.
OFFLINE_MODEL_NAME = "nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"
OFFLINE_MODEL_ARCHIVE_DIR = f"sherpa-onnx-{OFFLINE_MODEL_NAME}"
OFFLINE_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"{OFFLINE_MODEL_ARCHIVE_DIR}.tar.bz2"
)
OFFLINE_MODEL_DOWNLOAD_BYTES = 475_271_763
OFFLINE_MODEL_DOWNLOAD_MAX_BYTES = 500 * 1024 * 1024
OFFLINE_MODEL_DOWNLOAD_MB = 475
OFFLINE_MODEL_SHA256 = "c6bf5e0df765f9d5b43bc9e0536d4b4b3e7d40bdf5ecf13e45f134c51c05ae3a"
OFFLINE_MODEL_MAX_ENTRIES = 32
OFFLINE_MODEL_MAX_FILE_BYTES = 700 * 1024 * 1024
OFFLINE_MODEL_MAX_EXPANDED_BYTES = 760 * 1024 * 1024
# The archive, the unpacked files (682 MB) and some room: about 1.16 GB at the peak.
OFFLINE_MODEL_FREE_BYTES = 1_250 * 1000 * 1000
# Only these files are unpacked from the archive; its README and sample audio are skipped.
OFFLINE_MODEL_FILES = {
    "encoder.int8.onnx": "012e9321373af99021415e0b0eb3ec827b4be3153be6f30d9b448fe65e896e68",
    "decoder.int8.onnx": "19f9c98fc6d0a2c33a65a43b36fdb2e914c26c0aa9764be3aebc502a1e982fb0",
    "joiner.int8.onnx": "4101c7c679a0bc30483794b27a059e34e79232aa2068d78d51231a22c8b0d7ce",
    "tokens.txt": "729cc103155bafa785f9cd45746cd41cabe97eab7182fc04d594129587958f8a",
}
# Written next to the model files. The upstream archive carries neither the license nor
# a notice of origin; OpenMDW-1.1 asks that distributed copies keep both.
OFFLINE_MODEL_NOTICE_FILE = "NOTICE.txt"
OFFLINE_MODEL_DISPLAY_NAME = "NVIDIA Nemotron 3.5 ASR Streaming 0.6B"
OFFLINE_MODEL_LICENSE = "OpenMDW License 1.1"
OFFLINE_MODEL_LICENSE_URL = "https://openmdw.ai/license/1-1/"
OFFLINE_MODEL_NOTICE = (
    "Nemotron 3.5 ASR Streaming 0.6B (nvidia/nemotron-3.5-asr-streaming-0.6b) by NVIDIA "
    "Corporation, int8 ONNX export by the sherpa-onnx project.\n"
    "https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b\n"
    "Licensed under the OpenMDW License Agreement, version 1.1 (OpenMDW-1.1)\n"
    f"{OFFLINE_MODEL_LICENSE_URL}\n"
)
# Models that earlier versions downloaded and this one no longer uses: Vosk (0.5.5 and
# earlier) and Nemotron Speech Streaming EN, which local dev builds used briefly. Their
# folders are deleted once, and a marker remembers to tell the user to download the new one.
LEGACY_VOSK_MODEL_NAME = "vosk-model-en-us-0.22-lgraph"
LEGACY_MODEL_NAMES = (
    LEGACY_VOSK_MODEL_NAME,
    "nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25",
)
LEGACY_VOSK_STAGING_PREFIX = ".vosk-"
# Where a download unpacks before it becomes the model. ".offline-" was the prefix before
# 0.6.0 went out; it is swept too, except the replaced-model marker below, a file.
OFFLINE_STAGING_PREFIX = ".offline-staging-"
_OLD_OFFLINE_STAGING_PREFIX = ".offline-"
# A staging folder younger than this may belong to a download that is running.
STALE_STAGING_SECONDS = 3600
OFFLINE_MODEL_REPLACED_MARKER = ".offline-model-replaced"
SYSTEM_ENGINE = "system"
OFFLINE_ENGINE = "offline"
OFFLINE_ENGINE_NAME = "Raticode's offline speech model"
OFFLINE_SWITCH = "You can download Raticode's offline speech model in Settings > Devices."
OFFLINE_MODEL_MISSING = (
    f"{OFFLINE_ENGINE_NAME} isn't downloaded yet. Download it (about "
    f"{OFFLINE_MODEL_DOWNLOAD_MB} MB) in Settings > Devices > Speech recognition."
)
OFFLINE_MODEL_REPLACED = (
    f"{OFFLINE_ENGINE_NAME} was replaced by a newer one, so the old model was "
    f"removed. Download the new one (about {OFFLINE_MODEL_DOWNLOAD_MB} MB) in "
    "Settings > Devices > Speech recognition."
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
_offline_engine: Any | None = None
_offline_engine_path: Path | None = None
_offline_engine_used = 0.0
_offline_engine_lock = threading.Lock()
_offline_engine_timer: threading.Timer | None = None
_legacy_cleaned: set[Path] = set()
_legacy_cleaned_lock = threading.Lock()
_transcription_sessions: dict[str, _TranscriptionSession] = {}
_transcription_sessions_lock = threading.Lock()


@dataclass
class _TranscriptionSession:
    recognizer: Any
    committed: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    updated_at: float = field(default_factory=time.monotonic)
    # The OS recognizer, when one is used; the offline model takes over if it fails.
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
    engine_chosen: bool = True,
) -> dict[str, str]:
    """Starts a dictation with the OS recognizer, or Raticode's offline model.

    ``engine`` is the user's choice in Settings > Devices; ``engine_chosen`` is False
    when they never chose. Linux has no OS recognizer, and an OS without a usable one
    falls back to the offline model, saying why. On Windows, unless the user chose the
    Windows recognizer, the offline model is used once it is downloaded: it is far more
    accurate than System.Speech, which stays for PCs without the download.
    """
    clean_legacy_speech_models(data_dir)
    speech: system_speech.SystemSpeechSession | None = None
    notice = ""
    # Why the OS recognizer wasn't used, kept for the error if the offline model fails too.
    skipped = ""
    if engine != OFFLINE_ENGINE:
        found = system_speech.system_engine()
        if prefers_offline_model(found.id, engine_chosen=engine_chosen, data_dir=data_dir):
            log.info(
                "Dictation uses %s instead of %s, because it is downloaded",
                OFFLINE_ENGINE_NAME,
                found.name,
            )
        elif found.present:
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
            recognizer = _new_offline_recognizer(data_dir)
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
        finally:
            if session.recognizer is not None:
                session.recognizer = None
                _schedule_offline_unload()


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
        final = session.recognizer.finish()
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


def transcription_engines(
    *,
    data_dir: Path,
    refresh: bool = False,
    preference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What Settings > Devices shows: the OS recognizer's state and the offline model's.

    With the user's ``preference`` ({"engine", "chosen"}), ``engine`` is the recognizer
    dictation will start with, so Settings shows the offline model on a Windows PC that
    has downloaded it and never chose the Windows recognizer.
    """
    clean_legacy_speech_models(data_dir)
    found = system_speech.system_engine()
    status = system_speech.check_system_engine(found, refresh=refresh)
    result: dict[str, Any] = {"system": status, "offline": offline_model_status(data_dir=data_dir)}
    if preference is not None:
        engine = str(preference.get("engine") or SYSTEM_ENGINE)
        chosen = bool(preference.get("chosen"))
        if engine != OFFLINE_ENGINE and prefers_offline_model(
            found.id, engine_chosen=chosen, data_dir=data_dir
        ):
            engine = OFFLINE_ENGINE
        result["engine"] = engine
    return result


def prefers_offline_model(system_id: str, *, engine_chosen: bool, data_dir: Path) -> bool:
    """Windows uses the downloaded offline model unless the user chose the Windows one."""
    return system_id == "windows" and not engine_chosen and _offline_model_present(data_dir)


def offline_model_status(*, data_dir: Path) -> dict[str, Any]:
    """Whether the offline model is installed, and how its download is going if one ran."""
    installed = _offline_model_present(data_dir)
    status: dict[str, Any] = {
        "name": OFFLINE_ENGINE_NAME,
        "installed": installed,
        "downloadMb": OFFLINE_MODEL_DOWNLOAD_MB,
        "model": OFFLINE_MODEL_DISPLAY_NAME,
        "license": OFFLINE_MODEL_LICENSE,
        "licenseUrl": OFFLINE_MODEL_LICENSE_URL,
    }
    if not installed and _legacy_model_replaced(data_dir):
        status["replaced"] = True
    job = _download_for(_offline_model_dir(data_dir))
    if job is not None and (job.active or (job.state == "failed" and not installed)):
        status["download"] = job.view()
    return status


def start_offline_model_download(*, data_dir: Path) -> dict[str, Any]:
    """Downloads and installs the offline model in the background; the user asked for it.

    Only one download runs at a time. Its progress is read with offline_model_status.
    """
    global _model_download
    clean_legacy_speech_models(data_dir)
    model_path = _offline_model_dir(data_dir)
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
        _ensure_offline_model(job.model_path, progress=progress)
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


def _offline_model_dir(data_dir: Path) -> Path:
    # macOS data roots can pass through /var -> /private/var. Canonicalize
    # the trusted root, while preserving link checks below it.
    return data_dir.resolve() / "speech-models" / OFFLINE_MODEL_NAME


def clean_legacy_speech_models(data_dir: Path) -> None:
    """Deletes speech models earlier versions downloaded, once per data folder.

    Raticode no longer uses Vosk (about 215 MB) or the English-only Nemotron model
    (about 662 MB), so their folders can't be used. A
    marker remembers that it was there, so Settings and dictation can say the model was
    replaced instead of only "not downloaded". Nothing new is downloaded. Links are never
    followed: a link in its place is removed as a link, and only plain folders inside
    Raticode's own speech-models folder are deleted.
    """
    try:
        root = data_dir.resolve() / "speech-models"
    except OSError:
        return
    with _legacy_cleaned_lock:
        if root in _legacy_cleaned:
            return
        _legacy_cleaned.add(root)
    try:
        if root.is_symlink() or not root.is_dir():
            return
        for name in LEGACY_MODEL_NAMES:
            legacy = root / name
            if legacy.is_symlink() or legacy.exists():
                _write_replaced_marker(root, name)
                _remove_without_following(legacy)
                log.info("Removed the old speech model %s; Raticode now uses a different one", name)
        for entry in root.iterdir():
            # Unpacking folders a Vosk download left behind when it was interrupted.
            if entry.name.startswith(LEGACY_VOSK_STAGING_PREFIX):
                _remove_without_following(entry)
            elif _stale_offline_staging(entry):
                # A killed or quit download leaves about 1.2 GB behind each time.
                _remove_without_following(entry)
    except OSError:
        log.warning("Couldn't remove the old Vosk speech model", exc_info=True)


def _stale_offline_staging(entry: Path) -> bool:
    if entry.name == OFFLINE_MODEL_REPLACED_MARKER or not entry.name.startswith(
        _OLD_OFFLINE_STAGING_PREFIX
    ):
        return False
    try:
        info = entry.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and time.time() - info.st_mtime > STALE_STAGING_SECONDS


def _remove_without_following(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    # shutil.rmtree doesn't follow links inside the folder.
    shutil.rmtree(path)


def _write_replaced_marker(root: Path, name: str) -> None:
    marker = root / OFFLINE_MODEL_REPLACED_MARKER
    if marker.is_symlink():
        marker.unlink()
    if not marker.exists():
        with atomic_binary_output(marker) as output:
            output.write(f"{name}\n".encode())


def _legacy_model_replaced(data_dir: Path) -> bool:
    try:
        marker = data_dir.resolve() / "speech-models" / OFFLINE_MODEL_REPLACED_MARKER
        return marker.is_file() and not marker.is_symlink()
    except OSError:
        return False


def _forget_legacy_model(model_root: Path) -> None:
    """The new model is installed, so the "replaced" message is no longer needed."""
    marker = model_root / OFFLINE_MODEL_REPLACED_MARKER
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        log.warning("Couldn't remove %s", marker, exc_info=True)


def _offline_notice(reason: str) -> str:
    return f"{reason.rstrip('.')}. Using {OFFLINE_ENGINE_NAME} instead."


def _accept_offline_audio(session: _TranscriptionSession, pcm: bytes) -> str:
    finals, partial = session.recognizer.accept(pcm)
    session.committed.extend(text for text in finals if text)
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
    if data_dir is None or not _offline_model_present(data_dir):
        log.warning("%s isn't installed, so dictation can't carry on", OFFLINE_ENGINE_NAME)
        raise ChatMediaError(f"{reason} {OFFLINE_SWITCH}")
    try:
        session.recognizer = _new_offline_recognizer(data_dir)
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
    if session is not None and session.recognizer is not None:
        session.recognizer = None
        _schedule_offline_unload()


def _offline_model_present(data_dir: Path) -> bool:
    """A cheap check that the offline model was installed; loading it verifies every file."""
    try:
        model_path = _offline_model_dir(data_dir)
        if _offline_engine is not None and _offline_engine_path == model_path:
            return True
        return model_path.is_dir() and all(
            (model_path / name).is_file() for name in OFFLINE_MODEL_FILES
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

    clean_legacy_speech_models(data_dir)
    recognizer = _new_offline_recognizer(data_dir)
    parts: list[str] = []
    try:
        for offset in range(0, len(pcm), 8_000):
            finals, _partial = recognizer.accept(pcm[offset : offset + 8_000])
            parts.extend(finals)
        parts.append(recognizer.finish())
    finally:
        _schedule_offline_unload()
    return " ".join(part for part in parts if part).strip()


def _new_offline_recognizer(data_dir: Path) -> offline_speech.OfflineRecognizer:
    return offline_speech.OfflineRecognizer(_load_offline_model(data_dir))


def _load_offline_model(data_dir: Path) -> Any:
    """The loaded sherpa-onnx recognizer, shared by dictations; loads it on first use."""
    global _offline_engine, _offline_engine_path, _offline_engine_used
    model_path = _offline_model_dir(data_dir)
    with _offline_engine_lock:
        _offline_engine_used = time.monotonic()
        if _offline_engine is not None and _offline_engine_path == model_path:
            return _offline_engine
        _require_offline_model(model_path)
        try:
            sherpa_onnx = offline_speech.import_sherpa_onnx()
        except offline_speech.OfflineSpeechUnavailable as exc:
            raise ChatMediaError(str(exc)) from exc
        started = time.monotonic()
        try:
            engine = offline_speech.new_engine(model_path, sherpa_onnx)
        except Exception as exc:  # noqa: BLE001
            log.warning("The local speech model couldn't be loaded", exc_info=True)
            raise ChatMediaError(
                f"The local speech model couldn't be loaded ({type(exc).__name__})."
            ) from exc
        _offline_engine, _offline_engine_path = engine, model_path
        log.info("Loaded the local speech model in %.1f s", time.monotonic() - started)
        return engine


def _schedule_offline_unload(delay: float | None = None) -> None:
    """Drops the loaded model after it sits unused, since it holds about 1 GB."""
    global _offline_engine_timer
    wait = offline_speech.IDLE_UNLOAD_SECONDS if delay is None else delay
    with _offline_engine_lock:
        if _offline_engine is None:
            return
        if _offline_engine_timer is not None:
            _offline_engine_timer.cancel()
        timer = threading.Timer(wait, _unload_idle_offline_model, kwargs={"idle": wait})
        timer.daemon = True
        _offline_engine_timer = timer
        timer.start()


def _unload_idle_offline_model(idle: float) -> bool:
    global _offline_engine, _offline_engine_path, _offline_engine_timer
    with _transcription_sessions_lock:
        in_use = any(session.recognizer is not None for session in _transcription_sessions.values())
    with _offline_engine_lock:
        _offline_engine_timer = None
        if _offline_engine is None or in_use:
            return False
        if time.monotonic() - _offline_engine_used < idle:
            return False
        # Dictations still holding it keep it alive until they end.
        _offline_engine = None
        _offline_engine_path = None
    log.info("Unloaded the local speech model after it sat unused")
    return True


def _valid_offline_model(model_path: Path) -> bool:
    if model_path.is_symlink() or not model_path.is_dir():
        return False
    expected = set(OFFLINE_MODEL_FILES)
    allowed = expected | {OFFLINE_MODEL_NOTICE_FILE}
    found: set[str] = set()
    try:
        for entry in model_path.iterdir():
            if entry.name not in allowed or entry.is_symlink() or not entry.is_file():
                return False
            if entry.name in expected:
                found.add(entry.name)
        if found != expected:
            return False
        for name, expected_digest in OFFLINE_MODEL_FILES.items():
            digest = hashlib.sha256()
            total = 0
            with open_binary_input(model_path / name) as source:
                while chunk := source.read(4 * 1024 * 1024):
                    total += len(chunk)
                    if total > OFFLINE_MODEL_MAX_FILE_BYTES:
                        return False
                    digest.update(chunk)
            if digest.hexdigest() != expected_digest:
                return False
    except OSError:
        return False
    return True


def _require_offline_model(model_path: Path) -> None:
    """The model must already be installed: a dictation never starts a 475 MB download.

    Settings > Devices downloads it, with progress (start_offline_model_download).
    """
    if _valid_offline_model(model_path):
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
    replaced = not damaged and _legacy_model_replaced(model_path.parent.parent)
    log.warning(
        "Dictation can't use %s: it is %s",
        OFFLINE_ENGINE_NAME,
        "damaged or out of date"
        if damaged
        else "not downloaded since the old model was replaced"
        if replaced
        else "not downloaded",
    )
    if damaged:
        raise ChatMediaError(OFFLINE_MODEL_DAMAGED)
    raise ChatMediaError(OFFLINE_MODEL_REPLACED if replaced else OFFLINE_MODEL_MISSING)


ModelProgress = Callable[[str, int, int], None]


def _ensure_offline_model(model_path: Path, *, progress: ModelProgress | None = None) -> None:
    def report(state: str, received: int = 0, total: int = 0) -> None:
        if progress is not None:
            progress(state, received, total)

    report("installing")
    if _valid_offline_model(model_path):
        return
    log.info(
        "The local speech model is %s; downloading it (about %d MB)",
        "damaged or out of date" if model_path.exists() else "not installed",
        OFFLINE_MODEL_DOWNLOAD_MB,
    )
    started = time.monotonic()
    model_root = model_path.parent
    model_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require_free_space(model_root)
    context, ca_source = _download_tls_context()
    try:
        # A private staging directory prevents partial downloads or extraction
        # failures from becoming a model that the native loader can consume.
        with tempfile.TemporaryDirectory(prefix=OFFLINE_STAGING_PREFIX, dir=model_root) as staging:
            stage = Path(staging)
            archive_path = stage / "model.tar.bz2"
            request = Request(OFFLINE_MODEL_URL, headers={"User-Agent": "Raticode local speech/2"})
            with (
                urlopen(request, timeout=60, context=context) as response,
                archive_path.open("xb") as target,
            ):
                total = _content_length(response)
                report("downloading", 0, total)
                _copy_limited(
                    response,
                    target,
                    OFFLINE_MODEL_DOWNLOAD_MAX_BYTES,
                    progress=lambda received: report("downloading", received, total),
                )
            size = archive_path.stat().st_size
            report("installing", size, total or size)
            with archive_path.open("rb") as downloaded:
                digest = hashlib.file_digest(downloaded, "sha256").hexdigest()
            if digest != OFFLINE_MODEL_SHA256:
                log.warning(
                    "The downloaded speech model (%d bytes) has the wrong checksum, "
                    "so it was discarded",
                    size,
                )
                raise ChatMediaError("The local speech model checksum does not match.")
            extracted = stage / OFFLINE_MODEL_NAME
            extracted.mkdir(mode=0o700)
            with tarfile.open(archive_path, "r|bz2") as archive:
                _extract_model_files(archive, extracted)
            # The archive is no longer needed; free its space before the swap.
            archive_path.unlink()
            if not _valid_offline_model(extracted):
                raise ChatMediaError("The downloaded local speech model is incomplete.")
            with atomic_binary_output(extracted / OFFLINE_MODEL_NOTICE_FILE) as output:
                output.write(OFFLINE_MODEL_NOTICE.encode())
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
            OFFLINE_MODEL_URL,
            ca_source,
            type(cause).__name__,
            cause,
            exc_info=True,
        )
        raise ChatMediaError(_download_failure_message(exc)) from exc
    _forget_legacy_model(model_root)
    log.info(
        "Installed the local speech model (%d bytes) in %.0f s",
        size,
        time.monotonic() - started,
    )


def _require_free_space(folder: Path) -> None:
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < OFFLINE_MODEL_FREE_BYTES:
        need = OFFLINE_MODEL_FREE_BYTES / 1e9
        log.warning("Not enough free space for the local speech model (%d bytes free)", free)
        raise ChatMediaError(
            f"The local speech model needs about {need:.1f} GB of free disk space while it "
            f"installs, and {free / 1e9:.1f} GB is free. Free some space and try again."
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
    return value if 0 < value <= OFFLINE_MODEL_DOWNLOAD_MAX_BYTES else 0


def _extract_model_files(archive: tarfile.TarFile, target: Path) -> None:
    """Unpacks only the model's own files from the archive, read front to back.

    Every other entry (the README, sample audio) is skipped. The wanted files must be
    plain files at ``<archive folder>/<name>``; links, devices or a repeated name stop
    the install. Sizes are capped per file and in total, so a damaged archive can't
    fill the disk. The archive's checksum was already verified.
    """
    wanted = {f"{OFFLINE_MODEL_ARCHIVE_DIR}/{name}": name for name in OFFLINE_MODEL_FILES}
    written: set[str] = set()
    entries = total = 0
    for member in archive:
        entries += 1
        total += max(0, member.size)
        if entries > OFFLINE_MODEL_MAX_ENTRIES:
            raise ChatMediaError("The local speech model archive has too many entries.")
        if total > OFFLINE_MODEL_MAX_EXPANDED_BYTES:
            raise ChatMediaError("The local speech model archive exceeds extraction limits.")
        name = wanted.get(member.name.removeprefix("./"))
        if name is None:
            continue
        if not member.isreg() or name in written:
            raise ChatMediaError("The local speech model archive contains an unsafe path.")
        if member.size > OFFLINE_MODEL_MAX_FILE_BYTES:
            raise ChatMediaError("The local speech model archive exceeds extraction limits.")
        source = archive.extractfile(member)
        if source is None:
            raise ChatMediaError("The local speech model archive is damaged.")
        destination = target / name
        with source, destination.open("xb") as output:
            os.chmod(destination, 0o600)
            _copy_limited(source, output, member.size)
        written.add(name)
    if written != set(OFFLINE_MODEL_FILES):
        raise ChatMediaError("The downloaded local speech model is incomplete.")


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
