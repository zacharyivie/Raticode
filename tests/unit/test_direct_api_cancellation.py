from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from gofer.core.http import HttpRequest, HttpResponse, UrllibHttpClient
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription


@pytest.mark.parametrize("provider", [OpenAiApiSubscription, AnthropicApiSubscription])
@pytest.mark.parametrize("when", ["before", "during", "response", "never"])
async def test_direct_api_honors_stop_signal(provider, when: str, tmp_path: Path) -> None:
    cancel = threading.Event()
    started = asyncio.Event()
    closed = asyncio.Event()
    requests: list[HttpRequest] = []

    class Client:
        async def send(self, request: HttpRequest) -> HttpResponse:
            requests.append(request)
            started.set()
            try:
                if when == "during":
                    await asyncio.Event().wait()
                if when == "response":
                    cancel.set()
                return HttpResponse(
                    status=200,
                    headers={},
                    body=b'{"output_text":"done","content":[{"type":"text","text":"done"}]}',
                )
            finally:
                closed.set()

    if when == "before":
        cancel.set()
    subscription = provider(Client())
    task = asyncio.create_task(
        subscription.execute(
            prompt="hello",
            working_dir=tmp_path,
            tools=[],
            mcp_servers=[],
            env={"GOFER_DIRECT_API_KEY": "test-key"},
            cancel_event=cancel,
            provider_settings=ResolvedProviderSettings(subscription=subscription.subscription_name),
        )
    )
    if when == "during":
        await asyncio.wait_for(started.wait(), timeout=2)
        cancel.set()
    result = await asyncio.wait_for(task, timeout=2)

    assert len(requests) == (0 if when == "before" else 1)
    if when == "never":
        assert result.success
        assert result.output == "done"
    else:
        assert not result.success
        assert result.exit_code == 130
        assert "stopped" in result.output.lower()
    if requests:
        assert closed.is_set(), "The provider must finish HTTP cleanup before returning"


@pytest.mark.parametrize("provider", [OpenAiApiSubscription, AnthropicApiSubscription])
async def test_direct_api_stop_closes_http_socket(provider, tmp_path: Path) -> None:
    cancel = threading.Event()
    received = asyncio.Event()
    disconnected = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await asyncio.wait_for(reader.readexactly(length), timeout=3)
            received.set()
            # Leave the response pending until the caller closes the connection.
            assert await asyncio.wait_for(reader.read(1), timeout=3) == b""
        finally:
            writer.close()
            await writer.wait_closed()
            disconnected.set()

    class LocalClient(UrllibHttpClient):
        async def send(self, request: HttpRequest) -> HttpResponse:
            return await super().send(replace(request, network_allowlist=["127.0.0.1"]))

    async with await asyncio.start_server(handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        subscription = provider(LocalClient())
        task = asyncio.create_task(
            subscription.execute(
                prompt="local cancellation fixture",
                working_dir=tmp_path,
                tools=[],
                mcp_servers=[],
                env={"GOFER_DIRECT_API_KEY": "test-key"},
                cancel_event=cancel,
                provider_settings=ResolvedProviderSettings(
                    subscription=subscription.subscription_name,
                    api_base_url=f"http://127.0.0.1:{port}/v1",
                ),
            )
        )
        try:
            await asyncio.wait_for(received.wait(), timeout=2)
            cancel.set()
            result = await asyncio.wait_for(task, timeout=2)
            assert not result.success
            assert result.exit_code == 130
            await asyncio.wait_for(disconnected.wait(), timeout=2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("provider", [OpenAiApiSubscription, AnthropicApiSubscription])
async def test_direct_api_preserves_external_cancellation(provider, tmp_path: Path) -> None:
    started = asyncio.Event()
    closed = asyncio.Event()

    class Client:
        async def send(self, request: HttpRequest) -> HttpResponse:
            started.set()
            try:
                await asyncio.Event().wait()
                raise AssertionError("The caller should cancel the pending request")
            finally:
                closed.set()

    subscription = provider(Client())
    task = asyncio.create_task(
        subscription.execute(
            prompt="hello",
            working_dir=tmp_path,
            tools=[],
            mcp_servers=[],
            env={"GOFER_DIRECT_API_KEY": "test-key"},
            cancel_event=threading.Event(),
            provider_settings=ResolvedProviderSettings(subscription=subscription.subscription_name),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.parametrize("provider", [OpenAiApiSubscription, AnthropicApiSubscription])
async def test_direct_api_preserves_transport_error(provider, tmp_path: Path) -> None:
    class Client:
        async def send(self, request: HttpRequest) -> HttpResponse:
            raise OSError("test connection failure")

    subscription = provider(Client())
    result = await subscription.execute(
        prompt="hello",
        working_dir=tmp_path,
        tools=[],
        mcp_servers=[],
        env={"GOFER_DIRECT_API_KEY": "test-key"},
        cancel_event=threading.Event(),
        provider_settings=ResolvedProviderSettings(subscription=subscription.subscription_name),
    )
    assert not result.success
    assert result.exit_code == 1
    assert result.output == "Provider service is temporarily unavailable: test connection failure"
