"""Operating-system sandbox for Rattish agent steps.

Claude Code steps run with the same sandbox settings as Rem's unattended turns
(``gofer.ui.claude_sandbox``): shell writes stay in the step's working
directory, its ``extra-paths`` and a scratch folder for the run; credential
folders are hidden from the shell and denied to the Read and Edit tools; and
the shell reaches only the hosts in the step's ``network`` field. In a
``workspace: worktree`` run the shell writes only the parts of the repository's
``.git`` folder the run's own branch needs (``RunGitAccess``). Codex keeps
its own ``workspace-write`` sandbox, and a triggered run can't give it full
access unless the step sets ``sandbox: off``.

Providers with no sandbox, and every provider on native Windows, run
unsandboxed. Preflight and the Triggers section say so.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from gofer.core.provider_profiles import ResolvedProviderSettings

ANY_HOST: Literal["any"] = "any"
SANDBOX_OFF = "off"
# Under the data folder, which the sandboxed shell can't otherwise see.
SCRATCH_FOLDER = "run-scratch"
SANDBOXED_PROVIDERS = frozenset({"claude_code", "codex"})
# Plain API calls: no shell and no file tools, so there is nothing to sandbox.
NO_SHELL_PROVIDERS = frozenset({"anthropic_api", "openai_api"})
PROVIDER_NAMES = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "copilot": "GitHub Copilot",
    "opencode": "OpenCode",
    "antigravity": "Antigravity",
    "grok": "Grok",
}
CODEX_FULL_ACCESS = "danger-full-access"
CODEX_NETWORK_ON = "sandbox_workspace_write.network_access=true"
_CODEX_BYPASS_FLAGS = frozenset({"--dangerously-bypass-approvals-and-sandbox", "--yolo"})

Network = list[str] | Literal["any"]

# Whether the run that is executing was started by a trigger. Set by the run
# service; node handlers started inside the run inherit it.
_TRIGGERED: ContextVar[bool] = ContextVar("rattish_triggered_run", default=False)


@contextmanager
def triggered_run(triggered: bool) -> Iterator[None]:
    token = _TRIGGERED.set(triggered)
    try:
        yield
    finally:
        _TRIGGERED.reset(token)


def in_triggered_run() -> bool:
    return _TRIGGERED.get()


# The folder holding the running run's file input copies (``file_inputs``). A
# standalone workflow keeps its runs in the data folder, which the sandbox hides, so
# Claude Code steps get this one folder back to read.
_INPUT_FILES: ContextVar[Path | None] = ContextVar("rattish_input_files", default=None)


@contextmanager
def input_files_visible(folder: Path) -> Iterator[None]:
    token = _INPUT_FILES.set(folder)
    try:
        yield
    finally:
        _INPUT_FILES.reset(token)


def input_files_folder() -> Path | None:
    folder = _INPUT_FILES.get()
    return folder if folder is not None and folder.is_dir() else None


# The repository and worktree folder of a ``workspace: worktree`` run that is executing.
# Set by the run service, like ``_TRIGGERED``.
_RUN_WORKTREE: ContextVar[tuple[Path, Path] | None] = ContextVar(
    "rattish_run_worktree", default=None
)


@contextmanager
def run_worktree(record: Mapping[str, Any] | None) -> Iterator[None]:
    """Mark the run's agent steps as working in this run worktree record, if any."""
    value = None
    if record is not None:
        value = (Path(str(record["repository"])), Path(str(record["checkout"])))
    token = _RUN_WORKTREE.set(value)
    try:
        yield
    finally:
        _RUN_WORKTREE.reset(token)


# Git settings for a run worktree's steps. Automatic gc and maintenance repack objects
# and pack refs, which needs the parts of .git the step can't write.
RUN_GIT_CONFIG = {"gc.auto": "0", "maintenance.auto": "false"}
# Files in a run worktree's admin folder its steps may read but not write: a step that
# rewrote ``commondir`` or ``gitdir`` could point the checkout at another repository, and
# ``config.worktree`` is config git reads for the checkout at worktree scope.
ADMIN_PROTECTED_FILES = ("commondir", "gitdir", "config.worktree")


