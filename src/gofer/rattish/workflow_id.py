"""The rule every workflow ID follows; IDs name folders, so the rule keeps them safe."""

from __future__ import annotations

import re

WORKFLOW_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,127}")


def validate_workflow_id(value: str) -> str:
    if not WORKFLOW_ID_PATTERN.fullmatch(value):
        raise ValueError("Workflow id must match [a-z0-9][a-z0-9-]{0,127}")
    return value
