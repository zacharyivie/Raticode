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
# Kept per call beside TOKEN_FIELDS but not summed in the history: the part of
# cache_write_tokens written for an hour, which Anthropic prices higher than five minutes.
STORED_TOKEN_FIELDS = (*TOKEN_FIELDS, "cache_write_1h_tokens")
logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_account_profile(name: str, provider: str, data_dir: Path | None) -> bool:
    """Whether the profile is an account of a provider with an account adapter.

    Each account's turns report its own limits, which choosing by usage reads.
    """
    from gofer.core.provider_accounts import account_adapter
    from gofer.core.provider_profiles import load_provider_profiles

    if account_adapter(provider) is None:
        return False
    profile = load_provider_profiles(data_dir).get(name)
    return bool(profile and profile.account_id and profile.subscription == provider)


def profile_usage_identity(name: str, data_dir: Path | None = None) -> str:
    from gofer.core.provider_profiles import load_provider_profiles

    profile = load_provider_profiles(data_dir).get(name)
    if profile is None:
        return f"profile:{name}:missing"
    if profile.account_id:
        # An account is its folder: changing its model keeps its usage history. Claude
        # Code accounts keep the "claude-account" prefix they were first recorded with.
        kind = (
            "claude-account"
            if profile.subscription == "claude_code"
            else f"{profile.subscription}-account"
        )
        fingerprint = hashlib.sha256(f"{kind}:{profile.account_id}".encode()).hexdigest()[:16]
        return f"profile:{name}:{fingerprint}"
    fingerprint = hashlib.sha256(profile.model_dump_json().encode()).hexdigest()[:16]
    return f"profile:{name}:{fingerprint}"


# Raised when the ledger's tables, columns, indexes or triggers change, so an open only
# prepares the file when it is older than this code.
USAGE_SCHEMA = 8
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
    # Limit alerts already sent: one row per account, window and alert level, kept
    # until the window resets so a restart doesn't repeat them (gofer.core.usage_alerts).
    """CREATE TABLE IF NOT EXISTS usage_limit_alerts (
        account TEXT NOT NULL, window TEXT NOT NULL, threshold INTEGER NOT NULL,
        resets_at TEXT NOT NULL, percent REAL NOT NULL, alerted_at TEXT NOT NULL,
        PRIMARY KEY(account, window, threshold)
    )""",
    # Every allowance reading that differed from the window's last one, so a card can
    # chart the window and its pace (gofer.core.usage_allowance_history). Profile is ""
    # for the provider's own sign-in, else the profile's usage identity.
    """CREATE TABLE IF NOT EXISTS usage_allowance_history (
        provider TEXT NOT NULL, profile TEXT NOT NULL, window_id TEXT NOT NULL,
        used_percent REAL NOT NULL, resets_at TEXT NOT NULL, observed_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS usage_allowance_window ON usage_allowance_history"
    "(provider, profile, window_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS usage_allowance_time ON usage_allowance_history(observed_at)",
    # How big each part of a Rem turn's prompt was: sizes only, never the text
    # (gofer.core.turn_context). The id is the turn's invocation id, so its tokens are the
    # usage_invocations row with the same id. ``call`` names the kind of one of Rem's own
    # tool-free calls; NULL is a thread's turn. ``attempt`` says
    # why the call was asked (turn_context.ATTEMPTS) and ``failed`` that its answer was
    # still unusable, so Rem's re-asks can be counted. ``rules_version`` names the call's
    # fixed rules (turn_context.rules_version), so re-asks can be counted per rules change.
    """CREATE TABLE IF NOT EXISTS usage_turn_context (
        id TEXT PRIMARY KEY, thread_id TEXT, run_id TEXT, provider TEXT, model TEXT,
        observed_at TEXT NOT NULL, resumed INTEGER NOT NULL DEFAULT 0, parts TEXT NOT NULL,
        call TEXT, attempt TEXT, failed INTEGER NOT NULL DEFAULT 0, rules_version TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS usage_turn_context_thread ON usage_turn_context"
    "(thread_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS usage_turn_context_time ON usage_turn_context(observed_at)",
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
        # A row for work a paired desktop ran names that computer (``computer_id``); its
        # calls are counted there, so it stays out of this computer's account totals.
        for column, kind in (
            ("run_id", "TEXT"),
            ("thread_id", "TEXT"),
            ("node_id", "TEXT"),
            ("cost_usd", "REAL"),
            ("computer_id", "TEXT"),
        ):
            if column not in columns:
                db.execute(f"ALTER TABLE usage_invocations ADD COLUMN {column} {kind}")
        db.execute("CREATE INDEX IF NOT EXISTS usage_run ON usage_invocations(run_id)")
        # A ledger from before Rem's own calls were recorded has no call kind column.
        turn_columns = {row[1] for row in db.execute("PRAGMA table_info(usage_turn_context)")}
        for column, kind in (
            ("call", "TEXT"),
            # Why each of Rem's calls was asked and whether it still failed.
            ("attempt", "TEXT"),
            ("failed", "INTEGER NOT NULL DEFAULT 0"),
            # Which fixed rules each of Rem's calls was sent with.
            ("rules_version", "TEXT"),
        ):
            if column not in turn_columns:
                db.execute(f"ALTER TABLE usage_turn_context ADD COLUMN {column} {kind}")
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
            return read_ledger_mark(db)
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Could not read the usage ledger (%s)", type(exc).__name__)
        return None


def read_ledger_mark(db: sqlite3.Connection) -> tuple[int, int]:
    """``ledger_mark`` on a ledger that is already open."""
    newest = db.execute("SELECT MAX(rowid) FROM usage_invocations").fetchone()[0]
    updates = db.execute("SELECT value FROM usage_meta WHERE key='updates'").fetchone()
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
    computer_id: str | None = None,
) -> None:
    """Replace a replayed invocation; new retries must have a new invocation id.

    ``computer_id`` names the paired desktop that ran the work, for the one summary row
    this computer keeps per Rem task run there (gofer.ui.rem_remote_tasks). That desktop
    keeps the call-by-call rows under its own account, so the row is left out of this
    computer's account totals (``LOCAL_ROWS``) and counts only for its run.

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
    recorded_model = served_model(model, metadata)
    try:
        with usage_db(data_dir) as db:
            db.execute(
                """INSERT INTO usage_invocations (id, provider, profile, model, observed_at,
                status, tokens, estimated, partial, run_id, thread_id, node_id, cost_usd,
                computer_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    recorded_model,
                    now_iso(),
                    status,
                    json.dumps(tokens),
                    int(metadata.get("estimated") is True),
                    int(metadata.get("partial") is True),
                    run_id or None,
                    thread_id or None,
                    node_id or None,
                    cost,
                    computer_id or None,
                ),
            )
            if (
                provider in {"openai_api", "anthropic_api", "claude_code"}
                or (profile and _is_account_profile(profile, provider, data_dir))
            ) and isinstance(metadata.get("quota_windows"), list):
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
                    identity = profile_usage_identity(profile, data_dir) if profile else ""
                    db.execute(
                        """INSERT INTO usage_rate_limits VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(provider, profile, model) DO UPDATE SET
                        observed_at=excluded.observed_at, windows=excluded.windows
                        WHERE excluded.observed_at >= usage_rate_limits.observed_at""",
                        (provider, identity, model or "", observed_at, json.dumps(windows)),
                    )
                    from gofer.core.usage_allowance_history import record_allowance_readings

                    record_allowance_readings(db, provider, identity, windows, observed_at)
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


