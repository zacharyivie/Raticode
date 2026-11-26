"""Claude Code accounts: the first provider account adapter (provider_accounts.py).

Each account is a ``claude_code`` provider profile with an ``account_id``. Its
folder, ``<data>/claude-accounts/<account_id>``, is the ``CLAUDE_CONFIG_DIR``
Claude Code runs with, so Claude Code keeps that account's sign-in there (or in
the macOS Keychain entry it keys to that folder) and refreshes it itself.

Raticode never reads, copies, stores or sends a token or credential file of
these accounts. It only starts the unmodified ``claude`` with the folder set:
``claude auth login`` to sign in, ``claude auth status`` to see whether the
account is signed in, and ``claude -p /usage`` for its limits.

The functions here keep their Claude-only meaning; the provider-neutral ones
live in provider_accounts.py.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.core import provider_accounts as _accounts
from gofer.core.provider_accounts import (
    ACCOUNT_ACTIONS,
    ACCOUNT_ID_PATTERN,
    NEEDS_SIGN_IN,
    READING_FRESH_SECONDS,
    SIGN_IN_TIMEOUT_SECONDS,
    SIGNED_IN,
    STATUS_TIMEOUT_SECONDS,
    STATUS_TTL_SECONDS,
    UNAVAILABLE,
    AccountAdapter,
    AccountError,
    SignIns,
    _status_cache,
    account_action,
    account_config_dir,
    account_headroom,
    account_view,
    accounts_overview,
    check_status,
    duplicate_warnings,
    forget_status,
    remove_account,
    rename_account,
    sign_ins,
)

if TYPE_CHECKING:
    from gofer.core.provider_profiles import ProviderProfile

__all__ = [
    "ACCOUNTS_DIR",
    "ACCOUNT_ACTIONS",
    "ACCOUNT_ID_PATTERN",
    "ADAPTER",
    "NEEDS_SIGN_IN",
    "OTHER_AUTH_ENV",
    "PROVIDER",
    "READING_FRESH_SECONDS",
    "SETUP_ALLOWLIST",
    "SIGNED_IN",
    "SIGN_IN_TIMEOUT_SECONDS",
    "STATUS_TIMEOUT_SECONDS",
    "STATUS_TTL_SECONDS",
    "UNAVAILABLE",
    "AccountError",
    "SignIns",
    "_status_cache",
    "account_action",
    "account_config_dir",
    "account_headroom",
    "account_view",
    "accounts_overview",
    "check_status",
    "duplicate_warnings",
    "forget_status",
    "remove_account",
    "rename_account",
    "sign_ins",
]

PROVIDER = "claude_code"
ACCOUNTS_DIR = "claude-accounts"
# Shared setup a new account may start with. Copied, never linked, and never
# anything that holds a sign-in, history or another account's settings.
SETUP_ALLOWLIST = ("CLAUDE.md", "skills", "commands", "agents", "output-styles")
# Environment that would make Claude Code use something other than the folder's sign-in.
OTHER_AUTH_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


def _claude_executable() -> str | None:
    from gofer.core.provider_capabilities import resolve_provider_executable

    return resolve_provider_executable("claude_code")


def default_setup_source() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def parse_status(payload: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Read ``claude auth status --json``. Keeps the plan; identity becomes a hash.

    The email is returned for display only and is never written anywhere.
    """
    if not isinstance(payload, dict):
        return {"state": UNAVAILABLE, "reason": "Claude Code gave no sign-in status."}
    expires = payload.get("loginExpiresAt")
    expired = False
    if isinstance(expires, str) and expires:
        try:
            expired = datetime.fromisoformat(expires.replace("Z", "+00:00")) <= (
                now or datetime.now(UTC)
            )
        except ValueError:
            expires = None
    signed_in = payload.get("loggedIn") is True and not expired
    method = payload.get("authMethod") if isinstance(payload.get("authMethod"), str) else None
    identity_parts = [str(payload.get(key) or "").strip().lower() for key in ("email", "orgId")]
    identity = (
        hashlib.sha256("\0".join(identity_parts).encode()).hexdigest()[:16]
        if signed_in and any(identity_parts)
        else None
    )
    result: dict[str, Any] = {
        "state": SIGNED_IN if signed_in else NEEDS_SIGN_IN,
        "auth_method": method,
        "plan": next(
            (
                str(payload[key])
                for key in ("planTier", "subscriptionType")
                if isinstance(payload.get(key), str) and payload.get(key)
            ),
            None,
        ),
        "login_expires_at": expires if isinstance(expires, str) else None,
        "email": payload.get("email")
        if signed_in and isinstance(payload.get("email"), str)
        else None,
        "identity": identity,
    }
    if not signed_in:
        result["reason"] = (
            "The sign-in expired. Sign in again." if expired else "Not signed in yet."
        )
    return result


