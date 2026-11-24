"""Which account of its provider a Rem run, reply or thread turn uses.

The order is fixed: the task's own account (the user's choice) first, then the
account the run already started on, then Settings > Rem's account for that
provider: the default sign-in, one named account, or "auto" to pick by usage
headroom. A run self-work started reads self-work's own choice for the provider
(Settings > Rem > Self-work) before Rem's setting; see ``self_work_config``.

Rem's setting is ``provider_accounts``, one value per provider. The first,
Claude-only version kept a single ``claude_account`` value; it is still read as
Claude Code's value when ``provider_accounts`` has none.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gofer.core.provider_accounts import AUTO

DEFAULT_PROVIDER = "claude_code"
MAX_ACCOUNT_NAME = 128
# A thread's own choice of the provider's default sign-in, which wins over Rem's
# setting. Account names start with a letter or digit, so no account has this name.
DEFAULT_SIGN_IN = "@default"
# A thread with no choice for its provider follows Rem's setting.
REM_SETTING = ""
_THREAD_CHOICE = re.compile(r"@default|auto|[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_PROVIDER_ID = re.compile(r"[a-z][a-z0-9_]{0,39}")


def configured_account(config: dict[str, Any], provider: str) -> str:
    """Rem's setting for this provider: "", "auto" or an account name."""
    accounts = config.get("provider_accounts")
    if isinstance(accounts, dict) and provider in accounts:
        return str(accounts.get(provider) or "")
    if provider == "claude_code":
        return str(config.get("claude_account") or "")
    return ""


def validate_provider_accounts(config: dict[str, Any]) -> None:
    """Raise ValueError unless Rem's account settings are well formed."""
    from gofer.core.provider_capabilities import PROVIDER_BINARIES

    legacy = config.get("claude_account", "")
    if not isinstance(legacy, str) or len(legacy) > MAX_ACCOUNT_NAME:
        raise ValueError("Invalid Claude Code account")
    accounts = config.get("provider_accounts", {})
    if not isinstance(accounts, dict) or len(accounts) > len(PROVIDER_BINARIES):
        raise ValueError("Invalid provider accounts")
    for provider, value in accounts.items():
        if provider not in PROVIDER_BINARIES:
            raise ValueError("Unknown provider in provider accounts")
        if not isinstance(value, str) or len(value) > MAX_ACCOUNT_NAME:
            raise ValueError(f"Invalid account for {provider}")


def validate_self_work_accounts(value: Any) -> dict[str, str]:
    """Self-work's own account choices, one per provider, or ValueError.

    Each is a thread-style choice: "" (Rem's setting), "@default", "auto" or an account
    name. Blank entries are dropped, so the result lists only the providers whose
    self-started runs don't follow Rem's setting.
    """
    from gofer.core.provider_capabilities import PROVIDER_BINARIES

    if not isinstance(value, dict) or len(value) > len(PROVIDER_BINARIES):
        raise ValueError("Invalid self-work accounts")
    for provider, choice in value.items():
        if provider not in PROVIDER_BINARIES:
            raise ValueError("Unknown provider in self-work accounts")
        if not isinstance(choice, str) or len(choice) > MAX_ACCOUNT_NAME:
            raise ValueError(f"Invalid self-work account for {provider}")
        if not valid_thread_choice(choice):
            raise ValueError(f"Invalid self-work account for {provider}")
    return {provider: choice for provider, choice in value.items() if choice != REM_SETTING}


def self_work_choices(settings: dict[str, Any]) -> dict[str, str]:
    """The saved self-work choices, leaving out anything malformed."""
    try:
        return validate_self_work_accounts(settings.get("provider_accounts") or {})
    except ValueError:
        return {}


def self_work_config(config: dict[str, Any], choices: dict[str, str]) -> dict[str, Any]:
    """Rem's configuration as a run self-work started sees it.

    Self-work's choice for a provider replaces Rem's setting for that provider; with no
    choice Rem's setting stays. The task's own account still wins over both, because
    ``pick_run_account`` reads it before Rem's setting.
    """
    if not choices:
        return config
    accounts = dict(config.get("provider_accounts") or {})
    if "claude_code" not in accounts and config.get("claude_account"):
        # The Claude-only setting, kept so providers without a choice still follow it.
        accounts["claude_code"] = str(config.get("claude_account") or "")
    for provider, choice in choices.items():
        accounts[provider] = "" if choice == DEFAULT_SIGN_IN else choice
    return {**config, "provider_accounts": accounts}


