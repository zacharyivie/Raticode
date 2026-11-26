"""Raticode's own git commands in checkouts agents can edit run no repository-named programs.

A worker can write ``.git/config`` and ``.gitattributes`` in its checkout. Clean, smudge
and process filters, textconv and external diff drivers, merge drivers and hooks named
there would otherwise run outside any sandbox when the backend stages, commits, checks
out, diffs or reads status (CWE-78). Scratch repositories only.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from gofer.rattish import run_worktrees
from gofer.ui import organization_repository, swarm_workspaces, ticket_patch
from gofer.ui.rem_home_worktree import ensure_worktree, release_worktree
from gofer.utils.process import (
    NO_REPOSITORY_PROGRAMS,
    run_repository_git,
    untrusted_repository_git_env,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def run(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


def plant(repo: Path, marker: Path) -> None:
    """Name a program in every repository-scoped setting git runs on file contents."""
    script = repo.parent / "planted.sh"
    script.write_text(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
    script.chmod(0o755)
    for key in (
        "filter.evil.clean",
        "filter.evil.smudge",
        "diff.evil.textconv",
        "diff.evil.command",
        "merge.evil.driver",
    ):
        run(repo, "config", key, str(script))
    run(repo, "config", "filter.evil.required", "true")
    (repo / ".gitattributes").write_text("* filter=evil diff=evil merge=evil\n")


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-q", "-b", "main")
    run(repo, "config", "user.name", "Test")
    run(repo, "config", "user.email", "test@example.invalid")
    (repo / "a.txt").write_text("one\n")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-q", "-m", "first")
    return repo


def settings(env: dict[str, str]) -> dict[str, str]:
    return {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(env.get("GIT_CONFIG_COUNT", "0")))
    }


def test_overrides_blank_repository_commands_but_keep_git_lfs(tmp_path):
    repo = repository(tmp_path)
    plant(repo, tmp_path / "marker")
    run(repo, "config", "filter.lfs.clean", "git-lfs clean -- %f")

    found = settings(untrusted_repository_git_env(repo, {}))

    assert found == {
        "filter.evil.clean": "",
        "filter.evil.smudge": "",
        "filter.evil.required": "false",
        "diff.evil.textconv": "cat",
        "diff.evil.command": "true",
        "merge.evil.driver": "false",
    }


def test_overrides_follow_existing_environment_settings(tmp_path):
    repo = repository(tmp_path)
    plant(repo, tmp_path / "marker")
    env = untrusted_repository_git_env(
        repo,
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "url.x.pushInsteadOf",
            "GIT_CONFIG_VALUE_0": "",
        },
    )
    assert env["GIT_CONFIG_KEY_0"] == "url.x.pushInsteadOf"
    assert settings(env)["filter.evil.clean"] == ""


def test_a_replace_ref_never_swaps_the_files_of_the_commit_raticode_checks(tmp_path):
    """Nightly audit, 25 November: a worker that can write the shared ``.git`` ran
    ``git replace X Y``, so a fresh worktree of its failing commit X held Y's passing
    files, the project check passed on "X" and X landed."""
    repo = repository(tmp_path)
    (repo / "check.sh").write_text("exit 1\n")
    run(repo, "add", "check.sh")
    run(repo, "commit", "-qm", "broken work")
    broken = run(repo, "rev-parse", "HEAD").strip()
    (repo / "check.sh").write_text("exit 0\n")
    run(repo, "commit", "-qam", "passing stand-in")
    stand_in = run(repo, "rev-parse", "HEAD").strip()
    run(repo, "replace", broken, stand_in)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "grafts").write_text(f"{broken} {stand_in}\n")
    # Plain git now shows the stand-in's files for the broken commit.
    assert run(repo, "show", f"{broken}:check.sh") == "exit 0\n"
    env = untrusted_repository_git_env(repo, dict(os.environ))
    fresh = tmp_path / "fresh"
    subprocess.run(
        [
            "git",
            *NO_REPOSITORY_PROGRAMS,
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "--detach",
            str(fresh),
            broken,
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    assert (fresh / "check.sh").read_text() == "exit 1\n"
    shown = subprocess.run(
        ["git", *NO_REPOSITORY_PROGRAMS, "-C", str(repo), "show", f"{broken}:check.sh"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout
    assert shown == "exit 1\n"


def test_a_repository_without_commands_adds_no_settings(tmp_path):
    repo = repository(tmp_path)
    assert settings(untrusted_repository_git_env(repo, {})) == {}


def test_a_subsection_with_an_equals_sign_is_still_blocked(tmp_path):
    # Review of 49b224e4: git splits ``-c key=value`` at the first "=", so overriding
    # filter."x=y".clean on the command line set a different key and the filter ran.
    repo = repository(tmp_path)
    marker = tmp_path / "marker"
    script = tmp_path / "planted.sh"
    script.write_text(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
    script.chmod(0o755)
    run(repo, "config", "filter.x=y.clean", str(script))
    (repo / ".gitattributes").write_text("* filter=x=y\n")
    (repo / "a.txt").write_text("changed\n")
    worktree = {
        "checkout": str(repo),
        "root": str(repo),
        "repository": str(repo),
        "branch": "main",
        "base": "",
    }

    assert settings(untrusted_repository_git_env(repo, {}))["filter.x=y.clean"] == ""
    organization_repository.git(repo, "add", "-A")
    swarm_workspaces.git(repo, "status", "--porcelain")
    assert not marker.exists(), marker.read_text()
    # release_worktree stages and commits with ``git add -A``, then removes the checkout;
    # point it at a throwaway checkout of the same repository.
    checkout = tmp_path / "data" / "rem-worktrees" / "eq"
    run(repo, "worktree", "add", "-q", "-b", "rem/eq", str(checkout))
    marker.unlink(missing_ok=True)
    (checkout / "b.txt").write_text("new\n")
    release_worktree({**worktree, "checkout": str(checkout), "branch": "rem/eq"}, "Task")
    assert not marker.exists(), marker.read_text()
    assert "b.txt" in run(repo, "show", "--name-only", "--format=", "rem/eq")


def test_repository_lfs_programs_turn_off_git_lfs_too(tmp_path):
    # git-lfs reads lfs.customtransfer and similar settings from the repository's own
    # config and runs what they name, so with any of them set no git-lfs filter is trusted.
    repo = repository(tmp_path)
    run(repo, "config", "filter.lfs.process", "git-lfs filter-process")
    run(repo, "config", "lfs.customtransfer.evil.path", "/tmp/evil")

    found = settings(untrusted_repository_git_env(repo, {}))

    assert found["filter.lfs.process"] == ""
    assert found["filter.lfs.clean"] == "" and found["filter.lfs.smudge"] == ""
    assert found["filter.lfs.required"] == "false"
    assert not any(key.startswith("lfs.") for key in found)


def test_unreadable_settings_fail_closed(tmp_path):
    with pytest.raises(OSError):
        untrusted_repository_git_env(tmp_path / "missing", {})


def test_rem_worktree_add_and_release_run_no_planted_filter(tmp_path):
    repo = repository(tmp_path)
    marker = tmp_path / "marker"
    plant(repo, marker)
    run(repo, "add", ".gitattributes")
    marker.unlink(missing_ok=True)
    run(repo, "-c", "filter.evil.clean=", "-c", "filter.evil.required=false", "commit", "-qm", "x")
    marker.unlink(missing_ok=True)
    worktree = {
        "checkout": str(tmp_path / "data" / "rem-worktrees" / "run"),
        "root": str(tmp_path / "data" / "rem-worktrees" / "run"),
        "repository": str(repo),
        "branch": "rem/task-run",
        "base": run(repo, "rev-parse", "HEAD").strip(),
    }

    ensure_worktree(worktree)  # worktree add checks files out: smudge
    checkout = Path(worktree["checkout"])
    (checkout / "a.txt").write_text("changed\n")
    (checkout / "b.txt").write_text("new\n")
    summary = release_worktree(worktree, "Task")  # status, add -A, commit

    assert not marker.exists(), marker.read_text()
    assert "couldn't commit" not in summary
    assert "b.txt" in run(repo, "show", "--name-only", "--format=", "rem/task-run")


def test_organization_and_swarm_git_run_no_planted_filter(tmp_path):
    repo = repository(tmp_path)
    marker = tmp_path / "marker"
    plant(repo, marker)
    (repo / "a.txt").write_text("changed\n")

    organization_repository.git(repo, "add", "-A")
    organization_repository.git(repo, "diff", "--cached")
    swarm_workspaces.git(repo, "status", "--porcelain")
    swarm_workspaces.git(repo, "diff", "HEAD")

    assert not marker.exists(), marker.read_text()


# One runner ----------------------------------------------------------------------------

SOURCE = Path(__file__).resolve().parents[2] / "src" / "gofer"
# run_repository_git itself, and the user's own Commit button, where the repository's
# hooks and signing run on purpose.
OWN_GIT_COMMANDS = {"utils/process.py", "ui/commit_message.py", "ui/generation_jobs.py"}


def git_command_literals(tree: ast.AST) -> list[int]:
    """Lines of list or tuple literals whose first item is the string "git"."""
    allowed: set[int] = set()
    for node in ast.walk(tree):
        # A JSON schema's ``"enum": ["git", ...]`` lists choices, not a command.
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "enum":
                    allowed.add(id(value))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.List, ast.Tuple))
        and id(node) not in allowed
        and node.elts
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "git"
    ]


def test_backend_git_commands_all_go_through_the_one_runner():
    assert all((SOURCE / name).is_file() for name in OWN_GIT_COMMANDS)
    found = [
        f"{path.relative_to(SOURCE).as_posix()}:{line}"
        for path in sorted(SOURCE.rglob("*.py"))
        if path.relative_to(SOURCE).as_posix() not in OWN_GIT_COMMANDS
        for line in git_command_literals(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not found, "Run git with gofer.utils.process.run_repository_git: " + ", ".join(found)


def test_the_scan_finds_a_command_but_not_a_schema_choice():
    code = (
        'subprocess.run(["git", "status"])\n'
        'command = ("git", "-C", root)\n'
        'schema = {"enum": ["git", "digest"]}\n'
    )
    assert sorted(git_command_literals(ast.parse(code))) == [1, 2]


def test_the_runner_runs_no_planted_program_and_passes_its_options(tmp_path):
    repo = repository(tmp_path)
    marker = tmp_path / "marker"
    plant(repo, marker)
    (repo / "a.txt").write_text("changed\n")

    status = run_repository_git(repo, "status", "--porcelain", timeout=30)
    added = run_repository_git(
        repo, "hash-object", "--stdin", "-w", timeout=30, input=b"piped\n", text=False
    )
    named = run_repository_git(
        repo / ".git", "config", "--get", "x.y", timeout=30, git_dir=True, config=("x.y=z",)
    )
    author = {"GIT_AUTHOR_NAME": "Given", "GIT_AUTHOR_EMAIL": "given@example.invalid"}
    ident = run_repository_git(
        repo, "var", "GIT_AUTHOR_IDENT", timeout=30, env={"PATH": os.environ["PATH"], **author}
    )

    assert not marker.exists(), marker.read_text()
    assert status.returncode == 0 and "a.txt" in status.stdout
    assert run(repo, "cat-file", "-p", added.stdout.decode().strip()) == "piped\n"
    assert named.stdout == "z\n"
    assert ident.stdout.startswith("Given <given@example.invalid>")
    assert run_repository_git(repo, "no-such-command", timeout=30).returncode != 0


def test_the_runner_fails_closed_when_settings_cant_be_read(tmp_path):
    with pytest.raises(OSError):
        run_repository_git(tmp_path / "missing", "status", timeout=30)


# Hooks named in config ------------------------------------------------------------------
#
# Git 2.54 and later run ``hook.<name>.command`` for each ``hook.<name>.event``. Neither
# ``core.hooksPath`` nor ``--no-verify`` stops them all: post-commit, prepare-commit-msg,
# post-checkout and reference-transaction still run.


def git_version() -> tuple[int, ...]:
    try:
        text = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return ()
    found = re.search(r"(\d+)\.(\d+)", text)
    return tuple(int(part) for part in found.groups()) if found else ()


needs_config_hooks = pytest.mark.skipif(
    git_version() < (2, 54), reason="hooks named in config need git 2.54 or later"
)

HOOK_EVENTS = (
    "pre-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "post-checkout",
    "reference-transaction",
    "post-index-change",
    "pre-auto-gc",
)


def plant_hooks(repo: Path, marker: Path, *scope: str, name: str = "planted") -> None:
    """Name a hook for every event Raticode's commits and checkouts can reach."""
    for event in HOOK_EVENTS:
        run(repo, "config", *scope, "--add", f"hook.{name}.event", event)
    run(repo, "config", *scope, f"hook.{name}.command", f"echo ran >> {shlex.quote(str(marker))}")


