"""Rem writer tasks under a worktree skill succeed only once their branch is on the
project's decided integration branch (``branch.integration``); there is no built-in one."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_landing import (
    branch_tips,
    landing_feedback,
    landing_opted_out,
    unlanded_branches,
)
from gofer.ui.rem_home_models import worktree_skill
from tests.unit.test_rem_home import home, recipe, wait_run  # noqa: F401
from tests.unit.test_rem_thread_parity import skill, thread_defaults

IDENTITY = ["-c", "user.name=T", "-c", "user.email=t@e", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *IDENTITY, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def repository(tmp_path: Path) -> Path:
    """A project whose own checkout is on test and whose dev is in another worktree."""
    project = tmp_path / "repo"
    project.mkdir()
    git(project, "init", "-q", "-b", "test")
    (project / "README.md").write_text("hello\n")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "init")
    git(project, "branch", "dev")
    git(project, "worktree", "add", "-q", str(tmp_path / "dev-home"), "dev")
    return project


def feature(project: Path, tmp_path: Path, name: str) -> tuple[Path, list[str]]:
    """Create a worktree branch with one commit, as a worker following the skill would."""
    slug = name.replace("/", "-")
    folder = tmp_path / "wt" / slug
    commands = [f"git -C {project} worktree add -b {name} {folder} dev"]
    git(project, "worktree", "add", "-q", "-b", name, str(folder), "dev")
    (folder / f"{slug}.txt").write_text("change\n")
    git(folder, "add", f"{slug}.txt")
    git(folder, "commit", "-qm", f"feat: {name}")
    commands.append(f'cd {folder} && git add {slug}.txt && git commit -m "feat: {slug}"')
    return folder, commands


def test_a_committed_branch_that_never_reached_dev_is_reported(tmp_path: Path):
    project = repository(tmp_path)
    before = branch_tips(project, "dev")
    assert before is not None and "dev" in before
    folder, commands = feature(project, tmp_path, "fix/thing")
    found = unlanded_branches(project, before, commands, integration="dev")
    assert found == [
        {"branch": "fix/thing", "worktree": str(folder), "commits": 1, "uncommitted": False}
    ]
    feedback = landing_feedback(found, "dev")
    assert "fix/thing" in feedback and "1 commit(s) not on dev" in feedback
    assert "merge --ff-only" in feedback and "Don't push" in feedback

    # Fast-forwarding dev where it is checked out lands it.
    git(tmp_path / "dev-home", "merge", "-q", "--ff-only", "fix/thing")
    assert unlanded_branches(project, before, commands, integration="dev") == []
    # Against another project's integration branch the same branch is checked there.
    git(project, "branch", "main", "test")
    found = unlanded_branches(project, before, commands, integration="main")
    assert [f["branch"] for f in found] == ["fix/thing"]
    assert "not on main" in landing_feedback(found, "main")


def test_uncommitted_changes_on_the_runs_branch_are_not_landed(tmp_path: Path):
    project = repository(tmp_path)
    before = branch_tips(project)
    assert before is not None
    folder = tmp_path / "wt" / "half-done"
    git(project, "worktree", "add", "-q", "-b", "half-done", str(folder), "dev")
    (folder / "README.md").write_text("edited\n")
    found = unlanded_branches(
        project, before, [f"git worktree add -b half-done {folder} dev"], integration="dev"
    )
    assert found and found[0]["uncommitted"] and not found[0]["commits"]
    assert "uncommitted changes" in landing_feedback(found, "dev")


def test_other_tasks_branches_and_the_users_checkout_are_not_this_runs(tmp_path: Path):
    project = repository(tmp_path)
    before = branch_tips(project)
    assert before is not None
    # Another task commits on its own branch while this run only reads it.
    folder, _ = feature(project, tmp_path, "someone-else")
    reads = [f"git -C {folder} log --oneline -3", "git branch -vv", "git diff dev someone-else"]
    assert unlanded_branches(project, before, reads, integration="dev") == []
    # The user's own checkout moving is never a task's unlanded work.
    (project / "notes.txt").write_text("user\n")
    git(project, "add", "notes.txt")
    git(project, "commit", "-qm", "user work")
    assert (
        unlanded_branches(project, before, [f"cd {project} && git commit -am x"], integration="dev")
        == []
    )
    # A branch that already existed and didn't move isn't this run's either.
    later = branch_tips(project)
    assert later is not None
    assert (
        unlanded_branches(project, later, [f"git -C {folder} commit --amend"], integration="dev")
        == []
    )


def test_projects_without_the_integration_branch_or_git_are_skipped(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert branch_tips(plain) is None
    git(plain, "init", "-q", "-b", "main")
    (plain / "a").write_text("a")
    git(plain, "add", "-A")
    git(plain, "commit", "-qm", "init")
    assert branch_tips(plain, "dev") is None
    assert branch_tips(plain, "main") == {"main": git(plain, "rev-parse", "HEAD")}


@pytest.mark.parametrize(
    "prompt",
    [
        "Build a prototype. Do not merge it into dev.",
        "Make the change but don't land it on dev yet.",
        "Leave the work on its own branch for the user to try.",
    ],
)
def test_tasks_can_say_their_work_stays_off_dev(prompt: str):
    assert landing_opted_out(prompt, "dev")


def test_opting_out_names_the_projects_own_branch():
    assert landing_opted_out("Do not merge it into main.", "main")
    assert not landing_opted_out("Do not merge it into main.", "dev")
    assert landing_opted_out("Don't land it on the integration branch yet.")


@pytest.mark.parametrize(
    "prompt",
    [
        "Fix the bug and land it on local dev. Do not push.",
        "Do not commit to main, push or deploy.",
        "Merge to dev when the task succeeds.",
    ],
)
def test_ordinary_task_wording_still_lands(prompt: str):
    assert not landing_opted_out(prompt, "dev")


@pytest.mark.parametrize(
    "description",
    [
        "Creates or reuses a git worktree off dev, then rebases onto dev and fast-forwards dev.",
        "Use when starting feature work: creates isolated git worktrees.",
    ],
)
def test_any_worktree_skill_keeps_rem_from_making_its_own_worktree(
    tmp_path: Path, description: str
):
    resources = {"skills": [skill(tmp_path, "using-git-worktrees", description)]}
    assert worktree_skill(resources) == "using-git-worktrees"


def decide_branch(home: RemHome, project: Path, branch: str = "dev") -> None:  # noqa: F811
    """The user's confirmed landing branch for the project."""
    home.change_decisions(
        {"action": "add", "topic": "branch.integration", "answer": branch, "project": str(project)}
    )


