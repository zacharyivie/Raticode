"""Shared renderer history, phone context revisions, pagination and revocation."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices.application import DeviceApplication
from gofer.devices.registry import PairingError
from gofer.devices.workspace import wire_id
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_pairing import registry as registry


def share(
    app: DeviceApplication, peer: str, tmp_path: Path, native: str = "desktop-thread"
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    app.workspace.enable(peer, True)
    context = {
        "title": "Shared project",
        "project_id": str(uuid4()),
        "resource_ids": [],
        "provider": "codex",
        "model": "model-a",
        "permission_mode": "read-only",
        "provider_permissions": {"codex": "read-only", "claude_code": "plan"},
        "project_path": str(tmp_path),
    }
    meta = {
        "id": native,
        "title": context["title"],
        "provider": "codex",
        "model": "model-a",
        "projectRoot": str(tmp_path),
    }
    result = app.workspace.exchange(
        peer,
        meta,
        [{"id": "old-message", "role": "user", "body": "Existing desktop history"}],
        context,
        None,
    )
    return context, meta, result


def test_disabled_sharing_and_revocation_survive_renderer_refresh(application, tmp_path):
    app, peer = application
    app.workspace.enable(peer, False)
    with pytest.raises(PairingError, match="sharing_disabled"):
        app.workspace.exchange(peer, {"id": "native"}, [], {}, None)
    context, meta, shared = share(app, peer, tmp_path)
    thread = shared["thread_id"]
    assert thread == wire_id(meta["id"])
    assert app.authorized(peer, thread)
    app.revoke_grant(peer, thread)
    assert app.workspace.exchange(peer, meta, [], context, shared["revision"]) == {"revoked": True}
    assert not app.authorized(peer, thread)


def test_phone_changes_model_without_changing_project_history_or_native_id(application, tmp_path):
    app, peer = application
    context, meta, shared = share(app, peer, tmp_path)
    app.workspace.providers = [{"id": "codex", "models": ["model-a", "model-b"]}]
    payload = {
        "expected_revision": shared["revision"],
        "project_id": context["project_id"],
        "resource_ids": [],
        "provider": "codex",
        "model": "model-b",
    }
    changed = app.handle(
        peer, event("thread.context.update", thread_id=shared["thread_id"], payload=payload)
    )[0]
    assert changed["type"] == "thread.snapshot"
    assert changed["payload"]["revision"] == 2
    exported = app.workspace.export(peer, shared["thread_id"])
    assert exported["metadata"]["id"] == meta["id"]
    assert exported["metadata"]["model"] == "model-b"
    assert exported["messages"][0]["body"] == "Existing desktop history"
    stale = app.workspace.exchange(peer, meta, [], context, 1)
    assert stale["revision"] == 2
    rejected = app.handle(
        peer,
        event("thread.context.update", sequence=1, thread_id=shared["thread_id"], payload=payload),
    )[0]
    assert rejected["payload"]["code"] == "conflict"


def test_shared_history_reaches_dispatch_and_reply_is_mirrored_once(application, tmp_path):
    app, peer = application
    _, _, shared = share(app, peer, tmp_path)
    request = event(
        thread_id=shared["thread_id"], payload={"text": "Continue", "attachment_ids": []}
    )
    app.handle(peer, request)
    claim = app.claim()
    assert claim["context"]["desktop_thread_id"] == "desktop-thread"
    assert [m["content"] for m in claim["messages"]] == ["Existing desktop history", "Continue"]
    app.complete(peer, request["request_id"], "Reply from Rem")
    assert [m["body"] for m in app.workspace.export(peer, shared["thread_id"])["messages"]] == [
        "Existing desktop history",
        "Continue",
        "Reply from Rem",
    ]
    page = app.handle(
        peer,
        event(
            "thread.history.request",
            sequence=1,
            thread_id=shared["thread_id"],
            payload={"before": None, "limit": 2},
        ),
    )[0]
    assert page["payload"]["has_more"]
    assert [m["text"] for m in page["payload"]["messages"]] == ["Continue", "Reply from Rem"]
    reply = next(e for e in app.pending(peer) if e["type"] == "chat.message")
    assert reply["payload"]["message_id"] == page["payload"]["messages"][-1]["id"]


def test_phone_creation_is_exported_to_desktop(application, tmp_path):
    app, peer = application
    context, meta, shared = share(app, peer, tmp_path)
    created = app.handle(
        peer,
        event(
            "thread.create",
            thread_id=None,
            payload={
                "title": "Phone thread",
                "template_thread_id": shared["thread_id"],
                **{k: context[k] for k in ("project_id", "resource_ids", "provider", "model")},
            },
        ),
    )[0]
    assert created["type"] == "thread.snapshot"
    result = app.workspace.export(peer, created["thread_id"])
    assert result["metadata"]["id"] != meta["id"]
    assert result["metadata"]["title"] == "Phone thread"
    assert result["metadata"]["projectRoot"] == str(tmp_path)


def test_thread_pagination_covers_more_than_one_hundred(application):
    app, peer = application
    for n in range(105):
        app.authorize(
            peer, str(uuid4()), {"title": str(n), "provider": "codex", "model": "model-a"}
        )
    seen: list[str] = []
    offset = 0
    for sequence in range(3):
        result = app.handle(
            peer,
            event(
                "sync.request",
                sequence=sequence,
                thread_id=None,
                payload={
                    "after_sequence": None,
                    "limit": 100,
                    "include_connection": True,
                    "threads_offset": offset,
                },
            ),
        )[-1]["payload"]
        seen.extend(t["thread_id"] for t in result["threads"])
        offset = result["next_threads_offset"]
    assert offset is None
    assert len(set(seen)) == 105


def test_queued_future_message_is_not_dispatched_as_part_of_current_turn(application, tmp_path):
    app, peer = application
    _, _, shared = share(app, peer, tmp_path)
    first = event(thread_id=shared["thread_id"], payload={"text": "First", "attachment_ids": []})
    second = event(
        sequence=1, thread_id=shared["thread_id"], payload={"text": "Second", "attachment_ids": []}
    )
    app.handle(peer, first)
    app.handle(peer, second)
    assert [m["content"] for m in app.claim()["messages"]] == ["Existing desktop history", "First"]


def test_desktop_history_edits_and_deletions_are_not_resurrected(application, tmp_path):
    app, peer = application
    context, meta, shared = share(app, peer, tmp_path)
    request = event(thread_id=shared["thread_id"])
    app.handle(peer, request)
    app.claim()
    app.complete(peer, request["request_id"], "Phone reply")
    complete = app.workspace.export(peer, shared["thread_id"])
    edited = [{"id": request["request_id"], "role": "user", "body": "Edited on desktop"}]
    result = app.workspace.exchange(peer, meta, edited, context, complete["revision"])
    assert result["messages"] == [{**edited[0], "origin": "phone"}]
    assert app.workspace.export(peer, shared["thread_id"])["messages"] == [
        {**edited[0], "origin": "phone"}
    ]


def test_desktop_deletion_revokes_and_removes_the_mirror(application, tmp_path):
    app, peer = application
    _, _, shared = share(app, peer, tmp_path)
    app.workspace.remove(peer, shared["thread_id"])
    assert not app.authorized(peer, shared["thread_id"])
    assert app.workspace.value(peer, shared["thread_id"]) is None


def test_attachment_metadata_survives_transfer_removal_and_renderer_round_trip(
    application, tmp_path
):
    import base64
    import hashlib

    app, peer = application
    context, meta, shared = share(app, peer, tmp_path)
    thread = shared["thread_id"]
    identifier = str(uuid4())
    data = b"# Attached on phone\n"
    app.files.offer(
        peer,
        thread,
        {
            "file_id": identifier,
            "name": "README.md",
            "mime": "text/markdown",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "expires_at": int(app.registry.clock()) + 3600,
        },
    )
    app.files.receive(
        peer,
        thread,
        {
            "file_id": identifier,
            "offset": 0,
            "eof": True,
            "data": base64.urlsafe_b64encode(data).rstrip(b"=").decode(),
        },
    )
    request = event(thread_id=thread, payload={"text": "", "attachment_ids": [identifier]})
    app.handle(peer, request)
    expected = [{"id": identifier, "name": "README.md", "size": len(data), "mime": "text/markdown"}]
    app.files.cancel(peer, thread, identifier)
    exported = app.workspace.export(peer, thread)
    user = next(m for m in exported["messages"] if m["id"] == request["request_id"])
    assert user["attachments"] == expected
    assert user["origin"] == "phone"
    app.workspace.exchange(peer, meta, exported["messages"], context, exported["revision"])
    rich = app.handle(
        peer,
        event(
            "thread.history.request",
            sequence=1,
            thread_id=thread,
            payload={"before": None, "include_presentation": True},
        ),
    )[0]["payload"]
    assert (
        next(m for m in rich["messages"] if m["id"] == request["request_id"])["attachments"]
        == expected
    )
    assert "README.md" not in str(
        app.registry.db.execute("SELECT value FROM device_message_attachments").fetchone()[0]
    )


@pytest.mark.parametrize("role", ["controller", "desktop"])
def test_new_phone_sync_default_waits_for_ack_and_survives_restart(tmp_path, role):
    from gofer.devices.registry import DeviceRegistry
    from gofer.devices.storage import Identity
    from tests.unit.test_device_application import TestSecrets

    store = TestSecrets()
    registry = DeviceRegistry(tmp_path, store, initialize=True)
    identity = Identity.create()
    peer, pin = identity.device_id, identity.pin
    app = DeviceApplication(registry)
    registry.propose(peer, pin, role, registry.clock() + 300)
    assert app.workspace.enabled() == []
    with pytest.raises(PairingError, match="identity_changed"):
        registry.confirm(peer, "0" * 64)
    assert registry.db.execute("SELECT COUNT(*) FROM device_workspace_peers").fetchone()[0] == 0
    registry.confirm(peer, pin.hex())
    assert app.workspace.enabled() == []
    registry.close()
    registry = DeviceRegistry(tmp_path, store)
    app = DeviceApplication(registry)
    ack = {"type": "session.ready_ack", "device_id": peer, "trust_revision": 1}
    registry.acknowledge(peer, pin, role, ack)
    assert app.workspace.enabled() == ([peer] if role == "controller" else [])
    if role == "controller":
        app.workspace.enable(peer, False)
        registry.close()
        registry = DeviceRegistry(tmp_path, store)
        app = DeviceApplication(registry)
        registry.acknowledge(peer, pin, role, ack)
        assert app.workspace.enabled() == []
        app.workspace.enable(peer, True)
        registry.revoke(peer)
        assert app.workspace.enabled() == []
        app.remove_peer(peer, revoked_only=True)
        registry.propose(peer, pin, role, registry.clock() + 300)
        registry.confirm(peer, pin.hex())
        registry.acknowledge(peer, pin, role, ack)
        assert app.workspace.enabled() == [peer]
    registry.close()


def test_confirmation_rolls_back_sync_default_with_trust(registry):
    from gofer.devices.storage import Identity

    identity = Identity.create()
    peer = identity.device_id
    registry.propose(peer, identity.pin, "controller", registry.clock() + 300)
    registry.db.execute("""CREATE TRIGGER fail_sync BEFORE INSERT ON device_workspace_peers
        BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        registry.confirm(peer, identity.pin.hex())
    assert registry.list()[0]["state"] == "pending"
    assert registry.trusted_pins() == frozenset()


