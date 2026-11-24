"""Spot MCP servers whose package can change between runs.

A local MCP server started through a package runner (``npx``, ``bunx``, ``pnpm dlx``,
``uvx``, ``pipx run`` and their aliases) downloads its package when it starts. Unless the
command names an exact version (``pkg@1.2.3`` for npm and uvx, ``pkg==1.2.3`` for Python
packages), each start can fetch a newer release than the one the user approved, so the
server's definition stays the same while the code it runs changes.

Pinning fixes only the package the command names. Its own dependencies still resolve
from version ranges when it is installed, so they can change too.

This module only reads the command and arguments. It never starts anything.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

UNPINNED_WARNING = "Package version not pinned: each run fetches the newest release"
TOP_LEVEL_ONLY = (
    "Pinning fixes the top-level package only: its dependencies can still change "
    "within the version ranges it allows."
)

Ecosystem = Literal["npm", "python"]

# An exact npm version: 1.2.3, 1.2.3-beta.1, 1.2.3+build.
_NPM_EXACT = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
# An exact Python version for ``==``: 1.2, 1.2.3, 1.2.3rc1, 2024.1.post2. No wildcard.
_PY_EXACT = re.compile(r"^v?\d+(?:\.\d+)*(?:[._-]?[A-Za-z]+\d*)*(?:\+[0-9A-Za-z.]+)?$")
_PY_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?")
_COMMIT = re.compile(r"[#@]([0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")

# Options that take a value in the next argument, per runner. Unknown options are taken
# to stand alone, so a value would be read as the package and flagged, never missed.
_NPX_VALUE_OPTIONS = frozenset(
    {
        "-p",
        "--package",
        "-c",
        "--call",
        "--registry",
        "--cache",
        "--userconfig",
        "-w",
        "--workspace",
        "--prefix",
        "--loglevel",
        "--node-options",
        "--shell",
        "--script-shell",
    }
)
_BUNX_VALUE_OPTIONS = frozenset({"-p", "--package", "--registry", "--cwd", "--config", "-c"})
_PNPM_VALUE_OPTIONS = frozenset(
    {
        "--package",
        "-c",
        "--shell-mode",
        "--reporter",
        "--dir",
        "-C",
        "--filter",
        "-F",
        "--loglevel",
        "--registry",
        "--allow-build",
    }
)
_YARN_VALUE_OPTIONS = frozenset({"-p", "--package", "--cwd"})
_UVX_VALUE_OPTIONS = frozenset(
    {
        "--from",
        "--with",
        "-w",
        "--with-editable",
        "--with-requirements",
        "--python",
        "-p",
        "--index",
        "--default-index",
        "--index-url",
        "-i",
        "--extra-index-url",
        "--find-links",
        "-f",
        "--constraints",
        "-c",
        "--constraint",
        "--overrides",
        "--override",
        "--build-constraints",
        "-b",
        "--env-file",
        "--cache-dir",
        "--directory",
        "--project",
        "--config-file",
        "--color",
        "--refresh-package",
        "--reinstall-package",
        "--upgrade-package",
        "-P",
        "--no-binary-package",
        "--only-binary",
        "--no-build-package",
        "--index-strategy",
        "--keyring-provider",
        "--resolution",
        "--prerelease",
        "--fork-strategy",
        "--exclude-newer",
        "--exclude-newer-package",
        "--link-mode",
        "--config-setting",
        "-C",
        "--config-settings-package",
        "--allow-insecure-host",
        "--trusted-host",
        "--python-platform",
        "--torch-backend",
        "--no-sources-package",
    }
)
_PIPX_VALUE_OPTIONS = frozenset(
    {"--spec", "--python", "--pip-args", "--index-url", "-i", "--backend", "--path"}
)
# Options whose value names a package the runner installs.
_NPM_PACKAGE_OPTIONS = frozenset({"-p", "--package"})
_UVX_PACKAGE_OPTIONS = frozenset({"--from", "--with", "-w"})
_PIPX_PACKAGE_OPTIONS = frozenset({"--spec"})
# Wrappers that run the rest of the line as a command.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ash", "fish"})
_MAX_DEPTH = 4


@dataclass(frozen=True, slots=True)
class PackageUse:
    """One package a runner fetches: the runner, what was written and whether it's exact."""

    runner: str
    spec: str
    pinned: bool

    def to_payload(self) -> dict[str, Any]:
        return {"runner": self.runner, "package": self.spec, "pinned": self.pinned}


