from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gofer.core import provider_preferences
from gofer.ui import chat_media, system_speech
from gofer.ui.chat_media import ChatMediaError

# Stands in for the macOS helper or the Windows script: same JSON lines, same pipes.
FAKE_HELPER = r"""
import json, os, sys
mode = sys.argv[1]
def say(**event):
    print(json.dumps(event), flush=True)
if mode == "fail":
    say(type="error", code="denied", message="Speech recognition is off for Raticode.")
    sys.exit(3)
if mode == "crash":
    sys.stderr.write("dyld: Library not loaded: Speech.framework\n")
    sys.stderr.flush()
    os.abort()
if mode == "trap":
    import signal
    os.kill(os.getpid(), signal.SIGTRAP)
if mode == "check":
    say(type="status", available=False, reason="Turn on Dictation.", language="en-US")
    sys.exit(0)
say(type="ready", language="en-US")
heard = 0
while True:
    chunk = sys.stdin.buffer.read1(65536)
    if not chunk:
        break
    heard += len(chunk)
    say(type="partial", text=f"heard {heard}")
    if mode == "phrases" and heard >= 8:
        say(type="final", text="first phrase")
        mode = "listen"
say(type="final", text=f"all {heard} bytes")
say(type="done")
"""


def fake_engine(mode: str, engine_id: str = "macos") -> system_speech.SystemEngine:
    command = (sys.executable, "-c", FAKE_HELPER, mode)
    return system_speech.SystemEngine(
        engine_id, "macOS speech recognition", command=command, check_command=command
    )


def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def chunk(session_id: str, pcm: bytes) -> dict[str, str]:
    return {"sessionId": session_id, "data": base64.b64encode(pcm).decode()}


class FakeVosk:
    def __init__(self) -> None:
        self.audio: list[bytes] = []

    def AcceptWaveform(self, pcm: bytes) -> bool:  # noqa: N802
        self.audio.append(pcm)
        return False

    def PartialResult(self) -> str:  # noqa: N802
        return json.dumps({"partial": f"offline {sum(map(len, self.audio))}"})

    def FinalResult(self) -> str:  # noqa: N802
        return json.dumps({"text": "offline end"})


@pytest.fixture(autouse=True)
def clean_sessions():
    chat_media._transcription_sessions.clear()
    system_speech._check_cache.clear()
    system_speech._logged_checks.clear()
    yield
    for session in list(chat_media._transcription_sessions.values()):
        chat_media._close_session(session)
    chat_media._transcription_sessions.clear()


def test_each_os_offers_its_own_recognizer_or_says_why_not(tmp_path) -> None:
    linux = system_speech.system_engine("linux", {})
    assert not linux.present
    assert "Linux doesn't include a speech recognizer" in linux.reason

    mac = system_speech.system_engine("darwin", {})
    assert mac.id == "macos" and not mac.present
    assert "doesn't include its macOS speech helper" in mac.reason

    helper = tmp_path / "raticode-speech"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    mac = system_speech.system_engine("darwin", {system_speech.HELPER_ENV: str(helper)})
    assert mac.command == (str(helper), "listen")
    assert mac.check_command == (str(helper), "check")

    root = tmp_path / "Windows"
    powershell = root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_text("")
    windows = system_speech.system_engine("win32", {"SystemRoot": str(root)})
    assert windows.command[0] == str(powershell)
    assert "-NonInteractive" in windows.command and "-NoProfile" in windows.command
    script = base64.b64decode(windows.command[-1]).decode("utf-16-le")
    assert script == system_speech.WINDOWS_SPEECH_SCRIPT
    assert dict(windows.env) == {"RATICODE_SPEECH_MODE": "listen"}
    assert dict(windows.check_env) == {"RATICODE_SPEECH_MODE": "check"}