def test_phone_organization_is_revisioned_and_preserves_context(application, tmp_path):
    app, peer = application
    context, meta, shared = share(app, peer, tmp_path)
    thread = shared["thread_id"]
    for sequence, (action, group) in enumerate(
        [("pin", "pinned"), ("unpin", "active"), ("archive", "archived"), ("restore", "active")]
    ):
        changed = app.handle(
            peer,
            event(
                "thread.manage",
                sequence=sequence,
                thread_id=thread,
                payload={"action": action, "expected_revision": sequence + 1},
            ),
        )[0]
        assert changed["payload"]["group"] == group
        assert changed["payload"]["revision"] == sequence + 2
        remote = app.workspace.export(peer, thread)
        assert remote["organization_modified"]
        assert remote["metadata"]["id"] == meta["id"]
        assert remote["metadata"]["projectRoot"] == meta["projectRoot"]
        assert remote["messages"][0]["body"] == "Existing desktop history"
    stale = app.handle(
        peer,
        event(
            "thread.manage",
            sequence=4,
            thread_id=thread,
            payload={"action": "delete", "expected_revision": 1},
        ),
    )[0]
    assert stale["payload"]["code"] == "conflict"
    assert not app.workspace.export(peer, thread)["delete_requested"]
    assert app.workspace.exchange(peer, meta, [], context, 1)["revision"] == 5


