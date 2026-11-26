"""Keep Codex's sandboxed commands out of Raticode's credential folders.

Codex's ``--sandbox workspace-write`` lets commands read the whole disk and write the
working folder, every ``--add-dir`` folder and temp. Rem's turns add Raticode's data
folder, which holds every account's config folder, so a prompt-injected turn could plant
a hook in a Claude Code account's settings or read another account's sign-in.

``credential_guard`` rewrites a ``codex exec`` command to use a Codex permission profile
with the same reach, plus a ``deny`` (no reading, no writing) for every folder in
``gofer.core.agent_credentials``. Measured with Codex 0.156 on Linux: a denied folder
inside a writable one can't be listed, read or written, a denied folder that doesn't
exist yet can't be created, and ``.git``, ``.codex`` and ``.agents`` in writable folders
stay read-only as before.

Turns nobody watches (self-work and reviews) go further: the rest of the data folder is
read-only to their commands, apart from their scratch folder, the command-slot
``activity`` and ``queue`` folders and a working folder inside the data folder. That is
what a sandboxed Claude Code turn may write there (``claude_sandbox.sandbox_settings``).

Codex uses its older sandbox settings, ignoring a profile, whenever a loaded config sets
``sandbox_mode``. Such commands are left as they were and ``legacy_reason`` says why.
Native Windows keeps the older settings too: its sandbox refuses split read/write rules.
Full access (``danger-full-access`` or the bypass flag) has no sandbox to change.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from gofer.core.agent_credentials import credential_folders

logger = logging.getLogger(__name__)

# Turned off only by the test suite's fake Codex binaries, whose expected arguments
# predate the profile; tests of the guard itself turn it back on.
ENABLED = True
PROFILE = "raticode"
PROFILE_SELECTOR = f'default_permissions="{PROFILE}"'
NETWORK_ON = "sandbox_workspace_write.network_access=true"
BYPASS_FLAGS = frozenset({"--dangerously-bypass-approvals-and-sandbox", "--yolo"})
_SANDBOX_FLAGS = ("--sandbox", "-s")
_VALUE_FLAGS = frozenset(
    {"-c", "--config", "-m", "--model", "-p", "--profile", "-C", "--cd", "-i", "--image"}
)


def _config_sets_sandbox_mode(path: Path, profile: str | None) -> bool:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    if "sandbox_mode" in data:
        return True
    profiles = data.get("profiles")
    if profile and isinstance(profiles, dict) and isinstance(profiles.get(profile), dict):
        return "sandbox_mode" in profiles[profile]
    return False


def legacy_reason(
    command: list[str],
    *,
    codex_home: Path | None = None,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Why this Codex command keeps its older sandbox settings, or None when it can't."""
    if (platform or sys.platform) == "win32":
        return "Codex's sandbox on native Windows can't keep folders out of a writable one"
    if "--ignore-user-config" in command:
        configs: list[Path] = []
    else:
        environ = os.environ if environ is None else environ
        home = codex_home or Path(environ.get("CODEX_HOME") or Path.home() / ".codex")
        configs = [home / "config.toml"]
    flags = _flags(command)
    profile = flags.get("--profile") or flags.get("-p")
    cwd = flags.get("--cd") or flags.get("-C")
    if cwd:
        configs.append(Path(cwd) / ".codex" / "config.toml")
    for path in configs:
        if _config_sets_sandbox_mode(path, profile):
            return f"{path} sets sandbox_mode, which makes Codex ignore permission profiles"
    if any(_config_key(value) == "sandbox_mode" for value in _config_values(command)):
        return "the command sets sandbox_mode"
    return None


def _flags(command: list[str]) -> dict[str, str]:
    """The last value of each flag that takes one (``--flag value`` or ``--flag=value``)."""
    found: dict[str, str] = {}
    for index, arg in enumerate(command[:-1]):
        if "=" in arg and arg.startswith("--"):
            name, value = arg.split("=", 1)
            found[name] = value
        elif arg in _VALUE_FLAGS or arg in _SANDBOX_FLAGS or arg == "--add-dir":
            found[arg] = command[index + 1]
    return found


