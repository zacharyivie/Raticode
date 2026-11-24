"""Optional desktop job extensions; the canonical mobile v2 contract stays frozen.

Older receivers reject these additional fields before dispatch. An organization job
or a Rem task therefore cannot silently degrade into an unmanaged job on an older
desktop.

- ``organization`` on job.submit: a turn an organization sent through a fleet target.
- ``rem_task`` on job.submit: a whole read-only Rem task (research or a conversation)
  the user pinned to the receiving desktop (gofer.ui.rem_remote_tasks). It carries the
  sending run's ID, the task's type, title, full prompt (past the 4,000-character
  ``intent`` cap, within one 64 KiB event) and its checks. The receiving desktop runs
  it as its own Rem task.
- ``usage`` on job.status: what the work used, with the provider and model that ran it.
- ``rem_task`` on job.status: the receiving desktop's run ID and, when the task saved
  one, its report's path relative to the project there.
"""

from __future__ import annotations

import copy
from typing import Any

# A Rem task's prompt and checks travel in one event, which is at most 64 KiB as UTF-8
# (gofer.devices.framing.MAX_RECORD). The sender also keeps the encoded task within
# REM_TASK_BYTES, since a character can take up to four bytes.
REM_TASK_PROMPT = 40000
REM_TASK_CHECKS = 20
REM_TASK_CHECK = 1000
REM_TASK_BYTES = 62000

_ID = {"type": "string", "minLength": 1, "maxLength": 100}

REM_TASK = {
    "type": "object",
    "properties": {
        "run_id": _ID,
        "type": {"enum": ["research", "errand"]},
        "execution_mode": {"enum": ["work", "conversation"]},
        "title": {"type": "string", "minLength": 1, "maxLength": 160},
        "prompt": {"type": "string", "minLength": 1, "maxLength": REM_TASK_PROMPT},
        "checks": {
            "type": "array",
            "maxItems": REM_TASK_CHECKS,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "description": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": REM_TASK_CHECK,
                    },
                },
                "required": ["id", "description"],
                "additionalProperties": False,
            },
        },
        "provider": {"type": "string", "minLength": 1, "maxLength": 64},
    },
    "required": ["run_id", "type", "execution_mode", "title", "prompt", "checks"],
    "additionalProperties": False,
}

REM_TASK_RESULT = {
    "type": "object",
    "properties": {
        "run_id": _ID,
        "report": {"type": ["string", "null"], "maxLength": 500},
    },
    "required": ["run_id"],
    "additionalProperties": False,
}


def event_schema(canonical: dict[str, Any]) -> dict[str, Any]:
    extended = copy.deepcopy(canonical)
    for rule in extended["allOf"]:
        kind = rule.get("if", {}).get("properties", {}).get("type", {}).get("const")
        if kind == "job.submit":
            properties = rule["then"]["properties"]["payload"]["properties"]
            properties["organization"] = {
                "type": "object",
                "properties": {
                    key: {"type": "string", "minLength": 1, "maxLength": 100}
                    for key in ("organizationId", "taskId", "parentRunId")
                },
                "required": ["organizationId", "taskId", "parentRunId"],
                "additionalProperties": False,
            }
            properties["rem_task"] = copy.deepcopy(REM_TASK)
        if kind == "job.status":
            properties = rule["then"]["properties"]["payload"]["properties"]
            usage: dict[str, Any] = {
                key: {"type": ["number", "null"], "minimum": 0}
                for key in (
                    "cost_usd",
                    "total_cost_usd",
                    "total_tokens",
                    "input_tokens",
                    "output_tokens",
                )
            }
            # The provider and model that really ran the work, on the desktop that ran it.
            usage["provider"] = {"type": "string", "minLength": 1, "maxLength": 64}
            usage["model"] = {"type": "string", "minLength": 1, "maxLength": 256}
            properties["usage"] = {
                "type": "object",
                "properties": usage,
                "additionalProperties": False,
            }
            properties["rem_task"] = copy.deepcopy(REM_TASK_RESULT)
    return extended