def test_phone_delete_waits_for_renderer_and_blocks_new_work(application, tmp_path):
    app, peer = application
    context, meta, shared = share(app, peer, tmp_path)
    thread = shared["thread_id"]
    request = event(
        "thread.manage", thread_id=thread, payload={"action": "delete", "expected_revision": 1}
    )
    response = app.handle(peer, request)[0]
    assert response["payload"]["delete_pending"]
    app.handle(peer, request)  # exact reconnect replay is harmless
    assert app.workspace.export(peer, thread)["revision"] == 2
    assert app.workspace.exchange(peer, meta, [], context, 2)["delete_requested"]
    assert app.workspace.export(peer, thread)["messages"]
    blocked = app.handle(peer, event(sequence=1, thread_id=thread))[0]
    assert blocked["type"] == "error"
    app.workspace.remove(peer, thread)  # local renderer acknowledged durable archive and deletion
    assert not app.authorized(peer, thread)
    assert app.workspace.exchange(peer, meta, [], context, 2) == {"revoked": True}


def test_ungranted_phone_cannot_organize_a_thread(application):
    app, peer = application
    result = app.handle(
        peer,
        event(
            "thread.manage",
            thread_id=str(uuid4()),
            payload={"action": "pin", "expected_revision": 1},
        ),
    )[0]
    assert result["payload"]["code"] == "unauthorized"


def test_new_phone_thread_uses_first_eight_words_once(application, tmp_path):
    app, peer = application
    context, _, shared = share(app, peer, tmp_path)
    created = app.handle(
        peer,
        event(
            "thread.create",
            thread_id=None,
            payload={
                "title": "New thread",
                "template_thread_id": shared["thread_id"],
                **{k: context[k] for k in ("project_id", "resource_ids", "provider", "model")},
            },
        ),
    )[0]
    thread = created["thread_id"]
    request = event(
        sequence=1,
        thread_id=thread,
        payload={
            "text": "  One two three four five six seven eight nine ten  ",
            "attachment_ids": [],
        },
    )
    app.handle(peer, request)
    app.handle(peer, request)
    remote = app.workspace.export(peer, thread)
    assert remote["metadata"]["title"] == "One two three four five six seven eight..."
    assert remote["title_modified"]
    assert remote["revision"] == 2
    app.handle(
        peer,
        event(
            sequence=2, thread_id=thread, payload={"text": "Second message", "attachment_ids": []}
        ),
    )
    assert app.workspace.export(peer, thread)["metadata"]["title"] == remote["metadata"]["title"]