def _program(word: str) -> str:
    """``npx`` from ``/usr/bin/npx``, ``npx.cmd`` or ``C:\\node\\npx.exe``."""
    name = PureWindowsPath(word).name if "\\" in word else PurePosixPath(word).name
    lowered = name.lower()
    for suffix in (".cmd", ".exe", ".bat", ".ps1"):
        if lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def _is_local(spec: str) -> bool:
    """A path on this computer, which a runner reads rather than downloads."""
    if spec.startswith((".", "/", "~", "\\", "file:")) or re.match(r"^[A-Za-z]:[\\/]", spec):
        return True
    return spec.endswith((".whl", ".tgz", ".tar.gz", ".zip")) and "://" not in spec


def _remote_pinned(spec: str) -> bool | None:
    """For a git or web address: pinned only to a full commit. None when it isn't one."""
    lowered = spec.lower()
    if "://" in spec or lowered.startswith(
        ("git+", "git@", "github:", "gitlab:", "bitbucket:", "gist:")
    ):
        return bool(_COMMIT.search(spec))
    return None


def npm_spec_pinned(spec: str) -> bool:
    """Whether an npm package spec names one exact version.

    ``pkg@1.2.3`` and ``@scope/pkg@1.2.3`` are exact. ``pkg``, ``pkg@latest``, a range
    (``pkg@^1.2``) or a tag are not. ``alias@npm:pkg@1.2.3`` checks the real package. A
    ``user/repo`` shorthand or a git address is exact only with a full commit hash.
    """
    remote = _remote_pinned(spec)
    if remote is not None:
        return remote
    at = spec.find("@", 1)
    if at < 0:
        # No version. "user/repo" is GitHub shorthand, also unpinned without a commit.
        return bool(_COMMIT.search(spec)) if "/" in spec and not spec.startswith("@") else False
    version = spec[at + 1 :]
    if version.startswith("npm:"):
        return npm_spec_pinned(version[4:])
    return bool(_NPM_EXACT.match(version))


def python_spec_pinned(spec: str, *, at_version: bool = False) -> bool:
    """Whether a Python requirement names one exact version.

    ``pkg==1.2.3`` and ``pkg===1.2.3`` are exact, with or without extras (``pkg[cli]``).
    ``uvx`` also takes ``pkg@1.2.3`` (``at_version``). A wildcard (``==1.2.*``), a range,
    ``@latest`` or no version at all is not. A direct URL is exact only with a full commit.
    """
    text = spec.split(";", 1)[0].strip()
    if " @ " in text:
        text = text.split(" @ ", 1)[1].strip()
    remote = _remote_pinned(text)
    if remote is not None:
        return remote
    match = _PY_NAME.match(text)
    if match is None:
        return False
    rest = text[match.end() :].strip()
    if at_version and rest.startswith("@"):
        version = rest[1:].strip()
        return version.lower() != "latest" and bool(_PY_EXACT.match(version))
    for operator in ("===", "=="):
        if rest.startswith(operator):
            version = rest[len(operator) :].strip()
            return "," not in version and "*" not in version and bool(_PY_EXACT.match(version))
    return False


