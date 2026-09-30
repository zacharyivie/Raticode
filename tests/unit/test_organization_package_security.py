from __future__ import annotations

import base64
import io
import os
import struct
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import organization_packages as packages
from gofer.ui.organizations import OrganizationManager


def test_package_rejects_non_string_schema():
    content = b"---\nname: Test\ndescription: Test\nslug: test\nschema: []\n---\nCompany"
    with pytest.raises(ValueError, match="schema"):
        packages.parse_package({"COMPANY.md": base64.b64encode(content).decode()})


def test_package_document_reference_must_identify_a_file():
    files = {
        "COMPANY.md": b"---\nname: Test\ndescription: Test\nslug: test\n---\nCompany",
        "agents/reviewer/AGENTS.md": b"---\nname: Reviewer\ndocs: [../../docs]\n---\nReview",
        "docs/review.md": b"Review instructions",
    }
    with pytest.raises(ValueError, match="document.*file"):
        packages.parse_package(
            {path: base64.b64encode(body).decode() for path, body in files.items()}
        )


@pytest.mark.parametrize(
    "field",
    [
        "includes: null",
        "includes: [42]",
        "sources: null",
        "sources: [{kind: 42}]",
        "skills: null",
        "docs: null",
    ],
)
def test_package_rejects_malformed_reference_fields(field):
    files = {
        "COMPANY.md": b"---\nname: Test\ndescription: Test\nslug: test\n---\nCompany",
        "agents/reviewer/AGENTS.md": f"---\nname: Reviewer\n{field}\n---\nReview".encode(),
    }
    with pytest.raises(ValueError, match="includes|sources|skills|docs"):
        packages.parse_package(
            {path: base64.b64encode(body).decode() for path, body in files.items()}
        )


@pytest.mark.parametrize(
    "filename,settings",
    [
        (".raticode.yaml", "company: []"),
        (".raticode.yaml", "agents: null"),
        (".raticode.yaml", "agents: {reviewer: []}"),
        (".raticode.yaml", "routines: {review: null}"),
        (".paperclip.yaml", "agents: []"),
        (".paperclip.yaml", "agents: {reviewer: {adapter: null}}"),
        (".paperclip.yaml", "agents: {reviewer: {adapter: {type: codex_local, config: []}}}"),
    ],
)
def test_package_rejects_malformed_provider_settings(filename, settings):
    files = {
        "COMPANY.md": b"---\nname: Test\ndescription: Test\nslug: test\n---\nCompany",
        "agents/reviewer/AGENTS.md": b"---\nname: Reviewer\n---\nReview",
        filename: f"schema: raticode/organizations/v1\n{settings}\n".encode(),
    }
    with pytest.raises(ValueError, match="company|agents|routines|adapter|config"):
        packages.parse_package(
            {path: base64.b64encode(body).decode() for path, body in files.items()}
        )


@pytest.mark.parametrize(
    "name",
    [
        ".",
        "skills/CON/SKILL.md",
        "skills/con.txt",
        "skills/COM¹/SKILL.md",
        "skills/LPT9/SKILL.md",
        "skills/NUL .md",
        "skills/CONOUT$/SKILL.md",
        "skills/review./SKILL.md",
        "skills/review /SKILL.md",
        "skills/review/SKILL.md ",
        "skills/review?/SKILL.md",
        "skills/review\n/SKILL.md",
        "folder/" * 65 + "SKILL.md",
        "x" * 4097,
    ],
)
def test_package_paths_reject_windows_aliases_and_device_names(name):
    with pytest.raises(ValueError, match="Unsafe package path"):
        packages.safe_path(name)


