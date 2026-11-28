"""A running run's journal: every finished step, on disk before the next one starts.

The record ``<run_id>.json`` of a running run is saved at most once a second
(``_LiveRecord`` in ``run_service``), so a crash could lose the steps of its last
second and Resume would run them again. Beside it, ``<run_id>.journal`` (mode 0600)
gets one JSON line per event and node run as soon as the run has checked them:
``{"event": {...}, "node_run": {...} | null}``. Each line is written straight to the
operating system, so a killed runner loses at most the step that was running.

When a record is read as interrupted, ``fold_into`` adds the journal's entries past
the record's last event to it; the caller then saves the record and deletes the
journal. A torn last line, from a runner killed mid-write, is ignored. A run's final
save deletes its journal, and ``delete_run_files`` deletes it with the run.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from gofer.rattish.contracts import canonical_json_bytes
from gofer.utils.atomic_output import open_binary_input, unlink_without_links

log = logging.getLogger(__name__)

JOURNAL_SUFFIX = ".journal"


def journal_path(record: Path) -> Path:
    """The journal of the run saved at ``record``."""
    # Writes and removals refuse linked parents, so name the folder by its real path.
    return record.parent.resolve() / f"{record.stem}{JOURNAL_SUFFIX}"


def journal_line(event: dict[str, Any], node_run: dict[str, Any] | None) -> bytes:
    """One entry's line. Raises ``TypeError`` or ``ValueError`` for values JSON can't hold."""
    return canonical_json_bytes({"event": event, "node_run": node_run}) + b"\n"


class RunJournal:
    """The journal a running run appends to. Writing is best effort: the record still saves."""

    def __init__(self, record: Path) -> None:
        self.path = journal_path(record)
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_TRUNC
            | os.O_APPEND
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        self._descriptor: int | None = os.open(self.path, flags, 0o600)

    def append(self, line: bytes) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        try:
            view = memoryview(line)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
        except OSError as exc:
            # The once-a-second record save still runs; only crash recovery gets coarser.
            log.warning("Stopped writing run journal %s: %s", self.path.name, exc)
            self.close()

    def close(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def finish(self) -> None:
        """Close and delete the journal, once the run's final record is saved."""
        self.close()
        remove_journal(self.path)


def remove_journal(path: Path) -> None:
    try:
        unlink_without_links(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not delete run journal %s: %s", path.name, exc)


def read_entries(path: Path) -> list[dict[str, Any]] | None:
    """The journal's entries in order, or None when it doesn't exist or can't be read.

    Reading stops at the first line that isn't a whole entry: only the last line can
    be torn, by a runner killed while writing it.
    """
    try:
        with open_binary_input(path) as stream:
            data = stream.read()
    except OSError:
        return None
    entries: list[dict[str, Any]] = []
    for line in data.split(b"\n")[:-1]:  # The part after the last newline is torn.
        try:
            entry = json.loads(line)
        except (UnicodeError, json.JSONDecodeError):
            break
        if not isinstance(entry, dict) or not isinstance(entry.get("event"), dict):
            break
        entries.append(entry)
    return entries


def fold_into(document: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
    """Add the entries past ``document``'s last event to it; whether any were added.

    Entries must follow on from the record's last sequence; a gap ends the fold, so
    the record never skips an event.
    """
    events = document.get("events")
    runs = document.get("runs")
    latest = document.get("latest_node_outputs")
    if not isinstance(events, list) or not isinstance(runs, list) or not isinstance(latest, dict):
        return False
    last = events[-1].get("sequence", len(events)) if events else 0
    added = False
    for entry in entries:
        event = entry["event"]
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or sequence <= last:
            continue
        if sequence != last + 1:
            break
        events.append(event)
        node_run = entry.get("node_run")
        if isinstance(node_run, dict):
            runs.append(node_run)
            if node_run.get("outcome") == "success":
                latest[str(node_run.get("node_id"))] = node_run.get("output")
        last = sequence
        added = True
    return added