def _options_and_rest(
    args: Sequence[str], value_options: frozenset[str], package_options: frozenset[str]
) -> tuple[list[str], list[str]]:
    """The packages named by options, and the arguments from the first positional one on."""
    named: list[str] = []
    index = 0
    while index < len(args):
        word = args[index]
        if word == "--":
            return named, list(args[index + 1 :])
        if not word.startswith("-") or word == "-":
            return named, list(args[index:])
        option, has_value, value = word.partition("=")
        if has_value:
            if option in package_options:
                named.append(value)
            index += 1
            continue
        if option in value_options and index + 1 < len(args):
            if option in package_options:
                named.append(args[index + 1])
            index += 2
            continue
        index += 1
    return named, []


def _npm_style(runner: str, args: Sequence[str], value_options: frozenset[str]) -> list[PackageUse]:
    named, rest = _options_and_rest(args, value_options, _NPM_PACKAGE_OPTIONS)
    specs = named or rest[:1]
    # With --package, the first positional word is a program the package provides.
    return [
        PackageUse(runner, spec, npm_spec_pinned(spec))
        for spec in specs
        if spec and not _is_local(spec)
    ]


def _uvx(runner: str, args: Sequence[str]) -> list[PackageUse]:
    named, rest = _options_and_rest(args, _UVX_VALUE_OPTIONS, _UVX_PACKAGE_OPTIONS)
    uses = [
        PackageUse(runner, spec, python_spec_pinned(spec, at_version=True))
        for spec in named
        if spec and not _is_local(spec)
    ]
    if rest and not _has_from(args) and not _is_local(rest[0]):
        uses.insert(0, PackageUse(runner, rest[0], python_spec_pinned(rest[0], at_version=True)))
    return uses


def _has_from(args: Sequence[str]) -> bool:
    for word in args:
        if word == "--":
            return False
        if word == "--from" or word.startswith("--from="):
            return True
    return False


def _pipx_run(args: Sequence[str]) -> list[PackageUse]:
    named, rest = _options_and_rest(args, _PIPX_VALUE_OPTIONS, _PIPX_PACKAGE_OPTIONS)
    if named:
        return [
            PackageUse("pipx run", spec, python_spec_pinned(spec))
            for spec in named
            if spec and not _is_local(spec)
        ]
    if rest and not _is_local(rest[0]):
        return [PackageUse("pipx run", rest[0], python_spec_pinned(rest[0]))]
    return []


def _subcommand(args: Sequence[str], value_options: frozenset[str]) -> tuple[str | None, list[str]]:
    """The first positional word after a program's own options, and what follows it."""
    _, rest = _options_and_rest(args, value_options, frozenset())
    if not rest:
        return None, []
    return rest[0], rest[1:]


