"""Finding the hardware address a paired phone needs to wake this computer.

This module only reads. The phone sends the Wake-on-LAN packet; the desktop never does.
The address is never logged and never appears in an exception this module raises.

The card that matters is the one holding the address phones connect to, not the default
route or the first card found. Only a physical card qualifies: bridges, bonds, ``veth``,
``tun``, WireGuard and ``tailscale0`` have no ``device`` link in sysfs, and firmware
doesn't listen for their addresses.
"""

from __future__ import annotations

import errno
import ipaddress
import re
import socket
import struct
import sys
from pathlib import Path

_FORMAT = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
SIOCGIFADDR = 0x8915
SYS_NET = Path("/sys/class/net")
# An interface with no IPv4 address, or one that vanished during the scan, holds nothing.
_HOLDS_NOTHING = (errno.EADDRNOTAVAIL, errno.ENODEV)


def supported() -> bool:
    """Other systems need their own reviewed way to read the address."""
    return sys.platform == "linux"


def private_ipv4(host: str) -> bool:
    """Whether ``host`` is in the private IPv4 ranges a phone accepts for Wi-Fi."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and any(address in network for network in _PRIVATE_NETWORKS)


def clean_address(value: object) -> str | None:
    """The address in wire form, or None if it isn't one a card can be woken by."""
    if not isinstance(value, str):
        return None
    address = value.strip().lower()
    if not _FORMAT.fullmatch(address):
        return None
    if address == "00:00:00:00:00:00" or int(address[:2], 16) & 1:
        # The multicast bit also covers the all-ff broadcast address.
        return None
    return address


def _interface_address(name: str) -> str | None:
    """The interface's IPv4 address, or None when it holds none."""
    import fcntl

    request = struct.pack("256s", name.encode()[:15])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            answer = fcntl.ioctl(probe.fileno(), SIOCGIFADDR, request)
        except OSError as exc:
            if exc.errno in _HOLDS_NOTHING:
                return None
            raise
    return socket.inet_ntoa(answer[20:24])


def hardware_address(host: str) -> str | None:
    """The hardware address of the one physical card holding ``host``, or None."""
    if not supported():
        return None
    try:
        cards = [
            name
            for _, name in socket.if_nameindex()
            if _interface_address(name) == host and (SYS_NET / name / "device").exists()
        ]
        return (
            clean_address((SYS_NET / cards[0] / "address").read_text()) if len(cards) == 1 else None
        )
    except (OSError, ValueError):
        return None
