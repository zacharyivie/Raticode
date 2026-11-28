"""POST /api/devices body actions run through DeviceControl.action and DEVICE_ACTIONS.

Disposable identities and loopback addresses only; nothing here opens a real connection.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import secrets
import threading
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices import control as control_module
from gofer.devices.control import DEVICE_ACTIONS, DeviceControl, Needs
from gofer.devices.registry import PairingError
from gofer.devices.storage import StorageError
from tests.unit.test_device_application import TestSecrets

LOOPBACK = {"host": "127.0.0.1", "port": 4100}
WIFI = {"host": "192.168.1.20", "port": 4100}
CONTEXT = {"provider": "codex", "model": "cli-default"}


@pytest.fixture
def control(tmp_path, monkeypatch) -> Iterator[DeviceControl]:
    for name in (
        "RATICODE_DEVICE_EXPERIMENTAL_NETWORK",
        "RATICODE_DEVICE_HOST",
        "RATICODE_DEVICE_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    value = DeviceControl(tmp_path, store=TestSecrets())
    value.action({"action": "enable"})
    yield value
    value.close()


def paired(control: DeviceControl, role: str) -> str:
    """A device paired with this desktop: ``controller`` is a phone, ``desktop`` a desktop."""
    registry = control.registry
    assert registry is not None
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, role, registry.clock() + 300)
    registry.confirm(peer, pin.hex())
    registry.acknowledge(
        peer, pin, role, {"type": "session.ready_ack", "device_id": peer, "trust_revision": 1}
    )
    return peer


def desktop_at(control: DeviceControl, endpoint: dict[str, Any]) -> str:
    peer = paired(control, "desktop")
    assert control.registry is not None
    control.registry.save_endpoint(peer, endpoint)
    return peer


def test_every_action_has_a_handler_and_a_parity_key():
    from tests.helpers.cli_parity import body_actions

    assert len(DEVICE_ACTIONS) == 37
    assert set(DEVICE_ACTIONS) <= body_actions("POST /api/devices")
    outside = {name for name, (needs, _) in DEVICE_ACTIONS.items() if needs is Needs.NOTHING}
    assert outside == {"fleet_status", "tailnet", "tailnet_check"}
    assert DEVICE_ACTIONS["enable"][0] is Needs.LOCK


def test_actions_need_the_started_service_except_enable(tmp_path, monkeypatch):
    monkeypatch.delenv("RATICODE_DEVICE_HOST", raising=False)
    monkeypatch.delenv("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", raising=False)
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        for name in ("cancel_invitation", "fleet_status", "tailnet_check", "not_an_action"):
            with pytest.raises(StorageError, match="protected_device_service_unavailable"):
                control.action({"action": name})
        assert control.action({"action": "enable"})["enabled"] is True
        unknown: list[Any] = ["not_an_action", None, ["send"]]
        for value in unknown:
            with pytest.raises(PairingError, match="unknown_device_action"):
                control.action({"action": value})
    finally:
        control.close()


def test_set_endpoint_saves_a_loopback_address_and_needs_the_opt_in_for_wifi(control):
    peer = paired(control, "desktop")
    with pytest.raises(PairingError, match="peer_endpoint_unknown"):
        control.action({"action": "set_endpoint", "device_id": peer, "endpoint": None})
    with pytest.raises(PairingError, match="invalid_lan_endpoint"):
        control.action({"action": "set_endpoint", "device_id": peer})
    with pytest.raises(PairingError, match="experimental_network_opt_in_required"):
        control.action({"action": "set_endpoint", "device_id": peer, "endpoint": WIFI})
    with pytest.raises(PairingError, match="peer_endpoint_unknown"):
        control.registry.endpoint(peer)  # type: ignore[union-attr]
    status = control.action({"action": "set_endpoint", "device_id": peer, "endpoint": LOOPBACK})
    assert status["enabled"] is True
    assert control.registry.endpoint(peer) == LOOPBACK  # type: ignore[union-attr]
    control.experimental_network = True
    control.action({"action": "set_endpoint", "device_id": peer, "endpoint": WIFI})
    assert control.registry.endpoint(peer) == WIFI  # type: ignore[union-attr]


def test_cancel_work_queues_a_cancellation_for_sent_work(control, monkeypatch):
    dispatched: list[tuple[tuple[str, str], str, dict[str, Any]]] = []

    caller = threading.get_ident()

    def dispatch(key, peer, event, extra=None):
        # The background loop can probe the new desktop or resend queued work at any
        # moment; only the sends these actions make count here.
        if threading.get_ident() == caller:
            dispatched.append((key, peer, event))
        done: concurrent.futures.Future[str] = concurrent.futures.Future()
        done.set_result("")
        return done

    monkeypatch.setattr(control, "_dispatch", dispatch)
    peer, request = desktop_at(control, LOOPBACK), str(uuid4())
    with pytest.raises(PairingError, match="request_unavailable"):
        control.action({"action": "cancel_work", "device_id": peer, "request_id": request})
    sent = {"device_id": peer, "request_id": request, "thread_id": str(uuid4()), "text": "Hi"}
    assert control.action({"action": "send", **sent})["state"] == "queued"
    cancelled = control.action({"action": "cancel_work", "device_id": peer, "request_id": request})
    assert cancelled == {"device_id": peer, "request_id": request, "state": "queued"}
    assert [key for key, _, _ in dispatched] == [(peer, request), (peer, f"{request}:cancel")]
    assert dispatched[-1][2]["type"] == "request.cancel"
    far = desktop_at(control, WIFI)
    with pytest.raises(PairingError, match="experimental_network_opt_in_required"):
        control.action({"action": "cancel_work", "device_id": far, "request_id": request})
    assert len(dispatched) == 2


def test_thread_history_needs_a_grant_and_revoke_thread_removes_it(control):
    peer, thread = paired(control, "controller"), str(uuid4())
    body = {"device_id": peer, "thread_id": thread}
    with pytest.raises(PairingError, match="thread_grant_required"):
        control.action({"action": "thread_history", **body})
    control.application.authorize(peer, thread, dict(CONTEXT))  # type: ignore[union-attr]
    assert control.action({"action": "thread_history", **body}) == {"messages": []}
    assert [g["thread_id"] for g in control.status()["grants"]] == [thread]
    status = control.action({"action": "revoke_thread", **body})
    assert status["grants"] == []
    with pytest.raises(PairingError, match="thread_grant_required"):
        control.action({"action": "thread_history", **body})


@pytest.mark.parametrize("choice", [None, 1, "true", [], {}])
def test_share_workspace_rejects_a_non_boolean(control, choice):
    peer = paired(control, "controller")
    with pytest.raises(PairingError, match="invalid_sharing_choice"):
        control.action({"action": "share_workspace", "device_id": peer, "enabled": choice})
    # A confirmed phone shares by default, and a bad choice leaves that as it was.
    assert control.status()["workspace_peers"] == [peer]


def test_share_workspace_turns_sharing_on_and_off_for_phones_only(control):
    phone, desktop = paired(control, "controller"), paired(control, "desktop")
    off = control.action({"action": "share_workspace", "device_id": phone, "enabled": False})
    assert off["workspace_peers"] == []
    on = control.action({"action": "share_workspace", "device_id": phone, "enabled": True})
    assert on["workspace_peers"] == [phone]
    off = control.action({"action": "share_workspace", "device_id": phone, "enabled": False})
    assert off["workspace_peers"] == []
    with pytest.raises(PairingError, match="phone_required"):
        control.action({"action": "share_workspace", "device_id": desktop, "enabled": True})


def test_settings_exchange_answers_with_the_phones_queued_changes(control):
    window = {"theme": "dark"}
    answer = control.action({"action": "settings_exchange", "settings": window, "applied": []})
    assert answer == {"changes": []}


def test_workspace_poll_and_remove_follow_shared_threads(control):
    phone = paired(control, "controller")
    control.action({"action": "share_workspace", "device_id": phone, "enabled": False})
    for action in ("workspace_poll", "workspace_remove"):
        with pytest.raises(PairingError, match="workspace_sharing_disabled"):
            control.action({"action": action, "device_id": phone, "thread_id": str(uuid4())})
    control.action({"action": "share_workspace", "device_id": phone, "enabled": True})
    assert control.action({"action": "workspace_poll", "device_id": phone}) == {"threads": []}
    shared = control.action(
        {
            "action": "workspace_exchange",
            "device_id": phone,
            "metadata": {"id": "desktop-thread-1", "title": "Checks"},
            "messages": [{"id": "m1", "role": "user", "body": "Run the checks"}],
            "context": dict(CONTEXT),
        }
    )
    thread = shared["thread_id"]
    polled = control.action({"action": "workspace_poll", "device_id": phone})
    assert [item["thread_id"] for item in polled["threads"]] == [thread]
    # A thread the phone already has at this sync token is left out.
    known = {thread: shared["sync_token"]}
    unchanged = control.action({"action": "workspace_poll", "device_id": phone, "known": known})
    assert unchanged == {"threads": []}
    removed = control.action(
        {"action": "workspace_remove", "device_id": phone, "thread_id": thread}
    )
    assert removed == {"removed": True}
    assert control.action({"action": "workspace_poll", "device_id": phone}) == {"threads": []}


def test_cancel_invitation_withdraws_the_current_invitation(control):
    invitations = control.invitations
    assert invitations is not None
    invitations.create("Local test", dict(LOOPBACK), role="controller")
    assert control.status()["invitation_available"] is True
    assert control.action({"action": "cancel_invitation"})["invitation_available"] is False


def test_reconnect_runs_one_at_a_time_and_cancel_pairing_stops_it(control, monkeypatch):
    started: list[tuple[str, dict[str, Any]]] = []

    async def reconnect(registry, peer, endpoint):
        started.append((peer, endpoint))
        await asyncio.Event().wait()
        return peer

    monkeypatch.setattr(control_module, "reconnect_desktop", reconnect)
    assert control.action({"action": "cancel_pairing"})["outbound"] == "idle"
    with pytest.raises(PairingError, match="peer_unavailable"):
        control.action({"action": "reconnect", "device_id": str(uuid4())})
    far = desktop_at(control, WIFI)
    with pytest.raises(PairingError, match="experimental_network_opt_in_required"):
        control.action({"action": "reconnect", "device_id": far})
    near = desktop_at(control, LOOPBACK)
    status = control.action({"action": "reconnect", "device_id": near})
    assert status["outbound"] == "awaiting_peer_confirmation"
    with pytest.raises(PairingError, match="pairing_already_running"):
        control.action({"action": "reconnect", "device_id": near})
    deadline = time.monotonic() + 5
    while not started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started == [(near, LOOPBACK)]
    assert control.action({"action": "cancel_pairing"})["outbound"] == "failed"
