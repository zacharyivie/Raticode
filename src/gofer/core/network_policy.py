from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import cast

METADATA_HOSTNAMES = {
    "metadata",
    "metadata.google.internal",
    "metadata.azure.com",
}

AddressResolver = Callable[[str, int | None], Iterable[str]]


@dataclass(frozen=True)
class NetworkPolicyViolation(ValueError):
    reason: str
    url: str

    def __str__(self) -> str:
        return f"HTTP request blocked by network policy: {self.reason} ({self.url})"


@dataclass(frozen=True)
class NetworkPolicyResult:
    url: str
    host: str
    port: int | None
    allowed_by: str | None = None


@dataclass(frozen=True)
class NetworkPolicyTarget(NetworkPolicyResult):
    connect_host: str = ""
    connect_port: int = 443


def validate_http_request_url(
    url: str,
    *,
    allowlist: Iterable[str] = (),
    resolver: AddressResolver | None = None,
) -> NetworkPolicyResult:
    parsed = parse_http_request_url(url)
    safe_url = _safe_url_for_error(parsed)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise NetworkPolicyViolation(
            f"unsupported URL scheme '{parsed.scheme or '<missing>'}'",
            safe_url,
        )
    host = parsed.hostname
    if not host:
        raise NetworkPolicyViolation("missing URL host", safe_url)

    normalized_host = _normalize_host(host)
    entries = tuple(_parse_allowlist(allowlist))
    host_allow = _host_allowlist_match(normalized_host, entries)
    if host_allow is not None:
        return NetworkPolicyResult(url=url, host=host, port=parsed.port, allowed_by=host_allow)

    if normalized_host in METADATA_HOSTNAMES:
        raise NetworkPolicyViolation(
            f"metadata host '{normalized_host}' is blocked",
            safe_url,
        )

    direct_address = _parse_ip(normalized_host)
    if direct_address is not None:
        return _validate_address(url, safe_url, host, parsed.port, direct_address, entries)

    resolved_addresses = tuple((resolver or resolve_host_addresses)(host, parsed.port))
    for address_text in resolved_addresses:
        address = _parse_ip(address_text)
        if address is None:
            continue
        result = _validate_address(url, safe_url, host, parsed.port, address, entries)
        if result.allowed_by is None and not _address_is_blocked(address):
            continue
    return NetworkPolicyResult(url=url, host=host, port=parsed.port)


def resolve_http_request_target(
    url: str,
    *,
    allowlist: Iterable[str] = (),
    resolver: AddressResolver | None = None,
) -> NetworkPolicyTarget:
    parsed = parse_http_request_url(url)
    result = validate_http_request_url(url, allowlist=allowlist, resolver=resolver)
    port = result.port or (80 if parsed.scheme.lower() == "http" else 443)
    normalized_host = _normalize_host(result.host)
    address = _parse_ip(normalized_host)
    if address is not None:
        return NetworkPolicyTarget(
            url=result.url,
            host=result.host,
            port=result.port,
            allowed_by=result.allowed_by,
            connect_host=str(address),
            connect_port=port,
        )

    resolved_addresses = tuple((resolver or resolve_host_addresses)(result.host, result.port))
    if result.allowed_by is not None:
        for address_text in resolved_addresses:
            address = _parse_ip(address_text)
            if address is None:
                continue
            return NetworkPolicyTarget(
                url=result.url,
                host=result.host,
                port=result.port,
                allowed_by=result.allowed_by,
                connect_host=str(address),
                connect_port=port,
            )

    entries = tuple(_parse_allowlist(allowlist))
    for address_text in resolved_addresses:
        address = _parse_ip(address_text)
        if address is None:
            continue
        address_result = _validate_address(
            url,
            _safe_url_for_error(parsed),
            result.host,
            result.port,
            address,
            entries,
        )
        return NetworkPolicyTarget(
            url=result.url,
            host=result.host,
            port=result.port,
            allowed_by=result.allowed_by or address_result.allowed_by,
            connect_host=str(address),
            connect_port=port,
        )

    raise NetworkPolicyViolation(
        f"host '{normalized_host}' did not resolve to a usable address",
        _safe_url_for_error(parsed),
    )