def self_work_menus(
    config: dict[str, Any], choices: dict[str, str], data_dir: Path
) -> list[dict[str, Any]]:
    """Settings > Rem > Self-work's account rows: one per provider with accounts.

    Each names Rem's setting and the account names, without checking sign-in, so the
    self-work view stays cheap to read every few seconds. A provider whose accounts
    were all removed stays listed while self-work still names one of them, so the
    choice can be put back.
    """
    from gofer.core.provider_accounts import adapters, list_accounts

    menus = []
    for provider, adapter in adapters().items():
        names = sorted(list_accounts(data_dir, provider))
        if not names and provider not in choices:
            continue
        setting = configured_account(config, provider)
        menus.append(
            {
                "provider": provider,
                "label": adapter.label,
                "choice": choices.get(provider, REM_SETTING),
                "rem_setting": setting,
                "rem_setting_label": _setting_label(setting),
                "accounts": names,
            }
        )
    return menus


def _task_account(config: dict[str, Any], provider: str) -> str:
    task = config.get("task_account")
    if isinstance(task, str) and task:
        return task
    if provider == "claude_code" and config.get("claude_account_explicit"):
        return str(config.get("claude_account") or "")
    return ""


def _kept(run: dict[str, Any], provider: str) -> Any:
    kept = run.get("provider_account")
    if isinstance(kept, dict) and kept.get("provider", DEFAULT_PROVIDER) == provider:
        return kept
    if provider == "claude_code":
        return run.get("claude_account")
    return None


