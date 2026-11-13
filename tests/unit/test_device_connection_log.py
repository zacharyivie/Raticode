"""Paired-device connections leave readable, privacy-safe lines in the app log."""

from __future__ import annotations

import asyncio
import errno
import logging
from types import SimpleNamespace

import pytest

from gofer.devices import control as control_module
from gofer.devices.client import pair_desktop, reconnect_desktop
from gofer.devices.control import DeviceControl
from gofer.devices.diagnostics import (
    Throttle,
    address_kind,
    duration,
    ending,
    repeated,
    route_label,
)
from gofer.devices.framing import FrameError
from gofer.devices.pairing import Invitations
from gofer.devices.registry import DeviceRegistry, PairingError
from gofer.devices.service import DeviceListener
from gofer.devices.tailnet import Detection
from gofer.devices.tls import DeviceTLSError
from tests.unit.test_device_application import TestSecrets

SERVICE = "gofer.devices.service"


def _messages(caplog: pytest.LogCaptureFixture, name: str = SERVICE) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == name]


def test_routes_and_addresses_are_described_without_the_address():
    assert route_label("100.101.102.103") == "Tailscale"
    assert route_label("192.168.1.20") == "the local network"
    assert route_label("127.0.0.1") == "this computer"
    assert route_label(None) == "an unknown route"
    assert address_kind("100.64.0.9") == "Tailscale"
    assert address_kind("10.0.0.3") == "local network"
    assert address_kind("8.8.8.8") == "public internet"
    assert address_kind("fe80::1") == "link-local"
    assert address_kind("not an address") == "unknown"


def test_endings_use_fixed_codes_and_plain_reasons():
    assert ending(None) == ("closed_by_peer", "the device closed the connection")
    assert ending(asyncio.IncompleteReadError(b"", 4))[0] == "closed_by_peer"
    assert ending(TimeoutError())[0] == "idle_timeout"
    assert ending(PairingError("peer_unavailable")) == (
        "peer_unavailable",
        "this device is no longer paired",
    )
    assert ending(PairingError("some_new_code")) == ("some_new_code", "refused (some_new_code)")
    code, reason = ending(DeviceTLSError("tls_authentication_failed"))
    assert code == "tls_authentication_failed" and "old pairing" in reason
    assert ending(FrameError("invalid_json"))[1].endswith("(invalid_json)")
    assert ending(ConnectionResetError())[0] == "connection_reset"
    assert ending(OSError(errno.EHOSTUNREACH, "secret detail"))[1] == "network error EHOSTUNREACH"
    assert ending(asyncio.CancelledError())[0] == "listener_closed"
    assert ending(None, replaced=True)[0] == "replaced"


def test_throttle_reports_how_many_repeats_were_skipped():
    throttle = Throttle(60, limit=2)
    assert throttle.check("a", now=0) == (True, 0)
    assert throttle.check("a", now=10) == (False, 0)
    assert throttle.check("a", now=20) == (False, 0)
    assert throttle.check("a", now=61) == (True, 2)
    assert repeated(2) == " (2 similar events since the last report)"
    assert repeated(0) == ""
    throttle.check("b", now=0)
    throttle.check("c", now=0)
    assert "a" not in throttle.last  # Bounded: the oldest key is forgotten.


def test_durations_read_naturally():
    assert duration(4.4) == "4 s"
    assert duration(125) == "2 min 5 s"
    assert duration(3600 * 3 + 60) == "3 h 1 min"
    assert duration(86400 * 3) == "3 days"


async def _pair(tmp_path) -> tuple[DeviceRegistry, DeviceRegistry, DeviceListener, int]:
    a = DeviceRegistry(tmp_path / "a", TestSecrets(), initialize=True)
    b = DeviceRegistry(tmp_path / "b", TestSecrets(), initialize=True)
    invites = Invitations(a)
    listener = DeviceListener(a, invites)
    port = await listener.start()
    payload = invites.create("Desktop A", {"host": "127.0.0.1", "port": port}, role="desktop")
    task = asyncio.create_task(pair_desktop(b, payload["uri"]))
    async with asyncio.timeout(5):
        while not a.list():
            await asyncio.sleep(0.01)
        a.confirm(b.identity.device_id, b.identity.pin.hex())
        await task
        while a.list()[0]["state"] != "active":
            await asyncio.sleep(0.01)
    return a, b, listener, port


async def _settle(listener: DeviceListener) -> None:
    async with asyncio.timeout(5):
        while listener.connections:
            await asyncio.sleep(0.01)


