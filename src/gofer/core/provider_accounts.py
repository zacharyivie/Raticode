"""Provider accounts: one official config folder per account, for any CLI provider.

Each account is a provider profile with an ``account_id``. Its folder,
``<data>/<provider>-accounts/<account_id>``, is the folder the provider's CLI
keeps its sign-in in (Claude Code's ``CLAUDE_CONFIG_DIR``, for example), so the
CLI keeps and refreshes that account's sign-in itself.

A small adapter per provider (``AccountAdapter``) says how: the environment for
a folder, the sign-in, sign-out and status commands, how to read the status
(signed in, identity and plan) and the usage, and which folders to hide from
agent tools. The built-in adapters are Claude Code (``claude_accounts.py``), Codex
(``codex_accounts.py``) and Grok (``grok_accounts.py``).

Raticode never reads, copies, stores or sends a token or credential file of
these accounts. It only starts the unmodified CLI with the folder set. Folders
are named by a random ID, so renaming an account never moves its folder (on
macOS that would sign it out), and nothing is ever an implicit "current
account": every run names the account it uses.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from gofer.utils.paths import get_data_dir
from gofer.utils.process import CREDENTIAL_ENV_VARS, UNSET_ENV, without_unset_env

if TYPE_CHECKING:
    from gofer.core.provider_profiles import ProviderProfile

ACCOUNT_ID_PATTERN = re.compile(r"[0-9a-f]{16}")
# Every provider's account folders end with this, so each <provider>-accounts folder
# is hidden from agent tools even before its adapter loads.
ACCOUNTS_FOLDER_SUFFIX = "-accounts"
STATUS_TTL_SECONDS = 120
STATUS_TIMEOUT_SECONDS = 20
SIGN_IN_TIMEOUT_SECONDS = 600
# Codex's one-time code expires after 15 minutes; a device sign-in waits that long
# unless its adapter says otherwise (device_sign_in_seconds).
DEVICE_SIGN_IN_TIMEOUT_SECONDS = 15 * 60
# Usage readings older than this count as unknown when choosing an account.
READING_FRESH_SECONDS = 30 * 60
# A sign-in's private scratch folder, inside the provider's accounts folder.
SIGN_IN_SCRATCH = ".sign-in"
NEEDS_SIGN_IN = "needs_sign_in"
SIGNED_IN = "signed_in"
UNAVAILABLE = "unavailable"
# Rem's per-provider setting: "" for the default sign-in, AUTO to choose by usage,
# or one account's name.
AUTO = "auto"


class AccountError(ValueError):
    """An account request that can't be done, with a message for the user."""


UsageReader = Callable[[str, dict[str, str], Path | None], Awaitable[list[dict[str, Any]] | None]]


@dataclass(frozen=True)
class AccountAdapter:
    """How one CLI provider keeps an account in its own folder.

    ``read_status`` turns the status command's output into ``state`` (SIGNED_IN or
    NEEDS_SIGN_IN), ``plan``, ``email`` (shown, never stored) and ``identity`` (a
    hash used only to warn when two folders hold the same sign-in).
    ``read_usage`` asks the CLI for the account's limits and returns usage windows,
    or None when it couldn't tell.
    """

    provider: str
    label: str
    # The CLI's command name, for messages.
    cli_name: str
    # The environment variable that names the folder the CLI keeps its sign-in in.
    folder_env: str
    sign_in_args: tuple[str, ...]
    status_args: tuple[str, ...]
    read_status: Callable[[str], dict[str, Any]]
    sign_out_args: tuple[str, ...] | None = None
    # A sign-in that prints a code to enter on another device, for a computer without a
    # browser (`codex login --device-auth`, `grok login --device-auth`). The terminal
    # sign-in offers it, and the phone too when phone_sign_in is set.
    device_sign_in_args: tuple[str, ...] | None = None
    read_usage: UsageReader | None = None
    usage_source: str = ""
    # Environment that would make the CLI use something other than the folder's sign-in.
    other_auth_env: tuple[str, ...] = ()
    # Shared setup a new account may start with. Copied, never linked, and never
    # anything that holds a sign-in, history or another account's settings.
    setup_allowlist: tuple[str, ...] = ()
    setup_description: str = ""
    setup_source: Callable[[], Path] | None = None
    # Folder under the data folder; defaults to "<provider>-accounts".
    folder_name: str = ""
    # What the identity names, for the shared sign-in warning ("Claude account").
    identity_noun: str = ""
    executable: Callable[[], str | None] | None = None
    # Sign-in opens a browser: a shim records its page so the UI can link it.
    browser_shim: bool = True
    # The account's sign-in state; defaults to check_status. Claude Code's adapter
    # looks up claude_accounts.check_status when called, so a stand-in replaces it.
    status_check: Callable[..., dict[str, Any]] | None = None
    # Reads the status itself instead of running status_args, for a CLI whose status
    # needs a conversation (Codex's app-server account/read). Gets the executable, the
    # folder's environment and the folder; returns what read_status would.
    status_reader: Callable[[str, dict[str, str], Path], dict[str, Any]] | None = None
    # Copies the shared setup into a new account's folder, for a provider whose setup
    # needs more than copying (Codex's config.toml without its sign-in settings).
    setup_copier: Callable[[Path, Path], list[str]] | None = None
    # Which allowlisted items differ from the source, and re-copying only those, for a
    # provider whose setup needs more than comparing files (Codex's cleaned config.toml).
    setup_comparer: Callable[[Path, Path], list[str]] | None = None
    setup_recopier: Callable[[Path, Path, list[str]], list[str]] | None = None
    # Choose by usage finds the 5-hour and weekly windows by their length, not by
    # Claude Code's window IDs (Codex names its windows primary and secondary).
    headroom_by_kind: bool = False
    # A failed sign-in shows the CLI's own last message (Codex prints why OpenAI
    # refused it), and the sign-in page comes from its output when no browser opened.
    sign_in_output: bool = False
    # The device sign-in prints its link and one-time code on lines of their own, as
    # ``codex login --device-auth`` and ``grok login --device-auth`` do, so a paired
    # phone can finish it
    # (SignIns.start(device=True), the phone's provider.account.sign_in).
    phone_sign_in: bool = False
    # Where to turn the device sign-in on, shown when the provider refuses it.
    device_sign_in_help: str = ""
    # How long the device sign-in's one-time code lasts, for the phone's countdown.
    device_sign_in_seconds: int = DEVICE_SIGN_IN_TIMEOUT_SECONDS

    @property
    def folder(self) -> str:
        return self.folder_name or f"{self.provider.replace('_', '-')}{ACCOUNTS_FOLDER_SUFFIX}"

    @property
    def noun(self) -> str:
        return self.identity_noun or f"{self.label} account"

    def find_executable(self) -> str | None:
        if self.executable is not None:
            return self.executable()
        from gofer.core.provider_capabilities import (
            PROVIDER_BINARIES,
            ProviderId,
            resolve_provider_executable,
        )

        if self.provider not in PROVIDER_BINARIES:
            return None
        return resolve_provider_executable(cast(ProviderId, self.provider))

    def environment(self, folder: Path, base: dict[str, str] | None = None) -> dict[str, str]:
        """A full child environment that uses only this folder's sign-in.

        It also names the other sign-in variables in UNSET_ENV, so a spawn that adds
        this to the parent's environment (build_subprocess_env) still drops them.
        """
        env = dict(os.environ if base is None else base)
        for name in self.other_auth_env:
            env.pop(name, None)
        # Raticode's own API tokens and the Jev key are never a provider's business,
        # including during a sign-in started from the user's terminal.
        for name in CREDENTIAL_ENV_VARS:
            env.pop(name, None)
        env[self.folder_env] = str(folder)
        if self.other_auth_env:
            env[UNSET_ENV] = " ".join(self.other_auth_env)
        return env

    def folder_overrides(self, folder: Path) -> dict[str, str]:
        """Only what a turn adds to its environment: the folder, and what to drop."""
        overrides = {self.folder_env: str(folder)}
        if self.other_auth_env:
            overrides[UNSET_ENV] = " ".join(self.other_auth_env)
        return overrides

    def hidden_folders(self, data_dir: Path | None = None) -> list[Path]:
        """Folders agent tools must never read or edit: every account of this provider."""
        return [accounts_root(self.provider, data_dir)]

    def missing_cli(self) -> str:
        return f"The {self.cli_name} CLI isn't installed or isn't on PATH."


