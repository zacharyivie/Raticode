"""PDF text extraction runs in a child process that a stuck or crashing PDF can't stall."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_pdf
from gofer.ui.brain_documents import extract
from gofer.ui.brain_library import BrainLibrary, disk_fingerprint
from tests.helpers.brain_library import wait_task

# Reading PDFs in a separate, limited process takes longer than other library tasks.
PDF_TIMEOUT = 30

POSIX = pytest.mark.skipif(os.name == "nt", reason="checks POSIX process groups and rlimits")

# A stand-in PDF reader. Each launch is logged; each PDF it reads is logged with its own
# and its helper's process IDs. The "startup" file next to the log sets how it starts:
# "slow" waits 2.5 s before saying ready, "never" never says ready. A PDF whose bytes say
# "slow" starts a helper and sleeps; one that says "crash" dies.
STUB = """
import json, os, subprocess, sys, time
log = {log!r}
startup = os.path.join(os.path.dirname(log), "startup")
mode = open(startup).read().strip() if os.path.exists(startup) else ""
with open(log + ".launches", "a") as handle:
    handle.write(f"{{os.getpid()}} {{mode}}\\n")
if mode == "never":
    time.sleep(300)
if mode == "slow":
    time.sleep(2.5)
sys.stdout.buffer.write(b"ready\\n")
sys.stdout.buffer.flush()
data = sys.stdin.buffer.read()
helper = None
if b"slow" in data:
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
with open(log, "a") as handle:
    handle.write(f"{{os.getpid()}} {{helper.pid if helper else 0}} {{data[:16]!r}}\\n")
    handle.flush()
if b"slow" in data:
    time.sleep(300)
if b"crash" in data:
    os.abort()
