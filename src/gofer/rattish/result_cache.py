"""Opt-in result cache for common-llm-task and jev nodes.

A node with ``cache: DURATION`` reuses a successful answer while it is younger than
that duration. Agent nodes never cache, because an agent can change files.

The key hashes the provider, profile, model and effort that would answer, the
rendered prompt and bound inputs, the output schema and the node's compiled form
(without its source position, routes and wiring, so editing other nodes or moving
this one keeps its entries). Changing the duration doesn't change the key: a
lookup checks an entry's age against the node's current duration.

Each workflow keeps its entries in its own folder under the data folder,
``rattish-cache/<workflow-id>/``. Folders are owner-only (0700) and entries are
owner-only files (0600). A folder holds at most ``MAX_WORKFLOW_BYTES``; writing
past that removes the entries that expire first. Each entry's file modification
time is set to its expiry, so pruning needs no reads.

Nodes that read a secret never cache, so no secret, nor an answer derived from one,
is written here. Test sets run without the cache.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.rattish.contracts import canonical_json_bytes, json_fingerprint
from gofer.utils.atomic_output import atomic_binary_output, mkdir_without_links

CACHE_FOLDER = "rattish-cache"
CACHEABLE_NODE_TYPES = frozenset({"common-llm-task", "jev"})
# One workflow's entries together. Writing past it removes the entries expiring first.
MAX_WORKFLOW_BYTES = 32 * 1024 * 1024
# A larger answer is used for its run but not saved.
MAX_ENTRY_BYTES = 2 * 1024 * 1024
MAX_CACHE_DURATION_SECONDS = 365 * 24 * 3600
KEY_VERSION = 1
ENTRY_VERSION = 1
DURATION_PATTERN = re.compile(r"([1-9][0-9]*)(s|m|h|d|w)")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_KEY_PATTERN = re.compile(r"[0-9a-f]{64}")
# The same rule as gofer.core.workflow.validate_workflow_id, without its heavy imports.
_WORKFLOW_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,127}")
# Parts of a compiled node that don't change its answer.
_NODE_FIELDS_OUTSIDE_KEY = frozenset({"source_span", "routes", "readiness"})


def cache_duration_seconds(value: Any) -> int | None:
    """Seconds in a ``cache`` duration such as ``7d``, or None when it isn't one."""
    if not isinstance(value, str):
        return None
    match = DURATION_PATTERN.fullmatch(value)
    if match is None:
        return None
    return int(match.group(1)) * _UNITS[match.group(2)]


def is_cacheable_workflow_id(workflow_id: str) -> bool:
    return _WORKFLOW_ID_PATTERN.fullmatch(workflow_id) is not None


def validate_workflow_id(workflow_id: str) -> str:
    if not is_cacheable_workflow_id(workflow_id):
        raise ValueError("Workflow id must match [a-z0-9][a-z0-9-]{0,127}")
    return workflow_id


def cache_root(data_dir: Path) -> Path:
    return data_dir / CACHE_FOLDER


def workflow_cache_dir(data_dir: Path, workflow_id: str) -> Path:
    return cache_root(data_dir) / validate_workflow_id(workflow_id)


def cache_key(
    node: Mapping[str, Any],
    *,
    project_root: str,
    prompt: Any,
    inputs: Mapping[str, Any],
) -> str:
    """The cache key of one node activation, as 64 hex digits.

    ``node`` is the compiled node with the provider that would answer already applied,
    so each fallback provider has its own entries.
    """
    configuration = dict(node["configuration"])
    configuration.pop("cache", None)
    compiled = {
        name: value
        for name, value in node.items()
        if name not in _NODE_FIELDS_OUTSIDE_KEY and name != "configuration"
    }
    compiled["configuration"] = configuration
    resolution = node["resolutions"].get("provider") or {}
    material = {
        "version": KEY_VERSION,
        "provider": resolution.get("provider_id") or node["type"],
        "profile": resolution.get("profile", configuration.get("profile")),
        "model": resolution.get("model", configuration.get("model")),
        "effort": resolution.get("effort", configuration.get("effort")),
        "prompt": prompt,
        "inputs": dict(inputs),
        "output_schema": node["output"]["schema"],
        "node": compiled,
        "project_root": project_root,
    }
    return json_fingerprint(json.loads(json.dumps(material, default=str))).removeprefix("sha256:")


@dataclass(frozen=True, slots=True)
class CachedResult:
    output: Any
    created_at: str


@dataclass(frozen=True, slots=True)
class CacheStats:
    entries: int
    bytes: int
    max_bytes: int = MAX_WORKFLOW_BYTES


