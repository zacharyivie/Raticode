from __future__ import annotations

import json
from pathlib import Path

import pytest

from gofer.rattish.workspaces import (
    DEFAULT_RATICODE_IGNORE,
    RattishWorkspaceError,
    create_registered_workflow,
    discover_registered_workflows,
    find_registered_workflow,
    list_registered_workflows,
)


def test_create_registered_workflow_uses_project_raticode_layout(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "app-data"

    workflow = create_registered_workflow(project, "Review PR", registry_dir=registry)

    expected_root = project / ".raticode" / "review-pr"
    assert workflow.workflow_id == "review-pr"
    assert workflow.project_root == project
    assert workflow.workflow_root == expected_root
    assert workflow.entrypoint == expected_root / "workflow.rattish"
    assert workflow.entrypoint.read_text(encoding="utf-8") == (
        'Rattish: 1\n\nWorkflow:\n  name: "Review PR"\n'
    )
    assert (expected_root / "workflow.metadata.json").read_text(encoding="utf-8").endswith("\n")
    assert (expected_root / ".raticodeignore").read_text(
        encoding="utf-8"
    ) == DEFAULT_RATICODE_IGNORE
    artifacts = list((expected_root / "compiled").glob("*.json"))
    assert len(artifacts) == 1
    assert not (registry / "radish" / "artifacts").exists()
    artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact["ir"]["workflow"]["id"] == "review-pr"
    assert find_registered_workflow("REVIEW-PR", registry_dir=registry) == workflow


def test_workflow_ids_are_allocated_globally_and_projects_remain_groupable(tmp_path: Path) -> None:
    first_project = tmp_path / "first-project"
    second_project = tmp_path / "second-project"
    first_project.mkdir()
    second_project.mkdir()
    registry = tmp_path / "app-data"

    first = create_registered_workflow(first_project, "Build", registry_dir=registry)
    second = create_registered_workflow(second_project, "Build", registry_dir=registry)
    third = create_registered_workflow(first_project, "Build", registry_dir=registry)

    assert [first.workflow_id, second.workflow_id, third.workflow_id] == [
        "build",
        "build-2",
        "build-3",
    ]
    registered = list_registered_workflows(registry_dir=registry)
    assert {item.project_root for item in registered} == {first_project, second_project}
    registry_document = json.loads(
        (registry / "radish" / "workspace-registry.json").read_text(encoding="utf-8")
    )
    assert [item["id"] for item in registry_document["workflows"]] == [
        "build",
        "build-3",
        "build-2",
    ]


def test_create_registered_workflow_does_not_modify_an_existing_directory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    existing = project / ".raticode" / "build"
    existing.mkdir(parents=True)
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    workflow = create_registered_workflow(project, "Build", registry_dir=tmp_path / "app-data")

    assert workflow.workflow_id == "build-2"
    assert marker.read_text(encoding="utf-8") == "keep"


def test_create_registered_workflow_requires_an_existing_project_folder(tmp_path: Path) -> None:
    with pytest.raises(RattishWorkspaceError, match="does not exist"):
        create_registered_workflow(
            tmp_path / "missing",
            "Build",
            registry_dir=tmp_path / "app-data",
        )


def test_discover_registered_workflows_only_checks_raticode_directories(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    raticode_workflow = project / ".raticode" / "review"
    custom_workflow = project / "automations" / "daily"
    ignored_workflow = project / "node_modules" / "dependency"
    for directory in (raticode_workflow, custom_workflow, ignored_workflow):
        directory.mkdir(parents=True)
    source = 'Rattish: 1\n\nWorkflow:\n  name: "Existing"\n'
    (raticode_workflow / "workflow.rattish").write_text(source, encoding="utf-8")
    (raticode_workflow / "helper.rattish").write_text(source, encoding="utf-8")
    (custom_workflow / "daily.rattish").write_text(source, encoding="utf-8")
    (ignored_workflow / "ignored.rattish").write_text(source, encoding="utf-8")
    registry = tmp_path / "app-data"

    first = discover_registered_workflows(project, registry_dir=registry)
    second = discover_registered_workflows(project, registry_dir=registry)

    assert [(workflow.workflow_id, workflow.entrypoint) for workflow in first] == [
        ("review", raticode_workflow / "workflow.rattish"),
    ]
    assert second == first
    assert list_registered_workflows(registry_dir=registry) == tuple(
        sorted(first, key=lambda workflow: (str(workflow.project_root), workflow.workflow_id))
    )
    assert (raticode_workflow / "workflow.rattish").read_text(encoding="utf-8") == source


def test_discovery_removes_previous_project_registrations_outside_raticode(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    workflow_root = project / ".raticode" / "kept"
    stray_root = project / "fixtures" / "mistaken-workflow"
    other_project = tmp_path / "other-project"
    other_root = other_project / "fixtures" / "existing-workflow"
    for directory in (workflow_root, stray_root, other_root):
        directory.mkdir(parents=True)
    source = 'Rattish: 1\n\nWorkflow:\n  name: "Existing"\n'
    (workflow_root / "workflow.rattish").write_text(source, encoding="utf-8")
    (stray_root / "workflow.rattish").write_text(source, encoding="utf-8")
    (other_root / "workflow.rattish").write_text(source, encoding="utf-8")
    registry = tmp_path / "app-data"
    registry_path = registry / "radish" / "workspace-registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "registry_version": 1,
                "workflows": [
                    {
                        "id": "stray",
                        "name": "Stray",
                        "project_root": str(project),
                        "workflow_root": str(stray_root),
                        "entrypoint": str(stray_root / "workflow.rattish"),
                        "created_at": "2026-09-01T00:00:00+00:00",
                    },
                    {
                        "id": "other",
                        "name": "Other",
                        "project_root": str(other_project),
                        "workflow_root": str(other_root),
                        "entrypoint": str(other_root / "workflow.rattish"),
                        "created_at": "2026-09-01T00:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    discovered = discover_registered_workflows(project, registry_dir=registry)

    assert [(workflow.workflow_id, workflow.workflow_root) for workflow in discovered] == [
        ("kept", workflow_root),
    ]
    assert [
        (workflow.workflow_id, workflow.workflow_root)
        for workflow in list_registered_workflows(registry_dir=registry)
    ] == [("kept", workflow_root)]
    persisted = json.loads(registry_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in persisted["workflows"]] == ["other", "kept"]
    assert (stray_root / "workflow.rattish").read_text(encoding="utf-8") == source


@pytest.mark.parametrize("action", ["create", "install", "discover"])
def test_workspace_operations_reject_linked_workspace_roots(tmp_path: Path, action: str) -> None:
    from gofer.rattish.workspaces import install_registered_workflow

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".raticode").symlink_to(outside, target_is_directory=True)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "workflow.rattish").write_text("Rattish: 1\n\nWorkflow:\n  name: test\n")
    with pytest.raises((OSError, RattishWorkspaceError)):
        if action == "create":
            create_registered_workflow(project, "test", registry_dir=tmp_path / "data")
        elif action == "install":
            install_registered_workflow(
                project, stage, "test", "test", registry_dir=tmp_path / "data"
            )
        else:
            discover_registered_workflows(project, registry_dir=tmp_path / "data")
    assert list(outside.iterdir()) == []
