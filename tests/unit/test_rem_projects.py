"""Rem opens and creates the local project folders a request needs (rem_projects)."""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome, workflow_answer_only
from gofer.ui.rem_home_models import Decision
from gofer.ui.rem_projects import ProjectOpen, ProjectOpenError, opened_note, prepare_project
from gofer.ui.rem_screen import acts
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe


@pytest.fixture
def places(tmp_path: Path) -> dict[str, Path]:
    user = tmp_path / "home"
    (user / "Projects").mkdir(parents=True)
    (user / ".ssh").mkdir()
    data = tmp_path / "state" / "data" / "gofer"
    data.mkdir(parents=True)
    (data.parent / ".gofer-trusted-bin").mkdir()
    return {"home": user, "data": data, "projects": user / "Projects"}


def prepare(places: dict[str, Path], path: str | Path, **options: Any) -> dict[str, Any]:
    return prepare_project(
        ProjectOpen(path=str(path), **options), data_dir=places["data"], home=places["home"]
    )


def test_creates_a_new_project_with_a_git_repository(places: dict[str, Path]):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    target = places["projects"] / "demo" / "app"
    opened = prepare(places, target, create=True, git_init=True)
    assert opened == {
        "root": str(target.resolve()),
        "name": "app",
        "created": True,
        "git_initialized": True,
        "existing": False,
    }
    assert (target / ".git").is_dir()
    assert opened_note([opened]) == f"Created and opened `{target}` with a new git repository."


def test_an_existing_folder_opens_as_it_is(places: dict[str, Path]):
    target = places["projects"] / "keep"
    target.mkdir()
    (target / "notes.txt").write_text("mine")
    before = sorted(os.listdir(target))
    # Asking to create or initialize an existing folder never changes it.
    opened = prepare(places, target, create=True, git_init=True)
    assert opened["created"] is False and opened["git_initialized"] is False
    assert opened["existing"] is True
    assert sorted(os.listdir(target)) == before
    assert (target / "notes.txt").read_text() == "mine"
    assert "nothing in it was changed" in opened_note([opened])


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("relative/project", "absolute folder path"),
        ("/", "whole filesystem"),
        ("{home}", "entire home folder"),
        ("{home}/..", "entire home folder"),
        ("{home}/.ssh", "credentials"),
        ("{home}/.ssh/keys", "credentials"),
        ("{home}/.config/microsoft-edge/Default", "credentials"),
        ("{home}/Library/Keychains", "credentials"),
        ("{home}/Library/Application Support/Microsoft Edge/Default", "credentials"),
        ("{home}/AppData/Local/Microsoft/Edge/User Data", "credentials"),
        ("{home}/AppData/Roaming/Microsoft/Protect", "credentials"),
        ("{home}/Library", "credentials"),
        ("/proc/self", "system state"),
        ("/etc/myapp", "system state"),
        ("{data}", "Raticode's own data"),
        ("{data}/rem-work", "Raticode's own data"),
        ("{state}", "Raticode's own data"),
    ],
)
def test_refuses_places_that_are_not_project_folders(
    places: dict[str, Path], path: str, reason: str
):
    target = path.format(
        home=places["home"], data=places["data"], state=places["data"].parent.parent
    )
    with pytest.raises(ProjectOpenError, match=reason):
        prepare(places, target, create=True)
    assert not (places["home"] / ".ssh" / "keys").exists()


def test_a_symlink_cannot_lead_into_a_refused_place(places: dict[str, Path]):
    link = places["projects"] / "innocent"
    link.symlink_to(places["home"] / ".ssh")
    with pytest.raises(ProjectOpenError, match="credentials"):
        prepare(places, link)


def test_missing_folders_and_files_get_clear_errors(places: dict[str, Path]):
    with pytest.raises(ProjectOpenError, match="doesn't exist. Ask Rem to create it"):
        prepare(places, places["projects"] / "missing")
    assert not (places["projects"] / "missing").exists()
    file = places["projects"] / "report.txt"
    file.write_text("data")
    with pytest.raises(ProjectOpenError, match="is a file, not a folder. Nothing was changed"):
        prepare(places, file, create=True)
    assert file.read_text() == "data"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_the_operating_systems_refusal_is_reported(places: dict[str, Path]):
    locked = places["projects"] / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(ProjectOpenError, match="operating system refused to create"):
            prepare(places, locked / "new", create=True)
        with pytest.raises(ProjectOpenError, match="won't let Raticode write"):
            prepare(places, locked, git_init=True)
    finally:
        locked.chmod(0o700)


def test_concurrent_requests_for_one_new_folder_all_open_it(places: dict[str, Path]):
    target = places["projects"] / "race"
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def open_it() -> None:
        try:
            results.append(prepare(places, target, create=True))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=open_it) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert {r["root"] for r in results} == {str(target.resolve())}
    assert sum(r["created"] for r in results) == 1