# --- registry -------------------------------------------------------------------------

_adapters: dict[str, AccountAdapter] = {}
_builtins_loaded = False
_registry_lock = threading.Lock()


def _load_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    with _registry_lock:
        if _builtins_loaded:
            return
        _builtins_loaded = True
    # Each built-in adapter registers itself when its module loads.
    import gofer.core.claude_accounts  # noqa: F401
    import gofer.core.codex_accounts  # noqa: F401
    import gofer.core.grok_accounts  # noqa: F401


def register_adapter(adapter: AccountAdapter) -> AccountAdapter:
    """Add or replace a provider's adapter. Returns it, for use as a decorator-like call."""
    _adapters[adapter.provider] = adapter
    return adapter


def unregister_adapter(provider: str) -> None:
    _adapters.pop(provider, None)


# Built-in adapters list in this order wherever providers are shown; others follow by ID.
BUILTIN_ORDER = ("claude_code", "codex", "grok")


def adapters() -> dict[str, AccountAdapter]:
    _load_builtins()
    return dict(
        sorted(
            _adapters.items(),
            key=lambda item: (
                BUILTIN_ORDER.index(item[0]) if item[0] in BUILTIN_ORDER else len(BUILTIN_ORDER),
                item[0],
            ),
        )
    )


def account_adapter(provider: str | None) -> AccountAdapter | None:
    if not provider:
        return None
    _load_builtins()
    return _adapters.get(provider)


def require_adapter(provider: Any) -> AccountAdapter:
    adapter = account_adapter(provider) if isinstance(provider, str) else None
    if adapter is None:
        raise AccountError(f"Accounts aren't available for '{provider}'")
    return adapter


def providers_view() -> list[dict[str, Any]]:
    """The providers that support accounts, for settings screens."""
    return [
        {
            "id": adapter.provider,
            "label": adapter.label,
            "cli": adapter.cli_name,
            "setup": adapter.setup_description,
            "can_copy_setup": bool(adapter.setup_allowlist),
            # Where "Copy my setup" reads from unless another folder is chosen.
            "setup_folder": str(adapter.setup_source()) if adapter.setup_source else "",
            # The CLI's terminal sign-in can also use a code from another device.
            "device_sign_in": adapter.device_sign_in_args is not None,
            # A paired phone can sign its accounts in with a link and a one-time code.
            "phone_sign_in": adapter.phone_sign_in and bool(adapter.device_sign_in_args),
        }
        for adapter in adapters().values()
    ]


# --- folders --------------------------------------------------------------------------


def accounts_root(provider: str, data_dir: Path | None = None) -> Path:
    return (data_dir or get_data_dir()) / require_adapter(provider).folder


def account_folder_names() -> list[str]:
    return [adapter.folder for adapter in adapters().values()]


def hidden_account_folders(data_dir: Path | None = None) -> list[Path]:
    """Every provider's account folders, plus any other <provider>-accounts folder found."""
    root = data_dir or get_data_dir()
    folders: list[Path] = []
    for adapter in adapters().values():
        for folder in adapter.hidden_folders(root):
            if folder not in folders:
                folders.append(folder)
    try:
        entries = sorted(root.iterdir()) if root.is_dir() else []
    except OSError:
        entries = []
    for entry in entries:
        if entry.name.endswith(ACCOUNTS_FOLDER_SUFFIX) and entry not in folders:
            folders.append(entry)
    return folders


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def is_account(profile: ProviderProfile | None, provider: str | None = None) -> bool:
    if not profile or not profile.account_id:
        return False
    if provider is not None and profile.subscription != provider:
        return False
    return account_adapter(profile.subscription) is not None


def account_dir(provider: str, account_id: str, data_dir: Path | None = None) -> Path:
    adapter = require_adapter(provider)
    if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise AccountError(f"Invalid {adapter.label} account ID")
    return accounts_root(provider, data_dir) / account_id


def list_accounts(
    data_dir: Path | None = None, provider: str | None = None
) -> dict[str, ProviderProfile]:
    from gofer.core.provider_profiles import load_provider_profiles

    return {
        name: profile
        for name, profile in load_provider_profiles(data_dir).items()
        if is_account(profile, provider)
    }


