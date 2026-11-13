"""Optional Tailscale reach. No real Tailscale, account, network change or phone."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from gofer.devices import control as control_module
from gofer.devices.client import pair_desktop
from gofer.devices.control import DeviceControl
from gofer.devices.framing import strict_json
from gofer.devices.pairing import Invitations
from gofer.devices.registry import DeviceRegistry, PairingError
from gofer.devices.service import DeviceListener
from gofer.devices.storage import StorageError
from gofer.devices.tailnet import Detection, detect, is_tailnet_address, tailnet_endpoint
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_pairing import TestSecrets

CONTRACT = Path(__file__).parents[2] / "src/gofer/devices/protocol/v2"


@pytest.mark.parametrize(
    "host,expected",
    [
        ("100.64.0.1", True),
        ("100.101.102.103", True),
        ("100.127.255.254", True),
        ("fd7a:115c:a1e0::1", True),
        ("100.128.0.1", False),
        ("100.63.255.255", False),
        ("192.168.1.20", False),
        ("8.8.8.8", False),
        ("fd00::1", False),
        ("desktop.tail1234.ts.net", False),
        ("", False),
    ],
)
def test_only_tailscale_node_addresses_qualify(host: str, expected: bool) -> None:
    assert is_tailnet_address(host) is expected
    if expected:
        assert tailnet_endpoint({"host": host, "port": 44233}) == {"host": host, "port": 44233}
    else:
        with pytest.raises(PairingError, match="invalid_tailnet_endpoint"):
            tailnet_endpoint({"host": host, "port": 44233})


@pytest.mark.parametrize(
    "value",
    [
        {"host": "100.101.102.103"},
        {"host": "100.101.102.103", "port": 0},
        {"host": "100.101.102.103", "port": "44233"},
        {"host": "100.101.102.103", "port": 44233, "extra": True},
    ],
)
def test_tailnet_endpoint_shape_is_closed(value: dict[str, Any]) -> None:
    with pytest.raises(PairingError, match="invalid_tailnet_endpoint"):
        tailnet_endpoint(value)


def status_json(state: str = "Running", ips: list[str] | None = None) -> str:
    return json.dumps(
        {
            "BackendState": state,
            "Self": {
                "TailscaleIPs": ips if ips is not None else ["fd7a:115c:a1e0::5", "100.101.1.2"],
                "DNSName": "desk.tail1234.ts.net.",
            },
            "CurrentTailnet": {"Name": "user@example.com"},
        }
    )


class Runner:
    def __init__(self, stdout: str = "", returncode: int = 0, error: Exception | None = None):
        self.stdout, self.returncode, self.error = stdout, returncode, error
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        assert options["timeout"] <= 5 and options["check"] is False
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def no_route(_: str) -> str | None:
    return None


def test_detection_reads_status_only_and_prefers_ipv4(monkeypatch) -> None:
    monkeypatch.delenv("RATICODE_TAILSCALE_HOST", raising=False)
    run = Runner(status_json())
    found = detect(cli=lambda: "/usr/bin/tailscale", run=run, route=no_route)
    # Raticode never signs in, brings Tailscale up or changes its settings.
    assert run.calls == [["/usr/bin/tailscale", "status", "--json", "--peers=false"]]
    assert found == Detection(
        "running",
        "100.101.1.2",
        ("fd7a:115c:a1e0::5", "100.101.1.2"),
        "desk.tail1234.ts.net",
        "user@example.com",
        "cli",
    )


@pytest.mark.parametrize(
    "state,expected",
    [
        ("NeedsLogin", "needs_login"),
        ("NeedsMachineAuth", "needs_approval"),
        ("Stopped", "stopped"),
        ("Starting", "starting"),
        ("Bogus", "unavailable"),
    ],
)
def test_detection_never_binds_unless_tailscale_is_running(monkeypatch, state, expected) -> None:
    monkeypatch.delenv("RATICODE_TAILSCALE_HOST", raising=False)
    found = detect(cli=lambda: "tailscale", run=Runner(status_json(state)), route=no_route)
    assert found.state == expected and found.host is None


def test_detection_ignores_addresses_outside_tailscale_ranges(monkeypatch) -> None:
    monkeypatch.delenv("RATICODE_TAILSCALE_HOST", raising=False)
    run = Runner(status_json(ips=["192.168.1.20", "8.8.8.8"]))
    found = detect(cli=lambda: "tailscale", run=run, route=no_route)
    assert found.state == "running" and found.host is None and found.addresses == ()
    garbage = detect(cli=lambda: "tailscale", run=Runner("not json"), route=no_route)
    assert garbage.state == "unavailable" and garbage.host is None


def test_detection_without_cli_trusts_only_tailscale_routes(monkeypatch) -> None:
    monkeypatch.delenv("RATICODE_TAILSCALE_HOST", raising=False)
    routes = {"192.0.2.1": "192.168.1.20", "100.100.100.100": "100.101.1.2"}
    found = detect(cli=lambda: None, route=routes.get)
    assert (found.state, found.host, found.source) == ("running", "100.101.1.2", "route")
    # A carrier-grade NAT address on the ordinary default route is not Tailscale.
    cgnat = {"192.0.2.1": "100.70.0.9", "100.100.100.100": "100.70.0.9"}
    assert detect(cli=lambda: None, route=cgnat.get) == Detection("not_installed", source="route")
    assert detect(cli=lambda: None, route=no_route).state == "not_installed"


def test_detection_handles_a_stopped_daemon_and_manual_address(monkeypatch) -> None:
    monkeypatch.delenv("RATICODE_TAILSCALE_HOST", raising=False)
    stopped = detect(cli=lambda: "tailscale", run=Runner("", returncode=1), route=no_route)
    assert stopped == Detection("stopped", source="cli")
    failed = detect(
        cli=lambda: "tailscale",
        run=Runner(error=subprocess.TimeoutExpired("tailscale", 5)),
        route=no_route,
    )
    assert failed.host is None
    monkeypatch.setenv("RATICODE_TAILSCALE_HOST", "100.101.1.9")
    manual = detect(cli=lambda: pytest.fail("manual address skips the CLI"))
    assert (manual.state, manual.host, manual.source) == ("running", "100.101.1.9", "manual")
    monkeypatch.setenv("RATICODE_TAILSCALE_HOST", "192.168.1.20")
    assert detect().state == "invalid_address"


@pytest.mark.asyncio
async def test_extra_address_shares_authentication_and_closes_its_sessions(tmp_path):
    a = DeviceRegistry(tmp_path / "a", TestSecrets(), initialize=True)
    b = DeviceRegistry(tmp_path / "b", TestSecrets(), initialize=True)
    invites = Invitations(a)
    listener = DeviceListener(a, invites)
    port = await listener.start("127.0.0.1")
    # 127.0.0.2 stands in for the Tailscale address; both are on this machine only.
    await listener.add_host("127.0.0.2", port)
    payload = invites.create("Desktop A", {"host": "127.0.0.2", "port": port}, role="desktop")
    task = asyncio.create_task(pair_desktop(b, payload["uri"]))
    try:
        async with asyncio.timeout(5):
            while not a.list():
                await asyncio.sleep(0.01)
            # The same pinned TLS and local confirmation apply on the extra address.
            assert a.list()[0]["state"] == "pending"
            a.confirm(b.identity.device_id, b.identity.pin.hex())
            assert await task == a.identity.device_id
        reader, writer = await asyncio.open_connection("127.0.0.2", port)
        await asyncio.sleep(0.05)
        await listener.remove_host("127.0.0.2")
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.2", port)
        # The ordinary listener is unaffected.
        _, still = await asyncio.open_connection("127.0.0.1", port)
        still.close()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await listener.close()
        a.close()
        b.close()
    assert listener.extra == {}


class FakeTailnet:
    """Stands in for ``detect`` and for binding a real tailnet address."""

    def __init__(self, monkeypatch, detection: Detection) -> None:
        self.detection = detection
        self.calls = 0
        self.bound: list[tuple[str, int]] = []
        self.released: list[str] = []
        monkeypatch.setattr(control_module, "detect", self)
        fake = self

        async def add_host(listener: DeviceListener, host: str, port: int) -> None:
            fake.bound.append((host, port))
            # Tailnet addresses cannot be bound in tests; hold a loopback server instead.
            listener.extra[host] = await asyncio.start_server(
                lambda reader, writer: writer.close(), "127.0.0.1", 0
            )

        real_remove = DeviceListener.remove_host

        async def remove_host(listener: DeviceListener, host: str) -> None:
            if host in listener.extra:
                fake.released.append(host)
            await real_remove(listener, host)

        monkeypatch.setattr(DeviceListener, "add_host", add_host)
        monkeypatch.setattr(DeviceListener, "remove_host", remove_host)

    def __call__(self) -> Detection:
        self.calls += 1
        return self.detection


RUNNING = Detection("running", "100.101.1.2", ("100.101.1.2",), "desk.ts.net", "me", "cli")


@pytest.fixture
def networked(monkeypatch):
    monkeypatch.setenv("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", "1")
    monkeypatch.setenv("RATICODE_DEVICE_HOST", "127.0.0.1")
    monkeypatch.delenv("RATICODE_TAILSCALE_HOST", raising=False)


def test_tailscale_is_off_until_the_user_opts_in(tmp_path, monkeypatch, networked):
    fake = FakeTailnet(monkeypatch, RUNNING)
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        with pytest.raises(StorageError):
            control.action({"action": "tailnet", "enabled": True})
        control.action({"action": "enable"})
        status = control.status()["tailnet"]
        assert status["enabled"] is False and status["state"] == "off" and status["host"] is None
        assert fake.calls == 0 and fake.bound == []
        assert control.application is not None and control.application.tailnet_endpoint is None
        with pytest.raises(PairingError, match="invalid_tailnet_choice"):
            control.action({"action": "tailnet", "enabled": "yes"})
    finally:
        control.close()


def test_tailscale_requires_the_network_preview(tmp_path, monkeypatch):
    monkeypatch.delenv("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", raising=False)
    monkeypatch.delenv("RATICODE_DEVICE_HOST", raising=False)
    fake = FakeTailnet(monkeypatch, RUNNING)
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        with pytest.raises(PairingError, match="experimental_network_opt_in_required"):
            control.action({"action": "tailnet", "enabled": True})
        assert control.status()["tailnet"]["state"] == "off" and fake.calls == 0
    finally:
        control.close()


def test_opt_in_listens_on_the_tailnet_address_and_survives_restart(
    tmp_path, monkeypatch, networked
):
    fake = FakeTailnet(monkeypatch, RUNNING)
    store = TestSecrets()
    control = DeviceControl(tmp_path, store=store)
    try:
        control.action({"action": "enable"})
        status = control.action({"action": "tailnet", "enabled": True})["tailnet"]
        assert status["state"] == "listening"
        assert (status["host"], status["port"]) == ("100.101.1.2", control.port)
        assert status["dns_name"] == "desk.ts.net" and status["source"] == "cli"
        # Same port as the LAN listener, so one access rule covers the phone.
        assert fake.bound == [("100.101.1.2", control.port)]
        assert control.application is not None
        assert control.application.tailnet_endpoint == {
            "host": "100.101.1.2",
            "port": control.port,
        }
        # A second check with the same address does not rebind.
        control.action({"action": "tailnet_check"})
        assert fake.bound == [("100.101.1.2", control.port)]
    finally:
        control.close()
    restarted = DeviceControl(tmp_path, store=store)
    try:
        assert restarted.tailnet_task is not None
        restarted.tailnet_task.result(timeout=5)
        assert restarted.status()["tailnet"]["state"] == "listening"
        off = restarted.action({"action": "tailnet", "enabled": False})["tailnet"]
        assert off == {
            "enabled": False,
            "state": "off",
            "host": None,
            "port": None,
            "addresses": [],
            "dns_name": None,
            "tailnet": None,
            "source": None,
            "checked_at": None,
            "error": None,
        }
        assert fake.released == ["100.101.1.2", "100.101.1.2"]
        assert restarted.application is not None
        assert restarted.application.tailnet_endpoint is None
    finally:
        restarted.close()


@pytest.mark.parametrize("state", ["not_installed", "needs_login", "stopped", "needs_approval"])
def test_missing_tailscale_reports_why_and_binds_nothing(tmp_path, monkeypatch, networked, state):
    fake = FakeTailnet(monkeypatch, Detection(state, source="cli"))
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        status = control.action({"action": "tailnet", "enabled": True})["tailnet"]
        assert status["enabled"] is True and status["state"] == state and status["host"] is None
        assert fake.bound == []
        assert control.application is not None and control.application.tailnet_endpoint is None
        # Not listening yet is not "off": phones keep the address they already have.
        assert control.application.tailnet_known is False
        # Signing in later is picked up by checking again.
        fake.detection = RUNNING
        assert control.action({"action": "tailnet_check"})["tailnet"]["state"] == "listening"
    finally:
        control.close()


def test_bind_failure_is_reported_without_touching_the_lan_listener(
    tmp_path, monkeypatch, networked
):
    FakeTailnet(monkeypatch, RUNNING)

    async def refuse(listener: DeviceListener, host: str, port: int) -> None:
        raise OSError("address not available")

    monkeypatch.setattr(DeviceListener, "add_host", refuse)
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        status = control.action({"action": "tailnet", "enabled": True})
        assert status["tailnet"]["state"] == "error"
        assert status["tailnet"]["error"] == "listen_failed"
        assert status["enabled"] and control.listener is not None
        assert control.listener.server is not None
    finally:
        control.close()


def test_status_polling_answers_while_tailscale_is_checked(tmp_path, monkeypatch, networked):
    import threading

    release = threading.Event()
    entered = threading.Event()

    def slow() -> Detection:
        entered.set()
        release.wait(5)
        return RUNNING

    FakeTailnet(monkeypatch, RUNNING)
    monkeypatch.setattr(control_module, "detect", slow)
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        worker = threading.Thread(
            target=control.action, args=({"action": "tailnet", "enabled": True},)
        )
        worker.start()
        assert entered.wait(5)
        # The Settings page keeps polling during the check; it must not wait for it.
        assert control.status()["tailnet"]["state"] == "checking"
        release.set()
        worker.join(5)
        assert control.status()["tailnet"]["state"] == "listening"
    finally:
        release.set()
        control.close()


def snapshot_connection(app, peer: str, sequence: int, **extra: bool) -> dict[str, Any]:
    payload = {"after_sequence": None, "limit": 100, "include_connection": True, **extra}
    record = app.handle(
        peer, event("sync.request", sequence=sequence, thread_id=None, payload=payload)
    )[-1]
    schema = json.loads((CONTRACT / "event.schema.json").read_text())
    Draft202012Validator(schema).validate(record)
    connection: dict[str, Any] = record["payload"]["connection"]
    return connection


def test_tailnet_address_reaches_only_phones_that_ask(application):
    app, peer = application
    app.lan_endpoint = {"host": "192.168.1.20", "port": 44233}
    # Older phones reject unknown fields, so they never receive the new one.
    assert "tailnet_endpoint" not in snapshot_connection(app, peer, 0)
    assert (
        snapshot_connection(app, peer, 1, include_tailnet_endpoint=True)["tailnet_endpoint"] is None
    )
    app.tailnet_endpoint = {"host": "100.101.1.2", "port": 44233}
    current = snapshot_connection(app, peer, 2, include_tailnet_endpoint=True)
    assert current["tailnet_endpoint"] == {"host": "100.101.1.2", "port": 44233}
    assert current["lan_endpoint"] == {"host": "192.168.1.20", "port": 44233}
    assert "tailnet_endpoint" not in snapshot_connection(app, peer, 3)


def test_tailnet_wire_fixtures_match_the_desktop_validators() -> None:
    request = strict_json((CONTRACT / "fixtures/sync.request.tailnet.json").read_bytes())
    snapshot = strict_json((CONTRACT / "fixtures/sync.snapshot.tailnet.json").read_bytes())
    assert request["payload"]["include_tailnet_endpoint"] is True
    endpoint = snapshot["payload"]["connection"]["tailnet_endpoint"]
    assert tailnet_endpoint(endpoint) == endpoint
    schema = json.loads((CONTRACT / "event.schema.json").read_text())
    bad = json.loads(json.dumps(snapshot))
    bad["payload"]["connection"]["tailnet_endpoint"]["scope"] = "global"
    assert not Draft202012Validator(schema).is_valid(bad)


@pytest.mark.parametrize(
    "blip",
    [
        Detection("starting", source="cli"),
        Detection("unavailable", source="cli"),
        Detection("stopped", source="cli"),
        Detection("not_detected", source="route"),
    ],
)
def test_a_tailscale_blip_keeps_the_listener_and_phone_sessions(
    tmp_path, monkeypatch, networked, blip
):
    """Waking from sleep or a slow `tailscale status` must not cut phones off."""
    fake = FakeTailnet(monkeypatch, RUNNING)
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        control.action({"action": "tailnet", "enabled": True})
        fake.detection = blip
        status = control.action({"action": "tailnet_check"})["tailnet"]
        # The guidance names what Tailscale reported, and the socket stays bound.
        assert status["state"] == blip.state
        assert status["host"] == "100.101.1.2"
        assert fake.released == []
        assert control.listener is not None and "100.101.1.2" in control.listener.extra
        assert control.application is not None
        assert control.application.tailnet_endpoint == {
            "host": "100.101.1.2",
            "port": control.port,
        }
        fake.detection = RUNNING
        assert control.action({"action": "tailnet_check"})["tailnet"]["state"] == "listening"
        assert fake.bound == [("100.101.1.2", control.port)]
    finally:
        control.close()


def test_a_new_tailscale_address_is_bound_before_the_old_one_closes(
    tmp_path, monkeypatch, networked
):
    fake = FakeTailnet(monkeypatch, RUNNING)
    order: list[str] = []
    bind, release = DeviceListener.add_host, DeviceListener.remove_host

    async def add_host(listener: DeviceListener, host: str, port: int) -> None:
        order.append("bind " + host)
        if host == "100.101.1.9":
            raise OSError("address not available yet")
        await bind(listener, host, port)

    async def remove_host(listener: DeviceListener, host: str) -> None:
        order.append("release " + host)
        await release(listener, host)

    monkeypatch.setattr(DeviceListener, "add_host", add_host)
    monkeypatch.setattr(DeviceListener, "remove_host", remove_host)
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        control.action({"action": "tailnet", "enabled": True})
        # A new address that cannot be bound yet leaves the working one in place.
        fake.detection = Detection("running", "100.101.1.9", ("100.101.1.9",), source="cli")
        failed = control.action({"action": "tailnet_check"})["tailnet"]
        assert failed["state"] == "error" and failed["host"] == "100.101.1.2"
        fake.detection = Detection("running", "100.101.1.3", ("100.101.1.3",), source="cli")
        moved = control.action({"action": "tailnet_check"})["tailnet"]
        assert (moved["state"], moved["host"]) == ("listening", "100.101.1.3")
        assert order == [
            "bind 100.101.1.2",
            "bind 100.101.1.9",
            "bind 100.101.1.3",
            "release 100.101.1.2",
        ]
        assert control.application is not None
        assert control.application.tailnet_endpoint == {"host": "100.101.1.3", "port": control.port}
    finally:
        control.close()


def test_phones_are_told_to_forget_the_address_only_when_the_option_is_off(application):
    app, peer = application
    app.lan_endpoint = {"host": "192.168.1.20", "port": 44233}
    # On but not listening yet, for example right after the desktop starts.
    app.tailnet_known = False
    assert "tailnet_endpoint" not in snapshot_connection(
        app, peer, 0, include_tailnet_endpoint=True
    )
    app.tailnet_endpoint = {"host": "100.101.1.2", "port": 44233}
    listening = snapshot_connection(app, peer, 1, include_tailnet_endpoint=True)
    assert listening["tailnet_endpoint"] == {"host": "100.101.1.2", "port": 44233}
    app.tailnet_endpoint, app.tailnet_known = None, True
    assert (
        snapshot_connection(app, peer, 2, include_tailnet_endpoint=True)["tailnet_endpoint"] is None
    )


def test_restart_leaves_the_phone_address_alone_until_the_first_check(
    tmp_path, monkeypatch, networked
):
    import threading

    store = TestSecrets()
    FakeTailnet(monkeypatch, RUNNING)
    first = DeviceControl(tmp_path, store=store)
    first.action({"action": "enable"})
    first.action({"action": "tailnet", "enabled": True})
    first.close()
    release = threading.Event()

    def slow() -> Detection:
        release.wait(5)
        return RUNNING

    monkeypatch.setattr(control_module, "detect", slow)
    restarted = DeviceControl(tmp_path, store=store)
    try:
        assert restarted.application is not None
        assert restarted.application.tailnet_known is False
        release.set()
        assert restarted.tailnet_task is not None
        restarted.tailnet_task.result(timeout=5)
        assert restarted.application.tailnet_known is True
        assert restarted.application.tailnet_endpoint is not None
    finally:
        release.set()
        restarted.close()


class _Session:
    def __init__(self, peer: str | None, role: str) -> None:
        self.peer, self.role = peer, role


@pytest.mark.asyncio
async def test_a_phone_that_signs_in_again_replaces_its_dead_sessions(registry_listener):
    listener = registry_listener
    phone, other_phone, desktop = "phone", "other-phone", "desktop"
    tasks: dict[str, asyncio.Task[None]] = {}
    writers: dict[str, object] = {}
    for name, peer, role in [
        ("old", phone, "controller"),
        ("older", phone, "controller"),
        ("other", other_phone, "controller"),
        ("desk1", desktop, "desktop"),
        ("desk2", desktop, "desktop"),
        ("new", phone, "controller"),
    ]:
        writers[name] = object()
        tasks[name] = asyncio.create_task(asyncio.sleep(30))
        listener.sessions[writers[name]] = _Session(peer, role)
        listener.connections[writers[name]] = tasks[name]
    listener._supersede(writers["new"], listener.sessions[writers["new"]])
    # A desktop's concurrent exchanges stay; a different desktop session cannot
    # end a phone's, because only the phone's own pinned identity matches.
    listener._supersede(writers["desk2"], listener.sessions[writers["desk2"]])
    await asyncio.sleep(0)
    assert tasks["old"].cancelled() and tasks["older"].cancelled()
    assert not any(tasks[name].done() for name in ("other", "desk1", "desk2", "new"))
    for task in tasks.values():
        task.cancel()
    await asyncio.gather(*tasks.values(), return_exceptions=True)
    listener.sessions.clear()
    listener.connections.clear()


@pytest.fixture
async def registry_listener(tmp_path):
    registry = DeviceRegistry(tmp_path, TestSecrets(), initialize=True)
    listener = DeviceListener(registry, Invitations(registry))
    yield listener
    registry.close()


@pytest.mark.asyncio
async def test_supersede_runs_only_after_the_phone_proves_its_identity(tmp_path, monkeypatch):
    """A connection that has not authenticated, or is still pairing, replaces nothing."""
    from gofer.devices import service

    seen: list[str] = []
    real = service.DeviceListener._supersede

    def spy(listener: DeviceListener, writer: Any, session: Any) -> None:
        seen.append(session.stage)
        real(listener, writer, session)

    monkeypatch.setattr(service.DeviceListener, "_supersede", spy)
    a = DeviceRegistry(tmp_path / "a", TestSecrets(), initialize=True)
    b = DeviceRegistry(tmp_path / "b", TestSecrets(), initialize=True)
    invites = Invitations(a)
    listener = DeviceListener(a, invites)
    port = await listener.start("127.0.0.1")
    payload = invites.create("Desktop A", {"host": "127.0.0.1", "port": port}, role="desktop")
    task = asyncio.create_task(pair_desktop(b, payload["uri"]))
    try:
        async with asyncio.timeout(5):
            while not a.list():
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.3)
            assert seen == []  # Waiting for confirmation is not proof of identity.
            a.confirm(b.identity.device_id, b.identity.pin.hex())
            await task
            while not seen:
                await asyncio.sleep(0.01)
        assert seen[0] in ("ack", "active")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await listener.close()
        a.close()
        b.close()
