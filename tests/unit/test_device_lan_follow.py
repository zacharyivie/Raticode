"""The phone listener follows this computer to a new local network address.

Loopback addresses 127.0.0.2-4 stand in for Wi-Fi addresses; they stay on this machine.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import socket
import time

import pytest

from gofer.devices import control as control_module
from gofer.devices.client import pair_desktop
from gofer.devices.control import DeviceControl
from gofer.devices.registry import DeviceRegistry
from tests.unit.test_device_pairing import TestSecrets


class Network:
    """What the operating system reports as this computer's outgoing address."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, host: str) -> None:
        self.host = host
        # Loopback stand-ins are always bindable; say which ones the computer still has.
        self.kept: set[str] = set()
        monkeypatch.setattr(control_module, "default_device_host", lambda _enabled: self.host)
        monkeypatch.setattr(control_module, "address_still_assigned", lambda h: h in self.kept)


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> Network:
    monkeypatch.setenv("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", "1")
    monkeypatch.delenv("RATICODE_DEVICE_HOST", raising=False)
    monkeypatch.delenv("RATICODE_DEVICE_PORT", raising=False)

    async def idle(_self: DeviceControl) -> None:
        # The tests drive each check themselves instead of the five-second loop.
        # A timer keeps the task referenced; an unreferenced Event would be collected.
        await asyncio.sleep(3600)

    monkeypatch.setattr(DeviceControl, "_remote_loop", idle)
    return Network(monkeypatch, "127.0.0.1")


def _check(control: DeviceControl) -> str | None:
    control.host_checked = 0.0
    host = control._host_to_follow()
    if host is not None:
        asyncio.run_coroutine_threadsafe(control._follow_host(host), control.loop).result(10)
    return host