def test_open_projects_is_an_action_that_question_only_workflows_drop():
    decision = Decision.model_validate(
        {"reply": "Sure.", "open_projects": [{"path": "/tmp/x", "create": True}]}
    )
    assert acts(decision)
    workflow_answer_only(decision)
    assert decision.open_projects == []
    with pytest.raises(ValueError):
        Decision.model_validate({"reply": "x", "open_projects": [{"path": "/tmp/x", "rm": True}]})


def _home_places(home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    projects = tmp_path / "user" / "Projects"
    projects.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    # The shared fixture keeps data beside tmp_path, and other tests leave Raticode's
    # trusted tools folder there, which makes pytest's whole temp root Raticode's data
    # root. Keep this test's data folder apart from the projects it opens.
    monkeypatch.setattr(home, "data_dir", tmp_path / "state" / "gofer")
    return projects


def test_rem_creates_a_project_and_starts_its_task_there(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    projects = _home_places(home, tmp_path, monkeypatch)
    existing = projects / "existing"
    existing.mkdir()
    home.configure({"provider": "codex", "projects": [{"root": str(existing), "grantId": "w"}]})
    granted: list[tuple[str, str]] = []

    def grant(root: Path, grant_id: str) -> str:
        granted.append((str(root), grant_id))
        return grant_id

    home.grant = grant
    demo = projects / "demo"
    home.generate = lambda p, c, e: {
        "reply": "I'll set up the demo.",
        "open_projects": [{"path": str(demo), "create": True}],
        "tasks": [recipe(demo, title="Run the demo")],
    }
    home.submit_message({"request_id": "u-1", "body": "Make a demo project and run it"})
    home._reply()

    assert demo.is_dir()
    config = home._current_config()
    roots = [p["root"] for p in config["projects"]]
    assert roots == [str(existing), str(demo)], "existing projects are kept"
    assert granted and granted[0][0] == str(demo)
    task = next(t for t in home.store.tasks() if t["title"] == "Run the demo")
    assert task["project_dir"] == str(demo)
    # The task's acknowledgement is Rem's reply, with what Raticode actually did.
    reply = next(m for m in home.store.conversation()["messages"] if m["id"].startswith("queued-"))
    assert reply["body"].startswith("I'll set up the demo.")
    assert f"Created and opened `{demo}`" in reply["body"]
    # The window hears about the folder until it lists it with its own grant.
    assert [p["root"] for p in home.pending_projects()] == [str(demo)]

    # A context update from a window that hasn't listed it yet keeps it.
    home.configure({"provider": "codex", "projects": [{"root": str(existing), "grantId": "w"}]})
    assert [p["root"] for p in home._current_config()["projects"]] == [str(existing), str(demo)]
    # After a restart the backend grant is registered again under the same ID.
    grant_id = home.pending_projects()[0]["grantId"]
    granted.clear()
    restarted = RemHome(
        home.store.path.parent,  # the fixture's store
        source=home.source,
        jobs=home.jobs,
        steering=home.steering,
        authorize=lambda c, r: None,
        grant=grant,
        start=False,
    )
    assert granted == [(str(demo), grant_id)]
    restarted.close()
    # Once the window lists it, the window's grant is used and Rem stops reporting it.
    home.configure(
        {
            "provider": "codex",
            "projects": [
                {"root": str(demo), "grantId": "window"},
                {"root": str(existing), "grantId": "w"},
            ],
        }
    )
    assert home.pending_projects() == []
    assert home._current_config()["projects"][0] == {"root": str(demo), "grantId": "window"}


def test_a_refused_folder_starts_nothing_and_says_why(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    projects = _home_places(home, tmp_path, monkeypatch)
    home.generate = lambda p, c, e: {
        "reply": "Creating it now.",
        "open_projects": [{"path": str(projects.parent / ".ssh" / "work"), "create": True}],
        "tasks": [recipe(projects.parent / ".ssh" / "work")],
    }
    home.submit_message({"request_id": "u-1", "body": "Make a project in .ssh/work"})
    home._reply()
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-u-1")
    assert reply["body"].startswith("I couldn't open the project.")
    assert "credentials" in reply["body"] and reply["body"].endswith("Nothing was started.")
    assert home.store.tasks() == []
    assert home.pending_projects() == []
    assert not (projects.parent / ".ssh" / "work").exists()


def test_an_already_open_folder_is_not_added_twice(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    projects = _home_places(home, tmp_path, monkeypatch)
    home.configure({"provider": "codex", "projects": [{"root": str(projects), "grantId": "w"}]})
    opened = home.open_projects([ProjectOpen(path=str(projects))])
    assert opened[0]["already_open"] is True
    assert home.pending_projects() == []
    assert opened_note(opened) == f"Already open: `{projects}`."


def test_rem_is_told_how_to_open_projects(home: RemHome):
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {"reply": "Hi."}

    home.generate = generate
    home.submit_message({"request_id": "u-1", "body": "Hello"})
    home._reply()
    assert "open_projects entries {path,create?,git_init?}" in prompts[0]
    assert "open_projects:[]" in prompts[0]
