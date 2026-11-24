"""Raticode runs a project's decided check command itself before a writer task is accepted.

The command is the project's ``checks.fast`` decision (``rem_decisions``), found by a
project scan (``rem_project_scan``) and confirmed by the user, or provisional while
self-work's auto mode is on. A worker's own claim that it ran the checks isn't the proof:
Raticode runs the command itself (``run_check``) in a fresh worktree of the commit that
lands: a detached ``git worktree add`` of the task worktree's HEAD under the data folder's
``run-scratch/check-<id>``, with the links and copies of the project's approved worktree
setup made in it, removed afterwards. A file the worker forgot to commit isn't there, so
the check fails instead of passing on it. Where no fresh worktree can be made (no git, an
unreadable commit, a setup that fails, or one with commands, which would run the worker's
committed files outside the sandbox) it runs in the task's worktree and the result says
why (``ran_in``). A failure runs once more on the same commit, in another fresh worktree
(``may_rerun``): a pass then goes to review marked ``flaky`` and is saved as the
project's flake record (``note_flake``), and only a failure that repeats goes back to
the worker with the output before any reviewer turn.

The command runs only in the exact text the decision holds. Rem can't change that text
(only the user can), and a provisional one runs only while auto mode is on
(``rem_decisions.may_run``).

It runs on files an unattended worker just wrote, so it gets the same operating-system
sandbox as a workflow's local MCP servers (``gofer.rattish.mcp_tool``), with no network:
the whole file system is read-only except the check folder and a scratch folder that
holds its temporary files and caches; the credential folders in the home folder, a
headless secrets folder and Raticode's data folder are hidden; and it has no network.
On Linux that is bubblewrap, on macOS ``sandbox-exec``. Where the sandbox can't start
(native Windows, bubblewrap missing or refused), Raticode never runs the check
unsandboxed: ``sandbox_problem`` says why, and the task falls back to the worker's own
check receipts and the review.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.core import command_slots
from gofer.ui import worktree_setup
from gofer.utils.process import (
    NO_REPOSITORY_PROGRAMS,
    build_subprocess_env,
    git_push_blocked_environment,
    untrusted_repository_git_env,
)

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 1800
OUTPUT_CHARS = 6000
# Exit codes that say the command itself couldn't run (not found, not executable), as
# opposed to the work under check failing it. Only these count toward asking again.
BROKEN_COMMAND = frozenset({126, 127})

# One of Raticode's own checks at a time in this backend; the shared test slots also
# hold it back while agents' test runs fill them.
_running = threading.Semaphore(1)
# Under the data folder, which the sandbox hides; each check binds its own folder back.
SCRATCH = "run-scratch"


def head(folder: str | Path) -> str:
    """The folder's checked-out commit, or "" outside git."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", *NO_REPOSITORY_PROGRAMS, "-C", str(folder), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=untrusted_repository_git_env(folder, build_subprocess_env()),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def leftovers(folder: str | Path) -> set[str]:
    """Untracked and ignored paths in the folder, as git lists them (a whole ignored
    folder is one entry); empty outside git. Compared before and after a failed run, it
    shows what that run left for its rerun to find."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "git",
                *NO_REPOSITORY_PROGRAMS,
                "-C",
                str(folder),
                "status",
                "--porcelain",
                "-z",
                "--ignored",
                "--untracked-files=normal",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=untrusted_repository_git_env(folder, build_subprocess_env()),
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    return {
        entry[3:]
        for entry in result.stdout.split("\0")
        if entry.startswith(("?? ", "!! ")) and len(entry) > 3
    }


def sandbox_problem(
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    probe: Callable[[], str | None] | None = None,
) -> str | None:
    """Why Raticode can't run a check in its sandbox on this computer, or None.

    ``probe`` defaults to the MCP sandbox's own bubblewrap check, cached per process.
    """
    from gofer.rattish import mcp_tool

    platform = platform or sys.platform
    if platform == "darwin":
        if not Path("/usr/bin/sandbox-exec").is_file():
            return "macOS's sandbox-exec isn't on this Mac"
        return None
    if not platform.startswith("linux"):
        return "Raticode has no sandbox for its checks on this operating system"
    if which("bwrap") is None:
        return "bubblewrap (bwrap) isn't installed"
    return (probe or mcp_tool._bwrap_probe)()


# The real check, kept apart from ``sandbox_problem`` so tests can swap one and still
# test the other.
real_problem = sandbox_problem


def sandboxed_command(
    command: str,
    folder: Path,
    *,
    data_dir: Path,
    scratch: Path,
    platform: str | None = None,
) -> list[str]:
    """``command`` wrapped in the check's sandbox: no network, writes only to ``folder``
    and ``scratch``, credential folders and ``data_dir`` hidden."""
    from gofer.rattish import mcp_tool

    settings = mcp_tool.SandboxSettings(network=[], working_dir=folder)
    shell = ["/bin/sh", "-c", command]
    if (platform or sys.platform) == "darwin":
        profile = mcp_tool.seatbelt_profile(
            settings=settings, data_dir=data_dir, scratch=scratch, proxy_port=None
        )
        return ["/usr/bin/sandbox-exec", "-p", profile, *shell]
    return mcp_tool.bwrap_command(
        shell, settings=settings, data_dir=data_dir, scratch=scratch, proxy_dir=None
    )


def _environment(scratch: Path) -> dict[str, str]:
    env = build_subprocess_env()
    env.update(git_push_blocked_environment(env))
    # The sandbox hides the sockets these name; dropping them keeps tools from trying.
    for name in ("DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK", "GPG_AGENT_INFO", "WAYLAND_DISPLAY"):
        env.pop(name, None)
    temp = scratch / "tmp"
    caches = scratch / "cache"
    for path in (temp, caches):
        path.mkdir(parents=True, exist_ok=True)
    # Temporary files and tool caches go to the scratch folder: the rest is read-only.
    env.update(
        {
            "TMPDIR": str(temp),
            "TMP": str(temp),
            "TEMP": str(temp),
            "XDG_CACHE_HOME": str(caches / "xdg"),
            "npm_config_cache": str(caches / "npm"),
            "UV_CACHE_DIR": str(caches / "xdg" / "uv"),
        }
    )
    return env


def run(
    command: str,
    folder: str | Path,
    *,
    data_dir: Path,
    cancel: threading.Event | None = None,
    timeout: int = TIMEOUT_SECONDS,
    platform: str | None = None,
) -> dict[str, Any]:
    """Run ``command`` in ``folder`` itself inside the check sandbox; the result for the
    run record. ``run_check`` is what Rem calls: it tries a fresh worktree first.

    When the sandbox can't start the command doesn't run at all: the result has
    ``skipped`` with the reason and no ``passed``. The output kept is the tail, at most
    ``OUTPUT_CHARS`` characters.
    """
    problem = sandbox_problem(platform)
    if problem:
        return _skipped(command, folder, problem)
    with _running:
        box = _new_box(data_dir)
        try:
            return _run_in(
                command,
                folder,
                data_dir=data_dir,
                box=box,
                cancel=cancel,
                timeout=timeout,
                platform=platform,
            )
        finally:
            shutil.rmtree(box, ignore_errors=True)


def _skipped(command: str, folder: str | Path, problem: str) -> dict[str, Any]:
    return {
        "command": command,
        "folder": str(folder),
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "skipped": problem,
        "passed": False,
        "sandboxed": False,
    }


def _new_box(data_dir: Path) -> Path:
    """A new ``run-scratch/check-<id>`` path (not made yet) for one run of the check."""
    return data_dir / SCRATCH / f"{BOX_PREFIX}{uuid4().hex}"


def _run_in(
    command: str,
    folder: str | Path,
    *,
    data_dir: Path,
    box: Path,
    cancel: threading.Event | None,
    timeout: int,
    platform: str | None,
    ran_folder: Path | None = None,
) -> dict[str, Any]:
    """Run ``command`` in the sandbox in ``ran_folder`` (default ``folder``), with its
    temporary files in ``box``'s scratch folder. The caller holds ``_running``."""
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    root = (ran_folder or Path(folder)).resolve()
    base = {"command": command, "folder": str(folder), "ran_at": started_at}
    scratch = box / BOX_SCRATCH
    lease = None
    try:
        box.mkdir(parents=True, mode=0o700, exist_ok=True)
        scratch.mkdir(mode=0o700)
        env = _environment(scratch)
    except OSError as exc:
        return {**base, "skipped": f"no scratch folder for it: {exc}", "passed": False}
    try:
        argv = sandboxed_command(
            command, root, data_dir=data_dir, scratch=scratch, platform=platform
        )
        with contextlib.suppress(OSError, ValueError):
            lease = command_slots.acquire(
                command_slots.slots_root(data_dir),
                "test",
                command=command[:200],
                label="Raticode's project check",
                notify=lambda message: log.info("%s", message),
            )
        try:
            step = worktree_setup._run_command(
                shlex.join(argv), root, env, time.monotonic() + timeout, cancel
            )
        finally:
            if lease is not None:
                lease.release()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    code = step.get("exitCode")
    return {
        **base,
        "head": head(root),
        "exit_code": code,
        "passed": bool(step.get("ok")),
        "seconds": step.get("seconds"),
        "output": str(step.get("output") or "")[-OUTPUT_CHARS:],
        "detail": step.get("detail") or "",
        "sandboxed": True,
    }


