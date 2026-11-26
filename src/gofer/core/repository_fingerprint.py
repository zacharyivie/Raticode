"""A repository's fingerprint: the same value for every clone, and never a path.

Paired desktops match a project here to one open there by this value
(gofer.core.placement_eligibility). It is the SHA-256 of the repository's root
commit IDs (``git rev-list --max-parents=0 HEAD``), sorted and joined with newlines.
Every clone of a repository has the same root commits, whatever folder it is in, and
the hash reveals nothing about the folder. A folder that isn't a git repository, or
has no commits yet, has no fingerprint.

Readings are kept for ``KEEP_SECONDS`` per folder, so placement and the catalog a
desktop shares don't run git on every pass. Callers that hold a lock (a device
session answering a catalog request, Rem's admission, an organization's claim) use
``known``, which never runs git itself: it answers from the kept reading and reads
a missing or old one on a background thread, answering ``PENDING`` meanwhile.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from gofer.utils.process import build_subprocess_env, run_repository_git

KEEP_SECONDS = 300.0
TIMEOUT_SECONDS = 10.0
# ``known``'s answer while a folder is being read; never a fingerprint, so it matches none.
PENDING = "pending"
_kept: dict[str, tuple[float, str | None]] = {}
_reading: set[str] = set()
_lock = threading.Lock()


def from_root_commits(commits: list[str]) -> str | None:
    """The fingerprint of a repository with these root commit IDs, or None without any."""
    ids = sorted({c.strip().lower() for c in commits if c.strip()})
    if not ids:
        return None
    return hashlib.sha256("\n".join(ids).encode("ascii")).hexdigest()


def _read(root: str) -> str | None:
    try:
        done = run_repository_git(
            root,
            "rev-list",
            "--max-parents=0",
            "HEAD",
            timeout=TIMEOUT_SECONDS,
            env={**build_subprocess_env(), "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if done.returncode:
        return None
    commits = done.stdout.split()
    if not all(len(c) in (40, 64) and all(ch in "0123456789abcdef" for ch in c) for c in commits):
        return None
    return from_root_commits(commits)


def _key(root: str | Path | None) -> str | None:
    if not root:
        return None
    try:
        return str(Path(root).expanduser().resolve())
    except (OSError, RuntimeError):
        return None


def _keep(key: str, now: float, value: str | None) -> None:
    with _lock:
        if len(_kept) >= 512:
            _kept.clear()
        _kept[key] = (now, value)


def fingerprint(
    root: str | Path | None, *, clock: Callable[[], float] = time.monotonic
) -> str | None:
    """This folder's repository fingerprint, or None when it has none. May run git."""
    key = _key(root)
    if key is None:
        return None
    now = clock()
    with _lock:
        kept = _kept.get(key)
        if kept is not None and now - kept[0] < KEEP_SECONDS:
            return kept[1]
    value = _read(key) if Path(key).is_dir() else None
    _keep(key, now, value)
    return value


def known(root: str | Path | None, *, clock: Callable[[], float] = time.monotonic) -> str | None:
    """The kept fingerprint, None for no repository, or ``PENDING``. Never runs git here.

    A folder with no fresh reading is read on a background thread; until it is in,
    the answer is ``PENDING``, or the old reading while an old one is refreshed.
    """
    key = _key(root)
    if key is None:
        return None
    now = clock()
    with _lock:
        kept = _kept.get(key)
        if kept is not None and now - kept[0] < KEEP_SECONDS:
            return kept[1]
        if key not in _reading:
            _reading.add(key)
            threading.Thread(
                target=_background, args=(key, clock), name="repository-fingerprint", daemon=True
            ).start()
    return kept[1] if kept is not None else PENDING


def _background(key: str, clock: Callable[[], float]) -> None:
    try:
        _keep(key, clock(), _read(key) if Path(key).is_dir() else None)
    finally:
        with _lock:
            _reading.discard(key)


def forget() -> None:
    """Drop every kept reading (tests)."""
    with _lock:
        _kept.clear()
