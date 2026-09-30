from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
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


def test_rattish_bundle_rejects_deep_manifest_before_creating_files(tmp_path: Path) -> None:
    bundle = tmp_path / "deep.raticode"
    depth = sys.getrecursionlimit() + 100
    manifest = (
        json.dumps(
            {
                "format": "raticode-workflow",
                "version": 1,
                "workflowId": "review",
                "workflowName": "Review",
                "files": ["workflow.rattish"],
            }
        )[:-1]
        + ', "extra":'
        + "[" * depth
        + "0"
        + "]" * depth
        + "}"
    )
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(BUNDLE_MANIFEST, manifest)
        archive.writestr("workflow.rattish", "Rattish: 1\n\nWorkflow:\n  name: Review\n")
    with pytest.raises(RattishBundleError, match="manifest"):
        preview_rattish_bundle(bundle)
    with pytest.raises(RattishBundleError, match="manifest"):
        import_rattish_bundle(bundle, tmp_path / "project", registry_dir=tmp_path / "registry")
    assert not (tmp_path / "project").exists()
    assert not (tmp_path / "registry").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO and open-file replacement semantics")
def test_rattish_bundle_rejects_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    bundle = tmp_path / "pipe.raticode"
    os.mkfifo(bundle)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from gofer.rattish.bundles import preview_rattish_bundle; "
            "preview_rattish_bundle(Path(sys.argv[1]))",
            str(bundle),
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "RattishBundleError" in result.stderr
    assert "ordinary file" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX open-file replacement semantics")
def test_rattish_import_extracts_the_archive_it_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gofer.rattish.bundles as bundles

    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    note = workflow.workflow_root / "notes.txt"
    note.write_text("validated content")
    bundle = tmp_path / "review.raticode"
    export_rattish_bundle("review", bundle, registry_dir=registry)
    note.write_text("replacement content")
    replacement = tmp_path / "replacement.raticode"
    export_rattish_bundle("review", replacement, registry_dir=registry)
    original_mkdtemp = bundles.tempfile.mkdtemp

    def replace_before_extraction(*, prefix: str) -> str:
        os.replace(replacement, bundle)
        return str(original_mkdtemp(prefix=prefix))

    monkeypatch.setattr(bundles.tempfile, "mkdtemp", replace_before_extraction)
    imported = import_rattish_bundle(bundle, project, registry_dir=registry)
    assert (imported.workflow_root / "notes.txt").read_text() == "validated content"


@pytest.mark.parametrize("member", [BUNDLE_MANIFEST, "workflow.rattish"])
@pytest.mark.parametrize(
    "fault", ["encrypted", "strong-encryption", "patched", "unsupported-compression"]
)
def test_rattish_bundle_rejects_unsupported_zip_members_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str, fault: str
) -> None:
    import gofer.rattish.bundles as bundles

    bundle = tmp_path / "unsupported.raticode"
    manifest = {
        "format": "raticode-workflow",
        "version": 1,
        "workflowId": "review",
        "workflowName": "Review",
        "files": ["workflow.rattish"],
    }
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(BUNDLE_MANIFEST, json.dumps(manifest))
        archive.writestr("workflow.rattish", "Rattish: 1\n\nWorkflow:\n  name: Review\n")
    with zipfile.ZipFile(bundle) as archive:
        local_offset = archive.getinfo(member).header_offset
        central_offset = archive.start_dir
        for info in archive.infolist():
            if info.filename == member:
                break
            central_offset += 46 + len(info.filename.encode()) + len(info.extra) + len(info.comment)
    raw = bytearray(bundle.read_bytes())
    if fault != "unsupported-compression":
        flags = {"encrypted": 0x01, "strong-encryption": 0x40, "patched": 0x20}[fault]
        struct.pack_into("<H", raw, local_offset + 6, flags)
        struct.pack_into("<H", raw, central_offset + 8, flags)
    else:
        struct.pack_into("<H", raw, local_offset + 8, 99)
        struct.pack_into("<H", raw, central_offset + 10, 99)
    bundle.write_bytes(raw)

    def unexpected_staging(**kwargs: object) -> str:
        pytest.fail("An unsupported archive reached extraction staging")

    monkeypatch.setattr(bundles.tempfile, "mkdtemp", unexpected_staging)
    with pytest.raises(RattishBundleError, match="encrypted|compression|patched"):
        preview_rattish_bundle(bundle)
    with pytest.raises(RattishBundleError, match="encrypted|compression|patched"):
        import_rattish_bundle(bundle, tmp_path / "project", registry_dir=tmp_path / "registry")


