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
from collections.abc import AsyncGenerator, Callable, Iterable, Iterator, Mapping
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


# Raised when the ledger's tables, columns, indexes or triggers change, so an open only
# prepares the file when it is older than this code.
USAGE_SCHEMA = 1
_USAGE_TABLES = (
    "CREATE TABLE IF NOT EXISTS usage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS usage_invocations (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, profile TEXT,
        model TEXT, observed_at TEXT NOT NULL, status TEXT NOT NULL,
        tokens TEXT NOT NULL, estimated INTEGER NOT NULL DEFAULT 0,
        partial INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS usage_time ON usage_invocations(observed_at)",
    """CREATE TABLE IF NOT EXISTS usage_snapshots (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, checked_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS usage_rate_limits (
        provider TEXT NOT NULL, profile TEXT NOT NULL, model TEXT NOT NULL,
        observed_at TEXT NOT NULL, windows TEXT NOT NULL,
        PRIMARY KEY(provider, profile, model)
    )""",
)


def _prepare_usage_db(db: sqlite3.Connection) -> None:
    """Create or upgrade the ledger once; later opens see the schema version and skip it."""
    if db.execute("PRAGMA user_version").fetchone()[0] >= USAGE_SCHEMA:
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        # Another process may have prepared it while this one waited for the lock.
        if db.execute("PRAGMA user_version").fetchone()[0] >= USAGE_SCHEMA:
            db.rollback()
            return
        for statement in _USAGE_TABLES:
            db.execute(statement)
        # Which run and thread made each call, so tickets and self-work can add up their use.
        columns = {row[1] for row in db.execute("PRAGMA table_info(usage_invocations)")}
        # A Rattish run also names the node that made the call, and what the call cost
        # when the provider reported it (NULL is unknown, never zero).
        for column, kind in (
            ("run_id", "TEXT"),
            ("thread_id", "TEXT"),
            ("node_id", "TEXT"),
            ("cost_usd", "REAL"),
        ):
            if column not in columns:
                db.execute(f"ALTER TABLE usage_invocations ADD COLUMN {column} {kind}")
        db.execute("CREATE INDEX IF NOT EXISTS usage_run ON usage_invocations(run_id)")
        db.execute("INSERT OR IGNORE INTO usage_meta VALUES ('started_at', ?)", (now_iso(),))
        # A replayed invocation updates its row in place, which the newest row doesn't show,
        # so updates are counted for readers that cache totals (ledger_mark).
        db.execute("INSERT OR IGNORE INTO usage_meta VALUES ('updates', '0')")
        db.execute(
            "CREATE TRIGGER IF NOT EXISTS usage_invocations_updated "
            "AFTER UPDATE ON usage_invocations BEGIN "
            "UPDATE usage_meta SET value=CAST(value AS INTEGER) + 1 WHERE key='updates'; END"
        )
        db.execute(f"PRAGMA user_version={USAGE_SCHEMA}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


@contextmanager
def usage_db(data_dir: Path | None = None) -> Iterator[sqlite3.Connection]:
    root = data_dir or get_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "usage.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    try:
        _prepare_usage_db(db)
        yield db
        # Only writes open a transaction; a read-only open commits nothing.
        if db.in_transaction:
            db.commit()
    finally:
        db.close()


def ledger_mark(data_dir: Path | None = None) -> tuple[int, int] | None:
    """The ledger's newest row and its count of in-place updates.

    Any new or replayed call changes it, so a total computed after reading the mark stays
    right while the mark is unchanged. None when the ledger can't be read.
    """
    try:
        with usage_db(data_dir) as db:
            newest = db.execute("SELECT MAX(rowid) FROM usage_invocations").fetchone()[0]
            updates = db.execute("SELECT value FROM usage_meta WHERE key='updates'").fetchone()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Could not read the usage ledger (%s)", type(exc).__name__)
        return None
    return int(newest or 0), int(updates[0]) if updates else 0


def record_invocation(
    *,
    invocation_id: str,
    provider: str,
    profile: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    status: str = "completed",
    data_dir: Path | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    node_id: str | None = None,
) -> None:
    """Replace a replayed invocation; new retries must have a new invocation id.

    ``run_id`` and ``thread_id`` name the Rem or organization run and the chat thread that
    made the call, when there is one. A call made inside a Rattish run's node is tagged
    with that run and node, and counted by the run. A child workflow's calls belong to the
    run and node that called the child.
    """
    from gofer.core.usage_context import ProviderCall, active_usage_observer, current_invocation

    metadata = metadata or {}
    observer = active_usage_observer()
    if observer is not None:
        run_id = observer.run_id
        node_id = observer.node_id
    cost = reported_cost(metadata)
    tokens = ledger_tokens(metadata)
    try:
        with usage_db(data_dir) as db:
            db.execute(
                """INSERT INTO usage_invocations (id, provider, profile, model, observed_at,
                status, tokens, estimated, partial, run_id, thread_id, node_id, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET tokens=excluded.tokens,
                status=excluded.status, estimated=excluded.estimated, partial=excluded.partial,
                cost_usd=excluded.cost_usd,
                run_id=COALESCE(excluded.run_id, usage_invocations.run_id),
                thread_id=COALESCE(excluded.thread_id, usage_invocations.thread_id),
                node_id=COALESCE(excluded.node_id, usage_invocations.node_id)""",
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
                    run_id or None,
                    thread_id or None,
                    node_id or None,
                    cost,
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
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        # Accounting must never fail the user's model invocation.
        logger.warning("Could not save provider usage (%s)", type(exc).__name__)
    invocation = current_invocation()
    if invocation is not None and invocation.invocation_id != invocation_id:
        invocation = None
    if observer is not None:
        try:
            # The run counts the call even when the ledger couldn't save it.
            observer.observe(
                ProviderCall(
                    provider=provider,
                    model=model,
                    tokens=tokens,
                    cost_usd=cost,
                    partial=metadata.get("partial") is True,
                    estimated=metadata.get("estimated") is True,
                    invocation_id=invocation_id,
                    cost_cap_usd=invocation.cost_cap_usd if invocation else None,
                    cost_cap_run_id=invocation.cost_cap_run_id if invocation else None,
                    stopped_at_cost_cap=metadata.get("stopped_at_cost_cap") is True,
                    failed=status != "completed",
                )
            )
        except Exception as exc:  # noqa: BLE001 - accounting never fails the call
            logger.warning("Could not count provider usage for its run (%s)", type(exc).__name__)
    from gofer.core.usage_service import notify_usage_activity

    notify_usage_activity(provider, data_dir)


def ledger_tokens(metadata: Mapping[str, Any]) -> dict[str, int]:
    """The token counts a provider reported, with a total when input and output are known."""
    tokens = {
        key: value
        for key in TOKEN_FIELDS
        if isinstance(value := metadata.get(key), int)
        and not isinstance(value, bool)
        and value >= 0
    }
    if "total_tokens" not in tokens and {"input_tokens", "output_tokens"} <= tokens.keys():
        tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
    return tokens


def reported_cost(metadata: Mapping[str, Any]) -> float | None:
    """The cost in US dollars a provider reported for a call, or None when it didn't."""
    for key in ("cost_usd", "total_cost_usd"):
        value = metadata.get(key)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            return float(value)
    return None


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


def usage_scope(workflow: Any, conversation_id: Any = None) -> dict[str, str | None]:
    """The run and thread a chat call belongs to, from the options Raticode passes it.

    Organization runs name their run in ``organizationRun``; Rem tasks in ``remManagedTask``.
    The thread is the chat thread the call shows in, else the conversation it continues.
    """
    workflow = workflow if isinstance(workflow, dict) else {}
    organization = workflow.get("organizationRun")
    task = workflow.get("remManagedTask")
    run = (organization.get("parentRunId") if isinstance(organization, dict) else None) or (
        task.get("runId") if isinstance(task, dict) else None
    )
    thread = workflow.get("chatThreadId") or conversation_id
    return {
        "run_id": run if isinstance(run, str) and run else None,
        "thread_id": thread if isinstance(thread, str) and thread else None,
    }


def empty_usage() -> dict[str, Any]:
    """A usage total before any call: nothing reported yet, so no token count either."""
    return {
        "calls": 0,
        "reported_calls": 0,
        "partial_calls": 0,
        "unknown_calls": 0,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def _add_call(target: dict[str, Any], tokens: dict[str, int], partial: bool) -> None:
    target["calls"] += 1
    complete = "total_tokens" in tokens or {"input_tokens", "output_tokens"} <= tokens.keys()
    if complete and not partial:
        target["reported_calls"] += 1
    elif tokens:
        target["partial_calls"] += 1
    else:
        target["unknown_calls"] += 1
    total = tokens.get("total_tokens")
    if total is None and tokens:
        total = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)
    for field, value in (
        ("input_tokens", tokens.get("input_tokens")),
        ("output_tokens", tokens.get("output_tokens")),
        ("total_tokens", total),
    ):
        if value is not None:
            target[field] = (target[field] or 0) + value


def merge_usage(*totals: dict[str, Any]) -> dict[str, Any]:
    merged = empty_usage()
    for total in totals:
        for field in ("calls", "reported_calls", "partial_calls", "unknown_calls"):
            merged[field] += int(total.get(field) or 0)
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            if total.get(field) is not None:
                merged[field] = (merged[field] or 0) + total[field]
    return merged


def usage_label(usage: dict[str, Any] | None) -> str:
    """One line for a usage total, saying "unknown" for calls no provider reported."""
    usage = usage or empty_usage()
    calls = int(usage.get("calls") or 0)
    if not calls:
        return "No model calls yet"
    plural = "call" if calls == 1 else "calls"
    unknown = int(usage.get("unknown_calls") or 0) + int(usage.get("partial_calls") or 0)
    total = usage.get("total_tokens")
    if total is None:
        return f"Tokens unknown: the provider didn't report usage for {calls} {plural}"
    text = f"{total:,} tokens over {calls} {plural}"
    if unknown:
        text += f", {unknown} of them with unknown or partial usage"
    return text


def run_usage(run_ids: Iterable[Any], data_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Usage per run from the calls recorded for it. Estimated counts are left out.

    A run with no recorded call has no entry. ``unknown_calls`` counts calls the provider
    didn't report tokens for, so a total is never shown as complete when it isn't.
    """
    wanted = sorted({r for r in run_ids if isinstance(r, str) and r})
    totals: dict[str, dict[str, Any]] = {}
    if not wanted:
        return totals
    try:
        with usage_db(data_dir) as db:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                rows = db.execute(
                    "SELECT run_id, tokens, estimated, partial FROM usage_invocations "
                    f"WHERE run_id IN ({','.join('?' * len(chunk))})",
                    chunk,
                ).fetchall()
                for row in rows:
                    tokens = {} if row["estimated"] else json.loads(row["tokens"])
                    target = totals.setdefault(row["run_id"], empty_usage())
                    _add_call(target, tokens, bool(row["partial"]))
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.warning("Could not read run usage (%s)", type(exc).__name__)
    return totals


def organization_ticket_usage(org: dict[str, Any], data_dir: Path) -> dict[str, dict[str, Any]]:
    """Each organization ticket's model use, by task ID.

    A run's calls come from the usage ledger. A run with none there, such as work done on
    another computer, counts once with what the run reported, or as unknown.
    """
    attempts = [a for a in (org["runtime"].get("attempts") or {}).values() if isinstance(a, dict)]
    used = run_usage((a.get("id") for a in attempts), data_dir)
    totals: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        task_id = attempt.get("taskId")
        if not task_id:
            continue
        if attempt.get("id") in used:
            part = used[attempt["id"]]
        elif attempt.get("finishedAt"):
            part = empty_usage()
            tokens = (attempt.get("usage") or {}).get("total_tokens")
            part["calls"] = 1
            if type(tokens) is int and tokens >= 0:
                part.update(reported_calls=1, total_tokens=tokens)
            else:
                part["unknown_calls"] = 1
        else:
            continue
        totals[task_id] = merge_usage(totals.get(task_id, empty_usage()), part)
    return totals


def run_tokens_since(since: datetime, data_dir: Path | None = None) -> dict[str, int]:
    """Reported tokens per run for calls recorded at or after ``since``."""
    totals: dict[str, int] = {}
    with usage_db(data_dir) as db:
        rows = db.execute(
            "SELECT run_id, tokens FROM usage_invocations "
            "WHERE run_id IS NOT NULL AND estimated=0 AND observed_at >= ?",
            (since.astimezone(UTC).isoformat(),),
        ).fetchall()
    for row in rows:
        tokens = json.loads(row["tokens"])
        total = tokens.get("total_tokens")
        if total is None:
            total = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)
        if total:
            totals[row["run_id"]] = totals.get(row["run_id"], 0) + int(total)
    return totals


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
        scope = usage_scope(bound.arguments.get("workflow"), bound.arguments.get("conversation_id"))
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
                    **scope,
                )

    return wrapped
