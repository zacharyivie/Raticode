"""A run's input values and trigger events, saved so the run can be resumed or run again.

Each run that took inputs or trigger events keeps them in ``<run_id>.inputs`` beside
its record ``<run_id>.json``, in the workflow's run folder. The file is created
owner-only (0600) in an owner-only folder. It never holds an input whose schema says
``format: password``, anywhere in that schema: those names are listed as withheld,
and whoever resumes or reruns the run supplies them again. Workflow secrets
(``secret["NAME"]``) are read from the secret store while a run goes and are never
run inputs, so they are never saved here either.

Limits match what a trigger can send: up to 256 file events, and a webhook body up
to 1 MiB. A run whose values don't fit saves none of them, and its record says why,
so Resume and Run again explain instead of starting with partial values.

The file goes when its run goes: ``delete_run_files`` removes the record, its stop
marker, this file and the run's file input copies (``file_inputs``) together, and
every place here that deletes a Rattish run calls it. Each run also ends by sweeping
values whose record is gone (``remove_orphaned_values``), so a run deleted some other
way, such as by hand or by an older clean-up, doesn't leave its inputs behind.
"""

from __future__ import annotations

import json
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gofer.rattish.contracts import canonical_json_bytes
from gofer.utils.atomic_output import atomic_binary_output, unlink_without_links

SAVED_VALUES_VERSION = 1
SAVED_VALUES_SUFFIX = ".inputs"
# The trigger service never sends more than these (MAX_FILE_CHANGES_PER_RUN and
# WEBHOOK_MAX_BODY_BYTES); a run started another way is held to the same limits.
MAX_SAVED_EVENTS = 256
MAX_EVENT_PAYLOAD_BYTES = 1024 * 1024
# Room for the event envelopes (paths, names, times) around those payloads.
MAX_SAVED_EVENTS_BYTES = MAX_EVENT_PAYLOAD_BYTES + 256 * 1024
MAX_SAVED_INPUTS_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class SavedRunValues:
    """What a run saved: inputs by name, withheld input names, and trigger events."""

    inputs: dict[str, Any] = field(default_factory=dict)
    withheld_inputs: tuple[str, ...] = ()
    trigger_events: tuple[dict[str, Any], ...] = ()


class SavedValuesUnavailable(RuntimeError):
    """Raised with a sentence for the user when saved values can't be used."""


def saved_values_path(record: Path) -> Path:
    """The saved values file for the run whose record is ``record``."""
    # Writes and removals refuse linked parents, so name the folder by its real path.
    return record.parent.resolve() / f"{record.stem}{SAVED_VALUES_SUFFIX}"


def _schema_is_password(schema: Any, depth: int = 0) -> bool:
    if depth > 32:
        return True
    if isinstance(schema, Mapping):
        if schema.get("format") == "password":
            return True
        return any(_schema_is_password(value, depth + 1) for value in schema.values())
    if isinstance(schema, list):
        return any(_schema_is_password(value, depth + 1) for value in schema)
    return False


def password_input_names(ir: Mapping[str, Any]) -> set[str]:
    """Declared inputs whose schema marks them, or a part of them, as a password."""
    return {
        str(item["name"])
        for item in ir["workflow"]["inputs"]
        if _schema_is_password(item.get("schema"))
    }


def _size(value: Any) -> int:
    return len(canonical_json_bytes(value))


def plan_saved_values(
    ir: Mapping[str, Any],
    supplied_inputs: Mapping[str, Any],
    trigger_events: Collection[Mapping[str, Any]] | None,
) -> tuple[SavedRunValues | None, dict[str, Any] | None]:
    """What to save for a run, and the summary its record keeps.

    Returns ``(None, None)`` for a run with nothing to save, and ``(None, summary)``
    with ``saved: False`` and a reason when its values are too large to keep.
    """
    events = [dict(event) for event in trigger_events or ()]
    if not supplied_inputs and not events:
        return None, None
    secret = password_input_names(ir)
    withheld = sorted(name for name in supplied_inputs if name in secret)
    kept = {name: value for name, value in supplied_inputs.items() if name not in secret}
    summary: dict[str, Any] = {
        "saved": True,
        "inputs": sorted(kept),
        "withheld_inputs": withheld,
        "trigger_events": len(events),
    }
    reason = ""
    try:
        if _size(kept) > MAX_SAVED_INPUTS_BYTES:
            reason = "Its input values are larger than 1 MiB, so they weren't saved."
        elif len(events) > MAX_SAVED_EVENTS:
            reason = (
                f"It was started by {len(events)} trigger events, more than the "
                f"{MAX_SAVED_EVENTS} Raticode saves."
            )
        elif any(_size(event.get("payload")) > MAX_EVENT_PAYLOAD_BYTES for event in events) or (
            _size(events) > MAX_SAVED_EVENTS_BYTES
        ):
            reason = "Its trigger event is larger than 1 MiB, so it wasn't saved."
    except (TypeError, ValueError, UnicodeError, RecursionError):
        reason = "Its values can't be saved as JSON."
    if reason:
        return None, {**summary, "saved": False, "reason": reason}
    return SavedRunValues(kept, tuple(withheld), tuple(events)), summary


