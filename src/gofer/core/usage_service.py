"""Shared usage dashboard with independent, bounded provider refreshes."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.core.provider_capabilities import CLI_PROVIDERS
from gofer.core.provider_profiles import load_provider_profiles
from gofer.core.provider_usage import provider_usage_placeholder
from gofer.core.usage_credentials import usage_credential_revision
from gofer.core.usage_ledger import activity_payload, now_iso, profile_usage_identity, usage_db
from gofer.utils.paths import get_data_dir

REFRESH_INTERVAL_SECONDS = 60
STALE_SECONDS = 900
# Allowances are kept current without opening Settings → Usage.
BACKGROUND_REFRESH_SECONDS = 600
BACKGROUND_START_DELAY_SECONDS = 15
# Provider activity changes its allowance; refresh once a burst of turns settles.
ACTIVITY_DELAY_SECONDS = 30
ACTIVITY_REFRESH_SECONDS = 120
# Claude Code turns report the account's windows for free. While those readings are
# this recent, polling would only add load to Claude's rate-limited usage endpoint.
CLAUDE_ACTIVITY_FRESH_SECONDS = 300
_refresh_lock = threading.Lock()
_refreshing: set[str] = set()
_background_lock = threading.Lock()
_background: dict[str, tuple[threading.Event, set[str]]] = {}
logger = logging.getLogger(__name__)


def _key(data_dir: Path | None) -> str:
    return str((data_dir or get_data_dir()).resolve())


def _accounts(data_dir: Path | None) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = [
        {"id": provider, "provider": provider, "profile": None} for provider in CLI_PROVIDERS
    ]
    accounts.extend(
        {"id": provider, "provider": provider, "profile": None}
        for provider in ("openai_api", "anthropic_api")
    )
    for name, profile in load_provider_profiles(data_dir).items():
        # Configuration changes invalidate snapshots without exposing secret values.
        accounts.append(
            {
                "id": profile_usage_identity(name, data_dir),
                "provider": profile.subscription,
                "profile": name,
            }
        )
    for account in accounts:
        account["credential_revision"] = usage_credential_revision(
            account["provider"], account["profile"], data_dir=data_dir
        )
    return accounts


def _age(timestamp: str | None) -> float:
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(str(timestamp))).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


def _measured_activity(
    limits: list[Any], identity: dict[str, Any]
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Newest measured Claude window per id, from any model's recent turns."""
    measured: dict[str, tuple[str, dict[str, Any]]] = {}
    if identity["provider"] != "claude_code" or identity["credential_revision"]:
        # A saved reporting token can name a different account from Claude Code's.
        return measured
    for limit in limits:
        if limit["provider"] != "claude_code" or limit["profile"] != (
            identity["id"] if identity["profile"] else ""
        ):
            continue
        for window in json.loads(limit["windows"]):
            if isinstance(window.get("used"), int | float) and window.get("id") not in measured:
                measured[window["id"]] = (limit["observed_at"], window)
    return measured


def _with_activity(
    row: dict[str, Any], measured: dict[str, tuple[str, dict[str, Any]]]
) -> dict[str, Any]:
    """Overlay windows from turns that are newer than the last polled reading."""
    newer = {
        key: (observed, window)
        for key, (observed, window) in measured.items()
        if not row.get("windows") or _age(observed) < _age(row.get("observed_at"))
    }
    if not newer:
        return row
    windows = {window["id"]: window for window in row.get("windows", [])}
    for key, (_, window) in newer.items():
        windows[key] = {**windows.get(key, {}), **window}
    polled = bool(row.get("windows"))
    return {
        **row,
        "windows": list(windows.values()),
        "observed_at": min((observed for observed, _ in newer.values()), key=_age),
        "status": "available",
        "source": row.get("source") if polled else "Claude Code activity",
        "reason": "Updated from Claude Code activity in Raticode."
        if polled
        else "From Claude Code activity in Raticode. Refresh for model-specific limits.",
    }


