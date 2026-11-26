"""Opening a project finds every workflow.rattish below it, not only those in .raticode."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gofer.rattish import project_scan
from gofer.rattish.workspaces import (
    RattishWorkspaceError,
    delete_registered_workflow,
    discover_project_workflows,
    find_registered_workflow,
    list_registered_workflows,
)

SOURCE = 'Rattish: 1\n\nWorkflow:\n  name: "{name}"\n'


def _workflow(folder: Path, name: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    entrypoint = folder / "workflow.rattish"
    entrypoint.write_text(SOURCE.format(name=name or folder.name), encoding="utf-8")
    return entrypoint


def _ids(workflows) -> dict[str, str]:
    return {workflow.relative_folder: workflow.workflow_id for workflow in workflows}


def _tree(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*")}


def test_nested_starters_and_managed_workflows_are_discovered(tmp_path: Path) -> None:
    project = tmp_path / "Raticode"
    for starter in ("jev-demo", "daily-report", "test-and-fix"):
        _workflow(project / "rattish" / "starters" / starter)
    _workflow(project / ".raticode" / "review")
    _workflow(project / "automations" / "nightly" / "cleanup")
    top = _workflow(project, "Top level")
    registry = tmp_path / "data"

    discovered = discover_project_workflows(project, registry_dir=registry).workflows

    assert _ids(discovered) == {
        ".raticode/review": "review",
        ".": "raticode",
        "rattish/starters/daily-report": "daily-report",
        "rattish/starters/jev-demo": "jev-demo",
        "rattish/starters/test-and-fix": "test-and-fix",
        "automations/nightly/cleanup": "cleanup",
    }
    by_id = {workflow.workflow_id: workflow for workflow in discovered}
    assert by_id["review"].managed is True
    assert by_id["jev-demo"].managed is False
    assert by_id["raticode"].entrypoint == top
    payload = by_id["jev-demo"].to_payload()
    assert payload["relativeFolder"] == "rattish/starters/jev-demo"
    assert payload["managed"] is False
    # Every discovered workflow is listed, found by ID and reachable by the rest of the app.
    assert {w.workflow_id for w in list_registered_workflows(registry_dir=registry)} == set(by_id)
    assert find_registered_workflow("jev-demo", registry_dir=registry).entrypoint == (
        project / "rattish" / "starters" / "jev-demo" / "workflow.rattish"
    )
    # Discovering again changes nothing.
    assert discover_project_workflows(project, registry_dir=registry).workflows == discovered


@pytest.mark.parametrize(
    "folder",
    [
        "node_modules/package/examples",
        "frontend/node_modules/pkg",
        ".git/hooks",
        ".venv/lib/examples",
        "venv/share",
        "src/__pycache__",
        "build/output",
        "dist/bundle",
        "website/.vitepress/cache/deps",
        "website/.vitepress/dist",
        ".mypy_cache/x",
        ".worktrees/feature/rattish/starters/jev-demo",
        "rem-worktrees/task/flows",
        "organization-worktrees/org/flows",
        ".taskurotta/legacy",
        ".raticode/review/subflows/child",
        "VENDOR/Node_Modules/pkg",
    ],
)
def test_skipped_folders_are_not_searched(tmp_path: Path, folder: str) -> None:
    project = tmp_path / "project"
    _workflow(project / folder)
    _workflow(project / "flows" / "kept")

    discovered = discover_project_workflows(project, registry_dir=tmp_path / "data").workflows

    assert _ids(discovered) == {"flows/kept": "kept"}


def test_marked_folders_and_nested_worktrees_are_skipped(tmp_path: Path) -> None:
    project = tmp_path / "project"
    # A virtual environment with any name, and a cache that tags itself.
    _workflow(project / "env-py311" / "share")
    (project / "env-py311" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    _workflow(project / "target" / "debug")
    (project / "target" / "CACHEDIR.TAG").write_text(
        "Signature: 8a477f597d28d172789f06886806bc55\n"
    )
    # A git worktree checked out inside the project, under any folder name.
    _workflow(project / "checkouts" / "feature" / "flows" / "copy")
    (project / "checkouts" / "feature" / ".git").write_text(
        f"gitdir: {project}/.git/worktrees/feature\n"
    )
    # A submodule's .git file is not a worktree: its workflows stay.
    _workflow(project / "vendor" / "lib" / "flows" / "sub")
    (project / "vendor" / "lib" / ".git").write_text("gitdir: ../../.git/modules/lib\n")
    # A Raticode data folder inside the project: its managed folders are skipped.
    data_folder = project / "raticode-data"
    (data_folder / "radish").mkdir(parents=True)
    (data_folder / "radish" / "workspace-registry.json").write_text(
        '{"registry_version": 1, "workflows": []}'
    )
    _workflow(data_folder / "workspaces" / "copy")
    _workflow(data_folder / "rem-work" / "task")
    _workflow(project / "flows" / "kept")

    discovered = discover_project_workflows(project, registry_dir=tmp_path / "data").workflows

    assert sorted(_ids(discovered)) == ["flows/kept", "vendor/lib/flows/sub"]


def test_the_app_data_folder_inside_a_project_is_skipped(tmp_path: Path) -> None:
    project = tmp_path / "home"
    data_dir = project / "app-data"
    _workflow(data_dir / "anything")
    _workflow(project / "flows" / "kept")

    discovered = discover_project_workflows(project, registry_dir=data_dir).workflows

    assert _ids(discovered) == {"flows/kept": "kept"}


def test_worktree_copies_of_starters_are_not_listed_twice(tmp_path: Path) -> None:
    project = tmp_path / "Raticode"
    _workflow(project / "rattish" / "starters" / "jev-demo")
    _workflow(project / ".worktrees" / "feature-a" / "rattish" / "starters" / "jev-demo")
    _workflow(project / ".worktrees" / "feature-b" / "rattish" / "starters" / "jev-demo")
    (project / ".worktrees" / "feature-a" / ".git").write_text(
        f"gitdir: {project}/.git/worktrees/feature-a\n"
    )
    registry = tmp_path / "data"

    discovered = discover_project_workflows(project, registry_dir=registry).workflows

    assert [(w.workflow_id, w.relative_folder) for w in discovered] == [
        ("jev-demo", "rattish/starters/jev-demo")
    ]
    assert len(list_registered_workflows(registry_dir=registry)) == 1


def test_same_folder_names_get_unique_stable_ids_from_their_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _workflow(project / ".raticode" / "daily")
    _workflow(project / "team-a" / "daily")
    _workflow(project / "team-b" / "daily")
    _workflow(project / "deep" / "team-a" / "daily")
    registry = tmp_path / "data"

    first = _ids(discover_project_workflows(project, registry_dir=registry).workflows)

    # The managed workflow keeps the plain name; the others use their folder path.
    assert first == {
        ".raticode/daily": "daily",
        "team-a/daily": "team-a-daily",
        "team-b/daily": "team-b-daily",
        "deep/team-a/daily": "deep-team-a-daily",
    }
    assert len(set(first.values())) == len(first)
    # A new workflow with a clashing path slug never takes an existing ID.
    _workflow(project / "team" / "a-daily")
    second = _ids(discover_project_workflows(project, registry_dir=registry).workflows)
    assert {key: second[key] for key in first} == first
    assert second["team/a-daily"] == "a-daily"
    _workflow(project / "x" / "team-a-daily")
    third = _ids(discover_project_workflows(project, registry_dir=registry).workflows)
    assert third["x/team-a-daily"] == "x-team-a-daily"


def test_ids_stay_unique_across_projects(tmp_path: Path) -> None:
    registry = tmp_path / "data"
    first_project = tmp_path / "one"
    second_project = tmp_path / "two"
    _workflow(first_project / "flows" / "report")
    _workflow(second_project / "flows" / "report")

    first = discover_project_workflows(first_project, registry_dir=registry).workflows
    second = discover_project_workflows(second_project, registry_dir=registry).workflows

    assert [w.workflow_id for w in first] == ["report"]
    assert [w.workflow_id for w in second] == ["flows-report"]


def test_symlinks_out_of_the_project_are_refused(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside_source = _workflow(outside / "flows" / "secret")
    # A linked folder and a linked workflow file are both ignored.
    (project / "linked").symlink_to(outside, target_is_directory=True)
    (project / "file-link").mkdir()
    (project / "file-link" / "workflow.rattish").symlink_to(outside_source)
    registry = tmp_path / "data"

    assert discover_project_workflows(project, registry_dir=registry).workflows == ()

    # A registration that reaches the file through a link is not listed or found.
    registry_path = registry / "radish" / "workspace-registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    linked_root = project / "linked" / "flows" / "secret"
    registry_path.write_text(
        json.dumps(
            {
                "registry_version": 1,
                "workflows": [
                    {
                        "id": "escaped",
                        "name": "Escaped",
                        "project_root": str(project),
                        "workflow_root": str(linked_root),
                        "entrypoint": str(linked_root / "workflow.rattish"),
                        "created_at": "2026-11-01T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert list_registered_workflows(registry_dir=registry) == ()
    with pytest.raises(RattishWorkspaceError):
        find_registered_workflow("escaped", registry_dir=registry)
    with pytest.raises(RattishWorkspaceError):
        delete_registered_workflow("escaped", registry_dir=registry)
    assert outside_source.is_file()


def test_registration_in_a_skipped_folder_is_hidden(tmp_path: Path) -> None:
    project = tmp_path / "project"
    hidden_root = project / "node_modules" / "pkg"
    _workflow(hidden_root)
    registry = tmp_path / "data"
    registry_path = registry / "radish" / "workspace-registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "registry_version": 1,
                "workflows": [
                    {
                        "id": "pkg",
                        "name": "pkg",
                        "project_root": str(project),
                        "workflow_root": str(hidden_root),
                        "entrypoint": str(hidden_root / "workflow.rattish"),
                        "created_at": "2026-11-01T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert list_registered_workflows(registry_dir=registry) == ()


def test_deleted_or_moved_project_workflows_are_forgotten(tmp_path: Path) -> None:
    project = tmp_path / "project"
    entrypoint = _workflow(project / "flows" / "report")
    managed = _workflow(project / ".raticode" / "kept")
    registry = tmp_path / "data"
    assert _ids(discover_project_workflows(project, registry_dir=registry).workflows) == {
        ".raticode/kept": "kept",
        "flows/report": "report",
    }

    entrypoint.unlink()
    managed.unlink()
    discovered = discover_project_workflows(project, registry_dir=registry).workflows

    assert discovered == ()
    persisted = json.loads((registry / "radish" / "workspace-registry.json").read_text())
    # Managed registrations are left alone, as before.
    assert [item["id"] for item in persisted["workflows"]] == ["kept"]
    # Putting it back gives it the same ID.
    _workflow(project / "flows" / "report")
    assert _ids(discover_project_workflows(project, registry_dir=registry).workflows) == {
        "flows/report": "report"
    }


def test_walk_limits_stop_deep_and_huge_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    deep = project.joinpath(*(f"d{index}" for index in range(project_scan.MAX_DEPTH + 2)))
    _workflow(deep)
    _workflow(project / "flows" / "shallow")
    registry = tmp_path / "data"

    discovery = discover_project_workflows(project, registry_dir=registry)

    assert discovery.limited is True
    assert _ids(discovery.workflows) == {"flows/shallow": "shallow"}

    monkeypatch.setattr(project_scan, "MAX_DIRECTORIES", 3)
    wide = tmp_path / "wide"
    for index in range(10):
        (wide / f"folder-{index}").mkdir(parents=True)
    scan = project_scan.scan_project_workflows(wide.resolve())
    assert scan.limited is True
    assert scan.directories == 3

    monkeypatch.setattr(project_scan, "MAX_WORKFLOWS", 2)
    many = tmp_path / "many"
    for index in range(5):
        _workflow(many / f"flow-{index}")
    scan = project_scan.scan_project_workflows(many.resolve())
    assert scan.limited is True
    assert len(scan.entrypoints) == 2


def test_discovery_stays_fast_on_a_large_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    # 4,000 dependency folders that must be pruned, not walked.
    for package in range(200):
        for child in range(20):
            (project / "node_modules" / f"pkg-{package}" / f"lib-{child}").mkdir(parents=True)
    # 1,500 ordinary source folders that are walked.
    for area in range(30):
        for module in range(50):
            (project / "src" / f"area-{area}" / f"module-{module}").mkdir(parents=True)
    for index in range(20):
        _workflow(project / "flows" / f"flow-{index}")
    registry = tmp_path / "data"

    started = time.perf_counter()
    scan = project_scan.scan_project_workflows(project.resolve(), data_dir=registry)
    elapsed = time.perf_counter() - started
    discovered = discover_project_workflows(project, registry_dir=registry).workflows

    assert len(scan.entrypoints) == 20
    assert len(discovered) == 20
    assert scan.directories < 1_700  # node_modules was never entered
    assert elapsed < 2.0, f"walk took {elapsed:.2f}s"


def test_project_workflows_run_without_writing_into_the_project(tmp_path: Path) -> None:
    from gofer.cli.main import app
    from gofer.rattish.run_service import rattish_run_directory
    from gofer.rattish.storage import registered_workflow_root
    from gofer.ui.api import open_project_payload

    project = tmp_path / "project"
    (project / "notes").mkdir(parents=True)
    (project / "notes" / "hello.txt").write_text("hello from the project", encoding="utf-8")
    flow = project / "automations" / "greet"
    flow.mkdir(parents=True)
    (flow / "workflow.rattish").write_text(
        "Rattish: 1\nWorkflow:\n  name: Greet\n"
        "Node read:\n  type: bash-command\n  command: cat notes/hello.txt\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    before = _tree(project)

    opened = open_project_payload(project, registry_dir=data_dir)
    [summary] = opened["workflows"]
    assert opened["discoveryLimited"] is False
    assert summary["id"] == "greet"
    assert summary["status"] == "Ready", summary["validationError"]
    assert summary["relativeFolder"] == "automations/greet"
    assert summary["managed"] is False

    result = CliRunner().invoke(app, ["workflow", "run", "greet", "--data-dir", str(data_dir)])
    assert result.exit_code == 0, result.output

    # Compiled builds and run records live in the app's data folder.
    assert registered_workflow_root("greet", data_dir) is None
    run_folder = rattish_run_directory(data_dir, "greet")
    assert run_folder.is_relative_to(data_dir.resolve())
    [record] = list(run_folder.glob("*.json"))
    # The command ran in the project folder, so its relative path resolved there.
    assert "hello from the project" in record.read_text(encoding="utf-8")
    assert _tree(project) == before


def test_workflow_at_the_project_top_is_not_bundled_with_the_project(tmp_path: Path) -> None:
    from gofer.rattish.bundles import (
        RattishBundleError,
        duplicate_rattish_workspace,
        export_rattish_bundle,
    )

    project = tmp_path / "project"
    _workflow(project, "Top")
    (project / ".env").write_text("TOKEN=secret\n")
    nested = _workflow(project / "flows" / "parent")
    _workflow(project / "flows" / "parent" / "child")
    (nested.parent / "helper.sh").write_text("echo hi\n")
    (nested.parent / ".env").write_text("TOKEN=secret\n")
    registry = tmp_path / "data"
    ids = _ids(discover_project_workflows(project, registry_dir=registry).workflows)

    with pytest.raises(RattishBundleError, match="own folder"):
        export_rattish_bundle(ids["."], tmp_path / "top.raticode", registry_dir=registry)

    preview = export_rattish_bundle(
        ids["flows/parent"], tmp_path / "parent.raticode", registry_dir=registry
    )
    # Default ignore rules apply and the nested workflow is a separate workflow.
    assert set(preview.files) == {"helper.sh", "workflow.rattish"}

    copy = duplicate_rattish_workspace(ids["flows/parent"], "Parent copy", registry_dir=registry)
    assert copy.managed is True
    assert copy.workflow_root.parent == project.resolve() / ".raticode"
    assert sorted(path.name for path in copy.workflow_root.iterdir() if path.is_file()) == [
        ".raticodeignore",
        "helper.sh",
        "workflow.metadata.json",
        "workflow.rattish",
    ]


def test_cli_list_project_discovers_and_shows_folders(tmp_path: Path) -> None:
    from gofer.cli.main import app

    project = tmp_path / "project"
    _workflow(project / "rattish" / "starters" / "jev-demo", "Jev demo")
    _workflow(project / ".raticode" / "review", "Review")
    registry = tmp_path / "data"
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["rattish", "list", "--project", str(project), "--registry-dir", str(registry)],
    )
    assert result.exit_code == 0, result.output
    assert "jev-demo  jev-demo  (rattish/starters/jev-demo)" in result.output
    assert "review  review\n" in result.output

    result = runner.invoke(
        app,
        ["rattish", "list", "--format", "json", "--registry-dir", str(registry)],
    )
    payload = json.loads(result.stdout)
    [group] = payload["projects"]
    assert {(w["id"], w["relativeFolder"], w["managed"]) for w in group["workflows"]} == {
        ("jev-demo", "rattish/starters/jev-demo", False),
        ("review", ".raticode/review", True),
    }

    missing = runner.invoke(
        app,
        ["rattish", "list", "--project", str(tmp_path / "nope"), "--registry-dir", str(registry)],
    )
    assert missing.exit_code == 2


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_project_root_given_through_a_link_is_resolved(tmp_path: Path) -> None:
    project = tmp_path / "real"
    _workflow(project / "flows" / "report")
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    registry = tmp_path / "data"

    [workflow] = discover_project_workflows(alias, registry_dir=registry).workflows

    assert workflow.project_root == project.resolve()
    assert list_registered_workflows(registry_dir=registry) == (workflow,)
