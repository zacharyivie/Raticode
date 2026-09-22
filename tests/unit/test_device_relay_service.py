"""Deterministic relay-session retry tests; no network, TLS keys or real sleeps."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gofer.devices import relay_service as module
from gofer.devices.relay import RelayError, RelayMessage


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["receive_publish", "poll_publish", "poll"])
async def test_retry_after_pauses_all_channel_work_and_discards_tls(
    monkeypatch: Any, failure: str
) -> None:
    now = 0.0
    calls: list[tuple[str, float]] = []
    instances: list[Any] = []
    failed_at: float | None = None
    resumed_at: float | None = None
    poll_count = 0

    class Session:
        def __init__(self, *_args: Any) -> None:
            self.closed = False
            self.peer = "peer"
            instances.append(self)

        def receive(self, _raw: bytes) -> list[bytes]:
            calls.append(("receive", now))
            return [b"first", b"must-not-send"] if failure == "receive_publish" else []

        def poll(self) -> list[bytes]:
            calls.append(("session_poll", now))
            return [b"first", b"must-not-send"] if failure == "poll_publish" else []

    class Relay:
        def __init__(self, _origin: str) -> None:
            pass

        async def poll(self, _topic: str, *, since: str) -> list[RelayMessage]:
            nonlocal poll_count, failed_at, resumed_at
            calls.append(("relay_poll", now))
            poll_count += 1
            if failure == "poll" and poll_count == 2:
                failed_at = now
                raise RelayError("relay_rate_limited", retry_after=7)
            if failed_at is not None:
                resumed_at = now
                service.closed = True
                return []
            return [RelayMessage("message", b"opaque")] if poll_count == 1 else []

        async def publish(self, _topic: str, _frame: bytes) -> None:
            nonlocal failed_at
            calls.append(("publish", now))
            failed_at = now
            raise RelayError("relay_rate_limited", retry_after=7)

    registry = SimpleNamespace(
        reconnect_pin=lambda _peer: b"pin",
        generation=lambda _peer: "generation",
        check_generation=lambda _peer, _generation: None,
    )
    service = module.RelayService(registry, None, None)  # type: ignore[arg-type]

    async def sleep(delay: float) -> None:
        nonlocal now
        now += delay
        if now > 20:
            raise AssertionError("channel did not resume after cooldown")

    monkeypatch.setattr(module, "NtfyRelay", Relay)
    monkeypatch.setattr(module, "DeviceSession", Session)
    monkeypatch.setattr(
        module, "Frame", SimpleNamespace(decode=lambda _raw: SimpleNamespace(session_id="session"))
    )
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now, time=lambda: 1000))
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(sleep=sleep))
    await service._channel(
        {"origin": "https://relay.invalid", "receive": "inbox", "send": "outbox", "peer": "peer"}
    )
    assert failed_at is not None and resumed_at is not None
    assert resumed_at >= failed_at + 7
    assert all(not failed_at < at < failed_at + 7 for _, at in calls)
    assert len(instances) == 1 and instances[0].closed
    assert sum(kind == "publish" for kind, _ in calls) == (0 if failure == "poll" else 1)
    # The failed flight is never resumed or republished after the deadline.
    assert not any(
        kind in ("receive", "session_poll", "publish") and at >= failed_at + 7 for kind, at in calls
    )


def test_success_only_clears_its_own_channel_and_operation():
    service = module.RelayService(None, None, None)  # type: ignore[arg-type]
    service._backoff(RelayError("relay_rate_limited", 60), {}, "phone", "publish")
    service._backoff(RelayError("relay_http", 10), {}, "invitation", "poll")
    service._succeeded("phone", "poll")
    service._succeeded("invitation", "publish")
    assert service.error == "relay_rate_limited"
    service._succeeded("invitation", "poll")
    assert service.error == "relay_rate_limited"
    service._succeeded("phone", "publish")
    assert service.error is None


@pytest.mark.asyncio
async def test_old_relay_channel_stops_if_identity_is_repaired_during_poll(monkeypatch):
    generation = "old"

    class Relay:
        def __init__(self, _origin):
            pass

        async def poll(self, _topic, *, since):
            nonlocal generation
            generation = "new"
            return [RelayMessage("message", b"must-not-decode")]

    def check_generation(_peer, expected):
        if generation != expected:
            raise ValueError("pairing_changed")

    registry = SimpleNamespace(
        reconnect_pin=lambda _peer: b"pin",
        generation=lambda _peer: generation,
        check_generation=check_generation,
    )
    service = module.RelayService(registry, None, None)  # type: ignore[arg-type]
    service.errors["inbox", "publish"] = "relay_rate_limited"

    def reject_decode(_raw):
        raise AssertionError("a removed channel processed an old routing message")

    monkeypatch.setattr(module, "Frame", SimpleNamespace(decode=reject_decode))
    monkeypatch.setattr(module, "NtfyRelay", Relay)
    await service._channel(
        {
            "origin": "https://relay.invalid",
            "receive": "inbox",
            "send": "outbox",
            "peer": "peer",
        }
    )
    assert service.error is None
