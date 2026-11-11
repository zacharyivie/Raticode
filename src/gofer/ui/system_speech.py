"""Speech-to-text from the operating system, for dictation in the desktop app.

Each OS supplies a different recognizer, and Linux supplies none:

* macOS: Apple's Speech framework, on-device only, through a small helper app
  (``frontend/native/macos/RaticodeSpeech.swift``) that ships inside Raticode.app. The
  desktop app passes its path in ``GOFER_SPEECH_HELPER``.
* Windows: the offline recognizer behind ``System.Speech``, driven by Windows
  PowerShell, which every Windows 10 and 11 install includes.
* Linux: nothing built in, so dictation uses Raticode's offline Vosk model.

Audio arrives from the desktop window as 16 kHz mono 16-bit PCM and is piped to the
helper's stdin; the helper answers with one JSON object per line on stdout:
``ready``, ``partial`` (the words of the current phrase so far), ``final`` (a finished
phrase), ``error`` (a code and a plain-language message) and ``done``. Nothing is
written to disk and no audio leaves the computer.

The log records how each dictation went (which recognizer, why one was skipped, when
the helper started, got ready, failed or exited) but never audio or recognized words.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from gofer.utils.logging import get_logger

log = get_logger(__name__)

HELPER_ENV = "GOFER_SPEECH_HELPER"
# macOS asks for permission the first time; that dialog waits for the user.
READY_TIMEOUT_SECONDS = 120.0
FINISH_TIMEOUT_SECONDS = 8.0
CHECK_TIMEOUT_SECONDS = 20.0
CHECK_CACHE_SECONDS = 60.0
# Audio is kept until the recognizer is ready, so it can be replayed into the offline
# model if the OS recognizer turns out to be unavailable. Two minutes is plenty.
REPLAY_MAX_BYTES = 16_000 * 2 * 120
# The helpers write nothing to stderr themselves; what lands there comes from the OS
# (a missing library, a refused signature), so its last lines are kept for the log.
STDERR_TAIL_BYTES = 2_000

MACOS_NAME = "macOS speech recognition"
WINDOWS_NAME = "Windows speech recognition"
LINUX_REASON = "Linux doesn't include a speech recognizer, so Raticode uses its own offline model."


@dataclass(frozen=True)
class SystemEngine:
    """The recognizer this OS offers, or why it offers none."""

    id: str
    name: str
    command: tuple[str, ...] = ()
    check_command: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    check_env: tuple[tuple[str, str], ...] = ()
    reason: str = ""
    # For the log: what was looked for and found, such as the helper's path.
    detail: str = ""

    @property
    def present(self) -> bool:
        return bool(self.command)


def system_engine(
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> SystemEngine:
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    if platform == "darwin":
        helper = str(environ.get(HELPER_ENV) or "").strip()
        problem = _helper_problem(helper)
        if problem:
            return SystemEngine(
                "macos",
                MACOS_NAME,
                reason="This copy of Raticode doesn't include its macOS speech helper.",
                detail=problem,
            )
        return SystemEngine(
            "macos",
            MACOS_NAME,
            command=(helper, "listen"),
            check_command=(helper, "check"),
            detail=f"helper {helper}",
        )
    if platform == "win32":
        powershell = _windows_powershell(environ)
        if not powershell:
            return SystemEngine(
                "windows",
                WINDOWS_NAME,
                reason="Windows PowerShell wasn't found on this PC.",
                detail="no powershell.exe under SystemRoot or on PATH",
            )
        command = (
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            _encoded_powershell(WINDOWS_SPEECH_SCRIPT),
        )
        return SystemEngine(
            "windows",
            WINDOWS_NAME,
            command=command,
            check_command=command,
            env=(("RATICODE_SPEECH_MODE", "listen"),),
            check_env=(("RATICODE_SPEECH_MODE", "check"),),
            detail=f"PowerShell {powershell}",
        )
    return SystemEngine("", "", reason=LINUX_REASON, detail=f"platform {platform}")


def _helper_problem(helper: str) -> str:
    """Why the macOS helper can't be run, in the words a log reader needs; "" if it can."""
    if not helper:
        return f"{HELPER_ENV} is not set, so the desktop app didn't pass a helper path"
    path = Path(helper)
    try:
        if not path.exists():
            return f"no helper at {helper}"
        if not path.is_file():
            return f"{helper} is not a file"
    except OSError as exc:
        return f"{helper} can't be read: {exc}"
    if not os.access(helper, os.X_OK):
        return f"{helper} is not executable"
    return ""


