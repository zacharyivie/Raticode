"""Local UI control of the backend-owned pairing service.

The runtime lives in the backend, never in the renderer. Real LAN networking
requires an explicit experimental opt-in. Independent review remains required
for production release. Devices connect over the local network, or over the
user's own Tailscale network when they opt in; there is no internet relay.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ipaddress
import logging
import os
import socket
import sqlite3
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any

from gofer.devices.application import DeviceApplication
from gofer.devices.client import exchange, pair_desktop, reconnect_desktop
from gofer.devices.pairing import Invitations, parse_invitation
from gofer.devices.registry import DeviceRegistry, PairingError
from gofer.devices.service import DeviceListener
from gofer.devices.storage import OSSecretStore, SecretStore, StorageError
from gofer.devices.tailnet import Detection, detect

# Seconds between Tailscale checks while the opt-in is on. A desktop that is not
# listening yet checks sooner, so starting Tailscale after Raticode still works.
TAILNET_RECHECK = 120
TAILNET_RETRY = 20

# Seconds between automatic fleet refreshes. Snapshots stay fresh for sixty.
FLEET_REFRESH = 30
# How long a status request waits for paired desktops to answer.
FLEET_WAIT = 5

logger = logging.getLogger(__name__)


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
        self.services: dict[str, Any] = {}
        self.chat_source: Any = None
        self.bridge: Any = None
        self.remote_tasks: dict[tuple[str, str], concurrent.futures.Future[str]] = {}
        # The request handler and the background loop both start exchanges.
        self.tasks_lock = threading.Lock()
        self.port: int | None = None
        self.error: str | None = None
        self.fleet_probed: dict[str, float] = {}
        self.fleet_due = False
        self.lock = threading.RLock()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever, daemon=True, name="device-service"
        )
        self.thread.start()
        self.outbound: concurrent.futures.Future[str] | None = None
        # Tailscale state. Guarded by its own lock, which is never held while
        # waiting on the device loop, so the loop may take it.
        self.tailnet_lock = threading.Lock()
        self.tailnet_task: concurrent.futures.Future[None] | None = None
        self.tailnet_host: str | None = None
        self.tailnet_detection: Detection | None = None
        self.tailnet_error: str | None = None
        self.tailnet_checked: float | None = None
        self.tailnet_due = 0.0
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
                "Device listener disabled. Configure a local IP address and turn on Phone "
                "and desktop pairing in Settings > Developer > Feature Preview."
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
            application.listener_endpoint = {"host": self.host, "port": port}
            self._apply_services()
            self._start_chat()
            asyncio.run_coroutine_threadsafe(self._remote_loop(), self.loop)
            if registry.tailnet_enabled():
                # Detection runs a short subprocess; never make startup wait for it.
                self._tailnet_start()
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
                # Cached evidence only; status polling never opens a connection.
                "fleet": self.application.fleet() if self.application else [],
                "dispatch_error": getattr(self.bridge, "error", None),
                "rem_ready": self.bridge is not None and not getattr(self.bridge, "error", None),
                "lan_host": self.host if self.listener else None,
                "grants": self.application.grants() if self.application else [],
                "workspace_peers": self.application.workspace.enabled() if self.application else [],
                "remote_control_peers": self.application.remote.enabled()
                if self.application
                else [],
                "desktop_control_peers": self.application.desktop.enabled()
                if self.application
                else [],
                "thread_host_peers": self.application.hosting() if self.application else [],
                "outbound": outbound,
                "invitation_available": bool(self.invitations and self.invitations.current),
                "tailnet": self._tailnet_status(),
            }

    def _tailnet_status(self) -> dict[str, Any]:
        enabled = bool(
            self.experimental_network and self.registry and self.registry.tailnet_enabled()
        )
        with self.tailnet_lock:
            detection, error, checked = (
                self.tailnet_detection,
                self.tailnet_error,
                self.tailnet_checked,
            )
            host, running = (
                self.tailnet_host,
                bool(self.tailnet_task and not self.tailnet_task.done()),
            )
        if not enabled:
            state = "off"
        elif host:
            state = "listening"
        elif error:
            state = "error"
        elif detection is None or running:
            state = "checking"
        else:
            state = "not_detected" if detection.state == "running" else detection.state
        return {
            "enabled": enabled,
            "state": state,
            "host": host,
            "port": self.port if host else None,
            "addresses": list(detection.addresses) if detection and enabled else [],
            "dns_name": detection.dns_name if detection and enabled else None,
            "tailnet": detection.tailnet if detection and enabled else None,
            "source": detection.source if detection and enabled else None,
            "checked_at": checked if enabled else None,
            "error": error if enabled else None,
        }

    def _tailnet_start(self) -> concurrent.futures.Future[None]:
        """Start one Tailscale check, or join the one already running. Any thread."""
        with self.tailnet_lock:
            if self.tailnet_task is None or self.tailnet_task.done():
                self.tailnet_task = asyncio.run_coroutine_threadsafe(
                    self._tailnet_refresh(), self.loop
                )
            return self.tailnet_task

    async def _tailnet_refresh(self) -> None:
        """Bind or release the Tailscale address to match the saved choice."""
        registry = self.registry
        if registry is None:
            return
        try:
            while True:
                wanted = self.experimental_network and registry.tailnet_enabled()
                detection = await asyncio.to_thread(detect) if wanted else None
                # The user may have switched while the check ran; follow the latest choice.
                if wanted == (self.experimental_network and registry.tailnet_enabled()):
                    break
            await self._tailnet_apply(detection)
        except Exception as error:  # noqa: BLE001
            logger.warning("Tailscale check failed: %s", type(error).__name__)
            with self.tailnet_lock:
                self.tailnet_error = "check_failed"
        finally:
            self.tailnet_due = time.monotonic() + (
                TAILNET_RECHECK if self.tailnet_host else TAILNET_RETRY
            )

    async def _tailnet_apply(self, detection: Detection | None) -> None:
        listener, port = self.listener, self.port
        host = str(ipaddress.ip_address(detection.host)) if detection and detection.host else None
        error = None
        if host != self.tailnet_host and listener is not None and port is not None:
            if self.tailnet_host is not None:
                await listener.remove_host(self.tailnet_host)
                with self.tailnet_lock:
                    self.tailnet_host = None
            if host is not None:
                try:
                    # Same port as the LAN listener, so one access rule covers both.
                    await listener.add_host(host, port)
                    with self.tailnet_lock:
                        self.tailnet_host = host
                except OSError:
                    error = "listen_failed"
        with self.tailnet_lock:
            self.tailnet_detection = detection
            self.tailnet_error = error
            self.tailnet_checked = time.time() if detection is not None else None
        if self.application is not None:
            self.application.tailnet_endpoint = (
                {"host": self.tailnet_host, "port": port}
                if self.tailnet_host and port is not None
                else None
            )

    def _tailnet_action(self, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.registry is None:
                raise StorageError("protected_device_service_unavailable")
            if not self.experimental_network:
                raise PairingError("experimental_network_opt_in_required")
            if body.get("action") == "tailnet":
                if not isinstance(body.get("enabled"), bool):
                    raise PairingError("invalid_tailnet_choice")
                self.registry.save_tailnet_enabled(body["enabled"])
            task = self._tailnet_start()
        # Wait outside the lock, so status polling keeps answering during the check.
        try:
            task.result(timeout=15)
        except concurrent.futures.TimeoutError:
            pass
        with self.lock:
            return self.status()

    def _reachable(self, peer: str) -> bool:
        assert self.registry is not None
        try:
            endpoint = self.registry.endpoint(peer)
        except ValueError:
            return False
        return self.experimental_network or ipaddress.ip_address(endpoint["host"]).is_loopback

    def _dispatch(
        self, key: tuple[str, str], peer: str, event: dict[str, Any]
    ) -> concurrent.futures.Future[str]:
        """Run one exchange per key. Runs on any thread and never takes ``self.lock``."""
        assert self.registry is not None and self.application is not None
        with self.tasks_lock:
            running = self.remote_tasks.get(key)
            if running is not None and not running.done():
                return running
            for finished in [k for k, task in self.remote_tasks.items() if task.done()]:
                del self.remote_tasks[finished]
            app = self.application
            task = self.remote_tasks[key] = asyncio.run_coroutine_threadsafe(
                exchange(
                    self.registry,
                    peer,
                    event,
                    partial(app.remote_result, peer),
                    preface=[app.acknowledgment(peer)],
                    trailer=[app.advertisement(peer)],
                ),
                self.loop,
            )
            return task

    def _refresh_fleet(
        self, *, force: bool = False, requested: bool = False
    ) -> list[concurrent.futures.Future[str]]:
        """Ask paired desktops for their fleet. Never takes ``self.lock``.

        The background loop asks each desktop every ``FLEET_REFRESH`` seconds. A
        status request also asks any desktop without a fresh answer right away.
        """
        if self.registry is None or self.application is None:
            return []
        now = self.registry.clock()
        started = []
        for fleet_peer in self.application.fleet():
            peer = fleet_peer["device_id"]
            if (
                fleet_peer["state"] != "active"
                or fleet_peer["role"] != "desktop"
                or not self._reachable(peer)
            ):
                continue
            elapsed = now - self.fleet_probed.get(peer, 0.0)
            stale = fleet_peer["reachability"] != "reachable"
            if not force and elapsed < (5 if requested and stale else FLEET_REFRESH):
                continue
            self.fleet_probed[peer] = now
            try:
                started.append(
                    self._dispatch((peer, "fleet"), peer, self.application.fleet_probe(peer))
                )
            except ValueError:
                continue
        return started

    def _fleet_status(self) -> dict[str, Any]:
        with self.lock:
            if self.registry is None or self.application is None:
                raise StorageError("protected_device_service_unavailable")
            pending = self._refresh_fleet(requested=True)
        if pending:
            # Answer with what the desktops say now, not the previous snapshot.
            concurrent.futures.wait(pending, timeout=FLEET_WAIT)
        with self.lock:
            assert self.registry is not None and self.application is not None
            return {"devices": self.application.fleet(), "observed_at": self.registry.clock()}

    def action(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("action") == "fleet_status":
            return self._fleet_status()
        if body.get("action") in ("tailnet", "tailnet_check"):
            return self._tailnet_action(body)
        with self.lock:
            action = body.get("action")
            if action == "enable":
                self.start(initialize=True)
                return self.status()
            if self.registry is None or self.invitations is None:
                raise StorageError("protected_device_service_unavailable")
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
            if action in {"send", "cancel_work"}:
                assert self.application is not None
                peer = str(body.get("device_id", ""))
                endpoint = self.registry.endpoint(peer)
                if (
                    not self.experimental_network
                    and not ipaddress.ip_address(endpoint["host"]).is_loopback
                ):
                    raise PairingError("experimental_network_opt_in_required")
                event = (
                    self.application.cancel_remote(peer, str(body.get("request_id", "")))
                    if action == "cancel_work"
                    else self.application.queue_remote(peer, body)
                )
                key = (peer, event["request_id"] + (":cancel" if action == "cancel_work" else ""))
                self._dispatch(key, peer, event)
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
            if action == "remote_control":
                assert self.application is not None
                if not isinstance(body.get("enabled"), bool):
                    raise PairingError("invalid_remote_control_choice")
                self.application.remote.enable(str(body.get("device_id", "")), body["enabled"])
                return self.status()
            if action == "desktop_control":
                assert self.application is not None
                if not isinstance(body.get("enabled"), bool):
                    raise PairingError("invalid_desktop_control_choice")
                self.application.desktop.enable(str(body.get("device_id", "")), body["enabled"])
                return self.status()
            if action == "host_threads":
                assert self.application is not None
                if not isinstance(body.get("enabled"), bool):
                    raise PairingError("invalid_thread_hosting_choice")
                self.application.enable_hosting(str(body.get("device_id", "")), body["enabled"])
                return self.status()
            if action == "host_template":
                assert self.application is not None
                # The local API builds this context from canonical, granted project paths.
                self.application.host_template(str(body.get("device_id", "")), body["context"])
                return {"saved": True}
            if action == "settings_exchange":
                assert self.application is not None
                return self.application.desktop.exchange_settings(
                    body.get("settings"), body.get("applied")
                )
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
                # Phones connect only over the local network, so a QR must carry its address.
                if self.port is None or ipaddress.ip_address(self.host).is_loopback:
                    raise PairingError("local_network_address_required")
                result = self.invitations.create(
                    str(body.get("name", "Raticode Desktop")),
                    {"host": self.host, "port": self.port},
                )
                import segno

                # segno leaves the background transparent by default, which turns
                # into dark-on-dark in dark mode; paint an opaque white field.
                result["qr"] = segno.make(result["uri"], micro=False).svg_data_uri(
                    scale=4, dark="#000", light="#fff", omitsize=True
                )
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
                self.outbound.add_done_callback(self._outbound_done)
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
                self.outbound.add_done_callback(self._outbound_done)
            elif action == "cancel_pairing":
                if self.outbound is not None:
                    self.outbound.cancel()
            else:
                raise PairingError("unknown_device_action")
            return self.status()

    def _outbound_done(self, future: concurrent.futures.Future[str]) -> None:
        # A new or resumed pairing introduces this desktop's address right away.
        if not future.cancelled() and future.exception() is None:
            self.fleet_due = True

    async def _remote_loop(self) -> None:
        last_retry = 0.0
        while True:
            try:
                if self.application is not None and self.registry is not None:
                    now = self.registry.clock()
                    if now - last_retry >= 30:
                        last_retry = now
                        for peer, event in self.application.queued_remote():
                            if self._reachable(peer):
                                # The exact durable request is retried on a fresh
                                # authenticated session.
                                self._dispatch((peer, event["request_id"]), peer, event)
                    # Paired desktops keep each other's status, address and shared
                    # threads current, so any device in the fleet sees the others.
                    due, self.fleet_due = self.fleet_due, False
                    self._refresh_fleet(force=due)
                    if (
                        self.experimental_network
                        and time.monotonic() >= self.tailnet_due
                        and (self.registry.tailnet_enabled() or self.tailnet_host)
                    ):
                        # Picks up Tailscale starting late or its address changing.
                        self._tailnet_start()
            except Exception as error:  # noqa: BLE001
                # Keep retrying and refreshing; one bad tick must not end the loop.
                logger.warning("Paired device background work failed: %s", type(error).__name__)
            await asyncio.sleep(5)

    def attach_services(
        self,
        *,
        providers: Any = None,
        swarms: Any = None,
        data_dir: Path | None = None,
        decide_workflow: Any = None,
    ) -> None:
        """Backend services the phone uses directly, without the desktop window."""
        with self.lock:
            self.services.update(
                {
                    "providers": providers,
                    "swarms": swarms,
                    "data_dir": data_dir,
                    "decide_workflow": decide_workflow,
                }
            )
            self._apply_services()

    def _apply_services(self) -> None:
        if self.application is None or not self.services:
            return
        self.application.workspace.provider_source = self.services.get("providers")
        self.application.workspace.refresh_providers()
        remote = self.application.remote
        remote.swarms = self.services.get("swarms")
        remote.data_dir = self.services.get("data_dir")
        remote.decide_workflow = self.services.get("decide_workflow")
        desktop = self.application.desktop
        desktop.rem = self.services.get("rem")
        desktop.organizations = self.services.get("organizations")
        desktop.usage = self.services.get("usage")

    def attach_desktop(
        self, *, rem: Any = None, organizations: Any = None, usage: Any = None
    ) -> None:
        """Rem, organizations and usage for phones allowed to use Raticode remotely."""
        with self.lock:
            self.services.update(rem=rem, organizations=organizations, usage=usage)
            self._apply_services()

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
