from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from pydantic import BaseModel, Field


class ResourceLimitError(ValueError):
    """Raised when workflow-controlled input exceeds a configured resource limit."""


class ResourceLimits(BaseModel):
    max_fanout_items: int = Field(default=1000, ge=0)
    max_files_scanned: int = Field(default=5000, ge=0)
    max_file_read_bytes: int = Field(default=1_048_576, ge=0)
    max_aggregate_read_bytes: int = Field(default=32_000_000, ge=0)
    max_vector_index_bytes: int = Field(default=50_000_000, ge=0)
    max_bundle_entries: int = Field(default=1000, ge=0)
    max_bundle_entry_bytes: int = Field(default=10_000_000, ge=0)
    max_bundle_total_uncompressed_bytes: int = Field(default=64_000_000, ge=0)
    max_bundle_compressed_bytes: int = Field(default=64_000_000, ge=0)
    max_bundle_metadata_bytes: int = Field(default=1_048_576, ge=0)
    max_bundle_compression_ratio: float = Field(default=100.0, gt=0, allow_inf_nan=False)
    max_log_message_bytes: int = Field(default=1_000, ge=0)
    max_log_bytes_per_node: int = Field(default=1_048_576, ge=0)
    max_log_bytes_per_run: int = Field(default=20_000_000, ge=0)
    max_api_request_body_bytes: int = Field(default=1_048_576, ge=0)
    max_api_log_response_bytes: int = Field(default=1_048_576, ge=0)
    max_chat_prompt_bytes: int = Field(default=128_000, ge=0)
    max_subprocess_output_bytes: int = Field(default=2_000_000, ge=0)
    max_watcher_queue_depth: int = Field(default=1000, ge=0)
    max_watcher_concurrency: int = Field(default=2, ge=1)
    max_fanout_concurrency: int = Field(default=1, ge=1)


DEFAULT_RESOURCE_LIMITS = ResourceLimits()

BUNDLE_RESOURCE_LIMIT_ENV: dict[str, str] = {
    "GOFER_BUNDLE_MAX_ENTRIES": "max_bundle_entries",
    "GOFER_BUNDLE_MAX_ENTRY_BYTES": "max_bundle_entry_bytes",
    "GOFER_BUNDLE_MAX_TOTAL_UNCOMPRESSED_BYTES": "max_bundle_total_uncompressed_bytes",
    "GOFER_BUNDLE_MAX_COMPRESSED_BYTES": "max_bundle_compressed_bytes",
    "GOFER_BUNDLE_MAX_METADATA_BYTES": "max_bundle_metadata_bytes",
    "GOFER_BUNDLE_MAX_COMPRESSION_RATIO": "max_bundle_compression_ratio",
}


def bundle_resource_limits_from_env(
    base: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
) -> ResourceLimits:
    overrides: dict[str, Any] = {}
    for env_name, field_name in BUNDLE_RESOURCE_LIMIT_ENV.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        if field_name == "max_bundle_compression_ratio":
            overrides[field_name] = float(raw)
        else:
            overrides[field_name] = int(raw)
    if not overrides:
        return base
    return ResourceLimits.model_validate({**base.model_dump(), **overrides})


def require_limit(actual: int, limit: int, label: str) -> None:
    if actual > limit:
        raise ResourceLimitError(f"{label} exceeded limit {limit} bytes (got {actual} bytes)")


@contextmanager
def _open_regular_binary_input(path: Path) -> Iterator[BinaryIO]:
    # A FIFO can replace a file after stat. Open without waiting for a writer,
    # then check the actual descriptor before reading any content.
    def nonblocking_open(name: str, flags: int) -> int:
        return os.open(name, flags | getattr(os, "O_NONBLOCK", 0))

    with open(path, "rb", opener=nonblocking_open) as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise OSError(f"{path} is not an ordinary file")
        yield source


def read_bytes_limited(path: Path, *, max_bytes: int) -> bytes:
    size = path.stat().st_size
    require_limit(size, max_bytes, f"{path} size")
    with _open_regular_binary_input(path) as source:
        data = source.read(max_bytes + 1)
    require_limit(len(data), max_bytes, f"{path} size")
    return data


def read_text_limited(
    path: Path,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    max_bytes: int,
) -> str:
    data = read_bytes_limited(path, max_bytes=max_bytes)
    # Match Path.read_text's universal newline handling after decoding, including
    # encodings where a newline occupies more than one byte.
    return data.decode(encoding, errors=errors).replace("\r\n", "\n").replace("\r", "\n")
