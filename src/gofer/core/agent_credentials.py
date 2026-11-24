"""The folders Raticode keeps credentials in, which no agent may read or change.

Every kind of run hides the same list: Claude Code turns and workflow steps through
their sandbox and Read/Edit deny rules (``gofer.ui.claude_sandbox``), Codex runs
through a Codex permission profile (``gofer.core.codex_sandbox``), and providers with
no sandbox of their own through a mount namespace that covers each folder
(``gofer.core.credential_mask``).

- ``devices``: paired phone and desktop keys.
- ``browser-grants``: sites the user shared with Rem's browser tabs.
- ``<provider>-accounts``: each Claude Code, Codex or Grok account's own config folder,
  with its sign-in. A Claude Code account's ``settings.json`` is that account's "user"
  settings, so a hook written there would run outside every sandbox, and a Codex
  account's ``config.toml`` can name programs Codex starts.
- A headless backend's secrets folder (``--secrets-dir`` or systemd's
  ``$CREDENTIALS_DIRECTORY``): the Jev key, Organizations and workflow secrets.

A CLI still reads its own account folder: it runs outside its sandbox. Only the
commands and file tools the agent drives lose them.
"""

from __future__ import annotations

import os
from pathlib import Path

from gofer.utils.paths import get_data_dir

# Credential stores in the user's home that an unattended agent's commands have no
# reason to read. Paths that don't exist on this computer are skipped, so one list
# serves Linux and macOS. Rem's own attended turns keep them: the user may ask a turn
# to push with their SSH key.
HOME_SECRET_PATHS = (
    "~/.ssh",
    "~/.gnupg",
    "~/.claude",
    "~/.claude.json",
    "~/.codex/auth.json",
    # Grok's default sign-in and its MCP servers' OAuth tokens.
    "~/.grok/auth.json",
    "~/.grok/mcp_credentials.json",
    "~/.aws",
    "~/.azure",
    "~/.config/gcloud",
    "~/.kube",
    "~/.docker/config.json",
    "~/.netrc",
    "~/.git-credentials",
    "~/.config/git/credentials",
    "~/.config/gh",
    "~/.local/share/keyrings",
    "~/.password-store",
    "~/.npmrc",
    "~/.pypirc",
    "~/Library/Keychains",
)

# Folders in Raticode's data folder that hold keys or grants; data_credential_names()
# adds every provider's account folders.
DATA_CREDENTIAL_FOLDERS = ("devices", "browser-grants")


def data_credential_names(data_dir: Path) -> list[str]:
    """Credential folders in the data folder, as names, whether or not they exist yet."""
    from gofer.core.provider_accounts import account_folder_names, hidden_account_folders

    names = [*DATA_CREDENTIAL_FOLDERS]
    for name in account_folder_names():
        if name not in names:
            names.append(name)
    for folder in hidden_account_folders(data_dir):
        if folder.parent == data_dir and folder.name not in names:
            names.append(folder.name)
    return names


def secrets_folders() -> list[Path]:
    """A headless backend's read-only secrets folder and systemd's credentials folder.

    The configured folder (``--secrets-dir``, or ``$CREDENTIALS_DIRECTORY`` by default)
    holds the Jev key and other keychain secrets, such as Organizations and workflow
    secrets. systemd's folder is listed too in case it differs, from the sealed copy once
    startup moved it out of the environment. Each folder is listed as given and as
    resolved, so a symlinked path can't be read under its other name.
    """
    from gofer.core.secrets_folder import CREDENTIALS_DIRECTORY_ENV, configured_secrets_dir
    from gofer.utils.process import credential_env

    candidates: list[Path] = []
    configured = configured_secrets_dir()
    if configured is not None:
        candidates.append(configured)
    systemd = credential_env(CREDENTIALS_DIRECTORY_ENV).strip()
    if systemd:
        candidates.append(Path(systemd))
    return _with_resolved(candidates)


def _with_resolved(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        path = Path(os.path.abspath(path.expanduser()))
        for variant in (path, path.resolve()):
            if variant not in found:
                found.append(variant)
    return found


def credential_folders(data_dir: Path | None = None) -> list[Path]:
    """Every credential folder Raticode creates, absolute, as given and as resolved.

    Includes account folders that don't exist yet, so a turn can't create one ahead of
    the user and leave a settings file in it. Raticode's default data folder is always
    covered, as well as ``data_dir`` when a caller runs with another one.
    """
    roots: list[Path] = []
    for root in (data_dir, get_data_dir()):
        if root is not None:
            root = Path(os.path.abspath(root.expanduser()))
            if root not in roots:
                roots.append(root)
    folders: list[Path] = []
    for root in roots:
        folders += [root / name for name in data_credential_names(root)]
    return _with_resolved([*folders, *secrets_folders()])


def inside_credential_folder(path: Path, data_dir: Path | None = None) -> Path | None:
    """The credential folder ``path`` is in (or is, or holds), or None."""
    target = Path(os.path.abspath(path.expanduser()))
    resolved = target.resolve()
    for folder in credential_folders(data_dir):
        for candidate in (target, resolved):
            if candidate.is_relative_to(folder) or folder.is_relative_to(candidate):
                return folder
    return None