def _config_values(command: list[str]) -> list[str]:
    return [
        command[index + 1] for index, arg in enumerate(command[:-1]) if arg in {"-c", "--config"}
    ] + [arg.split("=", 1)[1] for arg in command if arg.startswith("--config=")]


def _config_key(value: str) -> str:
    return value.split("=", 1)[0].strip()


def is_guarded(command: list[str]) -> bool:
    """Whether Raticode's own profile is the one in effect for this command.

    The last ``default_permissions`` must be Raticode's, with no ``sandbox_mode`` (which
    makes Codex ignore profiles) and no other ``permissions`` entries changing it.
    """
    values = _config_values(command)
    chosen = [v for v in values if _config_key(v) == "default_permissions"]
    if not chosen or chosen[-1].replace(" ", "") != PROFILE_SELECTOR:
        return False
    for value in values:
        key = _config_key(value)
        if key == "sandbox_mode" or (
            key.startswith("permissions.") and not key.startswith(f"permissions.{PROFILE}.")
        ):
            return False
    return not any(arg in BYPASS_FLAGS for arg in command[:-1]) and not any(
        arg in _SANDBOX_FLAGS or arg.startswith("--sandbox=") for arg in command[:-1]
    )


def overrides_sandbox(args: Iterable[str]) -> str | None:
    """A ``-c`` override in ``args`` that would replace Raticode's sandbox profile, if any."""
    items = list(args)
    for index, arg in enumerate(items):
        value = None
        if arg in {"-c", "--config"} and index + 1 < len(items):
            value = items[index + 1]
        elif arg.startswith("--config="):
            value = arg.split("=", 1)[1]
        if value is None:
            continue
        key = _config_key(value)
        if key in {"sandbox_mode", "default_permissions"} or key.startswith(
            ("permissions.", "sandbox_workspace_write.writable_roots")
        ):
            return value
    return None


