"""Device-local, idempotent usage accounting. Never persist prompts or credentials."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import sqlite3
import uuid
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec

from gofer.utils.paths import get_data_dir

P = ParamSpec("P")
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def profile_usage_identity(name: str, data_dir: Path | None = None) -> str:
    from gofer.core.provider_profiles import load_provider_profiles

    profile = load_provider_profiles(data_dir).get(name)
    if profile is None:
        return f"profile:{name}:missing"
    fingerprint = hashlib.sha256(profile.model_dump_json().encode()).hexdigest()[:16]
    return f"profile:{name}:{fingerprint}"


@contextmanager
def usage_db(data_dir: Path | None = None) -> Iterator[sqlite3.Connection]:
    root = data_dir or get_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "usage.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS usage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_invocations (
                id TEXT PRIMARY KEY, provider TEXT NOT NULL, profile TEXT,
                model TEXT, observed_at TEXT NOT NULL, status TEXT NOT NULL,
                tokens TEXT NOT NULL, estimated INTEGER NOT NULL DEFAULT 0,
                partial INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS usage_time ON usage_invocations(observed_at);
            CREATE TABLE IF NOT EXISTS usage_snapshots (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_rate_limits (
                provider TEXT NOT NULL, profile TEXT NOT NULL, model TEXT NOT NULL,
                observed_at TEXT NOT NULL, windows TEXT NOT NULL,
                PRIMARY KEY(provider, profile, model)
            );
        """)
        db.execute("INSERT OR IGNORE INTO usage_meta VALUES ('started_at', ?)", (now_iso(),))
        yield db
        db.commit()
    finally:
        db.close()


def record_invocation(
    *,
    invocation_id: str,
    provider: str,
    profile: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    status: str = "completed",
    data_dir: Path | None = None,
) -> None:
    """Replace a replayed invocation; new retries must have a new invocation id."""
    metadata = metadata or {}
    tokens = {
        key: value
        for key in TOKEN_FIELDS
        if isinstance(value := metadata.get(key), int)
        and not isinstance(value, bool)
        and value >= 0
    }
    if "total_tokens" not in tokens and {"input_tokens", "output_tokens"} <= tokens.keys():
        tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
    try:
        with usage_db(data_dir) as db:
            db.execute(
                """INSERT INTO usage_invocations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET tokens=excluded.tokens,
                status=excluded.status, estimated=excluded.estimated, partial=excluded.partial""",
                (
                    invocation_id,
                    provider,
                    profile,
                    model,
                    now_iso(),
                    status,
                    json.dumps(tokens),
                    int(metadata.get("estimated") is True),
                    int(metadata.get("partial") is True),
                ),
            )
            if provider in {"openai_api", "anthropic_api", "claude_code"} and isinstance(
                metadata.get("quota_windows"), list
            ):
                windows = []
                for raw in metadata["quota_windows"]:
                    if not isinstance(raw, dict) or raw.get("unit") not in {
                        "tokens",
                        "requests",
                        "percent",
                    }:
                        continue
                    window = {
                        key: raw[key]
                        for key in ("id", "label", "unit", "resets_at")
                        if isinstance(raw.get(key), str)
                    }
                    for key in ("remaining", "limit", "used", "remaining_percent"):
                        value = raw.get(key)
                        window[key] = (
                            value
                            if (
                                isinstance(value, int | float)
                                and not isinstance(value, bool)
                                and math.isfinite(value)
                                and value >= 0
                                and (key != "remaining_percent" or value <= 100)
                            )
                            else None
                        )
                    window["model"] = model
                    window["status"] = (
                        raw.get("status")
                        if raw.get("status") in {"allowed", "allowed_warning", "rejected"}
                        else None
                    )
                    windows.append(window)
                if windows:
                    observed_at = now_iso()
                    try:
                        observed = datetime.fromisoformat(str(metadata.get("quota_observed_at")))
                        if observed.tzinfo is not None and observed <= datetime.now(UTC):
                            observed_at = observed.astimezone(UTC).isoformat()
                    except (ValueError, TypeError):
                        pass
                    db.execute(
                        """INSERT INTO usage_rate_limits VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(provider, profile, model) DO UPDATE SET
                        observed_at=excluded.observed_at, windows=excluded.windows
                        WHERE excluded.observed_at >= usage_rate_limits.observed_at""",
                        (
                            provider,
                            profile_usage_identity(profile, data_dir) if profile else "",
                            model or "",
                            observed_at,
                            json.dumps(windows),
                        ),
                    )
    except (OSError, sqlite3.Error, ValueError, TypeError):
        # Accounting must never fail the user's model invocation.
        logger.warning("Could not save provider usage", exc_info=False)