def write_saved_values(record: Path, run_id: str, values: SavedRunValues) -> None:
    """Save a run's values beside its record, readable only by this user."""
    document = {
        "saved_values_version": SAVED_VALUES_VERSION,
        "run_id": run_id,
        "inputs": values.inputs,
        "withheld_inputs": list(values.withheld_inputs),
        "trigger_events": list(values.trigger_events),
    }
    # atomic_binary_output creates the file 0600 and missing folders 0700.
    with atomic_binary_output(saved_values_path(record)) as output:
        output.write(canonical_json_bytes(document) + b"\n")


def read_saved_values(record: Path, document: Mapping[str, Any]) -> SavedRunValues:
    """The values a run saved, checked against what its record says it saved.

    A run that took no inputs and no trigger events has nothing to read. Raises
    ``SavedValuesUnavailable`` with the reason when the values can't be used.
    """
    if not document.get("input_names") and not document.get("trigger_event_count"):
        return SavedRunValues()
    refusal = saved_values_refusal(document)
    if refusal is not None:
        raise SavedValuesUnavailable(refusal)
    try:
        saved = json.loads(saved_values_path(record).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SavedValuesUnavailable(
            "This run's saved input values are missing, so it can't use them. "
            "Start a new run instead."
        ) from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SavedValuesUnavailable(
            "This run's saved input values can't be read. Start a new run instead."
        ) from None
    if (
        not isinstance(saved, dict)
        or saved.get("saved_values_version") != SAVED_VALUES_VERSION
        or saved.get("run_id") != document.get("run_id")
        or not isinstance(saved.get("inputs"), dict)
        or not isinstance(saved.get("trigger_events"), list)
        or not isinstance(saved.get("withheld_inputs"), list)
    ):
        raise SavedValuesUnavailable(
            "This run's saved input values don't match its record. Start a new run instead."
        )
    return SavedRunValues(
        dict(saved["inputs"]),
        tuple(str(name) for name in saved["withheld_inputs"]),
        tuple(dict(event) for event in saved["trigger_events"] if isinstance(event, dict)),
    )


def saved_values_refusal(document: Mapping[str, Any]) -> str | None:
    """Why a run's inputs or trigger events can't be used again, from its record alone."""
    if not document.get("input_names") and not document.get("trigger_event_count"):
        return None
    summary = document.get("saved_values")
    if not isinstance(summary, Mapping):
        if document.get("input_names"):
            return (
                "This run was started with inputs before Raticode saved them. "
                "Start a new run instead."
            )
        return (
            "This run was started by an event before Raticode saved events. "
            "Start a new run instead."
        )
    if not summary.get("saved"):
        reason = str(summary.get("reason") or "Its values weren't saved.")
        return f"This run's inputs can't be used again. {reason} Start a new run instead."
    return None


def withheld_input_names(document: Mapping[str, Any]) -> list[str]:
    """Inputs the run didn't save because they are passwords; they must be given again."""
    summary = document.get("saved_values")
    if not isinstance(summary, Mapping):
        return []
    return [str(name) for name in summary.get("withheld_inputs") or []]


def merge_withheld_inputs(
    values: SavedRunValues, supplied: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Saved inputs plus the withheld ones given again; refuses missing or extra names."""
    given = dict(supplied or {})
    extra = sorted(set(given) - set(values.withheld_inputs))
    if extra:
        raise SavedValuesUnavailable(
            "Only inputs this run didn't save can be given again, not: " + ", ".join(extra) + "."
        )
    missing = [name for name in values.withheld_inputs if name not in given]
    if missing:
        raise SavedValuesUnavailable(
            "Enter "
            + ", ".join(missing)
            + " again. Raticode doesn't save password inputs, so this run needs "
            + ("it" if len(missing) == 1 else "them")
            + "."
        )
    return {**values.inputs, **given}


def delete_run_files(record: Path) -> None:
    """Delete a run: its record, stop marker, saved values and file input copies.

    Missing files are fine.
    """
    from gofer.rattish.file_inputs import remove_tree, run_files_dir

    folder = record.parent.resolve()
    remove_tree(run_files_dir(record))
    for path in (
        folder / record.name,
        folder / f"{record.stem}.stop",
        folder / f"{record.stem}{SAVED_VALUES_SUFFIX}",
    ):
        try:
            unlink_without_links(path)
        except FileNotFoundError:
            pass


# A run saves its values just before it writes its first record, so values younger than
# this may belong to a run that is still starting, in this process or another.
ORPHAN_GRACE_SECONDS = 600


def remove_orphaned_values(folder: Path, *, now: float | None = None) -> list[str]:
    """Delete saved values whose run record is gone; return the run IDs removed."""
    removed: list[str] = []
    try:
        candidates = list(folder.glob(f"*{SAVED_VALUES_SUFFIX}"))
    except OSError:
        return removed
    cutoff = (time.time() if now is None else now) - ORPHAN_GRACE_SECONDS
    for path in candidates:
        run_id = path.name.removesuffix(SAVED_VALUES_SUFFIX)
        if (folder / f"{run_id}.json").exists():
            continue
        try:
            if path.lstat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        try:
            unlink_without_links(folder.resolve() / path.name)
            removed.append(run_id)
        except OSError:
            continue
    return removed
