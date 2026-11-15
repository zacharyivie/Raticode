"""Project setup for the worktrees Rem, Organizations and Swarms create.

A project describes its setup in ``.raticode/worktree-setup.json``, committed with the
project: a command to run, files and folders to link or copy from the project folder,
and optional overrides per operating system. A repository could carry a harmful
command, so setup only runs in a version the user approved. Approvals are stored per
project, outside the repository, together with the exact text approved. When the file
changes, unattended runs keep using the last approved text until the user approves the
new one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from gofer.utils.process import (
    NO_REPOSITORY_PROGRAMS,
    build_subprocess_env,
    untrusted_repository_git_env,
)

CONFIG_PATH = ".raticode/worktree-setup.json"
APPROVALS_FILE = "worktree-setup-approvals.json"
MAX_CONFIG_BYTES = 64 * 1024
MAX_ENTRIES = 50
MAX_COMMANDS = 20
MAX_COMMAND_CHARS = 8000
DEFAULT_TIMEOUT_SECONDS = 900
MAX_TIMEOUT_SECONDS = 7200
OUTPUT_TAIL_CHARS = 6000
MAX_PENDING = 50
PLATFORMS = ("linux", "macos", "windows")
_KEYS = {"setup", "link", "copy", "timeoutSeconds"}
_TOP_KEYS = _KEYS | {"os", "description", "$schema"}

_lock = threading.Lock()


class SetupConfigError(ValueError):
    pass


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Configuration ---------------------------------------------------------------------


def _relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise SetupConfigError(f"Each {field} entry must be a relative path")
    text = value.strip().replace("\\", "/").rstrip("/")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or (len(text) > 1 and text[1] == ":")
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] == ".git"
        or any(ord(c) < 32 for c in text)
    ):
        raise SetupConfigError(
            f"{field} entry {value!r} must stay inside the project: a relative path "
            "without '..' that isn't in .git"
        )
    return text


def _section(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SetupConfigError(f"{where} must be a JSON object")
    allowed = _TOP_KEYS if where == "The setup file" else _KEYS
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SetupConfigError(f"{where} has unknown field(s): {', '.join(unknown)}")
    section: dict[str, Any] = {}
    if "setup" in raw:
        setup = raw["setup"]
        commands = [setup] if isinstance(setup, str) else setup
        if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
            raise SetupConfigError("setup must be a command or a list of commands")
        commands = [c for c in commands if c.strip()]
        if len(commands) > MAX_COMMANDS or any(len(c) > MAX_COMMAND_CHARS for c in commands):
            raise SetupConfigError(
                f"setup allows up to {MAX_COMMANDS} commands of {MAX_COMMAND_CHARS} characters"
            )
        section["setup"] = commands
    for field in ("link", "copy"):
        if field in raw:
            entries = raw[field]
            if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
                raise SetupConfigError(f"{field} must be a list of up to {MAX_ENTRIES} paths")
            section[field] = [_relative_path(entry, field) for entry in entries]
    if "timeoutSeconds" in raw:
        timeout = raw["timeoutSeconds"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= MAX_TIMEOUT_SECONDS
        ):
            raise SetupConfigError(
                f"timeoutSeconds must be a whole number from 1 to {MAX_TIMEOUT_SECONDS}"
            )
        section["timeoutSeconds"] = timeout
    return section


def parse_config(text: str) -> dict[str, Any]:
    """Validate the setup file's text and return its normalized form."""
    if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise SetupConfigError(f"The setup file is larger than {MAX_CONFIG_BYTES // 1024} KB")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SetupConfigError(f"The setup file isn't valid JSON: {exc}") from exc
    config = _section(raw, "The setup file")
    overrides = raw.get("os", {})
    if not isinstance(overrides, dict):
        raise SetupConfigError("os must map linux, macos or windows to overrides")
    unknown = sorted(set(overrides) - set(PLATFORMS))
    if unknown:
        raise SetupConfigError(
            f"os has unknown platform(s): {', '.join(unknown)}. Use linux, macos or windows"
        )
    config["os"] = {name: _section(value, f"os.{name}") for name, value in overrides.items()}
    return config


def effective_config(config: dict[str, Any], platform: str | None = None) -> dict[str, Any]:
    """The settings for one operating system: its override replaces the fields it sets."""
    platform = platform or current_platform()
    merged = {key: config[key] for key in _KEYS if key in config}
    merged.update(config.get("os", {}).get(platform, {}))
    return {
        "platform": platform,
        "setup": list(merged.get("setup", [])),
        "link": list(merged.get("link", [])),
        "copy": list(merged.get("copy", [])),
        "timeoutSeconds": int(merged.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)),
    }