def pick_run_account(
    run: dict[str, Any],
    config: dict[str, Any],
    data_dir: Path,
    *,
    provider: str = DEFAULT_PROVIDER,
    choose: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    from gofer.core.provider_accounts import list_accounts

    accounts = list_accounts(data_dir, provider)
    wanted = _task_account(config, provider)
    if wanted:
        if wanted not in accounts:
            return {
                "name": "",
                "source": "default",
                "why": f"The task's account '{wanted}' no longer exists; using the default "
                "sign-in.",
            }
        return {"name": wanted, "source": "task", "why": "Chosen for this task."}
    kept = _kept(run, provider)
    if isinstance(kept, dict) and (not kept.get("name") or kept.get("name") in accounts):
        # A run stays on the account it started on.
        return kept
    return pick_account(config, data_dir, provider=provider, choose=choose)


def pick_account(
    config: dict[str, Any],
    data_dir: Path,
    *,
    provider: str = DEFAULT_PROVIDER,
    choose: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Rem's setting alone: for a new run or one of Rem's own replies."""
    from gofer.core.provider_accounts import account_adapter, choose_account, list_accounts

    adapter = account_adapter(provider)
    label = adapter.label if adapter else provider
    wanted = configured_account(config, provider)
    if not wanted or adapter is None:
        return {"name": "", "source": "default", "why": "Rem uses the default sign-in."}
    if wanted != AUTO:
        if wanted in list_accounts(data_dir, provider):
            return {"name": wanted, "source": "setting", "why": f"Rem's {label} account."}
        return {
            "name": "",
            "source": "default",
            "why": f"The account '{wanted}' no longer exists; using the default sign-in.",
        }
    if choose is None:
        choice = choose_account(data_dir, provider=provider)
    else:
        choice = choose(data_dir)
    if choice is None:
        return {
            "name": "",
            "source": "default",
            "why": f"No {label} account is signed in with room left; using the default sign-in.",
        }
    return {
        "name": choice["name"],
        "source": AUTO,
        "why": choice.get("why") or "Most room left.",
        "skipped": choice.get("skipped") or [],
    }


def usage_next_account(
    config: dict[str, Any],
    data_dir: Path,
    *,
    provider: str = DEFAULT_PROVIDER,
    choose: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """Settings > Usage's "Rem uses next" badge: pick_account's answer, only under "auto".

    With the default sign-in or one named account there is no choice to show, so
    this returns None and no card gets the badge.
    """
    if configured_account(config, provider) != AUTO:
        return None
    return pick_account(config, data_dir, provider=provider, choose=choose)


def reply_account(provider: str, config: dict[str, Any], data_dir: Path | None) -> str | None:
    """The account Rem's own replies use with this provider (Settings > Rem), or None."""
    if data_dir is None or not configured_account(config, provider):
        return None
    return pick_account(config, data_dir, provider=provider).get("name") or None


def thread_account(
    conversation_id: Any,
    requested: str,
    data_dir: Path,
    *,
    provider: str = DEFAULT_PROVIDER,
    choose: Callable[..., dict[str, Any] | None] | None = None,
) -> str | None:
    """The account a desktop thread's turn uses. "auto" stays on the thread's last account.

    The thread's saved provider session (chat_sessions.py) records the account its
    last turn ran as. Keeping it avoids moving the conversation between accounts
    every turn; a new pick happens only when that account is gone or needs sign-in.
    """
    from gofer.core.provider_accounts import SIGNED_IN, account_status, list_accounts
    from gofer.ui.chat_sessions import _digest

    if requested == DEFAULT_SIGN_IN:
        return None
    if requested != AUTO:
        return requested or None
    accounts = list_accounts(data_dir, provider)
    if isinstance(conversation_id, str) and conversation_id:
        session = data_dir / "chat-provider-sessions" / _digest(conversation_id) / "session.json"
        try:
            last = str(json.loads(session.read_text(encoding="utf-8")).get("lastProvider") or "")
        except (OSError, ValueError, AttributeError):
            last = ""
        last_provider, _, name = last.partition("@")
        if (
            last_provider == provider
            and name in accounts
            and account_status(accounts[name], data_dir=data_dir).get("state") == SIGNED_IN
        ):
            return name
    return (
        pick_account(
            {"provider_accounts": {provider: AUTO}}, data_dir, provider=provider, choose=choose
        ).get("name")
        or None
    )


def valid_thread_choice(value: Any) -> bool:
    """A thread's account choice: "" (Rem's setting), "@default", "auto" or an account name."""
    return isinstance(value, str) and (
        value == REM_SETTING or bool(_THREAD_CHOICE.fullmatch(value))
    )


def clean_thread_accounts(value: Any) -> dict[str, str]:
    """A thread's saved choices, one per provider, without blanks or malformed entries."""
    if not isinstance(value, dict):
        return {}
    return {
        provider: choice
        for provider, choice in list(value.items())[:40]
        if isinstance(provider, str)
        and _PROVIDER_ID.fullmatch(provider)
        and valid_thread_choice(choice)
        and choice != REM_SETTING
    }


def turn_account(
    conversation_id: Any,
    choice: str,
    config: dict[str, Any],
    data_dir: Path,
    *,
    provider: str = DEFAULT_PROVIDER,
) -> str | None:
    """The account a thread turn runs as: the thread's own choice, else Rem's setting.

    The desktop window sends the same choice as providerProfile; turns sent from the
    phone resolve it here from the thread's saved choice.
    """
    from gofer.core.provider_accounts import account_adapter

    if account_adapter(provider) is None:
        return None
    wanted = (
        choice if valid_thread_choice(choice) and choice else configured_account(config, provider)
    )
    if not wanted:
        return None
    return thread_account(conversation_id, wanted, data_dir, provider=provider)


def account_menus(
    config: dict[str, Any],
    data_dir: Path,
    *,
    check: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The thread picker's Account menu for each provider that has accounts.

    Each entry names Rem's setting for the provider and lists its accounts with their
    sign-in state and last usage reading. Providers without accounts are left out, so
    the picker shows no menu for them.
    """
    from gofer.core.provider_accounts import accounts_overview, adapters

    overview = accounts_overview(data_dir, check=check, setup=False)
    menus = []
    for provider, adapter in adapters().items():
        accounts = [
            {
                "name": view["name"],
                "state": view.get("state") or "unknown",
                "needs_sign_in": bool(view.get("needs_sign_in")),
                "last_reading": view.get("last_reading"),
                # A device sign-in a phone can finish, and the one in progress, if any.
                "phone_sign_in": bool(view.get("phone_sign_in")),
                "sign_in": view.get("sign_in"),
            }
            for view in overview["accounts"]
            if view.get("provider") == provider
        ]
        if not accounts:
            continue
        setting = configured_account(config, provider)
        menus.append(
            {
                "provider": provider,
                "label": adapter.label,
                "rem_setting": setting,
                "rem_setting_label": _setting_label(setting),
                "accounts": accounts,
            }
        )
    return menus


def _setting_label(setting: str) -> str:
    if setting == AUTO:
        return "Choose by usage"
    return setting or "Default sign-in"
