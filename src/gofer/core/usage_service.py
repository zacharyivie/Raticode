"""Shared usage dashboard with independent, bounded provider refreshes."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.core.provider_capabilities import CLI_PROVIDERS
from gofer.core.provider_profiles import load_provider_profiles
from gofer.core.provider_usage import provider_usage_placeholder
from gofer.core.usage_credentials import usage_credential_revision
from gofer.core.usage_ledger import activity_payload, now_iso, profile_usage_identity, usage_db
from gofer.utils.paths import get_data_dir

REFRESH_INTERVAL_SECONDS = 60
STALE_SECONDS = 300
_refresh_lock = threading.Lock()
_refreshing: set[str] = set()


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


async def refresh_usage(data_dir: Path | None = None, *, days: int = 30) -> dict[str, Any]:
    from gofer.core.provider_usage import refresh_provider_usage

    # HTTP handlers have separate event loops; a threading guard coalesces them.
    key = str((data_dir or get_data_dir()).resolve())
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
        previous = {row["id"]: row for row in overview["accounts"]}

        async def refresh(identity: dict[str, Any]) -> None:
            stamp, snapshot = checked.get(identity["id"], (None, {}))
            if (
                snapshot.get("credential_revision", "") == identity["credential_revision"]
                and _age(stamp) < REFRESH_INTERVAL_SECONDS
            ):
                return
            try:
                row = await asyncio.wait_for(
                    refresh_provider_usage(
                        identity["provider"],
                        identity["profile"],
                        data_dir=data_dir,
                    ),
                    timeout=15,
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
            row = {**row, **identity}
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
