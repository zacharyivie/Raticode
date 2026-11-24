"""Raticode's offline speech model: sherpa-onnx running NVIDIA's Nemotron 3.5 ASR Streaming.

The model is a cache-aware streaming transducer (Nemotron 3.5 ASR Streaming 0.6B, int8,
560 ms chunks, OpenMDW-1.1) exported for sherpa-onnx. It is multilingual: streams leave
sherpa-onnx's per-stream "language" option unset, which detects the spoken language
(English measured 4.63% WER this way against 5.02% pinned to "en"). It is downloaded only from
Settings > Devices (see ``chat_media.start_offline_model_download``) into the data
folder's ``speech-models``; it is never part of the installer.

``OfflineRecognizer`` maps the model onto the dictation events Raticode already uses:
each chunk of 16 kHz 16-bit mono PCM returns the phrases finished since the last chunk
and the words of the current phrase so far (the partial). The desktop window shows
finished phrases plus the partial, so the append-only voice tidy keeps working.

Two details keep the model from dropping words, measured on 80 clips before this was
written: half a second of silence is fed before the first audio (and after each
reset), and a phrase is only finished after 2.4 s of silence, never at short pauses.

Nothing here logs audio or recognized words.
"""

from __future__ import annotations

import ctypes
import importlib.util
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gofer.utils.logging import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16_000
# Silence fed before the first audio and after each finished phrase. Without it the
# model dropped first words ("To avert the tendency" became "The tendency").
LEAD_IN_SAMPLES = SAMPLE_RATE // 2
# Silence fed at the end, so the last words leave the model's 560 ms look-ahead.
TAIL_SAMPLES = SAMPLE_RATE * 3 // 2
# A phrase ends after this much silence. Shorter endpoints (resets at every pause)
# cost words around each reset in the benchmark.
ENDPOINT_SILENCE_SECONDS = 2.4
# A phrase is also finished after this long, so a long dictation's committed text grows
# in pieces. Resets happen at the next silence-free chunk boundary.
ENDPOINT_MAX_PHRASE_SECONDS = 60.0
NUM_THREADS = 2
# The loaded model takes about 1 GB, so it is dropped after this long without a dictation.
IDLE_UNLOAD_SECONDS = 10 * 60

MODEL_FILES_ENCODER = "encoder.int8.onnx"
MODEL_FILES_DECODER = "decoder.int8.onnx"
MODEL_FILES_JOINER = "joiner.int8.onnx"
MODEL_FILES_TOKENS = "tokens.txt"


class OfflineSpeechUnavailable(RuntimeError):
    """sherpa-onnx can't be imported in this copy of Raticode."""


def import_sherpa_onnx(
    platform: str | None = None,
    *,
    find_spec: Callable[[str], Any] = importlib.util.find_spec,
    load_library: Callable[[str], Any] | None = None,
    loaded_path: Callable[[str], str] | None = None,
) -> Any:
    """Imports sherpa-onnx, on Windows pinning its own ONNX Runtime DLL first.

    sherpa-onnx's native module imports ``onnxruntime.dll`` by name. Raticode also ships
    ONNX Runtime for its embeddings, as ``onnxruntime/capi/onnxruntime.dll``, and
    Windows binds a by-name import to any DLL of that name already in the process. The
    two are different versions (sherpa-onnx 2.0.1 bundles 1.28.2, Raticode uses 1.30),
    so sherpa-onnx's copy is loaded by its full path before the import. Raticode's
    embeddings don't load ``onnxruntime.dll`` by name (their Python module links ONNX
    Runtime statically), so the pinned copy is the only one the import can find.
    """
    platform = platform or sys.platform
    if platform == "win32":
        _pin_windows_onnxruntime(
            find_spec=find_spec,
            load_library=load_library or _win_dll,
            loaded_path=loaded_path or _loaded_module_path,
        )
    try:
        import sherpa_onnx
    except ImportError as exc:
        log.warning("The sherpa-onnx speech library can't be imported", exc_info=True)
        raise OfflineSpeechUnavailable(
            "Offline speech support is missing from this copy of Raticode. Reinstall Raticode."
        ) from exc
    return sherpa_onnx


def sherpa_onnxruntime_dll(find_spec: Callable[[str], Any]) -> Path | None:
    """Where sherpa-onnx's own ``onnxruntime.dll`` is, or None when it can't be found."""
    try:
        spec = find_spec("sherpa_onnx")
    except (ImportError, ValueError):
        return None
    locations = list(getattr(spec, "submodule_search_locations", None) or [])
    if not locations and getattr(spec, "origin", None):
        locations = [str(Path(spec.origin).parent)]
    for location in locations:
        candidate = Path(location) / "lib" / "onnxruntime.dll"
        if candidate.is_file():
            return candidate
    return None


def _pin_windows_onnxruntime(
    *,
    find_spec: Callable[[str], Any],
    load_library: Callable[[str], Any],
    loaded_path: Callable[[str], str],
) -> None:
    dll = sherpa_onnxruntime_dll(find_spec)
    if dll is None:
        log.warning("sherpa-onnx's onnxruntime.dll wasn't found; the import may fail")
        return
    already = loaded_path("onnxruntime.dll")
    if already and Path(already).resolve() != dll.resolve():
        # Another ONNX Runtime with the same name is loaded: sherpa-onnx may bind to it.
        log.warning(
            "Another onnxruntime.dll is already loaded (%s); sherpa-onnx expects %s",
            already,
            dll,
        )
    try:
        load_library(str(dll))
    except OSError:
        log.warning("Couldn't load sherpa-onnx's onnxruntime.dll from %s", dll, exc_info=True)