def plant_at(scope: str, repo: Path, checkout: Path, marker: Path) -> None:
    """Hooks at local scope (the shared config) or in ``checkout``'s config.worktree."""
    if scope == "local":
        plant_hooks(repo, marker)
    else:
        run(repo, "config", "extensions.worktreeConfig", "true")
        plant_hooks(checkout, marker, "--worktree")


def hooks_run_for_plain_git(checkout: Path, marker: Path) -> bool:
    """The planted hooks are live: an ordinary commit runs them."""
    run(checkout, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "plain")
    ran = marker.exists()
    marker.unlink(missing_ok=True)
    return ran


def run_checkout(repo: Path, data_dir: Path) -> run_worktrees.RunWorktree:
    planned = run_worktrees.plan(data_dir, repo, "wf", "run-1")
    run_worktrees.create(planned)
    return planned


def commit_with_run_worktrees(checkout: Path) -> None:
    branch = run(checkout, "symbolic-ref", "--short", "HEAD").strip()
    assert run_worktrees.commit_changes(checkout, "Run", branch=branch)


def commit_with_ticket_patch(checkout: Path) -> None:
    branch = run(checkout, "symbolic-ref", "--short", "HEAD").strip()
    assert ticket_patch.commit_leftovers(str(checkout), "Ticket", branch=branch)