def shell(command: str) -> dict[str, Any]:
    return {
        "type": "thought",
        "text": "ran",
        "trace": {
            "kind": "tool",
            "category": "shell",
            "phase": "result",
            "status": "completed",
            "exitCode": 0,
            "output": "ok",
            "input": json.dumps({"command": command}),
        },
    }


def passing_review() -> list[dict[str, Any]]:
    return [
        {
            "type": "thought",
            "text": "Independent check",
            "trace": {
                "id": "review-check",
                "category": "shell",
                "phase": "result",
                "status": "completed",
                "exitCode": 0,
                "output": "1 passed",
            },
        },
        {
            "type": "final",
            "message": {
                "body": json.dumps(
                    {
                        "criteria": [{"id": "check", "result": "pass", "evidence": "Saw it"}],
                        "feedback": "",
                    }
                )
            },
        },
    ]


def worker_source(project: Path, tmp_path: Path, *, land_on_repair: bool):
    turns: list[dict[str, Any]] = []

    async def source(**kwargs: Any):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            for event in passing_review():
                yield event
            return
        turns.append(kwargs)
        if len(turns) == 1:
            _, commands = feature(project, tmp_path, "fix/rem-change")
            for command in commands:
                yield shell(command)
            yield shell("pytest tests/test_feature.py")
            yield {"type": "final", "message": {"body": "Committed on fix/rem-change."}}
            return
        if land_on_repair:
            git(tmp_path / "dev-home", "merge", "-q", "--ff-only", "fix/rem-change")
            yield shell(f"git -C {tmp_path / 'dev-home'} merge --ff-only fix/rem-change")
        yield shell("pytest tests/test_feature.py")
        yield {"type": "final", "message": {"body": f"Turn {len(turns)} done."}}

    return source, turns