def _win_dll(path: str) -> Any:
    return ctypes.WinDLL(path)  # type: ignore[attr-defined,unused-ignore]


def _loaded_module_path(name: str) -> str:
    """The full path of a DLL loaded in this process under ``name``, or ""."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.GetModuleHandleW(ctypes.c_wchar_p(name))
        if not handle:
            return ""
        buffer = ctypes.create_unicode_buffer(32_768)
        length = kernel32.GetModuleFileNameW(ctypes.c_void_p(handle), buffer, len(buffer))
        return str(buffer.value[:length]) if length else ""
    except (AttributeError, OSError):
        return ""


def new_engine(model_dir: Path, sherpa_onnx: Any) -> Any:
    """Loads the model into a sherpa-onnx recognizer. Takes a couple of seconds."""
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(model_dir / MODEL_FILES_TOKENS),
        encoder=str(model_dir / MODEL_FILES_ENCODER),
        decoder=str(model_dir / MODEL_FILES_DECODER),
        joiner=str(model_dir / MODEL_FILES_JOINER),
        num_threads=NUM_THREADS,
        sample_rate=SAMPLE_RATE,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=ENDPOINT_SILENCE_SECONDS,
        rule2_min_trailing_silence=ENDPOINT_SILENCE_SECONDS,
        rule3_min_utterance_length=ENDPOINT_MAX_PHRASE_SECONDS,
        decoding_method="greedy_search",
    )


_ENGINE_LOCKS: dict[int, tuple[Any, threading.Lock]] = {}
_ENGINE_LOCKS_GUARD = threading.Lock()


def engine_lock(engine: Any) -> threading.Lock:
    """The one lock every dictation on ``engine`` shares.

    sherpa-onnx's recognizer isn't documented as safe to decode from two threads, and
    up to 8 dictations can share it. The entry keeps the engine, so an id is never reused
    for another engine while its lock is listed. A model is reloaded only after it sat
    idle, so older entries have no dictation left and can be dropped.
    """
    with _ENGINE_LOCKS_GUARD:
        entry = _ENGINE_LOCKS.get(id(engine))
        if entry is None or entry[0] is not engine:
            if len(_ENGINE_LOCKS) >= 4:
                _ENGINE_LOCKS.clear()
            entry = (engine, threading.Lock())
            _ENGINE_LOCKS[id(engine)] = entry
        return entry[1]


@dataclass
class OfflineRecognizer:
    """One dictation on the offline model: finished phrases and a partial per chunk.

    ``engine`` is a sherpa-onnx ``OnlineRecognizer`` (or a test double with the same
    calls). One engine serves every dictation; ``lock`` keeps two dictations from
    decoding at the same moment.
    """

    engine: Any
    lock: threading.Lock = field(init=False)
    stream: Any = None
    finished: bool = False

    def __post_init__(self) -> None:
        self.lock = engine_lock(self.engine)
        self.stream = self.engine.create_stream()
        self._silence(LEAD_IN_SAMPLES)

    def accept(self, pcm: bytes) -> tuple[list[str], str]:
        """Feeds 16-bit PCM; returns phrases finished by this chunk and the current partial."""
        if self.finished:
            return [], ""
        samples = _float_samples(pcm)
        finals: list[str] = []
        with self.lock:
            self.stream.accept_waveform(SAMPLE_RATE, samples)
            self._decode()
            text = _text(self.engine.get_result(self.stream))
            if self.engine.is_endpoint(self.stream):
                if text:
                    finals.append(text)
                text = ""
                self.engine.reset(self.stream)
                self._silence(LEAD_IN_SAMPLES)
        return finals, text

    def finish(self) -> str:
        """Ends the dictation and returns the words of the last phrase."""
        if self.finished:
            return ""
        self.finished = True
        with self.lock:
            self._silence(TAIL_SAMPLES)
            self.stream.input_finished()
            self._decode()
            return _text(self.engine.get_result(self.stream))

    def _decode(self) -> None:
        while self.engine.is_ready(self.stream):
            self.engine.decode_stream(self.stream)

    def _silence(self, samples: int) -> None:
        self.stream.accept_waveform(SAMPLE_RATE, _zeros(samples))


def _float_samples(pcm: bytes) -> Any:
    """16-bit little-endian PCM as floats in [-1, 1], which sherpa-onnx expects."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with Raticode
        count = len(pcm) // 2
        values = memoryview(pcm[: count * 2]).cast("h")
        return [value / 32768.0 for value in values]
    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def _zeros(samples: int) -> Any:
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with Raticode
        return [0.0] * samples
    return np.zeros(samples, dtype=np.float32)


def _text(value: Any) -> str:
    # get_result returns a str; older sherpa-onnx versions returned an object with .text.
    text = value if isinstance(value, str) else getattr(value, "text", "")
    return str(text or "").strip()