@dataclass(frozen=True, slots=True)
class RunGitAccess:
    """The parts of a repository's shared ``.git`` folder a run worktree's step may write.

    New objects, the run's own worktree folder (its HEAD, index and reflog) and the
    run branches. Every other branch, tag and setting stays read-only, so a step can
    commit on its own branch but can't move the project's.
    """

    # None when the repository's ``.git`` folder couldn't be read: nothing is writable.
    common_dir: Path | None
    # ``worktrees/<name>`` for this run's worktree; None when it couldn't be found.
    admin_dir: Path | None

    def writable(self) -> list[Path]:
        from gofer.rattish.run_worktrees import BRANCH_PREFIX

        if self.common_dir is None:
            return []
        refs = Path("refs/heads", BRANCH_PREFIX)
        return [
            self.common_dir / "objects",
            *([self.admin_dir] if self.admin_dir is not None else []),
            self.common_dir / refs,
            self.common_dir / "logs" / refs,
        ]

    def protected(self) -> list[Path]:
        """Files in the writable admin folder the step must not change.

        ``commondir`` and ``gitdir`` say which repository and checkout the worktree
        belongs to, and ``config.worktree`` is worktree-scoped config, which can name
        hooks and filters. Raticode's own git commands in the checkout read all three.
        """
        if self.common_dir is None or self.admin_dir is None:
            return []
        return [self.admin_dir / name for name in ADMIN_PROTECTED_FILES]

    def create_folders(self) -> None:
        """Make the ref folders exist, as the sandbox only opens folders that do."""
        if self.common_dir is None:
            return
        for path in self.writable()[-2:]:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass


def _worktree_admin_dir(common_dir: Path, checkout: Path) -> Path | None:
    """The ``worktrees/<name>`` folder git keeps for ``checkout``.

    Found from the repository's side, whose ``gitdir`` files a step can't change for
    other worktrees, rather than from the checkout's own ``.git`` file, which it can.
    """
    target = (checkout / ".git").resolve()
    try:
        candidates = sorted((common_dir / "worktrees").iterdir())
    except OSError:
        return None
    for candidate in candidates:
        try:
            named = (candidate / "gitdir").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if named and Path(named).resolve() == target:
            return candidate.resolve()
    return None


def run_git_access(working_dir: Path) -> RunGitAccess | None:
    """The git folders a step working in the current run's worktree may write.

    None when no worktree run is executing or the step works outside the run's worktree;
    such steps keep the general rule. When the repository's ``.git`` folder can't be
    read, the step gets an empty set of folders rather than the whole of it.
    """
    current = _RUN_WORKTREE.get()
    if current is None:
        return None
    repository, checkout = current
    if not working_dir.resolve().is_relative_to(checkout.resolve()):
        return None
    from gofer.ui import claude_sandbox

    common = claude_sandbox.repository_git_dir(repository)
    if common is None:
        return RunGitAccess(None, None)
    return RunGitAccess(common, _worktree_admin_dir(common, checkout))


def run_git_environment(base: Mapping[str, str]) -> dict[str, str]:
    """``GIT_CONFIG_*`` entries that add ``RUN_GIT_CONFIG`` after those already in ``base``."""
    from gofer.utils.process import with_git_config

    merged = with_git_config(dict(base), RUN_GIT_CONFIG)
    return {key: value for key, value in merged.items() if base.get(key) != value}


