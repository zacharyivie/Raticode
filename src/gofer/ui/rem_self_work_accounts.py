"""Which account self-work's limits watch: the one each self-started run uses.

With Settings > Rem > Accounts on the default sign-in (or no accounts at all), self-work's
daily cap reads Claude Code's default sign-in and its rate-limit stop reads the default
sign-in of Rem's task provider, as before accounts.

Self-work's own account choice for the provider decides first (``self_work_config``), then
Settings > Rem > Accounts. With a named account, both read that account: its own weekly
window for the daily cap (its credits window when it reports credits instead, as Grok
does; see rem_self_work_daily), its own pace line for Pace to reset (rem_self_work_pace),
its own stored windows for the rate-limit stop. Under "Choose by usage", self-work asks
``pick_account`` with a chooser that also skips accounts at self-work's daily cap or
rate-limit percent, or with no room under their pace line, and starts nothing when every signed-in
account is skipped, whether for those limits or because its window is full. Only when no
account is signed in do runs fall back to the default sign-in. The chosen account is
written on each run self-work starts, so the run's turns use it (``pick_run_account``
keeps a run on the account it was given).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from gofer.ui import rem_self_work_daily as daily
from gofer.ui import rem_self_work_pace as pacing

AFTER = " Running work finishes normally."
# Daily counts of accounts self-work didn't use for this long are dropped.
FORGET_AFTER = timedelta(days=2)


@dataclass
class Pick:
    """The account self-work's next run would use, and whether a limit stops it."""

    # None: the default sign-in, checked the way it was before accounts.
    account: dict[str, Any] | None = None
    reason: tuple[str, str] | None = None
    skipped: list[dict[str, str]] = field(default_factory=list)


def run_account(run: dict[str, Any]) -> tuple[str, str] | None:
    """The (provider, account name) a run uses, or None for a provider's default sign-in."""
    kept = run.get("provider_account")
    if not isinstance(kept, dict):
        kept = run.get("claude_account")
    if isinstance(kept, dict) and kept.get("name"):
        return str(kept.get("provider") or "claude_code"), str(kept["name"])
    return None


def identity(name: str, data_dir: Path | None) -> str:
    from gofer.core.usage_ledger import profile_usage_identity

    return profile_usage_identity(name, data_dir)


def key_of(provider: str, name: str, data_dir: Path | None) -> str:
    return daily.account_key(provider, identity(name, data_dir))


def track(
    state: dict[str, Any],
    readings: dict[str, dict[str, Any] | None],
    busy: set[str],
    names: dict[str, tuple[str, str]],
    clock: datetime,
    zone: ZoneInfo,
) -> dict[str, Any]:
    """Advance each account's daily count; returns a new state and never changes ``state``.

    ``readings`` holds each tracked account's newest weekly reading by key, ``busy`` the keys
    of accounts self-work has work running on, ``names`` each key's (provider, name).
    """
    accounts = dict(state.get("accounts") or {}) if isinstance(state.get("accounts"), dict) else {}
    for key in set(accounts) | set(readings) | busy:
        sub = daily.account_state(state, key)
        if key not in names and not (sub.get("last") or {}).get("busy"):
            # The account was removed or renamed: its count no longer names anything.
            accounts.pop(key, None)
            continue
        provider, name = names.get(key) or (
            str(sub.get("provider") or ""),
            str(sub.get("name") or ""),
        )
        updated = daily.advance(
            {k: v for k, v in sub.items() if k not in {"provider", "name"}},
            readings.get(key),
            key in busy,
            clock,
            zone,
        )
        last = updated.get("last") or {}
        seen = daily._instant(last.get("busy_until") or last.get("observed_at"))
        if key not in busy and (seen is None or clock - seen > FORGET_AFTER):
            accounts.pop(key, None)
            continue
        accounts[key] = {**updated, "provider": provider, "name": name}
    result = {k: v for k, v in state.items() if k != "accounts"}
    if accounts:
        result["accounts"] = accounts
    return result