# A fresh worktree for each run ---------------------------------------------------------

# ``run-scratch/check-<id>`` holds one run: the worktree, the sandbox's scratch folder
# and a note naming the repository the worktree belongs to. The sandbox can write only
# to the first two, so a check can't change the note the clean-up trusts.
BOX_PREFIX = "check-"
BOX_WORKTREE = "worktree"
BOX_SCRATCH = "scratch"
BOX_NOTE = "repository.json"
FRESH_WORKTREE = "fresh_worktree"
TASK_WORKTREE = "task_worktree"
GIT_TIMEOUT_SECONDS = 300
OVERLAY_BATCH = 100


class NoFreshWorktree(Exception):
    """Why the check runs in the task's worktree instead of a fresh one."""


def _git(folder: str | Path, *args: str, timeout: int = 30) -> str:
    """Run git on a repository an agent may have edited; its output, or NoFreshWorktree."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", *NO_REPOSITORY_PROGRAMS, "-C", str(folder), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=untrusted_repository_git_env(folder, build_subprocess_env()),
        )
    except FileNotFoundError as exc:
        raise NoFreshWorktree("git isn't installed") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise NoFreshWorktree(f"git failed: {exc}") from exc
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise NoFreshWorktree(lines[-1] if lines else f"git exited with {result.returncode}")
    return result.stdout.rstrip("\n")


def _make_fresh(
    folder: Path,
    box: Path,
    *,
    data_dir: Path,
    source: Path,
    cancel: threading.Event | None,
    commit: str | None = None,
    overlay: tuple[str, list[str]] | None = None,
) -> tuple[Path, str, str]:
    """A detached worktree of ``folder``'s HEAD (or ``commit``) in ``box`` with the
    project's approved setup run in it: the matching folder in it, the commit and the
    setup's summary.

    ``overlay`` is another commit and repository-relative paths: those files are taken
    from that commit into the worktree before the setup runs, as the task's proving test
    does with its test files on the base commit (``rem_proving_test``).
    """
    try:
        common = _git(folder, "rev-parse", "--path-format=absolute", "--git-common-dir")
        prefix = _git(folder, "rev-parse", "--show-prefix")
    except NoFreshWorktree as exc:
        raise NoFreshWorktree(f"the folder isn't in a git repository ({exc})") from exc
    try:
        commit = _git(folder, "rev-parse", "--verify", f"{commit or 'HEAD'}^{{commit}}")
    except NoFreshWorktree as exc:
        raise NoFreshWorktree(f"its commit can't be read ({exc})") from exc
    box.mkdir(parents=True, mode=0o700, exist_ok=True)
    tree = box / BOX_WORKTREE
    # Written before the worktree exists, so a crash at any point leaves what the sweep
    # needs to remove it.
    (box / BOX_NOTE).write_text(
        json.dumps({"git_dir": common, "worktree": str(tree), "commit": commit}),
        encoding="utf-8",
    )
    try:
        _git(folder, "worktree", "add", "--detach", str(tree), commit, timeout=GIT_TIMEOUT_SECONDS)
    except NoFreshWorktree as exc:
        raise NoFreshWorktree(f"git couldn't check out commit {commit[:12]} ({exc})") from exc
    if overlay is not None:
        other, paths = overlay
        for start in range(0, len(paths), OVERLAY_BATCH):
            # Literal paths from the top of the repository, never read as patterns.
            batch = [f":(top,literal){path}" for path in paths[start : start + OVERLAY_BATCH]]
            try:
                _git(tree, "checkout", other, "--", *batch, timeout=GIT_TIMEOUT_SECONDS)
            except NoFreshWorktree as exc:
                raise NoFreshWorktree(
                    f"git couldn't add files from commit {other[:12]} to commit "
                    f"{commit[:12]} ({exc})"
                ) from exc
    checkout = tree / prefix
    if not checkout.is_dir():
        raise NoFreshWorktree(f"commit {commit[:12]} has no {prefix.rstrip('/')} folder")
    # Only the setup's links and copies: its commands would run the worker's own files
    # (a setup script, package scripts) outside the sandbox before anyone reviewed them.
    setup = worktree_setup.prepare_quietly(
        data_dir,
        source=source,
        worktree=checkout,
        surface="check",
        cancel=cancel,
        commands=False,
    )
    if setup is not None and setup.get("status") == "commands_not_run":
        raise NoFreshWorktree(
            "the project's worktree setup runs commands, and Raticode never runs them "
            "outside the sandbox on work nobody has reviewed yet"
        )
    if setup is not None and setup.get("status") != "passed":
        raise NoFreshWorktree(
            "the project's worktree setup didn't finish: "
            + str(setup.get("summary") or setup.get("status") or "it failed")
        )
    return checkout, commit, str((setup or {}).get("summary") or "")


def _remove_box(box: Path) -> None:
    """Remove one run's folder and its worktree from the repository it belongs to."""
    note: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        found = json.loads((box / BOX_NOTE).read_text(encoding="utf-8"))
        note = found if isinstance(found, dict) else {}
    git_dir = str(note.get("git_dir") or "")
    tree = str(box / BOX_WORKTREE)
    removed = False
    if git_dir and Path(git_dir).is_dir():
        # The repository's own git folder, not the worktree: the check could write the
        # worktree's ``.git`` file, but not this note or that folder.
        with contextlib.suppress(NoFreshWorktree):
            _git(git_dir, "worktree", "remove", "--force", tree, timeout=GIT_TIMEOUT_SECONDS)
            removed = True
    shutil.rmtree(box, ignore_errors=True)
    if git_dir and Path(git_dir).is_dir() and not removed:
        # The folder went first (a crash, a removal git refused): drop its record, and
        # prune only when the repository still lists it.
        with contextlib.suppress(NoFreshWorktree):
            listed = _git(git_dir, "worktree", "list", "--porcelain").splitlines()
            if {f"worktree {tree}", f"worktree {os.path.realpath(tree)}"} & set(listed):
                _git(git_dir, "worktree", "prune")
    if box.exists():
        log.warning("Couldn't fully remove the project check folder %s", box)


