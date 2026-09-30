from __future__ import annotations

import base64
import io
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

import pytest

from gofer.core.bundles import preview_workflow_bundle
from gofer.rattish.bundles import BUNDLE_MANIFEST, preview_rattish_bundle
from gofer.ui.organization_packages import read_zip


def _underreported_archive(compression: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name in ("manifest.json", "workflow.toml", BUNDLE_MANIFEST, "COMPANY.md"):
            archive.writestr(name, b"A" * 1024 * 1024)
    raw = bytearray(buffer.getvalue())
    with zipfile.ZipFile(buffer) as archive:
        central = archive.start_dir
        for info in archive.infolist():
            # Forge both size fields and the CRC so stdlib returns one byte
            # successfully, even though the compressed stream expands to 1 MiB.
            struct.pack_into("<I", raw, info.header_offset + 14, zlib.crc32(b"A"))
            struct.pack_into("<I", raw, info.header_offset + 22, 1)
            struct.pack_into("<I", raw, central + 16, zlib.crc32(b"A"))
            struct.pack_into("<I", raw, central + 24, 1)
            central += 46 + len(info.filename.encode()) + len(info.extra) + len(info.comment)
    return bytes(raw)


@pytest.mark.parametrize("consumer", ["workflow", "rattish", "organization"])
@pytest.mark.parametrize("compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_imports_bound_decompression_with_forged_size_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str, compression: int
) -> None:
    raw = _underreported_archive(compression)
    original = zipfile._get_decompressor  # type: ignore[attr-defined]
    allocations: list[int] = []

    class RecordingDecompressor:
        def __init__(self, wrapped: Any) -> None:
            self.wrapped = wrapped

        def decompress(self, *args: Any, **kwargs: Any) -> bytes:
            data: bytes = self.wrapped.decompress(*args, **kwargs)
            allocations.append(len(data))
            return data

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

    monkeypatch.setattr(
        zipfile, "_get_decompressor", lambda kind: RecordingDecompressor(original(kind))
    )
    bundle = tmp_path / "forged.zip"
    bundle.write_bytes(raw)
    if consumer == "organization":
        if compression == zipfile.ZIP_DEFLATED:
            assert set(read_zip(base64.b64encode(raw).decode()).values()) == {"QQ=="}
        else:
            with pytest.raises(ValueError, match="Unsupported organization ZIP member"):
                read_zip(base64.b64encode(raw).decode())
    else:
        with pytest.raises(ValueError):
            if consumer == "workflow":
                preview_workflow_bundle(bundle, data_dir=tmp_path / "target")
            else:
                preview_rattish_bundle(bundle)
    if compression == zipfile.ZIP_DEFLATED:
        assert allocations
        assert max(allocations) <= zipfile.ZipExtFile.MIN_READ_SIZE
    else:
        assert not allocations


@pytest.mark.parametrize("flags", [0x01, 0x20, 0x40])
def test_legacy_bundle_rejects_unsupported_flags_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: int
) -> None:
    raw = bytearray(_underreported_archive(zipfile.ZIP_DEFLATED))
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, 6, flags)
    struct.pack_into("<H", raw, central + 8, flags)
    bundle = tmp_path / "unsupported.zip"
    bundle.write_bytes(raw)

    def unexpected_open(*args: Any, **kwargs: Any) -> None:
        pytest.fail("An unsupported member reached decompression")

    monkeypatch.setattr(zipfile.ZipFile, "open", unexpected_open)
    with pytest.raises(ValueError, match="encrypted or patched"):
        preview_workflow_bundle(bundle, data_dir=tmp_path / "target")