def commit_with_rem_release(checkout: Path) -> None:
    branch = run(checkout, "symbolic-ref", "--short", "HEAD").strip()
    base = run(checkout, "rev-parse", "HEAD").strip()
    repository = run(checkout, "rev-parse", "--show-toplevel").strip()
    summary = release_worktree(
        {
            "checkout": str(checkout),
            "root": str(checkout),
            "repository": str(repository),
            "branch": branch,
            "base": base,
        },
        "Task",
    )
    assert "couldn't commit" not in summary


COMMIT_SITES = {
    "run_worktrees.commit_changes": commit_with_run_worktrees,
    "ticket_patch.commit_leftovers": commit_with_ticket_patch,
    "rem_home_worktree.release_worktree": commit_with_rem_release,
}


@needs_config_hooks
@pytest.mark.parametrize("scope", ["local", "worktree"])
@pytest.mark.parametrize("site", list(COMMIT_SITES))
def test_commits_run_no_hook_named_in_repository_config(tmp_path, scope, site):
    repo = repository(tmp_path)
    planned = run_checkout(repo, tmp_path / "data")
    marker = tmp_path / "marker"
    plant_at(scope, repo, planned.checkout, marker)
    assert hooks_run_for_plain_git(planned.checkout, marker)
    tip = run(repo, "rev-parse", planned.branch).strip()
    (planned.checkout / "b.txt").write_text("new\n")

    COMMIT_SITES[site](planned.checkout)

    assert not marker.exists(), marker.read_text()
    assert run(repo, "rev-parse", planned.branch).strip() != tip
    assert "b.txt" in run(repo, "show", "--name-only", "--format=", planned.branch)