def account_limit(
    settings: dict[str, Any],
    state: dict[str, Any],
    provider: str,
    name: str,
    data_dir: Path | None,
    clock: datetime,
    zone: ZoneInfo,
    running: Mapping[str, int] | None = None,
) -> tuple[str, str, str] | None:
    """Whether one account is past a self-work limit: a key, a sentence and a short phrase.

    ``running`` counts self-work tasks running on each account by its ``daily.account_key``,
    for the room Pace to reset keeps for them.
    """
    from gofer.core.usage_service import stored_windows

    who = identity(name, data_dir)
    if pacing.mode(settings) == pacing.PACE:
        on = (running or {}).get(daily.account_key(provider, who), 0)
        found = pacing.plan_account(settings, provider, data_dir, on, clock, profile=who)
        held = pacing.limit(found, name, zone, clock)
        if held:
            short = {
                "pace_unknown": f"{name} has no current allowance reading",
                "pace_ceiling": f"{name}'s 5-hour limit is nearly full",
                "pace_starting": f"{name} is starting its window",
            }.get(held[0], f"{name} is ahead of its pace line")
            return f"{held[0]}:{provider}:{name}", held[1], short
    cap = pacing.daily_cap(settings)
    if cap:
        sub = daily.account_state(state, daily.account_key(provider, who))
        reading = daily.read_weekly(data_dir, provider, who)
        stopped = daily.limit(cap, sub, reading, clock, zone, account=name)
        if stopped:
            # A credits window (Grok) says so: "has no current monthly credits reading".
            what = (reading or {}).get("measure") or "weekly"
            short = (
                f"{name} has no current {what} reading"
                if stopped[0] == "daily_allowance_unknown"
                else f"{name} reached the daily cap of {daily.cap_text(cap, reading)}"
            )
            return f"{stopped[0]}:{provider}:{name}", stopped[1], short
    percent = int(settings.get("rate_limit_percent") or 0)
    if percent:
        for window in stored_windows(provider, data_dir, profile=who):
            if window["percent"] >= percent:
                used = round(window["percent"])
                return (
                    f"rate:{provider}:{name}:{window['id']}",
                    f"Stopped starting new work: {name}'s {window['label']} limit is {used}% "
                    f"used, at or above the {percent}% set for self-work.{AFTER}",
                    f"{name}'s {window['label']} limit is {used}% used",
                )
    return None