def served_model(model: str | None, metadata: Mapping[str, Any]) -> str | None:
    """The model to record for a call: the chosen one, or the one the provider reported.

    A call made with ``cli-default`` (or no model) lets the provider's app pick, so the
    choice alone can't name a list price. When the provider's own output names the model
    that answered, as Claude Code's does, that name is recorded instead.
    """
    if model and model != "cli-default":
        return model
    reported = metadata.get("model")
    if isinstance(reported, str) and reported.strip() and reported.strip() != "cli-default":
        return reported.strip()[:200]
    return model


def ledger_tokens(metadata: Mapping[str, Any]) -> dict[str, int]:
    """The token counts a provider reported, with a total when input and output are known."""
    tokens = {
        key: value
        for key in STORED_TOKEN_FIELDS
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


# Calls made on this computer. A summary row for work a paired desktop ran names that
# computer and is left out of this computer's account totals.
LOCAL_ROWS = "computer_id IS NULL"


def activity_payload(data_dir: Path | None = None, *, days: int = 30) -> dict[str, Any]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with usage_db(data_dir) as db:
        rows = db.execute(
            f"SELECT * FROM usage_invocations WHERE observed_at >= ? AND {LOCAL_ROWS}",
            (cutoff,),
        ).fetchall()
        started = db.execute("SELECT value FROM usage_meta WHERE key='started_at'").fetchone()[0]
    return {"tracking_started_at": started, "activity": summarize_calls(rows)}


def summarize_calls(rows: Iterable[Any]) -> dict[str, Any]:
    """Call counts and reported tokens, in total and per provider account.

    Each row needs provider, profile, tokens, estimated and partial. Estimated counts are
    kept apart, and a token field no call reported stays None rather than 0.
    """
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
                    if field in target:
                        target[field] = (target[field] or 0) + value
    return {**total, "providers": list(groups.values())}


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


def run_summary(run_id: str, data_dir: Path | None = None) -> dict[str, Any]:
    """What one run used, as one summary another computer can record.

    Token counts and cost add up the run's reported calls; a field no call reported is
    left out. ``provider`` and ``model`` are the ones most of its calls used. Empty
    when the run made no recorded call.
    """
    summary: dict[str, Any] = {}
    try:
        with usage_db(data_dir) as db:
            rows = db.execute(
                "SELECT provider, model, tokens, estimated, cost_usd FROM usage_invocations "
                "WHERE run_id=? ORDER BY rowid",
                (run_id,),
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Could not read a run's usage (%s)", type(exc).__name__)
        return summary
    if not rows:
        return summary
    providers: dict[str, int] = {}
    models: dict[str, int] = {}
    for row in rows:
        providers[row["provider"]] = providers.get(row["provider"], 0) + 1
        if row["model"]:
            models[row["model"]] = models.get(row["model"], 0) + 1
        if row["estimated"]:
            continue
        tokens = json.loads(row["tokens"])
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if isinstance(tokens.get(key), int):
                summary[key] = summary.get(key, 0) + tokens[key]
        if row["cost_usd"] is not None:
            summary["cost_usd"] = summary.get("cost_usd", 0.0) + float(row["cost_usd"])
    summary["provider"] = max(providers, key=lambda name: providers[name])
    if models:
        summary["model"] = max(models, key=lambda name: models[name])
    return summary


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


# The event a chat stream yields once it has built its prompt, with each part's size.
CONTEXT_SENT = "context_sent"


def record_turn_parts(
    invocation_id: str,
    event: Mapping[str, Any],
    *,
    provider: str,
    model: Any,
    data_dir: Path | None,
    run_id: str | None,
    thread_id: str | None,
) -> None:
    """Save the part sizes a ``context_sent`` event carries. Never fails the turn."""
    from gofer.core.turn_context import record_turn_context

    parts = event.get("parts")
    if not isinstance(parts, list):
        return
    try:
        with usage_db(data_dir) as db:
            record_turn_context(
                db,
                invocation_id=invocation_id,
                parts=[part for part in parts if isinstance(part, dict)],
                resumed=event.get("resumed") is True,
                provider=provider,
                model=model if isinstance(model, str) else None,
                thread_id=thread_id,
                run_id=run_id,
            )
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        logger.warning("Could not save the turn's context sizes (%s)", type(exc).__name__)


def record_rem_call(
    call: str,
    prompt: str,
    system: str,
    sizes: Mapping[str, int] | None,
    *,
    config: Mapping[str, Any],
    data_dir: Path | None,
    run_id: str | None = None,
    attempt: str = "first",
) -> str | None:
    """Save the part sizes of one of Rem's own calls (turn_context.rem_call_parts).

    ``attempt`` says why it was asked (turn_context.ATTEMPTS): the first ask, a re-ask for
    the JSON format, a repair of invalid fields or an ask after a read. The fixed rules
    are kept only as their version (turn_context.rules_version). Sizes only, never the
    text, and never fails the call. Returns the record's ID for mark_rem_call_failed,
    or None when it couldn't be saved. The caller makes the provider call inside
    usage_context.named_invocation with that ID, so the call's tokens are recorded under
    it too and its cache use counts per kind (turn_context.standing_parts).
    """
    from gofer.core.turn_context import record_turn_context, rem_call_parts, rules_version

    model = config.get("model")
    invocation_id = uuid.uuid4().hex
    try:
        with usage_db(data_dir) as db:
            record_turn_context(
                db,
                invocation_id=invocation_id,
                parts=rem_call_parts(prompt, system, sizes),
                resumed=False,
                provider=str(config.get("provider") or "") or None,
                model=model if isinstance(model, str) and model != "cli-default" else None,
                thread_id=None,
                run_id=run_id,
                call=call,
                attempt=attempt,
                rules_version=rules_version(system),
            )
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        logger.warning("Could not save the size of Rem's call (%s)", type(exc).__name__)
        return None
    return invocation_id


def mark_rem_call_failed(invocation_id: str | None, data_dir: Path | None) -> None:
    """Mark one of Rem's recorded calls as failed: its answer still couldn't be used.

    Never fails the caller.
    """
    if not invocation_id:
        return
    try:
        with usage_db(data_dir) as db:
            db.execute(
                "UPDATE usage_turn_context SET failed=1 WHERE id=? AND call IS NOT NULL",
                (invocation_id,),
            )
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Could not mark Rem's call as failed (%s)", type(exc).__name__)


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
                if event.get("type") == CONTEXT_SENT:
                    # Sizes for the turn's info; the event stays inside the ledger.
                    record_turn_parts(
                        invocation_id,
                        event,
                        provider=provider,
                        model=bound.arguments.get("model"),
                        data_dir=bound.arguments.get("data_dir"),
                        **scope,
                    )
                    continue
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
                    profile=bound.arguments.get("provider_profile") or None,
                    model=bound.arguments.get("model"),
                    metadata=metadata,
                    status=status,
                    data_dir=bound.arguments.get("data_dir"),
                    **scope,
                )

    return wrapped
