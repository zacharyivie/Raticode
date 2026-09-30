from __future__ import annotations

import json
from pathlib import Path

import pytest

from gofer.core.http import HttpRequest, HttpResponse, UrllibHttpClient
from gofer.core.scheduler import _run_workflow
from gofer.core.watcher import WatchedWorkflow, WorkflowWatcher
from gofer.core.workflow import WatchConfig


@pytest.mark.parametrize("provider", ["openai_api", "anthropic_api"])
@pytest.mark.parametrize("trigger", ["schedule", "watch"])
def test_automatic_runs_support_direct_api_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, trigger: str
) -> None:
    requests: list[HttpRequest] = []

    async def send(self: UrllibHttpClient, request: HttpRequest) -> HttpResponse:
        requests.append(request)
        return HttpResponse(
            status=200,
            headers={},
            body=b'{"output_text":"done","content":[{"type":"text","text":"done"}]}',
        )

    monkeypatch.setattr(UrllibHttpClient, "send", send)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    (tmp_path / "prompt.md").write_text("Review this change.", encoding="utf-8")
    workflow_path = tmp_path / "workflow.toml"
    workflow_path.write_text(
        f'''
[workflow]
id = "automatic"
name = "Automatic provider regression"

[workflow.schedule]
cron_expression = "0 9 * * *"

[workflow.watch]
path = "."

[[nodes]]
id = "ask"
type = "agent"
agent_id = "assistant"
working_dir = "."
prompt_path = "prompt.md"

[agents.assistant]
subscription = "{provider}"
working_dir = "."
''',
        encoding="utf-8",
    )
    if trigger == "schedule":
        _run_workflow("automatic", str(workflow_path), {})
    else:
        watcher = WorkflowWatcher()
        watcher._run_workflow(
            WatchedWorkflow("automatic", workflow_path, WatchConfig(path=tmp_path)), []
        )

    assert len(requests) == 1
    sidecars = list((tmp_path / "logs/automatic").glob("*.outputs.json"))
    assert len(sidecars) == 1
    payload = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert payload["nodeOutputs"]["ask"]["success"] is True
    assert payload["nodeOutputs"]["ask"]["output"] == "done"
