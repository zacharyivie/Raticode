from __future__ import annotations

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
from gofer.rattish.workspaces import create_registered_workflow


def test_rattish_bundle_exports_non_ignored_workspace_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    (workflow.workflow_root / "scripts").mkdir()
    (workflow.workflow_root / "scripts" / "run.py").write_text("print('ok')\n")
    (workflow.workflow_root / "notes.txt").write_text("include me\n")
    (workflow.workflow_root / "keep.env").write_text("safe fixture\n")
    (workflow.workflow_root / "secret.env").write_text("TOKEN=secret\n")
    (workflow.workflow_root / "cache").mkdir()
    (workflow.workflow_root / "cache" / "result.json").write_text("{}\n")
    (workflow.workflow_root / ".raticodeignore").write_text(
        "*.env\n!keep.env\ncache/\ncompiled/\n",
        encoding="utf-8",
    )

    bundle = tmp_path / "review.raticode"
    preview = export_rattish_bundle("review", bundle, registry_dir=registry)

    assert preview.files == (
        ".raticodeignore",
        "keep.env",
        "notes.txt",
        "scripts/run.py",
        "workflow.metadata.json",
        "workflow.rattish",
    )
    with zipfile.ZipFile(bundle) as archive:
        assert sorted(archive.namelist()) == sorted([BUNDLE_MANIFEST, *preview.files])
        manifest = json.loads(archive.read(BUNDLE_MANIFEST))
        assert manifest["workflowId"] == "review"
        assert manifest["files"] == list(preview.files)
        assert "secret.env" not in archive.namelist()
        assert "cache/result.json" not in archive.namelist()


def test_rattish_bundle_imports_into_project_and_allocates_conflicting_id(tmp_path: Path) -> None:
    source_project = tmp_path / "source"
    target_project = tmp_path / "target"
    source_project.mkdir()
    target_project.mkdir()
    source_registry = tmp_path / "source-registry"
    target_registry = tmp_path / "target-registry"
    workflow = create_registered_workflow(source_project, "Review", registry_dir=source_registry)
    (workflow.workflow_root / "prompt.md").write_text("Review this.\n", encoding="utf-8")
    bundle = tmp_path / "review.raticode"
    export_rattish_bundle("review", bundle, registry_dir=source_registry)
    create_registered_workflow(target_project, "Review", registry_dir=target_registry)

    imported = import_rattish_bundle(
        bundle,
        target_project,
        registry_dir=target_registry,
    )

    assert imported.workflow_id == "review-2"
    assert imported.name == "Review"
    assert imported.entrypoint.is_file()
    assert (imported.workflow_root / "prompt.md").read_text(encoding="utf-8") == "Review this.\n"
    assert preview_rattish_bundle(bundle).workflow_name == "Review"


def test_rattish_bundle_rejects_archive_traversal(tmp_path: Path) -> None:
    bundle = tmp_path / "unsafe.raticode"
    manifest = {
        "format": "raticode-workflow",
        "version": 1,
        "workflowId": "unsafe",
        "workflowName": "Unsafe",
        "files": ["workflow.rattish", "../outside.txt"],
    }
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(BUNDLE_MANIFEST, json.dumps(manifest))
        archive.writestr("workflow.rattish", "Rattish: 1\n\nWorkflow:\n  name: Unsafe\n")
        archive.writestr("../outside.txt", "nope")

    with pytest.raises(RattishBundleError, match="Unsafe workflow bundle path"):
        preview_rattish_bundle(bundle)


def test_rattish_bundle_requires_entrypoint_to_survive_ignore_rules(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    (workflow.workflow_root / ".raticodeignore").write_text("workflow.rattish\n")

    with pytest.raises(RattishBundleError, match="excludes required workflow.rattish"):
        export_rattish_bundle("review", tmp_path / "review.raticode", registry_dir=registry)