@needs_config_hooks
@pytest.mark.parametrize("scope", ["local", "worktree"])
def test_worktree_add_runs_no_hook_named_in_repository_config(tmp_path, scope):
    repo = repository(tmp_path)
    marker = tmp_path / "marker"
    plant_at(scope, repo, repo, marker)
    run(repo, "worktree", "add", "-q", "-b", "plain", str(tmp_path / "plain"))
    assert marker.exists()  # The planted post-checkout and reference-transaction hooks.
    marker.unlink()

    run_checkout(repo, tmp_path / "data")
    ensure_worktree(
        {
            "checkout": str(tmp_path / "data" / "rem-worktrees" / "run"),
            "root": str(tmp_path / "data" / "rem-worktrees" / "run"),
            "repository": str(repo),
            "branch": "rem/task-run",
            "base": run(repo, "rev-parse", "HEAD").strip(),
        }
    )

    assert not marker.exists(), marker.read_text()
    assert (tmp_path / "data" / "rem-worktrees" / "run" / "a.txt").is_file()


@needs_config_hooks
@pytest.mark.parametrize("site", list(COMMIT_SITES))
def test_a_dot_git_file_pointed_at_a_planted_git_folder_runs_no_hooks(tmp_path, site):
    # An agent rewrote its checkout's .git file to name a git folder it made itself. Git
    # reads that folder's config at local scope, so its hooks are blanked like any other.
    repo = repository(tmp_path)
    planned = run_checkout(repo, tmp_path / "data")
    (tmp_path / "planted").mkdir()
    planted = repository(tmp_path / "planted") / ".git"
    run(planted.parent, "checkout", "-q", "-b", "agent")
    marker = tmp_path / "marker"
    plant_hooks(planted.parent, marker)
    (planned.checkout / ".git").write_text(f"gitdir: {planted}\n")
    assert hooks_run_for_plain_git(planned.checkout, marker)
    (planned.checkout / "b.txt").write_text("new\n")

    COMMIT_SITES[site](planned.checkout)

    assert not marker.exists(), marker.read_text()