@pytest.mark.parametrize("member", [BUNDLE_MANIFEST, "workflow.rattish"])
def test_rattish_bundle_reports_corrupt_deflate_as_bundle_error(
    tmp_path: Path, member: str
) -> None:
    bundle = tmp_path / "corrupt.raticode"
    manifest = {
        "format": "raticode-workflow",
        "version": 1,
        "workflowId": "review",
        "workflowName": "Review",
        "files": ["workflow.rattish"],
    }
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(BUNDLE_MANIFEST, json.dumps(manifest))
        archive.writestr("workflow.rattish", "Rattish: 1\n\nWorkflow:\n  name: Review\n")
    with zipfile.ZipFile(bundle) as archive:
        offset = archive.getinfo(member).header_offset
    raw = bytearray(bundle.read_bytes())
    name_size, extra_size = struct.unpack_from("<HH", raw, offset + 26)
    raw[offset + 30 + name_size + extra_size] = 0xFF  # Invalid DEFLATE block type.
    bundle.write_bytes(raw)
    with pytest.raises(RattishBundleError, match="Invalid .raticode bundle"):
        import_rattish_bundle(bundle, tmp_path / "project", registry_dir=tmp_path / "registry")
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "names",
    [
        ["workflow.rattish", "WORKFLOW.RATTISH"],
        ["scripts/run.py", "Scripts/other.py"],
        ["scripts", "scripts/run.py"],
        ["SCRIPTS", "scripts/run.py"],
        [BUNDLE_MANIFEST.upper()],
    ],
)
def test_rattish_bundle_rejects_colliding_paths_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str], reverse: bool
) -> None:
    import gofer.rattish.bundles as bundles

    files = sorted({"workflow.rattish", *names})
    bundle = tmp_path / "colliding.raticode"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            BUNDLE_MANIFEST,
            json.dumps(
                {
                    "format": "raticode-workflow",
                    "version": 1,
                    "workflowId": "review",
                    "workflowName": "Review",
                    "files": files,
                }
            ),
        )
        for name in reversed(files) if reverse else files:
            archive.writestr(name, "Rattish: 1\n\nWorkflow:\n  name: Review\n")

    def unexpected_staging(**kwargs: object) -> str:
        pytest.fail("A colliding archive reached extraction staging")

    monkeypatch.setattr(bundles.tempfile, "mkdtemp", unexpected_staging)
    with pytest.raises(RattishBundleError, match="[Cc]ollid"):
        preview_rattish_bundle(bundle)
    with pytest.raises(RattishBundleError, match="[Cc]ollid"):
        import_rattish_bundle(bundle, tmp_path / "project", registry_dir=tmp_path / "registry")