def test_the_windows_script_uses_the_offline_recognizer_on_piped_audio() -> None:
    script = system_speech.WINDOWS_SPEECH_SCRIPT
    assert "Add-Type -AssemblyName System.Speech" in script
    assert "new DictationGrammar()" in script
    assert "SetInputToAudioStream" in script
    assert (
        "new SpeechAudioFormatInfo(16000, AudioBitsPerSample.Sixteen, AudioChannel.Mono)" in script
    )
    assert "InstalledRecognizers()" in script
    # The encoded command must fit on a Windows command line (32,767 characters).
    assert len(system_speech._encoded_powershell(script)) < 30_000


def test_helper_session_streams_words_and_keeps_every_phrase() -> None:
    speech = system_speech.SystemSpeechSession(fake_engine("phrases"))
    speech.push(b"\x01\x00" * 4)
    wait_for(lambda: speech.text() == "first phrase")
    speech.push(b"\x01\x00" * 2)
    wait_for(lambda: speech.text() == "first phrase heard 12")
    assert speech.ready
    assert speech.replay() == []
    assert speech.finish() == "first phrase all 12 bytes"
    assert speech.failure == ""


def test_helper_secrets_are_not_passed_to_the_recognizer(monkeypatch) -> None:
    monkeypatch.setenv("GOFER_DESKTOP_GRANT_SECRET", "secret")
    seen: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        seen.update(kwargs)
        return subprocess.Popen(command, **kwargs)

    speech = system_speech.SystemSpeechSession(fake_engine("listen"), popen=fake_popen)
    speech.close()
    env = seen["env"]
    assert isinstance(env, dict)
    assert "GOFER_DESKTOP_GRANT_SECRET" not in env


def test_a_helper_that_refuses_reports_why_and_keeps_audio_for_replay() -> None:
    speech = system_speech.SystemSpeechSession(fake_engine("fail"))
    speech.push(b"\x02\x00" * 4)
    wait_for(lambda: speech.failure)
    assert speech.failure == "Speech recognition is off for Raticode."
    assert speech.failure_code == "denied"
    assert speech.replay() == [b"\x02\x00" * 4]
    speech.close()


def test_a_helper_macos_stops_is_explained() -> None:
    speech = system_speech.SystemSpeechSession(fake_engine("crash"))
    wait_for(lambda: speech.failure)
    assert "macOS stopped Raticode's speech helper" in speech.failure
    assert "installed Raticode app" in speech.failure


def test_check_reports_the_recognizers_state_and_caches_it() -> None:
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.run(command, **kwargs)

    engine = fake_engine("check")
    status = system_speech.check_system_engine(engine, run=run)
    assert status == {
        "id": "macos",
        "name": "macOS speech recognition",
        "available": False,
        "reason": "Turn on Dictation.",
        "language": "en-US",
    }
    assert system_speech.check_system_engine(engine, run=run) == status
    assert len(calls) == 1
    system_speech.check_system_engine(engine, run=run, refresh=True)
    assert len(calls) == 2
    linux = system_speech.check_system_engine(system_speech.system_engine("linux", {}))
    assert linux["available"] is False and "Linux" in linux["reason"]


