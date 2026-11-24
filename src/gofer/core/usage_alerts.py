"""Alerts when a provider account nears the limit of its 5-hour or weekly window.

After each usage refresh, ``check_limit_alerts`` reads every account's current
allowance (the rows Settings > Usage shows). When a window's reading is at or past an
alert level (80% and 95% by default) for the first time in that window, Raticode sends
one desktop notification naming the account, the window and its reset time, and leaves
the same notice for the paired phone in the computer's alert store
(``gofer.core.phone_alerts``), which the phone reads under Workflow messages.

Each level alerts once per window. The usage database keeps one row per account,
window and level with the window's reset time, so a restart doesn't repeat an alert.
A level re-arms when that reset time has passed, or, for a window with no reported
reset time, when a reading falls back under the level. Accounts that need sign-in,
stale readings and readings past their own reset time never alert.

Settings > Usage and ``Raticode usage alerts`` turn the alerts on or off and set the
two levels. They live in the usage database's ``usage_meta`` table.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.core.usage_ledger import usage_db
from gofer.utils.paths import get_data_dir

logger = logging.getLogger(__name__)

SETTINGS_KEY = "limit_alerts"
DEFAULT_THRESHOLDS = (80, 95)
# A reading older than this isn't new; Settings > Usage calls it stale too.
READING_FRESH_SECONDS = 900
# Rows for windows that reset longer ago than this are removed.
FORGET_AFTER = timedelta(days=8)
PHONE_SOURCE = "usage-limit"
PHONE_NAME = "Usage alerts"

FIVE_HOUR = "5-hour"
WEEKLY = "weekly"
_FIVE_HOUR_LABEL = re.compile(r"\b(?:5|five)[- ]hour\b", re.IGNORECASE)
_WEEKLY_LABEL = re.compile(r"\b(?:weekly|7[- ]day|seven[- ]day)\b", re.IGNORECASE)
# Labels that already say only what the window name says.
_PLAIN_LABELS = {"five-hour allowance", "weekly allowance", "5-hour allowance", "7-day allowance"}

DesktopSender = Callable[[str, str], Awaitable[None]]


class UsageAlertError(ValueError):
    """Settings for limit alerts that can't be saved."""


def alert_settings(data_dir: Path | None = None) -> dict[str, Any]:
    """Whether limit alerts are on and their two levels in percent, lowest first."""
    with usage_db(data_dir) as db:
        row = db.execute("SELECT value FROM usage_meta WHERE key=?", (SETTINGS_KEY,)).fetchone()
    stored: Any = {}
    if row is not None:
        try:
            stored = json.loads(row["value"])
        except (TypeError, ValueError):
            stored = {}
    if not isinstance(stored, dict):
        stored = {}
    enabled = stored.get("enabled")
    thresholds = stored.get("thresholds")
    try:
        levels = _check_thresholds(thresholds)
    except UsageAlertError:
        levels = list(DEFAULT_THRESHOLDS)
    return {
        "enabled": enabled if isinstance(enabled, bool) else True,
        "thresholds": levels,
        "defaults": {"enabled": True, "thresholds": list(DEFAULT_THRESHOLDS)},
    }


def save_alert_settings(
    data_dir: Path | None = None,
    *,
    enabled: Any = None,
    thresholds: Any = None,
) -> dict[str, Any]:
    """Change the switch, the levels or both; ``None`` keeps the saved value."""
    current = alert_settings(data_dir)
    if enabled is not None and not isinstance(enabled, bool):
        raise UsageAlertError("Turn alerts on or off with true or false.")
    levels = current["thresholds"] if thresholds is None else _check_thresholds(thresholds)
    value = {
        "enabled": current["enabled"] if enabled is None else enabled,
        "thresholds": levels,
    }
    with usage_db(data_dir) as db:
        db.execute(
            "INSERT OR REPLACE INTO usage_meta VALUES (?, ?)", (SETTINGS_KEY, json.dumps(value))
        )
    return alert_settings(data_dir)


