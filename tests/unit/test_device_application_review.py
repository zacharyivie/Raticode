"""Independent regressions for durable device authorization and replay boundaries."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices.application import DeviceApplication
from gofer.devices.registry import DeviceRegistry, PairingError


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value


@pytest.fixture
def app_peer(tmp_path: Path) -> Iterator[tuple[DeviceApplication, str]]:
    registry = DeviceRegistry(tmp_path, MemorySecrets(), initialize=True)
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "desktop", registry.clock() + 300)
    registry.confirm(peer, pin.hex())
    registry.acknowledge(
        peer,
        pin,
        "desktop",
        {
            "type": "session.ready_ack",
            "device_id": peer,
            "trust_revision": 1,
        },
    )
    app = DeviceApplication(registry)
    yield app, peer
    registry.close()


def chat(thread: str | None, sequence: int = 0, text: str = "first") -> dict[str, Any]:
    return {
        "version": 2,
        "id": str(uuid4()),
        "request_id": str(uuid4()),
        "thread_id": thread,
        "sequence": sequence,
        "type": "chat.submit",
        "payload": {"text": text, "attachment_ids": []},
    }


def authorize(app: DeviceApplication, peer: str, thread: str) -> dict[str, Any]:
    context = {
        "provider": "codex",
        "model": "cli-default",
        "project_id": str(uuid4()),
        "project_path": "/disposable",
        "policy_revision": 1,
    }
    app.authorize(peer, thread, context)
    return context


def test_thread_claim_excludes_future_queued_prompts(
    app_peer: tuple[DeviceApplication, str],
) -> None:
    app, peer = app_peer
    thread = str(uuid4())
    authorize(app, peer, thread)
    first, second = chat(thread), chat(thread, 1, "future secret prompt")
    app.handle(peer, first)
    app.handle(peer, second)
    claimed = app.claim()
    assert claimed is not None
    assert claimed["messages"] == [{"role": "user", "content": "first"}]
    assert app.claim() is None
    app.complete(peer, first["request_id"], "answer")
    next_claim = app.claim()
    assert next_claim is not None
    assert next_claim["event"]["request_id"] == second["request_id"]


def test_grant_replacement_stops_old_running_and_queued_work(
    app_peer: tuple[DeviceApplication, str],
) -> None:
    app, peer = app_peer
    thread = str(uuid4())
    authorize(app, peer, thread)
    first = chat(thread)
    app.handle(peer, first)
    app.handle(peer, chat(thread, 1))
    app.claim()
    authorize(app, peer, thread)
    assert not app.runnable(peer, first["request_id"])
    assert app.claim() is None


def job(
    app: DeviceApplication,
    thread: str,
    context: dict[str, Any],
    sequence: int = 0,
    job_id: str | None = None,
) -> dict[str, Any]:
    return chat(thread, sequence) | {
        "type": "job.submit",
        "payload": {
            "job_id": job_id or str(uuid4()),
            "target_device_id": app.registry.identity.device_id,
            "project_id": context["project_id"],
            "resource_ids": [],
            "intent": "authorized work",
            "expected_policy_revision": 1,
        },
    }


def test_controller_never_claims_fleet_job(app_peer: tuple[DeviceApplication, str]) -> None:
    app, peer = app_peer
    thread = str(uuid4())
    context = authorize(app, peer, thread)
    app.registry.db.execute("UPDATE peers SET role='controller' WHERE device_id=?", (peer,))
    result = app.handle(peer, job(app, thread, context))
    assert result[-1]["payload"]["state"] == "rejected"
    assert app.claim() is None


def test_same_job_id_new_request_cannot_execute_twice(
    app_peer: tuple[DeviceApplication, str],
) -> None:
    app, peer = app_peer
    thread = str(uuid4())
    context = authorize(app, peer, thread)
    first = job(app, thread, context)
    app.handle(peer, first)
    second = job(app, thread, context, 1, first["payload"]["job_id"])
    try:
        app.handle(peer, second)
    except PairingError:
        pass
    app.claim()
    app.complete(peer, first["request_id"], "done")
    assert app.claim() is None


def test_outbound_request_id_conflict_does_not_silently_reuse_old_work(
    app_peer: tuple[DeviceApplication, str],
) -> None:
    app, peer = app_peer
    request = {"request_id": str(uuid4()), "thread_id": str(uuid4()), "text": "first"}
    app.queue_remote(peer, request)
    with pytest.raises(PairingError, match="conflict"):
        app.queue_remote(peer, request | {"text": "different operation"})


def test_incremental_sync_always_has_correlated_response(
    app_peer: tuple[DeviceApplication, str],
) -> None:
    app, peer = app_peer
    request = chat(None) | {"type": "sync.request", "payload": {"after_sequence": 0, "limit": 1}}
    result = app.handle(peer, request)
    assert result[-1]["type"] == "sync.snapshot"
    assert result[-1]["request_id"] == request["request_id"]
