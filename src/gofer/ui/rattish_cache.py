"""Studio API for a workflow's result cache: how much it holds, and clearing it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.rattish.result_cache import ResultCache, is_cacheable_workflow_id


class ResultCacheRequestError(ValueError):
    """A request named no workflow or an invalid one."""


def _workflow_id(workflow_id: str | None) -> str:
    if not workflow_id:
        raise ResultCacheRequestError("Name the workflow with workflowId.")
    if not is_cacheable_workflow_id(workflow_id):
        raise ResultCacheRequestError("Invalid workflow id.")
    return workflow_id


def cache_payload(base: Path, workflow_id: str | None) -> dict[str, Any]:
    """What Workflow settings > Result cache shows."""
    safe_id = _workflow_id(workflow_id)
    stats = ResultCache(base).stats(safe_id)
    return {
        "workflowId": safe_id,
        "entries": stats.entries,
        "bytes": stats.bytes,
        "maxBytes": stats.max_bytes,
    }


def clear_cache_payload(base: Path, workflow_id: str | None) -> dict[str, Any]:
    safe_id = _workflow_id(workflow_id)
    removed = ResultCache(base).clear(safe_id)
    return {**cache_payload(base, safe_id), "removed": removed}
