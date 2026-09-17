"""Application-owned Git operations confined to a swarm assignment.

These permissions govern the managed tool, not arbitrary shell/network access.
Provider sandboxes remain in place; agents never need writable shared Git metadata.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from gofer.ui.swarm_workspaces import git


def permissions(value: Any = None) -> dict[str, bool]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"local", "remote"}:
        raise ValueError("gitPermissions must contain only local and remote booleans")
    result = {"local": value.get("local", True), "remote": value.get("remote", False)}
    if any(type(item) is not bool for item in result.values()):
        raise ValueError("Git permissions must be booleans")
    if result["remote"] and not result["local"]:
        raise ValueError("Remote Git requires local Git permission")
    return result


def _text(payload: dict[str, Any], key: str, maximum: int = 8000) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        raise ValueError(f"{key} must be nonempty text of at most {maximum} characters")
    return value


def execute(workspace: dict[str, str], settings: Any, payload: dict[str, Any]) -> dict[str, str]:
    policy = permissions(settings)
    operation = payload.get("operation")
    if not isinstance(operation, str) or operation not in {
        "status",
        "diff",
        "stage",
        "commit",
        "push",
        "create_pr",
    }:
        raise ValueError("Unknown managed Git operation")
    if not policy["local"]:
        raise ValueError("Local Git operations are disabled for this swarm run")
    if operation in {"push", "create_pr"} and not policy["remote"]:
        raise ValueError("Remote branches and pull requests are disabled for this swarm run")
    root = Path(workspace["path"]).resolve()
    branch = workspace.get("branch", "")
    if not (branch.startswith("taskurotta/swarm/")
            or re.fullmatch(r"raticode/swarm/[^/]+/attempt/[^/]+", branch)):
        raise ValueError("Managed Git requires an isolated swarm assignment branch")
    # Do not inherit a caller's alternate index, Git directory or config injection.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1")
    if git(root, "branch", "--show-current", env=env) != branch:
        raise ValueError("Assignment branch changed; refusing Git operation")
    if operation == "status":
        output = git(root, "status", "--short", env=env)
    elif operation == "diff":
        staged = payload.get("staged", False)
        if type(staged) is not bool:
            raise ValueError("staged must be a boolean")
        output = git(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            *(["--cached"] if staged else []),
            env=env,
        )
    elif operation == "stage":
        paths = payload.get("paths")
        if not isinstance(paths, list) or not 1 <= len(paths) <= 100:
            raise ValueError("Stage requires 1–100 relative paths")
        for name in paths:
            if not isinstance(name, str) or not name or "\0" in name or Path(name).is_absolute():
                raise ValueError("Stage paths must be relative to the assignment")
            path = root / name
            if ".." in Path(name).parts or ".git" in Path(name).parts:
                raise ValueError("Stage paths cannot access parent directories or Git metadata")
            # Git stages symlinks themselves; their parents must stay in the assignment.
            if path != root and not path.parent.resolve().is_relative_to(root):
                raise ValueError("Stage path is outside the assignment")
        output = git(root, "add", "--all", "--", *paths, env=env)
    elif operation == "commit":
        output = git(root, "commit", "-m", _text(payload, "message"), env=env)
    else:
        remote = payload.get("remote", "origin")
        if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
            raise ValueError("remote must name a configured Git remote")
        url = git(root, "remote", "get-url", remote, env=env)
        if operation == "push":
            # Publish only the assigned branch, never main, arbitrary refspecs or force.
            output = git(
                root,
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "push.followTags=false",
                "-c",
                f"remote.{remote}.mirror=false",
                "push",
                "--no-verify",
                "--recurse-submodules=no",
                remote,
                f"HEAD:refs/heads/{branch}",
                env=env,
            )
        else:
            # gh receives an explicit repository, head and base; no interactive prompts.
            push_urls = git(
                root, "remote", "get-url", "--push", "--all", remote, env=env
            ).splitlines()
            if push_urls != [url]:
                raise ValueError(
                    "Managed pull requests require one matching fetch and push URL; "
                    "fork or multiple push destinations are not supported"
                )
            match = re.fullmatch(
                r"(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?", url
            )
            if not match:
                raise ValueError("Managed pull requests currently require a GitHub remote")
            base = _text(payload, "base", 200)
            if base.startswith("-"):
                raise ValueError("Invalid pull request base branch")
            git(root, "check-ref-format", "--branch", base, env=env)
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    f"github.com/{match[1]}",
                    "--head",
                    branch,
                    "--base",
                    base,
                    "--title",
                    _text(payload, "title", 200),
                    "--body",
                    _text(payload, "body", 16000),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode:
                raise ValueError(result.stderr.strip() or "Pull request creation failed")
            output = result.stdout.strip()
    return {
        "operation": str(operation),
        "output": output[:32000],
        "revision": git(root, "rev-parse", "HEAD", env=env),
    }