def get_account(
    name: str, data_dir: Path | None = None, provider: str | None = None
) -> ProviderProfile:
    profile = list_accounts(data_dir, provider).get(name)
    if profile is None:
        label = require_adapter(provider).label + " " if provider else ""
        raise AccountError(f"No {label}account named '{name}'")
    return profile


def account_config_dir(profile: ProviderProfile, data_dir: Path | None = None) -> Path:
    """The account's folder, made owner-only. Raises if the profile isn't an account."""
    if not is_account(profile):
        raise AccountError(f"Provider profile '{profile.name}' isn't an account")
    _private_dir(accounts_root(profile.subscription, data_dir))
    return _private_dir(account_dir(profile.subscription, str(profile.account_id), data_dir))


def account_environment(
    profile: ProviderProfile, data_dir: Path | None = None, base: dict[str, str] | None = None
) -> dict[str, str]:
    folder = account_config_dir(profile, data_dir)
    return require_adapter(profile.subscription).environment(folder, base)


def copy_setup(source: Path, target: Path, allowlist: Iterable[str]) -> list[str]:
    """Copy the allowlisted setup items; symlinks are skipped, nothing else is read."""
    copied: list[str] = []
    for name in allowlist:
        item = source / name
        if item.is_symlink() or not item.exists():
            continue
        destination = target / name
        if destination.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, destination, symlinks=False, ignore=_skip_links)
        elif item.is_file():
            shutil.copyfile(item, destination)
        else:
            continue
        copied.append(name)
    return copied


def _skip_links(folder: str, names: list[str]) -> list[str]:
    return [name for name in names if os.path.islink(os.path.join(folder, name))]


def _setup_files(source: Path, name: str) -> list[Path]:
    """The regular files of one setup item, relative to ``source``. Symlinks are skipped."""
    item = source / name
    if item.is_symlink():
        return []
    if item.is_file():
        return [Path(name)]
    if not item.is_dir():
        return []
    files: list[Path] = []
    for folder, folders, names in os.walk(item):
        base = Path(folder)
        folders[:] = sorted(each for each in folders if not (base / each).is_symlink())
        for each in sorted(names):
            path = base / each
            if not path.is_symlink() and path.is_file():
                files.append(path.relative_to(source))
    return files


def _through_link(target: Path, relative: Path) -> bool:
    """Whether a symlink sits anywhere on the way to target/relative. Never written through."""
    path = target
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            return True
    return False


def _same_file(first: Path, second: Path) -> bool:
    try:
        if not second.is_file() or first.stat().st_size != second.stat().st_size:
            return False
        with first.open("rb") as one, second.open("rb") as other:
            while True:
                chunk = one.read(65536)
                if chunk != other.read(65536):
                    return False
                if not chunk:
                    return True
    except OSError:
        return False


def _stale_files(source: Path, target: Path, name: str) -> list[Path]:
    """Files of the item that the account lacks or has with other contents.

    Files only the account has don't count, and nothing behind a symlink in the
    account folder does.
    """
    return [
        relative
        for relative in _setup_files(source, name)
        if not _through_link(target, relative)
        and not _same_file(source / relative, target / relative)
    ]


def setup_differences(source: Path, target: Path, allowlist: Iterable[str]) -> list[str]:
    """The allowlisted items whose files in ``source`` the account folder lacks or has changed."""
    return [name for name in allowlist if _stale_files(source, target, name)]


def _replace_file(source: Path, target: Path, relative: Path) -> None:
    folder = target
    for part in relative.parts[:-1]:
        folder = folder / part
        if folder.exists() and not folder.is_dir():
            folder.unlink()
        folder.mkdir(exist_ok=True)
    destination = target / relative
    if destination.is_dir():
        shutil.rmtree(destination)
    shutil.copy2(source / relative, destination)


def recopy_setup_items(source: Path, target: Path, names: Iterable[str]) -> list[str]:
    """Copy the named items' changed files over the account's; returns the items copied.

    Unchanged files and files only the account has are left as they are.
    """
    copied: list[str] = []
    for name in names:
        stale = _stale_files(source, target, name)
        for relative in stale:
            _replace_file(source, target, relative)
        if stale:
            copied.append(name)
    return copied


def create_account(
    name: str,
    *,
    provider: str,
    data_dir: Path | None = None,
    copy_shared_setup: bool = False,
    setup_source: Path | None = None,
) -> ProviderProfile:
    from gofer.core.provider_profiles import (
        ProviderProfile,
        load_provider_profiles,
        save_provider_profiles,
        validate_provider_profile_name,
    )

    adapter = require_adapter(provider)
    name = name.strip()
    try:
        validate_provider_profile_name(name)
    except ValueError as exc:
        raise AccountError(
            "Account names use letters, numbers, dots, dashes and underscores, such as 'work'"
        ) from exc
    profiles = load_provider_profiles(data_dir)
    if name in profiles:
        raise AccountError(f"A provider profile named '{name}' already exists")
    if setup_source is not None:
        setup_source = setup_folder(setup_source)
    profile = ProviderProfile.model_validate(
        {"name": name, "subscription": provider, "account_id": secrets.token_hex(8)}
    )
    folder = account_config_dir(profile, data_dir)
    if copy_shared_setup:
        _copy_shared_setup(adapter, folder, setup_source)
    profiles[name] = profile
    save_provider_profiles(profiles, data_dir)
    forget_status(profile)
    return profile


def setup_folder(value: Any) -> Path:
    """A folder to copy shared setup from: a full path to an existing folder."""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise AccountError("Choose the folder to copy setup from")
    path = Path(str(value).strip()).expanduser()
    if not path.is_absolute():
        raise AccountError("Give the setup folder's full path")
    if not path.is_dir():
        raise AccountError(f"There's no folder at {path}")
    return path


def _copy_shared_setup(adapter: AccountAdapter, folder: Path, source: Path | None) -> list[str]:
    """Copy the provider's shared setup into ``folder``; returns what was copied.

    Only the adapter's allowlist is copied, symlinks are skipped, and nothing the
    account folder already has is replaced.
    """
    if not adapter.setup_allowlist:
        return []
    source = source or (adapter.setup_source() if adapter.setup_source else None)
    if source is None or source.resolve() == folder.resolve():
        return []
    if adapter.setup_copier is not None:
        return adapter.setup_copier(source, folder)
    return copy_setup(source, folder, adapter.setup_allowlist)


