"""Private IPv4 addresses written in IPv6 forms are private everywhere Raticode checks.

Python's ``is_global`` treats forms such as ``::127.0.0.1`` and ``::ffff:0:10.0.0.1`` as
global, so every place that keeps Raticode's own requests off the local network uses
``network_policy.is_global_address`` instead.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from typing import Any

import pytest

from gofer.core.network_policy import is_global_address
from gofer.ui import brain_research
from gofer.ui.rem_home_results import public_url

PRIVATE_IN_IPV6 = [
    "::127.0.0.1",
    "::ffff:127.0.0.1",
    "::ffff:0:127.0.0.1",
    "::ffff:0:10.0.0.1",
    "64:ff9b::7f00:1",
    "64:ff9b::c0a8:101",
    # Deprecated site-local, which Python counts as global.
    "fec0::1",
    "feff:ffff::1",
]


@pytest.mark.parametrize("address", PRIVATE_IN_IPV6)
def test_ipv6_forms_of_a_private_ipv4_are_not_global(address: str) -> None:
    assert not is_global_address(ipaddress.ip_address(address))


def test_public_addresses_stay_global() -> None:
    assert is_global_address(ipaddress.ip_address("93.184.215.14"))
    assert is_global_address(ipaddress.ip_address("64:ff9b::5db8:d70e"))
    assert is_global_address(ipaddress.ip_address("2606:4700:4700::1111"))


@pytest.mark.parametrize("address", PRIVATE_IN_IPV6)
def test_chat_source_links_drop_private_ipv6_forms(address: str) -> None:
    assert public_url(f"http://[{address}]/admin") == ""
    assert public_url("https://example.com/page") == "https://example.com/page"


@pytest.mark.parametrize("address", PRIVATE_IN_IPV6)
def test_second_brain_web_capture_refuses_hosts_that_resolve_to_them(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def resolve(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", (address, port, 0, 0))]

    monkeypatch.setattr(brain_research.socket, "getaddrinfo", resolve)
    with pytest.raises(ValueError, match="private, loopback or local network"):
        brain_research.fetch_public_page("https://sneaky.example/feed", threading.Event())
