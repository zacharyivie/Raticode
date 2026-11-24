"""A Rem worker's turn carries the report design rules and the Second Brain paragraph once.

The test builds what a model reads at the start of a turn: the prompt, every tool
server's instructions and every tool description. The report theme's design text is
only in what a rules tool returns, so it never appears there for a develop worker and
at most once for a research worker. The Second Brain paragraph is in the prompt only.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from gofer.cli.commands.second_brain import _parser as second_brain_parser
from gofer.cli.commands.second_brain import run_second_brain
from gofer.core.prompt_envelope import AgentResources
from gofer.ui import second_brain
from gofer.ui.chat import build_chat_prompt
from gofer.ui.rem_home import RemHome
from gofer.ui.report_outputs import serve_reports, with_report_outputs
from gofer.ui.report_themes import read_theme_file
from gofer.ui.second_brain import with_second_brain
from tests.unit.test_rem_home import home, recipe, wait_run  # noqa: F401 - the home fixture
from tests.unit.test_swarm_tools import request

DESIGN = "Orbit Lab: graphite surfaces, signal-orange accents and a faint starfield grid."
THEME = {"enabled": True, "format": "html", "theme": "custom-orbit", "instructions": DESIGN}
# Text that only the design rules contain: the custom theme, the closing design
# guidance every theme gets, and the Second Brain's own design text.
DESIGN_TEXT = (
    DESIGN,
    "Make the composition specific to the findings",
    "HTML design direction",
    "Make reports visually interesting",
)
BRAIN_PARAGRAPH = "Second Brain is enabled."
CLI = Path("/trusted/Raticode")
SERVE_STDIO = second_brain.serve_stdio
STDIO_REQUESTS = "".join(
    json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params}) + "\n"
    for number, method, params in (
        (1, "initialize", {}),
        (2, "tools/list", {}),
        (3, "tools/call", {"name": "rules", "arguments": {}}),
    )
)


def http_server(url: str) -> tuple[list[str], str | None]:
    """A turn's HTTP tool server: what the model reads up front, and its rules reply."""

    def call(method: str, params: dict[str, Any] | None = None) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        return request(url, body)[1]["result"]

    tools = call("tools/list")["tools"]
    upfront = [call("initialize")["instructions"], *(tool["description"] for tool in tools)]
    if not any(tool["name"] == "rules" for tool in tools):
        return upfront, None
    return upfront, call("tools/call", {"name": "rules", "arguments": {}})["content"][0]["text"]