def activity_payload(data_dir: Path | None = None, *, days: int = 30) -> dict[str, Any]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with usage_db(data_dir) as db:
        rows = db.execute(
            "SELECT * FROM usage_invocations WHERE observed_at >= ?", (cutoff,)
        ).fetchall()
        started = db.execute("SELECT value FROM usage_meta WHERE key='started_at'").fetchone()[0]
    groups: dict[tuple[str, str | None], dict[str, Any]] = {}

    def empty() -> dict[str, Any]:
        return {
            "calls": 0,
            "reported_calls": 0,
            "unknown_calls": 0,
            "partial_calls": 0,
            "estimated_calls": 0,
            **dict.fromkeys(TOKEN_FIELDS),
            "estimated_total_tokens": None,
        }

    total = empty()
    for row in rows:
        key = (row["provider"], row["profile"])
        group = groups.setdefault(key, {"provider": key[0], "profile": key[1], **empty()})
        tokens = json.loads(row["tokens"])
        complete = all(k in tokens for k in ("input_tokens", "output_tokens"))
        for target in (group, total):
            target["calls"] += 1
            if row["estimated"]:
                target["estimated_calls"] += 1
                if "total_tokens" in tokens:
                    target["estimated_total_tokens"] = (
                        target["estimated_total_tokens"] or 0
                    ) + tokens["total_tokens"]
            elif complete and not row["partial"]:
                target["reported_calls"] += 1
            elif tokens:
                target["partial_calls"] += 1
            else:
                target["unknown_calls"] += 1
            if not row["estimated"]:
                for field, value in tokens.items():
                    target[field] = (target[field] or 0) + value
    return {
        "tracking_started_at": started,
        "activity": {**total, "providers": list(groups.values())},
    }


def track_chat_usage(
    function: Callable[P, AsyncGenerator[dict[str, Any], None]],
) -> Callable[P, AsyncGenerator[dict[str, Any], None]]:
    """One ledger entry for a Rem/swarm turn, including cancelled stream consumers."""
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> AsyncGenerator[dict[str, Any], None]:
        from gofer.subscriptions.usage import normalize_usage

        bound = signature.bind(*args, **kwargs)
        provider = str(bound.arguments.get("provider", "unknown"))
        invocation_id = uuid.uuid4().hex
        metadata: dict[str, Any] = {}
        status = "failed"
        terminal = False
        source = function(*args, **kwargs)
        try:
            async for event in source:
                if isinstance(event.get("usage"), dict):
                    quota: dict[str, Any] = {
                        key: metadata[key]
                        for key in ("quota_windows", "quota_observed_at")
                        if key in metadata
                    }
                    metadata = normalize_usage(provider, event["usage"])
                    metadata = {**quota, **metadata}
                if event.get("type") == "final":
                    status = "completed"
                    terminal = True
                elif event.get("type") == "error":
                    status = "failed"
                    terminal = True
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            if not terminal:
                status = "cancelled"
            raise
        finally:
            try:
                await source.aclose()
            finally:
                cancel = bound.arguments.get("cancel_event")
                if cancel is not None and cancel.is_set():
                    status = "cancelled"
                if not terminal and metadata:
                    metadata["partial"] = True
                record_invocation(
                    invocation_id=invocation_id,
                    provider=provider,
                    model=bound.arguments.get("model"),
                    metadata=metadata,
                    status=status,
                    data_dir=bound.arguments.get("data_dir"),
                )

    return wrapped