async def test_pairing_sign_in_disconnect_and_reconnect_are_logged_without_secrets(
    tmp_path, caplog
):
    caplog.set_level(logging.DEBUG, logger="gofer")
    a, b, listener, port = await _pair(tmp_path)
    try:
        await _settle(listener)
        peer = b.identity.device_id
        await reconnect_desktop(b, a.identity.device_id, {"host": "127.0.0.1", "port": port})
        await _settle(listener)
        lines = _messages(caplog)
        assert any(
            line.startswith("A new device started pairing over this computer") for line in lines
        )
        signed_in = [line for line in lines if " connected over this computer" in line]
        assert len(signed_in) == 2, lines
        assert signed_in[0].startswith(f"Paired desktop {peer[:8]} connected")
        assert "newly paired" in signed_in[0]
        assert "resumed" in signed_in[1] and "back " in signed_in[1]
        ended = [line for line in lines if "disconnected from this computer after" in line]
        assert len(ended) == 2, lines
        assert all(line.endswith("the device closed the connection") for line in ended)
        # Never a pin, fingerprint, full device ID or invitation material.
        secrets = {
            peer,
            a.identity.device_id,
            b.identity.pin.hex(),
            a.identity.pin.hex(),
        }
        for record in caplog.records:
            text = record.getMessage()
            assert not any(secret in text for secret in secrets), text
            assert "raticode://" not in text
    finally:
        await listener.close()
        a.close()
        b.close()


async def test_a_removed_device_that_keeps_retrying_is_explained_once(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="gofer")
    a, b, listener, port = await _pair(tmp_path)
    try:
        await _settle(listener)
        a.revoke(b.identity.device_id)
        caplog.clear()
        for _attempt in range(3):
            with pytest.raises(Exception):  # noqa: B017 - any refusal ends the attempt
                await asyncio.wait_for(
                    reconnect_desktop(b, a.identity.device_id, {"host": "127.0.0.1", "port": port}),
                    5,
                )
            await _settle(listener)
        failures = [r for r in caplog.records if "could not sign in" in r.getMessage()]
        assert len(failures) == 1, _messages(caplog)
        assert failures[0].levelno == logging.WARNING
        assert "over this computer while" in failures[0].getMessage()
    finally:
        await listener.close()
        a.close()
        b.close()


async def test_a_connection_that_never_identifies_itself_is_debug_only(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="gofer")
    registry = DeviceRegistry(tmp_path, TestSecrets(), initialize=True)
    listener = DeviceListener(registry, Invitations(registry))
    port = await listener.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((3073).to_bytes(4, "big"))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()
        await _settle(listener)
        [record] = [r for r in caplog.records if r.name == SERVICE]
        assert record.levelno == logging.DEBUG
        assert record.getMessage().startswith(
            "A connection over this computer closed before identifying itself"
        )
    finally:
        await listener.close()
        registry.close()


async def test_turned_away_connections_are_summarized(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="gofer")
    registry = DeviceRegistry(tmp_path, TestSecrets(), initialize=True)
    listener = DeviceListener(registry, Invitations(registry))
    port = await listener.start()
    listener.global_tokens = 0
    listener.global_updated = float("inf")  # No refill during the test.
    try:
        for _ in range(3):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            assert await asyncio.wait_for(reader.read(), 2) == b""
            writer.close()
            await writer.wait_closed()
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [
            "Turned away a connection over this computer from a this computer address: too "
            "many connection attempts or 0 connections already open"
        ]
    finally:
        await listener.close()
        registry.close()


def _fake_control(**values: object) -> SimpleNamespace:
    base = {"tailnet_logged": None, "tailnet_host": None}
    base.update(values)
    return SimpleNamespace(**base)


def test_tailscale_state_is_logged_only_when_it_changes(caplog):
    caplog.set_level(logging.DEBUG, logger="gofer")
    fake = _fake_control()
    note = DeviceControl._note_tailnet
    note(fake, Detection("stopped", source="cli"))
    note(fake, Detection("stopped", source="cli"))
    note(fake, Detection("running", "100.90.1.2", ("100.90.1.2",), source="cli"))
    note(fake, Detection("running", "100.90.1.2", ("100.90.1.2",), source="cli"))
    note(fake, None)
    assert _messages(caplog, "gofer.devices.control") == [
        "Phones cannot use Tailscale yet: Tailscale is installed but not connected "
        "(checked by cli)",
        "Tailscale is running on this computer with address 100.90.1.2 (found by cli)",
        "Tailscale for phones is off",
    ]


def test_a_changed_local_address_is_reported_once(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger="gofer")
    addresses = iter(["192.168.1.20", "10.0.0.7", "10.0.0.7", "192.168.1.20"])
    monkeypatch.setattr(control_module, "default_device_host", lambda _enabled: next(addresses))
    fake = SimpleNamespace(
        host_detected=True,
        experimental_network=True,
        listener=object(),
        host="192.168.1.20",
        host_checked=0.0,
        host_change_noted=None,
    )
    for _ in range(4):
        fake.host_checked = 0.0
        DeviceControl._note_host_change(fake)
    [line] = _messages(caplog, "gofer.devices.control")
    assert line.startswith(
        "This computer's local network address changed from 192.168.1.20 to 10.0.0.7"
    )
    assert fake.host_change_noted is None  # Back on the old address, so a new move reports.


def test_a_fixed_or_local_only_address_is_never_rechecked(monkeypatch):
    monkeypatch.setattr(
        control_module, "default_device_host", lambda _enabled: pytest.fail("rechecked")
    )
    for values in (
        {"host_detected": False, "experimental_network": True, "listener": object()},
        {"host_detected": True, "experimental_network": False, "listener": object()},
        {"host_detected": True, "experimental_network": True, "listener": None},
    ):
        DeviceControl._note_host_change(SimpleNamespace(host_checked=0.0, **values))