class ResultCache:
    """One data folder's result cache. Workflows never share entries."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def _entry_path(self, workflow_id: str, key: str) -> Path:
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError("Invalid result cache key.")
        return workflow_cache_dir(self.data_dir, workflow_id) / f"{key}.json"

    def lookup(
        self,
        workflow_id: str,
        key: str,
        duration_seconds: int,
        *,
        now: datetime | None = None,
    ) -> CachedResult | None:
        """The saved answer for this key, if it is younger than ``duration_seconds``."""
        path = self._entry_path(workflow_id, key)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return None
        try:
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    return None
                raw = handle.read(MAX_ENTRY_BYTES + 1)
        except OSError:
            return None
        if len(raw) > MAX_ENTRY_BYTES:
            return None
        try:
            document = json.loads(raw)
            created = datetime.fromisoformat(document["created_at"])
            expires = datetime.fromisoformat(document["expires_at"])
        except (ValueError, TypeError, KeyError, UnicodeError):
            return None
        if not isinstance(document, dict) or document.get("version") != ENTRY_VERSION:
            return None
        if document.get("key") != key or "output" not in document:
            return None
        current = now or datetime.now(UTC)
        if current >= expires or current >= created + timedelta(seconds=duration_seconds):
            return None
        return CachedResult(document["output"], document["created_at"])

    def store(
        self,
        workflow_id: str,
        key: str,
        node_id: str,
        output: Any,
        duration_seconds: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Save a successful answer. False when it is too large to keep."""
        created = now or datetime.now(UTC)
        expires = created + timedelta(seconds=duration_seconds)
        document = {
            "version": ENTRY_VERSION,
            "key": key,
            "node_id": node_id,
            "created_at": created.isoformat(),
            "expires_at": expires.isoformat(),
            "output": output,
        }
        payload = canonical_json_bytes(document) + b"\n"
        if len(payload) > MAX_ENTRY_BYTES:
            return False
        path = self._entry_path(workflow_id, key)
        self._ensure_private_dirs(path.parent)
        expiry_ns = int(expires.timestamp() * 1_000_000_000)
        with atomic_binary_output(path, timestamps_ns=(expiry_ns, expiry_ns)) as handle:
            handle.write(payload)
        self._prune(path.parent, keep=path.name, now=created)
        return True

    def _ensure_private_dirs(self, directory: Path) -> None:
        mkdir_without_links(directory)
        if os.name == "nt":
            # Folders under the user's profile are already private to that user.
            return
        for folder in (cache_root(self.data_dir), directory):
            # mkdir honors the umask; an older folder may have been made wider.
            if stat.S_IMODE(folder.lstat().st_mode) != 0o700:
                os.chmod(folder, 0o700, follow_symlinks=False)

    def _entries(self, directory: Path) -> list[tuple[Path, os.stat_result]]:
        entries: list[tuple[Path, os.stat_result]] = []
        try:
            children = list(directory.iterdir())
        except OSError:
            return entries
        for child in children:
            if child.suffix != ".json" or not _KEY_PATTERN.fullmatch(child.stem):
                continue
            try:
                status = child.lstat()
            except OSError:
                continue
            if stat.S_ISREG(status.st_mode):
                entries.append((child, status))
        return entries

    def _prune(self, directory: Path, *, keep: str, now: datetime) -> None:
        """Remove expired entries, then the ones expiring first until under the cap."""
        now_seconds = now.timestamp()
        live: list[tuple[Path, os.stat_result]] = []
        for path, status in self._entries(directory):
            if status.st_mtime <= now_seconds and path.name != keep:
                path.unlink(missing_ok=True)
            else:
                live.append((path, status))
        total = sum(status.st_size for _, status in live)
        for path, status in sorted(live, key=lambda item: item[1].st_mtime):
            if total <= MAX_WORKFLOW_BYTES:
                break
            if path.name == keep:
                continue
            path.unlink(missing_ok=True)
            total -= status.st_size

    def stats(self, workflow_id: str, *, now: datetime | None = None) -> CacheStats:
        """Entries a lookup could still use, by expiry, and the space they take."""
        now_seconds = (now or datetime.now(UTC)).timestamp()
        entries = [
            status
            for _, status in self._entries(workflow_cache_dir(self.data_dir, workflow_id))
            if status.st_mtime > now_seconds
        ]
        return CacheStats(len(entries), sum(status.st_size for status in entries))

    def clear(self, workflow_id: str) -> int:
        """Remove one workflow's saved results. Returns how many were removed."""
        directory = workflow_cache_dir(self.data_dir, workflow_id)
        removed = 0
        for path, _ in self._entries(directory):
            path.unlink(missing_ok=True)
            removed += 1
        try:
            directory.rmdir()
        except OSError:
            pass
        return removed

    def clear_all(self) -> int:
        """Remove every workflow's saved results. Returns how many were removed."""
        root = cache_root(self.data_dir)
        removed = 0
        try:
            children = list(root.iterdir())
        except OSError:
            return 0
        for child in children:
            try:
                workflow_id = validate_workflow_id(child.name)
            except ValueError:
                continue
            if child.is_dir() and not child.is_symlink():
                removed += self.clear(workflow_id)
        return removed
