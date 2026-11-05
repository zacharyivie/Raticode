"""Source control on a paired phone: read-only status of open projects only."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gofer.devices import source_control as module
from gofer.devices.desktop_control import Refused
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def run(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Ada",
        GIT_AUTHOR_EMAIL="ada@example.com",
        GIT_COMMITTER_NAME="Ada",
        GIT_COMMITTER_EMAIL="ada@example.com",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env
    ).stdout


def repository(root: Path) -> None:
    run(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("hello\n")
    (root / "app.py").write_text("print(1)\n")
    run(root, "add", ".")
    run(root, "commit", "-q", "-m", "First commit")
    (root / "app.py").write_text("print(2)\n")
    run(root, "commit", "-q", "-am", "Print two")


def plain(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "digest"}


@pytest.fixture
def desk(phone):
    app, peer, context, send = phone
    app.desktop.enable(peer, True)
    roots = {p["name"]: Path(p["root"]) for p in context["projects"]}
    ids = {p["name"]: p["id"] for p in context["projects"]}
    return app, peer, send, roots, ids


def test_source_control_needs_phone_access_and_is_advertised(phone):
    app, peer, _, send = phone
    assert send("desktop.read", "source_control.list")["code"] == "unauthorized"
    assert send("desktop.read", "source_control.read", {"project_id": "x"})["code"] == (
        "unauthorized"
    )
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["source_control"] is True
    # Turning thread sync off also turns the view off.
    app.workspace.enable(peer, False)
    assert send("desktop.read", "source_control.list")["code"] == "unauthorized"


def test_list_shows_open_projects_without_paths(desk):
    _, _, send, roots, ids = desk
    repository(roots["Notes"])
    (roots["Notes"] / "README.md").write_text("changed\n")
    (roots["Notes"] / "new.txt").write_text("new\n")
    run(roots["Notes"], "add", "new.txt")
    (roots["Notes"] / "scratch.txt").write_text("scratch\n")

    listed = plain(send("desktop.read", "source_control.list")["value"])
    text = json.dumps(listed)
    assert str(roots["Notes"]) not in text and str(roots["Code"]) not in text
    assert "ada@example.com" not in text
    by_name = {p["name"]: p for p in listed["projects"]}
    assert set(by_name) == {"Notes", "Code"} and listed["more"] == 0
    notes = by_name["Notes"]
    assert notes["project_id"] == ids["Notes"] and notes["state"] == "ok"
    assert notes["branch"] == "main" and not notes["detached"]
    assert notes["upstream"] == "" and notes["ahead"] is None and notes["behind"] is None
    assert notes["changes"] == {
        "total": 3,
        "staged": 1,
        "unstaged": 1,
        "untracked": 1,
        "conflicts": 0,
    }
    assert notes["last_commit"]["subject"] == "Print two"
    assert notes["last_commit"]["author"] == "Ada"
    assert notes["operation"] == "" and notes["fetched_at"] == ""
    # The list is a summary; file lists and history come with one project's read.
    assert "files" not in notes and "commits" not in notes
    code = by_name["Code"]
    assert code["state"] == "not_repository" and code["detail"] == module.NOT_REPOSITORY


def test_read_shows_changes_history_worktrees_and_upstream(desk, tmp_path):
    _, _, send, roots, ids = desk
    root = roots["Notes"]
    repository(root)
    remote = tmp_path / "remote.git"
    run(tmp_path, "init", "-q", "--bare", str(remote))
    run(root, "remote", "add", "origin", str(remote))
    run(root, "push", "-q", "-u", "origin", "main")
    (root / "later.txt").write_text("later\n")
    run(root, "add", "later.txt")
    run(root, "commit", "-q", "-m", "Not pushed yet")
    (root / ".git" / "info" / "exclude").write_text(".worktrees/\n")
    run(root, "worktree", "add", "-q", "-b", "feature", str(root / ".worktrees" / "feature"))
    (root / ".worktrees" / "feature" / "wip.txt").write_text("wip\n")
    (root / "app.py").write_text("print(3)\n")
    run(root, "stash", "-q")
    (root / "README.md").write_text("edited\n")

    view = plain(send("desktop.read", "source_control.read", {"project_id": ids["Notes"]})["value"])
    text = json.dumps(view)
    assert str(root) not in text and str(remote) not in text
    assert view["name"] == "Notes" and view["state"] == "ok"
    assert view["upstream"] == "origin/main" and view["ahead"] == 1 and view["behind"] == 0
    assert view["fetched_at"] == ""  # Pushing doesn't fetch.
    assert view["files"] == [
        {"path": "README.md", "status": "modified", "staged": False, "unstaged": True}
    ]
    assert view["more_files"] == 0
    assert [c["subject"] for c in view["commits"]] == [
        "Not pushed yet",
        "Print two",
        "First commit",
    ]
    assert view["stashes"] == 1
    trees = {t["name"]: t for t in view["worktrees"]}
    assert trees[root.name]["current"] and trees[root.name]["branch"] == "main"
    # A worktree inside the project is named by its folder relative to the project.
    assert trees[".worktrees/feature"] == {
        "name": ".worktrees/feature",
        "branch": "feature",
        "detached": False,
        "current": False,
        "locked": False,
        "missing": False,
        "changes": 1,
    }

    missing = send("desktop.read", "source_control.read", {"project_id": "not-open"})
    assert missing["code"] == "not_found" and missing["detail"] == module.NOT_OPEN


def test_reads_never_change_the_repository(desk, monkeypatch, tmp_path):
    _, _, send, roots, ids = desk
    root = roots["Notes"]
    repository(root)
    (root / "app.py").write_text("print(4)\n")
    git_dir = root / ".git"
    before = {p: p.stat().st_mtime_ns for p in git_dir.rglob("*") if p.is_file()}
    # A desktop started from a Git hook must still read the project's own repository.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    repository(elsewhere)
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(elsewhere))

    view = send("desktop.read", "source_control.read", {"project_id": ids["Notes"]})["value"]
    send("desktop.read", "source_control.list")
    assert [f["path"] for f in view["files"]] == ["app.py"]
    after = {p: p.stat().st_mtime_ns for p in git_dir.rglob("*") if p.is_file()}
    assert after == before


def test_missing_folder_and_unavailable_git_are_explained(desk, monkeypatch):
    _, _, send, roots, ids = desk
    shutil.rmtree(roots["Code"])
    listed = send("desktop.read", "source_control.list")["value"]
    code = next(p for p in listed["projects"] if p["name"] == "Code")
    assert code["state"] == "missing" and code["detail"] == module.MISSING
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    notes = send("desktop.read", "source_control.read", {"project_id": ids["Notes"]})["value"]
    assert notes["state"] == "unavailable" and notes["detail"] == module.NO_GIT


def test_a_subfolder_project_shows_its_own_files(tmp_path):
    repository(tmp_path)
    sub = tmp_path / "web"
    sub.mkdir()
    (sub / "index.html").write_text("<p>hi</p>\n")
    (tmp_path / "outside.txt").write_text("x\n")
    status = module.project_status(str(sub), detail=True)
    assert status["subfolder"] == "web"
    assert [f["path"] for f in status["files"]] == ["index.html"]


def test_conflicts_and_renames_parse_like_the_desktop_panel():
    output = "\0".join(
        [
            "# branch.oid 0123456789abcdef0123456789abcdef01234567",
            "# branch.head (detached)",
            "2 R. N... 100644 100644 100644 aaa bbb R100 new name.py",
            "old name.py",
            "u UU N... 100644 100644 100644 100644 aaa bbb ccc both.py",
            "? untracked file.txt",
            "",
        ]
    )
    parsed = module.parse_status(output)
    assert parsed["detached"] and parsed["branch"] == "" and parsed["head"] == "0123456789ab"
    assert parsed["files"] == [
        {"path": "new name.py", "status": "renamed", "staged": True, "unstaged": False},
        {"path": "both.py", "status": "conflict", "staged": False, "unstaged": True},
        {"path": "untracked file.txt", "status": "untracked", "staged": False, "unstaged": True},
    ]


def test_a_project_closed_while_reading_is_not_shown():
    data = module.gather("source_control.list", {}, {})
    assert data == {"projects": [], "more": 0}
    gathered = {"project_id": "p", "root": "/old", "status": {"state": "missing"}}
    with pytest.raises(Refused):
        module.read("source_control.read", gathered, {"p": {"root": "/new", "name": "P"}})
    listed = module.read(
        "source_control.list",
        {"projects": [gathered], "more": 0},
        {"p": {"root": "/new", "name": "P"}},
    )
    assert listed == {"projects": [], "more": 0}


def test_history_graph_commits_diffs_and_branches(desk, tmp_path):
    _, _, send, roots, ids = desk
    root = roots["Notes"]
    repository(root)
    run(root, "switch", "-q", "-c", "feature")
    (root / "feature.txt").write_text("f\n")
    run(root, "add", ".")
    run(root, "commit", "-q", "-m", "Feature work\n\nWith a longer explanation.")
    run(root, "switch", "-q", "main")
    run(root, "merge", "-q", "--no-ff", "feature", "-m", "Merge feature")
    run(root, "tag", "v1")
    (root / "app.py").write_text("print(5)\n")
    (root / "new.txt").write_text("brand new\n")
    (root / ".env").write_text("TOKEN=secret\n")
    project = ids["Notes"]

    assert send("desktop.read", "overview")["value"]["source_control_history"] is True
    view = plain(send("desktop.read", "source_control.read", {"project_id": project})["value"])
    branches = {b["name"]: b for b in view["branches"]}
    assert branches["main"]["current"] and not branches["feature"]["current"]
    assert view["remotes"] == 0

    graph = plain(send("desktop.read", "source_control.history", {"project_id": project})["value"])
    text = json.dumps(graph)
    assert str(root) not in text and "ada@example.com" not in text
    subjects = [c["subject"] for c in graph["commits"]]
    assert subjects == ["Merge feature", "Feature work", "Print two", "First commit"]
    merge, feature = graph["commits"][0], graph["commits"][1]
    assert len(merge["parents"]) == 2 and merge["is_head"]
    assert "refs/tags/v1" in merge["labels"] and "refs/heads/main" in merge["labels"]
    assert feature["body"] == "With a longer explanation."
    assert feature["insertions"] == 1 and feature["deletions"] == 0
    assert {r["id"] for r in graph["refs"]} >= {
        "refs/heads/main",
        "refs/heads/feature",
        "refs/tags/v1",
    }
    assert graph["has_more"] is False
    only = plain(
        send(
            "desktop.read",
            "source_control.history",
            {"project_id": project, "refs": ["refs/heads/feature"], "limit": 100},
        )["value"]
    )
    assert [c["subject"] for c in only["commits"]] == ["Feature work", "Print two", "First commit"]
    bad = send("desktop.read", "source_control.history", {"project_id": project, "refs": ["--all"]})
    assert bad["code"] == "invalid"

    detail = plain(
        send(
            "desktop.read",
            "source_control.commit",
            {"project_id": project, "hash": feature["hash"]},
        )["value"]
    )
    assert detail["files"] == [
        {
            "path": "feature.txt",
            "old_path": "",
            "status": "added",
            "insertions": 1,
            "deletions": 0,
            "binary": False,
        }
    ]
    shown = send(
        "desktop.read",
        "source_control.diff",
        {"project_id": project, "source": "commit", "hash": feature["hash"], "path": "feature.txt"},
    )["value"]
    assert "+f" in shown["text"] and not shown["binary"]
    changed = send(
        "desktop.read",
        "source_control.diff",
        {"project_id": project, "source": "unstaged", "path": "app.py"},
    )["value"]
    assert "-print(2)" in changed["text"] and "+print(5)" in changed["text"]
    new = send(
        "desktop.read",
        "source_control.diff",
        {"project_id": project, "source": "unstaged", "path": "new.txt"},
    )["value"]
    assert "+brand new" in new["text"]

    # Only files the status or the commit lists, and never a secret-looking one.
    for params, code in [
        ({"source": "unstaged", "path": "README.md"}, "not_found"),
        ({"source": "staged", "path": "app.py"}, "not_found"),
        ({"source": "unstaged", "path": "../../etc/passwd"}, "not_found"),
        ({"source": "commit", "hash": feature["hash"], "path": "app.py"}, "not_found"),
        ({"source": "commit", "hash": "--all", "path": "feature.txt"}, "not_found"),
        ({"source": "unstaged", "path": ".env"}, "unauthorized"),
        ({"source": "elsewhere", "path": "app.py"}, "invalid"),
    ]:
        assert (
            send("desktop.read", "source_control.diff", {"project_id": project, **params})["code"]
            == code
        )
    assert (
        send("desktop.read", "source_control.commit", {"project_id": "nope", "hash": "abc1234"})[
            "code"
        ]
        == "not_found"
    )


def test_history_of_a_plain_folder_says_why(desk):
    _, _, send, roots, ids = desk
    refused = send("desktop.read", "source_control.history", {"project_id": ids["Code"]})
    assert refused == {"ok": False, "code": "unavailable", "detail": module.NOT_REPOSITORY}


def test_history_reads_never_change_the_repository(desk):
    _, _, send, roots, ids = desk
    root = roots["Notes"]
    repository(root)
    (root / "app.py").write_text("print(9)\n")
    before = {p: p.stat().st_mtime_ns for p in (root / ".git").rglob("*") if p.is_file()}
    head = run(root, "rev-parse", "HEAD").strip()
    project = ids["Notes"]
    send("desktop.read", "source_control.history", {"project_id": project})
    send("desktop.read", "source_control.commit", {"project_id": project, "hash": head})
    send(
        "desktop.read",
        "source_control.diff",
        {"project_id": project, "source": "unstaged", "path": "app.py"},
    )
    after = {p: p.stat().st_mtime_ns for p in (root / ".git").rglob("*") if p.is_file()}
    assert after == before