def package_uses(command: str, args: Iterable[Any] = (), *, _depth: int = 0) -> list[PackageUse]:
    """Each package a package runner in ``command`` and ``args`` fetches when it starts.

    Looks through ``env``, ``cmd /c`` and ``sh -c`` wrappers. Returns an empty list for a
    command that isn't a package runner.
    """
    words = [str(item) for item in args]
    command = str(command or "").strip()
    if not command or _depth > _MAX_DEPTH:
        return []
    if not words and any(space in command for space in " \t"):
        # A whole command line in one string, such as "npx -y pkg".
        try:
            split = shlex.split(command)
        except ValueError:
            split = command.split()
        if len(split) > 1:
            return package_uses(split[0], split[1:], _depth=_depth + 1)
    program = _program(command)
    if program in {"npx", "pnpx"}:
        return _npm_style(program, words, _NPX_VALUE_OPTIONS)
    if program == "bunx":
        return _npm_style("bunx", words, _BUNX_VALUE_OPTIONS)
    if program == "bun":
        sub, rest = _subcommand(words, _BUNX_VALUE_OPTIONS)
        return _npm_style("bun x", rest, _BUNX_VALUE_OPTIONS) if sub == "x" else []
    if program == "pnpm":
        sub, rest = _subcommand(words, _PNPM_VALUE_OPTIONS)
        return _npm_style("pnpm dlx", rest, _PNPM_VALUE_OPTIONS) if sub == "dlx" else []
    if program == "yarn":
        sub, rest = _subcommand(words, _YARN_VALUE_OPTIONS)
        return _npm_style("yarn dlx", rest, _YARN_VALUE_OPTIONS) if sub == "dlx" else []
    if program == "npm":
        sub, rest = _subcommand(words, _NPX_VALUE_OPTIONS)
        return _npm_style(f"npm {sub}", rest, _NPX_VALUE_OPTIONS) if sub in {"exec", "x"} else []
    if program == "uvx":
        return _uvx("uvx", words)
    if program == "uv":
        sub, rest = _subcommand(words, _UVX_VALUE_OPTIONS)
        if sub == "tool" and rest[:1] == ["run"]:
            return _uvx("uv tool run", rest[1:])
        return []
    if program == "pipx":
        sub, rest = _subcommand(words, _PIPX_VALUE_OPTIONS)
        return _pipx_run(rest) if sub == "run" else []
    if program == "env":
        index = 0
        while index < len(words) and (words[index].startswith("-") or "=" in words[index]):
            index += 2 if words[index] in {"-u", "--unset", "-C", "--chdir", "-S"} else 1
        if index < len(words):
            return package_uses(words[index], words[index + 1 :], _depth=_depth + 1)
        return []
    if program == "cmd" and words and words[0].lower() in {"/c", "/k"}:
        rest = words[1:]
        if len(rest) == 1:
            return package_uses(rest[0], (), _depth=_depth + 1)
        if rest:
            return package_uses(rest[0], rest[1:], _depth=_depth + 1)
        return []
    if program in _SHELLS:
        for index, word in enumerate(words):
            if word.startswith("-") and "c" in word.lstrip("-") and not word.startswith("--"):
                if index + 1 < len(words):
                    return _shell_line(words[index + 1], _depth + 1)
                return []
        return []
    return []


_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", ";;", "(", ")"})


def _shell_line(line: str, depth: int) -> list[PackageUse]:
    """The packages each command in a shell line fetches: ``cd x && npx -y pkg``."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = line.split()
    uses: list[PackageUse] = []
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token not in _SHELL_SEPARATORS:
            segment.append(token)
            continue
        while segment and (
            segment[0] == "exec" or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", segment[0])
        ):
            segment = segment[1:]
        if segment:
            uses.extend(package_uses(segment[0], segment[1:], _depth=depth))
        segment = []
    return uses


def unpinned_packages(server: Mapping[str, Any] | None) -> list[str]:
    """The packages a local server's runner fetches without an exact version."""
    if not server or server.get("type") != "stdio":
        return []
    return [
        use.spec
        for use in package_uses(str(server.get("command") or ""), server.get("args") or [])
        if not use.pinned
    ]


NPM_HOSTS = ("registry.npmjs.org",)
PYTHON_HOSTS = ("pypi.org", "files.pythonhosted.org")
_PYTHON_RUNNERS = frozenset({"uvx", "uv tool run", "pipx run"})


def package_hosts(server: Mapping[str, Any] | None) -> list[str]:
    """The default registry hosts a local server's package runners download from."""
    if not server or server.get("type") != "stdio":
        return []
    hosts: list[str] = []
    for use in package_uses(str(server.get("command") or ""), server.get("args") or []):
        for host in PYTHON_HOSTS if use.runner in _PYTHON_RUNNERS else NPM_HOSTS:
            if host not in hosts:
                hosts.append(host)
    return hosts


def unpinned_message(name: str, packages: Sequence[str]) -> str:
    """'MCP server 'github' ... ', naming the packages, for preflight and the CLI."""
    listed = ", ".join(packages[:5]) + (" and more" if len(packages) > 5 else "")
    return f"MCP server {name!r}: {UNPINNED_WARNING} ({listed})."