@pytest.mark.parametrize(
    "names",
    [
        ["skills/review/SKILL.md", "skills/Review/SKILL.md"],
        ["skills/review/SKILL.md", "skills/review/skill.md"],
        ["skills/review", "skills/review/SKILL.md"],
        ["skills/review/SKILL.md", "skills/review"],
    ],
)
def test_package_import_and_export_reject_colliding_assets(names):
    files = dict.fromkeys(names, base64.b64encode(b"content").decode())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"content")
    with pytest.raises(ValueError, match="Colliding package paths"):
        packages.read_zip(base64.b64encode(buffer.getvalue()).decode())
    with pytest.raises(ValueError, match="Colliding package paths"):
        packages.parse_package(files)
    with pytest.raises(ValueError, match="Colliding package paths"):
        packages.zip_package(files)


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_package_zip_keeps_supported_compression_and_directory_entries(compression):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        archive.writestr("skills/review/", b"")
        archive.writestr("skills/review/SKILL.md", b"review")
    assert packages.read_zip(base64.b64encode(buffer.getvalue()).decode()) == {
        "skills/review/SKILL.md": base64.b64encode(b"review").decode()
    }


@pytest.mark.parametrize(
    "compression,flags", [(99, 0), (12, 0), (14, 0), (0, 1), (0, 0x20), (0, 0x40)]
)
def test_package_zip_rejects_unsupported_members_with_a_validation_error(compression, flags):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("COMPANY.md", b"document")
    raw = bytearray(buffer.getvalue())
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<HH", raw, 6, flags, compression)
    struct.pack_into("<HH", raw, central + 8, flags, compression)
    with pytest.raises(ValueError, match="Unsupported organization ZIP member"):
        packages.read_zip(base64.b64encode(raw).decode())


def test_package_zip_rejects_nul_truncated_member_names():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("COMPANY.mdXignored", b"document")
    raw = buffer.getvalue().replace(b"COMPANY.mdXignored", b"COMPANY.md\x00ignored")
    with pytest.raises(ValueError, match="Unsafe package ZIP path"):
        packages.read_zip(base64.b64encode(raw).decode())


def test_package_zip_rejects_corrupt_compressed_data_with_a_validation_error():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("COMPANY.md", b"document")
        compressed_size = archive.getinfo("COMPANY.md").compress_size
    raw = bytearray(buffer.getvalue())
    name_length, extra_length = struct.unpack_from("<HH", raw, 26)
    start = 30 + name_length + extra_length
    # DEFLATE block type 3 is invalid and makes the decompressor raise zlib.error.
    raw[start : start + compressed_size] = b"\x06" * compressed_size
    with pytest.raises(ValueError, match="Invalid organization ZIP"):
        packages.read_zip(base64.b64encode(raw).decode())


@pytest.mark.skipif(os.name == "nt", reason="requires case-sensitive paths")
def test_directory_import_rejects_case_colliding_assets(tmp_path):
    for name in ("review", "Review"):
        if (tmp_path / "skills" / name).exists():
            pytest.skip("requires case-sensitive paths")
        (tmp_path / "skills" / name).mkdir(parents=True)
        (tmp_path / "skills" / name / "SKILL.md").write_text("review")
    with pytest.raises(ValueError, match="Colliding package paths"):
        packages.read_directory(tmp_path)


