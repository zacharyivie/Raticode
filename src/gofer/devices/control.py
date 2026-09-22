"""Local UI control of the backend-owned pairing service.

The runtime lives in the backend, never in the renderer. Real LAN networking
requires an explicit experimental opt-in. Independent review remains required
for production release. Pairing never sends secrets to a public relay.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ipaddress
import os
import socket
import sqlite3
import threading
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.devices.application import DeviceApplication
from gofer.devices.client import exchange, pair_desktop, reconnect_desktop
from gofer.devices.pairing import Invitations, parse_invitation
from gofer.devices.registry import DeviceRegistry, PairingError
from gofer.devices.service import DeviceListener
from gofer.devices.storage import OSSecretStore, SecretStore, StorageError


def default_device_host(network_enabled: bool) -> str:
    """Ask the OS for its outgoing private address, without sending a packet."""
    if network_enabled:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))
                host = str(probe.getsockname()[0])
            address = ipaddress.ip_address(host)
            if address.is_private and not (
                address.is_loopback
                or address.is_unspecified
                or address.is_link_local
                or address.is_multicast
            ):
                return host
        except OSError:
            pass
    return "127.0.0.1"


class DeviceControl:
    def __init__(self, directory: Path, *, store: SecretStore | None = None) -> None:
        self.directory = directory / "devices"
        self.store = store
        self.experimental_network = os.environ.get("RATICODE_DEVICE_EXPERIMENTAL_NETWORK") == "1"
        self.host = os.environ.get("RATICODE_DEVICE_HOST") or default_device_host(
            self.experimental_network
        )
        self.registry: DeviceRegistry | None = None
        self.invitations: Invitations | None = None
        self.listener: DeviceListener | None = None
        self.application: DeviceApplication | None = None
        self.chat_config: tuple[Any, ...] | None = None
        self.chat_source: Any = None
        self.bridge: Any = None
        self.remote_tasks: dict[tuple[str, str], concurrent.futures.Future[str]] = {}
        self.relay_service: Any = None
        self.port: int | None = None
        self.error: str | None = None
        self.last_fleet_refresh = 0.0
        self.lock = threading.RLock()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever, daemon=True, name="device-service"
        )
        self.thread.start()
        self.outbound: concurrent.futures.Future[str] | None = None
        self.configuration_error: str | None = None
        try:
            address = ipaddress.ip_address(self.host)
            if (
                address.is_unspecified
                or address.is_multicast
                or not (address.is_private or address.is_loopback)
            ):
                raise PairingError("device_host_must_be_local_address")
            if not address.is_loopback and not self.experimental_network:
                raise PairingError("experimental_network_opt_in_required")
        except ValueError:
            self.configuration_error = (
                "Device listener disabled. Configure a local IP address and explicitly opt "
                "in to experimental networking for LAN access, then restart the desktop."
            )
            self.error = self.configuration_error
        if self.configuration_error is None and (self.directory / "devices.sqlite3").exists():
            try:
                self.start(initialize=False)
            except (ValueError, OSError, sqlite3.Error):
                self.error = (
                    "Protected device storage could not be opened. Unlock the OS credential "
                    "store; if keys were lost, revoke this desktop on its peers "
                    "and pair a new identity."
                )

    def start(self, *, initialize: bool) -> None:
        with self.lock:
            if self.configuration_error is not None:
                raise PairingError("device_configuration_invalid")
            if self.registry is not None:
                return
            registry = DeviceRegistry(
                self.directory, self.store or OSSecretStore(), initialize=initialize
            )
            invitations = Invitations(registry)
            application = DeviceApplication(registry)
            listener = DeviceListener(registry, invitations, application)
            try:
                requested_port = int(os.environ.get("RATICODE_DEVICE_PORT", "0"))
            except ValueError:
                registry.close()
                raise PairingError("invalid_listener_port") from None
            if not 0 <= requested_port <= 65535:
                registry.close()
                raise PairingError("invalid_listener_port")
            requested_port = requested_port or registry.listener_port()
            future = asyncio.run_coroutine_threadsafe(
                listener.start(self.host, requested_port), self.loop
            )
            try:
                port = future.result(timeout=10)
                registry.save_listener_port(port)
            except BaseException:
                future.cancel()
                asyncio.run_coroutine_threadsafe(listener.close(), self.loop).result(timeout=10)
                registry.close()
                raise
            self.registry, self.invitations, self.listener = registry, invitations, listener
            self.application = application
            self.port = port
            application.lan_endpoint = (
                None
                if ipaddress.ip_address(self.host).is_loopback
                else {"host": self.host, "port": port}
            )
            self._start_chat()
            asyncio.run_coroutine_threadsafe(self._remote_loop(), self.loop)
            if self.experimental_network:
                from gofer.devices.relay_service import RelayService

                self.relay_service = RelayService(registry, invitations, application)
                asyncio.run_coroutine_threadsafe(self.relay_service.run(), self.loop)
            self.error = None

    def status(self) -> dict[str, Any]:
        with self.lock:
            outbound = "idle"
            if self.outbound is not None:
                if not self.outbound.done():
                    outbound = "awaiting_peer_confirmation"
                elif self.outbound.cancelled() or self.outbound.exception() is not None:
                    outbound = "failed"
                else:
                    outbound = "trust_saved"
            return {
                "enabled": self.registry is not None,
                "network_release": False,
                "experimental_network": self.experimental_network,
                "lan_available": bool(
                    self.listener and not ipaddress.ip_address(self.host).is_loopback
                ),
                "relay_enabled": self.relay_service is not None,
                "notice": (
                    "Independent security review is pending. Experimental encrypted device "
                    "networking is enabled. Remote Rem requires a local thread grant."
                    if self.experimental_network
                    else "Independent security review is pending. Pairing is limited to local "
                    "test peers until experimental networking is opted in."
                ),
                "error": self.error,
                "port": self.port,
                "device_id": self.registry.identity.device_id if self.registry else None,
                "fingerprint": self.registry.identity.pin.hex() if self.registry else None,
                "peers": self.registry.list() if self.registry else [],
                "dispatch_error": getattr(self.bridge, "error", None),
                "rem_ready": self.bridge is not None and not getattr(self.bridge, "error", None),
                "lan_host": self.host if self.listener else None,
                "relay_error": getattr(self.relay_service, "error", None),
                "grants": self.application.grants() if self.application else [],
                "workspace_peers": self.application.workspace.enabled() if self.application else [],
                "outbound": outbound,
                "invitation_available": bool(self.invitations and self.invitations.current),
            }

    def action(self, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            action = body.get("action")
            if action == "enable":
                self.start(initialize=True)
                return self.status()
            if self.registry is None or self.invitations is None:
                raise StorageError("protected_device_service_unavailable")
            if action == "fleet_status":
                assert self.application is not None
                now = self.registry.clock()
                if now - self.last_fleet_refresh >= 30:
                    self.last_fleet_refresh = now
                    for fleet_peer in self.registry.list():
                        if fleet_peer["state"] == "active" and fleet_peer["role"] == "desktop":
                            try:
                                self.registry.endpoint(fleet_peer["device_id"])
                                self.action(
                                    {
                                        "action": "send",
                                        "device_id": fleet_peer["device_id"],
                                        "thread_id": None,
                                        "request_id": str(uuid4()),
                                        "kind": "fleet.request",
                                    }
                                )
                            except ValueError:
                                continue
                return {"devices": self.application.fleet(), "observed_at": now}
            if action == "set_endpoint":
                from gofer.devices.pairing import lan_endpoint

                checked_endpoint = lan_endpoint(body.get("endpoint", {}))
                if checked_endpoint is None:
                    raise PairingError("peer_endpoint_unknown")
                if (
                    not self.experimental_network
                    and not ipaddress.ip_address(checked_endpoint["host"]).is_loopback
                ):
                    raise PairingError("experimental_network_opt_in_required")
                self.registry.save_endpoint(str(body.get("device_id", "")), checked_endpoint)
                return self.status()
            if action == "work_status":
                assert self.application is not None
                return self.application.remote_status(
                    str(body.get("device_id", "")), str(body.get("request_id", ""))
                )
            if action == "send":
                assert self.application is not None
                peer = str(body.get("device_id", ""))
                endpoint = self.registry.endpoint(peer)
                if (
                    not self.experimental_network
                    and not ipaddress.ip_address(endpoint["host"]).is_loopback
                ):
                    raise PairingError("experimental_network_opt_in_required")
                event = self.application.queue_remote(peer, body)
                key = (peer, event["request_id"])
                if key not in self.remote_tasks or self.remote_tasks[key].done():
                    app = self.application
                    self.remote_tasks[key] = asyncio.run_coroutine_threadsafe(
                        exchange(
                            self.registry,
                            peer,
                            event,
                            partial(app.remote_result, peer),
                        ),
                        self.loop,
                    )
                return {"device_id": peer, "request_id": event["request_id"], "state": "queued"}
            if action == "offer_file":
                assert self.application is not None
                return self.application.offer_file(
                    str(body.get("device_id", "")),
                    str(body.get("thread_id", "")),
                    str(body.get("path", "")),
                    origin_request=body.get("origin_request"),
                )
            if action == "thread_history":
                assert self.application is not None
                peer, thread = str(body.get("device_id", "")), str(body.get("thread_id", ""))
                if not self.application.authorized(peer, thread):
                    raise PairingError("thread_grant_required")
                return {"messages": self.application.history(peer, thread)}
            if action == "share_workspace":
                assert self.application is not None
                if not isinstance(body.get("enabled"), bool):
                    raise PairingError("invalid_sharing_choice")
                self.application.workspace.enable(str(body.get("device_id", "")), body["enabled"])
                return self.status()
            if action == "workspace_remove":
                assert self.application is not None
                self.application.workspace.remove(
                    str(body.get("device_id", "")), str(body.get("thread_id", ""))
                )
                return {"removed": True}
            if action == "workspace_poll":
                assert self.application is not None
                peer = str(body.get("device_id", ""))
                if peer not in self.application.workspace.enabled():
                    raise PairingError("workspace_sharing_disabled")
                with self.registry.lock:
                    return {
                        "threads": [
                            self.application.workspace.export(peer, row[0])
                            for row in self.registry.db.execute(
                                "SELECT thread FROM device_workspace_threads WHERE peer=?", (peer,)
                            )
                            if (body.get("known") or {}).get(row[0])
                            != self.application.workspace.sync_token(peer, row[0])
                        ]
                    }
            if action == "workspace_exchange":
                assert self.application is not None
                return self.application.workspace.exchange(
                    str(body.get("device_id", "")),
                    body["metadata"],
                    body["messages"],
                    body["context"],
                    body.get("revision"),
                )
            if action == "authorize_thread":
                if self.application is None or not isinstance(body.get("context"), dict):
                    raise PairingError("invalid_thread_context")
                context = body["context"]
                # Paths and permission modes originate only at this authenticated local API.
                from gofer.ui.device_chat import validate_context

                context = validate_context(context)
                self.application.authorize(
                    str(body.get("device_id", "")), str(body.get("thread_id", "")), context
                )
                return self.status()
            if action == "revoke_thread":
                assert self.application is not None
                self.application.revoke_grant(
                    str(body.get("device_id", "")), str(body.get("thread_id", ""))
                )
                return self.status()
            if action == "invite":
                if not self.experimental_network:
                    raise PairingError("experimental_network_opt_in_required")
                result = self.invitations.create(
                    str(body.get("name", "Raticode Desktop")),
                    str(body.get("relay", "https://ntfy.sh")),
                    (
                        None
                        if ipaddress.ip_address(self.host).is_loopback
                        else {"host": self.host, "port": self.port}
                    ),
                )
                import segno

                result["qr"] = segno.make(result["uri"], micro=False).svg_data_uri(scale=4)
                return result
            if action == "cancel_invitation":
                self.invitations.cancel()
            elif action == "confirm":
                self.registry.confirm(
                    str(body.get("device_id", "")), str(body.get("fingerprint", ""))
                )
            elif action in ("unpair", "remove_revoked"):
                assert self.application is not None
                self.application.remove_peer(
                    str(body.get("device_id", "")), revoked_only=action == "remove_revoked"
                )
            elif action == "revoke":
                self.registry.revoke(str(body.get("device_id", "")))
            elif action == "preview":
                _, invite = parse_invitation(str(body.get("uri", "")), self.registry.clock())
                endpoint = invite["lan_endpoint"]
                if endpoint is None or (
                    not self.experimental_network
                    and not ipaddress.ip_address(endpoint["host"]).is_loopback
                ):
                    raise PairingError("experimental_network_opt_in_required")
                return {
                    "name": invite["responder_name"],
                    "fingerprint": invite["responder_spki_sha256"],
                    "expires_at": invite["expires_at"],
                }
            elif action == "pair":
                uri = str(body.get("uri", ""))
                preview = self.action({"action": "preview", "uri": uri})
                if body.get("fingerprint") != preview["fingerprint"]:
                    raise PairingError("local_identity_confirmation_required")
                if self.outbound is not None and not self.outbound.done():
                    raise PairingError("pairing_already_running")
                self.outbound = asyncio.run_coroutine_threadsafe(
                    pair_desktop(self.registry, uri), self.loop
                )
            elif action == "reconnect":
                peer = str(body.get("device_id", ""))
                endpoint = self.registry.endpoint(peer)
                if (
                    not self.experimental_network
                    and not ipaddress.ip_address(endpoint["host"]).is_loopback
                ):
                    raise PairingError("experimental_network_opt_in_required")
                if self.outbound is not None and not self.outbound.done():
                    raise PairingError("pairing_already_running")
                self.outbound = asyncio.run_coroutine_threadsafe(
                    reconnect_desktop(self.registry, peer, endpoint), self.loop
                )
            elif action == "cancel_pairing":
                if self.outbound is not None:
                    self.outbound.cancel()
            else:
                raise PairingError("unknown_device_action")
            return self.status()

    async def _remote_loop(self) -> None:
        while True:
            if self.application is not None and self.registry is not None:
                for peer, event in self.application.queued_remote():
                    try:
                        endpoint = self.registry.endpoint(peer)
                        if (
                            not self.experimental_network
                            and not ipaddress.ip_address(endpoint["host"]).is_loopback
                        ):
                            continue
                    except ValueError:
                        continue
                    key = (peer, event["request_id"])
                    if key not in self.remote_tasks or self.remote_tasks[key].done():
                        # The exact durable request is retried on a fresh authenticated session.
                        app = self.application
                        self.remote_tasks[key] = asyncio.run_coroutine_threadsafe(
                            exchange(
                                self.registry,
                                peer,
                                event,
                                partial(app.remote_result, peer),
                            ),
                            self.loop,
                        )
            await asyncio.sleep(30)

    def attach_chat(
        self,
        jobs: Any,
        steering: Any,
        data_dir: Path,
        resource_limits: Any = None,
        *,
        source: Any = None,
    ) -> None:
        with self.lock:
            self.chat_config = (jobs, steering, data_dir, resource_limits)
            self.chat_source = source
            self._start_chat()

    def _start_chat(self) -> None:
        if self.application is not None and self.chat_config is not None and self.bridge is None:
            from gofer.ui.device_chat import DeviceChatBridge

            self.application.local_jobs = self.chat_config[0].active_snapshot
            self.application.local_chat_jobs = self.chat_config[0]
            self.bridge = DeviceChatBridge(
                self.application,
                *self.chat_config,
                fleet_control=self,
                **({"source": self.chat_source} if self.chat_source else {}),
            )
            self.application.rem_ready = lambda: (
                self.bridge is not None and self.bridge.error is None
            )
            asyncio.run_coroutine_threadsafe(self.bridge.run(), self.loop)

    def close(self) -> None:
        with self.lock:
            if self.bridge is not None:
                self.bridge.close()
            if self.relay_service is not None:
                self.relay_service.closed = True
            if self.outbound is not None:
                self.outbound.cancel()
            if self.listener is not None:
                asyncio.run_coroutine_threadsafe(self.listener.close(), self.loop).result(
                    timeout=10
                )

            async def drain_tasks() -> None:
                tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            asyncio.run_coroutine_threadsafe(drain_tasks(), self.loop).result(timeout=10)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)
            self.loop.close()
            if self.registry is not None:
                self.registry.close()
