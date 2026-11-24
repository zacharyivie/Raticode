"""Which provider account an organization worker's turns run as.

A worker's ``account`` is the ID of one of its provider's accounts in Settings >
Providers (provider_accounts.py), or None for the provider's default sign-in. The ID,
not the name, is stored, so renaming the account keeps the choice.

Saving checks a new choice against the accounts that exist. A choice saved earlier can
stop fitting: the account is removed or signed out. A turn then runs on the default
sign-in and records why, as Rem's runs do (rem_provider_accounts.pick_run_account).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_WHY = "This worker uses the default sign-in."


def _label(provider: str) -> str:
    from gofer.core.provider_accounts import account_adapter

    adapter = account_adapter(provider)
    return adapter.label if adapter else provider


def provider_accounts(data_dir: Path) -> list[dict[str, str]]:
    """Every provider's accounts as {provider, id, name}, sorted by provider then name."""
    from gofer.core.provider_accounts import adapters, list_accounts

    choices = []
    for provider in adapters():
        for name, profile in sorted(list_accounts(data_dir, provider).items()):
            choices.append({"provider": provider, "id": str(profile.account_id), "name": name})
    return choices


def find_account(data_dir: Path, provider: str, account_id: str) -> Any:
    """The provider's account with this ID, or None when it's gone."""
    from gofer.core.provider_accounts import account_adapter, list_accounts

    if account_adapter(provider) is None:
        return None
    return next(
        (
            profile
            for profile in list_accounts(data_dir, provider).values()
            if profile.account_id == account_id
        ),
        None,
    )


def check_accounts(
    previous: dict[str, Any], config: dict[str, Any], data_dir: Path
) -> dict[str, Any]:
    """The configuration to save, or ValueError for an account that doesn't fit.

    A newly chosen account must be one of the worker's provider's accounts. A worker
    that switched provider loses an account it already had, which belongs to the old
    provider, the way the editor clears it. An unchanged choice is kept even when the
    account was removed since: its turns fall back to the default sign-in.
    """
    employees = config.get("employees")
    if not isinstance(employees, list):
        return config
    before = {e.get("id"): e for e in previous.get("employees") or [] if isinstance(e, dict)}
    checked: list[Any] = []
    changed = False
    for employee in employees:
        account = employee.get("account") if isinstance(employee, dict) else None
        if not isinstance(account, str) or not account:
            checked.append(employee)
            continue
        provider = str(employee.get("provider") or "codex")
        old = before.get(employee.get("id")) or {}
        same_account = old.get("account") == account
        if same_account and str(old.get("provider") or "codex") == provider:
            checked.append(employee)
            continue
        if find_account(data_dir, provider, account) is not None:
            checked.append(employee)
            continue
        if same_account:
            # The provider changed, and the account belongs to the one before.
            checked.append({**employee, "account": None})
            changed = True
            continue
        name = employee.get("name") or employee.get("id") or "This worker"
        raise ValueError(
            f"{name}'s account isn't one of the {_label(provider)} accounts in Settings > "
            "Providers. Choose one of them, or the default sign-in."
        )
    return {**config, "employees": checked} if changed else config


def pick_worker_account(
    employee: dict[str, Any],
    data_dir: Path,
    *,
    status: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The account one of the worker's turns runs as, and why.

    Returns accountId and name (both empty for the default sign-in), source ("worker"
    or "default") and why. A removed account, or one that needs signing in again,
    falls back to the default sign-in.
    """
    from gofer.core.provider_accounts import NEEDS_SIGN_IN, account_status

    wanted = employee.get("account")
    if not isinstance(wanted, str) or not wanted:
        return {"accountId": None, "name": "", "source": "default", "why": DEFAULT_WHY}
    provider = str(employee.get("provider") or "codex")
    label = _label(provider)
    profile = find_account(data_dir, provider, wanted)
    if profile is None:
        return {
            "accountId": wanted,
            "name": "",
            "source": "default",
            "why": f"This worker's {label} account was removed; using the default sign-in.",
        }
    check = status or account_status
    try:
        state = check(profile, data_dir=data_dir).get("state")
    except (OSError, ValueError):
        # No answer about the sign-in isn't a sign-out: the turn says what went wrong.
        state = None
    if state == NEEDS_SIGN_IN:
        return {
            "accountId": wanted,
            "name": "",
            "source": "default",
            "why": f"This worker's {label} account '{profile.name}' is signed out; using the "
            "default sign-in. Sign it in again in Settings > Providers.",
        }
    return {
        "accountId": wanted,
        "name": profile.name,
        "source": "worker",
        "why": f"{label} account '{profile.name}', chosen for this worker.",
    }
