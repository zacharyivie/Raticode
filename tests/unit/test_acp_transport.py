"""Bidirectional fake agents exercise transport behavior without provider CLIs."""

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from gofer.subscriptions.acp_transport import (
    AcpRpcError,
    AcpTransportError,
    open_acp_transport,
)


def agent(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "fake agent.py"
    script.write_text(
        "import json, os, sys, time\n"
        "def read(): return json.loads(sys.stdin.readline())\n"
        "def send(value):\n"
        " value = {'jsonrpc': '2.0', **value}\n"
        " sys.stdout.write(json.dumps(value, ensure_ascii=False) + '\\n'); sys.stdout.flush()\n"
        + body
    )
    return [sys.executable, str(script)]


async def test_fragmented_unicode_notifications_and_interleaved_permission_request(tmp_path):
    command = agent(
        tmp_path,
        r"""
first = read()
assert first['jsonrpc'] == '2.0' and first['method'] == 'initialize'
send({'id': first['id'], 'result': {'protocolVersion': 1}})
prompt = read()
data = (json.dumps({'jsonrpc':'2.0', 'method':'session/update', 'params':{
    'sessionId':'session-a', 'update':{'sessionUpdate':'agent_message_chunk',
    'content':{'type':'text','text':'café 🐀'}}}}, ensure_ascii=False) + '\n').encode()
for byte in data:
 sys.stdout.buffer.write(bytes([byte])); sys.stdout.buffer.flush()
send({'id':'permission-a','method':'session/request_permission','params':{'sessionId':'session-a'}})
answer = read()
assert answer['id'] == 'permission-a' and answer['result']['outcome']['outcome'] == 'cancelled'
send({'id':prompt['id'],'result':{'stopReason':'end_turn'}})
time.sleep(30)
""",
    )
    seen = []

    async def permission(method, params):
        seen.append((method, params))
        return {"outcome": {"outcome": "cancelled"}}

    async with open_acp_transport(command, cwd=tmp_path, request_handler=permission) as rpc:
        assert await rpc.request("initialize", {"protocolVersion": 1}) == {"protocolVersion": 1}
        prompt = asyncio.create_task(rpc.request("session/prompt", {"sessionId": "session-a"}))
        event = await rpc.next_notification()
        assert event["params"]["update"]["content"]["text"] == "café 🐀"
        assert await prompt == {"stopReason": "end_turn"}
        assert seen == [("session/request_permission", {"sessionId": "session-a"})]
        process = rpc.process
    assert process.returncode is not None


async def test_concurrent_requests_correlate_out_of_order_responses(tmp_path):
    command = agent(
        tmp_path,
        """
first, second = read(), read()
for request in [second, first]:
 send({'id': request['id'], 'result': {'method': request['method']}})
time.sleep(30)
""",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        results = await asyncio.gather(rpc.request("first", {}), rpc.request("second", {}))
        assert results[0] == {"method": "first"}
        assert results[1] == {"method": "second"}


async def test_prompt_response_does_not_discard_queued_updates(tmp_path):
    command = agent(
        tmp_path,
        """
request = read()
for part in ['first', 'second', 'last']:
 send({'method':'session/update','params':{'text':part}})
send({'id':request['id'],'result':{'stopReason':'end_turn'}})
time.sleep(30)
""",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        assert await rpc.request("session/prompt", {}) == {"stopReason": "end_turn"}
        assert [event["params"]["text"] for event in rpc.drain_notifications()] == [
            "first",
            "second",
            "last",
        ]
        assert rpc.drain_notifications() == []


@pytest.mark.parametrize("ending", ["eof", "malformed"])
async def test_buffered_updates_survive_terminal_error(tmp_path, ending):
    finish = (
        "send({'id':request['id'],'result':{}})\nsys.exit(0)\n"
        if ending == "eof"
        else "sys.stdout.write('malformed\\n'); sys.stdout.flush(); time.sleep(30)\n"
    )
    command = agent(
        tmp_path,
        """
request = read()
for text in ['first', 'final chunk']:
 send({'method':'session/update','params':{'text':text}})
"""
        + finish,
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        if ending == "eof":
            assert await rpc.request("session/prompt", {}) == {}
            await rpc.process.wait()
        else:
            with pytest.raises(AcpTransportError):
                await rpc.request("session/prompt", {})
        await asyncio.sleep(0.05)
        assert [event["params"]["text"] for event in rpc.drain_notifications()] == [
            "first",
            "final chunk",
        ]
        assert rpc.drain_notifications() == []
        with pytest.raises(AcpTransportError):
            rpc.raise_if_failed()
        for _ in range(2):
            with pytest.raises(AcpTransportError):
                await asyncio.wait_for(rpc.next_notification(), 1)


async def test_terminal_error_wakes_all_notification_waiters(tmp_path):
    command = agent(
        tmp_path, "read()\nsys.stdout.write('bad JSON\\n'); sys.stdout.flush()\ntime.sleep(30)\n"
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        waiters = [asyncio.create_task(rpc.next_notification()) for _ in range(2)]
        await asyncio.sleep(0)
        with pytest.raises(AcpTransportError):
            await rpc.request("initialize", {})
        for waiter in waiters:
            with pytest.raises(AcpTransportError):
                await asyncio.wait_for(waiter, 1)


async def test_unknown_callback_rejected_without_blocking_response(tmp_path):
    command = agent(
        tmp_path,
        """
request = read()
send({'id':99,'method':'terminal/create','params':{'command':'never execute'}})
response = read()
assert response['error']['code'] == -32601
send({'id':request['id'],'result':{'rejected':True}})
time.sleep(30)
""",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        assert await rpc.request("initialize", {}) == {"rejected": True}


async def test_callback_exception_fails_closed_without_exposing_details(tmp_path):
    command = agent(
        tmp_path,
        """
request = read()
send({'id':2,'method':'session/request_permission','params':{}})
response = read()
assert response['error'] == {'code':-32603,'message':'Client request rejected'}
send({'id':request['id'],'result':{}})
time.sleep(30)
""",
    )

    async def reject(method, params):
        raise ValueError("secret callback state must not be sent")

    async with open_acp_transport(command, cwd=tmp_path, request_handler=reject) as rpc:
        assert await rpc.request("session/new", {}) == {}


async def test_rpc_auth_error_preserves_code_and_does_not_authenticate_or_retry(tmp_path):
    trace = tmp_path / "requests.jsonl"
    command = agent(
        tmp_path,
        rf"""
request = read()
with open({str(trace)!r},'w') as out: out.write(json.dumps(request) + '\n')
send({{'id':request['id'],'error':{{'code':-32000,'message':'Authentication required'}}}})
time.sleep(30)
""",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        with pytest.raises(AcpRpcError, match="Authentication required") as error:
            await rpc.request("session/new", {})
        assert error.value.code == -32000
    assert [json.loads(line)["method"] for line in trace.read_text().splitlines()] == [
        "session/new"
    ]


@pytest.mark.parametrize(
    "wire",
    [
        "not JSON\n",
        json.dumps({"id": 1, "result": {}}) + "\n",
        json.dumps({"jsonrpc": "2.0", "id": 33, "result": {}}) + "\n",
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}}) + "\n",
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": []}) + "\n",
    ],
)
async def test_malformed_responses_fail_pending_work(tmp_path, wire):
    command = agent(
        tmp_path, f"read()\nsys.stdout.write({wire!r}); sys.stdout.flush()\ntime.sleep(30)\n"
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        with pytest.raises(AcpTransportError):
            await rpc.request("initialize", {}, timeout=1)
    assert rpc.process.returncode is not None


@pytest.mark.parametrize("stream,newline", [("stdout", False), ("stdout", True), ("stderr", False)])
async def test_bounded_output_including_unterminated_lines_and_stderr(tmp_path, stream, newline):
    command = agent(
        tmp_path,
        f"read()\nsys.{stream}.write('x' * 4000 + {chr(10) if newline else ''!r}); "
        f"sys.{stream}.flush()\ntime.sleep(30)\n",
    )
    async with open_acp_transport(command, cwd=tmp_path, max_output_bytes=1024) as rpc:
        with pytest.raises(AcpTransportError, match="output"):
            await rpc.request("initialize", {}, timeout=1)
    assert len(rpc.stderr) <= 1024


@pytest.mark.parametrize("mode", ["cancel", "timeout", "request-timeout"])
async def test_hung_request_is_bounded_and_process_reaped(tmp_path, mode):
    command = agent(tmp_path, "read()\ntime.sleep(30)\n")
    cancel = threading.Event()
    async with open_acp_transport(
        command, cwd=tmp_path, cancel_event=cancel, timeout=0.1 if mode == "timeout" else None
    ) as rpc:
        pending = asyncio.create_task(
            rpc.request("session/prompt", {}, timeout=0.1 if mode == "request-timeout" else 2)
        )
        if mode == "cancel":
            await asyncio.sleep(0.05)
            cancel.set()
        with pytest.raises(AcpTransportError, match="cancelled|time"):
            await pending
        with pytest.raises(AcpTransportError):
            await rpc.request("must-not-retry", {})
    assert rpc.process.returncode is not None


async def test_cancel_before_launch_never_creates_process(tmp_path):
    marker = tmp_path / "launched"
    command = agent(tmp_path, f"open({str(marker)!r},'w').close()\n")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(AcpTransportError, match="before launch"):
        async with open_acp_transport(command, cwd=tmp_path, cancel_event=cancel):
            pytest.fail("Must not enter")
    assert not marker.exists()


async def test_request_timeout_includes_blocked_stdin_write(tmp_path):
    command = agent(tmp_path, "time.sleep(30)\n")
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        with pytest.raises(AcpTransportError, match="timed out"):
            await asyncio.wait_for(
                rpc.request("session/prompt", {"text": "x" * 4_000_000}, timeout=0.1), 2
            )
    assert rpc.process.returncode is not None


async def test_caller_task_cancellation_invalidates_connection(tmp_path):
    command = agent(tmp_path, "read()\ntime.sleep(30)\n")
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        pending = asyncio.create_task(rpc.request("session/prompt", {}))
        await asyncio.sleep(0.05)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(AcpTransportError, match="uncertain"):
            await rpc.request("must-not-replay", {})
    assert rpc.process.returncode is not None


async def test_cancellation_during_spawn_reaps_child(tmp_path, monkeypatch):
    command = agent(tmp_path, "time.sleep(30)\n")
    real_spawn = asyncio.create_subprocess_exec
    launched = asyncio.Event()
    release = asyncio.Event()
    children = []

    async def delayed_spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        children.append(process)
        launched.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)

    async def connect() -> None:
        async with open_acp_transport(command, cwd=tmp_path):
            pytest.fail("Cancelled connection must not be entered")

    task = asyncio.create_task(connect())
    await launched.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None


async def test_deeply_nested_json_fails_unbounded_request_promptly(tmp_path):
    command = agent(
        tmp_path,
        "read()\nsys.stdout.write('[' * 100000 + '0' + ']' * 100000 + '\\n'); "
        "sys.stdout.flush()\ntime.sleep(30)\n",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        with pytest.raises(AcpTransportError, match="malformed"):
            await asyncio.wait_for(rpc.request("session/prompt", {}, timeout=None), 1)
        with pytest.raises(AcpTransportError):
            await rpc.next_notification()
    assert rpc.process.returncode is not None


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGTERM behavior")
async def test_cancel_during_teardown_and_concurrent_close_wait_for_reaping(tmp_path):
    command = agent(
        tmp_path,
        """
import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
request = read()
send({'id':request['id'],'result':{}})
time.sleep(30)
""",
    )
    closing = asyncio.Event()
    transports = []

    async def owner() -> None:
        async with open_acp_transport(command, cwd=tmp_path) as rpc:
            transports.append(rpc)
            await rpc.request("initialize", {})
            closing.set()

    task = asyncio.create_task(owner())
    await closing.wait()
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()
    second_close = asyncio.create_task(transports[0].close())
    await asyncio.sleep(0.05)
    assert not second_close.done()
    assert transports[0].process.returncode is None
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 4)
    await second_close
    await transports[0].close()
    assert transports[0].process.returncode is not None


async def test_eof_and_caller_exception_close_process(tmp_path):
    command = agent(tmp_path, "read()\nsys.exit(3)\n")
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        with pytest.raises(AcpTransportError, match="closed"):
            await rpc.request("initialize", {})
    command = agent(tmp_path, "time.sleep(30)\n")
    with pytest.raises(ValueError, match="caller failed"):
        async with open_acp_transport(command, cwd=tmp_path) as rpc:
            raise ValueError("caller failed")
    assert rpc.process.returncode is not None


async def test_packaged_env_sanitized_and_explicit_env_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIfake")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "")
    command = agent(
        tmp_path,
        """
import ssl
request = read()
assert 'LD_LIBRARY_PATH' not in os.environ
assert os.environ['ACP_TEST_SETTING'] == 'per-run'
send({'id':request['id'],'result':{'workingDirectory':os.getcwd()}})
time.sleep(30)
""",
    )
    async with open_acp_transport(
        command, cwd=tmp_path, env={"ACP_TEST_SETTING": "per-run"}
    ) as rpc:
        assert await rpc.request("session/new", {}) == {"workingDirectory": str(tmp_path)}


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
async def test_cleanup_kills_child_ignoring_sigterm_before_return(tmp_path):
    pid_file = tmp_path / "child.pid"
    command = agent(
        tmp_path,
        f"""
import subprocess
child_code = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'
child = subprocess.Popen([sys.executable, '-c', child_code])
with open({str(pid_file)!r},'w') as out: out.write(str(child.pid))
time.sleep(0.1)
request=read()
send({{'id':request['id'],'result':{{}}}})
time.sleep(30)
""",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        await rpc.request("initialize", {})
    pid = int(pid_file.read_text())
    for _ in range(100):
        status = Path(f"/proc/{pid}/stat")
        if not status.exists() or status.read_text().split(") ", 1)[1].startswith("Z"):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("Child remains running after transport cleanup")


async def test_content_length_catalog_fragmented_unicode_and_cleanup(tmp_path):
    command = agent(
        tmp_path,
        r"""
header = sys.stdin.buffer.readline()
assert header.startswith(b'Content-Length: ')
assert sys.stdin.buffer.readline() == b'\r\n'
request = json.loads(sys.stdin.buffer.read(int(header.split(b':')[1])))
assert request['method'] == 'models.list'
body = json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': {
 'models': [{'id': 'test', 'name': 'Modèle'}]}}, ensure_ascii=False).encode()
data = f'Content-Length: {len(body)}\r\n\r\n'.encode() + body
for byte in data:
 sys.stdout.buffer.write(bytes([byte])); sys.stdout.buffer.flush()
time.sleep(30)
""",
    )
    async with open_acp_transport(command, cwd=tmp_path, content_length_framing=True) as rpc:
        result = await rpc.request("models.list", {})
        assert result["models"][0]["name"] == "Modèle"
        process = rpc.process
    assert process.returncode is not None


@pytest.mark.parametrize(
    "data",
    [
        b"Content-Length: 999999999\r\n\r\n",
        b"Content-Length: -1\r\n\r\n",
        b"Content-Length: nope\r\n\r\n",
        b"Content-Length: 20\r\n\r\n{}",
        b"Content-Length: 2\r\nxx{}",
    ],
)
async def test_content_length_invalid_or_oversized_frames(tmp_path, data):
    command = agent(tmp_path, f"sys.stdout.buffer.write({data!r}); sys.stdout.buffer.flush()\n")
    async with open_acp_transport(
        command, cwd=tmp_path, content_length_framing=True, max_output_bytes=1024
    ) as rpc:
        with pytest.raises(AcpTransportError):
            await rpc.request("models.list", {})