def _answers(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def test_pairing_turned_on_after_boot_listens_on_the_address_found_then(tmp_path, network):
    """Raticode started before Wi-Fi was up; turning pairing on uses today's address."""
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        assert control.host == "127.0.0.1"
        network.host = "127.0.0.2"
        status = control.action({"action": "enable"})
        assert status["lan_host"] == "127.0.0.2"
        assert _answers("127.0.0.2", status["port"])
    finally:
        control.close()


def test_the_listener_moves_to_a_new_address_on_the_same_port(tmp_path, network, caplog):
    caplog.set_level(logging.INFO, logger="gofer.devices.control")
    network.host = "127.0.0.2"
    store = TestSecrets()
    control = DeviceControl(tmp_path, store=store)
    peer = DeviceRegistry(tmp_path / "peer", TestSecrets(), initialize=True)
    try:
        control.action({"action": "enable"})
        port = control.port
        assert port is not None and control.invitations is not None
        control.invitations.create("Desk", {"host": "127.0.0.2", "port": port}, role="desktop")
        held = socket.create_connection(("127.0.0.2", port), timeout=2)

        network.host = "127.0.0.3"
        assert _check(control) is None  # Seen once; a passing address is not followed.
        assert _answers("127.0.0.2", port) and not _answers("127.0.0.3", port)
        assert _check(control) == "127.0.0.3"

        assert _answers("127.0.0.3", port)
        assert not _answers("127.0.0.2", port)
        # A connection that arrived before the move is left to end on its own.
        held.settimeout(0.2)
        with pytest.raises(socket.timeout):
            held.recv(1)
        held.close()
        status = control.status()
        assert (status["lan_host"], status["port"]) == ("127.0.0.3", port)
        assert status["lan_moved"]["from"] == "127.0.0.2"
        assert status["lan_moved"]["to"] == "127.0.0.3"
        assert abs(status["lan_moved"]["at"] - time.time()) < 60
        # The QR code naming the old address is withdrawn, and paired desktops are told.
        assert status["invitation_available"] is False
        assert control.fleet_due is True
        assert control.application is not None
        assert control.application.listener_endpoint == {"host": "127.0.0.3", "port": port}
        assert any("changed from 127.0.0.2 to 127.0.0.3" in r.getMessage() for r in caplog.records)

        # Pairing and the pinned identity work the same on the new address.
        payload = control.invitations.create(
            "Desk", {"host": "127.0.0.3", "port": port}, role="desktop"
        )
        registry = control.registry
        assert registry is not None

        async def pair() -> str:
            task = asyncio.create_task(pair_desktop(peer, payload["uri"]))
            async with asyncio.timeout(10):
                while not registry.list():
                    await asyncio.sleep(0.01)
                registry.confirm(peer.identity.device_id, peer.identity.pin.hex())
                return await task

        assert asyncio.run(pair()) == registry.identity.device_id
    finally:
        control.close()
        peer.close()

    # A restart binds the address the computer has now and keeps the saved port.
    network.host = "127.0.0.3"
    restarted = DeviceControl(tmp_path, store=store)
    try:
        assert (restarted.host, restarted.port) == ("127.0.0.3", port)
        assert restarted.status()["lan_moved"] is None
    finally:
        restarted.close()


def test_a_failed_move_keeps_the_old_address_and_is_logged_once(tmp_path, network, caplog):
    caplog.set_level(logging.INFO, logger="gofer.devices.control")
    network.host = "127.0.0.2"
    control = DeviceControl(tmp_path, store=TestSecrets())
    blocker = socket.socket()
    try:
        control.action({"action": "enable"})
        port = control.port
        assert port is not None
        blocker.bind(("127.0.0.4", port))
        blocker.listen()
        network.host = "127.0.0.4"
        _check(control)
        for _ in range(3):
            assert _check(control) == "127.0.0.4"
        assert _answers("127.0.0.2", port)
        assert control.status()["lan_host"] == "127.0.0.2"
        assert control.status()["lan_moved"] is None
        failures = [
            r
            for r in caplog.records
            if r.name == "gofer.devices.control" and r.levelno == logging.ERROR
        ]
        assert len(failures) == 1
        assert "could not listen for phones there" in failures[0].getMessage()
        assert errno.errorcode[errno.EADDRINUSE] in failures[0].getMessage()

        # Once the address frees up, the next check moves the listener.
        blocker.close()
        assert _check(control) == "127.0.0.4"
        assert control.status()["lan_host"] == "127.0.0.4"
    finally:
        blocker.close()
        control.close()


def test_losing_the_network_keeps_the_listener(tmp_path, network):
    network.host = "127.0.0.2"
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        port = control.port
        assert port is not None
        network.host = "127.0.0.1"
        for _ in range(3):
            assert _check(control) is None
        assert control.host == "127.0.0.2" and _answers("127.0.0.2", port)
    finally:
        control.close()


def test_a_fixed_address_never_moves(tmp_path, network, monkeypatch):
    monkeypatch.setenv("RATICODE_DEVICE_HOST", "127.0.0.2")
    network.host = "127.0.0.3"
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        assert control.host == "127.0.0.2"
        for _ in range(3):
            assert _check(control) is None
        assert control.status()["lan_host"] == "127.0.0.2"
    finally:
        control.close()


def test_the_listener_stays_on_an_address_the_computer_still_has(tmp_path, network):
    """Docking or a full-tunnel VPN moves the default route but keeps the Wi-Fi address."""
    network.host = "127.0.0.2"
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        port = control.port
        assert port is not None and control.host == "127.0.0.2"
        network.kept.add("127.0.0.2")
        network.host = "127.0.0.3"
        assert _check(control) is None
        assert _check(control) is None
        assert control.host == "127.0.0.2" and _answers("127.0.0.2", port)
        # Once the old address is really gone, the listener follows the computer.
        network.kept.clear()
        assert _check(control) is None  # the first check after a change waits for a second
        assert _check(control) == "127.0.0.3"
        assert _answers("127.0.0.3", port)
    finally:
        control.close()


def test_a_real_bind_tells_an_address_that_is_gone_from_one_that_is_kept() -> None:
    assert control_module.address_still_assigned("127.0.0.1")
    # TEST-NET-3 is never assigned to a machine running the tests.
    assert not control_module.address_still_assigned("203.0.113.77")
