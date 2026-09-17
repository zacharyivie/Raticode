"""Protocol tests use a local fake app-server; no provider is invoked."""

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.chat import build_chat_prompt
from gofer.ui.codex_steering import CodexTurnControl, stream_codex_turn


@pytest.mark.asyncio
@pytest.mark.parametrize("reject", [False, True])
async def test_active_steering_and_stale_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reject: bool
) -> None:
    script = tmp_path / "fake.py"
    log = tmp_path / "requests.jsonl"
    script.write_text("""import json, sys

def send(value):
 print(json.dumps(value), flush=True)

for line in sys.stdin:
 m=json.loads(line)
 with open(sys.argv[1], 'a') as f: f.write(line)
 method=m['method']
 if method=='initialized': continue
 result={}
 if method=='thread/start': result={'thread': {'id':'thread-1'}}
 if method=='turn/start': result={'turn': {'id':'turn-1'}}
 if method=='turn/steer':
  if sys.argv[2]=='True':
   send({'id':m['id'], 'error':{'code':-32601,'message':'unsupported'}})
  else: send({'id':m['id'], 'result':{'turnId':'turn-1'}})
  send({'method':'item/completed','params':{'item':{'id':'answer','type':'agentMessage','text':'done'}}})
  send({'method':'turn/completed','params':{'turn':{'id':'turn-1','status':'completed'}}})
  continue
 send({'id':m['id'], 'result':result})
""")
    spawn = asyncio.create_subprocess_exec
    captured: list[str] = []

    async def fake_spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        captured.extend(args)
        return await spawn(sys.executable, str(script), str(log), str(reject), **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    control = CodexTurnControl()
    command = [
        sys.executable,
        "exec",
        "--sandbox",
        "workspace-write",
        "--model",
        "model-test",
        "--add-dir",
        str(tmp_path),
        "-c",
        'model_reasoning_effort="high"',
        "-c",
        "features.shell_tool=false",
        "prompt",
    ]

    async def collect() -> list[dict[str, Any]]:
        return [
            event
            async for event in stream_codex_turn(
                command, control=control, cwd=tmp_path, cancel_event=None, max_output_bytes=100_000
            )
        ]

    running = asyncio.create_task(collect())
    for _ in range(100):
        if control.active or running.done():
            break
        await asyncio.sleep(0.01)
    assert control.active
    assert await control.steer("New task") is (not reject)
    events = await asyncio.wait_for(running, 3)
    assert events[-1]["returncode"] == 0
    assert '"agent_message"' in events[0]["text"]
    assert not control.active
    assert not await control.steer("Too late")
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert [request["method"] for request in requests][:4] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
    ]
    steer = next(request for request in requests if request["method"] == "turn/steer")
    assert steer["params"] == {
        "threadId": "thread-1",
        "expectedTurnId": "turn-1",
        "input": [{"type": "text", "text": "New task"}],
    }
    start = next(request for request in requests if request["method"] == "thread/start")
    assert start["params"]["sandbox"] == "workspace-write"
    assert start["params"]["approvalPolicy"] == "never"
    assert start["params"]["model"] == "model-test"
    assert captured == [
        sys.executable,
        "app-server",
        "-c",
        'model_reasoning_effort="high"',
        "-c",
        "features.shell_tool=false",
    ]


@pytest.mark.asyncio
async def test_cancel_during_initialization_reaps_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spawn = asyncio.create_subprocess_exec
    processes: list[asyncio.subprocess.Process] = []

    async def fake_spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    cancel = threading.Event()
    cancel.set()
    events = [
        event
        async for event in stream_codex_turn(
            [sys.executable, "exec", "--sandbox", "read-only", "prompt"],
            control=CodexTurnControl(),
            cwd=tmp_path,
            cancel_event=cancel,
            max_output_bytes=100_000,
        )
    ]
    assert events[-1]["returncode"] == 1
    assert processes[0].returncode is not None


def test_swarm_identity_reuses_chat_context() -> None:
    prompt = build_chat_prompt(
        "codex",
        "cli-default",
        [{"role": "user", "body": "task"}],
        {"projectRoot": "/tmp"},
        agent_instructions="You are the reviewer.",
    )
    assert prompt.startswith("You are the reviewer.")
    assert "You are Rem" not in prompt
    assert "Resource index" in prompt
    assert "USER: task" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_method", ["initialize", "turn/start"])
