"""Optional Tailscale reach for paired phones away from home.

Raticode never signs in to Tailscale, holds an auth key or calls its API. The user
installs Tailscale and signs in on each device with their own account; this module
only reads the desktop's own tailnet address so the device listener can also bind
it. Pinned, mutually authenticated TLS still runs inside the tunnel.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from gofer.devices.registry import PairingError

# Tailscale assigns node addresses only from these ranges.
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
# Tailscale's own resolver. Routing toward it picks the tailnet source address
# without sending a packet.
_QUAD100 = ("100.100.100.100", "fd7a:115c:a1e0::53")
_CLI_PATHS = {
    "darwin": ("/Applications/Tailscale.app/Contents/MacOS/Tailscale",),
    "win32": (r"C:\Program Files\Tailscale\tailscale.exe",),
}
_STATES = {
    "Running": "running",
    "NeedsLogin": "needs_login",
    "NeedsMachineAuth": "needs_approval",
    "Stopped": "stopped",
    "Starting": "starting",
}


def is_tailnet_address(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address in (TAILNET_V4 if address.version == 4 else TAILNET_V6)


def tailnet_endpoint(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """A phone may reach this desktop here. Only Tailscale node addresses qualify."""
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"host", "port"}
        or not isinstance(value["host"], str)
        or not is_tailnet_address(value["host"])
        or type(value["port"]) is not int
        or not 1 <= value["port"] <= 65535
    ):
        raise PairingError("invalid_tailnet_endpoint")
    return value


@dataclass(frozen=True)
class Detection:
    """What this desktop's Tailscale reports. ``host`` is set only when usable."""

    state: str
    host: str | None = None
    addresses: tuple[str, ...] = field(default_factory=tuple)
    dns_name: str | None = None
    tailnet: str | None = None
    source: str | None = None


def _cli() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return next((path for path in _CLI_PATHS.get(sys.platform, ()) if os.path.isfile(path)), None)


def _preferred(addresses: list[str]) -> str | None:
    # IPv4 first: it is the address people see in the Tailscale app and admin console.
    ordered = sorted(addresses, key=lambda host: ipaddress.ip_address(host).version)
    return ordered[0] if ordered else None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _from_status(raw: str) -> Detection:
    try:
        data = json.loads(raw)
    except ValueError:
        return Detection("unavailable", source="cli")
    if not isinstance(data, dict):
        return Detection("unavailable", source="cli")
    state = _STATES.get(str(data.get("BackendState")), "unavailable")
    me = _object(data.get("Self"))
    ips = me.get("TailscaleIPs")
    addresses = [ip for ip in (ips if isinstance(ips, list) else []) if isinstance(ip, str)]
    addresses = [ip for ip in addresses if is_tailnet_address(ip)]
    current = _object(data.get("CurrentTailnet"))
    dns = me.get("DNSName")
    name = current.get("Name")
    return Detection(
        state,
        _preferred(addresses) if state == "running" else None,
        tuple(addresses),
        str(dns).rstrip(".")[:253] if isinstance(dns, str) and dns else None,
        str(name)[:253] if isinstance(name, str) and name else None,
        "cli",
    )


def _route_source(target: str) -> str | None:
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as probe:
            probe.connect((target, 53))
            return str(probe.getsockname()[0])
    except OSError:
        return None


def _from_routes(route: Callable[[str], str | None]) -> Detection:
    # Without the CLI, trust a source address only when Tailscale's own route chose
    # it. A carrier-grade NAT address on the ordinary default route does not count.
    default = route("192.0.2.1")
    found = [
        host
        for target in _QUAD100
        if (host := route(target)) and is_tailnet_address(host) and host != default
    ]
    if not found:
        return Detection("not_detected", source="route")
    return Detection("running", _preferred(found), tuple(found), source="route")


def detect(
    *,
    cli: Callable[[], str | None] = _cli,
    run: Callable[..., Any] = subprocess.run,
    route: Callable[[str], str | None] = _route_source,
) -> Detection:
    """Read this desktop's tailnet address. Never signs in or changes Tailscale."""
    manual = os.environ.get("RATICODE_TAILSCALE_HOST")
    if manual:
        if not is_tailnet_address(manual):
            return Detection("invalid_address", source="manual")
        return Detection("running", manual, (manual,), source="manual")
    executable = cli()
    if executable is None:
        found = _from_routes(route)
        return found if found.host else Detection("not_installed", source="route")
    try:
        result = run(
            [executable, "status", "--json", "--peers=false"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return _from_routes(route)
    if result.returncode != 0 and not result.stdout.strip():
        # The daemon is not running, or this user may not read its status.
        found = _from_routes(route)
        return found if found.host else Detection("stopped", source="cli")
    return _from_status(result.stdout)
