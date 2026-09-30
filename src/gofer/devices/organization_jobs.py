"""Optional desktop job extension; the canonical mobile v2 contract stays frozen.

Older receivers reject these additional fields before dispatch. An organization job
therefore cannot silently degrade into an unmanaged job on an older desktop.
"""

from __future__ import annotations

import copy
from typing import Any


def event_schema(canonical: dict[str, Any]) -> dict[str, Any]:
    extended = copy.deepcopy(canonical)
    for rule in extended["allOf"]:
        kind = rule.get("if", {}).get("properties", {}).get("type", {}).get("const")
        if kind == "job.submit":
            rule["then"]["properties"]["payload"]["properties"]["organization"] = {
                "type": "object",
                "properties": {
                    key: {"type": "string", "minLength": 1, "maxLength": 100}
                    for key in ("organizationId", "taskId", "parentRunId")
                },
                "required": ["organizationId", "taskId", "parentRunId"],
                "additionalProperties": False,
            }
        if kind == "job.status":
            rule["then"]["properties"]["payload"]["properties"]["usage"] = {
                "type": "object",
                "properties": {
                    key: {"type": ["number", "null"], "minimum": 0}
                    for key in (
                        "cost_usd",
                        "total_cost_usd",
                        "total_tokens",
                        "input_tokens",
                        "output_tokens",
                    )
                },
                "additionalProperties": False,
            }
    return extended