class SandboxError(Exception):
    """A step can't start inside its sandbox."""

    def __init__(self, code: str, message: str, suggestion: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion


def builtin_adapter(subscription: object, subscription_id: str) -> bool:
    """Whether a provider runtime is Raticode's own Claude Code or Codex adapter.

    Only those start the CLIs the sandbox settings are written for. A stand-in,
    such as a test double, runs no shell of its own.
    """
    if subscription_id == "claude_code":
        from gofer.subscriptions.claude_code import ClaudeCodeSubscription

        return isinstance(subscription, ClaudeCodeSubscription)
    if subscription_id == "codex":
        from gofer.subscriptions.codex import CodexSubscription

        return isinstance(subscription, CodexSubscription)
    return False


def step_network(configuration: Mapping[str, Any]) -> Network:
    """The hosts a step's shell may reach: a list (default none) or ``"any"``."""
    value = configuration.get("network")
    if value == ANY_HOST:
        return ANY_HOST
    if isinstance(value, list):
        return [str(item).lower().rstrip(".") for item in value]
    return []


def sandbox_off(configuration: Mapping[str, Any]) -> bool:
    return configuration.get("sandbox") == SANDBOX_OFF


def provider_name(subscription_id: str) -> str:
    return PROVIDER_NAMES.get(subscription_id, subscription_id)


def run_scratch_dir(data_dir: Path, run_id: str) -> Path:
    return data_dir / SCRATCH_FOLDER / run_id


def remove_run_scratch(data_dir: Path, run_id: str) -> None:
    shutil.rmtree(run_scratch_dir(data_dir, run_id), ignore_errors=True)


def codex_full_access(settings: ResolvedProviderSettings | None) -> bool:
    """Whether a Codex profile turns Codex's own sandbox off."""
    if settings is None:
        return False
    if settings.sandbox_mode == CODEX_FULL_ACCESS:
        return True
    args = settings.extra_args
    for index, arg in enumerate(args):
        if arg in _CODEX_BYPASS_FLAGS:
            return True
        if arg in {"--sandbox", "-s"} and index + 1 < len(args):
            if args[index + 1] == CODEX_FULL_ACCESS:
                return True
        if arg == f"--sandbox={CODEX_FULL_ACCESS}":
            return True
    return False


def _passes_own_settings(settings: ResolvedProviderSettings | None) -> bool:
    """True when a profile passes --settings or --setting-sources, which the sandbox sets."""
    return settings is not None and any(
        arg == flag or arg.startswith(f"{flag}=")
        for arg in settings.extra_args
        for flag in ("--settings", "--setting-sources")
    )


def unsandboxed_reason(
    subscription_id: str,
    configuration: Mapping[str, Any],
    settings: ResolvedProviderSettings | None = None,
    *,
    platform: str | None = None,
) -> str | None:
    """Why this provider choice runs the step's shell without a sandbox, or None."""
    if subscription_id in NO_SHELL_PROVIDERS:
        return None
    if sandbox_off(configuration):
        return "the step sets sandbox: off"
    platform = platform or sys.platform
    name = provider_name(subscription_id)
    if subscription_id not in SANDBOXED_PROVIDERS:
        return f"{name} has no sandbox Raticode can turn on"
    if platform == "win32":
        return f"Raticode doesn't rely on {name}'s sandbox on native Windows"
    if subscription_id == "codex" and codex_full_access(settings):
        return "its profile gives Codex full access"
    return None


def sandbox_problem(
    subscription_id: str,
    configuration: Mapping[str, Any],
    settings: ResolvedProviderSettings | None,
    *,
    triggered: bool,
    platform: str | None = None,
) -> SandboxError | None:
    """Why this provider choice can't start inside its sandbox, or None.

    Unsandboxed choices (see ``unsandboxed_reason``) have no problem here; they
    are reported as warnings instead. The one exception is Codex full access,
    which a triggered run refuses unless the step sets ``sandbox: off``.
    """
    if sandbox_off(configuration) or subscription_id not in SANDBOXED_PROVIDERS:
        return None
    platform = platform or sys.platform
    network = step_network(configuration)
    if subscription_id == "codex":
        if codex_full_access(settings):
            if not triggered:
                return None
            profile = settings.profile_name if settings is not None else None
            where = f"profile {profile!r}" if profile else "its profile"
            return SandboxError(
                "RATTISH_PREFLIGHT_SANDBOX_REQUIRED",
                f"A triggered run can't give Codex full access, which {where} asks for.",
                f"Set Codex's sandbox mode in {where} back to workspace-write, or add "
                "`sandbox: off` to the step to let triggered runs use full access.",
            )
        from gofer.core.codex_sandbox import overrides_sandbox

        override = overrides_sandbox(settings.extra_args) if settings is not None else None
        if override is not None:
            profile = settings.profile_name if settings is not None else None
            where = f"Profile {profile!r}" if profile else "The profile"
            return SandboxError(
                "RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE",
                f"{where} passes `-c {override}` to Codex, which would replace the step's "
                "sandbox and the folders it keeps credentials out of.",
                "Remove it from the profile's extra arguments, or add `sandbox: off` to the step.",
            )
        if network and network != ANY_HOST:
            hosts = ", ".join(network)
            return SandboxError(
                "RATTISH_PREFLIGHT_NETWORK_UNSUPPORTED",
                f"Codex can only turn its shell's network on or off, so it can't be limited "
                f"to {hosts}.",
                "Write `network: any` for this step, or use claude-code, which can limit the "
                "shell to the hosts you list.",
            )
        return None
    if platform == "win32":
        return None
    from gofer.ui.claude_sandbox import unavailable_reason

    reason = unavailable_reason(platform)
    if reason:
        return SandboxError(
            "RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE",
            reason,
            "Install what it names, or add `sandbox: off` to the step to run it unsandboxed.",
        )
    if _passes_own_settings(settings):
        profile = settings.profile_name if settings is not None else None
        return SandboxError(
            "RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE",
            f"Profile {profile!r} passes its own --settings or --setting-sources to Claude "
            "Code, which would replace the step's sandbox.",
            "Remove them from the profile's extra arguments, or add `sandbox: off` to the step.",
        )
    return None


# ----------------------------------------------------------------------------
# Does a step need the network?

# Commands that reach the network on their own.
_NETWORK_COMMANDS = frozenset(
    {
        "aria2c",
        "curl",
        "ftp",
        "gh",
        "http",
        "https",
        "nc",
        "ncat",
        "rsync",
        "scp",
        "sftp",
        "ssh",
        "telnet",
        "wget",
        "xh",
    }
)
# Commands that reach it with these subcommands.
_NETWORK_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "apt": frozenset({"install", "update", "upgrade"}),
    "apt-get": frozenset({"install", "update", "upgrade"}),
    "brew": frozenset({"install", "update", "upgrade"}),
    "cargo": frozenset({"add", "fetch", "install", "publish", "update"}),
    "composer": frozenset({"install", "require", "update"}),
    "docker": frozenset({"login", "pull", "push"}),
    "gem": frozenset({"install", "update"}),
    "git": frozenset({"clone", "fetch", "ls-remote", "pull", "push", "submodule"}),
    "go": frozenset({"get", "install"}),
    "npm": frozenset({"add", "ci", "i", "install", "publish", "update", "view"}),
    "pip": frozenset({"download", "install"}),
    "pip3": frozenset({"download", "install"}),
    "pnpm": frozenset({"add", "i", "install", "publish", "update"}),
    "uv": frozenset({"add", "lock", "pip", "sync"}),
    "yarn": frozenset({"add", "install", "publish", "upgrade"}),
}
_BASH_RULE = re.compile(r"^Bash\((?P<command>.+)\)$")
_URL_HOST = re.compile(r"\bhttps?://(?P<host>[A-Za-z0-9.-]+)", re.IGNORECASE)
# Enough of a prompt file to find the web addresses it names.
MAX_PROMPT_BYTES = 256 * 1024