_check_cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}
_check_lock = threading.Lock()
# The last check result logged per engine, so a Settings page polling the same answer
# doesn't fill the log.
_logged_checks: dict[str, tuple[Any, ...]] = {}


def check_system_engine(
    engine: SystemEngine | None = None,
    *,
    refresh: bool = False,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Whether the OS recognizer can be used now, without recording or asking permission."""
    engine = engine or system_engine()
    result: dict[str, Any] = {"id": engine.id, "name": engine.name}
    if not engine.present:
        if engine.id:
            _log_check(engine, False, engine.reason, engine.detail)
        return {**result, "available": False, "reason": engine.reason}
    key = engine.check_command
    now = time.monotonic()
    with _check_lock:
        cached = _check_cache.get(key)
        if cached and not refresh and now - cached[0] < CHECK_CACHE_SECONDS:
            return dict(cached[1])
    try:
        completed = run(
            list(engine.check_command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CHECK_TIMEOUT_SECONDS,
            env=_helper_env(engine.check_env),
            **_no_window(),
        )
    except subprocess.TimeoutExpired:
        status = {"available": False, "reason": f"{engine.name} didn't answer in time."}
        how = f"check timed out after {CHECK_TIMEOUT_SECONDS:.0f} s"
    except OSError as exc:
        status = {"available": False, "reason": f"{engine.name} couldn't start: {exc}"}
        how = f"check couldn't start ({type(exc).__name__})"
    else:
        status = _check_status(engine, completed)
        how = f"check exited {_describe_exit(completed.returncode)}"
        if completed.returncode and completed.stderr:
            how += f"; stderr: {_tail(completed.stderr)}"
    _log_check(engine, status.get("available") is True, str(status.get("reason") or ""), how)
    result.update(status)
    with _check_lock:
        _check_cache[key] = (now, dict(result))
    return result


def try_system_engine(
    engine: SystemEngine | None = None,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Starts the macOS recognizer once with no audio, so macOS asks for its permission.

    `check` never asks, so without this the question only comes up in the middle of the
    first dictation. The helper gets an empty input: nothing is recorded or recognized.
    Only the macOS helper is tested; Windows needs no permission and `check` covers it.
    """
    engine = engine or system_engine()
    result: dict[str, Any] = {"id": engine.id, "name": engine.name}
    if engine.id != "macos":
        return {**result, "ok": False, "message": "Only macOS speech recognition needs a test."}
    if not engine.present:
        return {**result, "ok": False, "message": engine.reason}
    log.info("Testing %s (%s)", engine.name, engine.detail)
    try:
        completed = run(
            list(engine.command),
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            # macOS's permission dialog waits for the user.
            timeout=READY_TIMEOUT_SECONDS,
            env=_helper_env(engine.env),
            **_no_window(),
        )
    except subprocess.TimeoutExpired:
        ok, message, how = False, f"{engine.name} didn't answer in time.", "test timed out"
    except OSError as exc:
        ok, message, how = False, f"{engine.name} couldn't start: {exc}", "test couldn't start"
    else:
        ok, message = _trial_outcome(engine, completed)
        how = f"test exited {_describe_exit(completed.returncode)}"
        if not ok and completed.stderr:
            how += f"; stderr: {_tail(completed.stderr)}"
    if ok:
        log.info("%s test passed (%s)", engine.name, how)
    else:
        log.warning("%s test failed: %s (%s)", engine.name, message, how)
    with _check_lock:
        # The permission may have just changed; the next check asks the helper again.
        _check_cache.pop(engine.check_command, None)
    return {**result, "ok": ok, "message": message}


def _trial_outcome(
    engine: SystemEngine, completed: subprocess.CompletedProcess[str]
) -> tuple[bool, str]:
    events = [event for event in map(_parse_event, completed.stdout.splitlines()) if event]
    for event in events:
        if event.get("type") == "error":
            return False, _clip(event.get("message")) or f"{engine.name} stopped."
    ready = next((event for event in events if event.get("type") == "ready"), None)
    if ready is not None and completed.returncode == 0:
        language = _clip(ready.get("language"), 40)
        return True, (
            f"{engine.name} works" + (f" ({language})" if language else "") + ". "
            "Speech is recognized on this Mac."
        )
    return False, _exit_reason(engine, completed.returncode)


def _log_check(engine: SystemEngine, available: bool, reason: str, how: str) -> None:
    key = (available, reason)
    with _check_lock:
        if _logged_checks.get(engine.id) == key:
            return
        _logged_checks[engine.id] = key
    if available:
        log.info("%s is available for dictation (%s)", engine.name, engine.detail or how)
    else:
        log.warning(
            "%s is unavailable for dictation: %s (%s)",
            engine.name,
            reason or "no reason given",
            "; ".join(part for part in (engine.detail, how) if part),
        )


def _check_status(
    engine: SystemEngine, completed: subprocess.CompletedProcess[str]
) -> dict[str, Any]:
    events = [event for event in map(_parse_event, completed.stdout.splitlines()) if event]
    for event in reversed(events):
        if event.get("type") == "status":
            return {
                "available": event.get("available") is True,
                "reason": _clip(event.get("reason")),
                "language": _clip(event.get("language"), 40),
            }
        if event.get("type") == "error":
            return {"available": False, "reason": _clip(event.get("message"))}
    return {"available": False, "reason": _exit_reason(engine, completed.returncode)}


class SystemSpeechSession:
    """One dictation through the OS recognizer, fed audio as it is recorded."""

    def __init__(
        self,
        engine: SystemEngine,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not engine.present:
            raise ValueError(engine.reason or "No speech recognizer is available.")
        self.engine = engine
        self._clock = clock
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._done = threading.Event()
        self._committed: list[str] = []
        self._partial = ""
        self._failure = ""
        self._failure_code = ""
        self._replay: list[bytes] | None = []
        self._replay_bytes = 0
        self._started = clock()
        self._closing = False
        self._pushed_bytes = 0
        self._stderr: deque[bytes] = deque()
        self._stderr_bytes = 0
        self._writes: queue.Queue[bytes | None] = queue.Queue()
        try:
            self._process = popen(
                list(engine.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_helper_env(engine.env),
                **_no_window(),
            )
        except OSError as exc:
            log.warning("%s couldn't start its helper (%s): %s", engine.name, engine.detail, exc)
            raise
        log.info(
            "%s helper started (process %s, %s)",
            engine.name,
            getattr(self._process, "pid", "unknown"),
            engine.detail or engine.command[0],
        )
        threading.Thread(target=self._read, name="speech-helper-read", daemon=True).start()
        threading.Thread(target=self._write, name="speech-helper-write", daemon=True).start()
        if getattr(self._process, "stderr", None) is not None:
            threading.Thread(
                target=self._read_stderr, name="speech-helper-stderr", daemon=True
            ).start()

    # The desktop window's side.

    def push(self, pcm: bytes) -> None:
        with self._lock:
            if self._failure or self._done.is_set():
                return
            if self._replay is not None:
                if self._replay_bytes + len(pcm) <= REPLAY_MAX_BYTES:
                    self._replay.append(pcm)
                    self._replay_bytes += len(pcm)
                if self._clock() - self._started > READY_TIMEOUT_SECONDS:
                    self._fail_locked("timeout", f"{self.engine.name} didn't start in time.")
                    return
            self._pushed_bytes += len(pcm)
        self._writes.put(pcm)

    def text(self) -> str:
        with self._lock:
            return _join(*self._committed, self._partial)

    @property
    def failure(self) -> str:
        with self._lock:
            return self._failure

    @property
    def failure_code(self) -> str:
        with self._lock:
            return self._failure_code

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._replay is None

    def replay(self) -> list[bytes]:
        """Audio heard before the recognizer was ready; empty once it was."""
        with self._lock:
            return list(self._replay or [])

    def finish(self, timeout: float = FINISH_TIMEOUT_SECONDS) -> str:
        """Ends the audio and waits for the last phrase. Words heard so far are kept on timeout."""
        self._writes.put(None)
        if not self._done.wait(timeout):
            log.warning(
                "%s didn't finish within %.0f s of the end of the audio; "
                "keeping the words heard so far",
                self.engine.name,
                timeout,
            )
            with self._lock:
                self._committed = [_join(*self._committed, self._partial)]
                self._partial = ""
        self.close()
        return self.text()

    def close(self) -> None:
        with self._lock:
            self._closing = True
        self._writes.put(None)
        process = self._process
        if self._done.is_set() and process.poll() is None:
            # It said it was done and is about to exit on its own; let it, so its exit
            # status in the log is its own.
            try:
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    # The helper's side.

    def _read(self) -> None:
        stdout: IO[bytes] = self._process.stdout
        try:
            for raw in iter(stdout.readline, b""):
                event = _parse_event(raw.decode("utf-8", errors="replace"))
                if event:
                    self._handle(event)
        except (OSError, ValueError):
            pass
        returncode = self._wait_exit()
        with self._lock:
            closing = self._closing
            if not self._done.is_set() and not self._failure and not closing:
                if returncode not in (0, None) or not self._ready.is_set():
                    self._fail_locked("exited", _exit_reason(self.engine, returncode))
            failure, code = self._failure, self._failure_code
            was_ready = self._ready.is_set() and self._replay is None
            seconds = self._pushed_bytes / 32_000
        self._ready.set()
        self._done.set()
        exit_text = _describe_exit(returncode)
        if failure or (returncode not in (0, None) and not closing):
            stderr = self.stderr_tail()
            log.warning(
                "%s helper exited %s after %.1f s of audio (%s%s)%s",
                self.engine.name,
                exit_text,
                seconds,
                f"failure {code}" if failure else "no failure reported",
                "" if was_ready else ", before it was ready",
                f"; stderr: {stderr}" if stderr else "",
            )
        else:
            log.info(
                "%s helper exited %s after %.1f s of audio",
                self.engine.name,
                exit_text,
                seconds,
            )

    def stderr_tail(self) -> str:
        with self._lock:
            return _tail(b"".join(self._stderr).decode("utf-8", errors="replace"))

    def _read_stderr(self) -> None:
        stderr: IO[bytes] = self._process.stderr
        read = getattr(stderr, "read1", stderr.read)
        try:
            for raw in iter(lambda: read(4096), b""):
                with self._lock:
                    self._stderr.append(raw)
                    self._stderr_bytes += len(raw)
                    while self._stderr_bytes > STDERR_TAIL_BYTES and len(self._stderr) > 1:
                        self._stderr_bytes -= len(self._stderr.popleft())
        except (OSError, ValueError):
            pass

    def _wait_exit(self) -> int | None:
        try:
            return int(self._process.wait(timeout=5))
        except (subprocess.TimeoutExpired, OSError, TypeError, ValueError):
            return None

    def _handle(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        with self._lock:
            if kind == "ready":
                log.info(
                    "%s is ready after %.1f s (language %s)",
                    self.engine.name,
                    self._clock() - self._started,
                    _clip(event.get("language"), 40) or "unknown",
                )
                self._replay = None
                self._replay_bytes = 0
                self._ready.set()
            elif kind == "partial":
                self._partial = _clip(event.get("text"), 20_000)
            elif kind == "final":
                text = _clip(event.get("text"), 20_000)
                if text:
                    self._committed.append(text)
                self._partial = ""
            elif kind == "error":
                self._fail_locked(
                    _clip(event.get("code"), 40) or "error",
                    _clip(event.get("message")) or f"{self.engine.name} stopped.",
                )
            elif kind == "done":
                self._done.set()

    def _fail_locked(self, code: str, message: str) -> None:
        if self._failure:
            return
        log.warning("%s failed (%s): %s", self.engine.name, code, message)
        # Words of the phrase in progress stay with the dictation.
        if self._partial:
            self._committed.append(self._partial)
            self._partial = ""
        self._failure = message
        self._failure_code = code
        self._ready.set()

    def _write(self) -> None:
        stdin: IO[bytes] = self._process.stdin
        while True:
            chunk = self._writes.get()
            if chunk is None:
                break
            try:
                stdin.write(chunk)
                stdin.flush()
            except (OSError, ValueError):
                break
        try:
            stdin.close()
        except (OSError, ValueError):
            pass


def _exit_reason(engine: SystemEngine, returncode: int | None) -> str:
    if engine.id == "macos" and returncode is not None and returncode < 0:
        if returncode != -signal.SIGABRT:
            # A crash (SIGTRAP, SIGSEGV) or a refused code signature (SIGKILL), which the
            # permission text below would misdescribe. The log has the signal's name.
            return (
                f"Raticode's macOS speech helper stopped unexpectedly "
                f"({_signal_name(-returncode)})."
            )
        # TCC aborts a process that asks for speech recognition from an app whose
        # Info.plist lacks NSSpeechRecognitionUsageDescription, such as a development
        # Electron build.
        return (
            "macOS stopped Raticode's speech helper. This happens when Raticode runs "
            "without its speech permission text, such as a development build; use the "
            "installed Raticode app."
        )
    if engine.id == "windows" and returncode == 4:
        return (
            "Windows has no speech recognizer for dictation installed. Add one under "
            "Settings > Time & language > Speech, or use Raticode's offline model."
        )
    return f"{engine.name} stopped unexpectedly."


def _describe_exit(returncode: int | None) -> str:
    if returncode is None:
        return "with an unknown status"
    if returncode < 0:
        return f"on {_signal_name(-returncode)} (code {returncode})"
    return f"with code {returncode}"


def _signal_name(number: int) -> str:
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"signal {number}"


def _tail(text: str, limit: int = 600) -> str:
    """The end of a helper's stderr, on one line, for the log."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    joined = " | ".join(lines)
    return joined if len(joined) <= limit else "…" + joined[-limit:]


def _parse_event(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _clip(value: Any, limit: int = 400) -> str:
    return str(value or "").strip()[:limit]


def _join(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def _helper_env(extra: tuple[tuple[str, str], ...]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GOFER_")}
    env.update(extra)
    return env


def _no_window() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _windows_powershell(environ: Mapping[str, str]) -> str:
    root = environ.get("SystemRoot") or environ.get("SYSTEMROOT") or r"C:\Windows"
    candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("powershell.exe") or shutil.which("powershell") or ""


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


# Windows PowerShell 5.1 ships with every Windows 10 and 11 install, and with it the
# .NET Framework's System.Speech, the offline recognizer that Windows Speech
# Recognition used. The C# part is compiled by Add-Type: it reads audio from stdin in
# full blocks (SAPI treats a short read as the end of the audio) and reports phrases as
# JSON lines. Text is ASCII-escaped so the console code page can't garble it.
WINDOWS_SPEECH_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
function Say([string]$line) { [Console]::Out.WriteLine($line); [Console]::Out.Flush() }
try {
  Add-Type -AssemblyName System.Speech
} catch {
  Say ('{"type":"error","code":"missing","message":"Windows speech recognition ' +
    '(System.Speech) isn''t available on this PC."}')
  exit 3
}
$speech = [System.Speech.Recognition.SpeechRecognitionEngine].Assembly.Location
try {
  Add-Type -ReferencedAssemblies $speech -TypeDefinition @'
using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Speech.AudioFormat;
using System.Speech.Recognition;

// A live stream of unknown length that only ends when Raticode closes it.
public class RaticodePipeAudio : Stream {
  private readonly Stream inner;
  public RaticodePipeAudio(Stream inner) { this.inner = inner; }
  public override bool CanRead { get { return true; } }
  public override bool CanSeek { get { return false; } }
  public override bool CanWrite { get { return false; } }
  public override long Length { get { return -1L; } }
  public override long Position { get { return 0L; } set { } }
  public override void Flush() { }
  public override int Read(byte[] buffer, int offset, int count) {
    int total = 0;
    while (total < count) {
      int read = inner.Read(buffer, offset + total, count - total);
      if (read <= 0) break;
      total += read;
    }
    return total;
  }
  public override long Seek(long offset, SeekOrigin origin) { return 0L; }
  public override void SetLength(long value) { }
  public override void Write(byte[] buffer, int offset, int count) {
    throw new NotSupportedException();
  }
}

public static class RaticodeSpeech {
  private static readonly object Gate = new object();

  private static string Quote(string value) {
    var text = new StringBuilder("\"");
    foreach (char c in value ?? "") {
      if (c == '"' || c == '\\') { text.Append('\\').Append(c); }
      else if (c < 32 || c > 126) { text.Append("\\u").Append(((int)c).ToString("x4")); }
      else { text.Append(c); }
    }
    return text.Append('"').ToString();
  }

  private static void Emit(string type, string key, string value, string extra) {
    string line = "{\"type\":" + Quote(type);
    if (key != null) line += "," + Quote(key) + ":" + Quote(value);
    if (extra != null) line += "," + extra;
    line += "}";
    lock (Gate) { Console.Out.WriteLine(line); Console.Out.Flush(); }
  }

  private static void Fail(string message) {
    Emit("error", "message", message, "\"code\":\"recognizer\"");
  }

  private static RecognizerInfo Pick() {
    var installed = SpeechRecognitionEngine.InstalledRecognizers();
    if (installed.Count == 0) return null;
    CultureInfo culture = CultureInfo.CurrentUICulture;
    foreach (RecognizerInfo info in installed) if (info.Culture.Name == culture.Name) return info;
    foreach (RecognizerInfo info in installed) {
      if (info.Culture.TwoLetterISOLanguageName == culture.TwoLetterISOLanguageName) return info;
    }
    foreach (RecognizerInfo info in installed) if (info.Culture.Name == "en-US") return info;
    return installed[0];
  }

  public static int Run(string mode) {
    RecognizerInfo info;
    try { info = Pick(); }
    catch (Exception error) {
      Fail("Windows speech recognition couldn't start: " + error.Message);
      return 3;
    }
    if (info == null) {
      string reason = "Windows has no speech recognizer for dictation installed. "
        + "Add one under Settings > Time & language > Speech, or use Raticode's offline model.";
      if (mode == "check") { Emit("status", "reason", reason, "\"available\":false"); return 0; }
      Emit("error", "message", reason, "\"code\":\"no-recognizer\"");
      return 4;
    }
    if (mode == "check") {
      Emit("status", "language", info.Culture.Name, "\"available\":true");
      return 0;
    }
    var finished = new ManualResetEvent(false);
    using (var engine = new SpeechRecognitionEngine(info)) {
      try {
        engine.LoadGrammar(new DictationGrammar());
        engine.SetInputToAudioStream(
          new RaticodePipeAudio(Console.OpenStandardInput()),
          new SpeechAudioFormatInfo(16000, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
      } catch (Exception error) {
        Fail("Windows speech recognition couldn't start dictation: " + error.Message);
        return 3;
      }
      engine.SpeechHypothesized += delegate(object sender, SpeechHypothesizedEventArgs e) {
        Emit("partial", "text", e.Result.Text, null);
      };
      engine.SpeechRecognized += delegate(object sender, SpeechRecognizedEventArgs e) {
        Emit("final", "text", e.Result.Text, null);
      };
      engine.RecognizeCompleted += delegate(object sender, RecognizeCompletedEventArgs e) {
        if (e.Error != null) {
          Fail("Windows speech recognition stopped: " + e.Error.Message);
        }
        finished.Set();
      };
      Emit("ready", "language", info.Culture.Name, null);
      engine.RecognizeAsync(RecognizeMode.Multiple);
      finished.WaitOne();
    }
    Emit("done", null, null, null);
    return 0;
  }
}
'@
} catch {
  Say ('{"type":"error","code":"blocked","message":"Windows blocked Raticode''s speech ' +
    'script. PowerShell may be restricted on this PC by its administrator."}')
  exit 3
}
exit ([RaticodeSpeech]::Run([string]$env:RATICODE_SPEECH_MODE))
"""