def usage_overview(data_dir: Path | None = None, *, days: int = 30) -> dict[str, Any]:
    if days not in {1, 7, 30, 90}:
        raise ValueError("Usage period must be 1, 7, 30, or 90 days")
    with usage_db(data_dir) as db:
        snapshots = {
            row["id"]: json.loads(row["payload"])
            for row in db.execute("SELECT id, payload FROM usage_snapshots")
        }
        limits = db.execute("SELECT * FROM usage_rate_limits ORDER BY observed_at DESC").fetchall()
    accounts = []
    for identity in _accounts(data_dir):
        row = snapshots.get(
            identity["id"],
            provider_usage_placeholder(identity["provider"], identity["profile"]),
        )
        if row.get("credential_revision", "") != identity["credential_revision"]:
            row = provider_usage_placeholder(identity["provider"], identity["profile"])
        row = _with_activity(row, _measured_activity(limits, identity))
        captured = [
            limit
            for limit in limits
            if limit["provider"] == identity["provider"]
            and limit["profile"] == (identity["id"] if identity["profile"] else "")
        ]
        # A complete polled snapshot takes precedence over partial activity events.
        # Manual reporting credentials can name a different account from inference.
        activity_source = row.get("source") in {
            "Claude rate-limit events",
            "Response rate-limit headers",
        }
        if (
            captured
            and (not row.get("windows") or activity_source)
            and not identity["credential_revision"]
        ):
            windows = [window for limit in captured for window in json.loads(limit["windows"])]
            expired = any(_age(w.get("resets_at")) >= 0 for w in windows if w.get("resets_at"))
            expired = expired or any(
                _age(limit["observed_at"]) > STALE_SECONDS for limit in captured
            )
            event_source = identity["provider"] == "claude_code"
            row = {
                **row,
                "windows": windows,
                "observed_at": captured[0]["observed_at"],
                "source": "Claude rate-limit events"
                if event_source
                else "Response rate-limit headers",
                "status": "stale" if expired else "available",
                "reason": (
                    "Partial limit events from Claude activity. Other account limits may apply."
                    if event_source
                    else "Limits from the last API response. Refreshed by API activity."
                ),
            }
        reset_passed = any(
            _age(window.get("resets_at")) >= 0
            for window in row.get("windows", [])
            if window.get("resets_at")
        )
        if row.get("status") == "available" and (
            _age(row.get("observed_at")) > STALE_SECONDS or reset_passed
        ):
            row = {**row, "status": "stale", "reason": "Refresh to update this reading."}
        accounts.append({**row, **identity})
    return {
        "scope": "device",
        "days": days,
        "observed_at": now_iso(),
        "accounts": accounts,
        **activity_payload(data_dir, days=days),
    }