def _check_thresholds(value: Any) -> list[int]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise UsageAlertError("Set two alert levels, such as 80 and 95.")
    levels = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float) or item != int(item):
            raise UsageAlertError("Alert levels are whole percentages from 1 to 100.")
        if not 1 <= int(item) <= 100:
            raise UsageAlertError("Alert levels are whole percentages from 1 to 100.")
        levels.append(int(item))
    if levels[0] >= levels[1]:
        raise UsageAlertError("The second alert level must be higher than the first.")
    return levels


def window_kind(window: dict[str, Any]) -> str | None:
    """``5-hour`` or ``weekly`` for the windows alerts cover, None for any other window."""
    identifier = str(window.get("id") or "").rsplit(":", 1)[-1]
    label = str(window.get("label") or "")
    if identifier == "five_hour" or _FIVE_HOUR_LABEL.search(label):
        return FIVE_HOUR
    if identifier.startswith("seven_day") or _WEEKLY_LABEL.search(label):
        return WEEKLY
    return None


def account_name(account: dict[str, Any]) -> str:
    """How the alert names an account: the provider, and the profile when there is one."""
    from gofer.core.provider_usage import PROVIDER_LABELS

    provider = str(account.get("provider") or "")
    name = PROVIDER_LABELS.get(provider) or str(account.get("label") or provider or "Provider")
    profile = account.get("profile")
    return f"{name} ({profile})" if profile else name


def window_name(window: dict[str, Any], kind: str) -> str:
    """``5-hour window``, or ``weekly window (Weekly Opus allowance)`` for a narrower one."""
    label = str(window.get("label") or "").strip()
    base = f"{kind} window"
    if not label or label.lower() in _PLAIN_LABELS:
        return base
    return f"{base} ({label})"


def reset_text(resets_at: str, now: datetime | None = None) -> str:
    """The reset time in this computer's time zone, such as ``today at 3:40 PM``."""
    stamp = _parse(resets_at)
    if stamp is None:
        return "Its reset time wasn't reported."
    local = stamp.astimezone()
    today = (now or datetime.now(UTC)).astimezone().date()
    hour = local.hour % 12 or 12
    clock = f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    if local.date() == today:
        day = "today"
    elif local.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = f"{local:%a %b} {local.day}"
    zone = local.tzname() or ""
    return f"It resets {day} at {clock}{' ' + zone if zone else ''}."


