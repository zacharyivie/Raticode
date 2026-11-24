"""Read a PDF's text in a child process that can be stopped.

A broken or crafted PDF can make pypdf loop or allocate without end, and a scan that
reads it in the backend never finishes. Each PDF is read in its own child process with
a wall-clock limit and, on Linux and macOS, an address-space cap. A child that runs out
of time, memory or crashes is killed, and the caller records the file as failed.

The limit starts when the child says it is ready, not when it is started: the packaged
app is a single file that unpacks itself on every start, which can take a while on a slow
disk or under a virus scanner. Startup has its own, longer limit; a reader that never
starts says nothing about the PDF, so the caller tries that file again on a later scan.

The child imports nothing from Raticode beyond this module, so it starts quickly and
stays well under the memory cap.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Wall-clock time one PDF may take, and the child's address-space cap.
TIME_LIMIT = 60.0
MEMORY_LIMIT = 1024 * 1024 * 1024
MAX_PAGES = 500
TOO_SLOW = "This PDF took too long to read"
# Time the child may take to start and load pypdf before the PDF's own limit begins.
STARTUP_LIMIT = 180.0
NOT_STARTED = "The PDF reader didn't start. Raticode tries this file again on the next scan."
# The child writes this once pypdf is loaded, then reads the PDF from stdin.
READY = b"ready\n"

# ``Raticode _pdf-text`` (gofer.cli.main) runs child_main without loading the CLI.
CHILD_ARGUMENT = "_pdf-text"
# Environment the child keeps. Credentials and the packaged app's own variables stay out.
_KEEP_ENV = {
    "PATH",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SYSTEMROOT",
    "WINDIR",
    "LD_LIBRARY_PATH_ORIG",
}


class PdfReadStopped(ValueError):
    """The PDF reader ran out of time or memory, or crashed."""

    def __init__(self) -> None:
        super().__init__(TOO_SLOW)


class PdfReaderNotStarted(ValueError):
    """The reader process couldn't start or load pypdf; the PDF itself wasn't read."""

    def __init__(self) -> None:
        super().__init__(NOT_STARTED)


def read_pages(data: bytes, max_text: int) -> dict[str, Any]:
    """The text of each page and the document's title and author, read in this process."""
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("Unlock the encrypted PDF before indexing it")
    if len(reader.pages) > MAX_PAGES:
        raise ValueError(f"PDF exceeds the {MAX_PAGES} page extraction limit")
    pages: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        total += len(text.encode("utf-8"))
        if total > max_text:
            raise ValueError("Extracted PDF text exceeds 2 MB")
        pages.append(text)
    metadata: Any = reader.metadata
    return {
        "pages": pages,
        "title": str((metadata or {}).get("/Title") or ""),
        "author": str((metadata or {}).get("/Author") or ""),
    }


def _limit_resources() -> None:
    """Cap the child's address space and CPU time where the system allows it."""
    if sys.platform not in {"linux", "darwin"}:
        return
    import resource

    # CPU time is a backstop for a child whose parent died before it could kill it.
    for limit, value in (
        (resource.RLIMIT_AS, MEMORY_LIMIT),
        (resource.RLIMIT_CPU, int(TIME_LIMIT) + 10),
    ):
        try:
            _soft, hard = resource.getrlimit(limit)
            wanted = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(limit, (wanted, hard))
        except (ValueError, OSError):
            # macOS refuses some address-space limits; the wall-clock limit still holds.
            pass


def child_main(argv: list[str]) -> int:
    """Say ready once pypdf is loaded, read PDF bytes from stdin, write JSON to stdout."""
    _limit_resources()
    try:
        max_text = int(argv[0])
        import pypdf  # noqa: F401  (loaded before the parent's time limit starts)
    except Exception:
        return 2
    sys.stdout.buffer.write(READY)
    sys.stdout.buffer.flush()
    try:
        data = sys.stdin.buffer.read()
        reply: dict[str, Any] = {"ok": read_pages(data, max_text)}
    except MemoryError:
        return 3
    except Exception as exc:
        reply = {"error": str(exc)[:1000] or type(exc).__name__}
    sys.stdout.write(json.dumps(reply))
    sys.stdout.flush()
    return 0