def test_rem_sends_an_unlanded_branch_back_and_succeeds_once_it_lands(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    project = repository(tmp_path)
    home.configure({"provider": "claude_code", "resources": thread_defaults(tmp_path)})
    decide_branch(home, project)
    source, turns = worker_source(project, tmp_path, land_on_repair=True)
    home.source = source
    task = home.create_task({"request_id": "land", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    assert len(turns) == 2
    first = turns[0]["agent_instructions"]
    assert "with one exception: landing" in first
    assert "isn't finished until its branch has landed" in first
    assert "local dev, the project's recorded decision" in first
    assert "don't wait for a separate go-ahead" in first
    repair = turns[1]["messages"][-1]["body"]
    assert "hasn't landed on local dev" in repair and "fix/rem-change" in repair
    assert git(project, "merge-base", "--is-ancestor", "fix/rem-change", "dev") == ""


def test_rem_never_reports_success_for_work_that_stays_off_dev(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    project = repository(tmp_path)
    home.configure({"provider": "claude_code", "resources": thread_defaults(tmp_path)})
    decide_branch(home, project)
    source, turns = worker_source(project, tmp_path, land_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "stuck", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=30)
    assert run["state"] == "blocked", run
    assert "hasn't landed on local dev" in run["summary_short"] + json.dumps(run["criteria"])
    assert len(turns) >= 2


def test_an_opted_out_task_keeps_its_branch_and_still_succeeds(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    project = repository(tmp_path)
    home.configure({"provider": "claude_code", "resources": thread_defaults(tmp_path)})
    decide_branch(home, project)
    source, turns = worker_source(project, tmp_path, land_on_repair=False)
    home.source = source
    prompt = "Build a prototype for the user to try. Do not merge it into dev."
    task = home.create_task({"request_id": "proto", **recipe(project, prompt=prompt)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    assert len(turns) == 1
    assert "isn't finished until its branch has landed" not in turns[0]["agent_instructions"]


def test_a_general_worktree_skill_never_holds_a_task_for_dev(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    """Another user's worktree skill and a dev branch don't make Rem demand landing."""
    project = repository(tmp_path)
    resources = thread_defaults(tmp_path)
    resources["skills"] = [
        skill(tmp_path / "other", "using-git-worktrees", "Creates isolated git worktrees.")
    ]
    home.configure({"provider": "claude_code", "resources": resources})
    source, turns = worker_source(project, tmp_path, land_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "general", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    assert len(turns) == 1
    first = turns[0]["agent_instructions"]
    assert "with one exception: landing" in first
    assert "isn't finished until its branch has landed" not in first


def test_a_projects_first_task_finds_its_landing_branch_and_waits_for_the_user(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    """No built-in dev: the scan recommends one, and until the user confirms, no landing."""
    project = repository(tmp_path)
    home.configure({"provider": "claude_code", "resources": thread_defaults(tmp_path)})
    source, turns = worker_source(project, tmp_path, land_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "first", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    assert len(turns) == 1
    assert run["landing_branch"] is None
    assert "isn't finished until its branch has landed" not in turns[0]["agent_instructions"]
    waiting = [
        i for i in home.decisions(str(project))["items"] if i["topic"] == "branch.integration"
    ]
    assert [(i["status"], i["recommended"]) for i in waiting] == [("pending", "dev")]
    # The evidence is the enabled skill's own words, checked against the branch's commit.
    assert waiting[0]["evidence"][0]["kind"] == "commit"
    assert "fast-forwards dev" in waiting[0]["evidence"][0]["excerpt"]


def test_with_auto_mode_rem_decides_the_landing_branch_itself_and_holds_work_to_it(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    project = repository(tmp_path)
    with home.store.connect() as db:
        home.store.set_setting(db, "self_work", {"enabled": True, "auto_approve_proposals": True})
    home.configure({"provider": "claude_code", "resources": thread_defaults(tmp_path)})
    source, turns = worker_source(project, tmp_path, land_on_repair=True)
    home.source = source
    task = home.create_task({"request_id": "auto", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    assert run["landing_branch"] == "dev"
    assert len(turns) == 2
    decided = [
        i for i in home.decisions(str(project))["items"] if i["topic"] == "branch.integration"
    ]
    assert [(i["status"], i["answer"], i["decided_by"]) for i in decided] == [
        ("provisional", "dev", "rem")
    ]


def test_a_project_that_lands_on_main_is_held_to_main(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    project = tmp_path / "repo"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")
    (project / "README.md").write_text("hello\n")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "init")
    git(project, "checkout", "-q", "-b", "test")
    git(project, "worktree", "add", "-q", str(tmp_path / "dev-home"), "main")
    git(project, "branch", "dev", "main")
    home.configure({"provider": "claude_code", "resources": thread_defaults(tmp_path)})
    decide_branch(home, project, "main")
    source, turns = worker_source(project, tmp_path, land_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "main", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=30)
    assert run["state"] == "blocked", run
    assert run["landing_branch"] == "main"
    assert "local main, the project's recorded decision" in turns[0]["agent_instructions"]
    assert "hasn't landed on local main" in turns[1]["messages"][-1]["body"]


def test_projects_that_already_landed_on_dev_carry_it_over_as_confirmed(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    """Existing projects behave as before: their earlier landings become the decision."""
    project = repository(tmp_path)
    other = tmp_path / "gone"
    with home.store.connect() as db:
        for number, root in enumerate([project, project, other]):
            db.execute(
                "INSERT INTO runs(id, task, occurrence, state, body) VALUES (?, ?, ?, ?, ?)",
                (
                    f"old-{number}",
                    "t",
                    f"old-{number}",
                    "succeeded",
                    json.dumps({"recipe": {"project_dir": str(root)}, "branch_tips": {"dev": "x"}}),
                ),
            )
        home.store.set_setting(db, "rem_decision_migrations", {})
    assert home._carry_over_landing() == [str(project)]
    items = home.decisions(str(project))["items"]
    assert [(i["topic"], i["status"], i["answer"]) for i in items] == [
        ("branch.integration", "confirmed", "dev")
    ]
    assert items[0]["source"] == {"via": "carried over"}
    assert "2 earlier tasks" in items[0]["history"][0]["note"]
    assert home.integration_branch(str(project)) == "dev"
    # Once only: a decision the user forgets later isn't brought back.
    home.change_decisions({"action": "forget", "id": items[0]["id"]})
    assert home._carry_over_landing() == []
    assert home.integration_branch(str(project)) is None


def test_backend_git_in_agent_folders_runs_no_repository_programs(tmp_path, monkeypatch) -> None:
    """A worker that edits .git/config can't get fsmonitor or hooks run by the backend."""
    import subprocess

    from gofer.ui import rem_home_landing, rem_home_worktree, rem_threads

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    marker = tmp_path / "ran"
    script = tmp_path / "program.sh"
    script.write_text(f"#!/bin/sh\nenv > '{marker}'\n")
    script.chmod(0o700)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "post-commit").write_text(script.read_text())
    (hooks / "post-commit").chmod(0o700)
    for key, value in (("core.fsmonitor", str(script)), ("core.hooksPath", str(hooks))):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    (repo / "a.txt").write_text("a\n")
    monkeypatch.setenv("GOFER_DESKTOP_GRANT_SECRET", "desktop-grant-secret")

    rem_home_worktree._git(repo, "status", "--porcelain")
    rem_home_worktree._git(repo, "add", "-A")
    rem_home_worktree._git(
        repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"
    )
    rem_home_landing._git(repo, "status", "--porcelain")
    rem_threads._git_worktrees(repo)

    assert not marker.exists()