def _network_command(rule: str) -> str | None:
    """The network command a ``Bash(...)`` tool rule allows, if it names one."""
    match = _BASH_RULE.match(rule.strip())
    if match is None:
        return None
    command = match.group("command").split(":", 1)[0].replace("*", " ")
    words = command.split()
    if not words:
        return None
    program = os.path.basename(words[0])
    if program in _NETWORK_COMMANDS:
        return program
    subcommands = _NETWORK_SUBCOMMANDS.get(program)
    if subcommands and len(words) > 1 and words[1] in subcommands:
        return f"{program} {words[1]}"
    return None


def host_allowed(host: str, network: Network) -> bool:
    if network == ANY_HOST:
        return True
    host = host.lower().rstrip(".")
    for pattern in network:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


@dataclass(frozen=True, slots=True)
class NetworkNeed:
    """Signs that a step's shell needs hosts its ``network`` field doesn't allow."""

    hosts: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()

    def to_details(self) -> dict[str, Any]:
        return {"hosts": list(self.hosts), "commands": list(self.commands)}


def prompt_hosts(text: str) -> list[str]:
    hosts: list[str] = []
    for match in _URL_HOST.finditer(text):
        host = match.group("host").lower().strip(".")
        if ("." in host or host == "localhost") and host not in hosts:
            hosts.append(host)
    return hosts


def network_need(configuration: Mapping[str, Any], prompt_text: str | None) -> NetworkNeed | None:
    """What the step's own settings show its shell will reach, beyond ``network``.

    Two signs count: tool rules that allow a network command, such as
    ``Bash(curl:*)`` or ``Bash(git push:*)``, and web addresses in the prompt
    when the step can't use the WebFetch tool, which runs outside the sandbox.
    A command with no host only counts while ``network`` lists none.
    """
    network = step_network(configuration)
    if network == ANY_HOST:
        return None
    tools = [str(tool) for tool in configuration.get("tools") or []]
    commands: list[str] = []
    if not network:
        for tool in tools:
            command = _network_command(tool)
            if command and command not in commands:
                commands.append(command)
    hosts: list[str] = []
    if prompt_text and not any(tool.strip().startswith("WebFetch") for tool in tools):
        hosts = [host for host in prompt_hosts(prompt_text) if not host_allowed(host, network)]
    if not hosts and not commands:
        return None
    return NetworkNeed(tuple(hosts), tuple(commands))


