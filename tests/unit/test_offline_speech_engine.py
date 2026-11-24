"""sherpa-onnx loading: the Windows onnxruntime.dll name clash, and dropping the idle model."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gofer.ui import chat_media, offline_speech
from tests.helpers.fake_speech import FakeRecognizerEngine


def sherpa_package(root: Path) -> SimpleNamespace:
    package = root / "sherpa_onnx"
    (package / "lib").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "lib" / "onnxruntime.dll").write_bytes(b"MZ")
    return SimpleNamespace(
        origin=str(package / "__init__.py"), submodule_search_locations=[str(package)]
    )


def test_windows_loads_sherpas_own_onnxruntime_by_full_path_before_the_import(
    tmp_path, monkeypatch
) -> None:
    """Raticode's ONNX Runtime (1.30) is also called onnxruntime.dll. Windows binds a
    by-name import to whichever DLL of that name is loaded, so sherpa-onnx's copy
    (1.28.2) is loaded by its full path first."""
    spec = sherpa_package(tmp_path)
    loaded: list[str] = []
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(name="sherpa"))
    module = offline_speech.import_sherpa_onnx(
        "win32",
        find_spec=lambda _name: spec,
        load_library=loaded.append,
        loaded_path=lambda _name: "",
    )
    assert module.name == "sherpa"
    assert loaded == [str(tmp_path / "sherpa_onnx" / "lib" / "onnxruntime.dll")]


def test_windows_warns_when_another_onnxruntime_dll_is_already_loaded(
    tmp_path, monkeypatch, caplog
) -> None:
    spec = sherpa_package(tmp_path)
    loaded: list[str] = []
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace())
    other = tmp_path / "onnxruntime" / "capi" / "onnxruntime.dll"
    offline_speech.import_sherpa_onnx(
        "win32",
        find_spec=lambda _name: spec,
        load_library=loaded.append,
        loaded_path=lambda _name: str(other),
    )
    assert "Another onnxruntime.dll is already loaded" in caplog.text
    assert loaded == [str(tmp_path / "sherpa_onnx" / "lib" / "onnxruntime.dll")]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_systems_import_sherpa_onnx_without_preloading(platform, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace())
    offline_speech.import_sherpa_onnx(
        platform,
        find_spec=lambda _name: pytest.fail("no DLL lookup outside Windows"),
        load_library=lambda _path: pytest.fail("no preload outside Windows"),
        loaded_path=lambda _name: pytest.fail("no module lookup outside Windows"),
    )


def test_a_missing_library_says_to_reinstall(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    with pytest.raises(offline_speech.OfflineSpeechUnavailable, match="Reinstall Raticode"):
        offline_speech.import_sherpa_onnx("linux")


def test_the_engine_uses_the_measured_settings(tmp_path) -> None:
    seen: dict[str, object] = {}

    class OnlineRecognizer:
        @staticmethod
        def from_transducer(**kwargs):
            seen.update(kwargs)
            return "engine"

    assert offline_speech.new_engine(tmp_path, SimpleNamespace(OnlineRecognizer=OnlineRecognizer))
    assert seen["encoder"] == str(tmp_path / "encoder.int8.onnx")
    assert seen["num_threads"] == 2
    assert seen["sample_rate"] == 16_000
    # A phrase ends only after a long pause: short resets dropped words in the benchmark.
    assert seen["rule1_min_trailing_silence"] == seen["rule2_min_trailing_silence"] == 2.4


def test_dictations_on_one_model_never_decode_at_the_same_time() -> None:
    import threading
    import time

    active = 0
    most = 0
    guard = threading.Lock()

    class SlowEngine(FakeRecognizerEngine):
        ready: dict[int, bool] = {}

        def is_ready(self, stream):  # type: ignore[no-untyped-def]
            # Ready once per chunk: True, then False until the next call after it.
            now = not self.ready.get(id(stream), False)
            self.ready[id(stream)] = now
            return now

        def decode_stream(self, stream):  # type: ignore[no-untyped-def]
            nonlocal active, most
            with guard:
                active += 1
                most = max(most, active)
            time.sleep(0.01)
            with guard:
                active -= 1
            return super().decode_stream(stream)

    engine = SlowEngine(final="")
    first = offline_speech.OfflineRecognizer(engine)
    second = offline_speech.OfflineRecognizer(engine)
    assert first.lock is second.lock
    assert offline_speech.OfflineRecognizer(FakeRecognizerEngine(final="")).lock is not first.lock
    chunk = b"\x00\x00" * 1600
    threads = [
        threading.Thread(target=lambda r=r: [r.accept(chunk) for _ in range(5)])
        for r in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert most == 1


def test_the_idle_model_is_dropped_but_not_while_a_dictation_uses_it(monkeypatch, tmp_path):
    engine = FakeRecognizerEngine(final="Done.")
    monkeypatch.setattr(chat_media, "_offline_engine", engine)
    monkeypatch.setattr(chat_media, "_offline_engine_path", tmp_path)
    monkeypatch.setattr(chat_media, "_offline_engine_used", 0.0)
    chat_media._transcription_sessions.clear()
    session = chat_media._TranscriptionSession(recognizer=offline_speech.OfflineRecognizer(engine))
    chat_media._transcription_sessions["a" * 32] = session
    try:
        assert not chat_media._unload_idle_offline_model(idle=0)
        assert chat_media._offline_engine is engine
    finally:
        chat_media._transcription_sessions.clear()
    assert chat_media._unload_idle_offline_model(idle=0)
    assert chat_media._offline_engine is None
    # The dictation that held it still finishes.
    assert session.recognizer.finish() == "Done."


def test_finishing_a_dictation_schedules_the_unload(monkeypatch, tmp_path) -> None:
    engine = FakeRecognizerEngine(final="Hello.")
    scheduled: list[float | None] = []
    monkeypatch.setattr(chat_media, "_load_offline_model", lambda _dir: engine)
    monkeypatch.setattr(chat_media, "_schedule_offline_unload", lambda: scheduled.append(None))
    chat_media._transcription_sessions.clear()
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path, engine="offline")[
        "sessionId"
    ]
    assert chat_media.finish_chat_transcription({"sessionId": session_id}) == {"text": "Hello."}
    assert scheduled == [None]