def _parse(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _needs_sign_in(account: dict[str, Any]) -> bool:
    return account.get("needs_sign_in") is True or account.get("status") == "auth_required"


def _readings(
    accounts: list[dict[str, Any]], now: datetime
) -> list[tuple[dict[str, Any], dict[str, Any], str, float]]:
    """(account, window, kind, percent) for every fresh 5-hour or weekly reading."""
    from gofer.core.usage_service import window_used_percent

    found = []
    for account in accounts:
        if _needs_sign_in(account) or account.get("status") != "available":
            continue
        observed = _parse(account.get("observed_at"))
        if observed is None or (now - observed).total_seconds() > READING_FRESH_SECONDS:
            continue
        seen: set[str] = set()
        for window in account.get("windows") or []:
            if not isinstance(window, dict) or not window.get("id"):
                continue
            kind = window_kind(window)
            if kind is None or str(window["id"]) in seen:
                continue
            resets = _parse(window.get("resets_at")) if window.get("resets_at") else None
            if resets is not None and resets <= now:
                continue
            percent = window_used_percent(window)
            if percent is None:
                continue
            seen.add(str(window["id"]))
            found.append((account, window, kind, percent))
    return found


def _account_key(account: dict[str, Any]) -> str:
    # The profile's name, not its usage identity: editing a profile mustn't re-alert.
    return f"{account.get('provider') or ''}:{account.get('profile') or ''}"


def _armed(row: sqlite3.Row | None, percent: float, threshold: int, now: datetime) -> bool:
    """Whether this level may alert: never sent, or its window has reset since."""
    if row is None:
        return True
    resets = _parse(row["resets_at"]) if row["resets_at"] else None
    if resets is not None:
        return resets <= now
    # No reset time was reported: a reading back under the level means a new window.
    return percent < threshold


def claim_alerts(
    accounts: list[dict[str, Any]],
    data_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Record and return the alerts the current readings call for, one per window.

    Each returned alert names the highest level the window crossed. Every level it
    crossed is recorded, so a window at 97% sends one alert, not one per level.
    """
    settings = alert_settings(data_dir)
    if not settings["enabled"]:
        return []
    now = now or datetime.now(UTC)
    levels = sorted(settings["thresholds"])
    alerts: list[dict[str, Any]] = []
    with usage_db(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            for account, window, kind, percent in _readings(accounts, now):
                key, window_id = _account_key(account), str(window["id"])
                resets_at = str(window.get("resets_at") or "")
                crossed = []
                for level in levels:
                    row = db.execute(
                        "SELECT resets_at FROM usage_limit_alerts "
                        "WHERE account=? AND window=? AND threshold=?",
                        (key, window_id, level),
                    ).fetchone()
                    if row is not None and resets_at == "" and percent < level:
                        # A window with no reset time re-arms once it reads under the level.
                        db.execute(
                            "DELETE FROM usage_limit_alerts "
                            "WHERE account=? AND window=? AND threshold=?",
                            (key, window_id, level),
                        )
                        continue
                    if percent >= level and _armed(row, percent, level, now):
                        crossed.append(level)
                for level in crossed:
                    db.execute(
                        "INSERT OR REPLACE INTO usage_limit_alerts VALUES (?, ?, ?, ?, ?, ?)",
                        (key, window_id, level, resets_at, percent, now.isoformat()),
                    )
                if crossed:
                    alerts.append(
                        _alert(account, window, kind, percent, max(crossed), resets_at, now)
                    )
            cutoff = (now - FORGET_AFTER).isoformat()
            db.execute(
                "DELETE FROM usage_limit_alerts WHERE resets_at != '' AND resets_at < ?",
                (cutoff,),
            )
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return alerts


def _alert(
    account: dict[str, Any],
    window: dict[str, Any],
    kind: str,
    percent: float,
    level: int,
    resets_at: str,
    now: datetime,
) -> dict[str, Any]:
    name = account_name(account)
    where = window_name(window, kind)
    shown = min(100, int(percent))
    title = f"{name} is at {shown}% of its {kind} limit"
    body = f"{name} has used {shown}% of its {where}, past your {level}% alert. " + reset_text(
        resets_at, now
    )
    return {
        "account": name,
        "provider": account.get("provider"),
        "profile": account.get("profile"),
        "window": where,
        "window_id": str(window.get("id")),
        "kind": kind,
        "percent": percent,
        "threshold": level,
        "resets_at": resets_at,
        "title": title,
        "body": body,
    }


async def _desktop_notice(title: str, body: str) -> None:
    from gofer.core.approvals import DesktopNotificationAdapter, Notification

    await DesktopNotificationAdapter().send(Notification(title=title, body=body))


async def check_limit_alerts(
    data_dir: Path | None = None,
    *,
    accounts: list[dict[str, Any]] | None = None,
    desktop: DesktopSender | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Send the alerts the current allowance readings call for and return them.

    ``accounts`` defaults to the rows Settings > Usage shows. Each alert goes to the
    desktop as a notification and to the paired phone through the alert store; a
    delivery that fails is logged and doesn't stop the other.
    """
    from gofer.core.phone_alerts import PhoneAlertStore
    from gofer.core.usage_service import allowance_accounts

    root = data_dir or get_data_dir()
    rows = allowance_accounts(root) if accounts is None else accounts
    alerts = claim_alerts(rows, root, now=now)
    send = desktop or _desktop_notice
    for alert in alerts:
        try:
            await send(alert["title"], alert["body"])
            alert["desktop"] = True
        except Exception as exc:
            # No desktop session (a server or the command line) still reaches the phone.
            logger.info("Usage limit alert not shown on the desktop (%s)", type(exc).__name__)
            alert["desktop"] = False
        try:
            PhoneAlertStore(root).add(
                title=alert["title"],
                body=alert["body"],
                workflow_id="",
                run_id="",
                workflow_name=PHONE_NAME,
                source=PHONE_SOURCE,
            )
            alert["phone"] = True
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Could not leave the usage limit alert for the phone (%s)", exc)
            alert["phone"] = False
    return alerts