def test_package_zip_checks_file_count_before_validating_all_paths(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(1001):
            archive.writestr(f"{index}.txt", b"")

    def unexpected_validation(names):
        pytest.fail("Over-limit ZIPs must be rejected before path validation")

    monkeypatch.setattr(packages, "_validate_file_paths", unexpected_validation)
    with pytest.raises(ValueError, match="1000 files"):
        packages.read_zip(base64.b64encode(buffer.getvalue()).decode())


@pytest.mark.parametrize("style", ["flow-sequence", "flow-mapping", "block-mapping"])
def test_package_frontmatter_rejects_deep_nesting_before_loading(monkeypatch, style):
    called = False
    original_load = packages.yaml.safe_load

    def observe_load(value):
        nonlocal called
        called = True
        return original_load(value)

    monkeypatch.setattr(packages.yaml, "safe_load", observe_load)
    header = (
        "value: " + "[" * 1000 + "0" + "]" * 1000
        if style == "flow-sequence"
        else "value: " + "{key: " * 1000 + "0" + "}" * 1000
        if style == "flow-mapping"
        else "\n".join("  " * index + "key:" for index in range(100))
    )
    content = f"---\n{header}\n---\n".encode()
    with pytest.raises(ValueError, match="nesting"):
        packages.markdown(content)
    assert not called


def test_package_frontmatter_allows_normal_nested_values():
    data, body = packages.markdown(b"---\nvalues:\n  - {name: review}\n---\nInstructions")
    assert data == {"values": [{"name": "review"}]}
    assert body == "Instructions"


def test_directory_import_refuses_file_swapped_to_symlink(tmp_path, monkeypatch):
    root = tmp_path / "package"
    root.mkdir()
    entry = root / "COMPANY.md"
    entry.write_text("original")
    outside = tmp_path / "private.txt"
    outside.write_text("private content outside the import grant")
    original_stat = Path.stat
    swapped = False

    def swap_after_stat(path, *args, **kwargs):
        nonlocal swapped
        result = original_stat(path, *args, **kwargs)
        if path == entry and kwargs.get("follow_symlinks", True) and not swapped:
            swapped = True
            entry.unlink()
            entry.symlink_to(outside)
        return result

    monkeypatch.setattr(Path, "stat", swap_after_stat)
    with pytest.raises((ValueError, OSError)):
        packages.read_directory(root)
    assert swapped
    assert outside.read_text() == "private content outside the import grant"


def test_directory_import_bounds_reads_even_if_size_changes(tmp_path, monkeypatch):
    root = tmp_path / "package"
    root.mkdir()
    entry = root / "COMPANY.md"
    entry.write_bytes(b"x" * 33)
    monkeypatch.setattr(packages, "MAX_PACKAGE_BYTES", 32)
    original_stat = Path.stat

    def stale_size(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == entry:
            values = list(result)
            values[6] = 1  # The file grew after the metadata check.
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", stale_size)
    with pytest.raises(ValueError, match="16 MiB"):
        packages.read_directory(root)


def test_directory_import_keeps_nested_assets_and_ignores_git(tmp_path):
    (tmp_path / "skills/review").mkdir(parents=True)
    (tmp_path / "skills/review/SKILL.md").write_bytes(b"review instructions")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("local Git configuration")
    assert packages.read_directory(tmp_path) == {
        "skills/review/SKILL.md": base64.b64encode(b"review instructions").decode()
    }


@pytest.mark.parametrize("link_parent", [False, True])
def test_skill_materialization_does_not_write_through_links(tmp_path, link_parent):
    invoked = threading.Event()

    async def fake_stream(**options):
        invoked.set()
        yield {"type": "final", "message": {"body": "Reviewed"}}

    manager = OrganizationManager(tmp_path / "data", stream=fake_stream, start_runtime=False)
    try:
        org = manager.store.create(
            str(tmp_path),
            {
                "name": "Import test",
                "employees": [{"id": "reviewer", "name": "Reviewer", "skills": ["review"]}],
                "packageFiles": {
                    "skills/review/SKILL.md": base64.b64encode(b"review instructions").decode()
                },
            },
            "user",
            "Test skill cache",
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        protected = outside / "SKILL.md"
        protected.write_text("keep this file")
        cache = manager.data_dir / "organization-skills" / org["id"] / str(org["revision"])
        if link_parent:
            cache.mkdir(parents=True)
            (cache / "skills").symlink_to(outside, target_is_directory=True)
            (outside / "review").mkdir()
            protected = outside / "review/SKILL.md"
            protected.write_text("keep this file")
        else:
            (cache / "skills/review").mkdir(parents=True)
            (cache / "skills/review/SKILL.md").symlink_to(protected)

        def call(action: str, **params: Any) -> Any:
            return manager.call(
                str(tmp_path),
                "user",
                {"organizationId": org["id"], "action": action, "params": params},
            )

        call("task_create", title="Review", assignee="reviewer")
        call("control", state="running")
        manager.dispatch()
        for _, _, worker in list(manager._active.values()):
            worker.join(timeout=5)
            assert not worker.is_alive()
        assert protected.read_text() == "keep this file"
        if link_parent:
            assert not invoked.is_set()
        else:
            assert invoked.is_set()
            assert (cache / "skills/review/SKILL.md").read_text() == "review instructions"
    finally:
        manager.close()