async def refresh_usage(
    data_dir: Path | None = None,
    *,
    days: int = 30,
    providers: set[str] | None = None,
    min_age: float = REFRESH_INTERVAL_SECONDS,
    explicit: bool = False,
) -> dict[str, Any]:
    from gofer.core.provider_usage import CLI_REFRESH_TIMEOUT_SECONDS, refresh_provider_usage

    # HTTP handlers have separate event loops; a threading guard coalesces them.
    key = _key(data_dir)
    overview = usage_overview(data_dir, days=days)
    with _refresh_lock:
        if key in _refreshing:
            return {**overview, "refreshing": True}
        _refreshing.add(key)
    try:
        with usage_db(data_dir) as db:
            checked = {
                row["id"]: (row["checked_at"], json.loads(row["payload"]))
                for row in db.execute("SELECT id, checked_at, payload FROM usage_snapshots")
            }
            limits = db.execute(
                "SELECT * FROM usage_rate_limits ORDER BY observed_at DESC"
            ).fetchall()
        previous = {row["id"]: row for row in overview["accounts"]}

        async def refresh(identity: dict[str, Any]) -> None:
            stamp, snapshot = checked.get(identity["id"], (None, {}))
            if providers is not None and identity["provider"] not in providers:
                return
            if (
                snapshot.get("credential_revision", "") == identity["credential_revision"]
                and _age(stamp) < min_age
            ):
                return
            # Honor provider back-off. A user can retry other failures, never a 429.
            if _age(snapshot.get("retry_at")) < 0 and (
                not explicit or snapshot.get("rate_limited")
            ):
                return
            activity = _measured_activity(limits, identity)
            if activity and min(_age(observed) for observed, _ in activity.values()) < (
                CLAUDE_ACTIVITY_FRESH_SECONDS
            ):
                return
            try:
                row = await asyncio.wait_for(
                    refresh_provider_usage(
                        identity["provider"],
                        identity["profile"],
                        data_dir=data_dir,
                    ),
                    timeout=CLI_REFRESH_TIMEOUT_SECONDS + 5,
                )
            except Exception:
                # Provider stderr and HTTP bodies can contain credentials.
                row = {
                    "status": "error",
                    "reason": "Could not refresh usage. Try again shortly.",
                    "windows": [],
                    "observed_at": None,
                }
            if (
                usage_credential_revision(
                    identity["provider"], identity["profile"], data_dir=data_dir
                )
                != identity["credential_revision"]
            ):
                return
            retry: dict[str, Any] = {}
            if isinstance(seconds := row.pop("retry_after_seconds", None), int):
                retry = {
                    "retry_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
                    "rate_limited": row.pop("rate_limited", False) is True,
                }
            old = previous.get(identity["id"], {})
            if (
                row.get("status") != "available"
                and old.get("windows")
                and old.get("credential_revision", "") == identity["credential_revision"]
            ):
                row = {
                    **old,
                    "status": "stale",
                    "reason": row.get("reason"),
                    "refresh_status": row.get("status"),
                }
            # A retry time belongs only to the attempt that produced it.
            row = {k: v for k, v in row.items() if k not in {"retry_at", "rate_limited"}}
            row = {**row, **retry, **identity}
            with usage_db(data_dir) as db:
                db.execute(
                    "INSERT OR REPLACE INTO usage_snapshots VALUES (?, ?, ?)",
                    (identity["id"], json.dumps(row), now_iso()),
                )

        identities = _accounts(data_dir)
        await asyncio.gather(*(refresh(identity) for identity in identities))
        # If settings were saved while a provider was in flight, fetch that new
        # account before completing this refresh. Never keep the old allowance.
        revisions = {row["id"]: row["credential_revision"] for row in identities}
        changed = [
            row
            for row in _accounts(data_dir)
            if revisions.get(row["id"]) != row["credential_revision"]
        ]
        await asyncio.gather(*(refresh(identity) for identity in changed))
        return usage_overview(data_dir, days=days)
    finally:
        with _refresh_lock:
            _refreshing.discard(key)


def notify_usage_activity(provider: str, data_dir: Path | None = None) -> None:
    """Ask the background refresher, when running, to re-read this provider soon."""
    with _background_lock:
        registered = _background.get(_key(data_dir))
        if registered is not None:
            registered[1].add(provider)
            registered[0].set()


def start_background_refresh(data_dir: Path | None = None) -> Callable[[], None]:
    """Refresh allowances periodically and after provider activity; returns a stopper."""
    key = _key(data_dir)
    wake, stop = threading.Event(), threading.Event()
    active: set[str] = set()
    with _background_lock:
        _background[key] = (wake, active)

    def run(**options: Any) -> None:
        try:
            asyncio.run(refresh_usage(data_dir, **options))
        except Exception:
            # Provider failures are already per-account rows; never log secrets.
            logger.warning("Background usage refresh failed", exc_info=False)

    def loop() -> None:
        if stop.wait(BACKGROUND_START_DELAY_SECONDS):
            return
        run()
        while not stop.is_set():
            if not wake.wait(BACKGROUND_REFRESH_SECONDS):
                run()
                continue
            if stop.wait(ACTIVITY_DELAY_SECONDS):
                return
            with _background_lock:
                wake.clear()
                providers = set(active)
                active.clear()
            run(providers=providers, min_age=ACTIVITY_REFRESH_SECONDS)

    thread = threading.Thread(target=loop, name="raticode-usage-refresh", daemon=True)
    thread.start()

    def stopper() -> None:
        stop.set()
        wake.set()
        with _background_lock:
            if _background.get(key, (None,))[0] is wake:
                del _background[key]

    return stopper