def import_setup(
    name: str, *, data_dir: Path | None = None, setup_source: Path | str | None = None
) -> list[str]:
    """Copy shared setup into an existing account; items it already has are kept."""
    profile = get_account(name, data_dir)
    adapter = require_adapter(profile.subscription)
    if not adapter.setup_allowlist:
        raise AccountError(f"{adapter.label} accounts have no shared setup to copy")
    source = setup_folder(setup_source) if setup_source not in (None, "") else None
    return _copy_shared_setup(adapter, account_config_dir(profile, data_dir), source)


def _compare_setup(adapter: AccountAdapter, source: Path, folder: Path) -> list[str]:
    """Changed items the account already has. Ones it never got are Import setup's job."""
    if adapter.setup_comparer is not None:
        changed = adapter.setup_comparer(source, folder)
    else:
        changed = setup_differences(source, folder, adapter.setup_allowlist)
    return [name for name in changed if os.path.lexists(folder / name)]


def _shared_source(adapter: AccountAdapter, folder: Path, source: Path | None) -> Path | None:
    """The folder to compare with: ``source`` or the provider's own, never the account itself."""
    if not adapter.setup_allowlist:
        return None
    source = source or (adapter.setup_source() if adapter.setup_source else None)
    if source is None or not source.is_dir() or source.resolve() == folder.resolve():
        return None
    return source


def setup_changes(
    profile: ProviderProfile,
    data_dir: Path | None = None,
    setup_source: Path | None = None,
) -> list[str]:
    """Allowlisted setup items the account has that differ from its source folder.

    An item differs when the source has a file the account lacks or holds with other
    contents (for Codex's config.toml: a setting, after removing sign-in settings, the
    account lacks or sets otherwise). Only reads; [] when there's nothing to compare.
    """
    adapter = require_adapter(profile.subscription)
    folder = account_dir(profile.subscription, str(profile.account_id), data_dir)
    if not folder.is_dir():
        return []
    source = _shared_source(adapter, folder, setup_source)
    if source is None:
        return []
    try:
        return _compare_setup(adapter, source, folder)
    except (OSError, ValueError):
        return []


def recopy_setup(
    name: str, *, data_dir: Path | None = None, setup_source: Path | str | None = None
) -> list[str]:
    """Copy again only the setup items that differ from the source; returns them.

    Sign-in files, sessions and history aren't in any allowlist, so they're never read
    or written. Files and settings only the account has are kept.
    """
    profile = get_account(name, data_dir)
    adapter = require_adapter(profile.subscription)
    if not adapter.setup_allowlist:
        raise AccountError(f"{adapter.label} accounts have no shared setup to copy")
    chosen = setup_folder(setup_source) if setup_source not in (None, "") else None
    folder = account_config_dir(profile, data_dir)
    source = _shared_source(adapter, folder, chosen)
    if source is None:
        return []
    changed = _compare_setup(adapter, source, folder)
    if not changed:
        return []
    if adapter.setup_recopier is not None:
        return adapter.setup_recopier(source, folder, changed)
    return recopy_setup_items(source, folder, changed)


def rename_account(old: str, new: str, *, data_dir: Path | None = None) -> ProviderProfile:
    """Rename the account; its folder keeps its ID, so the sign-in stays."""
    from gofer.core.provider_profiles import (
        load_provider_profiles,
        save_provider_profiles,
        validate_provider_profile_name,
    )

    profile = get_account(old, data_dir)
    try:
        validate_provider_profile_name(new)
    except ValueError as exc:
        raise AccountError(
            "Account names use letters, numbers, dots, dashes and underscores"
        ) from exc
    profiles = load_provider_profiles(data_dir)
    if new in profiles:
        raise AccountError(f"A provider profile named '{new}' already exists")
    profiles.pop(old)
    profiles[new] = profile.model_copy(update={"name": new})
    save_provider_profiles(profiles, data_dir)
    return profiles[new]