@needs_config_hooks
def test_hooks_from_the_command_line_still_run(tmp_path):
    # Raticode's own Co-authored-by hook comes in through GIT_CONFIG_PARAMETERS at command
    # scope. It is never blanked, and git reads those entries after GIT_CONFIG_COUNT, so a
    # repository hook of the same name can't replace it either.
    repo = repository(tmp_path)
    marker, own = tmp_path / "marker", tmp_path / "own"
    plant_hooks(repo, marker)
    # A repository command under the same name, trying to replace Raticode's.
    run(repo, "config", "hook.own.command", f"echo ran >> {shlex.quote(str(marker))}")
    command = f"echo own >> {shlex.quote(str(own))}"
    parameters = f"'hook.own.event'='post-commit' 'hook.own.command'={shlex.quote(command)}"
    existing = os.environ.get("GIT_CONFIG_PARAMETERS", "").strip()
    env = {
        **os.environ,
        "GIT_CONFIG_PARAMETERS": f"{existing} {parameters}".strip(),
    }

    found = settings(untrusted_repository_git_env(repo, env))
    assert found["hook.planted.command"] == "true"
    assert found["hook.own.command"] == "true"  # The repository's own entry, local scope.
    subprocess.run(
        [
            "git",
            *NO_REPOSITORY_PROGRAMS,
            "-C",
            str(repo),
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        check=True,
        capture_output=True,
        env=untrusted_repository_git_env(repo, env),
    )

    assert not marker.exists(), marker.read_text()
    assert own.read_text() == "own\n"
