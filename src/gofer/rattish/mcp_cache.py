"""Package caches for local MCP servers that ``mcp-tool`` steps start.

Each server definition in Rem's list gets its own lasting folder,
``<data>/mcp-cache/<definition digest>/``, with ``npm`` and ``xdg`` inside. A sandboxed
server's npm, XDG and uv caches point there, so a package runner such as ``npx`` or
``uvx`` downloads the server's package once instead of on every start. Preflight, the
Studio's tool picker, ``Raticode rem mcp tools`` and runs all use the same folder.

Only list-only starts, which carry no workflow data, may write the folder. A tool call
reads it through a throwaway layer or copy (``mcp_tool`` sets that up per platform), so a call
can't leave files behind for later starts. A call that finds no list-only start has
filled the folder yet (``LISTED_MARKER``) runs one first.

The folder is keyed by the definition digest: a changed command, argument list or name
starts with an empty cache, and two servers never share one. The cache goes when its
server leaves the list or its definition changes, when it hasn't been used for
``UNUSED_DAYS`` days, or when the user clears it.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

CACHE_FOLDER = "mcp-cache"
UNUSED_DAYS = 30
# Touched on every start. Folder access times aren't reliable (noatime mounts).
USED_MARKER = ".last-used"
# Touched when a list-only start finishes, so a tool call knows the packages are in.
LISTED_MARKER = ".listed"
# Pruning runs on a start at most this often.
_PRUNE_MARKER = ".last-prune"
PRUNE_EVERY_SECONDS = 24 * 60 * 60


def cache_root(data_dir: Path) -> Path:
    return data_dir.resolve() / CACHE_FOLDER


def folder_name(digest: str) -> str:
    """The folder name for a definition digest: its hex part (``:`` isn't safe on Windows)."""
    return digest.removeprefix("sha256:")


def cache_dir(data_dir: Path, digest: str) -> Path:
    return cache_root(data_dir) / folder_name(digest)


def server_digest(server: Mapping[str, Any]) -> str | None:
    """The digest a local server's cache is keyed by; None for an HTTP server."""
    from gofer.rattish import mcp_tool

    if not mcp_tool.is_local(server):
        return None
    return mcp_tool.definition_digest(server)


def prepare(data_dir: Path, digest: str) -> Path:
    """Create (or reuse) the cache folder for ``digest`` and mark it used now."""
    root = cache_root(data_dir)
    folder = root / folder_name(digest)
    for path in (root, folder, folder / "npm", folder / "xdg"):
        path.mkdir(mode=0o700, exist_ok=True)
    (folder / USED_MARKER).touch()
    return folder


def mark_listed(folder: Path) -> None:
    """Record that a list-only start finished with this cache folder."""
    if folder.is_dir():
        with contextlib.suppress(OSError):
            (folder / LISTED_MARKER).touch()


def listed(folder: Path) -> bool:
    """Whether a list-only start has filled this cache folder since it was created."""
    return (folder / LISTED_MARKER).is_file()


def _last_used(folder: Path) -> float:
    marker = folder / USED_MARKER
    with contextlib.suppress(OSError):
        return marker.stat().st_mtime
    with contextlib.suppress(OSError):
        return folder.stat().st_mtime
    return 0.0


def folder_size(path: Path, *, stop_after: int | None = None) -> int:
    """The bytes the files in ``path`` hold; with ``stop_after``, counting stops once
    the total passes it."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, name)).st_size
            if stop_after is not None and total > stop_after:
                return total
    return total


def _remove(folder: Path) -> int:
    """Delete ``folder`` and return the bytes it held."""
    if not folder.is_dir():
        return 0
    size = folder_size(folder)
    shutil.rmtree(folder, ignore_errors=True)
    return size


def remove(data_dir: Path, digest: str | None) -> int:
    """Delete the cache for ``digest``; the bytes freed (0 when there was none)."""
    if not digest:
        return 0
    return _remove(cache_dir(data_dir, digest))


def remove_for_server(data_dir: Path, server: Mapping[str, Any] | None) -> int:
    if server is None:
        return 0
    return remove(data_dir, server_digest(server))


def clear_all(data_dir: Path) -> int:
    """Delete every server's cache; the bytes freed."""
    root = cache_root(data_dir)
    if not root.is_dir():
        return 0
    return sum(_remove(child) for child in root.iterdir() if child.is_dir())


def prune(
    data_dir: Path,
    *,
    keep: Iterable[str] | None = None,
    now: float | None = None,
    max_age_days: float = UNUSED_DAYS,
) -> list[str]:
    """Delete caches unused for ``max_age_days`` and, given ``keep``, those of other digests.

    ``keep`` holds the digests of the servers in Rem's list. A folder for any other digest
    belongs to a definition that was changed or removed outside Raticode's own editing,
    so nothing will use it again. Returns the folder names deleted.
    """
    root = cache_root(data_dir)
    if not root.is_dir():
        return []
    current = time.time() if now is None else now
    limit = current - max_age_days * 24 * 60 * 60
    kept = {folder_name(digest) for digest in keep} if keep is not None else None
    removed: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        stale = _last_used(child) < limit
        orphan = kept is not None and child.name not in kept
        if stale or orphan:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed


def library_digests(data_dir: Path) -> set[str] | None:
    """The digests of the local servers in Rem's list; None when the list can't be read."""
    from gofer.core import rem_library

    try:
        servers = rem_library.load_library(data_dir).mcpServers
    except (OSError, ValueError):
        return None
    digests: set[str] = set()
    for server in servers:
        digest = server_digest(
            server.model_dump(include={"name", "type", "url", "command", "args", "env"})
        )
        if digest:
            digests.add(digest)
    return digests


def prune_if_due(data_dir: Path, *, now: float | None = None) -> list[str]:
    """Prune at most once every ``PRUNE_EVERY_SECONDS``, keeping the list's servers."""
    root = cache_root(data_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = time.time() if now is None else now
    marker = root / _PRUNE_MARKER
    with contextlib.suppress(OSError):
        if current - marker.stat().st_mtime < PRUNE_EVERY_SECONDS:
            return []
    with contextlib.suppress(OSError):
        marker.touch()
        os.utime(marker, (current, current))
    return prune(data_dir, keep=library_digests(data_dir), now=current)
