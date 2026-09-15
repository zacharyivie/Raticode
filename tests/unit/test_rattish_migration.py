from pathlib import Path

import pytest

from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.migration import migrate_source
from gofer.rattish.workspaces import discover_registered_workflows, list_registered_workflows

LEGACY_SOURCE = (
    "Radish: 1\n\nWorkflow:\n  name: Legacy\n\n"
    "Node hello:\n  type: bash-command\n  command: echo hello\n"
)


def test_discovery_migrates_legacy_source_and_preserves_identity(tmp_path: Path) -> None:
    folder = tmp_path / "project" / ".raticode" / "demo"
    folder.mkdir(parents=True)
    source = folder / "workflow.rad"
    source.write_text(LEGACY_SOURCE)
    data = tmp_path / "data"
    first = discover_registered_workflows(tmp_path / "project", registry_dir=data)[0]
    assert first.entrypoint == source.with_suffix(".rattish")
    assert first.entrypoint.read_text() == LEGACY_SOURCE
    assert not source.exists()
    assert list_registered_workflows(registry_dir=data) == (first,)
    assert discover_registered_workflows(tmp_path / "project", registry_dir=data) == (first,)
    compiled = compile_rattish_file(source, data_dir=data)
    assert compiled.source_path == first.entrypoint
    assert len(compiled.ir["nodes"]) == 1


def test_existing_registry_keeps_id_and_creation_time(tmp_path: Path) -> None:
    import json

    project = tmp_path / "project"
    root = project / ".taskurotta" / "old"
    root.mkdir(parents=True)
    old = root / "workflow.rad"
    old.write_text(LEGACY_SOURCE)
    registry = tmp_path / "data" / "radish" / "workspace-registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "registry_version": 1,
                "workflows": [
                    {
                        "id": "existing-id",
                        "name": "Old",
                        "project_root": str(project),
                        "workflow_root": str(root),
                        "entrypoint": str(old),
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        )
    )
    workflow = list_registered_workflows(registry_dir=tmp_path / "data")[0]
    assert workflow.workflow_id == "existing-id"
    assert workflow.created_at == "2026-01-01T00:00:00Z"
    assert workflow.entrypoint == old.with_suffix(".rattish")
    assert workflow.entrypoint.exists()


def test_conflict_never_overwrites_source(tmp_path: Path) -> None:
    old = tmp_path / "workflow.rad"
    new = tmp_path / "workflow.rattish"
    old.write_text("old")
    new.write_text("new")
    with pytest.raises(FileExistsError, match="already exists"):
        migrate_source(old)
    assert old.read_text() == "old"
    assert new.read_text() == "new"


def test_migration_preserves_bytes_mode_and_old_references(tmp_path: Path) -> None:
    old = tmp_path / "custom.RAD"
    old.write_bytes(LEGACY_SOURCE.replace("\n", "\r\n").encode())
    old.chmod(0o640)
    new = migrate_source(old)
    assert new.suffix == ".rattish"
    assert new.read_bytes() == LEGACY_SOURCE.replace("\n", "\r\n").encode()
    assert new.stat().st_mode & 0o777 == 0o640
    assert migrate_source(old) == new


def test_migration_leaves_symbolic_links_alone(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text(LEGACY_SOURCE)
    old = tmp_path / "link.rad"
    old.symlink_to(target)
    assert migrate_source(old) == old
    assert old.is_symlink()


def test_legacy_child_reference_survives_migration(tmp_path: Path) -> None:
    child = tmp_path / "child.rad"
    child_source = LEGACY_SOURCE.replace("  name: Legacy", "  name: Legacy\n  interface-version: 1")
    child.write_text(child_source)
    parent = tmp_path / "parent.rattish"
    parent.write_text(
        "Rattish: 1\nWorkflow:\n  name: Parent\n"
        "Node child:\n  type: workflow\n  workflow-path: child.rad\n"
    )
    for _ in range(2):
        compiled = compile_rattish_file(parent, data_dir=tmp_path / "data")
        assert compiled.ir["nodes"][0]["type"] == "workflow"
    assert not child.exists()
    assert child.with_suffix(".rattish").read_text() == child_source


def test_import_migrates_legacy_bundle_entrypoint(tmp_path: Path) -> None:
    import json
    import zipfile

    from gofer.rattish.bundles import BUNDLE_MANIFEST, import_rattish_bundle

    bundle = tmp_path / "legacy.raticode"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            BUNDLE_MANIFEST,
            json.dumps(
                {
                    "format": "raticode-workflow",
                    "version": 1,
                    "workflowId": "legacy",
                    "workflowName": "Legacy",
                    "files": ["workflow.rad"],
                }
            ),
        )
        archive.writestr("workflow.rad", LEGACY_SOURCE)
    project = tmp_path / "project"
    project.mkdir()
    workflow = import_rattish_bundle(bundle, project, registry_dir=tmp_path / "data")
    assert workflow.entrypoint.name == "workflow.rattish"
    assert workflow.entrypoint.read_text() == LEGACY_SOURCE
    assert not (workflow.workflow_root / "workflow.rad").exists()


def test_concurrent_discovery_can_migrate_the_same_file(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    old = tmp_path / 'workflow.rad'
    old.write_text(LEGACY_SOURCE)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: migrate_source(old), range(32)))
    assert all(path == old.with_suffix('.rattish') for path in results)
    assert results[0].read_text() == LEGACY_SOURCE