def resolve_host_addresses(host: str, port: int | None) -> Iterable[str]:
    try:
        records = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError:
        return ()
    return {record[4][0] for record in records if record[4] and isinstance(record[4][0], str)}


def network_policy_warnings(url: str, allowlist: Iterable[str] = ()) -> list[str]:
    if "{{" in url and "}}" in url:
        return []
    try:
        validate_http_request_url(url, allowlist=allowlist, resolver=lambda _host, _port: ())
    except NetworkPolicyViolation as exc:
        return [str(exc)]
    return []


def _validate_address(
    url: str,
    safe_url: str,
    host: str,
    port: int | None,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    entries: tuple[tuple[str, object], ...],
) -> NetworkPolicyResult:
    address_allow = _address_allowlist_match(address, entries)
    if address_allow is not None:
        return NetworkPolicyResult(url=url, host=host, port=port, allowed_by=address_allow)
    if _address_is_blocked(address):
        raise NetworkPolicyViolation(
            f"blocked private or local address '{address}'",
            safe_url,
        )
    return NetworkPolicyResult(url=url, host=host, port=port)


def _address_is_blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_loopback
        # Shared address space (100.64.0.0/10) is neither private nor global.
        # It can route to internal carrier/VPN services and needs explicit consent.
        or not address.is_global
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def parse_http_request_url(url: str) -> urllib.parse.SplitResult:
    """Parse HTTP URL components without leaking malformed authority text."""
    try:
        parsed = urllib.parse.urlsplit(url)
        # Authority validation is lazy in urllib. Force it before diagnostics or
        # DNS lookup, whose ValueErrors otherwise quote untrusted credential text.
        parsed.hostname
        parsed.port
    except ValueError:
        raise NetworkPolicyViolation("invalid URL authority", "<invalid-url>") from None
    return parsed


def _safe_url_for_error(parsed: urllib.parse.SplitResult) -> str:
    # A malformed authority may contain userinfo even without a hostname.
    # Never use the raw netloc in diagnostics, which may reach run logs.
    netloc = parsed.hostname or "<missing-host>"
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    # Webhooks and other APIs also put credentials in path segments. The origin
    # is sufficient to identify the blocked destination in diagnostics.
    return urllib.parse.urlunsplit((parsed.scheme, netloc, "/", "", ""))


def _normalize_host(host: str) -> str:
    return host.strip("[]").rstrip(".").lower()


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _parse_allowlist(entries: Iterable[str]) -> Iterable[tuple[str, object]]:
    for raw_entry in entries:
        entry = raw_entry.strip().lower()
        if not entry:
            continue
        try:
            yield ("network", ipaddress.ip_network(entry, strict=False))
            continue
        except ValueError:
            pass
        address = _parse_ip(entry)
        if address is not None:
            yield ("address", address)
        else:
            yield ("host", entry.rstrip("."))


def _host_allowlist_match(
    host: str,
    entries: tuple[tuple[str, object], ...],
) -> str | None:
    for entry_type, value in entries:
        if entry_type != "host":
            continue
        pattern = str(value)
        if pattern.startswith("*.") and host.endswith(pattern[1:]):
            return pattern
        if host == pattern:
            return pattern
    return None


def _address_allowlist_match(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    entries: tuple[tuple[str, object], ...],
) -> str | None:
    for entry_type, value in entries:
        if entry_type == "address" and address == value:
            return str(value)
        if entry_type == "network":
            network = cast(
                ipaddress.IPv4Network | ipaddress.IPv6Network,
                value,
            )
            if address in network:
                return str(network)
    return None