def pick(
    settings: dict[str, Any],
    config: dict[str, Any],
    state: dict[str, Any],
    data_dir: Path,
    clock: datetime,
    zone: ZoneInfo,
    running: Mapping[str, int] | None = None,
) -> Pick:
    """The account self-work's next run would use, checked against self-work's limits.

    ``running`` counts self-work tasks running on each account (see ``account_limit``).
    """
    from gofer.core.provider_accounts import (
        AUTO,
        SIGNED_IN,
        account_adapter,
        account_headroom,
        account_status,
        choose_account,
        list_accounts,
    )
    from gofer.ui.rem_provider_accounts import (
        configured_account,
        pick_account,
        self_work_choices,
        self_work_config,
    )

    # Self-work's own account choice (Settings > Rem > Self-work) comes before Rem's setting.
    config = self_work_config(config, self_work_choices(settings))
    provider = str(config.get("provider") or "codex")
    adapter = account_adapter(provider)
    setting = configured_account(config, provider)
    if adapter is None or not setting:
        return Pick()
    accounts = list_accounts(data_dir, provider)
    if not accounts:
        return Pick()
    if setting != AUTO:
        chosen = pick_account(config, data_dir, provider=provider)
        if not chosen.get("name"):
            # The named account is gone: runs use the default sign-in.
            return Pick()
        stopped = account_limit(
            settings, state, provider, chosen["name"], data_dir, clock, zone, running
        )
        return Pick({**chosen, "provider": provider}, stopped[:2] if stopped else None)

    statuses: dict[str, dict[str, Any]] = {}

    def check(profile: Any, *, data_dir: Path | None = None) -> dict[str, Any]:
        # One sign-in check per account per pick, however many times it ranks them.
        if profile.name not in statuses:
            statuses[profile.name] = account_status(profile, data_dir=data_dir)
        return statuses[profile.name]

    limited: list[tuple[str, tuple[str, str, str]]] = []

    def choose(_: Path) -> dict[str, Any] | None:
        remaining = sorted(accounts)
        while remaining:
            choice = choose_account(data_dir, provider=provider, names=remaining, check=check)
            if choice is None:
                # A signed-in account skipped because it is full is a limit, like one at
                # self-work's own percent; it must not send runs to the default sign-in.
                for name in remaining:
                    profile = accounts.get(name)
                    if (
                        profile is None
                        or check(profile, data_dir=data_dir).get("state") != SIGNED_IN
                    ):
                        continue
                    room = account_headroom(profile, data_dir)
                    if not any(
                        room[key] is not None and room[key] >= 100
                        for key in ("seven_day", "five_hour", "credits")
                    ):
                        continue
                    what = (
                        "credits are used up"
                        if (room["credits"] or 0) >= 100
                        else ("usage limit is reached")
                    )
                    limited.append(
                        (
                            name,
                            (
                                f"rate:{provider}:{name}:full",
                                f"Stopped starting new work: {name}'s {what}.{AFTER}",
                                f"{name}'s {what}",
                            ),
                        )
                    )
                return None
            stopped = account_limit(
                settings, state, provider, choice["name"], data_dir, clock, zone, running
            )
            if stopped is None:
                choice["skipped"] = [
                    *(choice.get("skipped") or []),
                    *({"name": name, "reason": why[2]} for name, why in limited),
                ]
                if limited:
                    choice["why"] = (
                        f"{choice.get('why') or 'Most room left.'} Skipped for self-work: "
                        + "; ".join(why[2] for _, why in limited)
                        + "."
                    )
                return choice
            limited.append((choice["name"], stopped))
            remaining.remove(choice["name"])
        return None

    chosen = pick_account(config, data_dir, provider=provider, choose=choose)
    skipped = [{"name": name, "reason": why[2]} for name, why in limited]
    if chosen.get("name"):
        return Pick({**chosen, "provider": provider}, None, skipped)
    if not limited:
        # No account is signed in: runs use the default sign-in.
        return Pick()
    if len(limited) == 1 and len(accounts) == 1:
        return Pick(None, limited[0][1][:2], skipped)
    label = adapter.label
    return Pick(
        None,
        (
            "accounts_limited",
            f"Stopped starting new work: no {label} account has room for self-work. "
            + "; ".join(why[2] for _, why in limited)
            + f".{AFTER}",
        ),
        skipped,
    )


def shown(
    pick_now: dict[str, Any] | None,
    config: dict[str, Any],
    state: dict[str, Any],
    data_dir: Path,
    clock: datetime,
    zone: ZoneInfo,
) -> tuple[str, str] | None:
    """The (provider, name) the status line and Settings name, or None for the default sign-in.

    The account self-work picked last; before it picked one, Rem's named account, or under
    "Choose by usage" the account self-work used most today.
    """
    from gofer.core.provider_accounts import AUTO, account_adapter, list_accounts
    from gofer.ui.rem_provider_accounts import configured_account

    provider = str(config.get("provider") or "codex")
    if pick_now and pick_now.get("name") and pick_now.get("provider") == provider:
        return provider, str(pick_now["name"])
    setting = configured_account(config, provider)
    if account_adapter(provider) is None or not setting:
        return None
    accounts = list_accounts(data_dir, provider)
    if setting != AUTO:
        return (provider, setting) if setting in accounts else None
    used = [
        (daily.used_today(sub, clock, zone), str(sub.get("name") or ""))
        for sub in (state.get("accounts") or {}).values()
        if isinstance(sub, dict) and sub.get("provider") == provider and sub.get("name") in accounts
    ]
    used = [item for item in used if item[0] > 0]
    if used:
        return provider, max(used)[1]
    return None


def others(
    state: dict[str, Any], skip: str, clock: datetime, zone: ZoneInfo
) -> list[tuple[str, float]]:
    """Other accounts self-work used today, as (name, percent), without the key ``skip``."""
    found = []
    for key, sub in sorted((state.get("accounts") or {}).items()):
        if key == skip or not isinstance(sub, dict):
            continue
        used = daily.used_today(sub, clock, zone)
        if used > 0:
            found.append((str(sub.get("name") or ""), used))
    return found