def test_rattish_bundle_export_rejects_case_aliases_without_replacing_output(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    original = workflow.workflow_root / "notes.txt"
    alias = workflow.workflow_root / "NOTES.txt"
    original.write_text("original")
    if alias.exists():
        pytest.skip("Source filesystem is case-insensitive")
    alias.write_text("alias")
    output = tmp_path / "review.raticode"
    output.write_bytes(b"previous export")

    with pytest.raises(RattishBundleError, match="[Cc]ollid"):
        export_rattish_bundle("review", output, registry_dir=registry)
    assert output.read_bytes() == b"previous export"


@pytest.mark.parametrize("year", [1970, 2108])
def test_rattish_bundle_export_clamps_unrepresentable_timestamps(tmp_path: Path, year: int) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    note = workflow.workflow_root / "notes.txt"
    note.write_text("Keep the contents")
    timestamp = datetime(year, 6, 1, tzinfo=UTC).timestamp()
    os.utime(note, (timestamp, timestamp))
    output = tmp_path / "review.raticode"

    export_rattish_bundle("review", output, registry_dir=registry)

    with zipfile.ZipFile(output) as archive:
        assert archive.read("notes.txt") == note.read_bytes()
        assert archive.getinfo("notes.txt").date_time == (
            (1980, 1, 1, 0, 0, 0) if year < 1980 else (2107, 12, 31, 23, 59, 58)
        )
    assert note.stat().st_mtime == timestamp
    assert "notes.txt" in preview_rattish_bundle(output).files


def test_rattish_bundle_allows_shared_directory_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    scripts = workflow.workflow_root / "scripts"
    scripts.mkdir()
    (scripts / "first.py").write_text("first")
    (scripts / "second.py").write_text("second")
    output = tmp_path / "review.raticode"

    export_rattish_bundle("review", output, registry_dir=registry)
    imported = import_rattish_bundle(output, project, registry_dir=registry)

    assert (imported.workflow_root / "scripts/first.py").read_text() == "first"
    assert (imported.workflow_root / "scripts/second.py").read_text() == "second"


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


@pytest.mark.parametrize(
    "name",
    [
        "C:/outside.txt",
        "D:outside.txt",
        "notes.txt:stream",
        "nested/C:/outside.txt",
        "NUL",
        "nested/COM1.txt",
        "nested/LPT¹.txt",
        "folder./notes.txt",
        "folder /notes.txt",
        "./workflow.rattish",
        "nested//notes.txt",
        "nested/./notes.txt",
        "nested/line\nbreak.txt",
        "nested/file?.txt",
    ],
)
def test_rattish_bundle_rejects_nonportable_paths_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    import gofer.rattish.bundles as bundles

    bundle = tmp_path / "unsafe.raticode"
    manifest = {
        "format": "raticode-workflow",
        "version": 1,
        "workflowId": "unsafe",
        "workflowName": "Unsafe",
        "files": ["workflow.rattish", name],
    }
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(BUNDLE_MANIFEST, json.dumps(manifest))
        archive.writestr("workflow.rattish", "Rattish: 1\n\nWorkflow:\n  name: Unsafe\n")
        archive.writestr(name, "must not be extracted")

    def unexpected_staging(**kwargs: object) -> str:
        pytest.fail("An unsafe archive reached extraction staging")

    monkeypatch.setattr(bundles.tempfile, "mkdtemp", unexpected_staging)
    with pytest.raises(RattishBundleError, match="Unsafe workflow bundle path"):
        preview_rattish_bundle(bundle)
    with pytest.raises(RattishBundleError, match="Unsafe workflow bundle path"):
        import_rattish_bundle(bundle, tmp_path / "project", registry_dir=tmp_path / "registry")


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot create this source filename")
def test_rattish_bundle_export_rejects_nonportable_names_without_replacing_output(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    (workflow.workflow_root / "notes.txt:stream").write_text("not portable")
    output = tmp_path / "review.raticode"
    output.write_bytes(b"previous export")

    with pytest.raises(RattishBundleError, match="Unsafe workflow bundle path"):
        export_rattish_bundle("review", output, registry_dir=registry)

    assert output.read_bytes() == b"previous export"


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable permissions")
@pytest.mark.parametrize("mode", [0o751, 0o7751])
def test_rattish_bundle_round_trip_preserves_script_permissions(tmp_path: Path, mode: int) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    registry = tmp_path / "source-registry"
    workflow = create_registered_workflow(source, "Review", registry_dir=registry)
    script = workflow.workflow_root / "review script.sh"
    script.write_text("#!/bin/sh\nprintf 'reviewed\\n'\n")
    script.chmod(mode)
    bundle = tmp_path / "review.raticode"
    export_rattish_bundle("review", bundle, registry_dir=registry)

    imported = import_rattish_bundle(bundle, target, registry_dir=tmp_path / "target-registry")
    restored = imported.workflow_root / script.name

    assert restored.read_bytes() == script.read_bytes()
    assert restored.stat().st_mode & 0o7777 == 0o751


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks needs Windows developer mode")
@pytest.mark.parametrize("swap_parent", [False, True])
def test_rattish_bundle_export_rejects_file_swapped_after_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap_parent: bool
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "registry"
    workflow = create_registered_workflow(project, "Review", registry_dir=registry)
    notes = workflow.workflow_root / "notes"
    notes.mkdir()
    note = notes / "note.txt"
    note.write_text("public note")
    outside = tmp_path / "private.txt"
    outside.write_text("private data")
    outside_directory = tmp_path / "private"
    outside_directory.mkdir()
    (outside_directory / "note.txt").write_text("private data")
    output = tmp_path / "review.raticode"
    output.write_bytes(b"previous export")
    original_is_file = Path.is_file

    def swap_after_check(path: Path) -> bool:
        result = original_is_file(path)
        if path == note:
            if swap_parent:
                notes.rename(workflow.workflow_root / "original-notes")
                notes.symlink_to(outside_directory, target_is_directory=True)
            else:
                path.unlink()
                path.symlink_to(outside)
        return result

    monkeypatch.setattr(Path, "is_file", swap_after_check)
    with pytest.raises(RattishBundleError, match="Could not export workflow bundle"):
        export_rattish_bundle("review", output, registry_dir=registry)
    assert output.read_bytes() == b"previous export"