def read_file(folder: Path | str) -> dict[str, Any] | None:
    """The setup file in a folder: its text, hash and parsed form, or None when absent."""
    path = Path(folder) / CONFIG_PATH
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_CONFIG_BYTES:
            return {
                "path": str(path),
                "text": "",
                "sha256": "",
                "error": f"The setup file is larger than {MAX_CONFIG_BYTES // 1024} KB",
            }
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"path": str(path), "text": "", "sha256": "", "error": str(exc)}
    entry: dict[str, Any] = {"path": str(path), "text": text, "sha256": sha256_text(text)}
    try:
        entry["config"] = parse_config(text)
    except SetupConfigError as exc:
        entry["error"] = str(exc)
    return entry


# Project identity and approvals ----------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *NO_REPOSITORY_PROGRAMS, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=untrusted_repository_git_env(cwd, build_subprocess_env()),
    )
    if result.returncode:
        raise OSError(result.stderr.strip() or "git failed")
    return result.stdout.strip()


def project_key(project: Path | str) -> str:
    """One name for a project across all of its worktrees.

    It is the repository's shared git folder plus the project's place inside the
    repository, so approving in the main checkout also covers worktrees of it.
    """
    folder = Path(project).resolve()
    try:
        common = Path(_git(folder, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        top = Path(_git(folder, "rev-parse", "--show-toplevel")).resolve()
        inside = folder.relative_to(top).as_posix()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return f"folder:{folder}"
    return f"git:{common.resolve()}#{'' if inside == '.' else inside}"


class Approvals:
    """The user's setup approvals, one per project, in the data folder (mode 600)."""

    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / APPROVALS_FILE

    def _read(self) -> dict[str, Any]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "projects": {}, "pending": {}}
        if not isinstance(document, dict) or document.get("version") != 1:
            return {"version": 1, "projects": {}, "pending": {}}
        document.setdefault("projects", {})
        document.setdefault("pending", {})
        return document

    def _write(self, document: dict[str, Any]) -> None:
        from gofer.ui.headless import write_private_file

        write_private_file(self.path, json.dumps(document, indent=2, sort_keys=True))

    def get(self, key: str) -> dict[str, Any] | None:
        with _lock:
            record = self._read()["projects"].get(key)
        return dict(record) if isinstance(record, dict) else None

    def pending(self) -> list[dict[str, Any]]:
        with _lock:
            document = self._read()
        return sorted(
            (
                {"key": key, **value}
                for key, value in document["pending"].items()
                if isinstance(value, dict)
            ),
            key=lambda item: str(item.get("seenAt") or ""),
            reverse=True,
        )

    def pending_for(self, key: str) -> dict[str, Any] | None:
        with _lock:
            value = self._read()["pending"].get(key)
        return dict(value) if isinstance(value, dict) else None

    def note_pending(self, key: str, project: str, text: str, digest: str, source: str) -> None:
        with _lock:
            document = self._read()
            pending = document["pending"]
            pending[key] = {
                "projectPath": project,
                "sha256": digest,
                "text": text,
                "seenAt": _now(),
                "source": source,
            }
            if len(pending) > MAX_PENDING:
                for old in sorted(pending, key=lambda k: str(pending[k].get("seenAt")))[
                    : len(pending) - MAX_PENDING
                ]:
                    pending.pop(old, None)
            self._write(document)

    def approve(self, key: str, project: str, text: str) -> dict[str, Any]:
        parse_config(text)
        record = {
            "projectPath": project,
            "sha256": sha256_text(text),
            "text": text,
            "approvedAt": _now(),
        }
        with _lock:
            document = self._read()
            document["projects"][key] = record
            if document["pending"].get(key, {}).get("sha256") == record["sha256"]:
                document["pending"].pop(key, None)
            self._write(document)
        return record

    def revoke(self, key: str) -> bool:
        with _lock:
            document = self._read()
            removed = document["projects"].pop(key, None) is not None
            document["pending"].pop(key, None)
            if removed:
                self._write(document)
        return removed


def status(data_dir: Path | str, project: Path | str) -> dict[str, Any]:
    """What the app shows for a project: its setup file and whether it's approved."""
    folder = Path(project)
    if not folder.is_dir():
        raise ValueError(f"Project folder not found: {project}")
    key = project_key(folder)
    approvals = Approvals(data_dir)
    current = read_file(folder)
    approved = approvals.get(key)
    pending = approvals.pending_for(key)
    if pending and approved and pending.get("sha256") == approved.get("sha256"):
        pending = None
    if current is None:
        state = "none"
    elif current.get("error"):
        state = "invalid"
    elif approved and approved.get("sha256") == current["sha256"]:
        state = "approved"
    elif approved:
        state = "changed"
    else:
        state = "unapproved"
    file: dict[str, Any] | None = None
    if current is not None:
        file = {k: current[k] for k in ("path", "text", "sha256", "error") if k in current}
        if current.get("config"):
            file["effective"] = effective_config(current["config"])
    if pending and current and pending.get("sha256") == current.get("sha256"):
        pending = None
    if pending:
        try:
            pending["effective"] = effective_config(parse_config(str(pending.get("text"))))
        except SetupConfigError as exc:
            pending["error"] = str(exc)
    return {
        "project": str(folder),
        "key": key,
        "configPath": CONFIG_PATH,
        "platform": current_platform(),
        "state": state,
        "file": file,
        "approved": approved,
        "pending": pending,
    }


def approve(data_dir: Path | str, project: Path | str, digest: str) -> dict[str, Any]:
    """Approve the setup text whose hash the user reviewed: the project's file or the
    version a new worktree found."""
    folder = Path(project)
    if not folder.is_dir():
        raise ValueError(f"Project folder not found: {project}")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("sha256 must be the hash of the setup you reviewed")
    key = project_key(folder)
    approvals = Approvals(data_dir)
    candidates = [read_file(folder), approvals.pending_for(key)]
    text = next(
        (
            str(item["text"])
            for item in candidates
            if item and item.get("sha256") == digest and item.get("text")
        ),
        None,
    )
    if text is None:
        raise ValueError("The setup changed since you reviewed it. Review it again.")
    try:
        approvals.approve(key, str(folder), text)
    except SetupConfigError as exc:
        raise ValueError(str(exc)) from exc
    return status(data_dir, folder)


def revoke(data_dir: Path | str, project: Path | str) -> dict[str, Any]:
    folder = Path(project)
    if not folder.is_dir():
        raise ValueError(f"Project folder not found: {project}")
    Approvals(data_dir).revoke(project_key(folder))
    return status(data_dir, folder)


# Running setup ---------------------------------------------------------------------


def _ignored(worktree: Path, relative: str) -> bool:
    try:
        result = subprocess.run(
            ["git", *NO_REPOSITORY_PROGRAMS, "-C", str(worktree), "check-ignore", "-q", relative],
            capture_output=True,
            timeout=30,
            check=False,
            env=untrusted_repository_git_env(worktree, build_subprocess_env()),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _place(kind: str, relative: str, source: Path, worktree: Path) -> dict[str, Any]:
    step: dict[str, Any] = {"kind": kind, "path": relative, "ok": False}
    origin = source / relative
    target = worktree / relative
    if not origin.exists() and not origin.is_symlink():
        step["detail"] = f"{relative} doesn't exist in the project folder"
        return step
    if target.exists() or target.is_symlink():
        step.update(ok=True, skipped=True, detail="Already in the worktree; left as it is")
        return step
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "link":
            target.symlink_to(origin.resolve(), target_is_directory=origin.is_dir())
        elif origin.is_dir():
            shutil.copytree(origin, target, symlinks=True)
        else:
            shutil.copy2(origin, target, follow_symlinks=False)
    except OSError as exc:
        _remove(target)
        step["detail"] = f"Couldn't {kind} {relative}: {exc}"
        return step
    if not _ignored(worktree, relative):
        # A worker's changes are committed with ``git add -A``; an unignored link or copy
        # (an .env file, say) would land on the branch.
        _remove(target)
        step["detail"] = (
            f"{relative} isn't ignored by git, so it would be committed with the worktree's "
            "changes. Add it to .gitignore (for a folder link, without a trailing slash)."
        )
        return step
    step["ok"] = True
    return step


def _kill(process: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        process.kill()


def _run_command(
    command: str,
    worktree: Path,
    env: dict[str, str],
    deadline: float,
    cancel: threading.Event | None,
) -> dict[str, Any]:
    started = time.monotonic()
    step: dict[str, Any] = {"kind": "command", "command": command, "ok": False}
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=worktree,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=sys.platform != "win32",
        )
    except OSError as exc:
        step["detail"] = f"Couldn't start the command: {exc}"
        return step
    chunks: list[str] = []
    size = 0

    def read() -> None:
        nonlocal size
        assert process.stdout is not None
        for line in process.stdout:
            chunks.append(line)
            size += len(line)
            while size > OUTPUT_TAIL_CHARS * 2 and len(chunks) > 1:
                size -= len(chunks.pop(0))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    stopped = ""
    while process.poll() is None:
        if cancel is not None and cancel.is_set():
            stopped = "The run was cancelled during setup"
        elif time.monotonic() > deadline:
            stopped = "Setup ran past its time limit"
        if stopped:
            _kill(process)
            break
        time.sleep(0.2)
    process.wait()
    reader.join(timeout=5)
    output = "".join(chunks)[-OUTPUT_TAIL_CHARS:]
    step.update(
        exitCode=process.returncode,
        seconds=round(time.monotonic() - started, 1),
        output=output,
        ok=not stopped and process.returncode == 0,
    )
    if stopped:
        step["detail"] = stopped
    elif process.returncode:
        step["detail"] = f"Exited with code {process.returncode}"
    return step


def apply(
    settings: dict[str, Any],
    source: Path,
    worktree: Path,
    cancel: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """Link, copy, then run the setup commands; stop at the first failed command."""
    steps: list[dict[str, Any]] = []
    for kind in ("link", "copy"):
        for relative in settings[kind]:
            steps.append(_place(kind, relative, source, worktree))
    env = build_subprocess_env(
        {
            "RATICODE_WORKTREE_SETUP": "1",
            "RATICODE_PROJECT_ROOT": str(source),
            "RATICODE_WORKTREE_PATH": str(worktree),
        }
    )
    deadline = time.monotonic() + settings["timeoutSeconds"]
    for command in settings["setup"]:
        step = _run_command(command, worktree, env, deadline, cancel)
        steps.append(step)
        if not step["ok"]:
            break
    return steps


def prepare(
    data_dir: Path | str,
    *,
    source: Path | str,
    worktree: Path | str,
    surface: str,
    cancel: threading.Event | None = None,
) -> dict[str, Any] | None:
    """Run a project's approved setup in a worktree it just got.

    ``source`` is the project folder the worktree was made from (where ignored files such
    as installed packages live) and ``worktree`` the matching folder in the new checkout.
    Returns None when the project has no setup file, otherwise a result for the run.
    """
    source, worktree = Path(source), Path(worktree)
    # The committed file the new checkout got, not uncommitted edits in the project folder.
    found = read_file(worktree)
    if found is None:
        return None
    started = time.monotonic()
    key = project_key(source)
    approvals = Approvals(data_dir)
    approved = approvals.get(key)
    result: dict[str, Any] = {
        "projectPath": str(source),
        "worktreePath": str(worktree),
        "configPath": CONFIG_PATH,
        "sha256": found["sha256"],
        "platform": current_platform(),
        "startedAt": _now(),
        "steps": [],
    }
    if approved and approved.get("sha256") == found["sha256"] and found.get("config"):
        config, result["version"] = found["config"], "current"
    else:
        if found.get("text"):
            approvals.note_pending(key, str(source), found["text"], found["sha256"], surface)
        if not approved:
            reason = (
                f"its setup file is invalid: {found['error']}"
                if found.get("error")
                else "you haven't approved this project's worktree setup yet"
            )
            result.update(
                status="needs_approval",
                summary=f"Project setup didn't run: {reason}. Review it in the project's "
                "setup to approve it.",
            )
            return result
        try:
            config = parse_config(str(approved.get("text")))
        except SetupConfigError as exc:
            result.update(status="failed", summary=f"The approved project setup is invalid: {exc}")
            return result
        result["version"] = "approved-earlier"
        result["approvedSha256"] = approved.get("sha256")
        result["approvedAt"] = approved.get("approvedAt")
    settings = effective_config(config)
    try:
        result["steps"] = apply(settings, source, worktree, cancel)
    except Exception as exc:  # setup must never take down the run that asked for it
        result["steps"].append({"kind": "error", "ok": False, "detail": str(exc)})
    result["seconds"] = round(time.monotonic() - started, 1)
    failed = next((step for step in result["steps"] if not step["ok"]), None)
    changed = (
        " The setup file changed and isn't approved yet, so the version you approved "
        f"on {str(result.get('approvedAt') or '')[:10]} ran."
        if result["version"] == "approved-earlier"
        else ""
    )
    if failed:
        what = failed.get("command") or failed.get("path") or "setup"
        result.update(
            status="failed",
            summary=f"Project setup failed at {what}: {failed.get('detail', 'it failed')}."
            + changed,
        )
    else:
        result.update(
            status="passed",
            summary=f"Project setup finished in {result['seconds']:g}s." + changed,
        )
    return result


def prepare_quietly(
    data_dir: Path | str,
    *,
    source: Path | str,
    worktree: Path | str,
    surface: str,
    cancel: threading.Event | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """``prepare`` that reports its own crash as a failed result instead of raising."""
    try:
        return prepare(data_dir, source=source, worktree=worktree, surface=surface, cancel=cancel)
    except Exception as exc:
        if log:
            log(f"Worktree setup crashed: {exc}")
        return {
            "status": "failed",
            "summary": f"Project setup couldn't run: {exc}.",
            "projectPath": str(source),
            "worktreePath": str(worktree),
            "steps": [],
        }