async def test_fallback_only_before_a_turn_can_execute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_method: str
) -> None:
    from gofer.ui import codex_steering

    script = tmp_path / "fake_failure.py"
    script.write_text("""import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if m['method']=='initialized': continue
 if m['method']==sys.argv[1]:
  print(json.dumps({'id':m['id'],'error':{'code':-32601,'message':'unsupported'}}),flush=True)
 elif m['method']=='thread/start':
  print(json.dumps({'id':m['id'],'result':{'thread':{'id':'t'}}}),flush=True)
 else: print(json.dumps({'id':m['id'],'result':{}}),flush=True)
""")
    spawn = asyncio.create_subprocess_exec
    fallback_calls: list[list[str]] = []

    async def fake_spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        return await spawn(sys.executable, str(script), failure_method, **kwargs)

    async def fake_fallback(command: list[str], **kwargs: Any) -> Any:
        fallback_calls.append(command)
        yield {"type": "exit", "returncode": 0}

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(codex_steering, "stream_subprocess", fake_fallback)
    events = [
        event
        async for event in stream_codex_turn(
            [sys.executable, "exec", "--sandbox", "read-only", "prompt"],
            control=CodexTurnControl(),
            cwd=tmp_path,
            cancel_event=None,
            max_output_bytes=100_000,
        )
    ]
    assert bool(fallback_calls) == (failure_method == "initialize")
    assert events[-1]["returncode"] == (0 if failure_method == "initialize" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("ack timeout"), OSError("transport lost")])
async def test_missing_steering_ack_is_uncertain(failure: Exception) -> None:
    from typing import cast

    from gofer.ui.codex_steering import SteeringDeliveryUncertain

    class FakeTransport:
        async def request(self, method: str, params: Any) -> Any:
            raise failure

    control = CodexTurnControl()
    control.thread_id = "thread"
    control.turn_id = "turn"
    control._transport = cast(Any, FakeTransport())
    with pytest.raises(SteeringDeliveryUncertain):
        await control.steer("May already be accepted")
    assert not control.active


@pytest.mark.asyncio
async def test_wrong_steering_ack_is_uncertain() -> None:
    from typing import cast

    from gofer.ui.codex_steering import SteeringDeliveryUncertain

    class FakeTransport:
        async def request(self, method: str, params: Any) -> Any:
            return {"turnId": "other-turn"}

    control = CodexTurnControl()
    control.thread_id = "thread"
    control.turn_id = "turn"
    control._transport = cast(Any, FakeTransport())
    with pytest.raises(SteeringDeliveryUncertain):
        await control.steer("May already be accepted")


@pytest.mark.parametrize(
    "registered", [None, "http://127.0.0.1:1234/other", "http://127.0.0.1:1234/turn"]
)
def test_only_registered_swarm_tool_receives_approval(
    tmp_path: Path, registered: str | None
) -> None:
    from gofer.core.prompt_envelope import AgentResources, McpReference, codex_mcp_server_names
    from gofer.ui.chat import _build_chat_command

    resources = AgentResources(
        mcpServers=[McpReference(name="swarm", url="http://127.0.0.1:1234/turn")]
    )
    command = _build_chat_command(
        "codex",
        "cli-default",
        "prompt",
        data_dir=tmp_path,
        working_dir=tmp_path,
        resources=resources,
        trusted_swarm_url=registered,
    )
    actual_name = codex_mcp_server_names(resources, tmp_path)["swarm"]
    approval = f'mcp_servers.{actual_name}.tools.swarm_action.approval_mode="approve"'
    assert (approval in command) == (registered == "http://127.0.0.1:1234/turn")
    if registered == "http://127.0.0.1:1234/turn":
        assert f'mcp_servers.{actual_name}.enabled_tools=["swarm_action"]' in command


@pytest.mark.asyncio
async def test_unlimited_swarm_transport_survives_large_stdout_and_stderr(monkeypatch, tmp_path):
    script = tmp_path / "large.py"
    script.write_text("""import json, sys
for line in sys.stdin:
 m=json.loads(line)
 method=m['method']
 if method == 'initialized': continue
 result={}
 if method == 'thread/start': result={'thread': {'id': 'thread'}}
 if method == 'turn/start': result={'turn': {'id': 'turn'}}
 print(json.dumps({'id': m['id'], 'result': result}), flush=True)
 if method == 'turn/start':
  sys.stderr.write('x' * 2100000); sys.stderr.flush()
  for i in range(600):
   print(json.dumps({'method':'item/completed', 'params':{'item':{
    'type':'commandExecution','id':str(i),'aggregatedOutput':'x'*4096}}}), flush=True)
  print(json.dumps({'method':'turn/completed',
   'params':{'turn':{'status':'completed'}}}), flush=True)
""")
    spawn = asyncio.create_subprocess_exec

    async def fake_spawn(*args, **kwargs):
        return await spawn(sys.executable, str(script), **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    events = [
        event
        async for event in stream_codex_turn(
            [sys.executable, "exec", "--sandbox", "workspace-write", "prompt"],
            control=CodexTurnControl(),
            cwd=tmp_path,
            cancel_event=None,
            max_output_bytes=None,
        )
    ]
    assert events[-1] == {"type": "exit", "returncode": 0}
    assert len(events) == 601
