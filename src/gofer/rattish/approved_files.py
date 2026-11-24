"""Re-check a triggered run's code files right before each one is used.

A trigger runs only the workflow version the user approved (``trigger_approval``). The
scheduler compares fingerprints before the run starts, but while it runs an earlier
step, or any other program, could still rewrite a script a later step runs. So a
triggered run that needs approval carries the approved SHA-256 of each file the approval
covers, and the step that uses a file checks it first:

- Python scripts are checked immediately before Python starts.
- Shell scripts run from a checked copy in a per-run folder under the data directory,
  because Bash reads a script while it runs. A sandboxed agent shell can't see or write
  that folder.
- Agent prompt files and ``prompt-file`` templates are read once, and the bytes checked
  are the bytes used.
- Child workflows are checked against the source they were compiled from, and so is
  the workflow itself when the run starts.
- An ``mcp-tool`` step's server definition from Rem's list is checked right before the
  server starts or is called. It is keyed ``mcp-server:<name>`` instead of a path.
  Each project file its command or arguments name is checked as a script right before
  a local server starts (``check_server_file``). It is checked in place, as a Python
  script is, so a server that reads its script while it runs isn't covered beyond that
  moment.

A file that differs fails its step with ``CHANGED_MESSAGE``, and ``on_changed`` lets the
scheduler mark the trigger as changed and tell the phone. Files the approval doesn't
name, such as paths known only at run time, aren't checked. Manual runs carry no
approved files, so nothing here applies to them.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gofer.core.resources import read_bytes_limited
from gofer.utils.logging import get_logger

log = get_logger(__name__)

CHANGED_MESSAGE = "This file changed during the run, so it wasn't run."
SERVER_CHANGED_MESSAGE = (
    "This MCP server's definition changed since you approved the workflow, so it wasn't started."
)
CHANGED_CODE = "RATTISH_APPROVED_FILE_CHANGED"
# Under the data directory: the sandboxed agent shell is denied all of it except its
# own run-scratch folder, so it can't read or replace a checked copy.
COPY_FOLDER = "trigger-run-scripts"
# Copies left by a run that crashed are removed by a later run after this long.
STALE_COPY_SECONDS = 24 * 60 * 60
_CHUNK = 1024 * 1024

ChangedCallback = Callable[[str, str], None]

_ACTIVE: ContextVar[ApprovedFiles | None] = ContextVar("rattish_approved_files", default=None)


class ApprovedFileChanged(Exception):
    """A file the approval covers differs from the approved version, so it isn't used."""

    def __init__(self, path: Path, role: str) -> None:
        super().__init__(SERVER_CHANGED_MESSAGE if role == "mcp_server" else CHANGED_MESSAGE)
        self.path = path
        self.role = role

    def details(self) -> dict[str, Any]:
        return {"path": str(self.path), "role": self.role, "retryable": False}


@dataclass(slots=True)
class ApprovedFiles:
    """The approved SHA-256 of each file one triggered run may use, by absolute path.

    ``None`` as a digest means the file was missing when it was approved.
    ``on_changed(path, role)`` runs once per changed file.
    """

    digests: Mapping[str, str | None]
    data_dir: Path
    run_id: str
    on_changed: ChangedCallback | None = None
    _reported: set[str] = field(default_factory=set)
    _copies: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Any,
        *,
        data_dir: Path,
        run_id: str,
        on_changed: ChangedCallback | None = None,
    ) -> ApprovedFiles:
        """From a ``trigger_approval.Snapshot``: the version the trigger's user approved."""
        return cls(
            digests={item.path: item.sha256 for item in snapshot.files},
            data_dir=Path(data_dir),
            run_id=run_id,
            on_changed=on_changed,
        )

    def lookup(self, path: Path) -> tuple[bool, str | None]:
        """Whether the approval covers ``path``, and its approved digest."""
        for key in _keys(path):
            if key in self.digests:
                return True, self.digests[key]
        return False, None

    def require(self, path: Path, role: str, digest: str | None) -> None:
        """Raise ``ApprovedFileChanged`` unless ``digest`` is the approved one."""
        covered, expected = self.lookup(path)
        if not covered or digest == expected:
            return
        self.report(path, role)
        raise ApprovedFileChanged(path, role)

    def report(self, path: Path, role: str) -> None:
        with self._lock:
            if str(path) in self._reported:
                return
            self._reported.add(str(path))
        log.warning("Triggered run %s: %s changed since it was approved", self.run_id, path)
        if self.on_changed is None:
            return
        try:
            self.on_changed(str(path), role)
        except Exception:  # noqa: BLE001 - the step still fails; the notice is extra
            log.exception("Could not record that %s changed during run %s", path, self.run_id)

    @property
    def copy_dir(self) -> Path:
        return self.data_dir / COPY_FOLDER / self.run_id

    def next_copy_name(self, path: Path) -> str:
        with self._lock:
            self._copies += 1
            return f"{self._copies:03d}-{path.name}"

    def remove_copies(self) -> None:
        shutil.rmtree(self.copy_dir, ignore_errors=True)