def remove_account(
    name: str,
    *,
    data_dir: Path | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> None:
    """Sign the account out with its CLI, then delete its folder and profile.

    Signing out first lets the CLI remove an OS keychain entry (Claude Code's
    macOS Keychain, for example), which deleting the folder alone would leave behind.
    """
    from gofer.core.provider_profiles import load_provider_profiles, save_provider_profiles

    profile = get_account(name, data_dir)
    adapter = require_adapter(profile.subscription)
    folder = account_dir(profile.subscription, str(profile.account_id), data_dir)
    executable = adapter.find_executable()
    if executable and adapter.sign_out_args and folder.is_dir():
        try:
            run(
                [executable, *adapter.sign_out_args],
                env=without_unset_env(adapter.environment(folder)),
                cwd=str(folder),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=STATUS_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    sign_ins.cancel(name)
    profiles = load_provider_profiles(data_dir)
    profiles.pop(name, None)
    save_provider_profiles(profiles, data_dir)
    if folder.is_dir() and not folder.is_symlink():
        shutil.rmtree(folder)
    forget_status(profile)


# --- status ---------------------------------------------------------------------------

_status_lock = threading.Lock()
_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _status_key(profile: ProviderProfile) -> str:
    return f"{profile.subscription}:{profile.account_id}"


def forget_status(profile: ProviderProfile) -> None:
    with _status_lock:
        _status_cache.pop(_status_key(profile), None)


def check_status(
    profile: ProviderProfile,
    *,
    data_dir: Path | None = None,
    refresh: bool = False,
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """The account's sign-in state from its CLI's own status command."""
    adapter = require_adapter(profile.subscription)
    key = _status_key(profile)
    with _status_lock:
        cached = _status_cache.get(key)
    if cached and not refresh and time.monotonic() - cached[0] < STATUS_TTL_SECONDS:
        return dict(cached[1])
    executable = adapter.find_executable()
    if executable is None:
        status: dict[str, Any] = {"state": UNAVAILABLE, "reason": adapter.missing_cli()}
    else:
        folder = account_config_dir(profile, data_dir)
        env = without_unset_env(adapter.environment(folder))
        try:
            if adapter.status_reader is not None:
                status = adapter.status_reader(executable, env, folder)
            else:
                try:
                    completed = run(
                        [executable, *adapter.status_args],
                        env=env,
                        cwd=str(folder),
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        timeout=STATUS_TIMEOUT_SECONDS,
                        check=False,
                    )
                    output = str(completed.stdout or "")
                except (OSError, subprocess.SubprocessError):
                    output = ""
                status = adapter.read_status(output)
        except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
            status = {
                "state": UNAVAILABLE,
                "reason": f"{adapter.label} gave no sign-in status.",
            }
    status["checked_at"] = datetime.now(UTC).isoformat()
    with _status_lock:
        _status_cache[key] = (time.monotonic(), dict(status))
    return status


def account_status(
    profile: ProviderProfile, *, data_dir: Path | None = None, refresh: bool = False
) -> dict[str, Any]:
    """The account's sign-in state through its adapter (check_status unless it has its own)."""
    adapter = require_adapter(profile.subscription)
    if adapter.status_check is not None:
        return adapter.status_check(profile, data_dir=data_dir, refresh=refresh)
    return check_status(profile, data_dir=data_dir, refresh=refresh)


def recent_status(profile: ProviderProfile) -> dict[str, Any] | None:
    """The account's last sign-in check when it is under STATUS_TTL_SECONDS old, else None."""
    with _status_lock:
        cached = _status_cache.get(_status_key(profile))
    if cached and time.monotonic() - cached[0] < STATUS_TTL_SECONDS:
        return dict(cached[1])
    return None


# How long quick_status waits for a status check before calling the state unknown.
QUICK_STATUS_TIMEOUT_SECONDS = 5.0
_quick_checks: dict[str, tuple[threading.Event, dict[str, Any]]] = {}


def quick_status(
    profile: ProviderProfile,
    *,
    data_dir: Path | None = None,
    timeout: float = QUICK_STATUS_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """The account's sign-in state without waiting long, or None when it isn't known.

    Uses the last check when it is recent. Otherwise runs the adapter's status check and
    waits at most ``timeout`` seconds. A check that takes longer keeps running in the
    background, so its answer is the recent check the next call reuses, and a second
    call meanwhile waits on the same check instead of starting another.
    """
    recent = recent_status(profile)
    if recent is not None:
        return recent
    key = _status_key(profile)
    with _status_lock:
        running = _quick_checks.get(key)
        if running is None:
            running = (threading.Event(), {})
            _quick_checks[key] = running
            start = True
        else:
            start = False
    done, result = running
    if start:

        def check() -> None:
            try:
                result.update(account_status(profile, data_dir=data_dir))
            except (AccountError, OSError, ValueError, subprocess.SubprocessError):
                pass
            finally:
                with _status_lock:
                    _quick_checks.pop(key, None)
                done.set()

        threading.Thread(target=check, name="account-status", daemon=True).start()
    if not done.wait(max(timeout, 0.0)) or not result.get("state"):
        return None
    return dict(result)


def account_view(
    profile: ProviderProfile,
    status: dict[str, Any],
    data_dir: Path | None = None,
    *,
    setup: bool = True,
) -> dict[str, Any]:
    from gofer.core.usage_ledger import profile_usage_identity

    adapter = require_adapter(profile.subscription)
    view: dict[str, Any] = {}
    if setup and adapter.setup_allowlist:
        # Items of the shared setup that changed since they were copied (or never were).
        view["setup_changes"] = setup_changes(profile, data_dir)
    return view | {
        "name": profile.name,
        "provider": profile.subscription,
        "label": f"{adapter.label} · {profile.name}",
        "account_id": profile.account_id,
        "usage_id": profile_usage_identity(profile.name, data_dir),
        "model": profile.model,
        **{key: value for key, value in status.items() if key != "identity"},
        "needs_sign_in": status.get("state") == NEEDS_SIGN_IN,
        "phone_sign_in": adapter.phone_sign_in and bool(adapter.device_sign_in_args),
        "sign_in": sign_ins.state(profile.name, data_dir=data_dir, check=account_status),
        "last_reading": last_reading(profile, data_dir),
    }


def last_reading(profile: ProviderProfile, data_dir: Path | None = None) -> dict[str, Any] | None:
    """The account's newest weekly and 5-hour percentages used, however old, or None.

    Pickers show it next to the account with its age; choose_account only trusts
    readings from the last READING_FRESH_SECONDS (account_headroom).
    """
    from gofer.core.usage_ledger import profile_usage_identity
    from gofer.core.usage_service import latest_window_reading

    try:
        identity = profile_usage_identity(profile.name, data_dir)
        windows = {
            window: latest_window_reading(profile.subscription, window, data_dir, profile=identity)
            for window in ("seven_day", "five_hour")
        }
    except (OSError, ValueError, sqlite3.Error):
        return None
    found = {window: reading for window, reading in windows.items() if reading is not None}
    if not found:
        return None
    return {
        **{window: (found[window]["percent"] if window in found else None) for window in windows},
        "observed_at": max(str(reading.get("observed_at") or "") for reading in found.values())
        or None,
    }


def accounts_overview(
    data_dir: Path | None = None,
    *,
    refresh: bool = False,
    check: Callable[..., dict[str, Any]] | None = None,
    provider: str | None = None,
    setup: bool = True,
) -> dict[str, Any]:
    """Every account with its sign-in state, plus a warning per shared sign-in.

    ``provider`` limits the list to one provider; otherwise every provider's
    accounts are listed. ``providers`` names the providers that support accounts.
    """
    if provider is not None:
        require_adapter(provider)
    check = check or account_status
    accounts = list_accounts(data_dir, provider)
    statuses = {
        name: check(profile, data_dir=data_dir, refresh=refresh)
        for name, profile in accounts.items()
    }
    views = [
        account_view(profile, statuses[name], data_dir, setup=setup)
        for name, profile in accounts.items()
    ]
    warnings: list[dict[str, Any]] = []
    for each in sorted({profile.subscription for profile in accounts.values()}):
        warnings += duplicate_warnings(
            {name: s for name, s in statuses.items() if accounts[name].subscription == each},
            provider=each,
        )
    for view in views:
        for warning in warnings:
            if view["name"] in warning["accounts"]:
                view["duplicate_of"] = [n for n in warning["accounts"] if n != view["name"]]
    return {"accounts": views, "warnings": warnings, "providers": providers_view()}


def duplicate_warnings(
    statuses: dict[str, dict[str, Any]], *, provider: str = "claude_code"
) -> list[dict[str, Any]]:
    adapter = account_adapter(provider)
    noun = adapter.noun if adapter else "account"
    groups: dict[str, list[str]] = {}
    for name, status in statuses.items():
        if status.get("state") == SIGNED_IN and status.get("identity"):
            groups.setdefault(str(status["identity"]), []).append(name)
    return [
        {
            "provider": provider,
            "accounts": sorted(names),
            "message": (
                f"{', '.join(sorted(names))} are signed in to the same {noun}. "
                "Sign one of them out and in again with a different account, or remove it."
            ),
        }
        for names in groups.values()
        if len(names) > 1
    ]


# --- one-click sign-in ----------------------------------------------------------------


@dataclass
class _SignIn:
    process: subprocess.Popen[bytes]
    folder: Path
    scratch: Path
    label: str
    started: float = field(default_factory=time.monotonic)
    state: str = "waiting"
    reason: str | None = None
    # The CLI's own output, kept only for an adapter with sign_in_output.
    output: Path | None = None
    # A device sign-in: the CLI prints a link and a one-time code instead of opening
    # a browser, so it can be finished on another device such as a paired phone.
    device: bool = False
    help: str = ""
    timeout: float = SIGN_IN_TIMEOUT_SECONDS
    # The provider refused a device sign-in because it is turned off for the account.
    refused: bool = False


# Longest CLI message a failed sign-in shows.
SIGN_IN_MESSAGE_CHARS = 500
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_URL_LINE = re.compile(r"^https://\S+$")
# A one-time code on a line of its own, such as Codex's "ABCD-EFGHI". Grok accepts
# any letters, digits and hyphens from xAI, so the parts aren't limited in length.
_CODE_LINE = re.compile(r"^(?=.{4,64}$)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
# Codex says "device code login is not enabled ..." when it is off for the account
# or server; Grok says "Device-code login is not available for this deployment".
_DEVICE_REFUSED = re.compile(
    r"device[ -](?:code|auth)\b.*\b(?:not enabled|not available|disabled|turned off)",
    re.IGNORECASE,
)
# The code ran out before it was entered: Grok's "Device code expired. Run `grok
# login --device-auth` again." or Codex's "device auth timed out after 15 minutes".
_DEVICE_EXPIRED = re.compile(r"device[ -](?:code|auth) (?:expired|timed out)", re.IGNORECASE)
# How a Rust CLI's main() prints the error it stopped with (Grok's "Error: ...").
_ERROR_PREFIX = "Error: "


def _read_output(path: Path | None) -> list[str]:
    """The CLI's output lines, without color codes, from the last 64 KB."""
    if path is None:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 65536))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in _ANSI.sub("", text).splitlines() if line.strip()]


def sign_in_message(lines: list[str]) -> str | None:
    """The CLI's own reason for a failed sign-in: its last line that isn't an address,
    without the "Error: " a Rust CLI such as Grok puts before it.
    """
    for line in reversed(lines):
        if _URL_LINE.match(line):
            continue
        if line.startswith(_ERROR_PREFIX) and len(line) > len(_ERROR_PREFIX):
            line = line[len(_ERROR_PREFIX) :]
        return line[:SIGN_IN_MESSAGE_CHARS]
    return None


def sign_in_page(lines: list[str]) -> str | None:
    """The first sign-in address the CLI printed, for when no browser opened."""
    for line in lines:
        if _URL_LINE.match(line) and len(line) < 4096:
            return line
    return None


def device_code(lines: list[str]) -> str | None:
    """The one-time code a device sign-in printed: the first code-shaped line after
    a line that mentions a code, so a version number or a heading is never taken.
    """
    asked = False
    for line in lines:
        if asked and _CODE_LINE.match(line):
            return line
        if "code" in line.lower() and not _URL_LINE.match(line):
            asked = True
    return None


def device_sign_in_refused(message: str | None) -> bool:
    """Whether the CLI's message says device code sign-in is turned off."""
    return bool(message and _DEVICE_REFUSED.search(message))


def device_sign_in_expired(message: str | None) -> bool:
    """Whether the CLI's message says the one-time code ran out."""
    return bool(message and _DEVICE_EXPIRED.search(message))


_BROWSER_SHIM = """#!/bin/sh
# Records the sign-in page so Raticode can offer it as a link, then opens it.
printf '%s\\n' "$1" > {url_file}
exec {opener} "$1"
"""
# A device sign-in finishes on another device, so the computer's browser stays shut.
# Grok's device sign-in opens the link itself; through $BROWSER it reaches only this,
# and Grok still prints the link and code.
_NO_BROWSER_SHIM = """#!/bin/sh
exit 0
"""


class SignIns:
    """Runs a provider CLI's own sign-in for an account, in the background.

    The CLI opens the browser and receives the approval on its own temporary
    callback; the user only approves in the browser. Raticode watches the process
    and the status command to see when it is done. Nothing is typed into the
    login and its output is discarded, apart from the sign-in page address.

    A device sign-in (``device=True``, for a paired phone) runs the adapter's
    ``device_sign_in_args`` instead. No browser opens on the computer: the CLI prints
    a link and a one-time code, which ``state`` returns so the phone can open the link
    and show the code. The CLI exits 0 once the code was approved.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, _SignIn] = {}

    def start(
        self,
        name: str,
        *,
        data_dir: Path | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        device: bool = False,
    ) -> dict[str, Any]:
        profile = get_account(name, data_dir)
        adapter = require_adapter(profile.subscription)
        args = adapter.sign_in_args
        if device:
            if not adapter.phone_sign_in or not adapter.device_sign_in_args:
                raise AccountError(
                    f"{adapter.label} accounts can only be signed in on your computer"
                )
            args = adapter.device_sign_in_args
        executable = adapter.find_executable()
        if executable is None:
            raise AccountError(adapter.missing_cli().rstrip("."))
        self.cancel(name)
        folder = account_config_dir(profile, data_dir)
        scratch = Path(
            tempfile.mkdtemp(
                prefix="sign-in-",
                dir=_private_dir(accounts_root(profile.subscription, data_dir) / SIGN_IN_SCRATCH),
            )
        )
        env = without_unset_env(adapter.environment(folder))
        if os.name == "posix" and adapter.browser_shim and not device:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            shim = scratch / "browser"
            shim.write_text(
                _BROWSER_SHIM.replace("{opener}", opener).replace(
                    "{url_file}", shlex.quote(str(scratch / "sign-in-url"))
                )
            )
            shim.chmod(0o700)
            env["BROWSER"] = str(shim)
        elif os.name == "posix" and device:
            shim = scratch / "no-browser"
            shim.write_text(_NO_BROWSER_SHIM)
            shim.chmod(0o700)
            env["BROWSER"] = str(shim)
        # A device sign-in's link and code are only in the CLI's output.
        output = scratch / "output" if adapter.sign_in_output or device else None
        sink = output.open("wb") if output is not None else None
        try:
            process = popen(
                [executable, *args],
                env=env,
                cwd=str(folder),
                stdin=subprocess.DEVNULL,
                stdout=sink if sink is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if sink is not None else subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            )
        finally:
            if sink is not None:
                sink.close()
        forget_status(profile)
        with self._lock:
            # Two starts at once (the phone and the window) both got here: stop the one
            # this replaces, or its CLI keeps running with nothing to stop it.
            replaced = self._active.pop(name, None)
            self._active[name] = _SignIn(
                process=process,
                folder=folder,
                scratch=scratch,
                label=adapter.label,
                output=output,
                device=device,
                help=adapter.device_sign_in_help if device else "",
                timeout=adapter.device_sign_in_seconds if device else SIGN_IN_TIMEOUT_SECONDS,
            )
        if replaced is not None:
            self._stop(replaced)
        return self.state(name) or {}

    def state(
        self,
        name: str,
        *,
        data_dir: Path | None = None,
        check: Callable[..., dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            sign_in = self._active.get(name)
        if sign_in is None:
            return None
        if sign_in.state == "waiting":
            code = sign_in.process.poll()
            if time.monotonic() - sign_in.started > sign_in.timeout:
                self._stop(sign_in)
                sign_in.state, sign_in.reason = "timed_out", "The sign-in wasn't finished in time."
            elif code is not None:
                signed = False
                if check is not None:
                    try:
                        profile = get_account(name, data_dir)
                        signed = (
                            check(profile, data_dir=data_dir, refresh=True)["state"] == SIGNED_IN
                        )
                    except AccountError:
                        signed = False
                # A device sign-in is done when the CLI says so by exiting 0.
                if check is None or (sign_in.device and code == 0):
                    signed = signed or code == 0
                sign_in.state = "signed_in" if signed else "failed"
                if not signed:
                    # The CLI's own words, such as OpenAI refusing another paid
                    # account on this device, say more than a generic retry. Only
                    # when it failed: a success line would contradict the status.
                    message = sign_in_message(_read_output(sign_in.output)) if code else None
                    sign_in.refused = sign_in.device and device_sign_in_refused(message)
                    if sign_in.device and device_sign_in_expired(message):
                        # Same as Raticode's own deadline: the code ran out, not a refusal.
                        sign_in.state = "timed_out"
                        message = "The sign-in wasn't finished in time."
                    sign_in.reason = message or (
                        f"{sign_in.label} didn't finish the sign-in. Try again."
                    )
                shutil.rmtree(sign_in.scratch, ignore_errors=True)
        url = None
        one_time_code = None
        url_file = sign_in.scratch / "sign-in-url"
        if sign_in.state == "waiting" and url_file.is_file():
            text = url_file.read_text(errors="replace").strip()
            if text.startswith("https://") and len(text) < 4096:
                url = text
        if sign_in.state == "waiting" and url is None and sign_in.output is not None:
            lines = _read_output(sign_in.output)
            url = sign_in_page(lines)
            if sign_in.device:
                one_time_code = device_code(lines)
        state: dict[str, Any] = {
            "state": sign_in.state,
            "reason": sign_in.reason,
            "url": url,
            "seconds_left": max(0, int(sign_in.timeout - (time.monotonic() - sign_in.started))),
        }
        if sign_in.device:
            state.update(
                mode="device", code=one_time_code, help=sign_in.help, refused=sign_in.refused
            )
        return state

    def _stop(self, sign_in: _SignIn) -> None:
        if sign_in.process.poll() is None:
            sign_in.process.terminate()
            try:
                sign_in.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sign_in.process.kill()
        shutil.rmtree(sign_in.scratch, ignore_errors=True)

    def cancel(self, name: str) -> None:
        with self._lock:
            sign_in = self._active.pop(name, None)
        if sign_in is not None:
            self._stop(sign_in)

    def cancel_all(self) -> None:
        with self._lock:
            names = list(self._active)
        for name in names:
            self.cancel(name)


sign_ins = SignIns()


# --- choosing an account --------------------------------------------------------------


def _reading_age(observed: str | None) -> float:
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(str(observed))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def account_headroom(profile: ProviderProfile, data_dir: Path | None = None) -> dict[str, Any]:
    """How full the account's windows are, from stored readings only.

    ``seven_day`` and ``five_hour`` are the weekly and 5-hour windows; ``credits`` is the
    period's credits for a provider that reports those instead (Grok's ``_x.ai/billing``),
    with ``credits_label`` naming the period, such as "Monthly credits".
    """
    from gofer.core.usage_ledger import profile_usage_identity
    from gofer.core.usage_service import latest_window_reading

    identity = profile_usage_identity(profile.name, data_dir)
    adapter = account_adapter(profile.subscription)
    by_kind = adapter is not None and adapter.headroom_by_kind
    kinds = {"seven_day": "weekly", "five_hour": "5-hour", "credits": None}
    result: dict[str, Any] = {}
    for window in ("seven_day", "five_hour", "credits"):
        # A window the provider reported as null has no reading, so it stays unknown
        # (None) rather than counting as empty.
        reading = latest_window_reading(
            profile.subscription,
            window,
            data_dir,
            profile=identity,
            kind=kinds[window] if by_kind else None,
        )
        fresh = (
            reading is not None and _reading_age(reading["observed_at"]) <= READING_FRESH_SECONDS
        )
        result[window] = reading["percent"] if fresh and reading else None
        if window == "credits":
            result["credits_label"] = str((reading or {}).get("label") or "") if fresh else ""
    return result


def choose_account(
    data_dir: Path | None = None,
    *,
    provider: str,
    names: Iterable[str] | None = None,
    check: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Pick the provider's signed-in account with the most room left, or None.

    Accounts that need sign-in, or whose weekly, 5-hour or credits window is full, are
    skipped. Among the rest, known readings come before unknown ones, then the
    lowest weekly use, then the lowest 5-hour use, then the lowest credit use (Grok
    reports a period's credits instead of the other two), then the name.
    """
    check = check or account_status
    accounts = list_accounts(data_dir, provider)
    wanted = list(names) if names is not None else sorted(accounts)
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    skipped: list[dict[str, str]] = []
    for name in wanted:
        profile = accounts.get(name)
        if profile is None:
            continue
        status = check(profile, data_dir=data_dir)
        if status.get("state") != SIGNED_IN:
            skipped.append({"name": name, "reason": "needs sign-in"})
            continue
        room = account_headroom(profile, data_dir)
        weekly, hourly, credits = room["seven_day"], room["five_hour"], room["credits"]
        if (weekly is not None and weekly >= 100) or (hourly is not None and hourly >= 100):
            skipped.append({"name": name, "reason": "usage limit reached"})
            continue
        if credits is not None and credits >= 100:
            skipped.append({"name": name, "reason": "credits used up"})
            continue
        unknown = weekly is None and hourly is None and credits is None
        ranked.append(
            (
                (
                    unknown,
                    weekly if weekly is not None else 0,
                    hourly if hourly is not None else 0,
                    credits if credits is not None else 0,
                    name,
                ),
                {
                    "name": name,
                    "weekly_percent": weekly,
                    "five_hour_percent": hourly,
                    "credits_percent": credits,
                    "credits_label": room["credits_label"] if credits is not None else "",
                },
            )
        )
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    choice = dict(ranked[0][1])
    choice["skipped"] = skipped
    choice["why"] = _why(choice)
    return choice


def _why(choice: dict[str, Any]) -> str:
    weekly, hourly = choice.get("weekly_percent"), choice.get("five_hour_percent")
    credits = choice.get("credits_percent")
    if weekly is None and hourly is None and credits is None:
        return "No recent usage reading; the other accounts were fuller or need sign-in."
    parts = []
    if weekly is not None:
        parts.append(f"{weekly:.0f}% of its weekly allowance used")
    if hourly is not None:
        parts.append(f"{hourly:.0f}% of its 5-hour allowance used")
    if credits is not None:
        parts.append(f"{credits:.0f}% of its {_credits_name(choice.get('credits_label'))} used")
    return "Most room left: " + ", ".join(parts) + "."


def _credits_name(label: Any) -> str:
    """ "monthly credits" from Grok's "Monthly credits" label; "credits" without a period."""
    text = str(label or "").strip().lower()
    return text if text.endswith("credits") else "credits"


def account_deny_rules(data_dir: Path | None = None) -> list[str]:
    """Read and Edit deny rules for every provider's account folders, for Claude Code's tools.

    Each CLI still reads its own config folder; these only keep the agent's Read
    and Edit tools away from every account's sign-in and history.
    """
    rules: list[str] = []
    for folder in hidden_account_folders(data_dir):
        if not folder.is_dir():
            continue
        path = str(folder.resolve())
        spec = "/" + path if path.startswith("/") else path
        rules += [
            f"{tool}({target})" for target in (spec, f"{spec}/**") for tool in ("Read", "Edit")
        ]
    return rules


ACCOUNT_ACTIONS = (
    "create",
    "sign_in",
    "cancel_sign_in",
    "check",
    "rename",
    "import_setup",
    "copy_setup",
    "remove",
)
# Without a provider, a new account is a Claude Code account, as before.
DEFAULT_PROVIDER = "claude_code"


def account_action(
    body: Any, data_dir: Path | None = None, *, provider: str | None = None
) -> dict[str, Any]:
    """One account change from the desktop, the phone or the API; returns the new list.

    The provider comes from ``provider``, the body's ``provider``, or, for an
    existing account, the account itself. A new account without one is Claude Code.
    """
    if not isinstance(body, dict):
        raise AccountError("Send an object with an action")
    action = body.get("action")
    name = body.get("name")
    if action not in ACCOUNT_ACTIONS:
        raise AccountError("Unknown account action")
    if not isinstance(name, str) or not name.strip():
        raise AccountError("Name the account")
    name = name.strip()
    requested = provider or body.get("provider") or None
    if requested is not None:
        require_adapter(requested)
    if action != "create":
        account = get_account(name, data_dir, requested)
        requested = requested or account.subscription
    started: dict[str, Any] | None = None
    copied: list[str] | None = None
    # A folder to copy shared setup from; empty means the provider's own folder.
    setup_from = body.get("setupFrom") or None
    if action == "create":
        create_account(
            name,
            provider=requested or DEFAULT_PROVIDER,
            data_dir=data_dir,
            copy_shared_setup=body.get("copySetup") is True,
            setup_source=setup_folder(setup_from)
            if setup_from is not None and body.get("copySetup") is True
            else None,
        )
        if body.get("signIn") is True:
            started = sign_ins.start(name, data_dir=data_dir)
    elif action == "sign_in":
        # device: a link and one-time code to finish on another device (a paired phone).
        started = sign_ins.start(name, data_dir=data_dir, device=body.get("device") is True)
    elif action == "cancel_sign_in":
        sign_ins.cancel(name)
    elif action == "check":
        account_status(get_account(name, data_dir), data_dir=data_dir, refresh=True)
    elif action == "rename":
        new = body.get("newName")
        if not isinstance(new, str) or not new.strip():
            raise AccountError("Give the account a new name")
        rename_account(name, new.strip(), data_dir=data_dir)
    elif action == "import_setup":
        copied = import_setup(name, data_dir=data_dir, setup_source=setup_from)
    elif action == "copy_setup":
        copied = recopy_setup(name, data_dir=data_dir, setup_source=setup_from)
    else:
        remove_account(name, data_dir=data_dir)
    overview = accounts_overview(data_dir, provider=provider)
    if started is not None:
        overview["started"] = started
    if copied is not None:
        overview["copied"] = copied
    return overview


def read_json_status(text: str) -> Any:
    """The status command's JSON output, or None."""
    try:
        return json.loads(text or "null")
    except ValueError:
        return None