def stdio_server(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> tuple[list[str], str | None]:
    """Start a stdio tool server from the arguments the turn would run it with."""
    output = io.StringIO()

    def captured(call: Any, definitions: Any, instructions: str, name: str, **_: Any) -> None:
        SERVE_STDIO(
            call,
            definitions,
            instructions,
            name,
            input_stream=io.StringIO(STDIO_REQUESTS),
            output_stream=output,
        )

    monkeypatch.setattr(second_brain, "serve_stdio", captured)
    if args[:2] == ["ui", "second-brain"]:
        parsed = second_brain_parser().parse_args(args[2:])
        run_second_brain(
            parsed.root,
            parsed.report_format,
            parsed.report_theme,
            parsed.library_data_dir,
            parsed.brain_id,
            {},
            parsed.memory_folder,
            parsed.report_theme_file,
            parsed.task_report_tool,
        )
    else:
        assert args[:2] == ["ui", "reports"]
        options = dict(zip(args[2::2], args[3::2], strict=True))
        serve_reports(
            Path(options["--root"]),
            options["--report-format"],
            read_theme_file(Path(options["--report-theme-file"])),
        )
    initialize, listed, rules = (
        json.loads(line)["result"] for line in output.getvalue().splitlines()
    )
    return (
        [initialize["instructions"], *(tool["description"] for tool in listed["tools"])],
        rules["content"][0]["text"],
    )


def worker_turn(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
) -> tuple[str, list[str], list[str]]:
    """Run a worker and return its prompt, everything its servers say up front, and
    what their rules tools return. The turn is assembled the way stream_workflow_chat
    assembles it."""
    original = home.source
    seen: dict[str, Any] = {}

    async def source(**kwargs: Any):
        if "independent read-only reviewer" not in kwargs["agent_instructions"] and not seen:
            workflow = with_second_brain(
                kwargs["workflow"], CLI, {"threadId": "t"}, tmp_path / "data"
            )
            workflow = with_report_outputs(workflow, CLI, tmp_path, tmp_path / "data")
            assert workflow is not None
            seen["prompt"] = build_chat_prompt(
                "codex",
                kwargs.get("model") or "cli-default",
                kwargs.get("messages") or [],
                workflow,
                CLI,
                agent_instructions=kwargs["agent_instructions"],
            )
            upfront: list[str] = []
            rules: list[str] = []
            for server in AgentResources.model_validate(workflow["remResources"]).mcpServers:
                said, returned = (
                    http_server(server.url)
                    if server.type == "http"
                    else stdio_server(monkeypatch, server.args)
                )
                upfront += said
                rules += [returned] if returned else []
            seen.update(upfront=upfront, rules=rules)
            if task_type == "research":
                artifact = next(
                    s
                    for s in workflow["remResources"]["mcpServers"]
                    if s["name"] == "rem_artifacts"
                )
                saved = request(
                    artifact["url"],
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "save_report",
                            "arguments": {
                                "path": "findings.html",
                                "content": "<!doctype html><html><body>Findings</body></html>",
                            },
                        },
                    },
                )[1]["result"]
                assert not saved.get("isError"), saved
        async for event in original(**kwargs):
            yield event

    brain = tmp_path / "brain"
    brain.mkdir()
    home.source = source
    home.configure(
        {
            "provider": "codex",
            "resources": {"shell": True, "web": True},
            "report_theme": THEME,
            "brain": {"enabled": True, "root": str(brain)},
        }
    )
    task = home.create_task({"request_id": task_type, **recipe(tmp_path, type=task_type)})["task"]
    wait_run(home, task["id"], {"succeeded", "blocked"})
    return seen["prompt"], seen["upfront"], seen["rules"]


@pytest.mark.parametrize("task_type", ["develop", "research"])
def test_turn_carries_report_design_and_brain_rules_once(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
) -> None:
    prompt, upfront, rules = worker_turn(home, tmp_path, monkeypatch, task_type)
    everything = "\n".join([prompt, *upfront])
    counts = {text: everything.count(text) for text in DESIGN_TEXT}
    if task_type == "develop":
        assert counts == dict.fromkeys(DESIGN_TEXT, 0)
    else:
        assert all(count <= 1 for count in counts.values()), counts
    # The prompt says once where the rules are, and the paragraph is not repeated.
    assert prompt.count("for the report format and design rules") == 1
    assert everything.count(BRAIN_PARAGRAPH) == 1 and BRAIN_PARAGRAPH in prompt
    assert everything.count("Default report output:") == 0
    # The rules tools still return the full theme, each once.
    assert rules and all(text.count(DESIGN) == 1 for text in rules)
    assert all(text.count("Default report output: standalone HTML") == 1 for text in rules)
    tools = "rem_artifacts" if task_type == "research" else "reports"
    assert f"`rules` on {tools}" in prompt and "`rules` on second_brain" in prompt


TASK_REPORT_LINE = "Save this task's report with rem_artifacts.save_report, not save_note."
CHAT_SAVE_LINE = "save_note to create a report"


@pytest.mark.parametrize("task_type", ["develop", "research"])
def test_research_turns_are_told_one_way_to_save_their_report(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
) -> None:
    # Research tasks have rem_artifacts: the prompt and the second_brain rules tool
    # both send the report there and keep save_note for notes.
    prompt, _, rules = worker_turn(home, tmp_path, monkeypatch, task_type)
    brain_rules = [text for text in rules if BRAIN_PARAGRAPH in text]
    assert len(brain_rules) == 1
    if task_type == "research":
        assert TASK_REPORT_LINE in prompt and CHAT_SAVE_LINE not in prompt
        assert "Use save_note only for notes" in prompt
        assert "save_note to create a note" in prompt
        assert TASK_REPORT_LINE in brain_rules[0] and CHAT_SAVE_LINE not in brain_rules[0]
    else:
        # A develop task has no rem_artifacts, so it keeps the usual save_note line.
        assert TASK_REPORT_LINE not in prompt and CHAT_SAVE_LINE in prompt
        assert CHAT_SAVE_LINE in brain_rules[0]