def _toml(value: Any) -> str:
    """A TOML inline value. JSON strings are valid TOML basic strings."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)


def step_denied_paths() -> list[Path]:
    """Home credential stores an unattended workflow step's commands can't read either.

    The same list Claude Code steps hide (``agent_credentials.HOME_SECRET_PATHS``).
    """
    from gofer.core.agent_credentials import HOME_SECRET_PATHS

    return [Path(os.path.expanduser(path)) for path in HOME_SECRET_PATHS]


def existing_denied(paths: Iterable[Path]) -> list[Path]:
    """The paths a profile can deny: each must exist, or Codex's bubblewrap fails to start.

    A credential folder in an existing data folder that isn't there yet is created empty
    and owner-only first, so a turn can't create it and leave a settings file in it.
    """
    from gofer.core.agent_credentials import DATA_CREDENTIAL_FOLDERS
    from gofer.core.provider_accounts import ACCOUNTS_FOLDER_SUFFIX

    found: list[Path] = []
    for path in paths:
        if (
            not os.path.lexists(path)
            and path.parent.is_dir()
            and (path.name in DATA_CREDENTIAL_FOLDERS or path.name.endswith(ACCOUNTS_FOLDER_SUFFIX))
        ):
            try:
                path.mkdir(mode=0o700)
            except OSError:
                pass
        if path.exists() and path not in found:
            found.append(path)
    return found


# Codex 0.156 on Linux covers every denied file with empty data that bubblewrap reads
# from one file descriptor, and bubblewrap closes it after the first file. A second
# denied file then stops every turn before it starts: "bwrap: Can't write data to file
# <path>: Bad file descriptor". Folders are covered another way, so any number work.
PROFILE_FILE_LIMIT = 1


def split_denied(
    denied: Iterable[Path],
    *,
    codex_home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> tuple[list[Path], list[Path]]:
    """The denied paths Codex's profile can hold, and the files left for credential_mask.

    The profile keeps every folder and one file. Codex reads its own sign-in
    (``auth.json`` in its home) itself, so only the profile can keep it from Codex's
    commands while Codex still signs in; that file stays in the profile when it's
    denied. The other files are covered for the whole Codex process instead, which
    doesn't read them.
    """
    paths = list(denied)
    if not (platform or sys.platform).startswith("linux"):
        return paths, []
    environ = os.environ if environ is None else environ
    home = codex_home or Path(environ.get("CODEX_HOME") or Path.home() / ".codex")
    own = Path(os.path.abspath(home / "auth.json"))
    files = [path for path in paths if not path.is_dir()]
    kept = [path for path in files if Path(os.path.abspath(path)) == own] or files[:1]
    kept = kept[:PROFILE_FILE_LIMIT]
    return [path for path in paths if path.is_dir() or path in kept], [
        path for path in files if path not in kept
    ]


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def limited_data_writes(
    writable: Iterable[Path],
    *,
    data_dir: Path,
    data_writable: Iterable[Path],
    working_dir: Path | None,
) -> tuple[list[Path], list[Path]]:
    """The writable folders left outside the data folder, and the ones inside it.

    Inside the data folder a turn writes only ``data_writable`` and its own working
    folder when that is in there (a Rem task with no project works in
    ``rem-work/<task>``). Other added folders inside it lose their write access, as a
    sandboxed Claude Code turn's added folders do.
    """
    data = _absolute(data_dir)
    outside = [path for path in writable if not _absolute(path).is_relative_to(data)]
    inside = [_absolute(path) for path in data_writable]
    if working_dir is not None and _absolute(working_dir).is_relative_to(data):
        inside.append(_absolute(working_dir))
    return outside, list(dict.fromkeys(inside))


def filesystem_rules(
    mode: str,
    *,
    writable: Iterable[Path] = (),
    denied: Iterable[Path] = (),
    read_only: Path | None = None,
    read_only_writable: Iterable[Path] = (),
) -> dict[str, Any]:
    """The profile's filesystem table: what ``mode`` allowed, minus every denied folder.

    ``read_only`` is a folder, Raticode's data folder, that stays readable but not
    writable even inside a writable one, apart from the narrower folders in
    ``read_only_writable``. Codex applies the most specific entry, so a turn's working
    folder at or below it must be in ``read_only_writable`` to stay writable.
    """
    rules: dict[str, Any] = {":root": "read"}
    if mode == "workspace-write":
        rules[":workspace_roots"] = {".": "write"}
        rules[":tmpdir"] = "write"
        rules[":slash_tmp"] = "write"
        for path in writable:
            rules[str(_absolute(path))] = "write"
        if read_only is not None:
            rules[str(_absolute(read_only))] = "read"
            for path in read_only_writable:
                rules[str(_absolute(path))] = "write"
    for path in denied:
        # More specific entries win, so a denied folder inside a writable one stays shut.
        rules[str(path)] = "deny"
    return rules


def profile_config(
    mode: str,
    *,
    writable: Iterable[Path] = (),
    denied: Iterable[Path] = (),
    network: bool = False,
    read_only: Path | None = None,
    read_only_writable: Iterable[Path] = (),
) -> dict[str, Any]:
    """The config entries that select the profile, as dotted keys and values."""
    return {
        "default_permissions": PROFILE,
        f"permissions.{PROFILE}.filesystem": filesystem_rules(
            mode,
            writable=writable,
            denied=denied,
            read_only=read_only,
            read_only_writable=read_only_writable,
        ),
        f"permissions.{PROFILE}.network.enabled": network and mode == "workspace-write",
    }


def credential_guard(
    command: list[str],
    *,
    data_dir: Path | None = None,
    codex_home: Path | None = None,
    extra_denied: Iterable[Path] = (),
    data_writable: Iterable[Path] | None = None,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """``command`` with its sandbox mode turned into a profile that denies credentials.

    The last argument is the prompt (or ``-`` for stdin) and is never changed. Commands
    with full access, no ``--sandbox`` flag, or a reason to keep the older settings come
    back unchanged; a command already guarded comes back unchanged too.

    With ``data_writable`` (a list, even an empty one) and ``data_dir``, the data folder
    is read-only to a writing turn's commands apart from those folders and a working
    folder inside it: the same places a sandboxed Claude Code turn may write.
    """
    if not ENABLED or len(command) < 2 or is_guarded(command):
        return command
    if any(_config_key(value) == "default_permissions" for value in _config_values(command)):
        # Someone else's profile: leave it, and credential_mask covers the run instead.
        logger.warning("Codex keeps a permission profile Raticode didn't set")
        return command
    if any(arg in BYPASS_FLAGS for arg in command[:-1]):
        return command
    flags, prompt = command[:-1], command[-1:]
    mode: str | None = None
    position: int | None = None
    writable: list[Path] = []
    network = False
    kept: list[str] = []
    index = 0
    while index < len(flags):
        arg = flags[index]
        value = flags[index + 1] if index + 1 < len(flags) else None
        if arg in _SANDBOX_FLAGS and value is not None:
            mode, position = value, len(kept) if position is None else position
            index += 2
            continue
        if arg.startswith("--sandbox="):
            mode, position = arg.split("=", 1)[1], len(kept) if position is None else position
            index += 1
            continue
        if arg == "--add-dir" and value is not None:
            writable.append(Path(value))
            index += 2
            continue
        if arg.startswith("--add-dir="):
            writable.append(Path(arg.split("=", 1)[1]))
            index += 1
            continue
        if arg in {"-c", "--config"} and value is not None and value.replace(" ", "") == NETWORK_ON:
            network = True
            index += 2
            continue
        if arg in _VALUE_FLAGS and value is not None:
            kept += [arg, value]
            index += 2
            continue
        kept.append(arg)
        index += 1
    if mode not in {"workspace-write", "read-only"} or position is None:
        return command
    reason = legacy_reason(command, codex_home=codex_home, platform=platform, environ=environ)
    if reason:
        logger.warning("Codex keeps its own sandbox settings: %s", reason)
        return command
    denied, outside = split_denied(
        existing_denied([*credential_folders(data_dir), *(Path(p) for p in extra_denied)]),
        codex_home=codex_home,
        environ=environ,
        platform=platform,
    )
    read_only: Path | None = None
    read_only_writable: list[Path] = []
    if data_writable is not None and data_dir is not None:
        cwd = _flags(command).get("--cd") or _flags(command).get("-C")
        writable, read_only_writable = limited_data_writes(
            writable,
            data_dir=data_dir,
            data_writable=data_writable,
            working_dir=Path(cwd) if cwd else Path.cwd(),
        )
        read_only = data_dir
    config = profile_config(
        mode,
        writable=writable,
        denied=denied,
        network=network,
        read_only=read_only,
        read_only_writable=read_only_writable,
    )
    inserted: list[str] = []
    for key, value in config.items():
        inserted += ["-c", f"{key}={_toml(value)}"]
    guarded = [*kept[:position], *inserted, *kept[position:], *prompt]
    if not outside:
        return guarded
    from gofer.core.credential_mask import files_masked

    return files_masked(guarded, outside, platform=platform)


def app_server_config(command: list[str]) -> dict[str, Any] | None:
    """The profile entries of a guarded command, as an app-server ``config`` object."""
    if not is_guarded(command):
        return None
    config: dict[str, Any] = {}
    for value in _config_values(command):
        key = _config_key(value)
        if key == "default_permissions" or key.startswith(f"permissions.{PROFILE}."):
            raw = value.split("=", 1)[1]
            config[key] = tomllib.loads(f"v = {raw}")["v"]
    return config