def sweep(data_dir: Path) -> int:
    """Remove the ``check-*`` folders and worktrees earlier runs left (a cancel, a crash,
    a backend restart); how many. Waits for a check running in this backend to end."""
    with _running:
        return _sweep(data_dir)


def _sweep(data_dir: Path) -> int:
    root = data_dir / SCRATCH
    try:
        with os.scandir(root) as entries:
            boxes = [
                Path(entry.path)
                for entry in entries
                if entry.name.startswith(BOX_PREFIX) and entry.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return 0
    for box in sorted(boxes):
        _remove_box(box)
    if boxes:
        log.info("Removed %d leftover project check folder(s)", len(boxes))
    return len(boxes)


def start_sweep(data_dir: Path) -> threading.Thread:
    """``sweep`` in the background when the backend starts."""

    def work() -> None:
        try:
            sweep(data_dir)
        except Exception:  # noqa: BLE001 - a clean-up failure must not stop startup
            log.warning("Couldn't remove leftover project check folders", exc_info=True)

    thread = threading.Thread(target=work, name="project-check-sweep", daemon=True)
    thread.start()
    return thread


def run_check(
    command: str,
    folder: str | Path,
    *,
    data_dir: Path,
    source: str | Path | None = None,
    cancel: threading.Event | None = None,
    timeout: int = TIMEOUT_SECONDS,
    platform: str | None = None,
) -> dict[str, Any]:
    """Run the check on the commit in ``folder`` in a fresh worktree of it; the result.

    The worktree is a detached ``git worktree add`` of the folder's HEAD in
    ``run-scratch/check-<id>``; the links and copies of the project's approved worktree
    setup are made in it from ``source`` (the project folder, default ``folder``). A
    setup with commands isn't applied: they would run the worker's files outside the
    sandbox, so the check runs in ``folder`` instead. Then ``command`` runs there as
    ``run`` would, and the worktree is removed.
    The result has ``ran_in`` ``fresh_worktree``, ``head`` the commit and ``ran_folder``
    the folder it ran in. Where no fresh worktree can be made (outside git, an unreadable
    commit, a setup that fails or has commands) it runs in ``folder`` instead, with
    ``ran_in`` ``task_worktree`` and the reason in ``ran_in_reason``. Leftovers of earlier runs are
    swept first.
    """
    problem = sandbox_problem(platform)
    if problem:
        return _skipped(command, folder, problem)
    folder = Path(folder)
    with _running:
        _sweep(data_dir)
        box = _new_box(data_dir)
        try:
            try:
                checkout, commit, setup = _make_fresh(
                    folder.resolve(),
                    box,
                    data_dir=data_dir,
                    source=Path(source) if source else folder,
                    cancel=cancel,
                )
            except (NoFreshWorktree, OSError) as exc:
                _remove_box(box)
                reason = (
                    str(exc)
                    if isinstance(exc, NoFreshWorktree)
                    else f"no folder for a fresh worktree: {exc}"
                )
                if cancel is not None and cancel.is_set():
                    # Stopped while the worktree was made: nothing runs in its place.
                    return {
                        **_skipped(command, folder, "The run was cancelled"),
                        "sandboxed": True,
                        "detail": "The run was cancelled",
                        "ran_in": TASK_WORKTREE,
                        "ran_in_reason": reason,
                    }
                result = _run_in(
                    command,
                    folder,
                    data_dir=data_dir,
                    box=box,
                    cancel=cancel,
                    timeout=timeout,
                    platform=platform,
                )
                return {**result, "ran_in": TASK_WORKTREE, "ran_in_reason": reason}
            result = _run_in(
                command,
                folder,
                data_dir=data_dir,
                box=box,
                cancel=cancel,
                timeout=timeout,
                platform=platform,
                ran_folder=checkout,
            )
            if not result.get("skipped"):
                result.update(head=commit, ran_in=FRESH_WORKTREE, ran_folder=str(checkout))
                if setup:
                    result["setup"] = setup
            return result
        finally:
            _remove_box(box)


def broken(result: dict[str, Any]) -> bool:
    """Whether the failure says the command can't run at all, not that the work broke."""
    return not result.get("passed") and result.get("exit_code") in BROKEN_COMMAND


def may_rerun(result: dict[str, Any]) -> bool:
    """Whether a failed run is worth one more run on the same commit before any repair.

    Only a command that exited by itself with a failing code (not 126/127, which say it
    can't run at all). A run Raticode stopped (time limit, cancel) or couldn't start isn't
    rerun: another 30 minutes wouldn't tell a flaky test from a slow one.
    """
    code = result.get("exit_code")
    return (
        not result.get("passed")
        and not result.get("skipped")
        and isinstance(code, int)
        and not isinstance(code, bool)
        and code > 0
        and code not in BROKEN_COMMAND
    )


def reusable(result: dict[str, Any]) -> bool:
    """Whether a stored result may stand for a recheck of the same commit.

    A pass, a failure that repeated on a rerun of that commit (``confirmed``), or a command
    that can't run at all. A single failure from before reruns is run again.
    """
    return bool(result.get("passed") or result.get("confirmed") or broken(result))


def settle(first: dict[str, Any], again: dict[str, Any]) -> dict[str, Any]:
    """The result kept after a failed run and its rerun on the same commit.

    A pass is marked ``flaky`` and keeps the first run's output tail; a second failure is
    ``confirmed``, so only it goes back to the worker and is reused for that commit.
    """
    kept = {
        "first_exit_code": first.get("exit_code"),
        "first_seconds": first.get("seconds"),
        "first_output": str(first.get("output") or "")[-OUTPUT_CHARS:],
    }
    if first.get("ran_folder"):
        kept["first_ran_folder"] = first["ran_folder"]
    if again.get("passed"):
        return {**again, "flaky": True, **kept}
    return {**again, "confirmed": True, **kept}


def flaky_touches(
    tests: list[str], changed: list[str], output: str = "", folder: str = ""
) -> list[str]:
    """The files this change edited that a flaky run's first failure points at.

    A failing test's own file (``tests/x.py`` of ``tests/x.py::test_y``) counts, and so
    does an edited file the failing output names, as a traceback does; paths inside
    ``folder`` (the check's folder) are read relative to it, so ``clock.py`` doesn't match
    ``src/clock.py``. A flake there may be a race the change itself brought in, so the
    reviewer gets these by name instead of a plain pass. In ``changed`` order, once each.
    """
    if folder:
        output = output.replace(folder.rstrip("/") + "/", " ")
    own = {test.split("::", 1)[0] for test in tests}
    found = []
    for path in changed:
        named = path and re.search(
            rf"(?:^|[\s\"'(]){re.escape(path)}(?=[:\s\"'),]|$)", output, re.MULTILINE
        )
        if path in own or named:
            found.append(path)
    return list(dict.fromkeys(found))


# Per-project flake records, in Rem's settings: what the guardrail retro reads to turn a
# test that keeps flaking into a "stabilize this test" proposal.
FLAKES_SETTING = "check_flakes"
FLAKES_KEPT = 100
FLAKES_DAYS = 30


def note_flake(
    db: Any,
    store: Any,
    project: str,
    *,
    run_id: str,
    result: dict[str, Any],
    tests: list[str],
    clock: datetime | None = None,
) -> dict[str, Any]:
    """Save one flaky check run under its project; the newest ``FLAKES_KEPT`` stay.

    ``tests`` are the failing test names in the first run's output.
    """
    moment = clock or datetime.now(UTC)
    book = store.setting(db, FLAKES_SETTING) or {}
    found = book.get("projects")
    projects: dict[str, Any] = found if isinstance(found, dict) else {}
    oldest = moment.timestamp() - FLAKES_DAYS * 86400
    entry = {
        "run_id": run_id,
        "at": moment.isoformat(timespec="seconds"),
        "command": result.get("command") or "",
        "commit": result.get("head") or "",
        "tests": list(dict.fromkeys(t for t in tests if t))[:20],
    }
    kept = []
    for item in projects.get(project) or []:
        try:
            at = datetime.fromisoformat(str(item.get("at")))
        except (AttributeError, ValueError):
            continue
        if (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp() >= oldest:
            kept.append(item)
    projects[project] = [*kept, entry][-FLAKES_KEPT:]
    store.set_setting(db, FLAKES_SETTING, {**book, "projects": projects})
    return entry


# How that feedback starts. A ticket's timeline keeps it with the repair it caused, which is
# how Usage › Verified work tells a failed check from a review's request (rem_verified_work).
FEEDBACK_START = "Raticode ran this project's check ("


def feedback(result: dict[str, Any], decision_id: str) -> str:
    """What the worker is told when Raticode's run of the check failed."""
    why = result.get("detail") or f"exited with code {result.get('exit_code')}"
    tail = str(result.get("output") or "").strip()
    fresh = result.get("ran_in") == FRESH_WORKTREE
    where = (
        f"a fresh worktree of commit {str(result.get('head') or '')[:12]} from {result['folder']}"
        if fresh
        else str(result["folder"])
    )
    untracked = (
        " It ran on the committed files only: a file you didn't commit isn't there, so "
        "commit every file the work needs."
        if fresh
        else ""
    )
    return (
        f"{FEEDBACK_START}{decision_id}) itself in {where} "
        f"and it failed: `{result['command']}` {why}.{untracked} Fix the work so the check "
        "passes, commit, land it again if the task lands, and report the actual result. Don't "
        "change or skip the check to get around it; only the user can change the decided "
        "command." + (f"\nLast lines of its output:\n{tail}" if tail else "")
    )


REVIEW_GUIDANCE = (
    "raticode_check is a receipt Raticode produced itself: after the worker finished, it "
    "ran the project's decided check in its own sandbox on the commit shown, and the "
    "worker can't write or change this record. Trust it over the worker's receipts and "
    "summary. ran_in fresh_worktree means it ran in a new worktree of that commit, so only "
    "committed files were there; task_worktree means it ran in the worker's own folder, "
    "where files the worker never committed could have helped it pass, and ran_in_reason "
    "says why. A pass proves that command passed on that commit, and a worker receipt or "
    "claim that contradicts it is wrong. It proves nothing the command doesn't test, so "
    "still check each criterion yourself. flaky true means the first run failed and a "
    "rerun of the same commit passed; it counts as a pass, and Raticode records the flaky "
    "test separately. flaky_in_changed_files lists files this change edited that the "
    "first failure pointed at (a failing test's file, or a file its output names): the "
    "flake may be a race the change brought in, so report a should_fix finding on the "
    "standards axis that names them, unless the code shows the change can't cause it. "
    "first_run_output_tail is that failed first run, and first_run_left the untracked or "
    "ignored files it left in the folder: a pass that only came after the first run left a "
    "file the code then reads (a marker, a cache it writes itself) isn't proof, so report it "
    "as a blocker unless the file is plainly a tool's own cache or log.\n"
)


def receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Raticode's own run of the check as the reviewer sees it, beside the worker's receipts.

    The output is the stored tail (at most ``OUTPUT_CHARS``), so a test named there can be
    found by the reviewer.
    """
    return {
        "produced_by": "Raticode, not the worker",
        "decision_id": result.get("decision_id") or "",
        "command": result["command"],
        "folder": result["folder"],
        "commit": result.get("head") or "",
        "ran_in": result.get("ran_in") or TASK_WORKTREE,
        "ran_in_reason": result.get("ran_in_reason") or "",
        "exit_code": result.get("exit_code"),
        "passed": bool(result.get("passed")),
        "flaky": bool(result.get("flaky")),
        "flaky_in_changed_files": list(result.get("flaky_touches") or []),
        # The failed first run as it happened: its output and the files it left in the
        # folder, which the rerun could read.
        "first_run_output_tail": str(result.get("first_output") or "")
        if result.get("flaky")
        else "",
        "first_run_left": list(result.get("first_run_left") or []),
        "seconds": result.get("seconds"),
        "ran_at": result.get("ran_at") or "",
        "summary": result.get("summary") or summary(result),
        "output_tail": str(result.get("output") or ""),
    }


def summary(result: dict[str, Any]) -> str:
    """One line for the reviewer and the run's record."""
    state = "passed" if result.get("passed") else "failed"
    note = ""
    if result.get("flaky"):
        note = " on a rerun, after the first run on the same commit failed (flaky)"
        touched = list(result.get("flaky_touches") or [])
        if touched:
            note += (
                "; the first failure pointed at "
                + ", ".join(touched[:5])
                + (f" and {len(touched) - 5} more" if len(touched) > 5 else "")
                + ", which this change edited"
            )
    elif result.get("confirmed"):
        note = " again on a rerun of the same commit"
    commit = str(result.get("head") or "")[:12]
    if result.get("ran_in") == FRESH_WORKTREE:
        where = f"a fresh worktree of commit {commit} from {result['folder']}"
    else:
        where = f"{result['folder']} at {commit or 'no commit'}"
        if result.get("ran_in_reason"):
            where += f" (not in a fresh worktree: {result['ran_in_reason']})"
    return (
        f"Raticode ran `{result['command']}` in its sandbox in {where}: {state} in "
        f"{result.get('seconds')} s{note}."
    )