def network_fix(
    node_id: str, need: NetworkNeed, network: Network, *, codex: bool
) -> tuple[str, tuple[str, ...]]:
    """The preflight message and fix-it for a step whose shell needs the network."""
    reasons: list[str] = []
    if need.hosts:
        reasons.append("its prompt uses " + ", ".join(need.hosts))
    if need.commands:
        reasons.append("its tools allow " + ", ".join(need.commands))
    reach = "can reach no hosts" if not network else "can't reach them"
    message = (
        f"Agent {node_id!r} needs the network, but its shell {reach}: {' and '.join(reasons)}."
    )
    if codex:
        return message, ("Add `network: any` to the step. Codex can't limit its shell to hosts.",)
    if need.hosts:
        listed = [*(network if isinstance(network, list) else []), *need.hosts]
        lines = "\n".join(f"    - {host}" for host in listed)
        fix = f"Add the hosts to the step:\n  network:\n{lines}\nor write `network: any`."
    else:
        fix = (
            "List the hosts those commands reach in the step's `network` field, for example "
            "`network:` with `- registry.npmjs.org`, or write `network: any`."
        )
    return message, (fix,)


# ----------------------------------------------------------------------------
# Running a step


@dataclass(slots=True)
class PreparedStep:
    """What the runtime changes to start one provider call in its sandbox."""

    settings: ResolvedProviderSettings
    env: dict[str, str] = field(default_factory=dict)
    # The folder Claude Code starts in, where the sandbox may leave mount points.
    stub_root: Path | None = None
    # A run worktree's admin folder, where a protected file that doesn't exist yet
    # (``config.worktree``) may get a mount point too.
    admin_stub_root: Path | None = None
    sandboxed: bool = False


def claude_step_settings(
    *,
    working_dir: Path,
    data_dir: Path,
    scratch: Path,
    extra_paths: Iterable[Path],
    network: Network,
    input_files: Path | None = None,
    run_git: RunGitAccess | None = None,
) -> dict[str, Any]:
    """Claude Code settings for one sandboxed agent step.

    The same settings as Rem's unattended turns, with three differences: the
    step's ``extra-paths`` are writable, its ``network`` hosts are allowed, and
    Bash isn't approved automatically, so the step's own tool permissions still
    decide whether the shell may run at all.

    A worktree's commits go to the repository's shared ``.git`` folder. In a
    ``workspace: worktree`` run (``run_git``) only the parts its own branch needs
    are writable, apart from the admin folder's ``commondir``, ``gitdir`` and
    ``config.worktree``; in a worktree the user chose as the project, all of it is.
    """
    from gofer.ui import claude_sandbox

    git_writable = (
        run_git.writable() if run_git is not None else [claude_sandbox.git_common_dir(working_dir)]
    )
    settings = claude_sandbox.sandbox_settings(
        root=working_dir,
        data_dir=data_dir,
        read_only=False,
        writable=[scratch, *extra_paths, *git_writable],
        # The run's file input copies, read-only, even inside the data folder.
        visible=[input_files],
        protected=[input_files, *(run_git.protected() if run_git is not None else [])],
    )
    sandbox = settings["sandbox"]
    sandbox["autoAllowBashIfSandboxed"] = False
    if network == ANY_HOST:
        # Claude Code takes no wildcard in allowedDomains. A bare * in a WebFetch
        # domain rule allows every host for sandboxed commands (2.1.186 and later).
        sandbox["network"] = {"allowedDomains": [], "strictAllowlist": True}
        settings["permissions"]["allow"] = ["WebFetch(domain:*)"]
    else:
        sandbox["network"] = {"allowedDomains": list(network), "strictAllowlist": True}
    return settings


