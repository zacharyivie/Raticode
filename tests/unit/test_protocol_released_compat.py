"""The desktop keeps talking to phones that run the last released app, both ways.

``tests/fixtures/protocol-released/<version>/`` is a frozen copy of
``src/gofer/devices/protocol/v2`` from the commit the last phone app was released from.
A phone validates what it receives against its own copy of ``event.schema.json``, which
refuses unknown fields, and it keeps sending what that release's fixtures show.

- Desktop to phone: every current fixture must still fit the released schemas. A new
  field may break that only when the desktop sends it to phones that ask for it, and
  ``GATED`` names the request flag that asks. For each gated field a test drives the real
  desktop code with a phone that didn't ask and checks the field is left out.
- Phone to desktop: every message the released phone sends must pass the current
  desktop validator.

docs/releasing.md says how to replace the frozen copy when a new phone app is released.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from gofer.devices.application import _VALIDATOR
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "src/gofer/devices/protocol/v2"
RELEASED_ROOT = ROOT / "tests/fixtures/protocol-released"
# Schemas of their own; every other fixture is an event.
NAMED_SCHEMAS = ("invitation", "frame", "session")
# Event types the phone app sends (mobile/android/app/src/main). The desktop answers with
# the rest.
PHONE_SENDS = frozenset(
    {
        "approval.request",
        "approval.resolve",
        "catalog.request",
        "chat.submit",
        "desktop.action",
        "desktop.read",
        "file.cancel",
        "file.chunk",
        "file.offer",
        "file.request",
        "file.status",
        "fleet.request",
        "request.cancel",
        "swarm.control",
        "swarm.message",
        "swarm.request",
        "sync.request",
        "thread.context.update",
        "thread.create",
        "thread.history.request",
        "thread.manage",
        "thread.search.request",
    }
)


def _released() -> Path:
    [folder] = [p for p in RELEASED_ROOT.iterdir() if p.is_dir()]
    return folder


RELEASED = _released()


def _load(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text())
    return value


def _validators(folder: Path) -> dict[str, Draft202012Validator]:
    return {
        name: Draft202012Validator(_load(folder / f"{name}.schema.json"))
        for name in (*NAMED_SCHEMAS, "event")
    }


def _fixtures(folder: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted((folder / "fixtures").glob("*.json")):
        yield path.stem, _load(path)


# --- Gated fields ---------------------------------------------------------------------


def _remote_approvals(app: Any, peer: str, data_dir: Path) -> None:
    from gofer.core.approvals import ApprovalRequest, ApprovalStore

    app.remote.data_dir = data_dir
    app.remote.enable(peer, True)
    ApprovalStore(data_dir).create_or_update(
        ApprovalRequest(
            workflow_id="deploy",
            run_id="run1",
            node_id="channel",
            message="Which channel?",
            options=["Stable", "Beta"],
            reply_mode="optional",
        )
    )
    ApprovalStore(data_dir).create_or_update(
        ApprovalRequest(workflow_id="deploy", run_id="run1", node_id="plain", message="Ship it?")
    )


def _approval_items(app: Any, peer: str, sequence: int, payload: dict[str, Any]) -> list[Any]:
    [snapshot] = app.handle(
        peer, event("approval.request", sequence, thread_id=None, payload=payload)
    )
    assert snapshot["type"] == "approval.snapshot"
    items: list[Any] = snapshot["payload"]["approvals"]
    return items


def _approval_choices(app: Any, peer: str, data_dir: Path, field: str) -> None:
    """RemoteControl._snapshot leaves out options and reply unless the phone asked."""
    _remote_approvals(app, peer, data_dir)
    old = _approval_items(app, peer, 1, {})
    assert old, "an older phone still gets the plain approval"
    assert all(field not in item for item in old)
    new = _approval_items(app, peer, 2, {"include_choices": True})
    assert any(field in item for item in new), "the flag is what adds the field"


@dataclass(frozen=True)
class Gated:
    kind: str
    # Keys under the event's payload; "*" is every item of a list.
    path: tuple[str, ...]
    # The request flag or capability a phone sends to get the field.
    flag: str
    # Drives the real desktop code with a phone without the flag: (app, peer, data_dir).
    stripped: Callable[[Any, str, Path], None]


GATED = (
    Gated(
        "approval.snapshot",
        ("approvals", "*", "options"),
        "approval.request include_choices",
        lambda app, peer, data: _approval_choices(app, peer, data, "options"),
    ),
    Gated(
        "approval.snapshot",
        ("approvals", "*", "reply"),
        "approval.request include_choices",
        lambda app, peer, data: _approval_choices(app, peer, data, "reply"),
    ),
)


def _strip(value: Any, path: tuple[str, ...]) -> None:
    head, rest = path[0], path[1:]
    if head == "*":
        children = value if isinstance(value, list) else []
    elif isinstance(value, dict) and head in value:
        if not rest:
            del value[head]
            return
        children = [value[head]]
    else:
        children = []
    for child in children:
        _strip(child, rest)


def _as_an_old_phone_gets_it(record: dict[str, Any], gated: tuple[Gated, ...]) -> dict[str, Any]:
    stripped = copy.deepcopy(record)
    for entry in gated:
        if record.get("type") == entry.kind and isinstance(stripped.get("payload"), dict):
            _strip(stripped["payload"], entry.path)
    return stripped


def _too_new(
    fixtures: Iterator[tuple[str, dict[str, Any]]], gated: tuple[Gated, ...] = GATED
) -> list[str]:
    """Current fixtures that the released phone app would refuse, gated fields left out."""
    released = _validators(RELEASED)
    known = set(_load(RELEASED / "event.schema.json")["properties"]["type"]["enum"])
    failures = []
    for stem, record in fixtures:
        schema = stem if stem in NAMED_SCHEMAS else "event"
        if schema == "event" and record.get("type") not in known:
            continue  # A new type: the released phone app never asks for it.
        candidate = _as_an_old_phone_gets_it(record, gated)
        error = next(iter(released[schema].iter_errors(candidate)), None)
        if error is not None:
            where = "/".join(str(p) for p in error.absolute_path)
            failures.append(f"{stem}: {where}: {error.message[:300]}")
    return failures


def _schema_field(schema: dict[str, Any], kind: str, path: tuple[str, ...]) -> bool:
    for rule in schema["allOf"]:
        if rule.get("if", {}).get("properties", {}).get("type", {}).get("const") != kind:
            continue
        nodes = [rule["then"]["properties"]["payload"]]
        for key in path:
            found = []
            while nodes:
                node = nodes.pop()
                nodes.extend(node.get("oneOf", []) + node.get("anyOf", []))
                child = node.get("items") if key == "*" else node.get("properties", {}).get(key)
                if isinstance(child, dict):
                    found.append(child)
            nodes = found
        if nodes:
            return True
    return False


# --- Tests ----------------------------------------------------------------------------


def test_a_new_desktop_field_an_old_phone_cant_read_fails_without_a_gate() -> None:
    failures = _too_new(_fixtures(CURRENT))
    assert failures == [], (
        f"The phone app released with {RELEASED.name} refuses these current fixtures. "
        "Leave the new field out for phones that don't ask for it, and add it to GATED "
        "with the flag that asks:\n" + "\n".join(failures)
    )


def test_the_check_catches_a_new_field_unless_it_is_gated() -> None:
    record = _load(RELEASED / "fixtures/thread.snapshot.json")
    record["payload"]["only_new_phones_read_this"] = True
    [failure] = _too_new(iter([("thread.snapshot", record)]))
    assert failure.startswith("thread.snapshot: payload:")
    assert "only_new_phones_read_this" in failure
    gate = Gated("thread.snapshot", ("only_new_phones_read_this",), "x", lambda *_: None)
    assert _too_new(iter([("thread.snapshot", record)]), (gate,)) == []


def test_a_released_phone_message_still_passes_the_desktop_validator() -> None:
    checked = []
    failures = []
    for stem, record in _fixtures(RELEASED):
        if stem in NAMED_SCHEMAS or record["type"] not in PHONE_SENDS:
            continue
        checked.append(stem)
        error = next(iter(_VALIDATOR.iter_errors(record)), None)
        if error is not None:
            failures.append(f"{stem}: {error.message[:300]}")
    assert len(checked) >= 20
    assert failures == [], (
        f"The desktop now refuses what the phone app released with {RELEASED.name} "
        "sends:\n" + "\n".join(failures)
    )


def test_every_phone_message_type_is_one_the_released_schema_knows() -> None:
    known = set(_load(RELEASED / "event.schema.json")["properties"]["type"]["enum"])
    assert PHONE_SENDS <= known


def test_every_gated_field_is_still_in_the_current_schema() -> None:
    # Like SECRET_OPTION_EXCEPTIONS in test_cli_client.py: no entry outlives its field.
    schema = _load(CURRENT / "event.schema.json")
    missing = [entry for entry in GATED if not _schema_field(schema, entry.kind, entry.path)]
    assert missing == []


@pytest.mark.parametrize("entry", GATED, ids=lambda e: f"{e.kind}:{'.'.join(e.path)}")
def test_a_gated_field_is_left_out_for_a_phone_without_its_flag(
    entry: Gated, application: Any, tmp_path: Path
) -> None:
    app, peer = application
    entry.stripped(app, peer, tmp_path / "data")


def test_the_frozen_copy_is_unchanged_since_its_release() -> None:
    sums = {}
    for line in (RELEASED / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        sums[name] = digest
    files = {
        p.relative_to(RELEASED).as_posix()
        for p in RELEASED.rglob("*")
        if p.is_file() and p.name != "SHA256SUMS"
    }
    assert files == set(sums)
    for name, digest in sums.items():
        assert hashlib.sha256((RELEASED / name).read_bytes()).hexdigest() == digest, name