def _command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, CHILD_ARGUMENT]
    # -P keeps the working folder off the import path; PYTHONPATH names this package.
    return [
        sys.executable,
        "-P",
        "-c",
        "import sys; from gofer.ui.brain_pdf import child_main;"
        " raise SystemExit(child_main(sys.argv[1:]))",
    ]


def _environment() -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name in _KEEP_ENV}
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    # A packaged app that starts itself must unpack its own copy, not share the parent's.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _kill(process: subprocess.Popen[bytes]) -> None:
    """Stop the child and anything it started (a packaged app runs as two processes)."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
        process.kill()
    else:
        # SIGTERM first lets a packaged app's launcher remove its unpacked files. The
        # whole group gets SIGKILL afterwards even when the leader has already exited.
        for sent in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sent)
            except OSError:
                if process.returncode is None:
                    process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _wait(
    event: threading.Event, until: float, cancel: threading.Event | None, done: threading.Event
) -> bool:
    """Wait for ``event`` (or ``done``) until the monotonic time ``until``; False on timeout."""
    while not (event.is_set() or done.is_set()):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Indexing cancelled")
        remaining = until - time.monotonic()
        if remaining <= 0:
            return False
        event.wait(min(0.1, remaining))
    return True


def read_pdf(
    data: bytes,
    *,
    max_text: int,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Read a PDF in a child process.

    Raises PdfReadStopped when the PDF takes longer than TIME_LIMIT once the reader is
    ready, or the reader crashes or runs out of memory while reading it. Raises
    PdfReaderNotStarted when the reader isn't ready within STARTUP_LIMIT. The child's
    own errors (encrypted, too many pages, unreadable) come back as ValueError with their
    message. ``cancel`` stops the child and raises InterruptedError.
    """
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(
        [*_command(), str(max_text)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_environment(),
        **options,
    )
    assert process.stdin is not None and process.stdout is not None
    stdin, stdout = process.stdin, process.stdout
    ready = threading.Event()
    done = threading.Event()
    output = bytearray()

    def collect() -> None:
        try:
            head = stdout.read(len(READY))
            if head == READY:
                ready.set()
                output.extend(stdout.read())
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    def send() -> None:
        try:
            stdin.write(data)
            stdin.close()
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=collect, name="pdf-reader-output", daemon=True)
    reader.start()
    writer: threading.Thread | None = None
    try:
        if not _wait(ready, time.monotonic() + STARTUP_LIMIT, cancel, done):
            raise PdfReaderNotStarted
        if not ready.is_set():
            raise PdfReaderNotStarted
        # The PDF's own limit starts now that the reader has started and loaded pypdf.
        writer = threading.Thread(target=send, name="pdf-reader-input", daemon=True)
        writer.start()
        deadline = time.monotonic() + TIME_LIMIT
        if not _wait(done, deadline, cancel, done):
            raise PdfReadStopped
        try:
            process.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise PdfReadStopped from None
    except BaseException:
        _kill(process)
        raise
    finally:
        reader.join(timeout=5)
        if writer is not None:
            writer.join(timeout=5)
        for pipe in (stdin, stdout):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
    if process.returncode != 0:
        raise PdfReadStopped
    try:
        reply = json.loads(bytes(output))
    except ValueError:
        raise PdfReadStopped from None
    if not isinstance(reply, dict):
        raise PdfReadStopped
    if "error" in reply:
        raise ValueError(str(reply["error"]))
    result = reply.get("ok")
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("pages"), list)
        or not all(isinstance(text, str) for text in result["pages"])
    ):
        raise PdfReadStopped
    return result