def prepare_step(
    subscription_id: str,
    configuration: Mapping[str, Any],
    settings: ResolvedProviderSettings,
    *,
    working_dir: Path,
    extra_paths: Iterable[Path],
    data_dir: Path,
    run_id: str,
    triggered: bool | None = None,
    platform: str | None = None,
) -> PreparedStep:
    """Apply the step's sandbox to one provider call, or raise ``SandboxError``."""
    import json

    extra_paths = list(extra_paths)
    refused = credential_extra_paths(extra_paths, data_dir)
    if refused:
        # Never handed to any provider, sandboxed or not.
        raise SandboxError(
            "RATTISH_SANDBOX_CREDENTIAL_PATH",
            "A step can't be given a credential folder: " + "; ".join(refused) + ".",
            "Remove it from the step's extra-paths.",
        )
    triggered = in_triggered_run() if triggered is None else triggered
    platform = platform or sys.platform
    problem = sandbox_problem(
        subscription_id, configuration, settings, triggered=triggered, platform=platform
    )
    if problem is not None:
        raise SandboxError(
            problem.code.replace("_PREFLIGHT", ""), problem.message, problem.suggestion
        )
    if sandbox_off(configuration) or unsandboxed_reason(
        subscription_id, configuration, settings, platform=platform
    ):
        return PreparedStep(settings)
    network = step_network(configuration)
    if subscription_id == "codex":
        if network == ANY_HOST:
            settings = settings.model_copy(
                update={"extra_args": [*settings.extra_args, "-c", CODEX_NETWORK_ON]}
            )
        return PreparedStep(settings, sandboxed=True)
    from gofer.ui import claude_sandbox

    scratch = run_scratch_dir(data_dir.resolve(), run_id)
    scratch.mkdir(parents=True, exist_ok=True)
    run_git = run_git_access(working_dir)
    if run_git is not None:
        run_git.create_folders()
    claude_settings = claude_step_settings(
        working_dir=working_dir,
        data_dir=data_dir,
        scratch=scratch,
        extra_paths=extra_paths,
        network=network,
        input_files=input_files_folder(),
        run_git=run_git,
    )
    settings = settings.model_copy(
        update={
            "extra_args": [
                *settings.extra_args,
                # A project's own Claude settings could name hooks, which run unsandboxed.
                *claude_sandbox.project_instruction_args(working_dir),
                "--settings",
                json.dumps(claude_settings, separators=(",", ":")),
            ]
        }
    )
    env = claude_sandbox.sandbox_environment(scratch, read_only=False)
    if run_git is not None:
        # The step's process gets the parent's environment with its own env on top.
        step_env = {str(k): str(v) for k, v in (configuration.get("env") or {}).items()}
        env.update(run_git_environment({**os.environ, **step_env}))
    return PreparedStep(
        settings,
        env=env,
        stub_root=working_dir,
        admin_stub_root=run_git.admin_dir if run_git is not None else None,
        sandboxed=True,
    )


@contextmanager
def mount_stub_cleanup(prepared: PreparedStep) -> Iterator[None]:
    """Remove the empty mount points Claude Code's sandbox may leave behind."""
    folders = [
        folder
        for folder in (prepared.stub_root, prepared.admin_stub_root)
        if folder is not None and folder.is_dir()
    ]
    if not folders:
        yield
        return
    from gofer.ui import claude_sandbox

    roots = [claude_sandbox.track_mount_stubs(folder) for folder in folders]
    try:
        yield
    finally:
        for root in roots:
            claude_sandbox.release_mount_stubs(root)


# ----------------------------------------------------------------------------
# Reports for preflight and the Triggers section


def choice_settings(
    choice: Mapping[str, Any], data_dir: Path
) -> tuple[str | None, ResolvedProviderSettings | None]:
    """A provider choice's runtime subscription and resolved profile settings."""
    from gofer.core.provider_profiles import resolve_provider_settings
    from gofer.rattish.provider_runtime import runtime_subscription_id

    try:
        subscription_id: str = runtime_subscription_id(choice["provider_id"])
    except (KeyError, ValueError):
        return None, None
    try:
        settings = resolve_provider_settings(
            agent_subscription=subscription_id,  # type: ignore[arg-type]
            profile_name=choice.get("profile"),
            operation_model=choice.get("model"),
            operation_effort=choice.get("effort"),
            data_dir=data_dir,
        )
    except (OSError, ValueError):
        return subscription_id, None
    return subscription_id, settings