def _read_status(output: str) -> dict[str, Any]:
    return parse_status(_accounts.read_json_status(output))


async def _read_usage(
    executable: str, env: dict[str, str], data_dir: Path | None
) -> list[dict[str, Any]] | None:
    from gofer.core.provider_usage import _claude_cli_allowance

    return await _claude_cli_allowance(executable, env, data_dir)


ADAPTER = _accounts.register_adapter(
    AccountAdapter(
        provider=PROVIDER,
        label="Claude Code",
        cli_name="claude",
        folder_env="CLAUDE_CONFIG_DIR",
        folder_name=ACCOUNTS_DIR,
        sign_in_args=("auth", "login", "--claudeai"),
        sign_out_args=("auth", "logout"),
        status_args=("auth", "status", "--json"),
        read_status=_read_status,
        read_usage=_read_usage,
        usage_source="Claude Code /usage",
        other_auth_env=OTHER_AUTH_ENV,
        setup_allowlist=SETUP_ALLOWLIST,
        setup_description="CLAUDE.md, skills, commands and agents",
        setup_source=lambda: default_setup_source(),
        identity_noun="Claude account",
        # Looked up when called, so tests can stand in for the claude CLI or its status.
        executable=lambda: _claude_executable(),
        status_check=lambda profile, **options: check_status(profile, **options),
    )
)


def accounts_root(data_dir: Path | None = None) -> Path:
    return _accounts.accounts_root(PROVIDER, data_dir)


def is_account(profile: ProviderProfile | None) -> bool:
    return _accounts.is_account(profile, PROVIDER)


def account_dir(account_id: str, data_dir: Path | None = None) -> Path:
    if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise AccountError("Invalid Claude account ID")
    return accounts_root(data_dir) / account_id


def list_accounts(data_dir: Path | None = None) -> dict[str, ProviderProfile]:
    return _accounts.list_accounts(data_dir, PROVIDER)


def get_account(name: str, data_dir: Path | None = None) -> ProviderProfile:
    return _accounts.get_account(name, data_dir, PROVIDER)


def copy_setup(source: Path, target: Path) -> list[str]:
    """Copy the allowlisted setup items; symlinks are skipped, nothing else is read."""
    return _accounts.copy_setup(source, target, SETUP_ALLOWLIST)


def create_account(
    name: str,
    *,
    data_dir: Path | None = None,
    copy_shared_setup: bool = False,
    setup_source: Path | None = None,
) -> ProviderProfile:
    return _accounts.create_account(
        name,
        provider=PROVIDER,
        data_dir=data_dir,
        copy_shared_setup=copy_shared_setup,
        setup_source=setup_source,
    )


def choose_account(
    data_dir: Path | None = None,
    *,
    names: Iterable[str] | None = None,
    check: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Pick the signed-in Claude Code account with the most room left, or None."""
    return _accounts.choose_account(data_dir, provider=PROVIDER, names=names, check=check)


def account_deny_rules(data_dir: Path | None = None) -> list[str]:
    """Read and Edit deny rules for every provider's account folders."""
    return _accounts.account_deny_rules(data_dir)