def test_dictation_uses_the_os_recognizer_when_it_works(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(system_speech, "system_engine", lambda: fake_engine("listen"))
    monkeypatch.setattr(
        chat_media, "_new_vosk_recognizer", lambda _dir: pytest.fail("Vosk not needed")
    )
    started = chat_media.start_chat_transcription(data_dir=tmp_path)
    assert started["engine"] == "macos"
    assert started["engineName"] == "macOS speech recognition"
    assert "notice" not in started
    session_id = started["sessionId"]
    chat_media.stream_chat_transcription(chunk(session_id, b"\x01\x00" * 3))
    wait_for(lambda: chat_media._transcription_sessions[session_id].speech.text() == "heard 6")
    assert chat_media.stream_chat_transcription(chunk(session_id, b"\x01\x00"))["text"].startswith(
        "heard"
    )
    assert chat_media.finish_chat_transcription({"sessionId": session_id}) == {
        "text": "all 8 bytes"
    }


def test_a_refused_os_recognizer_hands_over_to_the_offline_model_without_losing_audio(
    monkeypatch, tmp_path
) -> None:
    vosk = FakeVosk()
    monkeypatch.setattr(system_speech, "system_engine", lambda: fake_engine("fail"))
    monkeypatch.setattr(chat_media, "_vosk_model_present", lambda _dir: True)
    monkeypatch.setattr(chat_media, "_new_vosk_recognizer", lambda _dir: vosk)
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path)["sessionId"]
    first, second = b"\x01\x00" * 2, b"\x02\x00" * 3
    assert chat_media.stream_chat_transcription(chunk(session_id, first)) == {"text": ""}
    wait_for(lambda: chat_media._transcription_sessions[session_id].speech.failure)
    result = chat_media.stream_chat_transcription(chunk(session_id, second))
    # Audio heard before the refusal is replayed into the offline model, in order.
    assert vosk.audio == [first, second]
    assert result == {
        "text": "offline 10",
        "engine": "offline",
        "notice": "Speech recognition is off for Raticode. "
        "Using Raticode's offline speech model instead.",
    }
    assert chat_media.finish_chat_transcription({"sessionId": session_id}) == {
        "text": "offline end",
        "notice": "Speech recognition is off for Raticode. "
        "Using Raticode's offline speech model instead.",
    }


def test_without_the_offline_model_a_refusal_explains_how_to_switch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(system_speech, "system_engine", lambda: fake_engine("fail"))
    monkeypatch.setattr(chat_media, "_vosk_model_present", lambda _dir: False)
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path)["sessionId"]
    chat_media.stream_chat_transcription(chunk(session_id, b"\x01\x00"))
    wait_for(lambda: chat_media._transcription_sessions[session_id].speech.failure)
    with pytest.raises(ChatMediaError) as raised:
        chat_media.stream_chat_transcription(chunk(session_id, b"\x01\x00"))
    assert str(raised.value) == (
        "Speech recognition is off for Raticode. "
        "You can download Raticode's offline speech model in Settings > Devices."
    )


