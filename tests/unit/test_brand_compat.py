"""Upgrade regressions for the Raticode file and registry names."""

import json
import zipfile
from pathlib import Path

import pytest

from gofer.rattish.bundles import (
    BUNDLE_MANIFEST,
    RattishBundleError,
    export_rattish_bundle,
    import_rattish_bundle,
    preview_rattish_bundle,
)
from gofer.rattish.workspaces import (
    WORKFLOW_IGNORE,
    WORKSPACE_DIRECTORY,
    RattishWorkspaceError,
    create_registered_workflow,
    delete_registered_workflow,
    discover_registered_workflows,
    find_registered_workflow,
    list_registered_workflows,
)
from gofer.utils.brand_compat import (
    LEGACY_BUNDLE_FORMATS,
    LEGACY_WORKFLOW_IGNORES,
    LEGACY_WORKSPACE_DIRECTORIES,
)


@pytest.mark.parametrize(
    "workspace_directory,workflow_ignore",
    zip(LEGACY_WORKSPACE_DIRECTORIES, LEGACY_WORKFLOW_IGNORES),
)
def test_previous_workspace_remains_discoverable_and_exports_with_current_names(
    tmp_path: Path, workspace_directory: str, workflow_ignore: str
):
    project = tmp_path / "project"
    root = project / workspace_directory / "review"
    root.mkdir(parents=True)
    (root / "workflow.rattish").write_text("Rattish: 1\nWorkflow:\n  name: Review\n")
    (root / workflow_ignore).write_text(".env\n")
    (root / ".env").write_text("PRIVATE_FIXTURE=excluded\n")
    registry = tmp_path / "registry"
    (workflow,) = discover_registered_workflows(project, registry_dir=registry)
    assert list_registered_workflows(registry_dir=registry) == (workflow,)
    bundle = tmp_path / "review.raticode"
    export_rattish_bundle(workflow.workflow_id, bundle, registry_dir=registry)
    with zipfile.ZipFile(bundle) as archive:
        assert WORKFLOW_IGNORE in archive.namelist()
        assert workflow_ignore not in archive.namelist()
        assert ".env" not in archive.namelist()
        assert BUNDLE_MANIFEST in archive.namelist()
    delete_registered_workflow(workflow.workflow_id, registry_dir=registry)
    assert not root.exists()


@pytest.mark.parametrize("workspace_directory", LEGACY_WORKSPACE_DIRECTORIES)
def test_renamed_workspace_keeps_registered_id_and_creation_time(
    tmp_path: Path, workspace_directory: str
):
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    registry_file = registry / "radish/workspace-registry.json"
    data = json.loads(registry_file.read_text())
    entry = data["workflows"][0]
    for key in ("workflow_root", "entrypoint"):
        entry[key] = entry[key].replace(WORKSPACE_DIRECTORY, workspace_directory)
    registry_file.write_text(json.dumps(data))
    assert find_registered_workflow(workflow.workflow_id, registry_dir=registry) == workflow
    assert discover_registered_workflows(project, registry_dir=registry) == (workflow,)


@pytest.mark.parametrize(
    "bundle_manifest,bundle_format,workflow_ignore",
    [
        (*item, ignore)
        for item, ignore in zip(LEGACY_BUNDLE_FORMATS.items(), LEGACY_WORKFLOW_IGNORES)
    ],
)
def test_previous_bundle_imports_with_current_ignore_file(
    tmp_path: Path, bundle_manifest: str, bundle_format: str, workflow_ignore: str
):
    bundle = tmp_path / "review.bundle"
    manifest = {
        "format": bundle_format,
        "version": 1,
        "workflowId": "review",
        "workflowName": "Review",
        "files": ["workflow.rattish", workflow_ignore],
    }
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(bundle_manifest, json.dumps(manifest))
        archive.writestr("workflow.rattish", "Rattish: 1\nWorkflow:\n  name: Review\n")
        archive.writestr(workflow_ignore, ".env\n")
    project = tmp_path / "project"
    project.mkdir()
    imported = import_rattish_bundle(bundle, project, registry_dir=tmp_path / "registry")
    assert imported.workflow_root.parent.name == WORKSPACE_DIRECTORY
    assert (imported.workflow_root / WORKFLOW_IGNORE).read_text() == ".env\n"
    assert not (imported.workflow_root / workflow_ignore).exists()
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(BUNDLE_MANIFEST, json.dumps(manifest))
    with pytest.raises(RattishBundleError, match="exactly one"):
        preview_rattish_bundle(bundle)


@pytest.mark.parametrize("workspace_directory", LEGACY_WORKSPACE_DIRECTORIES)
def test_previous_workspace_symlink_is_rejected(tmp_path: Path, workspace_directory: str):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / workspace_directory).symlink_to(outside, target_is_directory=True)
    with pytest.raises(RattishWorkspaceError, match="symbolic link"):
        discover_registered_workflows(project, registry_dir=tmp_path / "registry")
