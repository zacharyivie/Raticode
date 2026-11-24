"""Settings > Usage account cards: sign-in state per account and Rem's next Claude Code pick.

The usage overview already carries one row per provider account with its windows.
This adds what the cards at the top of the page need beyond that:

- ``sign_in_state`` for account rows of any provider with accounts, and for
  Claude Code's default sign-in: ``signed_in``, ``needs_sign_in``,
  ``unavailable`` or ``unknown``. An account reads it from its CLI's own status
  command (cached, see ``provider_accounts.check_status``; ``claude auth status``
  for Claude Code); Claude Code's default sign-in reads it from its last usage
  refresh.
- ``rem_next`` on the one row per provider Rem would use next (Claude Code, Codex),
  with the reason ``pick_account`` gives, only when Rem's account setting for that
  provider is "choose by usage". Readings stay per account: nothing here adds
  accounts up.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

SIGN_IN_STATES = ("signed_in", "needs_sign_in", "unavailable", "unknown")


def _default_sign_in(row: dict[str, Any]) -> str:
    status = row.get("refresh_status") or row.get("status")
    if status in {"auth_required", "authentication_required"} or row.get("needs_sign_in"):
        return "needs_sign_in"
    if row.get("windows") and row.get("status") in {"available", "stale"}:
        return "signed_in"
    return "unknown"


def _mark_next(rows: list[dict[str, Any]], provider: str, pick: dict[str, Any]) -> tuple[str, str]:
    """Put the "Rem uses next" badge on the provider's row Rem picked."""
    name = str(pick.get("name") or "")
    why = str(pick.get("why") or "")
    for row in rows:
        if row.get("provider") == provider and (row.get("profile") or "") == name:
            row["rem_next"] = {"why": why}
            break
    return name, why


def with_account_glance(
    overview: dict[str, Any],
    data_dir: Path | None = None,
    *,
    rem_pick: dict[str, Any] | None = None,
    check: Callable[..., dict[str, Any]] | None = None,
    rem_picks: dict[str, dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """The overview with each account row's sign-in state and Rem's next account.

    ``rem_pick`` is ``pick_account``'s answer for Claude Code when Rem chooses by
    usage, else None. ``rem_picks`` holds the same per provider, for the others.
    A pick's ``name`` is an account, or "" for the default sign-in.
    """
    from gofer.core.provider_accounts import (
        NEEDS_SIGN_IN,
        SIGNED_IN,
        account_adapter,
        account_status,
        list_accounts,
    )

    check = check or account_status
    try:
        accounts = list_accounts(data_dir)
    except (OSError, ValueError):
        accounts = {}
    rows: list[dict[str, Any]] = []
    for original in overview.get("accounts") or []:
        row = dict(original)
        profile = accounts.get(str(row.get("profile") or ""))
        if profile is not None and profile.subscription != row.get("provider"):
            profile = None
        if profile is not None or row.get("provider") == "claude_code":
            # Any provider's account reads its CLI's own status; the default
            # sign-in reads it from its last usage refresh (Claude Code only).
            if profile is not None:
                status = check(profile, data_dir=data_dir)
                state = str(status.get("state") or "unknown")
                row["sign_in_state"] = state if state in SIGN_IN_STATES else "unknown"
                row["sign_in_checked_at"] = status.get("checked_at")
                # A paired phone can sign this account in with a link and a code.
                adapter = account_adapter(profile.subscription)
                row["phone_sign_in"] = bool(
                    adapter and adapter.phone_sign_in and adapter.device_sign_in_args
                )
                if state == NEEDS_SIGN_IN:
                    row["needs_sign_in"] = True
                elif state == SIGNED_IN:
                    # Signed in again since the last usage refresh stored "needs sign-in".
                    row.pop("needs_sign_in", None)
            elif not row.get("profile"):
                row["sign_in_state"] = _default_sign_in(row)
        rows.append(row)
    result = {**overview, "accounts": rows}
    for provider, pick in (rem_picks or {}).items():
        if provider != "claude_code" and pick is not None:
            _mark_next(rows, provider, pick)
    if rem_pick is None:
        return result
    name, why = _mark_next(rows, "claude_code", rem_pick)
    result["rem_claude_account"] = {
        "mode": "auto",
        "name": name,
        "why": why,
        "skipped": [
            {"name": str(item.get("name") or ""), "reason": str(item.get("reason") or "")}
            for item in rem_pick.get("skipped") or []
            if isinstance(item, dict)
        ],
    }
    return result