pages = {{"pages": ["Quick evidence text"], "title": "", "author": ""}}
sys.stdout.write(json.dumps({{"ok": pages}}))
"""


@pytest.fixture
def stub_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "reader-starts.log"
    script = tmp_path / "stub_reader.py"
    script.write_text(STUB.format(log=str(log)), encoding="utf-8")
    monkeypatch.setattr(brain_pdf, "_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(brain_pdf, "TIME_LIMIT", 1.5)
    return log


def launches(log: Path) -> list[str]:
    path = log.with_name(log.name + ".launches")
    return path.read_text().splitlines() if path.exists() else []


def starts(log: Path) -> list[list[str]]:
    return [line.split(" ", 2) for line in log.read_text().splitlines()] if log.exists() else []


def gone(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # A killed helper is a zombie until init reaps it; that counts as gone.
        try:
            if Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].startswith("Z"):
                return True
        except (OSError, IndexError):
            pass
        time.sleep(0.05)
    return False


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


@POSIX
def test_slow_pdf_fails_once_and_the_scan_finishes(library, tmp_path, stub_reader):
    root = tmp_path / "brain"
    root.mkdir()
    (root / "quick.pdf").write_bytes(b"%PDF-1.7 quick")
    (root / "slow.pdf").write_bytes(b"%PDF-1.7 slow")
    (root / "notes.md").write_text("# Notes\n\nPlain evidence.\n", encoding="utf-8")

    began = time.monotonic()
    brain = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        assert wait_task(library, task, timeout=PDF_TIMEOUT)["state"] == "done"
    # The reader sleeps for 300 s; the scan finishes once the 1.5 s limit kills it.
    assert time.monotonic() - began < 20

    docs = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    slow = docs["slow.pdf"]
    assert slow["extraction"] == "failed"
    assert slow["error"] == "This PDF took too long to read"
    assert slow["fingerprint"] == disk_fingerprint(root, "slow.pdf")
    assert docs["quick.pdf"]["extraction"] == "ready"
    assert docs["quick.pdf"]["blocks"][0]["text"] == "Quick evidence text"
    assert docs["notes.md"]["extraction"] == "ready"
    assert brain["id"] not in library._scan_pending

    # The killed reader and the helper it started are both stopped.
    slow_start = next(start for start in starts(stub_reader) if "slow" in start[2])
    assert gone(int(slow_start[0]))
    assert gone(int(slow_start[1]))

    # Library health lists it with the other files that failed to extract.
    issues = [
        issue
        for issue in library.snapshot()["health"]
        if issue["kind"] == "extraction" and issue["docId"] == slow["id"]
    ]
    assert [issue["detail"] for issue in issues] == ["This PDF took too long to read"]

    # A second scan doesn't read it again while the file is unchanged.
    readers = len(starts(stub_reader))
    second = library.start_task("index", {"brainId": brain["id"]})
    assert wait_task(library, second, timeout=PDF_TIMEOUT)["state"] == "done"
    assert len(starts(stub_reader)) == readers
    again = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    assert again["slow.pdf"]["extraction"] == "failed"
    assert brain["id"] not in library._scan_pending

    # A changed file is read again.
    (root / "slow.pdf").write_bytes(b"%PDF-1.7 fixed now")
    third = library.start_task("index", {"brainId": brain["id"]})
    assert wait_task(library, third, timeout=PDF_TIMEOUT)["state"] == "done"
    fixed = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    assert fixed["slow.pdf"]["extraction"] == "ready"
    assert len(starts(stub_reader)) == readers + 1


def test_crashed_reader_is_reported_as_too_slow(stub_reader):
    with pytest.raises(brain_pdf.PdfReadStopped, match="This PDF took too long to read"):
        extract(b"%PDF-1.7 crash", ".pdf")


@POSIX
def test_cancel_stops_the_reader_and_is_not_a_failure(stub_reader):
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    began = time.monotonic()
    with pytest.raises(InterruptedError):
        extract(b"%PDF-1.7 slow", ".pdf", cancel=cancel)
    assert time.monotonic() - began < 1.4
    pid, helper, _ = starts(stub_reader)[0]
    assert gone(int(pid)) and gone(int(helper))


def test_reader_errors_keep_their_message():
    # The real child: pypdf's own error comes back as a ValueError, not a timeout.
    with pytest.raises(ValueError) as caught:
        extract(b"this is not a PDF", ".pdf")
    assert not isinstance(caught.value, brain_pdf.PdfReadStopped)


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux")
def test_memory_cap_stops_a_reader_that_allocates_too_much(tmp_path, monkeypatch):
    script = tmp_path / "greedy.py"
    script.write_text(
        "import sys\n"
        "from gofer.ui.brain_pdf import _limit_resources\n"
        "_limit_resources()\n"
        "sys.stdout.buffer.write(b'ready\\n'); sys.stdout.buffer.flush()\n"
        "hoard = bytearray(2 * 1024 * 1024 * 1024)\n"
        'print(\'{"ok": {"pages": []}}\')\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(brain_pdf, "_command", lambda: [sys.executable, str(script)])
    with pytest.raises(brain_pdf.PdfReadStopped):
        brain_pdf.read_pdf(b"%PDF-1.7", max_text=1024)


def brain_with_pdfs(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "brain"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF-1.7 first")
    (root / "b.pdf").write_bytes(b"%PDF-1.7 second")
    (root / "notes.md").write_text("# Notes\n\nPlain evidence.\n", encoding="utf-8")
    brain = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        assert wait_task(library, task, timeout=PDF_TIMEOUT)["state"] == "done"
    return brain, root


def test_slow_starting_reader_is_not_marked_failed(library, tmp_path, stub_reader):
    # The packaged app unpacks itself before the reader is ready. That time doesn't count
    # against the PDF: a 2.5 s start with a 1.5 s limit still reads both files.
    (tmp_path / "startup").write_text("slow")
    brain, _root = brain_with_pdfs(library, tmp_path)
    docs = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    for path in ("a.pdf", "b.pdf"):
        assert docs[path]["extraction"] == "ready", docs[path]["error"]
        assert docs[path]["blocks"][0]["text"] == "Quick evidence text"
    assert len(starts(stub_reader)) == 2


@POSIX
def test_reader_that_never_starts_leaves_pdfs_for_the_next_scan(
    library, tmp_path, stub_reader, monkeypatch
):
    monkeypatch.setattr(brain_pdf, "STARTUP_LIMIT", 1.0)
    (tmp_path / "startup").write_text("never")
    began = time.monotonic()
    brain, _root = brain_with_pdfs(library, tmp_path)
    # One wait for the reader, not one per PDF.
    assert time.monotonic() - began < 15
    assert len(launches(stub_reader)) == 1
    assert gone(int(launches(stub_reader)[0].split()[0]))

    docs = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    for path in ("a.pdf", "b.pdf"):
        # Not "failed": the PDF wasn't read, so nothing says it is bad.
        assert docs[path]["extraction"] == "pending"
        assert docs[path]["error"] == brain_pdf.NOT_STARTED
    assert docs["notes.md"]["extraction"] == "ready"
    assert brain["id"] not in library._scan_pending

    # The next scan reads the unchanged files once the reader starts again.
    (tmp_path / "startup").write_text("")
    again = library.start_task("index", {"brainId": brain["id"]})
    assert wait_task(library, again, timeout=PDF_TIMEOUT)["state"] == "done"
    docs = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    assert docs["a.pdf"]["extraction"] == "ready"
    assert docs["b.pdf"]["extraction"] == "ready"
