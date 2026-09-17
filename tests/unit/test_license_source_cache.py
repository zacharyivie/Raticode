"""Cached source notices retain the same lockfile verification as downloads."""

import hashlib
import importlib.util
import io
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "license_source_cache", ROOT / "scripts/collect-licenses.py"
)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


@pytest.fixture
def package():
    return {
        "name": "certifi",
        "version": "1.0",
        "sdist": {
            "url": "https://example.invalid/certifi-1.0.tar.gz",
            "hash": "sha256:" + hashlib.sha256(b"locked source").hexdigest(),
        },
    }


def test_cached_source_uses_locked_bytes_without_network(tmp_path, package):
    (tmp_path / "certifi-1.0.tar.gz").write_bytes(b"locked source")
    with patch.object(collector.urllib.request, "urlopen") as download:
        assert collector.source_bytes(package, tmp_path) == b"locked source"
    download.assert_not_called()


def test_corrupt_cached_source_fails_without_download_fallback(tmp_path, package):
    (tmp_path / "certifi-1.0.tar.gz").write_bytes(b"different source")
    with patch.object(collector.urllib.request, "urlopen") as download:
        with pytest.raises(ValueError, match="Source hash mismatch: certifi"):
            collector.source_bytes(package, tmp_path)
    download.assert_not_called()


@pytest.mark.parametrize("cache_enabled", [False, True])
@pytest.mark.parametrize("content", [b"locked source", b"different source"])
def test_uncached_source_downloads_and_verifies(tmp_path, package, cache_enabled, content):
    with patch.object(
        collector.urllib.request, "urlopen", return_value=io.BytesIO(content)
    ) as download:
        if content == b"locked source":
            assert collector.source_bytes(package, tmp_path if cache_enabled else None) == content
        else:
            with pytest.raises(ValueError, match="Source hash mismatch: certifi"):
                collector.source_bytes(package, tmp_path if cache_enabled else None)
    download.assert_called_once_with(package["sdist"]["url"], timeout=60)


def test_cached_source_still_requires_https(tmp_path, package):
    (tmp_path / "certifi-1.0.tar.gz").write_bytes(b"locked source")
    package["sdist"]["url"] = "http://example.invalid/source.tar.gz"
    with pytest.raises(ValueError, match="must use HTTPS"):
        collector.source_bytes(package, tmp_path)