def _keys(path: Path) -> tuple[str, ...]:
    try:
        resolved = str(path.resolve())
    except (OSError, RuntimeError):
        return (str(path),)
    return (str(path), resolved)


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _file_digest(path: Path) -> str | None:
    """The file's SHA-256, or None when it can't be read (gone, or not a file)."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
    except OSError:
        return None
    return f"sha256:{digest.hexdigest()}"


def active() -> ApprovedFiles | None:
    """The approved files of the triggered run executing now, if it needs approval."""
    return _ACTIVE.get()


@contextmanager
def guarding(files: ApprovedFiles | None) -> Iterator[None]:
    """Check every covered file the run uses against ``files``; None checks nothing.

    The run's checked copies are removed when it ends.
    """
    if files is None:
        yield
        return
    _remove_stale_copies(files.data_dir)
    token = _ACTIVE.set(files)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
        files.remove_copies()


def _remove_stale_copies(data_dir: Path) -> None:
    root = data_dir / COPY_FOLDER
    cutoff = time.time() - STALE_COPY_SECONDS
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def read_text(
    path: Path,
    *,
    role: str,
    encoding: str = "utf-8",
    errors: str = "strict",
    max_bytes: int,
) -> str:
    """Read a prompt file or template, checking the bytes it returns.

    Decodes like ``read_text_limited``, so a run with nothing to check reads the same text.
    """
    data = read_bytes_limited(path, max_bytes=max_bytes)
    files = active()
    if files is not None:
        files.require(path, role, _digest(data))
    return data.decode(encoding, errors=errors).replace("\r\n", "\n").replace("\r", "\n")


def check_file(path: Path, role: str) -> None:
    """Check a file whose reader is another program, immediately before it starts."""
    files = active()
    if files is None or not files.lookup(path)[0]:
        return
    files.require(path, role, _file_digest(path))


def check_server_file(path: Path) -> None:
    """Check a project file an MCP server's command or arguments name, before it starts.

    Like ``check_file``, but a file the approval doesn't name fails too: the server
    definition is approved, so a project file it names that appeared after the approval
    (written by an earlier step, say) is code nobody reviewed.
    """
    files = active()
    if files is None:
        return
    if not files.lookup(path)[0]:
        files.report(path, "script")
        raise ApprovedFileChanged(path, "script")
    check_file(path, "script")


def script_to_run(path: Path, interpreter: str) -> Path:
    """The path to hand ``interpreter`` for the script at ``path``.

    Python reads a whole script before it runs any of it, so the script is checked in
    place and keeps its folder as ``sys.path[0]``. Bash reads a script while it runs, so
    it gets a checked copy that nothing else can change; the copy's bytes are the bytes
    checked.
    """
    files = active()
    if files is None or not files.lookup(path)[0]:
        return path
    if interpreter != "bash":
        check_file(path, "script")
        return path
    return _checked_copy(files, path)


def _checked_copy(files: ApprovedFiles, path: Path) -> Path:
    folder = files.copy_dir
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if sys.platform != "win32":
        folder.chmod(0o700)
    target = folder / files.next_copy_name(path)
    digest = hashlib.sha256()
    try:
        source = path.open("rb")
    except OSError:
        files.require(path, "script", None)
        raise
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with source, os.fdopen(os.open(target, flags, 0o600), "wb") as copy:
        while chunk := source.read(_CHUNK):
            digest.update(chunk)
            copy.write(chunk)
    try:
        files.require(path, "script", f"sha256:{digest.hexdigest()}")
    except ApprovedFileChanged:
        target.unlink(missing_ok=True)
        raise
    target.chmod(0o400)
    return target


def check_compiled(
    path: Path, ir: Mapping[str, Any], role: str, *, files: ApprovedFiles | None = None
) -> None:
    """Check a workflow file against the approval and against the source ``ir`` came from.

    The compiler reads the file itself, so reading it again here and matching its
    fingerprint shows the compiled text is the approved text. ``files`` defaults to the
    running run's.
    """
    files = files if files is not None else active()
    if files is None or not files.lookup(path)[0]:
        return
    try:
        data = path.read_bytes()
    except OSError:
        files.require(path, role, None)
        return
    files.require(path, role, _digest(data))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    compiled = ir.get("source", {}).get("source_fingerprint")
    if text is not None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if compiled == _digest(normalized.encode("utf-8")):
            return
    files.report(path, role)
    raise ApprovedFileChanged(path, role)