def node_unsandboxed(
    node: Mapping[str, Any], data_dir: Path, *, platform: str | None = None
) -> list[dict[str, str]]:
    """Each provider in an agent step's chain that would run it unsandboxed, and why."""
    from gofer.rattish.provider_fallback import provider_choices

    if node.get("runtime_handler") != "raticode.agent":
        return []
    configuration = node.get("configuration") or {}
    found: list[dict[str, str]] = []
    for choice in provider_choices(node):
        subscription_id, settings = choice_settings(choice, data_dir)
        if subscription_id is None:
            continue
        reason = unsandboxed_reason(subscription_id, configuration, settings, platform=platform)
        if reason and not any(item["provider"] == choice["provider_id"] for item in found):
            found.append(
                {"node": str(node["id"]), "provider": choice["provider_id"], "reason": reason}
            )
    return found


def credential_extra_path(path: Path, data_dir: Path | None = None) -> str | None:
    """Why ``path`` can't be a step's extra path: it is, holds or sits in a credential folder.

    Raticode's own (account folders, paired-device keys, site grants, a headless
    secrets folder) and the home folder's (``~/.ssh``, ``~/.aws`` and the rest of
    ``agent_credentials.HOME_SECRET_PATHS``). None when it's none of those.
    """
    from gofer.core.agent_credentials import HOME_SECRET_PATHS, inside_credential_folder

    folder = inside_credential_folder(path, data_dir)
    if folder is not None:
        return f"{path} is or holds {folder}, where Raticode keeps credentials"
    target = Path(os.path.abspath(path.expanduser()))
    resolved = target.resolve()
    for secret in HOME_SECRET_PATHS:
        secret_path = Path(os.path.expanduser(secret))
        for candidate in (target, resolved):
            if candidate.is_relative_to(secret_path) or secret_path.is_relative_to(candidate):
                return f"{path} is or holds {secret_path}, where your credentials are kept"
    return None


def credential_extra_paths(extra_paths: Iterable[Path], data_dir: Path | None = None) -> list[str]:
    return [reason for path in extra_paths if (reason := credential_extra_path(path, data_dir))]


def node_widened(
    node: Mapping[str, Any], project_root: Path, data_dir: Path | None = None
) -> list[dict[str, str]]:
    """What an agent step's own settings let its shell reach beyond its project.

    Each extra path outside the project (which the shell may write) and ``network:
    any``. A workflow file sets both, so a cloned or edited workflow can widen its own
    sandbox; preflight warns and the Triggers section asks before a trigger runs it
    unattended. Steps that run unsandboxed are reported by ``node_unsandboxed`` instead.
    """
    from gofer.rattish.project_paths import project_path

    if node.get("runtime_handler") != "raticode.agent":
        return []
    configuration = node.get("configuration") or {}
    if sandbox_off(configuration):
        return []
    root = project_root.resolve()
    found: list[dict[str, str]] = []
    for authored in configuration.get("extra_paths") or []:
        if not isinstance(authored, str):
            continue
        try:
            path = project_path(root, authored)
        except ValueError:
            continue
        if path.resolve().is_relative_to(root):
            continue
        found.append(
            {
                "node": str(node["id"]),
                "kind": "path",
                "path": str(path),
                "reason": f"its shell can write {path}, outside the project",
            }
        )
    if step_network(configuration) == ANY_HOST:
        found.append(
            {"node": str(node["id"]), "kind": "network", "reason": "its shell can reach any host"}
        )
    return found


def widened_message(items: list[dict[str, str]]) -> str:
    return "Widens its sandbox: " + "; ".join(item["reason"] for item in items) + "."


def workflow_widened(ir: Mapping[str, Any], data_dir: Path | None = None) -> list[dict[str, str]]:
    """Every agent step of a compiled workflow whose own settings widen its sandbox."""
    root = Path(str((ir.get("source") or {}).get("project_root") or "."))
    found: list[dict[str, str]] = []
    for node in ir.get("nodes") or []:
        found.extend(node_widened(node, root, data_dir))
    return found


def unsandboxed_message(items: list[dict[str, str]]) -> str:
    parts = [f"{item['provider']} ({item['reason']})" for item in items]
    return "Runs unsandboxed: " + "; ".join(parts) + "."


def workflow_unsandboxed(
    ir: Mapping[str, Any], data_dir: Path, *, platform: str | None = None
) -> list[dict[str, str]]:
    """Every agent step and local MCP server of a compiled workflow that runs unsandboxed."""
    from gofer.rattish import mcp_tool

    found: list[dict[str, str]] = []
    for node in ir.get("nodes") or []:
        found.extend(node_unsandboxed(node, data_dir, platform=platform))
        found.extend(mcp_tool.node_unsandboxed(node, data_dir, platform=platform))
    return found
