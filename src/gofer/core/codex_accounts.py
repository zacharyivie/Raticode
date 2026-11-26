"""Codex accounts: a provider account adapter (provider_accounts.py) for the codex CLI.

Each account is a ``codex`` provider profile with an ``account_id``. Its folder,
``<data>/codex-accounts/<account_id>``, is the ``CODEX_HOME`` Codex runs with:
``codex exec`` turns (and their resume and fork), the steerable app-server turns
(codex_steering.py) and the usage reader. Codex keeps that account's sign-in there
(``auth.json``, or a keyring entry it keys to the folder) and refreshes it itself.

Raticode never reads, copies, stores or sends a token or ``auth.json``. It only
starts the unmodified ``codex`` with the folder set and the other sign-in variables
removed: ``codex login`` to sign in, ``codex logout`` before removing, and the
app-server's ``account/read`` (email and plan) and ``account/rateLimits/read``
(limits). The email is shown, never stored; a hash of it only warns when two
folders hold the same ChatGPT account.

Codex's own sandbox doesn't keep a turn out of other account folders: in both
``read-only`` and ``workspace-write`` it may read the whole disk, and Raticode's
data folder is one of a trusted turn's writable folders. See the website's
Providers page ("What Codex's sandbox can reach").
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Any

from gofer.core import provider_accounts as _accounts
from gofer.core.provider_accounts import (
    NEEDS_SIGN_IN,
    SIGNED_IN,
    STATUS_TIMEOUT_SECONDS,
    UNAVAILABLE,
    AccountAdapter,
)

PROVIDER = "codex"
ACCOUNTS_DIR = "codex-accounts"
# Environment that would make Codex use something other than the folder's sign-in.
OTHER_AUTH_ENV = ("OPENAI_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_API_KEY")
# Shared setup a new account may start with. Copied, never linked. config.toml is
# copied without its sign-in settings; auth.json, sessions and history never are.
SETUP_ALLOWLIST = ("AGENTS.md", "skills", "config.toml")
# Where a ChatGPT account turns on the device code sign-in that phones use.
DEVICE_SIGN_IN_HELP = (
    "Turn on device code sign-in for Codex in ChatGPT: Settings › Security "
    "(chatgpt.com/settings/security). For a workspace account, an admin allows it in "
    "the workspace's permissions."
)
# Largest config.toml a new account copies.
MAX_CONFIG_BYTES = 1024 * 1024
# Most output the sign-in check reads from ``codex app-server``.
STATUS_OUTPUT_BYTES = 1024 * 1024

# config.toml settings that pick or hold a sign-in, or send credentials somewhere.
CONFIG_AUTH_KEYS = frozenset(
    {
        "preferred_auth_method",
        "forced_login_method",
        "forced_chatgpt_workspace_id",
        "cli_auth_credentials_store",
        "mcp_oauth_credentials_store",
        "chatgpt_base_url",
        "openai_base_url",
        "model_provider",
        "model_providers",
        "experimental_bearer_token",
        "env",
        "set",
        "http_headers",
        "env_http_headers",
        "headers",
    }
)
# Any setting whose name says it holds a secret, at any depth.
_SECRET_WORDS = ("token", "secret", "password", "passwd", "api_key", "apikey", "auth", "credential")


def _codex_executable() -> str | None:
    from gofer.core.provider_capabilities import resolve_provider_executable

    return resolve_provider_executable("codex")


def default_setup_source() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


# --- shared setup -----------------------------------------------------------------------


def _is_auth_key(name: str) -> bool:
    lowered = name.lower()
    return lowered in CONFIG_AUTH_KEYS or any(word in lowered for word in _SECRET_WORDS)


def _without_auth(table: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in table.items():
        if _is_auth_key(key):
            continue
        if isinstance(value, dict):
            value = _without_auth(value)
        elif isinstance(value, list):
            value = [_without_auth(item) if isinstance(item, dict) else item for item in value]
        result[key] = value
    return result


def config_without_auth(text: str) -> str:
    """config.toml with every sign-in setting removed, as TOML. Raises ValueError if invalid."""
    import tomli_w

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("config.toml isn't valid TOML") from exc
    return tomli_w.dumps(_without_auth(document))


def copy_codex_setup(source: Path, target: Path) -> list[str]:
    """Copy AGENTS.md and skills as they are, and config.toml without sign-in settings."""
    copied = _accounts.copy_setup(source, target, ("AGENTS.md", "skills"))
    config = source / "config.toml"
    destination = target / "config.toml"
    if (
        config.is_file()
        and not config.is_symlink()
        and not destination.exists()
        and config.stat().st_size <= MAX_CONFIG_BYTES
    ):
        try:
            text = config_without_auth(config.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return copied
        destination.write_text(text, encoding="utf-8")
        if os.name == "posix":
            destination.chmod(0o600)
        copied.append("config.toml")
    return copied


# The items compared and copied as plain files; config.toml goes through the cleaner.
PLAIN_SETUP = ("AGENTS.md", "skills")


def _cleaned_source_config(source: Path) -> dict[str, Any] | None:
    """The source config.toml's settings without sign-in settings, or None if unusable."""
    config = source / "config.toml"
    try:
        if not config.is_file() or config.is_symlink() or config.stat().st_size > MAX_CONFIG_BYTES:
            return None
        return _without_auth(tomllib.loads(config.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _covers(have: dict[str, Any], want: dict[str, Any]) -> bool:
    """Whether ``have`` sets everything ``want`` does, the same way. Extra settings are fine."""
    for key, value in want.items():
        if key not in have:
            return False
        if isinstance(value, dict) and isinstance(have[key], dict):
            if not _covers(have[key], value):
                return False
        elif have[key] != value:
            return False
    return True


def _merged(have: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
    result = dict(have)
    for key, value in want.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merged(result[key], value)
        else:
            result[key] = value
    return result


def _account_config(target: Path) -> dict[str, Any] | None:
    """The account's config.toml settings; {} when it has none; None to leave it alone."""
    destination = target / "config.toml"
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        return None
    if not destination.exists():
        return {}
    try:
        return tomllib.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        # Codex can't read it either; the cleaned copy replaces it.
        return {}


def _config_differs(source: Path, target: Path) -> bool:
    want = _cleaned_source_config(source)
    have = _account_config(target)
    if not want or have is None:
        return False
    return not _covers(have, want)


def codex_setup_differences(source: Path, target: Path) -> list[str]:
    """AGENTS.md and skills compared as files; config.toml compared after the cleaner."""
    changed = _accounts.setup_differences(source, target, PLAIN_SETUP)
    if _config_differs(source, target):
        changed.append("config.toml")
    return changed


def recopy_codex_setup(source: Path, target: Path, names: list[str]) -> list[str]:
    """Copy again only ``names``. config.toml gets the source's cleaned settings merged in.

    The account's own settings, including its sign-in settings, stay; auth.json,
    sessions and history are never read or written.
    """
    import tomli_w

    copied = _accounts.recopy_setup_items(
        source, target, [name for name in names if name in PLAIN_SETUP]
    )
    if "config.toml" in names and _config_differs(source, target):
        want = _cleaned_source_config(source) or {}
        have = _account_config(target) or {}
        destination = target / "config.toml"
        destination.write_text(tomli_w.dumps(_merged(have, want)), encoding="utf-8")
        if os.name == "posix":
            destination.chmod(0o600)
        copied.append("config.toml")
    return copied


# --- status ------------------------------------------------------------------------------


def parse_account(response: Any) -> dict[str, Any]:
    """Read app-server ``account/read``. Keeps the plan; the identity becomes a hash.

    The email is returned for display only and is never written anywhere.
    """
    if not isinstance(response, dict):
        return {"state": UNAVAILABLE, "reason": "Codex gave no sign-in status."}
    account = response.get("account")
    if not isinstance(account, dict):
        return {
            "state": NEEDS_SIGN_IN,
            "auth_method": None,
            "plan": None,
            "email": None,
            "identity": None,
            "reason": "Not signed in yet.",
        }
    kind = account.get("type")
    if kind == "chatgpt":
        email = account.get("email") if isinstance(account.get("email"), str) else None
        plan = account.get("planType")
        return {
            "state": SIGNED_IN,
            "auth_method": "chatgpt",
            "plan": plan if isinstance(plan, str) and plan and plan != "unknown" else None,
            "email": email,
            "identity": (
                hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
                if email and email.strip()
                else None
            ),
        }
    labels = {"apiKey": "API key", "amazonBedrock": "Amazon Bedrock"}
    return {
        "state": SIGNED_IN,
        "auth_method": str(kind or "") or None,
        "plan": labels.get(str(kind), None),
        "email": None,
        "identity": None,
    }


async def _app_server_account(
    executable: str, env: dict[str, str], folder: Path, *, timeout: float
) -> dict[str, Any] | None:
    """Ask a short-lived ``codex app-server`` for ``account/read``; its result or None."""
    from gofer.core.credential_mask import masked_command
    from gofer.core.provider_usage import codex_account_read
    from gofer.subscriptions.acp_transport import AcpTransportError, open_acp_transport

    command = masked_command(
        [executable, "app-server"], data_dir=folder.parent.parent, keep=[folder]
    )
    try:
        async with open_acp_transport(
            command,
            cwd=folder,
            # The transport starts from Raticode's own environment; naming the other
            # sign-ins again keeps them out of it.
            env={**env, **ADAPTER.folder_overrides(folder)},
            timeout=timeout,
            max_output_bytes=STATUS_OUTPUT_BYTES,
            allow_missing_jsonrpc=True,
        ) as rpc:
            return await codex_account_read(rpc, client="raticode_accounts", timeout=timeout)
    except AcpTransportError:
        return None


def read_codex_status(
    executable: str, env: dict[str, str], folder: Path, *, timeout: float = STATUS_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """The account's sign-in state from Codex's own ``account/read``.

    Every caller is sync code outside an event loop (request threads, the CLI, device
    threads, quick_status's thread and asyncio.to_thread), so this runs its own loop.
    """
    return parse_account(asyncio.run(_app_server_account(executable, env, folder, timeout=timeout)))


def _read_status(output: str) -> dict[str, Any]:
    # Only used if something runs status_args directly; Codex reads it through the app-server.
    return parse_account(_accounts.read_json_status(output))


async def _read_usage(
    executable: str, env: dict[str, str], _data_dir: Path | None
) -> list[dict[str, Any]] | None:
    from gofer.core.provider_usage import codex_account_allowance

    return await codex_account_allowance(executable, env)


ADAPTER = _accounts.register_adapter(
    AccountAdapter(
        provider=PROVIDER,
        label="Codex",
        cli_name="codex",
        folder_env="CODEX_HOME",
        folder_name=ACCOUNTS_DIR,
        sign_in_args=("login",),
        sign_out_args=("logout",),
        status_args=("app-server",),
        read_status=_read_status,
        # Looked up when called, so tests can stand in for the codex CLI.
        status_reader=lambda executable, env, folder: read_codex_status(executable, env, folder),
        read_usage=_read_usage,
        usage_source="Codex account/rateLimits/read",
        other_auth_env=OTHER_AUTH_ENV,
        setup_allowlist=SETUP_ALLOWLIST,
        setup_description="AGENTS.md, skills and config.toml (without sign-in settings)",
        setup_source=lambda: default_setup_source(),
        setup_copier=lambda source, target: copy_codex_setup(source, target),
        setup_comparer=lambda source, target: codex_setup_differences(source, target),
        setup_recopier=lambda source, target, names: recopy_codex_setup(source, target, names),
        identity_noun="ChatGPT account",
        executable=lambda: _codex_executable(),
        headroom_by_kind=True,
        sign_in_output=True,
        # Prints https://auth.openai.com/codex/device and a one-time code (stdout, each on
        # its own line), then exits 0 once the code is approved, within 15 minutes.
        device_sign_in_args=("login", "--device-auth"),
        device_sign_in_help=DEVICE_SIGN_IN_HELP,
        phone_sign_in=True,
    )
)


def account_home(name: str, data_dir: Path | None = None) -> Path:
    """The CODEX_HOME of the Codex account named ``name``."""
    return _accounts.account_config_dir(_accounts.get_account(name, data_dir, PROVIDER), data_dir)


def _rem_config(data_dir: Path) -> dict[str, Any]:
    """Settings > Rem, read without opening Rem's store for writing."""
    import sqlite3
    from contextlib import closing

    store = data_dir / "rem-home.sqlite3"
    if not store.is_file():
        return {}
    try:
        with closing(
            sqlite3.connect(f"{store.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        ) as db:
            row = db.execute("SELECT body FROM settings WHERE key='config'").fetchone()
        value = json.loads(row[0]) if row else {}
    except (sqlite3.Error, OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def rem_codex_home(data_dir: Path, config: dict[str, Any] | None = None) -> Path | None:
    """The folder of the Codex account Settings > Rem names, or None.

    Only a named account counts: the default sign-in and "choose by usage" have no
    single folder, so callers fall back to Raticode's own CODEX_HOME.
    """
    from gofer.ui.rem_provider_accounts import configured_account

    wanted = configured_account(_rem_config(data_dir) if config is None else config, PROVIDER)
    if not wanted or wanted == _accounts.AUTO:
        return None
    try:
        return account_home(wanted, data_dir)
    except (_accounts.AccountError, OSError):
        return None
