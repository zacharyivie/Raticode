"""The computer's alert store: notices workflows leave for the paired phone.

A ``notification`` node with ``channel: phone`` adds one notice here with its title,
body, workflow and run ID. The run may come from the desktop window, the command line
or the background scheduler, so the store is a small SQLite file in the data folder
that any of those processes can write. The phone reads new notices through
``rem.notices`` at its next sync, and ``Raticode rem notices`` reads the same ones.

Each notice has a position that only grows. A reader with no position, or one past
the end after the file was cleared, only learns the current position, so turning the
alert on never reports older notices. The newest ``KEEP`` notices are kept; a reader
whose position is older than the ones removed is told how many it missed (``dropped``).

One run keeps at most ``RUN_CAP`` notices. Past that, the run has a single "And N more
from this run" notice. Each further notice deletes it and adds it again with a larger
N at the newest position, so a phone that already showed it hears the new count.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALERTS_FILE = "phone-alerts.sqlite3"
# Notices kept; older ones are removed as new ones arrive.
KEEP = 500
# Notices one run keeps before the rest fold into one "And N more" notice.
RUN_CAP = 20
# Notices returned by one read; ``more`` says there are others after them.
READ_ROWS = 50
TITLE_LIMIT = 160
BODY_LIMIT = 1500
# The alert switch these notices count under, on the phone and in ``rem notices``.
WORKFLOW_REASON = "workflow"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_name TEXT NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    more INTEGER NOT NULL DEFAULT 0
)
"""
# The highest position removed to keep the newest ``KEEP``; readers behind it missed some.
_META = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
_COLUMNS = "seq, reason, source, title, body, workflow_id, workflow_name, run_id, created_at, more"


def _clip(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    return text[:limit]


class PhoneAlertStore:
    """Notices for the phone, in ``<data dir>/phone-alerts.sqlite3``."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / ALERTS_FILE

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        if new:
            # Created private: a body may quote what a workflow found.
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(_SCHEMA)
            db.execute(_META)
            columns = {row[1] for row in db.execute("PRAGMA table_info(alerts)")}
            if "more" not in columns:
                # A file from before the per-run cap.
                db.execute("ALTER TABLE alerts ADD COLUMN more INTEGER NOT NULL DEFAULT 0")
            yield db
        finally:
            db.close()

    def add(
        self,
        *,
        title: str,
        body: str,
        workflow_id: str,
        run_id: str,
        workflow_name: str = "",
        source: str = "notification",
    ) -> dict[str, Any]:
        """Store one workflow notice and return it as a reader will see it.

        ``source`` says what left it: a notification node, or later Raticode's own
        alert when a run fails. Both count under the phone's Workflow messages switch.
        A run past ``RUN_CAP`` notices moves its one "And N more" notice forward instead;
        the returned notice then has ``more_from_run`` set to N.
        """
        created = datetime.now(UTC).isoformat(timespec="seconds")
        run = _clip(run_id, 128)
        row: tuple[Any, ...] = (
            WORKFLOW_REASON,
            _clip(source, 40),
            _clip(title, TITLE_LIMIT),
            _clip(body, BODY_LIMIT),
            _clip(workflow_id, 200),
            _clip(workflow_name, TITLE_LIMIT),
            run,
            created,
            0,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = _fold(db, row)
                cursor = db.execute(
                    "INSERT INTO alerts (reason, source, title, body, workflow_id, "
                    "workflow_name, run_id, created_at, more) VALUES (?,?,?,?,?,?,?,?,?)",
                    row,
                )
                seq = int(cursor.lastrowid or 0)
                _trim(db)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return _alert((seq, *row))

    def after(self, after: int | None) -> dict[str, Any]:
        """Notices after position ``after``, oldest first, and the position after them.

        ``dropped`` counts the positions after ``after`` that were removed before this
        read, so the phone can say messages were missed. Those are notices pruned to
        keep the newest ``KEEP``, plus any "And N more" notice that moved on from there.
        """
        empty = {"last": 0, "more": False, "dropped": 0, "alerts": []}
        if not self.path.exists():
            return empty
        with self._connect() as db:
            latest = int(db.execute("SELECT COALESCE(MAX(seq), 0) FROM alerts").fetchone()[0])
            if after is None or after > latest:
                return {**empty, "last": latest}
            trimmed = db.execute("SELECT value FROM meta WHERE key='trimmed_through'").fetchone()
            rows = db.execute(
                f"SELECT {_COLUMNS} FROM alerts WHERE seq > ? ORDER BY seq LIMIT ?",
                (after, READ_ROWS),
            ).fetchall()
        last = int(rows[-1][0]) if rows else latest
        return {
            "last": last,
            "more": last < latest,
            "dropped": max(0, int(trimmed[0]) - after) if trimmed else 0,
            "alerts": [_alert(row) for row in rows],
        }


def _fold(db: sqlite3.Connection, row: tuple[Any, ...]) -> tuple[Any, ...]:
    """The row to store: ``row`` itself, or the run's "And N more" notice once it is full."""
    run = row[6]
    if not run:
        return row
    kept = db.execute("SELECT COUNT(*) FROM alerts WHERE run_id=? AND more=0", (run,)).fetchone()[0]
    if int(kept) < RUN_CAP:
        return row
    folded = db.execute(
        "SELECT seq, more FROM alerts WHERE run_id=? AND more>0 ORDER BY seq DESC LIMIT 1",
        (run,),
    ).fetchone()
    count = 1 + (int(folded[1]) if folded else 0)
    if folded:
        db.execute("DELETE FROM alerts WHERE seq=?", (folded[0],))
    body = f"And {count} more from this run."
    return (*row[:2], "", body, *row[4:8], count)


def _trim(db: sqlite3.Connection) -> None:
    """Keep the newest ``KEEP`` notices and remember the highest position removed."""
    boundary = db.execute(
        "SELECT seq FROM alerts ORDER BY seq DESC LIMIT 1 OFFSET ?", (KEEP,)
    ).fetchone()
    if boundary is None:
        return
    db.execute("DELETE FROM alerts WHERE seq <= ?", (boundary[0],))
    db.execute(
        "INSERT INTO meta (key, value) VALUES ('trimmed_through', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=MAX(value, excluded.value)",
        (boundary[0],),
    )


def _alert(row: tuple[Any, ...]) -> dict[str, Any]:
    seq, reason, source, title, body, workflow_id, workflow_name, run_id, created, more = row
    return {
        "id": f"workflow-{seq}",
        "reason": reason,
        "source": source,
        "title": title,
        "body": body,
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "run_id": run_id,
        "at": created,
        # Set on a run's one "And N more" notice: how many notices past the cap it stands for.
        "more_from_run": int(more),
    }


def phone_paired(data_dir: Path) -> bool:
    """Whether a phone is paired with this computer, read without unlocking any keys."""
    path = Path(data_dir) / "devices" / "devices.sqlite3"
    if not path.is_file():
        return False
    try:
        db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return False
    try:
        row = db.execute(
            "SELECT 1 FROM peers WHERE role='controller' AND state IN ('confirmed','active') "
            "LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        db.close()
    return row is not None