def test_an_os_without_a_usable_recognizer_starts_on_the_offline_model(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(chat_media, "_new_vosk_recognizer", lambda _dir: FakeVosk())
    real = system_speech.system_engine
    monkeypatch.setattr(system_speech, "system_engine", lambda: real("darwin", {}))
    started = chat_media.start_chat_transcription(data_dir=tmp_path)
    assert started["engine"] == "offline"
    assert started["notice"] == (
        "This copy of Raticode doesn't include its macOS speech helper. "
        "Using Raticode's offline speech model instead."
    )
    # Linux has nothing to fall back from, so there is nothing to explain.
    monkeypatch.setattr(system_speech, "system_engine", lambda: real("linux", {}))
    assert "notice" not in chat_media.start_chat_transcription(data_dir=tmp_path)


def test_choosing_the_offline_model_skips_the_os_recognizer(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(system_speech, "system_engine", lambda: pytest.fail("not consulted"))
    monkeypatch.setattr(chat_media, "_new_vosk_recognizer", lambda _dir: FakeVosk())
    started = chat_media.start_chat_transcription(data_dir=tmp_path, engine="offline")
    assert started["engine"] == "offline"


def test_cancelling_a_dictation_stops_the_helper(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(system_speech, "system_engine", lambda: fake_engine("listen"))
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path)["sessionId"]
    process = chat_media._transcription_sessions[session_id].speech._process
    assert chat_media.cancel_chat_transcription({"sessionId": session_id}) == {"cancelled": True}
    wait_for(lambda: process.poll() is not None)


def test_settings_report_both_engines(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(system_speech, "system_engine", lambda: fake_engine("check"))
    engines = chat_media.transcription_engines(data_dir=tmp_path)
    assert engines["system"]["reason"] == "Turn on Dictation."
    assert engines["offline"] == {
        "name": "Raticode's offline speech model",
        "installed": False,
        "downloadMb": 130,
    }


def test_offline_model_presence_is_a_cheap_file_check(tmp_path) -> None:
    model = Path(tmp_path) / "speech-models" / chat_media.VOSK_MODEL_NAME
    assert not chat_media._vosk_model_present(tmp_path)
    for name in chat_media.VOSK_MODEL_FILES:
        (model / name).parent.mkdir(parents=True, exist_ok=True)
        (model / name).write_bytes(b"")
    assert chat_media._vosk_model_present(tmp_path)


def test_speech_engine_preference_defaults_to_the_os_and_validates(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(provider_preferences, "get_data_dir", lambda: tmp_path)
    assert provider_preferences.speech_preference() == {"engine": "system"}
    provider_preferences.save_speech_preference({"engine": "offline"})
    assert provider_preferences.speech_preference() == {"engine": "offline"}
    with pytest.raises(ValueError):
        provider_preferences.save_speech_preference({"engine": "cloud"})
    with pytest.raises(ValueError):
        provider_preferences.save_speech_preference({"engine": "system", "extra": 1})


# Logging: each stage of a dictation is in the app log, with no audio and no words.

WORDS = ("first phrase", "heard ", "all ", "offline ")


def assert_no_words(caplog) -> None:
    for record in caplog.records:
        text = record.getMessage()
        assert not any(word in text for word in WORDS), text


def test_a_missing_helper_says_what_was_looked_for(tmp_path) -> None:
    assert system_speech.HELPER_ENV in system_speech.system_engine("darwin", {}).detail
    gone = tmp_path / "gone"
    mac = system_speech.system_engine("darwin", {system_speech.HELPER_ENV: str(gone)})
    assert mac.detail == f"no helper at {gone}"
    plain = tmp_path / "raticode-speech"
    plain.write_text("")
    plain.chmod(0o644)
    mac = system_speech.system_engine("darwin", {system_speech.HELPER_ENV: str(plain)})
    assert mac.detail == f"{plain} is not executable" and not mac.present


def test_a_whole_dictation_is_logged_without_its_words(caplog) -> None:
    caplog.set_level("DEBUG", logger="gofer.ui.system_speech")
    speech = system_speech.SystemSpeechSession(fake_engine("phrases"))
    speech.push(b"\x01\x00" * 8)
    wait_for(lambda: speech.text().startswith("first phrase"))
    assert speech.finish().startswith("first phrase")
    wait_for(lambda: "helper exited" in caplog.text)
    assert "macOS speech recognition helper started (process" in caplog.text
    assert "macOS speech recognition is ready after" in caplog.text
    assert "(language en-US)" in caplog.text
    assert "helper exited with code 0 after 0.0 s of audio" in caplog.text
    assert_no_words(caplog)


def test_a_crashed_helper_logs_its_signal_and_stderr(caplog) -> None:
    speech = system_speech.SystemSpeechSession(fake_engine("crash"))
    wait_for(lambda: speech.failure and "helper exited" in caplog.text)
    assert "exited on SIGABRT (code -6)" in caplog.text
    assert "failure exited, before it was ready" in caplog.text
    assert "stderr: dyld: Library not loaded: Speech.framework" in caplog.text
    assert speech.stderr_tail() == "dyld: Library not loaded: Speech.framework"


def test_only_an_abort_is_blamed_on_missing_permission_text() -> None:
    speech = system_speech.SystemSpeechSession(fake_engine("trap"))
    wait_for(lambda: speech.failure)
    assert speech.failure == "Raticode's macOS speech helper stopped unexpectedly (SIGTRAP)."


def test_stopping_the_helper_ourselves_is_not_a_failure(caplog) -> None:
    caplog.set_level("INFO", logger="gofer.ui.system_speech")
    speech = system_speech.SystemSpeechSession(fake_engine("listen"))
    process = speech._process
    speech.close()
    wait_for(lambda: process.poll() is not None and "helper exited" in caplog.text)
    # Before, a terminated helper read as "macOS stopped Raticode's speech helper".
    assert speech.failure == ""
    assert "helper exited on SIGTERM" in caplog.text
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_a_refusal_is_logged_with_its_code(caplog) -> None:
    speech = system_speech.SystemSpeechSession(fake_engine("fail"))
    wait_for(lambda: speech.failure and "helper exited" in caplog.text)
    assert (
        "macOS speech recognition failed (denied): Speech recognition is off for Raticode."
        in caplog.text
    )
    assert "exited with code 3" in caplog.text


def test_a_helper_that_cant_be_started_is_logged(caplog) -> None:
    engine = system_speech.SystemEngine(
        "macos", "macOS speech recognition", command=("/nonexistent/raticode-speech", "listen")
    )
    with pytest.raises(OSError):
        system_speech.SystemSpeechSession(engine)
    assert "couldn't start its helper" in caplog.text


def test_check_results_are_logged_once_until_they_change(caplog) -> None:
    caplog.set_level("INFO", logger="gofer.ui.system_speech")
    engine = fake_engine("check")
    system_speech.check_system_engine(engine)
    system_speech.check_system_engine(engine, refresh=True)
    unavailable = [r for r in caplog.records if "unavailable for dictation" in r.getMessage()]
    assert len(unavailable) == 1
    assert "Turn on Dictation." in unavailable[0].getMessage()
    assert "check exited with code 0" in unavailable[0].getMessage()
    missing = system_speech.system_engine("darwin", {})
    system_speech.check_system_engine(missing)
    assert system_speech.HELPER_ENV in caplog.text


def test_dictation_logs_its_engine_and_how_much_audio_arrived(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(system_speech, "system_engine", lambda: fake_engine("listen"))
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path)["sessionId"]
    chat_media.stream_chat_transcription(chunk(session_id, b"\x01\x00" * 16_000))
    chat_media.finish_chat_transcription({"sessionId": session_id})
    assert "Dictation started with macOS speech recognition (1 open)" in caplog.text
    assert "Dictation finished (macOS speech recognition, 1.0 s of audio in 1 chunks" in (
        caplog.text
    )
    assert session_id not in caplog.text
    assert_no_words(caplog)


def test_a_missing_helper_and_a_failed_download_keep_both_reasons(
    monkeypatch, tmp_path, caplog
) -> None:
    def no_model(_dir):
        raise ChatMediaError("The local speech model couldn't be downloaded: the certificate.")

    monkeypatch.setattr(chat_media, "_new_vosk_recognizer", no_model)
    real = system_speech.system_engine
    monkeypatch.setattr(system_speech, "system_engine", lambda: real("darwin", {}))
    with pytest.raises(ChatMediaError) as raised:
        chat_media.start_chat_transcription(data_dir=tmp_path)
    # Before, only the download error was shown and the missing helper wasn't mentioned.
    assert str(raised.value) == (
        "This copy of Raticode doesn't include its macOS speech helper. "
        "The local speech model couldn't be downloaded: the certificate."
    )
    assert "Dictation can't use macOS speech recognition" in caplog.text
    assert system_speech.HELPER_ENV in caplog.text
    # Choosing the offline model says only what went wrong with it.
    with pytest.raises(ChatMediaError) as raised:
        chat_media.start_chat_transcription(data_dir=tmp_path, engine="offline")
    assert str(raised.value).startswith("The local speech model couldn't")


def test_a_fallback_mid_dictation_is_logged(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setattr(system_speech, "system_engine", lambda: fake_engine("fail"))
    monkeypatch.setattr(chat_media, "_vosk_model_present", lambda _dir: False)
    session_id = chat_media.start_chat_transcription(data_dir=tmp_path)["sessionId"]
    chat_media.stream_chat_transcription(chunk(session_id, b"\x01\x00"))
    wait_for(lambda: chat_media._transcription_sessions[session_id].speech.failure)
    with pytest.raises(ChatMediaError):
        chat_media.stream_chat_transcription(chunk(session_id, b"\x01\x00"))
    assert "macOS speech recognition stopped during dictation (denied)" in caplog.text
    assert "isn't installed, so dictation can't carry on" in caplog.text
